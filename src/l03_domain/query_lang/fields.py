"""The field table: what a query may filter on, and how its values coerce.

One registry answers three questions at once -- is this a field (the parser's
whitelist), which operators does it accept, and which column does the compiler
read. Splitting those apart would let a field be parsed but never compiled,
which is the kind of gap that only surfaces in production.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from l01_core.timeutil import now_ms
from l03_domain.query_lang.errors import QueryError

#: Bare ``YYYY-MM-DD`` values are read in the product's timezone, not UTC: an
#: operator typing "2026-11-01" means local midnight, the same midnight the filter
#: bar's date picker shows.
DISPLAY_TZ = "Asia/Shanghai"

#: Text fields compare case-insensitively. ``=`` is "contains" and ``*=`` adds the
#: ``*``/``?`` wildcards; ``%``/``_`` stay literal (ADR-044).
TEXT_OPS = frozenset({"==", "!=", "=", "*="})
BOOL_OPS = frozenset({"==", "!="})
#: Numeric and time fields are ordered, never substring-matched: ``status=2``
#: would otherwise silently match 200/201/204.
ORDER_OPS = frozenset({"==", "!=", ">", ">=", "<", "<="})

_KIND_LABEL = {
    "text": "文本",
    "int": "整数",
    "float": "小数",
    "ms": "毫秒数",
    "time": "时间",
    "bool": "布尔值",
}
_DURATION_UNITS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
_DURATION_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h|d)$", re.IGNORECASE)
_TRUE_WORDS = frozenset({"true", "1", "yes", "on", "是"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off", "否"})


@dataclass(frozen=True, slots=True)
class Field:
    """One searchable field and the column(s) that back it."""

    name: str
    column: str
    kind: str
    #: json1 paths inside ``attempts_json``; when set, the caller may inject an
    #: :class:`AttemptResolver` so a fail-over member matches too.
    attempt_paths: tuple[str, ...] = ()
    #: Columns OR-ed with ``column`` when the caller supplies them (a channel may
    #: be named by display name or by final channel-model id).
    extra_columns: tuple[str, ...] = ()
    hint: str = ""

    @property
    def operators(self) -> frozenset[str]:
        if self.kind == "text":
            return TEXT_OPS
        if self.kind == "bool":
            return BOOL_OPS
        return ORDER_OPS


#: The default-search columns a bareword reaches. This is the same reach the log
#: list has today (ids, models, channel, key label, user, error text); callers may
#: override it with ``search_columns=`` when their table differs.
DEFAULT_SEARCH_COLUMNS: tuple[str, ...] = (
    "request_id",
    "upstream_request_id",
    "requested_model",
    "channel_name",
    "upstream_model",
    "response_model",
    "api_key_label",
    "api_key_id",
    "user_id",
    "error_code",
    "error_message",
)

FIELDS: dict[str, Field] = {
    "model": Field("model", "requested_model", "text", hint="请求模型或组合名"),
    "upstream": Field(
        "upstream",
        "upstream_model",
        "text",
        attempt_paths=("$.upstream_model",),
        hint="上游模型名",
    ),
    "channel": Field(
        "channel",
        "channel_name",
        "text",
        attempt_paths=("$.channel_name", "$.channel_slug"),
        extra_columns=("final_channel_model_id",),
        hint="渠道名或渠道模型 id",
    ),
    "channel_model": Field(
        "channel_model",
        "final_channel_model_id",
        "text",
        attempt_paths=("$.channel_model_id",),
        hint="最终渠道模型 id",
    ),
    "status": Field("status", "http_status", "int", hint="HTTP 状态码"),
    "error": Field(
        "error",
        "error_code",
        "text",
        attempt_paths=("$.error_code",),
        hint="错误码，如 NO_AVAILABLE_CHANNEL",
    ),
    "msg": Field(
        "msg",
        "error_message",
        "text",
        attempt_paths=("$.message",),
        hint="错误正文",
    ),
    "key": Field(
        "key",
        "api_key_label",
        "text",
        extra_columns=("api_key_id",),
        hint="密钥标识",
    ),
    "user": Field("user", "user_id", "text", hint="用户 id"),
    "stream": Field("stream", "is_stream", "bool", hint="是否流式"),
    "source": Field("source", "source", "text", hint="chat 或 probe"),
    "latency": Field("latency", "total_ms", "ms", hint="总耗时"),
    "ttft": Field("ttft", "ttft_ms", "ms", hint="首字耗时"),
    "ttat": Field("ttat", "ttfa_ms", "ms", hint="首答耗时（正文或工具调用；仅推理为空）"),
    "tokens": Field(
        "tokens",
        "tokens_total",
        "int",
        hint="输入+输出 token 数（调用方传两个 token 列的和）",
    ),
    "cost": Field("cost", "cost_usd", "float", hint="费用（美元）"),
    "time": Field("time", "created_at", "time", hint="请求时间"),
}

#: 真实列名 → 主名的别名（task-126）：按数据库字段名写也不再 400。别名与主名
#: 同列同谓词，has_syntax 同样认别名；语法帮助与错误建议仍按主名展示。
FIELD_ALIASES: dict[str, str] = {
    "upstream_model": "upstream",
    "requested_model": "model",
    "channel_name": "channel",
    "error_code": "error",
    "error_message": "msg",
    "http_status": "status",
    "total_ms": "latency",
    "ttft_ms": "ttft",
    "ttfa_ms": "ttat",
    "created_at": "time",
    "is_stream": "stream",
    "api_key_label": "key",
    "user_id": "user",
}


def field_names() -> tuple[str, ...]:
    """Field names in registry order; used by docs and error suggestions."""
    return tuple(FIELDS)


def allowed_ops(field: Field) -> frozenset[str]:
    """Operators this field accepts; the parser rejects everything else."""
    return field.operators


def operator_hint(field: Field) -> str:
    return "可用运算符：" + " ".join(sorted(field.operators))


def coerce(field: Field, op: str, raw: str, *, column: int) -> Any:
    """Turn the user's text into the value a bound parameter will carry.

    ``column`` is the 1-based position of the value in the query, so a type
    error points at the value instead of at the field name.
    """
    text = raw.strip()
    if field.kind == "text":
        if op in ("=", "*=") and text == "":
            raise QueryError(
                f"字段 {field.name} 的 {op} 需要一个非空值",
                column=column,
                suggestion=f"例如 {field.name}=gpt-4",
            )
        return raw
    if field.kind == "bool":
        return _parse_bool(field, text, raw, column)
    if field.kind == "int":
        return _parse_int(field, text, raw, column)
    if field.kind == "float":
        return _parse_float(field, text, raw, column)
    if field.kind == "ms":
        value = parse_duration_ms(text)
        if value is None:
            raise QueryError(
                f"字段 {field.name} 需要毫秒数，收到 {raw!r}",
                column=column,
                suggestion="支持 500（毫秒）、2s、1d 这类写法",
            )
        return value
    if field.kind == "time":
        value = parse_time_ms(text, now=now_ms())
        if value is None:
            raise QueryError(
                f"无法识别的时间值 {raw!r}",
                column=column,
                suggestion="支持 30d、24h 这类相对时间，或 2026-11-01、2026-11-01T12:00 这类绝对时间",
            )
        return value
    raise QueryError(
        f"字段 {field.name} 的类型 {field.kind} 尚不支持",
        column=column,
    )


def parse_duration_ms(text: str) -> int | None:
    """``500`` / ``2s`` / ``1.5d`` as milliseconds, or ``None``."""
    match = _DURATION_RE.match(text)
    if match is not None:
        number = float(match.group("number"))
        unit = match.group("unit").lower()
        return round(number * _DURATION_UNITS[unit])
    if text.isdigit():
        return int(text)
    return None


def parse_time_ms(text: str, *, now: int) -> int | None:
    """An epoch-millisecond instant for a relative or absolute time value.

    A bare duration ("7d") means "7 days before now" regardless of the operator,
    so ``time>7d`` reads "within the last week" and ``time<7d`` reads "older
    than a week"; the operator keeps its literal meaning.
    """
    duration = parse_duration_ms(text) if not text.isdigit() else None
    if duration is not None:
        return now - duration
    if text.isdigit():
        return int(text)
    return parse_timestamp_ms(text)


def parse_timestamp_ms(text: str) -> int | None:
    """``2026-11-01`` / ``2026-11-01T12:00`` in :data:`DISPLAY_TZ`, or ``None``."""
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(DISPLAY_TZ))
    return int(parsed.timestamp() * 1000)


def _parse_bool(field: Field, text: str, raw: str, column: int) -> bool:
    lowered = text.lower()
    if lowered in _TRUE_WORDS:
        return True
    if lowered in _FALSE_WORDS:
        return False
    raise QueryError(
        f"字段 {field.name} 需要 true/false，收到 {raw!r}",
        column=column,
        suggestion=f"写成 {field.name}==true 或 {field.name}==false",
    )


def _parse_int(field: Field, text: str, raw: str, column: int) -> int:
    try:
        return int(text)
    except ValueError:
        raise QueryError(
            f"字段 {field.name} 需要{_KIND_LABEL[field.kind]}，收到 {raw!r}",
            column=column,
            suggestion=f"例如 {field.name}>=400",
        ) from None


def _parse_float(field: Field, text: str, raw: str, column: int) -> float:
    candidate = text[1:].strip() if text.startswith("$") else text
    try:
        value = float(candidate)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise QueryError(
            f"字段 {field.name} 需要{_KIND_LABEL[field.kind]}，收到 {raw!r}",
            column=column,
            suggestion=f"例如 {field.name}>0.5",
        )
    return value
