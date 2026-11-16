from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from l01_core.errors import OpenAIError
from l01_core.timeutil import now_ms
from l04_repo import orm
from l05_service import billing, routing
from l05_service.llm import Usage
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
        "name": "模型测试渠道",
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


async def test_model_crud_defaults_trim_and_probe_fields(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    created = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="  openai/gpt-4o  ",
        call_model="  gpt-alias  ",
        input_price_per_1m=2.5,
        output_price_per_1m=10,
        parameters={
            "max_input_tokens": 128000,
            "max_output_tokens": 4096,
            "reasoning_level": "  high  ",
            "input_types": [" text ", "image", "text", ""],
            "output_types": ["text", "embedding"],
        },
        enabled=True,
    )

    assert created["upstream_model"] == "openai/gpt-4o"
    assert created["call_model"] == "gpt-alias"
    assert created["input_price_per_1m"] == 2.5
    assert created["output_price_per_1m"] == 10
    assert created["parameters"] == {
        "max_input_tokens": 128000,
        "max_output_tokens": 4096,
        "reasoning_level": "high",
        "input_types": ["text", "image"],
        "output_types": ["text", "embedding"],
    }
    assert created["enabled"] is True
    assert created["probe_status"] == "unknown"
    assert created["probe_latency_ms"] is None
    assert created["probe_message"] == ""
    assert created["probed_at"] is None
    assert created["channel_id"] == channel["id"]

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]
    assert listed.json()["total"] == 1

    updated = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={
            "call_model": "  better-alias  ",
            "enabled": False,
            "parameters": {
                "max_input_tokens": 0,
                "max_output_tokens": 8192,
                "input_types": ["text", "image"],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["call_model"] == "better-alias"
    assert body["enabled"] is False
    assert body["parameters"]["max_input_tokens"] == 0
    assert body["parameters"]["max_output_tokens"] == 8192
    assert body["parameters"]["input_types"] == ["text", "image"]
    assert body["parameters"]["reasoning_level"] == "high"
    assert body["probe_status"] == "unknown"

    deleted = await client.delete(f"/api/channel-models/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json() == {"items": [], "total": 0}


async def test_call_model_defaults_to_upstream_model(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    created = await _model(client, headers, channel["id"], upstream_model="vendor/model-x")

    assert created["call_model"] == "vendor/model-x"


@pytest.mark.parametrize(
    "payload",
    [
        {"upstream_model": "bad model"},
        {"upstream_model": "/leading-slash"},
        {"upstream_model": ""},
        {"upstream_model": "ok-model", "call_model": "bad call"},
        {"upstream_model": "ok-model", "call_model": "/leading-slash"},
        {"upstream_model": "ok-model", "parameters": {"max_input_tokens": -1}},
        {"upstream_model": "ok-model", "parameters": {"max_output_tokens": 100_000_001}},
        {"upstream_model": "ok-model", "parameters": {"input_types": ["tools"]}},
        {"upstream_model": "ok-model", "input_price_per_1m": -0.1},
        {"upstream_model": "ok-model", "output_price_per_1m": -1},
    ],
)
async def test_model_rejects_invalid_fields(client, upstream, payload):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    response = await client.post(
        f"/api/channels/{channel['id']}/models", headers=headers, json=payload
    )

    assert response.status_code in {400, 422}
    assert response.json()["code"] == "INVALID_REQUEST"


async def test_call_model_is_unique_per_channel_only(client, upstream):
    headers = await _token(client)
    first_channel = await _channel(client, headers, upstream.base_url)
    second_channel = await _channel(client, headers, upstream.base_url)

    await _model(client, headers, first_channel["id"], call_model="dup")
    duplicate = await client.post(
        f"/api/channels/{first_channel['id']}/models",
        headers=headers,
        json={"upstream_model": "another-model", "call_model": "dup"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CONFLICT"

    other = await _model(client, headers, second_channel["id"], call_model="dup")
    assert other["call_model"] == "dup"

    # Renaming must respect the unique constraint inside the same channel.
    third = await _model(client, headers, first_channel["id"], call_model="third")
    clash = await client.patch(
        f"/api/channel-models/{third['id']}", headers=headers, json={"call_model": "dup"}
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == "CONFLICT"


async def test_model_rejects_explicit_combo_name_collision(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    async with app.state.session_factory() as session:
        session.add(orm.ComboORM(id="CB000000000000000000000001", name="shared-name"))
        await session.commit()

    response = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "some-model", "call_model": "shared-name"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


async def test_model_requires_existing_channel(client, upstream):
    headers = await _token(client)

    response = await client.post(
        "/api/channels/CH0000000000000000000000FF/models",
        headers=headers,
        json={"upstream_model": "fake-model"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_delete_model_referenced_by_combo_returns_409(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="referenced")

    async with app.state.session_factory() as session:
        combo = orm.ComboORM(id="CB000000000000000000000002", name="combo-b")
        session.add(combo)
        await session.commit()
        session.add(
            orm.ComboTargetORM(
                id="CT000000000000000000000002",
                combo_id=combo.id,
                target_type="channel_model",
                channel_model_id=model["id"],
                priority=1,
            )
        )
        await session.commit()

    response = await client.delete(f"/api/channel-models/{model['id']}", headers=headers)

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"
    assert "组合" in response.json()["message"]


async def test_model_endpoints_require_admin_session(client):
    assert (await client.get("/api/channels/CH000000000000000000000001/models")).status_code == 401
    assert (await client.post("/api/channel-models/CM000000000000000000000001/probe")).status_code == 401
    assert (await client.post("/api/channels/CH000000000000000000000001/probe-all")).status_code == 401


async def test_model_response_never_leaks_channel_key(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"])

    listing = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)

    assert "channel-secret-key" not in json.dumps(model, ensure_ascii=False)
    assert "channel-secret-key" not in json.dumps(listing.json(), ensure_ascii=False)


async def _patch_stored_model(app, model_id: str, **values) -> None:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        for key, value in values.items():
            setattr(model, key, value)
        await session.commit()


async def _stored_breaker(app, model_id: str) -> dict:
    async with app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        assert model is not None
        return {
            "half_open_since": model.half_open_since,
            "cooldown_until": model.cooldown_until,
            "fault_until": model.fault_until,
        }


async def test_patch_disable_marks_model_disabled_in_channel_list(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="封禁渠道", slug="ban-channel")
    model = await _model(client, headers, channel["id"], call_model="ban-leaf")

    response = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"enabled": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["visible_status"] == "disabled"
    assert body["health_status"] == "normal"

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    item = listed.json()["items"][0]
    assert item["enabled"] is False
    assert item["visible_status"] == "disabled"

    # A disabled direct model is no longer advertised as callable.
    catalog = await client.get("/api/catalog", headers=headers)
    assert all(
        entry["id"] != model["id"]
        for entry in catalog.json()["items"]
        if entry["kind"] == "direct"
    )
    # The management catalog keeps it listed, otherwise the ban would be a dead end.
    widened = await client.get("/api/catalog?include_disabled=true", headers=headers)
    assert widened.status_code == 200, widened.text
    banned = [
        entry
        for entry in widened.json()["items"]
        if entry["kind"] == "direct" and entry["id"] == model["id"]
    ]
    assert len(banned) == 1
    assert banned[0]["enabled"] is False
    assert banned[0]["status"] == "disabled"
    assert banned[0]["visible_status"] == "disabled"


async def test_enable_then_reset_closes_breaker(client, upstream, app):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="解禁渠道", slug="unban-channel")
    model = await _model(client, headers, channel["id"], call_model="unban-leaf")
    base = now_ms()

    disabled = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    await _patch_stored_model(
        app,
        model["id"],
        health_status="open",
        consecutive_failures=4,
        consecutive_trip_count=2,
        escalation_trip_count=2,
        opened_at=base,
        cooldown_until=base + 60_000,
        fault_until=None,
        half_open_since=None,
    )

    enabled = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert enabled.json()["health_status"] == "open"

    reset = await client.post(
        f"/api/channel-models/{model['id']}/reset", headers=headers
    )
    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert body["health_status"] == "normal"
    assert body["visible_status"] == "normal"
    assert body["consecutive_failures"] == 0
    assert body["consecutive_trip_count"] == 0
    assert body["escalation_trip_count"] == 0
    assert body["cooldown_until"] is None
    assert body["fault_until"] is None
    assert body["next_retry_at"] is None
    stored = await _stored_breaker(app, model["id"])
    assert stored["half_open_since"] is None


async def test_reset_is_idempotent_for_normal_model(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="复位渠道", slug="reset-channel")
    model = await _model(client, headers, channel["id"], call_model="reset-leaf")

    first = await client.post(
        f"/api/channel-models/{model['id']}/reset", headers=headers
    )
    assert first.status_code == 200, first.text
    assert first.json()["health_status"] == "normal"
    assert first.json()["visible_status"] == "normal"
    assert first.json()["consecutive_failures"] == 0

    second = await client.post(
        f"/api/channel-models/{model['id']}/reset", headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["health_status"] == "normal"
    assert second.json()["consecutive_failures"] == 0


async def test_model_patch_and_reset_return_404_for_unknown_model(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="不存在模型", slug="missing-model")
    await _model(client, headers, channel["id"], call_model="missing-leaf")

    unknown = "CM000000000000000000000099"
    missing_patch = await client.patch(
        f"/api/channel-models/{unknown}",
        headers=headers,
        json={"enabled": False},
    )
    assert missing_patch.status_code == 404

    missing_reset = await client.post(
        f"/api/channel-models/{unknown}/reset",
        headers=headers,
    )
    assert missing_reset.status_code == 404


async def test_cache_prices_default_null_and_roundtrip(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="缓存计价", slug="cache-price")

    plain = await _model(client, headers, channel["id"], call_model="plain")
    assert plain["cache_read_price_per_1m"] is None
    assert plain["cache_write_price_per_1m"] is None

    created = await _model(
        client,
        headers,
        channel["id"],
        call_model="cached",
        cache_read_price_per_1m=0.05,
        cache_write_price_per_1m=0.15,
    )
    assert created["cache_read_price_per_1m"] == 0.05
    assert created["cache_write_price_per_1m"] == 0.15

    patched = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"cache_read_price_per_1m": 0.02},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["cache_read_price_per_1m"] == 0.02
    assert patched.json()["cache_write_price_per_1m"] == 0.15

    # 显式 null = 清空配置，回到"按输入原价"而不是"命中免费"。
    cleared = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"cache_read_price_per_1m": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cache_read_price_per_1m"] is None
    assert cleared.json()["cache_write_price_per_1m"] == 0.15

    negative = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"cache_write_price_per_1m": -1},
    )
    assert negative.status_code == 422


async def test_catalog_exposes_cache_prices(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="目录缓存价", slug="catalog-cache")
    created = await _model(
        client, headers, channel["id"], call_model="cached-catalog", cache_read_price_per_1m=0.03
    )

    response = await client.get("/api/catalog", headers=headers)
    assert response.status_code == 200, response.text
    item = next(entry for entry in response.json()["items"] if entry["id"] == created["id"])
    assert item["cache_read_price_per_1m"] == 0.03
    assert item["cache_write_price_per_1m"] is None


def _priced_model(**overrides) -> orm.ChannelModelORM:
    values = {
        "id": "CMTEST000000000000000001",
        "channel_id": "CHTEST0000000000000001",
        "upstream_model": "upstream",
        "call_model": "call",
        "input_price_per_1m": 2.0,
        "output_price_per_1m": 10.0,
    }
    values.update(overrides)
    return orm.ChannelModelORM(**values)


def test_compute_cost_without_cache_price_matches_legacy_formula():
    model = _priced_model()
    usage = Usage(prompt_tokens=1000, completion_tokens=100, total_tokens=1100, cached_tokens=400)
    assert billing.compute_cost(model, usage) == round(
        1000 / 1_000_000 * 2.0 + 100 / 1_000_000 * 10.0, 8
    )


def test_compute_cost_bills_cache_hits_at_configured_read_price():
    model = _priced_model(cache_read_price_per_1m=0.5)
    usage = Usage(prompt_tokens=1000, completion_tokens=100, total_tokens=1100, cached_tokens=400)
    assert billing.compute_cost(model, usage) == round(
        600 / 1_000_000 * 2.0 + 400 / 1_000_000 * 0.5 + 100 / 1_000_000 * 10.0, 8
    )


def test_compute_cost_clamps_cached_tokens_into_prompt():
    model = _priced_model(cache_read_price_per_1m=0.0)
    # cached 超过 prompt：多余部分不得变成"折扣"。
    over = Usage(prompt_tokens=1000, completion_tokens=0, total_tokens=1500, cached_tokens=1500)
    assert billing.compute_cost(model, over) == 0.0

    negative = Usage(prompt_tokens=1000, completion_tokens=0, total_tokens=900, cached_tokens=-400)
    assert billing.compute_cost(model, negative) == round(1000 / 1_000_000 * 2.0, 8)


async def _list_models(client, headers, channel_id: str) -> list[dict]:
    response = await client.get(f"/api/channels/{channel_id}/models", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_default_probe_model_is_unique_per_channel(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="默认探测渠道", slug="default-probe")
    first = await _model(client, headers, channel["id"], upstream_model="probe-a", call_model="probe-a")
    second = await _model(client, headers, channel["id"], upstream_model="probe-b", call_model="probe-b")

    # The first model added to a channel becomes the default probe target.
    assert first["probe_default"] is True
    assert second["probe_default"] is False

    picked = await client.post(
        f"/api/channel-models/{second['id']}/probe-default", headers=headers
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["probe_default"] is True

    listed = await _list_models(client, headers, channel["id"])
    flags = {item["id"]: item["probe_default"] for item in listed}
    assert flags[first["id"]] is False
    assert flags[second["id"]] is True

    # Deleting the default model promotes the next remaining model.
    removed = await client.delete(f"/api/channel-models/{second['id']}", headers=headers)
    assert removed.status_code == 204, removed.text
    remaining = await _list_models(client, headers, channel["id"])
    assert [item["id"] for item in remaining] == [first["id"]]
    assert remaining[0]["probe_default"] is True


async def test_default_probe_endpoint_requires_admin_session(client):
    response = await client.post("/api/channel-models/CM000000000000000000000001/probe-default")
    assert response.status_code == 401


async def _api_key(client, headers) -> dict[str, str]:
    response = await client.post("/api/keys", headers=headers, json={"label": "ban-until"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['key']}"}


def _chat_body(model: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def _catalog_entry(client, headers, model_id: str, *, include_disabled: bool = False):
    url = "/api/catalog?include_disabled=true" if include_disabled else "/api/catalog"
    response = await client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    for entry in response.json()["items"]:
        if entry["id"] == model_id:
            return entry
    return None


async def test_timed_cooldown_stays_listed_and_answers_503_until_it_revives(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="定时封禁渠道", slug="timed-ban")
    model = await _model(client, headers, channel["id"], call_model="timed-leaf")
    auth = await _api_key(client, headers)
    direct = f"{channel['slug']}/timed-leaf"

    until = datetime.now(timezone.utc) + timedelta(seconds=2)
    banned = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"banned_until": until.isoformat()},
    )
    assert banned.status_code == 200, banned.text
    body = banned.json()
    assert body["enabled"] is False
    assert body["banned_until"] is not None
    assert body["banned_until"] <= int(until.timestamp() * 1000) + 1

    # 冷却期间（ADR-070 §3.2）：条目仍出现在默认目录里，客户端仍能看到名字，
    # 只是暂时不可调用。带 include_disabled 的管理视图同样可见。
    cooling = await _catalog_entry(client, headers, model["id"])
    assert cooling is not None
    assert cooling["enabled"] is False
    assert cooling["status"] == "disabled"
    assert cooling["cooldown_reason"] == "MODEL_COOLDOWN"
    hidden = await _catalog_entry(client, headers, model["id"], include_disabled=True)
    assert hidden is not None
    assert hidden["enabled"] is False
    assert hidden["status"] == "disabled"
    # 管理目录要能看到封禁到期时刻，前端据此显示「封禁至 …」。
    assert hidden["banned_until"] is not None
    assert hidden["banned_until"] == body["banned_until"]
    # /v1/models 广播所有非封禁条目（含冷却中）。
    directory = await client.get("/v1/models", headers=auth)
    assert directory.status_code == 200, directory.text
    assert direct in {item["id"] for item in directory.json()["data"]}
    # 冷却是临时措施：直连调用回答 503 + Retry-After（封禁才是永久措施 → 404）。
    blocked = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body(direct)
    )
    assert blocked.status_code == 503, blocked.text
    assert blocked.json()["error"]["code"] == "MODEL_COOLDOWN"
    assert int(blocked.headers["Retry-After"]) >= 1

    # 到期无需任何后台任务：读取侧惰性判活即自动复活。
    await asyncio.sleep(max(0.0, (until - datetime.now(timezone.utc)).total_seconds()) + 0.6)
    listed = await _list_models(client, headers, channel["id"])
    assert listed[0]["enabled"] is True
    assert listed[0]["banned_until"] is None
    revived = await _catalog_entry(client, headers, model["id"])
    assert revived is not None
    assert revived["enabled"] is True
    assert revived["banned_until"] is None
    served = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body(direct)
    )
    assert served.status_code == 200, served.text


async def test_permanent_ban_needs_explicit_enable_and_rejects_bad_timestamp(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="永久封禁渠道", slug="perm-ban")
    model = await _model(client, headers, channel["id"], call_model="perm-leaf")

    # banned_until=null 表示永久封禁：enabled 落库为 false，且不会被惰性复活。
    permanent = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"banned_until": None},
    )
    assert permanent.status_code == 200, permanent.text
    assert permanent.json()["enabled"] is False
    assert permanent.json()["banned_until"] is None
    listed = await _list_models(client, headers, channel["id"])
    assert listed[0]["enabled"] is False

    auth = await _api_key(client, headers)
    gone = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("perm-ban/perm-leaf")
    )
    # 永久封禁 = 对客户端表现为没有这个模型（ADR-070 §3）；冷却是临时措施 → 503。
    assert gone.status_code == 404, gone.text
    directory = await client.get("/v1/models", headers=auth)
    assert "perm-ban/perm-leaf" not in {item["id"] for item in directory.json()["data"]}

    # 显式 enabled=true 一律解封并清掉定时封禁。
    enabled = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert enabled.json()["banned_until"] is None

    # 时间戳无法解析时拒绝，不能静默当成永久封禁。
    bad = await client.patch(
        f"/api/channel-models/{model['id']}",
        headers=headers,
        json={"banned_until": "明天上午"},
    )
    assert bad.status_code == 400, bad.text
    still = await _list_models(client, headers, channel["id"])
    assert still[0]["enabled"] is True


async def test_all_cooling_group_stays_listed_and_answers_503_with_retry_after(client, upstream):
    """组里所有成员都在冷却：组名仍广播，调用回答 503 + Retry-After（ADR-070 §3.2）。"""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="冷却分组渠道", slug="cool-group")
    model = await _model(client, headers, channel["id"], call_model="cool-leaf")
    until = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()
    banned = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={
            "model_ids": [model["id"]],
            "op": "cooldown",
            "banned_until": until,
        },
    )
    assert banned.json()["succeeded"] == 1

    auth = await _api_key(client, headers)
    blocked = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("cool-leaf")
    )
    assert blocked.status_code == 503, blocked.text
    assert blocked.json()["error"]["code"] == "MODEL_COOLDOWN"
    assert int(blocked.headers["Retry-After"]) >= 1
    # /v1/models 仍广播这个组名。
    directory = await client.get("/v1/models", headers=auth)
    assert "cool-leaf" in {item["id"] for item in directory.json()["data"]}


async def test_batch_patch_reports_missing_ids_and_is_idempotent(client, upstream):
    """批量封禁幂等；缺失 id 单独报错，其余行照常提交（ADR-070 §4.1）。"""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="批量渠道", slug="batch")
    first = await _model(client, headers, channel["id"], call_model="batch-a")
    second = await _model(client, headers, channel["id"], call_model="batch-b")

    missing = "CM0000000000000000000000zz"
    response = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={"model_ids": [first["id"], second["id"], missing], "op": "ban"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["succeeded"] == 2
    assert payload["failed"] == 1
    assert payload["results"][2] == {
        "model_id": missing,
        "ok": False,
        "error": "MODEL_NOT_FOUND",
    }

    # 同一批量重复提交，结果一致（幂等）。
    repeat = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={"model_ids": [first["id"], first["id"]], "op": "ban"},
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["succeeded"] == 1

    until = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    cooldown = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={
            "model_ids": [first["id"]],
            "op": "cooldown",
            "banned_until": until,
        },
    )
    assert cooldown.json()["succeeded"] == 1

    listed = {item["id"]: item for item in await _list_models(client, headers, channel["id"])}
    assert listed[first["id"]]["cooldown_reason"] == "MODEL_COOLDOWN"
    assert listed[second["id"]]["cooldown_reason"] == "MODEL_BANNED"


