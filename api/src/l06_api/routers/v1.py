from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

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


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: dict[str, Any] = Body(...),
    context: UserKeyContext = Depends(get_user_key),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """OpenAI-compatible chat: JSON by default, SSE when ``stream=true``."""
    request_id = str(getattr(request.state, "request_id", ""))
    # 来源地址与原始请求头一起进入请求日志（转发头由 request_meta 解析）。
    client_ip = request.client.host if request.client else ""
    request_headers = dict(request.headers)
    # 节点级在飞上限（429 快速失败）：流式与非流式共用一个进程内计数器。
    gate = get_inflight_gate()
    gate.acquire()
    if body.get("stream"):
        registry = getattr(request.app.state, "stream_registry", None)
        # Everything until StreamingResponse owns the stream sits inside one
        # guard: any failure here -- including client-disconnect cancellation,
        # which arrives as a BaseException -- must give back the in-flight
        # slot (and the stream slot) exactly once. After the response is
        # returned, _gated_events owns the in-flight release.
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

            async def _gated_events() -> Any:
                # 在飞槽位跟随 SSE 迭代器：读完 [DONE] 或客户端断连取消时归还。
                try:
                    async for chunk in run.events():
                        yield chunk
                finally:
                    gate.release()

            return StreamingResponse(
                _gated_events(),
                media_type="text/event-stream",
                headers=headers,
                background=BackgroundTask(run.finalize),
            )
        except BaseException:
            # 流式响应尚未接管槽位：并发流槽位与在飞槽位各还一次。
            if acquired_registry and registry is not None:
                await registry.release()
            gate.release()
            raise
    try:
        result = await chat_service.complete_direct(
            session,
            api_key=context.api_key,
            user=context.user,
            body=body,
            request_id=request_id,
            session_factory=request.app.state.session_factory,
            client_ip=client_ip,
            request_headers=request_headers,
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
    finally:
        gate.release()


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
