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
