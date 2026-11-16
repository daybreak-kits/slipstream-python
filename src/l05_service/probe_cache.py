from __future__ import annotations

from typing import Any

from l01_core import runtime
from l02_infra.ttl_cache import CacheStats, TTLCache
from l05_service import probe

_PROBE_ALL_CACHE: TTLCache[tuple[str, str], dict[str, Any]] = TTLCache(
    maxsize=128, ttl_ms=30_000
)


def _ttl_ms() -> int:
    return int(getattr(runtime.current_settings(), "probe_result_ttl_ms", 30_000) or 0)


def probe_cache_stats() -> dict[str, Any]:
    """Hit/miss counters for the batch probe-all cache."""
    return _PROBE_ALL_CACHE.stats().as_dict()


def reset_probe_cache() -> None:
    _PROBE_ALL_CACHE.clear()
    _PROBE_ALL_CACHE.reset_stats()


async def probe_all_models_cached(
    session: Any,
    channel: Any,
    mode: str = "deep",
    force: bool = False,
) -> dict[str, Any]:
    """Run the batch probe-all path, reusing one result per (channel, mode).

    The manual single-model probe endpoint deliberately bypasses this wrapper:
    clicking one model's probe must always hit the upstream. Batch probe-all is
    the expensive path, so a short TTL is worth the staleness; force=true
    always refreshes the cache entry.
    """
    normalized = "shallow" if mode == "shallow" else "deep"
    key = (channel.id, normalized)
    ttl_ms = _ttl_ms()
    if ttl_ms > 0 and not force:
        cached = _PROBE_ALL_CACHE.get(key)
        if cached is not None:
            result = dict(cached)
            result["cached"] = True
            return result
    result = await probe.probe_all_models(session, channel, normalized, force=force)
    payload = dict(result)
    if ttl_ms > 0:
        _PROBE_ALL_CACHE.set(key, payload, ttl_ms=ttl_ms)
    else:
        _PROBE_ALL_CACHE.delete(key)
    payload["cached"] = False
    return payload
