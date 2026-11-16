"""Pure failover-rule parsing and matching.

The top-level combo owns the rules; child combos only contribute members and
priority. A rule matches an upstream failure by HTTP status and, for non-stream
requests, an optional case-insensitive regex over the first 8 KB of the raw
response. ``stop`` wins over ``next`` when both would match; unmatched failures
fall back to ``default_action`` (or, when no rules are configured, to the
transport's ``retryable`` flag so existing behaviour is preserved).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["next", "stop"]
_BODY_LIMIT = 8192
_ACTION_ALIASES = {
    "next": "next",
    "switch": "next",
    "retry": "next",
    "stop": "stop",
    "abort": "stop",
}
_STATUS_TOKEN_RE = re.compile(r"^\s*(\d{3})(?:\s*-\s*(\d{3}))?\s*$")


@dataclass(frozen=True, slots=True)
class FailoverRule:
    """One switch rule; an empty ``status`` and ``body_regex`` matches anything."""

    statuses: tuple[str, ...] = ()
    body_regex: str | None = None
    action: Action = "next"

    def matches_status(self, status: int | None) -> bool:
        if not self.statuses:
            return True
        if status is None:
            return False
        for token in self.statuses:
            parsed = _parse_status_token(token)
            if parsed is None:
                continue
            start, end = parsed
            if start <= status <= end:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": [_status_value(token) for token in self.statuses],
            "body_regex": self.body_regex,
            "action": self.action,
        }


@dataclass(frozen=True, slots=True)
class FailoverRules:
    rules: tuple[FailoverRule, ...] = ()
    default_action: Action = "next"
    configured: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [rule.to_dict() for rule in self.rules],
            "default_action": self.default_action,
        }


EMPTY_RULES = FailoverRules()


def _parse_action(value: Any) -> Action | None:
    text = str(value or "").strip().lower()
    return _ACTION_ALIASES.get(text)  # type: ignore[return-value]


def _parse_status_token(value: Any) -> tuple[int, int] | None:
    match = _STATUS_TOKEN_RE.match(str(value))
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        return None
    return start, end


def _status_value(token: str) -> int | str:
    parsed = _parse_status_token(token)
    if parsed is not None and parsed[0] == parsed[1]:
        return parsed[0]
    return token


def _normalize_statuses(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, list) else [value]
    tokens: list[str] = []
    for item in items:
        parsed = _parse_status_token(item)
        if parsed is None:
            raise ValueError(f"无效的状态码规则：{item!r}")
        start, end = parsed
        tokens.append(str(start) if start == end else f"{start}-{end}")
    return tuple(tokens)


def _normalize_body_regex(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        re.compile(text, re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"无效的正则表达式：{error}") from error
    return text


def parse_failover_rules(raw: Any) -> FailoverRules:
    """Accept the documented object, a bare rule list, or already-loaded JSON.

    Malformed rules are ignored rather than breaking request routing; the admin
    API validates and rejects them at save time via :func:`normalize_failover_rules`.
    """
    if raw in (None, "", []):
        return EMPTY_RULES
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except Exception:
            return EMPTY_RULES
    if isinstance(raw, list):
        rules_raw: list[Any] = raw
        default_action: Action = "next"
    elif isinstance(raw, dict):
        rules_raw = raw.get("rules") if isinstance(raw.get("rules"), list) else []
        default_action = _parse_action(raw.get("default_action")) or "next"
    else:
        return EMPTY_RULES

    rules: list[FailoverRule] = []
    for item in rules_raw:
        if not isinstance(item, dict):
            continue
        try:
            statuses = _normalize_statuses(item.get("status"))
            body_regex = _normalize_body_regex(item.get("body_regex"))
        except ValueError:
            continue
        action = _parse_action(item.get("action")) or "next"
        rules.append(
            FailoverRule(statuses=statuses, body_regex=body_regex, action=action)
        )
    # The persisted shape has no explicit "configured" marker. An empty rule
    # list with the default "next" is indistinguishable from the default combo
    # value, so keep the transport fallback there; an explicit "stop" default
    # still means every unmatched failure must stop.
    return FailoverRules(
        rules=tuple(rules),
        default_action=default_action,
        configured=bool(rules) or default_action == "stop",
    )


def normalize_failover_rules(raw: Any) -> dict[str, Any]:
    """Validate an admin payload and return the canonical stored shape."""
    if raw is None:
        return EMPTY_RULES.to_dict()
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except Exception as error:
            raise ValueError("failover_rules 必须是合法的 JSON") from error
    if isinstance(raw, list):
        raw = {"rules": raw}
    if not isinstance(raw, dict):
        raise ValueError("failover_rules 必须是对象或规则数组")

    raw_rules = raw.get("rules") if raw.get("rules") is not None else []
    if not isinstance(raw_rules, list):
        raise ValueError("failover_rules.rules 必须是数组")
    if len(raw_rules) > 50:
        raise ValueError("failover_rules 最多 50 条")

    raw_default = raw.get("default_action")
    default_action = _parse_action(raw_default) if raw_default else "next"
    if default_action is None:
        raise ValueError("failover_rules.default_action 仅支持 next 或 stop")

    rules = []
    for index, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 条切换规则必须是对象")
        try:
            statuses = _normalize_statuses(item.get("status"))
        except ValueError as error:
            raise ValueError(f"第 {index + 1} 条切换规则：{error}") from error
        body_regex = _normalize_body_regex(item.get("body_regex"))
        action = _parse_action(item.get("action")) if item.get("action") else "next"
        if action is None:
            raise ValueError(f"第 {index + 1} 条切换规则的 action 仅支持 next 或 stop")
        rules.append(
            FailoverRule(statuses=statuses, body_regex=body_regex, action=action)
        )

    return FailoverRules(
        rules=tuple(rules), default_action=default_action, configured=True
    ).to_dict()


def choose_action(
    *,
    status_code: int | None,
    retryable: bool,
    body: str | None,
    rules: FailoverRules,
    is_stream: bool = False,
    tier: str | None = None,
) -> Action:
    """Decide whether to try the next candidate or stop the whole request.

    An explicit rule wins. Otherwise the error tier decides: a permanent
    upstream failure (dead credential, exhausted quota) rotates to another
    candidate because retrying the same key cannot help, while a client error
    stops immediately because every candidate would reject the request.
    """
    matches: list[Action] = []
    for rule in rules.rules:
        if not rule.matches_status(status_code):
            continue
        if rule.body_regex:
            # Streaming failures only match on status code.
            if is_stream or not body:
                continue
            try:
                if re.search(rule.body_regex, body[:_BODY_LIMIT], re.IGNORECASE) is None:
                    continue
            except re.error:
                continue
        matches.append(rule.action)

    if "stop" in matches:
        return "stop"
    if matches:
        return "next"
    if tier == "permanent":
        return "next"
    if tier == "client":
        return "stop"
    if rules.configured:
        return rules.default_action
    # No explicit rules: preserve the transport's classification.
    return "next" if retryable else "stop"
