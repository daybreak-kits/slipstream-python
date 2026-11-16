from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from l01_core.config import get_settings
from l05_service.llm import gateway as gateway_module
from l05_service.llm.gateway import has_answer_surface, meaningful_delta
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


async def _prepare(
    client, upstream: FakeUpstreamServer, path: str = ""
) -> tuple[dict[str, str], dict, dict]:
    headers = await _token(client)
    channel = (
        await client.post(
            "/api/channels",
            headers=headers,
            json={
                "name": "latency",
                "connection_url": upstream.base_url + path,
                "api_key": "channel-secret-key",
                "protocol": "openai_chat",
                "enabled": True,
            },
        )
    ).json()
    await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "gpt-test"},
    )
    key = (await client.post("/api/keys", headers=headers, json={"label": "latency"})).json()
    return headers, channel, key


async def _wait_for_log(client, headers, request_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"log {request_id} not found")


def test_has_answer_surface_accepts_text_and_tool_calls_but_not_reasoning() -> None:
    def chunk(delta: dict[str, Any]) -> dict[str, Any]:
        return {"choices": [{"index": 0, "delta": delta}]}

    assert has_answer_surface(chunk({"content": "hi"})) is True
    assert has_answer_surface(chunk({"content": "   "})) is False
    assert has_answer_surface(chunk({"content": [{"type": "text", "text": "hi"}]})) is True
    # Reasoning alone is not an answer: a thinking-only stream keeps TTFA null.
    assert has_answer_surface(chunk({"reasoning_content": "thinking"})) is False
    # A tool call is the answer for an agentic client (no visible text at all).
    assert has_answer_surface(chunk({"tool_calls": [{"id": "call-1"}]})) is True
    assert has_answer_surface(chunk({"function_call": {"name": "bash"}})) is True
    assert has_answer_surface({"choices": []}) is False


async def test_reasoning_delay_is_counted_in_ttft_but_not_ttfa(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-reasoning")
    requested = f"{channel['slug']}/gpt-test"

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": requested,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    assert response.status_code == 200, response.text

    item = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    detail = (await client.get(f"/api/logs/{item['request_id']}", headers=headers)).json()

    ttft = detail["ttft_ms"]
    ttfa = detail["ttfa_ms"]
    ttlt = detail["ttlt_ms"]
    total = detail["total_ms"]
    assert ttft is not None and ttfa is not None and ttlt is not None
    assert 0 <= ttft <= ttfa <= ttlt <= total
    # The fake upstream pauses 200ms after the reasoning delta, so TTFA must be
    # measurably later than TTFT when reasoning is excluded.
    assert ttfa - ttft >= 150

    recent = (await client.get("/api/logs/recent?limit=5", headers=headers)).json()
    listed = next(row for row in recent["items"] if row["request_id"] == item["request_id"])
    assert (listed["ttft_ms"], listed["ttfa_ms"], listed["ttlt_ms"]) == (ttft, ttfa, ttlt)


async def test_non_stream_request_has_null_stream_latencies(client, upstream):
    headers, channel, key = await _prepare(client, upstream)
    requested = f"{channel['slug']}/gpt-test"

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": requested,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["is_stream"] is False
    assert log["ttft_ms"] is None
    assert log["ttfa_ms"] is None
    assert log["ttlt_ms"] is None
    assert log["total_ms"] >= 0


def test_list_content_parts_are_visible_text() -> None:
    def chunk(delta):
        return {"choices": [{"index": 0, "delta": delta}]}

    parts = [{"type": "text", "text": "你"}, "好"]
    assert has_answer_surface(chunk({"content": parts})) is True
    assert meaningful_delta(chunk({"content": parts})) is True
    # An empty typed part is not visible text.
    assert has_answer_surface(chunk({"content": [{"type": "text", "text": ""}]})) is False
    assert meaningful_delta(chunk({"content": [{"type": "text", "text": ""}]})) is False
    # Tool-call-only deltas open the stream (TTFT) and are the first answer
    # (TTFA) for an agentic client.
    tools = [{"index": 0, "id": "call-1", "function": {"name": "bash", "arguments": ""}}]
    assert meaningful_delta(chunk({"tool_calls": tools})) is True
    assert has_answer_surface(chunk({"tool_calls": tools})) is True


@pytest.fixture(params=["litellm", "httpx"])
def stream_transport(request, monkeypatch):
    """Exercise both stream paths: LiteLLM (default) and native httpx."""
    if request.param == "httpx":
        base = get_settings()

        def _patched_settings():
            return base.model_copy(update={"upstream_transport": "httpx"})

        monkeypatch.setattr(gateway_module, "get_settings", _patched_settings)
    return request.param


async def _stream_chat(client, headers, key, requested: str):
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": requested,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    assert response.status_code == 200, response.text
    return await _wait_for_log(client, headers, response.headers["X-Request-ID"])


async def test_list_content_parts_set_ttfa_on_both_transports(
    client, upstream, stream_transport
):
    # LiteLLM types "Delta.content" as "str | None", so a chunk with list-shaped
    # content would be dropped by its stream adapter. The LiteLLM transport
    # repairs those parts at the byte level before the chunk is parsed.
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-parts")
    log = await _stream_chat(client, headers, key, f"{channel['slug']}/gpt-test")

    assert log["source"] == "chat"
    ttft, ttfa, ttlt, total = log["ttft_ms"], log["ttfa_ms"], log["ttlt_ms"], log["total_ms"]
    assert ttft is not None and ttfa is not None and ttlt is not None
    assert 0 <= ttft <= ttfa <= ttlt <= total


def test_content_list_normalising_stream_repairs_typed_parts() -> None:
    rewrite = gateway_module._ContentListNormalisingStream._rewrite_line
    typed = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": [{"type": "text", "text": "你"}, "好"]},
                }
            ]
        }
    ).encode("utf-8")
    repaired = rewrite(b"data: " + typed)
    payload = json.loads(repaired.split(b":", 1)[1].strip())
    assert payload["choices"][0]["delta"]["content"] == "你好"

    # Plain string content, keep-alive lines and the [DONE] sentinel pass through.
    plain = b'data: {"choices": [{"delta": {"content": "hi"}}]}'
    assert rewrite(plain) == plain
    assert rewrite(b": keep-alive") == b": keep-alive"
    assert rewrite(b"data: [DONE]") == b"data: [DONE]"


