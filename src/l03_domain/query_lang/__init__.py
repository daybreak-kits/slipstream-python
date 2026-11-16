"""FOFA-style query language for the log search box.

The public surface stays small on purpose: :func:`parse` and :func:`has_syntax`
on the request path, :func:`compile_query` in the repository layer. Nodes, fields
and tokens are exported for tests and for the ADR, not for routers to lean on.

Usage::

    from l03_domain.query_lang import compile_query, parse

    node = parse(request.query)                 # strict iff it carries grammar
    expression = compile_query(node, columns, attempts=SqliteAttempts(...))

The compiled value is a SQLAlchemy expression, never a SQL string; see
``docs/adr/ADR-071-search-query-language.md`` for the grammar and the
reasons behind the field table.
"""

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
from l03_domain.query_lang.compiler import (
    DEFAULT_HOOKS,
    DEFAULT_SEARCH_ATTEMPT_PATHS,
    AttemptMatch,
    AttemptResolver,
    DialectHooks,
    compile_query,
)
from l03_domain.query_lang.errors import QueryError
from l03_domain.query_lang.fields import (
    DEFAULT_SEARCH_COLUMNS,
    FIELD_ALIASES,
    FIELDS,
    Field,
    allowed_ops,
    field_names,
)
from l03_domain.query_lang.parser import (
    MAX_DEPTH,
    MAX_PREDICATES,
    MAX_QUERY_LENGTH,
    has_syntax,
    parse,
    parse_query,
)

__all__ = [
    "And",
    "AttemptMatch",
    "AttemptResolver",
    "Bareword",
    "Comparison",
    "DEFAULT_HOOKS",
    "DEFAULT_SEARCH_ATTEMPT_PATHS",
    "DEFAULT_SEARCH_COLUMNS",
    "DialectHooks",
    "FIELDS",
    "FIELD_ALIASES",
    "Field",
    "MAX_DEPTH",
    "MAX_PREDICATES",
    "MAX_QUERY_LENGTH",
    "Node",
    "Not",
    "Or",
    "QueryError",
    "allowed_ops",
    "compile_query",
    "count_predicates",
    "depth",
    "field_names",
    "has_syntax",
    "iter_predicates",
    "parse",
    "parse_query",
]
