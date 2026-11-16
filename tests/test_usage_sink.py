"""ZS-1: the usage write path keeps SQL out of the request.

The request path only appends one complete row to an append-only WAL; a
background task batches the rows into ``request_logs``. These tests pin the
properties the ticket asks for:

  * no statement runs while the request records its usage (statement probe),
  * the recorded row covers every ``request_logs`` column,
  * a failing sink never fails the request and keeps the row in the WAL,
  * concurrent records share one batch and the WAL stays readable,
  * a replayed row does not create a second row.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest
from sqlalchemy import event, select

from l01_core import runtime
from l01_core.config import Settings
from l04_repo import orm
from l05_service import body_logging, ledger, log_pipeline, usage_sink
from l05_service.app_settings import save_body_settings
from l05_service.body_logging import BodySnapshot
from l05_service.chat import _write_log
from l05_service.llm.gateway import Usage
from tests.fake_upstream_server import FakeUpstreamServer

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"


class _StatementCounter:
    """Count the statements the engine runs inside the block."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self.count = 0
        self.statements: list[str] = []

    def _on_execute(self, *args, **kwargs) -> None:
        self.count += 1
        statement = args[2] if len(args) > 2 else ""
        self.statements.append(" ".join(str(statement).split()))

    def __enter__(self) -> Self:
        event.listen(
            self._engine.sync_engine, "before_cursor_execute", self._on_execute
        )
        return self

    def __exit__(self, *exc_info) -> bool:
        event.remove(
            self._engine.sync_engine, "before_cursor_execute", self._on_execute
        )
        return False


@pytest.fixture
def wal_root() -> Iterator[Path]:
    path = SCRATCH_ROOT / f"usage-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def test_settings(database_url: str, wal_root: Path) -> Settings:
    """The usage pipeline is opt-in: USAGE_PIPELINE=off by default."""
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        usage_pipeline="on",
        usage_wal_dir=str(wal_root / "usage"),
        # The background runner must not race the explicit flushes below.
        usage_sink_interval_ms=60_000,
    )


@pytest.fixture(autouse=True)
def _isolated_usage_sink():
    usage_sink.usage_sink.reset()
    runtime.reset_settings()
    yield
    usage_sink.usage_sink.reset()
    runtime.reset_settings()


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


async def _write(app, request_id: str, **overrides) -> None:
    """Run the request's usage write exactly like the gateway does."""
    values = {
        "request_id": request_id,
        "requested_model": "gpt-test",
        "api_key": SimpleNamespace(id="key-1", label="ci"),
        "user": SimpleNamespace(id="user-1"),
        "target": None,
        "http_status": 200,
        "usage": Usage(
            prompt_tokens=11, completion_tokens=7, total_tokens=18, cached_tokens=3
        ),
        "total_ms": 42,
        "attempts": [{"channel_slug": "channel-a", "status": "success"}],
        "response_model": "gpt-test",
        "ttft_ms": 9,
        "client_ip": "203.0.113.7",
        "request_headers": {
            "x-forwarded-for": "198.51.100.9",
            "authorization": "Bearer secret-token",
        },
    }
    values.update(overrides)
    async with app.state.session_factory() as session:
        await _write_log(session, **values)


async def _rows(app) -> list[orm.RequestLogORM]:
    async with app.state.session_factory() as session:
        result = await session.execute(select(orm.RequestLogORM))
        return list(result.scalars().all())


def _wal_entries(wal_root: Path) -> list[dict]:
    """Every record currently stored in the WAL, oldest first."""
    entries: list[dict] = []
    for path in sorted(wal_root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if "committed" not in payload:
                entries.append(payload)
    return entries


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(client, headers, url: str) -> dict:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "直连测试渠道",
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str) -> dict:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "gpt-test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers, label: str) -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


