"""httpx 传输的回归测试矩阵。

契约来源：docs/upstream_transport.md 与 api/src/l05_service/llm/gateway.py。
所有用例由 httpx.MockTransport 驱动，不监听端口、不访问网络。
每条用例的 docstring 写明它验证的是哪一条契约（gateway.py 行号 / 文档章节）。
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from l01_core.errors import ErrorCode
from l05_service import chat as chat_service
from l05_service.llm import gateway
from l05_service.upstream_settings import UpstreamTimeouts

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> SimpleNamespace:
    """最小 Settings 替身：httpx 臂只读这些字段（gateway.py:571-591、:988-993）。"""
    base = {
        "upstream_transport": "httpx",
        "request_timeout_s": 30,
        "upstream_connect_timeout_s": 5,
        "upstream_read_timeout_s": 30,
        "upstream_first_byte_timeout_s": 30,
        "upstream_max_connections": 200,
        "upstream_max_keepalive_connections": 50,
        "upstream_keepalive_expiry_s": 30,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _channel(**overrides: Any) -> SimpleNamespace:
    base = {
        "id": "ch_test",
        "slug": "test",
        "name": "Test",
        "connection_url": "http://upstream.test/v1",
        "auth_style": "bearer",
        "protocol": "openai_chat",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _prepared(**overrides: Any) -> SimpleNamespace:
    base = {
        "headers": {"Authorization": "Bearer test", "Content-Type": "application/json"},
        "body": {"messages": [{"role": "user", "content": "hi"}]},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@asynccontextmanager
async def _upstream(monkeypatch, handler, **setting_overrides):
    """把 gateway 的所有上游请求指向进程内的 MockTransport。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gateway, "_get_client", lambda: client)
    monkeypatch.setattr(gateway, "get_settings", lambda: _settings(**setting_overrides))
    try:
        yield client
    finally:
        await client.aclose()


def _json_handler(payload: Any, *, status: int = 200, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers=headers or {})

    return handler


def _text_handler(text: str, *, status: int = 200, content_type: str = "text/plain"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=text, headers={"content-type": content_type})

    return handler


def _raising_handler(exc: BaseException):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _sse(*chunks: dict, done: bool = True) -> bytes:
    parts = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    if done:
        parts.append("data: [DONE]\n\n")
    return "".join(parts).encode()


def _chunk(
    content: str | None = None,
    *,
    finish: str | None = None,
    usage: dict | None = None,
    ident: str = "c1",
) -> dict:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    payload: dict = {
        "id": ident,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


class _FailingStream(httpx.AsyncByteStream):
    """先吐出一点字节，再抛出指定异常（模拟流中途失败）。"""

    def __init__(self, exc: BaseException, prefix: bytes = b"") -> None:
        self._exc = exc
        self._prefix = prefix

    async def __aiter__(self):
        if self._prefix:
            yield self._prefix
        raise self._exc


def _stream_handler(
    *,
    status: int = 200,
    body: bytes = b"",
    headers: dict | None = None,
    fail: BaseException | None = None,
    prefix: bytes = b"",
):
    def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            _FailingStream(fail, prefix)
            if fail is not None
            else httpx.ByteStream(body)
        )
        return httpx.Response(
            status,
            stream=stream,
            headers=headers or {"content-type": "text/event-stream"},
        )

    return handler


async def _complete(monkeypatch, handler, **setting_overrides):
    async with _upstream(monkeypatch, handler, **setting_overrides):
        return await gateway._httpx_complete(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            30,
            "req_test",
            _prepared(),
        )


async def _open_stream(monkeypatch, handler, **setting_overrides):
    async with _upstream(monkeypatch, handler, **setting_overrides):
        return await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "test-model",
            30,
            "req_test",
            _prepared(),
        )


# ---------------------------------------------------------------------------
# 非流式：成功路径
# ---------------------------------------------------------------------------


