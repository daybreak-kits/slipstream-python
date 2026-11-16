"""models.dev derived defaults: status and manual refresh (ADR-073).

The lazy trigger lives on the model directory read (routers/catalog.py); this
router only exposes what an administrator needs to see and to force a refresh.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError
from l05_service.model_defaults import ModelDefaultsService
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import ModelDefaultsRefreshOut, ModelDefaultsStatusOut

router = APIRouter(
    prefix="/api/model-defaults",
    tags=["model-defaults"],
    dependencies=[Depends(get_current_admin)],
)


def _service(request: Request) -> ModelDefaultsService:
    service = getattr(request.app.state, "model_defaults", None)
    if service is None:
        raise AppError("INTERNAL", "模型默认值同步未启用", status=503)
    return service


@router.get("/status", response_model=ModelDefaultsStatusOut)
async def model_defaults_status(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ModelDefaultsStatusOut:
    """Sync status: today's attempt, snapshot date, staleness, derived rows."""
    service = _service(request)
    return ModelDefaultsStatusOut(**await service.status(session))


@router.post("/refresh", response_model=ModelDefaultsRefreshOut)
async def model_defaults_refresh(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ModelDefaultsRefreshOut:
    """Force a pull (the "refresh now" button); same code path as the lazy trigger."""
    service = _service(request)
    outcome = await service.refresh(session, trigger="manual", force=True)
    return ModelDefaultsRefreshOut(**outcome.as_dict())
