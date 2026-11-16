from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from l02_infra import dialect as dialect_module  # noqa: E402
from l02_infra.db import (  # noqa: E402
    POSTGRES_COLUMN_DDL,
    _postgres_column_specs,
    Base,
    _table_columns,
    build_engine,
)
from l02_infra.dialect import (  # noqa: E402
    POSTGRESQL,
    SQLITE,
    DialectError,
    column_names,
    dialect_of,
    execute_with_lock_retry,
    is_lock_error,
    normalize_database_url,
    require_driver,
)
from l04_repo import orm  # noqa: F401,E402  (registers every table)
from l04_repo.dialects import conflict_insert, dialect_name  # noqa: E402

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "db" / "migrate_sqlite_to_postgres.py"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "postgres_schema.sql"


def _migration_module():
    """Import the migration script without installing it as a package."""
    spec = importlib.util.spec_from_file_location(
        "migrate_sqlite_to_postgres", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up in sys.modules while the class body is
    # being annotated, so the module has to be registered before exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def sqlite_file() -> Path:
    """A throwaway SQLite path inside the repository (tmp_path is unavailable)."""
    root = Path(
        os.environ.get("PYTEST_DB_DIR")
        or Path(__file__).resolve().parents[1] / ".pytest-dbs"
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"pgtest-{uuid.uuid4().hex}.db"
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


@pytest.fixture()
def populated_sqlite(sqlite_file: Path) -> Path:
    """A small SQLite database with the real schema and a few rows."""
    engine = create_engine(f"sqlite:///{sqlite_file.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            orm.UserORM(
                id="u1",
                username="alice",
                display_name="Alice",
                created_at=1_700_000_000_000,
            )
        )
        for index in range(3):
            session.add(
                orm.RequestLogORM(
                    id=f"log{index}",
                    request_id=f"req{index}",
                    requested_model="gpt-test",
                    created_at=1_700_000_000_000 + index,
                    is_stream=True,
                    usage_estimated=True,
                )
            )
        session.add(
            orm.RequestBodyORM(request_id="req0", request_body="{}", created_at=1)
        )
        session.commit()
    engine.dispose()
    return sqlite_file


# ---------------------------------------------------------------------------
# URL normalisation / driver check
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "sqlite+aiosqlite:///./data/app.db",
            "sqlite+aiosqlite:///./data/app.db",
        ),
        ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql+asyncpg://u@h/db", "postgresql+asyncpg://u@h/db"),
    ],
)
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["sqlite:///./data/app.db", "postgresql+psycopg2://u@h/db", "mysql://u@h/db", ""],
)
def test_sync_or_unknown_urls_are_rejected(raw: str) -> None:
    with pytest.raises(DialectError):
        normalize_database_url(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sqlite+aiosqlite:///x.db", SQLITE),
        ("postgresql://u@h/db", POSTGRESQL),
        ("postgres://u@h/db", POSTGRESQL),
    ],
)
def test_dialect_of(raw: str, expected: str) -> None:
    assert dialect_of(raw) == expected


def test_require_driver_names_the_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dialect_module.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(DialectError) as excinfo:
        require_driver("postgresql+asyncpg://u@h/db")
    assert "pip install asyncpg" in str(excinfo.value)


def test_require_driver_skips_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(name: str):  # pragma: no cover - must not be called
        raise AssertionError(f"unexpected driver lookup for {name}")

    monkeypatch.setattr(dialect_module.importlib.util, "find_spec", _forbidden)
    require_driver("sqlite+aiosqlite:///./data/app.db")


def test_build_engine_reports_missing_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dialect_module.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(DialectError, match="asyncpg"):
        build_engine("postgresql://u:p@127.0.0.1:5432/db")


# ---------------------------------------------------------------------------
# ON CONFLICT statements
# ---------------------------------------------------------------------------
def _compiled(statement, dialect) -> str:
    return str(statement.compile(dialect=dialect))


def test_conflict_insert_picks_the_dialect_construct() -> None:
    for dialect_name_value, dialect in (
        (SQLITE, sqlite.dialect()),
        (POSTGRESQL, postgresql.dialect()),
    ):
        statement = (
            conflict_insert(dialect_name_value, orm.RequestLogORM)
            .values([{"id": "log1", "request_id": "req1"}])
            .on_conflict_do_nothing()
        )
        assert "ON CONFLICT" in _compiled(statement, dialect)


