"""Aggregate views over the request log.

The log list answers "which request happened". The two aggregate views answer
"how did it go", and both read the same facts:

* "combo" groups the matched request rows by the model the client asked
  for, so a combo shows up as one row instead of one row per member call.
* "status" treats the *attempts* of the matched rows as the facts: every
  attempt of every matched row is flattened, the attempts that never reached
  the upstream are dropped, and the rest are cut into contiguous status
  segments.

Two properties of the stored data shape this module and are worth stating
plainly, because they decide what a segment can say:

* An attempt record carries no HTTP status and no timestamp. The status is
  derived from the attempt's error kind (a rate limit is a 429; a network
  failure never had an HTTP status and is shown as a pseudo status whose
  label is the error code), and the moment of an attempt is the moment of the
  request row that recorded it.
* A skipped attempt never reached the upstream, so it says nothing about how
  the upstream behaved and stays out of the status view.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.textfold import fold_key
from l04_repo import repo
from l05_service.aggregates import percentile

#: Two attempts further apart than this are two events, not one segment.
STATUS_SEGMENT_IDLE_MS = 5 * 60 * 1000
#: The aggregate views never scan more rows than this; the answer then says so.
MAX_SCAN_ROWS = 50_000
#: How many request ids one group hands back as a sample.
SAMPLE_REQUEST_IDS = 5
#: The label used for attempts that never reached a model.
UNROUTED_LABEL = "未路由"
#: The label used for rows that carry no requested model.
UNNAMED_MODEL_LABEL = "未命名模型"
#: Prefix of a status segment key, so a drill-down can tell the two kinds.
STATUS_SEGMENT_PREFIX = "status:"

#: Attempt error kinds that stand for a real HTTP status. Every other kind
#: (a timeout, a refused connection, ...) never had an HTTP status of its own
#: and is shown as a pseudo status whose label is the error code itself.
KIND_HTTP_STATUS: dict[str, int] = {
    "rate_limit": 429,
    "not_found": 404,
    "bad_request": 400,
    "auth": 502,
    "upstream": 502,
    "bad_response": 502,
    "empty": 502,
    "stream_abort": 502,
}


@dataclass(frozen=True, slots=True)
class SegmentKey:
    """A parsed status segment key: the group to drill back into."""

    model: str
    status_code: int | None
    status_label: str
    first_at: int
    last_at: int
    error_kinds: list[str]


def _loads_attempts(raw: Any) -> list[dict[str, Any]]:
    """The attempt list of one row, ignoring anything that is not a list.

    Accepts the already-decoded list too: a rows-mode scan hands the payloads
    over directly (ADR-072) while every existing caller keeps passing the JSON
    string from the dict.
    """
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def attempt_status(row_status: int, attempt: dict[str, Any]) -> tuple[int | None, str]:
    """The status of one attempt as (status_code, status_label).

    An attempt written after this change stores the status the upstream really
    answered with, and that stored code wins. Older rows have no such field, so
    the status is derived instead: a successful attempt takes the status of its
    request (the request ended on that attempt), and a failed attempt takes the
    status its error kind stands for. A failure that never had an HTTP status
    keeps its error code as a pseudo status.
    """
    for key in ("http_status", "status_code"):
        value = attempt.get(key)
        # A stored code wins: it is what the upstream answered, which no
        # error-kind mapping can reconstruct (a 401 is not a 502).
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value, str(value)
    attempt_status_value = str(attempt.get("status") or "")
    error_code = str(attempt.get("error_code") or "")
    if attempt_status_value == "success":
        status = int(row_status)
        return status, str(status)
    if error_code in KIND_HTTP_STATUS:
        status = KIND_HTTP_STATUS[error_code]
        return status, str(status)
    if error_code:
        return None, error_code
    return None, "error"


def _cache_ratio(prompt_tokens: int, cached_tokens: int) -> float:
    """Cache hit ratio of an aggregate, with the containment rule of billing."""
    if cached_tokens <= 0:
        return 0.0
    if prompt_tokens >= cached_tokens:
        denominator = prompt_tokens
    else:
        denominator = prompt_tokens + cached_tokens
    return round(cached_tokens / denominator, 6) if denominator else 0.0


def _row_channels(row: dict[str, Any]) -> set[str]:
    """Every channel name a row touched: the winner and the members."""
    names = {str(row.get("channel_name") or "")}
    for attempt in _loads_attempts(row.get("attempts_json")):
        names.add(str(attempt.get("channel_name") or ""))
    return {name for name in names if name}


def _row_attempts(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The attempts of one row that actually reached the upstream."""
    return [
        attempt
        for attempt in _loads_attempts(row.get("attempts_json"))
        if str(attempt.get("status") or "") != "skipped"
    ]


