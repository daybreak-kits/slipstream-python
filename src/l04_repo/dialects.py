"""Dialect specific INSERT statements.

``ON CONFLICT`` is not portable SQL: SQLite and PostgreSQL each need their
own insert construct. The repositories ask this module for the right one
instead of importing a dialect module directly, so the switch lives in one
place.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

SQLITE = "sqlite"
POSTGRESQL = "postgresql"


def dialect_name(session: Any) -> str:
    """The dialect of the session's bind, defaulting to SQLite."""
    bind = getattr(session, "bind", None)
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", SQLITE)


def conflict_insert(dialect: str, table: Any) -> Any:
    """The insert construct that supports ON CONFLICT on this dialect."""
    if dialect == POSTGRESQL:
        return postgresql_insert(table)
    return sqlite_insert(table)
