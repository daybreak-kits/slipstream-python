from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode, OpenAIError
from l01_core.ids import key_prefix, new_api_key, new_id
from l02_infra.crypto import decrypt_secret, encrypt_secret, hash_api_key
from l04_repo import orm, repo
from l05_service.config_catalog import catalog_for_session

DEFAULT_USERNAME = "default"
DEFAULT_LABEL = "default"


def serialize_api_key(key: orm.ApiKeyORM) -> dict[str, object]:
    return {
        "id": key.id,
        "label": key.label,
        "prefix": key.key_prefix,
        "enabled": bool(key.enabled),
        "last_used_at": key.last_used_at,
        "created_at": key.created_at,
    }


async def ensure_default_user(session: AsyncSession) -> orm.UserORM:
    user = await repo.get_user_by_username(session, DEFAULT_USERNAME)
    if user is not None:
        return user
    try:
        return await repo.create_user(session, DEFAULT_USERNAME, "默认用户")
    except IntegrityError:
        await session.rollback()
        user = await repo.get_user_by_username(session, DEFAULT_USERNAME)
        if user is None:
            raise
        return user


async def create_api_key(
    session: AsyncSession, label: str | None = None
) -> tuple[orm.ApiKeyORM, str]:
    """Create a key and return ``(row, plaintext)``; the plaintext is stored
    encrypted (``key_enc``) so admins can re-copy it from the UI."""
    user = await ensure_default_user(session)
    clean_label = (label or "").strip() or DEFAULT_LABEL
    if len(clean_label) > 64:
        raise AppError(ErrorCode.INVALID_REQUEST, "密钥标识最长 64 位", status=400)
    if await repo.get_api_key_by_label(session, user.id, clean_label) is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            f"密钥标识 {clean_label} 已存在，请换一个标识",
            status=409,
        )

    plaintext = new_api_key()
    key = orm.ApiKeyORM(
        id=new_id(),
        user_id=user.id,
        label=clean_label,
        key_prefix=key_prefix(plaintext),
        key_hash=hash_api_key(plaintext),
        key_enc=encrypt_secret(plaintext),
        enabled=True,
    )
    try:
        key = await repo.save_api_key(session, key)
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            f"密钥标识 {clean_label} 已存在，请换一个标识",
            status=409,
        ) from exc
    return key, plaintext


async def list_api_keys(session: AsyncSession) -> list[dict[str, object]]:
    # last_used_at is buffered in memory on the request path; flush it so the
    # admin list reflects the chat that just completed.
    catalog = catalog_for_session(session)
    if catalog is not None:
        await catalog.flush_key_touches()
    return [serialize_api_key(key) for key in await repo.list_api_keys(session)]


def invalid_api_key(message: str = "API 密钥无效或已封禁") -> OpenAIError:
    return OpenAIError(401, message, ErrorCode.INVALID_API_KEY)


async def authenticate_api_key(
    session: AsyncSession, raw_key: str | None
) -> tuple[orm.ApiKeyORM, orm.UserORM]:
    """Resolve a Bearer user key or raise an OpenAI-style 401."""
    token = (raw_key or "").strip()
    if not token:
        raise invalid_api_key("缺少 API 密钥")
    key_hash = hash_api_key(token)
    catalog = catalog_for_session(session)
    if catalog is not None:
        await catalog.ensure_fresh()
    resolved = None
    if catalog is not None and catalog.snapshot is not None:
        resolved = catalog.lookup_api_key(key_hash)
    if resolved is None:
        # Unknown key: fall back to SQL once so a key created after the last
        # poll (or a revoked key whose row is gone) gets the exact DB answer.
        resolved = await repo.get_api_key_with_user_by_hash(session, key_hash)
    if resolved is None:
        raise invalid_api_key()
    key, user = resolved
    if not key.enabled:
        raise invalid_api_key()
    if catalog is not None and catalog.snapshot is not None:
        # Telemetry only: the poll loop batches last_used_at into SQL.
        catalog.touch_api_key(key)
    else:
        await repo.touch_api_key(session, key)
    return key, user


async def update_api_key(
    session: AsyncSession,
    key_id: str,
    *,
    label: str | None = None,
    enabled: bool | None = None,
) -> dict[str, object]:
    key = await repo.get_api_key(session, key_id)
    if key is None:
        raise AppError(ErrorCode.NOT_FOUND, "密钥不存在", status=404)
    if label is not None:
        clean = label.strip() or DEFAULT_LABEL
        if len(clean) > 64:
            raise AppError(ErrorCode.INVALID_REQUEST, "密钥标识最长 64 位", status=400)
        existing = await repo.get_api_key_by_label(session, key.user_id, clean)
        if existing is not None and existing.id != key.id:
            raise AppError(ErrorCode.CONFLICT, f"密钥标识 {clean} 已存在，请换一个标识", status=409)
        key.label = clean
    if enabled is not None:
        key.enabled = bool(enabled)
    key = await repo.save_api_key(session, key)
    return serialize_api_key(key)


async def reset_api_key(session: AsyncSession, key_id: str) -> tuple[orm.ApiKeyORM, str]:
    """Rotate a key: mint a new secret, keep the row; plaintext is returned once."""
    key = await repo.get_api_key(session, key_id)
    if key is None:
        raise AppError(ErrorCode.NOT_FOUND, "密钥不存在", status=404)
    plaintext = new_api_key()
    key.key_prefix = key_prefix(plaintext)
    key.key_hash = hash_api_key(plaintext)
    key.key_enc = encrypt_secret(plaintext)
    key.last_used_at = None
    key = await repo.save_api_key(session, key)
    return key, plaintext


async def reveal_api_key(session: AsyncSession, key_id: str) -> str:
    """Return a key's stored plaintext; legacy hash-only rows cannot be revealed."""
    key = await repo.get_api_key(session, key_id)
    if key is None:
        raise AppError(ErrorCode.NOT_FOUND, "密钥不存在", status=404)
    if not key.key_enc:
        raise AppError(
            ErrorCode.CONFLICT,
            "该密钥创建于旧版本，未保留明文，请重置密钥后复制新密钥",
            status=409,
        )
    return decrypt_secret(key.key_enc)


async def delete_api_key(session: AsyncSession, key_id: str) -> None:
    key = await repo.get_api_key(session, key_id)
    if key is None:
        raise AppError(ErrorCode.NOT_FOUND, "密钥不存在", status=404)
    await repo.delete_api_key(session, key)
