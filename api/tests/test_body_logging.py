from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest

from l04_repo import repo
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


async def _channel(client, headers, url: str, **overrides) -> dict:
    payload = {
        "name": "正文日志渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, **overrides) -> dict:
    payload = {"upstream_model": "fake-model", "call_model": "gpt-test", **overrides}
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": "body"})
    assert response.status_code == 201, response.text
    return response.json()


async def _set_policy(client, headers, policy: str) -> None:
    response = await client.patch(
        "/api/settings", headers=headers, json={"body_policy": policy}
    )
    assert response.status_code == 200, response.text


async def _wait_body(app, request_id: str, timeout: float = 4.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with app.state.session_factory() as session:
            row = await repo.get_request_body(session, request_id)
        if row is not None:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"body row for {request_id} was not written")


def _chat_body(model: str, **overrides) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    body.update(overrides)
    return body


async def _setup(client, headers, base_url: str, policy: str = "all"):
    await _set_policy(client, headers, policy)
    channel = await _channel(client, headers, base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    return channel, key


async def test_all_policy_stores_redacted_request_upstream_and_response(
    client, app, upstream
):
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "all")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(
            f"{channel['slug']}/gpt-test",
            messages=[
                {
                    "role": "user",
                    "content": "hello https://example.test/cb?token=QUERY_SECRET&keep=1",
                }
            ],
            metadata={"api_key": "BODY_SECRET", "trace": "keep-me"},
        ),
    )

    assert response.status_code == 200, response.text
    request_id = response.headers["X-Request-ID"]
    row = await _wait_body(app, request_id)

    rendered = json.dumps(
        {
            "request_body": row.request_body,
            "upstream_body": row.upstream_body,
            "response_body": row.response_body,
        },
        ensure_ascii=False,
    )
    assert "QUERY_SECRET" not in rendered
    assert "BODY_SECRET" not in rendered
    assert "keep-me" in rendered
    assert "keep=1" in rendered
    assert "***" in row.request_body
    assert "***" in row.upstream_body
    assert "OK" in row.response_body
    assert row.response_summary is None
    assert row.reasoning_content is None


async def test_all_policy_truncates_bodies_to_16k(client, app, upstream):
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "all")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(
            f"{channel['slug']}/gpt-test",
            messages=[{"role": "user", "content": "x" * 40_000}],
        ),
    )

    assert response.status_code == 200, response.text
    row = await _wait_body(app, response.headers["X-Request-ID"])

    assert len(row.request_body.encode("utf-8")) <= 16 * 1024
    assert len(row.upstream_body.encode("utf-8")) <= 16 * 1024
    assert row.request_body.endswith("[truncated]")
    assert row.upstream_body.endswith("[truncated]")


async def test_reasoning_content_has_its_own_budget_and_is_removed_from_body(
    client, app, upstream
):
    headers = await _token(client)
    reasoning_channel = await _channel(
        client, headers, f"{upstream.base_url}/openai-reasoning-chat"
    )
    await _model(client, headers, reasoning_channel["id"])
    key = await _key(client, headers)
    await _set_policy(client, headers, "all")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{reasoning_channel['slug']}/gpt-test"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "OK"
    row = await _wait_body(app, response.headers["X-Request-ID"])
    assert "OK" in row.response_body
    assert "R" * 100 not in row.response_body
    assert row.reasoning_content.startswith("R" * 100)
    assert len(row.reasoning_content.encode("utf-8")) <= 16 * 1024
    assert row.reasoning_content.endswith("[truncated]")


async def test_off_policy_writes_no_body(client, app, upstream):
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "off")

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 200, response.text
    await asyncio.sleep(0.2)
    async with app.state.session_factory() as session:
        row = await repo.get_request_body(session, response.headers["X-Request-ID"])
    assert row is None


