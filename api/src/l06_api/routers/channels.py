from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from l03_domain.model_types import infer_model_type_source
from l04_repo import orm
from l05_service import channel_models as model_service
from l05_service import channels as channel_service
from l05_service import probe
from l05_service import probe_cache
from l05_service import rewrite as rewrite_service
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import (
    ChannelCreate,
    ChannelDiscoverRequest,
    ChannelDiscoverResult,
    ChannelDraftTestRequest,
    ChannelKeyInput,
    ChannelKeyList,
    ChannelKeyOut,
    ChannelKeyUpdate,
    ChannelList,
    ChannelModelBulkCreate,
    ChannelModelBulkResult,
    ChannelModelCreate,
    ChannelModelList,
    ChannelModelOut,
    ChannelModelUpdate,
    ChannelOut,
    ChannelUpdate,
    ConnectionTestResult,
    DiscoveredModel,
    ProbeAllResult,
    ProbeRequest,
    ProbeResult,
    RewritePreviewRequest,
    RewritePreviewResult,
)

router = APIRouter(
    prefix="/api/channels",
    tags=["channels"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=ChannelList)
async def list_channels(
    search: str = Query(default="", max_length=200),
    status: str = Query(default="", max_length=16),
    session: AsyncSession = Depends(get_db),
) -> ChannelList:
    items = await channel_service.list_channels(session, search=search, status=status)
    return ChannelList(items=items, total=len(items))


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate, session: AsyncSession = Depends(get_db)
) -> ChannelOut:
    data = payload.model_dump()
    if "protocol" not in payload.model_fields_set:
        # Only an explicit choice is manual; otherwise the protocol stays auto.
        data["protocol"] = None
    channel = await channel_service.create_channel(session, data)
    return ChannelOut(**await channel_service.serialize_channel_with_count(session, channel))


@router.post("/test-connection", response_model=ConnectionTestResult)
async def test_draft_connection(
    payload: ChannelDraftTestRequest, session: AsyncSession = Depends(get_db)
) -> ConnectionTestResult:
    existing: orm.ChannelORM | None = None
    if payload.channel_id:
        existing = await channel_service.get_channel_or_404(session, payload.channel_id)
    draft = channel_service.draft_probe_channel(
        existing,
        connection_url=payload.connection_url,
        api_key=payload.api_key,
        protocol=payload.protocol,
        rewrite_rules=payload.rewrite_rules,
    )
    result = await probe.test_connection(session, draft, persist=False)
    if existing is not None:
        # Draft probes run on a transient ORM object, so ``persist=True`` cannot
        # write the verdict back to the stored row. The service owns the writeback;
        # the router only passes the result through.
        await channel_service.record_connection_result(
            session,
            existing,
            status=str(result["status"]),
            latency_ms=int(result["latency_ms"]),
            message=str(result["message"]),
            tested_at=result["tested_at"],
        )
    return ConnectionTestResult(**result)


@router.post("/discover-models", response_model=ChannelDiscoverResult)
async def discover_models(
    payload: ChannelDiscoverRequest, session: AsyncSession = Depends(get_db)
) -> ChannelDiscoverResult:
    existing: orm.ChannelORM | None = None
    if payload.channel_id:
        existing = await channel_service.get_channel_or_404(session, payload.channel_id)
    draft = channel_service.draft_probe_channel(
        existing,
        connection_url=payload.connection_url,
        api_key=payload.api_key,
        protocol=payload.protocol,
        rewrite_rules=payload.rewrite_rules,
    )
    result = await probe.discover_models(draft, probe.probe_timeout_seconds())
    already_added: set[str] = set()
    if existing is not None:
        stored = await model_service.list_channel_models(session, existing.id)
        already_added = {item["upstream_model"] for item in stored}
    discovered: list[DiscoveredModel] = []
    for name in result["models"]:
        model_type, model_type_source = infer_model_type_source(name)
        discovered.append(
            DiscoveredModel(
                upstream_model=name,
                already_added=name in already_added,
                model_type=model_type,
                model_type_source=model_type_source,
            )
        )
    return ChannelDiscoverResult(
        status=result["status"],
        models=discovered,
        protocol=result["protocol"],
        auth_style=result["auth_style"],
        http_status=result["http_status"],
        latency_ms=result["latency_ms"],
        message=result["message"],
    )


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(
    channel_id: str, session: AsyncSession = Depends(get_db)
) -> ChannelOut:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    return ChannelOut(**await channel_service.serialize_channel_with_count(session, channel))


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str,
    payload: ChannelUpdate,
    session: AsyncSession = Depends(get_db),
) -> ChannelOut:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    updated = await channel_service.update_channel(
        session, channel, payload.model_dump(), payload.model_fields_set
    )
    return ChannelOut(**await channel_service.serialize_channel_with_count(session, updated))


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: str, session: AsyncSession = Depends(get_db)
) -> Response:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    await channel_service.delete_channel(session, channel)
    return Response(status_code=204)


