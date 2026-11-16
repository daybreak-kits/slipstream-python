from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from l01_core.errors import AppError, ErrorCode, OpenAIError


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _retry_after_headers(status: int, details: dict[str, Any]) -> dict[str, str]:
    """Emit Retry-After only when the wait is actually known.

    A cooling 503 names its recovery moment (``retry_after_s``); a window that
    never reopens inside the horizon cannot, and a one-second hint would make
    clients retry forever. Rate limits keep a one-second floor because being
    throttled is itself the signal to come back.
    """
    if status not in (429, 503):
        return {}
    value = details.get("retry_after_s")
    try:
        seconds = max(1, int(value)) if value is not None else None
    except (TypeError, ValueError):
        seconds = None
    if seconds is None:
        return {"Retry-After": "1"} if status == 429 else {}
    return {"Retry-After": str(seconds)}


def install_exception_handlers(app: FastAPI) -> None:
    """Keep every API error on the same {code, message, details, request_id} shape."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        content: dict[str, object] = {
            "code": exc.code,
            "message": exc.message,
            "request_id": _request_id(request),
        }
        if exc.details:
            content["details"] = exc.details
        return JSONResponse(
            status_code=exc.status,
            content=content,
            headers=_retry_after_headers(exc.status, exc.details),
        )

    @app.exception_handler(OpenAIError)
    async def _handle_openai_error(request: Request, exc: OpenAIError) -> JSONResponse:
        error: dict[str, object] = {
            "message": exc.message,
            "type": exc.type,
            "code": exc.code,
            "request_id": _request_id(request),
        }
        if exc.param:
            error["param"] = exc.param
        for key, value in exc.details.items():
            if key not in error:
                error[key] = value
        return JSONResponse(
            status_code=exc.status,
            content={"error": error},
            headers=_retry_after_headers(exc.status, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": ErrorCode.INVALID_REQUEST,
                "message": "请求参数不合法",
                "details": {
                    "errors": [
                        {"loc": list(item.get("loc", ())), "msg": item.get("msg", ""), "type": item.get("type", "")}
                        for item in exc.errors()
                    ]
                },
                "request_id": _request_id(request),
            },
        )
