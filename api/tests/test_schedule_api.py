from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from l05_service import routing
from tests.fake_upstream_server import FakeUpstreamServer

SHANGHAI = ZoneInfo("Asia/Shanghai")
# Tuesday 02:00 in Shanghai is still inside the Monday 23:00-08:00 window.
TUESDAY_2AM_SH = int(datetime(2026, 11, 10, 2, 0, tzinfo=SHANGHAI).timestamp() * 1000)
# Monday 00:30 UTC is Monday 08:30 in Shanghai.
MONDAY_0030_UTC = int(datetime(2026, 11, 9, 0, 30, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def frozen_now(monkeypatch):
    def freeze(value: int) -> None:
        monkeypatch.setattr(routing, "now_ms", lambda: value)

    return freeze


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
    name: str = "计划窗口渠道",
    enabled: bool = True,
    priority: int = 1,
) -> dict:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "priority": priority,
            "enabled": enabled,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(
    client,
    headers,
    channel_id: str,
    *,
    call_model: str = "scheduled",
    schedule: dict | None = None,
    enabled: bool = True,
) -> dict:
    payload: dict = {
        "upstream_model": "fake-model",
        "call_model": call_model,
        "enabled": enabled,
    }
    if schedule is not None:
        payload["schedule"] = schedule
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": "schedule"})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


async def test_schedule_round_trips_through_model_api(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    schedule = {
        "version": 2,
        "timezone": "America/New_York",
        "rules": [
            {"kind": "weekly", "days": [1, 3], "start": "23:00", "end": "08:00"},
            {"kind": "daily", "start": "12:00", "end": "14:00"},
        ],
    }
    # daily 在写入时归一成 days=[1..7] 的 weekly 规则（ADR-070 §1.3）。
    expected = {
        "version": 2,
        "timezone": "America/New_York",
        "rules": [
            {"kind": "weekly", "days": [1, 3], "start": "23:00", "end": "08:00"},
            {"kind": "daily", "days": [1, 2, 3, 4, 5, 6, 7], "start": "12:00", "end": "14:00"},
        ],
    }

    created = await _model(client, headers, channel["id"], schedule=schedule)
    assert created["schedule"] == expected

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["schedule"] == expected

    new_schedule = {
        "version": 2,
        "timezone": "UTC",
        "rules": [{"kind": "weekly", "days": [7], "start": "01:30", "end": "02:15"}],
    }
    updated = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"schedule": new_schedule},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["schedule"] == new_schedule


async def test_legacy_segments_payload_normalizes_to_v2_rules(client, upstream):
    """v1 的 segments 输入仍然合法：读回来是等价的 weekly v2 规则。"""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    created = await _model(
        client,
        headers,
        channel["id"],
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [2, 4], "start": "09:00", "end": "11:00"}],
        },
    )
    assert created["schedule"] == {
        "version": 2,
        "timezone": "Asia/Shanghai",
        "rules": [{"kind": "weekly", "days": [2, 4], "start": "09:00", "end": "11:00"}],
    }


async def test_model_without_schedule_reports_default_timezone_and_no_rules(
    client, upstream
):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    created = await _model(client, headers, channel["id"])

    # 空 rules = 永不进入计划停用窗口（ADR-070 §1.3）。
    assert created["schedule"] == {"version": 2, "timezone": "Asia/Shanghai", "rules": []}


@pytest.mark.parametrize(
    "schedule",
    [
        {"timezone": "Mars/Phobos", "segments": []},
        {"timezone": "Asia/Shanghai", "segments": [{"days": [0], "start": "09:00", "end": "10:00"}]},
        {"timezone": "Asia/Shanghai", "segments": [{"days": [8], "start": "09:00", "end": "10:00"}]},
        {"timezone": "Asia/Shanghai", "segments": [{"days": [], "start": "09:00", "end": "10:00"}]},
        {"timezone": "Asia/Shanghai", "segments": [{"days": [1], "start": "25:00", "end": "10:00"}]},
        {"timezone": "Asia/Shanghai", "segments": [{"days": [1], "start": "09:00", "end": "9am"}]},
    ],
)
async def test_invalid_schedule_is_rejected(client, upstream, schedule):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    response = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "schedule": schedule},
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_REQUEST"
    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.json()["total"] == 0


async def test_all_candidates_scheduled_off_returns_503_with_next_available(
    client, upstream, frozen_now
):
    frozen_now(TUESDAY_2AM_SH)
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(
        client,
        headers,
        channel["id"],
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [1], "start": "23:00", "end": "08:00"}],
        },
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/scheduled"),
    )

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "MODEL_SCHEDULED_OFFLINE"
    expected = int(datetime(2026, 11, 10, 8, 0, tzinfo=SHANGHAI).timestamp() * 1000)
    assert error["next_available_at"] == expected
    # The upstream was never contacted for a model that is inside its window.
    assert upstream.find("POST", "/chat/completions") == []


async def test_whole_week_schedule_reports_no_next_available(
    client, upstream, frozen_now
):
    frozen_now(TUESDAY_2AM_SH)
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(
        client,
        headers,
        channel["id"],
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "00:00"}],
        },
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/scheduled"),
    )

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "MODEL_SCHEDULED_OFFLINE"
    assert error["next_available_at"] is None
    # SF-3：算不出恢复时刻就不发 Retry-After（否则客户端按 1 秒无限重试），
    # 文案按视野（HORIZON_DAYS）说清楚，不再写「本周」。
    assert "retry-after" not in response.headers
    assert "未来 62 天内没有可恢复的时间" in error["message"]


