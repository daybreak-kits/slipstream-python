from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx
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


async def _channel(
    client,
    headers,
    url: str,
    *,
    protocol: str | None = None,
    api_key: str = "channel-secret-key",
    rewrite_rules: dict | None = None,
) -> dict:
    payload: dict = {
        "name": "探测渠道",
        "connection_url": url,
        "api_key": api_key,
        "enabled": True,
    }
    if rewrite_rules is not None:
        payload["rewrite_rules"] = rewrite_rules
    if protocol is not None:
        payload["protocol"] = protocol
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, upstream_model: str = "fake-model") -> dict:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": upstream_model},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_probe_detects_openai_and_persists_result(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["protocol"] == "openai_chat"
    assert body["auth_style"] == "bearer"
    assert body["latency_ms"] >= 0
    assert body["message"]
    assert body["probed_at"] is not None
    assert body["mode"] == "deep"
    assert "channel-secret-key" not in json.dumps(body, ensure_ascii=False)

    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.json()["protocol"] == "openai_chat"
    assert detail.json()["protocol_source"] == "auto"
    assert detail.json()["auth_style"] == "bearer"

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    stored = listed.json()["items"][0]
    assert stored["probe_status"] == "ok"
    assert stored["probe_latency_ms"] == body["latency_ms"]
    assert stored["probe_message"] == body["message"]
    assert stored["probed_at"] == body["probed_at"]


async def test_deep_probe_uses_max_tokens_16_and_short_prompt(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    chats = upstream.find("POST", "/openai/v1/chat/completions")
    assert chats
    payload = chats[-1]["json"]
    assert payload is not None
    assert payload["max_tokens"] == 16
    assert payload["model"] == "fake-model"
    assert payload["stream"] is False
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["content"] == "ping"


async def test_deep_probe_with_ok_history_skips_models_scan(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    first = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    assert len(upstream.find("GET", "/openai/v1/models")) == 1

    second = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "ok"
    # Fast path: the ok history validates protocol/auth, so the second probe
    # calls the model directly without re-scanning /models.
    assert len(upstream.find("GET", "/openai/v1/models")) == 1
    assert len(upstream.find("POST", "/openai/v1/chat/completions")) == 2


async def test_deep_probe_fast_path_failure_falls_back_to_detection(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])
    first = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers, json={"mode": "deep"}
    )
    assert first.json()["status"] == "ok"

    patch = await client.patch(
        f"/api/channels/{channel['id']}",
        headers=headers,
        json={"connection_url": f"{upstream.base_url}/openai-empty-chat"},
    )
    assert patch.status_code == 200, patch.text

    second = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )
    body = second.json()
    assert body["status"] == "error"
    # The stored pair stopped working: full detection re-ran before giving up.
    assert len(upstream.find("GET", "/openai-empty-chat/v1/models")) == 1
    assert len(upstream.find("POST", "/openai-empty-chat/v1/chat/completions")) == 2


async def test_deep_probe_applies_request_header_and_body_rules(client, upstream):
    headers = await _token(client)
    rules = {
        "request_headers": [
            {"mode": "set", "path": "X-Codegen-Client-Name", "value": "qagent"}
        ],
            "request_body": [{"mode": "create", "path": "client", "value": "qagent"}],
    }
    channel = await _channel(
        client,
        headers,
        f"{upstream.base_url}/required-body#",
        rewrite_rules=rules,
    )
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "ok"
    chats = upstream.find("POST", "/required-body")
    assert chats
    assert chats[-1]["headers"].get("X-Codegen-Client-Name") == "qagent"
    assert chats[-1]["json"]["client"] == "qagent"


