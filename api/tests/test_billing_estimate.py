from __future__ import annotations

import pytest

from l05_service import billing


class _StubEncoding:
    """Deterministic encoder: one token per 3 characters."""

    def encode_ordinary(self, text: str) -> list[int]:
        return [0] * (len(text) // 3)


def test_heuristic_tokens_splits_by_script():
    # ASCII（英文/数字/符号）按 4 字符/词元；非 ASCII（中文等）按 1.5 词元/字。
    assert billing.heuristic_tokens("") == 0
    assert billing.heuristic_tokens("abcdefgh") == 2
    assert billing.heuristic_tokens("中文") == 3
    assert billing.heuristic_tokens("ab中文cd") == 1 + 3


def test_estimate_usage_from_text_uses_encoder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(billing, "_get_tiktoken_encoding", lambda: _StubEncoding())
    usage = billing.estimate_usage_from_text(
        [{"role": "user", "content": "abcdefghij"}],
        "abcdefghij",  # 10 // 3 = 3 tokens
        completion_overflow_chars=3,  # 3 * 3 // 2 = 4 tokens
    )
    assert usage.completion_tokens == 3 + 4
    assert usage.prompt_tokens > 0
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


def test_estimate_usage_from_text_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(billing, "_get_tiktoken_encoding", lambda: None)
    usage = billing.estimate_usage_from_text(
        [{"role": "user", "content": "abcd"}],
        "abcdefgh中文",  # ascii 8 -> 2；非 ascii 2 -> 3
        completion_overflow_chars=2,  # 2 * 3 // 2 = 3
    )
    assert usage.completion_tokens == 5 + 3
    assert usage.prompt_tokens > 0


def test_estimate_usage_respects_known_prompt_tokens(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(billing, "_get_tiktoken_encoding", lambda: None)
    usage = billing.estimate_usage(
        [{"role": "user", "content": "hi"}], "hello", prompt_tokens=42
    )
    assert usage.prompt_tokens == 42
    assert usage.completion_tokens == 1  # "hello" 5 个 ASCII 字符 -> 5 // 4
    assert usage.total_tokens == 43


def test_estimate_counts_media_parts_as_constant(monkeypatch: pytest.MonkeyPatch):
    """P1.5-D 裁定：非文本媒体 part 按常数计（两条路径同规则）。"""
    monkeypatch.setattr(billing, "_get_tiktoken_encoding", lambda: _StubEncoding())
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "x" * 400000}}
    usage = billing.estimate_usage_from_text(
        [{"role": "user", "content": [{"type": "text", "text": "abcdefghij"}, image]}],
    )
    # 占位符 {"type":"media"} 走 stub 分词（len//3），base64 不进分词器：
    # 文本贡献 + 常数 1000，而不是 base64//4 的灾难性高估。
    assert usage.prompt_tokens < 2000
    assert usage.prompt_tokens >= billing._IMAGE_TOKENS


def test_estimate_media_constant_in_heuristic_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(billing, "_get_tiktoken_encoding", lambda: None)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "x" * 100}}
    usage = billing.estimate_usage_from_text(
        [{"role": "user", "content": [{"type": "text", "text": "abcdefgh"}, image]}],
    )
    assert usage.prompt_tokens == 2 + billing._IMAGE_TOKENS + billing._PROMPT_STRUCT_TOKENS


def test_estimate_no_media_parts_unchanged(monkeypatch: pytest.MonkeyPatch):
    """无媒体 part 时净化是恒等变换：结果 == 直接序列化原始 messages。"""
    import json as _json

    monkeypatch.setattr(billing, "_get_tiktoken_encoding", lambda: _StubEncoding())
    messages = [{"role": "user", "content": [{"type": "text", "text": "abcdefghij"}]}]
    usage = billing.estimate_usage_from_text(messages)
    expected = len(_StubEncoding().encode_ordinary(_json.dumps(messages, ensure_ascii=False)))
    assert usage.prompt_tokens == expected
