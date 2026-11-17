from __future__ import annotations

import asyncio
import copy
import json
import weakref
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core import jsonfast
from l01_core.timeutil import now_ms

from l01_core.logging import get_logger, redact, redact_text
from l04_repo import repo
from l05_service.app_settings import BodyLogSettings, get_body_settings

logger = get_logger("system.body_logging")

# reasoning_content has its own quota and therefore must not consume the
# request/response body budget.
REASONING_MAX_BYTES = 16 * 1024
TRUNCATE_MARKER = "\n…[truncated]"


@dataclass(slots=True)
class BodySnapshot:
    """Raw values captured while forwarding; rendered/redacted only on write."""

    request_body: Any = None
    # upstream_body 可为 dict（dict 路径的规范化重建）或 bytes（P2 快路径下
    # 与 httpx 请求共享同一对象的改写后原始请求体，零拷贝）——render_value
    # 的 bytes 分支解码后走与 str 相同的 redact_text+truncate 路径。
    upstream_body: Any = None
    response_body: Any = None
    response_summary: Any = None
    reasoning_content: Any = None


def truncate_text(value: str, limit_bytes: int) -> str:
    """Cut UTF-8 text to at most ``limit_bytes`` without splitting a character."""
    if value is None:
        return value
    if limit_bytes <= 0:
        return ""
    data = value.encode("utf-8")
    if len(data) <= limit_bytes:
        return value
    marker = TRUNCATE_MARKER.encode("utf-8")
    if limit_bytes <= len(marker):
        return marker[:limit_bytes].decode("utf-8", "ignore")
    cut = data[: limit_bytes - len(marker)].decode("utf-8", "ignore")
    result = cut + TRUNCATE_MARKER
    while len(result.encode("utf-8")) > limit_bytes:
        result = result[:-1]
    return result


def _bound_text(value: str, limit_bytes: int) -> str:
    """按字符数预截断（先切后算，chars ≥ bytes 只会多切不会越界）。

    终点仍有 truncate_text 做字节级精截；这里的目的只是把 redact/dumps
    的工作量钉在预算附近，避免对 1.2MB 级正文全文跑正则与序列化。
    """
    if limit_bytes <= 0:
        return ""
    if len(value) <= limit_bytes:
        return value
    keep = max(0, limit_bytes - len(TRUNCATE_MARKER))
    return value[:keep] + TRUNCATE_MARKER


def _bound_deep(value: Any, limit_bytes: int) -> Any:
    """递归预截断结构里的每个超长字符串；非字符串原样返回。"""
    if isinstance(value, str):
        return _bound_text(value, limit_bytes)
    if isinstance(value, list):
        return [_bound_deep(item, limit_bytes) for item in value]
    if isinstance(value, dict):
        return {key: _bound_deep(item, limit_bytes) for key, item in value.items()}
    return value


def render_value(value: Any, limit_bytes: int) -> str | None:
    """Redact then JSON/text-serialize and truncate a captured value.

    结构与超长字符串先经 _bound_deep/_bound_text 预截断，redact 与序列化
    只在界后负载上执行；输出与「全文处理后再截断」相比只有超大正文的
    尾部内容不同（同样的截断标记、同样的字节上限）。
    """
    if value is None:
        return None
    if limit_bytes <= 0:
        return ""
    if isinstance(value, bytes):
        # P2 快路径：upstream_body 以改写后 bytes 快照持有（与 httpx 请求
        # 共享同一对象，不再为此解码出一份长寿命 str）；渲染时解码进 str
        # 路径，与 str 输入同一套 redact_text+truncate。非 utf-8 字节以
        # 替换字符呈现。
        return render_value(value.decode("utf-8", "replace"), limit_bytes)
    if isinstance(value, str):
        text = redact_text(_bound_text(value, limit_bytes))
    else:
        # orjson 渲染：body 行进 WAL/落库前的最后一步，膝点归因里 stdlib
        # iterencode 占主循环 ~2.5%，换 compact bytes + decode 甩掉它。
        text = jsonfast.dumps_compact(redact(_bound_deep(value, limit_bytes))).decode("utf-8")
    return truncate_text(text, limit_bytes)


def build_body_record(snapshot: BodySnapshot, settings: BodyLogSettings) -> dict[str, str | None]:
    return {
        "request_body": render_value(snapshot.request_body, settings.truncate_bytes),
        "upstream_body": render_value(snapshot.upstream_body, settings.truncate_bytes),
        "response_body": render_value(snapshot.response_body, settings.truncate_bytes),
        "response_summary": render_value(snapshot.response_summary, settings.summary_max_bytes),
        "reasoning_content": render_value(snapshot.reasoning_content, REASONING_MAX_BYTES)
        or None,
    }


def should_capture(settings: BodyLogSettings, *, http_status: int, error_code: str | None) -> bool:
    if settings.mode == "off":
        return False
    if settings.mode == "all":
        return True
    # errors: 4xx/5xx, pseudo-success, stream break, timeout, connection failure
    # all carry a non-2xx mapped status or an error code.
    return int(http_status or 0) >= 400 or bool(error_code)


