from __future__ import annotations

import json
import math
from typing import Any, NamedTuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from l04_repo import orm, repo
from l05_service import ledger_read
from l05_service.routing import session_key_from_headers


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank percentile (``ceil(p/100 * n)``); empty input returns 0."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return int(ordered[min(rank, len(ordered)) - 1])


def percentile_set(values: list[int]) -> dict[str, int]:
    return {f"p{p}": percentile(values, p) for p in (50, 90, 95, 99)}


MIN_PERCENTILE_SAMPLES = 5


def paired_rows(rows: list[orm.RequestLogORM]) -> list[orm.RequestLogORM]:
    """Rows usable for first-token/total comparisons.

    The old dashboard sampled ``ttft_ms`` and ``total_ms`` independently, which
    could produce a total P95 below the TTFT P95. Every latency percentile now
    comes from this one cohort.
    """
    return [
        row
        for row in rows
        if row.ttft_ms is not None and int(row.total_ms or 0) > 0
    ]


def _metric_percentiles(values: list[int]) -> dict[str, int] | None:
    if len(values) < MIN_PERCENTILE_SAMPLES:
        return None
    return percentile_set(values)


def latency_percentiles(rows: list[orm.RequestLogORM]) -> dict[str, Any]:
    paired = paired_rows(rows)
    ttfts = [int(row.ttft_ms) for row in paired if row.ttft_ms is not None]
    ttfas = [int(row.ttfa_ms) for row in paired if row.ttfa_ms is not None]
    ttlts = [int(row.ttlt_ms) for row in paired if row.ttlt_ms is not None]
    totals = [int(row.total_ms) for row in paired]
    return {
        "samples": len(paired),
        "ttft": _metric_percentiles(ttfts),
        "ttfa": _metric_percentiles(ttfas),
        "ttlt": _metric_percentiles(ttlts),
        "total": _metric_percentiles(totals),
    }


# A stream can end without upstream usage even though the whole prompt was
# already sent. Those rows are still real requests: the prompt count is the
# send-side number and the cache is unknown, so they enter the cache cohorts
# with cached_tokens = 0. A row whose prompt was synthesised without any
# interruption evidence (the legacy tokenizer fallback) stays excluded.
INTERRUPTED_ERROR_CODES = frozenset({"STREAM_STOPPED", "STREAM_ABORTED"})
INTERRUPTED_HTTP_STATUSES = frozenset({499, 502})


def prompt_tokens_known(row: orm.RequestLogORM) -> bool:
    """Whether prompt_tokens describes the request that was sent upstream.

    Upstream usage always qualifies. An interrupted stream also qualifies: the
    prompt was fully sent before the break, so its count is known even though
    the completion and the cache are not. Everything else that still carries
    usage_estimated came from the character tokenizer and is excluded.
    """
    if not bool(row.usage_estimated):
        return True
    if int(row.prompt_tokens or 0) <= 0:
        return False
    error_code = str(getattr(row, "error_code", "") or "").upper()
    if error_code in INTERRUPTED_ERROR_CODES:
        return True
    return int(getattr(row, "http_status", 0) or 0) in INTERRUPTED_HTTP_STATUSES


def counted_rows(rows: list[orm.RequestLogORM]) -> list[orm.RequestLogORM]:
    """The cohort every token/cache metric reads: prompt is a known number."""
    return [row for row in rows if prompt_tokens_known(row)]


def _row_cached_tokens(row: orm.RequestLogORM) -> int:
    """Cache hits for one row; an interrupted row never claims a hit."""
    if bool(row.usage_estimated):
        return 0
    return int(row.cached_tokens or 0)


class UsageTotals(NamedTuple):
    """Token and cost sums over the rows whose prompt count is known."""

    counted: list[orm.RequestLogORM]
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float


