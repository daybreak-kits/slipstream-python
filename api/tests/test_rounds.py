from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest

from l01_core.timeutil import now_ms
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
    name: str = "轮次渠道",
    slug: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    if slug:
        payload["slug"] = slug
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(
    client,
    headers,
    channel_id: str,
    *,
    upstream_model: str = "fake-model",
    call_model: str = "round-leaf",
    priority: int = 1,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={
            "upstream_model": upstream_model,
            "call_model": call_model,
            "priority": priority,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _leaf_target(model_id: str, priority: int = 1, enabled: bool = True) -> dict[str, Any]:
    return {
        "target_type": "channel_model",
        "channel_model_id": model_id,
        "priority": priority,
        "enabled": enabled,
    }


def _child_target(combo_id: str, priority: int = 1) -> dict[str, Any]:
    return {
        "target_type": "child_combo",
        "child_combo_id": combo_id,
        "priority": priority,
        "enabled": True,
    }


def _rule(status: int | list[int], action: str = "next", body_regex: str | None = None):
    return {"status": status, "body_regex": body_regex, "action": action}


async def _combo(
    client,
    headers,
    name: str,
    targets: list[dict[str, Any]],
    *,
    max_attempts: int = 3,
    rules: list[dict[str, Any]] | None = None,
    default_action: str = "next",
):
    payload = {
        "name": name,
        "description": "",
        "enabled": True,
        "max_attempts": max_attempts,
        "failover_rules": {
            "rules": rules or [],
            "default_action": default_action,
        },
        "targets": targets,
    }
    return await client.post("/api/combos", headers=headers, json=payload)


async def _key(client, headers, label: str = "rounds") -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str, *, stream: bool = False) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }


async def _wait_for_log(client, headers, request_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


def _chat_posts(upstream: FakeUpstreamServer) -> list[dict[str, Any]]:
    return upstream.find("POST", "/chat/completions")


async def _stored_model(app, model_id: str):
    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, model_id)
        assert stored is not None
        return {
            "consecutive_failures": int(stored.consecutive_failures or 0),
            "health_status": stored.health_status,
        }


async def _patch_model(app, model_id: str, **values: Any) -> None:
    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelModelORM, model_id)
        assert stored is not None
        for key, value in values.items():
            setattr(stored, key, value)
        await session.commit()


async def test_one_round_tries_each_available_member_once(client, upstream, app):
    headers = await _token(client)
    broken = await _channel(
        client, headers, upstream.base_url + "/broken", name="坏主", slug="round-bad"
    )
    healthy = await _channel(client, headers, upstream.base_url, name="好备", slug="round-good")
    broken_model = await _model(client, headers, broken["id"], call_model="round-a")
    healthy_model = await _model(
        client, headers, healthy["id"], upstream_model="fake-model-2", call_model="round-b"
    )

    created = await _combo(
        client,
        headers,
        "one-round",
        # 数值越大越先调用：坏叶子取 2，好叶子取 1。
        [_leaf_target(broken_model["id"], 2), _leaf_target(healthy_model["id"], 1)],
        max_attempts=3,
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("one-round"),
    )
    assert response.status_code == 200, response.text

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]
    assert [attempt["round"] for attempt in log["attempts"]] == [1, 1]
    assert len(_chat_posts(upstream)) == 2
    assert (await _stored_model(app, broken_model["id"]))["consecutive_failures"] == 1


async def test_fully_failed_round_counts_as_one_attempt_and_runs_again(client, upstream, app):
    headers = await _token(client)
    broken = await _channel(
        client, headers, upstream.base_url + "/broken", name="一直坏", slug="round-dead"
    )
    broken_model = await _model(client, headers, broken["id"], call_model="round-dead-model")

    created = await _combo(
        client,
        headers,
        "two-rounds",
        [_leaf_target(broken_model["id"], 1)],
        max_attempts=2,
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("two-rounds"),
    )
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "error"]
    assert [attempt["round"] for attempt in log["attempts"]] == [1, 2]
    assert len(_chat_posts(upstream)) == 2
    stored = await _stored_model(app, broken_model["id"])
    assert stored["consecutive_failures"] == 2


