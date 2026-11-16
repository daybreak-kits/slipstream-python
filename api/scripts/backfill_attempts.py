"""Backfill the ``attempts`` table from ``request_logs.attempts_json`` (ADR-072).

Stage 1 writes both shapes, but the rows that existed before the switch only
have the JSON array. This script copies them, one id-ordered batch at a time,
and records its position in ``attempts_backfill_state`` so a crashed or
interrupted run resumes instead of starting over.

Properties the ADR asks for:

* rows created *after* the start are left alone (``cutoff = max(id)``): the
  dual write owns those, and this script must never race it.
* ``ON CONFLICT (log_id, seq) DO NOTHING`` makes a replayed batch a no-op, so
  running the script twice is safe.
* a row whose ``attempts_json`` is not a JSON array is counted and skipped:
  it is damaged data, not a reason to abort the whole backfill.

Usage (from ``api``)::

    python scripts/backfill_attempts.py                # resume/continue
    python scripts/backfill_attempts.py --reset        # start from the top
    python scripts/backfill_attempts.py --dry-run      # count only
    python scripts/backfill_attempts.py --limit 10000  # stop after N rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from l01_core.config import get_settings  # noqa: E402
from l01_core.timeutil import now_ms  # noqa: E402
from l02_infra.db import build_engine, build_sessionmaker, init_db  # noqa: E402
from l04_repo import attempt_filters, orm  # noqa: E402

STATE_TABLE = "attempts_backfill_state"
STATE_NAME = "attempts"
DEFAULT_BATCH_SIZE = 5000

_CREATE_STATE = (
    "CREATE TABLE IF NOT EXISTS " + STATE_TABLE + " ("
    "    name TEXT PRIMARY KEY,"
    "    cursor TEXT,"
    "    updated_at BIGINT NOT NULL"
    ")"
)


def parse_attempts(raw: Any) -> list[dict[str, Any]] | None:
    """The payloads in a stored value, or ``None`` when it is not a JSON array."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


async def ensure_state(session: AsyncSession) -> None:
    """Create the resumability table if this is the first run."""
    await session.execute(text(_CREATE_STATE))
    await session.commit()


async def read_cursor(session: AsyncSession) -> str | None:
    row = (
        await session.execute(
            text(f"SELECT cursor FROM {STATE_TABLE} WHERE name = :name"),
            {"name": STATE_NAME},
        )
    ).first()
    return str(row[0]) if row and row[0] is not None else None


async def write_cursor(session: AsyncSession, cursor: str | None) -> None:
    """Persist the last completed id; ``ON CONFLICT`` keeps the write one statement."""
    await session.execute(
        text(
            f"INSERT INTO {STATE_TABLE} (name, cursor, updated_at) "
            "VALUES (:name, :cursor, :now) "
            "ON CONFLICT (name) DO UPDATE SET cursor = :cursor, updated_at = :now"
        ),
        {"name": STATE_NAME, "cursor": cursor, "now": now_ms()},
    )


async def reset_state(session: AsyncSession) -> None:
    """Forget the cursor so a run starts from the oldest row again."""
    await session.execute(
        text(f"DELETE FROM {STATE_TABLE} WHERE name = :name"), {"name": STATE_NAME}
    )
    await session.commit()


async def backfill_attempts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    dry_run: bool = False,
    reset: bool = False,
) -> dict[str, Any]:
    """Copy every un-copied trail, in id order, committing one batch at a time.

    The returned dict is the run report (counts, cutoff, resume point). A
    ``finished`` flag of False means the caller stopped at ``limit`` and a later
    run continues from ``cursor``.
    """
    stats: dict[str, Any] = {
        "batches": 0,
        "scanned": 0,
        "attempts": 0,
        "rows_written": 0,
        "bad_json": 0,
        "cutoff": "",
        "resumed_from": None,
        "cursor": None,
        "finished": True,
        "dry_run": dry_run,
    }
    async with session_factory() as session:
        await ensure_state(session)
        if reset:
            await reset_state(session)
        cursor = await read_cursor(session)
        stats["resumed_from"] = cursor
        cutoff = (await session.execute(select(func.max(orm.RequestLogORM.id)))).scalar()
        stats["cutoff"] = cutoff or ""
        if cutoff is None:
            return stats
        scanned = 0
        while True:
            statement = select(
                orm.RequestLogORM.id, orm.RequestLogORM.attempts_json
            ).where(orm.RequestLogORM.id <= cutoff)
            if cursor:
                statement = statement.where(orm.RequestLogORM.id > cursor)
            statement = statement.order_by(orm.RequestLogORM.id).limit(batch_size)
            rows = (await session.execute(statement)).all()
            if not rows:
                stats["finished"] = True
                break
            for log_id, raw in rows:
                scanned += 1
                attempts = parse_attempts(raw)
                if attempts is None:
                    stats["bad_json"] += 1
                    continue
                if not attempts:
                    continue
                stats["rows_written"] += 1
                stats["attempts"] += len(attempts)
                if not dry_run:
                    await attempt_filters.insert_attempts(session, log_id, attempts)
            cursor = rows[-1][0]
            stats["cursor"] = cursor
            stats["batches"] += 1
            if not dry_run:
                # Trail rows and the cursor commit together: a crash replays
                # the batch, and the unique (log_id, seq) makes that harmless.
                await write_cursor(session, cursor)
                await session.commit()
            if limit is not None and scanned >= limit:
                stats["finished"] = False
                break
            if len(rows) < batch_size:
                stats["finished"] = True
                break
        stats["scanned"] = scanned
    return stats


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    await init_db(engine)
    try:
        stats = await backfill_attempts(
            session_factory,
            batch_size=args.batch,
            limit=args.limit,
            dry_run=args.dry_run,
            reset=args.reset,
        )
    finally:
        await engine.dispose()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
