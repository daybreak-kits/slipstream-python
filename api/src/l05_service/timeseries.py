from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.textfold import fold_key
from l04_repo import orm, repo
from l05_service import ledger_read
from l05_service.aggregates import (
    MIN_PERCENTILE_SAMPLES,
    cache_hit_rate,
    latency_percentiles,
    paired_rows,
    percentile,
    usage_totals,
)
from l05_service.channel_axis import channel_axis, model_axis


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def range_window_ms(
    range_key: str,
    tz_name: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    now: datetime | None = None,
) -> tuple[int, int, str]:
    """Resolve a range key into UTC ms boundaries plus bucket granularity.

    ``today`` starts at local midnight in the caller's IANA timezone. Explicit
    start/end values win over the range key, and ``custom`` derives granularity
    from the width of the explicit window.
    """
    tz = _tz(tz_name)
    current = now or datetime.now(timezone.utc)
    local_now = current.astimezone(tz)
    if range_key == "today":
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        default_start = int(start_local.timestamp() * 1000)
        granularity = "hour"
    elif range_key == "week":
        monday = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=(local_now.weekday())
        )
        default_start = int(monday.timestamp() * 1000)
        granularity = "hour"
    elif range_key == "7d":
        default_start = int((current - timedelta(days=7)).timestamp() * 1000)
        granularity = "day"
    elif range_key == "30d":
        default_start = int((current - timedelta(days=30)).timestamp() * 1000)
        granularity = "day"
    else:
        default_start = int((current - timedelta(hours=24)).timestamp() * 1000)
        granularity = "hour"

    start = start_ms if start_ms is not None else default_start
    end = end_ms if end_ms is not None else int(current.timestamp() * 1000)
    if range_key == "custom" and start_ms is not None and end_ms is not None:
        granularity = "hour" if end - start <= 2 * 86_400_000 else "day"
    return start, end, granularity


