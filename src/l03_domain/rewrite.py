from __future__ import annotations

import copy
import re
from typing import Any

MISSING = object()

BODY_MODES = {
    "create",
    "update",
    "delete",
    "copy",
    "prepend",
    "append",
    "regex_replace",
    "to_upper",
    "to_lower",
}
HEADER_MODES = {"set", "delete", "rename"}
CONDITION_MODES = {"full", "regex", "gt", "gte", "lt", "lte"}

LEGACY_BODY_MODES = {
    "set",
    "move",
    "trim_prefix",
    "trim_suffix",
    "ensure_prefix",
    "ensure_suffix",
    "trim_space",
    "replace",
}
LEGACY_CONDITION_MODES = {"prefix", "suffix", "contains"}

# Credential-bearing headers are owned by the channel configuration. Rules can
# never set, delete or rename them (or rename another header onto them), even
# when the stored rule set was written before this guard existed.
# Protocol/transport and gateway-identity headers are protected for the same
# reason: request rules run *after* the gateway fixed them, so rewriting
# content-type/content-length/host would break the wire contract and rewriting
# x-request-id would break tracing.
PROTECTED_HEADERS = frozenset(
    {
        "authorization",
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "content-type",
        "content-length",
        "host",
        "x-request-id",
    }
)
# Response rules keep their pre-existing reach (credentials only): the gateway's
# own SSE headers are enforced even later, in the API layer.
PROTECTED_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "api-key", "x-api-key", "cookie", "set-cookie"}
)


def _protected_headers(scope: str) -> frozenset[str]:
    return PROTECTED_CREDENTIAL_HEADERS if scope == "response" else PROTECTED_HEADERS


_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def is_protected_header(name: str, scope: str = "request") -> bool:
    return str(name or "").strip().lower() in _protected_headers(scope)


