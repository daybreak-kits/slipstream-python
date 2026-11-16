"""The operation-log side of the search query language (ADR-071).

:mod:`l03_domain.query_lang` knows the grammar, the field table and the LIKE
semantics; it knows nothing about `request_logs`. This module is the other half
of the seam: it hands the compiler the log table's columns, resolves
attempt-level predicates over `attempts_json`, and widens the `model` filter so
it answers the question the operator is actually asking -- "show me this model"
means the winning model *or* a fail-over member of the combo that served it.

Nothing here is wired into the routers yet: :mod:`l04_repo.repo` keeps its
current filters until the search box switch lands, so both paths can be tested
side by side.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import and_, case, false, func, literal, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from l03_domain.query_lang import (
    DEFAULT_HOOKS,
    DEFAULT_SEARCH_COLUMNS,
    FIELDS,
    And,
    AttemptMatch,
    AttemptResolver,
    Comparison,
    Node,
    Not,
    Or,
    compile_query,
)
from l04_repo.orm import ChannelModelORM, ComboORM, ComboTargetORM, RequestLogORM
from l04_repo.search import like_contains

#: ``tokens`` is a field but not a column: operators compare the sum, which is what
#: "how many tokens did this request cost" means to a human. The compiler binds the
#: value and renders the sum, so no expression string ever reaches the database.
_COLUMN_EXPRESSIONS: dict[str, Any] = {
    "tokens_total": RequestLogORM.prompt_tokens + RequestLogORM.completion_tokens,
}


def _column_names() -> tuple[str, ...]:
    """Every name a compiled predicate or a bareword may ask for."""
    names: list[str] = []
    for field in FIELDS.values():
        names.append(field.column)
        names.extend(field.extra_columns)
    names.extend(DEFAULT_SEARCH_COLUMNS)
    return tuple(dict.fromkeys(names))


def _log_column(name: str) -> Any:
    expression = _COLUMN_EXPRESSIONS.get(name)
    if expression is not None:
        return expression
    return getattr(RequestLogORM, name)


#: The mapping ``compile_query`` needs for ``request_logs``: every
#: :class:`~l03_domain.query_lang.Field` column, the extra columns a field also
#: matches, the eleven default-search columns and the token sum.
LOG_COLUMNS: Mapping[str, Any] = {name: _log_column(name) for name in _column_names()}


def log_columns(search_columns: Iterable[str] | None = None) -> dict[str, Any]:
    """``LOG_COLUMNS`` narrowed/widened to a different bareword reach.

    The log list searches the same eleven columns as before; a probe table or a
    saved view can pass its own list without duplicating the field table.
    """
    columns = dict(LOG_COLUMNS)
    if search_columns is None:
        return columns
    for name in search_columns:
        columns.setdefault(name, _log_column(name))
    return columns


# --------------------------------------------------------------------------- #
# Attempt-level predicates (``attempts_json``)
# --------------------------------------------------------------------------- #

#: Text operators; the attempt trail only ever stores text, so an ordered
#: comparison (``msg>5``) has nothing to match there and degrades to the row.
_TEXT_OPS = frozenset({"==", "=", "*=", "!="})


def _wildcard_regex(pattern: str) -> re.Pattern[str]:
    """``*``/``?`` as a regex; ``%`` and ``_`` stay literal (ADR-044)."""
    parts: list[str] = []
    for char in pattern:
        if char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    return re.compile("(?s)" + "".join(parts) + "\\Z")


def _matches_text(candidate: str | None, op: str, value: str) -> bool:
    """The Python mirror of the compiler's text rules, used to read combo tables."""
    text = (candidate or "").lower()
    needle = value.lower()
    if op == "==":
        return text == needle
    if op == "=":
        return needle in text
    if op == "*=":
        return _wildcard_regex(needle).match(text) is not None
    if op == "!=":
        return text != needle
    return False


def attempt_rows(column: Any | None = None) -> Any:
    """``json_each(attempts_json)``, guarded against rows that are not JSON.

    ``json_each`` raises on malformed input (``malformed JSON`` from SQLite), and
    ``json_valid(NULL)`` is NULL rather than 0 -- hence the CASE, which is the
    same guard :func:`l04_repo.repo._attempt_json_rows` applies.
    """
    source = RequestLogORM.attempts_json if column is None else column
    guarded = case((func.json_valid(source) == 1, source), else_=literal("[]"))
    return func.json_each(guarded).table_valued("value")


