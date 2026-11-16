from __future__ import annotations

import asyncio

from l01_core.errors import AppError, ErrorCode


class StreamRegistry:
    """Tracks in-flight SSE relays so shutdown can drain them.

    Uvicorn stops accepting connections before it runs lifespan teardown, but a
    process manager may send SIGTERM while long streams are still open. The
    registry closes admission first, then waits for every relay to call
    ``release`` before the shared HTTP/DB pools are disposed.
    """

    def __init__(self) -> None:
        self._active = 0
        self._closing = False
        self._lock = asyncio.Lock()
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active(self) -> int:
        return self._active

    @property
    def closing(self) -> bool:
        return self._closing

    def begin_shutdown(self) -> None:
        """Stop admitting streams. Safe to call from a signal handler."""
        self._closing = True

    async def acquire(self) -> None:
        async with self._lock:
            if self._closing:
                raise AppError(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    "网关正在关闭，暂不接收新的流式请求",
                    status=503,
                )
            self._active += 1
            self._idle.clear()

    async def release(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def drain(self, timeout_s: float) -> bool:
        """Wait for active relays. Returns False when the deadline expires."""
        self.begin_shutdown()
        if self._active == 0:
            return True
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=max(0.0, timeout_s))
        except asyncio.TimeoutError:
            return False
        return True
