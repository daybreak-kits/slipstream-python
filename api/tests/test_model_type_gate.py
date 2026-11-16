from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

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


async def _channel(client, headers, url: str, *, slug: str = "embed-channel") -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "Embedding Channel",
            "slug": slug,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, *, call_model: str, model_type: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={
            "upstream_model": call_model,
            "call_model": call_model,
            "model_type": model_type,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": "types"})
    assert response.status_code == 201, response.text
    return response.json()


async def test_chat_rejects_embedding_model_without_calling_upstream(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="text-embedding-3-small", model_type="embedding")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={"model": "text-embedding-3-small", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "NOT_IMPLEMENTED"
    assert "embedding" in error["message"]
    assert not upstream.find("POST", "/v1/chat/completions")


async def test_chat_still_serves_chat_model_in_same_channel(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="chat-model", model_type="chat")
    await _model(client, headers, channel["id"], call_model="bge-m3", model_type="embedding")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={"model": "chat-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "chat-model"
