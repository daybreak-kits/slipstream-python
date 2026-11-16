"""Row storage for the per-attempt trail (ADR-072).

``request_logs.attempts_json`` carried one request's whole fail-over trail as
a JSON array. Every attempt-level filter had to expand that array with json1
(``json_each`` / ``json_extract``) before it could compare anything, which
measured 6-12x slower than the same equality on a scalar column (ADR-072).
This module is the one place that knows both shapes:

* the settings readers that decide which shape is written and read
  (``ATTEMPTS_WRITE`` / ``ATTEMPTS_SOURCE``, both defaulting to JSON so the
  change ships dark and moves one stage at a time);
* the converters between the API payload and an :class:`AttemptORM` row, so a
  caller never sees the storage change;
* the write helpers, which never commit on their own -- they run inside the
  transaction that writes or patches the parent row, so a failed request can
  never leave half a trail behind;
* the predicates and the query-language resolver the attempt filters compile
  to: ``EXISTS (SELECT 1 FROM attempts WHERE log_id = request_logs.id ...)``
  instead of json1 over the blob.

Read this module top-down: settings, payload conversion, writes, reads, then
the SQL predicates and the resolver.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, delete, exists, func, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core import runtime
from l01_core.ids import new_id
from l02_infra.dialect import POSTGRESQL, SQLITE, execute_with_lock_retry
from l03_domain.query_lang import DEFAULT_HOOKS
from l04_repo import orm
from l04_repo.dialects import conflict_insert, dialect_name
from l04_repo.search import like_contains

JSON_WRITE = "json"
ROWS_WRITE = "rows"
BOTH_WRITE = "both"
JSON_SOURCE = "json"
ROWS_SOURCE = "attempts"

#: Payload message limit -- the same cut ``chat._attempt`` applies, kept here so
#: a caller that hands over a raw payload cannot widen the stored column.
MESSAGE_LIMIT = 2000

#: Text operators the attempt trail answers; mirror of log_query._TEXT_OPS.
TEXT_OPS = frozenset({"==", "=", "*=", "!="})

#: json1 path -> attempts column. The payload keeps its JSON names (the web
#: client, ADR-071 query fields and every stored blob use them); only storage
#: changed, so the mapping is deliberately boring and lives in one dict.
ATTEMPT_COLUMNS: dict[str, Any] = {
    "channel_name": orm.AttemptORM.channel_name,
    "channel_slug": orm.AttemptORM.channel_slug,
    "channel_model_id": orm.AttemptORM.channel_model_id,
    "upstream_model": orm.AttemptORM.upstream_model,
    "error_code": orm.AttemptORM.error_code,
    "message": orm.AttemptORM.message,
    "http_status": orm.AttemptORM.http_status,
}

#: The columns a keyword search reaches (the same six fields as
#: ``repo.ATTEMPT_SEARCH_PATHS``). The message column is the reason the trail is
#: searched at all: a fail-over hop's failure text exists nowhere else.
SEARCH_COLUMNS: tuple[Any, ...] = (
    orm.AttemptORM.channel_name,
    orm.AttemptORM.channel_slug,
    orm.AttemptORM.channel_model_id,
    orm.AttemptORM.upstream_model,
    orm.AttemptORM.error_code,
    orm.AttemptORM.message,
)


# --------------------------------------------------------------------------- #
# Settings (ADR-072 stages)
# --------------------------------------------------------------------------- #


def _settings() -> Any:
    """The bound settings, falling back to the environment in a bare process."""
    return runtime.current_settings()


def write_mode() -> str:
    """``json`` | ``rows`` | ``both`` (ADR-072 stage 1)."""
    return _settings().attempts_write_mode


def read_mode() -> str:
    """``json`` | ``attempts`` (ADR-072 stage 2)."""
    return _settings().attempts_read_mode


def writes_attempt_rows() -> bool:
    """True when new requests also (or only) write the attempts table."""
    return write_mode() in (ROWS_WRITE, BOTH_WRITE)


def writes_attempt_json() -> bool:
    """True when new requests still write ``attempts_json`` (stages 1 and 2)."""
    return write_mode() in (JSON_WRITE, BOTH_WRITE)


def reads_attempt_rows() -> bool:
    """True when attempt-level filters read the attempts table (stage 2)."""
    return read_mode() == ROWS_SOURCE


# --------------------------------------------------------------------------- #
# Payload conversion
# --------------------------------------------------------------------------- #


def _loads(raw: Any, fallback: Any) -> Any:
    """Parse a stored JSON value, returning ``fallback`` for anything unusable.

    A row written by an older build (or by a half-failed write) need not hold
    valid JSON; every reader here tolerates that instead of raising, because a
    log page must still render when one row in the window is damaged.
    """
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (list, dict)):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return fallback
    if not isinstance(value, type(fallback)):
        return fallback
    return value


def attempts_from_json(raw: Any) -> list[dict[str, Any]]:
    """The attempt payloads in a stored ``attempts_json`` value (never raises)."""
    value = _loads(raw, [])
    return [item for item in value if isinstance(item, dict)]


def _payload_int(value: Any) -> int | None:
    """An optional integer payload field (a skipped hop has no ms/status)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def payload_to_row(log_id: str, seq: int, payload: dict[str, Any]) -> dict[str, Any]:
    """One API attempt payload as an ``attempts`` row.

    The payload's optional keys become NULL columns, not empty strings:
    ``at``/``http_status``/the header dicts are absent when the hop did not have
    them, and the reverse conversion has to be able to tell "absent" from
    "empty".
    """
    headers = _loads(payload.get("request_headers"), {})
    response_headers = _loads(payload.get("response_headers"), {})
    return {
        "id": new_id(),
        "log_id": log_id,
        "seq": int(seq),
        "status": str(payload.get("status") or ""),
        "skipped": bool(payload.get("skipped")),
        "channel_model_id": payload.get("channel_model_id"),
        "channel_slug": str(payload.get("channel_slug") or ""),
        "channel_name": str(payload.get("channel_name") or ""),
        "upstream_model": str(payload.get("upstream_model") or ""),
        "http_status": _payload_int(payload.get("http_status")),
        "error_code": payload.get("error_code"),
        "message": str(payload.get("message") or "")[:MESSAGE_LIMIT],
        "ms": _payload_int(payload.get("ms")) or 0,
        "round": _payload_int(payload.get("round")) or 1,
        "at_ms": _payload_int(payload.get("at")),
        "priority_path_json": json.dumps(
            payload.get("priority_path") or [], ensure_ascii=False
        ),
        "request_headers_json": json.dumps(headers, ensure_ascii=False),
        "response_headers_json": json.dumps(response_headers, ensure_ascii=False),
    }


