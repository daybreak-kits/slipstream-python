from __future__ import annotations

import pytest

from l03_domain.failover import (
    FailoverRules,
    choose_action,
    normalize_failover_rules,
    parse_failover_rules,
)


def _rules(*items, default_action: str = "next") -> FailoverRules:
    return parse_failover_rules(
        {"rules": list(items), "default_action": default_action}
    )


def test_normalize_returns_the_canonical_object_and_rejects_bad_input() -> None:
    assert normalize_failover_rules(None) == {"rules": [], "default_action": "next"}
    assert normalize_failover_rules([{"status": 429, "action": "next"}]) == {
        "rules": [{"status": [429], "body_regex": None, "action": "next"}],
        "default_action": "next",
    }
    assert normalize_failover_rules({"rules": [{"status": "500-599"}]})[
        "rules"
    ][0]["status"] == ["500-599"]

    with pytest.raises(ValueError):
        normalize_failover_rules({"rules": [{"status": 400, "body_regex": "("}]})
    with pytest.raises(ValueError):
        normalize_failover_rules({"rules": [{"status": 400, "action": "abort?"}]})
    with pytest.raises(ValueError):
        normalize_failover_rules({"default_action": "maybe"})


def test_stop_wins_when_several_rules_match() -> None:
    rules = _rules(
        {"status": [500], "action": "next"},
        {"status": [500], "action": "stop"},
    )
    assert (
        choose_action(status_code=500, retryable=True, body="boom", rules=rules)
        == "stop"
    )


def test_body_regex_is_case_insensitive_and_skipped_for_streams() -> None:
    rules = _rules(
        {"status": 400, "body_regex": "content policy", "action": "stop"},
        {"status": 400, "action": "next"},
    )
    assert (
        choose_action(
            status_code=400,
            retryable=False,
            body="Blocked by CONTENT POLICY",
            rules=rules,
        )
        == "stop"
    )
    # Streams match on status only, so the second rule decides.
    assert (
        choose_action(
            status_code=400,
            retryable=False,
            body="Blocked by CONTENT POLICY",
            rules=rules,
            is_stream=True,
        )
        == "next"
    )


def test_default_action_applies_when_no_rule_matches() -> None:
    rules = _rules({"status": 429, "action": "next"}, default_action="stop")
    assert (
        choose_action(status_code=500, retryable=True, body="", rules=rules)
        == "stop"
    )
    # An explicit "stop" default survives persistence even with no rules.
    empty = _rules(default_action="stop")
    assert (
        choose_action(status_code=500, retryable=True, body="", rules=empty)
        == "stop"
    )


def test_unconfigured_rules_fall_back_to_transport_retryable() -> None:
    rules = parse_failover_rules("[]")
    assert (
        choose_action(status_code=429, retryable=True, body="", rules=rules)
        == "next"
    )
    assert (
        choose_action(status_code=400, retryable=False, body="", rules=rules)
        == "stop"
    )
