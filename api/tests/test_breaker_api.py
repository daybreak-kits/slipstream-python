from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, text, update

from l01_core.config import Settings
from l01_core.timeutil import now_ms
from l04_repo import orm
from l05_service.routing import Candidate, admit_candidate, record_failure
from l07_bootstrap.app_factory import create_app
from tests.fake_upstream_server import FakeUpstreamServer


class _StatementCounter:
    """Record the SQL statements one engine runs inside the block."""

    def __init__(self, app: Any) -> None:
        self._engine = app.state.db_engine.sync_engine
        self.statements: list[str] = []

    def _before_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(" ".join(str(statement).split()))

    def __enter__(self) -> "_StatementCounter":
        event.listen(self._engine, "before_cursor_execute", self._before_execute)
        return self

    def __exit__(self, *exc_info: object) -> bool:
        event.remove(self._engine, "before_cursor_execute", self._before_execute)
        return False

    @property
    def updates(self) -> list[str]:
        return [s for s in self.statements if s.startswith("UPDATE")]


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(
    client, headers, url: str, *, name: str, slug: str
) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "slug": slug,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(
    client,
    headers,
    channel_id: str,
    *,
    upstream_model: str = "fake-model",
    call_model: str = "breaker-leaf",
    cooldown: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "upstream_model": upstream_model,
        "call_model": call_model,
        "enabled": True,
    }
    if cooldown is not None:
        payload["cooldown"] = cooldown
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> str:
    response = await client.post("/api/keys", headers=headers, json={"label": "breaker"})
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _chat(key: str, model: str) -> dict[str, Any]:
    return {
        "headers": {"Authorization": f"Bearer {key}"},
        "json": {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
        },
    }


async def _patch_model(app, model_id: str, **values: Any) -> None:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        for key, value in values.items():
            setattr(model, key, value)
        await session.commit()


async def _stored(app, model_id: str) -> dict[str, Any]:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        return {
            "health_status": model.health_status,
            "consecutive_failures": int(model.consecutive_failures or 0),
            "consecutive_trip_count": int(model.consecutive_trip_count or 0),
            "escalation_trip_count": int(model.escalation_trip_count or 0),
            "cooldown_until": model.cooldown_until,
            "fault_until": model.fault_until,
            "half_open_since": model.half_open_since,
        }


async def _api_model(client, headers, channel_id: str, model_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/channels/{channel_id}/models", headers=headers)
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["id"] == model_id:
            return item
    raise AssertionError("model not listed")


async def test_failure_threshold_opens_circuit_and_next_request_is_skipped(
    client, upstream, app
):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/broken", name="坏渠道", slug="brk-open"
    )
    model = await _model(
        client,
        headers,
        channel["id"],
        call_model="open-leaf",
        cooldown={"failure_threshold": 2, "cooldown_s": 60},
    )
    key = await _key(client, headers)
    direct = "brk-open/open-leaf"

    first = await client.post("/v1/chat/completions", **_chat(key, direct))
    assert first.status_code == 502, first.text
    assert (await _stored(app, model["id"]))["health_status"] == "normal"

    second = await client.post("/v1/chat/completions", **_chat(key, direct))
    assert second.status_code == 502, second.text
    opened = await _stored(app, model["id"])
    assert opened["health_status"] == "open"
    assert opened["consecutive_failures"] == 2
    assert opened["consecutive_trip_count"] == 1
    assert opened["escalation_trip_count"] == 1
    assert opened["cooldown_until"] is not None
    assert opened["cooldown_until"] > now_ms()

    posts_before = len(upstream.find("POST", "/chat/completions"))
    third = await client.post("/v1/chat/completions", **_chat(key, direct))
    assert third.status_code == 503, third.text
    assert third.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"
    assert len(upstream.find("POST", "/chat/completions")) == posts_before

    listed = await _api_model(client, headers, channel["id"], model["id"])
    assert listed["visible_status"] == "open"
    assert listed["health_status"] == "open"


