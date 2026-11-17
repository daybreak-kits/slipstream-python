from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from l01_core import jsonfast
from l01_core.config import get_settings
from l01_core.logging import get_logger
from l03_domain.urls import build_chat_url, build_models_url
from l04_repo import orm
from l05_service.channels import select_channel_api_key
from l05_service.upstream_settings import UpstreamTimeouts

logger = get_logger("slipstream.gateway")


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


# How the failover loop must treat an upstream failure.
# permanent: this upstream credential/model cannot serve the request at all,
#   so the candidate is parked and the request rotates instead of burning the
#   retry budget.
# transient: the upstream may recover, so a retry is allowed.
# client: the request itself is wrong, so retrying cannot help.
TIER_PERMANENT = "permanent"
TIER_TRANSIENT = "transient"
TIER_CLIENT = "client"

# Upstream bodies that mean "this key is out of credit" rather than "slow down".
_QUOTA_MARKERS: tuple[str, ...] = (
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "quota exceeded",
    "exceeded quota",
    "billing_hard_limit_reached",
    "credit balance is too low",
    "out of credits",
    "payment required",
)

# Ignore absurd Retry-After values instead of parking a channel for a day.
_MAX_RETRY_AFTER_MS = 3_600_000

# Upstream error text is untrusted. It may be sent to a client only after it has
# been bounded, stripped of control characters, and stripped of credential-like
# tokens; the untouched original stays in LLMError.raw for operators.
_CLIENT_MESSAGE_MAX_CHARS = 200
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}")
_API_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|x-api-key)['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{6,}"
)
_SK_TOKEN_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9._-]{8,}")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class LLMError(Exception):
    kind: str
    message: str
    status_code: int | None = None
    retryable: bool = True
    raw: str | None = None
    # The failover decision reads the tier before retryable, so a permanent
    # failure (dead key, exhausted quota) rotates without consuming budget.
    tier: str = TIER_TRANSIENT
    # Milliseconds from an upstream Retry-After header, if it sent one.
    retry_after_ms: int | None = None
    # Response headers of a failed upstream call, when captured.
    headers: dict[str, str] | None = None

    def __str__(self) -> str:
        return self.message


def json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# Transport framing headers are never copied to the upstream and never relayed
# back to the client: they describe the hop, not the message.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Client credentials must never reach an upstream: the gateway authenticates
# with the channel key instead. This is a security boundary, not a preference.
CLIENT_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "api-key", "x-api-key"}
)


def client_request_headers(request_headers: dict[str, str] | None) -> dict[str, str]:
    """The client's headers as the baseline for the upstream request.

    Keeps the client's own headers (user-agent, custom x-* headers, tracing
    headers, ...) so the caller controls what reaches the upstream, while
    dropping the transport framing and every client credential. Keys are
    normalised to lowercase: header names are case-insensitive (RFC 9110),
    and the gateway-owned overlays in build_headers must replace — not
    duplicate — a client header that differs only in case (undici/Node
    clients send lowercase names).
    """
    if not request_headers:
        return {}
    return {
        name.lower(): value
        for name, value in request_headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"host", "content-length"}
        and name.lower() not in CLIENT_CREDENTIAL_HEADERS
    }


def build_headers(
    channel: orm.ChannelORM,
    request_id: str | None = None,
    auth_style: str | None = None,
    client_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    # The client's headers are the baseline (transparency for custom headers);
    # the gateway overlays the headers it owns so a client can neither spoof the
    # credential nor the request id.
    headers = client_request_headers(client_headers)
    # Gateway-owned headers are written lowercase: the baseline is already
    # normalised (client_request_headers), so these overlays replace the
    # client's values instead of emitting case-duplicate headers on the wire.
    headers["content-type"] = "application/json"
    headers.setdefault("accept", "application/json")
    key = select_channel_api_key(channel, request_id or "gateway")
    style = auth_style or channel.auth_style or "bearer"
    if key:
        if style == "api-key":
            headers["api-key"] = key
        elif style == "x-api-key":
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["authorization"] = f"Bearer {key}"
    if request_id:
        headers["x-request-id"] = request_id
    return headers


def build_request_body(channel: orm.ChannelORM, upstream_model: str, body: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {k: v for k, v in body.items() if v is not None}
    merged["model"] = upstream_model
    merged["messages"] = body.get("messages", [])
    if body.get("stream"):
        merged["stream"] = True
        options = dict(merged.get("stream_options") or {})
        options.setdefault("include_usage", True)
        merged["stream_options"] = options
    return merged


# 请求侧字节快路径的模型 needle：带键名前缀引号，与 ADR-078 同一安全论证——
# JSON 字符串内部的引号必被转义，未转义的 '"model":"' 序列只可能出现在
# 结构位，不会误伤消息文本。
_MODEL_NEEDLE_PREFIX = b'"model":"'


def fast_forward_content(
    channel: orm.ChannelORM,
    raw_body: bytes,
    body: dict[str, Any],
    *,
    requested_model: str,
    upstream_model: str,
    prepared: Any = None,
) -> bytes | None:
    """无改写渠道的请求侧字节快路径（P2）：客户端原始 bytes 直接作为上游请求体。

    语义基线是 :func:`build_request_body`（docs/upstream_transport.md §2）：
    强制 model=upstream_model、messages 缺省补 []、剥离值为 None 的键、
    stream 请求 setdefault stream_options.include_usage=true。本函数在字节域
    复刻同一语义：上游收到的字节 = 客户端字节，仅 model 值被替换、stream
    请求在最后一个非空白字节（必须是 }，保留尾部空白）前注入
    `,"stream_options":{"include_usage":true}`。

    资格条件（全部满足才返回改写后 bytes；任何一条不满足返回 None，调用方
    回退 dict 路径——回退路径与今天逐字节一致，宁可慢不可错）：

    1. httpx 臂（``_use_litellm`` 为假）：litellm 臂从 dict 构造请求，字节域
       无法替代；
    2. ``prepared.no_body_rules``：渠道没有 request_body 改写规则。标记在
       prepare_request 内随规则解析顺带产出，不额外 json.loads 规则；header
       规则不碰 body，不影响资格；
    3. bytes 不含 b"null"：保守复刻 None 剥离语义（null 也可能出现在嵌套
       结构或字符串里，出现即回退，安全性优先）；
    4. model needle：宽松正则 '"model"\\s*:\\s*"<requested>"' 全文恰好命中
       一次，且紧凑 needle '"model":"<requested>"' 命中。恰好一次是关键——
       顶层 model 键在任何拼写下都贡献恰好一次命中，命中数 >1 说明嵌套
       对象里还有同值 model（bytes.replace 会误伤嵌套数据），命中数 =1 但
       紧凑 needle 不命中说明顶层是非紧凑拼写（替换点不确定），都回退；
       另外 upstream_model 含引号/反斜杠/控制字符时回退（dict 路径会转义，
       字节域注入会产生非法 JSON）；
    5. stream 请求（body["stream"] is True）：bytes 无 '"stream_options"'
       needle（顶层已有值时 setdefault 尊重调用方的语义在字节域难廉价判定，
       回退）；body["stream"] truthy 但非 True 的怪值（如 1）回退——dict
       路径会强制 stream=True，字节域不等价。messages 缺失的场景无需字节层
       处理：_validate_body 已在 dict 层拒绝无 messages 的请求。

    needle 替换不按 (requested_model, upstream_model) 缓存：重试/候选切换时
    每个候选的 upstream_model 不同，逐请求计算即可（C 级 replace，成本可忽略）。
    """
    if prepared is not None and not getattr(prepared, "no_body_rules", False):
        return None
    if _use_litellm(channel):
        return None
    if raw_body.startswith(b"\xef\xbb\xbf") or raw_body.find(b"null") >= 0:
        # BOM 前缀：json.loads 能解析但上游多半不能，dict 路径发的是净 JSON。
        return None
    stream_value = body.get("stream")
    inject_usage = False
    if stream_value is True:
        if raw_body.find(b'"stream_options"') >= 0:
            return None
        inject_usage = True
    elif stream_value:
        # truthy 非 true：dict 路径会强制 stream=True，字节域不等价。
        return None
    requested_bytes = requested_model.encode("utf-8")
    loose_needle = re.compile(rb'"model"\s*:\s*"' + re.escape(requested_bytes) + rb'"')
    if len(loose_needle.findall(raw_body)) != 1:
        return None
    model_needle = _MODEL_NEEDLE_PREFIX + requested_bytes + b'"'
    if raw_body.find(model_needle) < 0:
        return None
    if requested_model == upstream_model:
        content = raw_body
    else:
        upstream_bytes = upstream_model.encode("utf-8")
        if (
            b'"' in upstream_bytes
            or b"\\" in upstream_bytes
            or any(byte < 0x20 or byte == 0x7F for byte in upstream_bytes)
        ):
            return None
        content = raw_body.replace(model_needle, _MODEL_NEEDLE_PREFIX + upstream_bytes + b'"')
    if inject_usage:
        trimmed = content.rstrip()
        if not trimmed.endswith(b"}"):
            return None
        content = (
            trimmed[:-1]
            + b',"stream_options":{"include_usage":true}'
            + b"}"
            + content[len(trimmed) :]
        )
    return content


def _sanitize_client_message(value: str) -> str:
    """Return a bounded, credential-masked form safe to send to a client."""
    text = _BEARER_TOKEN_RE.sub("Bearer ***", value)
    text = _API_KEY_VALUE_RE.sub(lambda match: match.group(1) + "=***", text)
    text = _SK_TOKEN_RE.sub("sk-***", text)
    text = _CONTROL_CHARS_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:_CLIENT_MESSAGE_MAX_CHARS]


def _error_message(payload: Any, fallback: str) -> str:
    """Pick the client-facing message from an upstream error payload.

    Only a string message/code is relayed, and only after sanitisation. The
    whole JSON body is never echoed: when the payload has no usable string
    message the caller's safe fallback wins.
    """
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "code"):
                value = error.get(key)
                if isinstance(value, str):
                    message = _sanitize_client_message(value)
                    if message:
                        return message
            return fallback
        if isinstance(error, str):
            message = _sanitize_client_message(error)
            if message:
                return message
            return fallback
        message = payload.get("message")
        if isinstance(message, str):
            message = _sanitize_client_message(message)
            if message:
                return message
    return fallback