def _bucket_start(local: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _by_dimension(
    items: list[orm.RequestLogORM],
    field: str,
    fallback: str,
    resolver: Callable[[Any], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate one dimension; resolver (when given) owns identity and label.

    resolver 是轴口径的唯一入口（ADR-071 决策 14 最终裁定）：渠道轴给
    channel_id、上游模型轴给 channel-model id、综合模型轴给 combo id。
    不传 resolver 时按 fold_key(getattr(row, field)) 折叠分组。
    """
    groups: dict[str, list[orm.RequestLogORM]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in items:
        if resolver is not None:
            key, name = resolver(row)
        else:
            raw = getattr(row, field) or fallback
            key = fold_key(raw)
            name = str(raw)
        labels.setdefault(key, name)
        groups[key].append(row)
    series: list[dict[str, Any]] = []
    for key, rows in sorted(
        groups.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        name = labels[key]
        totals = [int(row.total_ms) for row in paired_rows(rows)]
        usage = usage_totals(rows)
        series.append(
            {
                "name": name,
                "requests": len(rows),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_tokens": usage.cached_tokens,
                "cache_hit_rate": cache_hit_rate(rows),
                "cost_usd": usage.cost_usd,
                "errors": sum(1 for row in rows if int(row.http_status or 0) >= 400),
                "p95_total_ms": (
                    percentile(totals, 95)
                    if len(totals) >= MIN_PERCENTILE_SAMPLES
                    else None
                ),
            }
        )
    return series


def _by_model(
    items: list[orm.RequestLogORM],
    resolver: Callable[[Any], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    # 综合模型轴（裁定③）：手动组合 id → 直连 channel-model id → 按名回退。
    return _by_dimension(items, "requested_model", "(未命名)", resolver=resolver)


def _by_upstream_model(
    items: list[orm.RequestLogORM],
    resolver: Callable[[Any], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    # 上游模型轴（裁定②）：分组键 final_channel_model_id。空 upstream_model
    # 表示请求没有命中任何上游（路由失败），不能成为图例，归入 (未命名) 兜底，
    # 与 requested_model 维度的文案保持一致。
    return _by_dimension(items, "upstream_model", "(未命名)", resolver=resolver)


def _by_channel(
    items: list[orm.RequestLogORM],
    resolver: Callable[[Any], tuple[str, str]],
) -> list[dict[str, Any]]:
    # 空 channel_name 表示请求从未命中任何渠道，不是渠道名缺失；
    # 渠道轴按 channel_id 归并（ADR-071 决策 14），哨兵单独成桶。
    return _by_dimension(items, "channel_name", "请求模型无可用渠道", resolver=resolver)


def _named_totals(
    items: list[orm.RequestLogORM],
    field: str,
    fallback: str,
    resolver: Callable[[Any], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate request/token/cost totals per model or channel in one pass.

    Mirrors usage_totals: estimated rows still count as requests but never
    contribute tokens or money. Every name is returned (ADR-040); the frontend
    cycles its 16-colour palette instead of merging names into an other bucket.
    """
    stats: dict[str, dict[str, Any]] = {}
    for row in items:
        if resolver is not None:
            key, name = resolver(row)
        else:
            raw = getattr(row, field) or fallback
            key = fold_key(raw)
            name = str(raw)
        entry = stats.setdefault(
            key,
            {
                "name": name,
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        entry["requests"] += 1
        if not bool(row.usage_estimated):
            entry["prompt_tokens"] += int(row.prompt_tokens or 0)
            entry["completion_tokens"] += int(row.completion_tokens or 0)
            entry["cached_tokens"] += int(row.cached_tokens or 0)
            entry["cost_usd"] += float(row.cost_usd or 0)
    for entry in stats.values():
        entry["cost_usd"] = round(entry["cost_usd"], 8)
    return sorted(stats.values(), key=lambda item: (-item["requests"], item["name"]))


async def compute_timeseries(
    session: AsyncSession,
    *,
    range_key: str,
    tz_name: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    channel: str = "",
    model: str = "",
    status: str = "",
    source: str = "chat",
) -> dict[str, Any]:
    tz = _tz(tz_name)
    start, end, granularity = range_window_ms(range_key, tz_name, start_ms, end_ms)
    rows = await repo.list_request_logs(
        session,
        start_ms=start,
        end_ms=end,
        channel=channel,
        model=model,
        status=status,
        source=source,
    )
    rows = await ledger_read.apply_overlay(session, rows)
    # 渠道轴的身份解析一次做完：channel_model_id → channel_id → 当前渠道名。
    axis = await channel_axis(session, rows, fallback_label="请求模型无可用渠道")
    # 模型两轴同样一次解析（裁定②③）：上游轴按 channel-model id、综合轴按
    # combo id；实体缺失退回行内快照，历史 null 行按名回退、不回填。
    models = await model_axis(session, rows, fallback_label="(未命名)")

    buckets: dict[int, list[orm.RequestLogORM]] = defaultdict(list)
    for row in rows:
        local = datetime.fromtimestamp(int(row.created_at) / 1000, tz)
        bucket = _bucket_start(local, granularity)
        buckets[int(bucket.timestamp() * 1000)].append(row)

    points: list[dict[str, Any]] = []
    for bucket_ms in sorted(buckets):
        items = buckets[bucket_ms]
        latency = latency_percentiles(items)
        usage = usage_totals(items)
        points.append(
            {
                "start_ms": bucket_ms,
                "requests": len(items),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_tokens": usage.cached_tokens,
                "cache_hit_rate": cache_hit_rate(items),
                "cost_usd": usage.cost_usd,
                "errors": sum(1 for r in items if int(r.http_status or 0) >= 400),
                "p95_ttft_ms": latency["ttft"]["p95"] if latency["ttft"] else None,
                "p95_ttfa_ms": latency["ttfa"]["p95"] if latency["ttfa"] else None,
                "p95_ttlt_ms": latency["ttlt"]["p95"] if latency["ttlt"] else None,
                "p95_total_ms": latency["total"]["p95"] if latency["total"] else None,
                "by_model": _by_model(items, models.model_ident),
                "by_upstream_model": _by_upstream_model(items, models.upstream_ident),
                "by_channel": _by_channel(items, axis.ident_label),
            }
        )

    top_models = _named_totals(
        rows, "requested_model", "(未命名)", models.model_ident
    )
    # 上游模型维度：空 upstream_model 表示请求没有命中任何上游（路由失败）。
    top_upstream_models = _named_totals(
        rows, "upstream_model", "(未命名)", models.upstream_ident
    )
    # 路由失败、从未命中任何渠道的请求（503/NO_AVAILABLE_CHANNEL）写入空 channel_name，
    # 这是「一个渠道都没服务成功」，不是渠道名数据缺失。
    top_channels = _named_totals(
        rows, "channel_name", "请求模型无可用渠道", axis.ident_label
    )
    error_counts = Counter(
        (r.error_code or f"HTTP {r.http_status}") for r in rows if int(r.http_status or 0) >= 400
    )
    heatmap: Counter[tuple[int, int]] = Counter()
    for row in rows:
        local = datetime.fromtimestamp(int(row.created_at) / 1000, tz)
        heatmap[(local.isoweekday(), local.hour)] += 1

    return {
        "range": range_key,
        "timezone": tz_name,
        "granularity": granularity,
        "start_ms": start,
        "end_ms": end,
        "points": points,
        # Return every used name: the frontend cycles its 16-colour palette
        # (palette[i % 16]) and never merges names into an "其他" bucket
        # (ADR-040), so truncating here would silently drop traffic.
        "top_models": top_models,
        "top_upstream_models": top_upstream_models,
        "top_channels": top_channels,
        "error_distribution": [
            {"code": code, "count": count} for code, count in error_counts.most_common()
        ],
        "heatmap": [
            {"weekday": weekday, "hour": hour, "requests": requests}
            for (weekday, hour), requests in sorted(heatmap.items())
        ],
    }