async def test_non_stream_200_returns_payload_usage_latency_and_headers(monkeypatch):
    """契约：complete() 返回 (payload, usage, latency_ms, headers)（gateway.py:1037-1051）；
    usage 由 extract_usage 归一为 prompt/completion/total/cached（gateway.py:371-385）。"""
    payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 5,
            "total_tokens": 16,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }
    async with _upstream(
        monkeypatch,
        _json_handler(payload, headers={"x-upstream-request-id": "up-1"}),
    ):
        body, usage, latency_ms, headers = await gateway._httpx_complete(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            30,
            "req_test",
            _prepared(),
        )
    assert body["id"] == "chatcmpl-1"
    assert body["choices"][0]["message"]["content"] == "hi"
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (11, 5, 16)
    assert usage.cached_tokens == 4
    assert latency_ms >= 0
    # 上游响应头透传（httpx 会把头名归一化为小写，契约只要求头存在）。
    assert headers["x-upstream-request-id"] == "up-1"


async def test_non_stream_usage_falls_back_to_input_output_tokens(monkeypatch):
    """契约：上游只给 input_tokens/output_tokens 时也要归一（gateway.py:375-377），
    total 缺省时由 prompt+completion 补齐。"""
    payload = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    _, usage, _, _ = await _complete(monkeypatch, _json_handler(payload))
    assert usage.prompt_tokens == 7
    assert usage.completion_tokens == 3
    assert usage.total_tokens == 10
    assert usage.cached_tokens == 0


async def test_non_stream_request_body_is_rewritten(monkeypatch):
    """契约：build_request_body 丢弃 None 值、强制 model、透传 messages
    （gateway.py:121-130）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        seen["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"content": "hi"}}]},
        )

    async with _upstream(monkeypatch, handler):
        await gateway._httpx_complete(
            _channel(),
            "upstream-model",
            {"messages": [{"role": "user", "content": "hi"}], "temperature": None},
            30,
            "req_test",
            _prepared(),
        )
    assert seen["body"]["model"] == "upstream-model"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "temperature" not in seen["body"]
    assert seen["headers"]["authorization"] == "Bearer test"


async def test_complete_dispatches_to_httpx_arm_when_transport_is_httpx(monkeypatch):
    """契约：complete() 先问 _use_litellm，transport=httpx 时必须走 httpx 臂
    （gateway.py:813-817、:1037-1051）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"content": "hi"}}]}
        )

    async with _upstream(monkeypatch, handler, upstream_transport="httpx"):
        body, usage, latency_ms, _headers = await gateway.complete(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            30,
            "req_test",
            _prepared(),
        )
    assert body["choices"][0]["message"]["content"] == "hi"
    assert usage.total_tokens == 0
    assert latency_ms >= 0
    assert calls == ["/v1/chat/completions"]


def test_use_litellm_gate_states(monkeypatch):
    """契约：_use_litellm = (transport == litellm) 且 (api_base 非空)
    （gateway.py:813-832）；拿不到 OpenAI chat URL 的渠道即便配置 litellm 也回退 httpx。"""
    openai_channel = _channel(connection_url="http://upstream.test/v1")
    monkeypatch.setattr(
        gateway, "get_settings", lambda: _settings(upstream_transport="litellm")
    )
    assert gateway._use_litellm(openai_channel) is True
    assert gateway._litellm_api_base(openai_channel) == "http://upstream.test/v1"
    # 非 chat-completions 端点（尾随 # 表示精确端点）拿不到 api_base → 回退 httpx。
    responses_channel = _channel(connection_url="http://upstream.test/v1/responses#")
    assert gateway._litellm_api_base(responses_channel) is None
    assert gateway._use_litellm(responses_channel) is False
    monkeypatch.setattr(
        gateway, "get_settings", lambda: _settings(upstream_transport="httpx")
    )
    assert gateway._use_litellm(openai_channel) is False


# ---------------------------------------------------------------------------
# 非流式：错误路径
# ---------------------------------------------------------------------------


