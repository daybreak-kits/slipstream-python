from __future__ import annotations

import json
from typing import Any, Iterator

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
# Tokenizer-backed fallback estimation (ADR-075 修订): bounded estimation --
# text within the tiktoken budget gets real token counts, anything larger is
# folded by the script-aware heuristic so the cost stays bounded no matter
# how big the relayed text grows.
# ---------------------------------------------------------------------------

# tiktoken 精确分词的文本长度上限（字符数）。实测 encode_ordinary ~38MB/s：
# 128K 字符以内最坏 ~10ms（中文 ≈ 384KB UTF-8），真分词器的精度收益大于成本；
# 超出后编码成本随文本线性膨胀（4MB ≈ 105ms，且发生在流式 finalize、与在飞
# 请求抢同一事件循环），改走 heuristic_tokens 的脚本折算（ADR-075 口径，
# 误差量级见 docs/upstream_transport.md §11.4-1：±30%，随语言变化）。
_TIKTOKEN_MAX_CHARS = 131_072

# 启发式 prompt 折算里每条消息的 JSON 结构开销（role/content 键名、引号等，
# o200k 下约 6-10 tok，取中值）。仅在跳过完整序列化的 prompt 折算路径上使用。
_PROMPT_STRUCT_TOKENS = 8

# 每个非文本多模态 part（图片/音频/文件）的估算常数。各家口径差异大且不可比：
# OpenAI 低细节 85 / 高细节 ~1105，Claude 按像素 ≈ 1000（1024×768），Gemini 固定
# 258——取档位中值 1000。估算行带 usage_estimated 标记（GLOSSARY §5），本就不
# 是计费口径；取常数的动机是两侧都要消灭 base64 当文本处理的灾难性高估
# （1MB 图 ≈ 350K tok，误差 350×）：精确路径把 media part 替换成占位符再分词，
# 启发式路径直接加常数（2026-11-19 用户裁定按建议落地）。
_IMAGE_TOKENS = 1000

# media part 的识别键：content 列表里带这些键（或没有 text 字段但有 type）的
# dict part 按一个媒体单位计。
_MEDIA_PART_KEYS = ("image_url", "image", "input_audio", "input_file", "file")

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


# UTF-8 字节映射表：多字节序列的首字节（≥ 0xC0）标记为 1，ASCII 字节与
# 续字节（0x80-0xBF）标记为 0。首字节个数 == 非 ASCII 字符个数。
_U8_FIRST_BYTE_MARKS = bytes(1 if b >= 0xC0 else 0 for b in range(256))


def heuristic_tokens(text: str) -> int:
    # ASCII（英文/数字/常见符号）按 4 字符/词元；非 ASCII（中文等）按 1.5 词元/字。
    # 实现走 isascii 快路径 + UTF-8 首字节标记计数，全程 C 级扫描：旧的逐字符
    # Python 循环只有 ~10MB/s，4MB relay 文本要数百毫秒，与 tiktoken 一样是
    # finalize 的延迟尖峰来源。与旧实现逐位一致：len - 首字节数 即 ASCII 字符数。
    if text.isascii():
        return len(text) // 4
    # lone surrogate 按 3 字节编码（首字节 ED ≥ 0xC0），同样计 1 个非 ASCII 字，
    # 与逐字符实现一致；surrogatepass 保证估算路径永不抛编码错误。
    non_ascii = text.encode("utf-8", "surrogatepass").translate(_U8_FIRST_BYTE_MARKS).count(1)
    return (len(text) - non_ascii) // 4 + (non_ascii * 3) // 2


def _is_media_part(part: Any) -> bool:
    """content 列表里的非文本 part（图片/音频/文件）按媒体单位计。"""
    if not isinstance(part, dict):
        return False
    if isinstance(part.get("text"), str):
        return False
    return any(key in part for key in _MEDIA_PART_KEYS) or "type" in part


