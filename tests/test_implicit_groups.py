from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from l04_repo import orm
from l05_service import routing
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
    name: str = "隐式分组渠道",
    slug: str | None = None,
    priority: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "priority": priority,
        "enabled": True,
    }
    if slug:
        payload["slug"] = slug
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(
    client, headers, channel_id: str, *, call_model: str, **overrides
) -> dict[str, Any]:
    payload = {
        "upstream_model": "fake-model",
        "call_model": call_model,
        **overrides,
    }
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers, label: str = "groups") -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str, *, stream: bool = False) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }


async def _groups(client, headers) -> list[dict[str, Any]]:
    response = await client.get("/api/groups", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _wait_for_log(client, headers, request_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


async def test_single_member_group_is_callable_by_bare_name(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="solo")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("solo"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "solo"
    assert upstream.find("POST", "/v1/chat/completions")

    groups = await _groups(client, headers)
    assert [group["name"] for group in groups] == ["solo"]
    # 隐式分组对外报二元 kind=combo + origin=auto（只读派生）。
    assert groups[0]["kind"] == "combo"
    assert groups[0]["origin"] == "auto"
    assert groups[0]["member_count"] == 1
    assert groups[0]["callable"] is True
    assert groups[0]["members"][0]["channel_slug"] == channel["slug"]
    assert groups[0]["members"][0]["priority"] == 1

    models = (await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key['key']}"}
    )).json()["data"]
    assert "solo" in [item["id"] for item in models]
    assert f"{channel['slug']}/solo" in [item["id"] for item in models]


async def test_group_fails_over_to_next_priority_on_upstream_error(client, upstream):
    headers = await _token(client)
    # 数值越大越先调用：坏渠道优先级更高，先失败再落到备用渠道。
    failing = await _channel(
        client, headers, upstream.base_url + "/broken", name="主渠道", priority=2
    )
    healthy = await _channel(
        client, headers, upstream.base_url, name="备用渠道", priority=1
    )
    await _model(client, headers, failing["id"], call_model="shared")
    await _model(client, headers, healthy["id"], call_model="shared")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("shared"),
    )

    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "备用渠道"
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]
    assert log["attempts"][0]["error_code"] == "upstream"
    assert log["attempts"][1]["channel_name"] == "备用渠道"


async def test_equal_priority_layers_are_shuffled_but_priority_stays_ordered():
    def candidate(model_id: str, priority: int):
        return routing.Candidate(
            channel=SimpleNamespace(enabled=True, protocol="openai_chat"),
            model=SimpleNamespace(id=model_id, enabled=True, priority=priority),
            priority=priority,
        )

    items = [candidate("a", 1), candidate("b", 1), candidate("c", 2)]
    first_ids: set[str] = set()
    tail_ids: set[tuple[str, ...]] = set()
    for seed in range(40):
        ordered = routing.order_candidates(items, rng=random.Random(seed))
        # 层按 priority 降序（数值越大越先），层内保持等概率随机。
        assert [item.priority for item in ordered] == [2, 1, 1]
        first_ids.add(ordered[0].model.id)
        tail_ids.add(tuple(item.model.id for item in ordered[1:]))

    # 高优先级整层压住低优先级层，只有同层的 a/b 会被洗牌。
    assert first_ids == {"c"}
    assert tail_ids == {("a", "b"), ("b", "a")}


async def test_higher_priority_is_tried_first_even_when_both_are_healthy(client, upstream):
    headers = await _token(client)
    first = await _channel(
        client, headers, upstream.base_url, name="优先级一", priority=2
    )
    second = await _channel(
        client, headers, upstream.base_url, name="优先级二", priority=1
    )
    await _model(client, headers, first["id"], call_model="ordered")
    await _model(client, headers, second["id"], call_model="ordered")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("ordered"),
    )

    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "优先级一"
    assert [attempt["status"] for attempt in log["attempts"]] == ["success"]


