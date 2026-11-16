"""Helpers for building safe SQL LIKE patterns.

Search boxes accept arbitrary text, so ``%`` and ``_`` must match literally
instead of acting as LIKE wildcards; otherwise a one-character query silently
turns into a full-table scan or matches unrelated rows.
"""

from __future__ import annotations

from typing import Any

LIKE_ESCAPE = "\\"


def like_pattern(text: str) -> str:
    """Return a case-insensitive "contains" pattern with wildcards escaped."""
    escaped = (
        text.strip()
        .replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def like_contains(column: Any, text: str) -> Any:
    """Match ``column`` against ``text`` case-insensitively, wildcards literal."""
    return column.ilike(like_pattern(text), escape=LIKE_ESCAPE)