def _count_media_parts(messages: list[dict[str, Any]]) -> int:
    """Number of non-text media parts across all messages (image-ish units)."""
    count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for part in content if _is_media_part(part))
    return count


def _prompt_text_parts(messages: list[dict[str, Any]]) -> Iterator[str]:
    # Yield the token-relevant text of a message list: content 字符串或多模态
    # part 的 text 字段、tool_calls 的 arguments JSON。粗估与启发式折算共用这一
    # 份提取逻辑，两边对「什么算 prompt 文本」的认识不会漂移。媒体 part 不产出
    # 文本——它们按 _IMAGE_TOKENS 常数另计（见 _count_media_parts）。
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        yield text
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, dict):
                    arguments = (call.get("function") or {}).get("arguments")
                    if isinstance(arguments, str):
                        yield arguments


def _sanitized_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """精确路径的序列化输入：media part 换成占位符（base64 不得进分词器）。

    旧行为把 base64 当普通文本 tokenize（1MB 图 ≈ 350K tok 的灾难性高估）；
    占位符 + 常数（_IMAGE_TOKENS）把误差拉回常数级。只处理 content 列表，
    其余字段浅拷贝。
    """
    sanitized: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(_is_media_part(part) for part in content):
            replacement = []
            for part in content:
                if _is_media_part(part):
                    replacement.append({"type": "media"})
                else:
                    replacement.append(part)
            message = {**message, "content": replacement}
        sanitized.append(message)
    return sanitized


def _prompt_chars(messages: list[dict[str, Any]]) -> int:
    """Cheap prompt magnitude check: sum content lengths, no serialization."""
    return sum(len(text) for text in _prompt_text_parts(messages))


def _prompt_heuristic_tokens(messages: list[dict[str, Any]]) -> int:
    """Script-rate folding for prompts too large (or unable) to tokenize exactly."""
    tokens = sum(heuristic_tokens(text) for text in _prompt_text_parts(messages))
    media = _count_media_parts(messages) * _IMAGE_TOKENS
    return max(1, tokens + media + len(messages) * _PROMPT_STRUCT_TOKENS)


def estimate_usage_from_text(
    messages: list[dict[str, Any]],
    completion_text: str = "",
    prompt_tokens: int = 0,
    completion_overflow_chars: int = 0,
) -> Usage:
    # Estimate from relayed text, bounded: text within _TIKTOKEN_MAX_CHARS is
    # counted by the real tokenizer (when the vocabulary loaded), anything
    # larger falls to the script-aware heuristic so finalize cost stays at the
    # millisecond level. completion_overflow_chars covers text beyond the
    # stream buffer cap -- counted at the non-ASCII rate as the conservative
    # side of the heuristic (long outputs skew multilingual).
    prompt_needs_encode = False
    if not prompt_tokens:
        prompt_needs_encode = _prompt_chars(messages) <= _TIKTOKEN_MAX_CHARS
    completion_needs_encode = len(completion_text) <= _TIKTOKEN_MAX_CHARS
    # 词表按需懒加载：两侧都超阈值时不为一词表下载付出首包 ~7s。
    encoding = (
        _get_tiktoken_encoding()
        if (prompt_needs_encode or completion_needs_encode)
        else None
    )
    if not prompt_tokens:
        media_tokens = _count_media_parts(messages) * _IMAGE_TOKENS
        if prompt_needs_encode and encoding is not None:
            # 小 prompt 只在此处序列化一次（阈值内 dumps 成本可忽略）；media
            # part 先换成占位符（base64 进分词器是 350× 级高估），常数另计。
            serialized = json.dumps(_sanitized_messages(messages), ensure_ascii=False)
            prompt_tokens = len(encoding.encode_ordinary(serialized)) + media_tokens
        else:
            # 大 prompt（或词表不可用）不构造完整 JSON：逐段折算 + 结构常数。
            prompt_tokens = _prompt_heuristic_tokens(messages)
    if completion_needs_encode and encoding is not None:
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
