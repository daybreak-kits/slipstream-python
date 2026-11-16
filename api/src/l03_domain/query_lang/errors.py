"""Errors raised while parsing or compiling a search query.

The parser has to point at the character the user got wrong: "语法错误" without a
column leaves the operator hunting for the typo in a sixty-character query.
Every error therefore carries a 1-based column, and `details` repeats it in the
API envelope so the UI can draw a caret instead of re-parsing the message.
"""

from __future__ import annotations

from typing import Any

from l01_core.errors import AppError, ErrorCode

#: A malformed query is an ordinary request error: the user typed something the
#: language does not accept, and retyping it is the fix.
QUERY_ERROR_CODE = ErrorCode.INVALID_REQUEST


class QueryError(AppError):
    """A query that could not be parsed or compiled.

    ``column`` is 1-based and counts characters in the query exactly as typed
    (not bytes); ``0`` means the query as a whole is at fault (empty query, over
    the length limit).
    """

    __slots__ = ("column", "suggestion")

    def __init__(
        self,
        message: str,
        *,
        column: int = 0,
        suggestion: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {"column": column}
        if suggestion:
            merged["suggestion"] = suggestion
        if details:
            merged.update(details)
        super().__init__(QUERY_ERROR_CODE, message, status=400, details=merged)
        self.column = column
        self.suggestion = suggestion
