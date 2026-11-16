from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from l01_core import runtime
from l04_repo import orm
from l05_service import log_pipeline
from l05_service.chat import _write_log
from l05_service.llm import Usage


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


async def _row(app, request_id: str) -> orm.RequestLogORM | None:
    async with app.state.session_factory() as session:
        return (
            await session.execute(
                select(orm.RequestLogORM).where(
                    orm.RequestLogORM.request_id == request_id
                )
            )
        ).scalar_one_or_none()


def _pipeline(app, **kwargs) -> log_pipeline.ObservabilityPipeline:
    return log_pipeline.ObservabilityPipeline(
        session_factory=app.state.session_factory, autostart=False, **kwargs
    )


@pytest.fixture
def split_on(app, client, monkeypatch):
    """The split path with a hand-driven queue, so nothing races the test."""
    pipeline = _pipeline(app)
    monkeypatch.setattr(log_pipeline, "observability", pipeline)
    monkeypatch.setattr(log_pipeline, "split_enabled", lambda: True)
    return pipeline


async def test_billing_row_lands_before_diagnostics(app, split_on) -> None:
    await _write(app, "req-split-1")

    row = await _row(app, "req-split-1")
    assert (row.prompt_tokens, row.completion_tokens) == (10, 5)
    assert row.cost_usd == 0.0
    assert row.attempts_json == "[]"
    assert row.ttft_ms is None
    assert split_on.stats()["queued"] == 1

    assert await split_on.flush() == 1

    row = await _row(app, "req-split-1")
    assert (row.ttft_ms, row.total_ms) == (7, 42)
    assert json.loads(row.attempts_json)[0]["channel_slug"] == "channel-a"
    assert row.api_key_label == "ci"


async def test_full_queue_drops_diagnostics_not_billing(client, app, monkeypatch) -> None:
    pipeline = _pipeline(app, maxsize=2)
    monkeypatch.setattr(log_pipeline, "observability", pipeline)
    monkeypatch.setattr(log_pipeline, "split_enabled", lambda: True)

    for index in range(3):
        await _write(app, f"req-drop-{index}", ttft_ms=index + 1)

    assert pipeline.stats() == {
        "queued": 2,
        "written": 0,
        "dropped": 1,
        "reinserted": 0,
        "failed_batches": 0,
    }
    assert await pipeline.flush() == 2

    async with app.state.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(orm.RequestLogORM).order_by(orm.RequestLogORM.request_id)
                )
            )
            .scalars()
            .all()
        )
    # Every request kept its row and its money; only the third lost its trail.
    assert [row.request_id for row in rows] == [
        "req-drop-0",
        "req-drop-1",
        "req-drop-2",
    ]
    assert [row.prompt_tokens for row in rows] == [10, 10, 10]
    assert [row.ttft_ms for row in rows] == [1, 2, None]


async def test_purged_row_is_rebuilt_with_diagnostics(app, split_on) -> None:
    await _write(app, "req-purged")
    async with app.state.session_factory() as session:
        await session.execute(
            delete(orm.RequestLogORM).where(
                orm.RequestLogORM.request_id == "req-purged"
            )
        )
        await session.commit()

    assert await split_on.flush() == 1

    row = await _row(app, "req-purged")
    assert row is not None
    assert row.ttft_ms == 7
    assert json.loads(row.attempts_json)[0]["channel_slug"] == "channel-a"
    assert split_on.stats()["reinserted"] == 1


async def test_split_off_writes_one_complete_row(client, app, monkeypatch) -> None:
    monkeypatch.setattr(log_pipeline, "observability", _pipeline(app))
    runtime.bind_settings(app.state.settings.model_copy(update={"ledger_split": "off"}))
    try:
        assert log_pipeline.split_enabled() is False
        await _write(app, "req-off")
    finally:
        runtime.bind_settings(app.state.settings)

    row = await _row(app, "req-off")
    assert row.ttft_ms == 7
    assert json.loads(row.attempts_json)[0]["channel_slug"] == "channel-a"


async def test_log_read_flushes_pending_diagnostics(client, app, split_on) -> None:
    await _write(app, "req-read")
    assert split_on.stats()["queued"] == 1

    login = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.get(
        "/api/logs/summary",
        headers=headers,
        params={"start_ms": 0, "end_ms": 4_000_000_000_000},
    )

    assert response.status_code == 200, response.text
    assert split_on.stats()["queued"] == 0
    row = await _row(app, "req-read")
    assert row.ttft_ms == 7
