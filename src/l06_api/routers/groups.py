from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from l05_service import groups as group_service
from l06_api.deps import get_current_admin, get_db

router = APIRouter(
    prefix="/api/groups",
    tags=["groups"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("")
async def list_implicit_groups(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Read-only, derived view of same-named bare ``call_model`` rows."""
    items = [
        group_service.serialize_group(group)
        for group in await group_service.list_groups(session)
    ]
    return {"items": items, "total": len(items)}