async def test_half_open_success_clears_failure_and_trip_but_only_decrements_escalation(
    client, upstream, app
):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="恢复渠道", slug="brk-recover"
    )
    model = await _model(client, headers, channel["id"], call_model="recover-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=2,
        opened_at=now_ms() - 60_000,
        cooldown_until=now_ms() - 1_000,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-recover/recover-leaf")
    )
    assert response.status_code == 200, response.text

    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0
    assert stored["consecutive_trip_count"] == 0
    # Half-open success decrements escalation instead of clearing it.
    assert stored["escalation_trip_count"] == 1
    assert stored["cooldown_until"] is None


async def test_half_open_failure_reopens_circuit(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/broken", name="半开坏", slug="brk-halfbad"
    )
    model = await _model(client, headers, channel["id"], call_model="halfbad-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=1,
        opened_at=now_ms() - 60_000,
        cooldown_until=now_ms() - 1_000,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-halfbad/halfbad-leaf")
    )
    assert response.status_code == 502, response.text

    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "open"
    assert stored["consecutive_failures"] == 6
    assert stored["consecutive_trip_count"] == 2
    assert stored["escalation_trip_count"] == 2
    assert stored["cooldown_until"] is not None and stored["cooldown_until"] > now_ms()

    skipped = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-halfbad/halfbad-leaf")
    )
    assert skipped.status_code == 503
    assert skipped.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"


async def test_only_one_half_open_probe_is_admitted_at_a_time(
    client, upstream, app
):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/slow", name="慢渠道", slug="brk-one"
    )
    model = await _model(client, headers, channel["id"], call_model="one-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=1,
        cooldown_until=now_ms() - 1_000,
    )
    key = await _key(client, headers)

    first = asyncio.create_task(
        client.post("/v1/chat/completions", **_chat(key, "brk-one/one-leaf"))
    )
    await asyncio.sleep(0.4)
    assert (await _stored(app, model["id"]))["health_status"] == "half_open"

    second = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-one/one-leaf")
    )
    assert second.status_code == 503, second.text
    assert second.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"

    first_response = await first
    assert first_response.status_code == 200, first_response.text
    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["escalation_trip_count"] == 0


async def test_half_open_probe_claim_is_atomic_across_sessions(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="原子渠道", slug="brk-atomic"
    )
    model = await _model(client, headers, channel["id"], call_model="atomic-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="open",
        cooldown_until=now_ms() - 1_000,
    )

    async with app.state.session_factory() as first_session:
        first = Candidate(
            channel=await first_session.get(orm.ChannelORM, channel["id"]),
            model=await first_session.get(orm.ChannelModelORM, model["id"]),
        )
        async with app.state.session_factory() as second_session:
            second = Candidate(
                channel=await second_session.get(orm.ChannelORM, channel["id"]),
                model=await second_session.get(orm.ChannelModelORM, model["id"]),
            )
            first_result, second_result = await asyncio.gather(
                admit_candidate(first_session, first),
                admit_candidate(second_session, second),
            )

    assert sorted(
        [reason for reason in (first_result, second_result) if reason is None]
    ) == [None]
    assert {first_result, second_result} == {None, "CIRCUIT_OPEN"}
    assert (await _stored(app, model["id"]))["health_status"] == "half_open"


async def test_admit_candidate_releases_sqlite_write_lock(
    client, upstream, app
):
    """A no-op admission must not keep a write transaction during streaming."""
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="锁渠道", slug="brk-lock"
    )
    model = await _model(client, headers, channel["id"], call_model="lock-leaf")

    async with app.state.session_factory() as first_session:
        first = Candidate(
            channel=await first_session.get(orm.ChannelORM, channel["id"]),
            model=await first_session.get(orm.ChannelModelORM, model["id"]),
        )
        assert await admit_candidate(first_session, first) is None

        async with app.state.session_factory() as second_session:
            await second_session.execute(text("PRAGMA busy_timeout=100"))
            await second_session.execute(
                update(orm.ChannelModelORM)
                .where(orm.ChannelModelORM.id == model["id"])
                .values(probe_status="ok")
            )
            await second_session.commit()

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, model["id"])
        assert stored is not None
        assert stored.probe_status == "ok"