async def test_tool_call_only_stream_sets_ttfa_equal_to_ttft_on_both_transports(
    client, upstream, stream_transport
):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-tools")
    log = await _stream_chat(client, headers, key, f"{channel['slug']}/gpt-test")

    ttft, ttfa, ttlt, total = log["ttft_ms"], log["ttfa_ms"], log["ttlt_ms"], log["total_ms"]
    assert ttft is not None and ttfa is not None and ttlt is not None
    # The first delta already carries the tool call, so the first answer lands
    # on the same frame as the first byte: TTFA == TTFT, never "TTFT set but
    # TTFA null" for a tool-use workload.
    assert ttfa == ttft
    assert 0 <= ttft <= ttfa <= ttlt <= total


async def test_reasoning_only_stream_keeps_ttfa_null(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-stream-reasoning-only")
    log = await _stream_chat(client, headers, key, f"{channel['slug']}/gpt-test")

    # Thinking is not an answer: TTFT is set by the reasoning delta, TTFA waits
    # for a delta that never comes.
    assert log["ttft_ms"] is not None
    assert log["ttfa_ms"] is None
    assert log["ttlt_ms"] is not None


async def test_non_stream_tool_call_only_response_is_not_a_pseudo_success(
    client, upstream
):
    headers, channel, key = await _prepare(client, upstream, "/openai-json-tools")
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["http_status"] == 200
    assert log["error_code"] is None
    # TTFT/TTFA are stream-only fields: a non-stream call keeps them null and is
    # measured by total_ms instead.
    assert log["ttft_ms"] is None
    assert log["ttfa_ms"] is None


async def test_non_stream_legacy_function_call_is_not_a_pseudo_success(client, upstream):
    headers, channel, key = await _prepare(client, upstream, "/openai-json-function-call")
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    log = await _wait_for_log(client, headers, response.headers["X-Request-ID"])
    assert log["http_status"] == 200