def parse_retry_after_ms(headers: Any) -> int | None:
    """Read a Retry-After header (delta-seconds or HTTP-date) as milliseconds.

    Unparsable values are ignored: a malformed header must not park a channel.
    """
    if not headers:
        return None
    raw: str | None = None
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == "retry-after":
            raw = str(value)
            break
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            when = parsedate_to_datetime(raw)
        except Exception:
            logger.debug("忽略无法解析的 Retry-After: %s", raw)
            return None
        if when is None:
            logger.debug("忽略无法解析的 Retry-After: %s", raw)
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        logger.debug("忽略非正的 Retry-After: %s", raw)
        return None
    return int(min(seconds * 1000.0, float(_MAX_RETRY_AFTER_MS)))


def classify_http_error(status: int, text: str, headers: Any = None) -> LLMError:
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    message = _error_message(payload, f"上游返回 HTTP {status}")
    retry_after_ms = parse_retry_after_ms(headers)
    lowered = text[:2000].lower()
    quota_exhausted = any(marker in lowered for marker in _QUOTA_MARKERS)
    if status == 429:
        if quota_exhausted:
            # Retrying the same key cannot fix an out-of-credit account.
            return LLMError(
                "rate_limit",
                message,
                status,
                False,
                text[:2000],
                tier=TIER_PERMANENT,
                retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
            )
        return LLMError(
            "rate_limit",
            message,
            status,
            True,
            text[:2000],
            tier=TIER_TRANSIENT,
            retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
        )
    if status in (401, 403):
        return LLMError(
            "auth",
            message,
            status,
            False,
            text[:2000],
            tier=TIER_PERMANENT,
            retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
        )
    if status == 402:
        return LLMError(
            "rate_limit",
            message,
            status,
            False,
            text[:2000],
            tier=TIER_PERMANENT,
            retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
        )
    if status == 404:
        # The upstream does not know this model: another candidate may, but
        # this one will keep answering 404.
        return LLMError(
            "not_found",
            message,
            status,
            False,
            text[:2000],
            tier=TIER_PERMANENT,
            retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
        )
    if 400 <= status < 500:
        return LLMError(
            "bad_request",
            message,
            status,
            False,
            text[:2000],
            tier=TIER_CLIENT,
            retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
        )
    return LLMError(
        "upstream",
        message,
        status,
        True,
        text[:2000],
        tier=TIER_TRANSIENT,
        retry_after_ms=retry_after_ms,
                headers=dict(headers) if headers else None,
    )


def content_text(content: Any) -> str:
    """Plain text of a message/delta content field.

    OpenAI streams send a string, but some compatible providers (and the
    chunks LiteLLM relays from them) send a list of typed parts. Both count as
    a delta, and only real text ever satisfies "visible content".
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
        return "".join(parts)
    return ""


def meaningful_content(payload: dict[str, Any]) -> bool:
    choices = payload.get("choices") or []
    if not choices:
        return False
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content_text(content).strip():
        return True
    # A non-stream answer may legitimately carry non-text parts (e.g. images).
    if isinstance(content, list) and content:
        return True
    if message.get("reasoning_content") or message.get("reasoning"):
        return True
    if message.get("tool_calls") or message.get("function_call"):
        return True
    return False


def has_answer_surface(payload: dict[str, Any]) -> bool:
    """True when this delta carries the first answer (text or a tool call).

    A tool call is an answer surface: agentic clients never write text, so
    judging on content alone left TTFA null while TTFT was set — "first byte
    yes, first answer no" for the whole tool-use workload. Reasoning alone is
    still excluded: thinking is not an answer.
    """
    choices = payload.get("choices") or []
    if not choices:
        return False
    delta = choices[0].get("delta") or {}
    if content_text(delta.get("content")).strip():
        return True
    return bool(delta.get("tool_calls") or delta.get("function_call"))


def meaningful_delta(payload: dict[str, Any]) -> bool:
    choices = payload.get("choices") or []
    if not choices:
        return False
    delta = choices[0].get("delta") or {}
    if content_text(delta.get("content")).strip():
        return True
    if delta.get("reasoning_content") or delta.get("reasoning"):
        return True
    if delta.get("tool_calls"):
        return True
    return False


def extract_usage(payload: dict[str, Any] | None) -> Usage:
    if not payload:
        return Usage()
    usage = payload.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    details = usage.get("prompt_tokens_details") or {}
    cached = int(
        details.get("cached_tokens")
        or usage.get("cached_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or 0
    )
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total, cached_tokens=cached)


# ---------------------------------------------------------------------------
# Shared transport plumbing
# ---------------------------------------------------------------------------

_raw_client: httpx.AsyncClient | None = None
_litellm_client: httpx.AsyncClient | None = None
_probe_client: httpx.AsyncClient | None = None

# LiteLLM synthesises usage with its tokenizer when the upstream omits a
# usage chunk, so the presence of ``chunk.usage`` cannot tell us whether the
# numbers came from the provider. The transport below taps the raw SSE bytes
# and records whether a real upstream ``usage`` object was observed.
_USAGE_TRACE_HEADER = "x-qagent-usage-trace"


class _UsageObserver:
    __slots__ = ("seen", "finished", "usage")

    def __init__(self) -> None:
        self.seen = False
        # True only when the upstream itself sent a finish_reason chunk or the
        # [DONE] sentinel. LiteLLM fabricates a finish_reason="stop" for a
        # truncated connection, so its output alone cannot be trusted.
        self.finished = False
        self.usage: Usage | None = None


_usage_observers: dict[str, _UsageObserver] = {}


def _flatten_delta_content(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Replace list-shaped ``delta.content`` values with their plain text.

    LiteLLM types ``Delta.content`` as ``str | None``, so a chunk whose content
    is a list of typed parts fails validation and the whole chunk is dropped
    by the stream adapter. Rewriting the list to its text keeps those upstreams
    readable. ``None`` is returned when no delta needed a change.
    """
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None
    rewritten: list[Any] = []
    changed = False
    for choice in choices:
        if not isinstance(choice, dict):
            rewritten.append(choice)
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict) or not isinstance(delta.get("content"), list):
            rewritten.append(choice)
            continue
        text = content_text(delta["content"])
        if not text:
            # No textual part in this chunk: leave it untouched. LiteLLM drops
            # it exactly as it does today, and the original payload is kept.
            rewritten.append(choice)
            continue
        new_delta = dict(delta)
        new_delta["content"] = text
        new_choice = dict(choice)
        new_choice["delta"] = new_delta
        rewritten.append(new_choice)
        changed = True
    if not changed:
        return None
    payload = dict(payload)
    payload["choices"] = rewritten
    return payload


class _ContentListNormalisingStream(httpx.AsyncByteStream):
    """Rewrite SSE deltas that carry a list of typed content parts.

    Only ``data:`` lines are touched, and only when they really contain a
    list-shaped ``delta.content``; every other byte is passed through
    unchanged so LiteLLM keeps seeing the upstream stream verbatim.
    """

    _MAX_BUFFER = 4 * 1024 * 1024

    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self._inner = inner
        self._buffer = b""

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        async for chunk in self._inner:
            self._buffer += chunk
            pieces: list[bytes] = []
            while True:
                index = self._buffer.find(b"\n")
                if index < 0:
                    break
                line = self._buffer[:index]
                self._buffer = self._buffer[index + 1 :]
                pieces.append(self._rewrite_line(line))
                pieces.append(b"\n")
            if pieces:
                yield b"".join(pieces)
            if len(self._buffer) > self._MAX_BUFFER:
                # A single SSE frame this large cannot be rewritten safely.
                # Flush it untouched instead of buffering without bound.
                yield self._buffer
                self._buffer = b""

    async def aclose(self) -> None:
        await self._inner.aclose()

    @staticmethod
    def _rewrite_line(line: bytes) -> bytes:
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return line
        body = stripped[len(b"data:") :].strip()
        if not body or body == b"[DONE]":
            return line
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001 - keep non-JSON SSE untouched
            return line
        if not isinstance(payload, dict):
            return line
        rewritten = _flatten_delta_content(payload)
        if rewritten is None:
            return line
        encoded = json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        head = b"data"
        separator = b" " if stripped[len(head) + 1 :].startswith(b" ") else b""
        return head + b":" + separator + encoded


class _UsageTapStream(httpx.AsyncByteStream):
    """Pass raw response bytes through while watching for a usage chunk."""

    def __init__(self, inner: httpx.AsyncByteStream, observer: _UsageObserver) -> None:
        self._inner = inner
        self._observer = observer
        self._buffer = b""

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        async for chunk in self._inner:
            self._observe(chunk)
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()

    def _observe(self, chunk: bytes) -> None:
        if self._observer.seen and self._observer.finished:
            return
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            data = line.strip()
            if not data.startswith(b"data:"):
                continue
            data = data[5:].strip()
            if not data:
                continue
            if data == b"[DONE]":
                self._observer.finished = True
                continue
            try:
                payload = json.loads(data)
            except Exception:  # noqa: BLE001 - ignore partial/non-JSON SSE
                continue
            if not isinstance(payload, dict):
                continue
            usage = payload.get("usage")
            if isinstance(usage, dict) and usage:
                self._observer.seen = True
                parsed = extract_usage(payload)
                if parsed.total_tokens:
                    self._observer.usage = parsed
            for choice in payload.get("choices") or []:
                if isinstance(choice, dict) and choice.get("finish_reason"):
                    self._observer.finished = True
                    break
        # Protect against a single enormous content delta. The usage chunk is
        # a small standalone line, so losing the head of a huge line is safe.
        if len(self._buffer) > 65536:
            self._buffer = self._buffer[-8192:]


def _setting(settings_obj: Any, name: str, default: Any) -> Any:
    value = getattr(settings_obj, name, default)
    return default if value is None else value


def _limits(settings_obj: Any) -> httpx.Limits:
    return httpx.Limits(
        max_connections=int(_setting(settings_obj, "upstream_max_connections", 200)),
        max_keepalive_connections=int(
            _setting(settings_obj, "upstream_max_keepalive_connections", 50)
        ),
        keepalive_expiry=float(_setting(settings_obj, "upstream_keepalive_expiry_s", 30.0)),
    )


