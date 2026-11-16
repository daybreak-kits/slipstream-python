from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

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
    name: str = "组合渠道",
    slug: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": enabled,
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
    call_model: str = "leaf",
    priority: int = 1,
    enabled: bool = True,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={
            "upstream_model": upstream_model,
            "call_model": call_model,
            "priority": priority,
            "enabled": enabled,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _combo(
    name: str,
    targets: list[dict[str, Any]],
    *,
    enabled: bool = True,
    description: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "enabled": enabled,
        "targets": targets,
    }


def _leaf_target(model_id: str, priority: int = 1, enabled: bool = True) -> dict[str, Any]:
    return {
        "target_type": "channel_model",
        "channel_model_id": model_id,
        "priority": priority,
        "enabled": enabled,
    }


def _child_target(combo_id: str, priority: int = 1, enabled: bool = True) -> dict[str, Any]:
    return {
        "target_type": "child_combo",
        "child_combo_id": combo_id,
        "priority": priority,
        "enabled": enabled,
    }


async def _create_combo(client, headers, payload: dict[str, Any], expected: int = 201):
    return await client.post("/api/combos", headers=headers, json=payload)


async def _key(client, headers, label: str = "combos") -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def _wait_for_log(client, headers, request_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


async def test_combo_crud_nested_priority_and_failover(client, upstream):
    headers = await _token(client)
    primary = await _channel(client, headers, upstream.base_url, name="主渠道", slug="primary")
    backup = await _channel(client, headers, upstream.base_url, name="备用渠道", slug="backup")
    primary_model = await _model(
        client, headers, primary["id"], upstream_model="fake-model", call_model="chat-a"
    )
    backup_model = await _model(
        client, headers, backup["id"], upstream_model="fake-model-2", call_model="chat-b"
    )

    created = await _create_combo(
        client,
        headers,
        _combo(
            "team-chat",
            [_leaf_target(backup_model["id"], priority=2), _leaf_target(primary_model["id"], 1)],
            description="主备组合",
        ),
    )
    assert created.status_code == 201, created.text
    combo = created.json()
    assert combo["name"] == "team-chat"
    assert combo["depth"] == 1
    assert combo["member_count"] == 2
    # 数值越大越先调用：priority 2 的备用渠道排在前面，成员列表也按降序返回。
    assert [member["priority"] for member in combo["members"]] == [2, 1]
    assert combo["callable"] is True

    listed = await client.get("/api/combos", headers=headers)
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["items"]] == ["team-chat"]

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("team-chat"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["attempts"][0]["upstream_model"] == "fake-model-2"

    # Update replaces members and priority; primary (now priority 2) wins.
    updated = await client.patch(
        f"/api/combos/{combo['id']}",
        headers=headers,
        json={
            "targets": [
                _leaf_target(primary_model["id"], priority=2),
                _leaf_target(backup_model["id"], priority=1),
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    assert [member["priority"] for member in updated.json()["members"]] == [2, 1]

    second = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("team-chat"),
    )
    assert second.status_code == 200, second.text
    second_log = await _wait_for_log(client, headers, second.headers["X-Request-ID"])
    assert second_log["attempts"][0]["upstream_model"] == "fake-model"

    models = (await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key['key']}"}
    )).json()["data"]
    assert "team-chat" in [item["id"] for item in models]
    assert next(item for item in models if item["id"] == "team-chat")["owned_by"] == "combo"


async def test_combo_three_level_nesting_flattens_priority_path(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    leaf = await _model(client, headers, channel["id"], call_model="nested-leaf")

    grandchild = await _create_combo(
        client, headers, _combo("grandchild", [_leaf_target(leaf["id"])])
    )
    assert grandchild.status_code == 201, grandchild.text
    child = await _create_combo(
        client,
        headers,
        _combo("child", [_child_target(grandchild.json()["id"], priority=1)]),
    )
    assert child.status_code == 201, child.text
    root = await _create_combo(
        client,
        headers,
        _combo("root", [_child_target(child.json()["id"], priority=1)]),
    )
    assert root.status_code == 201, root.text
    assert root.json()["depth"] == 3

    detail = await client.get(f"/api/combos/{root.json()['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["depth"] == 3
    assert detail.json()["members"][0]["target_type"] == "child_combo"
    assert detail.json()["members"][0]["child_combo_name"] == "child"

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("root"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["attempts"][0]["upstream_model"] == "fake-model"


async def test_combo_priority_path_orders_nested_members_before_later_root_layers(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="首渠道", slug="first")
    second = await _channel(client, headers, upstream.base_url, name="次渠道", slug="second")
    first_model = await _model(
        client, headers, first["id"], upstream_model="fake-model", call_model="one"
    )
    second_model = await _model(
        client, headers, second["id"], upstream_model="fake-model-2", call_model="two"
    )

    child = await _create_combo(
        client, headers, _combo("nested-child", [_leaf_target(second_model["id"])])
    )
    assert child.status_code == 201, child.text
    # 子组合优先级 2 > 叶子 1：整棵子树的根层先跑，因此第一条尝试落在 child 内。
    root = await _create_combo(
        client,
        headers,
        _combo(
            "nested-root",
            [
                _child_target(child.json()["id"], priority=2),
                _leaf_target(first_model["id"], priority=1),
            ],
        ),
    )
    assert root.status_code == 201, root.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("nested-root"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["attempts"][0]["upstream_model"] == "fake-model-2"


async def test_combo_fails_over_within_layer_then_next_priority(client, upstream):
    headers = await _token(client)
    broken = await _channel(client, headers, upstream.base_url + "/broken", name="坏渠道", slug="bad")
    healthy = await _channel(client, headers, upstream.base_url, name="好渠道", slug="good")
    broken_model = await _model(
        client, headers, broken["id"], upstream_model="fake-model", call_model="bad-model"
    )
    healthy_model = await _model(
        client, headers, healthy["id"], upstream_model="fake-model-2", call_model="good-model"
    )

    created = await _create_combo(
        client,
        headers,
        _combo(
            "failover-combo",
            [
                # 坏渠道优先级更高：先试它，失败后落到同组合的下一层好渠道。
                _leaf_target(broken_model["id"], priority=2),
                _leaf_target(healthy_model["id"], priority=1),
            ],
        ),
    )
    assert created.status_code == 201, created.text

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("failover-combo"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]


async def test_combo_name_conflicts_are_rejected(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, slug="named")
    await _model(client, headers, channel["id"], call_model="shared-model")
    key = await _key(client, headers)

    slash = await _create_combo(
        client, headers, _combo("with/slash", [])
    )
    assert slash.status_code == 400
    assert slash.json()["code"] == "INVALID_REQUEST"

    slug_conflict = await _create_combo(
        client, headers, _combo("named", [])
    )
    assert slug_conflict.status_code == 409
    assert slug_conflict.json()["code"] == "CONFLICT"

    group_conflict = await _create_combo(
        client, headers, _combo("shared-model", [])
    )
    assert group_conflict.status_code == 409
    assert group_conflict.json()["code"] == "CONFLICT"

    first = await _create_combo(client, headers, _combo("unique-combo", []))
    assert first.status_code == 201, first.text
    duplicate = await _create_combo(client, headers, _combo("unique-combo", []))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CONFLICT"

    # A channel model still cannot take an existing combo name.
    model_conflict = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "unique-combo"},
    )
    assert model_conflict.status_code == 409


async def test_combo_rejects_duplicate_targets(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="dup")

    response = await _create_combo(
        client,
        headers,
        _combo(
            "duplicate-targets",
            [_leaf_target(model["id"]), _leaf_target(model["id"], priority=2)],
        ),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert "重复" in response.json()["message"]


async def test_combo_rejects_cycle_and_self_reference(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="cycle-leaf")

    first = await _create_combo(client, headers, _combo("cycle-a", [_leaf_target(model["id"])]))
    second = await _create_combo(
        client, headers, _combo("cycle-b", [_child_target(first.json()["id"])])
    )
    assert first.status_code == 201 and second.status_code == 201

    cycle = await client.patch(
        f"/api/combos/{first.json()['id']}",
        headers=headers,
        json={"targets": [_child_target(second.json()["id"])]},
    )
    assert cycle.status_code == 400
    assert cycle.json()["code"] == "INVALID_REQUEST"
    assert "环" in cycle.json()["message"] or "循环" in cycle.json()["message"]

    self_reference = await client.patch(
        f"/api/combos/{first.json()['id']}",
        headers=headers,
        json={"targets": [_child_target(first.json()["id"])]},
    )
    assert self_reference.status_code == 400
    assert "环" in self_reference.json()["message"] or "循环" in self_reference.json()["message"]


async def test_combo_rejects_more_than_three_levels(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="deep-leaf")

    level3 = await _create_combo(
        client, headers, _combo("depth-3", [_leaf_target(model["id"])])
    )
    assert level3.status_code == 201
    level2 = await _create_combo(
        client, headers, _combo("depth-2", [_child_target(level3.json()["id"])])
    )
    assert level2.status_code == 201
    level1 = await _create_combo(
        client, headers, _combo("depth-1", [_child_target(level2.json()["id"])])
    )
    assert level1.status_code == 201

    too_deep = await _create_combo(
        client,
        headers,
        _combo("depth-0", [_child_target(level1.json()["id"])]),
    )
    assert too_deep.status_code == 400
    assert too_deep.json()["code"] == "INVALID_REQUEST"
    assert "3" in too_deep.json()["message"] or "层" in too_deep.json()["message"]


async def test_combo_update_cannot_deepen_an_existing_parent_beyond_three(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="grow-leaf")

    c = await _create_combo(client, headers, _combo("grow-c", [_leaf_target(model["id"])]))
    b = await _create_combo(
        client, headers, _combo("grow-b", [_child_target(c.json()["id"])])
    )
    a = await _create_combo(
        client, headers, _combo("grow-a", [_child_target(b.json()["id"])])
    )
    assert c.status_code == 201 and b.status_code == 201 and a.status_code == 201

    # A -> B -> C is already 3 levels; adding a child combo to C would make A 4 deep.
    response = await client.patch(
        f"/api/combos/{c.json()['id']}",
        headers=headers,
        json={"targets": [_child_target(c.json()["id"], priority=1)]},
    )
    # Self reference is caught as a cycle before depth.
    assert response.status_code == 400


async def test_combo_rejects_disabled_child_and_disabled_combo_is_not_callable(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="disabled-leaf")

    child = await _create_combo(
        client, headers, _combo("disabled-child", [_leaf_target(model["id"])], enabled=False)
    )
    assert child.status_code == 201, child.text
    rejected = await _create_combo(
        client,
        headers,
        _combo("references-disabled", [_child_target(child.json()["id"])]),
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "CONFLICT"
    assert "已封禁" in rejected.json()["message"]

    disabled_root = await _create_combo(
        client,
        headers,
        _combo("disabled-root", [_leaf_target(model["id"])], enabled=False),
    )
    assert disabled_root.status_code == 201

    key = await _key(client, headers)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("disabled-root"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


async def test_combo_rejects_invalid_target_payloads(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="valid-leaf")

    missing_model = await _create_combo(
        client,
        headers,
        _combo(
            "missing-model",
            [{"target_type": "channel_model", "channel_model_id": "does-not-exist"}],
        ),
    )
    assert missing_model.status_code == 400

    wrong_shape = await _create_combo(
        client,
        headers,
        _combo(
            "wrong-shape",
            [
                {
                    "target_type": "child_combo",
                    "channel_model_id": model["id"],
                    "priority": 1,
                }
            ],
        ),
    )
    assert wrong_shape.status_code == 400


async def test_delete_combo_referenced_by_parent_is_blocked(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="delete-leaf")

    child = await _create_combo(client, headers, _combo("delete-child", [_leaf_target(model["id"])]))
    parent = await _create_combo(
        client, headers, _combo("delete-parent", [_child_target(child.json()["id"])])
    )
    assert child.status_code == 201 and parent.status_code == 201

    blocked = await client.delete(f"/api/combos/{child.json()['id']}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CONFLICT"

    deleted = await client.delete(f"/api/combos/{parent.json()['id']}", headers=headers)
    assert deleted.status_code == 204


def test_priority_path_shuffles_within_the_same_nested_layer():
    def candidate(model_id: str, path: tuple[int, ...]):
        return routing.Candidate(
            channel=SimpleNamespace(enabled=True, protocol="openai_chat"),
            model=SimpleNamespace(id=model_id, enabled=True, priority=path[-1]),
            priority=path[0],
            priority_path=path,
        )

    items = [
        candidate("a", (1, 1)),
        candidate("b", (1, 1)),
        candidate("c", (1, 2)),
        candidate("d", (2,)),
    ]
    orderings: set[tuple[str, ...]] = set()
    for seed in range(30):
        ordered = routing.order_candidates(items, rng=__import__("random").Random(seed))
        assert [item.priority_path for item in ordered] == [(2,), (1, 2), (1, 1), (1, 1)]
        orderings.add(tuple(item.model.id for item in ordered if item.priority_path == (1, 1)))
    assert orderings == {("a", "b"), ("b", "a")}
