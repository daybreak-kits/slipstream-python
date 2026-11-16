from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from l01_core.errors import ErrorCode
from l05_service.chat import map_error
from l05_service.llm import gateway
from l05_service.llm.gateway import (
    LLMError,
    TIER_CLIENT,
    TIER_PERMANENT,
    TIER_TRANSIENT,
    classify_http_error,
)

SECRET = "sk-live-1234567890abcdef"
UPSTREAM_TRACE = "upstream-secret-trace-9f3c"


def test_non_json_upstream_body_not_in_response_message() -> None:
    raw = f"<html><body>{UPSTREAM_TRACE}</body></html>"

    error = classify_http_error(500, raw)
    status, code, message = map_error(error)

    assert status == 502
    assert code == ErrorCode.UPSTREAM_ERROR
    assert message == "上游返回 HTTP 500"
    assert UPSTREAM_TRACE not in message
    # Operators still get the original text internally.
    assert error.raw == raw


def test_json_without_message_not_echoed() -> None:
    raw = json.dumps(
        {"error": {"type": "server_error"}, "trace": UPSTREAM_TRACE}
    )

    error = classify_http_error(500, raw)

    assert error.message == "上游返回 HTTP 500"
    assert UPSTREAM_TRACE not in error.message
    assert "server_error" not in error.message
    assert error.raw == raw[:2000]


def test_json_error_code_is_relayed_but_whole_body_is_not() -> None:
    raw = json.dumps({"error": {"code": "E_UPSTREAM"}, "detail": UPSTREAM_TRACE})

    error = classify_http_error(500, raw)

    assert error.message == "E_UPSTREAM"
    assert UPSTREAM_TRACE not in error.message


@pytest.mark.parametrize(
    ("secret", "masked"),
    [
        (SECRET, "sk-***"),
        ("Bearer abcdef1234567890", "Bearer ***"),
        ("api_key=abcdef1234567890", "api_key=***"),
        ('"api_key": "abcdef1234567890"', "api_key=***"),
    ],
)
def test_credentials_are_masked(secret: str, masked: str) -> None:
    raw = json.dumps({"error": {"message": f"provider rejected {secret}"}})

    error = classify_http_error(500, raw)

    assert masked in error.message
    assert secret not in error.message


def test_message_is_truncated_to_200_chars() -> None:
    raw = json.dumps({"error": {"message": "x" * 500}})

    error = classify_http_error(500, raw)

    assert len(error.message) == 200
    assert error.raw == raw[:2000]


def test_control_characters_are_stripped() -> None:
    raw = json.dumps({"error": {"message": "bad\x00 upstream\nfailure\tplease"}})

    error = classify_http_error(500, raw)

    assert error.message == "bad upstream failure please"
    assert "\x00" not in error.message
    assert "\n" not in error.message
    assert "\t" not in error.message


def test_quota_429_remains_permanent() -> None:
    raw = json.dumps(
        {
            "error": {
                "message": "You exceeded your current quota",
                "code": "insufficient_quota",
            }
        }
    )

    error = classify_http_error(429, raw)

    assert error.kind == "rate_limit"
    assert error.tier == TIER_PERMANENT
    assert error.retryable is False
    assert error.message == "You exceeded your current quota"
    status, code, message = map_error(error)
    assert status == 503
    assert code == ErrorCode.UPSTREAM_QUOTA_EXHAUSTED
    assert message == "You exceeded your current quota"


def test_quota_marker_still_read_from_raw_text() -> None:
    raw = f"insufficient_quota {UPSTREAM_TRACE}"

    error = classify_http_error(429, raw)

    assert error.tier == TIER_PERMANENT
    assert error.retryable is False
    assert error.message == "上游返回 HTTP 429"
    assert UPSTREAM_TRACE not in error.message


@pytest.mark.parametrize(
    ("status", "body", "kind", "tier", "retryable"),
    [
        (401, {"error": {"message": "invalid api key"}}, "auth", TIER_PERMANENT, False),
        (400, {"error": {"message": "bad field"}}, "bad_request", TIER_CLIENT, False),
        (429, {"error": {"message": "slow down"}}, "rate_limit", TIER_TRANSIENT, True),
        (503, "upstream down", "upstream", TIER_TRANSIENT, True),
    ],
)
def test_existing_tier_semantics_unchanged(
    status: int, body: object, kind: str, tier: str, retryable: bool
) -> None:
    raw = json.dumps(body) if isinstance(body, dict) else body

    error = classify_http_error(status, raw)

    assert error.kind == kind
    assert error.tier == tier
    assert error.retryable is retryable


class _LiteLLMTimeout(Exception):
    pass


class _FailingLineStream:
    def __aiter__(self) -> "_FailingLineStream":
        return self

    async def __anext__(self) -> str:
        raise httpx.ReadError(SECRET)


class _FailingLinesResponse:
    headers: dict[str, str] = {}

    def aiter_lines(self) -> _FailingLineStream:
        return _FailingLineStream()


