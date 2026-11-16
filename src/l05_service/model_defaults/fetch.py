"""Conditional GET for the models.dev snapshot (ADR-073 decisions 1 and 2).

The request path never calls this module: the pull is triggered lazily from the
model directory read and from the manual refresh endpoint. A 304 is a success --
it only refreshes the bookkeeping -- and every failure stays a value instead of
an exception, so a broken network cannot turn the settings page into a 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from l01_core.timeutil import now_ms

FetchStatus = Literal["ok", "not_modified", "error"]
MAX_ERROR_TEXT = 200


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: FetchStatus
    body: bytes | None = None
    etag: str = ""
    last_modified: str = ""
    http_date: str = ""
    bytes: int = 0
    status_code: int = 0
    error_summary: str = ""
    elapsed_ms: int = 0
    fetched_at: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def not_modified(self) -> bool:
        return self.status == "not_modified"


def _header(response: httpx.Response, name: str) -> str:
    return str(response.headers.get(name) or "")


def _summarize(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:MAX_ERROR_TEXT]


async def fetch_snapshot(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str = "",
    last_modified: str = "",
    timeout_s: float = 60.0,
) -> FetchResult:
    """Conditional GET of url; returns a FetchResult, never raises.

    etag/last_modified come from the snapshot that is already on disk. When
    either is present the request carries the matching validator and a 304 means
    the on-disk payload is still current.
    """
    headers: dict[str, str] = {"accept": "application/json"}
    if etag:
        headers["if-none-match"] = etag
    if last_modified:
        headers["if-modified-since"] = last_modified
    started = now_ms()
    try:
        response = await client.get(url, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        return FetchResult(
            status="error",
            error_summary=f"timeout after {timeout_s:g}s ({_summarize(exc)})"[:MAX_ERROR_TEXT],
            elapsed_ms=now_ms() - started,
            fetched_at=started,
        )
    except httpx.HTTPError as exc:
        return FetchResult(
            status="error",
            error_summary=_summarize(exc),
            elapsed_ms=now_ms() - started,
            fetched_at=started,
        )
    elapsed = now_ms() - started
    if response.status_code == 304:
        return FetchResult(
            status="not_modified",
            etag=_header(response, "etag") or etag,
            last_modified=_header(response, "last-modified") or last_modified,
            http_date=_header(response, "date"),
            status_code=304,
            elapsed_ms=elapsed,
            fetched_at=started,
        )
    if response.status_code != 200:
        return FetchResult(
            status="error",
            status_code=response.status_code,
            error_summary=f"HTTP {response.status_code}",
            elapsed_ms=elapsed,
            fetched_at=started,
        )
    body = response.content
    if not body:
        return FetchResult(
            status="error",
            status_code=200,
            error_summary="empty body",
            elapsed_ms=elapsed,
            fetched_at=started,
        )
    return FetchResult(
        status="ok",
        body=body,
        etag=_header(response, "etag"),
        last_modified=_header(response, "last-modified"),
        http_date=_header(response, "date"),
        bytes=len(body),
        status_code=200,
        elapsed_ms=elapsed,
        fetched_at=started,
    )
