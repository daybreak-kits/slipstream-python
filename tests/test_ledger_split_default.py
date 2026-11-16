"""LEDGER_SPLIT 的产品默认值：off（单写路径），env 仍可显式打开 split。

默认值改成 off 之后，三件事必须同时成立：
1. 默认构造出的 Settings.ledger_split == "off"，三处开关（split / ledger / overlay）都为 False；
2. off 时请求日志一次写完：billing 与 diagnostics 同在 request_logs 一行，request_ledger 零行，
   无 WAL、无后台 sink，诊断列也不再进观测队列；
3. LEDGER_SPLIT=on 仍能把 split 路径打开（回退开关保留）。
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from l01_core import runtime
from l01_core.config import Settings
from l04_repo import orm
from l05_service import ledger, ledger_read, log_pipeline
from l05_service.chat import _write_log
from l05_service.llm import Usage

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"


@pytest.fixture
def wal_root() -> Iterator[Path]:
    """WAL scratch dir inside the repo (the sandbox blocks fresh temp dirs)."""
    path = SCRATCH_ROOT / f"ledger-default-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def test_settings(database_url: str, wal_root: Path, monkeypatch) -> Settings:
    """不传 ledger_split：用产品默认值本身（WAL 目录指向仓库内临时目录）。"""
    monkeypatch.delenv("LEDGER_SPLIT", raising=False)
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        ledger_wal_dir=str(wal_root / "service"),
    )


@pytest.fixture(autouse=True)
def _isolated_ledger_service_and_runtime():
    """账本服务与 runtime 绑定都是全局状态：用例前后清干净。"""
    ledger.ledger_service.reset()
    yield
    ledger.ledger_service.reset()
    runtime.reset_settings()


async def _write(app, request_id: str, **overrides) -> None:
    values = {
        "request_id": request_id,
        "requested_model": "model-a",
        "api_key": SimpleNamespace(id="key-1", label="ci"),
        "user": SimpleNamespace(id="user-1"),
        "target": None,
        "http_status": 200,
        "usage": Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        "total_ms": 42,
        "attempts": [{"channel_slug": "channel-a", "status": "success"}],
        "ttft_ms": 7,
        "session_factory": app.state.session_factory,
    }
    values.update(overrides)
    async with app.state.session_factory() as session:
        await _write_log(session, **values)


async def test_default_settings_keep_every_split_switch_off(monkeypatch) -> None:
    """产品默认：ledger_split == "off"，split / ledger / overlay 三个开关全 False。"""
    monkeypatch.delenv("LEDGER_SPLIT", raising=False)
    settings = Settings(_env_file=None)

    assert settings.ledger_split == "off"

    runtime.bind_settings(settings)
    try:
        assert log_pipeline.split_enabled() is False
        assert ledger_read.overlay_enabled() is False
        assert ledger.ledger_service.enabled() is False
    finally:
        runtime.reset_settings()


async def test_env_override_re_enables_the_split_path(monkeypatch) -> None:
    """回退开关保留：LEDGER_SPLIT=on 打开 split 路径。"""
    monkeypatch.setenv("LEDGER_SPLIT", "on")
    settings = Settings(_env_file=None)

    assert settings.ledger_split == "on"

    runtime.bind_settings(settings)
    try:
        assert log_pipeline.split_enabled() is True
        assert ledger_read.overlay_enabled() is True
        assert ledger.ledger_service.enabled() is True
    finally:
        runtime.reset_settings()


async def test_off_path_writes_one_request_log_row_and_no_ledger_row(
    client, app, wal_root
) -> None:
    """默认 off：request_logs 恰好一行，request_ledger 零行，无 WAL、无后台 sink。"""
    assert app.state.settings.ledger_split == "off"

    await _write(app, "req-default-off")

    async with app.state.session_factory() as session:
        log_rows = list(
            (await session.execute(select(orm.RequestLogORM))).scalars().all()
        )
        ledger_count = await session.execute(
            select(func.count()).select_from(orm.RequestLedgerORM)
        )

    assert len(log_rows) == 1
    assert log_rows[0].request_id == "req-default-off"
    assert ledger_count.scalar_one() == 0

    stats = ledger.ledger_service.stats()
    assert stats["enabled"] is False
    assert stats["written"] == 0
    assert stats["pending"] == 0
    assert ledger.ledger_service._wal is None
    assert list(wal_root.rglob("*")) == []


async def test_off_path_row_carries_billing_and_diagnostics(client, app) -> None:
    """默认 off：billing 与 diagnostics 在同一行（不靠后台 UPDATE 补齐）。"""
    await _write(app, "req-off-columns")

    async with app.state.session_factory() as session:
        row = (
            await session.execute(
                select(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == "req-off-columns"
                )
            )
        ).scalar_one()

    assert row.prompt_tokens == 10
    assert row.completion_tokens == 5
    assert row.cached_tokens == 0
    assert row.http_status == 200
    assert row.usage_estimated is False

    assert row.ttft_ms == 7
    assert row.total_ms == 42
    assert row.api_key_label == "ci"
    assert json.loads(row.attempts_json)[0]["channel_slug"] == "channel-a"


async def test_off_path_never_queues_diagnostics(client, app, monkeypatch) -> None:
    """默认 off：诊断列随同一行写入，不再进后台观测队列。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        log_pipeline,
        "submit_observability",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    await _write(app, "req-off-no-queue")

    assert calls == []
