"""Usage write path: a WAL append in the request, a batch behind it.

The request path never runs a statement for logging. It hands one complete row
to the sink, which appends it to an append-only WAL; a background task takes
the pending rows and upserts them into request_logs in batches. Delivery is
at-least-once and the upsert is idempotent on request_id, so a replayed
segment never creates a second row.

Failure rules:
  * a broken WAL or a dead database never touches the request: the answer is
    already on the wire,
  * a row that cannot be written stays in the WAL and is retried,
  * a row that keeps failing is dropped instead of blocking the queue.
"""

from __future__ import annotations

import asyncio
from typing import Any

from l01_core import runtime
from l01_core.logging import get_logger
from l04_repo import usage_repo
from l05_service.body_logging import BodySnapshot, write_body_log
from l05_service.ledger.wal import WalWriter, replay_pending

logger = get_logger("system.usage_sink")

# A row that fails this many times is dropped: it would block every row
# behind it forever.
MAX_ROW_ATTEMPTS = 3


class _ReplaySink:
    """Adapter that keeps replay_pending from clearing a partly delivered segment."""

    def __init__(self, sink: UsageSink) -> None:
        self._sink = sink

    async def write(self, records: list[dict[str, Any]]) -> int:
        written = await self._sink.write(records)
        if written < len(records):
            raise RuntimeError("usage replay delivered only part of the segment")
        return written


