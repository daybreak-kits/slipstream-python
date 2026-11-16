"""SQLite sink for billing rows.

Only the SQLite implementation is in scope; the interface exists so a
PostgreSQL sink can be added once SQLite write-lock contention (E7f) forces it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l04_repo import ledger_repo


class SqliteLedgerSink:
    """Persist ledger batches idempotently (ON CONFLICT(request_id) DO NOTHING)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def write(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        async with self._session_factory() as session:
            await ledger_repo.insert_ledger_rows(session, records)
            await session.commit()
        return len(records)
