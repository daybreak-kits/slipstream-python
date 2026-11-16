"""On-disk snapshot for the models.dev daily sync (ADR-073).

The snapshot is two files under models_dev_dir:

* api.json  -- the raw https://models.dev/api.json body, byte for byte.
* meta.json -- fetch bookkeeping (validators, counters, last attempt).

Invariants (ADR-073 decision 1):

* A failed pull never damages the previous snapshot: save_failure() only
  rewrites meta.json and keeps the validators, so the next conditional request
  can still answer 304 against the snapshot that is already on disk.
* api.json is replaced atomically and only after the payload parsed as a JSON
  object, so a truncated download or an HTML error page cannot become the live
  snapshot.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from l01_core.timeutil import now_ms

SNAPSHOT_API_NAME = "api.json"
SNAPSHOT_META_NAME = "meta.json"
DEFAULT_SNAPSHOT_DIR = "data/models-dev"
META_VERSION = 1
MAX_ERROR_SUMMARY = 200


class SnapshotError(RuntimeError):
    """The payload was refused: writing it would damage a good snapshot."""


@dataclass(frozen=True, slots=True)
class SnapshotMeta:
    """Bookkeeping for the on-disk snapshot.

    fetched_at is the last *successful* fetch (ms); attempted_at is the last
    attempt of any result. last_attempt_date is the local calendar day of that
    attempt, which is what the lazy trigger compares against so a failed pull is
    not retried until tomorrow (ADR-073 decisions 1 and 2).
    """

    version: int = META_VERSION
    source_url: str = ""
    fetched_at: int = 0
    attempted_at: int = 0
    last_attempt_date: str = ""
    http_date: str = ""
    etag: str = ""
    last_modified: str = ""
    bytes: int = 0
    providers: int = 0
    entries: int = 0
    error_summary: str = ""
    error_at: int = 0


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def meta_from_json(payload: dict[str, Any] | None) -> SnapshotMeta:
    """Build a meta from a stored payload; unknown/missing values fall back."""
    data = payload if isinstance(payload, dict) else {}
    return SnapshotMeta(
        version=_as_int(data.get("version"), META_VERSION),
        source_url=str(data.get("source_url") or ""),
        fetched_at=_as_int(data.get("fetched_at")),
        attempted_at=_as_int(data.get("attempted_at")),
        last_attempt_date=str(data.get("last_attempt_date") or ""),
        http_date=str(data.get("http_date") or ""),
        etag=str(data.get("etag") or ""),
        last_modified=str(data.get("last_modified") or ""),
        bytes=_as_int(data.get("bytes")),
        providers=_as_int(data.get("providers")),
        entries=_as_int(data.get("entries")),
        error_summary=str(data.get("error_summary") or ""),
        error_at=_as_int(data.get("error_at")),
    )


def meta_to_json(meta: SnapshotMeta) -> dict[str, Any]:
    return asdict(meta)


def snapshot_counts(payload: dict[str, Any]) -> tuple[int, int]:
    """Return (providers, entries) for a provider -> {models: {...}} payload."""
    providers = 0
    entries = 0
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        providers += 1
        models = value.get("models")
        if isinstance(models, dict):
            entries += len(models)
    return providers, entries


def _write_atomic(path: Path, data: bytes) -> None:
    """Write data to path through a sibling temp file and os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class SnapshotStore:
    """Reads and writes the models.dev snapshot under one directory."""

    def __init__(self, root: str | Path = DEFAULT_SNAPSHOT_DIR) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def api_path(self) -> Path:
        return self._root / SNAPSHOT_API_NAME

    @property
    def meta_path(self) -> Path:
        return self._root / SNAPSHOT_META_NAME

    def load_meta(self) -> SnapshotMeta:
        try:
            raw = self.meta_path.read_text(encoding="utf-8")
        except OSError:
            return SnapshotMeta()
        try:
            payload = json.loads(raw)
        except ValueError:
            return SnapshotMeta()
        return meta_from_json(payload if isinstance(payload, dict) else None)

    def load_bytes(self) -> bytes | None:
        try:
            return self.api_path.read_bytes()
        except OSError:
            return None

    def load_api(self) -> dict[str, Any] | None:
        raw = self.load_bytes()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def present(self) -> bool:
        try:
            return self.api_path.stat().st_size > 0
        except OSError:
            return False

    def save(self, api_bytes: bytes, *, meta: SnapshotMeta) -> SnapshotMeta:
        """Store a validated payload plus bookkeeping; raises SnapshotError."""
        if not api_bytes:
            raise SnapshotError("empty snapshot payload")
        try:
            payload = json.loads(api_bytes)
        except ValueError as exc:
            raise SnapshotError(f"snapshot payload is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SnapshotError("snapshot payload is not a JSON object")
        providers, entries = snapshot_counts(payload)
        stamped = replace(
            meta,
            version=META_VERSION,
            attempted_at=meta.attempted_at or now_ms(),
            bytes=len(api_bytes),
            providers=meta.providers or providers,
            entries=meta.entries or entries,
            error_summary="",
            error_at=0,
        )
        _write_atomic(self.api_path, api_bytes)
        _write_atomic(self.meta_path, json.dumps(meta_to_json(stamped), ensure_ascii=False).encode("utf-8"))
        return stamped

    def save_meta(self, meta: SnapshotMeta) -> SnapshotMeta:
        """Rewrite only meta.json (304 path and failure path)."""
        stamped = replace(meta, version=META_VERSION)
        _write_atomic(self.meta_path, json.dumps(meta_to_json(stamped), ensure_ascii=False).encode("utf-8"))
        return stamped

    def save_failure(
        self,
        error_summary: str,
        *,
        attempted_at: int | None = None,
        last_attempt_date: str = "",
        source_url: str = "",
    ) -> SnapshotMeta:
        """Record a failed attempt without touching the payload or its validators."""
        current = self.load_meta()
        stamp = attempted_at or now_ms()
        return self.save_meta(
            replace(
                current,
                source_url=source_url or current.source_url,
                attempted_at=stamp,
                last_attempt_date=last_attempt_date or current.last_attempt_date,
                error_summary=str(error_summary)[:MAX_ERROR_SUMMARY],
                error_at=stamp,
            )
        )
