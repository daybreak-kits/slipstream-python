from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core.config import Settings
from l01_core.logging import get_logger
from l01_core.timeutil import now_ms
from l04_repo import orm, repo
from l05_service.app_settings import BodyLogSettings, get_body_settings
from l05_service.channel_models import is_model_enabled
from l05_service.groups import GroupMember, ImplicitGroup, is_bare_call_model

logger = get_logger("system.catalog_cache")

# The request path must never open a read transaction. A Catalog instance is
# bound to the app's session factory; every request session carries a pointer to
# it in session.info and the read ports use that pointer instead of SQL.
_CATALOG_SESSION_KEY = "catalog"
_BOUND_CATALOGS: "weakref.WeakKeyDictionary[Any, Catalog]" = weakref.WeakKeyDictionary()


@dataclass(slots=True)
class CatalogSnapshot:
    """One immutable generation of routing/auth configuration.

    ORM rows are detached after the load transaction closes. They are read-only
    here: management writes always land in SQLite first, then the version poll
    replaces the whole snapshot atomically. Breaker state is the one exception:
    it is mutation in flight (see routing.record_failure), so the cached rows are
    patched in place after a successful conditional UPDATE.
    """

    version: str
    loaded_at_ms: int
    channels: dict[str, orm.ChannelORM]
    models: dict[str, orm.ChannelModelORM]
    direct: dict[tuple[str, str], tuple[orm.ChannelORM, orm.ChannelModelORM]]
    models_by_call_model: dict[str, tuple[orm.ChannelModelORM, ...]]
    combos_by_id: dict[str, orm.ComboORM]
    combos_by_name: dict[str, orm.ComboORM]
    targets_by_combo: dict[str, tuple[orm.ComboTargetORM, ...]]
    groups: dict[str, ImplicitGroup]
    api_keys: dict[str, tuple[orm.ApiKeyORM, orm.UserORM]]
    body_settings: BodyLogSettings


