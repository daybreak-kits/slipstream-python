"""Header passthrough semantics.

Outbound: the client's request headers are the baseline; the gateway strips
transport framing and client credentials, then overlays the channel credential,
the request id and the protocol Content-Type.
Inbound: upstream response headers are relayed unless the gateway must own them
(SSE framing, buffering, our request id) - gateway headers are applied last and
therefore win.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import update

from l02_infra.crypto import encrypt_secret
from l04_repo import orm
from l05_service.llm import gateway as gateway_module
from l05_service.llm.gateway import client_request_headers
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Any:
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


async def _force_auth_style(app, channel_id: str, style: str) -> None:
    """Pin the stored auth style: creation derives it from connection probes,
    so the three build_headers branches are covered by writing the column."""
    async with app.state.session_factory() as session:
        await session.execute(
            update(orm.ChannelORM)
            .where(orm.ChannelORM.id == channel_id)
            .values(auth_style=style)
        )
        await session.commit()


async def _model(client, headers, channel_id: str, *, call_model: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": call_model, "enabled": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def test_client_request_headers_keep_custom_and_drop_framing_and_credentials() -> None:
    filtered = client_request_headers(
        {
            "Host": "client.example",
            "Content-Length": "42",
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=5",
            "TE": "trailers",
            "Trailer": "expires",
            "Transfer-Encoding": "chunked",
            "Upgrade": "websocket",
            "Proxy-Authorization": "Basic Zm9v",
            "Authorization": "Bearer client-token",
            "Cookie": "sekret=1",
            "x-api-key": "client-leak",
            "api-key": "client-leak-2",
            "x-custom-client": "keep-me",
            "User-Agent": "client-ua/1.0",
        }
    )
    assert filtered == {"x-custom-client": "keep-me", "user-agent": "client-ua/1.0"}


def test_build_headers_merges_client_case_and_writes_owned_names_lowercase() -> None:
    """网关自有头按小写写入；与客户端基线合并不产生仅大小写不同的重复头。"""
    channel = orm.ChannelORM(
        api_key_enc=encrypt_secret("channel-secret-key"), auth_style="bearer"
    )
    merged = gateway_module.build_headers(
        channel,
        "req_1",
        "bearer",
        {
            "accept": "application/json",
            "content-type": "text/plain",
            "X-Custom-Client": "keep-me",
            "user-agent": "client-ua/1.0",
        },
    )
    names = list(merged)
    assert names == [name.lower() for name in names]  # 全部小写
    assert len(names) == len({name.lower() for name in names})  # 无大小写重复
    assert merged["content-type"] == "application/json"  # 自有头替换客户端值
    assert merged["accept"] == "application/json"
    assert merged["authorization"] == "Bearer channel-secret-key"
    assert merged["x-custom-client"] == "keep-me"
    assert merged["x-request-id"] == "req_1"


async def test_client_headers_are_the_baseline_with_credentials_stripped(
    client, upstream
) -> None:
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/echo-headers", name="头透传", slug="hdr-base"
    )
    await _model(client, headers, channel["id"], call_model="hdr-leaf")
    key = await _key(client, headers, "headers-base")

    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "x-custom-client": "keep-me",
            "user-agent": "client-ua/1.0",
            "cookie": "sekret=1",
            "x-api-key": "client-leak",
            "api-key": "client-leak-2",
            "keep-alive": "timeout=5",
        },
        json=_chat_body("hdr-leaf"),
    )
    assert response.status_code == 200, response.text
    echoed = response.json()["echoed_headers"]
    # 客户端自定义头与 UA 原样透传
    assert echoed["x-custom-client"] == "keep-me"
    assert echoed["user-agent"] == "client-ua/1.0"
    # 安全红线：客户端凭据一个都不许到上游，认证换成渠道密钥
    assert echoed["authorization"] == "Bearer channel-secret-key"
    assert "cookie" not in echoed
    assert "x-api-key" not in echoed
    assert "api-key" not in echoed
    # hop-by-hop 客户端头不转发（httpx 自己补的 connection 不算客户端来源）
    assert "keep-alive" not in echoed
    # x-request-id 由网关生成并作为响应头返回
    assert echoed["x-request-id"].startswith("req_")
    assert echoed["x-request-id"] == response.headers["x-request-id"]


async def test_auth_style_variants_carry_the_channel_key(client, upstream, app) -> None:
    headers = await _token(client)
    cases = [
        ("bearer", "authorization", "Bearer channel-secret-key"),
        ("api-key", "api-key", "channel-secret-key"),
        ("x-api-key", "x-api-key", "channel-secret-key"),
    ]
    for index, (style, field_name, expected) in enumerate(cases):
        channel = await _channel(
            client,
            headers,
            upstream.base_url + "/echo-headers",
            name=f"认证风格{index}",
            slug=f"hdr-style-{index}",
        )
        await _force_auth_style(app, channel["id"], style)
        model_name = f"style-leaf-{index}"
        await _model(client, headers, channel["id"], call_model=model_name)
        key = await _key(client, headers, f"headers-style-{index}")
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=_chat_body(model_name),
        )
        assert response.status_code == 200, response.text
        echoed = response.json()["echoed_headers"]
        assert echoed[field_name] == expected
        if style == "x-api-key":
            assert echoed["anthropic-version"] == "2023-06-01"
        elif style == "api-key":
            assert "authorization" not in echoed


async def test_upstream_response_headers_are_relayed_and_gateway_headers_win(
    client, upstream
) -> None:
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/echo-headers", name="响应头", slug="hdr-resp"
    )
    await _model(client, headers, channel["id"], call_model="resp-leaf")
    key = await _key(client, headers, "headers-resp")
    auth = {"Authorization": f"Bearer {key}"}

    plain = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("resp-leaf")
    )
    assert plain.status_code == 200, plain.text
    # 上游响应头原样转发；非流式 content-type/cache-control 按上游
    assert plain.headers["x-upstream-trace"] == "upstream-123"
    assert plain.headers["cache-control"] == "max-age=999"
    assert plain.headers["content-type"].startswith("application/json")
    # 网关自己的 request id 覆盖上游回传的 x-request-id
    assert plain.headers["x-request-id"].startswith("req_")
    assert plain.headers["x-request-id"] != "upstream-rid"

    stream = await client.post(
        "/v1/chat/completions", headers=auth, json=_chat_body("resp-leaf", stream=True)
    )
    assert stream.status_code == 200, stream.text
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["x-upstream-trace"] == "upstream-123"
    # 网关必需头在上游头之后覆盖：上游的 max-age=999 不得赢（含大小写不同的重复头）
    assert stream.headers["cache-control"] == "no-cache"
    assert stream.headers["x-accel-buffering"] == "no"
    assert stream.headers["x-request-id"].startswith("req_")
    assert stream.headers["x-request-id"] != "upstream-rid"
    # 分块响应不转发 content-length / hop-by-hop
    assert "content-length" not in stream.headers
    # 流本身可用
    assert "hi" in stream.text


async def _store_rewrite_rules(app, channel_id: str, rules: dict[str, Any]) -> None:
    """Plant stored rules directly: old rule sets predate the protection guard,
    so the runtime path (not the save-time validator) is what must reject them."""
    async with app.state.session_factory() as session:
        await session.execute(
            update(orm.ChannelORM)
            .where(orm.ChannelORM.id == channel_id)
            .values(rewrite_rules_json=json.dumps(rules))
        )
        await session.commit()


async def test_stored_request_rules_cannot_touch_gateway_headers(client, upstream, app) -> None:
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/echo-headers", name="规则保护", slug="hdr-rules"
    )
    await _model(client, headers, channel["id"], call_model="rules-leaf")
    key = await _key(client, headers, "headers-rules")
    await _store_rewrite_rules(
        app,
        channel["id"],
        {
            "request_headers": [
                {"mode": "set", "path": "content-type", "value": "text/plain"},
                {"mode": "set", "path": "x-request-id", "value": "rule-rid"},
                {"mode": "delete", "path": "host"},
                {"mode": "rename", "path": "X-Custom", "to": "host"},
                {"mode": "set", "path": "x-api-version", "value": "2024-06-01"},
            ],
            "response_headers": [
                {"mode": "set", "path": "content-type", "value": "text/custom"},
                {"mode": "set", "path": "x-rule-response", "value": "kept"},
            ],
        },
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "x-custom": "keep-me"},
        json=_chat_body("rules-leaf"),
    )
    assert response.status_code == 200, response.text
    echoed = response.json()["echoed_headers"]
    # 请求侧：协议契约与网关身份头不被旧规则改写
    assert echoed["content-type"] == "application/json"
    assert echoed["x-request-id"].startswith("req_")
    assert echoed["x-custom"] == "keep-me"
    assert echoed["host"].startswith("127.0.0.1:")
    # ……但渠道自有协议头仍可设置
    assert echoed["x-api-version"] == "2024-06-01"
    # 响应侧规则行为不变
    assert response.headers["content-type"] == "text/custom"
    assert response.headers["x-rule-response"] == "kept"
