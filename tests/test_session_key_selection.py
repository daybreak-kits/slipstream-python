"""Sticky channel-credential selection: one conversation keeps one key.

The provider's prefix cache is isolated per account (per key), so rotating keys
per request is rotating cache domains per request. These tests pin the rule that
a conversation key keeps the same credential on a multi-key channel, while the
old request-id spread stays the default when no conversation key is present.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterator
from typing import Any

import pytest

from l05_service import channels
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


def _stub_channel() -> Any:
    """A handle for select_channel_api_key; channel_api_keys is patched."""
    return object()


@pytest.fixture
def three_keys(monkeypatch) -> list[str]:
    values = ["key-a", "key-b", "key-c"]
    monkeypatch.setattr(
        channels, "channel_api_keys", lambda channel, **kwargs: list(values)
    )
    return values


def _legacy_pick(values: list[str], request_id: str) -> str:
    digest = hashlib.sha256(request_id.encode("utf-8")).digest()
    return values[int.from_bytes(digest[:4], "big") % len(values)]


def test_one_conversation_always_gets_the_same_key(three_keys) -> None:
    picked = {
        channels.select_channel_api_key(
            _stub_channel(), f"req-{index}", session_key="conv-1"
        )
        for index in range(40)
    }
    assert len(picked) == 1
    assert picked.pop() in three_keys


def test_different_conversations_can_use_different_keys(three_keys) -> None:
    picked = {
        channels.select_channel_api_key(
            _stub_channel(), "same-request", session_key=f"conv-{index}"
        )
        for index in range(60)
    }
    assert len(picked) >= 2


def test_the_bound_scope_matches_the_explicit_argument(three_keys) -> None:
    explicit = channels.select_channel_api_key(
        _stub_channel(), "req", session_key="conv-scope"
    )
    with channels.key_session_scope("conv-scope"):
        assert channels.current_key_session_key() == "conv-scope"
        scoped = {
            channels.select_channel_api_key(_stub_channel(), f"req-{index}")
            for index in range(20)
        }
    assert scoped == {explicit}
    assert channels.current_key_session_key() == ""


def test_without_a_conversation_key_the_request_id_still_spreads(three_keys) -> None:
    picked = {
        channels.select_channel_api_key(_stub_channel(), f"req-{index}")
        for index in range(60)
    }
    assert len(picked) >= 2
    for index in range(5):
        assert (
            channels.select_channel_api_key(_stub_channel(), f"req-{index}")
            == _legacy_pick(three_keys, f"req-{index}")
        )


def test_a_single_key_is_never_rotated(monkeypatch) -> None:
    monkeypatch.setattr(
        channels, "channel_api_keys", lambda channel, **kwargs: ["only-key"]
    )
    assert channels.select_channel_api_key(_stub_channel(), "req") == "only-key"
    assert (
        channels.select_channel_api_key(_stub_channel(), "req", session_key="conv")
        == "only-key"
    )
    with channels.key_session_scope("conv"):
        assert channels.select_channel_api_key(_stub_channel(), "req") == "only-key"


def test_no_keys_selected(monkeypatch) -> None:
    monkeypatch.setattr(channels, "channel_api_keys", lambda channel, **kwargs: [])
    assert channels.select_channel_api_key(_stub_channel(), "req") == ""
    assert (
        channels.select_channel_api_key(_stub_channel(), "req", session_key="conv")
        == ""
    )


def test_nested_scopes_restore_the_outer_key() -> None:
    with channels.key_session_scope("outer"):
        with channels.key_session_scope("inner"):
            assert channels.current_key_session_key() == "inner"
        assert channels.current_key_session_key() == "outer"
    assert channels.current_key_session_key() == ""


# -- chat wiring -------------------------------------------------------------


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(client, headers, url: str, *, name: str) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _multi_key_channel(client, headers, url: str, *, name: str) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "protocol": "openai_chat",
            "api_keys": [
                {"label": "甲", "api_key": "sk-bad-1111"},
                {"label": "乙", "api_key": "sk-bad-2222"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(
    client, headers, channel_id: str, *, call_model: str
) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": call_model},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict[str, Any]:
    response = await client.post(
        "/api/keys", headers=headers, json={"label": "key-select"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def _wait_for_log(
    client, headers, request_id: str, timeout: float = 3.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


async def test_chat_uses_the_conversation_key_when_building_headers(
    client, upstream, monkeypatch
) -> None:
    from l05_service import rewrite

    seen: list[str] = []
    real = rewrite.build_headers

    def spy(channel, request_id=None, auth_style=None, client_headers=None):
        seen.append(channels.current_key_session_key())
        return real(channel, request_id, auth_style, client_headers)

    monkeypatch.setattr(rewrite, "build_headers", spy)

    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="键作用域")
    await _model(client, headers, channel["id"], call_model="key-scope")
    key = await _key(client, headers)
    auth = {"Authorization": f"Bearer {key['key']}"}

    response = await client.post(
        "/v1/chat/completions",
        headers={**auth, "x-session-id": "conv-key"},
        json=_chat_body("key-scope"),
    )
    assert response.status_code == 200, response.text
    assert "conv-key" in seen
    assert all(scope == "conv-key" for scope in seen)

    # The fallback header is threaded exactly the same way.
    seen.clear()
    response = await client.post(
        "/v1/chat/completions",
        headers={**auth, "x-bili-plugin-conversation": "conv-fallback"},
        json=_chat_body("key-scope"),
    )
    assert response.status_code == 200, response.text
    assert "conv-fallback" in seen
    assert all(scope == "conv-fallback" for scope in seen)

    # Without a conversation header the scope stays empty and selection falls
    # back to the request id (the legacy behaviour).
    seen.clear()
    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("key-scope")
    )
    assert response.status_code == 200, response.text
    assert seen
    assert all(scope == "" for scope in seen)


async def test_auth_failure_still_rotates_to_the_next_channel(client, upstream) -> None:
    headers = await _token(client)
    broken = await _multi_key_channel(
        client, headers, upstream.base_url + "/unauthorized", name="坏密钥"
    )
    healthy = await _channel(client, headers, upstream.base_url, name="好密钥")
    await _model(client, headers, broken["id"], call_model="key-rotate")
    await _model(client, headers, healthy["id"], call_model="key-rotate")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key['key']}",
            "x-session-id": "conv-rotate",
        },
        json=_chat_body("key-rotate"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "好密钥"
