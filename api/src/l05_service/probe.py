from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.config import get_settings
from l01_core.errors import AppError, ErrorCode
from l01_core.ids import new_id
from l01_core.timeutil import now_ms
from l03_domain.model_types import (
    DEFAULT_MODEL_TYPE,
    MODEL_TYPE_AUDIO_TRANSCRIPTION,
    MODEL_TYPE_AUDIO_TTS,
    MODEL_TYPE_CHAT,
    MODEL_TYPE_EMBEDDING,
    MODEL_TYPE_IMAGE_GENERATION,
    MODEL_TYPE_RERANK,
    MODEL_TYPE_VIDEO,
    PROBED_MODEL_TYPES,
)
from l03_domain.rewrite import apply_body_rules, apply_header_rules
from l03_domain.urls import (
    RERANK_FLAVORS,
    build_anthropic_messages_url,
    build_chat_url,
    build_embeddings_urls,
    build_gemini_embed_url,
    build_gemini_generate_url,
    build_models_url,
    build_rerank_urls,
    build_type_endpoint_urls,
)
from l04_repo import orm, repo
from l05_service.channels import channel_api_key
from l05_service.llm import Usage, extract_usage
from l05_service.llm.gateway import (
    UpstreamError,
    UpstreamTimeoutError,
    upstream_probe_get,
    upstream_probe_post,
)
from l05_service.routing import Candidate, record_success

PROTOCOL_OPENAI = "openai_chat"
PROTOCOL_ANTHROPIC = "anthropic_messages"
PROTOCOL_GEMINI = "gemini"

PROTOCOL_ORDER: tuple[str, ...] = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC, PROTOCOL_GEMINI)
PROTOCOL_LABELS = {
    PROTOCOL_OPENAI: "OpenAI 兼容",
    PROTOCOL_ANTHROPIC: "Anthropic",
    PROTOCOL_GEMINI: "Gemini",
}

AUTH_CANDIDATES: dict[str, tuple[str, ...]] = {
    PROTOCOL_OPENAI: ("bearer", "api-key", "x-api-key"),
    PROTOCOL_ANTHROPIC: ("x-api-key",),
    PROTOCOL_GEMINI: ("query-key",),
}

PROBE_PROMPT = "ping"

# 非 chat 类型的探测负载：embedding 要一段输入，rerank 要 query + 文档。
EMBEDDING_INPUT = [PROBE_PROMPT]
RERANK_QUERY = PROBE_PROMPT
RERANK_DOCUMENTS = ["a", "b"]

# 有专属探测实现的类型。
TYPED_PROBE_TYPES: tuple[str, ...] = (MODEL_TYPE_EMBEDDING, MODEL_TYPE_RERANK)

# image/audio/video 只存类型，自动探活默认关闭；显式 force=true 才尽力
# 探一次（各家端点不统一，只要求 2xx 可达）。transcription 要上传音频文
# 件，没有 JSON 探测形态，因此不在此表里。
FORCED_TYPE_PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    MODEL_TYPE_IMAGE_GENERATION: (
        "images/generations",
        {"prompt": PROBE_PROMPT, "n": 1},
    ),
    MODEL_TYPE_AUDIO_TTS: (
        "audio/speech",
        {"input": PROBE_PROMPT, "voice": "alloy"},
    ),
    MODEL_TYPE_VIDEO: ("videos", {"prompt": PROBE_PROMPT}),
}


def probe_timeout_seconds() -> int:
    """Single source of truth for connection/model probe timeouts."""
    return max(1, int(get_settings().probe_timeout_s or 20))


@dataclass(slots=True)
class ModelsAttempt:
    ok: bool = False
    status: int | None = None
    message: str = ""
    timed_out: bool = False
    models: list[str] = field(default_factory=list)
    auth_style: str | None = None


@dataclass(slots=True)
class ChatAttempt:
    ok: bool = False
    status: int | None = None
    message: str = ""
    timed_out: bool = False
    text: str = ""
    auth_style: str | None = None
    protocol: str | None = None
    usage: Usage | None = None


@dataclass(slots=True)
class Detection:
    ok: bool
    protocol: str | None
    auth_style: str | None
    models: list[str]
    http_status: int | None
    message: str
    timed_out: bool


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------
def _channel_key(channel: orm.ChannelORM) -> str:
    return channel_api_key(channel)


def _upstream_error_detail(response: Any) -> str:
    """上游错误正文的摘要：排障时要能看到上游原话，不能只看到我们的结论。"""
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("msg")
    elif isinstance(error, str):
        message = error
    else:
        message = payload.get("message") or payload.get("detail")
    if not isinstance(message, str):
        return ""
    return " ".join(message.split())[:200]


def _auth_failure_message(status: int, has_key: bool, detail: str = "") -> str:
    """401/403 的措辞要区分「没带密钥」和「密钥被拒」两种原因。

    渠道还没有密钥时，上游拒绝的是匿名请求，与密钥是否有效无关；照旧写
    「请检查渠道密钥」会让人去排查一个并不存在的问题。上游原话附在括号里，
    管理员才能对照上游文档判断。
    """
    if has_key:
        text = f"上游返回 HTTP {status}：密钥被上游拒绝，请检查密钥是否有效"
    else:
        text = f"上游返回 HTTP {status}：本次请求未携带渠道密钥"
    return f"{text}（上游原话：{detail}）" if detail else text


