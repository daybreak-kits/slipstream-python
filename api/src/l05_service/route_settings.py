"""DB-backed routing policy overrides (ADR-077).

Three deployment-wide knobs live in the admin settings page and are stored as
one JSON row in ``app_settings`` (key ``route_settings``):

* ``strategy`` -- uniform | stable | cost | latency (default uniform);
* ``window_minutes`` -- observation window for member stats, 15..1440,
  default 30;
* ``inflight_cap`` -- gateway admission limit, 0 disables admission control,
  default 256.

Same contract as the ADR-075 upstream timeouts: defaults are constants (not
env), the request path resolves them from a short-lived process cache that
boot warms and save/reset refresh, and other workers catch up within one TTL.
Invalid stored values degrade to the safe default instead of failing traffic.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.timeutil import now_ms
from l04_repo import orm, repo

ROUTE_SETTINGS_SETTING_KEY = "route_settings"

ROUTE_STRATEGIES = ("uniform", "stable", "cost", "latency")
MIN_WINDOW_MINUTES = 15
MAX_WINDOW_MINUTES = 1440
DEFAULT_STRATEGY = "uniform"
DEFAULT_WINDOW_MINUTES = 30
DEFAULT_INFLIGHT_CAP = 256
MAX_INFLIGHT_CAP = 100_000

CACHE_TTL_S = 15.0
_CACHE: list[Any] = [None, 0.0]  # [resolved RouteSettings, loaded monotonic]


@dataclass(frozen=True, slots=True)
class RouteSettings:
    strategy: str = DEFAULT_STRATEGY
    window_minutes: int = DEFAULT_WINDOW_MINUTES
    inflight_cap: int = DEFAULT_INFLIGHT_CAP
    updated_at: int | None = None
    updated_by: str = ""
    is_default: bool = True


def normalize_strategy(value: Any) -> str:
    """Unknown or blank strategies fall back to uniform ordering."""
    text = str(value or "").strip().lower()
    return text if text in ROUTE_STRATEGIES else DEFAULT_STRATEGY


def normalize_window_minutes(value: Any) -> int:
    """Clamp the observation window into the 15..1440 minute band."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINUTES
    return max(MIN_WINDOW_MINUTES, min(MAX_WINDOW_MINUTES, parsed))


def normalize_inflight_cap(value: Any) -> int:
    """Clamp the admission cap; 0 disables admission control."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INFLIGHT_CAP
    return max(0, min(MAX_INFLIGHT_CAP, parsed))


def _cache_get() -> RouteSettings | None:
    cached, loaded_at = _CACHE
    if cached is None or time.monotonic() - loaded_at >= CACHE_TTL_S:
        return None
    return cached


def _cache_put(value: RouteSettings) -> None:
    _CACHE[0] = value
    _CACHE[1] = time.monotonic()


def _cache_invalidate() -> None:
    _CACHE[0] = None
    _CACHE[1] = 0.0


def default_route_settings() -> RouteSettings:
    return RouteSettings()


def _settings_from_json(
    payload: dict[str, Any],
    *,
    updated_at: int | None = None,
    updated_by: str = "",
) -> RouteSettings:
    def pick(name: str, normalize: Any) -> Any:
        return normalize(payload.get(name)) if name in payload else normalize(None)

    return RouteSettings(
        strategy=pick("strategy", normalize_strategy),
        window_minutes=pick("window_minutes", normalize_window_minutes),
        inflight_cap=pick("inflight_cap", normalize_inflight_cap),
        updated_at=updated_at,
        updated_by=updated_by,
        is_default=False,
    )


def cached_route_settings() -> RouteSettings:
    """Sync read for request-path code without a session (e.g. admission).

    Returns the warm process cache or the ADR-077 defaults; never SQL. Boot
    and the settings PATCH keep it fresh within one TTL.
    """
    return _cache_get() or RouteSettings()


async def get_route_settings(session: AsyncSession) -> RouteSettings:
    cached = _cache_get()
    if cached is not None:
        return cached
    row = await repo.get_app_setting(session, ROUTE_SETTINGS_SETTING_KEY)
    if row is None:
        resolved = default_route_settings()
    else:
        try:
            payload = json.loads(row.value_json or "{}")
        except Exception:
            payload = {}
        resolved = _settings_from_json(
            payload if isinstance(payload, dict) else {},
            updated_at=int(row.updated_at) if row.updated_at is not None else None,
            updated_by=row.updated_by or "",
        )
    _cache_put(resolved)
    return resolved


async def save_route_settings(
    session: AsyncSession,
    *,
    strategy: Any = None,
    window_minutes: Any = None,
    inflight_cap: Any = None,
    updated_by: str,
) -> RouteSettings:
    current = await get_route_settings(session)
    next_settings = RouteSettings(
        strategy=normalize_strategy(
            strategy if strategy is not None else current.strategy
        ),
        window_minutes=normalize_window_minutes(
            window_minutes if window_minutes is not None else current.window_minutes
        ),
        inflight_cap=normalize_inflight_cap(
            inflight_cap if inflight_cap is not None else current.inflight_cap
        ),
        updated_at=now_ms(),
        updated_by=updated_by or "",
        is_default=False,
    )
    payload = json.dumps(
        {
            "strategy": next_settings.strategy,
            "window_minutes": next_settings.window_minutes,
            "inflight_cap": next_settings.inflight_cap,
        },
        ensure_ascii=False,
    )
    row = await repo.get_app_setting(session, ROUTE_SETTINGS_SETTING_KEY)
    if row is None:
        row = orm.AppSettingORM(
            key=ROUTE_SETTINGS_SETTING_KEY,
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
    # route_stats holds a second 15s settings snapshot; sync it here so the
    # promised save-is-immediate contract holds for both copies. Local import
    # only: route_stats imports this module at the top level.
    from l05_service import route_stats

    route_stats._settings_cache[0] = next_settings
    route_stats._settings_cache[1] = time.monotonic()
    return next_settings


async def reset_route_settings(session: AsyncSession) -> RouteSettings:
    await repo.delete_app_setting(session, ROUTE_SETTINGS_SETTING_KEY)
    defaults = default_route_settings()
    _cache_put(defaults)
    # Keep the route_stats snapshot coherent with the fresh defaults.
    from l05_service import route_stats

    route_stats._settings_cache[0] = defaults
    route_stats._settings_cache[1] = time.monotonic()
    return defaults
