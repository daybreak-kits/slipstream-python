"""Dialect helpers: one place that knows how SQLite and PostgreSQL differ.

The application speaks SQLAlchemy Core/ORM, so almost every statement is
dialect neutral. The exceptions live here instead of being spread over the
repositories:

* the URL scheme decides which driver is used (the app is async only),
* ``ON CONFLICT`` needs the dialect specific insert construct,
* column introspection is ``PRAGMA table_info`` on SQLite and
  ``information_schema`` on PostgreSQL,
* SQLite serialises writers, so a locked database is retried; PostgreSQL
  lets the server arbitrate writes and must not be retried client side.
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

SQLITE = "sqlite"
POSTGRESQL = "postgresql"

PG_DRIVER = "asyncpg"
PG_INSTALL_HINT = "pip install asyncpg==0.30.0"

# Column introspection. SQLite has no information_schema, PostgreSQL has no
# PRAGMA, so each dialect gets its own statement.
SQLITE_COLUMNS_SQL = "PRAGMA table_info({table})"
POSTGRESQL_COLUMNS_SQL = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND table_name = :table_name"
)


class DialectError(RuntimeError):
    """Raised when the configured database URL cannot be served."""


def normalize_database_url(database_url: str) -> str:
    """Return an async-driver URL for the configured database.

    ``postgresql://`` and ``postgres://`` are rewritten to the asyncpg
    driver; a synchronous driver is rejected with the exact URL to use
    instead of a driver stack trace.
    """
    url = (database_url or "").strip()
    if not url:
        raise DialectError("DATABASE_URL is empty")
    if url.startswith("sqlite+aiosqlite"):
        return url
    if url.startswith("sqlite"):
        raise DialectError(
            "SQLite needs the async driver: use sqlite+aiosqlite:///path/to/app.db"
        )
    scheme, separator, rest = url.partition("://")
    if not separator:
        raise DialectError(f"unsupported DATABASE_URL: {url!r}")
    driver = scheme.split("+", 1)[1] if "+" in scheme else ""
    if scheme.split("+", 1)[0] in ("postgresql", "postgres"):
        if driver and driver != PG_DRIVER:
            raise DialectError(
                f"PostgreSQL needs the async driver {PG_DRIVER}: "
                f"use postgresql+{PG_DRIVER}://... (got {scheme})"
            )
        return f"postgresql+{PG_DRIVER}://{rest}"
    raise DialectError(
        f"unsupported DATABASE_URL scheme {scheme!r}: use "
        "sqlite+aiosqlite:///path/to/app.db or postgresql+asyncpg://host/db"
    )


def dialect_of(database_url: str) -> str:
    """``sqlite`` or ``postgresql`` for a (possibly unnormalised) URL."""
    url = (database_url or "").strip().lower()
    if url.startswith("sqlite"):
        return SQLITE
    if url.startswith("postgres"):
        return POSTGRESQL
    raise DialectError(f"unsupported DATABASE_URL scheme: {url!r}")


def require_driver(database_url: str) -> None:
    """Fail with an actionable message when the driver is missing."""
    if dialect_of(database_url) != POSTGRESQL:
        return
    if importlib.util.find_spec(PG_DRIVER) is None:
        raise DialectError(
            f"PostgreSQL needs the {PG_DRIVER} driver, which is not installed: "
            f"{PG_INSTALL_HINT}"
        )


def column_names(conn: Any, table: str) -> set[str]:
    """The column names of a table, on whichever dialect is in use."""
    dialect = getattr(getattr(conn, "dialect", None), "name", SQLITE)
    if dialect == POSTGRESQL:
        rows = conn.execute(
            text(POSTGRESQL_COLUMNS_SQL), {"table_name": table}
        ).fetchall()
        return {str(row[0]) for row in rows}
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def is_lock_error(exc: BaseException) -> bool:
    """True for the SQLite "database is locked" family of errors."""
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


async def execute_with_lock_retry(
    session: Any,
    statement: Any,
    *,
    dialect: str,
    attempts: int = 3,
    base_delay_s: float = 0.05,
) -> Any:
    """Execute a write, retrying only on SQLite's single-writer lock.

    SQLite has one writer, so a burst of writers can hit "database is
    locked" even with busy_timeout set. PostgreSQL arbitrates writes in the
    server and reports real errors; retrying there would hide them and can
    break the transaction, so the retry is SQLite-only by construction.
    """
    total = max(1, int(attempts))
    for attempt in range(total):
        try:
            return await session.execute(statement)
        except OperationalError as exc:
            if dialect != SQLITE or not is_lock_error(exc) or attempt == total - 1:
                raise
            await asyncio.sleep(base_delay_s * (attempt + 1))
    raise RuntimeError("unreachable")
