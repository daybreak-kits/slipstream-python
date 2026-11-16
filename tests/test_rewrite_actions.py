from __future__ import annotations

import pytest

from l03_domain.rewrite import apply_body_rules, apply_body_rules_report


BASE = {
    "model": "public/model",
    "a": {"b": "Hello"},
    "n": 3,
    "flag": True,
    "items": [{"v": "one"}, {"v": "two"}],
}


def _apply_one(rule: dict, body: dict | None = None) -> tuple[dict, list[dict]]:
    return apply_body_rules_report(body or BASE, [rule], {})


@pytest.mark.parametrize(
    ("rule", "path", "expected"),
    [
        ({"mode": "create", "path": "c", "value": 1}, "c", 1),
        ({"mode": "update", "path": "a.b", "value": "world"}, "a.b", "world"),
        ({"mode": "delete", "path": "flag"}, "flag", "missing"),
        ({"mode": "copy", "from": "a.b", "to": "d"}, "d", "Hello"),
        ({"mode": "prepend", "path": "a.b", "value": ">> "}, "a.b", ">> Hello"),
        ({"mode": "append", "path": "a.b", "value": " <<"}, "a.b", "Hello <<"),
        (
            {"mode": "regex_replace", "path": "a.b", "pattern": "l+", "replacement": "L"},
            "a.b",
            "HeLo",
        ),
        ({"mode": "to_upper", "path": "a.b"}, "a.b", "HELLO"),
        ({"mode": "to_lower", "path": "a.b"}, "a.b", "hello"),
    ],
)
def test_each_body_action_changes_body(rule, path, expected):
    body, skipped = _apply_one(rule)

    assert skipped == []
    if expected == "missing":
        current = body
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = "missing"
                break
            current = current[part]
        assert current == "missing"
    else:
        current = body
        for part in path.split("."):
            current = current[part]
        assert current == expected


@pytest.mark.parametrize(
    ("rule", "reason"),
    [
        ({"mode": "create", "path": "a.b", "value": "x"}, "path_exists"),
        ({"mode": "update", "path": "missing.path", "value": "x"}, "path_missing"),
        ({"mode": "copy", "from": "missing", "to": "d"}, "path_missing"),
        ({"mode": "copy", "from": "a.b", "to": "a.b"}, "same_path"),
        ({"mode": "prepend", "path": "n", "value": "x"}, "type_mismatch"),
        ({"mode": "append", "path": "n", "value": "x"}, "type_mismatch"),
        ({"mode": "to_upper", "path": "flag"}, "type_mismatch"),
        ({"mode": "to_lower", "path": "items"}, "type_mismatch"),
        (
            {"mode": "regex_replace", "path": "a.b", "pattern": "(", "replacement": "x"},
            "invalid_regex",
        ),
        ({"mode": "set", "path": "a.b", "value": "x"}, "unsupported_mode"),
        (
            {
                "mode": "update",
                "path": "a.b",
                "value": "x",
                "conditions": [{"path": "model", "mode": "contains", "value": "x"}],
            },
            "condition_unsupported",
        ),
        (
            {
                "mode": "update",
                "path": "a.b",
                "value": "x",
                "conditions": [{"path": "model", "mode": "full", "value": "other"}],
            },
            "condition_not_met",
        ),
        (
            {
                "mode": "create",
                "path": "a.b.c",
                "value": "x",
            },
            "error",
        ),
    ],
)
def test_skip_reason_matrix(rule, reason):
    _, skipped = _apply_one(rule)

    assert [item["reason"] for item in skipped] == [reason]


def test_delete_missing_is_a_successful_no_op():
    body, skipped = _apply_one({"mode": "delete", "path": "missing"})

    assert skipped == []
    assert body == BASE


def test_report_keeps_applying_after_a_skipped_rule():
    rules = [
        {"mode": "create", "path": "a.b", "value": "ignored"},
        {"mode": "create", "path": "added", "value": "yes"},
    ]
    body, skipped = apply_body_rules_report(BASE, rules, {})

    assert body["a"]["b"] == "Hello"
    assert body["added"] == "yes"
    assert skipped == [
        {"index": 0, "mode": "create", "path": "a.b", "reason": "path_exists"}
    ]


def test_wrapper_returns_body_without_report():
    body = apply_body_rules(BASE, [{"mode": "update", "path": "a.b", "value": "ok"}], {})

    assert body["a"]["b"] == "ok"
    assert BASE["a"]["b"] == "Hello"
