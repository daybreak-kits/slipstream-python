from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.timeutil import now_ms
from l04_repo import orm, repo

BODY_LOG_SETTING_KEY = "body_logging"
BODY_MODES = ("off", "errors", "all")
BodyMode = Literal["off", "errors", "all"]

# Ticket 15 fixed limits. They are stored with the policy so the settings page
# can display what the gateway is actually enforcing.
DEFAULT_BODY_TRUNCATE_BYTES = 16 * 1024
DEFAULT_BODY_RETENTION_DAYS = 7
DEFAULT_SUMMARY_MAX_BYTES = 64 * 1024
LOG_METADATA_RETENTION_DAYS = 30

MIN_BODY_TRUNCATE_BYTES = 1024
MAX_BODY_TRUNCATE_BYTES = 1024 * 1024
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 90
MIN_SUMMARY_MAX_BYTES = 1024
MAX_SUMMARY_MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BodyLogSettings:
    mode: BodyMode = "off"
    truncate_bytes: int = DEFAULT_BODY_TRUNCATE_BYTES
    retention_days: int = DEFAULT_BODY_RETENTION_DAYS
    summary_max_bytes: int = DEFAULT_SUMMARY_MAX_BYTES
    updated_at: int | None = None
    updated_by: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode != "off"


def _clamp(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _parse_mode(value: Any) -> BodyMode:
    mode = str(value or "").strip().lower()
    return mode if mode in BODY_MODES else "off"  # type: ignore[return-value]


def body_settings_from_json(
    payload: dict[str, Any] | None,
    *,
    updated_at: int | None = None,
    updated_by: str = "",
) -> BodyLogSettings:
    data = payload if isinstance(payload, dict) else {}
    return BodyLogSettings(
        mode=_parse_mode(data.get("mode") or data.get("body_policy")),
        truncate_bytes=_clamp(
            data.get("truncate_bytes"),
            DEFAULT_BODY_TRUNCATE_BYTES,
            MIN_BODY_TRUNCATE_BYTES,
            MAX_BODY_TRUNCATE_BYTES,
        ),
        retention_days=_clamp(
            data.get("retention_days"),
            DEFAULT_BODY_RETENTION_DAYS,
            MIN_RETENTION_DAYS,
            MAX_RETENTION_DAYS,
        ),
        summary_max_bytes=_clamp(
            data.get("summary_max_bytes"),
            DEFAULT_SUMMARY_MAX_BYTES,
            MIN_SUMMARY_MAX_BYTES,
            MAX_SUMMARY_MAX_BYTES,
        ),
        updated_at=updated_at,
        updated_by=updated_by or "",
    )


def body_settings_to_json(settings: BodyLogSettings) -> str:
    return json.dumps(
        {
            "mode": settings.mode,
            "truncate_bytes": settings.truncate_bytes,
            "retention_days": settings.retention_days,
            "summary_max_bytes": settings.summary_max_bytes,
        },
        ensure_ascii=False,
    )


async def get_body_settings(session: AsyncSession) -> BodyLogSettings:
    row = await repo.get_app_setting(session, BODY_LOG_SETTING_KEY)
    if row is None:
        return BodyLogSettings()
    try:
        payload = json.loads(row.value_json or "{}")
    except Exception:
        payload = {}
    return body_settings_from_json(
        payload if isinstance(payload, dict) else {},
        updated_at=int(row.updated_at) if row.updated_at is not None else None,
        updated_by=row.updated_by or "",
    )


async def save_body_settings(
    session: AsyncSession,
    *,
    mode: BodyMode | None = None,
    truncate_bytes: int | None = None,
    retention_days: int | None = None,
    summary_max_bytes: int | None = None,
    updated_by: str,
) -> BodyLogSettings:
    current = await get_body_settings(session)
    next_settings = replace(
        current,
        mode=_parse_mode(mode) if mode is not None else current.mode,
        truncate_bytes=current.truncate_bytes if truncate_bytes is None else truncate_bytes,
        retention_days=current.retention_days if retention_days is None else retention_days,
        summary_max_bytes=current.summary_max_bytes if summary_max_bytes is None else summary_max_bytes,
        updated_at=now_ms(),
        updated_by=updated_by or "",
    )
    row = await repo.get_app_setting(session, BODY_LOG_SETTING_KEY)
    payload = body_settings_to_json(next_settings)
    if row is None:
        row = orm.AppSettingORM(
            key=BODY_LOG_SETTING_KEY,
            value_json=payload,
            updated_at=next_settings.updated_at,
            updated_by=next_settings.updated_by,
        )
        await repo.save_app_setting(session, row)
    else:
        row.value_json = payload
        row.updated_at = next_settings.updated_at
        row.updated_by = next_settings.updated_by
        await repo.save_app_setting(session, row)
    # Local import avoids a module cycle: body_logging imports this module for
    # the policy dataclass and default reader.
    from l05_service.body_logging import invalidate_body_settings_cache

    invalidate_body_settings_cache()
    return next_settings
