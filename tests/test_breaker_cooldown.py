from __future__ import annotations

import time

from l01_core.timeutil import now_ms
from l03_domain.breaker import (
    BreakerConfig,
    BreakerState,
    effective_cooldown_until,
    member_view,
    next_retry_at,
)
from l04_repo import orm
from l05_service.channel_models import cooldown_reason, schedule_for

NOW = 1_700_000_000_000


async def _login(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _model(client, headers, **payload: object) -> tuple[str, str]:
    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "熔断渠道",
            "slug": "breaker-cooldown",
            "connection_url": "https://upstream.test/v1",
            "api_key": "sk-x",
            "protocol": "openai_chat",
        },
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    body: dict = {"upstream_model": "cool", "call_model": "cool"}
    body.update(payload)
    model = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=body
    )
    assert model.status_code == 201, model.text
    return channel_id, model.json()["id"]


async def _set_row(client, model_id: str, **fields: object) -> None:
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = await session.get(orm.ChannelModelORM, model_id)
        for name, value in fields.items():
            setattr(row, name, value)
        await session.commit()


async def _entry(client, headers, model_id: str) -> dict:
    response = await client.get("/api/catalog", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    return next(
        item for item in items if item["kind"] == "direct" and item["id"] == model_id
    )


def _next_local_midnight_ms() -> int:
    """下一个本地零点，和额度耗尽后的冷却驻留时刻同形。"""
    current = time.localtime()
    midnight = time.mktime(
        (current.tm_year, current.tm_mon, current.tm_mday + 1, 0, 0, 0, 0, 0, -1)
    )
    return int(midnight * 1000)


def test_open_state_without_deadline_derives_a_real_expiry() -> None:
    """熔断打开但 cooldown_until 丢失时，按配置从 opened_at 推出真实到期时刻。"""
    config = BreakerConfig(failure_threshold=5, cooldown_s=30, fault_cooldown_s=300)
    state = BreakerState(
        health="open",
        consecutive_failures=5,
        consecutive_trip_count=1,
        opened_at=NOW - 1_000,
        cooldown_until=None,
    )
    assert effective_cooldown_until(state, config) == NOW - 1_000 + 30_000
    view = member_view(state, True, NOW, config)
    assert view["visible"] == "open"
    assert view["cooldown_until"] == NOW - 1_000 + 30_000
    assert view["cooldown_until"] > NOW
    assert next_retry_at(state, NOW, config) == NOW - 1_000 + 30_000


def test_escalated_open_state_uses_the_escalation_cooldown() -> None:
    """已经升级过的模型用升级冷却时长，而不是基础冷却时长。"""
    config = BreakerConfig(cooldown_s=30, fault_cooldown_s=300)
    state = BreakerState(
        health="open",
        consecutive_failures=9,
        consecutive_trip_count=3,
        escalation_trip_count=1,
        opened_at=NOW - 1_000,
    )
    assert effective_cooldown_until(state, config) == NOW - 1_000 + 300_000


def test_open_state_without_any_timer_has_no_fake_deadline() -> None:
    """连 opened_at 都没有时不能编一个 0 出来，只能说恢复时间待定。"""
    state = BreakerState(health="open", consecutive_failures=5)
    assert effective_cooldown_until(state, BreakerConfig()) is None
    view = member_view(state, True, NOW, BreakerConfig())
    assert view["cooldown_until"] is None
    assert next_retry_at(state, NOW, BreakerConfig()) is None


async def test_catalog_reports_a_real_expiry_for_an_open_model(client) -> None:
    headers = await _login(client)
    _channel_id, model_id = await _model(client, headers)
    now = now_ms()
    await _set_row(
        client,
        model_id,
        health_status="open",
        opened_at=now - 5_000,
        cooldown_until=None,
        consecutive_failures=5,
        consecutive_trip_count=1,
    )
    entry = await _entry(client, headers, model_id)
    assert entry["health_status"] == "open"
    assert entry["visible_status"] == "open"
    assert entry["cooldown_until"] is not None
    assert entry["cooldown_until"] > now
    assert "连续失败 5 次" in entry["status_message"]
    assert "重试" in entry["status_message"]
    assert "00:00" not in entry["status_message"]


async def test_catalog_note_never_renders_a_zero_clock(client) -> None:
    """午夜到期（额度耗尽的冷却驻留）也不能显示成 00:00 这种坏值。"""
    headers = await _login(client)
    _channel_id, model_id = await _model(client, headers)
    await _set_row(
        client,
        model_id,
        health_status="open",
        opened_at=now_ms() - 1_000,
        cooldown_until=_next_local_midnight_ms(),
        consecutive_failures=0,
    )
    entry = await _entry(client, headers, model_id)
    assert "00:00" not in entry["status_message"]
    assert "连续失败 0 次" not in entry["status_message"]
    assert "恢复时间待定" not in entry["status_message"]
    assert "重试" in entry["status_message"]


async def test_catalog_note_says_pending_when_no_deadline_can_be_derived(
    client,
) -> None:
    headers = await _login(client)
    _channel_id, model_id = await _model(client, headers)
    await _set_row(
        client,
        model_id,
        health_status="open",
        opened_at=None,
        cooldown_until=None,
        consecutive_failures=5,
    )
    entry = await _entry(client, headers, model_id)
    assert entry["cooldown_until"] is None
    assert "恢复时间待定" in entry["status_message"]
    assert "00:00" not in entry["status_message"]


async def test_channel_model_detail_agrees_with_the_catalog(client) -> None:
    """渠道模型详情与目录给出同一个真实到期时刻。"""
    headers = await _login(client)
    channel_id, model_id = await _model(client, headers)
    now = now_ms()
    await _set_row(
        client,
        model_id,
        health_status="open",
        opened_at=now - 5_000,
        cooldown_until=None,
        consecutive_failures=5,
    )
    entry = await _entry(client, headers, model_id)
    response = await client.get(f"/api/channels/{channel_id}/models", headers=headers)
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["items"] if row["id"] == model_id)
    assert item["cooldown_until"] == entry["cooldown_until"]
    assert item["cooldown_until"] > now
    assert item["next_retry_at"] == item["cooldown_until"]


