"""Compact JSON encoding for hot paths: orjson when available, stdlib otherwise.

orjson output is byte-identical to the stdlib compact form (ensure_ascii=False,
separators=(",", ":") encoded UTF-8) for JSON-compatible values, except that
NaN/Infinity render as null and tiny floats print without a padded exponent
(same parsed value, different spelling). Any value orjson refuses (ints beyond
64 bits, Decimal, ...) falls back to the stdlib encoder so the behaviour never
changes."""

from __future__ import annotations

import json
from typing import Any

try:  # orjson is a pure accelerator; the stdlib path is the semantic baseline.
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None


def dumps_compact(value: Any) -> bytes:
    """Encode as compact UTF-8 JSON bytes, fast path via orjson."""
    if _orjson is not None:
        try:
            return _orjson.dumps(value)
        except (TypeError, ValueError):
            pass
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def loads(value: "str | bytes") -> Any:
    """Decode JSON from str or UTF-8 bytes, fast path via orjson.

    SSE 帧跟踪是每帧一次的纯解析热路径：bytes 直进解析器可以省掉整条
    流的逐行 decode。orjson 拒绝的输入回退 stdlib，行为不变化。
    """
    if _orjson is not None:
        try:
            return _orjson.loads(value)
        except ValueError:
            pass
    return json.loads(value)