def usage_totals(rows: list[orm.RequestLogORM]) -> UsageTotals:
    """Sum tokens and cost over the cache cohort.

    Interrupted streams contribute their known prompt and zero cache hits. Rows
    whose prompt came from the character tokenizer stay out of every sum, as do
    their synthetic cost. The summaries, the daily aggregates and the cache
    metrics all read the same cohort through this helper.
    """
    counted = counted_rows(rows)
    return UsageTotals(
        counted=counted,
        prompt_tokens=sum(int(row.prompt_tokens or 0) for row in counted),
        completion_tokens=sum(int(row.completion_tokens or 0) for row in counted),
        cached_tokens=sum(_row_cached_tokens(row) for row in counted),
        cost_usd=round(sum(float(row.cost_usd or 0) for row in counted), 8),
    )


def cache_hit_rate(rows: list[orm.RequestLogORM]) -> float:
    """A. Token-weighted cache hit rate: sum(cached) / sum(prompt).

    The denominator follows the vendored provider convention: when the upstream
    reports cached tokens inside prompt_tokens the prompt sum is used as is,
    when it reports them in parallel the cached sum is added to it.
    """
    usage = usage_totals(rows)
    if usage.cached_tokens <= 0 or usage.prompt_tokens <= 0:
        return 0.0
    denominator = (
        usage.prompt_tokens
        if usage.prompt_tokens >= usage.cached_tokens
        else usage.prompt_tokens + usage.cached_tokens
    )
    return round(usage.cached_tokens / denominator, 6)


def cache_hit_rate_samples(rows: list[orm.RequestLogORM]) -> int:
    """Number of rows in the cache cohort (rows that can express a hit rate)."""
    return len(counted_rows(rows))


def cache_request_hits(rows: list[orm.RequestLogORM]) -> int:
    """Requests in the cohort that reported at least one cached token."""
    return sum(1 for row in counted_rows(rows) if _row_cached_tokens(row) > 0)


def cache_request_hit_rate(rows: list[orm.RequestLogORM]) -> float:
    """B. Request-level hit rate: share of requests with cached_tokens > 0.

    This is the number vendor dashboards (new-api and friends) print next to a
    session-affinity panel, where a request either reused a cached prefix or
    did not.
    """
    samples = len(counted_rows(rows))
    if samples <= 0:
        return 0.0
    return round(cache_request_hits(rows) / samples, 6)


def _headers_of(row: orm.RequestLogORM) -> dict[str, object]:
    raw = getattr(row, "request_headers_json", "") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def session_key(row: orm.RequestLogORM) -> str:
    """Affinity/session key of one request, from the headers it arrived with."""
    return session_key_from_headers(_headers_of(row))


