from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode, OpenAIError
from l02_infra.tokens import TokenError, decode_access_token
from l04_repo import orm
from l05_service import keys as key_service

_bearer = HTTPBearer(auto_error=False)


@dataclass(slots=True)
class UserKeyContext:
    api_key: orm.ApiKeyORM
    user: orm.UserORM


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Session bound to the app instance's engine, so tests stay isolated."""
    async with request.app.state.session_factory() as session:
        if request.app.state.settings.zero_sql_pipeline:
            # Classic mode keeps the exact pre-catalog read path (SQL per
            # request); only memory mode attaches the in-process snapshot.
            session.info["catalog"] = getattr(request.app.state, "catalog", None)
        yield session


async def get_current_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db),
) -> orm.AdminUserORM:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(ErrorCode.UNAUTHORIZED, "未登录或登录已过期", status=401)
    try:
        payload = decode_access_token(credentials.credentials, request.app.state.settings)
    except TokenError as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "未登录或登录已过期", status=401) from exc

    admin = await session.get(orm.AdminUserORM, str(payload["sub"]))
    if admin is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "未登录或登录已过期", status=401)
    return admin


PLAYGROUND_KEY_LABEL = "playground"


def _playground_key(user: orm.UserORM) -> orm.ApiKeyORM:
    """Unpersisted stub so admin-console traffic logs under playground."""
    return orm.ApiKeyORM(
        id="",
        user_id=user.id,
        label=PLAYGROUND_KEY_LABEL,
        key_prefix="",
        key_hash="",
        enabled=True,
    )


async def get_user_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db),
) -> UserKeyContext:
    """Authenticate the public /v1 API.

    Normal traffic presents ``Authorization: Bearer sk-...``. The playground
    reuses the admin console JWT instead, so no API key ever reaches the
    browser; such requests still run the full gateway route and are logged
    with api_key_label=playground for troubleshooting.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise key_service.invalid_api_key("缺少 API 密钥")
    token = credentials.credentials
    try:
        api_key, user = await key_service.authenticate_api_key(session, token)
        return UserKeyContext(api_key=api_key, user=user)
    except OpenAIError:
        pass
    try:
        payload = decode_access_token(token, request.app.state.settings)
    except TokenError as exc:
        raise key_service.invalid_api_key() from exc
    admin = await session.get(orm.AdminUserORM, str(payload["sub"]))
    if admin is None:
        raise key_service.invalid_api_key()
    user = await key_service.ensure_default_user(session)
    return UserKeyContext(api_key=_playground_key(user), user=user)
