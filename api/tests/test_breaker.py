from __future__ import annotations

from l03_domain.breaker import (
    HALF_OPEN_PROBE_LEASE_MS,
    BreakerConfig,
    BreakerState,
    member_view,
    next_retry_at,
    on_failure,
    on_reset,
    on_success,
    parse_breaker_config,
    state_from_model,
    state_to_values,
    tick,
    visible_state,
)

NOW = 1_000_000


def _open(now: int = NOW, cooldown_s: int = 30) -> BreakerState:
    return BreakerState(
        health="open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=1,
        opened_at=now,
        cooldown_until=now + cooldown_s * 1000,
    )


def test_parse_config_defaults_partial_and_invalid_values():
    assert parse_breaker_config("{}") == BreakerConfig()
    assert parse_breaker_config(None) == BreakerConfig()

    parsed = parse_breaker_config(
        {
            "failure_threshold": 2,
            "cooldown_s": 7,
            "escalate_mode": "immediate",
            "escalate_after": 9,
            "unknown": "ignored",
        }
    )
    assert parsed.failure_threshold == 2
    assert parsed.cooldown_s == 7
    assert parsed.escalate_mode == "immediate"
    assert parsed.escalate_after == 9
    # Untouched fields keep spec defaults.
    assert parsed.rate_limit_cooldown_s == 5
    assert parsed.fault_cooldown_s == 300

    assert parse_breaker_config("not json").failure_threshold == 5
    assert parse_breaker_config({"failure_threshold": 0}).failure_threshold == 5
    assert parse_breaker_config({"escalate_mode": "sometimes"}).escalate_mode == "after_n"


def test_threshold_trips_open_with_normal_cooldown():
    config = BreakerConfig(failure_threshold=3, cooldown_s=30)
    state = BreakerState()
    for _ in range(2):
        state = on_failure(state, config, NOW)
        assert state.health == "normal"

    state = on_failure(state, config, NOW)
    assert state.health == "open"
    assert state.consecutive_failures == 3
    assert state.consecutive_trip_count == 1
    assert state.escalation_trip_count == 1
    assert state.opened_at == NOW
    assert state.cooldown_until == NOW + 30_000
    assert state.fault_until is None


def test_rate_limit_failure_parks_by_time_only():
    """429 不进故障口径：计数不动、不 trip，只设短冷却到点自动恢复。"""
    config = BreakerConfig(failure_threshold=1, cooldown_s=30, rate_limit_cooldown_s=5)
    state = on_failure(BreakerState(), config, NOW, rate_limited=True)
    assert state.health == "normal"
    assert state.consecutive_failures == 0
    assert state.consecutive_trip_count == 0
    assert state.escalation_trip_count == 0
    assert state.opened_at is None
    assert state.cooldown_until == NOW + 5_000


def test_rate_limit_failure_releases_the_half_open_probe():
    state = tick(_open(), NOW + 30_000)[0]
    config = BreakerConfig(failure_threshold=5, cooldown_s=30, rate_limit_cooldown_s=5)

    parked = on_failure(state, config, NOW + 31_000, rate_limited=True)

    assert parked.health == "normal"
    assert parked.half_open_since is None
    # The fault memory survives: only the probing cycle restarts.
    assert parked.consecutive_failures == 5
    assert parked.consecutive_trip_count == 1
    assert parked.cooldown_until == NOW + 36_000


def test_cooldown_expiry_enters_half_open_without_resetting_counters():
    state = _open()
    assert tick(state, NOW + 29_999)[0].health == "open"

    promoted, changed = tick(state, NOW + 30_000)
    assert changed is True
    assert promoted.health == "half_open"
    assert promoted.consecutive_failures == 5
    assert promoted.consecutive_trip_count == 1
    assert promoted.escalation_trip_count == 1
    # The claim time is what later lets a lost probe expire.
    assert promoted.half_open_since == NOW + 30_000


