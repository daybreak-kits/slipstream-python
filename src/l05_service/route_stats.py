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
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.timeutil import now_ms
from l04_repo import orm
from l05_service import route_settings

OBSERVATION_MIN_SUCCESSES = 30
STATS_CACHE_TTL_S = 15.0
# Row cap for the raw-sample queries so a huge window cannot drag every
# request row through them; the p50 below is an approximation once truncated.
STATS_ROW_SAMPLE_LIMIT = 20000

_stats_cache: dict[int, tuple[dict[str, "MemberStat"], float]] = {}
_settings_cache: list[Any] = [None, 0.0]


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


def _cached_settings() -> route_settings.RouteSettings | None:
    cached, loaded_at = _settings_cache
    if cached is None or time.monotonic() - loaded_at >= route_settings.CACHE_TTL_S:
        return None
    return cached


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
    """
    settings = await _ensure_settings(session)
    strategy, window = _effective_fields(combo, settings)
    stats = await ensure_stats(session, window)
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