class SqliteAttemptResolver:
    """``EXISTS(... json_each ...)`` for one :class:`AttemptMatch``.

    SQLite is the only dialect the operation log runs on today (sqlite+aiosqlite
    in dev, Postgres in the deployment story). The instance carries the dialect
    name so a Postgres caller can keep the same injection point and get ``None``
    back -- the predicate then degrades to the request columns, which is the
    documented fallback rather than a silently different answer.
    """

    def __init__(self, dialect: str = "sqlite", *, column: Any | None = None) -> None:
        self.dialect = dialect
        self._column = column

    def matches(self, match: AttemptMatch) -> Any | None:
        if self.dialect.split("+", 1)[0] != "sqlite":
            return None
        if not match.paths or match.op not in _TEXT_OPS:
            return None
        rows = attempt_rows(self._column)
        text = str(match.value)
        lowered = text.lower()

        def condition(extracted: Any) -> Any:
            if match.op == "=":
                return like_contains(extracted, text)
            if match.op == "*=":
                return DEFAULT_HOOKS.wildcard(extracted, text)
            # ``!=`` is answered by negating the equality match, so it asks
            # "did *any* attempt carry this value"; the compiler ANDs that in.
            return func.lower(extracted) == lowered

        matched = (
            select(literal(1))
            .select_from(rows)
            .where(or_(*[condition(func.json_extract(rows.c.value, path)) for path in match.paths]))
            .exists()
        )
        return not_(matched) if match.op == "!=" else matched


def attempt_resolver(dialect: str | None) -> AttemptResolver | None:
    """The resolver for ``dialect`, or ``None`` meaning "degrade, and log it".

    A caller that gets ``None`` must record the fallback (ADR-069's rule for the
    same situation): the query still runs, but attempt-level matches are lost, and
    losing them silently is how a search box starts lying.
    """
    from l04_repo import attempt_filters

    if attempt_filters.reads_attempt_rows():
        # ADR-072 stage 2: the predicates are plain SQL, so they no longer need
        # a json1-capable dialect and Postgres answers the full match too.
        return attempt_filters.attempt_resolver(dialect)
    if dialect is None:
        return SqliteAttemptResolver()
    if dialect.split("+", 1)[0] == "sqlite":
        return SqliteAttemptResolver(dialect)
    return None


# --------------------------------------------------------------------------- #
# Combo membership (``model=``)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ComboGraph:
    """Combos and the member models they can reach, read once per request."""

    names: Mapping[str, str]
    #: combo id -> member upstream models (lower-cased, child combos expanded).
    members: Mapping[str, tuple[str, ...]]

    def combos_with_member(self, value: str, op: str) -> tuple[str, ...]:
        """Combo ids that can answer ``value`` through at least one member."""
        found = [
            combo_id
            for combo_id, models in self.members.items()
            if any(_matches_text(model, op, value) for model in models)
        ]
        return tuple(sorted(found))


async def load_combo_graph(session: AsyncSession) -> ComboGraph:
    """Read ``combos``, ``combo_targets`` and ``channel_models`` into one graph.

    Disabled combos are included on purpose: a log row written last week was
    served by whatever was enabled then, and a filter that hides those rows
    looks like data loss to the operator reading them.
    """
    names: dict[str, str] = {}
    for combo_id, name in (await session.execute(select(ComboORM.id, ComboORM.name))).all():
        names[combo_id] = name

    model_names: dict[str, str] = {}
    for channel_model_id, upstream_model in (
        await session.execute(select(ChannelModelORM.id, ChannelModelORM.upstream_model))
    ).all():
        model_names[channel_model_id] = (upstream_model or "").lower()

    direct: dict[str, set[str]] = {combo_id: set() for combo_id in names}
    children: dict[str, list[str]] = {}
    targets = await session.execute(
        select(
            ComboTargetORM.combo_id,
            ComboTargetORM.target_type,
            ComboTargetORM.channel_model_id,
            ComboTargetORM.child_combo_id,
        )
    )
    for combo_id, target_type, channel_model_id, child_combo_id in targets.all():
        if target_type == "channel_model" and channel_model_id is not None:
            model = model_names.get(channel_model_id)
            if model:
                direct.setdefault(combo_id, set()).add(model)
        elif target_type == "combo" and child_combo_id is not None:
            children.setdefault(combo_id, []).append(child_combo_id)

    members: dict[str, tuple[str, ...]] = {}
    for combo_id in names:
        reached: set[str] = set()
        stack = [combo_id]
        seen = {combo_id}
        while stack:
            current = stack.pop()
            reached |= direct.get(current, set())
            for child in children.get(current, ()):  # nested combos are members too
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        members[combo_id] = tuple(sorted(reached))
    return ComboGraph(names=names, members=members)


def _positive_op(op: str) -> str:
    """The operator a ``!=`` filter negates; anything else is already positive."""
    return "==" if op == "!=" else op


@dataclass(frozen=True, slots=True)
class ModelMatch:
    """A prepared ``model`` filter: the typed value plus the combos answering it.

    Reading the combo tables needs a session; building the predicate does not.
    The split keeps :func:`l04_repo.repo._request_log_conditions` synchronous
    while still widening ``model`` to the three arms below.
    """

    value: str
    #: combos with a member matching the value; empty when the value names none.
    combo_ids: tuple[str, ...] = ()
    #: those combos' names, lower-cased -- older rows only carry the name.
    combo_names: tuple[str, ...] = ()


def _members(match: ModelMatch) -> Any:
    if not match.combo_ids:
        return false()
    return or_(
        RequestLogORM.resolved_combo_id.in_(match.combo_ids),
        # Rows written before `resolved_combo_id` existed only carry the combo
        # name in `requested_model`; the name is unique, so it is a safe stand-in.
        func.lower(RequestLogORM.requested_model).in_(match.combo_names),
    )