class Catalog:
    """Per-process read cache for channels, models, combos, groups and keys."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._snapshot: CatalogSnapshot | None = None
        self._task: asyncio.Task[None] | None = None
        self._dirty_keys: dict[str, int] = {}
        self._reload_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._dirty = False

    async def startup(self) -> None:
        async with self._session_factory() as session:
            await repo.ensure_catalog_version(session)
        await self.reload()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._poll_loop())

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        self._stop.set()
        if task is not None:
            try:
                # Let the loop finish an in-flight DB operation and close its
                # session instead of cancelling it mid-transaction: a cancelled
                # session can keep the SQLite file handle open past engine
                # disposal on Windows.
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception:  # noqa: BLE001
                logger.warning("catalog poll task stopped with an error", exc_info=True)
        await self._flush_key_touches()

    @property
    def snapshot(self) -> CatalogSnapshot | None:
        return self._snapshot

    @property
    def version(self) -> str | None:
        snapshot = self._snapshot
        return snapshot.version if snapshot is not None else None

    @property
    def loaded_at_ms(self) -> int | None:
        snapshot = self._snapshot
        return snapshot.loaded_at_ms if snapshot is not None else None

    def lookup_api_key(self, key_hash: str) -> tuple[orm.ApiKeyORM, orm.UserORM] | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.api_keys.get(key_hash)

    def touch_api_key(self, key: orm.ApiKeyORM) -> None:
        """Mark a key used without SQL; the poll loop batches the UPDATE."""
        now = now_ms()
        fresh = (
            key.last_used_at is not None
            and now - key.last_used_at < repo.API_KEY_TOUCH_INTERVAL_MS
        )
        if fresh:
            return
        key.last_used_at = now
        if key.id:
            self._dirty_keys[key.id] = now

    async def reload(self) -> CatalogSnapshot:
        snapshot = await self._load()
        async with self._reload_lock:
            self._snapshot = snapshot
        return snapshot

    async def refresh(self) -> None:
        """Force a reload (tests and operators); version polling uses this too."""
        self._dirty = False
        await self.reload()

    def mark_dirty(self) -> None:
        """Record an in-process config write so the next read reloads once."""
        self._dirty = True

    async def ensure_fresh(self) -> None:
        """Apply a local committed write before the next read.

        Steady state is a flag check and no SQL. Only the first request after a
        management write pays one full reload; the version poll still covers
        changes made by another process.
        """
        while self._dirty:
            self._dirty = False
            try:
                await self.reload()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("catalog dirty reload failed", exc_info=True)
                break

    async def _read_version(self) -> str:
        async with self._session_factory() as session:
            return await repo.get_catalog_version(session) or ""

    async def _load(self) -> CatalogSnapshot:
        async with self._session_factory() as session:
            channels = list(await repo.list_channels(session))
            models = list(await repo.list_all_channel_models(session))
            combos = list(await repo.list_combos(session))
            targets = list(await repo.list_all_combo_targets(session))
            keys = list(await repo.list_api_keys(session))
            users = list(await repo.list_users(session))
            body_settings = await get_body_settings(session)
            version = await repo.get_catalog_version(session) or ""

        channel_by_id = {channel.id: channel for channel in channels}
        model_by_id = {model.id: model for model in models}
        direct: dict[tuple[str, str], tuple[orm.ChannelORM, orm.ChannelModelORM]] = {}
        models_by_call: dict[str, list[orm.ChannelModelORM]] = {}
        for model in models:
            models_by_call.setdefault(model.call_model, []).append(model)
            channel = channel_by_id.get(model.channel_id)
            if channel is not None:
                direct.setdefault((channel.slug, model.call_model), (channel, model))

        # Implicit groups: mirror groups.list_groups exactly (permanent bans and
        # non-bare rows excluded, timed-banned rows kept for lazy expiry, members in
        # descending channel priority then slug/created_at).
        grouped: dict[str, list[GroupMember]] = {}
        for model in models:
            if not is_bare_call_model(model.call_model):
                continue
            if not is_model_enabled(model, now_ms()) and model.banned_until is None:
                continue
            channel = channel_by_id.get(model.channel_id)
            if channel is None:
                continue
            grouped.setdefault(model.call_model, []).append(
                GroupMember(channel=channel, model=model)
            )
        groups: dict[str, ImplicitGroup] = {}
        for name in sorted(grouped):
            members = sorted(
                grouped[name],
                key=lambda member: (
                    -int(member.channel.priority or 1),
                    member.channel.slug,
                    int(member.model.created_at or 0),
                ),
            )
            groups[name] = ImplicitGroup(name=name, members=tuple(members))

        targets_by_combo: dict[str, list[orm.ComboTargetORM]] = {}
        for target in targets:
            targets_by_combo.setdefault(target.combo_id, []).append(target)

        user_by_id = {user.id: user for user in users}
        api_keys: dict[str, tuple[orm.ApiKeyORM, orm.UserORM]] = {}
        for key in keys:
            user = user_by_id.get(key.user_id)
            if user is not None:
                api_keys[key.key_hash] = (key, user)

        return CatalogSnapshot(
            version=version,
            loaded_at_ms=now_ms(),
            channels=channel_by_id,
            models=model_by_id,
            direct=direct,
            models_by_call_model={
                name: tuple(rows) for name, rows in models_by_call.items()
            },
            combos_by_id={combo.id: combo for combo in combos},
            combos_by_name={combo.name: combo for combo in combos},
            targets_by_combo={
                combo_id: tuple(rows) for combo_id, rows in targets_by_combo.items()
            },
            groups=groups,
            api_keys=api_keys,
            body_settings=body_settings,
        )

    async def _poll_loop(self) -> None:
        interval = max(0.005, int(self._settings.catalog_poll_interval_ms) / 1000.0)
        ttl = max(0, int(self._settings.catalog_ttl_ms))
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            try:
                version = await self._read_version()
                current = self._snapshot
                if current is None or version != current.version:
                    await self.reload()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                # Version polling is best effort. If it stays broken, a full
                # reload on the TTL keeps the process from serving stale config
                # forever, and requests keep flowing on the last good snapshot.
                current = self._snapshot
                stale = current is None or now_ms() - current.loaded_at_ms >= ttl
                if stale:
                    logger.warning(
                        "catalog version poll failed; reloading on TTL: %s", error
                    )
                    try:
                        await self.reload()
                    except Exception:  # noqa: BLE001
                        logger.warning("catalog TTL reload failed", exc_info=True)
            try:
                await self._flush_key_touches()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("catalog key-touch flush failed", exc_info=True)

    async def flush_key_touches(self) -> None:
        """Persist buffered last_used_at values (admin reads call this first)."""
        await self._flush_key_touches()

    async def _flush_key_touches(self) -> None:
        if not self._dirty_keys:
            return
        pending = dict(self._dirty_keys)
        async with self._session_factory() as session:
            await repo.batch_touch_api_keys(session, pending)
        for key_id, touched_at in pending.items():
            if self._dirty_keys.get(key_id) == touched_at:
                self._dirty_keys.pop(key_id, None)


def bind_catalog(
    session_factory: async_sessionmaker[AsyncSession],
    catalog: Catalog,
    *,
    engine: Any | None = None,
) -> None:
    """Bind a catalog to a session factory for non-request callers."""
    _BOUND_CATALOGS[session_factory] = catalog
    if engine is not None:
        repo.bind_write_catalog(engine, catalog)


def catalog_for_session(session: AsyncSession) -> Catalog | None:
    """The catalog this session should read/write the snapshot through.

    Request sessions carry an explicit pointer (``l06_api.deps.get_db``). A
    session opened outside a request -- the stream finalizer in
    ``l05_service.chat`` is the one that matters -- has none, yet still has to
    keep the shared snapshot in step with the breaker row it just committed.
    Fall back to the engine binding, which is how ``repo`` already reaches the
    catalog for write invalidation.
    """
    try:
        bound = session.info.get(_CATALOG_SESSION_KEY)
    except Exception:  # noqa: BLE001
        return None
    if bound is not None:
        return bound
    return _catalog_for_engine(getattr(session, "bind", None))


def _catalog_for_engine(engine: Any) -> Catalog | None:
    if engine is None:
        return None
    sync_engine = getattr(engine, "sync_engine", engine)
    try:
        return repo.write_catalog_for(sync_engine)
    except Exception:  # noqa: BLE001
        return None

