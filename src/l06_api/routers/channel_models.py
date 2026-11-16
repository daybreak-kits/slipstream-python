from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError

from l05_service import channel_models as model_service
from l05_service import probe
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import (
    ChannelModelBatchResult,
    ChannelModelBatchUpdate,
    ChannelModelOut,
    ChannelModelUpdate,
    ModelDefaultsAdoptOut,
    ModelDefaultsAdoptRequest,
    ModelPrefillOut,
    ModelPrefillRequest,
    ProbeRequest,
    ProbeResult,
)

router = APIRouter(
    prefix="/api/channel-models",
    tags=["channel-models"],
    dependencies=[Depends(get_current_admin)],
)


@router.patch("/batch", response_model=ChannelModelBatchResult)
async def batch_update_channel_models(
    payload: ChannelModelBatchUpdate,
    session: AsyncSession = Depends(get_db),
) -> ChannelModelBatchResult:
    """One op over many rows; registered before /{model_id} on purpose."""
    result = await model_service.batch_update_channel_models(
        session,
        payload.model_ids,
        payload.op,
        banned_until=payload.banned_until,
        schedule=payload.schedule.model_dump(exclude_none=True) if payload.schedule else None,
    )
    return ChannelModelBatchResult(**result)


@router.post("/prefill", response_model=ModelPrefillOut)
async def prefill_channel_model(
    payload: ModelPrefillRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ModelPrefillOut:
    """Default values for a new channel model (ADR-073 decision 7).

    Only reads the pulled snapshot: it never triggers a refresh and never
    fails, so creating a model is never blocked by the sync.
    """
    service = getattr(request.app.state, "model_defaults", None)
    if service is None:
        return ModelPrefillOut(upstream_model=payload.upstream_model)
    result = await service.prefill(session, payload.upstream_model)
    return ModelPrefillOut(**result)

@router.patch("/{model_id}", response_model=ChannelModelOut)
async def update_channel_model(
    model_id: str,
    payload: ChannelModelUpdate,
    session: AsyncSession = Depends(get_db),
) -> ChannelModelOut:
    model = await model_service.get_channel_model_or_404(session, model_id)
    updated = await model_service.update_channel_model(
        session,
        model,
        payload.model_dump(exclude_unset=True),
        payload.model_fields_set,
    )
    return ChannelModelOut(**model_service.serialize_channel_model(updated))


@router.post("/{model_id}/reset", response_model=ChannelModelOut)
async def reset_channel_model(
    model_id: str, session: AsyncSession = Depends(get_db)
) -> ChannelModelOut:
    """Manually clear a circuit breaker (open or fault) back to normal."""
    model = await model_service.get_channel_model_or_404(session, model_id)
    reset = await model_service.reset_breaker(session, model)
    return ChannelModelOut(**model_service.serialize_channel_model(reset))


@router.post("/{model_id}/adopt-defaults", response_model=ModelDefaultsAdoptOut)
async def adopt_channel_model_defaults(
    model_id: str,
    request: Request,
    payload: ModelDefaultsAdoptRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> ModelDefaultsAdoptOut:
    """Restore auto values (ADR-073 decision 6): value-compare against the
    derived-table baseline; force=True adopts every pullable field."""
    service = getattr(request.app.state, "model_defaults", None)
    if service is None:
        raise AppError("INTERNAL", "模型默认值同步未启用", status=503)
    model = await model_service.get_channel_model_or_404(session, model_id)
    force = bool(payload.force) if payload is not None else False
    result = await service.adopt_defaults(session, model, force=force)
    return ModelDefaultsAdoptOut(**result)


@router.post("/{model_id}/probe-default", response_model=ChannelModelOut)
async def set_default_probe_model(
    model_id: str, session: AsyncSession = Depends(get_db)
) -> ChannelModelOut:
    """Pick the model this channel uses as its default probe target."""
    model = await model_service.get_channel_model_or_404(session, model_id)
    updated = await model_service.set_default_probe_model(session, model)
    return ChannelModelOut(**model_service.serialize_channel_model(updated))


@router.delete("/{model_id}", status_code=204)
async def delete_channel_model(
    model_id: str, session: AsyncSession = Depends(get_db)
) -> Response:
    model = await model_service.get_channel_model_or_404(session, model_id)
    await model_service.delete_channel_model(session, model)
    return Response(status_code=204)


@router.post("/{model_id}/probe", response_model=ProbeResult)
async def probe_channel_model(
    model_id: str,
    payload: ProbeRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> ProbeResult:
    model = await model_service.get_channel_model_or_404(session, model_id)
    channel = await model_service.get_channel_or_404(session, model.channel_id)
    mode = payload.mode if payload is not None else "deep"
    force = payload.force if payload is not None else False
    result = await probe.probe_model(session, channel, model, mode, force=force)
    return ProbeResult(**result)
