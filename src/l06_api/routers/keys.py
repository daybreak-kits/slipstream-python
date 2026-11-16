from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from l05_service import keys as key_service
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import KeyCreate, KeyCreatedOut, KeyList, KeyOut, KeyUpdate

router = APIRouter(
    prefix="/api/keys",
    tags=["keys"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=KeyList)
async def list_keys(session: AsyncSession = Depends(get_db)) -> KeyList:
    items = await key_service.list_api_keys(session)
    return KeyList(items=[KeyOut(**item) for item in items], total=len(items))


@router.post("", response_model=KeyCreatedOut, status_code=201)
async def create_key(
    payload: KeyCreate, session: AsyncSession = Depends(get_db)
) -> KeyCreatedOut:
    key, plaintext = await key_service.create_api_key(session, payload.label)
    return KeyCreatedOut(**key_service.serialize_api_key(key), key=plaintext)


@router.patch("/{key_id}", response_model=KeyOut)
async def update_key(
    key_id: str, payload: KeyUpdate, session: AsyncSession = Depends(get_db)
) -> KeyOut:
    item = await key_service.update_api_key(
        session, key_id, label=payload.label, enabled=payload.enabled
    )
    return KeyOut(**item)


@router.post("/{key_id}/reset", response_model=KeyCreatedOut)
async def reset_key(
    key_id: str, session: AsyncSession = Depends(get_db)
) -> KeyCreatedOut:
    key, plaintext = await key_service.reset_api_key(session, key_id)
    return KeyCreatedOut(**key_service.serialize_api_key(key), key=plaintext)


@router.get("/{key_id}/secret")
async def reveal_key(
    key_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    return {"key": await key_service.reveal_api_key(session, key_id)}


@router.delete("/{key_id}", status_code=204)
async def delete_key(key_id: str, session: AsyncSession = Depends(get_db)) -> None:
    await key_service.delete_api_key(session, key_id)
