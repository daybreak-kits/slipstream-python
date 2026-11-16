from __future__ import annotations

import json

from l01_core.timeutil import now_ms
from l04_repo import orm

async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_catalog_covers_direct_combo_and_group(client) -> None:
    headers = await _login(client)
    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={"name": "渠道甲", "slug": "alpha", "connection_url": "https://upstream.test/v1", "api_key": "sk-x", "protocol": "openai_chat"},
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    model = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={
            "upstream_model": "shared",
            "call_model": "shared",
            "parameters": {
                "max_input_tokens": 32000,
                "max_output_tokens": 4096,
                "input_types": ["text", "image"],
                "output_types": ["text"],
            },
        },
    )
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]
    combo = await client.post(
        "/api/combos",
        headers=headers,
        json={"name": "fancy", "enabled": True, "targets": [{"target_type": "channel_model", "channel_model_id": model_id, "priority": 1}]},
    )
    assert combo.status_code in (200, 201), combo.text

    response = await client.get("/api/catalog", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    kinds = {(item["name"], item["kind"]) for item in items}
    assert ("alpha/shared", "direct") in kinds
    assert ("fancy", "combo") in kinds
    # 隐式分组也报 kind="combo"，origin="auto" 标明它是查询期派生、只读。
    assert ("shared", "combo") in kinds
    assert next(i for i in items if i["name"] == "fancy")["origin"] == "manual"
    assert next(i for i in items if i["name"] == "shared")["origin"] == "auto"
    direct = next(item for item in items if item["kind"] == "direct")
    # 无排期的条目 schedule 下发 null（task-118）。
    assert direct["schedule"] is None
    assert direct["health_status"] == "normal"
    assert direct["visible_status"] == "normal"
    assert direct["status_message"] == ""
    assert direct["consecutive_failures"] == 0
    combo_item = next(item for item in items if item["name"] == "fancy")
    assert combo_item["health_status"] == "normal"
    assert combo_item["visible_status"] == "normal"
    assert combo_item["status_message"] == ""
    group = next(item for item in items if item["name"] == "shared")
    assert group["parameters"]["max_input_tokens"] == 32000
    assert group["parameters"]["input_types"] == ["text", "image"]
    assert group["visible_status"] == "normal"
    assert group["status_message"] == ""

    key = await client.post("/api/keys", headers=headers, json={"label": "catalog"})
    models = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key.json()['key']}"}
    )
    assert models.status_code == 200
    data = models.json()["data"]
    ids = {item["id"] for item in data}
    assert {"alpha/shared", "fancy", "shared"} <= ids
    group = next(item for item in data if item["id"] == "shared")
    assert group["context_window"] == 32000
    assert group["capabilities"] == ["text", "image"]

    # A combo with no targets cannot route anything and must not be advertised.
    empty = await client.post(
        "/api/combos",
        headers=headers,
        json={"name": "empty-combo", "enabled": True, "targets": []},
    )
    assert empty.status_code == 201, empty.text
    models = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key.json()['key']}"}
    )
    ids = {item["id"] for item in models.json()["data"]}
    assert "empty-combo" not in ids


async def _scheduled_channel(client, headers, slug: str, name: str) -> str:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "slug": slug,
            "connection_url": "https://upstream.test/v1",
            "api_key": "sk-x",
            "protocol": "openai_chat",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_catalog_entry_carries_the_v2_schedule(client) -> None:
    headers = await _login(client)
    channel_id = await _scheduled_channel(client, headers, "sched", "排期渠道")
    created = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={
            "upstream_model": "sched-leaf",
            "call_model": "sched-leaf",
            "schedule": {
                "version": 2,
                "timezone": "Asia/Shanghai",
                "rules": [{"kind": "weekly", "days": [1, 3], "start": "22:00", "end": "02:00"}],
            },
        },
    )
    assert created.status_code == 201, created.text
    items = (await client.get("/api/catalog", headers=headers)).json()["items"]
    entry = next(item for item in items if item["name"] == "sched/sched-leaf")
    assert entry["schedule"] == {
        "version": 2,
        "timezone": "Asia/Shanghai",
        "rules": [{"kind": "weekly", "days": [1, 3], "start": "22:00", "end": "02:00"}],
    }