def _stored_rewrite_rules(channel: orm.ChannelORM) -> dict[str, Any]:
    try:
        parsed = json.loads(channel.rewrite_rules_json or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _apply_probe_request_rules(
    channel: orm.ChannelORM,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    upstream_model: str = "",
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Apply the same request rewrite rules used by the real proxy.

    Probes and connection tests must see the upstream exactly like a chat
    request does, otherwise channels that depend on custom headers (for
    example ``X-Codegen-Client-Name``) can pass ``/models`` detection but
    fail model probes with 403.
    """
    rules = _stored_rewrite_rules(channel)
    variables = {
        "model": upstream_model,
        "upstream_model": upstream_model,
        "original_model": upstream_model,
    }
    request_headers = rules.get("request_headers")
    rewritten_headers = apply_header_rules(
        dict(headers),
        request_headers if isinstance(request_headers, list) else [],
        variables,
        body,
    )
    if body is None:
        return rewritten_headers, None
    request_body = rules.get("request_body")
    rewritten_body = apply_body_rules(
        body,
        request_body if isinstance(request_body, list) else [],
        variables,
    )
    # The gateway owns model/stream framing; keep probe behaviour identical.
    for key in ("model", "stream"):
        if key in body:
            rewritten_body[key] = body[key]
    return rewritten_headers, rewritten_body


def _auth_headers(auth_style: str, key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if not key:
        return headers
    if auth_style == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif auth_style == "api-key":
        headers["api-key"] = key
    elif auth_style == "x-api-key":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _auth_candidates(protocol: str, current: str | None) -> tuple[str, ...]:
    candidates = list(AUTH_CANDIDATES.get(protocol, AUTH_CANDIDATES[PROTOCOL_OPENAI]))
    if current and current in candidates:
        candidates.remove(current)
        candidates.insert(0, current)
    return tuple(candidates)


def _request_timeout(timeout_s: int) -> tuple[int, int]:
    """Keep one protocol attempt bounded; three protocols run back to back.

    Returns the (total, connect) budget handed to the gateway probe seam.
    """
    per_request = max(1, min(int(timeout_s), 10))
    return per_request, min(10, per_request)


def _max_tokens() -> int:
    return max(1, int(get_settings().probe_max_tokens or 64))


# ---------------------------------------------------------------------------
# Protocol detection via /models
# ---------------------------------------------------------------------------
def _openai_models(payload: Any) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    models: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        # Anthropic model objects carry type=model; OpenAI uses object=model.
        if item.get("type") == "model" and item.get("object") != "model":
            return None
        model_id = item.get("id")
        if not model_id:
            return None
        models.append(str(model_id))
    return models


def _anthropic_models(payload: Any) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        typed = [
            item
            for item in data
            if isinstance(item, dict) and item.get("type") == "model"
        ]
        if typed and len(typed) == len(data):
            return [str(item.get("id") or item.get("display_name") or "model") for item in typed]
        if not data and ("has_more" in payload or "first_id" in payload):
            return []
    if "has_more" in payload or "first_id" in payload:
        if isinstance(data, list):
            return [
                str(item.get("id") or item.get("display_name") or "model")
                for item in data
                if isinstance(item, dict)
            ]
    return None


def _gemini_models(payload: Any) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        return None
    names = [
        str(item["name"])
        for item in models
        if isinstance(item, dict) and item.get("name")
    ]
    return names or None


def _signature(protocol: str, payload: Any) -> list[str] | None:
    if protocol == PROTOCOL_ANTHROPIC:
        return _anthropic_models(payload)
    if protocol == PROTOCOL_GEMINI:
        return _gemini_models(payload)
    return _openai_models(payload)


async def _get_models(
    channel: orm.ChannelORM, protocol: str, timeout_s: int
) -> ModelsAttempt:
    url = build_models_url(channel.connection_url)
    key = _channel_key(channel)
    last_status: int | None = None
    last_message = "探测失败"

    for auth_style in _auth_candidates(protocol, channel.auth_style):
        params = {"key": key} if auth_style == "query-key" and key else None
        base_headers = {} if auth_style == "query-key" else _auth_headers(auth_style, key)
        headers, _ = _apply_probe_request_rules(channel, base_headers, None)
        total_s, connect_s = _request_timeout(timeout_s)
        try:
            response = await upstream_probe_get(
                url,
                timeout_s=total_s,
                connect_timeout_s=connect_s,
                headers=headers,
                params=params,
            )
        except UpstreamTimeoutError:
            return ModelsAttempt(
                status=last_status,
                timed_out=True,
                message="连接超时：上游在规定时间内没有响应",
            )
        except UpstreamError:
            return ModelsAttempt(
                status=last_status,
                message="无法连接上游：地址不可达或连接被拒绝",
            )

        status = response.status_code
        last_status = status
        if status in (401, 403):
            last_message = _auth_failure_message(
                status, bool(key), _upstream_error_detail(response)
            )
            continue
        if status >= 400:
            return ModelsAttempt(status=status, message=f"上游返回 HTTP {status}")

        try:
            payload = response.json()
        except Exception:
            return ModelsAttempt(
                status=status, message=f"HTTP {status} 返回的不是 JSON"
            )

        models = _signature(protocol, payload)
        if models is None:
            return ModelsAttempt(
                status=status,
                message=(
                    f"HTTP {status} 的响应不符合 "
                    f"{PROTOCOL_LABELS.get(protocol, protocol)} 的 /models 约定"
                ),
            )
        return ModelsAttempt(
            ok=True,
            status=status,
            models=models,
            auth_style=auth_style,
            message=f"/models 返回 {len(models)} 个模型",
        )

    return ModelsAttempt(status=last_status, message=last_message)


async def detect_protocol(channel: orm.ChannelORM, timeout_s: int) -> Detection:
    """Probe OpenAI -> Anthropic -> Gemini sequentially and remember what worked."""
    last_status: int | None = None
    last_message = "探测失败：上游没有返回可识别的模型列表"
    timeouts = 0

    for protocol in PROTOCOL_ORDER:
        attempt = await _get_models(channel, protocol, timeout_s)
        if attempt.ok:
            return Detection(
                ok=True,
                protocol=protocol,
                auth_style=attempt.auth_style,
                models=attempt.models,
                http_status=attempt.status,
                message=f"协议识别成功：{PROTOCOL_LABELS[protocol]}",
                timed_out=False,
            )
        if attempt.timed_out:
            timeouts += 1
        if attempt.status is not None:
            last_status = attempt.status
        if attempt.message:
            last_message = attempt.message

    if timeouts == len(PROTOCOL_ORDER):
        return Detection(
            ok=False,
            protocol=None,
            auth_style=None,
            models=[],
            http_status=last_status,
            message=f"连接超时：{timeout_s}s 内三种协议均没有响应",
            timed_out=True,
        )
    return Detection(
        ok=False,
        protocol=None,
        auth_style=None,
        models=[],
        http_status=last_status,
        message=f"探测失败：{last_message}",
        timed_out=False,
    )


async def discover_models(
    channel: orm.ChannelORM,
    timeout_s: int,
) -> dict[str, Any]:
    """Fetch the upstream model list without persisting anything.

    A draft channel can be passed in: the caller builds an unsaved
    ``ChannelORM`` from the current form values (including unsaved rewrite
    rules), so the discovery request is identical to the eventual save.
    """
    started = time.perf_counter()
    auto = channel.protocol_source == "auto" or channel.protocol == "unknown"
    protocols = PROTOCOL_ORDER if auto else (channel.protocol,)
    last_status: int | None = None
    last_message = "上游没有返回可识别的模型列表"
    timeouts = 0

    for protocol in protocols:
        attempt = await _get_models(channel, protocol, timeout_s)
        if attempt.ok:
            return {
                "status": "ok",
                "models": attempt.models,
                "protocol": protocol,
                "auth_style": attempt.auth_style or channel.auth_style,
                "http_status": attempt.status,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "message": (
                    f"{PROTOCOL_LABELS.get(protocol, protocol)} · "
                    f"{attempt.message}"
                ),
            }
        if attempt.timed_out:
            timeouts += 1
        if attempt.status is not None:
            last_status = attempt.status
        if attempt.message:
            last_message = attempt.message

    if timeouts == len(protocols):
        return {
            "status": "timeout",
            "models": [],
            "protocol": channel.protocol,
            "auth_style": channel.auth_style,
            "http_status": last_status,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "message": f"连接超时：{timeout_s}s 内没有响应",
        }
    message = f"获取模型失败：{last_message}"
    if last_status in (401, 403) and not _channel_key(channel) and "未携带渠道密钥" not in message:
        # 兜底：即使上游的 401 文案来自别的分支，也要说清这次请求根本没带密钥。
        message = f"{message}（本次请求未携带渠道密钥）"
    return {
        "status": "error",
        "models": [],
        "protocol": channel.protocol,
        "auth_style": channel.auth_style,
        "http_status": last_status,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "message": message,
    }


# ---------------------------------------------------------------------------
# Minimal chat fallback
# ---------------------------------------------------------------------------
def _chat_url(protocol: str, channel: orm.ChannelORM, upstream_model: str) -> str:
    if protocol == PROTOCOL_ANTHROPIC:
        return build_anthropic_messages_url(channel.connection_url)
    if protocol == PROTOCOL_GEMINI:
        return build_gemini_generate_url(channel.connection_url, upstream_model)
    return build_chat_url(channel.connection_url)


def _chat_body(
    protocol: str, upstream_model: str, max_tokens: int
) -> dict[str, Any]:
    if protocol == PROTOCOL_ANTHROPIC:
        return {
            "model": upstream_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
        }
    if protocol == PROTOCOL_GEMINI:
        return {
            "contents": [{"parts": [{"text": PROBE_PROMPT}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
    return {
        "model": upstream_model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }


@dataclass(slots=True)
class _ChatSignals:
    """One probe response judged by every output signal it can carry.

    A response counts as a real answer when *any* signal is present: the text
    part, the reasoning part (a thinking model spends the tiny probe budget on
    reasoning and leaves content empty), a tool call, or reported output tokens.
    Judging on text alone reported a healthy thinking model as empty output.
    """

    text: str = ""
    reasoning: str = ""
    tool_calls: int = 0

    def detail(self, usage: Usage | None) -> str:
        """The "why this probe succeeded" line, empty when nothing was found."""
        parts: list[str] = []
        if self.text:
            parts.append(f"正文 {len(self.text)} 字")
        if self.reasoning:
            parts.append(f"思考块 {len(self.reasoning)} 字")
        if self.tool_calls:
            parts.append(f"工具调用 {self.tool_calls} 个")
        if usage is not None and usage.completion_tokens > 0:
            parts.append(f"usage 输出 {usage.completion_tokens} tokens")
        return f"识别输出：{' · '.join(parts)}" if parts else ""


def _empty_output_message(status: int) -> str:
    """Failure wording that names every signal instead of only the text one."""
    return (
        f"上游返回 {status}，但没有任何可识别输出"
        "（正文、思考块、工具调用均空，usage 输出 0 tokens）"
    )


def _content_text(value: Any) -> str:
    """Flatten a content field that is either plain text or OpenAI text parts."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "")
            for item in value
            if isinstance(item, dict)
        )
    return ""


def _extract_chat_signals(protocol: str, payload: Any) -> _ChatSignals:
    """Collect the output signals of one probe response, per wire protocol.

    Both the non-streaming ``message`` shape and the streaming ``delta`` shape
    are read: the probe asks for ``stream: false``, but a gateway that ignores
    that flag (or a compat layer replaying a stream) still answers in deltas,
    and a reasoning-only delta is output just like a message would be.
    """
    signals = _ChatSignals()
    if not isinstance(payload, dict):
        return signals
    texts: list[str] = []
    reasoning: list[str] = []
    tools = 0
    if protocol == PROTOCOL_ANTHROPIC:
        blocks = payload.get("content")
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind in ("thinking", "redacted_thinking"):
                reasoning.append(str(block.get("thinking") or block.get("text") or ""))
            elif kind == "tool_use":
                tools += 1
    elif protocol == PROTOCOL_GEMINI:
        candidates = payload.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else None
        content = (first or {}).get("content") if isinstance(first, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            piece = str(part.get("text") or "")
            if part.get("thought") is True:
                reasoning.append(piece)
            else:
                texts.append(piece)
            if part.get("functionCall") is not None:
                tools += 1
    else:
        choices = payload.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        holders = ()
        if isinstance(first, dict):
            holders = (first.get("message"), first.get("delta"))
        for holder in holders:
            if not isinstance(holder, dict):
                continue
            texts.append(_content_text(holder.get("content")))
            reasoning.append(
                str(holder.get("reasoning_content") or holder.get("reasoning") or "")
            )
            calls = holder.get("tool_calls")
            if isinstance(calls, list):
                tools += len(calls)
            elif holder.get("function_call") is not None:
                tools += 1
    signals.text = "".join(texts).strip()
    signals.reasoning = "".join(reasoning).strip()
    signals.tool_calls = tools
    return signals


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_chat_usage(protocol: str, payload: Any) -> Usage | None:
    """Token usage reported by a probe response, keyed by wire protocol.

    Returns None when the upstream answered without a usage block. The value is
    only used to tell whether a deep probe produced a valid answer: probe log
    rows always book zero tokens, usage_estimated=true and cost_usd=0 (ssot
    §5.5), even though ADR-016 means the upstream may still have billed us for
    the call. In other words, "探测会真实计费" is about the upstream invoice,
    not about the tokens recorded in request_logs.
    """
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if protocol == PROTOCOL_ANTHROPIC:
        if not isinstance(usage, dict) or not usage:
            return None
        cached = _as_int(usage.get("cache_read_input_tokens"))
        prompt = (
            _as_int(usage.get("input_tokens"))
            + cached
            + _as_int(usage.get("cache_creation_input_tokens"))
        )
        completion = _as_int(usage.get("output_tokens"))
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            cached_tokens=cached,
        )
    if protocol == PROTOCOL_GEMINI:
        metadata = payload.get("usageMetadata")
        if not isinstance(metadata, dict) or not metadata:
            return None
        prompt = _as_int(metadata.get("promptTokenCount"))
        completion = _as_int(metadata.get("candidatesTokenCount"))
        cached = _as_int(metadata.get("cachedContentTokenCount"))
        total = _as_int(metadata.get("totalTokenCount")) or (prompt + completion)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cached_tokens=cached,
        )
    if not isinstance(usage, dict) or not usage:
        return None
    return extract_usage(payload)