async def test_non_stream_401_is_permanent_auth_error(monkeypatch):
    """契约：401 → kind=auth、retryable=False、tier=permanent，且响应头被捕获
    （classify_http_error，gateway.py:248-258）；对外映射为 502 UPSTREAM_AUTH_FAILED
    （chat.py:115-127）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch,
            _json_handler(
                {"error": {"message": "invalid api key"}},
                status=401,
                headers={"x-request-id": "up-401"},
            ),
        )
    err = excinfo.value
    assert err.kind == "auth"
    assert err.status_code == 401
    assert err.retryable is False
    assert err.tier == gateway.TIER_PERMANENT
    assert err.headers is not None and err.headers["x-request-id"] == "up-401"
    status, code, message = chat_service.map_error(err)
    assert (status, code) == (502, ErrorCode.UPSTREAM_AUTH_FAILED)
    assert message == err.message


async def test_non_stream_404_is_permanent_not_found(monkeypatch):
    """契约：404 → kind=not_found、tier=permanent；对外 404 MODEL_NOT_FOUND
    （gateway.py:270-282、chat.py:126-127）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch, _json_handler({"error": {"message": "no such model"}}, status=404)
        )
    err = excinfo.value
    assert err.kind == "not_found"
    assert err.status_code == 404
    assert err.retryable is False
    assert err.tier == gateway.TIER_PERMANENT
    assert chat_service.map_error(err)[:2] == (404, ErrorCode.MODEL_NOT_FOUND)


async def test_non_stream_429_is_transient_rate_limit_with_retry_after(monkeypatch):
    """契约：429（无配额字样）→ kind=rate_limit、retryable=True、tier=transient，
    并解析 Retry-After 为毫秒（gateway.py:225-247）；对外 429 UPSTREAM_RATE_LIMITED。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch,
            _json_handler(
                {"error": {"message": "slow down"}},
                status=429,
                headers={"Retry-After": "3"},
            ),
        )
    err = excinfo.value
    assert err.kind == "rate_limit"
    assert err.status_code == 429
    assert err.retryable is True
    assert err.tier == gateway.TIER_TRANSIENT
    assert err.retry_after_ms == 3000
    assert chat_service.map_error(err)[:2] == (429, ErrorCode.UPSTREAM_RATE_LIMITED)
    assert chat_service.error_details(err) == {"retry_after_s": 3}


async def test_non_stream_429_with_quota_marker_is_permanent(monkeypatch):
    """契约：429 且正文含配额字样（insufficient_quota）→ retryable=False、tier=permanent
    （gateway.py:225-237）；对外 503 UPSTREAM_QUOTA_EXHAUSTED（chat.py:124-125）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch,
            _json_handler(
                {"error": {"message": "You exceeded your current quota"}}, status=429
            ),
        )
    err = excinfo.value
    assert err.kind == "rate_limit"
    assert err.retryable is False
    assert err.tier == gateway.TIER_PERMANENT
    assert chat_service.map_error(err)[:2] == (503, ErrorCode.UPSTREAM_QUOTA_EXHAUSTED)


async def test_non_stream_500_is_transient_upstream_error(monkeypatch):
    """契约：5xx → kind=upstream、retryable=True、tier=transient
    （gateway.py:294-303）；对外 502 UPSTREAM_ERROR（chat.py:144）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(monkeypatch, _text_handler("boom", status=500))
    err = excinfo.value
    assert err.kind == "upstream"
    assert err.status_code == 500
    assert err.retryable is True
    assert err.tier == gateway.TIER_TRANSIENT
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.UPSTREAM_ERROR)


async def test_non_stream_200_with_non_json_body_is_bad_response(monkeypatch):
    """契约：200 但正文不是 JSON → kind=bad_response（gateway.py:1126-1129）；
    对外 502 BAD_UPSTREAM_RESPONSE（chat.py:134-137）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(monkeypatch, _text_handler("<html>bad gateway</html>"))
    err = excinfo.value
    assert err.kind == "bad_response"
    assert err.status_code == 200
    assert err.retryable is True
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.BAD_UPSTREAM_RESPONSE)


async def test_non_stream_200_without_content_is_empty_pseudo_success(monkeypatch):
    """契约：200 但没有可用内容 → kind=empty（gateway.py:1130-1131，meaningful_content
    在 :330-345）；对外 502 EMPTY_RESPONSE（chat.py:132-133）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(monkeypatch, _json_handler({"id": "x", "choices": []}))
    err = excinfo.value
    assert err.kind == "empty"
    assert err.status_code == 200
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.EMPTY_RESPONSE)


async def test_non_stream_connect_timeout_is_connection_error(monkeypatch):
    """契约：连接超时 → kind=connection（httpx.ConnectTimeout 分支，gateway.py:1116-1117）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch, _raising_handler(httpx.ConnectTimeout("connect timed out"))
        )
    err = excinfo.value
    assert err.kind == "connection"
    assert err.retryable is True
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.UPSTREAM_ERROR)