class CacheableTotals(NamedTuple):
    """C. How much of the prompt had already been seen in the same session."""

    tokens: int
    prompt_tokens: int
    sessions: int

    @property
    def ratio(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return round(self.tokens / self.prompt_tokens, 6)


def cacheable_totals(rows: list[orm.RequestLogORM]) -> CacheableTotals:
    """Estimate reusable prompt tokens from the per-session prompt sequence.

    Within one session the conversation grows, so a request's already-seen
    prefix is bounded by the largest prompt any earlier request in that session
    sent. The sum of min(prompt_i, max earlier prompt) is the sum form of "how
    much of this prompt the provider may already hold"; the first request of a
    session contributes 0. Requests without a session header contribute their
    prompt to the denominator and 0 to the numerator, so the ratio stays
    comparable to the token-weighted hit rate.
    """
    counted = counted_rows(rows)
    prompt_total = sum(int(row.prompt_tokens or 0) for row in counted)
    by_session: dict[str, list[orm.RequestLogORM]] = {}
    for row in counted:
        key = session_key(row)
        if key:
            by_session.setdefault(key, []).append(row)
    reusable = 0
    for items in by_session.values():
        items.sort(key=lambda row: int(row.created_at or 0))
        seen_prompt = 0
        for row in items:
            prompt = int(row.prompt_tokens or 0)
            reusable += min(prompt, seen_prompt)
            seen_prompt = max(seen_prompt, prompt)
    return CacheableTotals(tokens=reusable, prompt_tokens=prompt_total, sessions=len(by_session))


def cache_saved_usd(
    rows: list[orm.RequestLogORM],
    prices: dict[str, tuple[float, float]] | None = None,
) -> float:
    """D. Money the cache saved: cached tokens at input price minus cache price.

    prices maps channel_model_id to (input_price_per_1m, cache_read_price_per_1m).
    Models that never configured a cache price bill hits at the input price, so
    they save nothing.
    """
    if not prices:
        return 0.0
    saved = 0.0
    for row in counted_rows(rows):
        cached = _row_cached_tokens(row)
        if cached <= 0:
            continue
        price = prices.get(str(getattr(row, "final_channel_model_id", "") or ""))
        if not price:
            continue
        input_price, cache_price = price
        saved += cached / 1_000_000 * max(float(input_price) - float(cache_price), 0.0)
    return round(saved, 8)


async def load_model_prices(
    session: AsyncSession, rows: list[orm.RequestLogORM]
) -> dict[str, tuple[float, float]]:
    """Read the input/cache prices of the models these rows ran on."""
    model_ids = {
        str(row.final_channel_model_id)
        for row in rows
        if getattr(row, "final_channel_model_id", None)
    }
    if not model_ids:
        return {}
    result = await session.execute(
        select(
            orm.ChannelModelORM.id,
            orm.ChannelModelORM.input_price_per_1m,
            orm.ChannelModelORM.cache_read_price_per_1m,
        ).where(orm.ChannelModelORM.id.in_(model_ids))
    )
    prices: dict[str, tuple[float, float]] = {}
    for model_id, input_price, cache_price in result.all():
        input_value = float(input_price or 0.0)
        cached_value = input_value if cache_price is None else float(cache_price)
        prices[str(model_id)] = (input_value, cached_value)
    return prices


def activity_metrics(
    rows: list[orm.RequestLogORM], span_minutes: float
) -> dict[str, Any]:
    """Requests per minute over the distinct minutes that carried traffic."""
    per_minute: dict[int, int] = {}
    for row in rows:
        minute = int(row.created_at) // 60_000
        per_minute[minute] = per_minute.get(minute, 0) + 1
    active_minutes = len(per_minute)
    peak_rpm = max(per_minute.values()) if per_minute else 0
    return {
        "active_minutes": active_minutes,
        "peak_rpm": peak_rpm,
        "span_minutes": round(span_minutes, 2),
        "rpm": round(len(rows) / max(active_minutes, 1), 2),
    }



async def compute_log_summary(
    session: AsyncSession,
    start_ms: int,
    end_ms: int,
    *,
    channel: str = "",
    model: str = "",
    status: str = "",
    search: str = "",
    api_key_label: str = "",
    user_id: str = "",
    stream: str = "",
) -> dict[str, Any]:
    """Aggregates for the log page; tokens only come from real upstream usage."""
    rows = await repo.list_request_logs(
        session,
        start_ms=start_ms,
        end_ms=end_ms,
        channel=channel,
        model=model,
        status=status,
        search=search,
        api_key_label=api_key_label,
        user_id=user_id,
        stream=stream,
    )
    rows = await ledger_read.apply_overlay(session, rows)
    prices = await load_model_prices(session, rows)
    return summary_metrics(rows, start_ms=start_ms, end_ms=end_ms, prices=prices)


def summary_metrics(
    rows: list[orm.RequestLogORM],
    *,
    start_ms: int,
    end_ms: int,
    prices: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """The shared shape of a summary: tokens, cost, cache, rate and activity.

    The log page and the dashboard expose the same numbers, so both build them
    here; each caller adds only the fields specific to its view. The cache
    block carries the three rates the cache panel shows plus the money the hits
    saved: A token-weighted (cache_hit_rate), B request-level
    (cache_request_hit_rate), C cacheable share estimated from the session's
    prompt sequence (cacheable_ratio), D saved tokens and money
    (cache_saved_tokens / cache_saved_usd).
    """
    usage = usage_totals(rows)
    cacheable = cacheable_totals(rows)
    actual = activity_metrics(rows, (end_ms - start_ms) / 60_000)
    return {
        "requests": len(rows),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cached_tokens": usage.cached_tokens,
        "cost_usd": usage.cost_usd,
        "cache_hit_rate": cache_hit_rate(rows),
        "cache_hit_rate_samples": cache_hit_rate_samples(rows),
        "cache_request_hit_rate": cache_request_hit_rate(rows),
        "cache_request_hits": cache_request_hits(rows),
        "cacheable_ratio": cacheable.ratio,
        "cacheable_tokens": cacheable.tokens,
        "cacheable_sessions": cacheable.sessions,
        "cache_saved_tokens": usage.cached_tokens,
        "cache_saved_usd": cache_saved_usd(rows, prices),
        "active_minutes": actual["active_minutes"],
        "span_minutes": actual["span_minutes"],
        "rpm": actual["rpm"],
        "tpm": round(
            (usage.prompt_tokens + usage.completion_tokens)
            / max(actual["active_minutes"], 1),
            2,
        ),
        "peak_rpm": actual["peak_rpm"],
        "estimated_rows": len(rows) - len(usage.counted),
    }


async def compute_summary(
    session: AsyncSession,
    start_ms: int,
    end_ms: int,
    *,
    channel: str = "",
    model: str = "",
    status: str = "",
    source: str = "chat",
) -> dict[str, Any]:
    rows = await repo.list_request_logs(
        session,
        start_ms=start_ms,
        end_ms=end_ms,
        channel=channel,
        model=model,
        status=status,
        source=source,
    )
    rows = await ledger_read.apply_overlay(session, rows)
    prices = await load_model_prices(session, rows)
    metrics = summary_metrics(rows, start_ms=start_ms, end_ms=end_ms, prices=prices)
    errors = sum(1 for r in rows if int(r.http_status or 0) >= 400)
    latency = latency_percentiles(rows)
    metrics["errors"] = errors
    metrics["error_rate"] = (
        round(errors / metrics["requests"], 6) if metrics["requests"] else 0.0
    )
    # Kept as aliases for older clients; same cohort as ``latency``.
    metrics["p95_ttft_ms"] = latency["ttft"]["p95"] if latency["ttft"] else None
    metrics["p95_total_ms"] = latency["total"]["p95"] if latency["total"] else None
    metrics["latency"] = latency
    return metrics


def _day_start_ms(ts_ms: int) -> int:
    return ts_ms - (ts_ms % 86_400_000)


async def refresh_daily_aggregates(
    session: AsyncSession, start_ms: int, end_ms: int
) -> int:
    """Rebuild daily_aggregates for whole UTC days intersecting the range."""
    first_day = _day_start_ms(start_ms)
    last_day = _day_start_ms(max(start_ms, end_ms - 1))
    days = list(range(first_day, last_day + 86_400_000, 86_400_000))
    if not days:
        return 0
    rows = await repo.list_request_logs(
        session, start_ms=days[0], end_ms=days[-1] + 86_400_000, source="chat"
    )
    rows = await ledger_read.apply_overlay(session, rows)
    buckets: dict[tuple[int, str | None, str], list[orm.RequestLogORM]] = {}
    entries: list[dict[str, Any]] = []
    for row in rows:
        key = (
            _day_start_ms(int(row.created_at)),
            row.final_channel_model_id,
            row.requested_model or "",
        )
        buckets.setdefault(key, []).append(row)
    for index, ((day_ms, channel_id, model_key), items) in enumerate(sorted(buckets.items())):
        usage = usage_totals(items)
        latency = latency_percentiles(items)
        ttft_p95 = latency["ttft"]["p95"] if latency["ttft"] else 0
        total_p95 = latency["total"]["p95"] if latency["total"] else 0
        entries.append(
            {
                "id": f"agg_{day_ms}_{index}",
                "day_ms": day_ms,
                "channel_id": channel_id,
                "model_key": model_key,
                "requests": len(items),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_tokens": usage.cached_tokens,
                "cost_usd": usage.cost_usd,
                "errors": sum(1 for r in items if int(r.http_status or 0) >= 400),
                "ttft_p95_ms": ttft_p95,
                "total_p95_ms": total_p95,
            }
        )
    await repo.replace_daily_aggregates(session, days, entries)
    return len(buckets)