def _header_key(headers: dict[str, str], name: str) -> str | None:
    """HTTP header names are case-insensitive; return the stored spelling."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for existing in headers:
        if existing.lower() == wanted:
            return existing
    return None


def split_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for segment in str(path or "").split("."):
        if segment == "":
            continue
        if re.fullmatch(r"-?\d+", segment):
            parts.append(int(segment))
        else:
            parts.append(segment)
    return parts


def get_path(data: Any, path: str) -> Any:
    current = data
    for part in split_path(path):
        if isinstance(part, int):
            if not isinstance(current, list):
                return MISSING
            if part < 0:
                part = len(current) + part
            if part < 0 or part >= len(current):
                return MISSING
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return MISSING
            current = current[part]
    return current


def set_path(data: Any, path: str, value: Any) -> None:
    parts = split_path(path)
    if not parts:
        raise ValueError("路径不能为空")
    current = data
    for index, part in enumerate(parts[:-1]):
        nxt = parts[index + 1]
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ValueError(f"路径 {path} 不是数组")
            if part < 0:
                part = len(current) + part
            while len(current) <= part:
                current.append({} if not isinstance(nxt, int) else [])
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise ValueError(f"路径 {path} 不是对象")
            if part not in current or current[part] is None:
                current[part] = [] if isinstance(nxt, int) else {}
            current = current[part]
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise ValueError(f"路径 {path} 不是数组")
        if last < 0:
            last = len(current) + last
        while len(current) <= last:
            current.append(None)
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise ValueError(f"路径 {path} 不是对象")
        current[last] = value


def delete_path(data: Any, path: str) -> bool:
    parts = split_path(path)
    if not parts:
        return False
    parent = get_path(data, ".".join(str(p) for p in parts[:-1])) if len(parts) > 1 else data
    if parent is MISSING:
        return False
    last = parts[-1]
    if isinstance(last, int) and isinstance(parent, list):
        if last < 0:
            last = len(parent) + last
        if 0 <= last < len(parent):
            parent.pop(last)
            return True
        return False
    if isinstance(last, str) and isinstance(parent, dict) and last in parent:
        parent.pop(last)
        return True
    return False


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _coerce_result(original: Any, value: Any) -> Any:
    if isinstance(original, str):
        return _as_text(value)
    if isinstance(original, (int, float)) and not isinstance(original, bool) and isinstance(value, str):
        try:
            return type(original)(value)
        except ValueError:
            return value
    return value


def _resolve(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return re.sub(
            r"\$\{([A-Za-z0-9_]+)\}",
            lambda m: _as_text(variables.get(m.group(1), m.group(0))),
            value,
        )
    return value


def condition_matches(condition: dict[str, Any], body: dict[str, Any], variables: dict[str, Any]) -> bool:
    path = str(condition.get("path") or "")
    if path in variables:
        actual = variables[path]
    else:
        actual = get_path(body, path) if path else body
    missing = actual is MISSING
    mode = str(condition.get("mode") or "full")
    expected = condition.get("value")
    passed = False
    if missing:
        passed = bool(condition.get("pass_missing_key", False))
    elif mode == "full":
        passed = actual == expected
    elif mode == "regex":
        try:
            passed = re.search(_as_text(expected), _as_text(actual)) is not None
        except re.error:
            passed = False
    elif mode in {"gt", "gte", "lt", "lte"}:
        try:
            left, right = float(actual), float(expected)
            passed = {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[mode]
        except (TypeError, ValueError):
            passed = False
    if condition.get("invert"):
        return not passed
    return passed


def _conditions_ok(rule: dict[str, Any], body: dict[str, Any], variables: dict[str, Any]) -> bool:
    conditions = rule.get("conditions") or []
    if not conditions:
        return True
    valid = [c for c in conditions if isinstance(c, dict)]
    if not valid:
        return False
    results = [condition_matches(c, body, variables) for c in valid]
    logic = str(rule.get("logic") or "AND").upper()
    return any(results) if logic == "OR" else all(results)


def _rule_result(value: Any, changed: bool, skipped_reason: str = "") -> dict[str, Any]:
    return {"value": value, "changed": changed, "skipped_reason": skipped_reason}


def _apply_rule(body: dict[str, Any], rule: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    """Apply one body rule and report the outcome.

    ``skipped_reason`` is an empty string on success, otherwise one of
    ``path_exists``, ``path_missing``, ``same_path`` or ``type_mismatch``.
    """
    mode = str(rule.get("mode") or "")
    path = str(rule.get("path") or "")
    src = str(rule.get("from") or rule.get("source") or "")
    dst = str(rule.get("to") or rule.get("target") or "")
    value = _resolve(rule.get("value"), variables)

    if mode == "create":
        existing = get_path(body, path)
        if existing is not MISSING:
            return _rule_result(existing, False, "path_exists")
        set_path(body, path, value)
        return _rule_result(value, True)

    if mode == "update":
        existing = get_path(body, path)
        if existing is MISSING:
            return _rule_result(MISSING, False, "path_missing")
        set_path(body, path, value)
        return _rule_result(value, value != existing)

    if mode == "delete":
        existing = get_path(body, path)
        if existing is MISSING:
            return _rule_result(MISSING, False)
        delete_path(body, path)
        return _rule_result(MISSING, True)

    if mode == "copy":
        if not src or not dst:
            return _rule_result(MISSING, False, "path_missing")
        if src == dst:
            return _rule_result(MISSING, False, "same_path")
        existing = get_path(body, src)
        if existing is MISSING:
            return _rule_result(MISSING, False, "path_missing")
        set_path(body, dst, copy.deepcopy(existing))
        return _rule_result(existing, True)

    if mode in {"prepend", "append"}:
        existing = get_path(body, path)
        if not isinstance(existing, str):
            return _rule_result(existing, False, "type_mismatch")
        text = _as_text(value)
        updated = text + existing if mode == "prepend" else existing + text
        set_path(body, path, updated)
        return _rule_result(updated, updated != existing)

    if mode == "regex_replace":
        existing = get_path(body, path)
        if not isinstance(existing, str):
            return _rule_result(existing, False, "type_mismatch")
        pattern = _as_text(rule.get("pattern", rule.get("value", "")))
        replacement = _as_text(
            _resolve(rule.get("replacement", rule.get("with", "")), variables)
        )
        try:
            updated = re.sub(pattern, replacement, existing)
        except re.error:
            return _rule_result(existing, False, "invalid_regex")
        set_path(body, path, updated)
        return _rule_result(updated, updated != existing)

    if mode in {"to_upper", "to_lower"}:
        existing = get_path(body, path)
        if not isinstance(existing, str):
            return _rule_result(existing, False, "type_mismatch")
        updated = existing.upper() if mode == "to_upper" else existing.lower()
        set_path(body, path, updated)
        return _rule_result(updated, updated != existing)

    raise ValueError(f"\u672a\u77e5\u7684\u6539\u5199\u6a21\u5f0f\uff1a{mode}")


def _has_unsupported_condition(rule: dict[str, Any]) -> bool:
    for condition in rule.get("conditions") or []:
        if isinstance(condition, dict) and str(condition.get("mode") or "full") not in CONDITION_MODES:
            return True
    return False


def apply_body_rules_report(
    body: dict[str, Any],
    rules: list[dict[str, Any]],
    variables: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply rules to a deep copy and return ``(body, skipped_rules)``."""
    result = copy.deepcopy(body)
    skipped: list[dict[str, Any]] = []
    for index, rule in enumerate(rules or []):
        if not isinstance(rule, dict):
            continue
        mode = str(rule.get("mode") or "")
        if mode not in BODY_MODES:
            skipped.append(
                {
                    "index": index,
                    "mode": mode,
                    "path": str(rule.get("path") or rule.get("from") or ""),
                    "reason": "unsupported_mode",
                }
            )
            continue
        if _has_unsupported_condition(rule):
            skipped.append(
                {
                    "index": index,
                    "mode": mode,
                    "path": str(rule.get("path") or rule.get("from") or ""),
                    "reason": "condition_unsupported",
                }
            )
            continue
        if not _conditions_ok(rule, result, variables):
            skipped.append(
                {
                    "index": index,
                    "mode": mode,
                    "path": str(rule.get("path") or rule.get("from") or ""),
                    "reason": "condition_not_met",
                }
            )
            continue
        try:
            outcome = _apply_rule(result, rule, variables)
        except (KeyError, TypeError, ValueError) as error:
            skipped.append(
                {
                    "index": index,
                    "mode": mode,
                    "path": str(rule.get("path") or rule.get("from") or ""),
                    "reason": "error",
                    "message": str(error),
                }
            )
            continue
        if outcome.get("skipped_reason"):
            skipped.append(
                {
                    "index": index,
                    "mode": mode,
                    "path": str(rule.get("path") or rule.get("from") or ""),
                    "reason": outcome["skipped_reason"],
                }
            )
    return result, skipped