async def test_non_stream_read_timeout_is_timeout_error(monkeypatch):
    """契约：读超时 → kind=timeout（gateway.py:1118-1119）；对外 504 UPSTREAM_TIMEOUT
    （chat.py:128-129）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch, _raising_handler(httpx.ReadTimeout("read timed out"))
        )
    err = excinfo.value
    assert err.kind == "timeout"
    assert err.retryable is True
    assert chat_service.map_error(err)[:2] == (504, ErrorCode.UPSTREAM_TIMEOUT)


async def test_non_stream_connect_error_is_connection_error(monkeypatch):
    """契约：连接被拒 → kind=connection（httpx.ConnectError 分支，gateway.py:1116-1117）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _complete(
            monkeypatch, _raising_handler(httpx.ConnectError("connection refused"))
        )
    assert excinfo.value.kind == "connection"


async def test_non_stream_cancelled_error_is_not_swallowed(monkeypatch):
    """契约：客户端取消必须原样穿透，不能被兜底成 LLMError
    （httpx 臂只捕获 httpx 异常，gateway.py:1116-1121）。"""
    with pytest.raises(asyncio.CancelledError):
        await _complete(monkeypatch, _raising_handler(asyncio.CancelledError()))


# ---------------------------------------------------------------------------
# 流式
# ---------------------------------------------------------------------------


