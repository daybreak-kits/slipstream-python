"""Copy a SQLite database into PostgreSQL: batched, resumable, verifiable.

The gateway currently runs on SQLite, which has a single writer. Several
gateway processes that share one SQLite file lose log rows silently (measured:
rows/req 1.000 -> 0.951 with 8 writers), so PostgreSQL is the way to more than
one writer. This script moves the data across; it never touches the source.

Usage:

    # 1. plan only, no database connection needed
    python llmapi/db/migrate_sqlite_to_postgres.py \
        --sqlite llmapi/data/app.db \
        --pg-url postgresql+asyncpg://slipstream:secret@127.0.0.1:5432/slipstream \
        --dry-run

    # 2. copy (resumable: the state file records the last copied key)
    python llmapi/db/migrate_sqlite_to_postgres.py \
        --sqlite llmapi/data/app.db \
        --pg-url postgresql+asyncpg://... \
        --state .pg-migrate-state.json

    # 3. verify row counts
    python llmapi/db/migrate_sqlite_to_postgres.py \
        --sqlite llmapi/data/app.db \
        --pg-url postgresql+asyncpg://... \
        --verify-only

Properties:

* idempotent: every insert is ON CONFLICT DO NOTHING on the primary key, so
  running the script twice does not duplicate rows,
* resumable: the state file holds the last copied primary key per table, so
  an interrupted run continues where it stopped,
* batched: --batch-size rows per transaction (default 500),
* verifiable: per table row counts are compared at the end,
* non-destructive: the SQLite file is opened read-only.

Notes:

* asyncpg is not installed in this workspace, so the copy path could not be
  executed here. The planning, batching, conversion and state handling are
  pure functions and are covered by tests; the PostgreSQL round trip itself
  has to be verified in the deployment environment.
* Booleans are stored as 0/1 in SQLite and as real booleans in PostgreSQL, so
  every boolean column is converted on the way.
* JSON columns are TEXT in both databases (the application serialises them
  itself), so they are copied verbatim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parent
SRC_DIR = DB_DIR.parent / "api" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlalchemy.dialects.postgresql import insert as postgresql_insert  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402

from l02_infra.db import Base, build_engine, init_db  # noqa: E402
from l04_repo import orm  # noqa: F401,E402  (importing registers every table)

# Parents before children: a foreign key target must exist before its rows.
TABLE_ORDER = (
    "users",
    "admin_users",
    "api_keys",
    "app_settings",
    "channels",
    "channel_keys",
    "channel_models",
    "model_defaults",
    "combos",
    "combo_targets",
    "request_logs",
    "attempts",
    "request_bodies",
    "request_ledger",
    "ledger_adjustments",
    "daily_aggregates",
)

DEFAULT_BATCH_SIZE = 500


@dataclass
class TablePlan:
    """What one table needs: which columns, which key, how many rows."""

    name: str
    columns: list[str] = field(default_factory=list)
    primary_key: str = ""
    rows: int = 0
    missing_in_sqlite: list[str] = field(default_factory=list)
    extra_in_sqlite: list[str] = field(default_factory=list)

    @property
    def count_sql(self) -> str:
        return f"SELECT COUNT(*) FROM {self.name}"


def _orm_table(name: str) -> Any:
    return Base.metadata.tables.get(name)


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row[1]) for row in rows]


def sqlite_primary_key(conn: sqlite3.Connection, table: str) -> str:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for row in rows:
        if int(row[5] or 0) == 1:
            return str(row[1])
    return ""


def plan_tables(
    sqlite_path: str | Path, tables: list[str] | None = None
) -> list[TablePlan]:
    """Describe the copy: per table the columns, the key and the row count.

    Read-only: the SQLite file is never written.
    """
    if tables:
        wanted = list(tables)
    else:
        # TABLE_ORDER fixes parent-before-child order; any ORM table missing
        # from it is appended, so adding a table to orm.py can never leave it
        # out of the migration by accident.
        wanted = list(TABLE_ORDER) + [
            name for name in Base.metadata.tables if name not in TABLE_ORDER
        ]
    plans: list[TablePlan] = []
    uri = f"file:{Path(sqlite_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        existing = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for name in wanted:
            if name not in existing:
                continue
            orm_table = _orm_table(name)
            sqlite_cols = sqlite_columns(conn, name)
            if orm_table is None:
                # A table the ORM no longer knows (dropped from the code): plan
                # it with its real row count, so nothing looks emptier than it
                # is and the operator sees what would be left behind.
                plans.append(
                    TablePlan(
                        name=name,
                        columns=list(sqlite_cols),
                        rows=int(
                            conn.execute(
                                f"SELECT COUNT(*) FROM {name}"
                            ).fetchone()[0]
                        ),
                    )
                )
                continue
            orm_cols = list(orm_table.columns.keys())
            shared = [col for col in orm_cols if col in sqlite_cols]
            plan = TablePlan(
                name=name,
                columns=shared,
                primary_key=sqlite_primary_key(conn, name),
            )
            plan.missing_in_sqlite = [col for col in orm_cols if col not in sqlite_cols]
            plan.extra_in_sqlite = [
                col for col in sqlite_cols if col not in orm_cols
            ]
            plan.rows = int(conn.execute(plan.count_sql).fetchone()[0])
            plans.append(plan)
    finally:
        conn.close()
    return plans


def sqlite_table_names(sqlite_path: str | Path) -> list[str]:
    """Every user table in the SQLite file, read-only."""
    uri = f"file:{Path(sqlite_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")]


def describe_gaps(
    sqlite_path: str | Path, planned: list[str]
) -> tuple[list[str], list[str]]:
    """What the plan does not cover, in both directions.

    Returns (absent, orphans):

    * absent: tables the ORM knows but the SQLite file does not have. They are
      created empty in PostgreSQL - a normal result of a schema upgrade that
      never ran against this file, but worth stating out loud.
    * orphans: tables the SQLite file has that neither the ORM nor TABLE_ORDER
      names. Their rows would be silently left behind, so the caller must warn.
    """
    present = sqlite_table_names(sqlite_path)
    present_set = set(present)
    planned_set = set(planned)
    absent = [name for name in Base.metadata.tables if name not in present_set]
    orphans = [
        name
        for name in present
        if name not in planned_set and name not in Base.metadata.tables
    ]
    return absent, orphans


def convert_value(table: Any, column: str, value: Any) -> Any:
    """Convert one SQLite value into the type the PostgreSQL column wants.

    SQLite has no boolean type: it stores 0/1 in a BOOLEAN column, while
    PostgreSQL refuses anything but true/false.
    """
    if value is None:
        return None
    if table is None:
        return value
    col = table.columns.get(column)
    if col is None:
        return value
    if getattr(col.type, "python_type", None) is bool and isinstance(value, int):
        return bool(value)
    return value


def convert_row(table: Any, row: dict[str, Any]) -> dict[str, Any]:
    return {key: convert_value(table, key, value) for key, value in row.items()}


def read_batch(
    conn: sqlite3.Connection,
    plan: TablePlan,
    cursor: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Read the next batch of rows after the cursor key."""
    columns = ", ".join(plan.columns)
    if cursor and plan.primary_key:
        sql = (
            f"SELECT {columns} FROM {plan.name} "
            f"WHERE {plan.primary_key} > ? ORDER BY {plan.primary_key} LIMIT ?"
        )
        params: tuple[Any, ...] = (cursor, int(limit))
    else:
        sql = f"SELECT {columns} FROM {plan.name} ORDER BY {plan.primary_key or 'rowid'} LIMIT ?"
        params = (int(limit),)
    cursor_object = conn.execute(sql, params)
    names = [description[0] for description in cursor_object.description]
    return [dict(zip(names, row)) for row in cursor_object.fetchall()]


