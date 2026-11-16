from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode
from l02_infra.tokens import create_access_token
from l04_repo import orm
from l05_service.auth import authenticate_admin, change_admin_password
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import AdminSummary, LoginRequest, LoginResponse, PasswordChange

router = APIRouter(prefix="/api/auth", tags=["auth"])


def admin_summary(admin: orm.AdminUserORM) -> AdminSummary:
    return AdminSummary(
        id=admin.id,
        username=admin.username,
        created_at=admin.created_at,
        updated_at=admin.updated_at,
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, session: AsyncSession = Depends(get_db)) -> LoginResponse:
    admin = await authenticate_admin(session, payload.username, payload.password)
    if admin is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "用户名或密码错误", status=401)

    token, expires_at = create_access_token(admin.id, admin.username, request.app.state.settings)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_at=expires_at,
        admin=admin_summary(admin),
    )


@router.get("/me", response_model=AdminSummary)
async def me(admin: orm.AdminUserORM = Depends(get_current_admin)) -> AdminSummary:
    return admin_summary(admin)


@router.post("/password")
async def change_password(
    payload: PasswordChange,
    admin: orm.AdminUserORM = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    if not await change_admin_password(session, admin, payload.current_password, payload.new_password):
        raise AppError(ErrorCode.UNAUTHORIZED, "当前密码不正确", status=401)
    return {"ok": True}
