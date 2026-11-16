from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core import runtime
from l01_core.logging import get_logger
from l04_repo import attempt_filters, orm, repo

logger = get_logger("slipstream.log_pipeline")

# The queue is deliberately small: it only has to cover the gap between the
# request that wrote a row and the next flush tick. Sizing it for a backlog
# would just turn memory into the new failure mode.
QUEUE_MAXSIZE = 1000
FLUSH_BATCH = 50
FLUSH_INTERVAL_S = 0.2
DRAIN_TIMEOUT_S = 5.0


def split_enabled() -> bool:
    """True unless LEDGER_SPLIT=off asks for the single-write path."""
    return runtime.current_settings().ledger_split != "off"


@dataclass(slots=True)
class _Pending:
    log_id: str
    billing: dict[str, Any]
    observability: dict[str, Any]


class ObservabilityPipeline:
    """Bounded in-memory queue plus the task that turns it into UPDATEs.

    The billing columns are kept alongside the diagnostics so the flusher can
    rebuild a whole row when the UPDATE finds nothing: retention may already
    have purged the row, and a lost primary key must not also cost the
    attempt trail of that request.
    """

    def __init__(
        self,
        *,
        maxsize: int = QUEUE_MAXSIZE,
        batch: int = FLUSH_BATCH,
        interval_s: float = FLUSH_INTERVAL_S,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        autostart: bool = True,
    ) -> None:
        self.maxsize = maxsize
        self.batch = batch
        self.interval_s = interval_s
        self.autostart = autostart
        self._session_factory = session_factory
        self._queue: deque[_Pending] = deque()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._written = 0
        self._dropped = 0
        self._reinserted = 0
        self._failed = 0

    # -- wiring ---------------------------------------------------------
    def bind(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def stats(self) -> dict[str, int]:
        return {
            "queued": len(self._queue),
            "written": self._written,
            "dropped": self._dropped,
            "reinserted": self._reinserted,
            "failed_batches": self._failed,
        }

    # -- hot path -------------------------------------------------------
    def submit(
        self,
        log_id: str,
        billing: dict[str, Any],
        observability: dict[str, Any],
    ) -> bool:
        """Queue one row's diagnostics. False means they were dropped."""
        if self._session_factory is None or len(self._queue) >= self.maxsize:
            self._dropped += 1
            return False
        self._queue.append(_Pending(log_id, billing, observability))
        self._ensure_runner()
        return True

    # -- flushing -------------------------------------------------------
    async def flush(self) -> int:
        """Write everything currently queued; safe to call from a read path."""
        written = 0
        while self._queue:
            written += await self._flush_batch()
        return written

    async def _flush_batch(self) -> int:
        batch: list[_Pending] = []
        while self._queue and len(batch) < self.batch:
            batch.append(self._queue.popleft())
        if not batch or self._session_factory is None:
            return 0
        written = 0
        rows_mode = attempt_filters.writes_attempt_rows()
        keep_json = attempt_filters.writes_attempt_json()
        try:
            async with self._session_factory() as session:
                for item in batch:
                    values = item.observability
                    attempts: list[dict[str, Any]] | None = None
                    if rows_mode:
                        attempts = attempt_filters.attempts_from_json(
                            values.get("attempts_json")
                        )
                        if not keep_json:
                            values = {**values, "attempts_json": "[]"}
                    matched = await repo.update_request_log(
                        session, item.log_id, **values
                    )
                    if matched == 0:
                        session.add(
                            orm.RequestLogORM(
                                id=item.log_id, **item.billing, **values
                            )
                        )
                        self._reinserted += 1
                        if rows_mode:
                            # The rebuilt parent has to exist before its
                            # attempts reach the FK (autoflush would too, but
                            # relying on it here is a trap for the next edit).
                            await session.flush()
                            await attempt_filters.insert_attempts(
                                session, item.log_id, attempts or []
                            )
                    elif attempts is not None:
                        # A re-delivered patch must not double the trail.
                        await attempt_filters.replace_attempts(
                            session, item.log_id, attempts
                        )
                    written += 1
                await session.commit()
        except Exception:
            # The batch is dropped on purpose: a poison row would otherwise
            # block every later write, and these columns are allowed to be lost.
            self._failed += 1
            logger.warning(
                "observability flush failed; dropped %d rows", len(batch), exc_info=True
            )
            return 0
        self._written += written
        return written

    def _ensure_runner(self) -> None:
        if not self.autostart or self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - submit only runs in a request
            return
        self._stopping = False
        self._task = loop.create_task(self._runner())

    async def _runner(self) -> None:
        while not self._stopping:
            try:
                if self._queue:
                    await self._flush_batch()
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                logger.exception("observability pipeline loop crashed")
                await asyncio.sleep(self.interval_s)

    async def drain(self, timeout: float = DRAIN_TIMEOUT_S) -> None:
        task, self._task = self._task, None
        self._stopping = True
        try:
            await asyncio.wait_for(self.flush(), timeout)
        except Exception:
            logger.warning(
                "observability drain timed out; %d rows dropped", len(self._queue)
            )
            self._dropped += len(self._queue)
            self._queue.clear()
        if task is not None:
            # Let the loop notice _stopping and leave on its own first: a
            # cancellation landing inside a commit can leave a SQLite handle
            # open, and the queue is already written by the flush above.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (asyncio.CancelledError, Exception):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task


# One process-wide instance; create_app() binds the session factory.
observability = ObservabilityPipeline()


def submit_observability(
    log_id: str, billing: dict[str, Any], observability_values: dict[str, Any]
) -> bool:
    return observability.submit(log_id, billing, observability_values)


async def flush_observability() -> int:
    return await observability.flush()


async def drain_observability() -> None:
    await observability.drain()


def observability_stats() -> dict[str, int]:
    return observability.stats()