async def test_request_path_records_without_touching_the_database(client, app):
    """Acceptance 1: the usage write runs no statement inside the request."""
    with _StatementCounter(app.state.db_engine) as counter:
        await _write(app, "req-zero-sql")

    assert counter.count == 0
    assert counter.statements == []
    stats = usage_sink.usage_sink.stats()
    assert stats["enabled"] is True
    assert stats["pending"] == 1
    assert stats["written"] == 0


async def test_record_covers_every_request_logs_column(client, app, wal_root):
    """Acceptance 2: the record holds every column of request_logs."""
    await _write(app, "req-columns")
    usage_sink.usage_sink.flush_wal()

    entries = _wal_entries(wal_root)
    assert len(entries) == 1
    row = entries[0]["row"]
    columns = {column.name for column in orm.RequestLogORM.__table__.columns}
    assert set(row) == columns

    assert row["request_id"] == "req-columns"
    assert row["user_id"] == "user-1"
    assert row["api_key_id"] == "key-1"
    assert row["api_key_label"] == "ci"
    assert row["requested_model"] == "gpt-test"
    assert row["source"] == "chat"
    assert row["http_status"] == 200
    assert row["prompt_tokens"] == 11
    assert row["completion_tokens"] == 7
    assert row["cached_tokens"] == 3
    assert row["total_ms"] == 42
    assert row["ttft_ms"] == 9
    assert row["is_stream"] is False
    assert row["usage_estimated"] is False
    assert row["client_ip"] == "203.0.113.7"
    assert json.loads(row["attempts_json"])[0]["channel_slug"] == "channel-a"
    assert json.loads(row["forwarded_json"])[0]["header"] == "X-Forwarded-For"
    assert "secret-token" not in row["request_headers_json"]


async def test_flush_writes_exactly_one_row_per_request(client, app):
    await _write(app, "req-flush")

    assert await usage_sink.flush_usage_records() == 1

    rows = await _rows(app)
    assert [row.request_id for row in rows] == ["req-flush"]
    assert rows[0].prompt_tokens == 11
    assert rows[0].api_key_label == "ci"
    assert rows[0].total_ms == 42


async def test_sink_failure_keeps_the_row_in_the_wal(
    client, app, wal_root, monkeypatch
):
    """Acceptance 3: a failing sink loses nothing and fails nothing."""

    async def boom(self, records):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(usage_sink.UsageSink, "write", boom)

    await _write(app, "req-fault")

    assert await usage_sink.flush_usage_records() == 0
    assert usage_sink.usage_sink.stats()["failed_batches"] == 1
    assert await _rows(app) == []
    assert [entry["row"]["request_id"] for entry in _wal_entries(wal_root)] == [
        "req-fault"
    ]


async def test_request_still_succeeds_when_the_sink_cannot_write(
    client, app, wal_root, upstream, monkeypatch
):
    """Acceptance 3: the client gets its answer even when logging is broken."""

    async def boom(self, records):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(usage_sink.UsageSink, "write", boom)

    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers, label="sink-down")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200, response.text
    entries = _wal_entries(wal_root)
    assert len(entries) == 1
    assert entries[0]["row"]["http_status"] == 200


async def test_recovery_delivers_the_row_once(client, app, monkeypatch):
    """Acceptance 3: after the failure the row is delivered, exactly once."""
    original = usage_sink.UsageSink.write
    calls = {"count": 0}

    async def flaky(self, records):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("database is gone")
        return await original(self, records)

    monkeypatch.setattr(usage_sink.UsageSink, "write", flaky)

    await _write(app, "req-recover")

    assert await usage_sink.flush_usage_records() == 0
    assert await usage_sink.flush_usage_records() == 1

    rows = await _rows(app)
    assert [row.request_id for row in rows] == ["req-recover"]