def test_half_open_success_clears_failure_and_trip_but_only_decrements_escalation():
    state = _open()
    state = tick(state, NOW + 30_000)[0]
    state = BreakerState(
        health=state.health,
        consecutive_failures=state.consecutive_failures,
        consecutive_trip_count=state.consecutive_trip_count,
        escalation_trip_count=2,
        opened_at=state.opened_at,
        cooldown_until=state.cooldown_until,
        half_open_since=state.half_open_since,
    )

    recovered = on_success(state, BreakerConfig(), NOW + 31_000)
    assert recovered.health == "normal"
    assert recovered.consecutive_failures == 0
    assert recovered.consecutive_trip_count == 0
    assert recovered.escalation_trip_count == 1
    assert recovered.opened_at is None
    assert recovered.cooldown_until is None


def test_half_open_success_does_not_go_below_zero_escalation():
    state = BreakerState(health="half_open", escalation_trip_count=0)
    recovered = on_success(state, BreakerConfig(), NOW)
    assert recovered.escalation_trip_count == 0
    assert recovered.health == "normal"


def test_half_open_failure_trips_again_and_increments_both_trip_counters():
    state = tick(_open(), NOW + 30_000)[0]
    config = BreakerConfig(failure_threshold=5, cooldown_s=10, escalate_after=3)

    reopened = on_failure(state, config, NOW + 31_000)
    assert reopened.health == "open"
    assert reopened.consecutive_trip_count == 2
    assert reopened.escalation_trip_count == 2
    assert reopened.cooldown_until == NOW + 41_000


def test_normal_success_clears_escalation_counter_too():
    state = BreakerState(
        health="normal",
        consecutive_failures=2,
        consecutive_trip_count=4,
        escalation_trip_count=2,
    )
    recovered = on_success(state, BreakerConfig(), NOW)
    assert recovered == BreakerState()


def test_escalate_after_n_turns_open_into_fault():
    config = BreakerConfig(
        failure_threshold=1, cooldown_s=10, escalate_mode="after_n", escalate_after=2,
        fault_cooldown_s=300,
    )
    first = on_failure(BreakerState(), config, NOW)
    assert first.health == "open"

    half_open = tick(first, NOW + 10_000)[0]
    faulted = on_failure(half_open, config, NOW + 11_000)
    assert faulted.health == "fault"
    assert faulted.consecutive_trip_count == 2
    assert faulted.escalation_trip_count == 2
    assert faulted.cooldown_until is None
    assert faulted.fault_until == NOW + 311_000


def test_escalate_mode_never_and_immediate():
    never = BreakerConfig(failure_threshold=1, escalate_mode="never")
    state = on_failure(BreakerState(), never, NOW)
    assert state.health == "open"

    immediate = BreakerConfig(failure_threshold=1, escalate_mode="immediate")
    state = on_failure(BreakerState(), immediate, NOW)
    assert state.health == "fault"


def test_fault_auto_recovers_and_fully_clears_counters():
    fault = BreakerState(
        health="fault",
        consecutive_failures=9,
        consecutive_trip_count=3,
        escalation_trip_count=3,
        opened_at=NOW,
        fault_until=NOW + 60_000,
    )
    still, changed = tick(fault, NOW + 59_999)
    assert changed is False
    assert still == fault

    recovered, changed = tick(fault, NOW + 60_000)
    assert changed is True
    assert recovered == BreakerState()


def test_manual_reset_clears_everything():
    assert on_reset() == BreakerState()


def test_visible_state_is_five_values_and_timer_expiry_is_automatic():
    now = NOW
    assert visible_state(BreakerState(), True, now) == "normal"
    assert visible_state(_open(), True, now) == "open"
    assert (
        visible_state(tick(_open(), now + 30_000)[0], True, now + 30_000)
        == "half_open"
    )
    assert (
        visible_state(
            BreakerState(health="fault", fault_until=now + 1), True, now
        )
        == "fault"
    )
    assert (
        visible_state(
            BreakerState(health="fault", fault_until=now + 1), True, now + 1
        )
        == "normal"
    )
    assert (
        visible_state(
            BreakerState(health="fault", fault_until=now + 1), False, now
        )
        == "disabled"
    )