class UsageSink:
    """Append-only write path for the request log."""

    def __init__(self) -> None:
        self._wal: WalWriter | None = None
        self._session_factory: Any = None
        self._runner_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._spill: list[dict[str, Any]] = []
        self._attempts: dict[str, int] = {}
        self._written = 0
        self._dropped = 0
        self._degraded = 0
        self._failed_batches = 0
        self._last_error = ""

    # -- wiring -------------------------------------------------------------

    def enabled(self) -> bool:
        """True when the zero-SQL write path is switched on."""
        return runtime.current_settings().zero_sql_pipeline

    async def startup(self, session_factory: Any) -> None:
        """Replay what a previous process left behind, then start the runner."""
        if not self.enabled():
            return
        self._session_factory = session_factory
        settings = runtime.current_settings()
        directory = settings.usage_wal_dir
        try:
            await replay_pending(directory, _ReplaySink(self))
        except Exception as exc:  # noqa: BLE001 - replay must not block startup
            self._last_error = str(exc)
            logger.warning("usage WAL replay failed, the rows stay in the WAL: %s", exc)
        self._wal = WalWriter(directory, flush_ms=settings.usage_wal_flush_ms)
        self._runner_task = asyncio.create_task(self._runner(), name="usage-sink")

    async def drain(self) -> None:
        """Stop the runner and deliver what is left."""
        task, self._runner_task = self._runner_task, None
        if task is not None:
            task.cancel()
            try:
                # A shutdown cancelling drain() must not kill the runner
                # mid-commit (same invariant as ledger/service.py).
                await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            except asyncio.CancelledError:
                pass
        try:
            await self.flush_once()
        except Exception as exc:  # noqa: BLE001 - shutdown must not fail
            logger.warning("usage sink drain failed, the rows stay in the WAL: %s", exc)
        wal, self._wal = self._wal, None
        if wal is not None:
            wal.close()

    def reset(self) -> None:
        """Drop every in-memory state (used by the tests)."""
        task, self._runner_task = self._runner_task, None
        if task is not None:
            task.cancel()
        wal, self._wal = self._wal, None
        if wal is not None:
            wal.close()
        self._session_factory = None
        self._spill.clear()
        self._attempts.clear()
        self._written = 0
        self._dropped = 0
        self._degraded = 0
        self._failed_batches = 0
        self._last_error = ""

    # -- request path -------------------------------------------------------

    def record(
        self, row: dict[str, Any], *, body: dict[str, Any] | None = None
    ) -> bool:
        """Append one row to the WAL. Never raises: logging must not fail a request."""
        if self._wal is None:
            self._degraded += 1
            self._last_error = "the usage sink is not started"
            logger.warning("usage record dropped: the usage sink is not started")
            return False
        entry = {"row": row, "body": body}
        outcome = self._append(entry)
        if outcome == "ok":
            return True
        if outcome == "bad" and body is not None:
            # The body was the problem: keep the row, drop the body.
            logger.warning("usage record dropped its body (not serialisable)")
            outcome = self._append({"row": row, "body": None})
            if outcome == "ok":
                return True
        if outcome == "bad":
            self._dropped += 1
        return False

    def _append(self, entry: dict[str, Any]) -> str:
        """Append one entry to the WAL: "ok", "bad" (not serialisable) or "io"."""
        wal = self._wal
        if wal is None:
            return "io"
        try:
            wal.append(entry)
        except (TypeError, ValueError) as exc:
            logger.warning("usage record is not serialisable: %s", exc)
            return "bad"
        except OSError as exc:
            self._spill.append(entry)
            self._degraded += 1
            self._last_error = str(exc)
            logger.warning("usage WAL append failed, the row waits in memory: %s", exc)
            return "io"
        return "ok"

    def flush_wal(self) -> None:
        """Flush the WAL file to disk (fsync) without delivering anything."""
        wal = self._wal
        if wal is not None:
            wal.flush()

    # -- background path ----------------------------------------------------

    async def flush_once(self) -> int:
        """Deliver the pending rows. Returns how many rows became durable."""
        wal = self._wal
        if wal is None:
            return 0
        written = 0
        async with self._lock:
            self._drain_spill()
            wal.flush()
            batch_size = max(1, runtime.current_settings().usage_sink_batch)
            while True:
                batch = wal.take_pending(batch_size)
                if not batch:
                    break
                # A row that keeps failing is dropped instead of blocking the queue.
                while batch and self._attempts.get(self._request_id(batch[0]), 0) >= (
                    MAX_ROW_ATTEMPTS
                ):
                    wal.ack(1)
                    self._dropped += 1
                    logger.warning(
                        "usage row dropped after %d attempts (request_id=%s)",
                        MAX_ROW_ATTEMPTS,
                        self._request_id(batch[0]),
                    )
                    batch = batch[1:]
                if not batch:
                    continue
                try:
                    count = await self.write(batch)
                except Exception as exc:  # noqa: BLE001 - the rows stay in the WAL
                    self._failed_batches += 1
                    self._last_error = str(exc)
                    logger.warning(
                        "usage sink flush failed, the rows stay in the WAL: %s", exc
                    )
                    break
                if count <= 0:
                    self._failed_batches += 1
                    break
                wal.ack(count)
                written += count
        return written

    async def write(self, records: list[dict[str, Any]]) -> int:
        """Persist a batch of records. Returns how many rows became durable."""
        session_factory = self._session_factory
        if session_factory is None or not records:
            return 0
        entries = [self._entry(record) for record in records]
        rows = [entry["row"] for entry in entries]
        written = 0
        async with session_factory() as session:
            try:
                await usage_repo.insert_request_logs(session, rows)
                await session.commit()
                written = len(rows)
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the batch
                await session.rollback()
                logger.warning("usage batch write failed, isolating the rows: %s", exc)
                written = await self._write_row_by_row(rows)
        if written:
            await self._write_bodies(entries[:written])
            self._written += written
            for entry in entries[:written]:
                self._attempts.pop(self._request_id(entry["row"]), None)
        return written

    async def _write_row_by_row(self, rows: list[dict[str, Any]]) -> int:
        """Write the rows one by one; stop at the first failure.

        The rows that made it are a prefix of the batch, so the caller can
        acknowledge exactly those and retry the rest.
        """
        session_factory = self._session_factory
        written = 0
        for row in rows:
            request_id = self._request_id(row)
            try:
                async with session_factory() as session:
                    await usage_repo.insert_request_log(session, row)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 - the row stays in the WAL
                self._attempts[request_id] = self._attempts.get(request_id, 0) + 1
                self._last_error = str(exc)
                logger.warning("usage row failed (request_id=%s): %s", request_id, exc)
                break
            written += 1
        return written

    async def _write_bodies(self, entries: list[dict[str, Any]]) -> None:
        """Write the body rows, after their parent row is committed."""
        if self._session_factory is None:
            return
        for entry in entries:
            body = entry.get("body")
            if not body:
                continue
            row = entry["row"]
            try:
                snapshot = BodySnapshot(**body)
            except TypeError:
                logger.warning("usage body skipped: the snapshot does not fit")
                continue
            try:
                await write_body_log(
                    self._session_factory,
                    self._request_id(row),
                    snapshot,
                    http_status=int(row.get("http_status") or 200),
                    error_code=row.get("error_code"),
                )
            except Exception as exc:  # noqa: BLE001 - the body is best effort
                logger.warning("usage body write failed: %s", exc)

    def _drain_spill(self) -> None:
        """Move rows whose WAL append failed back into the WAL."""
        if not self._spill or self._wal is None:
            return
        while self._spill:
            entry = self._spill[0]
            outcome = self._append(entry)
            if outcome != "ok":
                return
            self._spill.pop(0)

    async def _runner(self) -> None:
        while True:
            await asyncio.sleep(self._interval())
            try:
                await self.flush_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the runner must not die
                self._last_error = str(exc)
                logger.warning("usage sink flush crashed: %s", exc)

    def _interval(self) -> float:
        settings = runtime.current_settings()
        return max(0.01, settings.usage_sink_interval_ms / 1000.0)

    @staticmethod
    def _entry(record: dict[str, Any]) -> dict[str, Any]:
        row = record.get("row")
        if isinstance(row, dict):
            return {"row": row, "body": record.get("body")}
        return {"row": record, "body": None}

    @staticmethod
    def _request_id(row: dict[str, Any]) -> str:
        return str(row.get("request_id") or "")

    def stats(self) -> dict[str, Any]:
        wal = self._wal
        return {
            "enabled": self.enabled(),
            "started": wal is not None,
            "written": self._written,
            "dropped": self._dropped,
            "degraded": self._degraded,
            "failed_batches": self._failed_batches,
            "pending": wal.pending if wal is not None else 0,
            "spill": len(self._spill),
            "last_error": self._last_error,
            "wal": str(wal.path) if wal is not None and wal.path else "",
        }


usage_sink = UsageSink()


async def flush_usage_records() -> int:
    """Deliver the pending usage rows (read-your-writes for the log pages)."""
    return await usage_sink.flush_once()
