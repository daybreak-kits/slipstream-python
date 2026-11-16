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


async def _channel(client, headers, url: str, **overrides) -> dict[str, Any]:
    payload = {
        "name": "响应改写渠道",
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


async def _key(client, headers, label: str = "resp") -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


async def _configure(client, headers, channel_id: str, rules: dict[str, Any]) -> None:
    response = await client.patch(
        f"/api/channels/{channel_id}", headers=headers, json={"rewrite_rules": rules}
    )
    assert response.status_code == 200, response.text


async def _wait_log(app, request_id: str):
    for _ in range(100):
        async with app.state.session_factory() as session:
            log = await repo.get_log_by_request_id(session, request_id)
            if log is not None:
                return log
        await asyncio.sleep(0.02)
    raise AssertionError(f"log {request_id} was not written")


async def _setup(client, headers, upstream: FakeUpstreamServer, rules: dict[str, Any]):
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    await _configure(client, headers, channel["id"], rules)
    return channel, key


def _chat_payload(channel: dict[str, Any], key: dict[str, Any], **extra) -> dict[str, Any]:
    return {
        "headers": {"Authorization": f"Bearer {key['key']}"},
        "json": {
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
            **extra,
        },
    }


async def test_non_stream_success_rewrites_headers_and_body_and_logs_diff(
    client, app, upstream
):
    headers = await _token(client)
    channel, key = await _setup(
        client,
        headers,
        upstream,
        {
            "response_headers": [
                {"mode": "set", "path": "X-Gateway", "value": "slipstream"},
                {"mode": "delete", "path": "x-upstream-trace"},
            ],
            "response_body": [
                {"mode": "update", "path": "choices.0.message.content", "value": "rewritten"},
                {"mode": "create", "path": "gateway.rewritten", "value": True},
            ],
        },
    )

    response = await client.post("/v1/chat/completions", **_chat_payload(channel, key))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "rewritten"
    assert body["gateway"]["rewritten"] is True
    assert body["model"] == f"{channel['slug']}/gpt-test"
    assert response.headers["X-Gateway"] == "slipstream"
    assert "x-upstream-trace" not in {name.lower() for name in response.headers}

    log = await _wait_log(app, response.headers["X-Request-ID"])
    assert log.rewrite_applied is True
    assert log.rewrite_skipped == ""
    diff = json.loads(log.rewrite_diff_json)
    assert set(diff["response_headers"]) == {"x-gateway", "x-upstream-trace"}
    assert {
        "choices.0.message.content",
        "gateway",
    } <= {change["path"] for change in diff["response_body"]}

    detail = await client.get(
        f"/api/logs/{response.headers['X-Request-ID']}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["rewrite_applied"] is True
    assert payload["rewrite_skipped"] == ""
    assert payload["rewrite_diff"] == diff


async def test_stream_skips_body_rewrite_but_keeps_headers_and_logs_reason(
    client, app, upstream
):
    headers = await _token(client)
    channel, key = await _setup(
        client,
        headers,
        upstream,
        {
            "response_headers": [
                {"mode": "set", "path": "X-Gateway", "value": "stream-gateway"}
            ],
            "response_body": [
                {"mode": "update", "path": "choices.0.message.content", "value": "MUTATED"}
            ],
        },
    )

    async with client.stream(
        "POST", "/v1/chat/completions", **_chat_payload(channel, key, stream=True)
    ) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["X-Gateway"] == "stream-gateway"
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "MUTATED" not in text
    assert '"hel"' in text and '"lo"' in text

    log = await _wait_log(app, response.headers["X-Request-ID"])
    assert log.rewrite_skipped == "stream"
    diff = json.loads(log.rewrite_diff_json)
    assert diff["response_body"] == []
    assert "x-gateway" in diff["response_headers"]


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [("/content-policy", 400), ("/all-broken", 502)],
)
async def test_error_responses_are_never_rewritten(
    client, app, upstream, path, expected_status
):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url + path)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers, label="err")
    await _configure(
        client,
        headers,
        channel["id"],
        {
            "response_headers": [
                {"mode": "set", "path": "X-Gateway", "value": "should-not-appear"}
            ],
            "response_body": [
                {"mode": "update", "path": "error.message", "value": "masked"}
            ],
        },
    )

    response = await client.post("/v1/chat/completions", **_chat_payload(channel, key))
    assert response.status_code == expected_status, response.text
    assert "X-Gateway" not in response.headers
    assert response.json()["error"]["message"] != "masked"

    log = await _wait_log(app, response.headers["X-Request-ID"])
    assert log.rewrite_applied is False
    assert json.loads(log.rewrite_diff_json) == {}


async def test_rewrite_failure_returns_the_original_response(
    client, app, upstream, monkeypatch
):
    headers = await _token(client)
    channel, key = await _setup(
        client,
        headers,
        upstream,
        {
            "response_headers": [
                {"mode": "set", "path": "X-Gateway", "value": "should-not-appear"}
            ],
            "response_body": [
                {"mode": "update", "path": "choices.0.message.content", "value": "x"}
            ],
        },
    )

    def broken(*_args, **_kwargs):
        raise RuntimeError("rule engine exploded")

    monkeypatch.setattr("l05_service.rewrite.apply_body_rules_report", broken)

    response = await client.post("/v1/chat/completions", **_chat_payload(channel, key))
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "OK"
    assert "X-Gateway" not in response.headers

    log = await _wait_log(app, response.headers["X-Request-ID"])
    assert log.rewrite_applied is False
    assert log.rewrite_skipped == "error"
    diff = json.loads(log.rewrite_diff_json)
    assert "rule engine exploded" in diff["error"]