def _client_timeout(
    settings_obj: Any, total_s: float, upstream_timeouts: UpstreamTimeouts | None = None
) -> httpx.Timeout:
    # upstream_timeouts（设置页覆盖，upstream_settings 服务）优先于 env 默认。
    connect_cfg = (
        float(upstream_timeouts.connect_timeout_s)
        if upstream_timeouts is not None
        else float(_setting(settings_obj, "upstream_connect_timeout_s", 10.0))
    )
    read_cfg = (
        float(upstream_timeouts.read_timeout_s)
        if upstream_timeouts is not None
        else float(_setting(settings_obj, "upstream_read_timeout_s", total_s))
    )
    connect = min(total_s, connect_cfg)
    read = min(total_s, read_cfg)
    write = min(total_s, 10.0)
    pool = min(total_s, 10.0)
    return httpx.Timeout(total_s, connect=max(0.1, connect), read=max(0.1, read), write=max(0.1, write), pool=max(0.1, pool))


def _stream_client_timeout(
    settings_obj: Any, read_s: float, upstream_timeouts: UpstreamTimeouts | None = None
) -> httpx.Timeout:
    # Client timeout for a streaming request: no total, read is per-chunk.
    # httpx would otherwise kill the body read at total even though the
    # watcher no longer enforces a deadline (ADR-075).
    connect_cfg = (
        float(upstream_timeouts.connect_timeout_s)
        if upstream_timeouts is not None
        else float(_setting(settings_obj, "upstream_connect_timeout_s", 10.0))
    )
    connect = min(read_s, connect_cfg)
    return httpx.Timeout(None, connect=max(0.1, connect), read=max(0.1, read_s), write=10.0, pool=10.0)


class _HeaderCaseTransport(httpx.AsyncHTTPTransport):
    """Restore conventional header casing after the OpenAI SDK normalises it.

    HTTP header names are case-insensitive, but several OpenAI-compatible
    upstreams (and compatibility tests) compare names literally. The SDK hands
    us lowercase names; this transport writes them back in canonical form.
    """

    _LOWER: frozenset[str] = frozenset(
        {"authorization", "content-type", "content-length", "host", "accept", "user-agent"}
    )

    @staticmethod
    def _canonical(name: str) -> str:
        lower = name.lower()
        if lower in _HeaderCaseTransport._LOWER:
            return "-".join(part.capitalize() for part in lower.split("-"))
        return "-".join(part.capitalize() for part in lower.split("-"))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        trace_id = request.headers.get(_USAGE_TRACE_HEADER)
        raw: list[tuple[bytes, bytes]] = []
        for name, value in request.headers.multi_items():
            if name.lower() == _USAGE_TRACE_HEADER:
                continue
            raw.append((self._canonical(name).encode("ascii"), value.encode("latin-1")))
        request.headers = httpx.Headers(raw)
        response = await super().handle_async_request(request)
        observer = _usage_observers.get(trace_id) if trace_id else None
        if observer is not None:
            # Repair typed content parts before LiteLLM parses the chunk: its
            # "Delta.content" is typed "str | None", so a list value makes the
            # whole chunk fail validation and the body is silently lost.
            response.stream = _UsageTapStream(
                _ContentListNormalisingStream(response.stream), observer
            )
        return response


def _new_client(settings_obj: Any, *, canonical_headers: bool = False) -> httpx.AsyncClient:
    transport = _HeaderCaseTransport() if canonical_headers else None
    return httpx.AsyncClient(
        transport=transport,
        limits=_limits(settings_obj),
        timeout=_client_timeout(settings_obj, float(settings_obj.request_timeout_s)),
        follow_redirects=True,
    )


def _get_client() -> httpx.AsyncClient:
    global _raw_client
    if _raw_client is None:
        settings_obj = get_settings()
        _raw_client = _new_client(settings_obj)
    return _raw_client


def _new_probe_client(settings_obj: Any) -> httpx.AsyncClient:
    """Build the process-wide client used only by upstream probes.

    Probes are one-shot requests that sit outside the chat hot path. Reusing a
    single client keeps its connection pool (and the DNS/TLS sessions inside it)
    warm between probes instead of paying a fresh handshake per call. Probe
    semantics stay unchanged: redirects are not followed (httpx default, as
    before) and every probe still passes its own timeout per request.
    """
    return httpx.AsyncClient(
        limits=_limits(settings_obj),
        timeout=_client_timeout(settings_obj, float(settings_obj.request_timeout_s)),
    )


def _get_probe_client() -> httpx.AsyncClient:
    global _probe_client
    if _probe_client is None or _probe_client.is_closed:
        _probe_client = _new_probe_client(get_settings())
    return _probe_client


_litellm_warmed = False
_litellm_cost_calc_disabled = False


def _litellm_cost_calc_enabled() -> bool:
    """LITELLM_COST_CALC=on keeps LiteLLM's own cost math (default: stubbed)."""
    value = str(_setting(get_settings(), "litellm_cost_calc", "off")).strip().lower()
    return value in ("on", "1", "true", "yes", "enabled")


def _disable_litellm_cost_calc() -> None:
    """Stub LiteLLM's response cost calculation; billing prices from DB rows.

    LiteLLM computes a cost for every request twice: once in the success
    handler and once in the streaming end-of-stream chunk builder. Each pass
    walks the model cost map and, for models missing from it, runs a zero-cost
    diagnostic; together they cost several ms of CPU per request. Nothing here
    reads response_cost or usage.cost (billing.compute_cost prices from DB
    prices, _response_headers only needs _hidden_params headers), so one
    zero-returning stub removes the work from every call site: the Logging
    method, its _response_cost_calculator alias, the cost_calculator module
    function and the litellm lazy export. LITELLM_COST_CALC=on skips this
    and restores the real calculator.
    """
    global _litellm_cost_calc_disabled
    if _litellm_cost_calc_disabled:
        return
    import litellm
    import litellm.cost_calculator as cost_calculator
    from litellm.litellm_core_utils.litellm_logging import Logging

    def _zero_cost(*_args: Any, **_kwargs: Any) -> float:
        return 0.0

    Logging.response_cost_calculator = _zero_cost  # type: ignore[method-assign]
    Logging._response_cost_calculator = _zero_cost  # type: ignore[attr-defined]
    cost_calculator.response_cost_calculator = _zero_cost  # type: ignore[assignment]
    litellm.response_cost_calculator = _zero_cost  # type: ignore[assignment]
    _litellm_cost_calc_disabled = True
    logger.info("litellm cost calculation stubbed (billing uses DB prices)")


async def _warm_litellm() -> None:
    """Run LiteLLM's one-time streaming initialization before real traffic.

    The first ``litellm.acompletion(stream=True)`` in a process spends most of
    a second importing/initializing internals. During that window incoming SSE
    bytes sit in the socket buffer, so the first request's TTFT/TTFA get
    compressed. A mock-transport warmup pays that cost during startup instead.
    """
    global _litellm_warmed
    if _litellm_warmed:
        return

    import litellm

    def handler(_request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"id":"warmup","object":"chat.completion.chunk",'
            '"model":"warmup","choices":[{"index":0,"delta":{"content":"x"},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"warmup","object":"chat.completion.chunk",'
            '"model":"warmup","choices":[{"index":0,"delta":{},'
            '"finish_reason":"stop"}]}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    warm_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    previous = getattr(litellm, "aclient_session", None)
    litellm.aclient_session = warm_client
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model="openai/warmup",
                api_base="http://litellm-warmup.invalid/v1",
                api_key="sk-warmup",
                messages=[{"role": "user", "content": "warmup"}],
                stream=True,
                stream_options={"include_usage": True},
                timeout=5,
                max_retries=0,
            ),
            timeout=10,
        )
        async for _chunk in response:
            pass
    except Exception:
        # Warmup is best-effort; a failure must not stop the gateway.
        pass
    finally:
        litellm.aclient_session = previous
        await warm_client.aclose()
        litellm.in_memory_llm_clients_cache.flush_cache()
        _litellm_warmed = True


async def startup(settings_obj: Any | None = None) -> None:
    """Create the process-wide HTTP pools and hand one to LiteLLM."""
    global _raw_client, _litellm_client
    settings_obj = settings_obj or get_settings()
    if _raw_client is None:
        _raw_client = _new_client(settings_obj)
    transport = str(_setting(settings_obj, "upstream_transport", "httpx")).lower()
    if transport == "litellm":
        import litellm

        if not _litellm_cost_calc_enabled():
            try:
                _disable_litellm_cost_calc()
            except Exception:  # noqa: BLE001 - a broken stub must not block startup
                logger.exception("litellm cost-calc stub failed; keeping cost calculation")

        if _litellm_client is None:
            _litellm_client = _new_client(settings_obj, canonical_headers=True)
        # The OpenAI client is cached by litellm across event loops. A test or
        # a process restart must not reuse a client bound to a dead loop.
        litellm.in_memory_llm_clients_cache.flush_cache()
        litellm.aclient_session = _litellm_client
        await _warm_litellm()


async def shutdown() -> None:
    global _raw_client, _litellm_client, _probe_client
    raw, litellm_client, probe = _raw_client, _litellm_client, _probe_client
    _raw_client = None
    _litellm_client = None
    _probe_client = None
    if "litellm" in sys.modules:
        import litellm

        if getattr(litellm, "aclient_session", None) is litellm_client:
            litellm.aclient_session = None
    for client in (probe, litellm_client, raw):
        if client is not None:
            await client.aclose()


def _use_litellm(channel: orm.ChannelORM) -> bool:
    settings_obj = get_settings()
    if str(_setting(settings_obj, "upstream_transport", "httpx")).lower() != "litellm":
        return False
    return _litellm_api_base(channel) is not None


def _litellm_api_base(channel: orm.ChannelORM) -> str | None:
    """Map the gateway's chat URL back to the ``api_base`` LiteLLM expects.

    LiteLLM appends ``/chat/completions`` to ``api_base``. Our URL builder has
    already chosen ``/v1`` or a versioned path, so stripping the final suffix
    preserves the exact endpoint the httpx path would have called.
    """
    chat_url = build_chat_url(channel.connection_url)
    suffix = "/chat/completions"
    if not chat_url.endswith(suffix):
        return None
    base = chat_url[: -len(suffix)]
    return base or None