def apply_body_rules(body: dict[str, Any], rules: list[dict[str, Any]], variables: dict[str, Any]) -> dict[str, Any]:
    """Apply rules to a deep copy; returns the changed body and is safe for logging."""
    result, _ = apply_body_rules_report(body, rules, variables)
    return result


def apply_header_rules(
    headers: dict[str, str],
    rules: list[dict[str, Any]],
    variables: dict[str, Any],
    body: dict[str, Any] | None = None,
    *,
    scope: str = "request",
) -> dict[str, str]:
    """Apply set/delete/rename rules while never touching protected headers.

    Names are matched case-insensitively, so ``authorization`` and
    ``Authorization`` are the same header and both are protected. Request
    rules cannot touch the protocol/transport or gateway-identity headers
    either; response rules keep the credential-only guard (``scope="response"``).
    """
    result = dict(headers)
    condition_body = body or {}
    for rule in rules or []:
        if not isinstance(rule, dict) or not _conditions_ok(rule, condition_body, variables):
            continue
        mode = str(rule.get("mode") or "")
        name = str(rule.get("path") or "")
        if not name or is_protected_header(name, scope):
            continue
        existing = _header_key(result, name)
        if mode == "set":
            result[existing or name] = _as_text(_resolve(rule.get("value"), variables))
        elif mode == "delete":
            if existing:
                result.pop(existing, None)
        elif mode == "rename":
            to = str(rule.get("to") or "")
            if not to or is_protected_header(to, scope) or not existing:
                continue
            target = _header_key(result, to) or to
            result[target] = result.pop(existing)
    return result


MISSING_PLACEHOLDER = "<missing>"


def _preview(value: Any, limit: int = 200) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, list) and len(value) > 20:
        return value[:20] + ["…"]
    if isinstance(value, dict) and len(value) > 20:
        return {key: value[key] for key in list(value)[:20]}
    return value


