"""PostgreSQL integration tests: skipped unless LLMAPI_TEST_PG_DSN is set.

This workspace has no PostgreSQL server (no psql, no docker, no network), so
these tests never run here. They are the checklist that closes the "unverified"
items in docs/postgres.md:

    export LLMAPI_TEST_PG_DSN='postgresql+asyncpg://slipstream:secret@127.0.0.1:5432/slipstream_test'
    cd api
    PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider \
        tests/test_postgres_integration.py

Use a database that only holds test data: every test writes and deletes rows.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from l02_infra.db import (  # noqa: E402
    _table_columns,
    build_engine,
    build_sessionmaker,
    init_db,
)
from l02_infra.dialect import dialect_of  # noqa: E402
from l04_repo import ledger_repo, orm, repo, usage_repo  # noqa: E402

PG_DSN = os.environ.get("LLMAPI_TEST_PG_DSN", "").strip()

pytestmark = pytest.mark.skipif(
    not PG_DSN,
    reason="set LLMAPI_TEST_PG_DSN=postgresql+asyncpg://user:pass@host/db to run",
)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@pytest.fixture()
async def engine():
    assert PG_DSN
    built = build_engine(PG_DSN)
    await init_db(built)
    try:
        yield built
    finally:
        await built.dispose()


@pytest.fixture()
async def session_factory(engine):
    return build_sessionmaker(engine)


async def test_engine_is_postgresql(engine) -> None:
    assert dialect_of(str(engine.url)) == "postgresql"


async def test_init_db_is_idempotent(engine) -> None:
    await init_db(engine)
    async with engine.connect() as conn:
        columns = await conn.run_sync(_table_columns, "request_logs")
    assert {"id", "request_id", "created_at", "ttfa_ms"} <= columns


async def test_column_introspection_matches_schema(engine) -> None:
    async with engine.connect() as conn:
        columns = await conn.run_sync(_table_columns, "request_ledger")
    assert {"id", "request_id", "cost_usd", "usage_estimated"} <= columns


async def test_request_log_insert_is_idempotent(session_factory) -> None:
    request_id = _unique("req")
    row = usage_repo.build_log_row(billing={}, observability={})
    row["request_id"] = request_id
    row["requested_model"] = "gpt-test"
    try:
        async with session_factory() as session:
            assert await usage_repo.insert_request_logs(session, [row]) == 1
            await session.commit()
        async with session_factory() as session:
            assert await usage_repo.insert_request_logs(session, [row]) == 1
            await session.commit()
        async with session_factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(orm.RequestLogORM)
                    .where(orm.RequestLogORM.request_id == request_id)
                )
            ).scalar_one()
        assert count == 1
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == request_id
                )
            )
            await session.commit()


async def test_boolean_columns_roundtrip_as_bool(session_factory) -> None:
    request_id = _unique("bool")
    row = usage_repo.build_log_row(billing={}, observability={})
    row["request_id"] = request_id
    row["requested_model"] = "gpt-test"
    row["is_stream"] = True
    row["usage_estimated"] = True
    row["rewrite_applied"] = False
    try:
        async with session_factory() as session:
            await usage_repo.insert_request_logs(session, [row])
            await session.commit()
        async with session_factory() as session:
            stored = (
                await session.execute(
                    select(orm.RequestLogORM).where(
                        orm.RequestLogORM.request_id == request_id
                    )
                )
            ).scalar_one()
        assert stored.is_stream is True
        assert stored.usage_estimated is True
        assert stored.rewrite_applied is False
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == request_id
                )
            )
            await session.commit()


async def test_ledger_insert_is_idempotent(session_factory) -> None:
    request_id = _unique("ledger")
    row = ledger_repo.ledger_row(request_id=request_id, prompt_tokens=7, cost_usd=0.25)
    try:
        async with session_factory() as session:
            assert await ledger_repo.insert_ledger_rows(session, [row]) == 1
            await session.commit()
        async with session_factory() as session:
            await ledger_repo.insert_ledger_rows(session, [row])
            await session.commit()
        async with session_factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(orm.RequestLedgerORM)
                    .where(orm.RequestLedgerORM.request_id == request_id)
                )
            ).scalar_one()
            stored = (
                await session.execute(
                    select(orm.RequestLedgerORM).where(
                        orm.RequestLedgerORM.request_id == request_id
                    )
                )
            ).scalar_one()
        assert count == 1
        assert stored.usage_estimated is False
        assert abs(stored.cost_usd - 0.25) < 1e-9
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(orm.RequestLedgerORM).where(
                    orm.RequestLedgerORM.request_id == request_id
                )
            )
            await session.commit()


async def test_request_body_upsert_keeps_the_latest_body(session_factory) -> None:
    request_id = _unique("body")
    row = usage_repo.build_log_row(billing={}, observability={})
    row["request_id"] = request_id
    row["requested_model"] = "gpt-test"
    try:
        async with session_factory() as session:
            await usage_repo.insert_request_log(session, row)
            await session.commit()
        async with session_factory() as session:
            await repo.save_request_body(session, request_id=request_id, request_body="first")
        async with session_factory() as session:
            await repo.save_request_body(session, request_id=request_id, request_body="second")
        async with session_factory() as session:
            stored = await repo.get_request_body(session, request_id)
        assert stored is not None
        assert stored.request_body == "second"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(orm.RequestBodyORM).where(
                    orm.RequestBodyORM.request_id == request_id
                )
            )
            await session.execute(
                delete(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == request_id
                )
            )
            await session.commit()


async def test_concurrent_writers_do_not_collide(session_factory) -> None:
    request_ids = [_unique("conc") for _ in range(20)]

    async def _write(request_id: str) -> None:
        row = usage_repo.build_log_row(billing={}, observability={})
        row["request_id"] = request_id
        row["requested_model"] = "gpt-test"
        async with session_factory() as session:
            await usage_repo.insert_request_logs(session, [row])
            await session.commit()

    try:
        await asyncio.gather(*(_write(request_id) for request_id in request_ids))
        async with session_factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(orm.RequestLogORM)
                    .where(orm.RequestLogORM.request_id.in_(request_ids))
                )
            ).scalar_one()
        assert count == len(request_ids)
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id.in_(request_ids)
                )
            )
            await session.commit()