async def test_stream_200_multi_frame_with_usage_and_done(monkeypatch):
    """契约：SSE 多帧 → 首个有意义的 delta 之前会 prime；usage 只认上游真实发送的
    （gateway.py:1202-1266、:1284-1350）；改写后 model 换成对外名（:1268-1282）。"""
    body = _sse(
        _chunk("he"),
        _chunk("llo"),
        _chunk(None, finish="stop"),
        _chunk(None, usage={"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}),
    )
    async with _upstream(
        monkeypatch, _stream_handler(body=body, headers={"x-upstream-id": "up-9"})
    ):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "public-model",
            30,
            "req_test",
            _prepared(),
        )
        # prime() 只读到第一个有意义的 delta；usage / finish_reason / [DONE] 要等
        # 消费完整个流才有（gateway.py:1202-1266、:1284-1350）。
        assert stream.got_content is True
        assert stream.response_id == "c1"
        assert stream.upstream_headers["x-upstream-id"] == "up-9"
        chunks = [line async for line in stream.iter_sse()]
        assert stream.usage.total_tokens == 13
        assert stream.usage.prompt_tokens == 9
        assert stream.usage.completion_tokens == 4
        assert stream.usage_from_upstream is True
        assert stream.finish_reason == "stop"
        assert stream.saw_done is True
        assert stream.terminal is True
        assert stream.content_chars == 5
        await stream.close()
    text = "".join(chunks)
    assert '"content": "he"' in text
    assert '"content": "llo"' in text
    assert "data: [DONE]" in text
    assert '"model": "public-model"' in text


async def test_stream_4xx_raises_llm_error_instead_of_empty_stream(monkeypatch):
    """契约：流式请求收到 4xx 必须抛 LLMError，不能产出一条空流
    （gateway.py:1750-1753）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _open_stream(
            monkeypatch,
            _json_handler({"error": {"message": "invalid api key"}}, status=401),
        )
    err = excinfo.value
    assert err.kind == "auth"
    assert err.status_code == 401
    assert err.retryable is False
    assert err.tier == gateway.TIER_PERMANENT


async def test_stream_5xx_raises_transient_upstream_error(monkeypatch):
    """契约：流式 5xx → classify_http_error 的 upstream 分支（gateway.py:1750-1753）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _open_stream(monkeypatch, _text_handler("boom", status=503))
    err = excinfo.value
    assert err.kind == "upstream"
    assert err.status_code == 503
    assert err.retryable is True
    assert err.tier == gateway.TIER_TRANSIENT


@pytest.mark.parametrize("body", ['{"choices":[]}', "<html>not sse</html>"])
async def test_stream_200_with_non_sse_body_is_bad_response(monkeypatch, body):
    """契约：200 但 content-type 不是 SSE、也没有任何 data 帧 → bad_response
    （prime 终态分支，gateway.py:1263-1290）；对外 502 BAD_UPSTREAM_RESPONSE。
    上游忽略 stream=true 时不能再报成 504 timeout。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _open_stream(
            monkeypatch,
            _text_handler(body, content_type="application/json"),
        )
    err = excinfo.value
    assert err.kind == "bad_response"
    assert err.status_code == 200
    assert err.retryable is True
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.BAD_UPSTREAM_RESPONSE)


async def test_stream_read_timeout_before_first_byte_is_timeout(monkeypatch):
    """契约：连接已建立但首字节前读超时 → kind=timeout（gateway.py:1253-1260）。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _open_stream(
            monkeypatch, _stream_handler(fail=httpx.ReadTimeout("read timed out"))
        )
    err = excinfo.value
    assert err.kind == "timeout"
    assert err.retryable is True
    assert chat_service.map_error(err)[:2] == (504, ErrorCode.UPSTREAM_TIMEOUT)


async def test_stream_empty_body_with_sse_content_type_is_empty(monkeypatch):
    """契约：content-type 是 SSE 但上游直接 EOF、一个 data 帧都没有 → empty
    （prime 终态分支，gateway.py:1263-1290）；对外 502 EMPTY_RESPONSE。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _open_stream(monkeypatch, _stream_handler(body=b""))
    err = excinfo.value
    assert err.kind == "empty"
    assert err.status_code == 200
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.EMPTY_RESPONSE)


async def test_stream_data_frames_are_accepted_even_with_non_sse_content_type(monkeypatch):
    """契约：分类只看「有没有 data 帧」。上游确实在发 data 帧时，即便 content-type
    不是 text/event-stream 也必须正常流式返回（部分兼容上游不声明 SSE）。"""
    body = _sse(_chunk("hi"), _chunk(None, finish="stop"))
    async with _upstream(
        monkeypatch,
        _stream_handler(body=body, headers={"content-type": "application/json"}),
    ):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "public-model",
            30,
            "req_test",
            _prepared(),
        )
        assert stream.got_content is True
        chunks = [line async for line in stream.iter_sse()]
        await stream.close()
    assert '"content": "hi"' in "".join(chunks)


async def test_stream_only_done_sentinel_is_empty_pseudo_success(monkeypatch):
    """契约：只收到 [DONE]、没有任何内容 → kind=empty（gateway.py:1263-1264）；
    对外 502 EMPTY_RESPONSE。"""
    with pytest.raises(gateway.LLMError) as excinfo:
        await _open_stream(monkeypatch, _stream_handler(body=b"data: [DONE]\n\n"))
    err = excinfo.value
    assert err.kind == "empty"
    assert err.status_code == 200
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.EMPTY_RESPONSE)


async def test_stream_error_payload_after_content_is_stream_abort(monkeypatch):
    """契约：流内出现 error payload → kind=stream_abort、retryable=False
    （gateway.py:1304-1310）；对外 502 STREAM_ABORTED（chat.py:142-143）。"""
    body = _sse(_chunk("hi"), {"error": {"message": "upstream exploded"}}, done=False)
    async with _upstream(monkeypatch, _stream_handler(body=body)):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "public-model",
            30,
            "req_test",
            _prepared(),
        )
        with pytest.raises(gateway.LLMError) as excinfo:
            [line async for line in stream.iter_sse()]
        await stream.close()
    err = excinfo.value
    assert err.kind == "stream_abort"
    assert err.retryable is False
    assert chat_service.map_error(err)[:2] == (502, ErrorCode.STREAM_ABORTED)


async def test_stream_read_timeout_after_first_byte_is_stream_abort(monkeypatch):
    """契约：首字节之后读超时 → kind=stream_abort、不重试（gateway.py:1328-1339）。"""
    prefix = _sse(_chunk("hi"), done=False)
    async with _upstream(
        monkeypatch,
        _stream_handler(fail=httpx.ReadTimeout("read timed out"), prefix=prefix),
    ):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "public-model",
            30,
            "req_test",
            _prepared(),
        )
        with pytest.raises(gateway.LLMError) as excinfo:
            [line async for line in stream.iter_sse()]
        await stream.close()
    err = excinfo.value
    assert err.kind == "stream_abort"
    assert err.retryable is False


async def test_stream_missing_completion_marker_is_stream_abort(monkeypatch):
    """契约：有内容但既无 finish_reason 也无 [DONE] → 判定为首字节后断流
    （gateway.py:1349-1350）。"""
    body = _sse(_chunk("hi"), done=False)
    async with _upstream(monkeypatch, _stream_handler(body=body)):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "public-model",
            30,
            "req_test",
            _prepared(),
        )
        with pytest.raises(gateway.LLMError) as excinfo:
            [line async for line in stream.iter_sse()]
        await stream.close()
    err = excinfo.value
    assert err.kind == "stream_abort"
    assert err.retryable is False
    assert "断流" in err.message


async def test_stream_cancelled_error_is_not_swallowed(monkeypatch):
    """契约：客户端取消必须原样穿透流式读取（gateway.py:1328-1345 只捕获超时与
    httpx 异常，不捕获 CancelledError）。"""
    prefix = _sse(_chunk("hi"), done=False)
    async with _upstream(
        monkeypatch,
        _stream_handler(fail=asyncio.CancelledError(), prefix=prefix),
    ):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}]},
            "public-model",
            30,
            "req_test",
            _prepared(),
        )
        with pytest.raises(asyncio.CancelledError):
            [line async for line in stream.iter_sse()]
        await stream.close()


# ---------------------------------------------------------------------------
# 连接复用
# ---------------------------------------------------------------------------


async def test_httpx_arm_reuses_one_client_for_all_requests(monkeypatch):
    """契约：httpx 臂复用同一个进程级 AsyncClient，不按请求建连
    （gateway.py:633-648，文档 §1 连接池）。"""
    seen_clients: list[int] = []
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200, json={"choices": [{"index": 0, "message": {"content": "hi"}}]}
        )

    async with _upstream(monkeypatch, handler) as client:
        seen_clients.append(id(client))
        for _ in range(3):
            await gateway._httpx_complete(
                _channel(),
                "test-model",
                {"messages": [{"role": "user", "content": "hi"}]},
                30,
                "req_test",
                _prepared(),
            )
    assert calls == ["/v1/chat/completions"] * 3
    assert len(set(seen_clients)) == 1


# ---------------------------------------------------------------------------
# 既有回归用例（保持原样）
# ---------------------------------------------------------------------------


async def test_httpx_open_stream_sets_timeout_on_request(monkeypatch):
    """Regression: AsyncClient.send() has no timeout kwarg.

    The timeout must be attached to the request built by build_request();
    passing it to send() raises TypeError and turns every fallback stream
    into a 500.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://upstream.test/v1/chat/completions"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"id":"c1","object":"chat.completion.chunk",'
                '"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
                'data: {"id":"c1","object":"chat.completion.chunk",'
                '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gateway, "_get_client", lambda: client)
    channel = SimpleNamespace(
        id="ch_test",
        slug="test",
        name="Test",
        connection_url="http://upstream.test/v1",
    )
    prepared = SimpleNamespace(
        headers={"Authorization": "Bearer test"},
        body={"messages": [{"role": "user", "content": "hi"}]},
    )
    try:
        stream = await gateway._httpx_open_stream(
            channel,
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}], "stream": True},
            "test-model",
            30,
            "req_test",
            prepared,
        )
        assert stream.got_content is True
        await stream.close()
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# ADR-074：流式无总闸、思考块计入输出估算
# ---------------------------------------------------------------------------