def test_litellm_exception_text_not_echoed() -> None:
    error = gateway._map_litellm_error(_LiteLLMTimeout(f"provider failed with {SECRET}"))

    assert error.kind == "timeout"
    assert error.message == "上游调用超时"
    assert SECRET not in error.message
    assert SECRET in (error.raw or "")


def test_litellm_connect_exception_text_not_echoed() -> None:
    error = gateway._map_litellm_error(httpx.ConnectError(f"connect failed with {SECRET}"))

    assert error.kind == "connection"
    assert error.message == "无法连接上游"
    assert SECRET not in error.message
    assert SECRET in (error.raw or "")


def test_streaming_connect_failure_not_echoed(monkeypatch) -> None:
    class _FailingClient:
        def build_request(self, *args, **kwargs):
            return object()

        async def send(self, request, stream=True):
            raise httpx.ConnectError(f"connect failed with {SECRET}")

    monkeypatch.setattr(gateway, "_get_client", lambda: _FailingClient())
    monkeypatch.setattr(gateway, "get_settings", lambda: SimpleNamespace())

    with pytest.raises(LLMError) as caught:
        asyncio.run(
            gateway._httpx_open_stream(
                channel=SimpleNamespace(connection_url="http://upstream.example/v1"),
                upstream_model="m",
                body={},
                public_name="p",
                timeout_s=10,
                request_id="r",
                prepared=SimpleNamespace(headers={}, body={}),
            )
        )

    assert caught.value.kind == "connection"
    assert caught.value.message == "无法连接上游"
    assert SECRET not in caught.value.message


def test_streaming_read_failure_not_echoed() -> None:
    stream = gateway.UpstreamStream(
        client=None,
        response=_FailingLinesResponse(),
        channel=SimpleNamespace(),
        upstream_model="m",
        public_name="p",
        request_id="r",
        read_timeout_s=10,
    )

    with pytest.raises(LLMError) as caught:
        asyncio.run(stream.prime())

    assert caught.value.kind == "connection"
    assert caught.value.message == "读取上游流失败"
    assert SECRET not in caught.value.message


class _FailingAfterChunkStream:
    def __init__(self) -> None:
        self._first = True

    def __aiter__(self) -> "_FailingAfterChunkStream":
        return self

    async def __anext__(self) -> str:
        if self._first:
            self._first = False
            return 'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}'
        raise httpx.ReadError(SECRET)


class _FailingAfterChunkResponse:
    headers: dict[str, str] = {}

    def aiter_lines(self) -> _FailingAfterChunkStream:
        return _FailingAfterChunkStream()


class _ReasoningThenFailStream:
    def __init__(self) -> None:
        self._sent = False

    def __aiter__(self) -> "_ReasoningThenFailStream":
        return self

    async def __anext__(self) -> str:
        if self._sent:
            raise httpx.ReadError(SECRET)
        self._sent = True
        return (
            'data: {"choices":[{"index":0,"delta":'
            '{"content":"x","reasoning_content":"think"}}]}'
        )


class _ReasoningThenFailResponse:
    headers: dict[str, str] = {}

    def aiter_lines(self) -> _ReasoningThenFailStream:
        return _ReasoningThenFailStream()


def _stream_with(response) -> gateway.UpstreamStream:
    return gateway.UpstreamStream(
        client=None,
        response=response,
        channel=SimpleNamespace(),
        upstream_model="m",
        public_name="p",
        request_id="r",
        read_timeout_s=10,
    )


def test_streaming_iter_read_failure_not_echoed() -> None:
    stream = _stream_with(_FailingLinesResponse())

    async def _consume() -> None:
        async for _ in stream.iter_sse():
            pass

    with pytest.raises(LLMError) as caught:
        asyncio.run(_consume())

    assert caught.value.kind == "connection"
    assert caught.value.message == "无法读取上游流"
    assert SECRET not in caught.value.message


def test_streaming_mid_abort_not_echoed() -> None:
    stream = _stream_with(_FailingAfterChunkResponse())

    async def _consume() -> None:
        async for _ in stream.iter_sse():
            pass

    with pytest.raises(LLMError) as caught:
        asyncio.run(_consume())

    assert caught.value.kind == "stream_abort"
    assert caught.value.message.startswith("上游流在首字节后中断")
    # Evidence: elapsed / bytes / reasoning; detail comes from gateway._abort_evidence.
    assert "已流式 " in caught.value.message
    assert "未收到思考块" in caught.value.message
    assert SECRET not in caught.value.message


def test_streaming_abort_evidence_reports_reasoning_blocks() -> None:
    stream = _stream_with(_ReasoningThenFailResponse())

    async def _consume() -> None:
        async for _ in stream.iter_sse():
            pass

    with pytest.raises(LLMError) as caught:
        asyncio.run(_consume())

    assert caught.value.kind == "stream_abort"
    assert "已收到思考块" in caught.value.message
    assert "未收到思考块" not in caught.value.message
    assert SECRET not in caught.value.message
