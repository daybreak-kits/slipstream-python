from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from l01_core.errors import AppError, ErrorCode
from l01_core.jsonfast import dumps_compact
from l05_service import catalog
from l05_service import chat as chat_service
from l05_service.inflight import get_inflight_gate
from l06_api.deps import UserKeyContext, get_db, get_user_key

router = APIRouter(prefix="/v1", tags=["v1"])


def _overlay_headers(base: dict[str, str], overlay: dict[str, str]) -> dict[str, str]:
    """Overlay headers case-insensitively so the gateway's copy always wins."""
    owned = {name.lower() for name in overlay}
    merged = {name: value for name, value in base.items() if name.lower() not in owned}
    merged.update(overlay)
    return merged


async def _inflight_slot() -> AsyncIterator[None]:
    """在飞闸门依赖：先于鉴权与请求体解析占槽（429 快速失败）。

    acquire 超限抛 429 发生在 yield 之前：FastAPI 不会注册退出回调，没占到
    的槽位自然无需释放。占槽成功后，无论 handler 正常返回、流式消费完毕
    还是客户端断连取消，依赖栈退出时都恰好归还一次（流式退出发生在响应体
    发送完毕之后，长连接持槽语义与旧实现一致）。
    """
    gate = get_inflight_gate()
    gate.acquire()
    try:
        yield
    finally:
        gate.release()


def _body_too_large(limit: int) -> AppError:
    return AppError(
        ErrorCode.INVALID_REQUEST,
        f"请求体超过大小上限 {limit} 字节",
        status=413,
        details={"max_body_bytes": limit},
    )


def _invalid_body_error(errors: list[dict[str, Any]], body: bytes) -> RequestValidationError:
    """按 Body(...) 时代的 422 信封构造校验错误（errors.py 统一渲染）。

    error dict 的形状与 FastAPI 解析 Body 时的构造一致，信封字节级不变。
    """
    return RequestValidationError(errors, body=body)