async def test_batch_schedule_overwrites_every_row_with_the_same_v2_rules(client, upstream):
    """批量 schedule 对每行写入同一份 v2 文档（ADR-070 §4.1）。"""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="批量排程渠道", slug="batch-sched")
    first = await _model(client, headers, channel["id"], call_model="sched-a")
    second = await _model(client, headers, channel["id"], call_model="sched-b")

    now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
    schedule = {
        "version": 2,
        "timezone": "Asia/Shanghai",
        # 覆盖「现在」±几分钟的跨天窗口，让断言不依赖运行时刻。
        "rules": [
            {
                "kind": "daily",
                "start": (now_sh - timedelta(minutes=2)).strftime("%H:%M"),
                "end": (now_sh + timedelta(minutes=5)).strftime("%H:%M"),
            }
        ],
    }
    response = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={
            "model_ids": [first["id"], second["id"]],
            "op": "schedule",
            "schedule": schedule,
        },
    )
    assert response.status_code == 200, response.text
    listed = await _list_models(client, headers, channel["id"])
    for item in listed:
        assert item["schedule"]["version"] == 2
        assert item["schedule"]["timezone"] == "Asia/Shanghai"
        assert item["schedule"]["rules"][0]["kind"] == "daily"
        assert item["schedule"]["rules"][0]["start"] == schedule["rules"][0]["start"]
        assert item["schedule"]["rules"][0]["end"] == schedule["rules"][0]["end"]
        assert item["cooldown_reason"] == "COOLDOWN_WINDOW"

    unban = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={"model_ids": [first["id"], second["id"]], "op": "unban"},
    )
    assert unban.json()["succeeded"] == 2