# The policy lives in the database so it can change without a restart. Reading
# it once per request costs an extra session + SELECT even when body logging is
# off, which is the default and the steady-state hot path. A short TTL keeps
# admin changes visible within seconds while removing that query.
BODY_SETTINGS_CACHE_TTL_MS = 5_000
_body_settings_cache: "weakref.WeakKeyDictionary[Any, tuple[int, BodyLogSettings]]" = (
    weakref.WeakKeyDictionary()
)
# When a catalog is bound, the body-logging policy is part of its snapshot and
# is refreshed by the version poll. The TTL cache above remains the fallback for
# callers (tests, scripts) that never bind a catalog.
_body_catalogs: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()


def bind_body_catalog(session_factory: Any, catalog: Any) -> None:
    _body_catalogs[session_factory] = catalog


def invalidate_body_settings_cache() -> None:
    _body_settings_cache.clear()


async def cached_body_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> BodyLogSettings:
    catalog = _body_catalogs.get(session_factory)
    if catalog is not None:
        await catalog.ensure_fresh()
        if catalog.snapshot is not None:
            return catalog.snapshot.body_settings
    now = now_ms()
    cached = _body_settings_cache.get(session_factory)
    if cached is not None and now - cached[0] < BODY_SETTINGS_CACHE_TTL_MS:
        return cached[1]
    async with session_factory() as session:
        policy = await get_body_settings(session)
    _body_settings_cache[session_factory] = (now, policy)
    return policy


async def write_body_log(
    session_factory: async_sessionmaker[AsyncSession],
    request_id: str,
    snapshot: BodySnapshot | None,
    *,
    http_status: int,
    error_code: str | None = None,
    settings: BodyLogSettings | None = None,
) -> bool:
    """Persist one body row. A failure is a warning only, never a request error."""
    if snapshot is None:
        return False
    try:
        policy = settings or await cached_body_settings(session_factory)
        if not should_capture(policy, http_status=http_status, error_code=error_code):
            return False
        async with session_factory() as session:
            record = build_body_record(snapshot, policy)
            if not any(record.values()):
                return False
            await repo.save_request_body(session, request_id=request_id, **record)
            return True
    except Exception as error:  # noqa: BLE001
        # Logging is best-effort; the client response has already been produced.
        logger.warning(
            "request body log write failed (request_id=%s): %s", request_id, error
        )
        return False


_pending_writes: set[asyncio.Task[bool]] = set()


def schedule_body_write(
    session_factory: async_sessionmaker[AsyncSession] | None,
    request_id: str,
    snapshot: BodySnapshot | None,
    *,
    http_status: int,
    error_code: str | None = None,
) -> asyncio.Task[bool] | None:
    """Schedule an async write without awaiting it in the forwarding path."""
    if session_factory is None or snapshot is None:
        return None
    try:
        task = asyncio.get_running_loop().create_task(
            write_body_log(
                session_factory,
                request_id,
                snapshot,
                http_status=http_status,
                error_code=error_code,
            )
        )
    except RuntimeError:
        return None
    _pending_writes.add(task)
    task.add_done_callback(_pending_writes.discard)
    return task


async def drain_body_writes(timeout: float = 2.0) -> None:
    """Let in-flight body writes finish before the engine is disposed."""
    pending = [task for task in _pending_writes if not task.done()]
    if not pending:
        return
    await asyncio.wait(pending, timeout=timeout)


def extract_reasoning_content(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    value = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(value, str):
        return value
    if value:
        return json.dumps(value, ensure_ascii=False)
    return None


def strip_reasoning_content(payload: Any) -> Any:
    """Remove reasoning from the response body so it is not counted twice."""
    if not isinstance(payload, dict):
        return payload
    copied = copy.deepcopy(payload)
    copied.pop("reasoning_content", None)
    copied.pop("reasoning", None)
    for choice in copied.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            message.pop("reasoning_content", None)
            message.pop("reasoning", None)
        delta = choice.get("delta")
        if isinstance(delta, dict):
            delta.pop("reasoning_content", None)
            delta.pop("reasoning", None)
    return copied


def stream_summary_payload(
    stream: Any,
    *,
    error_kind: str | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    events = list(getattr(stream, "error_events", []) or [])
    if error_kind:
        event = {"kind": error_kind, "message": (error_message or "")[:2000]}
        if event not in events:
            events.append(event)
    return {
        "delta_count": int(getattr(stream, "delta_count", 0) or 0),
        "first_preview": str(getattr(stream, "first_preview", "") or ""),
        "last_preview": str(getattr(stream, "last_preview", "") or ""),
        "finish_reason": getattr(stream, "finish_reason", None),
        "saw_done": bool(getattr(stream, "saw_done", False)),
        "error_events": events,
    }


def empty_stream_summary(error_kind: str | None = None, error_message: str = "") -> dict[str, Any]:
    return {
        "delta_count": 0,
        "first_preview": "",
        "last_preview": "",
        "finish_reason": None,
        "saw_done": False,
        "error_events": (
            [{"kind": error_kind, "message": (error_message or "")[:2000]}]
            if error_kind
            else []
        ),
    }
