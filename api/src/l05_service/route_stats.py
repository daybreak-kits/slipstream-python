"""Per-member routing statistics and policy resolution (ADR-077).

The strategy layer reorders same-priority members by observed behaviour:

* stability -- success rate over finished real-chat attempts (probes are
  excluded too: probe health has its own admit_candidate/cooldown path);
* cost -- effective average price per million billed tokens
  (sum(cost_usd) / sum(prompt+completion tokens) * 1e6; unpriced = free =
  0.0, probes excluded so health checks do not drag the price to zero);
* latency -- median TTFA over real chat traffic.

Observation period: a member only influences a tuned ordering after
OBSERVATION_MIN_SUCCESSES successful attempts in the window. Untuned
members stay in the uniform pool so they keep collecting samples; tuned
members are ordered by the strategy metric ahead of that pool, but the
priority layers themselves are never touched (ADR-077: absolute order
stays deterministic).

Under the ADR-072 JSON persistence shape no attempts rows exist, so the
observation gate is never met and the ordering degrades to uniform.

Cache contract mirrors upstream_settings/route_settings: 15s TTL, warmed
at boot, refreshed lazily by the async resolve paths (the only places with
a session) and immediately by save/reset. The snapshot resolve path reads
memory only -- a cold cache there degrades to uniform, never to SQL.

memory 管线（请求会话挂着目录快照）的刷新是 stale-while-revalidate：TTL
过期后回读陈旧值，由过期后的第一个发现者调度后台单飞刷新（single-flight），
聚合 SQL 从请求路径挪到后台短会话里；刷新失败静默保留陈旧值，下一次请求
再重新调度。classic 模式保持原有的同步重载语义不变。
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core.logging import get_logger
from l01_core.timeutil import now_ms
from l04_repo import orm
from l05_service import route_settings
from l05_service.config_catalog import catalog_for_session

logger = get_logger("system.route_stats")

OBSERVATION_MIN_SUCCESSES = 30
STATS_CACHE_TTL_S = 15.0
# Row cap for the raw-sample queries so a huge window cannot drag every
# request row through them; the p50 below is an approximation once truncated.
STATS_ROW_SAMPLE_LIMIT = 20000

_stats_cache: dict[int, tuple[dict[str, "MemberStat"], float]] = {}
_settings_cache: list[Any] = [None, 0.0]
# 后台单飞刷新任务（按统计窗口键控）。必须由本模块持有强引用：事件循环
# 只保留弱引用，挂到模块上任务才不会被中途回收。
_refresh_tasks: dict[int, asyncio.Task[None]] = {}


@dataclass(frozen=True, slots=True)
class MemberStat:
    successes: int = 0
    total: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    ttfa_ms: tuple[int, ...] = ()

    @property
    def tuned(self) -> bool:
        """Past the observation period: enough successes to trust."""
        return self.successes >= OBSERVATION_MIN_SUCCESSES

    @property
    def success_rate(self) -> float:
        return self.successes / self.total if self.total > 0 else 0.0

    @property
    def price_per_million(self) -> float:
        """Effective average price per million billed tokens (0 = free)."""
        if self.tokens <= 0:
            return 0.0
        return self.cost_usd / self.tokens * 1_000_000.0

    @property
    def p50_ttfa_ms(self) -> float | None:
        return statistics.median(self.ttfa_ms) if self.ttfa_ms else None


@dataclass(frozen=True, slots=True)
class RouteOrderPolicy:
    strategy: str = route_settings.DEFAULT_STRATEGY
    stats: Mapping[str, MemberStat] = field(default_factory=dict)


def reset_caches() -> None:
    """Drop every cached value; conftest calls this around each test."""
    _stats_cache.clear()
    _settings_cache[0] = None
    _settings_cache[1] = 0.0
    for task in _refresh_tasks.values():
        task.cancel()
    _refresh_tasks.clear()


def _cached_settings() -> route_settings.RouteSettings | None:
    cached, loaded_at = _settings_cache
    if cached is None or time.monotonic() - loaded_at >= route_settings.CACHE_TTL_S:
        return None
    return cached


def _settings_copy() -> tuple[route_settings.RouteSettings | None, bool]:
    """内存里的设置副本；expired 表示已超过 TTL（值本身可能仍可用）。"""
    cached, loaded_at = _settings_cache
    if cached is None:
        return None, False
    return cached, time.monotonic() - loaded_at >= route_settings.CACHE_TTL_S


def _stats_copy(
    window_minutes: int,
) -> tuple[dict[str, "MemberStat"] | None, bool]:
    """内存里的统计副本；None 表示从未加载过（冷缓存）。"""
    entry = _stats_cache.get(window_minutes)
    if entry is None:
        return None, False
    return entry[0], time.monotonic() - entry[1] >= STATS_CACHE_TTL_S


def _schedule_cache_refresh(
    factory: async_sessionmaker[AsyncSession], window_minutes: int
) -> None:
    """单飞调度后台刷新：过期后第一个发现者建任务，其余请求沿用陈旧值。

    done 的旧任务在下次调度时被替换，保证任意时刻每个窗口至多一个刷新
    任务在飞。
    """
    existing = _refresh_tasks.get(window_minutes)
    if existing is not None and not existing.done():
        return
    _refresh_tasks[window_minutes] = asyncio.create_task(
        _refresh_route_caches(factory, window_minutes)
    )


async def _refresh_route_caches(
    factory: async_sessionmaker[AsyncSession], window_minutes: int
) -> None:
    """后台刷新路由设置与统计：请求路径已经拿着陈旧值在服务，这里失败
    只告警、保留陈旧值，下一次请求发现仍过期会再调度一轮。"""
    try:
        async with factory() as session:
            settings = await route_settings.get_route_settings(session)
            _settings_cache[0] = settings
            _settings_cache[1] = time.monotonic()
            stats = await _load_stats(session, window_minutes)
            _stats_cache[window_minutes] = (stats, time.monotonic())
    except Exception:  # noqa: BLE001 - 后台刷新失败不能打扰正在服务的请求
        logger.warning(
            "路由策略后台刷新失败，沿用陈旧统计 window=%s",
            window_minutes,
            exc_info=True,
        )


def _stats_for_window(window_minutes: int) -> dict[str, MemberStat]:
    entry = _stats_cache.get(window_minutes)
    if entry is None or time.monotonic() - entry[1] >= STATS_CACHE_TTL_S:
        return {}
    return entry[0]


async def _ensure_settings(
    session: AsyncSession,
) -> route_settings.RouteSettings:
    cached = _cached_settings()
    if cached is not None:
        return cached
    settings = await route_settings.get_route_settings(session)
    _settings_cache[0] = settings
    _settings_cache[1] = time.monotonic()
    return settings


async def ensure_stats(
    session: AsyncSession, window_minutes: int,
) -> dict[str, MemberStat]:
    entry = _stats_cache.get(window_minutes)
    if entry is not None and time.monotonic() - entry[1] < STATS_CACHE_TTL_S:
        return entry[0]
    stats = await _load_stats(session, window_minutes)
    _stats_cache[window_minutes] = (stats, time.monotonic())
    return stats


def current_policy() -> RouteOrderPolicy:
    """Global policy from memory only (no session in the snapshot path)."""
    settings = _cached_settings() or route_settings.RouteSettings()
    return RouteOrderPolicy(
        strategy=settings.strategy,
        stats=_stats_for_window(settings.window_minutes),
    )


def _effective_fields(
    combo: Any | None, settings: route_settings.RouteSettings,
) -> tuple[str, int]:
    """Combo-level override wins over the global policy, field by field.

    An empty route_strategy or a 0 route_window_minutes means "follow the
    global value" for that field, so deployments can override one knob
    without pinning the other.
    """
    strategy = settings.strategy
    window = settings.window_minutes
    if combo is None:
        return strategy, window
    override_strategy = str(getattr(combo, "route_strategy", "") or "")
    if override_strategy.strip():
        strategy = route_settings.normalize_strategy(override_strategy)
    try:
        override_window = int(getattr(combo, "route_window_minutes", 0) or 0)
    except (TypeError, ValueError):
        override_window = 0
    if route_settings.MIN_WINDOW_MINUTES <= override_window <= route_settings.MAX_WINDOW_MINUTES:
        window = override_window
    return strategy, window


def policy_for_combo(combo: Any) -> RouteOrderPolicy:
    """Global policy for a combo, from memory only (snapshot path)."""
    settings = _cached_settings() or route_settings.RouteSettings()
    strategy, window = _effective_fields(combo, settings)
    return RouteOrderPolicy(
        strategy=strategy,
        stats=_stats_for_window(window),
    )


async def resolve_policy(
    session: AsyncSession, combo: Any = None,
) -> RouteOrderPolicy:
    """Resolve the effective policy, refreshing stale caches on the way.

    The async resolve paths own the session, so this is where the lazy TTL
    refresh happens; the cached snapshot path never reaches here.

    classic 会话（没挂目录快照）保持原有同步重载；memory 会话走
    stale-while-revalidate：TTL 过期后回读陈旧值并调度后台单飞刷新，
    聚合 SQL 不再落在请求路径上。冷缓存（从未加载过）仍同步加载一次，
    否则没有值可回读。
    """
    catalog = catalog_for_session(session)
    stale = False
    settings, settings_expired = _settings_copy()
    if settings is None or (settings_expired and catalog is None):
        settings = await _ensure_settings(session)
        settings_expired = False
    elif settings_expired:
        stale = True
    strategy, window = _effective_fields(combo, settings)
    stats, stats_expired = _stats_copy(window)
    if stats is None or (stats_expired and catalog is None):
        stats = await ensure_stats(session, window)
        stats_expired = False
    elif stats_expired:
        stale = True
    if stale and catalog is not None:
        _schedule_cache_refresh(catalog.session_factory, window)
    return RouteOrderPolicy(strategy=strategy, stats=stats)


async def refresh_if_stale(session: AsyncSession) -> None:
    """Boot/lifespan warm-up: settings plus the stats for its window."""
    settings = await _ensure_settings(session)
    await ensure_stats(session, settings.window_minutes)


async def _load_stats(
    session: AsyncSession, window_minutes: int,
) -> dict[str, MemberStat]:
    cutoff = now_ms() - window_minutes * 60_000

    attempt_rows = await session.execute(
        select(
            orm.AttemptORM.channel_model_id,
            orm.AttemptORM.status,
            func.count(),
        )
        .join(orm.RequestLogORM, orm.RequestLogORM.id == orm.AttemptORM.log_id)
        .where(
            orm.AttemptORM.skipped == False,  # noqa: E712
            orm.AttemptORM.channel_model_id.isnot(None),
            # Real chat traffic only: probe attempts must never satisfy the
            # observation gate (probe health has its own admit/cooldown path),
            # keeping this cut symmetric with the source == chat filters below.
            orm.RequestLogORM.source == "chat",
            # RequestLogORM.created_at is a second-level proxy for the attempt
            # event time (ADR decision 3 names the attempt event time itself).
            orm.RequestLogORM.created_at >= cutoff,
        )
        .group_by(orm.AttemptORM.channel_model_id, orm.AttemptORM.status)
    )

    usage_rows = await session.execute(
        select(
            orm.RequestLogORM.final_channel_model_id,
            func.count(),
            func.coalesce(func.sum(orm.RequestLogORM.cost_usd), 0.0),
            func.coalesce(func.sum(
                orm.RequestLogORM.prompt_tokens
                + orm.RequestLogORM.completion_tokens
            ), 0),
        )
        .where(
            # Same proxy caveat: created_at stands in for the request event
            # time, while ADR decision 3 names the attempt event time.
            orm.RequestLogORM.created_at >= cutoff,
            orm.RequestLogORM.source == "chat",
            orm.RequestLogORM.final_channel_model_id.isnot(None),
        )
        .group_by(orm.RequestLogORM.final_channel_model_id)
        # Row guard: a huge window must not drag every request through here.
        .limit(STATS_ROW_SAMPLE_LIMIT)
    )

    ttfa_rows = await session.execute(
        select(
            orm.RequestLogORM.final_channel_model_id,
            orm.RequestLogORM.ttfa_ms,
        )
        .where(
            orm.RequestLogORM.created_at >= cutoff,
            orm.RequestLogORM.source == "chat",
            orm.RequestLogORM.ttfa_ms.isnot(None),
            orm.RequestLogORM.final_channel_model_id.isnot(None),
        )
        # Row guard for huge windows; p50 is approximate under truncation.
        .limit(STATS_ROW_SAMPLE_LIMIT)
    )

    stats: dict[str, MemberStat] = {}
    for member_id, status, count in attempt_rows:
        stat = stats.setdefault(str(member_id), MemberStat())
        total = stat.total + int(count)
        successes = stat.successes + (int(count) if status == "success" else 0)
        stats[str(member_id)] = replace(
            stat, total=total, successes=successes
        )

    for member_id, count, cost, tokens in usage_rows:
        stat = stats.setdefault(str(member_id), MemberStat())
        stats[str(member_id)] = replace(
            stat,
            cost_usd=stat.cost_usd + float(cost or 0.0),
            tokens=stat.tokens + int(tokens or 0),
        )
        # A member with usage but no attempt rows (legacy rows from before
        # the attempts split) still needs a total for the success rate.
        if stat.total == 0:
            stats[str(member_id)] = replace(
                stats[str(member_id)], total=int(count)
            )

    ttfa_by_member: dict[str, list[int]] = {}
    for member_id, ttfa in ttfa_rows:
        ttfa_by_member.setdefault(str(member_id), []).append(int(ttfa))
    for member_id, samples in ttfa_by_member.items():
        stat = stats.setdefault(str(member_id), MemberStat())
        stats[str(member_id)] = replace(
            stat, ttfa_ms=tuple(samples)
        )

    return stats