def load_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.is_file():
        return {"tables": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tables": {}}
    if not isinstance(data, dict):
        return {"tables": {}}
    data.setdefault("tables", {})
    return data


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


async def _count_postgres(engine: AsyncEngine, table: str) -> int:
    from sqlalchemy import func, select

    orm_table = _orm_table(table)
    if orm_table is None:
        return -1
    async with engine.connect() as conn:
        result = await conn.execute(select(func.count()).select_from(orm_table))
        return int(result.scalar_one() or 0)


async def copy_tables(
    plans: list[TablePlan],
    sqlite_path: str | Path,
    pg_url: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    state_path: str | Path | None = None,
    limit: int | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Copy every planned table into PostgreSQL, batch by batch."""
    engine = build_engine(pg_url)
    await init_db(engine)
    state = load_state(state_path) if state_path else {"tables": {}}
    report: dict[str, Any] = {"tables": {}}
    uri = f"file:{Path(sqlite_path).as_posix()}?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    try:
        for plan in plans:
            orm_table = _orm_table(plan.name)
            if orm_table is None:
                continue
            synced = 0
            cursor = state["tables"].get(plan.name, {}).get("cursor")
            while True:
                rows = read_batch(source, plan, cursor, batch_size)
                if not rows:
                    break
                if limit is not None and synced + len(rows) > limit:
                    rows = rows[: max(0, limit - synced)]
                    if not rows:
                        break
                payload = [convert_row(orm_table, row) for row in rows]
                statement = (
                    postgresql_insert(orm_table)
                    .values(payload)
                    .on_conflict_do_nothing()
                )
                async with engine.begin() as conn:
                    await conn.execute(statement)
                synced += len(rows)
                cursor = str(rows[-1].get(plan.primary_key, cursor))
                if state_path:
                    state["tables"][plan.name] = {
                        "cursor": cursor,
                        "synced": synced,
                    }
                    save_state(state_path, state)
                if verbose:
                    print(f"  {plan.name}: {synced} rows copied")
                if limit is not None and synced >= limit:
                    break
            report["tables"][plan.name] = {"copied": synced, "expected": plan.rows}
    finally:
        source.close()
        await engine.dispose()
    return report


async def verify_counts(
    plans: list[TablePlan], pg_url: str
) -> tuple[bool, list[dict[str, Any]]]:
    """Compare the SQLite and PostgreSQL row counts per table."""
    engine = build_engine(pg_url)
    rows: list[dict[str, Any]] = []
    ok = True
    try:
        for plan in plans:
            postgres_rows = await _count_postgres(engine, plan.name)
            same = postgres_rows == plan.rows
            ok = ok and same
            rows.append(
                {
                    "table": plan.name,
                    "sqlite": plan.rows,
                    "postgres": postgres_rows,
                    "match": same,
                }
            )
    finally:
        await engine.dispose()
    return ok, rows


def _print_plan(plans: list[TablePlan]) -> None:
    total = 0
    print("table                 rows    columns  missing-in-sqlite  extra-in-sqlite")
    for plan in plans:
        total += plan.rows
        missing = ",".join(plan.missing_in_sqlite) or "-"
        # Columns that only exist in the SQLite file are never copied: their
        # data is dropped, so the operator has to see them before the cutover.
        extra = ",".join(plan.extra_in_sqlite) or "-"
        print(
            f"{plan.name:<20} {plan.rows:>6}  {len(plan.columns):>7}  "
            f"{missing:<18} {extra}"
        )
    print(f"{'TOTAL':<20} {total:>6}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="path of the SQLite file")
    parser.add_argument(
        "--pg-url",
        default="",
        help="PostgreSQL URL (postgresql+asyncpg://user:pass@host/db)",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--state", default="", help="state file for resuming")
    parser.add_argument("--tables", default="", help="comma separated table list")
    parser.add_argument("--limit", type=int, default=0, help="stop after N rows")
    parser.add_argument("--dry-run", action="store_true", help="plan only")
    parser.add_argument(
        "--verify-only", action="store_true", help="only compare row counts"
    )
    args = parser.parse_args(argv)

    tables = [item for item in args.tables.split(",") if item.strip()] or None
    plans = plan_tables(args.sqlite, tables)
    _print_plan(plans)
    absent, orphans = describe_gaps(args.sqlite, [plan.name for plan in plans])
    if absent:
        print(
            "note: tables absent from the sqlite file (they start empty in "
            f"postgresql): {', '.join(absent)}"
        )
    if orphans:
        print(
            "warning: tables in the sqlite file that nothing migrates "
            f"(left behind): {', '.join(orphans)}"
        )
    if args.dry_run:
        print("dry run: nothing was written")
        return 0
    if not args.pg_url:
        print("error: --pg-url is required unless --dry-run is given")
        return 2
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        print(
            "error: the asyncpg driver is not installed; "
            "install it with: pip install asyncpg==0.30.0"
        )
        return 2

    if args.verify_only:
        ok, rows = asyncio.run(verify_counts(plans, args.pg_url))
        for row in rows:
            print(
                f"{row['table']:<20} sqlite={row['sqlite']:<8} "
                f"postgres={row['postgres']:<8} {'OK' if row['match'] else 'MISMATCH'}"
            )
        return 0 if ok else 1

    report = asyncio.run(
        copy_tables(
            plans,
            args.sqlite,
            args.pg_url,
            batch_size=args.batch_size,
            state_path=args.state or None,
            limit=args.limit or None,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
