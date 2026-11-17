"""/v1/chat/completions 入口层行为钉子：请求体上限（413）、闸门前移与释放。

P1-R6：max_body_bytes 之前全仓无人使用（超大请求解析完才被拒）；在飞闸门
原先在 body 解析与鉴权之后。这里把新语义钉住：

- 413 短路：Content-Length 超限在读体之前拒绝；无该头时按实际长度判；
- 闸门顺序：429 先于 413、先于 401（acquire 在鉴权/读体之前）；
- 释放账目：acquire 失败不占名额；鉴权失败/读体失败后槽位必须归还；
- 信封：413/422 与 errors.py 现有 AppError / RequestValidationError 风格一致。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from l01_core.config import Settings
from l05_service.inflight import get_inflight_gate, reset_inflight_gate
from tests.fake_upstream_server import FakeUpstreamServer


LIMIT = 256


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


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        models_dev_enabled=False,
        max_body_bytes=LIMIT,
    )


def _set_cap(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    """预置 route_settings 进程缓存（is_default=False 模拟设置页已保存的行）。"""
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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": call_model, "enabled": True},
    )
    assert response.status_code == 201, response.text


async def _key(client, headers, label: str) -> str:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()["key"]


async def _api_key(client, upstream, slug: str) -> str:
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url, name="BL " + slug, slug=slug
    )
    await _model(client, headers, channel["id"], call_model="call-model")
    return await _key(client, headers, "bl-" + slug)


async def test_content_length_over_limit_short_circuits_413(client, upstream) -> None:
    api = await _api_key(client, upstream, "cl413")
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api}"},
        json={"model": "call-model", "messages": [{"role": "user", "content": "x" * 500}]},
    )
    assert response.status_code == 413, response.text
    assert "retry-after" not in response.headers
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["max_body_bytes"] == LIMIT
    assert body["request_id"].startswith("req_")
    # 请求体超限在解析与转发之前被拒：上游一个请求都没收到。
    assert upstream.find("POST", "/v1/chat/completions") == []


async def test_actual_length_over_limit_without_content_length(client, upstream) -> None:
    """chunked（无 Content-Length）也一样按实际长度拒。"""
    api = await _api_key(client, upstream, "chunk413")
    payload = (
        b'{"model": "call-model", "messages": [{"role": "user", "content": "'
        + b"x" * 500
        + b'"}]}'
    )

    async def chunks() -> Any:
        yield payload[:100]
        yield payload[100:]

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api}"},
        content=chunks(),
    )
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "INVALID_REQUEST"
    assert upstream.find("POST", "/v1/chat/completions") == []


async def test_body_within_limit_is_not_rejected(client, upstream) -> None:
    """合法请求行为零变化：贴着上限的请求照常到达上游并 200。"""
    api = await _api_key(client, upstream, "within")
    body = {"model": "call-model", "messages": [{"role": "user", "content": "hi"}]}
    response = await client.post(
        "/v1/chat/completions", headers={"Authorization": f"Bearer {api}"}, json=body
    )
    assert response.status_code == 200, response.text
    assert upstream.find("POST", "/v1/chat/completions") != []


async def test_gate_rejects_before_body_check(client, upstream, monkeypatch) -> None:
    """闸门前移：槽位已满时超限请求是 429（轮不到 413）。"""
    _set_cap(monkeypatch, 1)
    api = await _api_key(client, upstream, "gate429")
    gate = get_inflight_gate()
    gate.acquire()  # 占满唯一槽位
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {api}"},
            json={"model": "call-model", "messages": [{"role": "user", "content": "x" * 500}]},
        )
    finally:
        gate.release()
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "INFLIGHT_LIMITED"
    assert response.headers["retry-after"] == "1"


async def test_gate_rejects_before_auth(client, upstream, monkeypatch) -> None:
    """闸门前移：无凭据的请求也先撞 429（轮不到 401）。"""
    _set_cap(monkeypatch, 1)
    gate = get_inflight_gate()
    gate.acquire()
    try:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "call-model", "messages": [{"role": "user", "content": "hi"}]},
        )
    finally:
        gate.release()
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "INFLIGHT_LIMITED"


async def test_auth_failure_releases_the_slot(client, upstream) -> None:
    """acquire 成功但鉴权失败（401）：依赖栈退出必须归还槽位，不留泄漏。"""
    gate = get_inflight_gate()
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-not-a-real-key"},
        json={"model": "call-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401, response.text
    assert gate.inflight == 0


async def test_body_failure_releases_the_slot(client, upstream) -> None:
    """acquire 成功但请求体超限（413）：槽位同样归还。"""
    api = await _api_key(client, upstream, "release413")
    gate = get_inflight_gate()
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api}"},
        json={"model": "call-model", "messages": [{"role": "user", "content": "x" * 500}]},
    )
    assert response.status_code == 413, response.text
    assert gate.inflight == 0


async def test_invalid_json_keeps_422_envelope(client, upstream) -> None:
    """非法 JSON 走既有 RequestValidationError 渲染：422 + INVALID_REQUEST。"""
    api = await _api_key(client, upstream, "badjson")
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api}",
            "Content-Type": "application/json",
        },
        content=b'{"model": "call-model", "messages": [oops]}',
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "请求参数不合法"
    assert body["details"]["errors"][0]["type"] == "json_invalid"
    assert get_inflight_gate().inflight == 0
