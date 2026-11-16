"""Write-ahead log for billing rows.

The request hot path only appends a JSON line to a buffered file; the SQLite
sink drains that file in batches. A crash between the append and the database
commit leaves records in the file, and replay is safe because request_ledger
is unique on request_id.

File layout: one file per process start, wal-<yyyymmdd-hh>-p<pid>-<start>.jsonl.
The suffix keeps the committed-prefix marker unambiguous and lets several
processes share one directory (they never write the same file).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Protocol

from l01_core.jsonfast import dumps_compact
from l01_core.logging import get_logger

logger = get_logger("slipstream.ledger.wal")

WAL_PREFIX = "wal-"
WAL_SUFFIX = ".jsonl"

# wal-<yyyymmdd-hh>-p<pid>-<yyyymmdd-HHMMSS>.jsonl, see WalWriter._ensure_file.
_PID_PATTERN = re.compile(
    rf"^{re.escape(WAL_PREFIX)}\d{{8}}-\d{{2}}-p(\d+)-"
    rf"\d{{8}}-\d{{6}}{re.escape(WAL_SUFFIX)}$"
)

_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _owner_pid(name: str) -> int | None:
    """Return the pid encoded in a WAL file name, or None when it cannot be read."""
    match = _PID_PATTERN.match(name)
    if match is None:
        return None
    pid = int(match.group(1))
    return pid if pid > 0 else None


def _windows_owner_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Only "no such process" proves the writer is gone; access denied or any
        # other error is treated as alive.
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _owner_alive(pid: int) -> bool:
    """True when a process with this pid may still be writing its WAL file.

    Every uncertain answer is True: leaving a file alone only delays a replay,
    while touching a live writer's file loses bills. A zombie counts as alive
    and a recycled pid keeps its file around - both are deliberate.
    """
    if pid <= 0:
        return True
    if os.name == "nt":
        return _windows_owner_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class _Sink(Protocol):
    async def write(self, records: list[dict[str, Any]]) -> int: ...


def _parse_file(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return (records, durable prefix length) for one WAL file.

    Records are written in order and acknowledged as a prefix, so the largest
    committed marker tells replay where the durable part ends. A torn final
    line from a crash is ignored; every earlier line still stands.
    """
    records: list[dict[str, Any]] = []
    committed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("账本 WAL 读取失败 %s：%s", path, exc)
        return records, committed
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            logger.warning("账本 WAL 存在损坏行，已忽略 %s", path.name)
            continue
        if "committed" in obj:
            try:
                committed = max(committed, int(obj["committed"]))
            except (TypeError, ValueError):
                continue
        else:
            records.append(obj)
    return records, min(committed, len(records))


def pending_records(path: Path) -> list[dict[str, Any]]:
    records, committed = _parse_file(path)
    return records[committed:]


class WalWriter:
    """Append-only, fsync-batched writer for ledger rows.

    append() only touches memory plus a buffered file write; flush() (at most
    every flush_ms or max_records appends) makes the batch durable. The sink
    calls ack() after the database commit, which appends a committed marker.
    When every record in the file is durable the file is truncated so a
    long-lived process does not accumulate an unbounded WAL.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        flush_ms: int = 100,
        max_records: int = 256,
        clock=time.monotonic,
    ) -> None:
        self.directory = Path(directory)
        self.flush_ms = max(1, int(flush_ms))
        self.max_records = max(1, int(max_records))
        self._clock = clock
        self._started = time.strftime("%Y%m%d-%H%M%S")
        self._file: Any = None
        self._path: Path | None = None
        self._records = 0
        self._pending: list[dict[str, Any]] = []
        self._unflushed = 0
        self._last_flush = clock()
        self.appends = 0
        self.flushes = 0

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def pending(self) -> int:
        return len(self._pending)

    def _ensure_file(self) -> None:
        if self._file is not None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        hour = time.strftime("%Y%m%d-%H")
        self._path = self.directory / (
            f"{WAL_PREFIX}{hour}-p{os.getpid()}-{self._started}{WAL_SUFFIX}"
        )
        self._file = self._path.open("a", encoding="utf-8")

    def append(self, record: dict[str, Any]) -> None:
        self._ensure_file()
        line = dumps_compact(record).decode("utf-8")
        self._file.write(line + "\n")
        self._records += 1
        self._pending.append(record)
        self._unflushed += 1
        self.appends += 1
        elapsed_ms = (self._clock() - self._last_flush) * 1000.0
        if self._unflushed >= self.max_records or elapsed_ms >= self.flush_ms:
            self.flush()

    def flush(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._unflushed = 0
        self._last_flush = self._clock()
        self.flushes += 1

    def take_pending(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return list(self._pending)
        return list(self._pending[:limit])

    def ack(self, count: int) -> None:
        if count <= 0 or self._file is None:
            return
        del self._pending[:count]
        committed = self._records - len(self._pending)
        self._file.write(json.dumps({"committed": committed}) + "\n")
        self.flush()
        if not self._pending:
            self._truncate()

    def _truncate(self) -> None:
        # Every record is durable: an empty file replays nothing next startup.
        self.close()
        if self._path is None:
            return
        try:
            self._file = self._path.open("w", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk level failure
            logger.warning("账本 WAL 截断失败 %s：%s", self._path, exc)
            self._file = None
            return
        self._records = 0
        self._unflushed = 0
        self._last_flush = self._clock()

    def close(self) -> None:
        if self._file is None:
            return
        try:
            self.flush()
        finally:
            self._file.close()
            self._file = None


def _clear_after_replay(path: Path) -> bool:
    """Empty a replayed WAL file: its rows are committed, replay is idempotent."""
    try:
        with path.open("w", encoding="utf-8"):
            pass
    except OSError as exc:
        logger.warning("账本 WAL 回放后清空失败 %s：%s", path, exc)
        return False
    return True


async def replay_pending(
    directory: str | Path,
    sink: _Sink,
    *,
    keep_open_s: float = 60.0,
) -> int:
    """Replay unacknowledged records left behind by writers that are gone.

    Replay is idempotent: duplicate request_ids are absorbed by the unique key.
    A file is only touched when its owner pid is no longer alive - several
    processes share the directory, and unlinking (or emptying) a live writer's
    file would let it append to a dead inode, losing bills on a crash. A file
    whose owner cannot be determined is replayed but never emptied or deleted:
    replaying twice is cheap, dropping a live writer's WAL is not. Files are
    deleted only once nothing is pending and they stayed quiet for keep_open_s.
    """
    root = Path(directory)
    if not root.is_dir():
        return 0
    total = 0
    now = time.time()
    for path in sorted(root.glob(f"{WAL_PREFIX}*{WAL_SUFFIX}")):
        pid = _owner_pid(path.name)
        if pid is not None and _owner_alive(pid):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        records, committed = _parse_file(path)
        pending = records[committed:]
        replayed = False
        if pending:
            await sink.write(pending)
            total += len(pending)
            replayed = True
            logger.info("账本 WAL 回放 %s：%d 条", path.name, len(pending))
            if pid is None:
                logger.warning("账本 WAL 属主未知，回放后保留 %s", path.name)
            elif not _clear_after_replay(path):
                continue
        if pid is None or replayed:
            continue
        if not pending and now - mtime >= keep_open_s:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("账本 WAL 清理失败 %s：%s", path, exc)
    return total