async def test_catalog_entry_normalizes_a_legacy_v1_schedule_row(client) -> None:
    headers = await _login(client)
    channel_id = await _scheduled_channel(client, headers, "legacy-sched", "历史排期渠道")
    created = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "legacy-leaf", "call_model": "legacy-leaf"},
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    # 直接把历史 v1 行写进库，模拟旧版本留下的数据。
    async with client._transport.app.state.session_factory() as session:
        model = await session.get(orm.ChannelModelORM, model_id)
        model.schedule_json = json.dumps(
            {
                "timezone": "UTC",
                "segments": [{"days": [2, 4], "start": "09:00", "end": "11:00"}],
            }
        )
        await session.commit()
    items = (await client.get("/api/catalog", headers=headers)).json()["items"]
    entry = next(item for item in items if item["name"] == "legacy-sched/legacy-leaf")
    assert entry["schedule"] == {
        "version": 2,
        "timezone": "UTC",
        "rules": [{"kind": "weekly", "days": [2, 4], "start": "09:00", "end": "11:00"}],
    }


def _member(health: str, visible: str, retry: int | None):
    return (health, visible, None, None, retry)


async def test_catalog_aggregate_health_prefers_worst_and_earliest_retry() -> None:
    from l05_service import catalog

    now = 1_000_000
    aggregated = catalog._aggregate_members(
        [
            _member("normal", "normal", None),
            _member("open", "open", now + 30_000),
            _member("fault", "fault", now + 5_000),
        ],
        total=3,
    )
    assert aggregated["health_status"] == "fault"
    assert aggregated["visible_status"] == "fault"
    assert aggregated["cooldown_until"] == now + 5_000
    assert "3 个成员中" in aggregated["status_message"]
    assert "1 个故障冷却" in aggregated["status_message"]
    assert "1 个熔断冷却" in aggregated["status_message"]

    only_open = catalog._aggregate_members(
        [_member("normal", "normal", None), _member("open", "open", now + 7_000)],
        total=2,
    )
    assert only_open["health_status"] == "open"
    assert only_open["visible_status"] == "open"
    assert only_open["cooldown_until"] == now + 7_000

    disabled = catalog._aggregate_members(
        [_member("normal", "disabled", None), _member("normal", "disabled", None)],
        total=2,
    )
    assert disabled["visible_status"] == "disabled"
    assert "已封禁" in disabled["status_message"]

    mixed = catalog._aggregate_members(
        [_member("normal", "disabled", None), _member("normal", "normal", None)],
        total=2,
    )
    assert mixed["visible_status"] == "disabled"
    assert "1 个已封禁" in mixed["status_message"]

    half_open = catalog._aggregate_members(
        [
            _member("half_open", "half_open", None),
            _member("open", "open", now + 9_000),
        ],
        total=2,
    )
    assert half_open["health_status"] == "open"
    assert half_open["visible_status"] == "open"
    assert "1 个熔断冷却" in half_open["status_message"]
    assert "1 个半开试探" in half_open["status_message"]

    probe_only = catalog._aggregate_members(
        [
            _member("half_open", "half_open", None),
            _member("normal", "disabled", None),
        ],
        total=2,
    )
    assert probe_only["visible_status"] == "half_open"
    assert "1 个半开试探" in probe_only["status_message"]

    all_five = catalog._aggregate_members(
        [
            _member("fault", "fault", now + 5_000),
            _member("open", "open", now + 7_000),
            _member("half_open", "half_open", None),
            _member("normal", "disabled", None),
            _member("normal", "normal", None),
        ],
        total=5,
    )
    assert all_five["health_status"] == "fault"
    assert all_five["visible_status"] == "fault"
    message = all_five["status_message"]
    assert "1 个故障冷却" in message
    assert "1 个熔断冷却" in message
    assert "1 个半开试探" in message
    assert "1 个已封禁" in message
    assert "限流冷却" not in message


