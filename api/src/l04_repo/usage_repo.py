"""Rows and idempotent writes for the usage write path.

The request path never talks to the database: it hands one complete row to the
usage sink, which batches the rows into request_logs in the background. The
write is idempotent on request_logs.request_id, so a replayed WAL segment can
never create a second row.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.ids import new_id
from l01_core.logging import redact_headers
from l01_core.request_meta import parse_forwarded
from l02_infra.dialect import POSTGRESQL, SQLITE, execute_with_lock_retry
from l04_repo import attempt_filters, orm
from l04_repo.dialects import conflict_insert, dialect_name

# Header values are display-only audit data: keep them short.
HEADER_VALUE_LIMIT = 1024


def _column_default(column: Any) -> Any:
    """The value SQLAlchemy would store for a column the caller did not set."""
    default = column.default
    if default is None:
        return None
    if getattr(default, "is_callable", False):
        for args in ((), (None,)):
            try:
                return default.arg(*args)
            except TypeError:
                continue
        return None
    return default.arg


def _safe_headers(headers: dict[str, str] | None) -> dict[str, Any]:
    """The request headers as they may be stored: credentials masked."""
    redacted = redact_headers(dict(headers or {}))
    return {
        name: (value[:HEADER_VALUE_LIMIT] if isinstance(value, str) else value)
        for name, value in redacted.items()
    }


def build_log_row(
    *,
    billing: dict[str, Any],
    observability: dict[str, Any],
    client_ip: str = "",
    request_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a complete request_logs row: every column, no value left to chance."""
    row: dict[str, Any] = {
        column.name: _column_default(column)
        for column in orm.RequestLogORM.__table__.columns
    }
    row["id"] = new_id()
    row.update(billing)
    row.update(observability)
    row["source"] = row.get("source") or "chat"
    row["client_ip"] = client_ip or ""
    row["request_headers_json"] = json.dumps(
        _safe_headers(request_headers), ensure_ascii=False, default=str
    )
    row["forwarded_json"] = json.dumps(
        parse_forwarded(request_headers), ensure_ascii=False, default=str
    )
    return row


def _dialect_name(session: AsyncSession) -> str:
    """The dialect of the session's bind (SQLite by default)."""
    return dialect_name(session)


def _split_trails(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Strip each row's attempt trail so it can be written after the parent row.

    Returns the (possibly rewritten) rows and the trails keyed by log id. JSON
    mode returns the rows untouched: the payload string stays the storage.
    """
    if not attempt_filters.writes_attempt_rows():
        return rows, {}
    trails: dict[str, list[dict[str, Any]]] = {}
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        trails[copy["id"]] = attempt_filters.attempts_from_json(
            copy.get("attempts_json")
        )
        if not attempt_filters.writes_attempt_json():
            copy["attempts_json"] = "[]"
        cleaned.append(copy)
    return cleaned, trails


async def insert_request_logs(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert a batch of request_logs rows, idempotent on request_id."""
    if not rows:
        return 0
    rows, trails = _split_trails(rows)
    dialect = _dialect_name(session)
    if dialect in (SQLITE, POSTGRESQL):
        # No conflict target: any uniqueness violation (the primary key or the
        # unique request_id) means the row is already there. SQLite and
        # PostgreSQL both accept ON CONFLICT DO NOTHING without a target.
        statement = (
            conflict_insert(dialect, orm.RequestLogORM)
            .values(list(rows))
            .on_conflict_do_nothing()
        )
        await execute_with_lock_retry(session, statement, dialect=dialect)
        # ON CONFLICT DO NOTHING keeps a replayed WAL segment from duplicating
        # a row, and the attempts insert is idempotent on (log_id, seq) for
        # the same reason.
        for log_id, attempts in trails.items():
            await attempt_filters.insert_attempts(session, log_id, attempts)
        return len(rows)
    written = 0
    for row in rows:
        existing = await session.execute(
            select(orm.RequestLogORM.id).where(
                orm.RequestLogORM.request_id == row["request_id"]
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        session.add(orm.RequestLogORM(**row))
        await session.commit()
        if row["id"] in trails:
            await attempt_filters.insert_attempts(
                session, row["id"], trails[row["id"]]
            )
            await session.commit()
        written += 1
    return written


async def insert_request_log(session: AsyncSession, row: dict[str, Any]) -> bool:
    """Insert one request_logs row, idempotent on request_id."""
    rows, trails = _split_trails([row])
    row = rows[0]
    dialect = _dialect_name(session)
    if dialect in (SQLITE, POSTGRESQL):
        statement = (
            conflict_insert(dialect, orm.RequestLogORM)
            .values([row])
            .on_conflict_do_nothing()
        )
        await execute_with_lock_retry(session, statement, dialect=dialect)
        for log_id, attempts in trails.items():
            await attempt_filters.insert_attempts(session, log_id, attempts)
        return True
    existing = await session.execute(
        select(orm.RequestLogORM.id).where(
            orm.RequestLogORM.request_id == row["request_id"]
        )
    )
    if existing.scalar_one_or_none() is not None:
        return False
    session.add(orm.RequestLogORM(**row))
    await session.commit()
    for log_id, attempts in trails.items():
        await attempt_filters.insert_attempts(session, log_id, attempts)
    await session.commit()
    return True