async def test_call_model_with_slash_is_not_grouped_but_resolves_by_native_id(client, upstream):
    """A provider-native id stays out of the implicit groups.

    The catalog still publishes it only under the qualified name, but a client
    that asks for the native id is routed to the single channel owning it.
    """
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"], call_model="openai/gpt-4o")

    groups = await _groups(client, headers)
    assert groups == []

    key = await _key(client, headers)
    models = (await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key['key']}"}
    )).json()["data"]
    ids = [item["id"] for item in models]
    assert f"{channel['slug']}/openai/gpt-4o" in ids
    assert "openai/gpt-4o" not in ids

    native = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("openai/gpt-4o"),
    )
    assert native.status_code == 200, native.text

    direct = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/openai/gpt-4o"),
    )
    assert direct.status_code == 200, direct.text


async def test_explicit_combo_name_wins_over_group_and_conflicts_are_rejected(client, app, upstream):
    headers = await _token(client)
    group_channel = await _channel(
        client, headers, upstream.base_url + "/broken", name="分组渠道", priority=1
    )
    combo_channel = await _channel(
        client, headers, upstream.base_url, name="组合渠道", priority=2
    )
    group_model = await _model(
        client, headers, group_channel["id"], call_model="precedence"
    )
    combo_model = await _model(
        client, headers, combo_channel["id"], call_model="precedence"
    )

    async with app.state.session_factory() as session:
        combo = orm.ComboORM(id="CB000000000000000000000010", name="precedence")
        session.add(combo)
        await session.commit()
        session.add(
            orm.ComboTargetORM(
                id="CT000000000000000000000010",
                combo_id=combo.id,
                target_type="channel_model",
                channel_model_id=combo_model["id"],
                priority=1,
            )
        )
        await session.commit()

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("precedence"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "组合渠道"

    # A new channel model cannot reuse the combo name.
    collision = await client.post(
        f"/api/channels/{group_channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "precedence"},
    )
    assert collision.status_code == 409
    assert collision.json()["code"] == "CONFLICT"

    # Bare names share the namespace with channel slugs.
    assert group_model["id"] != combo_model["id"]
    slug_collision = await client.post(
        f"/api/channels/{group_channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": combo_channel["slug"]},
    )
    assert slug_collision.status_code == 409
    assert slug_collision.json()["code"] == "CONFLICT"


async def test_group_with_only_disabled_channels_is_not_callable(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="停用渠道")
    await _model(client, headers, channel["id"], call_model="offline")

    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelORM, channel["id"])
        assert stored is not None
        stored.enabled = False
        await session.commit()

    groups = await _groups(client, headers)
    assert len(groups) == 1
    assert groups[0]["name"] == "offline"
    assert groups[0]["callable"] is False

    key = await _key(client, headers)
    models = (await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key['key']}"}
    )).json()["data"]
    assert "offline" not in [item["id"] for item in models]

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("offline"),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NO_AVAILABLE_CHANNEL"


async def test_all_group_members_failing_returns_last_error(client, upstream):
    headers = await _token(client)
    first = await _channel(
        client, headers, upstream.base_url + "/broken", name="坏一", priority=1
    )
    second = await _channel(
        client, headers, upstream.base_url + "/broken?x=2", name="坏二", priority=2
    )
    await _model(client, headers, first["id"], call_model="dead")
    await _model(client, headers, second["id"], call_model="dead")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("dead"),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "error"]


async def test_unknown_bare_name_is_404(client):
    headers = await _token(client)
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("never-configured"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


async def test_streaming_bare_name_fails_over_before_first_byte(client, upstream):
    headers = await _token(client)
    # 数值越大越先调用：坏渠道取 2，先失败再落到备用渠道。
    failing = await _channel(
        client, headers, upstream.base_url + "/broken", name="流式主", priority=2
    )
    healthy = await _channel(
        client, headers, upstream.base_url, name="流式备", priority=1
    )
    await _model(client, headers, failing["id"], call_model="streamed")
    await _model(client, headers, healthy["id"], call_model="streamed")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("streamed", stream=True),
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"content": "hel"' in response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is True
    assert log["http_status"] == 200
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]
    assert log["channel_name"] == "流式备"