async def test_clearing_the_schedule_restores_the_member(client) -> None:
    """清空排期后成员立刻不再被判冷却，目录与读侧口径一致。"""
    headers = await _login(client)
    _channel_id, model_id = await _model(
        client,
        headers,
        schedule={
            "version": 2,
            "timezone": "Asia/Shanghai",
            "rules": [{"kind": "daily", "start": "00:00", "end": "23:59"}],
        },
    )
    entry = await _entry(client, headers, model_id)
    assert entry["cooldown_reason"] == "COOLDOWN_WINDOW"
    assert "排期冷却窗口中" in entry["status_message"]

    cleared = await client.patch(
        f"/api/channel-models/{model_id}", headers=headers, json={"schedule": None}
    )
    assert cleared.status_code == 200, cleared.text
    entry = await _entry(client, headers, model_id)
    assert entry["cooldown_reason"] is None
    assert entry["visible_status"] == "normal"
    assert entry["status_message"] == ""
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = await session.get(orm.ChannelModelORM, model_id)
        assert schedule_for(row) is None
        assert cooldown_reason(row, now_ms()) is None


async def test_writing_an_empty_rule_set_clears_the_schedule(client) -> None:
    """写空规则集（永不冷却）等价于清空排期。"""
    headers = await _login(client)
    _channel_id, model_id = await _model(
        client,
        headers,
        schedule={
            "version": 2,
            "timezone": "Asia/Shanghai",
            "rules": [{"kind": "daily", "start": "00:00", "end": "23:59"}],
        },
    )
    assert (await _entry(client, headers, model_id))["cooldown_reason"] == "COOLDOWN_WINDOW"
    written = await client.patch(
        f"/api/channel-models/{model_id}",
        headers=headers,
        json={"schedule": {"version": 2, "timezone": "Asia/Shanghai", "rules": []}},
    )
    assert written.status_code == 200, written.text
    entry = await _entry(client, headers, model_id)
    assert entry["cooldown_reason"] is None
    assert entry["visible_status"] == "normal"