def _sample_request_ids(rows: list[dict[str, Any]]) -> list[str]:
    """The newest request ids of a group, at most SAMPLE_REQUEST_IDS of them."""
    ordered = sorted(rows, key=lambda row: int(row.get("created_at") or 0), reverse=True)
    ids: list[str] = []
    for row in ordered:
        request_id = str(row.get("request_id") or "")
        if request_id and request_id not in ids:
            ids.append(request_id)
        if len(ids) >= SAMPLE_REQUEST_IDS:
            break
    return ids


def combo_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the matched rows by the model the client asked for."""
    # 同名不同写法并成一个桶（只折 ASCII，与 SQL lower() 同口径），
    # 展示名保留首见拼写（ADR-071 决策 14）。
    buckets: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        raw = str(row.get("requested_model") or "")
        key = fold_key(raw)
        labels.setdefault(key, raw)
        buckets.setdefault(key, []).append(row)
    groups: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        name = labels[key]
        prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in bucket)
        completion_tokens = sum(int(row.get("completion_tokens") or 0) for row in bucket)
        cached_tokens = sum(int(row.get("cached_tokens") or 0) for row in bucket)
        ttfts = [
            int(row["ttft_ms"]) for row in bucket if row.get("ttft_ms") is not None
        ]
        totals = [
            int(row.get("total_ms") or 0)
            for row in bucket
            if int(row.get("total_ms") or 0) > 0
        ]
        groups.append(
            {
                "key": name or UNNAMED_MODEL_LABEL,
                "label": name or UNNAMED_MODEL_LABEL,
                "status_code": None,
                "status_label": "",
                "first_at": min(int(row.get("created_at") or 0) for row in bucket),
                "last_at": max(int(row.get("created_at") or 0) for row in bucket),
                "requests": len(bucket),
                "attempts": sum(len(_row_attempts(row)) for row in bucket),
                "errors": sum(
                    1 for row in bucket if int(row.get("http_status") or 0) >= 400
                ),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_tokens": cached_tokens,
                "cache_ratio": _cache_ratio(prompt_tokens, cached_tokens),
                "cost_usd": round(
                    sum(float(row.get("cost_usd") or 0) for row in bucket), 8
                ),
                "ttft_p50_ms": percentile(ttfts, 50),
                "total_p50_ms": percentile(totals, 50),
                "total_p95_ms": percentile(totals, 95),
                "channels": sorted(
                    {name for row in bucket for name in _row_channels(row)}
                ),
                "error_codes": sorted(
                    {
                        str(row.get("error_code"))
                        for row in bucket
                        if row.get("error_code")
                    }
                ),
                "sample_request_ids": _sample_request_ids(bucket),
            }
        )
    groups.sort(key=lambda group: (-group["requests"], group["label"]))
    return groups


def _attempt_at(attempt: dict[str, Any], row_created_at: int) -> int:
    """When the hop started: its recorded wall clock, else its request row.

    Rows written before attempts carried ``at`` fall back to the row's
    ``created_at``, which is the same moment plus the request's own duration.
    """
    value = attempt.get("at")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return row_created_at


def _segment_key(model: str, status_label: str, first_at: int, last_at: int) -> str:
    """A key that carries the segment back to the rows behind it.

    The two timestamps are the ``created_at`` window of the request rows, not
    the attempt window reported on the group: a drill-down filters rows, and an
    attempt is always stamped before its own row is written.
    """
    return (
        f"{STATUS_SEGMENT_PREFIX}{quote(status_label, safe='')}"
        f":{first_at}:{last_at}:{quote(model, safe='')}"
    )


def status_segments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cut the attempts of the matched rows into contiguous status segments.

    The scan walks every attempt in time order. A segment ends when the
    upstream model changes, when the status changes, or when the gap to the
    previous attempt is longer than STATUS_SEGMENT_IDLE_MS.
    """
    facts: list[dict[str, Any]] = []
    for row in rows:
        created_at = int(row.get("created_at") or 0)
        row_status = int(row.get("http_status") or 0)
        row_id = str(row.get("id") or "")
        request_id = str(row.get("request_id") or "")
        for index, attempt in enumerate(_row_attempts(row)):
            status_code, status_label = attempt_status(row_status, attempt)
            facts.append(
                {
                    "at": _attempt_at(attempt, created_at),
                    "order": index,
                    "row_id": row_id,
                    "request_id": request_id,
                    "row": row,
                    "upstream_model": str(attempt.get("upstream_model") or ""),
                    "attempt_status": str(attempt.get("status") or ""),
                    "status_code": status_code,
                    "status_label": status_label,
                    "error_code": str(attempt.get("error_code") or ""),
                    "channel_name": str(attempt.get("channel_name") or ""),
                }
            )
    facts.sort(key=lambda fact: (fact["at"], fact["order"], fact["request_id"]))

    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for fact in facts:
        if current:
            previous = current[-1]
            same_model = fold_key(previous["upstream_model"]) == fold_key(
                fact["upstream_model"]
            )
            same_status = (
                previous["status_code"] == fact["status_code"]
                and previous["status_label"] == fact["status_label"]
            )
            idle = fact["at"] - previous["at"] <= STATUS_SEGMENT_IDLE_MS
            if not (same_model and same_status and idle):
                segments.append(current)
                current = []
        current.append(fact)
    if current:
        segments.append(current)

    groups: list[dict[str, Any]] = []
    for segment in segments:
        model = segment[0]["upstream_model"]
        status_code = segment[0]["status_code"]
        status_label = segment[0]["status_label"]
        rows_in_segment: dict[str, dict[str, Any]] = {}
        for fact in segment:
            rows_in_segment.setdefault(fact["row_id"], fact["row"])
        bucket = list(rows_in_segment.values())
        prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in bucket)
        completion_tokens = sum(int(row.get("completion_tokens") or 0) for row in bucket)
        cached_tokens = sum(int(row.get("cached_tokens") or 0) for row in bucket)
        ttfts = [
            int(row["ttft_ms"]) for row in bucket if row.get("ttft_ms") is not None
        ]
        totals = [
            int(row.get("total_ms") or 0)
            for row in bucket
            if int(row.get("total_ms") or 0) > 0
        ]
        errors = sum(
            1
            for fact in segment
            if fact["attempt_status"] != "success"
            or int(fact["status_code"] or 0) >= 400
        )
        first_at = min(fact["at"] for fact in segment)
        last_at = max(fact["at"] for fact in segment)
        # The row window (not the attempt window) is what a drill-down can
        # filter on, so the key carries that one.
        row_at = [int(fact["row"].get("created_at") or 0) for fact in segment]
        key = _segment_key(model, status_label, min(row_at), max(row_at))
        groups.append(
            {
                "key": key,
                "label": model or UNROUTED_LABEL,
                "status_code": status_code,
                "status_label": status_label,
                "first_at": first_at,
                "last_at": last_at,
                "requests": len(bucket),
                "attempts": len(segment),
                "errors": errors,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_tokens": cached_tokens,
                "cache_ratio": _cache_ratio(prompt_tokens, cached_tokens),
                "cost_usd": round(
                    sum(float(row.get("cost_usd") or 0) for row in bucket), 8
                ),
                "ttft_p50_ms": percentile(ttfts, 50),
                "total_p50_ms": percentile(totals, 50),
                "total_p95_ms": percentile(totals, 95),
                "channels": sorted(
                    {fact["channel_name"] for fact in segment if fact["channel_name"]}
                ),
                "error_codes": sorted(
                    {fact["error_code"] for fact in segment if fact["error_code"]}
                ),
                "sample_request_ids": _sample_request_ids(bucket),
            }
        )
    groups.sort(key=lambda group: (-group["last_at"], group["label"]))
    return groups