async def test_escalate_after_turns_fault_and_ui_shows_fault(
    client, upstream, app
):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/broken", name="升级渠道", slug="brk-fault"
    )
    model = await _model(
        client,
        headers,
        channel["id"],
        call_model="fault-leaf",
        cooldown={
            "failure_threshold": 1,
            "cooldown_s": 60,
            "escalate_mode": "after_n",
            "escalate_after": 2,
            "fault_cooldown_s": 600,
        },
    )
    key = await _key(client, headers)
    direct = "brk-fault/fault-leaf"

    first = await client.post("/v1/chat/completions", **_chat(key, direct))
    assert first.status_code == 502
    assert (await _stored(app, model["id"]))["health_status"] == "open"

    await _patch_model(app, model["id"], cooldown_until=now_ms() - 1_000)
    second = await client.post("/v1/chat/completions", **_chat(key, direct))
    assert second.status_code == 502

    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "fault"
    assert stored["consecutive_trip_count"] == 2
    assert stored["escalation_trip_count"] == 2
    assert stored["fault_until"] is not None and stored["fault_until"] > now_ms()

    listed = await _api_model(client, headers, channel["id"], model["id"])
    assert listed["visible_status"] == "fault"

    third = await client.post("/v1/chat/completions", **_chat(key, direct))
    assert third.status_code == 503
    assert third.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"


async def test_fault_auto_recovers_on_the_next_request(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="自愈渠道", slug="brk-heal"
    )
    model = await _model(client, headers, channel["id"], call_model="heal-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="fault",
        consecutive_failures=9,
        consecutive_trip_count=3,
        escalation_trip_count=3,
        fault_until=now_ms() - 1_000,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-heal/heal-leaf")
    )
    assert response.status_code == 200, response.text

    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0
    assert stored["consecutive_trip_count"] == 0
    assert stored["escalation_trip_count"] == 0
    assert stored["fault_until"] is None


async def test_manual_reset_clears_fault_and_counters(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="重置渠道", slug="brk-reset"
    )
    model = await _model(client, headers, channel["id"], call_model="reset-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="fault",
        consecutive_failures=7,
        consecutive_trip_count=3,
        escalation_trip_count=3,
        fault_until=now_ms() + 600_000,
    )

    reset = await client.post(
        f"/api/channel-models/{model['id']}/reset", headers=headers
    )
    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert body["health_status"] == "normal"
    assert body["visible_status"] == "normal"
    assert body["consecutive_failures"] == 0
    assert body["consecutive_trip_count"] == 0
    assert body["escalation_trip_count"] == 0
    assert body["fault_until"] is None
    assert body["cooldown_until"] is None

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-reset/reset-leaf")
    )
    assert response.status_code == 200, response.text


async def test_visible_status_distinguishes_fault_from_disabled(
    client, upstream, app
):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="可见状态", slug="brk-visible"
    )
    model = await _model(client, headers, channel["id"], call_model="visible-leaf")
    await _patch_model(app, model["id"], health_status="fault", fault_until=now_ms() + 60_000)

    faulted = await _api_model(client, headers, channel["id"], model["id"])
    assert faulted["visible_status"] == "fault"
    assert faulted["health_status"] == "fault"

    disabled = await client.patch(
        f"/api/channel-models/{model['id']}", headers=headers, json={"enabled": False}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["visible_status"] == "disabled"
    assert disabled.json()["health_status"] == "fault"

    enabled = await client.patch(
        f"/api/channel-models/{model['id']}", headers=headers, json={"enabled": True}
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["visible_status"] == "fault"


async def test_stale_half_open_probe_is_released_on_next_request(client, upstream, app):
    """A probe lost to a restart/cancellation must not block the model forever."""
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="陈旧半开", slug="brk-stale"
    )
    model = await _model(client, headers, channel["id"], call_model="stale-leaf")
    stale = now_ms() - 3_600_000
    await _patch_model(
        app,
        model["id"],
        health_status="half_open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=2,
        opened_at=stale,
        cooldown_until=stale,
        half_open_since=stale,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-stale/stale-leaf")
    )
    assert response.status_code == 200, response.text

    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0
    assert stored["half_open_since"] is None


async def test_legacy_half_open_row_without_timestamp_is_admitted(client, upstream, app):
    """Rows written before half_open_since existed carry no claim time."""
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="遗留半开", slug="brk-legacy"
    )
    model = await _model(client, headers, channel["id"], call_model="legacy-leaf")
    await _patch_model(
        app,
        model["id"],
        health_status="half_open",
        consecutive_failures=5,
        opened_at=now_ms() - 3_600_000,
        cooldown_until=now_ms() - 3_600_000,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions", **_chat(key, "brk-legacy/legacy-leaf")
    )
    assert response.status_code == 200, response.text
    assert (await _stored(app, model["id"]))["half_open_since"] is None
    assert (await _stored(app, model["id"]))["health_status"] == "normal"