def _request_body_for_litellm(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    prepared: Any,
    *,
    stream: bool,
) -> tuple[str, list[dict[str, Any]], bool, dict[str, Any] | None, dict[str, Any]]:
    rewritten = prepared.body if prepared is not None else body
    request_body = build_request_body(
        channel,
        upstream_model,
        {**rewritten, "stream": True} if stream else rewritten,
    )
    messages = request_body.pop("messages", [])
    model = str(request_body.pop("model", upstream_model))
    stream_flag = bool(request_body.pop("stream", stream))
    stream_options = request_body.pop("stream_options", None)
    return model, messages, stream_flag, stream_options, request_body


def _litellm_auth(headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Convert gateway headers into an SDK api_key plus pass-through headers.

    The OpenAI SDK refuses an empty api_key and always sends an Authorization
    header. Vendors that authenticate with ``api-key`` / ``x-api-key`` therefore
    get a non-empty placeholder key while the real credential travels in
    ``extra_headers``. Bearer credentials are handed to the SDK directly.
    """
    api_key = "sk-litellm-placeholder"
    extra_headers: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower == "authorization":
            bearer = value[7:].strip() if value.lower().startswith("bearer ") else ""
            if bearer:
                api_key = bearer
            # Keep the gateway's header spelling. Some upstreams (and their
            # compatibility tests) compare header names case-sensitively even
            # though HTTP itself does not.
            extra_headers[name] = value
        elif lower in {"api-key", "x-api-key"}:
            extra_headers[name] = value
        elif lower in {"content-type", "content-length", "host", "accept-encoding"}:
            continue
        else:
            extra_headers[name] = value
    return api_key, extra_headers


def _model_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        payload = dump()
        if isinstance(payload, dict):
            return payload
    raise LLMError("upstream", "上游返回了无法解析的响应对象", retryable=True)


def _normalise_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop SDK-added None fields that would change the OpenAI response shape."""
    usage = payload.get("usage")
    if isinstance(usage, dict):
        payload["usage"] = {name: value for name, value in usage.items() if value is not None}
    return payload


def _response_headers(response: Any) -> dict[str, str]:
    hidden = getattr(response, "_hidden_params", None) or {}
    headers: Any = None
    if isinstance(hidden, dict):
        headers = hidden.get("headers") or hidden.get("response_headers")
    if headers is None:
        headers = getattr(response, "_response_headers", None)
    if headers is None:
        return {}
    try:
        return {str(name): str(value) for name, value in dict(headers).items()}
    except Exception:
        return {}


def _exception_text(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return json.dumps(body, ensure_ascii=False)
    if isinstance(body, str) and body:
        return body
    response = getattr(exc, "response", None)
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    return str(exc)


def _cause_chain(exc: Exception):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


# LiteLLM reports "the upstream answered, but the body is not an OpenAI chat
# completion" as a *synthetic* 500 (litellm/llms/openai/openai.py:554 async,
# :604 sync), so a 200 carrying an HTML error page would otherwise surface as
# UPSTREAM_ERROR and be indistinguishable from a real upstream 5xx. The marker
# is litellm's own wording for that branch; it is matched across the cause chain
# and degrades to the generic mapping if a future version rewords it.
_NON_JSON_UPSTREAM_MARKER = "empty or invalid response from llm endpoint"


def _is_non_json_upstream(exc: Exception) -> bool:
    """True when the upstream answered with a body that is not JSON."""
    return any(
        _NON_JSON_UPSTREAM_MARKER in str(cause).lower() for cause in _cause_chain(exc)
    )


def _map_litellm_error(exc: Exception) -> LLMError:
    text = _exception_text(exc)
    # A refused/timed-out TCP connect means the endpoint is unreachable, not
    # that an established upstream got slow. LiteLLM wraps it as Timeout, so
    # inspect the cause chain before the generic timeout mapping.
    for cause in _cause_chain(exc):
        if isinstance(cause, (httpx.ConnectTimeout, httpx.ConnectError)):
            return LLMError("connection", "无法连接上游", retryable=True, raw=text[:2000])
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return LLMError("timeout", "上游调用超时", retryable=True, raw=text[:2000])
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status == 408:
        return LLMError("timeout", "上游调用超时", retryable=True, raw=text[:2000])
    if _is_non_json_upstream(exc):
        # The client must be able to tell "upstream is broken" from "upstream
        # answered 200 with something that is not a chat completion".
        return LLMError(
            "bad_response", "上游返回了非 JSON 响应", retryable=True, raw=text[:2000]
        )
    if isinstance(status, int) and status >= 400:
        response = getattr(exc, "response", None)
        return classify_http_error(status, text, getattr(response, "headers", None))
    if "connection" in name or isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return LLMError("connection", "无法连接上游", retryable=True, raw=text[:2000])
    return LLMError("upstream", "上游调用失败", retryable=True, raw=text[:2000])


def _http_timeouts(total_s: float) -> tuple[float, float, float]:
    settings_obj = get_settings()
    total = max(0.1, float(total_s))
    first_byte = min(total, float(_setting(settings_obj, "upstream_first_byte_timeout_s", 30.0)))
    read = min(total, float(_setting(settings_obj, "upstream_read_timeout_s", total)))
    return total, max(0.1, first_byte), max(0.1, read)


def _stream_timeouts(
    timeout_s: float, upstream_timeouts: UpstreamTimeouts | None = None
) -> tuple[float, float]:
    # (first_byte, read) budgets for a streaming request. ADR-075: streaming
    # has no total gate -- the stream lives until the upstream finishes, the
    # client disconnects, or the per-chunk read watchdog fires. timeout_s
    # only backfills the read setting for deployments that never set it.
    # upstream_timeouts（设置页覆盖）优先于 env 默认。
    if upstream_timeouts is not None:
        return (
            max(0.1, float(upstream_timeouts.first_byte_timeout_s)),
            max(0.1, float(upstream_timeouts.read_timeout_s)),
        )
    settings_obj = get_settings()
    first_byte = float(_setting(settings_obj, "upstream_first_byte_timeout_s", 30.0))
    read = float(_setting(settings_obj, "upstream_read_timeout_s", timeout_s))
    return max(0.1, first_byte), max(0.1, read)


def _litellm_common(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    prepared: Any,
    *,
    stream: bool,
    timeout: httpx.Timeout,
    usage_trace: str | None = None,
) -> dict[str, Any]:
    api_base = _litellm_api_base(channel)
    if api_base is None:
        raise LLMError("connection", "当前连接地址不是 OpenAI chat completions 端点", retryable=False)
    headers = prepared.headers if prepared is not None else build_headers(channel)
    api_key, extra_headers = _litellm_auth(headers)
    if usage_trace:
        extra_headers[_USAGE_TRACE_HEADER] = usage_trace
    model, messages, stream_flag, stream_options, extra_body = _request_body_for_litellm(
        channel, upstream_model, body, prepared, stream=stream
    )
    kwargs: dict[str, Any] = {
        "model": f"openai/{model}",
        "messages": messages,
        "api_base": api_base,
        "api_key": api_key,
        "stream": stream_flag,
        "timeout": timeout,
        "max_retries": 0,
        "extra_headers": extra_headers,
        "extra_body": extra_body,
    }
    if stream_flag and stream_options:
        kwargs["stream_options"] = stream_options
    return kwargs


# ---------------------------------------------------------------------------
# Non-streaming transport
# ---------------------------------------------------------------------------


async def complete(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    timeout_s: int,
    request_id: str,
    prepared: Any = None,
    upstream_timeouts: UpstreamTimeouts | None = None,
    upstream_content: bytes | None = None,
) -> tuple[dict[str, Any], Usage, int, dict[str, str]]:
    if _use_litellm(channel):
        # 快路径 bytes 只对 httpx 臂有意义；litellm 臂恒走 dict 构造
        #（fast_forward_content 对 litellm 渠道返回 None，双保险）。
        return await _litellm_complete(
            channel, upstream_model, body, timeout_s, request_id, prepared, upstream_timeouts
        )
    return await _httpx_complete(
        channel, upstream_model, body, timeout_s, request_id, prepared, upstream_timeouts,
        upstream_content,
    )


async def _litellm_complete(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    timeout_s: int,
    request_id: str,
    prepared: Any,
    upstream_timeouts: UpstreamTimeouts | None = None,
) -> tuple[dict[str, Any], Usage, int, dict[str, str]]:
    import litellm

    started = time.perf_counter()
    total_s, _first_byte, _read = _http_timeouts(float(timeout_s))
    kwargs = _litellm_common(
        channel,
        upstream_model,
        body,
        prepared,
        stream=False,
        timeout=_client_timeout(get_settings(), total_s, upstream_timeouts),
    )
    try:
        # Give httpx's own connect/read timeout a moment to surface first so a
        # refused connection stays distinguishable from a slow upstream.
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs), timeout=total_s + 0.5
        )
    except asyncio.TimeoutError as exc:
        raise LLMError("timeout", f"上游在 {int(total_s)}s 内未返回响应", retryable=True) from exc
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalised into LLMError below
        raise _map_litellm_error(exc) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    payload = _normalise_payload(_model_payload(response))
    headers = _response_headers(response)
    if not meaningful_content(payload):
        raise LLMError(
            "empty",
            "上游返回 200，但内容为空（伪成功）",
            200,
            True,
            json.dumps(payload, ensure_ascii=False)[:2000],
        )
    return payload, extract_usage(payload), latency_ms, headers


