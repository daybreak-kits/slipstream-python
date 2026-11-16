from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from l02_infra.crypto import hash_api_key
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


async def _channel(client, headers, url: str, **overrides) -> dict:
    payload = {
        "name": "直连测试渠道",
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
    payload = {"upstream_model": "fake-model", **overrides}
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers, label: str = "ci") -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str, **overrides) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    body.update(overrides)
    return body


async def test_create_key_returns_plaintext_once_and_stores_only_hash(client, app):
    headers = await _token(client)

    created = await _key(client, headers, label="laptop")

    assert created["label"] == "laptop"
    assert created["key"].startswith("sk-")
    assert created["prefix"] == created["key"][:10]
    assert created["enabled"] is True
    assert created["last_used_at"] is None
    assert "key_hash" not in created

    listing = await client.get("/api/keys", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == created["id"]
    assert item["prefix"] == created["prefix"]
    assert "key" not in item
    assert "key_hash" not in item
    assert created["key"] not in json.dumps(body, ensure_ascii=False)

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ApiKeyORM, created["id"])
        assert stored is not None
        assert stored.key_hash == hash_api_key(created["key"])
        assert stored.key_hash != created["key"]
        user = await session.get(orm.UserORM, stored.user_id)
        assert user is not None
        assert user.username


async def test_create_key_rejects_duplicate_label(client):
    headers = await _token(client)
    await _key(client, headers, label="dup")

    response = await client.post("/api/keys", headers=headers, json={"label": "dup"})

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


async def test_keys_require_admin_session(client):
    assert (await client.get("/api/keys")).status_code == 401
    assert (await client.post("/api/keys", json={"label": "x"})).status_code == 401


