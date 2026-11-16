"""Circuit-breaker state transitions for one leaf channel model.

The persisted counters are the source of truth (single-process MVP). This
module is pure: it takes the counters plus ``now`` and returns the next
counters, so every transition is unit-testable without a database.

Counters are deliberately separate (ADR-025):

* ``consecutive_failures`` trips the breaker; any success clears it.
* ``consecutive_trip_count`` counts consecutive trips; any success clears it.
* ``escalation_trip_count`` moves the model toward ``fault``; a normal success
  clears it while a half-open success only decrements it by one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

HealthState = Literal["normal", "open", "half_open", "fault"]
VisibleState = Literal[
    "normal",
    "open",
    "half_open",
    "fault",
    "disabled",
]
EscalateMode = Literal["never", "after_n", "immediate"]

ESCALATE_MODES: tuple[str, ...] = ("never", "after_n", "immediate")

# A claimed half-open probe that never reports back (cancelled stream, crash or a
# dev-server reload) must not block the model forever. Five minutes comfortably
# exceeds request_timeout_s (120s) while keeping the recovery window short.
HALF_OPEN_PROBE_LEASE_MS = 300_000

# A permanent upstream failure (rejected credential, exhausted quota, model the
# key cannot reach) will not heal inside a retry window. Park the model long
# enough that a broken key stops burning requests, without needing a manual
# reset; an operator can still clear it from the channel page.
PERMANENT_COOLDOWN_MS = 15 * 60 * 1000


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5
    cooldown_s: int = 30
    rate_limit_cooldown_s: int = 5
    half_open_max: int = 1
    escalate_mode: EscalateMode = "after_n"
    escalate_after: int = 3
    fault_cooldown_s: int = 300


@dataclass(frozen=True, slots=True)
class BreakerState:
    health: HealthState = "normal"
    consecutive_failures: int = 0
    consecutive_trip_count: int = 0
    escalation_trip_count: int = 0
    opened_at: int | None = None
    cooldown_until: int | None = None
    fault_until: int | None = None
    # When the single half-open probe slot was claimed; None on legacy rows.
    half_open_since: int | None = None


def _as_int(
    value: Any, default: int, *, minimum: int = 0, clamp: bool = True
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return max(minimum, parsed) if clamp else default
    return parsed


def parse_breaker_config(
    raw: Any, defaults: BreakerConfig | None = None
) -> BreakerConfig:
    """Parse ``channel_models.cooldown_json``; unknown/missing fields keep defaults."""
    base = defaults or BreakerConfig()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}

    mode = str(raw.get("escalate_mode") or base.escalate_mode).strip().lower()
    if mode not in ESCALATE_MODES:
        mode = base.escalate_mode
    return BreakerConfig(
        failure_threshold=_as_int(
            raw.get("failure_threshold"),
            base.failure_threshold,
            minimum=1,
            clamp=False,
        ),
        cooldown_s=_as_int(raw.get("cooldown_s"), base.cooldown_s, minimum=0),
        rate_limit_cooldown_s=_as_int(
            raw.get("rate_limit_cooldown_s"), base.rate_limit_cooldown_s, minimum=0
        ),
        half_open_max=_as_int(
            raw.get("half_open_max"),
            base.half_open_max,
            minimum=1,
            clamp=False,
        ),
        escalate_mode=mode,  # type: ignore[arg-type]
        escalate_after=_as_int(
            raw.get("escalate_after"),
            base.escalate_after,
            minimum=1,
            clamp=False,
        ),
        fault_cooldown_s=_as_int(
            raw.get("fault_cooldown_s"), base.fault_cooldown_s, minimum=0
        ),
    )


def config_to_dict(config: BreakerConfig) -> dict[str, Any]:
    return asdict(config)


def cooldown_duration_s(state: BreakerState, config: BreakerConfig) -> int:
    """熔断打开时的冷却秒数。

    第一次跳闸用基础冷却时长；已经连续跳闸过（consecutive_trip_count）或者
    已经升级过（escalation_trip_count）的模型用升级冷却时长，免得一个反复
    出错的模型只被关几十秒又被放出来。
    """
    base = max(0, int(config.cooldown_s))
    escalated = state.consecutive_trip_count >= 2 or state.escalation_trip_count > 0
    if escalated:
        return max(base, max(0, int(config.fault_cooldown_s)))
    return base


def effective_cooldown_until(
    state: BreakerState,
    config: BreakerConfig | None = None,
) -> int | None:
    """熔断打开状态的真实到期时刻。

    cooldown_until 丢失（老行、手工改库）或者为 0 时，按熔断配置从 opened_at
    推出到期时刻；推不出来就返回 None，调用方必须如实写「恢复时间待定」，
    不能拿 0 冒充时刻（前端会把 0 渲染成 00:00）。
    """
    if state.health != "open":
        return state.cooldown_until
    until = state.cooldown_until
    if until is not None and until > 0:
        return until
    opened_at = state.opened_at
    if opened_at is None or opened_at <= 0:
        return None
    return opened_at + cooldown_duration_s(state, config or BreakerConfig()) * 1000


def state_from_model(model: Any) -> BreakerState:
    """Read breaker counters from an ORM row (or anything with the fields)."""
    health = str(getattr(model, "health_status", "normal") or "normal").lower()
    if health not in {"normal", "open", "half_open", "fault"}:
        health = "normal"
    return BreakerState(
        health=health,  # type: ignore[arg-type]
        consecutive_failures=_as_int(
            getattr(model, "consecutive_failures", 0), 0
        ),
        consecutive_trip_count=_as_int(
            getattr(model, "consecutive_trip_count", 0), 0
        ),
        escalation_trip_count=_as_int(
            getattr(model, "escalation_trip_count", 0), 0
        ),
        opened_at=getattr(model, "opened_at", None),
        cooldown_until=getattr(model, "cooldown_until", None),
        fault_until=getattr(model, "fault_until", None),
        half_open_since=getattr(model, "half_open_since", None),
    )


def apply_to_model(model: Any, state: BreakerState) -> None:
    model.health_status = state.health
    model.consecutive_failures = state.consecutive_failures
    model.consecutive_trip_count = state.consecutive_trip_count
    model.escalation_trip_count = state.escalation_trip_count
    model.opened_at = state.opened_at
    model.cooldown_until = state.cooldown_until
    model.fault_until = state.fault_until
    model.half_open_since = state.half_open_since


# ORM column name per BreakerState field. Deriving the UPDATE payload from
# this table keeps a new state field from needing a hand-written column list
# in every service-layer restore statement.
_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("health_status", "health"),
    ("consecutive_failures", "consecutive_failures"),
    ("consecutive_trip_count", "consecutive_trip_count"),
    ("escalation_trip_count", "escalation_trip_count"),
    ("opened_at", "opened_at"),
    ("cooldown_until", "cooldown_until"),
    ("fault_until", "fault_until"),
    ("half_open_since", "half_open_since"),
)


def state_to_values(state: BreakerState) -> dict[str, Any]:
    """Column values for a conditional UPDATE, derived from the state fields."""
    return {column: getattr(state, field) for column, field in _STATE_COLUMNS}


def tick(
    state: BreakerState, now_ms: int, config: BreakerConfig | None = None
) -> tuple[BreakerState, bool]:
    """Apply time-driven transitions: fault recovery and cooldown expiry.

    Returns the (possibly changed) state and whether it differs from the input.

    config lets the caller repair an open row whose cooldown deadline was
    lost: the deadline is derived from opened_at, so such a row still recovers
    on its own instead of staying open forever.
    """
    if state.health == "fault":
        if state.fault_until is not None and now_ms >= state.fault_until:
            return BreakerState(), True
        return state, False
    if state.health == "open":
        until = state.cooldown_until
        if until is None or until <= 0:
            derived = effective_cooldown_until(state, config)
            if derived is not None:
                state = replace(state, cooldown_until=derived)
                until = derived
        if until is not None and now_ms >= until:
            # Cooldown over: the next admitted request becomes the probe, and
            # half_open_since records when the probe slot was claimed.
            return replace(state, health="half_open", half_open_since=now_ms), True
        return state, False
    if state.health == "half_open":
        since = state.half_open_since
        if since is not None and now_ms - since < HALF_OPEN_PROBE_LEASE_MS:
            return state, False
        # The probe never reported back (cancelled, crashed or the process was
        # restarted) or the row predates half_open_since. Free the slot but keep
        # the counters so the next failure still trips quickly.
        return replace(
            state,
            health="normal",
            opened_at=None,
            cooldown_until=None,
            fault_until=None,
            half_open_since=None,
        ), True
    return state, False


def visible_state(
    state: BreakerState,
    enabled: bool,
    now_ms: int,
    config: BreakerConfig | None = None,
) -> VisibleState:
    """The five-value status shown in the admin UI.

    half_open is reported as-is so the UI never has to re-derive it from
    health_status; fault and disabled differ by source, not by severity.
    """
    if not enabled:
        return "disabled"
    current, _ = tick(state, now_ms, config)
    if current.health == "fault":
        return "fault"
    if current.health in {"open", "half_open"}:
        return current.health
    return "normal"


def next_retry_at(
    state: BreakerState, now_ms: int, config: BreakerConfig | None = None
) -> int | None:
    current, _ = tick(state, now_ms, config)
    if current.health == "fault":
        return current.fault_until
    if current.health == "open":
        return effective_cooldown_until(current, config)
    if (
        current.health == "normal"
        and current.cooldown_until is not None
        and current.cooldown_until > now_ms
    ):
        # A rate-limit cooldown parks a healthy model by time only; the
        # recovery moment is the cooldown itself.
        return current.cooldown_until
    return None


def member_view(
    state: BreakerState,
    enabled: bool,
    now_ms: int,
    config: BreakerConfig | None = None,
) -> dict[str, Any]:
    """One normalized status view for every admin surface.

    Applies the time-driven transitions once, then hides timers that no longer
    gate admission so the UI never shows a retry time in the past. Both the
    catalog and the channel model detail page must render this view, otherwise
    an expired cooldown looks open on one page and half_open on the other.
    """
    current, _ = tick(state, now_ms, config)
    visible = visible_state(current, enabled, now_ms, config)
    rate_limit_until = (
        current.cooldown_until
        if current.health == "normal"
        and current.cooldown_until is not None
        and current.cooldown_until > now_ms
        else None
    )
    return {
        "state": current,
        "health": current.health,
        "visible": visible,
        "cooldown_until": (
            effective_cooldown_until(current, config)
            if visible == "open" and current.health == "open"
            else rate_limit_until
        ),
        "fault_until": current.fault_until if visible == "fault" else None,
        "retry_at": next_retry_at(current, now_ms, config),
    }


def _trip(
    state: BreakerState,
    config: BreakerConfig,
    now_ms: int,
    *,
    cooldown_s: int,
) -> BreakerState:
    trips = state.consecutive_trip_count + 1
    escalation = state.escalation_trip_count + 1
    escalate = config.escalate_mode == "immediate" or (
        config.escalate_mode == "after_n" and escalation >= config.escalate_after
    )
    if escalate:
        return replace(
            state,
            health="fault",
            consecutive_trip_count=trips,
            escalation_trip_count=escalation,
            opened_at=now_ms,
            cooldown_until=None,
            half_open_since=None,
            fault_until=now_ms + config.fault_cooldown_s * 1000,
        )
    return replace(
        state,
        health="open",
        consecutive_trip_count=trips,
        escalation_trip_count=escalation,
        opened_at=now_ms,
        cooldown_until=now_ms + cooldown_s * 1000,
        fault_until=None,
        half_open_since=None,
    )


def on_failure(
    state: BreakerState,
    config: BreakerConfig,
    now_ms: int,
    *,
    rate_limited: bool = False,
) -> BreakerState:
    """Record one real upstream failure for an admitted candidate.

    A rate limit (429) is an expected, temporary condition, not a fault: it
    never moves the failure counters, never trips, never escalates. It only
    holds the candidate out for a short cooldown - record_failure lets the
    upstream Retry-After win; this is the rate_limit_cooldown_s fallback -
    and the model recovers on its own once the moment passes.
    """
    current, _ = tick(state, now_ms)
    if current.health in {"open", "fault"}:
        # Not callable in this state; never let a late failure move the clock.
        return current
    if rate_limited:
        # Time-based parking only: health stays normal (the cooldown gates
        # admission, not the circuit), and a half-open probe that hit a rate
        # limit is released so a fresh probe can go after the cooldown.
        return replace(
            current,
            health="normal",
            opened_at=None,
            cooldown_until=now_ms + config.rate_limit_cooldown_s * 1000,
            half_open_since=None,
        )

    failures = current.consecutive_failures + 1
    triggered = current.health == "half_open" or failures >= config.failure_threshold
    next_state = replace(current, consecutive_failures=failures)
    if not triggered:
        return next_state
    return _trip(next_state, config, now_ms, cooldown_s=config.cooldown_s)


def on_success(
    state: BreakerState, config: BreakerConfig, now_ms: int = 0
) -> BreakerState:
    """Record one successful call; half-open success only decrements escalation."""
    current, _ = tick(state, now_ms)
    if current.health == "half_open":
        escalation = max(0, current.escalation_trip_count - 1)
    else:
        escalation = 0
    return BreakerState(
        health="normal",
        consecutive_failures=0,
        consecutive_trip_count=0,
        escalation_trip_count=escalation,
    )


def on_reset() -> BreakerState:
    """Manual reset: fully closed, every counter and timer cleared."""
    return BreakerState()
