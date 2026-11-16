from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from l01_core.config import get_settings
from l01_core.ids import new_id
from l01_core.logging import get_logger
from l01_core.timeutil import now_ms
from l02_infra.dialect import (
    POSTGRESQL,
    SQLITE,
    column_names,
    dialect_of,
    normalize_database_url,
    require_driver,
)


class Base(DeclarativeBase):
    pass


settings = get_settings()

logger = get_logger("system.db")


def build_engine(database_url: str, settings_obj=None) -> AsyncEngine:
    """Create an engine, making sure the SQLite directory and pragmas are ready.

    Pool sizing is explicit: the previous SQLAlchemy defaults (5 + 10) were
    exhausted by ordinary dashboard traffic and streaming sessions. SQLite
    still serializes writers, so the pool is deliberately modest; the real
    streaming fix is releasing the request session before the SSE body starts.
    """
    url = normalize_database_url(database_url)
    dialect = dialect_of(url)

    if dialect == SQLITE:
        db_path = url.split("///", 1)[-1]
        if db_path and not db_path.startswith(":memory:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    else:
        require_driver(url)

    pool_kwargs = {
        "pool_pre_ping": bool(getattr(settings_obj, "db_pool_pre_ping", True)),
        "pool_size": int(getattr(settings_obj, "db_pool_size", 10)),
        "max_overflow": int(getattr(settings_obj, "db_max_overflow", 20)),
        "pool_timeout": float(getattr(settings_obj, "db_pool_timeout_s", 30.0)),
        "pool_recycle": int(getattr(settings_obj, "db_pool_recycle_s", 1800)),
    }
    if dialect == POSTGRESQL:
        # PostgreSQL has no single writer, so the pool can grow, and the
        # server needs to know who is connected: application_name shows up in
        # pg_stat_activity, statement_timeout keeps one slow query from
        # holding a pooled connection forever.
        server_settings: dict[str, str] = {
            "application_name": str(
                getattr(settings_obj, "db_application_name", "slipstream")
            )
        }
        statement_timeout_ms = int(
            getattr(settings_obj, "db_statement_timeout_ms", 0) or 0
        )
        if statement_timeout_ms > 0:
            server_settings["statement_timeout"] = str(statement_timeout_ms)
        pool_kwargs["connect_args"] = {"server_settings": server_settings}

    engine = create_async_engine(url, echo=False, future=True, **pool_kwargs)

    if dialect == SQLITE:

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            # WAL + NORMAL is the standard durability/throughput trade-off:
            # a power loss may drop recent commits but cannot corrupt the DB.
            # FULL fsyncs every commit, and this app commits several times per
            # request, which serialises the single SQLite writer.
            cursor.execute("PRAGMA synchronous=NORMAL")
            # Short write bursts must queue instead of surfacing as HTTP 500s.
            cursor.execute("PRAGMA busy_timeout=15000")
            # 排序/分组超出缓存时 SQLite 会去 %TEMP% 建临时文件，环境一紧张就
            # 抛 "unable to open database file"（2026-11-14 日志页深翻页 500 的
            # 根因：EXPLAIN QUERY PLAN 显示 USE TEMP B-TREE FOR ORDER BY）。
            # 本应用的排序集只有几千行，放内存里既快又不再依赖临时目录可写。
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


engine = build_engine(settings.database_url)
SessionLocal = build_sessionmaker(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


DEFAULT_MODEL_PARAMETERS = {
    "max_input_tokens": 0,
    "max_output_tokens": 0,
    "reasoning_level": "",
    "input_types": ["text"],
    "output_types": ["text"],
}


def _table_columns(conn, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    """The column names of a table, on whichever dialect is in use.

    SQLite answers with PRAGMA table_info, PostgreSQL with
    information_schema; both return the same set of names.
    """
    return column_names(conn, table)


LEDGER_COLUMN_DDL = {
    "request_ledger": {
        "id": "TEXT PRIMARY KEY",
        "request_id": "TEXT NOT NULL DEFAULT ''",
        "user_id": "TEXT",
        "api_key_id": "TEXT",
        "final_channel_model_id": "TEXT",
        "requested_model": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'chat'",
        "http_status": "INTEGER NOT NULL DEFAULT 200",
        "error_code": "TEXT",
        "prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
        "completion_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_ratio": "REAL NOT NULL DEFAULT 0",
        "cost_usd": "REAL NOT NULL DEFAULT 0",
        "usage_estimated": "BOOLEAN NOT NULL DEFAULT 0",
        "created_at": "INTEGER NOT NULL DEFAULT 0",
        "written_at": "INTEGER NOT NULL DEFAULT 0",
    },
    "ledger_adjustments": {
        "id": "TEXT PRIMARY KEY",
        "request_id": "TEXT NOT NULL DEFAULT ''",
        "delta_prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
        "delta_completion_tokens": "INTEGER NOT NULL DEFAULT 0",
        "delta_cached_tokens": "INTEGER NOT NULL DEFAULT 0",
        "delta_cost_usd": "REAL NOT NULL DEFAULT 0",
        "reason": "TEXT NOT NULL DEFAULT 'reconcile'",
        "actor": "TEXT NOT NULL DEFAULT ''",
        "created_at": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _migrate_ledger_schema(conn) -> None:  # type: ignore[no-untyped-def]
    """Backfill ledger columns for databases created by an earlier build.

    The tables themselves come from Base.metadata.create_all; like the rest
    of the legacy migration this only adds missing columns with plain ALTER
    TABLE, so no migration framework is needed.
    """
    for table, columns in LEDGER_COLUMN_DDL.items():
        existing = _table_columns(conn, table)
        if not existing:
            continue
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _migrate_legacy_schema(conn) -> None:  # type: ignore[no-untyped-def]
    """Bring pre-parameters databases forward without a migration framework.

    The legacy columns are kept only long enough to copy their values; once the
    unified JSON is populated SQLite can drop them, so ORM and database agree.
    """
    _migrate_ledger_schema(conn)

    channel_columns = _table_columns(conn, "channels")
    if channel_columns:
        if "priority" not in channel_columns:
            conn.exec_driver_sql(
                "ALTER TABLE channels ADD COLUMN priority INTEGER NOT NULL DEFAULT 1"
            )
        if "timeout_s" not in channel_columns:
            conn.exec_driver_sql(
                "ALTER TABLE channels ADD COLUMN timeout_s INTEGER NOT NULL DEFAULT 60"
            )

    # Legacy databases kept their only API key in channels.api_key_enc. Backfill
    # channel_keys once at startup so every GET stays read-only (ssot §4.1/§5.9).
    key_columns = _table_columns(conn, "channel_keys")
    required_key_columns = {
        "id",
        "channel_id",
        "label",
        "key_enc",
        "enabled",
        "created_at",
        "updated_at",
    }
    if channel_columns and required_key_columns <= key_columns:
        now = now_ms()
        legacy_keys = conn.exec_driver_sql(
            "SELECT c.id, c.api_key_enc FROM channels c "
            "WHERE c.api_key_enc != '' AND NOT EXISTS ("
            "SELECT 1 FROM channel_keys k WHERE k.channel_id = c.id)"
        ).fetchall()
        for channel_id, key_enc in legacy_keys:
            conn.exec_driver_sql(
                "INSERT INTO channel_keys "
                "(id, channel_id, label, key_enc, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (new_id(), channel_id, "默认密钥", key_enc, now, now),
            )

    log_columns = _table_columns(conn, "request_logs")
    if log_columns:
        if "ttfa_ms" not in log_columns:
            conn.exec_driver_sql("ALTER TABLE request_logs ADD COLUMN ttfa_ms INTEGER")
        if "ttlt_ms" not in log_columns:
            conn.exec_driver_sql("ALTER TABLE request_logs ADD COLUMN ttlt_ms INTEGER")
        if "source" not in log_columns:
            conn.exec_driver_sql(
                "ALTER TABLE request_logs ADD COLUMN source TEXT NOT NULL DEFAULT 'chat'"
            )
        if "client_ip" not in log_columns:
            conn.exec_driver_sql(
                "ALTER TABLE request_logs ADD COLUMN client_ip TEXT NOT NULL DEFAULT ''"
            )
        if "request_headers_json" not in log_columns:
            conn.exec_driver_sql(
                "ALTER TABLE request_logs ADD COLUMN request_headers_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        if "forwarded_json" not in log_columns:
            conn.exec_driver_sql(
                "ALTER TABLE request_logs ADD COLUMN forwarded_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )

    # ADR-077 routing policy override columns on combos. Both degrade safely:
    # "" = follow the global strategy, 0 = follow the global window.
    combo_columns = _table_columns(conn, "combos")
    if combo_columns:
        if "route_strategy" not in combo_columns:
            conn.exec_driver_sql(
                "ALTER TABLE combos ADD COLUMN route_strategy "
                "TEXT NOT NULL DEFAULT ''"
            )
        if "route_window_minutes" not in combo_columns:
            conn.exec_driver_sql(
                "ALTER TABLE combos ADD COLUMN route_window_minutes "
                "INTEGER NOT NULL DEFAULT 0"
            )

    key_columns = _table_columns(conn, "api_keys")
    if key_columns and "key_enc" not in key_columns:
        conn.exec_driver_sql(
            "ALTER TABLE api_keys ADD COLUMN key_enc TEXT NOT NULL DEFAULT ''"
        )

    model_columns = _table_columns(conn, "channel_models")
    if not model_columns:
        return
    if "parameters_json" not in model_columns:
        conn.exec_driver_sql("ALTER TABLE channel_models ADD COLUMN parameters_json TEXT")
    # 缓存计价两列：NULL 表示未配置，读取命中回退 input_price_per_1m 原价。
    if "cache_read_price_per_1m" not in model_columns:
        conn.exec_driver_sql(
            "ALTER TABLE channel_models ADD COLUMN cache_read_price_per_1m REAL"
        )
    if "cache_write_price_per_1m" not in model_columns:
        conn.exec_driver_sql(
            "ALTER TABLE channel_models ADD COLUMN cache_write_price_per_1m REAL"
        )
    # Very old tables predate health_status entirely; half_open rows cannot exist there.
    if "health_status" in model_columns and "half_open_since" not in model_columns:
        conn.exec_driver_sql("ALTER TABLE channel_models ADD COLUMN half_open_since INTEGER")
        # Rows left half_open by an older build never recorded when the probe
        # started. Startup means no request is in flight, so free the phantom slot.
        conn.exec_driver_sql(
                "UPDATE channel_models SET health_status='normal', opened_at=NULL, "
                "cooldown_until=NULL, fault_until=NULL, half_open_since=NULL "
                "WHERE health_status='half_open'"
            )

    if "probe_default" not in model_columns:
        conn.exec_driver_sql(
            "ALTER TABLE channel_models ADD COLUMN probe_default BOOLEAN NOT NULL DEFAULT 0"
        )

    # 模型类型两列：老库补列后历史行默认是 chat / default。
    model_type_missing = "model_type" not in model_columns
    if model_type_missing:
        conn.exec_driver_sql(
            "ALTER TABLE channel_models ADD COLUMN model_type "
            "TEXT NOT NULL DEFAULT 'chat'"
        )
    if "model_type_source" not in model_columns:
        conn.exec_driver_sql(
            "ALTER TABLE channel_models ADD COLUMN model_type_source "
            "TEXT NOT NULL DEFAULT 'default'"
        )
    # 定时封禁：NULL = 无定期封禁（此时 enabled=False 表示永久封禁），整数是
    # 到期毫秒。到期由读取侧惰性判活，没有后台任务，所以只补列、不回填。
    if "banned_until" not in model_columns:
        conn.exec_driver_sql("ALTER TABLE channel_models ADD COLUMN banned_until INTEGER")
    # 回填只在本次首次补列时跑；老到没有 upstream_model 列的表跳过（无从推断）。
    if model_type_missing and "upstream_model" in model_columns:
        # 存量回填：依据来自上游模型 id，命中名称启发式才改类型，来源记
        # upstream；判不出来的保持 chat/default。只在首次补列时跑一次，
        # 免得覆盖后来的人工改判（manual）。
        conn.exec_driver_sql(
            "UPDATE channel_models SET model_type='rerank', "
            "model_type_source='upstream' "
            "WHERE lower(upstream_model) LIKE '%rerank%'"
        )
        conn.exec_driver_sql(
            "UPDATE channel_models SET model_type='embedding', "
            "model_type_source='upstream' "
            "WHERE lower(upstream_model) LIKE '%embed%' AND model_type='chat'"
        )

    legacy_available = bool(
        model_columns & {"context_window", "capabilities_json", "display_name"}
    )
    if legacy_available:
        context_expr = (
            "context_window" if "context_window" in model_columns else "0"
        )
        capabilities_expr = (
            "capabilities_json" if "capabilities_json" in model_columns else "'[]'"
        )
        parameters_expr = (
            "parameters_json" if "parameters_json" in model_columns else "NULL"
        )
        rows = conn.exec_driver_sql(
            f"SELECT id, {context_expr}, {capabilities_expr}, {parameters_expr} "
            "FROM channel_models"
        ).fetchall()
        for row in rows:
            model_id, context_window, capabilities_raw, parameters_raw = row
            # A previous startup may have failed to drop the legacy columns
            # after a successful backfill. Never overwrite newer JSON.
            if parameters_raw:
                continue
            try:
                capabilities = json.loads(capabilities_raw or "[]")
            except (TypeError, ValueError):
                capabilities = []
            if not isinstance(capabilities, list):
                capabilities = []
            input_types = ["text"]
            for capability in capabilities:
                text_value = str(capability).strip().lower()
                if text_value == "vision":
                    text_value = "image"
                if text_value in {"image", "audio", "video", "file"}:
                    if text_value not in input_types:
                        input_types.append(text_value)
            parameters = {
                **DEFAULT_MODEL_PARAMETERS,
                "max_input_tokens": int(context_window or 0),
                "input_types": input_types,
            }
            conn.exec_driver_sql(
                "UPDATE channel_models SET parameters_json = ? WHERE id = ?",
                (json.dumps(parameters, ensure_ascii=False), model_id),
            )

    # Drop legacy columns only after the new JSON has been backfilled. Each
    # drop is best-effort: older SQLite builds or custom indexes may refuse.
    for column in ("display_name", "context_window", "capabilities_json", "priority"):
        if column not in model_columns:
            continue
        try:
            conn.exec_driver_sql(f"ALTER TABLE channel_models DROP COLUMN {column}")
        except Exception as exc:  # older SQLite or custom indexes may refuse
            logger.warning("删除遗留列 %s 失败：%s", column, exc)


POSTGRES_COLUMN_DDL = {
    "request_logs": {
        "ttfa_ms": "INTEGER",
        "ttlt_ms": "INTEGER",
        "source": "TEXT NOT NULL DEFAULT 'chat'",
        "client_ip": "TEXT NOT NULL DEFAULT ''",
        "request_headers_json": "TEXT NOT NULL DEFAULT '{}'",
        "forwarded_json": "TEXT NOT NULL DEFAULT '[]'",
    },
    "channels": {
        "priority": "INTEGER NOT NULL DEFAULT 1",
        "timeout_s": "INTEGER NOT NULL DEFAULT 60",
    },
    "api_keys": {
        "key_enc": "TEXT NOT NULL DEFAULT ''",
    },
    "channel_models": {
        "parameters_json": "TEXT",
        "cache_read_price_per_1m": "DOUBLE PRECISION",
        "cache_write_price_per_1m": "DOUBLE PRECISION",
        "probe_default": "BOOLEAN NOT NULL DEFAULT false",
        "model_type": "TEXT NOT NULL DEFAULT 'chat'",
        "model_type_source": "TEXT NOT NULL DEFAULT 'default'",
        "half_open_since": "BIGINT",
    },
    "request_ledger": {
        "id": "TEXT PRIMARY KEY",
        "request_id": "TEXT NOT NULL DEFAULT ''",
        "user_id": "TEXT",
        "api_key_id": "TEXT",
        "final_channel_model_id": "TEXT",
        "requested_model": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'chat'",
        "http_status": "INTEGER NOT NULL DEFAULT 200",
        "error_code": "TEXT",
        "prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
        "completion_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_ratio": "DOUBLE PRECISION NOT NULL DEFAULT 0",
        "cost_usd": "DOUBLE PRECISION NOT NULL DEFAULT 0",
        "usage_estimated": "BOOLEAN NOT NULL DEFAULT false",
        "created_at": "BIGINT NOT NULL DEFAULT 0",
        "written_at": "BIGINT NOT NULL DEFAULT 0",
    },
    "ledger_adjustments": {
        "id": "TEXT PRIMARY KEY",
        "request_id": "TEXT NOT NULL DEFAULT ''",
        "delta_prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
        "delta_completion_tokens": "INTEGER NOT NULL DEFAULT 0",
        "delta_cached_tokens": "INTEGER NOT NULL DEFAULT 0",
        "delta_cost_usd": "DOUBLE PRECISION NOT NULL DEFAULT 0",
        "reason": "TEXT NOT NULL DEFAULT 'reconcile'",
        "actor": "TEXT NOT NULL DEFAULT ''",
        "created_at": "BIGINT NOT NULL DEFAULT 0",
    },
}


def _postgres_column_specs() -> dict[str, dict[str, str]]:
    """The DDL of every column an older PostgreSQL database may still miss.

    Derived from the ORM metadata, so a new column in orm.py is added by the
    next startup and cannot be forgotten. POSTGRES_COLUMN_DDL overrides a
    column where a hand written default is the only safe form (a NOT NULL
    column without a server default cannot be added to a table that already
    has rows); those overrides are what allow a startup upgrade without an
    operator in the loop.
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateColumn

    # Importing the models is what registers them on Base.metadata; without it
    # the metadata is empty and no column would ever be added.
    from l04_repo import orm  # noqa: F401

    dialect = postgresql.dialect()
    specs: dict[str, dict[str, str]] = {}
    for table in Base.metadata.sorted_tables:
        overrides = POSTGRES_COLUMN_DDL.get(table.name, {})
        for column in table.columns:
            ddl = overrides.get(column.name)
            if ddl is None:
                ddl = str(CreateColumn(column).compile(dialect=dialect))
                if not column.nullable and column.server_default is None:
                    # PostgreSQL rejects a NOT NULL column without a default on
                    # a table that already has rows. Add it nullable and let an
                    # operator backfill and tighten it: an upgrade that stops
                    # half way is worse than a nullable column.
                    ddl = re.sub(r"\s+NOT NULL$", "", ddl)
            specs.setdefault(table.name, {})[column.name] = ddl
    return specs


def _migrate_postgres_schema(conn) -> None:  # type: ignore[no-untyped-def]
    """Add the columns a PostgreSQL database may still miss.

    A fresh PostgreSQL database gets every column from
    Base.metadata.create_all; this only matters for a database created by an
    older build. PostgreSQL has no legacy SQLite backfill to run (no dropped
    columns, no channel_keys backfill), and ADD COLUMN IF NOT EXISTS makes the
    whole step idempotent, so it is safe to run on every startup.
    """
    for table, columns in _postgres_column_specs().items():
        existing = _table_columns(conn, table)
        if not existing:
            continue
        for name, ddl in columns.items():
            if name in existing:
                continue
            conn.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}"
            )


async def init_db(db_engine: AsyncEngine | None = None) -> None:
    from l04_repo import orm  # noqa: F401

    target = db_engine or engine
    backend = target.url.get_backend_name()
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if backend == "sqlite":
            await conn.run_sync(_migrate_legacy_schema)
            await conn.execute(text("PRAGMA journal_mode=WAL"))
        else:
            # PostgreSQL has no single writer and no legacy SQLite columns to
            # backfill; only the idempotent column additions apply.
            await conn.run_sync(_migrate_postgres_schema)