def row_to_payload(row: orm.AttemptORM) -> dict[str, Any]:
    """An ``attempts`` row back as the API payload ``chat._attempt`` produces.

    Optional keys are restored only when they carry information, which is what
    keeps a JSON-mode row and a rows-mode row byte-identical on the wire.
    """
    payload: dict[str, Any] = {
        "channel_model_id": row.channel_model_id,
        "channel_slug": row.channel_slug or "",
        "channel_name": row.channel_name or "",
        "upstream_model": row.upstream_model or "",
        "status": row.status or "",
        "error_code": row.error_code,
        "message": row.message or "",
        "ms": int(row.ms or 0),
        "round": int(row.round or 1),
        "skipped": bool(row.skipped),
        "priority_path": _loads(row.priority_path_json, []),
    }
    if row.at_ms is not None:
        payload["at"] = int(row.at_ms)
    if row.http_status is not None:
        payload["http_status"] = int(row.http_status)
    request_headers = _loads(row.request_headers_json, {})
    if request_headers:
        payload["request_headers"] = request_headers
    response_headers = _loads(row.response_headers_json, {})
    if response_headers:
        payload["response_headers"] = response_headers
    return payload


def payloads_to_json(attempts: list[dict[str, Any]]) -> str:
    """Serialize payloads for a JSON-mode row (the one dumps the writers share)."""
    return json.dumps(attempts, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Writes (never commit: the caller owns the transaction)
# --------------------------------------------------------------------------- #


def _dialect(session: AsyncSession) -> str:
    return dialect_name(session)


async def insert_attempts(
    session: AsyncSession, log_id: str, attempts: list[dict[str, Any]]
) -> int:
    """Insert a log row's trail; a replayed batch conflicts and is ignored."""
    rows = [payload_to_row(log_id, seq, attempt) for seq, attempt in enumerate(attempts)]
    if not rows:
        return 0
    dialect = _dialect(session)
    if dialect in (SQLITE, POSTGRESQL):
        statement = (
            conflict_insert(dialect, orm.AttemptORM)
            .values(rows)
            .on_conflict_do_nothing()
        )
        await execute_with_lock_retry(session, statement, dialect=dialect)
        return len(rows)
    session.add_all([orm.AttemptORM(**row) for row in rows])
    return len(rows)


async def replace_attempts(
    session: AsyncSession, log_id: str, attempts: list[dict[str, Any]]
) -> int:
    """Replace a log row's trail in place (the deferred observability patch).

    A re-delivered patch must not double the trail, and a patch that clears the
    trail must clear it for real -- so this is delete-then-insert rather than an
    upsert.
    """
    await session.execute(delete(orm.AttemptORM).where(orm.AttemptORM.log_id == log_id))
    return await insert_attempts(session, log_id, attempts)


async def delete_attempts(session: AsyncSession, log_ids: list[str]) -> None:
    """Drop the trails of rows about to be deleted (belt to the FK's braces)."""
    if not log_ids:
        return
    await session.execute(
        delete(orm.AttemptORM).where(orm.AttemptORM.log_id.in_(list(log_ids)))
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


async def attempts_for_logs(
    session: AsyncSession, log_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Every trail for a page of log ids, in one query, in ``seq`` order.

    One IN query per page instead of one query per row: the log list renders 50
    rows and the drill-down pages are wider than that.
    """
    if not log_ids:
        return {}
    statement = (
        select(orm.AttemptORM)
        .where(orm.AttemptORM.log_id.in_(list(log_ids)))
        .order_by(orm.AttemptORM.log_id, orm.AttemptORM.seq)
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in (await session.execute(statement)).scalars().all():
        grouped.setdefault(row.log_id, []).append(row_to_payload(row))
    return grouped


def log_attempts(log: orm.RequestLogORM) -> list[dict[str, Any]] | None:
    """The payloads of an already loaded relationship, or ``None`` if not loaded.

    ``None`` is load-bearing: in rows mode an unloaded relationship must not be
    mistaken for an empty trail (the caller then falls back to the JSON column
    or loads the relationship), while ``[]`` is a real, empty trail.
    """
    loaded = log.__dict__.get("attempts")
    if loaded is None:
        return None
    return [row_to_payload(row) for row in sorted(loaded, key=lambda row: row.seq)]


# --------------------------------------------------------------------------- #
# SQL predicates (the rows-mode replacement for the json1 fragments)
# --------------------------------------------------------------------------- #


def _attempt_exists(*conditions: Any) -> Any:
    """``EXISTS (SELECT 1 FROM attempts WHERE log_id = request_logs.id ...)``."""
    return exists(
        select(literal(1))
        .select_from(orm.AttemptORM)
        .where(orm.AttemptORM.log_id == orm.RequestLogORM.id, *conditions)
    )


def any_condition() -> Any:
    """The row recorded at least one hop ("has an attempt trail")."""
    return _attempt_exists()


def error_condition() -> Any:
    """At least one hop failed (the replacement for the blob status check)."""
    return _attempt_exists(orm.AttemptORM.status == "error")


def search_condition(search: str) -> Any:
    """Any of the six searchable attempt columns contains ``search``."""
    return _attempt_exists(
        or_(*[like_contains(column, search) for column in SEARCH_COLUMNS])
    )


def field_condition(field: str, value: Any) -> Any | None:
    """``field == value`` over any hop; ``None`` when this field has no column.

    Numbers compare as numbers (the JSON blob had the same rule via
    ``json_extract``); text is a case-insensitive contains match, which is the
    documented meaning of ``=`` on the log page (ADR-044).
    """
    column = ATTEMPT_COLUMNS.get(field)
    if column is None:
        return None
    if isinstance(value, bool):
        return _attempt_exists(column.is_(value))
    if isinstance(value, (int, float)):
        return _attempt_exists(column == value)
    text = str(value)
    return _attempt_exists(like_contains(column, text))


class RowAttemptResolver:
    """Rows-mode :class:`AttemptResolver`: ``EXISTS`` over the attempts table.

    Deliberately not dialect-gated: the predicates are plain SQL, so Postgres
    answers the same thing (unlike :class:`SqliteAttemptResolver`, whose json1
    shape has no Postgres equivalent and degrades to the request columns).
    ``!=`` compiles to ``NOT EXISTS(equal)`` -- "no hop ever carried this
    value" -- which is the semantics ADR-072 pinned for fail-over rows.
    """

    def __init__(self, dialect: str | None = None) -> None:
        self.dialect = dialect

    def matches(self, match: Any) -> Any | None:
        if not match.paths or match.op not in TEXT_OPS:
            return None
        columns = [
            ATTEMPT_COLUMNS[path[2:]]
            for path in match.paths
            if path.startswith("$.") and path[2:] in ATTEMPT_COLUMNS
        ]
        if not columns:
            return None
        text = str(match.value)
        lowered = text.lower()

        def condition(column: Any) -> Any:
            if match.op == "=":
                return like_contains(column, text)
            if match.op == "*=":
                return DEFAULT_HOOKS.wildcard(column, text)
            return func.lower(column) == lowered

        matched = _attempt_exists(or_(*[condition(column) for column in columns]))
        return not_(matched) if match.op == "!=" else matched


def attempt_resolver(dialect: str | None = None) -> RowAttemptResolver:
    """The rows-mode resolver; ``repo`` uses this when ``ATTEMPTS_SOURCE`` says so."""
    return RowAttemptResolver(dialect)


__all__ = [
    "ATTEMPT_COLUMNS",
    "BOTH_WRITE",
    "JSON_SOURCE",
    "JSON_WRITE",
    "MESSAGE_LIMIT",
    "ROWS_SOURCE",
    "ROWS_WRITE",
    "RowAttemptResolver",
    "SEARCH_COLUMNS",
    "TEXT_OPS",
    "any_condition",
    "attempt_resolver",
    "attempts_for_logs",
    "attempts_from_json",
    "delete_attempts",
    "error_condition",
    "field_condition",
    "insert_attempts",
    "log_attempts",
    "payload_to_row",
    "payloads_to_json",
    "read_mode",
    "reads_attempt_rows",
    "replace_attempts",
    "row_to_payload",
    "search_condition",
    "write_mode",
    "writes_attempt_json",
    "writes_attempt_rows",
]
