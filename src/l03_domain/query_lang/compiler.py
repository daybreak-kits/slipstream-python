"""Compile a parsed query into a SQLAlchemy expression.

The output is an expression object, never a SQL string: the same query has to run
on SQLite and Postgres, and every user value has to reach the database as a bound
parameter -- a search box is the easiest place in a product to smuggle SQL.

Two seams keep the language reusable:

* ``columns`` -- the compiler never imports the ORM. The caller maps column names
  to instrumented attributes, so the operation log can compile the same query
  against a different table without touching the parser.
* :class:`AttemptResolver` -- attempt-level fields (member channel, member error
  message) live inside ``attempts_json``, whose query shape is dialect specific
  (json1 ``json_each`` on SQLite, ``jsonb_array_elements`` on Postgres). The caller
  injects the EXISTS(...) builder instead of the compiler guessing, so replacing
  the attempts table later leaves parser and operator set alone.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import and_, func, not_, or_

from l03_domain.query_lang.ast import (
    And,
    Bareword,
    Comparison,
    Node,
    Not,
    Or,
    count_predicates,
    depth,
    iter_predicates,
)
from l03_domain.query_lang.errors import QueryError
from l03_domain.query_lang.fields import (
    DEFAULT_SEARCH_COLUMNS,
    FIELDS,
    Field,
    coerce,
)
from l03_domain.query_lang.parser import MAX_DEPTH, MAX_PREDICATES

#: Ordered comparison for numeric and time fields; ``>`` on a column yields a
#: bound-parameter expression, not a formatted string. ``!="`` is absent on purpose:
#: it needs the NULL rule applied in :meth:`_Compiler._condition`.
_ORDER = {
    "==": operator.eq,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

#: json1 paths a keyword search also reaches, mirroring the attempt fields the
#: log list already searches (member channel, member model, failure).
DEFAULT_SEARCH_ATTEMPT_PATHS: tuple[str, ...] = (
    "$.channel_name",
    "$.channel_slug",
    "$.channel_model_id",
    "$.upstream_model",
    "$.error_code",
    "$.message",
)


@dataclass(frozen=True, slots=True)
class AttemptMatch:
    """One predicate the attempt trail is also allowed to satisfy."""

    paths: tuple[str, ...]
    op: str
    value: Any
    field: str


class DialectHooks(Protocol):
    """The LIKE shapes the compiler needs; the shipped defaults never vary by
    dialect (SQLAlchemy renders ``ilike`` per backend), so this seam exists for
    callers that want different escaping or a case-sensitive match."""

    def contains(self, column: Any, text: str) -> Any: ...

    def wildcard(self, column: Any, pattern: str) -> Any: ...


class AttemptResolver(Protocol):
    """Expand a predicate over the per-attempt trail stored on the row.

    A fail-over request records member channels and member error messages only
    inside ``attempts_json``; searching the winning columns alone would hide the
    hop that actually failed. Returning ``None`` means "this resolver cannot
    answer for this match" and the predicate degrades to the request columns.
    """

    def matches(self, match: AttemptMatch) -> Any | None: ...


class _DefaultHooks:
    """The shipped LIKE semantics: case-insensitive, SQL wildcards escaped.

    The escaping itself stays in :mod:`l04_repo.search` (ADR-044 owns the one
    definition of `%`/`_` handling). The import is lazy so the domain layer does
    not pull the repository layer in at import time -- the repository layer
    imports domain modules, and a module-level import here would close a cycle.
    """

    def contains(self, column: Any, text: str) -> Any:
        from l04_repo.search import like_contains

        return like_contains(column, text)

    def wildcard(self, column: Any, pattern: str) -> Any:
        from l04_repo.search import LIKE_ESCAPE

        return column.ilike(_wildcard_pattern(pattern), escape=LIKE_ESCAPE)


DEFAULT_HOOKS: DialectHooks = _DefaultHooks()


def _wildcard_pattern(pattern: str) -> str:
    """Turn ``*``/``?`` into LIKE wildcards, escaping everything else.

    The escape pass matters: ``model*=100%`` must look for a literal percent
    sign, otherwise the operator silently turns into an anything-matches filter.
    """
    out: list[str] = []
    for char in pattern:
        if char == "*":
            out.append("%")
        elif char == "?":
            out.append("_")
        elif char in ("%", "_", "\\"):
            out.append("\\" + char)
        else:
            out.append(char)
    return f"%{''.join(out)}%"


def compile_query(
    node: Node,
    columns: Mapping[str, Any],
    dialect_hooks: DialectHooks | None = None,
    *,
    attempts: AttemptResolver | None = None,
    search_columns: Sequence[str] | None = None,
) -> Any:
    """Compile ``node`` against ``columns`` into one SQLAlchemy expression.

    ``columns`` maps column names (``Field.column``, plus the default search
    columns) to either instrumented attributes or expressions such as
    ``prompt_tokens + completion_tokens``; a missing key is a caller bug and
    raises ``ValueError``, not a user-facing 400.
    """
    if count_predicates(node) > MAX_PREDICATES:
        raise QueryError(
            f"谓词超过 {MAX_PREDICATES} 个上限",
            column=_first_column(node),
            suggestion="先按时间范围或字段缩小查询，再分多次搜索",
        )
    if depth(node) > MAX_DEPTH:
        raise QueryError(
            f"表达式嵌套超过 {MAX_DEPTH} 层",
            column=_first_column(node),
            suggestion="把条件拆成几次搜索",
        )
    compiler = _Compiler(
        columns=columns,
        hooks=dialect_hooks if dialect_hooks is not None else DEFAULT_HOOKS,
        attempts=attempts,
        search_columns=search_columns,
    )
    return compiler.compile(node)


def _first_column(node: Node) -> int:
    predicates = iter_predicates(node)
    return predicates[0].column if predicates else 1


class _Compiler:
    """One-shot compiler; keeps no state between queries."""

    def __init__(
        self,
        *,
        columns: Mapping[str, Any],
        hooks: DialectHooks,
        attempts: AttemptResolver | None,
        search_columns: Sequence[str] | None,
    ) -> None:
        self._columns = columns
        self._hooks = hooks
        self._attempts = attempts
        self._search_columns = (
            tuple(search_columns) if search_columns is not None else DEFAULT_SEARCH_COLUMNS
        )

    def compile(self, node: Node) -> Any:
        if isinstance(node, Comparison):
            return self._comparison(node)
        if isinstance(node, Bareword):
            return self._bareword(node)
        if isinstance(node, And):
            return and_(*[self.compile(item) for item in node.items])
        if isinstance(node, Or):
            return or_(*[self.compile(item) for item in node.items])
        if isinstance(node, Not):
            return not_(self.compile(node.item))
        raise TypeError(f"unknown query node: {node!r}")

    def _column(self, name: str) -> Any:
        try:
            return self._columns[name]
        except KeyError:
            raise ValueError(
                f"query_lang: columns 缺少 {name!r}；调用方必须提供字段表与裸词搜索用到的列"
            ) from None

    def _comparison(self, node: Comparison) -> Any:
        field = FIELDS.get(node.field)
        if field is None:
            raise QueryError(
                f"未知字段 {node.field}",
                column=node.column,
                suggestion="字段表见 l03_domain.query_lang.fields",
            )
        value = coerce(
            field,
            node.op,
            node.value,
            column=node.value_column or node.column,
        )
        conditions = [self._condition(self._column(field.column), field, node.op, value)]
        for name in field.extra_columns:
            if name in self._columns:
                conditions.append(self._condition(self._columns[name], field, node.op, value))
        attempt_hit = self._attempt_condition(field.attempt_paths, node.op, value, field.name)
        if attempt_hit is None:
            if len(conditions) == 1:
                return conditions[0]
            return or_(*conditions)
        if node.op == "!=":
            # "Not equal" also has to mean "no attempt matched": OR-ing an
            # exists(attempt != value) would keep a row whose winning hop
            # differs while the hop that carried the value sits in the trail.
            # The resolver answers this op with NOT EXISTS.
            return and_(*conditions, attempt_hit)
        conditions.append(attempt_hit)
        return or_(*conditions)

    def _condition(self, column: Any, field: Field, op: str, value: Any) -> Any:
        if field.kind == "text":
            text = str(value)
            if op == "==":
                return func.lower(column) == text.lower()
            if op == "!=":
                # "Not equal" also covers rows with no value at all: an operator
                # typing msg!=timeout expects a row with an empty message to
                # match. Plain SQL would drop it (NULL != x is UNKNOWN), which
                # reads as "the filter ignored those rows".
                return or_(column.is_(None), func.lower(column) != text.lower())
            if op == "=":
                return self._hooks.contains(column, text)
            return self._hooks.wildcard(column, text)
        if field.kind == "bool":
            if op == "==":
                return column.is_(bool(value))
            return or_(column.is_(None), column.is_(not bool(value)))
        if op == "!=":
            # Same rule as text; ordering comparisons below keep plain
            # three-valued logic, so latency<3s still excludes empty rows.
            return or_(column.is_(None), column != value)
        return _ORDER[op](column, value)

    def _attempt_condition(
        self,
        paths: tuple[str, ...],
        op: str,
        value: Any,
        field: str,
    ) -> Any | None:
        if not paths or self._attempts is None:
            return None
        return self._attempts.matches(
            AttemptMatch(paths=paths, op=op, value=value, field=field)
        )

    def _bareword(self, node: Bareword) -> Any:
        conditions = [
            self._hooks.contains(self._column(name), node.text)
            for name in self._search_columns
        ]
        attempt_hit = self._attempt_condition(
            DEFAULT_SEARCH_ATTEMPT_PATHS, "=", node.text, ""
        )
        if attempt_hit is not None:
            conditions.append(attempt_hit)
        if not conditions:
            raise ValueError("query_lang: 裸词搜索没有可搜索的列")
        return or_(*conditions)
