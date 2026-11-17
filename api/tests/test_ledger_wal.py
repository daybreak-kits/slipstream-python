"""Ledger + WAL behaviour: idempotent billing, crash replay, degradation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from l01_core import runtime
from l01_core.config import Settings
from l04_repo import ledger_repo, orm
from l05_service import ledger, log_pipeline
from l05_service.chat import _write_log
from l05_service.ledger import wal as wal_module
from l05_service.ledger.sink import SqliteLedgerSink
from l05_service.ledger.wal import WalWriter, replay_pending
from l05_service.llm import Usage


SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"

# A pid that is far above both pid_max and any live Windows pid: always gone.
DEAD_PID = 999_999_999


def _reassign_owner(path: Path, pid: int) -> Path:
    """Rewrite the pid segment so replay sees a writer that has exited."""
    head, tail = path.name.split("-p", 1)
    _, _, started = tail.partition("-")
    target = path.with_name(f"{head}-p{pid}-{started}")
    path.rename(target)
    return target


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


@pytest.fixture
def wal_root() -> Iterator[Path]:
    """WAL scratch dir inside the repo (the sandbox blocks fresh temp dirs)."""
    path = SCRATCH_ROOT / f"wal-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def test_settings(database_url: str, wal_root: Path) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        ledger_split="on",
        ledger_wal_dir=str(wal_root / "service"),
    )


@pytest.fixture(autouse=True)
def _isolated_ledger_service():
    ledger.ledger_service.reset()
    yield
    ledger.ledger_service.reset()


async def _write(app, request_id: str, **overrides) -> None:
    values = dict(
        request_id=request_id,
        requested_model="model-a",
        api_key=SimpleNamespace(id="key-1", label="ci"),
        user=SimpleNamespace(id="user-1"),
        target=None,
        http_status=200,
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        total_ms=42,
        attempts=[{"channel_slug": "channel-a", "status": "success"}],
        ttft_ms=7,
        session_factory=app.state.session_factory,
    )
    values.update(overrides)
    async with app.state.session_factory() as session:
        await _write_log(session, **values)


async def _ledger_rows(app, request_id: str | None = None) -> list:
    async with app.state.session_factory() as session:
        query = select(orm.RequestLedgerORM)
        if request_id is not None:
            query = query.where(orm.RequestLedgerORM.request_id == request_id)
        return list((await session.execute(query)).scalars().all())


def _quiet_pipeline(app, **kwargs) -> log_pipeline.ObservabilityPipeline:
    return log_pipeline.ObservabilityPipeline(
        session_factory=app.state.session_factory, autostart=False, **kwargs
    )


async def test_three_deliveries_of_one_request_bill_once(client, app) -> None:
    for _ in range(3):
        await ledger.ledger_service.charge(
            ledger_repo.ledger_row(
                request_id="req-bill-once",
                prompt_tokens=10,
                completion_tokens=5,
                cached_tokens=2,
                cost_usd=0.25,
            )
        )

    # All three deliveries are flushed; the unique key keeps a single row.
    assert await ledger.ledger_service.flush_once() == 3

    rows = await _ledger_rows(app, "req-bill-once")
    assert len(rows) == 1
    assert (rows[0].prompt_tokens, rows[0].completion_tokens) == (10, 5)
    assert rows[0].cached_tokens == 2
    assert rows[0].cost_usd == 0.25
    assert ledger.ledger_service.stats()["pending"] == 0


async def test_wal_replay_after_crash_conserves_every_bill(client, app, wal_root) -> None:
    directory = wal_root / "crash-wal"
    writer = WalWriter(directory, flush_ms=10_000)

    expected = []
    for index in range(1000):
        row = ledger_repo.ledger_row(
            request_id=f"req-crash-{index}",
            prompt_tokens=index,
            cost_usd=index / 1000.0,
        )
        writer.append(row)
        expected.append(row)
    writer.flush()
    # The first 400 rows were committed to the database, then acked; the
    # process died before the remaining 600 made it to the sink.
    sink = SqliteLedgerSink(app.state.session_factory)
    await sink.write(expected[:400])
    writer.ack(400)
    path = Path(writer.path)
    writer.close()
    # The process that wrote the file is gone, so replay owns the leftovers.
    path = _reassign_owner(path, DEAD_PID)

    assert await replay_pending(directory, sink) == 600
    # The replayed file is cleared, so the next startup replays nothing.
    assert path.read_text(encoding="utf-8") == ""
    assert await replay_pending(directory, sink) == 0

    async with app.state.session_factory() as session:
        count = (
            await session.execute(select(func.count(orm.RequestLedgerORM.id)))
        ).scalar_one()
        distinct = (
            await session.execute(
                select(func.count(func.distinct(orm.RequestLedgerORM.request_id)))
            )
        ).scalar_one()
        prompt = (
            await session.execute(select(func.sum(orm.RequestLedgerORM.prompt_tokens)))
        ).scalar_one()
    assert (count, distinct) == (1000, 1000)
    assert prompt == sum(row["prompt_tokens"] for row in expected)


async def test_acked_wal_is_truncated_so_restart_replays_nothing(client, app, wal_root) -> None:
    directory = wal_root / "clean-wal"
    writer = WalWriter(directory, flush_ms=10_000)
    writer.append(ledger_repo.ledger_row(request_id="req-acked"))
    writer.flush()
    writer.ack(1)

    assert writer.pending == 0
    assert Path(writer.path).read_text(encoding="utf-8") == ""
    writer.close()

    assert await replay_pending(directory, SqliteLedgerSink(app.state.session_factory)) == 0
    assert await _ledger_rows(app) == []


async def test_background_sync_fsyncs_without_an_inline_flush(wal_root) -> None:
    """P1-R8: with background_sync rows become durable without blocking anyone.

    The writer's own thread fsyncs within the flush_ms window while the test
    thread never calls flush(); the batch then replays like any other.
    """
    directory = wal_root / "bg-wal"
    writer = WalWriter(directory, flush_ms=100, background_sync=True)
    writer.append(ledger_repo.ledger_row(request_id="req-bg", prompt_tokens=1))

    deadline = time.monotonic() + 2.0
    while writer.unsynced and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert writer.unsynced is False

    path = Path(writer.path)
    writer.close()
    lines = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["request_id"] for row in lines] == ["req-bg"]


async def test_wal_write_failure_degrades_to_synchronous_write(
    client, app, monkeypatch
) -> None:
    def _boom(self, record):  # noqa: ANN001 - test double
        raise OSError("disk full")

    monkeypatch.setattr(WalWriter, "append", _boom)

    await ledger.ledger_service.charge(
        ledger_repo.ledger_row(request_id="req-degraded", prompt_tokens=3)
    )

    stats = ledger.ledger_service.stats()
    assert stats["degraded"] == 1
    assert stats["written"] == 1
    rows = await _ledger_rows(app, "req-degraded")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 3


async def test_split_off_writes_no_ledger_row(client, app, monkeypatch) -> None:
    monkeypatch.setattr(log_pipeline, "observability", _quiet_pipeline(app))
    runtime.bind_settings(app.state.settings.model_copy(update={"ledger_split": "off"}))
    try:
        assert ledger.ledger_service.enabled() is False
        await _write(app, "req-off-ledger")
    finally:
        runtime.bind_settings(app.state.settings)

    assert await _ledger_rows(app) == []
    async with app.state.session_factory() as session:
        log_row = (
            await session.execute(
                select(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == "req-off-ledger"
                )
            )
        ).scalar_one_or_none()
    assert log_row is not None
    assert (log_row.prompt_tokens, log_row.ttft_ms) == (10, 7)


async def test_chat_write_lands_in_ledger_and_log(client, app, monkeypatch) -> None:
    monkeypatch.setattr(log_pipeline, "observability", _quiet_pipeline(app))

    await _write(app, "req-chat-ledger")
    await ledger.ledger_service.flush_once()

    rows = await _ledger_rows(app, "req-chat-ledger")
    assert len(rows) == 1
    row = rows[0]
    assert (row.prompt_tokens, row.completion_tokens) == (10, 5)
    assert (row.user_id, row.api_key_id) == ("user-1", "key-1")
    assert row.requested_model == "model-a"
    assert row.source == "chat"
    assert row.http_status == 200

    async with app.state.session_factory() as session:
        log_row = (
            await session.execute(
                select(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == "req-chat-ledger"
                )
            )
        ).scalar_one_or_none()
    assert log_row is not None
    assert log_row.prompt_tokens == 10


async def test_full_diagnostics_queue_never_drops_a_bill(client, app, monkeypatch) -> None:
    pipeline = _quiet_pipeline(app, maxsize=2)
    monkeypatch.setattr(log_pipeline, "observability", pipeline)
    monkeypatch.setattr(log_pipeline, "split_enabled", lambda: True)

    for index in range(3):
        await _write(app, f"req-bill-{index}", ttft_ms=index + 1)

    assert pipeline.stats()["dropped"] == 1
    assert await ledger.ledger_service.flush_once() == 3

    rows = await _ledger_rows(app)
    assert sorted(row.request_id for row in rows) == [
        "req-bill-0",
        "req-bill-1",
        "req-bill-2",
    ]
    assert [row.prompt_tokens for row in rows] == [10, 10, 10]


async def test_live_writer_wal_is_never_replayed_or_deleted(client, app, wal_root) -> None:
    directory = wal_root / "live-wal"
    writer = WalWriter(directory, flush_ms=10_000)
    writer.append(ledger_repo.ledger_row(request_id="req-live"))
    writer.flush()
    path = Path(writer.path)
    writer.close()

    assert wal_module._owner_pid(path.name) == os.getpid()
    assert wal_module._owner_alive(os.getpid()) is True
    _age(path, 3600)  # quiet long enough to be swept if the owner check were gone

    sink = SqliteLedgerSink(app.state.session_factory)
    assert await replay_pending(directory, sink) == 0
    assert path.exists()
    assert await _ledger_rows(app) == []


async def test_dead_writer_wal_is_replayed_once_then_truncated(client, app, wal_root) -> None:
    directory = wal_root / "dead-wal"
    writer = WalWriter(directory, flush_ms=10_000)
    for index in range(3):
        writer.append(
            ledger_repo.ledger_row(
                request_id=f"req-dead-{index}", prompt_tokens=index + 1
            )
        )
    writer.flush()
    path = Path(writer.path)
    writer.close()
    path = _reassign_owner(path, DEAD_PID)

    assert wal_module._owner_alive(DEAD_PID) is False
    sink = SqliteLedgerSink(app.state.session_factory)
    assert await replay_pending(directory, sink) == 3
    assert path.read_text(encoding="utf-8") == ""
    assert await replay_pending(directory, sink) == 0
    rows = await _ledger_rows(app)
    assert sorted(row.request_id for row in rows) == [
        "req-dead-0",
        "req-dead-1",
        "req-dead-2",
    ]


async def test_wal_without_owner_pid_is_replayed_and_kept(client, app, wal_root) -> None:
    directory = wal_root / "unknown-wal"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "wal-imported.jsonl"
    row = ledger_repo.ledger_row(request_id="req-unknown")
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    _age(path, 3600)

    assert wal_module._owner_pid(path.name) is None
    sink = SqliteLedgerSink(app.state.session_factory)
    assert await replay_pending(directory, sink) == 1
    assert path.exists()
    assert [row.request_id for row in await _ledger_rows(app)] == ["req-unknown"]