async def test_errors_policy_skips_success_but_captures_4xx_and_5xx(
    client, app, upstream
):
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "errors")
    auth = {"Authorization": f"Bearer {key['key']}"}

    success = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert success.status_code == 200
    await asyncio.sleep(0.2)
    async with app.state.session_factory() as session:
        assert await repo.get_request_body(session, success.headers["X-Request-ID"]) is None

    # Point extra channels at failing upstream paths to exercise 4xx and 5xx.
    failing_400 = await _channel(
        client, headers, f"{upstream.base_url}/content-policy"
    )
    await _model(client, headers, failing_400["id"])
    blocked = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json=_chat_body(f"{failing_400['slug']}/gpt-test"),
    )
    assert blocked.status_code == 400
    blocked_row = await _wait_body(app, blocked.headers["X-Request-ID"])
    assert "blocked by content policy" in blocked_row.response_body

    failing_500 = await _channel(client, headers, f"{upstream.base_url}/broken")
    await _model(client, headers, failing_500["id"])
    broken = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json=_chat_body(f"{failing_500['slug']}/gpt-test"),
    )
    assert broken.status_code == 502
    broken_row = await _wait_body(app, broken.headers["X-Request-ID"])
    assert "chat endpoint failed" in broken_row.response_body


async def test_errors_policy_captures_pseudo_success_timeout_and_connection_failure(
    client, app, upstream, monkeypatch
):
    from types import SimpleNamespace

    from l05_service import chat

    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(request_timeout_s=1))
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "errors")
    auth = {"Authorization": f"Bearer {key['key']}"}

    empty_channel = await _channel(
        client, headers, f"{upstream.base_url}/openai-empty-chat"
    )
    await _model(client, headers, empty_channel["id"])
    pseudo = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json=_chat_body(f"{empty_channel['slug']}/gpt-test"),
    )
    assert pseudo.status_code == 502, pseudo.text
    pseudo_row = await _wait_body(app, pseudo.headers["X-Request-ID"])
    assert pseudo_row.request_body is not None

    slow_channel = await _channel(
        client, headers, f"{upstream.base_url}/slow"
    )
    await _model(client, headers, slow_channel["id"])
    timed_out = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json=_chat_body(f"{slow_channel['slug']}/gpt-test"),
    )
    assert timed_out.status_code == 504, timed_out.text
    timeout_row = await _wait_body(app, timed_out.headers["X-Request-ID"])
    assert timeout_row.request_body is not None

    dead_channel = await _channel(
        client, headers, "http://127.0.0.1:1"
    )
    await _model(client, headers, dead_channel["id"])
    unreachable = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json=_chat_body(f"{dead_channel['slug']}/gpt-test"),
    )
    assert unreachable.status_code == 502, unreachable.text
    dead_row = await _wait_body(app, unreachable.headers["X-Request-ID"])
    assert dead_row.request_body is not None


async def test_all_policy_stream_keeps_only_a_compact_summary(client, app, upstream):
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "all")

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test", stream=True),
    ) as response:
        assert response.status_code == 200, response.text
        request_id = response.headers["X-Request-ID"]
        chunks = [chunk async for chunk in response.aiter_text()]
        joined = "".join(chunks)
        assert '"content": "hel"' in joined
        assert '"content": "lo"' in joined

    row = await _wait_body(app, request_id)
    summary = json.loads(row.response_summary)
    assert summary["delta_count"] == 2
    assert summary["first_preview"] == "hello"
    assert summary["last_preview"] == "hello"
    assert summary["finish_reason"] == "stop"
    assert summary["saw_done"] is True
    assert summary["error_events"] == []
    assert row.response_body is None
    assert row.reasoning_content is None
    assert row.request_body is not None
    assert row.upstream_body is not None


async def test_errors_policy_stream_abort_is_captured_with_error_event(
    client, app, upstream
):
    headers = await _token(client)
    channel, key = await _setup(client, headers, upstream.base_url, "errors")

    broken_channel = await _channel(
        client, headers, f"{upstream.base_url}/openai-stream-broken"
    )
    await _model(client, headers, broken_channel["id"])
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{broken_channel['slug']}/gpt-test", stream=True),
    ) as response:
        assert response.status_code == 200, response.text
        broken_id = response.headers["X-Request-ID"]
        async for _chunk in response.aiter_text():
            pass

    broken_row = await _wait_body(app, broken_id)
    summary = json.loads(broken_row.response_summary)
    assert broken_row.response_body is None
    assert summary["delta_count"] == 1
    assert summary["saw_done"] is False
    assert summary["finish_reason"] is None
    assert summary["error_events"]
    assert summary["error_events"][0]["kind"] == "stream_abort"
