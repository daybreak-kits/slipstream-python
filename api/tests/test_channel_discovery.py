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


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


RULES = {
    "request_headers": [
        {"mode": "set", "path": "X-Codegen-Client-Name", "value": "qagent"}
    ]
}


async def _channel(client, headers, url: str) -> dict:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "模型发现渠道",
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
            "rewrite_rules": RULES,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_discover_models_uses_draft_rules_without_saving(client, upstream):
    headers = await _token(client)

    response = await client.post(
        "/api/channels/discover-models",
        headers=headers,
        json={
            "connection_url": f"{upstream.base_url}/required-header#",
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "rewrite_rules": RULES,
        },
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "ok"
    assert body["protocol"] == "openai_chat"
    assert [item["upstream_model"] for item in body["models"]] == [
        "fake-model",
        "fake-model-2",
    ]
    assert all(item["already_added"] is False for item in body["models"])
    requests = upstream.find("GET", "/required-header")
    assert requests[-1]["headers"].get("X-Codegen-Client-Name") == "qagent"


async def test_discover_models_reuses_stored_key_and_marks_existing(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/required-header#")
    created = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model"},
    )
    assert created.status_code == 201, created.text

    response = await client.post(
        "/api/channels/discover-models",
        headers=headers,
        json={"channel_id": channel["id"]},
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "ok"
    flags = {item["upstream_model"]: item["already_added"] for item in body["models"]}
    assert flags == {"fake-model": True, "fake-model-2": False}


async def test_bulk_add_models_skips_duplicates(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/required-header#")

    first = await client.post(
        f"/api/channels/{channel['id']}/models/bulk",
        headers=headers,
        json={"upstream_models": ["fake-model", "fake-model-2", "fake-model"]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 2
    # The request model de-duplicates names before the service sees them.
    assert first.json()["skipped"] == 0
    assert len(first.json()["items"]) == 2

    second = await client.post(
        f"/api/channels/{channel['id']}/models/bulk",
        headers=headers,
        json={"upstream_models": ["fake-model", "fake-model-2"]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 2

    listed = await client.get(
        f"/api/channels/{channel['id']}/models", headers=headers
    )
    assert listed.json()["total"] == 2


async def test_discover_models_blames_the_missing_key_not_an_invalid_one(client, upstream):
    """没有密钥时上游拒绝的是匿名请求，不能提示「请检查渠道密钥」。"""
    headers = await _token(client)

    response = await client.post(
        "/api/channels/discover-models",
        headers=headers,
        json={
            "connection_url": f"{upstream.base_url}/unauthorized#",
            "protocol": "openai_chat",
        },
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "error"
    assert body["http_status"] == 401
    assert "本次请求未携带渠道密钥" in body["message"]
    assert "请检查渠道密钥" not in body["message"]
    # 上游原话要留在消息里，管理员才看得到上游到底嫌什么
    assert "invalid api key" in body["message"]


async def test_discover_models_reports_a_rejected_key_when_one_was_sent(client, upstream):
    headers = await _token(client)

    response = await client.post(
        "/api/channels/discover-models",
        headers=headers,
        json={
            "connection_url": f"{upstream.base_url}/unauthorized#",
            "api_key": "wrong-but-present-key",
            "protocol": "openai_chat",
        },
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "error"
    assert body["http_status"] == 401
    assert "密钥被上游拒绝" in body["message"]
    assert "未携带渠道密钥" not in body["message"]
    assert "invalid api key" in body["message"]