def test_ledger_insert_conflicts_on_request_id() -> None:
    statement = (
        conflict_insert(POSTGRESQL, orm.RequestLedgerORM)
        .values([{"id": "led1", "request_id": "req1"}])
        .on_conflict_do_nothing(index_elements=["request_id"])
    )
    sql = _compiled(statement, postgresql.dialect())
    assert "ON CONFLICT (request_id) DO NOTHING" in sql


def test_request_body_upsert_compiles_for_postgres() -> None:
    statement = (
        conflict_insert(POSTGRESQL, orm.RequestBodyORM)
        .values(request_id="req1", request_body="a")
        .on_conflict_do_update(
            index_elements=["request_id"], set_={"request_body": "b"}
        )
    )
    sql = _compiled(statement, postgresql.dialect())
    assert "ON CONFLICT (request_id) DO UPDATE" in sql
    assert "SET request_body" in sql


def test_dialect_name_defaults_to_sqlite() -> None:
    class _Bare:
        pass

    assert dialect_name(_Bare()) == SQLITE


# ---------------------------------------------------------------------------
# SQLite-only lock retry
# ---------------------------------------------------------------------------
class _FlakySession:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def execute(self, statement):  # noqa: ANN001 - fake session
        self.calls += 1
        if self.calls <= self.failures:
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return "ok"


async def test_lock_retry_retries_sqlite() -> None:
    session = _FlakySession(1)
    assert await execute_with_lock_retry(session, "stmt", dialect=SQLITE) == "ok"
    assert session.calls == 2


async def test_lock_retry_does_not_retry_postgres() -> None:
    session = _FlakySession(1)
    with pytest.raises(OperationalError):
        await execute_with_lock_retry(session, "stmt", dialect=POSTGRESQL)
    assert session.calls == 1


async def test_lock_retry_keeps_real_errors() -> None:
    class _Broken:
        async def execute(self, statement):  # noqa: ANN001 - fake session
            raise OperationalError("INSERT", {}, Exception("no such table: nope"))

    with pytest.raises(OperationalError):
        await execute_with_lock_retry(_Broken(), "stmt", dialect=SQLITE)


def test_is_lock_error_only_matches_the_sqlite_wording() -> None:
    assert is_lock_error(Exception("database is locked"))
    assert is_lock_error(Exception("database table is locked"))
    assert not is_lock_error(Exception("no such table"))


# ---------------------------------------------------------------------------
# Column introspection
# ---------------------------------------------------------------------------
def test_column_names_sqlite_branch() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE demo (id TEXT PRIMARY KEY, value INTEGER)")
        assert column_names(conn, "demo") == {"id", "value"}
        assert _table_columns(conn, "demo") == {"id", "value"}


def test_column_names_postgres_branch_uses_information_schema() -> None:
    class _Result:
        def fetchall(self):  # noqa: ANN201 - fake result
            return [("id",), ("request_id",)]

    class _Conn:
        dialect = type("_Dialect", (), {"name": POSTGRESQL})()
        seen: tuple[str, object] | None = None

        def execute(self, statement, params):  # noqa: ANN001 - fake connection
            self.seen = (str(statement), params)
            return _Result()

    conn = _Conn()
    assert column_names(conn, "request_logs") == {"id", "request_id"}
    assert conn.seen is not None
    assert "information_schema.columns" in conn.seen[0]
    assert conn.seen[1] == {"table_name": "request_logs"}


# ---------------------------------------------------------------------------
# Schema artefacts
# ---------------------------------------------------------------------------
def test_generated_schema_covers_every_table() -> None:
    assert SCHEMA_PATH.is_file(), "run db/generate_pg_schema.py"
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    for table in Base.metadata.sorted_tables:
        assert f"CREATE TABLE {table.name}" in schema
    assert "CREATE INDEX" in schema
    # No sequences: every id is a TEXT value generated by the application.
    assert "NEXTVAL" not in schema.upper()
    assert "SERIAL PRIMARY KEY" not in schema.upper()
    # Float columns must be spelled out: the idempotent ADD COLUMN migration
    # uses DOUBLE PRECISION, the generated schema has to say the same thing.
    assert "DOUBLE PRECISION" in schema
    assert not re.search(r"\bFLOAT\b", schema)