async def _post_chat(
    channel: orm.ChannelORM,
    protocol: str,
    auth_style: str,
    upstream_model: str,
    timeout_s: int,
) -> ChatAttempt:
    key = _channel_key(channel)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    params = None
    if protocol == PROTOCOL_GEMINI:
        params = {"key": key} if key else None
    else:
        headers.update(_auth_headers(auth_style, key))
    url = _chat_url(protocol, channel, upstream_model)
    body = _chat_body(protocol, upstream_model, _max_tokens())
    headers, body = _apply_probe_request_rules(
        channel, headers, body, upstream_model
    )

    total_s, connect_s = _request_timeout(timeout_s)
    try:
        response = await upstream_probe_post(
            url,
            timeout_s=total_s,
            connect_timeout_s=connect_s,
            headers=headers,
            params=params,
            json_body=body,
        )
    except UpstreamTimeoutError:
        return ChatAttempt(
            timed_out=True, message="连接超时：上游在规定时间内没有返回"
        )
    except UpstreamError:
        return ChatAttempt(message="无法连接上游：地址不可达或连接被拒绝")

    if response.status_code >= 400:
        if response.status_code in (401, 403):
            message = _auth_failure_message(
                response.status_code, bool(key), _upstream_error_detail(response)
            )
        else:
            message = f"上游返回 HTTP {response.status_code}"
        return ChatAttempt(status=response.status_code, message=message)

    try:
        payload = response.json()
    except Exception:
        return ChatAttempt(
            status=response.status_code,
            message=f"HTTP {response.status_code} 返回的不是 JSON",
        )

    usage = _extract_chat_usage(protocol, payload)
    signals = _extract_chat_signals(protocol, payload)
    detail = signals.detail(usage)
    if not detail:
        return ChatAttempt(
            status=response.status_code,
            message=_empty_output_message(response.status_code),
            usage=usage,
        )
    text = signals.text or signals.reasoning
    message = f"返回「{signals.text[:40]}」· {detail}" if signals.text else detail
    return ChatAttempt(
        ok=True,
        status=response.status_code,
        text=text,
        auth_style=auth_style,
        message=message,
        usage=usage,
    )


