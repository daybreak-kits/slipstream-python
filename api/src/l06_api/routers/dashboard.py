from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.config import get_settings
from l05_service import aggregates, flows as flows_service, timeseries as timeseries_service
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import DashboardFlows, DashboardSummary, DashboardTimeseries

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    range: str = Query(default="today", pattern="^(today|week|24h|7d|30d|custom)$"),
    tz: str | None = Query(default=None),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=0),
    channel: str = Query(default=""),
    model: str = Query(default=""),
    status: str = Query(default="", pattern="^(success|error)?$"),
    session: AsyncSession = Depends(get_db),
) -> DashboardSummary:
    timezone = tz or get_settings().default_timezone
    start, end, _ = timeseries_service.range_window_ms(range, timezone, start_ms, end_ms)
    data = await aggregates.compute_summary(
        session, start, end, channel=channel, model=model, status=status, source="chat"
    )
    return DashboardSummary(**data, start_ms=start, end_ms=end)


@router.post("/rebuild")
async def rebuild(
    start_ms: int = Query(ge=0),
    end_ms: int = Query(ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    days = await aggregates.refresh_daily_aggregates(session, start_ms, end_ms)
    return {"days": days}


@router.get("/timeseries", response_model=DashboardTimeseries)
async def timeseries(
    range: str = Query(default="today", pattern="^(today|week|24h|7d|30d|custom)$"),
    tz: str | None = Query(default=None),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=0),
    channel: str = Query(default=""),
    model: str = Query(default=""),
    status: str = Query(default="", pattern="^(success|error)?$"),
    dimension: str = Query(default="", pattern="^(|combo|upstream|channel)$"),
    session: AsyncSession = Depends(get_db),
) -> DashboardTimeseries:
    data = await timeseries_service.compute_timeseries(
        session,
        range_key=range,
        tz_name=tz or get_settings().default_timezone,
        start_ms=start_ms,
        end_ms=end_ms,
        channel=channel,
        model=model,
        status=status,
        source="chat",
    )
    # task-121：前端按 tab 带 dimension；缺省=全量（旧前端兼容），给定时
    # 每个桶只保留选中维度的 by_*，其余置空——键与字段形状保持前端契约不变。
    keep = {"combo": "by_model", "upstream": "by_upstream_model", "channel": "by_channel"}.get(dimension)
    if keep is not None:
        for point in data["points"]:
            for field in ("by_model", "by_upstream_model", "by_channel"):
                if field != keep:
                    point[field] = []
    return DashboardTimeseries(**data)


@router.get("/flows", response_model=DashboardFlows)
async def flows(
    range: str = Query(default="today", pattern="^(today|week|24h|7d|30d|custom)$"),
    tz: str | None = Query(default=None),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=0),
    metric: str = Query(default="requests", pattern="^(requests|tokens)$"),
    session: AsyncSession = Depends(get_db),
) -> DashboardFlows:
    # 分流图只吃时间窗口（与 /summary、/timeseries 同一套 range/tz 解析），
    # 不做渠道/模型过滤：五级链路本身就是按这些维度展开的。
    timezone = tz or get_settings().default_timezone
    start, end, _ = timeseries_service.range_window_ms(range, timezone, start_ms, end_ms)
    data = await flows_service.compute_flows(
        session, start_ms=start, end_ms=end, metric=metric
    )
    return DashboardFlows(**data)