def parse_segment_key(key: str) -> SegmentKey | None:
    """Read a status segment key back into the group it describes."""
    if not key.startswith(STATUS_SEGMENT_PREFIX):
        return None
    payload = key[len(STATUS_SEGMENT_PREFIX) :]
    parts = payload.split(":")
    if len(parts) != 4:
        return None
    status_label, first_at, last_at, model = parts
    try:
        first = int(first_at)
        last = int(last_at)
    except ValueError:
        return None
    status_code = int(status_label) if status_label.isdigit() else None
    if status_code is None:
        error_kinds = [status_label]
    else:
        error_kinds = [
            kind for kind, code in KIND_HTTP_STATUS.items() if code == status_code
        ]
    return SegmentKey(
        model=unquote(model),
        status_code=status_code,
        status_label=status_label,
        first_at=first,
        last_at=last,
        error_kinds=error_kinds,
    )


async def compute_groups(
    session: AsyncSession,
    *,
    view: str,
    page: int,
    page_size: int,
    filters: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, bool]:
    """The aggregate groups of one view, the group count and the scan flag."""
    rows, scan_limited = await repo.scan_request_log_facts(
        session, limit=MAX_SCAN_ROWS, **filters
    )
    if view == "combo":
        groups = combo_groups(rows)
    else:
        groups = status_segments(rows)
    total = len(groups)
    start = max(0, (page - 1) * page_size)
    return groups[start : start + page_size], total, scan_limited