async def _chat_fallback(
    channel: orm.ChannelORM, upstream_model: str, timeout_s: int
) -> ChatAttempt:
    last_status: int | None = None
    last_message = "三种协议都没有正常响应"
    last_usage: Usage | None = None
    any_timeout = False
    saw_non_timeout = False

    for protocol in PROTOCOL_ORDER:
        for auth_style in _auth_candidates(protocol, channel.auth_style):
            attempt = await _post_chat(
                channel, protocol, auth_style, upstream_model, timeout_s
            )
            if attempt.ok:
                attempt.message = (
                    f"{PROTOCOL_LABELS[protocol]} {attempt.message}"
                )
                attempt.auth_style = auth_style
                attempt.protocol = protocol
                return attempt
            if attempt.status is not None:
                last_status = attempt.status
            if attempt.usage is not None:
                last_usage = attempt.usage
            if attempt.timed_out:
                any_timeout = True
                break
            saw_non_timeout = True
            last_message = attempt.message
            if attempt.status in (401, 403):
                continue
            break

    return ChatAttempt(
        status=last_status,
        timed_out=any_timeout and not saw_non_timeout,
        message=last_message,
        usage=last_usage,
    )


# ---------------------------------------------------------------------------
# Model-type specific probes (embedding / rerank)
#
# 这两种模型没有 chat 端点，/models 探测只能证明“地址通”。所以直接打
# 它们自己的 endpoint：embedding 要求 data[0].embedding 非空，rerank 要求
# results 非空。统一返回 ChatAttempt，好复用 probe_model 的结果收尾。
#
# rerank 的方言各家不统一（/rerank 与 /ranking 都有人用），所以命中过的
# 方言要缓存进 parameters_json.rerank_flavor，下次直接用缓存，别每次试错。
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TypeTarget:
    """One candidate endpoint to probe a non-chat model."""

    url: str
    body: dict[str, Any]
    auth_style: str | None = None
    params: dict[str, str] | None = None
    flavor: str | None = None