def test_postgres_column_ddl_uses_postgres_types() -> None:
    specs = _postgres_column_specs()
    boolean = specs["request_ledger"]["usage_estimated"]
    assert boolean.startswith("BOOLEAN")
    assert "false" in boolean
    assert specs["request_ledger"]["cost_usd"].startswith("DOUBLE PRECISION")
    for columns in specs.values():
        for ddl in columns.values():
            assert " REAL" not in f" {ddl}"


def test_postgres_column_specs_cover_every_orm_column() -> None:
    """A column added to orm.py must also be addable to an existing database."""
    specs = _postgres_column_specs()
    for table in Base.metadata.sorted_tables:
        assert set(specs[table.name]) == {column.name for column in table.columns}


def test_postgres_column_specs_never_add_a_strict_not_null_column() -> None:
    # PostgreSQL refuses "ADD COLUMN ... NOT NULL" without a default on a table
    # that already has rows, so a derived column must either be nullable or be
    # listed in POSTGRES_COLUMN_DDL with an explicit default.
    for table, columns in _postgres_column_specs().items():
        overrides = POSTGRES_COLUMN_DDL.get(table, {})
        for name, ddl in columns.items():
            if name in overrides:
                continue
            assert not ddl.endswith(" NOT NULL"), f"{table}.{name}: {ddl}"


def test_postgres_column_ddl_overrides_keep_their_default() -> None:
    specs = _postgres_column_specs()
    assert specs["channel_models"]["model_type"] == "TEXT NOT NULL DEFAULT 'chat'"
    assert specs["request_ledger"]["usage_estimated"] == (
        "BOOLEAN NOT NULL DEFAULT false"
    )
    # Derived columns keep the exact type the ORM declares.
    assert specs["request_logs"]["request_id"] == "request_id VARCHAR(64)"


# ---------------------------------------------------------------------------
# SQLite keeps its behaviour
# ---------------------------------------------------------------------------
async def test_sqlite_engine_still_sets_wal(sqlite_file: Path) -> None:
    engine = build_engine(f"sqlite+aiosqlite:///{sqlite_file.as_posix()}")
    try:
        async with engine.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
    finally:
        await engine.dispose()
    assert str(mode).lower() == "wal"


# ---------------------------------------------------------------------------
# Migration script: planning, batching, conversion, state (no PostgreSQL)
# ---------------------------------------------------------------------------
def test_plan_tables_reports_rows_columns_and_keys(populated_sqlite: Path) -> None:
    module = _migration_module()
    plans = module.plan_tables(
        populated_sqlite, ["users", "request_logs", "request_bodies"]
    )
    by_name = {plan.name: plan for plan in plans}
    assert by_name["users"].rows == 1
    assert by_name["request_logs"].rows == 3
    assert by_name["users"].primary_key == "id"
    assert "username" in by_name["users"].columns
    assert by_name["request_logs"].missing_in_sqlite == []


def test_plan_tables_skips_tables_that_do_not_exist(populated_sqlite: Path) -> None:
    module = _migration_module()
    plans = module.plan_tables(populated_sqlite, ["users", "does_not_exist"])
    assert [plan.name for plan in plans] == ["users"]


def test_read_batch_paginates_by_primary_key(populated_sqlite: Path) -> None:
    module = _migration_module()
    plan = module.plan_tables(populated_sqlite, ["request_logs"])[0]
    conn = sqlite3.connect(f"file:{populated_sqlite.as_posix()}?mode=ro", uri=True)
    try:
        first = module.read_batch(conn, plan, None, 2)
        assert [row["id"] for row in first] == ["log0", "log1"]
        second = module.read_batch(conn, plan, first[-1]["id"], 2)
        assert [row["id"] for row in second] == ["log2"]
        assert module.read_batch(conn, plan, second[-1]["id"], 2) == []
    finally:
        conn.close()


