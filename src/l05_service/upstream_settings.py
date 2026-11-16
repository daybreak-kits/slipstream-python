from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.config import get_settings
from l01_core.errors import AppError, ErrorCode
from l01_core.timeutil import now_ms
from l04_repo import orm, repo

# DB-backed overrides for the three upstream watchdog timeouts (ADR-075).
# Env (UPSTREAM_*_TIMEOUT_S) provides the defaults; the admin settings page
# stores an override row in app_settings (key 'upstream_timeouts'). Effective
# value = DB row when present, env default otherwise.
UPSTREAM_TIMEOUT_SETTING_KEY = 'upstream_timeouts'
MAX_UPSTREAM_TIMEOUT_S = 86_400.0

# The request path never pays the app_settings read: these values are
# deployment-wide, so resolve them through a short-lived process cache.
# Boot warms it; save/reset refresh it immediately in-process, other workers
# catch up within one TTL. Keeps the zero-SQL pipeline invariant intact.
UPSTREAM_TIMEOUTS_CACHE_TTL_S = 15.0
_TIMEOUTS_CACHE: list[Any] = [None, 0.0]  # [resolved UpstreamTimeouts, loaded monotonic]


def _cache_get() -> UpstreamTimeouts | None:
    cached, loaded_at = _TIMEOUTS_CACHE
    if cached is None or time.monotonic() - loaded_at >= UPSTREAM_TIMEOUTS_CACHE_TTL_S:
        return None
    return cached


def _cache_put(value: UpstreamTimeouts) -> None:
    _TIMEOUTS_CACHE[0] = value
    _TIMEOUTS_CACHE[1] = time.monotonic()


def _cache_invalidate() -> None:
    _TIMEOUTS_CACHE[0] = None


@dataclass(frozen=True, slots=True)
class UpstreamTimeouts:
    connect_timeout_s: float
    first_byte_timeout_s: float
    read_timeout_s: float
    updated_at: int | None = None
    updated_by: str = ''
    is_default: bool = True


def default_upstream_timeouts() -> UpstreamTimeouts:
    settings = get_settings()
    return UpstreamTimeouts(
        connect_timeout_s=float(settings.upstream_connect_timeout_s),
        first_byte_timeout_s=float(settings.upstream_first_byte_timeout_s),
        read_timeout_s=float(settings.upstream_read_timeout_s),
    )


def normalize_timeout_value(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, f'{label} 必须是数字') from exc
    if not math.isfinite(number) or number <= 0 or number > MAX_UPSTREAM_TIMEOUT_S:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f'{label} 必须大于 0 且不超过 {int(MAX_UPSTREAM_TIMEOUT_S)} 秒',
        )
    return number


def _timeouts_from_json(
    payload: dict[str, Any],
    *,
    updated_at: int | None = None,
    updated_by: str = '',
) -> UpstreamTimeouts:
    defaults = default_upstream_timeouts()

    def pick(name: str) -> float:
        raw = payload.get(name)
        if raw is None:
            return getattr(defaults, name)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return getattr(defaults, name)
        if not math.isfinite(number) or number <= 0 or number > MAX_UPSTREAM_TIMEOUT_S:
            return getattr(defaults, name)
        return number

    return UpstreamTimeouts(
        connect_timeout_s=pick('connect_timeout_s'),
        first_byte_timeout_s=pick('first_byte_timeout_s'),
        read_timeout_s=pick('read_timeout_s'),
        updated_at=updated_at,
        updated_by=updated_by or '',
        is_default=False,
    )


async def get_upstream_timeouts(session: AsyncSession) -> UpstreamTimeouts:
    cached = _cache_get()
    if cached is not None:
        return cached
    row = await repo.get_app_setting(session, UPSTREAM_TIMEOUT_SETTING_KEY)
    if row is None:
        resolved = default_upstream_timeouts()
    else:
        try:
            payload = json.loads(row.value_json or '{}')
        except Exception:
            payload = {}
        resolved = _timeouts_from_json(
            payload if isinstance(payload, dict) else {},
            updated_at=int(row.updated_at) if row.updated_at is not None else None,
            updated_by=row.updated_by or '',
        )
    _cache_put(resolved)
    return resolved


async def save_upstream_timeouts(
    session: AsyncSession,
    *,
    connect_timeout_s: Any,
    first_byte_timeout_s: Any,
    read_timeout_s: Any,
    updated_by: str,
) -> UpstreamTimeouts:
    next_settings = UpstreamTimeouts(
        connect_timeout_s=normalize_timeout_value(connect_timeout_s, label='连接超时'),
        first_byte_timeout_s=normalize_timeout_value(first_byte_timeout_s, label='首字节超时'),
        read_timeout_s=normalize_timeout_value(read_timeout_s, label='读空闲超时'),
        updated_at=now_ms(),
        updated_by=updated_by or '',
        is_default=False,
    )
    payload = json.dumps(
        {
            'connect_timeout_s': next_settings.connect_timeout_s,
            'first_byte_timeout_s': next_settings.first_byte_timeout_s,
            'read_timeout_s': next_settings.read_timeout_s,
        },
        ensure_ascii=False,
    )
    row = await repo.get_app_setting(session, UPSTREAM_TIMEOUT_SETTING_KEY)
    if row is None:
        row = orm.AppSettingORM(
            key=UPSTREAM_TIMEOUT_SETTING_KEY,
            value_json=payload,
            updated_at=next_settings.updated_at,
            updated_by=next_settings.updated_by,
        )
    else:
        row.value_json = payload
        row.updated_at = next_settings.updated_at
        row.updated_by = next_settings.updated_by
    await repo.save_app_setting(session, row)
    _cache_put(next_settings)
    return next_settings


async def reset_upstream_timeouts(session: AsyncSession) -> UpstreamTimeouts:
    await repo.delete_app_setting(session, UPSTREAM_TIMEOUT_SETTING_KEY)
    defaults = default_upstream_timeouts()
    _cache_put(defaults)
    return defaults