async def test_reset_key_rotates_secret_and_revokes_old(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(
        client, headers, channel["id"], upstream_model="fake-model", call_model="gpt-test"
    )
    key = await _key(client, headers, label="rotate-me")

    first = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert first.status_code == 200, first.text

    response = await client.post(f"/api/keys/{key['id']}/reset", headers=headers)
    assert response.status_code == 200, response.text
    rotated = response.json()
    assert rotated["id"] == key["id"]
    assert rotated["label"] == "rotate-me"
    assert rotated["key"].startswith("sk-")
    assert rotated["key"] != key["key"]
    assert rotated["prefix"] == rotated["key"][:10]
    assert rotated["prefix"] != key["prefix"]
    assert rotated["last_used_at"] is None

    stale = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert stale.status_code == 401

    fresh = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {rotated['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert fresh.status_code == 200, fresh.text

    revealed = await client.get(f"/api/keys/{key['id']}/secret", headers=headers)
    assert revealed.status_code == 200
    assert revealed.json()["key"] == rotated["key"]

    unauth = await client.post(f"/api/keys/{key['id']}/reset")
    assert unauth.status_code == 401

    missing = await client.post("/api/keys/does-not-exist/reset", headers=headers)
    assert missing.status_code == 404


async def test_reveal_key_returns_stored_plaintext(client, app):
    headers = await _token(client)
    key = await _key(client, headers, label="reveal-me")

    revealed = await client.get(f"/api/keys/{key['id']}/secret", headers=headers)
    assert revealed.status_code == 200
    assert revealed.json() == {"key": key["key"]}

    assert (await client.get(f"/api/keys/{key['id']}/secret")).status_code == 401
    missing = await client.get("/api/keys/nope/secret", headers=headers)
    assert missing.status_code == 404

    # Rows created before key_enc only kept a hash; they must reset first.
    async with app.state.session_factory() as session:
        stored = await session.get(orm.ApiKeyORM, key["id"])
        assert stored is not None
        stored.key_enc = ""
        await session.commit()
    legacy = await client.get(f"/api/keys/{key['id']}/secret", headers=headers)
    assert legacy.status_code == 409
    assert legacy.json()["code"] == "CONFLICT"


async def test_direct_chat_success_forwards_params_and_writes_log(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(
        client,
        headers,
        channel["id"],
        upstream_model="fake-model",
        call_model="gpt-test",
        input_price_per_1m=2.0,
        output_price_per_1m=10.0,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(
            f"{channel['slug']}/gpt-test", temperature=0.2, max_tokens=32
        ),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model"] == f"{channel['slug']}/gpt-test"
    assert payload["choices"][0]["message"]["content"] == "OK"
    assert payload["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
    }
    assert response.headers["X-Request-ID"]

    posted = upstream.find("POST", "/v1/chat/completions")
    assert len(posted) == 1
    request = posted[0]
    assert request["json"]["model"] == "fake-model"
    assert request["json"]["messages"] == [{"role": "user", "content": "hello"}]
    assert request["json"]["temperature"] == 0.2
    assert request["json"]["max_tokens"] == 32
    # e3402bd: the gateway emits every self-built header in lowercase.
    assert request["headers"].get("authorization") == "Bearer channel-secret-key"

    keys = (await client.get("/api/keys", headers=headers)).json()
    assert keys["items"][0]["last_used_at"] is not None

    logs = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    assert logs["total"] == 1
    log = logs["items"][0]
    assert log["request_id"] == response.headers["X-Request-ID"]
    assert log["user_id"]
    assert log["api_key_id"] == key["id"]
    assert log["api_key_label"] == "ci"
    assert log["requested_model"] == f"{channel['slug']}/gpt-test"
    assert log["channel_name"] == channel["name"]
    assert log["upstream_model"] == "fake-model"
    assert log["response_model"] == "fake-model"
    assert log["http_status"] == 200
    assert log["is_stream"] is False
    assert log["prompt_tokens"] == 3
    assert log["completion_tokens"] == 1
    assert log["cached_tokens"] == 0
    assert log["cost_usd"] == pytest.approx(0.000016)
    assert log["ttft_ms"] is None
    assert log["throughput_tps"] is None
    assert log["usage_estimated"] is False
    assert log["total_ms"] >= 0
    assert log["attempts"][0]["status"] == "success"
    assert log["attempts"][0]["upstream_model"] == "fake-model"
    assert log["created_at"] > 0
    assert key["key"] not in json.dumps(logs, ensure_ascii=False)


async def test_direct_chat_accepts_admin_session_without_api_key(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="gpt-test")

    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "OK"

    logs = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    assert logs["total"] == 1
    log = logs["items"][0]
    assert log["api_key_id"] == ""
    assert log["api_key_label"] == "playground"
    assert log["user_id"]

    foreign = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer junk-not-jwt-not-key"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert foreign.status_code == 401


async def test_direct_chat_rejects_missing_and_unknown_key(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="gpt-test")

    missing = await client.post(
        "/v1/chat/completions", json=_chat_body(f"{channel['slug']}/gpt-test")
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "INVALID_API_KEY"
    assert missing.json()["error"]["request_id"]

    unknown = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-not-a-real-key"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "INVALID_API_KEY"

    assert upstream.find("POST", "/v1/chat/completions") == []


async def test_direct_chat_rejects_disabled_key(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ApiKeyORM, key["id"])
        assert stored is not None
        stored.enabled = False
        await session.commit()

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_API_KEY"
    assert upstream.find("POST", "/v1/chat/completions") == []


async def test_direct_chat_unknown_model_is_404_and_logged(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)

    for model in (f"{channel['slug']}/missing", "missing-bare"):
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key['key']}"},
            json=_chat_body(model),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"

    assert upstream.find("POST", "/v1/chat/completions") == []

    logs = (await client.get("/api/logs/recent?limit=10", headers=headers)).json()
    assert logs["total"] == 2
    for log in logs["items"]:
        assert log["http_status"] == 404
        assert log["error_code"] == "MODEL_NOT_FOUND"
        assert log["prompt_tokens"] == 0
        assert log["cost_usd"] == 0


@pytest.mark.parametrize(
    ("path", "upstream_status", "expected_code"),
    [
        ("/unauthorized", 401, "UPSTREAM_AUTH_FAILED"),
        ("/broken", 500, "UPSTREAM_ERROR"),
    ],
)
async def test_direct_chat_upstream_4xx_5xx_maps_to_502(
    client, upstream, path, upstream_status, expected_code
):
    # 上游 401 属认证失败档（错误三档），非泛化 UPSTREAM_ERROR
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url + path)
    await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 502
    error = response.json()["error"]
    # 401 maps to tier=permanent auth since the P0-C error-taxonomy change.
    expected_code = "UPSTREAM_AUTH_FAILED" if path == "/unauthorized" else "UPSTREAM_ERROR"
    assert error["code"] == expected_code
    assert error["type"] == "server_error"
    assert error["request_id"] == response.headers["X-Request-ID"]

    logs = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    log = logs["items"][0]
    assert log["http_status"] == 502
    assert log["error_code"] == expected_code
    assert log["attempts"][0]["status"] == "error"
    assert log["attempts"][0]["error_code"] in {"auth", "upstream"}
    assert log["prompt_tokens"] == 0


def _gateway_mock(monkeypatch, handler) -> None:
    """Route gateway upstream calls through MockTransport: no port, no network."""
    from l05_service.llm import gateway

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gateway, "_get_client", lambda: client)
    # Force the httpx arm; the litellm arm would ignore the patched client.
    monkeypatch.setattr(gateway, "_use_litellm", lambda channel: False)


def _completion_without_usage(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-nousage",
            "object": "chat.completion",
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "a short reply from upstream"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _completion_with_usage(request: httpx.Request) -> httpx.Response:
    payload = _completion_without_usage(request).json()
    payload["id"] = "chatcmpl-usage"
    payload["usage"] = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    return httpx.Response(200, json=payload)


async def _non_stream_log(client, monkeypatch, handler):
    headers = await _token(client)
    channel = await _channel(client, headers, "http://upstream.test/v1")
    await _model(
        client,
        headers,
        channel["id"],
        call_model="gpt-test",
        input_price_per_1m=2.0,
        output_price_per_1m=8.0,
    )
    key = await _key(client, headers)
    _gateway_mock(monkeypatch, handler)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )
    assert response.status_code == 200, response.text
    logs = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    return logs["items"][0]


async def test_non_stream_missing_usage_is_estimated_and_flagged(client, monkeypatch):
    """契约：httpx 臂的非流式上游不返回 usage 时，记录行必须按估算词元记账并
    标 usage_estimated=True；否则该请求被当成 0 词元（少算计费、命中率记 0）。"""
    log = await _non_stream_log(client, monkeypatch, _completion_without_usage)

    assert log["usage_estimated"] is True
    assert log["prompt_tokens"] > 0
    assert log["completion_tokens"] > 0
    assert log["cost_usd"] > 0


async def test_non_stream_upstream_usage_is_kept_verbatim(client, monkeypatch):
    """契约：上游给了 usage 时原样记账、不加 estimated 标记（防过度估算）。"""
    log = await _non_stream_log(client, monkeypatch, _completion_with_usage)

    assert log["usage_estimated"] is False
    assert (log["prompt_tokens"], log["completion_tokens"]) == (3, 1)


async def test_direct_chat_timeout_is_504(client, upstream, monkeypatch):
    from types import SimpleNamespace

    from l05_service import chat

    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(request_timeout_s=1))
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url + "/slow")
    await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert response.json()["error"]["type"] == "server_error"

    logs = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    assert logs["items"][0]["http_status"] == 504
    assert logs["items"][0]["error_code"] == "UPSTREAM_TIMEOUT"


async def test_direct_chat_pseudo_success_empty_content_is_502(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url + "/openai-empty-chat")
    await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EMPTY_RESPONSE"

    logs = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    assert logs["items"][0]["error_code"] == "EMPTY_RESPONSE"
    assert logs["items"][0]["http_status"] == 502


async def test_direct_chat_disabled_model_is_404(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, model["id"])
        assert stored is not None
        stored.enabled = False
        await session.commit()

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/gpt-test"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


async def test_v1_models_lists_direct_slug_call_model(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="gpt-a")
    await _model(client, headers, channel["id"], upstream_model="fake-model-2", call_model="gpt-b")
    disabled = await _model(client, headers, channel["id"], call_model="gpt-c")
    key = await _key(client, headers)

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, disabled["id"])
        assert stored is not None
        stored.enabled = False
        await session.commit()

    response = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key['key']}"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    ids = [item["id"] for item in data]
    assert ids == [
        f"{channel['slug']}/gpt-a",
        f"{channel['slug']}/gpt-b",
        "gpt-a",
        "gpt-b",
    ]
    # kind 只有 direct/combo：隐式分组对外也是 combo，来源是 origin=auto。
    assert [item["kind"] for item in data] == ["direct", "direct", "combo", "combo"]
    assert [item["origin"] for item in data] == [None, None, "auto", "auto"]
    assert disabled["call_model"] not in ids

    unauthorized = await client.get("/v1/models")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "INVALID_API_KEY"


async def test_catalog_lists_enabled_direct_models_for_admin(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(
        client,
        headers,
        channel["id"],
        upstream_model="fake-model",
        call_model="gpt-test",
        input_price_per_1m=1.5,
    )

    response = await client.get("/api/catalog", headers=headers)

    assert response.status_code == 200
    items = response.json()["items"]
    by_name = {item["name"]: item for item in items}
    assert len(items) == 2
    direct = by_name[f"{channel['slug']}/gpt-test"]
    assert direct["kind"] == "direct"
    assert direct["channel_slug"] == channel["slug"]
    assert direct["call_model"] == "gpt-test"
    assert direct["input_price_per_1m"] == 1.5
    # The same call_model also forms an implicit group bare name in the catalog.
    group = by_name["gpt-test"]
    assert group["kind"] == "combo"
    assert group["origin"] == "auto"
    assert group["status"] == "enabled"


async def test_recent_logs_requires_admin(client):
    assert (await client.get("/api/logs/recent")).status_code == 401