async def test_replayed_row_does_not_duplicate(client, app, wal_root):
    """Acceptance 3: replaying a WAL segment is idempotent on request_id."""
    await _write(app, "req-replay")
    usage_sink.usage_sink.flush_wal()
    entry = _wal_entries(wal_root)[0]

    assert await usage_sink.flush_usage_records() == 1
    await usage_sink.usage_sink.write([entry])

    rows = await _rows(app)
    assert [row.request_id for row in rows] == ["req-replay"]


async def test_concurrent_records_share_one_batch(client, app, wal_root, monkeypatch):
    """Acceptance 4: N requests share one batch and the WAL stays readable."""
    batches: list[int] = []
    original = usage_sink.UsageSink.write

    async def spy(self, records):
        batches.append(len(records))
        return await original(self, records)

    monkeypatch.setattr(usage_sink.UsageSink, "write", spy)

    await asyncio.gather(
        *(_write(app, f"req-concurrent-{index}") for index in range(25))
    )

    usage_sink.usage_sink.flush_wal()
    entries = _wal_entries(wal_root)
    assert len(entries) == 25
    assert len({entry["row"]["request_id"] for entry in entries}) == 25

    assert await usage_sink.flush_usage_records() == 25
    assert batches == [25]
    rows = await _rows(app)
    assert len(rows) == 25


async def test_body_row_is_written_after_its_parent(client, app):
    """request_bodies has a foreign key on request_logs: order matters."""
    async with app.state.session_factory() as session:
        await save_body_settings(session, mode="all", updated_by="zs-1")
        await session.commit()
    body_logging.invalidate_body_settings_cache()

    await _write(
        app,
        "req-body",
        body_snapshot=BodySnapshot(request_body={"messages": [{"role": "user"}]}),
    )

    assert await usage_sink.flush_usage_records() == 1

    async with app.state.session_factory() as session:
        parent = (
            await session.execute(
                select(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == "req-body"
                )
            )
        ).scalar_one()
        body = (
            await session.execute(
                select(orm.RequestBodyORM).where(
                    orm.RequestBodyORM.request_id == "req-body"
                )
            )
        ).scalar_one_or_none()
    assert parent is not None
    assert body is not None
    assert body.request_body


async def test_pipeline_off_keeps_the_single_write_path(database_url: str):
    """The new pipeline is opt-in; the old path stays byte for byte."""
    settings = Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
    )
    from l07_bootstrap.app_factory import create_app

    app = create_app(settings=settings, database_url=settings.database_url)
    async with app.router.lifespan_context(app):
        await _write(app, "req-legacy")

        rows = await _rows(app)
        assert [row.request_id for row in rows] == ["req-legacy"]
        assert usage_sink.usage_sink.stats()["enabled"] is False


async def test_ledger_split_still_charges_when_the_usage_pipeline_is_on(
    app, client, wal_root, monkeypatch
):
    """LEDGER_SPLIT=on keeps writing request_ledger rows on the new path."""
    base = runtime.current_settings()
    monkeypatch.setattr(
        runtime,
        "current_settings",
        lambda: base.model_copy(
            update={
                "ledger_split": "on",
                "ledger_wal_dir": str(wal_root / "ledger"),
            }
        ),
    )
    monkeypatch.setattr(log_pipeline, "split_enabled", lambda: True)
    await ledger.ledger_service.startup(app.state.session_factory)
    try:
        # The request itself still runs no statement.
        with _StatementCounter(app.state.db_engine) as counter:
            await _write(app, "req-usage-ledger")
        assert counter.count == 0
        assert await ledger.ledger_service.flush_once() == 1
        async with app.state.session_factory() as session:
            ledger_rows = list(
                (
                    await session.execute(
                        select(orm.RequestLedgerORM).where(
                            orm.RequestLedgerORM.request_id == "req-usage-ledger"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(ledger_rows) == 1
        assert ledger_rows[0].prompt_tokens == 11
        # request_logs stays empty until the sink flushes: the ledger charge
        # must not drag SQL back into the request.
        assert await _rows(app) == []
    finally:
        ledger.ledger_service.reset()
