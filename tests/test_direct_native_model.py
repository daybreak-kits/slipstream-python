from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from l05_service import routing
from tests.fake_upstream_server import FakeUpstreamServer

NATIVE = "nvidia/nemotron-3-ultra-550b-a55b"


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


async def _channel(client, headers, url: str, *, name: str, slug: str) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "slug": slug,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "priority": 1,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, *, call_model: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": call_model, "call_model": call_model},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": "native"})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def _chat(client, key: dict[str, Any], model: str):
    return await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(model),
    )


async def test_native_id_containing_slash_routes_to_its_channel(client, upstream):
    """OpenRouter/NIM ids like "nvidia/..." must reach the channel that owns them."""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="NVIDIA NIM", slug="nvidia-nim")
    await _model(client, headers, channel["id"], call_model=NATIVE)
    key = await _key(client, headers)

    response = await _chat(client, key, NATIVE)

    assert response.status_code == 200, response.text
    assert response.json()["model"] == NATIVE
    sent = upstream.find("POST", "/v1/chat/completions")
    assert len(sent) == 1
    assert sent[0]["json"]["model"] == NATIVE


async def test_qualified_name_keeps_working_next_to_native_lookup(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="NVIDIA NIM", slug="nvidia-nim")
    await _model(client, headers, channel["id"], call_model=NATIVE)
    key = await _key(client, headers)

    qualified = await _chat(client, key, f"nvidia-nim/{NATIVE}")
    native = await _chat(client, key, NATIVE)

    assert qualified.status_code == 200, qualified.text
    assert native.status_code == 200, native.text
    assert len(upstream.find("POST", "/v1/chat/completions")) == 2


async def test_ambiguous_native_id_is_rejected_with_qualified_names(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="NIM", slug="nvidia-nim")
    second = await _channel(client, headers, upstream.base_url, name="OR", slug="openrouter")
    await _model(client, headers, first["id"], call_model=NATIVE)
    await _model(client, headers, second["id"], call_model=NATIVE)
    key = await _key(client, headers)

    response = await _chat(client, key, NATIVE)

    assert response.status_code == 404, response.text
    message = response.json()["error"]["message"]
    assert f"nvidia-nim/{NATIVE}" in message
    assert f"openrouter/{NATIVE}" in message
    assert not upstream.find("POST", "/v1/chat/completions")


async def test_unknown_native_id_still_404(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="NIM", slug="nvidia-nim")
    await _model(client, headers, channel["id"], call_model=NATIVE)
    key = await _key(client, headers)

    response = await _chat(client, key, "nvidia/does-not-exist")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"
    assert not upstream.find("POST", "/v1/chat/completions")


def _snapshot(channels, models):
    return routing.CatalogSnapshot(  # type: ignore[attr-defined]
        version="v1",
        loaded_at_ms=0,
        channels={channel.id: channel for channel in channels},
        models={model.id: model for model in models},
        direct={},
        models_by_call_model={model.call_model: (model,) for model in models},
        combos_by_id={},
        combos_by_name={},
        targets_by_combo={},
        groups={},
        api_keys={},
        body_settings=None,
    )


def _rows(enabled: bool = True, protocol: str = "openai_chat"):
    from l04_repo import orm

    channel = orm.ChannelORM(
        id="ch1",
        slug="nvidia-nim",
        name="NIM",
        connection_url="http://upstream.test",
        api_key_enc="",
        protocol=protocol,
        enabled=True,
        priority=1,
    )
    model = orm.ChannelModelORM(
        id="m1",
        channel_id="ch1",
        upstream_model=NATIVE,
        call_model=NATIVE,
        enabled=enabled,
    )
    return channel, model


def test_cached_snapshot_resolves_native_id():
    channel, model = _rows()
    snapshot = _snapshot([channel], [model])

    target = routing._cached_call_model_target(snapshot, NATIVE)

    assert target.kind == "direct"
    assert target.name == NATIVE
    assert len(target.candidates) == 1
    assert target.candidates[0].model.id == "m1"


def test_cached_snapshot_rejects_disabled_native_id():
    channel, model = _rows(enabled=False)
    snapshot = _snapshot([channel], [model])

    with pytest.raises(routing.OpenAIError) as caught:
        routing._cached_call_model_target(snapshot, NATIVE)

    assert caught.value.status == 404
    assert f"nvidia-nim/{NATIVE}" in caught.value.message


def test_cached_snapshot_rejects_non_openai_protocol():
    channel, model = _rows(protocol="anthropic_messages")
    snapshot = _snapshot([channel], [model])

    with pytest.raises(routing.OpenAIError) as caught:
        routing._cached_call_model_target(snapshot, NATIVE)

    assert caught.value.status == 404