def _chat_stream(key: str, model: str) -> dict[str, Any]:
    return {
        "headers": {"Authorization": f"Bearer {key}"},
        "json": {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    }


def _sse_events(text: str) -> list[tuple[str | None, str]]:
    events: list[tuple[str | None, str]] = []
    for block in text.split("\n\n"):
        name = None
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if data:
            events.append((name, "\n".join(data)))
    return events


async def _stored_until(app, model_id: str, predicate) -> dict[str, Any]:
    """Streaming finalization lands in a background task: poll until it does."""
    stored = await _stored(app, model_id)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if predicate(stored):
            return stored
        await asyncio.sleep(0.05)
        stored = await _stored(app, model_id)
    raise AssertionError("熔断状态未在 3s 内到达预期，最后为 %s" % stored)


async def test_stream_abort_feeds_the_failure_breaker(client, upstream, app):
    """STREAM_ABORTED 是真实到达上游后被掐断：必须计入故障熔断并最终拒答。"""
    headers = await _token(client)
    channel = await _channel(
        client,
        headers,
        upstream.base_url + "/openai-stream-broken",
        name="断流渠道",
        slug="brk-abort",
    )
    model = await _model(
        client,
        headers,
        channel["id"],
        call_model="abort-leaf",
        cooldown={"failure_threshold": 2, "cooldown_s": 60},
    )
    key = await _key(client, headers)

    first = await client.post(
        "/v1/chat/completions", **_chat_stream(key, "brk-abort/abort-leaf")
    )
    assert first.status_code == 200
    error_event = next(data for name, data in _sse_events(first.text) if name == "error")
    assert json.loads(error_event)["error"]["code"] == "STREAM_ABORTED"
    stored = await _stored_until(
        app, model["id"], lambda s: s["consecutive_failures"] == 1
    )
    assert stored["health_status"] == "normal"

    second = await client.post(
        "/v1/chat/completions", **_chat_stream(key, "brk-abort/abort-leaf")
    )
    assert second.status_code == 200
    stored = await _stored_until(
        app,
        model["id"],
        lambda s: s["health_status"] == "open" and s["consecutive_failures"] == 2,
    )
    assert stored["consecutive_trip_count"] == 1
    assert stored["cooldown_until"] is not None
    assert stored["cooldown_until"] > now_ms()

    # 被熔断跳过的请求不再打上游，也不继续抬升失败计数。
    posts_before = len(upstream.find("POST", "/chat/completions"))
    third = await client.post(
        "/v1/chat/completions", **_chat_stream(key, "brk-abort/abort-leaf")
    )
    assert third.status_code == 503
    assert third.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"
    assert len(upstream.find("POST", "/chat/completions")) == posts_before
    assert (await _stored(app, model["id"]))["consecutive_failures"] == 2

    listed = await _api_model(client, headers, channel["id"], model["id"])
    assert listed["visible_status"] == "open"


async def test_record_failure_skips_the_write_when_state_does_not_move(
    client, upstream, app
):
    """open 状态下迟到的失败不迁移状态：不写库，只同步缓存行。

    on_failure 对已 open 的行有「不让迟到失败移动时钟」的守卫；这类调用
    的状态逐字段相同，不产生 UPDATE 也不提交事务（P1-R4）。
    """
    headers = await _token(client)
    channel = await _channel(
        client,
        headers,
        upstream.base_url + "/broken",
        name="迟到失败渠道",
        slug="brk-late",
    )
    model = await _model(client, headers, channel["id"], call_model="late-leaf")
    now = now_ms()
    # 前一次失败已把熔断打开：把行直接置成 open，冷却仍在未来。
    await _patch_model(
        app,
        model["id"],
        health_status="open",
        consecutive_failures=1,
        consecutive_trip_count=1,
        escalation_trip_count=1,
        opened_at=now,
        cooldown_until=now + 600_000,
        half_open_since=None,
    )

    factory = app.state.session_factory
    async with factory() as session:
        channel_row = await session.get(orm.ChannelORM, channel["id"])
        model_row = await session.get(orm.ChannelModelORM, model["id"])
        assert channel_row is not None and model_row is not None
        candidate = Candidate(channel=channel_row, model=model_row)
        with _StatementCounter(app) as counter:
            await record_failure(session, candidate, at_ms=now + 1000)
        # 读事务仍在（没有 commit），也没有任何 UPDATE 语句。
        assert session.in_transaction(), "状态未迁移的失败不应提交事务"
        assert counter.updates == [], counter.statements

    # 库里的行原样保留：迟到的失败不移动时钟。
    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "open"
    assert stored["cooldown_until"] == now + 600_000
    assert stored["consecutive_failures"] == 1
    assert stored["consecutive_trip_count"] == 1
    assert stored["half_open_since"] is None


@pytest_asyncio.fixture
async def memory_app(database_url, tmp_path) -> Iterator[Any]:
    """App wired for the zero-SQL pipeline, which serves admission from the
    in-process catalog snapshot instead of reading the row per request."""
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        models_dev_enabled=False,
        usage_pipeline="memory",
        usage_wal_dir=str(tmp_path / "usage"),
        # Keep the background sink out of the assertions.
        usage_sink_interval_ms=60_000,
    )
    app = create_app(settings=settings, database_url=database_url)
    async with app.router.lifespan_context(app):
        yield app


