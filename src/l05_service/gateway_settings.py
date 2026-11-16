from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode
from l01_core.timeutil import now_ms
from l04_repo import orm, repo

GATEWAY_SETTING_KEY = "gateway"
MAX_GATEWAY_URL_LENGTH = 512


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    base_url: str = ""
    updated_at: int | None = None
    updated_by: str = ""


def normalize_gateway_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if len(raw) > MAX_GATEWAY_URL_LENGTH:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"网关地址最长 {MAX_GATEWAY_URL_LENGTH} 个字符",
        )
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "网关地址必须是 http:// 或 https:// 开头的完整地址",
        )
    if parsed.username or parsed.password:
        raise AppError(ErrorCode.INVALID_REQUEST, "网关地址不能包含账号密码")
    if parsed.query or parsed.fragment:
        raise AppError(ErrorCode.INVALID_REQUEST, "网关地址不能包含查询参数或锚点")
    return raw


def gateway_settings_from_json(
    payload: dict[str, Any] | None,
    *,
    updated_at: int | None = None,
    updated_by: str = "",
) -> GatewaySettings:
    data = payload if isinstance(payload, dict) else {}
    try:
        base_url = normalize_gateway_base_url(data.get("base_url"))
    except AppError:
        base_url = ""
    return GatewaySettings(
        base_url=base_url,
        updated_at=updated_at,
        updated_by=updated_by or "",
    )


async def get_gateway_settings(session: AsyncSession) -> GatewaySettings:
    row = await repo.get_app_setting(session, GATEWAY_SETTING_KEY)
    if row is None:
        return GatewaySettings()
    try:
        payload = json.loads(row.value_json or "{}")
    except Exception:
        payload = {}
    return gateway_settings_from_json(
        payload if isinstance(payload, dict) else {},
        updated_at=int(row.updated_at) if row.updated_at is not None else None,
        updated_by=row.updated_by or "",
    )


async def save_gateway_settings(
    session: AsyncSession, *, base_url: Any, updated_by: str
) -> GatewaySettings:
    normalized = normalize_gateway_base_url(base_url)
    current = await get_gateway_settings(session)
    next_settings = replace(
        current,
        base_url=normalized,
        updated_at=now_ms(),
        updated_by=updated_by or "",
    )
    payload = json.dumps({"base_url": next_settings.base_url}, ensure_ascii=False)
    row = await repo.get_app_setting(session, GATEWAY_SETTING_KEY)
    if row is None:
        row = orm.AppSettingORM(
            key=GATEWAY_SETTING_KEY,
            value_json=payload,
            updated_at=next_settings.updated_at,
            updated_by=next_settings.updated_by,
        )
    else:
        row.value_json = payload
        row.updated_at = next_settings.updated_at
        row.updated_by = next_settings.updated_by
    await repo.save_app_setting(session, row)
    return next_settings