def _not_members(match: ModelMatch) -> Any:
    if not match.combo_ids:
        return true()
    return and_(
        or_(
            RequestLogORM.resolved_combo_id.is_(None),
            RequestLogORM.resolved_combo_id.notin_(match.combo_ids),
        ),
        func.lower(RequestLogORM.requested_model).notin_(match.combo_names),
    )


async def prepare_model_match(
    session: AsyncSession,
    value: str,
    *,
    op: str = "==",
    graph: ComboGraph | None = None,
) -> ModelMatch:
    """Resolve ``value`` against the combo tables once, for a later predicate."""
    text = str(value)
    if not text:
        return ModelMatch(value="")
    loaded = graph if graph is not None else await load_combo_graph(session)
    combo_ids = loaded.combos_with_member(text, _positive_op(op))
    names = tuple(sorted({loaded.names[combo_id].lower() for combo_id in combo_ids}))
    return ModelMatch(value=text, combo_ids=combo_ids, combo_names=names)


def model_match_condition(
    match: ModelMatch,
    *,
    op: str = "==",
    attempts: AttemptResolver | None = None,
) -> Any:
    """The widened ``model`` predicate, with no database access at all."""
    text = match.value
    node = Comparison(field="model", op=op, value=text, column=1, value_column=1)
    requested = compile_query(node, LOG_COLUMNS, attempts=attempts)
    upstream = compile_query(replace(node, field="upstream"), LOG_COLUMNS, attempts=attempts)
    if op == "!=":
        return and_(requested, upstream, _not_members(match))
    return or_(requested, upstream, _members(match))


async def model_match(
    session: AsyncSession,
    *,
    op: str,
    value: str,
    attempts: AttemptResolver | None = None,
    graph: ComboGraph | None = None,
) -> Any:
    """The widened ``model`` filter: requested, upstream, or combo member.

    The user's report is the reason this exists: filtering
    ``MiniMax-M3.1-Flash` returned four rows while more than seven thousand
    requests were served by a combo of that model, because those rows record the
    *combo* as their requested model. Three arms answer it:

    1. ``requested_model`` matches -- the plain answer, and the only one today;
    2. ``upstream_model`` matches -- the row won through a channel of that model;
    3. the row belongs to a combo with that model as a member -- the fail-over
       case, matched via ``resolved_combo_id` or the combo name.

    Operators keep their meaning: ``=`` is "contains", ``==`` is "equals",
    ``*=`` is wildcard, ``!=`` is "none of the three arms match" and stays
    NULL-inclusive like the compiler's own rule.
    """
    loaded = graph if graph is not None else await load_combo_graph(session)
    prepared = await prepare_model_match(session, value, op=op, graph=loaded)
    return model_match_condition(prepared, op=op, attempts=attempts)


# --------------------------------------------------------------------------- #
# Whole-tree compilation
# --------------------------------------------------------------------------- #


async def compile_log_filter(
    session: AsyncSession,
    node: Node,
    *,
    attempts: AttemptResolver | None = None,
    search_columns: Iterable[str] | None = None,
) -> Any:
    """Compile a parsed query into one ``request_logs`` predicate.

    The tree is compiled once against ``LOG_COLUMNS`` before anything touches the
    combo tables, so a type error or an unknown field still surfaces as the same
    :class:`~l03_domain.query_lang.QueryError` the plain compiler raises, with the
    same column and suggestion the UI already knows how to draw.
    """
    columns = log_columns(search_columns)
    compile_query(node, columns, attempts=attempts)
    graph = await load_combo_graph(session)
    return await _compile_node(session, node, attempts=attempts, graph=graph, columns=columns)


async def _compile_node(
    session: AsyncSession,
    node: Node,
    *,
    attempts: AttemptResolver | None,
    graph: ComboGraph,
    columns: Mapping[str, Any],
) -> Any:
    if isinstance(node, Comparison) and node.field == "model":
        return await model_match(
            session, op=node.op, value=node.value, attempts=attempts, graph=graph
        )
    if isinstance(node, And):
        return and_(
            *[
                await _compile_node(session, item, attempts=attempts, graph=graph, columns=columns)
                for item in node.items
            ]
        )
    if isinstance(node, Or):
        return or_(
            *[
                await _compile_node(session, item, attempts=attempts, graph=graph, columns=columns)
                for item in node.items
            ]
        )
    if isinstance(node, Not):
        return not_(
            await _compile_node(session, node.item, attempts=attempts, graph=graph, columns=columns)
        )
    return compile_query(node, columns, attempts=attempts)


__all__ = [
    "LOG_COLUMNS",
    "ComboGraph",
    "ModelMatch",
    "SqliteAttemptResolver",
    "attempt_resolver",
    "attempt_rows",
    "compile_log_filter",
    "load_combo_graph",
    "log_columns",
    "model_match",
    "model_match_condition",
    "prepare_model_match",
]