async def _read_json_body(request: Request) -> tuple[dict[str, Any], bytes]:
    """显式读请求体并解析 JSON；Content-Length 超限在读之前短路 413。

    取代 Body(...) 的隐式全量解析：Starlette 在参数校验阶段就读完请求体，
    超大请求不受 max_body_bytes 约束且解析完才被拒。Content-Length 是客户
    端自报的（chunked 等场景缺失），缺失时读完按实际长度兜底。

    返回 (解析后的 dict, 原始 bytes)：P2 字节快路径把原始 bytes 作为上游
    请求体直接转发（仅字节级 model 替换，fast_forward_content 判定资格），
    其存活期由调用链管理——上游调用完成后随局部引用释放（流式在首字节后
    即解除）；日志快照照旧持有解析后的 dict。Starlette 的缓存副本照旧在
    解析完成后置空（P1.5 的内存修复，语义不变）。
    """
    limit = int(request.app.state.settings.max_body_bytes)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_length = int(declared)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > limit:
            raise _body_too_large(limit)
    body = await request.body()
    if len(body) > limit:
        raise _body_too_large(limit)
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        # JSONDecodeError: 与 FastAPI 的 json_invalid 错误同构（loc 精确到位置）。
        raise _invalid_body_error(
            [
                {
                    "type": "json_invalid",
                    "loc": ("body", exc.pos),
                    "msg": "JSON decode error",
                    "input": {},
                    "ctx": {"error": exc.msg},
                }
            ],
            body,
        ) from exc
    if not isinstance(parsed, dict):
        # Body(dict) 时代的 pydantic dict_type 校验错误，信封形状不变。
        raise _invalid_body_error(
            [
                {
                    "type": "dict_type",
                    "loc": ("body",),
                    "msg": "Input should be a valid dictionary",
                    "input": parsed,
                }
            ],
            body,
        )
    # 解析完成后释放 Starlette 的缓存副本：request._body 置空后，原始 bytes
    # 只被本函数的局部引用持有，交给快路径按需使用（v1.5 修复的语义保留）。
    request._body = b""
    return parsed, body


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    # 依赖按声明顺序求解：在飞闸门先于鉴权（get_user_key）与请求体解析占槽。
    _inflight: None = Depends(_inflight_slot),
    context: UserKeyContext = Depends(get_user_key),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """OpenAI-compatible chat: JSON by default, SSE when ``stream=true``."""
    request_id = str(getattr(request.state, "request_id", ""))
    # 来源地址与原始请求头一起进入请求日志（转发头由 request_meta 解析）。
    client_ip = request.client.host if request.client else ""
    request_headers = dict(request.headers)
    body, raw_body = await _read_json_body(request)
    if body.get("stream"):
        registry = getattr(request.app.state, "stream_registry", None)
        # Everything until StreamingResponse owns the stream sits inside one
        # guard: any failure here -- including client-disconnect cancellation,
        # which arrives as a BaseException -- must give back the stream slot
        # exactly once. The in-flight slot itself stays with the dependency
        # stack, which releases it after the response body has been sent.
        acquired_registry = False
        try:
            if registry is not None:
                await registry.acquire()
                acquired_registry = True
            run = await chat_service.start_direct_stream(
                session,
                api_key=context.api_key,
                user=context.user,
                body=body,
                request_id=request_id,
                session_factory=request.app.state.session_factory,
                client_ip=client_ip,
                request_headers=request_headers,
                raw_body=raw_body,
            )
            # The SSE body can outlive this request scope. Release the pooled DB
            # connection now instead of holding it for the whole stream.
            await session.close()
            if registry is not None:
                run.registry = registry
            # Gateway-owned headers are applied after the upstream copy so they
            # always win: SSE framing, buffering and our request id are promises
            # the gateway makes to the client.
            gateway_headers = {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Content-Type": "text/event-stream",
                "x-request-id": request_id,
            }
            headers = _overlay_headers(
                dict(run.response.headers) if run.response is not None else {},
                gateway_headers,
            )

            async def _events() -> Any:
                # 纯 SSE 中继；在飞槽位由 _inflight_slot 依赖栈在流结束后归还。
                async for chunk in run.events():
                    yield chunk

            return StreamingResponse(
                _events(),
                media_type="text/event-stream",
                headers=headers,
                background=BackgroundTask(run.finalize),
            )
        except BaseException:
            # 流式响应尚未接管：并发流槽位仍需归还（在飞槽位归依赖栈管）。
            if acquired_registry and registry is not None:
                await registry.release()
            raise
    result = await chat_service.complete_direct(
        session,
        api_key=context.api_key,
        user=context.user,
        body=body,
        request_id=request_id,
        session_factory=request.app.state.session_factory,
        client_ip=client_ip,
        request_headers=request_headers,
        raw_body=raw_body,
    )
    # Upstream response headers are relayed as-is (framing already stripped by
    # prepare_response); the gateway owns its request id.
    headers = _overlay_headers(dict(result.headers), {"x-request-id": request_id})
    # orjson renders the compact body (same bytes as JSONResponse for
    # JSON-compatible payloads); Response keeps JSONResponse's header
    # behaviour: content-type is only added when the upstream sent none.
    return Response(
        content=dumps_compact(result.payload),
        headers=headers,
        media_type="application/json",
    )


@router.get("/models")
async def list_models(
    _context: UserKeyContext = Depends(get_user_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await catalog.list_model_entries(session)
    return {
        "object": "list",
        "data": [
            {
                "id": item["id"],
                "object": "model",
                "created": int(item["created_at"]) // 1000,
                "created_at": int(item["created_at"]),
                "owned_by": item["owned_by"],
                "parameters": item["parameters"],
                # Kept for OpenAI-compatible clients that still read the
                # legacy fields.
                "context_window": item["parameters"]["max_input_tokens"],
                "capabilities": item["parameters"]["input_types"],
                "kind": item["kind"],
                "origin": item.get("origin"),
            }
            for item in items
        ],
    }