def _model_parameters(model: orm.ChannelModelORM) -> dict[str, Any]:
    """Read the model's unified parameter blob, tolerating garbage."""
    try:
        parsed = json.loads(model.parameters_json or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cached_rerank_flavor(model: orm.ChannelModelORM) -> str | None:
    """The rerank dialect a previous probe found, if it is still valid."""
    flavor = _model_parameters(model).get("rerank_flavor")
    if isinstance(flavor, str) and flavor in RERANK_FLAVORS:
        return flavor
    return None


def _remember_rerank_flavor(model: orm.ChannelModelORM, flavor: str) -> None:
    """Cache the dialect that answered so later probes skip the guess."""
    parameters = _model_parameters(model)
    if parameters.get("rerank_flavor") == flavor:
        return
    parameters["rerank_flavor"] = flavor
    model.parameters_json = json.dumps(parameters, ensure_ascii=False)


def _rerank_flavor_of(url: str) -> str | None:
    """The rerank dialect an URL points at (`/rerank` vs `/ranking`)."""
    normalized = url.rstrip("/").lower()
    for flavor in RERANK_FLAVORS:
        if normalized.endswith("/" + flavor):
            return flavor
    return None


def _type_probe_targets(
    model: orm.ChannelModelORM,
    channel: orm.ChannelORM,
    model_type: str,
    key: str,
) -> list[TypeTarget]:
    """Candidate probes for one model, most likely first."""
    upstream_model = model.upstream_model
    targets: list[TypeTarget] = []
    if model_type == MODEL_TYPE_RERANK:
        cached = _cached_rerank_flavor(model)
        flavors = [cached] if cached else []
        flavors.extend(name for name in RERANK_FLAVORS if name != cached)
        body = {
            "model": upstream_model,
            "query": RERANK_QUERY,
            "documents": list(RERANK_DOCUMENTS),
        }
        for auth_style in _auth_candidates(PROTOCOL_OPENAI, channel.auth_style):
            seen: set[str] = set()
            for flavor in flavors:
                for url in build_rerank_urls(channel.connection_url, flavor):
                    if url in seen:
                        continue
                    seen.add(url)
                    targets.append(
                        TypeTarget(
                            url=url,
                            body=dict(body),
                            auth_style=auth_style,
                            # build_rerank_urls 会把所有方言都展开，所以方言必须按
                            # URL 判定，否则命中 /ranking 也会被记成 /rerank。
                            flavor=_rerank_flavor_of(url) or flavor,
                        )
                    )
        return targets

    # embedding：Gemini 原生走 :embedContent，OpenAI 兼容走 /embeddings。
    if channel.protocol == PROTOCOL_GEMINI:
        targets.append(
            TypeTarget(
                url=build_gemini_embed_url(channel.connection_url, upstream_model),
                body={"content": {"parts": [{"text": EMBEDDING_INPUT[0]}]}},
                params={"key": key} if key else None,
            )
        )
    body = {"model": upstream_model, "input": list(EMBEDDING_INPUT)}
    for auth_style in _auth_candidates(PROTOCOL_OPENAI, channel.auth_style):
        for url in build_embeddings_urls(channel.connection_url):
            targets.append(TypeTarget(url=url, body=dict(body), auth_style=auth_style))
    return targets


def _type_probe_summary(model_type: str, payload: Any) -> str:
    """Validate the success shape and return a short human summary."""
    if not isinstance(payload, dict):
        return ""
    if model_type == MODEL_TYPE_RERANK:
        results = payload.get("results")
        if isinstance(results, list) and results:
            return f"返回 {len(results)} 条排序结果"
        return ""
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        embedding = first.get("embedding") if isinstance(first, dict) else None
        if isinstance(embedding, list) and embedding:
            return f"返回 embedding 维度 {len(embedding)}"
    # Gemini 原生 embedContent 返回 {"embedding": {"values": [...]}}。
    native = payload.get("embedding")
    if isinstance(native, dict):
        values = native.get("values")
        if isinstance(values, list) and values:
            return f"返回 embedding 维度 {len(values)}"
    return ""


async def _post_type_probe(
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM,
    model_type: str,
    timeout_s: int,
) -> ChatAttempt:
    """Probe one embedding/rerank model, walking the candidate endpoints."""
    key = _channel_key(channel)
    targets = _type_probe_targets(model, channel, model_type, key)
    total_s, connect_s = _request_timeout(timeout_s)
    label = "embedding" if model_type == MODEL_TYPE_EMBEDDING else "rerank"
    status: int | None = None
    message = f"{label} 探测没有可用地址"
    for index, target in enumerate(targets):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if target.auth_style:
            headers.update(_auth_headers(target.auth_style, key))
        headers, body = _apply_probe_request_rules(
            channel, headers, dict(target.body), model.upstream_model
        )
        try:
            response = await upstream_probe_post(
                target.url,
                timeout_s=total_s,
                connect_timeout_s=connect_s,
                headers=headers,
                params=target.params,
                json_body=body,
            )
        except UpstreamTimeoutError:
            return ChatAttempt(
                timed_out=True, message="连接超时：上游在规定时间内没有返回"
            )
        except UpstreamError:
            return ChatAttempt(message="无法连接上游：地址不可达或连接被拒绝")
        status = response.status_code
        more = index + 1 < len(targets)
        if response.status_code >= 400:
            message = (
                _auth_failure_message(
                    response.status_code,
                    bool(key),
                    _upstream_error_detail(response),
                )
                if response.status_code in (401, 403)
                else f"上游返回 HTTP {response.status_code}"
            )
            # 端点不对（404/405）、方言不对（400/422）、密钥不对（401/403）
            # 都换下一个候选再试，别一次就把探测判死。
            if more and response.status_code in (400, 401, 403, 404, 405, 422):
                continue
            return ChatAttempt(status=status, message=message)
        try:
            payload = response.json()
        except Exception:
            return ChatAttempt(
                status=status,
                message=f"HTTP {status} 返回的不是 JSON",
            )
        summary = _type_probe_summary(model_type, payload)
        if not summary:
            message = f"上游返回 200，但 {label} 结果为空"
            if more:
                continue
            return ChatAttempt(status=status, message=message)
        if target.flavor:
            _remember_rerank_flavor(model, target.flavor)
        return ChatAttempt(
            ok=True,
            status=status,
            text=summary,
            auth_style=target.auth_style,
            message=summary,
        )
    return ChatAttempt(status=status, message=message)


async def _probe_type_model(
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM,
    model_type: str,
    timeout_s: int,
) -> ChatAttempt:
    """Probe a non-chat model against its own endpoint."""
    return await _post_type_probe(channel, model, model_type, timeout_s)


async def _post_forced_type_probe(
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM,
    model_type: str,
    timeout_s: int,
) -> ChatAttempt:
    """Best-effort probe for the types that have no standard probe."""
    forced = FORCED_TYPE_PROBES.get(model_type)
    if forced is None:
        return ChatAttempt(message=f"{model_type} 没有可自动化的探测端点")
    endpoint, extra = forced
    key = _channel_key(channel)
    total_s, connect_s = _request_timeout(timeout_s)
    body = {"model": model.upstream_model, **extra}
    message = f"{model_type} 探测没有可用地址"
    for auth_style in _auth_candidates(PROTOCOL_OPENAI, channel.auth_style):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(_auth_headers(auth_style, key))
        headers, body_out = _apply_probe_request_rules(
            channel, headers, dict(body), model.upstream_model
        )
        for url in build_type_endpoint_urls(channel.connection_url, endpoint):
            try:
                response = await upstream_probe_post(
                    url,
                    timeout_s=total_s,
                    connect_timeout_s=connect_s,
                    headers=headers,
                    params=None,
                    json_body=body_out,
                )
            except UpstreamTimeoutError:
                return ChatAttempt(
                    timed_out=True, message="连接超时：上游在规定时间内没有返回"
                )
            except UpstreamError:
                return ChatAttempt(message="无法连接上游：地址不可达或连接被拒绝")
            if response.status_code < 400:
                # 音频/图片端点返回二进制，只要可达就算探活成功。
                return ChatAttempt(
                    ok=True,
                    status=response.status_code,
                    message=f"HTTP {response.status_code}，端点可达",
                    auth_style=auth_style,
                )
            message = f"上游返回 HTTP {response.status_code}"
    return ChatAttempt(message=message)


PROBE_SOURCE = "probe"


def _probe_requested_model(channel: orm.ChannelORM, model: orm.ChannelModelORM | None) -> str:
    """Log probes under the same slug/call_model name the gateway logs.

    Connection tests have no model at all, so ``requested_model`` intentionally
    stays empty in storage; the API serializes it as ``null`` and the log UI
    renders its "—" fallback for that case.
    """
    if model is None:
        return ""
    return f"{channel.slug}/{model.call_model}"


def _probe_attempt(
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM | None,
    *,
    status: str,
    latency_ms: int,
    message: str,
) -> dict[str, Any]:
    ok = status == "ok"
    return {
        "channel_model_id": model.id if model else None,
        "channel_slug": channel.slug,
        "channel_name": channel.name,
        "upstream_model": model.upstream_model if model else "",
        "status": "success" if ok else "error",
        "error_code": None if ok else ("PROBE_TIMEOUT" if status == "timeout" else "PROBE_ERROR"),
        "message": "" if ok else message[:2000],
        "ms": latency_ms,
        "round": 1,
        "skipped": False,
        "priority_path": [],
    }


async def _log_probe(
    session: AsyncSession,
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM | None,
    *,
    status: str,
    latency_ms: int,
    message: str,
    http_status: int | None,
    usage: Usage | None = None,
) -> None:
    """Record a probe run in the usage log with source=probe.

    Per ssot §5.5 a probe row always carries zero tokens, usage_estimated=true
    and cost_usd=0, even when the upstream reports usage: ADR-016's "probes are
    really billed" means the upstream may charge us for the call, not that this
    log entry books tokens or money. The streaming timings stay null (non-stream
    shape), which also keeps probe rows out of the latency-percentile cohort.
    """
    # Reported usage is intentionally ignored; the argument is kept so callers
    # that already extracted usage do not need a separate code path.
    _ = usage
    ok = status == "ok"
    if http_status is None:
        http_status = 200 if ok else (504 if status == "timeout" else 502)
    error_code = None if ok else ("PROBE_TIMEOUT" if status == "timeout" else "PROBE_ERROR")
    try:
        await repo.add_request_log(
            session,
            request_id=f"probe-{new_id()}",
            requested_model=_probe_requested_model(channel, model),
            final_channel_model_id=model.id if model else None,
            channel_name=channel.name,
            upstream_model=model.upstream_model if model else "",
            is_stream=False,
            http_status=http_status,
            error_code=error_code,
            error_message="" if ok else message[:2000],
            total_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            cached_tokens=0,
            cache_ratio=0.0,
            cost_usd=0.0,
            usage_estimated=True,
            attempts_json=json.dumps(
                [
                    _probe_attempt(
                        channel, model, status=status, latency_ms=latency_ms, message=message
                    )
                ],
                ensure_ascii=False,
            ),
            source=PROBE_SOURCE,
        )
    except Exception:
        # Logging must never turn a probe result into a 500.
        await session.rollback()


# ---------------------------------------------------------------------------
# Model probe
# ---------------------------------------------------------------------------
async def _detect_then_chat(
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM,
    timeout_s: int,
    mode: str,
) -> tuple[bool, str, str, int | None, str, str, Usage | None]:
    """Probe via /models detection first, then (deep) a real chat call.

    The legacy flow: re-discovers protocol and auth style from the upstream
    every time, and falls back to a chat scan across protocols when /models
    is unavailable. Slow but self-healing; the fast path in probe_model only
    skips it while the previous probe of this model still stands.
    """
    detection = await detect_protocol(channel, timeout_s)
    found = detection.ok
    protocol = detection.protocol or channel.protocol
    auth_style = detection.auth_style or channel.auth_style
    http_status: int | None = detection.http_status
    status = "error"
    message = detection.message
    usage: Usage | None = None
    if detection.ok:
        if mode == "shallow":
            status = "ok"
            message = (
                f"探测成功：{PROTOCOL_LABELS[detection.protocol or protocol]} "
                f"/models 可用，返回 {len(detection.models)} 个模型"
            )
        else:
            chat = await _post_chat(
                channel, detection.protocol or protocol, auth_style, model.upstream_model, timeout_s
            )
            http_status = chat.status
            usage = chat.usage
            if chat.ok:
                status = "ok"
                message = (
                    f"探测成功：{PROTOCOL_LABELS[detection.protocol or protocol]} "
                    f"{chat.message}"
                )
            else:
                status = "timeout" if chat.timed_out else "error"
                message = f"探测失败：{chat.message}"
    elif mode == "deep":
        fallback = await _chat_fallback(channel, model.upstream_model, timeout_s)
        usage = fallback.usage
        if fallback.ok:
            found = True
            protocol = fallback.protocol or protocol
            auth_style = fallback.auth_style or auth_style
            http_status = fallback.status
            status = "ok"
            message = f"探测成功：{fallback.message}"
        else:
            status = "timeout" if fallback.timed_out else "error"
            message = f"探测失败：{fallback.message}"
    else:
        status = "timeout" if detection.timed_out else "error"
        message = detection.message
    return found, protocol, auth_style, http_status, status, message, usage


async def probe_model(
    session: AsyncSession,
    channel: orm.ChannelORM,
    model: orm.ChannelModelORM,
    mode: str = "deep",
    force: bool = False,
) -> dict[str, Any]:
    mode = "shallow" if mode == "shallow" else "deep"
    timeout_s = probe_timeout_seconds()
    started = time.perf_counter()

    # 老行升级上来的 model_type 可能是空串，统一按 chat 处理。
    model_type = model.model_type or DEFAULT_MODEL_TYPE
    if model_type not in PROBED_MODEL_TYPES and not force:
        # image/audio/video 只存类型：自动探活默认关闭，要探必须显式 force。
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"模型 {model.call_model} 的类型是 {model_type}，"
            "该类型默认不自动探测，如需探测请显式传 force=true",
        )
    found = False
    protocol = channel.protocol
    auth_style = channel.auth_style
    http_status: int | None = None
    status = "error"
    message = ""
    usage: Usage | None = None

    validated = (
        mode == "deep"
        and model.probe_status == "ok"
        and model.probed_at is not None
        and channel.protocol in PROTOCOL_ORDER
    )
    if model_type in TYPED_PROBE_TYPES:
        # embedding/rerank 不走 /models 协议探测：那只能证明地址通，证明不
        # 了这个模型可用。shallow 也做真实调用——这两类没有更便宜的等价探测。
        attempt = await _probe_type_model(channel, model, model_type, timeout_s)
        http_status = attempt.status
        if attempt.ok:
            found = True
            status = "ok"
            message = f"探测成功：{attempt.message}"
        else:
            status = "timeout" if attempt.timed_out else "error"
            message = f"探测失败：{attempt.message}"
    elif model_type not in PROBED_MODEL_TYPES:
        # 只有 force=true 才会走到这里（上面已按类型拦截）。image/audio/video
        # 没有统一探测端点，尽力打一次自家端点，能通就算通。
        attempt = await _post_forced_type_probe(channel, model, model_type, timeout_s)
        http_status = attempt.status
        if attempt.ok:
            found = True
            status = "ok"
            message = f"探测成功：{attempt.message}"
        else:
            status = "timeout" if attempt.timed_out else "error"
            message = f"探测失败：{attempt.message}"
    elif validated:
        # Fast path: the last probe of this model succeeded, so its stored
        # protocol/auth pair is known-good. Call the model directly instead
        # of re-scanning /models (saves a full DNS+TLS+RTT round trip per
        # re-probe). On failure the stored pair is stale, so fall back to
        # the full detection flow once to self-heal protocol/auth drift.
        chat = await _post_chat(
            channel, channel.protocol, channel.auth_style, model.upstream_model, timeout_s
        )
        if chat.ok:
            found = True
            protocol = channel.protocol
            auth_style = channel.auth_style
            http_status = chat.status
            status = "ok"
            message = f"探测成功：{PROTOCOL_LABELS[channel.protocol]} {chat.message}"
            usage = chat.usage
        else:
            (
                found,
                protocol,
                auth_style,
                http_status,
                status,
                message,
                usage,
            ) = await _detect_then_chat(channel, model, timeout_s, mode)
    else:
        (
            found,
            protocol,
            auth_style,
            http_status,
            status,
            message,
            usage,
        ) = await _detect_then_chat(channel, model, timeout_s, mode)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    probed_at = now_ms()
    model.probe_status = status
    model.probe_latency_ms = elapsed_ms
    model.probe_message = message[:400]
    model.probed_at = probed_at

    if found and model_type == MODEL_TYPE_CHAT:
        # 只有 chat 探测会重新判定渠道协议/鉴权。embedding/rerank/其它类型
        # 没有 chat 端点，用 chat body 判协议只会把渠道写歪，所以不动它。
        previous_protocol = channel.protocol
        channel.protocol = protocol
        channel.auth_style = auth_style
        # Keep a manual choice only when detection agrees with it.
        channel.protocol_source = (
            "manual"
            if channel.protocol_source == "manual" and previous_protocol == protocol
            else "auto"
        )
    await session.commit()

    if status == "ok" and mode == "deep":
        # A deep probe really called this model, so a success revives the
        # breaker through the same writer the proxied success path uses.
        # Shallow probes and connection tests only prove the endpoint answers.
        await record_success(session, Candidate(channel=channel, model=model))

    result = {
        "model_id": model.id,
        "channel_id": model.channel_id,
        "upstream_model": model.upstream_model,
        "call_model": model.call_model,
        "model_type": model_type,
        "status": status,
        "latency_ms": elapsed_ms,
        "message": message[:400],
        "probed_at": probed_at,
        "mode": mode,
        "protocol": channel.protocol,
        "auth_style": channel.auth_style,
        "http_status": http_status,
    }
    await _log_probe(
        session,
        channel,
        model,
        status=status,
        latency_ms=elapsed_ms,
        message=message,
        http_status=http_status,
        usage=usage,
    )
    return result