@router.get("/{channel_id}/keys", response_model=ChannelKeyList)
async def list_keys(
    channel_id: str, session: AsyncSession = Depends(get_db)
) -> ChannelKeyList:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    items = await channel_service.list_channel_keys(session, channel)
    return ChannelKeyList(items=[ChannelKeyOut(**item) for item in items], total=len(items))


@router.post("/{channel_id}/keys", response_model=ChannelKeyOut, status_code=201)
async def create_key(
    channel_id: str,
    payload: ChannelKeyInput,
    session: AsyncSession = Depends(get_db),
) -> ChannelKeyOut:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    key = await channel_service.add_channel_key(
        session, channel, label=payload.label, api_key=payload.api_key
    )
    return ChannelKeyOut(**channel_service.serialize_channel_key(key))


@router.delete("/{channel_id}/keys/{key_id}", status_code=204)
async def delete_key(
    channel_id: str, key_id: str, session: AsyncSession = Depends(get_db)
) -> Response:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    await channel_service.delete_channel_key(session, channel, key_id)
    return Response(status_code=204)


@router.patch("/{channel_id}/keys/{key_id}", response_model=ChannelKeyOut)
async def update_key(
    channel_id: str,
    key_id: str,
    payload: ChannelKeyUpdate,
    session: AsyncSession = Depends(get_db),
) -> ChannelKeyOut:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    key = await channel_service.update_channel_key(
        session, channel, key_id, label=payload.label, enabled=payload.enabled
    )
    return ChannelKeyOut(**channel_service.serialize_channel_key(key))


@router.get("/{channel_id}/models", response_model=ChannelModelList)
async def list_channel_models(
    channel_id: str, session: AsyncSession = Depends(get_db)
) -> ChannelModelList:
    items = await model_service.list_channel_models(session, channel_id)
    return ChannelModelList(items=items, total=len(items))


@router.post("/{channel_id}/models", response_model=ChannelModelOut, status_code=201)
async def create_channel_model(
    channel_id: str,
    payload: ChannelModelCreate,
    session: AsyncSession = Depends(get_db),
) -> ChannelModelOut:
    model = await model_service.create_channel_model(
        session, channel_id, payload.model_dump()
    )
    return ChannelModelOut(**model_service.serialize_channel_model(model))


@router.post("/{channel_id}/models/bulk", response_model=ChannelModelBulkResult)
async def bulk_create_channel_models(
    channel_id: str,
    payload: ChannelModelBulkCreate,
    session: AsyncSession = Depends(get_db),
) -> ChannelModelBulkResult:
    result = await model_service.bulk_create_channel_models(
        session, channel_id, payload.upstream_models
    )
    return ChannelModelBulkResult(**result)


@router.post("/{channel_id}/probe-all", response_model=ProbeAllResult)
async def probe_all_channel_models(
    channel_id: str,
    payload: ProbeRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> ProbeAllResult:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    mode = payload.mode if payload is not None else "deep"
    force = payload.force if payload is not None else False
    result = await probe_cache.probe_all_models_cached(
        session, channel, mode, force=force
    )
    return ProbeAllResult(**result)


@router.post("/{channel_id}/rewrite-preview", response_model=RewritePreviewResult)
async def preview_rewrite_rules(
    channel_id: str,
    payload: RewritePreviewRequest,
    session: AsyncSession = Depends(get_db),
) -> RewritePreviewResult:
    channel = await channel_service.get_channel_or_404(session, channel_id)
    result = rewrite_service.preview_rules(
        channel,
        section=payload.section,
        rules_override=payload.rules,
        body=payload.body,
        headers=payload.headers,
    )
    return RewritePreviewResult(**result)


@router.post("/{channel_id}/test-connection", response_model=ConnectionTestResult)
async def test_connection(
    channel_id: str, session: AsyncSession = Depends(get_db)
) -> ConnectionTestResult:
    channel: orm.ChannelORM = await channel_service.get_channel_or_404(session, channel_id)
    result = await probe.test_connection(session, channel)
    return ConnectionTestResult(**result)
