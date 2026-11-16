from __future__ import annotations

import json
from typing import Any

from l01_core.logging import get_logger
from l04_repo import orm
from l05_service.llm import Usage

logger = get_logger("slipstream.billing")


def cache_ratio(usage: Usage) -> float:
    """
    Vendor-agnostic cache hit ratio.

    - Containment: prompt_tokens already includes cached tokens -> denominator = prompt_tokens
    - Parallel: prompt_tokens is uncached only -> denominator = prompt_tokens + cached_tokens
    """
    if usage.cached_tokens <= 0:
        return 0.0
    if usage.prompt_tokens >= usage.cached_tokens:
        denominator = usage.prompt_tokens
    else:
        denominator = usage.prompt_tokens + usage.cached_tokens
    return round(usage.cached_tokens / denominator, 6) if denominator else 0.0


def compute_cost(model: orm.ChannelModelORM | None, usage: Usage) -> float:
    """
    Split prompt tokens into cache hits and misses.

    cache_read_price_per_1m NULL means "not configured": hits fall back to
    input_price_per_1m, so the result is bit-identical to the pre-split formula.
    Cached tokens are clamped to [0, prompt_tokens] because vendors sometimes
    report hits larger than the prompt, and an unclamped count would turn a cost
    into a discount. cache_write_price_per_1m is stored but not billed yet
    (stage 2 needs the Anthropic-native entry point).
    """
    if not model:
        return 0.0
    prompt_tokens = max(int(usage.prompt_tokens or 0), 0)
    completion_tokens = max(int(usage.completion_tokens or 0), 0)
    cached_tokens = min(max(int(usage.cached_tokens or 0), 0), prompt_tokens)
    input_price = float(model.input_price_per_1m or 0)
    cached_price = model.cache_read_price_per_1m
    cached_price = input_price if cached_price is None else float(cached_price)
    return round(
        (prompt_tokens - cached_tokens) / 1_000_000 * input_price
        + cached_tokens / 1_000_000 * cached_price
        + completion_tokens / 1_000_000 * float(model.output_price_per_1m or 0),
        8,
    )


def estimate_usage(messages: list[dict[str, Any]], output_text: str = "", prompt_tokens: int = 0) -> Usage:
    return estimate_usage_from_text(messages, output_text, prompt_tokens=prompt_tokens)


def estimate_usage_from_chars(
    messages: list[dict[str, Any]], completion_chars: int, prompt_tokens: int = 0
) -> Usage:
    """Cheap token estimate: ~4 characters per token, no tokenizer dependency."""
    if not prompt_tokens:
        serialized = json.dumps(messages, ensure_ascii=False)
        prompt_tokens = max(1, len(serialized) // 4)
    completion_tokens = max(0, completion_chars // 4)
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


# ---------------------------------------------------------------------------
# Tokenizer-backed fallback estimation (ADR-075 修订): real token counts when
# the tiktoken vocabulary is available, script-aware char heuristic otherwise.
# ---------------------------------------------------------------------------

_TIKTOKEN_STATE: str = "untried"  # untried | ready | unavailable
_TIKTOKEN_ENCODING: Any = None
TIKTOKEN_ENCODING_NAME = "o200k_base"


def _get_tiktoken_encoding() -> Any:
    # Lazy singleton: the first call downloads the vocabulary (~7s, cached on
    # disk afterwards). Any failure (offline deployment, missing wheel) is
    # permanent for this process and callers fall back to heuristic_tokens.
    global _TIKTOKEN_STATE, _TIKTOKEN_ENCODING
    if _TIKTOKEN_STATE == "ready":
        return _TIKTOKEN_ENCODING
    if _TIKTOKEN_STATE == "unavailable":
        return None
    try:
        import tiktoken

        _TIKTOKEN_ENCODING = tiktoken.get_encoding(TIKTOKEN_ENCODING_NAME)
        _TIKTOKEN_STATE = "ready"
    except Exception as exc:  # noqa: BLE001 - estimation must never break serving
        logger.warning("tiktoken 词表不可用，兜底估算退回按脚本折算: %s", exc)
        _TIKTOKEN_STATE = "unavailable"
        _TIKTOKEN_ENCODING = None
    return _TIKTOKEN_ENCODING


def heuristic_tokens(text: str) -> int:
    # ASCII（英文/数字/常见符号）按 4 字符/词元；非 ASCII（中文等）按 1.5 词元/字。
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return ascii_chars // 4 + ((len(text) - ascii_chars) * 3) // 2


def estimate_usage_from_text(
    messages: list[dict[str, Any]],
    completion_text: str = "",
    prompt_tokens: int = 0,
    completion_overflow_chars: int = 0,
) -> Usage:
    # Estimate from relayed text: tiktoken counts when the vocabulary loaded,
    # otherwise the script-aware heuristic. completion_overflow_chars covers
    # text beyond the stream buffer cap -- counted at the non-ASCII rate as
    # the conservative side of the heuristic (long outputs skew multilingual).
    encoding = _get_tiktoken_encoding()
    if not prompt_tokens:
        serialized = json.dumps(messages, ensure_ascii=False)
        prompt_tokens = (
            len(encoding.encode_ordinary(serialized))
            if encoding is not None
            else heuristic_tokens(serialized)
        )
    if encoding is not None:
        completion_tokens = len(encoding.encode_ordinary(completion_text))
    else:
        completion_tokens = heuristic_tokens(completion_text)
    completion_tokens = max(0, completion_tokens) + (max(0, completion_overflow_chars) * 3) // 2
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


MIN_GENERATION_MS = 100


def throughput_tps(completion_tokens: int, total_ms: int, ttft_ms: int | None) -> float | None:
    """Output tokens divided by generation time (total minus time to first token)."""
    if ttft_ms is None or completion_tokens <= 0 or total_ms <= 0:
        return None
    generation_ms = total_ms - ttft_ms
    # 上游把整段输出缓冲后一次性吐出时（窗口趋近 0），没有「生成速度」可言：
    # 152 token / 5ms 会被算成 30400 t/s 这种假速率，按无吞吐处理。
    if generation_ms < MIN_GENERATION_MS:
        return None
    return round(completion_tokens / (generation_ms / 1000), 2)