async def probe_all_models(
    session: AsyncSession,
    channel: orm.ChannelORM,
    mode: str = "deep",
    force: bool = False,
) -> dict[str, Any]:
    mode = "shallow" if mode == "shallow" else "deep"
    models = await repo.list_channel_models(session, channel_id=channel.id)
    # Probe the channel's default model first so its result shows up earliest.
    models.sort(key=lambda model: 0 if bool(model.probe_default) else 1)
    items: list[dict[str, Any]] = []
    skipped = 0
    for model in models:
        model_type = model.model_type or DEFAULT_MODEL_TYPE
        if model_type not in PROBED_MODEL_TYPES and not force:
            # image/audio/video 默认不探：记进 skipped，不混进 items。
            skipped += 1
            continue
        items.append(await probe_model(session, channel, model, mode, force=force))
    counts = {"ok": 0, "timeout": 0, "error": 0, "unknown": 0}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "channel_id": channel.id,
        "mode": mode,
        "total": len(models),
        "skipped": skipped,
        **counts,
        "items": items,
        "protocol": channel.protocol,
        "auth_style": channel.auth_style,
        "protocol_source": channel.protocol_source,
    }


# ---------------------------------------------------------------------------
# Channel connection test (address + key + /models only)
# ---------------------------------------------------------------------------
def _snippet(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return str(content or message.get("reasoning_content") or message.get("reasoning") or "").strip()


def _extract_models(payload: Any) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        models: list[str] = []
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("name")
                if model_id:
                    models.append(str(model_id))
        return models
    models_field = payload.get("models")
    if isinstance(models_field, list):
        models = []
        for item in models_field:
            if isinstance(item, dict):
                name = item.get("name") or item.get("id")
                if name:
                    models.append(str(name).removeprefix("models/"))
        return models
    return None


async def _record(
    session: AsyncSession,
    channel: orm.ChannelORM,
    *,
    status: str,
    latency_ms: int,
    message: str,
    http_status: int | None = None,
    model_count: int = 0,
    models: list[str] | None = None,
    auth_style: str | None = None,
    protocol: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    tested_at = now_ms()
    channel.connection_status = status
    channel.connection_latency_ms = latency_ms
    channel.connection_message = message[:400]
    channel.connection_tested_at = tested_at
    if status == "ok":
        if auth_style:
            channel.auth_style = auth_style
        if protocol and (channel.protocol_source == "auto" or channel.protocol == "unknown"):
            channel.protocol = protocol
    if persist:
        await session.commit()
    return {
        "status": status,
        "latency_ms": latency_ms,
        "http_status": http_status,
        "message": message,
        "model_count": model_count,
        "models_preview": (models or [])[:20],
        "protocol": channel.protocol,
        "auth_style": channel.auth_style,
        "tested_at": tested_at,
    }


async def test_connection(
    session: AsyncSession, channel: orm.ChannelORM, *, persist: bool = True
) -> dict[str, Any]:
    """GET the channel's /models endpoint with a bounded timeout and persist the verdict."""
    async def record(**kwargs: Any) -> dict[str, Any]:
        result = await _record(session, channel, persist=persist, **kwargs)
        if persist:
            # Draft (unsaved) channels are transient UX checks, so only real
            # channels produce usage-log rows.
            await _log_probe(
                session,
                channel,
                None,
                status=str(result.get("status") or "error"),
                latency_ms=int(result.get("latency_ms") or 0),
                message=str(result.get("message") or ""),
                http_status=result.get("http_status"),
            )
        return result

    timeout_s = probe_timeout_seconds()
    started = time.perf_counter()

    # A channel with protocol_source=auto has not been pinned by the operator.
    # Try the three wire protocols before falling back to the default /models
    # check, otherwise Anthropic/Gemini channels always look broken here.
    if channel.protocol_source == "auto" or channel.protocol == "unknown":
        detection = await detect_protocol(channel, timeout_s)
        if detection.ok:
            channel.protocol = detection.protocol or channel.protocol
            channel.auth_style = detection.auth_style or channel.auth_style
            channel.protocol_source = "auto"
            return await record(
                status="ok",
                latency_ms=int((time.perf_counter() - started) * 1000),
                http_status=detection.http_status,
                message=f"{detection.message}，发现 {len(detection.models)} 个模型",
                model_count=len(detection.models),
                models=detection.models,
                auth_style=detection.auth_style,
                protocol=detection.protocol,
            )
        return await record(
            status="timeout" if detection.timed_out else "error",
            latency_ms=int((time.perf_counter() - started) * 1000),
            http_status=detection.http_status,
            message=detection.message,
        )

    url = build_models_url(channel.connection_url)
    protocol = channel.protocol or PROTOCOL_OPENAI
    key = _channel_key(channel)

    last_status: int | None = None
    last_message = "探测失败"
    for auth_style in _auth_candidates(protocol, channel.auth_style):
        params = {"key": key} if auth_style == "query-key" and key else None
        base_headers = {} if auth_style == "query-key" else _auth_headers(auth_style, key)
        headers, _ = _apply_probe_request_rules(channel, base_headers, None)
        try:
            response = await upstream_probe_get(
                url,
                timeout_s=timeout_s,
                connect_timeout_s=min(10, timeout_s),
                headers=headers,
                params=params,
            )
        except UpstreamTimeoutError:
            return await record(
                status="timeout",
                latency_ms=int((time.perf_counter() - started) * 1000),
                message=f"连接超时：{timeout_s}s 内没有响应",
            )
        except UpstreamError:
            return await record(
                status="error",
                latency_ms=int((time.perf_counter() - started) * 1000),
                message="连接失败：无法建立到上游的连接",
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        last_status = response.status_code
        if response.status_code in (401, 403):
            last_message = _auth_failure_message(
                response.status_code, bool(key), _upstream_error_detail(response)
            )
            continue
        if response.status_code >= 400:
            hint = ""
            if response.status_code == 404:
                hint = "；该地址可能不支持 /models，请改用模型探测"
            return await record(
                status="error",
                latency_ms=latency_ms,
                http_status=response.status_code,
                message=f"上游返回 HTTP {response.status_code}{hint}",
            )

        try:
            payload = response.json()
        except Exception:
            return await record(
                status="error",
                latency_ms=latency_ms,
                http_status=response.status_code,
                message="连接成功，但 /models 返回的不是 JSON",
            )
        models = _extract_models(payload)
        if models is None:
            return await record(
                status="error",
                latency_ms=latency_ms,
                http_status=response.status_code,
                message="上游返回格式不符合 /models 约定",
            )
        return await record(
            status="ok",
            latency_ms=latency_ms,
            http_status=response.status_code,
            message=f"连接正常，发现 {len(models)} 个模型",
            model_count=len(models),
            models=models,
            auth_style=auth_style,
            protocol=protocol,
        )

    return await record(
        status="error",
        latency_ms=int((time.perf_counter() - started) * 1000),
        http_status=last_status,
        message=last_message,
    )
