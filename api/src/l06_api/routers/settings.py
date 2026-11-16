from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from l04_repo import orm
from l05_service import app_settings as body_settings_service
from l05_service import gateway_settings as gateway_settings_service
from l05_service import route_settings as route_settings_service
from l05_service import upstream_settings as upstream_settings_service
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _serialize(
    settings: body_settings_service.BodyLogSettings,
    gateway: gateway_settings_service.GatewaySettings,
    upstream: upstream_settings_service.UpstreamTimeouts,
    route_cfg: route_settings_service.RouteSettings,
) -> SettingsOut:
    return SettingsOut(
        body_policy=settings.mode,
        body_truncate_bytes=settings.truncate_bytes,
        body_retention_days=settings.retention_days,
        summary_max_bytes=settings.summary_max_bytes,
        updated_at=settings.updated_at,
        updated_by=settings.updated_by,
        log_retention_days=body_settings_service.LOG_METADATA_RETENTION_DAYS,
        redaction_forced=True,
        gateway_base_url=gateway.base_url,
        gateway_updated_at=gateway.updated_at,
        gateway_updated_by=gateway.updated_by,
        upstream_connect_timeout_s=upstream.connect_timeout_s,
        upstream_first_byte_timeout_s=upstream.first_byte_timeout_s,
        upstream_read_timeout_s=upstream.read_timeout_s,
        upstream_timeouts_updated_at=upstream.updated_at,
        upstream_timeouts_updated_by=upstream.updated_by,
        upstream_timeouts_is_default=upstream.is_default,
        route_strategy=route_cfg.strategy,
        route_window_minutes=route_cfg.window_minutes,
        gateway_inflight_cap=route_cfg.inflight_cap,
    )


@router.get("", response_model=SettingsOut)
async def read_settings(
    _admin: orm.AdminUserORM = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
) -> SettingsOut:
    return _serialize(
        await body_settings_service.get_body_settings(session),
        await gateway_settings_service.get_gateway_settings(session),
        await upstream_settings_service.get_upstream_timeouts(session),
        await route_settings_service.get_route_settings(session),
    )


@router.patch("", response_model=SettingsOut)
async def update_settings(
    payload: SettingsUpdate,
    admin: orm.AdminUserORM = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
) -> SettingsOut:
    current = await body_settings_service.get_body_settings(session)
    gateway = await gateway_settings_service.get_gateway_settings(session)
    route_cfg = await route_settings_service.get_route_settings(session)
    body_fields = (
        payload.body_policy,
        payload.body_truncate_bytes,
        payload.body_retention_days,
        payload.summary_max_bytes,
    )
    timeout_fields = (
        payload.upstream_connect_timeout_s,
        payload.upstream_first_byte_timeout_s,
        payload.upstream_read_timeout_s,
    )
    route_fields = (
        payload.route_strategy,
        payload.route_window_minutes,
        payload.gateway_inflight_cap,
    )
    if (
        all(value is None for value in body_fields)
        and payload.gateway_base_url is None
        and all(value is None for value in timeout_fields)
        and not payload.upstream_timeouts_reset
        and all(value is None for value in route_fields)
    ):
        upstream_current = await upstream_settings_service.get_upstream_timeouts(session)
        return _serialize(current, gateway, upstream_current, route_cfg)
    updated = current
    if any(value is not None for value in body_fields):
        updated = await body_settings_service.save_body_settings(
            session,
            mode=payload.body_policy,
            truncate_bytes=payload.body_truncate_bytes,
            retention_days=payload.body_retention_days,
            summary_max_bytes=payload.summary_max_bytes,
            updated_by=admin.username,
        )
    if payload.gateway_base_url is not None:
        gateway = await gateway_settings_service.save_gateway_settings(
            session,
            base_url=payload.gateway_base_url,
            updated_by=admin.username,
        )
    upstream = await upstream_settings_service.get_upstream_timeouts(session)
    if payload.upstream_timeouts_reset:
        # 重置优先于同时提交的数值：删除覆盖行，回到 env 默认。
        upstream = await upstream_settings_service.reset_upstream_timeouts(session)
    elif any(value is not None for value in timeout_fields):
        upstream = await upstream_settings_service.save_upstream_timeouts(
            session,
            connect_timeout_s=(
                payload.upstream_connect_timeout_s
                if payload.upstream_connect_timeout_s is not None
                else upstream.connect_timeout_s
            ),
            first_byte_timeout_s=(
                payload.upstream_first_byte_timeout_s
                if payload.upstream_first_byte_timeout_s is not None
                else upstream.first_byte_timeout_s
            ),
            read_timeout_s=(
                payload.upstream_read_timeout_s
                if payload.upstream_read_timeout_s is not None
                else upstream.read_timeout_s
            ),
            updated_by=admin.username,
        )
    if any(value is not None for value in route_fields):
        route_cfg = await route_settings_service.save_route_settings(
            session,
            strategy=payload.route_strategy,
            window_minutes=payload.route_window_minutes,
            inflight_cap=payload.gateway_inflight_cap,
            updated_by=admin.username,
        )
    return _serialize(updated, gateway, upstream, route_cfg)