async def test_all_members_skipped_returns_503_without_consuming_rounds(client, upstream, app):
    headers = await _token(client)
    disabled_channel = await _channel(
        client, headers, upstream.base_url, name="人工停用", slug="round-disabled"
    )
    open_channel = await _channel(
        client, headers, upstream.base_url, name="熔断中", slug="round-open"
    )
    manual_channel = await _channel(
        client, headers, upstream.base_url, name="成员停用", slug="round-member-off"
    )
    disabled_model = await _model(client, headers, disabled_channel["id"], call_model="off")
    open_model = await _model(client, headers, open_channel["id"], call_model="open")
    manual_model = await _model(client, headers, manual_channel["id"], call_model="manual-off")

    patch = await client.patch(
        f"/api/channels/{disabled_channel['id']}", headers=headers, json={"enabled": False}
    )
    assert patch.status_code == 200, patch.text
    await _patch_model(
        app,
        open_model["id"],
        health_status="open",
        cooldown_until=now_ms() + 60_000,
    )

    created = await _combo(
        client,
        headers,
        "all-skipped",
        [
            _leaf_target(disabled_model["id"], 1),
            _leaf_target(open_model["id"], 2),
            _leaf_target(manual_model["id"], 3, enabled=False),
        ],
        max_attempts=3,
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("all-skipped"),
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == [
        "skipped",
        "skipped",
        "skipped",
    ]
    assert {attempt["error_code"] for attempt in log["attempts"]} == {
        "CHANNEL_DISABLED",
        "CIRCUIT_OPEN",
        "MEMBER_DISABLED",
    }
    assert all(attempt["skipped"] is True for attempt in log["attempts"])
    assert all(attempt["round"] == 1 for attempt in log["attempts"])
    assert _chat_posts(upstream) == []


async def test_action_stop_ends_the_request_without_finishing_the_round(client, upstream):
    headers = await _token(client)
    policy = await _channel(
        client, headers, upstream.base_url + "/content-policy", name="审核拦截", slug="round-policy"
    )
    healthy = await _channel(client, headers, upstream.base_url, name="本应可切", slug="round-backup")
    policy_model = await _model(client, headers, policy["id"], call_model="policy")
    healthy_model = await _model(
        client, headers, healthy["id"], upstream_model="fake-model-2", call_model="backup"
    )

    created = await _combo(
        client,
        headers,
        "stop-combo",
        [_leaf_target(policy_model["id"], 2), _leaf_target(healthy_model["id"], 1)],
        max_attempts=3,
        rules=[_rule(400, "stop", "(?i)content policy")],
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("stop-combo"),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_REQUEST"

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error"]
    assert log["attempts"][0]["round"] == 1
    assert len(_chat_posts(upstream)) == 1


async def test_action_next_can_retry_a_status_that_is_normally_terminal(client, upstream):
    headers = await _token(client)
    policy = await _channel(
        client, headers, upstream.base_url + "/content-policy", name="可切 400", slug="round-next-400"
    )
    healthy = await _channel(client, headers, upstream.base_url, name="接住", slug="round-next-good")
    policy_model = await _model(client, headers, policy["id"], call_model="policy-next")
    healthy_model = await _model(
        client, headers, healthy["id"], upstream_model="fake-model-2", call_model="backup-next"
    )

    created = await _combo(
        client,
        headers,
        "next-400",
        [_leaf_target(policy_model["id"], 2), _leaf_target(healthy_model["id"], 1)],
        max_attempts=1,
        rules=[_rule(400, "next")],
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("next-400"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]


async def test_child_combo_rules_and_rounds_are_ignored(client, upstream):
    headers = await _token(client)
    child_channel = await _channel(
        client, headers, upstream.base_url + "/broken", name="子组合坏叶", slug="round-child-bad"
    )
    root_channel = await _channel(
        client, headers, upstream.base_url, name="根组合好叶", slug="round-root-good"
    )
    child_leaf = await _model(client, headers, child_channel["id"], call_model="child-leaf")
    root_leaf = await _model(
        client, headers, root_channel["id"], upstream_model="fake-model-2", call_model="root-leaf"
    )

    child = await _combo(
        client,
        headers,
        "round-child",
        [_leaf_target(child_leaf["id"], 1)],
        max_attempts=5,
        rules=[_rule(500, "stop")],
    )
    assert child.status_code == 201, child.text

    root = await _combo(
        client,
        headers,
        "round-root",
        [_child_target(child.json()["id"], 2), _leaf_target(root_leaf["id"], 1)],
        max_attempts=1,
    )
    assert root.status_code == 201, root.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("round-root"),
    )
    assert response.status_code == 200, response.text

    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    # The child's stop-on-500 rule did not stop the root request, and its
    # max_attempts=5 did not turn one root round into five child rounds.
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]
    assert [attempt["round"] for attempt in log["attempts"]] == [1, 1]
    assert len(_chat_posts(upstream)) == 2


async def test_failure_counters_are_per_leaf_and_success_resets(client, upstream, app):
    headers = await _token(client)
    first = await _channel(
        client, headers, upstream.base_url + "/broken", name="坏一", slug="counter-a"
    )
    second = await _channel(
        client, headers, upstream.base_url + "/broken?x=2", name="坏二", slug="counter-b"
    )
    healthy = await _channel(client, headers, upstream.base_url, name="健康", slug="counter-c")
    first_model = await _model(client, headers, first["id"], call_model="counter-a-model")
    second_model = await _model(client, headers, second["id"], call_model="counter-b-model")
    healthy_model = await _model(
        client, headers, healthy["id"], upstream_model="fake-model-2", call_model="counter-c-model"
    )
    await _patch_model(app, healthy_model["id"], consecutive_failures=7)

    created = await _combo(
        client,
        headers,
        "counter-combo",
        [_leaf_target(first_model["id"], 1), _leaf_target(second_model["id"], 2)],
        max_attempts=2,
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    failed = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("counter-combo"),
    )
    assert failed.status_code == 502, failed.text
    assert (await _stored_model(app, first_model["id"]))["consecutive_failures"] == 2
    assert (await _stored_model(app, second_model["id"]))["consecutive_failures"] == 2

    healthy_combo = await _combo(
        client,
        headers,
        "counter-healthy",
        [_leaf_target(healthy_model["id"], 1)],
        max_attempts=1,
    )
    assert healthy_combo.status_code == 201, healthy_combo.text
    ok = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("counter-healthy"),
    )
    assert ok.status_code == 200, ok.text
    assert (await _stored_model(app, healthy_model["id"]))["consecutive_failures"] == 0


