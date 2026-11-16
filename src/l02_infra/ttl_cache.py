from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING = object()


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Point-in-time counters for one cache (or an aggregate)."""

    hits: int = 0
    misses: int = 0
    sets: int = 0
    evictions: int = 0
    expirations: int = 0
    size: int = 0
    maxsize: int = 0
    default_ttl_ms: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        lookups = self.lookups
        return (self.hits / lookups) if lookups else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "size": self.size,
            "maxsize": self.maxsize,
            "default_ttl_ms": self.default_ttl_ms,
            "hit_rate": self.hit_rate,
        }


class TTLCache(Generic[K, V]):
    """Small thread-safe cache with per-entry TTL and LRU capacity.

    The clock is injectable so tests can advance time without sleeping. A
    negative or zero TTL disables storage; this makes an operator switch
    (ttl_ms=0) turn the cache into a pass-through without code changes.
    """

    def __init__(
        self,
        *,
        maxsize: int = 1024,
        ttl_ms: int = 2_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = int(maxsize)
        self._default_ttl_ms = int(ttl_ms)
        self._clock = clock
        self._lock = threading.RLock()
        self._items: "OrderedDict[K, tuple[float, V]]" = OrderedDict()
        self._async_locks: dict[K, asyncio.Lock] = {}
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0
        self._expirations = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def _peek(self, key: K, *, touch: bool = False) -> tuple[bool, Any]:
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return False, _MISSING
            expires_at, value = entry
            if expires_at <= now:
                del self._items[key]
                self._expirations += 1
                return False, _MISSING
            if touch:
                self._items.move_to_end(key)
            return True, value

    def _lookup(self, key: K) -> tuple[bool, Any]:
        found, value = self._peek(key, touch=True)
        with self._lock:
            if found:
                self._hits += 1
            else:
                self._misses += 1
        return found, value

    def get(self, key: K, default: Any = None) -> Any:
        found, value = self._lookup(key)
        return value if found else default

    def contains(self, key: K) -> bool:
        """Check presence without counting a hit or a miss."""
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return False
            expires_at, _ = entry
            if expires_at <= now:
                del self._items[key]
                self._expirations += 1
                return False
            return True

    def set(self, key: K, value: V, *, ttl_ms: int | None = None) -> None:
        ttl = self._default_ttl_ms if ttl_ms is None else int(ttl_ms)
        if ttl <= 0:
            self.delete(key)
            return
        expires_at = self._clock() + ttl / 1000.0
        with self._lock:
            existed = key in self._items
            if existed:
                del self._items[key]
            while len(self._items) >= self._maxsize:
                self._items.popitem(last=False)
                self._evictions += 1
            self._items[key] = (expires_at, value)
            self._sets += 1
            if not existed:
                self._prune_async_locks_locked()

    def delete(self, key: K) -> bool:
        with self._lock:
            return self._items.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._sets = 0
            self._evictions = 0
            self._expirations = 0

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                sets=self._sets,
                evictions=self._evictions,
                expirations=self._expirations,
                size=len(self._items),
                maxsize=self._maxsize,
                default_ttl_ms=self._default_ttl_ms,
            )

    async def get_or_set(
        self,
        key: K,
        factory: Callable[[], Any],
        *,
        ttl_ms: int | None = None,
    ) -> V:
        """Return a cached value, or build it exactly once per key.

        The synchronous get fast path stays lock-free with respect to other
        coroutines; only the miss path serializes on a per-key asyncio lock.
        """
        found, value = self._peek(key, touch=True)
        if found:
            self._record(hit=True)
            return value
        lock = self._async_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._async_locks[key] = lock
        async with lock:
            # Another coroutine may have filled the key while we waited. A
            # waiter that observes the fill counts as a hit, so one logical
            # miss is recorded once and never double-counted.
            found, value = self._peek(key, touch=True)
            if found:
                self._record(hit=True)
                return value
            self._record(hit=False)
            value = await factory()
            self.set(key, value, ttl_ms=ttl_ms)
            return value

    def _record(self, *, hit: bool) -> None:
        with self._lock:
            if hit:
                self._hits += 1
            else:
                self._misses += 1

    def _prune_async_locks_locked(self) -> None:
        # Locks are only held during a miss; dropping unlocked entries keeps a
        # large key space from growing without bound. Callers already waiting
        # keep their reference, so correctness does not depend on the pruning.
        if len(self._async_locks) <= max(16, self._maxsize * 2):
            return
        for key, lock in list(self._async_locks.items()):
            if not lock.locked():
                self._async_locks.pop(key, None)