async def test_shallow_probe_only_hits_models(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "shallow"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["protocol"] == "openai_chat"
    assert not upstream.find("POST", "/openai/v1/chat/completions")


async def test_test_connection_auto_detects_anthropic_protocol(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/anthropic")

    response = await client.post(
        f"/api/channels/{channel['id']}/test-connection", headers=headers
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["protocol"] == "anthropic_messages"
    assert body["auth_style"] == "x-api-key"
    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.json()["protocol"] == "anthropic_messages"
    assert detail.json()["protocol_source"] == "auto"


async def test_probe_detects_anthropic_with_x_api_key(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/anthropic")
    model = await _model(client, headers, channel["id"], upstream_model="claude-fake")

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["protocol"] == "anthropic_messages"
    assert body["auth_style"] == "x-api-key"

    chats = upstream.find("POST", "/anthropic/v1/messages")
    assert chats
    assert chats[-1]["headers"].get("anthropic-version") == "2023-06-01"
    assert chats[-1]["json"]["max_tokens"] == 16

    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.json()["protocol"] == "anthropic_messages"
    assert detail.json()["auth_style"] == "x-api-key"


async def test_probe_detects_gemini_with_query_key(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/gemini/v1beta")
    model = await _model(client, headers, channel["id"], upstream_model="gemini-fake")

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["protocol"] == "gemini"
    assert body["auth_style"] == "query-key"

    chats = [
        item
        for item in upstream.requests
        if item["method"] == "POST" and ":generateContent" in item["path"]
    ]
    assert chats
    assert chats[-1]["query"].get("key") == ["channel-secret-key"]
    assert chats[-1]["json"]["generationConfig"]["maxOutputTokens"] == 16
    assert chats[-1]["json"]["contents"][0]["parts"][0]["text"] == "ping"

    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.json()["protocol"] == "gemini"
    assert detail.json()["auth_style"] == "query-key"


async def test_deep_probe_falls_back_to_chat_when_models_endpoint_missing(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai-nomodels")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["protocol"] == "openai_chat"
    assert body["auth_style"] == "bearer"
    assert upstream.find("POST", "/openai-nomodels/v1/chat/completions")


async def test_shallow_probe_fails_when_models_endpoint_missing(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai-nomodels")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "shallow"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "error"
    assert not upstream.find("POST", "/openai-nomodels/v1/chat/completions")


async def test_auth_failure_returns_error_and_never_leaks_key(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client,
        headers,
        f"{upstream.base_url}/openai",
        api_key="wrong-secret-key",
    )
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "error"
    assert "401" in body["message"]
    assert "wrong-secret-key" not in json.dumps(body, ensure_ascii=False)

    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["protocol_source"] == "auto"


async def test_probe_timeout_is_persisted_and_channel_stays_saved(
    client, upstream, monkeypatch
):
    from l05_service import probe

    monkeypatch.setattr(probe, "probe_timeout_seconds", lambda: 1)
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/slow#")
    model = await _model(client, headers, channel["id"])

    started = time.perf_counter()
    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "shallow"},
    )
    elapsed = time.perf_counter() - started

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "timeout"
    assert elapsed >= 2.0
    assert body["latency_ms"] >= 1000
    assert "超时" in body["message"]

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json()["items"][0]["probe_status"] == "timeout"
    assert listed.json()["items"][0]["probed_at"] is not None

    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "探测渠道"


async def test_all_protocols_failing_returns_structured_error(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/all-broken")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "error"
    assert body["protocol"] in {"openai_chat", "anthropic_messages", "gemini"}
    assert body["message"]

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json()["items"][0]["probe_status"] == "error"
    detail = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert detail.status_code == 200


async def test_probe_does_not_touch_breaker_counters(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/all-broken")
    model = await _model(client, headers, channel["id"])

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, model["id"])
        assert stored is not None
        stored.consecutive_failures = 4
        stored.consecutive_trip_count = 2
        stored.escalation_trip_count = 1
        stored.health_status = "normal"
        await session.commit()

    await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, model["id"])
        assert stored is not None
        assert stored.probe_status == "error"
        assert stored.consecutive_failures == 4
        assert stored.consecutive_trip_count == 2
        assert stored.escalation_trip_count == 1
        assert stored.health_status == "normal"
        assert stored.opened_at is None
        assert stored.cooldown_until is None


async def test_probe_all_reports_counts_and_refreshes_each_model(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    for name in ("fake-model", "fake-model-2", "third-model"):
        await _model(client, headers, channel["id"], upstream_model=name)

    response = await client.post(
        f"/api/channels/{channel['id']}/probe-all",
        headers=headers,
        json={"mode": "shallow"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 3
    assert body["ok"] == 3
    assert body["timeout"] == 0
    assert body["error"] == 0
    assert body["unknown"] == 0
    assert len(body["items"]) == 3
    assert {item["status"] for item in body["items"]} == {"ok"}
    assert body["protocol"] == "openai_chat"
    assert body["auth_style"] == "bearer"

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert {item["probe_status"] for item in listed.json()["items"]} == {"ok"}


async def test_probe_all_reports_failure_counts(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, f"{upstream.base_url}/openai", api_key="wrong-key"
    )
    await _model(client, headers, channel["id"], upstream_model="fake-model")
    await _model(client, headers, channel["id"], upstream_model="fake-model-2")

    response = await client.post(
        f"/api/channels/{channel['id']}/probe-all",
        headers=headers,
        json={"mode": "shallow"},
    )

    body = response.json()
    assert body["total"] == 2
    assert body["ok"] == 0
    assert body["error"] == 2
    assert body["unknown"] == 0


async def test_probe_all_with_no_models_returns_empty_summary(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")

    response = await client.post(
        f"/api/channels/{channel['id']}/probe-all",
        headers=headers,
        json={"mode": "deep"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": channel["id"],
        "mode": "deep",
        "total": 0,
        "skipped": 0,
        "cached": False,
        "ok": 0,
        "timeout": 0,
        "error": 0,
        "unknown": 0,
        "items": [],
        "protocol": "openai_chat",
        "auth_style": "bearer",
        "protocol_source": "auto",
    }


async def test_unknown_stays_unknown_until_first_probe(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json()["items"][0]["probe_status"] == "unknown"
    assert listed.json()["items"][0]["probed_at"] is None

    await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"mode": "shallow"},
    )

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json()["items"][0]["probe_status"] == "ok"
    assert listed.json()["items"][0]["probed_at"] is not None


async def test_probe_unknown_model_id_returns_404(client):
    headers = await _token(client)

    response = await client.post(
        "/api/channel-models/CM0000000000000000000000FF/probe",
        headers=headers,
        json={"mode": "deep"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def _log_items(client, headers) -> list[dict]:
    response = await client.get("/api/logs/recent?limit=50", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_deep_probe_writes_usage_log(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers, json={"mode": "deep"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    items = [
        item
        for item in await _log_items(client, headers)
        if item.get("source") == "probe" and item["channel_name"] == channel["name"]
    ]
    assert len(items) == 1
    item = items[0]
    assert item["requested_model"].startswith(f"{channel['slug']}/")
    assert item["upstream_model"] == "fake-model"
    assert item["is_stream"] is False
    assert item["http_status"] == 200
    # ssot §5.5: probe rows never book tokens, even when upstream reports usage.
    assert item["usage_estimated"] is True
    assert item["prompt_tokens"] == 0
    assert item["completion_tokens"] == 0
    assert item["cached_tokens"] == 0
    assert item["cache_ratio"] == 0.0
    assert item["cost_usd"] == 0.0
    assert item["total_ms"] >= 0
    assert item["ttft_ms"] is None
    assert item["ttfa_ms"] is None
    assert item["ttlt_ms"] is None

    detail = (await client.get(f"/api/logs/{item['request_id']}", headers=headers)).json()
    assert detail["source"] == "probe"
    attempt = detail["attempts"][0]
    assert attempt["status"] == "success"
    assert attempt["channel_slug"] == channel["slug"]
    assert attempt["upstream_model"] == "fake-model"


async def test_channel_test_connection_writes_usage_log(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, f"{upstream.base_url}/openai", protocol="openai_chat"
    )

    response = await client.post(
        f"/api/channels/{channel['id']}/test-connection", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    items = [
        item
        for item in await _log_items(client, headers)
        if item.get("source") == "probe" and item["channel_name"] == channel["name"]
    ]
    assert len(items) == 1
    item = items[0]
    assert item["requested_model"] is None
    assert item["is_stream"] is False
    assert item["http_status"] == 200
    assert item["usage_estimated"] is True
    assert item["prompt_tokens"] == 0
    assert item["completion_tokens"] == 0
    assert item["cached_tokens"] == 0
    assert item["cost_usd"] == 0.0

    detail = (await client.get(f"/api/logs/{item['request_id']}", headers=headers)).json()
    assert detail["source"] == "probe"
    assert detail["requested_model"] is None
    assert detail["attempts"][0]["status"] == "success"


async def test_draft_test_connection_does_not_write_usage_log(client, upstream):
    headers = await _token(client)

    response = await client.post(
        "/api/channels/test-connection",
        headers=headers,
        json={
            "connection_url": upstream.base_url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    items = [item for item in await _log_items(client, headers) if item.get("source") == "probe"]
    assert items == []


async def test_deep_probe_without_usage_marks_estimated(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai-no-usage")
    model = await _model(client, headers, channel["id"])

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers, json={"mode": "deep"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    items = [
        item
        for item in await _log_items(client, headers)
        if item.get("source") == "probe" and item["channel_name"] == channel["name"]
    ]
    assert len(items) == 1
    item = items[0]
    assert item["http_status"] == 200
    assert item["prompt_tokens"] == 0
    assert item["completion_tokens"] == 0
    assert item["cached_tokens"] == 0
    assert item["cache_ratio"] == 0.0
    assert item["cost_usd"] == 0.0
    assert item["usage_estimated"] is True


def test_extract_chat_usage_reads_every_protocol():
    from l05_service.probe import _extract_chat_usage

    openai = _extract_chat_usage(
        "openai_chat",
        {
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 2,
                "total_tokens": 9,
                "prompt_tokens_details": {"cached_tokens": 3},
            }
        },
    )
    assert openai is not None
    assert (openai.prompt_tokens, openai.completion_tokens, openai.cached_tokens) == (7, 2, 3)

    anthropic = _extract_chat_usage(
        "anthropic_messages",
        {
            "usage": {
                "input_tokens": 5,
                "output_tokens": 2,
                "cache_read_input_tokens": 4,
                "cache_creation_input_tokens": 1,
            }
        },
    )
    assert anthropic is not None
    assert (anthropic.prompt_tokens, anthropic.completion_tokens) == (10, 2)
    assert anthropic.cached_tokens == 4

    gemini = _extract_chat_usage(
        "gemini",
        {
            "usageMetadata": {
                "promptTokenCount": 6,
                "candidatesTokenCount": 2,
                "cachedContentTokenCount": 1,
                "totalTokenCount": 8,
            }
        },
    )
    assert gemini is not None
    assert (gemini.prompt_tokens, gemini.completion_tokens, gemini.cached_tokens) == (6, 2, 1)
    assert gemini.total_tokens == 8

    assert _extract_chat_usage("openai_chat", {"choices": []}) is None
    assert _extract_chat_usage("openai_chat", {}) is None


async def _patch_model(app, model_id: str, **values) -> None:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        for key, value in values.items():
            setattr(model, key, value)
        await session.commit()


async def _stored_health(app, model_id: str) -> dict:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        return {
            "health_status": model.health_status,
            "consecutive_failures": int(model.consecutive_failures or 0),
            "consecutive_trip_count": int(model.consecutive_trip_count or 0),
            "escalation_trip_count": int(model.escalation_trip_count or 0),
            "cooldown_until": model.cooldown_until,
            "fault_until": model.fault_until,
        }


async def _catalog_entry(client, headers, model_id: str) -> dict:
    response = await client.get("/api/catalog", headers=headers)
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["kind"] == "direct" and item["id"] == model_id:
            return item
    raise AssertionError("catalog entry missing")


async def _open_breaker(app, model_id: str, *, cooldown_until: int) -> None:
    await _patch_model(
        app,
        model_id,
        health_status="open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        escalation_trip_count=2,
        opened_at=cooldown_until - 60_000,
        cooldown_until=cooldown_until,
    )


async def test_deep_probe_success_recovers_open_breaker(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])
    # Expired cooldown: the catalog derives half_open from it.
    await _open_breaker(app, model["id"], cooldown_until=int(time.time() * 1000) - 1_000)

    before = await _catalog_entry(client, headers, model["id"])
    # The expired cooldown derives half_open, which the UI reads directly.
    assert before["health_status"] == "half_open"
    assert before["visible_status"] == "half_open"

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers, json={"mode": "deep"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    stored = await _stored_health(app, model["id"])
    assert stored["health_status"] == "normal"
    assert stored["consecutive_failures"] == 0
    assert stored["consecutive_trip_count"] == 0
    # Half-open success decrements escalation instead of clearing it.
    assert stored["escalation_trip_count"] == 1
    assert stored["cooldown_until"] is None
    assert stored["fault_until"] is None

    after = await _catalog_entry(client, headers, model["id"])
    assert after["health_status"] == "normal"
    assert after["visible_status"] == "normal"

    # The recovery must not swallow the usage log row.
    items = [
        item
        for item in await _log_items(client, headers)
        if item.get("source") == "probe" and item["channel_name"] == channel["name"]
    ]
    assert len(items) == 1


async def test_deep_probe_failure_keeps_open_breaker(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/broken")
    model = await _model(client, headers, channel["id"])
    future = int(time.time() * 1000) + 60_000
    await _open_breaker(app, model["id"], cooldown_until=future)

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers, json={"mode": "deep"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] in {"error", "timeout"}

    stored = await _stored_health(app, model["id"])
    assert stored["health_status"] == "open"
    assert stored["consecutive_failures"] == 5
    assert stored["consecutive_trip_count"] == 1
    assert stored["escalation_trip_count"] == 2
    assert stored["cooldown_until"] == future


async def test_connection_test_success_does_not_recover_open_breaker(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai", protocol="openai_chat")
    model = await _model(client, headers, channel["id"])
    future = int(time.time() * 1000) + 60_000
    await _open_breaker(app, model["id"], cooldown_until=future)

    response = await client.post(
        f"/api/channels/{channel['id']}/test-connection", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    # /models reachability proves address + key, not that this model answers.
    stored = await _stored_health(app, model["id"])
    assert stored["health_status"] == "open"
    assert stored["cooldown_until"] == future


async def test_probes_reuse_one_process_wide_client(client, upstream, monkeypatch):
    from l05_service.llm import gateway

    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(client, headers, channel["id"])

    created: list[httpx.AsyncClient] = []
    real_client = httpx.AsyncClient

    class RecordingClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", RecordingClient)
    monkeypatch.setattr(gateway, "_probe_client", None)
    try:
        for _ in range(2):
            response = await client.post(
                f"/api/channel-models/{model['id']}/probe",
                headers=headers,
                json={"mode": "deep"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "ok"

        assert len(created) == 1
        assert gateway._probe_client is created[0]
        assert not created[0].is_closed
    finally:
        monkeypatch.setattr(gateway, "_probe_client", None)
        if created and not created[0].is_closed:
            await created[0].aclose()
