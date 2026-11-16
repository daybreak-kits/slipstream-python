from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from l04_repo import orm
from tests.fake_upstream_server import FakeUpstreamServer


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


async def _channel(client, headers, url: str, **overrides) -> dict[str, Any]:
    payload = {
        "name": "流式测试渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, **overrides) -> dict[str, Any]:
    payload = {"upstream_model": "fake-model", "call_model": "gpt-test", **overrides}
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": "stream"})
    assert response.status_code == 201, response.text
    return response.json()


async def _prepare(client, upstream: FakeUpstreamServer, path: str = ""):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url + path)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    return headers, channel, key


def _chat_body(model: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    body.update(overrides)
    return body


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


def _delta_text(text: str) -> str:
    chunks: list[str] = []
    for name, data in _sse_events(text):
        if name == "error" or data == "[DONE]":
            continue
        payload = json.loads(data)
        for choice in payload.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                chunks.append(content)
    return "".join(chunks)


async def _wait_for_log(client, headers, request_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


async def test_stream_forwards_deltas_done_and_logs_metrics(client, upstream):
    headers, channel, key = await _prepare(client, upstream)
    requested = f"{channel['slug']}/gpt-test"

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(requested),
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert _delta_text(text) == "hello"
    events = _sse_events(text)
    assert events[-1] == (None, "[DONE]")
    for name, data in events:
        assert name is None
        if data != "[DONE]":
            assert json.loads(data)["model"] == requested

    posted = upstream.find("POST", "/v1/chat/completions")
    assert len(posted) == 1
    assert posted[0]["json"]["stream"] is True
    assert posted[0]["json"]["stream_options"]["include_usage"] is True

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is True
    assert log["http_status"] == 200
    assert log["error_code"] is None
    assert log["prompt_tokens"] == 4
    assert log["completion_tokens"] == 2
    assert log["usage_estimated"] is False
    assert log["ttft_ms"] is not None and log["ttft_ms"] >= 0
    assert log["total_ms"] >= log["ttft_ms"]
    assert log["throughput_tps"] is not None and log["throughput_tps"] > 0
    assert log["attempts"][0]["status"] == "success"


async def test_stream_without_upstream_usage_estimates_and_marks(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-nousage")
    requested = f"{channel['slug']}/gpt-test"

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(requested),
    )

    assert response.status_code == 200, response.text
    assert _delta_text(response.text) == "hello"
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["usage_estimated"] is True
    assert log["prompt_tokens"] > 0
    assert log["completion_tokens"] >= 1
    assert log["throughput_tps"] > 0


async def test_stream_estimate_counts_relayed_reasoning(client, upstream):
    """契约 ADR-074/075：无上游 usage 时，输出估算覆盖正文 + 已转发思考块
    （tokenizer 计数；测试经 conftest 固定走脚本折算保持离线确定性）。"""
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-relayed-thinking")
    requested = f"{channel['slug']}/gpt-test"

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(requested),
    )

    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["usage_estimated"] is True
    # "think think"(11) + "lo"(2) = 13 个 ASCII 字符：脚本折算 13 // 4 = 3
    assert log["completion_tokens"] == 3


async def test_stream_pre_first_byte_5xx_returns_json_error(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/broken")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is True
    assert log["http_status"] == 502
    assert log["error_code"] == "UPSTREAM_ERROR"
    assert log["ttft_ms"] is None
    assert log["throughput_tps"] is None
    assert log["attempts"][0]["status"] == "error"


async def test_stream_pre_first_byte_empty_is_502(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-empty")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EMPTY_RESPONSE"

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["error_code"] == "EMPTY_RESPONSE"
    assert log["is_stream"] is True


async def test_stream_abort_after_first_byte_emits_error_event_without_done(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-broken")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 200
    text = response.text
    assert _delta_text(text) == "hel"
    assert "[DONE]" not in text
    names = [name for name, _ in _sse_events(text)]
    assert "error" in names
    error_event = next(data for name, data in _sse_events(text) if name == "error")
    assert json.loads(error_event)["error"]["code"] == "STREAM_ABORTED"

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is True
    assert log["http_status"] == 502
    assert log["error_code"] == "STREAM_ABORTED"
    assert log["ttft_ms"] is not None
    assert log["attempts"][0]["status"] == "error"
    assert log["attempts"][0]["error_code"] == "stream_abort"


async def test_stream_upstream_error_event_after_first_byte(client, upstream):
    headers, channel, key = await _prepare(
        client, upstream, "/openai-stream-upstream-error"
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 200
    assert "[DONE]" not in response.text
    error_event = next(data for name, data in _sse_events(response.text) if name == "error")
    assert json.loads(error_event)["error"]["code"] == "STREAM_ABORTED"


async def _call_with_disconnect(
    app,
    body: dict[str, Any],
    api_key: str,
    request_id: str,
    *,
    delay: float = 0.2,
    disconnect_on: bytes | None = None,
):
    payload = json.dumps(body).encode()
    sent: list[bytes] = []
    disconnected = asyncio.Event()
    body_consumed = False

    async def receive():
        nonlocal body_consumed
        if not body_consumed:
            body_consumed = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.append(message["body"])
            if disconnect_on is not None and disconnect_on in message["body"]:
                disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {api_key}".encode()),
            (b"x-request-id", request_id.encode()),
        ],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }

    async def disconnect_soon():
        if disconnect_on is not None:
            # Let the client receive the marker, then close immediately.
            await asyncio.wait_for(disconnected.wait(), timeout=5)
            return
        await asyncio.sleep(delay)
        disconnected.set()

    await asyncio.gather(app(scope, receive, send), disconnect_soon())
    return b"".join(sent).decode(errors="replace")


async def test_client_disconnect_cancels_upstream_and_logs_stopped(client, app, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-slow")
    request_id = "req_stream_cancel_test"

    started = time.monotonic()
    text = await _call_with_disconnect(
        app, _chat_body(f"{channel['slug']}/gpt-test"), key["key"], request_id
    )
    elapsed = time.monotonic() - started

    assert "hel" in text
    # Upstream sleeps 1.5s before its second chunk. The ASGI disconnect is
    # observed on the next relay wakeup, so allow that one upstream chunk but
    # not a full drain.
    assert elapsed < 2.0

    log = await _wait_for_log(client, headers, request_id)
    assert log["is_stream"] is True
    assert log["http_status"] == 499
    assert log["error_code"] == "STREAM_STOPPED"
    assert log["attempts"][0]["status"] == "stopped"
    assert log["ttft_ms"] is not None
    assert log["total_ms"] < 2500


async def test_client_disconnect_after_finish_marker_logs_success(
    client, app, upstream
):
    headers, channel, key = await _prepare(
        client, upstream, "/openai-stream-finish-slow"
    )
    request_id = "req_stream_finish_close_test"

    started = time.monotonic()
    text = await _call_with_disconnect(
        app,
        _chat_body(f"{channel['slug']}/gpt-test"),
        key["key"],
        request_id,
        disconnect_on=b'"finish_reason": "stop"',
    )
    elapsed = time.monotonic() - started

    assert "hel" in text
    # The client closes right after receiving finish_reason; the upstream still
    # holds the stream open for 1.5s, so this only bounds the teardown path.
    assert elapsed < 2.5

    log = await _wait_for_log(client, headers, request_id)
    assert log["is_stream"] is True
    assert log["http_status"] == 200
    assert log["error_code"] is None
    assert log["attempts"][0]["status"] == "success"
    assert log["usage_estimated"] is False
    assert log["prompt_tokens"] == 4
    assert log["completion_tokens"] == 2
    assert log["total_ms"] < 1200


async def test_concurrent_streams_do_not_hold_sqlite_write_lock(client, upstream):
    """Five streams priming at once must not serialize on a DB write lock.

    The fake upstream holds the first byte for 6s, longer than the 5s SQLite
    busy timeout. Before admit_candidate started committing its conditional
    UPDATE unconditionally, every request after the first would block on the
    write transaction and fail with 500 database is locked.
    """
    _, channel, key = await _prepare(client, upstream, "/openai-stream-hold")
    headers = {"Authorization": f"Bearer {key['key']}"}
    body = _chat_body(f"{channel['slug']}/gpt-test")

    responses = await asyncio.gather(
        *[
            client.post(
                "/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=15.0,
            )
            for _ in range(5)
        ]
    )

    assert [response.status_code for response in responses] == [200] * 5
    for response in responses:
        assert "[DONE]" in response.text
        assert "database is locked" not in response.text


async def test_client_cancelled_stream_does_not_feed_the_failure_breaker(
    client, app, upstream
):
    """只计「真实到达上游后中断」：客户端断开（499 STREAM_STOPPED）不进熔断。"""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url + "/openai-stream-slow")
    model = await _model(
        client,
        headers,
        channel["id"],
        cooldown={"failure_threshold": 1, "cooldown_s": 60},
    )
    key = await _key(client, headers)
    request_id = "req_cancel_breaker_test"

    await _call_with_disconnect(
        app, _chat_body(f"{channel['slug']}/gpt-test"), key["key"], request_id
    )
    log = await _wait_for_log(client, headers, request_id)
    assert log["http_status"] == 499
    assert log["error_code"] == "STREAM_STOPPED"
    assert log["attempts"][0]["status"] == "stopped"

    # 阈值=1：任何一次被计入的失败都会立即 trip；熔断必须保持 normal。
    async with app.state.session_factory() as session:
        row = await session.get(orm.ChannelModelORM, model["id"])
        assert row is not None
        assert (row.health_status or "normal") == "normal"
        assert int(row.consecutive_failures or 0) == 0

    # on_success 清零：人为置非零失败计数，一次成功流式后必须归零。
    async with app.state.session_factory() as session:
        row = await session.get(orm.ChannelModelORM, model["id"])
        assert row is not None
        row.consecutive_failures = 3
        row.consecutive_trip_count = 1
        await session.commit()

    ok = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert ok.status_code == 200
    assert _delta_text(ok.text) == "hello"

    async with app.state.session_factory() as session:
        row = await session.get(orm.ChannelModelORM, model["id"])
        assert row is not None
        assert int(row.consecutive_failures or 0) == 0
        assert int(row.consecutive_trip_count or 0) == 0
        assert (row.health_status or "normal") == "normal"