class _SlowLineStream:
    def __init__(self, lines: list[str], delay_s: float) -> None:
        self._lines = list(lines)
        self._delay = delay_s

    def __aiter__(self) -> "_SlowLineStream":
        return self

    async def __anext__(self) -> str:
        if not self._lines:
            raise StopAsyncIteration
        await asyncio.sleep(self._delay)
        return self._lines.pop(0)


class _SlowLinesResponse:
    headers: dict = {}

    def __init__(self, lines: list[str], delay_s: float) -> None:
        self._lines = lines
        self._delay = delay_s

    def aiter_lines(self) -> _SlowLineStream:
        return _SlowLineStream(self._lines, self._delay)


def _slow_stream(lines: list[str], delay_s: float) -> gateway.UpstreamStream:
    return gateway.UpstreamStream(
        client=None,
        response=_SlowLinesResponse(lines, delay_s),
        channel=SimpleNamespace(),
        upstream_model="m",
        public_name="p",
        request_id="r",
        read_timeout_s=5,
    )


async def test_stream_survives_past_the_request_total() -> None:
    """契约 ADR-074：流没有总闸——累计时长超过请求超时不再中断，
    只受逐块读空闲看门狗约束。三块间隔 0.35s、累计 >1s：旧实现以
    total_timeout_s=1 掐断，新实现完整收完。"""
    lines = [
        "data: " + json.dumps(_chunk("hi")) + "\n",
        "\n",
        "data: " + json.dumps(_chunk(finish="stop")) + "\n",
        "\n",
        "data: [DONE]\n",
    ]
    stream = _slow_stream(lines, 0.35)
    started = time.perf_counter()
    out = [line async for line in stream.iter_sse()]
    elapsed = time.perf_counter() - started
    await stream.close()
    assert len(out) == 5
    assert elapsed > 1.0
    assert out[-1] == "data: [DONE]\n\n"
    assert '"stop"' in out[2]


