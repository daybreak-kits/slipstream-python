"""Ledger read side: billing overlay, reconcile repair, coverage self check."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from l01_core.config import Settings
from l01_core.timeutil import now_ms
from l04_repo import ledger_repo, orm, repo
from l05_service import aggregates, ledger_read
from l05_service.ledger import reconcile

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"


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
    """读侧 overlay 只在 split 打开时存在：产品默认是 off，这里显式开(on)。"""
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        ledger_split="on",
        ledger_wal_dir=str(wal_root / "service"),
    )


@pytest.fixture
def report_dir() -> Iterator[Path]:
    """Report scratch dir inside the repo (the sandbox blocks new temp dirs)."""
    path = SCRATCH_ROOT / f"report-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


async def _log(app, request_id: str, **values) -> None:
    payload = dict(
        request_id=request_id,
        requested_model="model-a",
        prompt_tokens=100,
        completion_tokens=10,
        cached_tokens=0,
        cost_usd=1.0,
        created_at=now_ms(),
    )
    payload.update(values)
    async with app.state.session_factory() as session:
        await repo.add_request_log(session, **payload)


async def _bill(app, request_id: str, **values) -> None:
    payload = dict(
        request_id=request_id,
        prompt_tokens=100,
        completion_tokens=10,
        cached_tokens=0,
        cost_usd=1.0,
        created_at=now_ms(),
    )
    payload.update(values)
    async with app.state.session_factory() as session:
        await ledger_repo.insert_ledger_rows(
            session, [ledger_repo.ledger_row(**payload)]
        )
        await session.commit()


async def _summary(app, *, start_ms: int = 0, end_ms: int | None = None) -> dict:
    async with app.state.session_factory() as session:
        return await aggregates.compute_summary(
            session, start_ms, end_ms if end_ms is not None else now_ms() + 60_000
        )


async def test_summary_uses_ledger_billing_and_adjustments(client, app) -> None:
    await _log(app, "req-1")
    await _bill(app, "req-1", prompt_tokens=120, completion_tokens=12, cost_usd=1.5)
    async with app.state.session_factory() as session:
        await ledger_repo.add_ledger_adjustment(
            session,
            ledger_repo.adjustment_row(
                request_id="req-1", delta_prompt_tokens=5, delta_cost_usd=0.25, reason="manual"
            ),
        )
    summary = await _summary(app)
    assert summary["requests"] == 1
    assert summary["prompt_tokens"] == 125
    assert summary["completion_tokens"] == 12
    assert summary["cost_usd"] == 1.75


async def test_summary_falls_back_to_request_logs_when_split_off(client, app, monkeypatch) -> None:
    await _log(app, "req-1")
    await _bill(app, "req-1", prompt_tokens=120, cost_usd=1.5)
    assert (await _summary(app))["prompt_tokens"] == 120
    monkeypatch.setattr(ledger_read, "overlay_enabled", lambda: False)
    summary = await _summary(app)
    assert summary["prompt_tokens"] == 100
    assert summary["cost_usd"] == 1.0


async def test_rows_without_ledger_keep_request_logs_billing(client, app) -> None:
    await _log(app, "req-1", prompt_tokens=10, cost_usd=0.1)
    await _log(app, "req-2", prompt_tokens=20, cost_usd=0.2)
    await _bill(app, "req-2", prompt_tokens=200, cost_usd=2.0)
    summary = await _summary(app)
    assert summary["requests"] == 2
    assert summary["prompt_tokens"] == 210
    assert summary["cost_usd"] == 2.1


async def test_compare_report_flags_three_kinds_and_repairs(client, app) -> None:
    await _log(app, "req-1")
    await _bill(app, "req-1")
    await _log(app, "req-2")
    await _log(app, "req-3", prompt_tokens=90)
    await _bill(app, "req-3", prompt_tokens=90)
    await _bill(app, "req-4", prompt_tokens=50, cost_usd=0.5)
    report = {
        "req-1": {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 0, "cost_usd": 1.0},
        "req-2": {"prompt_tokens": 200, "completion_tokens": 20, "cached_tokens": 0, "cost_usd": 2.0},
        "req-3": {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 0, "cost_usd": 1.0},
    }
    async with app.state.session_factory() as session:
        differences = await reconcile.compare_report(session, report)
    kinds = {(d.request_id, d.kind, d.field) for d in differences}
    assert kinds == {
        ("req-2", "missing_in_ledger", "prompt_tokens"),
        ("req-2", "missing_in_ledger", "completion_tokens"),
        ("req-2", "missing_in_ledger", "cost_usd"),
        ("req-3", "usage_mismatch", "prompt_tokens"),
        ("req-4", "missing_in_report", "prompt_tokens"),
        ("req-4", "missing_in_report", "completion_tokens"),
        ("req-4", "missing_in_report", "cost_usd"),
    }
    async with app.state.session_factory() as session:
        repaired = await reconcile.apply_differences(session, differences, actor="tester")
    assert repaired == 3
    async with app.state.session_factory() as session:
        remaining = await reconcile.compare_report(session, report)
        usage = await ledger_repo.ledger_usage_by_request(
            session, ["req-1", "req-2", "req-3", "req-4"]
        )
        totals = await ledger_repo.ledger_totals(session)
    assert remaining == []
    assert usage["req-2"]["prompt_tokens"] == 200
    assert usage["req-2"]["has_ledger_row"] is True
    assert usage["req-3"]["prompt_tokens"] == 100
    assert usage["req-4"]["prompt_tokens"] == 0
    assert totals["prompt_tokens"] == 400
    assert totals["cost_usd"] == 4.0
    async with app.state.session_factory() as session:
        adjustments = (await session.execute(select_adjustments())).scalars().all()
    assert [row.reason for row in adjustments] == ["reconcile", "reconcile"]
    assert {row.actor for row in adjustments} == {"tester"}


def select_adjustments():
    from sqlalchemy import select

    return select(orm.LedgerAdjustmentORM)


async def test_self_check_reports_coverage_until_balanced(client, app) -> None:
    for request_id in ("req-1", "req-2", "req-3"):
        await _log(app, request_id)
    await _bill(app, "req-1")
    await _bill(app, "req-2")
    async with app.state.session_factory() as session:
        check = await reconcile.self_check(session)
    assert check["requests"] == {"request_logs": 3, "request_ledger": 2}
    assert check["coverage"] == 0.666667
    assert check["missing_in_ledger"] == ["req-3"]
    assert check["ledger_only"] == []
    assert check["balanced"] is False
    await _bill(app, "req-3")
    async with app.state.session_factory() as session:
        check = await reconcile.self_check(session)
    assert check["coverage"] == 1.0
    assert check["balanced"] is True
    assert check["totals"]["cost_usd"] == {"request_logs": 3.0, "request_ledger": 3.0}


async def test_load_report_reads_csv_with_bom_and_json(report_dir: Path) -> None:
    csv_path = report_dir / "usage.csv"
    csv_path.write_text(
        "request_id,prompt_tokens,completion_tokens,cached_tokens,cost_usd\n"
        "req-1,10,5,0,0.02\n"
        "req-2,3,,,0.5\n",
        encoding="utf-8-sig",
    )
    json_path = report_dir / "usage.json"
    json_path.write_text(
        '{"rows": [{"request_id": "req-3", "prompt_tokens": 7, "cost_usd": 0.125}]}',
        encoding="utf-8",
    )
    assert reconcile.load_report(csv_path)["req-1"] == {
        "prompt_tokens": 10.0,
        "completion_tokens": 5.0,
        "cached_tokens": 0.0,
        "cost_usd": 0.02,
    }
    assert reconcile.load_report(csv_path)["req-2"]["completion_tokens"] == 0.0
    assert reconcile.load_report(json_path)["req-3"] == {
        "prompt_tokens": 7.0,
        "completion_tokens": 0.0,
        "cached_tokens": 0.0,
        "cost_usd": 0.125,
    }
    bad = report_dir / "bad.csv"
    bad.write_text("prompt_tokens,cost_usd\n1,0.1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        reconcile.load_report(bad)