def test_cooldown_only_answers_503_when_no_expiry_is_known(monkeypatch):
    """审计 N-4：全体候选冷却但算不出到期时刻时回答 503，而不是空集 max 抛 500。"""
    rows = [(object(), object())]
    monkeypatch.setattr(routing, "ban_reason", lambda model, at: routing.SKIP_MODEL_COOLDOWN)
    monkeypatch.setattr(routing, "banned_until_ms", lambda model, at: None)

    with pytest.raises(OpenAIError) as caught:
        routing._cooldown_only("cooling-model", rows)

    assert caught.value.status == 503
    assert caught.value.code == "MODEL_COOLDOWN"
    assert caught.value.details["next_available_at"] is None
    assert "retry_after_s" not in caught.value.details

async def test_capability_flags_round_trip_and_none_is_not_written(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    created = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="flagged-model",
        parameters={
            "max_input_tokens": 1024,
            "tool_call": True,
            "reasoning": False,
            "structured_output": True,
        },
    )

    assert created["parameters"]["tool_call"] is True
    assert created["parameters"]["reasoning"] is False
    assert created["parameters"]["structured_output"] is True
    # None 不写 JSON：没有提交的附件能力不出现在响应里
    assert "attachment" not in created["parameters"]

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json()["items"][0]["parameters"]["tool_call"] is True

    # ORM 默认值不变（老行与新行同形）
    default_json = orm.ChannelModelORM.__table__.c.parameters_json.default.arg
    assert default_json == (
        '{"max_input_tokens":0,"max_output_tokens":0,"reasoning_level":"",'
        '"input_types":["text"],"output_types":["text"]}'
    )


def test_model_parameters_schema_carries_the_capability_flags():
    from l06_api.schemas import ModelParametersIn, ModelParametersOut

    params = ModelParametersIn(tool_call=True, reasoning=False, attachment=None)
    assert params.tool_call is True
    assert params.reasoning is False
    assert params.attachment is None

    out = ModelParametersOut(**params.model_dump())
    assert out.tool_call is True
    assert out.reasoning is False
    # None 不写 JSON
    assert "attachment" not in out.model_dump()
