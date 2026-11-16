"""节点级在飞上限：429 快速失败、槽位释放与流式持有语义。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from l05_service import chat as chat_service
from l05_service.inflight import get_inflight_gate, reset_inflight_gate
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Any:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def _fresh_gate() -> Any:
    reset_inflight_gate()
    yield
    reset_inflight_gate()


def _set_cap(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    # cap 每次 acquire 动态读 route_settings 进程缓存（ADR-077：设置页
    # PATCH 热生效）；测试直接替换整个缓存对象，测后 monkeypatch 自动还原。
    # is_default=False 模拟「设置页已保存的 DB 行」——这是它压住 env 兜底的
    # 判据（inflight._cap 只在 is_default=True 时读 GATEWAY_INFLIGHT_CAP）。
    from l05_service import route_settings

    monkeypatch.setattr(
        route_settings,
        "_CACHE",
        [
            route_settings.RouteSettings(
                strategy="",
                window_minutes=30,
                inflight_cap=value,
                is_default=False,
            ),
            time.monotonic(),
        ],
    )


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def _channel(client, headers, url: str, *, name: str, slug: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, *, call_model: str) -> None:
    response = await client.post(
        "/api/channels/" + channel_id + "/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": call_model, "enabled": True},
    )
    assert response.status_code == 201, response.text


async def _key(client, headers, label: str) -> str:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _chat_body(model: str, *, stream: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if stream:
        body["stream"] = True
    return body


async def _setup(
    client,
    upstream: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cap: int | None,
    slug: str,
) -> Any:
    """配好渠道/模型/密钥并返回 (gate, /v1 鉴权头)。

    cap 非 None 时预置进 route_settings 进程缓存（模拟 DB 行）；
    cap=None 不写缓存，走「无 DB 行」路径（env 兜底用例）。
    """
    if cap is not None:
        _set_cap(monkeypatch, cap)
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="IC " + slug, slug=slug
    )
    await _model(client, headers, channel["id"], call_model="call-model")
    key = await _key(client, headers, "ic-" + slug)
    return get_inflight_gate(), {"Authorization": "Bearer " + key}


async def test_over_cap_returns_429_with_retry_after(client, upstream, monkeypatch) -> None:
    gate, api = await _setup(client, upstream, monkeypatch, cap=1, slug="cap429")
    gate.acquire()  # 手动占满唯一槽位，模拟一个在飞请求
    try:
        response = await client.post(
            "/v1/chat/completions", headers=api, json=_chat_body("call-model")
        )
    finally:
        gate.release()
    assert response.status_code == 429, response.text
    assert response.headers["retry-after"] == "1"
    body = response.json()
    assert body["code"] == "INFLIGHT_LIMITED"
    assert body["details"]["limit"] == 1


async def test_slot_is_released_after_completion(client, upstream, monkeypatch) -> None:
    gate, api = await _setup(client, upstream, monkeypatch, cap=1, slug="release")
    first = await client.post(
        "/v1/chat/completions", headers=api, json=_chat_body("call-model")
    )
    assert first.status_code == 200, first.text
    assert gate.inflight == 0
    second = await client.post(
        "/v1/chat/completions", headers=api, json=_chat_body("call-model")
    )
    assert second.status_code == 200, second.text
    assert gate.inflight == 0


async def test_streaming_holds_the_slot_until_stream_end(
    client, upstream, monkeypatch
) -> None:
    gate, api = await _setup(client, upstream, monkeypatch, cap=1, slug="stream")
    real_start = chat_service.start_direct_stream
    entered = asyncio.Event()
    go = asyncio.Event()

    async def probe_events(real_events: Any) -> Any:
        # real_events 就是 run.events() 返回的 async generator（非函数），直接迭代；
        # handler 早已返回 StreamingResponse：迭代期间槽位必须仍被占用。
        assert gate.inflight == 1
        async for chunk in real_events:
            yield chunk
        # 最后一个 chunk 消费完、_gated_events 的 finally 尚未执行。
        assert gate.inflight == 1

    async def spy_start(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await go.wait()
        run = await real_start(*args, **kwargs)

        class _RunProxy:
            """包装 run：events() 带槽位探针，其余属性直通，避免 slots 限制。"""

            def __init__(self) -> None:
                self.response = run.response
                self.registry = None
                self.finalize = run.finalize

            def events(self) -> Any:
                return probe_events(run.events())

        return _RunProxy()

    monkeypatch.setattr(chat_service, "start_direct_stream", spy_start)
    stream_task = asyncio.create_task(
        client.post(
            "/v1/chat/completions",
            headers=api,
            json=_chat_body("call-model", stream=True),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        rejected = await asyncio.wait_for(
            client.post(
                "/v1/chat/completions", headers=api, json=_chat_body("call-model")
            ),
            timeout=5,
        )
        assert rejected.status_code == 429, rejected.text
        assert rejected.headers["retry-after"] == "1"
    finally:
        go.set()
        response = await asyncio.wait_for(stream_task, timeout=30)
    assert response.status_code == 200, response.text
    assert gate.inflight == 0
    after = await client.post(
        "/v1/chat/completions", headers=api, json=_chat_body("call-model")
    )
    assert after.status_code == 200, after.text


async def test_zero_cap_disables_admission(client, upstream, monkeypatch) -> None:
    gate, api = await _setup(client, upstream, monkeypatch, cap=0, slug="free")
    gate.acquire()
    gate.acquire()
    gate.acquire()  # cap=0：acquire 直接放行、不计数
    assert gate.inflight == 0
    response = await client.post(
        "/v1/chat/completions", headers=api, json=_chat_body("call-model")
    )
    assert response.status_code == 200, response.text
    assert gate.inflight == 0


async def test_admin_patch_tightens_cap_without_restart(
    client, upstream, monkeypatch
) -> None:
    """端到端：admin PATCH /api/settings 改 cap，gate 无需重启即时生效。"""
    gate, api = await _setup(client, upstream, monkeypatch, cap=4, slug="patch")
    admin = await _token(client)
    patched = await client.patch(
        "/api/settings", headers=admin, json={"gateway_inflight_cap": 1}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["gateway_inflight_cap"] == 1
    gate.acquire()  # cap=1：第一个「在飞」请求占住唯一槽位
    try:
        response = await client.post(
            "/v1/chat/completions", headers=api, json=_chat_body("call-model")
        )
    finally:
        gate.release()
    assert response.status_code == 429, response.text
    assert response.headers["retry-after"] == "1"
    assert response.json()["code"] == "INFLIGHT_LIMITED"
    # 放宽同样即时生效：同一进程内后续请求恢复放行。
    widened = await client.patch(
        "/api/settings", headers=admin, json={"gateway_inflight_cap": 4}
    )
    assert widened.status_code == 200, widened.text
    assert widened.json()["gateway_inflight_cap"] == 4
    after = await client.post(
        "/v1/chat/completions", headers=api, json=_chat_body("call-model")
    )
    assert after.status_code == 200, after.text
    assert gate.inflight == 0


async def test_env_fallback_and_db_row_suppression(
    client, upstream, monkeypatch
) -> None:
    """ADR-077 决策 7：无 DB 行时 env 兜底生效；存行后 DB 值压住 env。"""
    from l01_core import config
    from l05_service import route_settings

    route_settings._cache_invalidate()  # 缓存回落默认（is_default=True）：无 DB 行
    settings_copy = config.get_settings().model_copy(
        update={"gateway_inflight_cap": 1}
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings_copy)
    gate, api = await _setup(client, upstream, monkeypatch, cap=None, slug="envfb")
    gate.acquire()  # env cap=1：占住唯一槽位
    try:
        rejected = await client.post(
            "/v1/chat/completions", headers=api, json=_chat_body("call-model")
        )
    finally:
        gate.release()
    assert rejected.status_code == 429, rejected.text
    assert rejected.headers["retry-after"] == "1"
    assert rejected.json()["details"]["limit"] == 1
    # 设置页存行（缓存 is_default=False，cap=0=关闭准入）：env=1 被压住，
    # 请求放行；若实现错误 env 优先，这里会是 429。
    _set_cap(monkeypatch, 0)
    gate.acquire()
    try:
        allowed = await client.post(
            "/v1/chat/completions", headers=api, json=_chat_body("call-model")
        )
    finally:
        gate.release()
    assert allowed.status_code == 200, allowed.text
    assert gate.inflight == 0
