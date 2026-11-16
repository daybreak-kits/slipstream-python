"""Session affinity: header key, cross-layer promotion, TTL and wiring."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from l05_service import routing
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture(autouse=True)
def clean_affinity() -> Iterator[None]:
    routing.clear_affinity()
    try:
        yield
    finally:
        routing.clear_affinity()


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


def _candidate(model_id: str, priority: int = 1) -> routing.Candidate:
    return routing.Candidate(
        channel=SimpleNamespace(enabled=True, protocol="openai_chat"),
        model=SimpleNamespace(id=model_id),
        priority=priority,
        priority_path=(priority,),
    )


def _ids(items: list[routing.Candidate]) -> list[str]:
    return [item.model.id for item in items]


def test_session_key_prefers_the_dedicated_header() -> None:
    assert routing.session_key_from_headers({"x-session-id": " s-1 "}) == "s-1"
    assert routing.session_key_from_headers({"X-Session-Id": "s-2"}) == "s-2"
    assert routing.session_key_from_headers({"x-bili-plugin-conversation": "conv"}) == "conv"
    # The dedicated header wins when both arrive.
    assert (
        routing.session_key_from_headers(
            {"x-bili-plugin-conversation": "conv", "x-session-id": "s"}
        )
        == "s"
    )
    assert routing.session_key_from_headers({}) == ""
    assert routing.session_key_from_headers(None) == ""
    assert routing.session_key_from_headers({"x-session-id": "   "}) == ""


def test_affinity_promotes_the_remembered_channel_across_layers() -> None:
    # Candidates arrive already ordered by layer: the higher layer first.
    items = [_candidate("a1", 2), _candidate("b1", 1), _candidate("b2", 1)]

    # Cache prefixes live on the channel that served the previous turn, so the
    # remembered target outranks the layer it happens to sit in.
    routing.remember_affinity("s", "b2")
    assert _ids(routing.apply_affinity(items, "s")) == ["b2", "a1", "b1"]

    routing.remember_affinity("s", "b1")
    assert _ids(routing.apply_affinity(items, "s")) == ["b1", "a1", "b2"]


def test_affinity_falls_back_when_the_remembered_target_is_unavailable() -> None:
    # A target that is not in the admitted list is not callable right now:
    # nothing is promoted and the plain layer order applies.
    items = [_candidate("a1", 2), _candidate("b1", 1)]
    routing.remember_affinity("s", "gone")
    assert _ids(routing.apply_affinity(items, "s")) == ["a1", "b1"]

    # No key, an unknown key and a blank key all keep the incoming order.
    assert _ids(routing.apply_affinity(items, "")) == ["a1", "b1"]
    assert _ids(routing.apply_affinity(items, "other")) == ["a1", "b1"]


def test_affinity_does_not_reorder_the_candidates_it_leaves_behind() -> None:
    # Randomness inside a layer stays the caller's business (order_candidates
    # shuffled it); affinity only lifts one candidate off the front.
    items = [_candidate("a", 1), _candidate("b", 1), _candidate("c", 1)]
    assert _ids(routing.apply_affinity(items, "brand-new")) == ["a", "b", "c"]
    routing.remember_affinity("s", "c")
    assert _ids(routing.apply_affinity(items, "s")) == ["c", "a", "b"]


def test_equal_priority_members_stay_uniform_under_affinity() -> None:
    # Affinity must not bias the per-layer shuffle: with no hint it only passes
    # the ordered candidates through, so both members still start equally often.
    items = [_candidate("a", 1), _candidate("b", 1)]
    first_seen = {"a": 0, "b": 0}
    for seed in range(400):
        ordered = routing.order_candidates(items, random.Random(seed))
        ordered = routing.apply_affinity(ordered, "fresh-session")
        first_seen[_ids(ordered)[0]] += 1
    assert 160 <= first_seen["a"] <= 240


def test_forget_affinity_only_drops_the_matching_target() -> None:
    routing.remember_affinity("s", "m1")
    routing.forget_affinity("s", "m2")
    assert routing.affinity_model_id("s") == "m1"
    routing.forget_affinity("s", "m1")
    assert routing.affinity_model_id("s") is None
    # Missing keys and blank keys are safe no-ops.
    routing.forget_affinity("", "m1")
    routing.forget_affinity(None)
    assert routing.affinity_model_id("s") is None


def test_affinity_expires_after_its_ttl() -> None:
    routing.remember_affinity("s", "m1", now=100.0, ttl_s=60)
    assert routing.affinity_model_id("s", now=159.9) == "m1"
    assert routing.affinity_model_id("s", now=160.0) is None
    assert routing.affinity_model_id("s", now=161.0) is None


def test_affinity_table_is_bounded() -> None:
    total = routing.AFFINITY_MAX_SESSIONS + 5
    for index in range(total):
        routing.remember_affinity(f"s-{index}", f"m-{index}", now=0.0)
    assert routing.affinity_model_id("s-0", now=0.0) is None
    assert routing.affinity_model_id(f"s-{total - 1}", now=0.0) == f"m-{total - 1}"


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(
    client, headers, url: str, *, name: str, priority: int = 1
) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "priority": priority,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, *, call_model: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": call_model},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers) -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": "affinity"})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str, *, stream: bool = False) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }


async def _wait_for_log(
    client, headers, request_id: str, timeout: float = 3.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


async def test_session_header_pins_the_remembered_channel(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="亲和甲", priority=1)
    second = await _channel(client, headers, upstream.base_url, name="亲和乙", priority=1)
    await _model(client, headers, first["id"], call_model="affinity")
    second_model = await _model(client, headers, second["id"], call_model="affinity")
    key = await _key(client, headers)
    auth = {"Authorization": f"Bearer {key['key']}"}

    routing.remember_affinity("conv-1", second_model["id"])
    response = await client.post(
        "/v1/chat/completions",
        headers={**auth, "x-session-id": "conv-1"},
        json=_chat_body("affinity"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "亲和乙"

    # The fallback header carries the key just the same.
    routing.remember_affinity("conv-2", second_model["id"])
    response = await client.post(
        "/v1/chat/completions",
        headers={**auth, "x-bili-plugin-conversation": "conv-2"},
        json=_chat_body("affinity"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "亲和乙"


async def test_successful_request_remembers_the_winner(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="记忆甲", priority=1)
    second = await _channel(client, headers, upstream.base_url, name="记忆乙", priority=1)
    first_model = await _model(client, headers, first["id"], call_model="remember")
    second_model = await _model(client, headers, second["id"], call_model="remember")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key['key']}",
            "x-session-id": "conv-3",
        },
        json=_chat_body("remember"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    remembered = routing.affinity_model_id("conv-3")
    assert remembered in {first_model["id"], second_model["id"]}
    expected = "记忆甲" if remembered == first_model["id"] else "记忆乙"
    assert log["channel_name"] == expected


async def test_cross_layer_affinity_keeps_the_session_on_its_channel(client, upstream):
    headers = await _token(client)
    high = await _channel(client, headers, upstream.base_url, name="高层", priority=2)
    low = await _channel(client, headers, upstream.base_url, name="低层", priority=1)
    high_model = await _model(client, headers, high["id"], call_model="cross-layer")
    low_model = await _model(client, headers, low["id"], call_model="cross-layer")
    key = await _key(client, headers)
    auth = {"Authorization": f"Bearer {key['key']}", "x-session-id": "conv-cross"}

    # Remembered on the lower layer, yet the session must stay on it: the
    # provider-side cache prefix is bound to that channel.
    routing.remember_affinity("conv-cross", low_model["id"])
    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("cross-layer")
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "低层"

    # Once the remembered channel is unavailable the hint falls back to the
    # plain layer order, and the new winner replaces the stale hint.
    patched = await client.patch(
        f"/api/channels/{low['id']}", headers=headers, json={"enabled": False}
    )
    assert patched.status_code == 200, patched.text
    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("cross-layer")
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "高层"
    assert routing.affinity_model_id("conv-cross") == high_model["id"]


async def test_failed_remembered_channel_is_forgotten_and_failover_wins(client, upstream):
    headers = await _token(client)
    broken = await _channel(
        client, headers, upstream.base_url + "/broken", name="坏亲和", priority=2
    )
    healthy = await _channel(client, headers, upstream.base_url, name="好亲和", priority=1)
    broken_model = await _model(client, headers, broken["id"], call_model="affinity-fail")
    healthy_model = await _model(client, headers, healthy["id"], call_model="affinity-fail")
    key = await _key(client, headers)

    routing.remember_affinity("conv-fail", broken_model["id"])
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key['key']}",
            "x-session-id": "conv-fail",
        },
        json=_chat_body("affinity-fail"),
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert [attempt["status"] for attempt in log["attempts"]] == ["error", "success"]
    assert log["channel_name"] == "好亲和"
    # The broken channel is no longer remembered; the failover winner is.
    assert routing.affinity_model_id("conv-fail") == healthy_model["id"]


async def test_failure_of_the_only_candidate_clears_the_memory(client, upstream):
    headers = await _token(client)
    only = await _channel(
        client, headers, upstream.base_url + "/broken", name="唯一坏", priority=1
    )
    only_model = await _model(client, headers, only["id"], call_model="affinity-dead")
    key = await _key(client, headers)

    routing.remember_affinity("conv-dead", only_model["id"])
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key['key']}",
            "x-session-id": "conv-dead",
        },
        json=_chat_body("affinity-dead"),
    )
    assert response.status_code == 502, response.text
    assert routing.affinity_model_id("conv-dead") is None


async def test_stream_terminal_records_the_affinity(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="流式甲", priority=1)
    second = await _channel(client, headers, upstream.base_url, name="流式乙", priority=1)
    first_model = await _model(client, headers, first["id"], call_model="stream-affinity")
    second_model = await _model(client, headers, second["id"], call_model="stream-affinity")
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key['key']}",
            "x-session-id": "conv-stream",
        },
        json=_chat_body("stream-affinity", stream=True),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is True
    expected = first_model["id"] if log["channel_name"] == "流式甲" else second_model["id"]
    assert routing.affinity_model_id("conv-stream") == expected


async def test_stream_affinity_is_written_only_by_the_terminal_marker(monkeypatch) -> None:
    from l05_service import chat
    from l05_service.llm.gateway import LLMError

    calls: list[str] = []

    async def fake_success(session, target, *args, **kwargs) -> None:
        calls.append("success")

    async def fake_failure(session, target, *args, **kwargs) -> None:
        calls.append("failure")

    monkeypatch.setattr(chat, "record_success", fake_success)
    monkeypatch.setattr(chat, "record_failure", fake_failure)

    def run_for(*, error=None):
        run = object.__new__(chat.StreamRun)
        run.stream = SimpleNamespace()
        run.target = SimpleNamespace(model=SimpleNamespace(id="model-x"))
        run.request_id = "rid"
        run.session_key = "conv-stream-unit"
        run.error = error
        return run

    # A client that disappears before the terminal marker is not a success, so
    # neither the breaker nor the affinity table may treat it as one.
    await chat.StreamRun._report_breaker_outcome(run_for(), None, http_status=499)
    assert calls == []
    assert routing.affinity_model_id("conv-stream-unit") is None

    await chat.StreamRun._report_breaker_outcome(run_for(), None, http_status=200)
    assert calls == ["success"]
    assert routing.affinity_model_id("conv-stream-unit") == "model-x"

    # A mid-stream failure clears the memory instead of pinning a broken channel.
    error = LLMError("stream_aborted", "boom", status_code=502, retryable=True)
    await chat.StreamRun._report_breaker_outcome(
        run_for(error=error), None, http_status=502
    )
    assert calls == ["success", "failure"]
    assert routing.affinity_model_id("conv-stream-unit") is None


def test_config_change_drops_remembered_targets() -> None:
    routing.remember_affinity("s", "m1")
    assert routing.affinity_model_id("s") == "m1"

    # A write that can reorder the candidates makes every old hint stale: the
    # next read ignores and evicts it, so plain layer order decides again.
    routing.bump_affinity_epoch()
    assert routing.affinity_model_id("s") is None
    assert "s" not in routing._affinity

    # The next success is remembered under the new epoch.
    routing.remember_affinity("s", "m2")
    assert routing.affinity_model_id("s") == "m2"


async def test_priority_edit_invalidates_the_sticky_session(client, upstream):
    headers = await _token(client)
    high = await _channel(client, headers, upstream.base_url, name="改前高层", priority=2)
    low = await _channel(client, headers, upstream.base_url, name="改前低层", priority=1)
    high_model = await _model(client, headers, high["id"], call_model="epoch-priority")
    low_model = await _model(client, headers, low["id"], call_model="epoch-priority")
    key = await _key(client, headers)
    auth = {"Authorization": f"Bearer {key['key']}", "x-session-id": "conv-epoch"}

    routing.remember_affinity("conv-epoch", low_model["id"])
    # Without a configuration change the sticky hint keeps winning, twice.
    for _ in range(2):
        response = await client.post(
            "/v1/chat/completions", headers=auth, json=_chat_body("epoch-priority")
        )
        assert response.status_code == 200, response.text
        log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
        assert log["channel_name"] == "改前低层"

    # Re-order the layers: the new order must apply at once instead of the
    # session staying glued to a target the user just put behind another one.
    patched = await client.patch(
        f"/api/channels/{high['id']}", headers=headers, json={"priority": 3}
    )
    assert patched.status_code == 200, patched.text

    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("epoch-priority")
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "改前高层"
    assert routing.affinity_model_id("conv-epoch") == high_model["id"]


async def test_combo_member_edit_invalidates_the_remembered_target(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="组合甲", priority=1)
    second = await _channel(client, headers, upstream.base_url, name="组合乙", priority=1)
    first_model = await _model(client, headers, first["id"], call_model="combo-epoch-a")
    second_model = await _model(client, headers, second["id"], call_model="combo-epoch-b")
    created = await client.post(
        "/api/combos",
        headers=headers,
        json={
            "name": "epoch-combo",
            "targets": [
                {
                    "target_type": "channel_model",
                    "channel_model_id": first_model["id"],
                    "priority": 2,
                },
                {
                    "target_type": "channel_model",
                    "channel_model_id": second_model["id"],
                    "priority": 1,
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    combo = created.json()
    key = await _key(client, headers)
    auth = {"Authorization": f"Bearer {key['key']}", "x-session-id": "conv-combo-epoch"}

    routing.remember_affinity("conv-combo-epoch", second_model["id"])
    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("epoch-combo")
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "组合乙"

    # Dropping the remembered member is a combo write: the hint must go.
    patched = await client.patch(
        f"/api/combos/{combo['id']}",
        headers=headers,
        json={
            "targets": [
                {
                    "target_type": "channel_model",
                    "channel_model_id": first_model["id"],
                    "priority": 2,
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    assert routing.affinity_model_id("conv-combo-epoch") is None

    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("epoch-combo")
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "组合甲"
    assert routing.affinity_model_id("conv-combo-epoch") == first_model["id"]


async def test_model_delete_invalidates_the_remembered_target(client, upstream):
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="删模甲", priority=2)
    second = await _channel(client, headers, upstream.base_url, name="删模乙", priority=1)
    first_model = await _model(client, headers, first["id"], call_model="epoch-delete")
    second_model = await _model(client, headers, second["id"], call_model="epoch-delete")
    key = await _key(client, headers)
    auth = {"Authorization": f"Bearer {key['key']}", "x-session-id": "conv-model-epoch"}

    routing.remember_affinity("conv-model-epoch", first_model["id"])
    deleted = await client.delete(
        f"/api/channel-models/{first_model['id']}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text
    assert routing.affinity_model_id("conv-model-epoch") is None

    response = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("epoch-delete")
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["channel_name"] == "删模乙"
    assert routing.affinity_model_id("conv-model-epoch") == second_model["id"]