def _same_name_stream(lines: list[str], delay_s: float = 0.0) -> gateway.UpstreamStream:
    """与 _slow_stream 相同，但对外名与上游名一致（触发逐行直通路径）。"""
    return gateway.UpstreamStream(
        client=None,
        response=_SlowLinesResponse(lines, delay_s),
        channel=SimpleNamespace(),
        upstream_model="m",
        public_name="m",
        request_id="r",
        read_timeout_s=5,
    )


async def test_stream_same_name_relays_lines_verbatim() -> None:
    """契约 task-159：无 sse_patch 且 public_name == upstream_model 时逐行
    原样直通（不重新序列化、不加空格），同时跟踪状态（usage / finish_reason /
    [DONE] / got_content）保持完整。"""
    compact = (
        'data: {"id":"c1","object":"chat.completion.chunk",'
        '"model":"m","choices":[{"index":0,"delta":{"content":"hi"},'
        '"finish_reason":null}]}'
    )
    usage_chunk = _chunk(
        None, usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    )
    lines = [
        compact,
        "",
        "data: " + json.dumps(_chunk(None, finish="stop")),
        "",
        "data: " + json.dumps(usage_chunk),
        "",
        "data: [DONE]",
    ]
    stream = _same_name_stream(lines)
    out = [line async for line in stream.iter_sse()]
    await stream.close()
    # 原样直通：第一帧保持上游的紧凑 JSON，不被 json.dumps 重排加空格
    assert out[0] == compact + "\n"
    assert out[1] == "\n"
    assert out[-1] == "data: [DONE]\n"
    # 跟踪不变：伪成功 / 断流 / usage 记账全部依赖这条解析路径
    assert stream.got_content is True
    assert stream.finish_reason == "stop"
    assert stream.saw_done is True
    assert stream.terminal is True
    assert stream.content_chars == 2
    assert stream.usage.total_tokens == 5
    assert stream.usage_from_upstream is True


async def test_stream_rewrite_still_applies_patch_and_rename() -> None:
    """契约 task-159：sse_patch 注入或名称改写时必须保留逐帧改写路径。"""
    stream = _slow_stream(
        [
            "data: " + json.dumps(_chunk("hi")),
            "",
            "data: " + json.dumps(_chunk(None, finish="stop")),
            "",
            "data: [DONE]",
        ],
        0.0,
    )
    stream.sse_patch = {"gateway_extra": "x"}
    out = [line async for line in stream.iter_sse()]
    await stream.close()
    assert '"gateway_extra": "x"' in out[0]
    assert '"model": "p"' in out[0]
    assert '"content": "hi"' in out[0]
    assert stream.finish_reason == "stop"
    assert stream.saw_done is True


