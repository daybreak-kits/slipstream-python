from __future__ import annotations

import json
import math
import random
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import ErrorCode, OpenAIError
from l01_core.timeutil import now_ms
from l03_domain.breaker import (
    HALF_OPEN_PROBE_LEASE_MS,
    PERMANENT_COOLDOWN_MS,
    apply_to_model,
    on_failure,
    on_reset,
    on_success,
    parse_breaker_config,
    state_from_model,
    state_to_values,
    tick,
)
from l03_domain.failover import EMPTY_RULES, FailoverRules, parse_failover_rules
from l03_domain.schedule import HORIZON_DAYS, Schedule, is_offline, next_available_ms
from l04_repo import orm, repo
from l05_service.channel_models import (
    SKIP_COOLDOWN_WINDOW,
    SKIP_MODEL_BANNED,
    SKIP_MODEL_COOLDOWN,
    ban_reason,
    banned_until_ms,
    is_model_enabled,
    schedule_for,
)
from l05_service.config_catalog import CatalogSnapshot, catalog_for_session
from l05_service.groups import ImplicitGroup, get_group
from l05_service import route_stats

OPENAI_PROTOCOL = "openai_chat"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One callable channel model plus the priority path that selected it.

    A flat group/combo uses ``(priority,)``; nested combos use the full path,
    e.g. ``(1, 2)`` for a P2 member inside a P1 child combo. Sorting is
    lexicographic over the path, so the whole subtree of the earlier root layer
    runs before the next root layer.
    """

    channel: orm.ChannelORM
    model: orm.ChannelModelORM
    priority: int = 1
    priority_path: tuple[int, ...] = ()
    # False when the combo member itself is disabled. Kept in the candidate
    # list so the attempts timeline can explain the skip.
    member_enabled: bool = True


@dataclass(frozen=True, slots=True)
class CallTarget:
    kind: Literal["direct", "combo"]
    name: str
    candidates: tuple[Candidate, ...]
    # 只有组合带 origin：manual = combos 表里的显式组合（可编辑）；auto = 查询期由
    # 同名 channel_models 派生的隐式分组（只读，成员优先级取 channels.priority）。
    origin: Literal["manual", "auto"] | None = None
    group: ImplicitGroup | None = None
    combo: orm.ComboORM | None = None
    # Only the top-level combo owns rounds and rules. Direct calls and implicit
    # groups always run a single round with transport-default switching.
    max_attempts: int = 1
    failover_rules: FailoverRules = field(default_factory=lambda: EMPTY_RULES)


def split_direct_model(value: str | None) -> tuple[str, str] | None:
    """Split ``channel_slug/call_model`` at the first slash; call_model may contain more."""
    text = (value or "").strip()
    if "/" not in text:
        return None
    slug, call_model = text.split("/", 1)
    if not slug or not call_model:
        return None
    return slug, call_model


def _not_found(requested: str, suggestions: Iterable[str] = ()) -> OpenAIError:
    message = f"模型 {requested} 不存在、已封禁或无法调用"
    hints = [item for item in suggestions if item]
    if hints:
        message += "；可用直连：" + "、".join(hints)
    return OpenAIError(404, message, ErrorCode.MODEL_NOT_FOUND, "invalid_request_error")


def _no_available(requested: str) -> OpenAIError:
    return OpenAIError(
        503,
        f"模型 {requested} 当前没有可用渠道",
        ErrorCode.NO_AVAILABLE_CHANNEL,
        "server_error",
    )


def retry_after_s(at_ms: int | None) -> int | None:
    """Whole seconds a client should wait before retrying (ADR-070 §3.1)."""
    if at_ms is None:
        return None
    return max(1, math.ceil((int(at_ms) - now_ms()) / 1000))


def scheduled_offline_error(requested: str, next_available_at: int | None) -> OpenAIError:
    if next_available_at is None:
        # 排期视野是 HORIZON_DAYS 天（只有到这之外才可能重新开门），不是「本周」。
        message = (
            f"模型 {requested} 的所有候选均处于排期冷却窗口，"
            f"且未来 {HORIZON_DAYS} 天内没有可恢复的时间"
        )
    else:
        message = f"模型 {requested} 的所有候选均处于排期冷却窗口"
    details: dict[str, Any] = {
        "next_available_at": next_available_at,
    }
    seconds = retry_after_s(next_available_at)
    if seconds is not None:
        details["retry_after_s"] = seconds
    return OpenAIError(
        503,
        message,
        ErrorCode.MODEL_SCHEDULED_OFFLINE,
        "server_error",
        details=details,
    )


def model_cooldown_error(requested: str, banned_until: int | None) -> OpenAIError:
    """503 for a model that is cooling down and revives on its own.

    A permanent ban stays a 404: that model does not exist for clients. A timed
    cooldown is temporary, so the caller gets the recovery moment in the body and
    in ``Retry-After`` (ADR-070 §3.1).
    """
    seconds = retry_after_s(banned_until)
    message = f"模型 {requested} 处于冷却中"
    if seconds is not None:
        message += f"，预计 {seconds} 秒后恢复"
    details: dict[str, Any] = {
        "next_available_at": banned_until,
    }
    if seconds is not None:
        details["retry_after_s"] = seconds
    return OpenAIError(503, message, ErrorCode.MODEL_COOLDOWN, "server_error", details=details)


def _direct_model_gate(
    requested: str, channel: orm.ChannelORM, model: orm.ChannelModelORM
) -> None:
    """Ban axis for a direct call: 404 when banned for good, 503 while cooling."""
    if not channel.enabled:
        raise _not_found(requested)
    reason = ban_reason(model, now_ms())
    if reason is None:
        return
    if reason == SKIP_MODEL_COOLDOWN:
        raise model_cooldown_error(requested, banned_until_ms(model, now_ms()))
    raise _not_found(requested)


def _cooldown_only(requested: str, rows: list[tuple[Any, orm.ChannelModelORM]]) -> None:
    """Raise 503 when every row that could serve the name is merely cooling."""
    if not rows:
        return
    at = now_ms()
    if any(ban_reason(model, at) != SKIP_MODEL_COOLDOWN for _channel, model in rows):
        return
    moments = [banned_until_ms(model, at) for _channel, model in rows]
    # 冷却行按定义都带到期时刻；真出现算不出的行时，回答 503（不带恢复时刻）
    # 也不能让空集 max 把 500 抛给调用方。
    known = [moment for moment in moments if moment is not None]
    raise model_cooldown_error(requested, max(known) if known else None)


def cooldown_recovery_at(
    models: Iterable[orm.ChannelModelORM], at_ms: int | None = None
) -> int | None:
    """The latest ban expiry among cooling rows: all of them must revive first."""
    moment = now_ms() if at_ms is None else at_ms
    expiries = [
        banned_until_ms(model, moment)
        for model in models
        if ban_reason(model, moment) == SKIP_MODEL_COOLDOWN
    ]
    expiries = [value for value in expiries if value is not None]
    return max(expiries) if expiries else None


async def _direct_suggestions(session: AsyncSession, name: str) -> list[str]:
    channels = {channel.id: channel for channel in await repo.list_channels(session)}
    suggestions: list[str] = []
    for model in await repo.list_all_channel_models(session):
        if model.call_model != name:
            continue
        channel = channels.get(model.channel_id)
        if channel is None:
            continue
        suggestions.append(f"{channel.slug}/{model.call_model}")
    return sorted(suggestions)


async def _call_model_candidate(session: AsyncSession, requested: str) -> CallTarget:
    """SQL twin of _cached_call_model_target for native ids that contain "/"."""
    rows = await repo.list_enabled_channel_models_by_call_model(session, requested)
    openai_rows = [
        (channel, model) for model, channel in rows if channel.protocol == OPENAI_PROTOCOL
    ]
    usable = [
        (channel, model)
        for channel, model in openai_rows
        if channel.enabled and ban_reason(model, now_ms()) is None
    ]
    if len(usable) == 1:
        channel, model = usable[0]
        return CallTarget(
            kind="direct",
            name=requested,
            candidates=(
                Candidate(channel=channel, model=model, priority=int(channel.priority or 1)),
            ),
        )
    _cooldown_only(
        requested,
        [(channel, model) for channel, model in openai_rows if channel.enabled],
    )
    raise _not_found(
        requested, sorted(f"{channel.slug}/{model.call_model}" for model, channel in rows)
    )


async def _direct_candidate(session: AsyncSession, requested: str) -> CallTarget:
    parsed = split_direct_model(requested)
    if parsed is None:
        raise _not_found(requested)
    slug, call_model = parsed
    resolved = await repo.get_channel_and_model_by_call(session, slug, call_model)
    if resolved is None:
        return await _call_model_candidate(session, requested)
    channel, model = resolved
    _direct_model_gate(requested, channel, model)
    if channel.protocol != OPENAI_PROTOCOL:
        raise OpenAIError(
            400,
            f"渠道 {slug} 的协议为 {channel.protocol}，本版本直连仅支持 OpenAI 兼容协议",
            ErrorCode.NOT_IMPLEMENTED,
            "invalid_request_error",
        )
    return CallTarget(
        kind="direct",
        name=requested,
        candidates=(Candidate(channel=channel, model=model, priority=int(channel.priority or 1)),),
    )


def _candidate_available(candidate: Candidate) -> bool:
    return bool(
        candidate.channel.enabled
        and is_model_enabled(candidate.model, now_ms())
        and candidate.channel.protocol == OPENAI_PROTOCOL
    )


def priority_path(candidate: Candidate) -> tuple[int, ...]:
    return candidate.priority_path or (int(candidate.priority or 1),)


def schedule_for_model(model: orm.ChannelModelORM) -> Schedule | None:
    """The model's parsed schedule, or ``None`` when it has no windows.

    Parsing is memoised on the stored JSON text, so a per-attempt call does no
    JSON decode and no date arithmetic (ADR-070 §2.3).
    """
    return schedule_for(model)


def _schedule_offline(model: orm.ChannelModelORM, at_ms: int) -> bool:
    schedule = schedule_for_model(model)
    return bool(schedule is not None and is_offline(at_ms, schedule))


def schedule_next_available_at(model: orm.ChannelModelORM, at_ms: int) -> int | None:
    """When a currently scheduled-off model may next become callable."""
    schedule = schedule_for_model(model)
    if schedule is None:
        return at_ms
    return next_available_ms(at_ms, schedule)


def next_available_at(candidates: Iterable[Candidate], at_ms: int) -> int | None:
    moments = [
        moment
        for moment in (
            schedule_next_available_at(candidate.model, at_ms) for candidate in candidates
        )
        if moment is not None
    ]
    return min(moments) if moments else None


def _static_unavailable(candidate: Candidate, at_ms: int) -> str | None:
    """Checks that do not involve breaker timers or counters."""
    if not candidate.member_enabled:
        return "MEMBER_DISABLED"
    if not candidate.channel.enabled:
        return "CHANNEL_DISABLED"
    banned = ban_reason(candidate.model, at_ms)
    if banned is not None:
        return banned
    if candidate.channel.protocol != OPENAI_PROTOCOL:
        return "PROTOCOL_UNSUPPORTED"
    if _schedule_offline(candidate.model, at_ms):
        return SKIP_COOLDOWN_WINDOW
    return None


def unavailable_reason(candidate: Candidate, at_ms: int | None = None) -> str | None:
    """Why a candidate cannot be called right now, or ``None`` when it can.

    This is the read-only view used for display/tests. Runtime admission uses
    :func:`admit_candidate`, which also promotes an expired cooldown and claims
    the single half-open probe slot.
    """
    now = at_ms if at_ms is not None else now_ms()
    reason = _static_unavailable(candidate, now)
    if reason is not None:
        return reason

    health = (candidate.model.health_status or "normal").lower()
    if health == "disabled":
        return SKIP_MODEL_BANNED
    current, _ = tick(state_from_model(candidate.model), now)
    if current.health == "fault":
        return "CIRCUIT_FAULT"
    if current.health in {"open", "half_open"}:
        return "CIRCUIT_OPEN"
    cooldown_until = candidate.model.cooldown_until
    if cooldown_until is not None and cooldown_until > now:
        # A rate-limit cooldown parks a healthy model by time only.
        return "RATE_LIMIT_COOLDOWN"
    return None


async def admit_candidate(
    session: AsyncSession, candidate: Candidate, at_ms: int | None = None
) -> str | None:
    """Admit one call, applying time-driven breaker transitions.

    ``open`` whose cooldown elapsed becomes ``half_open`` and this call claims
    the only probe slot. A row that is already ``half_open`` belongs to another
    in-flight probe, so it is skipped. ``fault`` whose timer elapsed is fully
    reset. Returns the skip reason, or ``None`` when the call may proceed.
    """
    now = at_ms if at_ms is not None else now_ms()
    reason = _static_unavailable(candidate, now)
    if reason is not None:
        return reason

    # With a catalog snapshot the row is already in memory. Healthy models are
    # the steady state and touch no SQL at all. Breaker transitions below are
    # rare and still use conditional UPDATEs; they patch the cached row after
    # the write so later requests observe the new breaker state.
    catalog = catalog_for_session(session)
    cached = catalog is not None and catalog.snapshot is not None
    if cached:
        model = candidate.model
        # Breaker/probe columns are deliberately excluded from the catalog
        # version, so an out-of-band breaker edit (operator reset, test fixture)
        # does not trigger a reload. When the cached row claims a non-normal
        # state, confirm it against the database before rejecting the call; a
        # healthy row still costs zero SQL.
        if (model.health_status or "normal").lower() != "normal":
            fresh = await session.get(
                orm.ChannelModelORM, candidate.model.id, populate_existing=True
            )
            if fresh is not None:
                apply_to_model(model, state_from_model(fresh))
    else:
        # resolve_target just loaded this row in the same session, so this is
        # usually an identity-map hit (no SQL). If the row is stale, the
        # conditional UPDATE below is still the arbiter for open/fault states.
        model = await session.get(orm.ChannelModelORM, candidate.model.id)
    if model is None:
        return SKIP_MODEL_BANNED
    banned = ban_reason(model, now_ms())
    if banned is not None:
        return banned
    raw_health = (model.health_status or "normal").lower()
    if raw_health == "disabled":
        return SKIP_MODEL_BANNED
    # The overwhelmingly common case is a healthy model. Do not open a write
    # transaction here: this function runs for every attempt, and an
    # unconditional UPDATE + COMMIT made the hot path pay two SQLite round
    # trips even though neither conditional UPDATE could match a row.
    if raw_health == "normal":
        cooldown_until = model.cooldown_until
        if cooldown_until is not None and cooldown_until > now:
            # A rate-limit cooldown (429) parks a healthy model by time only:
            # no breaker counters, automatic recovery once the moment passes.
            return "RATE_LIMIT_COOLDOWN"
        return None
    if raw_health == "half_open":
        # A probe normally owns the single slot until it reports back. If it never
        # did (cancelled stream, crash, dev-server reload) the lease expires and the
        # next request must be able to recover the model instead of skipping it forever.
        current, released = tick(state_from_model(model), now)
        if not released or current.health != "normal":
            return "CIRCUIT_OPEN"
        freed = await repo.release_expired_half_open(
            session,
            model.id,
            now=now,
            lease_ms=HALF_OPEN_PROBE_LEASE_MS,
            values=state_to_values(current),
        )
        if cached:
            if not freed:
                return "CIRCUIT_OPEN"
            apply_to_model(model, current)
            return None
        model = await session.get(
            orm.ChannelModelORM, candidate.model.id, populate_existing=True
        )
        if model is None:
            return SKIP_MODEL_BANNED
        banned = ban_reason(model, now_ms())
        if banned is not None:
            return banned
        health = (model.health_status or "normal").lower()
        if health == "fault":
            return "CIRCUIT_FAULT"
        if health == "open" or (health == "half_open" and not freed):
            return "CIRCUIT_OPEN"
        return None
    if raw_health not in {"open", "fault"}:
        return None

    if raw_health == "fault":
        if model.fault_until is None or model.fault_until > now:
            return "CIRCUIT_FAULT"
        # Conditional UPDATE so two concurrent requests cannot both recover
        # the same fault. The loser sees the already-normal row on reload.
        reset_state = on_reset()
        reset = await repo.reset_expired_fault(
            session, model.id, now=now, values=state_to_values(reset_state)
        )
        if cached:
            if not reset:
                return "CIRCUIT_FAULT"
            apply_to_model(model, reset_state)
            return None
        model = await session.get(
            orm.ChannelModelORM, candidate.model.id, populate_existing=True
        )
        if model is None:
            return SKIP_MODEL_BANNED
        banned = ban_reason(model, now_ms())
        if banned is not None:
            return banned
        health = (model.health_status or "normal").lower()
        if health == "fault":
            return "CIRCUIT_FAULT"
        if health in {"open", "half_open"}:
            return "CIRCUIT_OPEN"
        return None

    # raw_health == "open": only an expired cooldown may promote the row and
    # claim the single half-open probe slot.
    if model.cooldown_until is None or model.cooldown_until > now:
        return "CIRCUIT_OPEN"
    # Derive the promoted row from the breaker state so half_open_since and every
    # other lease column follow the same mapping as the other branches.
    promoted, _ = tick(state_from_model(model), now)
    # The repo helper commits before returning, so the write lock is gone
    # before streaming starts.
    probe_claimed = bool(
        await repo.claim_half_open_probe(
            session, model.id, now=now, values=state_to_values(promoted)
        )
    )
    if cached:
        if not probe_claimed:
            return "CIRCUIT_OPEN"
        apply_to_model(model, promoted)
        return None
    model = await session.get(
        orm.ChannelModelORM, candidate.model.id, populate_existing=True
    )
    if model is None:
        return SKIP_MODEL_BANNED
    banned = ban_reason(model, now_ms())
    if banned is not None:
        return banned
    health = (model.health_status or "normal").lower()
    if health == "fault":
        return "CIRCUIT_FAULT"
    if health == "open":
        return "CIRCUIT_OPEN"
    if health == "half_open" and not probe_claimed:
        # The probe slot belongs to the request that promoted open -> half_open.
        return "CIRCUIT_OPEN"
    return None


async def record_failure(
    session: AsyncSession,
    candidate: Candidate,
    *,
    rate_limited: bool = False,
    at_ms: int | None = None,
    permanent: bool = False,
    retry_after_ms: int | None = None,
    park_until_ms: int | None = None,
) -> None:
    """Record one failed call and trip/escalate the breaker when thresholds hit.

    A permanent failure (rejected credential, exhausted quota, a model this key
    cannot reach) is not counted as a breaker failure: five identical
    rejections would trip the breaker and hide the real cause. The model is
    parked instead, because retrying the same key cannot help. A transient
    failure honours an upstream Retry-After by extending the cooldown.
    """
    model = await session.get(
        orm.ChannelModelORM, candidate.model.id, populate_existing=True
    )
    if model is None:
        return
    now = at_ms if at_ms is not None else now_ms()
    state = state_from_model(model)
    if permanent:
        park = park_until_ms if park_until_ms is not None else now + PERMANENT_COOLDOWN_MS
        until = max(int(state.cooldown_until or 0), park)
        apply_to_model(
            model,
            replace(
                state,
                health="open",
                opened_at=now,
                cooldown_until=until,
                half_open_since=None,
            ),
        )
        await session.commit()
        _sync_cached_model(session, candidate, model)
        return
    config = parse_breaker_config(model.cooldown_json)
    apply_to_model(model, on_failure(state, config, now, rate_limited=rate_limited))
    if retry_after_ms:
        # Retry-After is authoritative: keep the candidate out for at least the
        # window the upstream asked for, even when the breaker did not trip.
        current = state_from_model(model)
        bounded = min(int(retry_after_ms), config.fault_cooldown_s * 1000)
        until = max(int(current.cooldown_until or 0), now + bounded)
        if rate_limited:
            # Rate limiting parks by time only: health stays normal, the
            # cooldown alone gates admission until the moment passes.
            apply_to_model(model, replace(current, cooldown_until=until))
        else:
            opened_at = current.opened_at if current.opened_at is not None else now
            health = (
                current.health
                if current.health in {"open", "half_open", "fault"}
                else "open"
            )
            apply_to_model(
                model,
                replace(
                    current,
                    health=health,
                    opened_at=opened_at,
                    cooldown_until=until,
                ),
            )
    await session.commit()
    _sync_cached_model(session, candidate, model)


async def record_success(
    session: AsyncSession, candidate: Candidate, at_ms: int | None = None
) -> None:
    """Record a successful call, including the half-open probe semantics."""
    now = at_ms if at_ms is not None else now_ms()
    # A success against an already-healthy model is a no-op. Avoid the extra
    # SELECT + COMMIT on the hot path; only re-read when the candidate carries
    # breaker state that a success would actually clear.
    current = state_from_model(candidate.model)
    if on_success(current, parse_breaker_config(candidate.model.cooldown_json), now) == current:
        return
    model = await session.get(
        orm.ChannelModelORM, candidate.model.id, populate_existing=True
    )
    if model is None:
        return
    current = state_from_model(model)
    next_state = on_success(current, parse_breaker_config(model.cooldown_json), now)
    if next_state == current:
        return
    apply_to_model(model, next_state)
    await session.commit()
    _sync_cached_model(session, candidate, model)


def order_candidates(
    candidates: Iterable[Candidate],
    rng: random.Random | None = None,
    policy: "route_stats.RouteOrderPolicy | None" = None,
) -> list[Candidate]:
    """Order by priority path, highest numbers first; shuffle each layer evenly.

    priority_path sorts lexicographically, so reversing the path order keeps a
    nested combo's whole subtree ahead of the next root layer while putting the
    larger (higher-priority) number first at every level. 1 is the lowest
    priority: a member with priority 2 runs before a member with priority 1.

    Within a layer a routing policy (ADR-077) may reorder members by observed
    behaviour; the layers themselves stay absolute.
    """
    layers: dict[tuple[int, ...], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        layers[priority_path(candidate)].append(candidate)
    ordered: list[Candidate] = []
    shuffle = rng.shuffle if rng is not None else random.shuffle
    for path in sorted(layers, reverse=True):
        ordered.extend(_order_layer(list(layers[path]), shuffle, rng, policy))
    return ordered


def _order_layer(
    layer: list[Candidate],
    shuffle: Any,
    rng: random.Random | None,
    policy: "route_stats.RouteOrderPolicy | None",
) -> list[Candidate]:
    """Order one priority layer: strategy metric first, uniform pool woven in.

    Members past the observation period sort by the strategy metric (stable
    success rate desc, cost price asc, latency p50 TTFA asc). Observation-
    phase members and members without stats stay in a uniform pool inserted
    at random slots, so they keep collecting the samples the strategy needs
    without outranking proven members on average. Uniform (or no policy, or a
    single-member layer) keeps the historical even shuffle.
    """
    if policy is None or policy.strategy == "uniform" or len(layer) < 2:
        if len(layer) > 1:
            shuffle(layer)
        return layer
    stats = policy.stats
    tuned: list[Candidate] = []
    rest: list[Candidate] = []
    for candidate in layer:
        stat = stats.get(str(candidate.model.id))
        if stat is not None and stat.tuned:
            tuned.append(candidate)
        else:
            rest.append(candidate)
    if not tuned:
        shuffle(layer)
        return layer
    if policy.strategy == "stable":
        tuned.sort(key=lambda c: stats[str(c.model.id)].success_rate, reverse=True)
    elif policy.strategy == "cost":
        tuned.sort(key=lambda c: stats[str(c.model.id)].price_per_million)
    elif policy.strategy == "latency":
        tuned.sort(
            key=lambda c: stats[str(c.model.id)].p50_ttfa_ms
            if stats[str(c.model.id)].p50_ttfa_ms is not None
            else float("inf"),
        )
    ordered = list(tuned)
    uniform_random = rng.random if rng is not None else random.random
    for member in rest:
        ordered.insert(int(uniform_random() * (len(ordered) + 1)), member)
    return ordered


# ---------------------------------------------------------------------------
# Session affinity
# ---------------------------------------------------------------------------
# A conversation that keeps hitting the same favourite channel reuses that
# channel's prompt cache, and a provider binds that prefix to the channel (and
# account) that served the previous turn. Affinity therefore outranks the
# per-request priority layer: session continuity beats starting from the highest
# layer again, because a layer switch is exactly what invalidates the prefix.
#
# The table is deliberately process-local and best-effort:
#
# * It is lost on restart, and every worker keeps its own. A conversation may
#   therefore bounce between workers (or lose its hint after a reload). Both are
#   acceptable for a hint that only reorders candidates; move it to a shared
#   store (Redis) if it ever has to survive restarts or be global.
# * A remembered target is promoted only while it is still callable. When it is
#   unavailable (channel/model disabled, breaker open, outside its schedule) it
#   is not in the admitted candidate list at all, so ordering falls back to the
#   plain layer order and equal-priority members stay shuffled.
# * WARNING for multi-key channels: channels.select_channel_api_key hashes
#   sha256(request_id) to choose among a channel's keys. Every channel has
#   exactly one key today, so this never fires. A second key would make the
#   upstream see a different account on every request, isolating the provider's
#   prefix cache per account and silently defeating this whole mechanism.
#   Suggested fix (Lead's call, not implemented here): pick the key with the
#   same session key -- stable hash modulo the enabled keys, switching only on
#   auth/quota failures -- before a second key ships.
SESSION_HEADER_NAMES = ("x-session-id", "x-bili-plugin-conversation")
AFFINITY_TTL_S = 3600.0
AFFINITY_MAX_SESSIONS = 4096

_affinity: "OrderedDict[str, tuple[float, str, int]]" = OrderedDict()

# Routing-configuration epoch. Every write that can change the ordered candidate
# list bumps it, and a memory written under an older epoch is ignored (and
# dropped) on the next read: a user who edits a combo or a priority expects the
# new layer order to apply immediately instead of being stuck on a target that
# is no longer preferred. The counter is process-local like the table itself; a
# worker that did not serve the write keeps its hint until its own next write.
# (The DB catalog version already survives restarts and crosses workers; this
# counter only has to be cheap enough to read on every request.)
_affinity_epoch = 0


def bump_affinity_epoch() -> None:
    """Invalidate every remembered target after a routing-configuration write."""
    global _affinity_epoch
    _affinity_epoch += 1


def affinity_epoch() -> int:
    """The current routing-configuration epoch (tests and diagnostics)."""
    return _affinity_epoch


def session_key_from_headers(headers: Mapping[str, Any] | None) -> str:
    """The affinity key a request carries, or "" when it carries none."""
    if not headers:
        return ""
    lowered: dict[str, Any] = {}
    for name, value in headers.items():
        lowered.setdefault(str(name).lower(), value)
    for name in SESSION_HEADER_NAMES:
        value = lowered.get(name)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _affinity_now() -> float:
    return time.monotonic()


def _affinity_prune(now: float) -> None:
    """Drop expired keys and keep the table under its capacity bound.

    Entries are stored in remember order and every TTL is the same, so the
    oldest entry is always the first candidate for eviction.
    """
    while _affinity:
        oldest_key = next(iter(_affinity))
        expires_at, _model_id, _epoch = _affinity[oldest_key]
        if expires_at > now and len(_affinity) <= AFFINITY_MAX_SESSIONS:
            break
        _affinity.pop(oldest_key, None)


def remember_affinity(
    session_key: str | None,
    model_id: str | None,
    *,
    ttl_s: float = AFFINITY_TTL_S,
    now: float | None = None,
) -> None:
    """Remember the channel model that served a session successfully."""
    key = (session_key or "").strip()
    if not key or not model_id:
        return
    moment = _affinity_now() if now is None else now
    _affinity.pop(key, None)
    _affinity[key] = (moment + max(0.0, float(ttl_s)), str(model_id), _affinity_epoch)
    _affinity_prune(moment)


def affinity_model_id(session_key: str | None, *, now: float | None = None) -> str | None:
    """The remembered channel model for a session, or None."""
    key = (session_key or "").strip()
    if not key:
        return None
    moment = _affinity_now() if now is None else now
    _affinity_prune(moment)
    entry = _affinity.get(key)
    if entry is None:
        return None
    expires_at, model_id, epoch = entry
    if expires_at <= moment or epoch != _affinity_epoch:
        # Expired, or written before the last routing-configuration change: the
        # hint is no longer trustworthy, so drop it and let layer order decide.
        _affinity.pop(key, None)
        return None
    return model_id


def forget_affinity(session_key: str | None, model_id: str | None = None) -> None:
    """Drop a session's remembered target after that target failed a call.

    Passing model_id only clears the memory while it still points at the failed
    candidate, so a slow failure of an older choice cannot erase a newer memory
    that a concurrent request already wrote.
    """
    key = (session_key or "").strip()
    if not key:
        return
    if model_id is None:
        _affinity.pop(key, None)
        return
    entry = _affinity.get(key)
    if entry is not None and entry[1] == str(model_id):
        _affinity.pop(key, None)


def apply_affinity(
    candidates: Iterable[Candidate],
    session_key: str | None,
    *,
    now: float | None = None,
) -> list[Candidate]:
    """Promote a session's remembered candidate to the very front.

    Cross-layer promotion is deliberate: the prompt-cache prefix is bound to the
    channel that served the session's previous turn, so staying on it is worth
    more than restarting from the highest priority layer. When the remembered
    model is not in candidates it is not callable right now (channel or model
    disabled, breaker open, outside its schedule), so nothing moves and the
    caller's layer order applies unchanged; equal-priority members keep the
    shuffle they already got from order_candidates.
    """
    ordered = list(candidates)
    model_id = affinity_model_id(session_key, now=now)
    if model_id is None:
        return ordered
    for position, item in enumerate(ordered):
        if item.model.id == model_id:
            if position:
                ordered.insert(0, ordered.pop(position))
            return ordered
    return ordered


def clear_affinity() -> None:
    """Forget every remembered target (tests and operator resets)."""
    _affinity.clear()


async def _flatten_combo(
    session: AsyncSession,
    combo: orm.ComboORM,
    prefix: tuple[int, ...],
    visiting: frozenset[str],
) -> list[Candidate]:
    """Recursively expand child combos into one ordered list of leaf candidates."""
    if combo.id in visiting:
        return []
    next_visiting = visiting | {combo.id}
    candidates: list[Candidate] = []
    for target, model, channel in await repo.list_combo_members(session, combo.id):
        # 显式组合成员永远取 combo_targets.priority（1 = 最低，数值越大越先）；
        # channels.priority 不参与，只有自动组合（隐式分组）才读渠道优先级。
        path = prefix + (int(target.priority or 1),)
        if target.child_combo_id:
            child = await repo.get_combo(session, target.child_combo_id)
            if child is None or not child.enabled:
                continue
            candidates.extend(
                await _flatten_combo(session, child, path, next_visiting)
            )
            continue
        if not target.channel_model_id:
            continue
        if model is None or channel is None:
            continue
        candidates.append(
            Candidate(
                channel=channel,
                model=model,
                priority=path[0],
                priority_path=path,
                # A manually disabled member stays in the list so the
                # attempts timeline can record the skip.
                member_enabled=bool(target.enabled),
            )
        )
    return candidates


async def _combo_candidates(
    session: AsyncSession, combo: orm.ComboORM
) -> list[Candidate]:
    """Flatten an explicit combo, including nested child combos, by priority path."""
    policy = await route_stats.resolve_policy(session, combo)
    return order_candidates(
        await _flatten_combo(session, combo, (), frozenset()), policy=policy
    )


async def _group_candidates(
    session: AsyncSession, group: ImplicitGroup
) -> list[Candidate]:
    # Keep members whose channel is disabled in the list so a skip can be
    # logged; the attempt loop filters them without consuming a round.
    # 隐式分组是自动组合：成员优先级取 channels.priority（1 = 最低，越大越先）。
    candidates = [
        Candidate(
            channel=member.channel,
            model=member.model,
            priority=int(member.channel.priority or 1),
            priority_path=(int(member.channel.priority or 1),),
        )
        for member in group.members
    ]
    policy = await route_stats.resolve_policy(session)
    return order_candidates(candidates, policy=policy)


def _sync_cached_model(
    session: AsyncSession, candidate: Candidate, model: orm.ChannelModelORM
) -> None:
    """Copy breaker columns back to the catalog row after a write.

    The snapshot is not reloaded for breaker traffic (version changes only), so
    the shared cached model must carry the state the next request will admit.
    """
    catalog = catalog_for_session(session)
    if catalog is not None and catalog.snapshot is not None:
        apply_to_model(candidate.model, state_from_model(model))


def _cached_direct_suggestions(snapshot: CatalogSnapshot, name: str) -> list[str]:
    suggestions: list[str] = []
    for model in snapshot.models_by_call_model.get(name, ()):
        channel = snapshot.channels.get(model.channel_id)
        if channel is not None:
            suggestions.append(f"{channel.slug}/{model.call_model}")
    return sorted(suggestions)


def _cached_call_model_target(snapshot: CatalogSnapshot, requested: str) -> CallTarget:
    """Resolve a provider-native call_model that itself contains "/".

    OpenRouter and NVIDIA NIM hand out ids such as
    "nvidia/nemotron-3-ultra-550b-a55b"; the qualified-name parser reads the
    first segment as a channel slug and misses. Treat the whole string as a
    call_model and accept it only when exactly one usable channel offers it.
    """
    rows = [
        (snapshot.channels[model.channel_id], model)
        for model in snapshot.models_by_call_model.get(requested, ())
        if model.channel_id in snapshot.channels
    ]
    openai_rows = [
        (channel, model) for channel, model in rows if channel.protocol == OPENAI_PROTOCOL
    ]
    usable = [
        (channel, model)
        for channel, model in openai_rows
        if channel.enabled and ban_reason(model, now_ms()) is None
    ]
    if len(usable) == 1:
        channel, model = usable[0]
        return CallTarget(
            kind="direct",
            name=requested,
            candidates=(
                Candidate(channel=channel, model=model, priority=int(channel.priority or 1)),
            ),
        )
    _cooldown_only(
        requested,
        [(channel, model) for channel, model in openai_rows if channel.enabled],
    )
    raise _not_found(
        requested,
        sorted({f"{channel.slug}/{model.call_model}" for channel, model in rows}),
    )


def _cached_direct_target(
    snapshot: CatalogSnapshot, requested: str, parsed: tuple[str, str]
) -> CallTarget:
    slug, call_model = parsed
    resolved = snapshot.direct.get((slug, call_model))
    if resolved is None:
        return _cached_call_model_target(snapshot, requested)
    channel, model = resolved
    _direct_model_gate(requested, channel, model)
    if channel.protocol != OPENAI_PROTOCOL:
        raise OpenAIError(
            400,
            f"渠道 {slug} 的协议为 {channel.protocol}，本版本直连仅支持 OpenAI 兼容协议",
            ErrorCode.NOT_IMPLEMENTED,
            "invalid_request_error",
        )
    return CallTarget(
        kind="direct",
        name=requested,
        candidates=(
            Candidate(channel=channel, model=model, priority=int(channel.priority or 1)),
        ),
    )


def _flatten_cached_combo(
    snapshot: CatalogSnapshot,
    combo: orm.ComboORM,
    prefix: tuple[int, ...],
    visiting: frozenset[str],
) -> list[Candidate]:
    if combo.id in visiting:
        return []
    next_visiting = visiting | {combo.id}
    candidates: list[Candidate] = []
    for target in snapshot.targets_by_combo.get(combo.id, ()):
        # 同上：显式成员只认 combo_targets.priority，与 channels.priority 无关。
        path = prefix + (int(target.priority or 1),)
        if target.child_combo_id:
            child = snapshot.combos_by_id.get(target.child_combo_id)
            if child is None or not child.enabled:
                continue
            candidates.extend(
                _flatten_cached_combo(snapshot, child, path, next_visiting)
            )
            continue
        if not target.channel_model_id:
            continue
        model = snapshot.models.get(target.channel_model_id)
        if model is None:
            continue
        channel = snapshot.channels.get(model.channel_id)
        if channel is None:
            continue
        candidates.append(
            Candidate(
                channel=channel,
                model=model,
                priority=path[0],
                priority_path=path,
                member_enabled=bool(target.enabled),
            )
        )
    return candidates


def _cached_combo_candidates(
    snapshot: CatalogSnapshot, combo: orm.ComboORM
) -> list[Candidate]:
    # Snapshot path: memory only. A cold route-stats cache degrades to the
    # uniform shuffle instead of paying SQL.
    return order_candidates(
        _flatten_cached_combo(snapshot, combo, (), frozenset()),
        policy=route_stats.policy_for_combo(combo),
    )


def _cached_group_candidates(
    snapshot: CatalogSnapshot, group: ImplicitGroup
) -> list[Candidate]:
    candidates = [
        Candidate(
            channel=member.channel,
            model=member.model,
            priority=int(member.channel.priority or 1),
            priority_path=(int(member.channel.priority or 1),),
        )
        for member in group.members
    ]
    return order_candidates(candidates, policy=route_stats.current_policy())


def _resolve_from_snapshot(snapshot: CatalogSnapshot, name: str) -> CallTarget:
    """Resolve purely from memory, mirroring the SQL path branch by branch."""
    if "/" in name:
        parsed = split_direct_model(name)
        if parsed is None:
            raise _not_found(name)
        return _cached_direct_target(snapshot, name, parsed)

    combo = snapshot.combos_by_name.get(name)
    if combo is not None:
        if not combo.enabled:
            raise _not_found(name)
        candidates = _cached_combo_candidates(snapshot, combo)
        if not candidates:
            raise _no_available(name)
        return CallTarget(
            kind="combo",
            origin="manual",
            name=name,
            candidates=tuple(candidates),
            combo=combo,
            max_attempts=max(1, int(combo.max_attempts or 1)),
            failover_rules=parse_failover_rules(combo.failover_rules_json),
        )

    group = snapshot.groups.get(name)
    if group is None:
        raise _not_found(name, _cached_direct_suggestions(snapshot, name))
    candidates = _cached_group_candidates(snapshot, group)
    if not candidates:
        raise _no_available(name)
    # 隐式分组是查询期派生的自动组合，对外也报 kind="combo"，只读不可编辑。
    return CallTarget(
        kind="combo", name=name, origin="auto", candidates=tuple(candidates), group=group
    )


async def resolve_target(session: AsyncSession, requested: str) -> CallTarget:
    """Resolve a client ``model`` string into ordered candidates.

    ``slug/call_model`` is always direct. A bare name prefers an explicit combo
    first, then falls back to an implicit group derived from same-named
    ``call_model`` rows across channels.
    """
    name = (requested or "").strip()
    if not name:
        raise _not_found(requested)

    catalog = catalog_for_session(session)
    if catalog is not None:
        await catalog.ensure_fresh()
    if catalog is not None and catalog.snapshot is not None:
        return _resolve_from_snapshot(catalog.snapshot, name)

    if "/" in name:
        return await _direct_candidate(session, name)

    combo = await repo.get_combo_by_name(session, name)
    if combo is not None:
        if not combo.enabled:
            raise _not_found(name)
        candidates = await _combo_candidates(session, combo)
        if not candidates:
            raise _no_available(name)
        return CallTarget(
            kind="combo",
            origin="manual",
            name=name,
            candidates=tuple(candidates),
            combo=combo,
            max_attempts=max(1, int(combo.max_attempts or 1)),
            failover_rules=parse_failover_rules(combo.failover_rules_json),
        )

    group = await get_group(session, name)
    if group is None:
        raise _not_found(name, await _direct_suggestions(session, name))
    candidates = await _group_candidates(session, group)
    if not candidates:
        raise _no_available(name)
    # 隐式分组是查询期派生的自动组合，对外也报 kind="combo"，只读不可编辑。
    return CallTarget(
        kind="combo", name=name, origin="auto", candidates=tuple(candidates), group=group
    )


async def runnable_candidates(target: CallTarget) -> list[Candidate]:
    """Candidates that may be called right now (used by tests and callers)."""
    return [
        candidate
        for candidate in target.candidates
        if unavailable_reason(candidate) is None
    ]

