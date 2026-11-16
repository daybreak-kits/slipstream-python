from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class OpenAIError(Exception):
    """An error rendered with the OpenAI-compatible ``{error: ...}`` envelope."""

    status: int
    message: str
    code: str
    type: str = "invalid_request_error"
    param: str | None = None

    def __init__(
        self,
        status: int,
        message: str,
        code: str,
        type: str = "invalid_request_error",
        param: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.type = type
        self.param = param
        # Extra machine-readable fields, e.g. ``next_available_at``. They are
        # merged into the OpenAI error object by the API error handler.
        self.details = details or {}

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ErrorCode:
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_SCHEDULED_OFFLINE = "MODEL_SCHEDULED_OFFLINE"
    # ADR-070 D3: temporary unavailability that recovers on its own (timed ban).
    MODEL_COOLDOWN = "MODEL_COOLDOWN"
    NO_AVAILABLE_CHANNEL = "NO_AVAILABLE_CHANNEL"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_AUTH_FAILED = "UPSTREAM_AUTH_FAILED"
    UPSTREAM_QUOTA_EXHAUSTED = "UPSTREAM_QUOTA_EXHAUSTED"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    STREAM_ABORTED = "STREAM_ABORTED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INVALID_API_KEY = "INVALID_API_KEY"
    BAD_UPSTREAM_RESPONSE = "BAD_UPSTREAM_RESPONSE"
    INTERNAL = "INTERNAL"
