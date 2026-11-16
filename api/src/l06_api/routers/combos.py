from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from l05_service import combos as combo_service
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import ComboCreate, ComboList, ComboOut, ComboUpdate

router = APIRouter(
    prefix="/api/combos",
    tags=["combos"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=ComboList)
async def list_combos(session: AsyncSession = Depends(get_db)) -> ComboList:
    items = await combo_service.list_combos(session)
    return ComboList(items=items, total=len(items))


@router.post("", response_model=ComboOut, status_code=201)
async def create_combo(
    payload: ComboCreate, session: AsyncSession = Depends(get_db)
) -> ComboOut:
    combo = await combo_service.create_combo(session, payload.model_dump())
    return ComboOut(**await combo_service.serialize_combo(session, combo))


@router.get("/{combo_id}", response_model=ComboOut)
async def get_combo(combo_id: str, session: AsyncSession = Depends(get_db)) -> ComboOut:
    combo = await combo_service.get_combo_or_404(session, combo_id)
    return ComboOut(**await combo_service.serialize_combo(session, combo))


@router.patch("/{combo_id}", response_model=ComboOut)
async def update_combo(
    combo_id: str,
    payload: ComboUpdate,
    session: AsyncSession = Depends(get_db),
) -> ComboOut:
    combo = await combo_service.get_combo_or_404(session, combo_id)
    updated = await combo_service.update_combo(
        session, combo, payload.model_dump(), payload.model_fields_set
    )
    return ComboOut(**await combo_service.serialize_combo(session, updated))


@router.delete("/{combo_id}", status_code=204)
async def delete_combo(combo_id: str, session: AsyncSession = Depends(get_db)) -> Response:
    combo = await combo_service.get_combo_or_404(session, combo_id)
    await combo_service.delete_combo(session, combo)
    return Response(status_code=204)
