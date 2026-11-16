from __future__ import annotations

import json
import socket
from collections.abc import Iterator

import pytest

from sqlalchemy.exc import IntegrityError

from l04_repo import orm, repo
from l03_domain.naming import slug_from_url
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_payload(url: str, **overrides) -> dict:
    payload = {
        "name": "测试渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    return payload


async def _create(client, headers, url: str, **overrides):
    response = await client.post(
        "/api/channels", headers=headers, json=_create_payload(url, **overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def test_requires_admin_session(client):
    response = await client.get("/api/channels")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_create_channel_derives_slug_and_endpoint_preview(client, upstream):
    headers = await _token(client)

    body = await _create(client, headers, f"{upstream.base_url}/v1")

    assert body["slug"].startswith("ch-") or body["slug"][0].isalpha()
    assert body["slug"] == slug_from_url(f"{upstream.base_url}/v1")
    assert body["name"] == "测试渠道"
    assert body["protocol"] == "openai_chat"
    assert body["api_key_set"] is True
    assert body["api_key_masked"].endswith("key")
    assert "channel-secret-key" not in json.dumps(body, ensure_ascii=False)
    assert body["endpoint_preview"] == f"{upstream.base_url}/v1/chat/completions"
    assert body["models_endpoint_preview"] == f"{upstream.base_url}/v1/models"
    assert body["model_count"] == 0
    assert body["connection_status"] == "unknown"


async def test_create_channel_with_explicit_slug_and_reject_invalid_or_duplicate(client, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url, slug="slipstream")
    assert created["slug"] == "slipstream"

    duplicate = await client.post(
        "/api/channels",
        headers=headers,
        json=_create_payload(upstream.base_url, slug="slipstream", name="重复"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CONFLICT"

    invalid = await client.post(
        "/api/channels",
        headers=headers,
        json=_create_payload(upstream.base_url, slug="1-bad-slug"),
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_REQUEST"


async def test_auto_slug_collisions_get_numeric_suffix(client, upstream):
    headers = await _token(client)

    first = await _create(client, headers, upstream.base_url, name="一")
    second = await _create(client, headers, upstream.base_url, name="二")

    assert second["slug"] == f"{first['slug']}-2"


async def test_channel_key_is_encrypted_at_rest_and_never_returned(client, app, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelORM, created["id"])

    assert stored is not None
    assert stored.api_key_enc != "channel-secret-key"
    assert "channel-secret-key" not in stored.api_key_enc

    detail = await client.get(f"/api/channels/{created['id']}", headers=headers)
    listing = await client.get("/api/channels", headers=headers)

    for response in (detail, listing):
        text = json.dumps(response.json(), ensure_ascii=False)
        assert "channel-secret-key" not in text
        assert stored.api_key_enc not in text


async def test_patch_keeps_key_when_omitted_and_clears_when_empty(client, app, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    renamed = await client.patch(
        f"/api/channels/{created['id']}",
        headers=headers,
        json={"name": "改名后"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "改名后"
    assert renamed.json()["api_key_set"] is True

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelORM, created["id"])
    assert stored is not None and stored.api_key_enc

    cleared = await client.patch(
        f"/api/channels/{created['id']}",
        headers=headers,
        json={"api_key": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["api_key_set"] is False
    assert cleared.json()["api_key_masked"] == ""


async def test_patch_validates_slug_and_priority(client, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    bad_slug = await client.patch(
        f"/api/channels/{created['id']}", headers=headers, json={"slug": "bad slug!"}
    )
    assert bad_slug.status_code == 400
    assert bad_slug.json()["code"] == "INVALID_REQUEST"

    for priority in (0, 1001):
        bad_priority = await client.patch(
            f"/api/channels/{created['id']}", headers=headers, json={"priority": priority}
        )
        assert bad_priority.status_code == 422

    updated = await client.patch(
        f"/api/channels/{created['id']}", headers=headers, json={"priority": 7}
    )
    assert updated.status_code == 200
    assert updated.json()["priority"] == 7


async def test_exact_endpoint_suffix_is_stored_and_previewed_without_hash(client, upstream):
    headers = await _token(client)

    created = await _create(
        client, headers, f"{upstream.base_url}/exact#", protocol="openai_chat"
    )

    assert created["connection_url"].endswith("#")
    assert created["endpoint_preview"] == f"{upstream.base_url}/exact"
    assert created["models_endpoint_preview"] == f"{upstream.base_url}/exact"

    tested = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    assert tested.status_code == 200
    assert tested.json()["status"] == "ok"
    assert [item["path"] for item in upstream.requests].count("/exact") >= 1
    assert not any(item["path"].endswith("/v1/models") for item in upstream.requests)


async def test_delete_channel_without_model_references(client, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    response = await client.delete(f"/api/channels/{created['id']}", headers=headers)

    assert response.status_code == 204
    missing = await client.get(f"/api/channels/{created['id']}", headers=headers)
    assert missing.status_code == 404


async def test_delete_channel_referenced_by_combo_target_returns_409(client, app, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    async with app.state.session_factory() as session:
        model = orm.ChannelModelORM(
            id="CM000000000000000000000001",
            channel_id=created["id"],
            upstream_model="fake-model",
            call_model="fake-model",
        )
        combo = orm.ComboORM(id="CB000000000000000000000001", name="combo-a")
        session.add_all([model, combo])
        await session.commit()
        session.add(
            orm.ComboTargetORM(
                id="CT000000000000000000000001",
                combo_id=combo.id,
                target_type="channel_model",
                channel_model_id=model.id,
                priority=1,
            )
        )
        await session.commit()

    blocked = await client.delete(f"/api/channels/{created['id']}", headers=headers)

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CONFLICT"
    assert "组合" in blocked.json()["message"]


async def test_list_search_status_filter_and_model_count(client, app, upstream):
    headers = await _token(client)
    ok = await _create(client, headers, upstream.base_url, name="Alpha")
    await _create(
        client,
        headers,
        f"{upstream.base_url}/unauthorized",
        name="Beta",
        api_key="wrong-key",
    )

    await client.post(f"/api/channels/{ok['id']}/test-connection", headers=headers)
    listing = await client.get("/api/channels", headers=headers)
    failed = next(item for item in listing.json()["items"] if item["name"] == "Beta")
    await client.post(f"/api/channels/{failed['id']}/test-connection", headers=headers)

    async with app.state.session_factory() as session:
        session.add(
            orm.ChannelModelORM(
                id="CM000000000000000000000002",
                channel_id=ok["id"],
                upstream_model="fake-model",
                call_model="fake-model",
            )
        )
        await session.commit()

    search = await client.get("/api/channels?search=alp", headers=headers)
    assert [item["name"] for item in search.json()["items"]] == ["Alpha"]
    assert search.json()["items"][0]["model_count"] == 1

    only_ok = await client.get("/api/channels?status=ok", headers=headers)
    assert [item["name"] for item in only_ok.json()["items"]] == ["Alpha"]

    only_error = await client.get("/api/channels?status=error", headers=headers)
    assert [item["name"] for item in only_error.json()["items"]] == ["Beta"]


async def test_toggle_enabled(client, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    response = await client.patch(
        f"/api/channels/{created['id']}", headers=headers, json={"enabled": False}
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------
async def test_connection_success_persists_status_and_latency(client, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["model_count"] == 2
    assert body["latency_ms"] >= 0
    assert "fake-model" in body["models_preview"]
    assert body["http_status"] == 200
    assert "channel-secret-key" not in json.dumps(body, ensure_ascii=False)

    detail = await client.get(f"/api/channels/{created['id']}", headers=headers)
    assert detail.json()["connection_status"] == "ok"
    assert detail.json()["connection_latency_ms"] is not None
    assert detail.json()["connection_tested_at"] is not None


async def test_draft_test_connection_persists_verdict_on_saved_channel(client, upstream):
    headers = await _token(client)
    created = await _create(client, headers, upstream.base_url)

    response = await client.post(
        "/api/channels/test-connection",
        headers=headers,
        json={"channel_id": created["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    detail = (await client.get(f"/api/channels/{created['id']}", headers=headers)).json()
    assert detail["connection_status"] == "ok"
    assert detail["connection_latency_ms"] is not None
    assert detail["connection_tested_at"] is not None


async def test_connection_401_returns_error_without_leaking_key(client, upstream):
    headers = await _token(client)
    created = await _create(
        client,
        headers,
        f"{upstream.base_url}/unauthorized",
        api_key="wrong-secret-key",
    )

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    body = response.json()

    assert body["status"] == "error"
    assert body["http_status"] == 401
    assert "401" in body["message"]
    assert "wrong-secret-key" not in json.dumps(body, ensure_ascii=False)

    detail = await client.get(f"/api/channels/{created['id']}", headers=headers)
    assert detail.json()["connection_status"] == "error"


async def test_connection_timeout_returns_timeout_status(client, upstream, monkeypatch):
    from l05_service import probe

    monkeypatch.setattr(probe, "probe_timeout_seconds", lambda: 1)
    headers = await _token(client)
    created = await _create(client, headers, f"{upstream.base_url}/slow#")

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "timeout"

    detail = await client.get(f"/api/channels/{created['id']}", headers=headers)
    assert detail.json()["connection_status"] == "timeout"


async def test_connection_refused_returns_error_status(client, closed_port):
    headers = await _token(client)
    created = await _create(client, headers, f"http://127.0.0.1:{closed_port}/v1")

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["http_status"] is None


async def test_connection_uses_anthropic_x_api_key(client, upstream):
    headers = await _token(client)
    created = await _create(
        client,
        headers,
        f"{upstream.base_url}/anthropic",
        protocol="anthropic_messages",
    )

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    body = response.json()

    assert body["status"] == "ok"
    assert body["auth_style"] == "x-api-key"
    assert body["protocol"] == "anthropic_messages"
    anthropic_requests = [
        item for item in upstream.requests if item["path"].endswith("/v1/models")
    ]
    assert anthropic_requests
    assert anthropic_requests[-1]["headers"].get("anthropic-version") == "2023-06-01"


async def test_connection_uses_gemini_query_key(client, upstream):
    headers = await _token(client)
    created = await _create(
        client,
        headers,
        f"{upstream.base_url}/gemini/v1beta",
        protocol="gemini",
    )

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    body = response.json()

    assert body["status"] == "ok"
    assert body["auth_style"] == "query-key"
    assert body["protocol"] == "gemini"


async def test_connection_applies_request_header_rewrite_rules(client, upstream):
    headers = await _token(client)
    rules = {
        "request_headers": [
            {"mode": "set", "path": "X-Codegen-Client-Name", "value": "qagent"}
        ]
    }

    without_rules = await _create(
        client, headers, f"{upstream.base_url}/required-header#"
    )
    failed = await client.post(
        f"/api/channels/{without_rules['id']}/test-connection", headers=headers
    )
    assert failed.json()["status"] == "error"
    assert failed.json()["http_status"] == 403

    created = await _create(
        client,
        headers,
        f"{upstream.base_url}/required-header#",
        rewrite_rules=rules,
    )
    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["model_count"] == 2
    requests = upstream.find("GET", "/required-header")
    assert requests
    assert requests[-1]["headers"].get("X-Codegen-Client-Name") == "qagent"


async def test_connection_falls_back_across_auth_styles(client, upstream):
    headers = await _token(client)
    created = await _create(
        client,
        headers,
        f"{upstream.base_url}/anthropic",
        protocol="openai_chat",
    )

    response = await client.post(
        f"/api/channels/{created['id']}/test-connection", headers=headers
    )
    body = response.json()

    # The OpenAI protocol is kept, but the working auth style is remembered.
    assert body["status"] == "ok"
    assert body["protocol"] == "openai_chat"
    assert body["auth_style"] in {"x-api-key", "api-key", "bearer"}

async def test_integrity_error_other_than_slug_is_not_reported_as_conflict(client, monkeypatch):
    """Only a real slug collision may surface as 409; other failures are 500."""
    headers = await _token(client)

    async def failing_save(session, channel):
        raise IntegrityError(
            "INSERT INTO channels (slug) VALUES (?)",
            {},
            Exception("NOT NULL constraint failed: channels.timeout_s"),
        )

    monkeypatch.setattr(repo, "save_channel", failing_save)
    response = await client.post(
        "/api/channels",
        headers=headers,
        json=_create_payload("https://example.com/v1"),
    )

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["code"] == "INTERNAL"
    assert "已被使用" not in body["message"]
    assert "NOT NULL" not in body["message"]
    assert "INSERT" not in body["message"]
