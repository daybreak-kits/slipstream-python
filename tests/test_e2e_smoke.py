from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


async def test_end_to_end_smoke(client, upstream: FakeUpstreamServer) -> None:
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "冒烟渠道",
            "slug": "smoke",
            "connection_url": f"{upstream.base_url}/v1",
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]

    model = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "smoke-model"},
    )
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]

    probe = await client.post(
        f"/api/channel-models/{model_id}/probe", headers=headers, json={"mode": "shallow"}
    )
    assert probe.status_code == 200, probe.text
    assert probe.json()["status"] in {"ok", "error", "timeout"}


    combo = await client.post(
        "/api/combos",
        headers=headers,
        json={
            "name": "smoke-combo",
            "enabled": True,
            "targets": [
                {"target_type": "channel_model", "channel_model_id": model_id, "priority": 1}
            ],
        },
    )
    assert combo.status_code in (200, 201), combo.text


    key = await client.post("/api/keys", headers=headers, json={"label": "smoke"})
    assert key.status_code == 201
    auth = {"Authorization": f"Bearer {key.json()['key']}"}

    models = await client.get("/v1/models", headers=auth)
    assert models.status_code == 200
    assert "smoke/shared" not in {item["id"] for item in models.json()["data"]}
    assert "smoke-combo" in {item["id"] for item in models.json()["data"]}

    chat = await client.post(
        "/v1/chat/completions",
        headers=auth,
        json={
            "model": "smoke-combo",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["choices"][0]["message"]["content"]

    logs = await client.get("/api/logs", headers=headers)
    assert logs.status_code == 200
    assert logs.json()["total"] >= 1

    summary = await client.get("/api/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["requests"] >= 1
