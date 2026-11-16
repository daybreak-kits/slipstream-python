"""Ledger write service: WAL append on the hot path, batched SQLite sink.

Degradation order (never reversed): if the WAL append itself fails the row is
written to SQLite synchronously and counted as degraded; if only the sink
fails, the row stays in the WAL and is retried. A bill is never dropped for a
diagnostics reason.
"""

from __future__ import annotations

import asyncio
from typing import Any

from l01_core import runtime
from l01_core.logging import get_logger
from l05_service.ledger.sink import SqliteLedgerSink
from l05_service.ledger.wal import WalWriter, replay_pending

logger = get_logger("slipstream.ledger")


class LedgerService:
    def __init__(self) -> None:
        self._session_factory: Any = None
        self._sink: SqliteLedgerSink | None = None
        self._wal: WalWriter | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self.replayed = 0
        self.written = 0
        self.degraded = 0
        self.failed_batches = 0
        self.last_error = ""

    def enabled(self) -> bool:
        return runtime.current_settings().ledger_split != "off"

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "replayed": self.replayed,
            "written": self.written,
            "degraded": self.degraded,
            "failed_batches": self.failed_batches,
            "pending": self._wal.pending if self._wal is not None else 0,
            "wal": self._wal.path.name if self._wal is not None and self._wal.path else "",
            "last_error": self.last_error,
        }

    async def startup(self, session_factory: Any) -> None:
        if not self.enabled():
            return
        cfg = runtime.current_settings()
        self._session_factory = session_factory
        self._sink = SqliteLedgerSink(session_factory)
        self._wal = WalWriter(
            cfg.ledger_wal_dir,
            flush_ms=cfg.ledger_wal_flush_ms,
        )
        self.replayed = await replay_pending(cfg.ledger_wal_dir, self._sink)
        if self.replayed:
            logger.info("账本启动回放完成：%d 条", self.replayed)
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._runner(), name="ledger-sink")

    async def charge(self, record: dict[str, Any]) -> None:
        if not self.enabled() or self._wal is None:
            return
        try:
            self._wal.append(record)
        except OSError as exc:
            self.degraded += 1
            self.last_error = str(exc)
            logger.warning("账本 WAL 写入失败，降级为同步写：%s", exc)
            await self._write_now([record])

    async def _write_now(self, records: list[dict[str, Any]]) -> None:
        if self._sink is None:
            return
        try:
            await self._sink.write(records)
            self.written += len(records)
        except Exception as exc:  # a bill write must not fail the request
            self.failed_batches += 1
            self.last_error = str(exc)
            logger.warning("账本同步写失败：%s", exc)

    async def flush_once(self) -> int:
        wal = self._wal
        if wal is None or self._sink is None:
            return 0
        cfg = runtime.current_settings()
        batch_size = max(1, int(cfg.ledger_sink_batch))
        total = 0
        async with self._lock:
            wal.flush()
            while True:
                batch = wal.take_pending(batch_size)
                if not batch:
                    break
                try:
                    await self._sink.write(batch)
                except Exception as exc:
                    self.failed_batches += 1
                    self.last_error = str(exc)
                    logger.warning("账本落库失败，保留在 WAL 待重试：%s", exc)
                    break
                wal.ack(len(batch))
                self.written += len(batch)
                total += len(batch)
        return total

    async def _runner(self) -> None:
        interval = max(50, int(runtime.current_settings().ledger_sink_interval_ms)) / 1000.0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            try:
                await self.flush_once()
            except Exception as exc:  # never let the sink task die
                logger.warning("账本 sink 循环异常：%s", exc)

    async def drain(self, timeout: float = 5.0) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            # The sink loop wakes up on _stop by itself, so let it finish the
            # batch it may be committing. Cancelling it mid-commit aborts the
            # greenlet-driven close and leaks the SQLite handle.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (asyncio.CancelledError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        await self.flush_once()
        if self._wal is not None:
            self._wal.close()
        self._wal = None

    def reset(self) -> None:
        self._session_factory = None
        self._sink = None
        self._wal = None
        self._task = None
        self._stop = asyncio.Event()
        self.replayed = 0
        self.written = 0
        self.degraded = 0
        self.failed_batches = 0
        self.last_error = ""


ledger_service = LedgerService()
