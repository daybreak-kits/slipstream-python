from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from l01_core.config import Settings
from l03_domain.failover import FailoverRules, choose_action
from l04_repo import orm
from l05_service import routing
from l05_service.chat import StreamRun
from l05_service.llm import (
    TIER_CLIENT,
    TIER_PERMANENT,
    TIER_TRANSIENT,
    LLMError,
    gateway,
)
from l05_service.llm.gateway import classify_http_error, parse_retry_after_ms
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        upstream_transport="httpx",
    )


@pytest.fixture(autouse=True)
def _httpx_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the httpx transport: the classification under test lives there."""
    monkeypatch.setattr(gateway, "_use_litellm", lambda *args, **kwargs: False)


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def _channel(
    client, headers, url: str, *, name: str, priority: int = 1
) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "priority": priority,
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
    call_model: str,
    priority: int = 1,
    cooldown: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "upstream_model": "fake-model",
        "call_model": call_model,
        "priority": priority,
        "enabled": True,
    }
    if cooldown is not None:
        payload["cooldown"] = cooldown
    response = await client.post(
        "/api/channels/" + channel_id + "/models",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _api_key(client, headers, label: str = "taxonomy") -> str:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _chat_body(model: str, stream: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if stream:
        body["stream"] = True
    return body


def _post_chat(client, key: str, body: dict[str, Any]):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key},
        json=body,
    )


def _upstream_hits(upstream: FakeUpstreamServer, prefix: str) -> int:
    return sum(
        1
        for request in upstream.requests
        if request["method"] == "POST" and request["path"].startswith(prefix)
    )


# classification
# ---------------------------------------------------------------------------


def test_classify_permanent_statuses() -> None:
    auth = classify_http_error(401, '{"error":{"message":"invalid api key"}}')
    assert (auth.kind, auth.tier, auth.retryable) == ("auth", TIER_PERMANENT, False)

    forbidden = classify_http_error(403, '{"error":{"message":"forbidden"}}')
    assert (forbidden.tier, forbidden.retryable) == (TIER_PERMANENT, False)

    payment = classify_http_error(402, '{"error":{"message":"payment required"}}')
    assert (payment.tier, payment.retryable) == (TIER_PERMANENT, False)

    missing = classify_http_error(404, '{"error":{"message":"no such model"}}')
    assert (missing.kind, missing.tier) == ("not_found", TIER_PERMANENT)


def test_classify_429_splits_quota_from_throttle() -> None:
    quota = classify_http_error(
        429,
        '{"error":{"message":"You exceeded your current quota",'
        '"code":"insufficient_quota"}}',
    )
    assert (quota.kind, quota.tier, quota.retryable) == (
        "rate_limit",
        TIER_PERMANENT,
        False,
    )

    throttled = classify_http_error(
        429, '{"error":{"message":"slow down"}}', {"Retry-After": "5"}
    )
    assert (throttled.tier, throttled.retryable) == (TIER_TRANSIENT, True)
    assert throttled.retry_after_ms == 5000


def test_classify_client_and_server_errors() -> None:
    bad = classify_http_error(400, '{"error":{"message":"bad field"}}')
    assert (bad.kind, bad.tier, bad.retryable) == ("bad_request", TIER_CLIENT, False)

    broken = classify_http_error(503, "upstream down")
    assert (broken.tier, broken.retryable) == (TIER_TRANSIENT, True)


def test_parse_retry_after_seconds_and_dates() -> None:
    assert parse_retry_after_ms({"Retry-After": "5"}) == 5000
    assert parse_retry_after_ms({"retry-after": "0"}) is None
    assert parse_retry_after_ms({"Retry-After": "soon"}) is None
    assert parse_retry_after_ms(None) is None
    assert parse_retry_after_ms({"Retry-After": "999999"}) == 3_600_000

    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=20)
    stamped = parse_retry_after_ms(
        {"Retry-After": when.strftime("%a, %d %b %Y %H:%M:%S GMT")}
    )
    assert stamped is not None and 15_000 <= stamped <= 21_000


def test_choose_action_respects_tier() -> None:
    rules = FailoverRules(rules=(), configured=False, default_action="stop")
    common: dict[str, Any] = {
        "status_code": 401,
        "retryable": True,
        "body": "",
        "rules": rules,
        "is_stream": False,
    }
    assert choose_action(tier=TIER_PERMANENT, **common) == "next"
    assert choose_action(tier=TIER_CLIENT, **common) == "stop"


# end-to-end behaviour through the gateway
# ---------------------------------------------------------------------------


async def test_permanent_auth_rotates_and_parks(client, upstream):
    headers = await _token(client)
    # Implicit-group order comes from channel priority (larger numbers first); equal
    # priorities land in one layer and dispatch becomes a 50/50 shuffle
    # (see FLAKE_AUTH_PARK.md). The dead channel goes first so it is tried once.
    bad = await _channel(
        client,
        headers,
        upstream.base_url + "/unauthorized",
        name="bad",
        priority=2,
    )
    good = await _channel(client, headers, upstream.base_url, name="good", priority=1)
    await _model(client, headers, bad["id"], call_model="tax-shared", priority=1)
    await _model(client, headers, good["id"], call_model="tax-shared", priority=2)
    key = await _api_key(client, headers)

    first = await _post_chat(client, key, _chat_body("tax-shared"))
    assert first.status_code == 200, first.text
    assert _upstream_hits(upstream, "/unauthorized") == 1

    second = await _post_chat(client, key, _chat_body("tax-shared"))
    assert second.status_code == 200, second.text
    # The parked channel is skipped, so the dead key is tried once in total.
    assert _upstream_hits(upstream, "/unauthorized") == 1


async def test_permanent_auth_alone_returns_502(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/unauthorized", name="dead"
    )
    await _model(client, headers, channel["id"], call_model="tax-solo")
    key = await _api_key(client, headers)

    response = await _post_chat(client, key, _chat_body("tax-solo"))
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "UPSTREAM_AUTH_FAILED"
    assert _upstream_hits(upstream, "/unauthorized") == 1


async def test_quota_exhausted_returns_503_with_retry_after(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/quota-exhausted", name="broke"
    )
    await _model(client, headers, channel["id"], call_model="tax-quota")
    key = await _api_key(client, headers)

    response = await _post_chat(client, key, _chat_body("tax-quota"))
    assert response.status_code == 503, response.text
    assert response.headers["retry-after"] == "5"
    error = response.json()["error"]
    assert error["code"] == "UPSTREAM_QUOTA_EXHAUSTED"
    assert error["retry_after_s"] == 5
    assert _upstream_hits(upstream, "/quota-exhausted") == 1


async def test_retry_after_window_has_no_upstream_hits(client, upstream, monkeypatch):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/rate-limited", name="throttled"
    )
    await _model(client, headers, channel["id"], call_model="tax-limited")
    key = await _api_key(client, headers)

    first = await _post_chat(client, key, _chat_body("tax-limited"))
    assert first.status_code == 429, first.text
    assert first.headers["retry-after"] == "5"
    assert _upstream_hits(upstream, "/rate-limited") == 1

    # Inside the Retry-After window the candidate is parked: no upstream call.
    second = await _post_chat(client, key, _chat_body("tax-limited"))
    assert second.status_code == 503, second.text
    assert _upstream_hits(upstream, "/rate-limited") == 1

    # After the window the candidate is probed again.
    later = routing.now_ms() + 6_000
    monkeypatch.setattr(routing, "now_ms", lambda: later)
    third = await _post_chat(client, key, _chat_body("tax-limited"))
    assert third.status_code == 429, third.text
    assert _upstream_hits(upstream, "/rate-limited") == 2


# streaming contracts
# ---------------------------------------------------------------------------


class _FakeUpstreamStream:
    """A minimal upstream stream: one chunk, then a failure or a hang."""

    def __init__(self, error: LLMError | None = None) -> None:
        self.terminal = False
        self.saw_done = False
        self.error_events: list[dict[str, Any]] = []
        self._error = error

    async def iter_sse(self) -> AsyncIterator[str]:
        yield 'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
        if self._error is not None:
            raise self._error
        await asyncio.sleep(30)


def _stream_run(stream: Any) -> StreamRun:
    return StreamRun(
        stream=stream,
        target=SimpleNamespace(),
        session_factory=None,
        request_id="req-taxonomy",
        requested_model="tax-model",
        api_key_id="key-1",
        api_key_label="taxonomy",
        user_id="user-1",
        messages=[{"role": "user", "content": "hello"}],
        started=time.perf_counter(),
    )


def _error_frame(chunks: list[str]) -> dict[str, Any]:
    frame = next(chunk for chunk in chunks if chunk.startswith("event: error"))
    return json.loads(frame.split("data: ", 1)[1].strip())


async def test_stream_pre_first_byte_failure_is_a_json_error(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/unauthorized", name="dead-stream"
    )
    await _model(client, headers, channel["id"], call_model="tax-stream")
    key = await _api_key(client, headers)

    response = await _post_chat(client, key, _chat_body("tax-stream", stream=True))
    assert response.status_code == 502, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "UPSTREAM_AUTH_FAILED"


async def test_stream_mid_abort_yields_error_frame_without_done(monkeypatch):
    monkeypatch.setattr(StreamRun, "_schedule_finalize", lambda self: None)
    error = LLMError(
        "rate_limit",
        "slow down",
        429,
        True,
        tier=TIER_TRANSIENT,
        retry_after_ms=5000,
    )
    run = _stream_run(_FakeUpstreamStream(error))

    chunks = [chunk async for chunk in run.events()]

    assert chunks[0].startswith("data: ")
    assert not any("[DONE]" in chunk for chunk in chunks)
    payload = _error_frame(chunks)
    # The SSE error frame keeps its existing field structure; the retry hint
    # only reaches HTTP error bodies and their Retry-After header.
    assert payload["error"]["code"] == "UPSTREAM_RATE_LIMITED"
    assert set(payload["error"]) == {"message", "type", "code", "request_id"}
    assert run.error is error


async def test_stream_client_disconnect_marks_cancelled(monkeypatch):
    monkeypatch.setattr(StreamRun, "_schedule_finalize", lambda self: None)
    run = _stream_run(_FakeUpstreamStream())

    generator = run.events()
    first = await generator.__anext__()
    assert first.startswith("data: ")
    pending = asyncio.ensure_future(generator.__anext__())
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert run.cancelled is True
    assert run.error is None


async def test_permanent_failures_stop_after_two_candidates(client, upstream):
    headers = await _token(client)
    for index in range(4):
        channel = await _channel(
            client,
            headers,
            upstream.base_url + "/unauthorized",
            name="dead-" + str(index),
        )
        await _model(
            client, headers, channel["id"], call_model="tax-cap", priority=index + 1
        )
    key = await _api_key(client, headers)

    response = await _post_chat(client, key, _chat_body("tax-cap"))
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "UPSTREAM_AUTH_FAILED"
    # One switch is allowed: two candidates are tried, the other two are never
    # touched, so they are never cooled down either.
    assert _upstream_hits(upstream, "/unauthorized") == 2


def _budget_probe() -> Any:
    from l05_service.chat import _RetryBudget

    return _RetryBudget


def test_retry_budget_refund_restores_remaining():
    budget = _budget_probe()(3, 60.0)
    assert budget.remaining == 3
    assert budget.consume() is True
    assert budget.remaining == 2
    budget.refund()
    assert budget.remaining == 3
    assert budget.consume() is True
    assert budget.remaining == 2
    assert budget.consume() is True
    assert budget.remaining == 1
    assert budget.consume() is True
    assert budget.remaining == 0
    assert budget.consume() is False


async def test_permanent_failure_keeps_the_retry_budget(monkeypatch):
    from l03_domain.failover import EMPTY_RULES
    from l05_service import chat as chat_module
    from l05_service.chat import FailoverFailed, _RetryBudget, _RoundAttempts

    budgets: list[Any] = []

    class _SpyBudget(_RetryBudget):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            budgets.append(self)

    monkeypatch.setattr(chat_module, "_RetryBudget", _SpyBudget)

    async def _admit(session, candidate):
        return None

    async def _record_failure(session, candidate, **kwargs):
        return None

    async def _record_success(session, candidate, at_ms=None):
        return None

    monkeypatch.setattr(chat_module, "admit_candidate", _admit)
    monkeypatch.setattr(chat_module, "record_failure", _record_failure)
    monkeypatch.setattr(chat_module, "record_success", _record_success)

    def _candidate(name: str, priority: int) -> Any:
        return SimpleNamespace(
            priority=priority,
            priority_path=(priority,),
            channel=SimpleNamespace(id="c-" + name, slug=name, name=name),
            model=SimpleNamespace(id="m-" + name, upstream_model=name),
        )

    target = SimpleNamespace(
        name="bench",
        candidates=[_candidate("a", 1), _candidate("b", 2)],
        max_attempts=3,
        failover_rules=EMPTY_RULES,
    )

    async def _invoke(candidate, captured):
        raise LLMError("auth", "invalid api key", 401, False, tier=TIER_PERMANENT)

    runner = _RoundAttempts(None, target, attempts=[], is_stream=False)
    with pytest.raises(FailoverFailed):
        await runner.run(_invoke)
    assert budgets[0].remaining == budgets[0].max_attempts


async def test_transient_failure_consumes_the_retry_budget(monkeypatch):
    from l03_domain.failover import EMPTY_RULES
    from l05_service import chat as chat_module
    from l05_service.chat import _RetryBudget, _RoundAttempts

    budgets: list[Any] = []

    class _SpyBudget(_RetryBudget):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            budgets.append(self)

    monkeypatch.setattr(chat_module, "_RetryBudget", _SpyBudget)

    async def _admit(session, candidate):
        return None

    async def _record_failure(session, candidate, **kwargs):
        return None

    async def _record_success(session, candidate, at_ms=None):
        return None

    monkeypatch.setattr(chat_module, "admit_candidate", _admit)
    monkeypatch.setattr(chat_module, "record_failure", _record_failure)
    monkeypatch.setattr(chat_module, "record_success", _record_success)

    def _candidate(name: str, priority: int) -> Any:
        return SimpleNamespace(
            priority=priority,
            priority_path=(priority,),
            channel=SimpleNamespace(id="c-" + name, slug=name, name=name),
            model=SimpleNamespace(id="m-" + name, upstream_model=name),
        )

    target = SimpleNamespace(
        name="bench",
        # 数值越大越先调用：会失败的 a 取高优先级，确保先失败再落到 b。
        candidates=[_candidate("a", 2), _candidate("b", 1)],
        max_attempts=3,
        failover_rules=EMPTY_RULES,
    )

    async def _invoke(candidate, captured):
        if candidate.model.id == "m-a":
            raise LLMError("upstream", "boom", 500, True, tier=TIER_TRANSIENT)
        return "ok"

    runner = _RoundAttempts(None, target, attempts=[], is_stream=False)
    winner, round_index, value = await runner.run(_invoke)
    assert value == "ok"
    # Two upstream attempts were made (the failed one and the successful one),
    # and only the transient failure is charged to the budget.
    assert budgets[0].remaining == budgets[0].max_attempts - 2


def test_quota_park_lands_on_next_local_midnight() -> None:
    from l05_service import chat as chat_module

    now = time.time()
    until_ms = chat_module._quota_park_until_ms()
    delta_s = until_ms / 1000.0 - now
    assert 0.0 < delta_s <= 24 * 3600 + 5
    local = time.localtime(until_ms / 1000.0)
    assert (local.tm_hour, local.tm_min) == (0, 0)
    assert local.tm_sec <= 1


async def _stored(app, model_id: str) -> dict[str, Any]:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        return {
            "health_status": model.health_status,
            "consecutive_failures": int(model.consecutive_failures or 0),
            "consecutive_trip_count": int(model.consecutive_trip_count or 0),
            "cooldown_until": model.cooldown_until,
        }


async def test_rate_limited_429_never_raises_failure_counters(
    client, upstream, app, monkeypatch
):
    """429 是限流口径：不增故障计数、不 trip、health 不进 open；冷却窗口内
    跳过（503 + Retry-After），窗口过后自动恢复；冷却态经管理面 cooldown 可见。"""
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/rate-limited", name="throttled-model"
    )
    model = await _model(
        client,
        headers,
        channel["id"],
        call_model="tax-throttle",
        cooldown={
            "failure_threshold": 2,
            "cooldown_s": 60,
            "rate_limit_cooldown_s": 3,
        },
    )
    key = await _api_key(client, headers)

    first = await _post_chat(client, key, _chat_body("tax-throttle"))
    assert first.status_code == 429, first.text
    assert first.json()["error"]["code"] == "UPSTREAM_RATE_LIMITED"
    assert first.headers["retry-after"] == "5"
    # 一次请求恰好一次真实上游尝试：限流不做请求内重试。
    assert _upstream_hits(upstream, "/rate-limited") == 1
    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0
    assert stored["consecutive_trip_count"] == 0
    # 时长 Retry-After=5s 优先于 rate_limit_cooldown_s=3s。
    assert stored["cooldown_until"] is not None
    now = routing.now_ms()
    assert now + 4_500 <= stored["cooldown_until"] <= now + 5_500

    listed = (
        await client.get(
            f"/api/channels/{channel['id']}/models", headers=headers
        )
    ).json()["items"]
    detail = next(item for item in listed if item["id"] == model["id"])
    # 冷却态经管理面 cooldown 可见，但状态仍是 normal（没有 trip）。
    assert detail["visible_status"] == "normal"
    assert detail["cooldown_until"] is not None

    # 冷却窗口内：不再打上游，也没有把模型判成故障。
    second = await _post_chat(client, key, _chat_body("tax-throttle"))
    assert second.status_code == 503, second.text
    assert _upstream_hits(upstream, "/rate-limited") == 1
    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0

    # 冷却到点自动恢复：再次真实尝试，429 依旧不累积。
    later = routing.now_ms() + 6_000
    monkeypatch.setattr(routing, "now_ms", lambda: later)
    third = await _post_chat(client, key, _chat_body("tax-throttle"))
    assert third.status_code == 429, third.text
    assert _upstream_hits(upstream, "/rate-limited") == 2
    stored = await _stored(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0
    assert stored["consecutive_trip_count"] == 0


async def test_rate_limit_does_not_count_into_stream_abort_failures(
    client, upstream, app
):
    """限流与故障两本账：1 次 429 + 1 次断流 → 熔断只记 1 次失败。"""
    headers = await _token(client)
    # Implicit groups take the channel priority; larger numbers run first.
    throttled = await _channel(
        client,
        headers,
        upstream.base_url + "/rate-limited",
        name="限流先到",
        priority=2,
    )
    broken = await _channel(
        client,
        headers,
        upstream.base_url + "/openai-stream-broken",
        name="断流兜底",
        priority=1,
    )
    throttled_model = await _model(
        client,
        headers,
        throttled["id"],
        call_model="split-leaf",
        cooldown={"failure_threshold": 2, "cooldown_s": 60},
    )
    broken_model = await _model(
        client,
        headers,
        broken["id"],
        call_model="split-leaf",
        cooldown={"failure_threshold": 2, "cooldown_s": 60},
    )
    key = await _api_key(client, headers)

    response = await _post_chat(client, key, _chat_body("split-leaf", stream=True))

    assert response.status_code == 200, response.text
    assert "STREAM_ABORTED" in response.text
    assert _upstream_hits(upstream, "/rate-limited") == 1
    assert _upstream_hits(upstream, "/openai-stream-broken") == 1
    limited = await _stored(app, throttled_model["id"])
    assert limited["health_status"] == "normal"
    assert limited["consecutive_failures"] == 0
    assert limited["cooldown_until"] is not None
    aborted = await _stored(app, broken_model["id"])
    assert aborted["consecutive_failures"] == 1
    assert aborted["health_status"] == "normal"