async def _httpx_complete(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    timeout_s: int,
    request_id: str,
    prepared: Any,
    upstream_timeouts: UpstreamTimeouts | None = None,
    upstream_content: bytes | None = None,
) -> tuple[dict[str, Any], Usage, int, dict[str, str]]:
    started = time.perf_counter()
    total_s, _first_byte, _read = _http_timeouts(float(timeout_s))
    url = build_chat_url(channel.connection_url)
    headers = prepared.headers if prepared is not None else build_headers(channel, request_id)
    rewritten = prepared.body if prepared is not None else body
    if upstream_content is not None:
        # P2 快路径：调用方已按 candidate 算好改写后 bytes（fast_forward_content），
        # 直接交给 httpx content=，跳过 dict→重建 dict→编码。headers/URL/超时/
        # 错误分类与 dict 路径完全一致；Content-Length 由 httpx 从 bytes 计算。
        content = upstream_content
    else:
        # 预编码一次（orjson，compact bytes）再交给 httpx：json= 会让 httpx 内部
        # 再走一遍 stdlib json.dumps，200KB 级请求体在膝点处占主循环 ~8% CPU
        #（py-spy 304 QPS 归因）；build_headers 已固定 content-type。
        content = jsonfast.dumps_compact(build_request_body(channel, upstream_model, rewritten))
    try:
        response = await _get_client().post(
            url,
            headers=headers,
            content=content,
            timeout=_client_timeout(get_settings(), total_s, upstream_timeouts),
        )
    except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
        raise LLMError("connection", "无法连接上游", retryable=True) from exc
    except httpx.TimeoutException as exc:
        raise LLMError("timeout", f"上游在 {int(total_s)}s 内未返回响应", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise LLMError("connection", "无法连接上游", retryable=True) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code >= 400:
        raise classify_http_error(response.status_code, response.text, response.headers)
    try:
        payload = response.json()
    except Exception as exc:
        raise LLMError("bad_response", "上游返回了非 JSON 响应", response.status_code, True, response.text[:2000]) from exc
    if not meaningful_content(payload):
        raise LLMError("empty", "上游返回 200，但内容为空（伪成功）", response.status_code, True, json.dumps(payload)[:2000])
    return payload, extract_usage(payload), latency_ms, dict(response.headers)


# ---------------------------------------------------------------------------
# Streaming transport
# ---------------------------------------------------------------------------

# 流式跟踪字节门的 needle 集合（P2）：_track_line 对一帧的全部副作用只读这些
# 字段，帧载荷不含任一 needle 时与「解析后无副作用」等价，可跳过解析
# （keep-alive / 纯 role 帧不再逐帧 json.loads）。集合来源核对（gateway.py）：
#   _track_line 本体读  id / error / usage / finish_reason；
#   meaningful_delta    读 content / reasoning_content / reasoning / tool_calls；
#   has_answer_surface  额外读 function_call——reasoning-only 帧同时带
#                       function_call 时仍要置 first_answer_at（TTFA）；
#   _track_delta        读 content / reasoning_content / reasoning。
# 没有第六种触发字段：delta.role 等其余字段无任何消费者。needle 带键名前缀
# 引号（"reasoning / "tool_calls / "function_call 不带收口引号，同时覆盖
# _content 变体），转义安全论证同 ADR-078：字符串内部引号必被转义，未转义
# 序列只可能是结构位。"id" 是条件 needle：response_id 采到后不再为 id 解析。
# 代价：上游若用 \uXXXX 转义拼写键名（正常编码器不会），该帧被跳过——与
# ADR-078 接受的 needle miss 同级；usage/finish/content 的漏解析只发生在
# 这种非常规上游，属有意接受的有界风险。
_TRACK_NEEDLES_BYTES: tuple[bytes, ...] = (
    b'"usage"',
    b'"error"',
    b'"finish_reason"',
    b'"content"',
    b'"reasoning',
    b'"tool_calls',
    b'"function_call',
)
_TRACK_NEEDLES_TEXT: tuple[str, ...] = (
    '"usage"',
    '"error"',
    '"finish_reason"',
    '"content"',
    '"reasoning',
    '"tool_calls',
    '"function_call',
)
_ID_NEEDLE_BYTES = b'"id"'
_ID_NEEDLE_TEXT = '"id"'


class UpstreamStream:
    """A primed SSE stream. `prime()` consumes until the first meaningful delta."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        response: httpx.Response,
        channel: orm.ChannelORM,
        upstream_model: str,
        public_name: str,
        request_id: str,
        *,
        read_timeout_s: float = 120.0,
    ) -> None:
        self.client = client
        self.response = response
        self.channel = channel
        self.upstream_model = upstream_model
        self.public_name = public_name
        self.request_id = request_id
        self.upstream_headers: dict[str, str] = dict(response.headers)
        self.prefetched: list[str] = []
        # The upstream response id is searchable in the admin log list.
        self.response_id = ""

        self.usage = Usage()
        self.finish_reason: str | None = None
        self.got_content = False
        self.saw_done = False
        self.started = time.perf_counter()
        self.first_content_at: float | None = None
        self.first_answer_at: float | None = None
        self.last_delta_at: float | None = None
        self.content_chars = 0
        self.content_preview = ""
        # Compact response summary for the request_bodies table.
        self.delta_count = 0
        self.first_preview = ""
        self.last_preview = ""
        self.reasoning_content = ""
        # Unbounded char counter for usage estimation; reasoning_content above
        # stays capped at 64K because it goes into the request_bodies snapshot.
        self.reasoning_chars = 0
        # Bounded relay-text buffer for tokenizer-based usage estimation
        # (ADR-075): beyond the cap the remainder is estimated from its char
        # count at the non-ASCII rate instead of being tokenized.
        self._relay_text_parts: list[str] = []
        self._relay_text_chars = 0
        self.error_events: list[dict[str, str]] = []
        self.sse_patch: dict[str, Any] = {}
        self.read_timeout_s = max(0.1, read_timeout_s)
        self.usage_from_upstream = False
        # Exactly one body iterator for the whole response: abandoning a
        # second iterator makes httpx treat the body as already streamed.
        # 响应支持 aiter_bytes 时一律从原始字节流读取；是否改写、以何种
        # 方式改写在迭代时决定（直通块模式 / 改写块模式，见 iter_sse），
        # 二者都以网络块为转发粒度。只有测试替身（仅有 aiter_lines）才
        # 退化为逐行路径。
        if hasattr(response, "aiter_bytes"):
            # 不带 chunk_size：httpx 的 ByteChunker 攒满 chunk_size 才吐块，
            # 流中途出错时压在 chunker 里的未满块会连同 error 安全前缀一起
            # 丢失。无参调用按网络块即时产出，块级批发由 _rewrite_blocks /
            # _passthrough_blocks 自己负责。
            self._byte_iter = self.response.aiter_bytes()
            self._line_iter = None
            self._raw_buf = b""
        else:
            self._byte_iter = None
            self._line_iter = self.response.aiter_lines()
        # 字节级改写序列（"model":"<upstream>" → "model":"<public>"）。
        # 带键名前缀，因此只会命中真正的 JSON 结构位（字符串内部出现的
        # 引号必被转义，无法构成未转义的 "model":" 序列）；上游若用带空格
        # 或转义形式回显 model，needle 不命中，自动回退到 rewrite_line 的
        # json 解析路径，语义不丢。
        self._rename_needle = b'"model":"' + self.upstream_model.encode("utf-8") + b'"'
        self._rename_replacement = b'"model":"' + self.public_name.encode("utf-8") + b'"'
        # Raw SSE bytes observed, for STREAM_ABORTED evidence messages.
        self.bytes_seen = 0

    def _abort_evidence(self) -> str:
        """Evidence for a mid-stream abort: elapsed / bytes / reasoning seen."""
        parts = [f"已流式 {time.perf_counter() - self.started:.1f}s"]
        if self.bytes_seen:
            parts.append(f"收到 {self.bytes_seen} 字节")
        parts.append("已收到思考块" if self.reasoning_content else "未收到思考块")
        return "（" + " / ".join(parts) + "）"

    async def _next_line(self) -> str | None:
        try:
            if self._byte_iter is not None:
                line = await asyncio.wait_for(
                    self._raw_next_line(), timeout=self.read_timeout_s
                )
            else:
                line = await asyncio.wait_for(
                    self._line_iter.__anext__(),
                    timeout=self.read_timeout_s,
                )
        except StopAsyncIteration:
            return None
        self.bytes_seen += len(line.encode("utf-8")) + 1
        return line

    async def _raw_next_line(self) -> str:
        """直通模式下从原始字节流取下一行 SSE（含跨块行缓冲）。"""
        while True:
            index = self._raw_buf.find(b"\n")
            if index >= 0:
                line = self._raw_buf[:index]
                self._raw_buf = self._raw_buf[index + 1 :]
                return line.decode("utf-8", "replace")
            try:
                chunk = await self._byte_iter.__anext__()
            except StopAsyncIteration:
                if not self._raw_buf:
                    raise
                line = self._raw_buf
                self._raw_buf = b""
                return line.decode("utf-8", "replace")
            self._raw_buf += chunk

    async def prime(self) -> None:
        consumed = 0
        try:
            while True:
                line = await self._next_line()
                if line is None:
                    break
                consumed += 1
                self.prefetched.append(line)
                if not line.startswith("data:"):
                    if consumed > 200:
                        break
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    self.saw_done = True
                    break
                try:
                    payload = json.loads(data)
                except Exception:
                    if consumed > 200:
                        break
                    continue
                if payload.get("error"):
                    raise LLMError(
                        "upstream",
                        _error_message(payload, "上游流式返回错误"),
                        retryable=True,
                        raw=data[:2000],
                    )
                self.response_id = str(payload.get("id") or self.response_id)
                usage = extract_usage(payload)
                if usage.total_tokens:
                    self.usage = usage
                    self.usage_from_upstream = True
                choices = payload.get("choices") or []
                if choices:
                    reason = choices[0].get("finish_reason")
                    if reason:
                        self.finish_reason = reason
                    if meaningful_delta(payload):
                        self.got_content = True
                        self.first_content_at = time.perf_counter()
                        if self.first_answer_at is None and has_answer_surface(payload):
                            self.first_answer_at = self.first_content_at
                        self._track_delta(payload)
                        break
                    if reason:
                        break
                if consumed > 200:
                    break
        except asyncio.TimeoutError as exc:
            raise LLMError(
                "timeout", "上游流式连接已建立，但没有及时返回首字节内容", retryable=True
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(
                "timeout", "上游流式连接已建立，但没有及时返回首字节内容", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError("connection", "读取上游流失败", retryable=True) from exc
        if not self.got_content and (self.finish_reason or self.saw_done):
            raise LLMError("empty", "上游流式响应结束，但没有产生任何内容（伪成功）", 200, True)
        if not self.got_content and not self.finish_reason and not self.saw_done:
            # Reaching this point means the stream ended (EOF): a stream that is
            # still open but silent is caught by the read-timeout handlers
            # above. Ending without a single usable data frame is therefore a
            # contract violation, not a timeout.
            content_type = str(self.response.headers.get("content-type", "")).lower()
            if "text/event-stream" not in content_type:
                # 200 + JSON/HTML instead of a stream: the upstream ignored
                # stream=true, so the client must not be told "upstream is slow".
                raise LLMError(
                    "bad_response",
                    "上游返回 200，但不是 SSE 流（content-type 非 text/event-stream"
                    f"，实际为 {content_type or '缺失'}），且没有任何 data 帧",
                    self.response.status_code,
                    True,
                    "\n".join(self.prefetched)[:2000],
                )
            raise LLMError(
                "empty", "上游 SSE 流在产生任何内容前就结束（伪成功）", 200, True
            )

    def rewrite_line(self, line: str, payload: Any = None) -> str:
        """Rewrite one SSE line for the client.

        The optional 'payload' argument carries the frame already parsed by
        iter_sse's tracking pass, so a data frame is never json.loads'd
        twice on the rewrite path.
        """
        if not line.startswith("data:"):
            return line
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return line
        if payload is None:
            try:
                payload = json.loads(data)
            except Exception:
                return line
        if isinstance(payload, dict):
            payload.update(self.sse_patch)
            payload["model"] = self.public_name
            return "data: " + json.dumps(payload, ensure_ascii=False)
        return line

    async def iter_sse(self):  # type: ignore[no-untyped-def]
        # 改写只在两种情况下才需要：调用方注入了 sse_patch，或上游回显的
        # model 名与对外名不同（直连名调用恒真）。改写与否只决定块内行处
        # 理方式，不决定转发粒度：真实 httpx 响应一律按网络块 aiter_bytes
        # 转发（直通 = 字节原样；改写 = 块内逐行跟踪 + 改写后重组）。
        # 跟踪解析本身每帧照跑：usage 兜底、finish_reason、断流判定、relay
        # 文本累计都要求读每一帧，盲转会破坏伪成功/断流/记账语义。
        # 只提供 aiter_lines 的测试替身退化为逐行路径。
        rewrite = bool(self.sse_patch) or self.public_name != self.upstream_model
        try:
            if self._byte_iter is not None:
                if rewrite:
                    async for block in self._rewrite_blocks():
                        yield block
                else:
                    async for block in self._passthrough_blocks():
                        yield block
            else:
                for line in self.prefetched:
                    yield self.rewrite_line(line) + "\n" if rewrite else line + "\n"
                self.prefetched.clear()
                while True:
                    line = await self._next_line()
                    if line is None:
                        break
                    payload = self._track_line(line) if line.startswith("data:") else None
                    if rewrite:
                        yield self.rewrite_line(line, payload) + "\n"
                    else:
                        yield line + "\n"
        except asyncio.TimeoutError as exc:
            if self.got_content:
                raise LLMError(
                    "stream_abort",
                    f"上游流在首字节后超时中断{self._abort_evidence()}",
                    retryable=False,
                ) from exc
            raise LLMError("timeout", "上游流读取超时", retryable=True) from exc
        except httpx.TimeoutException as exc:
            if self.got_content:
                raise LLMError(
                    "stream_abort",
                    f"上游流在首字节后超时中断{self._abort_evidence()}",
                    retryable=False,
                ) from exc
            raise LLMError("timeout", "上游流读取超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            if self.got_content:
                raise LLMError(
                    "stream_abort",
                    f"上游流在首字节后中断{self._abort_evidence()}",
                    retryable=False,
                ) from exc
            raise LLMError("connection", "无法读取上游流", retryable=True) from exc

        if not self.got_content:
            raise LLMError("empty", "上游流式响应结束，但没有产生任何内容（伪成功）", 200, True)
        if not self.saw_done and not self.finish_reason:
            raise LLMError(
                "stream_abort",
                f"上游流结束但缺少完成标记，判定为首字节后断流{self._abort_evidence()}",
                retryable=False,
            )

    def _frame_may_matter(self, data: bytes | str) -> bool:
        """字节门：帧载荷不含任何触发 needle 时返回 False（跳过解析）。

        needle 集合与来源见模块级 _TRACK_NEEDLES_* 的注释；"id" 仅在
        response_id 还没采到时才算命中——采到后不再为 id 解析。批量路径
        （bytes）用 bytes.find（memchr），逐行路径用 str.find。
        """
        if isinstance(data, bytes):
            for needle in _TRACK_NEEDLES_BYTES:
                if data.find(needle) >= 0:
                    return True
            return not self.response_id and data.find(_ID_NEEDLE_BYTES) >= 0
        for needle in _TRACK_NEEDLES_TEXT:
            if data.find(needle) >= 0:
                return True
        return not self.response_id and data.find(_ID_NEEDLE_TEXT) >= 0

    def _track_line(self, line: str | bytes) -> dict[str, Any] | None:
        """单行 SSE 跟踪：[DONE] / error / usage / finish_reason / 内容统计。

        error 帧抛 LLMError（逐行路径直接向外传播；直通路径先捕获、转发安全
        前缀后再抛，保证 error 帧本身不发往客户端）。返回该行已解析的 payload
        供逐行改写复用，避免同一帧被 json.loads 两次。
        接受 str（aiter_lines 逐行路径）或 bytes（字节块路径直进解析器，
        省掉每帧 decode）。
        解析前先过字节门（_frame_may_matter）：不含任何触发 needle 的帧直接
        返回 None，与今天「解析后无副作用」的帧等价；改写路径拿到 None 后由
        rewrite_line 自己兜底 json.loads，语义不丢。
        """
        if isinstance(line, bytes):
            if not line.startswith(b"data:"):
                return None
            data = line[5:].strip()
            if data == b"[DONE]":
                self.saw_done = True
                return None
        else:
            if not line.startswith("data:"):
                return None
            data = line[5:].strip()
            if data == "[DONE]":
                self.saw_done = True
                return None
        if not self._frame_may_matter(data):
            return None
        try:
            payload = jsonfast.loads(data)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        self.response_id = str(payload.get("id") or self.response_id)
        if payload.get("error"):
            raise LLMError(
                "stream_abort",
                _error_message(payload, "上游流式返回错误") + self._abort_evidence(),
                retryable=False,
                raw=data[:2000],
            )
        if payload.get("usage"):
            usage = extract_usage(payload)
            if usage.total_tokens:
                self.usage = usage
                self.usage_from_upstream = True
        choices = payload.get("choices") or []
        if choices:
            reason = choices[0].get("finish_reason")
            if reason:
                self.finish_reason = reason
            if meaningful_delta(payload):
                if self.first_content_at is None:
                    self.first_content_at = time.perf_counter()
                if self.first_answer_at is None and has_answer_surface(payload):
                    self.first_answer_at = time.perf_counter()
                self.got_content = True
                self._track_delta(payload)
        return payload

    def _track_raw_block(self, block: bytes) -> tuple[int, LLMError | None]:
        """直通模式：跟踪块内的完整行（按 \n 切分）。

        返回 (首个 error 帧的字节偏移, 错误)；无 error 帧时 (-1, None)。
        行以 bytes 直进 _track_line（解析器原生吃 bytes，省每帧 decode）。
        """
        start = 0
        while True:
            index = block.find(b"\n", start)
            if index < 0:
                return -1, None
            try:
                self._track_line(block[start:index])
            except LLMError as error:
                return start, error
            start = index + 1

    async def _passthrough_blocks(self):  # type: ignore[no-untyped-def]
        """直通模式：按网络块原样转发上游字节，转发前完成逐行跟踪。

        与逐行模式的对外差异只有两点：每个网络块一条 ASGI 消息（而不是
        data 行 + 空行两条），且字节完全 verbatim（\r\n 不被规范成 \n）。
        SSE 事件边界、[DONE]、合成错误事件的语义不变。
        """
        for line in self.prefetched:
            yield (line + "\n").encode("utf-8")
        self.prefetched.clear()
        buf = self._raw_buf
        self._raw_buf = b""
        while True:
            if b"\n" not in buf:
                try:
                    chunk = await asyncio.wait_for(
                        self._byte_iter.__anext__(), timeout=self.read_timeout_s
                    )
                except StopAsyncIteration:
                    if not buf:
                        return
                    error_offset, error = self._track_raw_block(buf + b"\n")
                    if error is not None:
                        yield buf[:error_offset]
                        raise error
                    yield buf
                    return
                buf += chunk
                self.bytes_seen += len(chunk)
                continue
            cut = buf.rfind(b"\n") + 1
            block = buf[:cut]
            buf = buf[cut:]
            error_offset, error = self._track_raw_block(block)
            if error is not None:
                yield block[:error_offset]
                raise error
            yield block

    async def _rewrite_blocks(self):  # type: ignore[no-untyped-def]
        """改写块模式：按网络块转发，块内逐行跟踪 + 改写。

        与旧逐行改写路径对外语义一致（每行仍跟踪并改写、error 帧不发客户端、
        行尾规范成 \n），差异只在转发粒度：每个网络块一条 ASGI 消息，而不是
        每行一条——逐帧 yield 的事件循环churn是直连名长流塌陷的主因。
        纯改名场景（sse_patch 为空）优先走字节级 needle 替换，命中即免去
        json.dumps；needle 不命中（带空格/转义的非常规回显）回退 rewrite_line。
        """
        for line in self.prefetched:
            # prefetched 行已在 prime() 内跟踪过，这里只改写不重复跟踪
            #（重复跟踪会把 delta 计数与 relay 文本翻倍，污染 usage 估算）。
            yield (self.rewrite_line(line) + "\n").encode("utf-8")
        self.prefetched.clear()
        buf = self._raw_buf
        self._raw_buf = b""
        while True:
            if b"\n" not in buf:
                try:
                    chunk = await asyncio.wait_for(
                        self._byte_iter.__anext__(), timeout=self.read_timeout_s
                    )
                except StopAsyncIteration:
                    if not buf:
                        return
                    out, error = self._rewrite_raw_block(buf + b"\n")
                    if error is not None:
                        yield out
                        raise error
                    yield out
                    return
                buf += chunk
                self.bytes_seen += len(chunk)
                continue
            cut = buf.rfind(b"\n") + 1
            block = buf[:cut]
            buf = buf[cut:]
            out, error = self._rewrite_raw_block(block)
            if error is not None:
                yield out
                raise error
            yield out

    def _rewrite_raw_block(self, block: bytes) -> tuple[bytes, LLMError | None]:
        """改写块内完整行（按 \n 切分），返回 (改写后字节, 首个 error)。

        error 帧与其后内容不出现在改写结果里（与逐行路径的抛出语义一致：
        客户端只收到 error 帧之前的安全前缀）。纯改名行在字节域完成替换，
        decode 只为跟踪服务；非 data 行原样透传（行尾 \r 规范成 \n，与
        aiter_lines 行为一致）。
        """
        pure_rename = not self.sse_patch and self.public_name != self.upstream_model
        out: list[bytes] = []
        start = 0
        while True:
            index = block.find(b"\n", start)
            if index < 0:
                break
            raw = block[start:index]
            start = index + 1
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            renamed = False
            if pure_rename and raw.find(self._rename_needle) >= 0:
                raw = raw.replace(self._rename_needle, self._rename_replacement)
                renamed = True
            line = raw.decode("utf-8", "replace")
            try:
                payload = self._track_line(line) if line.startswith("data:") else None
                if renamed:
                    out.append(raw + b"\n")
                else:
                    out.append((self.rewrite_line(line, payload) + "\n").encode("utf-8"))
            except LLMError as error:
                return b"".join(out), error
        return b"".join(out), None

    def _track_delta(self, payload: dict[str, Any]) -> None:
        choices = payload.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta") or {}
        self.last_delta_at = time.perf_counter()
        self.delta_count += 1
        content = delta.get("content")
        if isinstance(content, str):
            self.content_chars += len(content)
            self._stash_relay_text(content)
            if len(self.content_preview) < 4000:
                self.content_preview += content
            if len(self.first_preview) < 2000:
                self.first_preview = (self.first_preview + content)[:2000]
            self.last_preview = (self.last_preview + content)[-2000:]
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str):
            self.reasoning_chars += len(reasoning)
            self._stash_relay_text(reasoning)
            if len(self.reasoning_content) < 65536:
                self.reasoning_content += reasoning

    _RELAY_TEXT_CAP = 262_144

    def _stash_relay_text(self, text: str) -> None:
        # Accumulate relayed output (bounded) so the usage fallback can count
        # tokens with a real tokenizer instead of guessing from char counts.
        if self._relay_text_chars >= self._RELAY_TEXT_CAP:
            return
        room = self._RELAY_TEXT_CAP - self._relay_text_chars
        kept = text if len(text) <= room else text[:room]
        self._relay_text_parts.append(kept)
        self._relay_text_chars += len(kept)

    @property
    def relay_text(self) -> str:
        return "".join(self._relay_text_parts)

    @property
    def terminal(self) -> bool:
        """The upstream itself signalled completion (finish_reason or [DONE])."""
        return self.saw_done or self.finish_reason is not None

    @property
    def ttft_ms(self) -> int | None:
        if self.first_content_at is None:
            return None
        return max(0, int((self.first_content_at - self.started) * 1000))

    @property
    def ttfa_ms(self) -> int | None:
        if self.first_answer_at is None:
            return None
        return max(0, int((self.first_answer_at - self.started) * 1000))

    @property
    def ttlt_ms(self) -> int | None:
        if self.last_delta_at is None:
            return None
        return max(0, int((self.last_delta_at - self.started) * 1000))

    async def close(self) -> None:
        try:
            if self._line_iter is not None:
                await self._line_iter.aclose()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._byte_iter is not None:
                await self._byte_iter.aclose()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.response.aclose()
        except Exception:  # noqa: BLE001
            pass
        # 释放对响应/请求对象的引用：memray 归因显示 finalize 之后若仍持有
        # response（其上挂着 1.2MB 级的编码请求体），引用链会存活到进程结束
        # （160 条流 = 183MB 不归还）。迭代器已关闭，这里置空是纯回收。
        self.response = None
        self._byte_iter = None
        self._line_iter = None
        self._raw_buf = b""
        self.prefetched = []


class LiteLLMUpstreamStream:
    """The LiteLLM chunk iterator presented with the same contract as UpstreamStream."""

    def __init__(
        self,
        stream: Any,
        channel: orm.ChannelORM,
        upstream_model: str,
        public_name: str,
        request_id: str,
        upstream_headers: dict[str, str],
        *,
        read_timeout_s: float = 120.0,
        usage_trace: str | None = None,
        usage_observer: _UsageObserver | None = None,
    ) -> None:
        self.stream = stream
        self._usage_trace = usage_trace
        self._usage_observer = usage_observer
        self.channel = channel
        self.upstream_model = upstream_model
        self.public_name = public_name
        self.request_id = request_id
        self.upstream_headers = upstream_headers
        self.prefetched: list[dict[str, Any]] = []
        self.response_id = ""
        self._usage = Usage()
        self.finish_reason: str | None = None
        self.got_content = False
        self.saw_done = False
        self.started = time.perf_counter()
        self.first_content_at: float | None = None
        self.first_answer_at: float | None = None
        self.last_delta_at: float | None = None
        self.content_chars = 0
        self.content_preview = ""
        self.delta_count = 0
        self.first_preview = ""
        self.last_preview = ""
        self.reasoning_content = ""
        # Unbounded char counter for usage estimation; reasoning_content above
        # stays capped at 64K because it goes into the request_bodies snapshot.
        self.reasoning_chars = 0
        # Bounded relay-text buffer for tokenizer-based usage estimation
        # (ADR-075): beyond the cap the remainder is estimated from its char
        # count at the non-ASCII rate instead of being tokenized.
        self._relay_text_parts: list[str] = []
        self._relay_text_chars = 0
        self.error_events: list[dict[str, str]] = []
        self.sse_patch: dict[str, Any] = {}
        self.read_timeout_s = max(0.1, read_timeout_s)
        self._iterator = stream.__aiter__()
        self._ended = False
        # Raw chunk bytes observed, for STREAM_ABORTED evidence messages.
        self.bytes_seen = 0

    @property
    def usage(self) -> Usage:
        # When the client disconnects immediately after finish_reason, LiteLLM
        # may never yield its final usage chunk. The raw SSE tap has already
        # seen the provider's usage, so use that copy instead.
        if self._usage.total_tokens:
            return self._usage
        if self._usage_observer is not None and self._usage_observer.usage is not None:
            return self._usage_observer.usage
        return self._usage

    @usage.setter
    def usage(self, value: Usage) -> None:
        self._usage = value

    @property
    def usage_from_upstream(self) -> bool:
        return bool(self._usage_observer is not None and self._usage_observer.seen)

    _RELAY_TEXT_CAP = 262_144

    def _stash_relay_text(self, text: str) -> None:
        # Accumulate relayed output (bounded) so the usage fallback can count
        # tokens with a real tokenizer instead of guessing from char counts.
        if self._relay_text_chars >= self._RELAY_TEXT_CAP:
            return
        room = self._RELAY_TEXT_CAP - self._relay_text_chars
        kept = text if len(text) <= room else text[:room]
        self._relay_text_parts.append(kept)
        self._relay_text_chars += len(kept)

    @property
    def relay_text(self) -> str:
        return "".join(self._relay_text_parts)

    @property
    def terminal(self) -> bool:
        if self._usage_observer is not None:
            return self._usage_observer.finished
        return self.saw_done or self.finish_reason is not None

    def _abort_evidence(self) -> str:
        """Evidence for a mid-stream abort: elapsed / bytes / reasoning seen."""
        parts = [f"已流式 {time.perf_counter() - self.started:.1f}s"]
        if self.bytes_seen:
            parts.append(f"收到 {self.bytes_seen} 字节")
        parts.append("已收到思考块" if self.reasoning_content else "未收到思考块")
        return "（" + " / ".join(parts) + "）"

    async def _next_payload(self) -> dict[str, Any] | None:
        try:
            chunk = await asyncio.wait_for(
                self._iterator.__anext__(),
                timeout=self.read_timeout_s,
            )
        except StopAsyncIteration:
            self._ended = True
            return None
        except asyncio.TimeoutError as exc:
            if self.got_content:
                raise LLMError(
                    "stream_abort",
                    f"上游流在首字节后超时中断{self._abort_evidence()}",
                    retryable=False,
                ) from exc
            raise LLMError("timeout", "上游流读取超时", retryable=True) from exc
        except Exception as exc:  # noqa: BLE001 - normalised into LLMError below
            if self.got_content:
                raise LLMError(
                    "stream_abort",
                    f"上游流在首字节后中断{self._abort_evidence()}",
                    retryable=False,
                ) from exc
            raise _map_litellm_error(exc) from exc
        payload = _normalise_payload(_model_payload(chunk))
        self.bytes_seen += len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return payload

    def _is_synthetic_finish(self, payload: dict[str, Any]) -> bool:
        choices = payload.get("choices") or []
        if not choices or not choices[0].get("finish_reason"):
            return False
        return self._usage_observer is not None and not self._usage_observer.finished

    def _update(self, payload: dict[str, Any]) -> None:
        if payload.get("error"):
            raise LLMError(
                "upstream",
                _error_message(payload, "上游流式返回错误"),
                retryable=True,
                raw=json.dumps(payload, ensure_ascii=False)[:2000],
            )
        self.response_id = str(payload.get("id") or self.response_id)
        usage = extract_usage(payload)
        if usage.total_tokens:
            self.usage = usage
        choices = payload.get("choices") or []
        if not choices:
            return
        reason = choices[0].get("finish_reason")
        if reason:
            # LiteLLM does not relay the raw [DONE] sentinel. Only trust a
            # finish_reason that the raw transport actually saw: LiteLLM also
            # invents finish_reason="stop" when a connection is truncated.
            if self._usage_observer is None or self._usage_observer.finished:
                self.finish_reason = reason
                self.saw_done = True
        if meaningful_delta(payload):
            now = time.perf_counter()
            if not self.got_content:
                self.got_content = True
                self.first_content_at = now
            if self.first_answer_at is None and has_answer_surface(payload):
                self.first_answer_at = now
            self._track_delta(payload)

    async def prime(self) -> None:
        consumed = 0
        while True:
            payload = await self._next_payload()
            if payload is None:
                break
            consumed += 1
            self.prefetched.append(payload)
            self._update(payload)
            if self.got_content or self.finish_reason:
                break
            if consumed > 200:
                break
        if not self.got_content and (self.finish_reason or self.saw_done or self._ended):
            raise LLMError("empty", "上游流式响应结束，但没有产生任何内容（伪成功）", 200, True)
        if not self.got_content and not self.finish_reason and not self._ended:
            raise LLMError("timeout", "上游流式连接已建立，但迟迟没有首字节内容", retryable=True)

    def _serialize(self, payload: dict[str, Any]) -> str:
        if not self.sse_patch and payload.get("model") == self.public_name:
            # Nothing to patch and the model name is already the public one:
            # skip the defensive copy, the emitted frame is identical.
            return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        item = dict(payload)
        item.update(self.sse_patch)
        item["model"] = self.public_name
        return "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"

    async def iter_sse(self):  # type: ignore[no-untyped-def]
        for payload in self.prefetched:
            yield self._serialize(payload)
        self.prefetched.clear()
        while True:
            payload = await self._next_payload()
            if payload is None:
                break
            self._update(payload)
            if self._is_synthetic_finish(payload):
                continue
            yield self._serialize(payload)
        if not self.got_content:
            raise LLMError("empty", "上游流式响应结束，但没有产生任何内容（伪成功）", 200, True)
        if not self.terminal:
            raise LLMError(
                "stream_abort",
                f"上游流结束但缺少完成标记，判定为首字节后断流{self._abort_evidence()}",
                retryable=False,
            )
        # Preserve the OpenAI SSE sentinel that raw httpx streams already carry.
        yield "data: [DONE]\n\n"

    def _track_delta(self, payload: dict[str, Any]) -> None:
        choices = payload.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta") or {}
        self.last_delta_at = time.perf_counter()
        self.delta_count += 1
        content = delta.get("content")
        if isinstance(content, str):
            self.content_chars += len(content)
            self._stash_relay_text(content)
            if len(self.content_preview) < 4000:
                self.content_preview += content
            if len(self.first_preview) < 2000:
                self.first_preview = (self.first_preview + content)[:2000]
            self.last_preview = (self.last_preview + content)[-2000:]
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str):
            self.reasoning_chars += len(reasoning)
            self._stash_relay_text(reasoning)
            if len(self.reasoning_content) < 65536:
                self.reasoning_content += reasoning

    @property
    def ttft_ms(self) -> int | None:
        if self.first_content_at is None:
            return None
        return max(0, int((self.first_content_at - self.started) * 1000))

    @property
    def ttfa_ms(self) -> int | None:
        if self.first_answer_at is None:
            return None
        return max(0, int((self.first_answer_at - self.started) * 1000))

    @property
    def ttlt_ms(self) -> int | None:
        if self.last_delta_at is None:
            return None
        return max(0, int((self.last_delta_at - self.started) * 1000))

    async def close(self) -> None:
        if self._usage_trace:
            _usage_observers.pop(self._usage_trace, None)
        try:
            await self.stream.aclose()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._iterator.aclose()
        except Exception:  # noqa: BLE001
            pass


async def open_stream(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    public_name: str,
    timeout_s: int,
    request_id: str,
    prepared: Any = None,
    upstream_timeouts: UpstreamTimeouts | None = None,
    upstream_content: bytes | None = None,
):
    if _use_litellm(channel):
        # 快路径 bytes 只对 httpx 臂有意义；litellm 臂恒走 dict 构造。
        return await _litellm_open_stream(
            channel, upstream_model, body, public_name, timeout_s, request_id, prepared, upstream_timeouts
        )
    return await _httpx_open_stream(
        channel, upstream_model, body, public_name, timeout_s, request_id, prepared, upstream_timeouts,
        upstream_content,
    )


async def _litellm_open_stream(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    public_name: str,
    timeout_s: int,
    request_id: str,
    prepared: Any,
    upstream_timeouts: UpstreamTimeouts | None = None,
):
    import litellm

    first_byte_s, read_s = _stream_timeouts(float(timeout_s), upstream_timeouts)
    usage_trace = f"{request_id}:{time.perf_counter_ns()}"
    usage_observer = _UsageObserver()
    _usage_observers[usage_trace] = usage_observer
    kwargs = _litellm_common(
        channel,
        upstream_model,
        body,
        prepared,
        stream=True,
        timeout=_stream_client_timeout(get_settings(), read_s, upstream_timeouts),
        usage_trace=usage_trace,
    )
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs), timeout=first_byte_s + 0.5
        )
    except asyncio.TimeoutError as exc:
        _usage_observers.pop(usage_trace, None)
        raise LLMError(
            "timeout", f"upstream did not return first byte within {int(first_byte_s)}s", retryable=True
        ) from exc
    except asyncio.CancelledError:
        _usage_observers.pop(usage_trace, None)
        raise
    except Exception as exc:  # noqa: BLE001
        _usage_observers.pop(usage_trace, None)
        raise _map_litellm_error(exc) from exc

    stream = LiteLLMUpstreamStream(
        response,
        channel,
        upstream_model,
        public_name,
        request_id,
        _response_headers(response),
        read_timeout_s=read_s,
        usage_trace=usage_trace,
        usage_observer=usage_observer,
    )
    try:
        await asyncio.wait_for(stream.prime(), timeout=first_byte_s)
    except asyncio.TimeoutError as exc:
        await stream.close()
        raise LLMError(
            "timeout", f"upstream did not return first content within {int(first_byte_s)}s", retryable=True
        ) from exc
    except Exception:
        await stream.close()
        raise
    return stream


async def _httpx_open_stream(
    channel: orm.ChannelORM,
    upstream_model: str,
    body: dict[str, Any],
    public_name: str,
    timeout_s: int,
    request_id: str,
    prepared: Any,
    upstream_timeouts: UpstreamTimeouts | None = None,
    upstream_content: bytes | None = None,
):
    first_byte_s, read_s = _stream_timeouts(float(timeout_s), upstream_timeouts)
    url = build_chat_url(channel.connection_url)
    headers = prepared.headers if prepared is not None else build_headers(channel, request_id)
    rewritten = prepared.body if prepared is not None else body
    if upstream_content is not None:
        # P2 快路径：见 _httpx_complete 的同名注释。bytes 存活到上游调用完成
        # 后随引用链释放（httpx request 持有它直到流 close()，与今天的编码体
        # 同一生命周期）；流式首字节后调用方局部引用即已解除。
        content = upstream_content
    else:
        content = jsonfast.dumps_compact(
            build_request_body(channel, upstream_model, {**rewritten, "stream": True})
        )
    client = _get_client()
    response: httpx.Response | None = None
    try:
        request = client.build_request(
            "POST",
            url,
            headers=headers,
            # 预编码一次（orjson）：见 _httpx_complete 的同名注释；memray 归因
            # 里这份编码体还是重载流 RSS 的最大单件（1.2MB/流）。
            content=content,
            timeout=_stream_client_timeout(get_settings(), read_s, upstream_timeouts),
        )
        response = await client.send(request, stream=True)
    except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
        raise LLMError("connection", "无法连接上游", retryable=True) from exc
    except httpx.TimeoutException as exc:
        raise LLMError("timeout", f"上游在 {int(first_byte_s)}s 内未返回首字节", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise LLMError("connection", "无法连接上游", retryable=True) from exc

    if response.status_code >= 400:
        text = (await response.aread()).decode(errors="replace")
        await response.aclose()
        raise classify_http_error(response.status_code, text, response.headers)

    stream = UpstreamStream(
        client,
        response,
        channel,
        upstream_model,
        public_name,
        request_id,
        read_timeout_s=read_s,
    )
    try:
        await asyncio.wait_for(stream.prime(), timeout=first_byte_s)
    except asyncio.TimeoutError as exc:
        await stream.close()
        raise LLMError(
            "timeout", f"上游在 {int(first_byte_s)}s 内未返回首字节内容", retryable=True
        ) from exc
    except Exception:
        await stream.close()
        raise
    return stream

# ---------------------------------------------------------------------------
# Upstream probe seam
#
# ssot: 只有 llm/gateway.py 允许发上游 HTTP。渠道探测（模型列表 / 连通性 /
# chat 探测）统一走下面两个 helper；调用方因此不必导入 httpx，只需要捕获
# 这里定义的传输层异常。
# ---------------------------------------------------------------------------


class UpstreamError(Exception):
    """Base class for transport failures raised by the probe seam."""


class UpstreamTimeoutError(UpstreamError):
    """The upstream did not answer within the configured timeout."""


class UpstreamConnectError(UpstreamError):
    """The upstream is unreachable (refused, DNS or TLS failure, ...)."""


class UpstreamTransportError(UpstreamError):
    """Any other transport failure while talking to the upstream."""


def _probe_timeout(
    timeout_s: float, connect_timeout_s: float | None = None
) -> httpx.Timeout:
    """Build the timeout for a single probe request.

    ``connect_timeout_s=None`` mirrors ``httpx.Timeout(timeout_s)``; passing a
    value mirrors ``httpx.Timeout(timeout_s, connect=connect_timeout_s)``.
    """
    if connect_timeout_s is None:
        return httpx.Timeout(timeout_s)
    return httpx.Timeout(timeout_s, connect=connect_timeout_s)


async def _probe_request(
    method: str,
    url: str,
    *,
    timeout_s: float,
    connect_timeout_s: float | None,
    headers: dict[str, str] | None,
    params: dict[str, Any] | None,
    json_body: Any,
) -> httpx.Response:
    timeout = _probe_timeout(timeout_s, connect_timeout_s)
    try:
        client = _get_probe_client()
        if method == "POST":
            return await client.post(
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout,
            )
        return await client.get(url, headers=headers, params=params, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise UpstreamTimeoutError(str(exc) or type(exc).__name__) from exc
    except httpx.ConnectError as exc:
        raise UpstreamConnectError(str(exc) or type(exc).__name__) from exc
    except httpx.HTTPError as exc:
        raise UpstreamTransportError(str(exc) or type(exc).__name__) from exc


async def upstream_probe_get(
    url: str,
    *,
    timeout_s: float,
    connect_timeout_s: float | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET an upstream probe endpoint on behalf of the service layer."""
    return await _probe_request(
        "GET",
        url,
        timeout_s=timeout_s,
        connect_timeout_s=connect_timeout_s,
        headers=headers,
        params=params,
        json_body=None,
    )


async def upstream_probe_post(
    url: str,
    *,
    timeout_s: float,
    connect_timeout_s: float | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> httpx.Response:
    """POST an upstream probe body on behalf of the service layer."""
    return await _probe_request(
        "POST",
        url,
        timeout_s=timeout_s,
        connect_timeout_s=connect_timeout_s,
        headers=headers,
        params=params,
        json_body=json_body,
    )
