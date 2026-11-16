from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from l05_service import catalog
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import CatalogEntries, CatalogEntry

router = APIRouter(
    prefix="/api/catalog",
    tags=["catalog"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=CatalogEntries)
async def list_catalog(
    request: Request,
    session: AsyncSession = Depends(get_db),
    include_disabled: bool = Query(
        default=False,
        description="同时列出已封禁（及冷却中）的直连条目（模型管理页用；调用面默认只看可调用模型）。",
    ),
) -> CatalogEntries:
    # Lazy trigger (ADR-073 decision 2 / D3): the directory read is the only
    # place that pulls a fresh snapshot. Failures never break this read.
    service = getattr(request.app.state, "model_defaults", None)
    if service is not None:
        await service.ensure_fresh(session)
    items = await catalog.list_catalog_entries(session, include_disabled=include_disabled)
    return CatalogEntries(items=[CatalogEntry(**item) for item in items], total=len(items))