async def test_catalog_member_status_maps_breaker_state() -> None:
    from types import SimpleNamespace

    from l05_service import catalog

    now = 1_000_000
    open_model = SimpleNamespace(health_status="open", cooldown_until=now + 30_000)
    health, visible, cooldown_until, fault_until, retry_at = catalog._member_status(
        open_model, None, True, now
    )
    assert (health, visible) == ("open", "open")
    assert cooldown_until == now + 30_000
    assert fault_until is None
    assert retry_at == now + 30_000

    fault_model = SimpleNamespace(health_status="fault", fault_until=now + 60_000)
    _, visible, _, fault_until, retry_at = catalog._member_status(
        fault_model, None, True, now
    )
    assert visible == "fault"
    assert fault_until == now + 60_000
    assert retry_at == now + 60_000

    # A disabled member never leaks its breaker state.
    _, visible, _, _, _ = catalog._member_status(open_model, None, False, now)
    assert visible == "disabled"

async def test_catalog_expired_breaker_is_normalized_and_reason_shown(client) -> None:
    headers = await _login(client)
    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={"name": "渠道乙", "slug": "beta", "connection_url": "https://upstream.test/v1", "api_key": "sk-y", "protocol": "openai_chat"},
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    model = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "reason", "call_model": "reason"},
    )
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]

    async def set_health(**fields: object) -> None:
        async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
            row = await session.get(orm.ChannelModelORM, model_id)
            for name, value in fields.items():
                setattr(row, name, value)
            await session.commit()

    async def direct_entry() -> dict:
        response = await client.get("/api/catalog", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        return next(item for item in items if item["kind"] == "direct" and item["id"] == model_id)

    now = now_ms()
    await set_health(health_status="open", cooldown_until=now - 1_000, consecutive_failures=7)
    entry = await direct_entry()
    assert entry["health_status"] == "half_open"
    assert entry["visible_status"] == "half_open"
    assert entry["cooldown_until"] is None
    assert "冷却已到" in entry["status_message"]
    assert "等待下次调用探测" in entry["status_message"]

    await set_health(health_status="open", cooldown_until=now + 30_000, consecutive_failures=3)
    entry = await direct_entry()
    assert entry["health_status"] == "open"
    assert entry["cooldown_until"] == now + 30_000
    assert "连续失败 3 次" in entry["status_message"]
    assert "重试" in entry["status_message"]

    await set_health(health_status="fault", cooldown_until=None, fault_until=now + 60_000)
    entry = await direct_entry()
    assert entry["health_status"] == "fault"
    assert entry["visible_status"] == "fault"
    assert entry["fault_until"] == now + 60_000
    assert "冷却中（故障）" in entry["status_message"]
    assert "恢复" in entry["status_message"]

    await set_health(health_status="fault", fault_until=now - 1_000)
    entry = await direct_entry()
    assert entry["health_status"] == "normal"
    assert entry["visible_status"] == "normal"
    assert entry["fault_until"] is None
    assert entry["status_message"] == ""

async def test_catalog_include_disabled_keeps_banned_direct_entry(client) -> None:
    """停用直连模型默认从目录消失（调用面语义），管理目录带 include_disabled 仍能看见并启用。"""
    headers = await _login(client)
    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={"name": "渠道乙", "slug": "beta", "connection_url": "https://upstream.test/v1", "api_key": "sk-x", "protocol": "openai_chat"},
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    model = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "banned-model", "call_model": "banned-model"},
    )
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]

    banned = await client.patch(
        f"/api/channel-models/{model_id}", headers=headers, json={"enabled": False}
    )
    assert banned.status_code == 200, banned.text
    assert banned.json()["visible_status"] == "disabled"

    # 默认目录只看可调用模型：停用条目不再出现。
    default = await client.get("/api/catalog", headers=headers)
    assert default.status_code == 200, default.text
    assert all(item["id"] != model_id for item in default.json()["items"])

    # 管理目录仍列出它（status/visible 都停用），保证封禁可逆、可就地启用。
    widened = await client.get("/api/catalog?include_disabled=true", headers=headers)
    assert widened.status_code == 200, widened.text
    entries = [item for item in widened.json()["items"] if item["id"] == model_id]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["enabled"] is False
    assert entry["status"] == "disabled"
    assert entry["visible_status"] == "disabled"