def test_reasoning_chars_escape_the_snapshot_cap() -> None:
    """契约 ADR-074：reasoning_chars 无上限计数（输出估算用）；
    reasoning_content 维持 64K 截断（request_bodies 快照用）。"""
    lines = [
        "data: "
        + json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"reasoning_content": "思" * 60_000}, "finish_reason": None}
                ],
            }
        )
        + "\n",
        "\n",
        "data: "
        + json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"reasoning_content": "思" * 10_000}, "finish_reason": None}
                ],
            }
        )
        + "\n",
        "\n",
        "data: "
        + json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"reasoning_content": "思" * 5_000}, "finish_reason": None}
                ],
            }
        )
        + "\n",
        "\n",
        "data: " + json.dumps(_chunk("hi")) + "\n",
        "\n",
        "data: " + json.dumps(_chunk(finish="stop")) + "\n",
        "\n",
        "data: [DONE]\n",
    ]
    stream = _slow_stream(lines, 0.0)

    async def _consume() -> None:
        [_ async for _ in stream.iter_sse()]
        await stream.close()

    asyncio.run(_consume())
    # 计数无上限；快照缓冲到 65536 后整块截停（不切半块）。
    assert stream.reasoning_chars == 75_000
    assert len(stream.reasoning_content) == 70_000


async def test_open_stream_request_has_no_total_deadline(monkeypatch):
    """契约 ADR-074：流式上游请求的 httpx timeout 不带 total（否则 httpx
    会在总闸位置掐断 body 读取）；read 空闲预算照常附带在请求上。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_chunk("hi"), _chunk(finish="stop")),
        )

    async with _upstream(monkeypatch, handler):
        stream = await gateway._httpx_open_stream(
            _channel(),
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}], "stream": True},
            "test-model",
            30,
            "req_test",
            _prepared(),
        )
        await stream.close()

    assert seen, "upstream request missing"
    budget = seen[0].extensions["timeout"]
    # total=None 时 httpx 的 extensions 不含 "timeout" 键：body 读取没有总闸。
    assert "timeout" not in budget
    assert budget["read"] == 30.0  # UPSTREAM_READ_TIMEOUT_S，来自 _settings()


# ---------------------------------------------------------------------------
# 设置页覆盖（upstream_settings.UpstreamTimeouts）优先于 env 默认
# ---------------------------------------------------------------------------


def test_client_timeout_honours_upstream_timeouts_override():
    """设置页覆盖：非流式 _client_timeout 的 connect/read 取覆盖值。"""
    overrides = UpstreamTimeouts(connect_timeout_s=3, first_byte_timeout_s=7, read_timeout_s=45)
    timeout = gateway._client_timeout(_settings(), 120.0, overrides)
    assert timeout.connect == 3.0
    assert timeout.read == 45.0


def test_client_timeout_caps_overrides_at_request_total():
    """非流式总闸仍封顶：覆盖值超过 REQUEST_TIMEOUT_S 时被压回总闸。"""
    overrides = UpstreamTimeouts(connect_timeout_s=3, first_byte_timeout_s=7, read_timeout_s=600)
    timeout = gateway._client_timeout(_settings(), 30.0, overrides)
    assert timeout.connect == 3.0
    assert timeout.read == 30.0


def test_stream_timeouts_honour_upstream_timeouts_override():
    """设置页覆盖：流式 (first_byte, read) 直接取覆盖值，无总闸封顶。"""
    overrides = UpstreamTimeouts(connect_timeout_s=3, first_byte_timeout_s=9, read_timeout_s=250)
    assert gateway._stream_timeouts(30.0, overrides) == (9.0, 250.0)


def test_stream_client_timeout_honours_connect_override():
    """设置页覆盖：流式 httpx client timeout 的 connect 取覆盖值，read 仍为逐块看门狗。"""
    overrides = UpstreamTimeouts(connect_timeout_s=3, first_byte_timeout_s=9, read_timeout_s=250)
    timeout = gateway._stream_client_timeout(_settings(), 120.0, overrides)
    assert timeout.connect == 3.0
    assert timeout.read == 120.0


def test_relay_text_cap_limits_buffer():
    """ADR-075 修订：转发文本缓冲有上限；封顶后不再累积（超出部分按字符折算）。"""
    stream = object.__new__(gateway.UpstreamStream)
    stream._relay_text_parts = []
    stream._relay_text_chars = 0
    stream._stash_relay_text("a" * 10)
    stream._stash_relay_text("b" * 10)
    assert stream.relay_text == "a" * 10 + "b" * 10
    stream._relay_text_chars = gateway.UpstreamStream._RELAY_TEXT_CAP
    stream._stash_relay_text("c")
    assert stream.relay_text == "a" * 10 + "b" * 10