async def test_stream_abort_feeds_the_breaker_in_the_memory_pipeline(
    memory_app, upstream
):
    """Same guarantee as the classic case, but the breaker row has to travel
    back into the cached snapshot.

    A streaming request reports its outcome from the finalizer, which opens its
    own session. That session never passes through ``get_db``, so it carries no
    catalog pointer; before the engine-binding fallback in
    ``config_catalog.catalog_for_session`` the write-back silently did nothing
    and admission kept reading the cached row as healthy -- every request after
    the trip was routed upstream again. Classic mode missed this because it
    re-reads the row per request.
    """
    assert memory_app.state.settings.zero_sql_pipeline is True
    transport = httpx.ASGITransport(app=memory_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        headers = await _token(client)
        channel = await _channel(
            client,
            headers,
            upstream.base_url + "/openai-stream-broken",
            name="断流渠道",
            slug="brk-abort-mem",
        )
        model = await _model(
            client,
            headers,
            channel["id"],
            call_model="abort-leaf",
            cooldown={"failure_threshold": 2, "cooldown_s": 60},
        )
        key = await _key(client, headers)

        for _ in range(2):
            response = await client.post(
                "/v1/chat/completions",
                **_chat_stream(key, "brk-abort-mem/abort-leaf"),
            )
            assert response.status_code == 200
            assert (
                json.loads(
                    next(
                        data
                        for name, data in _sse_events(response.text)
                        if name == "error"
                    )
                )["error"]["code"]
                == "STREAM_ABORTED"
            )
        stored = await _stored_until(
            memory_app,
            model["id"],
            lambda s: s["health_status"] == "open" and s["consecutive_failures"] == 2,
        )
        assert stored["cooldown_until"] > now_ms()

        # The cached snapshot must already reflect the trip: this request is
        # admitted from memory, so a stale row here means it reaches upstream.
        posts_before = len(upstream.find("POST", "/chat/completions"))
        third = await client.post(
            "/v1/chat/completions",
            **_chat_stream(key, "brk-abort-mem/abort-leaf"),
        )
        assert third.status_code == 503
        assert third.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"
        assert len(upstream.find("POST", "/chat/completions")) == posts_before
