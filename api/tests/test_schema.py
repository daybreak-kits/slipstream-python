from __future__ import annotations

import json

from sqlalchemy import create_engine, text

from l02_infra.db import _migrate_legacy_schema
from l03_domain.schedule import parse_schedule
from l04_repo import orm


async def _columns(engine, table: str) -> set[str]:
    async with engine.connect() as connection:
        rows = (await connection.execute(text(f"PRAGMA table_info({table})"))).all()
    return {row[1] for row in rows}


async def test_channel_models_have_dual_trip_counters_and_no_weight(client, app):
    columns = await _columns(app.state.db_engine, "channel_models")

    assert "weight" not in columns
    assert "open_count" not in columns
    assert {"consecutive_failures", "consecutive_trip_count", "escalation_trip_count"} <= columns


async def test_combo_targets_have_no_weight(client, app):
    columns = await _columns(app.state.db_engine, "combo_targets")

    assert "weight" not in columns


async def test_request_bodies_table_matches_spec(client, app):
    columns = await _columns(app.state.db_engine, "request_bodies")

    assert columns == {
        "request_id",
        "request_body",
        "upstream_body",
        "response_body",
        "response_summary",
        "reasoning_content",
        "created_at",
    }


async def test_app_settings_table_matches_spec(client, app):
    columns = await _columns(app.state.db_engine, "app_settings")

    assert columns == {"key", "value_json", "updated_at", "updated_by"}


async def test_request_logs_do_not_carry_full_bodies(client, app):
    columns = await _columns(app.state.db_engine, "request_logs")

    assert not {"request_body_json", "upstream_body_json", "response_body_json"} & columns


async def test_request_logs_have_four_latency_columns(client, app):
    columns = await _columns(app.state.db_engine, "request_logs")

    assert {"ttft_ms", "ttfa_ms", "ttlt_ms", "total_ms"} <= columns


def test_legacy_request_logs_gain_latency_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE request_logs (id TEXT PRIMARY KEY, request_id TEXT)"
        )
        _migrate_legacy_schema(connection)
        _migrate_legacy_schema(connection)
        columns = {
            str(row[1])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(request_logs)"
            ).fetchall()
        }

    assert {"ttfa_ms", "ttlt_ms"} <= columns


def test_legacy_channel_model_schema_is_backfilled_without_clobbering_json():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE channel_models ("
            "id TEXT PRIMARY KEY, context_window INTEGER, "
            "capabilities_json TEXT, display_name TEXT, priority INTEGER, "
            "parameters_json TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO channel_models VALUES "
            "('legacy', 128000, '[\"chat\", \"vision\"]', 'Old', 3, NULL)"
        )
        custom = json.dumps(
            {
                "max_input_tokens": 64000,
                "max_output_tokens": 2048,
                "reasoning_level": "high",
                "input_types": ["text"],
                "output_types": ["text"],
            }
        )
        connection.exec_driver_sql(
            "INSERT INTO channel_models VALUES "
            "('current', 1, '[]', '', 1, ?)",
            (custom,),
        )

        _migrate_legacy_schema(connection)

        rows = dict(
            connection.exec_driver_sql(
                "SELECT id, parameters_json FROM channel_models"
            ).fetchall()
        )
        columns = {
            str(row[1])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(channel_models)"
            ).fetchall()
        }

    migrated = json.loads(rows["legacy"])
    assert migrated["max_input_tokens"] == 128000
    assert migrated["input_types"] == ["text", "image"]
    assert json.loads(rows["current"])["reasoning_level"] == "high"
    assert columns == {
        "id",
        "parameters_json",
        "cache_read_price_per_1m",
        "cache_write_price_per_1m",
        "probe_default",
        # ba1aa30 起 ORM 增加模型类型两列，老库迁移必须补齐它们。
        "model_type",
        "model_type_source",
        # task-83 起增加定时封禁列，老库迁移必须补齐（NULL = 未定时封禁）。
        "banned_until",
    }


def test_schedule_json_default_uses_segments_format():
    default = orm.ChannelModelORM.__table__.c.schedule_json.default.arg
    payload = json.loads(default)

    assert set(payload) <= {"timezone", "segments"}
    assert "segments" in payload
    assert parse_schedule(payload).segments == ()


def test_legacy_channels_table_gains_timeout_column():
    """A pre-timeout_s channels table must be migrated before any insert."""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE channels (id TEXT PRIMARY KEY, slug TEXT UNIQUE)"
        )
        _migrate_legacy_schema(connection)
        _migrate_legacy_schema(connection)
        columns = {
            str(row[1])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(channels)"
            ).fetchall()
        }

    assert {"priority", "timeout_s"} <= columns


def test_channel_orm_always_writes_timeout_column():
    """The ORM must supply timeout_s itself, so legacy rows never break inserts."""
    column = orm.ChannelORM.__table__.c.timeout_s

    assert column.default is not None
    assert column.server_default is not None


async def test_model_defaults_table_matches_spec(client, app):
    columns = await _columns(app.state.db_engine, "model_defaults")

    assert columns == {
        "model_id",
        "matched_by",
        "providers_json",
        "rule_version",
        "fields_json",
        "pulled_at",
    }