async def test_streaming_uses_the_same_rounds(client, upstream):
    headers = await _token(client)
    broken = await _channel(
        client, headers, upstream.base_url + "/broken", name="流式坏", slug="stream-bad"
    )
    broken_model = await _model(client, headers, broken["id"], call_model="stream-round")

    created = await _combo(
        client,
        headers,
        "stream-rounds",
        [_leaf_target(broken_model["id"], 1)],
        max_attempts=2,
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("stream-rounds", stream=True),
    )
    assert response.status_code == 502, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is True
    assert [attempt["round"] for attempt in log["attempts"]] == [1, 2]
    assert len(_chat_posts(upstream)) == 2


async def test_combo_settings_are_editable_and_validated(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, slug="settings-channel")
    model = await _model(client, headers, channel["id"], call_model="settings-leaf")

    created = await _combo(
        client,
        headers,
        "settings-combo",
        [_leaf_target(model["id"])],
        max_attempts=2,
        rules=[_rule([429, "500-599"], "next"), _rule(400, "stop", "content")],
        default_action="stop",
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["max_attempts"] == 2
    assert body["failover_rules"] == {
        "rules": [
            {"status": [429, "500-599"], "body_regex": None, "action": "next"},
            {"status": [400], "body_regex": "content", "action": "stop"},
        ],
        "default_action": "stop",
    }

    updated = await client.patch(
        f"/api/combos/{body['id']}",
        headers=headers,
        json={"max_attempts": 4, "failover_rules": {"rules": [_rule(401, "stop")]}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["max_attempts"] == 4
    assert updated.json()["failover_rules"]["rules"][0]["status"] == [401]
    assert updated.json()["failover_rules"]["default_action"] == "next"

    bad_regex = await client.patch(
        f"/api/combos/{body['id']}",
        headers=headers,
        json={"failover_rules": {"rules": [{"status": [400], "body_regex": "("}]}},
    )
    assert bad_regex.status_code == 400, bad_regex.text
    assert bad_regex.json()["code"] == "INVALID_REQUEST"

    # The legacy bare-rule-list format is still accepted.
    legacy = await client.patch(
        f"/api/combos/{body['id']}",
        headers=headers,
        json={"failover_rules": [_rule(403, "next")]},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["failover_rules"]["rules"][0]["status"] == [403]