def test_member_view_returns_one_normalized_five_state_view():
    now = NOW
    normal = member_view(BreakerState(), True, now)
    assert (normal["health"], normal["visible"]) == ("normal", "normal")
    assert normal["cooldown_until"] is None
    assert normal["fault_until"] is None
    assert normal["retry_at"] is None

    opened = member_view(_open(), True, now)
    assert (opened["health"], opened["visible"]) == ("open", "open")
    assert opened["cooldown_until"] == now + 30_000
    assert opened["retry_at"] == now + 30_000

    expired = member_view(_open(), True, now + 30_000)
    assert (expired["health"], expired["visible"]) == (
        "half_open",
        "half_open",
    )
    assert expired["cooldown_until"] is None
    assert expired["retry_at"] is None

    probing = member_view(
        BreakerState(health="half_open", half_open_since=now), True, now
    )
    assert (probing["health"], probing["visible"]) == ("half_open", "half_open")

    fault = member_view(
        BreakerState(health="fault", fault_until=now + 60_000), True, now
    )
    assert (fault["health"], fault["visible"]) == ("fault", "fault")
    assert fault["fault_until"] == now + 60_000
    assert fault["cooldown_until"] is None

    disabled = member_view(_open(), False, now)
    assert (disabled["health"], disabled["visible"]) == ("open", "disabled")
    assert disabled["cooldown_until"] is None
    assert disabled["fault_until"] is None


def test_next_retry_at_follows_open_and_fault_timers():
    assert next_retry_at(BreakerState(), NOW) is None
    assert next_retry_at(_open(), NOW) == NOW + 30_000
    fault = BreakerState(health="fault", fault_until=NOW + 60_000)
    assert next_retry_at(fault, NOW) == NOW + 60_000
    assert next_retry_at(fault, NOW + 60_000) is None


def test_half_open_probe_lease_expiry_frees_the_slot_but_keeps_counters():
    state = BreakerState(
        health="half_open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=2,
        opened_at=NOW,
        cooldown_until=NOW,
        half_open_since=NOW,
    )

    still, changed = tick(state, NOW + HALF_OPEN_PROBE_LEASE_MS - 1)
    assert changed is False
    assert still.health == "half_open"

    freed, changed = tick(state, NOW + HALF_OPEN_PROBE_LEASE_MS)
    assert changed is True
    assert freed.health == "normal"
    assert freed.consecutive_failures == 5
    assert freed.consecutive_trip_count == 1
    assert freed.escalation_trip_count == 2
    assert freed.half_open_since is None
    assert freed.cooldown_until is None
    assert freed.fault_until is None


def test_legacy_half_open_row_without_timestamp_is_released():
    freed, changed = tick(BreakerState(health="half_open"), NOW)
    assert changed is True
    assert freed == BreakerState()


def test_trip_clears_half_open_timestamp():
    half = BreakerState(
        health="half_open",
        consecutive_failures=5,
        half_open_since=NOW,
    )
    reopened = on_failure(half, BreakerConfig(failure_threshold=5), NOW + 1_000)
    assert reopened.health == "open"
    assert reopened.half_open_since is None


def test_state_to_values_covers_every_persisted_column():
    assert state_to_values(BreakerState()) == {
        "health_status": "normal",
        "consecutive_failures": 0,
        "consecutive_trip_count": 0,
        "escalation_trip_count": 0,
        "opened_at": None,
        "cooldown_until": None,
        "fault_until": None,
        "half_open_since": None,
    }


def test_state_to_values_renames_health_field():
    opened = state_to_values(_open())
    assert opened["health_status"] == "open"
    assert opened["cooldown_until"] == NOW + 30_000