async def test_scheduled_model_is_skipped_when_a_fallback_is_available(
    client, upstream, frozen_now
):
    frozen_now(TUESDAY_2AM_SH)
    headers = await _token(client)
    offline = await _channel(
        client, headers, upstream.base_url, name="计划停用", priority=1
    )
    healthy = await _channel(
        client, headers, upstream.base_url, name="正常", priority=2
    )
    await _model(
        client,
        headers,
        offline["id"],
        call_model="shared-schedule",
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [1], "start": "23:00", "end": "08:00"}],
        },
    )
    await _model(
        client,
        headers,
        healthy["id"],
        call_model="shared-schedule",
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [3], "start": "23:00", "end": "08:00"}],
        },
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("shared-schedule"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "shared-schedule"
    assert len(upstream.find("POST", "/chat/completions")) == 1


async def test_timezone_boundary_uses_the_model_timezone(client, upstream, frozen_now):
    frozen_now(MONDAY_0030_UTC)
    headers = await _token(client)
    utc_channel = await _channel(client, headers, upstream.base_url, name="UTC 渠道")
    shanghai_channel = await _channel(
        client, headers, upstream.base_url, name="上海渠道"
    )
    await _model(
        client,
        headers,
        utc_channel["id"],
        call_model="utc-window",
        schedule={
            "timezone": "UTC",
            "segments": [{"days": [1], "start": "00:00", "end": "01:00"}],
        },
    )
    await _model(
        client,
        headers,
        shanghai_channel["id"],
        call_model="shanghai-window",
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [1], "start": "00:00", "end": "01:00"}],
        },
    )
    key = await _key(client, headers)

    offline = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{utc_channel['slug']}/utc-window"),
    )
    assert offline.status_code == 503, offline.text
    assert offline.json()["error"]["code"] == "MODEL_SCHEDULED_OFFLINE"

    online = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{shanghai_channel['slug']}/shanghai-window"),
    )
    assert online.status_code == 200, online.text


async def test_mixed_unavailable_reasons_stay_generic_503(
    client, upstream, frozen_now
):
    frozen_now(TUESDAY_2AM_SH)
    headers = await _token(client)
    offline_channel = await _channel(
        client, headers, upstream.base_url, name="计划停用", priority=1
    )
    disabled_channel = await _channel(
        client, headers, upstream.base_url, name="人工停用", enabled=False, priority=2
    )
    await _model(
        client,
        headers,
        offline_channel["id"],
        call_model="mixed",
        schedule={
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [1], "start": "23:00", "end": "08:00"}],
        },
    )
    await _model(
        client,
        headers,
        disabled_channel["id"],
        call_model="mixed",
        enabled=True,
    )
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("mixed"),
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"


def _weekly_rules(count: int) -> list[dict]:
    """count 条互不重叠的周一窗口：00:00-01:00、01:00-02:00……"""
    return [
        {
            "kind": "weekly",
            "days": [1],
            "start": f"{hour:02d}:00",
            "end": f"{hour + 1:02d}:00",
        }
        for hour in range(count)
    ]


async def test_schedule_with_more_than_ten_rules_is_rejected(client, upstream):
    """ADR-070 §2.4：规则数上限 10 条，写侧拒绝而不是静默截断（审计 SF-1）。"""
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="规则上限渠道")

    # 10 条是上限本身，必须还能写入。
    accepted = await _model(
        client,
        headers,
        channel["id"],
        call_model="ten-rules",
        schedule={"timezone": "Asia/Shanghai", "rules": _weekly_rules(10)},
    )
    assert len(accepted["schedule"]["rules"]) == 10

    rejected = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={
            "upstream_model": "fake-model",
            "call_model": "eleven-rules",
            "schedule": {"timezone": "Asia/Shanghai", "rules": _weekly_rules(11)},
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "INVALID_REQUEST"

    # v1 的 segments 一条换一条规则，同样受上限约束。
    legacy = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={
            "upstream_model": "fake-model",
            "call_model": "eleven-segments",
            "schedule": {
                "timezone": "Asia/Shanghai",
                "segments": [
                    {"days": [1], "start": f"{hour:02d}:00", "end": f"{hour + 1:02d}:00"}
                    for hour in range(11)
                ],
            },
        },
    )
    assert legacy.status_code == 422, legacy.text

    # 批量 PATCH 复用同一份 schema：超限整单拒绝，已存的 10 条规则不变。
    patched = await client.patch(
        "/api/channel-models/batch",
        headers=headers,
        json={
            "model_ids": [accepted["id"]],
            "op": "schedule",
            "schedule": {"timezone": "Asia/Shanghai", "rules": _weekly_rules(11)},
        },
    )
    assert patched.status_code == 422, patched.text

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    items = listed.json()["items"]
    assert [item["call_model"] for item in items] == ["ten-rules"]
    assert len(items[0]["schedule"]["rules"]) == 10


def test_retry_after_header_only_when_the_recovery_moment_is_known():
    """SF-3：只有算得出恢复时刻才发 Retry-After；限流保留 1 秒兜底。"""
    from l06_api.errors import _retry_after_headers

    assert _retry_after_headers(503, {}) == {}
    assert _retry_after_headers(503, {"retry_after_s": 5}) == {"Retry-After": "5"}
    assert _retry_after_headers(503, {"retry_after_s": "soon"}) == {}
    assert _retry_after_headers(429, {}) == {"Retry-After": "1"}
    assert _retry_after_headers(404, {"retry_after_s": 5}) == {}