def test_booleans_are_converted_on_the_way_out(populated_sqlite: Path) -> None:
    module = _migration_module()
    plan = module.plan_tables(populated_sqlite, ["request_logs"])[0]
    table = Base.metadata.tables["request_logs"]
    conn = sqlite3.connect(f"file:{populated_sqlite.as_posix()}?mode=ro", uri=True)
    try:
        row = module.read_batch(conn, plan, None, 1)[0]
    finally:
        conn.close()
    assert row["is_stream"] == 1
    converted = module.convert_row(table, row)
    assert converted["is_stream"] is True
    assert converted["usage_estimated"] is True


def test_state_file_roundtrip(sqlite_file: Path) -> None:
    module = _migration_module()
    state_path = Path(f"{sqlite_file}.state.json")
    module.save_state(
        state_path, {"tables": {"request_logs": {"cursor": "log2", "synced": 2}}}
    )
    loaded = module.load_state(state_path)
    assert loaded["tables"]["request_logs"]["cursor"] == "log2"
    state_path.unlink(missing_ok=True)
    assert module.load_state(state_path) == {"tables": {}}


def test_dry_run_touches_no_postgres(
    populated_sqlite: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _migration_module()
    assert module.main(["--sqlite", str(populated_sqlite), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "request_logs" in output
    assert "dry run" in output


def test_copy_without_a_url_fails_cleanly(
    populated_sqlite: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _migration_module()
    assert module.main(["--sqlite", str(populated_sqlite)]) == 2
    assert "--pg-url" in capsys.readouterr().out


def test_plan_shows_columns_that_only_exist_in_sqlite(
    populated_sqlite: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = sqlite3.connect(populated_sqlite)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN legacy_flag INTEGER")
        conn.commit()
    finally:
        conn.close()
    module = _migration_module()
    assert module.main(["--sqlite", str(populated_sqlite), "--dry-run"]) == 0
    output = capsys.readouterr().out
    # The column exists in SQLite but not in the ORM, so it is never copied:
    # the plan has to say so instead of dropping the data quietly.
    assert "legacy_flag" in output
    assert "extra-in-sqlite" in output


def test_describe_gaps_reports_orphan_tables(populated_sqlite: Path) -> None:
    conn = sqlite3.connect(populated_sqlite)
    try:
        conn.execute("CREATE TABLE legacy_usage (id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    module = _migration_module()
    planned = [plan.name for plan in module.plan_tables(populated_sqlite)]
    absent, orphans = module.describe_gaps(populated_sqlite, planned)
    assert "legacy_usage" in orphans
    # create_all built every ORM table, so nothing is absent here.
    assert absent == []


def test_describe_gaps_reports_tables_missing_from_the_file(
    sqlite_file: Path,
) -> None:
    conn = sqlite3.connect(sqlite_file)
    try:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    module = _migration_module()
    absent, orphans = module.describe_gaps(sqlite_file, ["users"])
    assert "users" not in absent
    assert "request_logs" in absent
    assert orphans == []


def test_default_plan_covers_every_orm_table(populated_sqlite: Path) -> None:
    module = _migration_module()
    plans = module.plan_tables(populated_sqlite)
    planned = {plan.name for plan in plans}
    # Every table the ORM declares has to be part of the default plan, so a
    # new table in orm.py can never be skipped by the migration.
    assert planned == set(Base.metadata.tables)


def test_unknown_table_is_planned_with_its_real_row_count(
    populated_sqlite: Path,
) -> None:
    conn = sqlite3.connect(populated_sqlite)
    try:
        conn.execute("CREATE TABLE legacy_usage (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO legacy_usage (id) VALUES ('a')")
        conn.execute("INSERT INTO legacy_usage (id) VALUES ('b')")
        conn.commit()
    finally:
        conn.close()
    module = _migration_module()
    plan = module.plan_tables(populated_sqlite, ["legacy_usage"])[0]
    assert plan.rows == 2
    assert plan.columns == ["id"]


def test_dry_run_warns_about_tables_nothing_migrates(
    populated_sqlite: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = sqlite3.connect(populated_sqlite)
    try:
        conn.execute("CREATE TABLE legacy_usage (id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    module = _migration_module()
    assert module.main(["--sqlite", str(populated_sqlite), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "legacy_usage" in output
    assert "warning" in output
