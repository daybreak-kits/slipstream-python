"""探测输出判定的单元测试。

探测的成功判据是「存在任一可识别输出」：正文、思考块（reasoning_content /
thinking / Gemini thought）、工具调用，或 usage 的输出 token。本文件只测判定，
不测探测的协议/计费口径。真实事故：ZAI glm-5.3-flash 把探测预算全花在思考上，
正文为空但 reasoning_content 与 usage 都有值，旧逻辑报「内容是空的」。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from l04_repo import orm
from l05_service import probe


def _channel(protocol: str = probe.PROTOCOL_OPENAI) -> orm.ChannelORM:
    return orm.ChannelORM(
        id="channel-probe",
        slug="channel-probe",
        name="探测渠道",
        connection_url="http://127.0.0.1:9",
        protocol=protocol,
        auth_style="bearer",
        rewrite_rules_json="{}",
        enabled=True,
    )


async def _probe_with(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    *protocol: str,
) -> probe.ChatAttempt:
    """Run _post_chat against a canned upstream response."""

    async def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(probe, "upstream_probe_post", fake_post)
    monkeypatch.setattr(probe, "_channel_key", lambda channel: "test-key")
    selected = protocol[0] if protocol else probe.PROTOCOL_OPENAI
    return await probe._post_chat(
        _channel(selected), selected, "bearer", "glm-5.3-flash", 5
    )


def _openai(
    message: dict[str, Any] | None = None,
    *,
    delta: dict[str, Any] | None = None,
    completion: int = 0,
) -> dict[str, Any]:
    choice: dict[str, Any] = {"index": 0}
    if message is not None:
        choice["message"] = message
    if delta is not None:
        choice["delta"] = delta
    return {
        "choices": [choice],
        "usage": {
            "prompt_tokens": 84,
            "completion_tokens": completion,
            "total_tokens": 84 + completion,
        },
    }


async def test_reasoning_content_with_empty_text_is_success(monkeypatch):
    payload = _openai(
        {"role": "assistant", "content": "", "reasoning_content": "先想清楚再回答"},
        completion=109,
    )
    attempt = await _probe_with(monkeypatch, payload)
    assert attempt.ok is True
    assert "思考块" in attempt.message
    assert attempt.message.startswith("识别输出：")
    assert "空的" not in attempt.message


async def test_stream_delta_reasoning_is_success(monkeypatch):
    payload = _openai(delta={"reasoning_content": "流式思考片段"}, completion=24)
    attempt = await _probe_with(monkeypatch, payload)
    assert attempt.ok is True
    assert "思考块" in attempt.message


async def test_all_signals_empty_is_failure_with_new_wording(monkeypatch):
    payload = _openai({"role": "assistant", "content": ""}, completion=0)
    attempt = await _probe_with(monkeypatch, payload)
    assert attempt.ok is False
    assert attempt.message == (
        "上游返回 200，但没有任何可识别输出"
        "（正文、思考块、工具调用均空，usage 输出 0 tokens）"
    )


async def test_tool_calls_only_is_success(monkeypatch):
    payload = _openai(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "ping", "arguments": "{}"},
                }
            ],
        },
        completion=0,
    )
    attempt = await _probe_with(monkeypatch, payload)
    assert attempt.ok is True
    assert "工具调用" in attempt.message


async def test_usage_output_tokens_only_is_success(monkeypatch):
    payload = _openai({"role": "assistant", "content": ""}, completion=12)
    attempt = await _probe_with(monkeypatch, payload)
    assert attempt.ok is True
    assert "usage 输出 12 tokens" in attempt.message


async def test_text_output_reports_character_count(monkeypatch):
    payload = _openai({"role": "assistant", "content": "OK"}, completion=1)
    attempt = await _probe_with(monkeypatch, payload)
    assert attempt.ok is True
    assert attempt.text == "OK"
    assert "正文 2 字" in attempt.message


async def test_anthropic_thinking_block_is_success(monkeypatch):
    payload = {
        "content": [
            {"type": "thinking", "thinking": "逐步推理中"},
            {"type": "text", "text": ""},
        ],
        "usage": {"input_tokens": 84, "output_tokens": 40},
    }
    attempt = await _probe_with(monkeypatch, payload, probe.PROTOCOL_ANTHROPIC)
    assert attempt.ok is True
    assert "思考块" in attempt.message


async def test_gemini_thought_part_is_success(monkeypatch):
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "想一想", "thought": True},
                        {"text": ""},
                    ]
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 84, "candidatesTokenCount": 40},
    }
    attempt = await _probe_with(monkeypatch, payload, probe.PROTOCOL_GEMINI)
    assert attempt.ok is True
    assert "思考块" in attempt.message