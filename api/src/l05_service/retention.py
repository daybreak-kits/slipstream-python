from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core.logging import get_logger
from l01_core.timeutil import next_local_midnight_ms, now_ms
from l04_repo import repo
from l05_service.app_settings import (
    DEFAULT_BODY_RETENTION_DAYS,
    LOG_METADATA_RETENTION_DAYS,
    get_body_settings,
)

logger = get_logger("system.retention")


async def expire_body_text(session: AsyncSession, *, retention_days: int, now: int) -> int:
    """NULL all body columns older than the retention window."""
    before_ms = now - max(1, int(retention_days)) * 86_400_000
    return await repo.clear_request_bodies_before(session, before_ms)


async def purge_log_metadata(session: AsyncSession, *, now: int) -> int:
    """Delete old logs; request_bodies rows are removed with them."""
    before_ms = now - LOG_METADATA_RETENTION_DAYS * 86_400_000
    return await repo.purge_logs_before(session, before_ms)


async def run_daily_retention(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Run both retention rules. Exposed for tests and the startup task."""
    current = now_ms() if now is None else now
    async with session_factory() as session:
        settings = await get_body_settings(session)
        expired = await expire_body_text(
            session, retention_days=settings.retention_days, now=current
        )
        purged = await purge_log_metadata(session, now=current)
    return {
        "bodies_expired": expired,
        "logs_purged": purged,
        "body_retention_days": settings.retention_days,
        "log_retention_days": LOG_METADATA_RETENTION_DAYS,
    }


async def retention_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timezone: str = "Asia/Shanghai",
) -> None:
    """Run retention at every local midnight for the lifetime of the app."""
    while True:
        now = now_ms()
        delay_s = max(1.0, (next_local_midnight_ms(now, timezone) - now) / 1000)
        await asyncio.sleep(delay_s)
        try:
            await run_daily_retention(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning("daily retention failed: %s", error)


def default_body_retention_days() -> int:
    return DEFAULT_BODY_RETENTION_DAYS