def diff_body(before: Any, after: Any, path: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Human-readable leaf diff used for the request log (values are previews)."""
    changes: list[dict[str, Any]] = []

    def walk(left: Any, right: Any, current: str) -> None:
        if len(changes) >= limit:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in list(left) + [k for k in right if k not in left]:
                child = f"{current}.{key}" if current else str(key)
                if key not in left:
                    changes.append({"path": child, "before": MISSING_PLACEHOLDER, "after": _preview(right[key])})
                elif key not in right:
                    changes.append({"path": child, "before": _preview(left[key]), "after": MISSING_PLACEHOLDER})
                else:
                    walk(left[key], right[key], child)
            return
        if isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                child = f"{current}.{index}" if current else str(index)
                if index >= len(left):
                    changes.append({"path": child, "before": MISSING_PLACEHOLDER, "after": _preview(right[index])})
                elif index >= len(right):
                    changes.append({"path": child, "before": _preview(left[index]), "after": MISSING_PLACEHOLDER})
                else:
                    walk(left[index], right[index], child)
            return
        if left != right:
            changes.append({"path": current, "before": _preview(left), "after": _preview(right)})

    walk(before, after, path)
    return changes


def validate_rules(rules: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(rules, dict):
        return ["改写规则必须是对象"]
    sections = {
        "request_headers": HEADER_MODES,
        "request_body": BODY_MODES,
        "response_headers": {"set", "delete"},
        "response_body": BODY_MODES,
    }
    for section in rules:
        if section not in sections:
            errors.append(f"未知的改写作用段：{section}")
    for section, allowed in sections.items():
        items = rules.get(section) or []
        if not isinstance(items, list):
            errors.append(f"{section} 必须是数组")
            continue
        for index, rule in enumerate(items):
            label = f"{section}[{index}]"
            if not isinstance(rule, dict):
                errors.append(f"{label} 必须是对象")
                continue
            mode = str(rule.get("mode") or "")
            if mode in LEGACY_BODY_MODES and section.endswith("body"):
                errors.append(
                    f"{label} 旧规则不再支持动作 {mode}，请改用 "
                    "create/update/delete/copy/prepend/append/regex_replace/to_upper/to_lower"
                )
                continue
            if mode not in allowed:
                errors.append(f"{label} 未知模式：{mode}")
                continue

            if section.endswith("headers"):
                path = rule.get("path") or (rule.get("from") if mode == "rename" else None)
                if not path:
                    errors.append(f"{label} 缺少 path/from")
                    continue
                name = str(path)
                scope = "response" if section.startswith("response") else "request"
                if not _HEADER_NAME_RE.match(name):
                    errors.append(f"{label} 请求头名称不合法：{name}")
                if is_protected_header(name, scope):
                    errors.append(f"{label} 请求头 {name} 受保护，不允许被改写")
                if mode == "rename":
                    to = str(rule.get("to") or "")
                    if not to:
                        errors.append(f"{label} rename 缺少 to")
                    elif is_protected_header(to, scope):
                        errors.append(f"{label} 不允许把请求头改名为受保护请求头 {to}")
                    elif not _HEADER_NAME_RE.match(to):
                        errors.append(f"{label} 目标请求头名称不合法：{to}")
                if mode == "set" and "value" not in rule:
                    errors.append(f"{label} set 缺少 value")
                continue

            if mode == "copy":
                source = str(rule.get("from") or rule.get("source") or "")
                target = str(rule.get("to") or rule.get("target") or "")
                if not source:
                    errors.append(f"{label} copy 缺少 from")
                if not target:
                    errors.append(f"{label} copy 缺少 to")
                if source and source == target:
                    errors.append(f"{label} copy 的源路径和目标路径不能相同")
            else:
                path = str(rule.get("path") or "")
                if not path:
                    errors.append(f"{label} 缺少 path")
                    continue
                if mode in {"create", "update"} and "value" not in rule:
                    errors.append(f"{label} {mode} 缺少 value")
                if mode in {"prepend", "append"}:
                    if "value" not in rule:
                        errors.append(f"{label} {mode} 缺少 value")
                    elif not isinstance(rule.get("value"), str):
                        errors.append(f"{label} {mode} 的 value 必须是字符串")
                if mode == "regex_replace":
                    pattern = rule.get("pattern", rule.get("value"))
                    if pattern is None:
                        errors.append(f"{label} regex_replace 缺少 pattern")
                    else:
                        try:
                            re.compile(_as_text(pattern))
                        except re.error:
                            errors.append(f"{label} 替换正则不合法")

            logic = str(rule.get("logic") or "AND").upper()
            if logic not in {"AND", "OR"}:
                errors.append(f"{label} logic 仅支持 AND 或 OR")
            conditions = rule.get("conditions") or []
            if not isinstance(conditions, list):
                errors.append(f"{label} conditions 必须是数组")
                continue
            for condition in conditions:
                if not isinstance(condition, dict):
                    errors.append(f"{label} 条件必须是对象")
                    continue
                condition_mode = str(condition.get("mode") or "full")
                if condition_mode in LEGACY_CONDITION_MODES:
                    errors.append(
                        f"{label} 旧条件不再支持 {condition_mode}，请改用 full/regex/gt/gte/lt/lte"
                    )
                    continue
                if condition_mode not in CONDITION_MODES:
                    errors.append(f"{label} 条件模式不合法")
                if not condition.get("path"):
                    errors.append(f"{label} 条件缺少 path")
                if condition_mode == "regex":
                    try:
                        re.compile(_as_text(condition.get("value")))
                    except re.error:
                        errors.append(f"{label} 条件正则不合法")
    return errors
