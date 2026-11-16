from __future__ import annotations

import asyncio
import json
import weakref
from typing import Any

from sqlalchemy import (
    and_,
    case,
    delete,
    event,
    exists,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from l01_core import runtime
from l01_core.ids import new_id
from l01_core.timeutil import now_ms
from l02_infra.dialect import SQLITE, execute_with_lock_retry
from l04_repo import attempt_filters, log_query, orm
from l04_repo.dialects import conflict_insert, dialect_name
from l04_repo.search import LIKE_ESCAPE, like_contains, like_pattern


# ---------------------------------------------------------------------------
# Users / API keys
# ---------------------------------------------------------------------------
async def get_user_by_username(session: AsyncSession, username: str) -> orm.UserORM | None:
    return (await session.execute(select(orm.UserORM).where(orm.UserORM.username == username))).scalar_one_or_none()


async def create_user(session: AsyncSession, username: str, display_name: str = "") -> orm.UserORM:
    user = orm.UserORM(id=new_id(), username=username, display_name=display_name or username)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def list_users(session: AsyncSession) -> list[orm.UserORM]:
    return list((await session.execute(select(orm.UserORM).order_by(orm.UserORM.created_at.asc()))).scalars().all())


async def list_api_keys(session: AsyncSession, user_id: str | None = None) -> list[orm.ApiKeyORM]:
    stmt = select(orm.ApiKeyORM).order_by(orm.ApiKeyORM.created_at.desc())
    if user_id:
        stmt = stmt.where(orm.ApiKeyORM.user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_api_key_by_hash(session: AsyncSession, key_hash: str) -> orm.ApiKeyORM | None:
    return (
        await session.execute(select(orm.ApiKeyORM).where(orm.ApiKeyORM.key_hash == key_hash))
    ).scalar_one_or_none()


async def get_api_key_with_user_by_hash(
    session: AsyncSession, key_hash: str
) -> tuple[orm.ApiKeyORM, orm.UserORM] | None:
    """Resolve a key and its owner in one statement (L1 hot path).

    Authentication used to spend two round trips here (api_keys, then
    users). One inner join keeps the same 401 semantics: a key whose user
    row is missing is indistinguishable from an unknown key.
    """
    stmt = (
        select(orm.ApiKeyORM, orm.UserORM)
        .join(orm.UserORM, orm.ApiKeyORM.user_id == orm.UserORM.id)
        .where(orm.ApiKeyORM.key_hash == key_hash)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def get_api_key(session: AsyncSession, key_id: str) -> orm.ApiKeyORM | None:
    return await session.get(orm.ApiKeyORM, key_id)


async def get_api_key_by_label(session: AsyncSession, user_id: str, label: str) -> orm.ApiKeyORM | None:
    return (
        await session.execute(
            select(orm.ApiKeyORM).where(orm.ApiKeyORM.user_id == user_id, orm.ApiKeyORM.label == label)
        )
    ).scalar_one_or_none()


async def save_api_key(session: AsyncSession, key: orm.ApiKeyORM) -> orm.ApiKeyORM:
    session.add(key)
    await bump_catalog_version(session)
    await session.commit()
    await session.refresh(key)
    return key


# ``last_used_at`` is telemetry, not correctness. Updating it on every
# completion turns each request into an extra SQLite write (and fsync under the
# default journal mode). One-minute resolution is enough for the admin UI and
# removes that write from the steady-state hot path.
API_KEY_TOUCH_INTERVAL_MS = 60_000


async def touch_api_key(session: AsyncSession, key: orm.ApiKeyORM) -> None:
    now = now_ms()
    if (
        key.last_used_at is not None
        and now - key.last_used_at < API_KEY_TOUCH_INTERVAL_MS
    ):
        # Still end the read transaction: a later request-log INSERT reuses this
        # session, and a long-lived read snapshot cannot always upgrade in WAL.
        await session.commit()
        return
    key.last_used_at = now
    await session.commit()


async def delete_api_key(session: AsyncSession, key: orm.ApiKeyORM) -> None:
    await session.delete(key)
    await bump_catalog_version(session)
    await session.commit()


# ---------------------------------------------------------------------------
# Admin users
# ---------------------------------------------------------------------------
async def get_admin_by_username(session: AsyncSession, username: str) -> orm.AdminUserORM | None:
    return (
        await session.execute(select(orm.AdminUserORM).where(orm.AdminUserORM.username == username))
    ).scalar_one_or_none()


async def count_admins(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count(orm.AdminUserORM.id)))).scalar_one() or 0)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------
async def get_channel(session: AsyncSession, channel_id: str) -> orm.ChannelORM | None:
    return await session.get(orm.ChannelORM, channel_id)


async def get_channel_by_slug(session: AsyncSession, slug: str) -> orm.ChannelORM | None:
    return (await session.execute(select(orm.ChannelORM).where(orm.ChannelORM.slug == slug))).scalar_one_or_none()


async def list_channels(
    session: AsyncSession, search: str = "", status: str = ""
) -> list[orm.ChannelORM]:
    """Channels newest-first; ``search`` is literal, ``status`` exact match."""
    stmt = select(orm.ChannelORM).order_by(orm.ChannelORM.created_at.desc())
    if search:
        stmt = stmt.where(
            or_(
                like_contains(orm.ChannelORM.slug, search),
                like_contains(orm.ChannelORM.name, search),
                like_contains(orm.ChannelORM.note, search),
                like_contains(orm.ChannelORM.connection_url, search),
            )
        )
    if status:
        stmt = stmt.where(orm.ChannelORM.connection_status == status)
    return list((await session.execute(stmt)).scalars().all())

async def channel_probe_summaries(
    session: AsyncSession, channel_id: str | None = None
) -> dict[str, dict[str, Any]]:
    """Latest deep model-probe result per channel (not /v1/models tests)."""
    stmt = select(
        orm.ChannelModelORM.channel_id,
        orm.ChannelModelORM.probe_status,
        orm.ChannelModelORM.probe_latency_ms,
        orm.ChannelModelORM.probed_at,
    ).where(orm.ChannelModelORM.probed_at.is_not(None))
    if channel_id:
        stmt = stmt.where(orm.ChannelModelORM.channel_id == channel_id)
    summaries: dict[str, dict[str, Any]] = {}
    for row in (await session.execute(stmt)).all():
        current = summaries.get(row.channel_id)
        if current is None or int(row.probed_at or 0) > int(current["probed_at"] or 0):
            summaries[row.channel_id] = {
                "status": row.probe_status or "unknown",
                "latency_ms": row.probe_latency_ms,
                "probed_at": row.probed_at,
            }
    return summaries


async def save_channel(
    session: AsyncSession, channel: orm.ChannelORM, *, bump_catalog: bool = True
) -> orm.ChannelORM:
    if channel not in session:
        session.add(channel)
    if bump_catalog:
        await bump_catalog_version(session)
    await session.commit()
    await session.refresh(channel)
    return channel


async def delete_channel(session: AsyncSession, channel: orm.ChannelORM) -> None:
    await session.delete(channel)
    await bump_catalog_version(session)
    await session.commit()


async def channel_combo_reference_count(session: AsyncSession, channel_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(orm.ComboTargetORM.id))
                .join(
                    orm.ChannelModelORM,
                    orm.ComboTargetORM.channel_model_id == orm.ChannelModelORM.id,
                )
                .where(orm.ChannelModelORM.channel_id == channel_id)
            )
        ).scalar_one()
        or 0
    )


async def list_channel_keys(session: AsyncSession, channel_id: str) -> list[orm.ChannelKeyORM]:
    return list(
        (
            await session.execute(
                select(orm.ChannelKeyORM)
                .where(orm.ChannelKeyORM.channel_id == channel_id)
                .order_by(orm.ChannelKeyORM.created_at.asc(), orm.ChannelKeyORM.id.asc())
            )
        )
        .scalars()
        .all()
    )


async def list_all_channel_keys(session: AsyncSession) -> list[orm.ChannelKeyORM]:
    return list(
        (
            await session.execute(
                select(orm.ChannelKeyORM).order_by(
                    orm.ChannelKeyORM.channel_id.asc(),
                    orm.ChannelKeyORM.created_at.asc(),
                )
            )
        )
        .scalars()
        .all()
    )


async def get_channel_key(session: AsyncSession, key_id: str) -> orm.ChannelKeyORM | None:
    return await session.get(orm.ChannelKeyORM, key_id)


async def save_channel_key(
    session: AsyncSession, key: orm.ChannelKeyORM
) -> orm.ChannelKeyORM:
    session.add(key)
    await bump_catalog_version(session)
    await session.commit()
    await session.refresh(key)
    return key


async def delete_channel_key(session: AsyncSession, key: orm.ChannelKeyORM) -> None:
    await session.delete(key)
    await bump_catalog_version(session)
    await session.commit()


async def channel_model_counts(session: AsyncSession) -> dict[str, dict[str, int]]:
    rows = (
        await session.execute(
            select(orm.ChannelModelORM.channel_id, func.count(orm.ChannelModelORM.id)).group_by(
                orm.ChannelModelORM.channel_id
            )
        )
    ).all()
    return {row[0]: {"total": int(row[1] or 0), "healthy": 0} for row in rows}


# ---------------------------------------------------------------------------
# Channel models
# ---------------------------------------------------------------------------
async def get_channel_model(session: AsyncSession, model_id: str) -> orm.ChannelModelORM | None:
    return await session.get(orm.ChannelModelORM, model_id)


async def get_channel_model_by_call(
    session: AsyncSession, channel_id: str, call_model: str
) -> orm.ChannelModelORM | None:
    return (
        await session.execute(
            select(orm.ChannelModelORM).where(
                orm.ChannelModelORM.channel_id == channel_id,
                orm.ChannelModelORM.call_model == call_model,
            )
        )
    ).scalar_one_or_none()


async def get_channel_and_model_by_call(
    session: AsyncSession, slug: str, call_model: str
) -> tuple[orm.ChannelORM, orm.ChannelModelORM] | None:
    """Channel and its model row in one statement (L1 hot path).

    A direct call used to fetch the channel by slug (plus its keys selectin)
    and then the model row in a second statement. The join merges both
    lookups; the channel keys relationship still loads through its selectin
    statement.
    """
    stmt = (
        select(orm.ChannelORM, orm.ChannelModelORM)
        .join(orm.ChannelModelORM, orm.ChannelModelORM.channel_id == orm.ChannelORM.id)
        .where(
            orm.ChannelORM.slug == slug,
            orm.ChannelModelORM.call_model == call_model,
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def list_channel_models(
    session: AsyncSession, channel_id: str | None = None, search: str = ""
) -> list[orm.ChannelModelORM]:
    stmt = select(orm.ChannelModelORM).order_by(orm.ChannelModelORM.created_at.asc())
    if channel_id:
        stmt = stmt.where(orm.ChannelModelORM.channel_id == channel_id)
    if search:
        stmt = stmt.where(
            or_(
                like_contains(orm.ChannelModelORM.upstream_model, search),
                like_contains(orm.ChannelModelORM.call_model, search),
            )
        )
    return list((await session.execute(stmt)).scalars().all())


async def list_all_channel_models(session: AsyncSession) -> list[orm.ChannelModelORM]:
    return list((await session.execute(select(orm.ChannelModelORM).order_by(orm.ChannelModelORM.created_at.asc()))).scalars().all())


async def list_channel_models_enabled(session: AsyncSession) -> list[orm.ChannelModelORM]:
    """Enabled rows only; the models.dev value-compare sync only touches live rows."""
    stmt = (
        select(orm.ChannelModelORM)
        .where(orm.ChannelModelORM.enabled == True)  # noqa: E712
        .order_by(orm.ChannelModelORM.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def save_channel_model(
    session: AsyncSession, model: orm.ChannelModelORM, *, bump_catalog: bool = True
) -> orm.ChannelModelORM:
    session.add(model)
    if bump_catalog:
        await bump_catalog_version(session)
    await session.commit()
    await session.refresh(model)
    return model


async def save_channel_models(
    session: AsyncSession, models: list[orm.ChannelModelORM]
) -> None:
    """Persist several rows in one transaction and bump the catalog once."""
    session.add_all(models)
    await bump_catalog_version(session)
    await session.commit()


async def delete_channel_model(session: AsyncSession, model: orm.ChannelModelORM) -> None:
    await session.delete(model)
    await bump_catalog_version(session)
    await session.commit()


async def channel_model_ref_count(session: AsyncSession, model_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(orm.ComboTargetORM.id)).where(orm.ComboTargetORM.channel_model_id == model_id)
            )
        ).scalar_one()
        or 0
    )


# ---------------------------------------------------------------------------
# Breaker transitions
# ---------------------------------------------------------------------------
# Breaker state changes are conditional UPDATEs: the WHERE clause decides the
# winner when two requests race for the same transition. They live here so that
# l05_service never spells SQL itself (ssot §4.1). Each helper commits before
# returning, so the caller never streams while holding an idle write lock.
async def release_expired_half_open(
    session: AsyncSession, model_id: str, *, now: int, lease_ms: int, values: dict[str, Any]
) -> int:
    """Release a half-open probe slot whose lease expired; return affected rows."""
    result = await session.execute(
        update(orm.ChannelModelORM)
        .where(
            orm.ChannelModelORM.id == model_id,
            orm.ChannelModelORM.health_status == "half_open",
            or_(
                orm.ChannelModelORM.half_open_since.is_(None),
                orm.ChannelModelORM.half_open_since <= now - lease_ms,
            ),
        )
        .values(**values)
    )
    rowcount = int(result.rowcount or 0)
    await session.commit()
    return rowcount


async def reset_expired_fault(
    session: AsyncSession, model_id: str, *, now: int, values: dict[str, Any]
) -> int:
    """Reset a fault whose backoff expired; return affected rows."""
    result = await session.execute(
        update(orm.ChannelModelORM)
        .where(
            orm.ChannelModelORM.id == model_id,
            orm.ChannelModelORM.health_status == "fault",
            orm.ChannelModelORM.fault_until.is_not(None),
            orm.ChannelModelORM.fault_until <= now,
        )
        .values(**values)
    )
    rowcount = int(result.rowcount or 0)
    await session.commit()
    return rowcount


async def claim_half_open_probe(
    session: AsyncSession, model_id: str, *, now: int, values: dict[str, Any]
) -> int:
    """Promote an open row to half_open and claim its probe slot; return affected rows."""
    result = await session.execute(
        update(orm.ChannelModelORM)
        .where(
            orm.ChannelModelORM.id == model_id,
            orm.ChannelModelORM.health_status == "open",
            orm.ChannelModelORM.cooldown_until.is_not(None),
            orm.ChannelModelORM.cooldown_until <= now,
        )
        .values(**values)
    )
    rowcount = int(result.rowcount or 0)
    await session.commit()
    return rowcount


# ---------------------------------------------------------------------------
# Combos
# ---------------------------------------------------------------------------
async def get_combo(session: AsyncSession, combo_id: str) -> orm.ComboORM | None:
    return await session.get(orm.ComboORM, combo_id)


async def get_combo_by_name(session: AsyncSession, name: str) -> orm.ComboORM | None:
    return (await session.execute(select(orm.ComboORM).where(orm.ComboORM.name == name))).scalar_one_or_none()


async def list_combos(session: AsyncSession) -> list[orm.ComboORM]:
    return list((await session.execute(select(orm.ComboORM).order_by(orm.ComboORM.created_at.desc()))).scalars().all())


async def save_combo(session: AsyncSession, combo: orm.ComboORM) -> orm.ComboORM:
    session.add(combo)
    await bump_catalog_version(session)
    await session.commit()
    await session.refresh(combo)
    return combo


async def delete_combo(session: AsyncSession, combo: orm.ComboORM) -> None:
    await session.delete(combo)
    await bump_catalog_version(session)
    await session.commit()


async def list_combo_targets(session: AsyncSession, combo_id: str) -> list[orm.ComboTargetORM]:
    return list(
        (
            await session.execute(
                select(orm.ComboTargetORM)
                .where(orm.ComboTargetORM.combo_id == combo_id)
                # 优先级数值越大越先调用（1 = 最低），与 routing.order_candidates 同方向。
                .order_by(orm.ComboTargetORM.priority.desc(), orm.ComboTargetORM.created_at.asc())
            )
        ).scalars().all()
    )


async def list_combo_members(
    session: AsyncSession, combo_id: str
) -> list[tuple[orm.ComboTargetORM, orm.ChannelModelORM | None, orm.ChannelORM | None]]:
    """Combo targets joined with their member model and channel in one statement.

    Expanding a combo used to load each member model by id and then its channel
    by id, three statements per member. One outer join returns the same rows in
    the same order; child-combo rows carry no member columns and are still
    expanded recursively by the caller.
    """
    rows = (
        await session.execute(
            select(orm.ComboTargetORM, orm.ChannelModelORM, orm.ChannelORM)
            .select_from(orm.ComboTargetORM)
            .outerjoin(
                orm.ChannelModelORM,
                orm.ChannelModelORM.id == orm.ComboTargetORM.channel_model_id,
            )
            .outerjoin(
                orm.ChannelORM,
                orm.ChannelORM.id == orm.ChannelModelORM.channel_id,
            )
            .where(orm.ComboTargetORM.combo_id == combo_id)
            .order_by(
                # 同上：priority 越大越先，created_at 只做相同优先级内的稳定排序。
                orm.ComboTargetORM.priority.desc(),
                orm.ComboTargetORM.created_at.asc(),
            )
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


async def list_enabled_channel_models_by_call_model(
    session: AsyncSession, call_model: str
) -> list[tuple[orm.ChannelModelORM, orm.ChannelORM]]:
    """Enabled channel models for one exact ``call_model``, joined with their channel.

    ``channel_models.call_model`` is indexed, so resolving one implicit group is
    a point lookup instead of listing every channel and channel model to derive
    all groups. Disabled models never join a group, so they are filtered here.
    """
    rows = (
        await session.execute(
            select(orm.ChannelModelORM, orm.ChannelORM)
            .select_from(orm.ChannelModelORM)
            .join(orm.ChannelORM, orm.ChannelORM.id == orm.ChannelModelORM.channel_id)
            .where(
                orm.ChannelModelORM.call_model == call_model,
                or_(
                    orm.ChannelModelORM.enabled.is_(True),
                    # 定时封禁未到期的行也要取回：到期由读取侧按 banned_until 惰性判活。
                    orm.ChannelModelORM.banned_until.is_not(None),
                ),
            )
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def list_all_combo_targets(session: AsyncSession) -> list[orm.ComboTargetORM]:
    return list(
        (
            await session.execute(
                select(orm.ComboTargetORM).order_by(
                    orm.ComboTargetORM.combo_id.asc(),
                    orm.ComboTargetORM.priority.desc(),
                    orm.ComboTargetORM.created_at.asc(),
                )
            )
        ).scalars().all()
    )


async def replace_combo_targets(
    session: AsyncSession, combo_id: str, targets: list[dict[str, Any]]
) -> list[orm.ComboTargetORM]:
    await session.execute(delete(orm.ComboTargetORM).where(orm.ComboTargetORM.combo_id == combo_id))
    created: list[orm.ComboTargetORM] = []
    for item in targets:
        target = orm.ComboTargetORM(
            id=new_id(),
            combo_id=combo_id,
            target_type=item["target_type"],
            channel_model_id=item.get("channel_model_id"),
            child_combo_id=item.get("child_combo_id"),
            priority=int(item.get("priority", 1)),
            enabled=bool(item.get("enabled", True)),
        )
        session.add(target)
        created.append(target)
    await bump_catalog_version(session)
    await session.commit()
    return created


async def combo_target_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(orm.ComboTargetORM.combo_id, func.count(orm.ComboTargetORM.id)).group_by(
                orm.ComboTargetORM.combo_id
            )
        )
    ).all()
    return {row[0]: int(row[1]) for row in rows}


async def combo_ref_count(session: AsyncSession, combo_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(orm.ComboTargetORM.id)).where(orm.ComboTargetORM.child_combo_id == combo_id)
            )
        ).scalar_one()
        or 0
    )


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def _log_load_options() -> tuple[Any, ...]:
    """Eager-load the row-mode attempt trail for the rows that carry one.

    A JSON-mode deployment has no rows to load, so it pays nothing; a rows-mode
    page loads every trail of the page in one extra query instead of one query
    per row when a serializer touches ``log.attempts``.
    """
    if not attempt_filters.reads_attempt_rows():
        return ()
    return (selectinload(orm.RequestLogORM.attempts),)


async def add_request_log(session: AsyncSession, **values: Any) -> orm.RequestLogORM:
    """Insert one log row, writing its attempt trail in the configured shape.

    ``attempts`` (a payload list) wins over ``attempts_json`` (the string the
    request path already built): the JSON string is the wire between the two
    shapes, so a caller keeps writing what it always wrote and only this
    function decides where it lands (ADR-072 stage 1).
    """
    attempts = values.pop("attempts", None)
    rows_mode = attempt_filters.writes_attempt_rows()
    log = orm.RequestLogORM(id=new_id(), **values)
    if rows_mode:
        if attempts is None:
            attempts = attempt_filters.attempts_from_json(log.attempts_json)
        if not attempt_filters.writes_attempt_json():
            log.attempts_json = "[]"
    session.add(log)
    if rows_mode and attempts:
        # The parent row has to exist before the attempt inserts reach the FK.
        await session.flush()
        await attempt_filters.insert_attempts(session, log.id, attempts)
    await session.commit()
    return log


async def update_request_log(session: AsyncSession, log_id: str, **values: Any) -> int:
    """Patch one log row in place and report how many rows matched.

    Used by the deferred observability pipeline: a zero means the row vanished
    (retention purge) and the caller has to rebuild it. The caller commits, so a
    whole batch of patches shares one transaction.
    """
    result = await session.execute(
        update(orm.RequestLogORM).where(orm.RequestLogORM.id == log_id).values(**values)
    )
    return int(result.rowcount or 0)


async def list_logs(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    requested_model: str = "",
    model_exact: str = "",
    channel: str = "",
    status: str = "",
    attempt_status: str = "",
    search: str = "",
    api_key_label: str = "",
    user_id: str = "",
    start_ms: int | None = None,
    end_ms: int | None = None,
    stream: str = "",
    predicate: Any | None = None,
) -> tuple[list[orm.RequestLogORM], int]:
    stmt = select(orm.RequestLogORM).options(*_log_load_options())
    count_stmt = select(func.count(orm.RequestLogORM.id))
    conditions = _request_log_conditions(
        channel=channel,
        model=requested_model,
        model_match=await _prepare_model(session, requested_model),
        status=status,
        attempt_status=attempt_status,
        search=search,
        api_key_label=api_key_label,
        user_id=user_id,
        stream=stream,
        dialect=dialect_name(session),
    )
    if model_exact:
        # A drill-down pins one combo name even when the free-text model filter
        # is also in play, so the two never collapse into one condition.
        conditions.append(orm.RequestLogORM.requested_model == model_exact)
    if predicate is not None:
        # One compiled query-language predicate ANDed into the same where.
        conditions.append(predicate)
    if start_ms is not None:
        conditions.append(orm.RequestLogORM.created_at >= start_ms)
    if end_ms is not None:
        conditions.append(orm.RequestLogORM.created_at < end_ms)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    stmt = stmt.order_by(orm.RequestLogORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    return list((await session.execute(stmt)).scalars().all()), total

async def scan_request_log_facts(
    session: AsyncSession,
    *,
    limit: int,
    channel: str = "",
    model: str = "",
    status: str = "",
    attempt_status: str = "",
    source: str = "",
    search: str = "",
    api_key_label: str = "",
    user_id: str = "",
    stream: str = "",
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """The newest matching rows as plain dicts, plus a truncation flag.

    Only the columns an aggregate view reads are selected, and the scan stops
    at "limit" rows: a log table that outgrows the limit answers from its
    newest rows and says so instead of streaming the whole table into memory.
    """
    columns = [
        orm.RequestLogORM.id,
        orm.RequestLogORM.request_id,
        orm.RequestLogORM.created_at,
        orm.RequestLogORM.requested_model,
        orm.RequestLogORM.channel_name,
        orm.RequestLogORM.upstream_model,
        orm.RequestLogORM.http_status,
        orm.RequestLogORM.error_code,
        orm.RequestLogORM.prompt_tokens,
        orm.RequestLogORM.completion_tokens,
        orm.RequestLogORM.cached_tokens,
        orm.RequestLogORM.cost_usd,
        orm.RequestLogORM.ttft_ms,
        orm.RequestLogORM.total_ms,
    ]
    if not attempt_filters.reads_attempt_rows():
        # JSON mode: the blob is the trail. Rows mode loads it below in one IN
        # query for the page and never selects the (now empty) column.
        columns.append(orm.RequestLogORM.attempts_json)
    stmt = select(*columns)
    conditions = _request_log_conditions(
        channel=channel,
        model=model,
        model_match=await _prepare_model(session, model),
        status=status,
        attempt_status=attempt_status,
        source=source,
        search=search,
        api_key_label=api_key_label,
        user_id=user_id,
        stream=stream,
        dialect=dialect_name(session),
    )
    if start_ms is not None:
        conditions.append(orm.RequestLogORM.created_at >= start_ms)
    if end_ms is not None:
        conditions.append(orm.RequestLogORM.created_at < end_ms)
    stmt = (
        stmt.where(*conditions)
        .order_by(orm.RequestLogORM.created_at.desc())
        .limit(limit + 1)
    )
    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > limit
    facts = [dict(row._mapping) for row in rows[:limit]]
    if attempt_filters.reads_attempt_rows():
        grouped = await attempt_filters.attempts_for_logs(
            session, [fact["id"] for fact in facts]
        )
        for fact in facts:
            attempts = grouped.get(fact["id"], [])
            # The aggregate views still read ``attempts_json`` from the dict;
            # rebuilding it here keeps log_views (and any other scan consumer)
            # on one shape while the storage moves underneath (ADR-072).
            fact["attempts"] = attempts
            fact["attempts_json"] = attempt_filters.payloads_to_json(attempts)
    return facts, truncated


async def list_logs_for_segment(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    upstream_model: str = "",
    status_code: int | None = None,
    error_kinds: list[str] | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    channel: str = "",
    model: str = "",
    status: str = "",
    attempt_status: str = "",
    search: str = "",
    api_key_label: str = "",
    user_id: str = "",
    stream: str = "",
    predicate: Any | None = None,
) -> tuple[list[orm.RequestLogORM], int]:
    """The request rows behind one status segment, paged like the log list.

    A segment is identified by the upstream model it names, the status its
    attempts ended on, and the ``created_at`` window of its request rows. An
    unrouted segment names no model, so it matches on the status and its
    window alone.
    """
    conditions = _request_log_conditions(
        channel=channel,
        model=model,
        model_match=await _prepare_model(session, model),
        status=status,
        attempt_status=attempt_status,
        search=search,
        api_key_label=api_key_label,
        user_id=user_id,
        stream=stream,
        dialect=dialect_name(session),
    )
    if predicate is not None:
        conditions.append(predicate)
    if start_ms is not None:
        conditions.append(orm.RequestLogORM.created_at >= start_ms)
    if end_ms is not None:
        conditions.append(orm.RequestLogORM.created_at <= end_ms)
    if upstream_model:
        conditions.append(
            or_(
                *_attempt_field_fragments(
                    "upstream_model", upstream_model, dialect_name(session)
                )
            )
        )
    status_parts: list[Any] = []
    if status_code is not None:
        # The status a row ended on is not always the status of the hop inside
        # it (a fail-over row ends 200 after a 502 member), so the recorded
        # per-attempt code is part of the match.
        status_parts.append(orm.RequestLogORM.http_status == status_code)
        status_parts.extend(
            _attempt_field_fragments(
                "http_status", status_code, dialect_name(session)
            )
        )
    for kind in error_kinds or []:
        status_parts.extend(
            _attempt_field_fragments("error_code", kind, dialect_name(session))
        )
    if status_parts:
        conditions.append(or_(*status_parts))
    stmt = select(orm.RequestLogORM).options(*_log_load_options()).where(*conditions)
    count_stmt = select(func.count(orm.RequestLogORM.id)).where(*conditions)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    stmt = (
        stmt.order_by(orm.RequestLogORM.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await session.execute(stmt)).scalars().all()), total


#: The attempt fields a search box is expected to reach: the member channel, the
#: member upstream model, the model id and the failure itself, as json1 paths.
ATTEMPT_SEARCH_PATHS = (
    "$.channel_name",
    "$.channel_slug",
    "$.channel_model_id",
    "$.upstream_model",
    "$.error_code",
    "$.message",
)


def _attempt_json_rows() -> Any:
    """``attempts_json`` as json1 rows, tolerating NULL and malformed blobs.

    A row written by an older build (or by a half-failed write) need not hold a
    JSON array, and json_each() raises on malformed input -- so the value is
    normalized to ``[]`` before it is expanded.
    """
    guarded = case(
        (
            func.json_valid(orm.RequestLogORM.attempts_json) == 1,
            orm.RequestLogORM.attempts_json,
        ),
        else_=literal("[]"),
    )
    return func.json_each(guarded).table_valued("value")


def _attempt_search_condition(search: str) -> Any:
    """True when any attempt field contains the search text.

    json1 expands the stored array into rows, so the LIKE runs against one
    field's value instead of the whole blob: over the blob it also matched the
    JSON keys themselves (channel_slug, priority_path, ...) and could start in
    one value and end in the next, which turned a one-character search into a
    match on every row that had ever recorded an attempt.
    """
    attempts = _attempt_json_rows()
    return exists(
        select(literal(1))
        .select_from(attempts)
        .where(
            or_(
                *(
                    like_contains(func.json_extract(attempts.c.value, path), search)
                    for path in ATTEMPT_SEARCH_PATHS
                )
            )
        )
    )


def _attempt_value_condition(search: str) -> Any:
    """Non-SQLite fallback: a value-prefix match over the raw blob.

    CAPABILITY DOWNGRADE, not parity: this branch only matches from the start of
    a field's value and never sees the middle of it or the error message body,
    so a Postgres deployment searches strictly less than SQLite does. The real
    fix there is a jsonb expression over ``attempts_json::jsonb`` (e.g.
    ``jsonb_array_elements`` + ``->>``), which this codebase has no reason to
    carry until such a deployment exists. Do not read the two branches as
    equivalent when diagnosing a missing search hit.
    """
    escaped = like_pattern(search)[1:-1]
    parts: list[Any] = []
    for path in ATTEMPT_SEARCH_PATHS:
        field = path[2:]
        for separator in ('": "', '":"'):
            parts.append(
                orm.RequestLogORM.attempts_json.ilike(
                    f'%"{field}{separator}{escaped}%', escape=LIKE_ESCAPE
                )
            )
    return or_(*parts)


def _attempt_typed_equality(field: str, value: Any) -> Any:
    """json1 typed equality over the attempts array (SQLite only).

    A numeric filter cannot travel as a blob fragment: ``"http_status": 502``
    is a substring of ``"http_status": 5020``, so the prefix match reported
    hits that never happened. json_extract compares the parsed number instead.
    """
    attempts = _attempt_json_rows()
    return exists(
        select(literal(1))
        .select_from(attempts)
        .where(func.json_extract(attempts.c.value, f"$.{field}") == value)
    )


def _attempt_field_fragments(
    field: str, value: Any, dialect: str = SQLITE
) -> list[Any]:
    """``attempts_json`` fragments holding ``field == value``.

    Both the default ``json.dumps`` separators (``", "`` / ``": "``) and the
    compact ones are matched, and LIKE wildcards inside the value stay literal.
    Numbers and booleans cannot travel as substrings (``3200`` also hits
    ``32000``): on SQLite they go through a json_extract typed equality, on
    another backend through a separator-anchored LIKE, where the match has to
    end at the value instead of running into a longer one.
    """
    if attempt_filters.reads_attempt_rows():
        condition = attempt_filters.field_condition(field, value)
        if condition is not None:
            return [condition]
        # An unknown field keeps the JSON fallback below instead of silently
        # matching nothing.
    encoded = json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if dialect == SQLITE:
            return [_attempt_typed_equality(field, value)]
        return [
            like_contains(orm.RequestLogORM.attempts_json, f'"{field}": {encoded},'),
            like_contains(orm.RequestLogORM.attempts_json, f'"{field}": {encoded}}}'),
            like_contains(orm.RequestLogORM.attempts_json, f'"{field}":{encoded},'),
            like_contains(orm.RequestLogORM.attempts_json, f'"{field}":{encoded}}}'),
        ]
    return [
        like_contains(orm.RequestLogORM.attempts_json, f'"{field}": {encoded}'),
        like_contains(orm.RequestLogORM.attempts_json, f'"{field}":{encoded}'),
    ]


def _attempt_error_condition() -> Any:
    """True when at least one attempt recorded ``status: "error"``."""
    if attempt_filters.reads_attempt_rows():
        return attempt_filters.error_condition()
    return or_(
        like_contains(orm.RequestLogORM.attempts_json, '"status": "error"'),
        like_contains(orm.RequestLogORM.attempts_json, '"status":"error"'),
    )


def _attempt_any_condition() -> Any:
    """True when the row recorded at least one attempt (not ``[]``/NULL)."""
    if attempt_filters.reads_attempt_rows():
        return attempt_filters.any_condition()
    return and_(
        orm.RequestLogORM.attempts_json.is_not(None),
        orm.RequestLogORM.attempts_json != "",
        orm.RequestLogORM.attempts_json != "[]",
    )


async def _prepare_model(
    session: AsyncSession, model: str, *, op: str = "=="
) -> log_query.ModelMatch | None:
    """The widened ``model=`` matcher, or ``None`` when no model filter is set.

    ``model=`` answers "show me this model" -- the requested model, the upstream
    model that won, or a fail-over member of the combo that served the request
    (ADR-071 decision 14's sibling fix for the four-row report). Reading the combo
    tables is the only part that needs a session, so it happens here once and the
    synchronous condition builder gets a plain value object.
    """
    if not model:
        return None
    return await log_query.prepare_model_match(session, model, op=op)


def _request_log_conditions(
    *,
    dialect: str = SQLITE,
    channel: str = "",
    model_match: log_query.ModelMatch | None = None,
    model: str = "",
    status: str = "",
    attempt_status: str = "",
    source: str = "",
    search: str = "",
    api_key_label: str = "",
    user_id: str = "",
    stream: str = "",
) -> list[Any]:
    """Shared request-log filter vocabulary (dashboard, logs, timeseries).

    ``channel`` accepts the display name, the final channel-model id, or a
    channel recorded on a failed attempt, so a fail-over request is still found
    through the channel that actually errored. ``model`` is the requested
    model/combo call name. ``status`` maps success/error to http_status ranges,
    while ``attempt_status`` looks inside ``attempts_json`` (error = at least
    one failed attempt, success = attempts happened but none failed).
    ``source`` pins dashboard views to real chat traffic (``"chat"``) so probe
    rows never dilute them. Search matches ids, model/channel names and the
    attempt trail literally (LIKE wildcards escaped).
    """
    conditions: list[Any] = []
    if channel:
        conditions.append(
            or_(
                orm.RequestLogORM.channel_name == channel,
                orm.RequestLogORM.final_channel_model_id == channel,
                *_attempt_field_fragments("channel_name", channel, dialect),
                *_attempt_field_fragments("channel_slug", channel, dialect),
            )
        )
    if model:
        if model_match is not None:
            # The attempt trail is the third arm: a fail-over hop that only
            # recorded the model inside attempts_json still answers "model=".
            conditions.append(
                log_query.model_match_condition(
                    model_match, attempts=log_query.attempt_resolver(dialect)
                )
            )
        else:
            # Callers that cannot read the combo tables keep the pre-ADR-113
            # exact match; every caller in this module passes a prepared match.
            conditions.append(orm.RequestLogORM.requested_model == model)
    if status == "success":
        conditions.append(orm.RequestLogORM.http_status < 400)
    elif status == "error":
        conditions.append(orm.RequestLogORM.http_status >= 400)
    if attempt_status == "error":
        conditions.append(_attempt_error_condition())
    elif attempt_status == "success":
        conditions.append(and_(_attempt_any_condition(), ~_attempt_error_condition()))
    if source:
        conditions.append(orm.RequestLogORM.source == source)
    if stream == "stream":
        conditions.append(orm.RequestLogORM.is_stream.is_(True))
    elif stream == "json":
        conditions.append(orm.RequestLogORM.is_stream.is_(False))
    if api_key_label:
        conditions.append(orm.RequestLogORM.api_key_label == api_key_label)
    if user_id:
        conditions.append(orm.RequestLogORM.user_id == user_id)
    if search:
        if attempt_filters.reads_attempt_rows():
            # Rows mode: plain LIKE over six scalar columns, so every dialect
            # answers the full match (the JSON branch below is SQLite-only).
            attempt_hit = attempt_filters.search_condition(search)
        else:
            # json1 is SQLite-only; another backend keeps the weaker blob match.
            attempt_hit = (
                _attempt_search_condition(search)
                if dialect == SQLITE
                else _attempt_value_condition(search)
            )
        # The search box reaches the whole request, not just the winning row:
        # a member channel, a member upstream model, an error code and an
        # error message only ever live inside attempts_json, and the operator
        # name only lives on the user row.
        conditions.append(
            or_(
                like_contains(orm.RequestLogORM.request_id, search),
                like_contains(orm.RequestLogORM.upstream_request_id, search),
                like_contains(orm.RequestLogORM.requested_model, search),
                like_contains(orm.RequestLogORM.channel_name, search),
                like_contains(orm.RequestLogORM.upstream_model, search),
                like_contains(orm.RequestLogORM.response_model, search),
                attempt_hit,
                like_contains(orm.RequestLogORM.api_key_label, search),
                like_contains(orm.RequestLogORM.api_key_id, search),
                orm.RequestLogORM.user_id.in_(
                    select(orm.UserORM.id).where(
                        or_(
                            like_contains(orm.UserORM.username, search),
                            like_contains(orm.UserORM.display_name, search),
                        )
                    )
                ),
            )
        )
    return conditions


async def list_request_logs(
    session: AsyncSession,
    *,
    start_ms: int,
    end_ms: int,
    channel: str = "",
    model: str = "",
    status: str = "",
    attempt_status: str = "",
    source: str = "",
    search: str = "",
    api_key_label: str = "",
    user_id: str = "",
    stream: str = "",
) -> list[orm.RequestLogORM]:
    """Every request-log row in [start_ms, end_ms) matching the shared filters."""
    stmt = select(orm.RequestLogORM).options(*_log_load_options()).where(
        orm.RequestLogORM.created_at >= start_ms,
        orm.RequestLogORM.created_at < end_ms,
        *_request_log_conditions(
            channel=channel,
            model=model,
            model_match=await _prepare_model(session, model),
            status=status,
            attempt_status=attempt_status,
            source=source,
            search=search,
            api_key_label=api_key_label,
            user_id=user_id,
            stream=stream,
        dialect=dialect_name(session),
        ),
    )
    return list((await session.execute(stmt)).scalars().all())


async def replace_daily_aggregates(
    session: AsyncSession, days: list[int], entries: list[dict[str, Any]]
) -> None:
    """Replace daily_aggregate rows for whole UTC days in one transaction."""
    await session.execute(
        delete(orm.DailyAggregateORM).where(orm.DailyAggregateORM.day_ms.in_(days))
    )
    session.add_all(orm.DailyAggregateORM(**entry) for entry in entries)
    await session.commit()

async def get_log_by_request_id(session: AsyncSession, request_id: str) -> orm.RequestLogORM | None:
    return (
        await session.execute(
            select(orm.RequestLogORM)
            .options(*_log_load_options())
            .where(orm.RequestLogORM.request_id == request_id)
        )
    ).scalar_one_or_none()


async def get_log_by_any_id(session: AsyncSession, value: str) -> orm.RequestLogORM | None:
    """Resolve a gateway request id or the id returned by the upstream."""
    return (
        await session.execute(
            select(orm.RequestLogORM)
            .options(*_log_load_options())
            .where(
                or_(
                    orm.RequestLogORM.request_id == value,
                    orm.RequestLogORM.upstream_request_id == value,
                )
            )
            .order_by(orm.RequestLogORM.created_at.desc())
        )
    ).scalars().first()


LOG_FILTER_LIMIT = 50


async def log_filter_options(
    session: AsyncSession,
    *,
    q: str = "",
) -> tuple[list[str], list[str], list[str], list[orm.UserORM], dict[str, bool]]:
    """Distinct values used by the logs filter bar, hard-capped at 50 each.

    The extra row (limit 51) is used only to report whether more values exist;
    the response never returns more than ``LOG_FILTER_LIMIT`` entries.
    """
    needle = q.strip()

    async def distinct_values(column):  # type: ignore[no-untyped-def]
        stmt = select(column).where(column.is_not(None), column != "")
        if needle:
            stmt = stmt.where(like_contains(column, needle))
        rows = await session.execute(
            stmt.distinct().order_by(column).limit(LOG_FILTER_LIMIT + 1)
        )
        values = [value for value in rows.scalars().all() if value]
        truncated = len(values) > LOG_FILTER_LIMIT
        return values[:LOG_FILTER_LIMIT], truncated

    channels, channels_truncated = await distinct_values(orm.RequestLogORM.channel_name)
    models, models_truncated = await distinct_values(orm.RequestLogORM.requested_model)
    labels, labels_truncated = await distinct_values(orm.RequestLogORM.api_key_label)

    user_stmt = select(orm.UserORM)
    if needle:
        user_stmt = user_stmt.where(
            or_(
                like_contains(orm.UserORM.username, needle),
                like_contains(orm.UserORM.display_name, needle),
            )
        )
    user_stmt = user_stmt.order_by(orm.UserORM.username).limit(LOG_FILTER_LIMIT + 1)
    users = list((await session.execute(user_stmt)).scalars().all())
    users_truncated = len(users) > LOG_FILTER_LIMIT
    users = users[:LOG_FILTER_LIMIT]

    return (
        channels,
        models,
        labels,
        users,
        {
            "channels": channels_truncated,
            "models": models_truncated,
            "key_labels": labels_truncated,
            "users": users_truncated,
        },
    )


async def purge_logs_before(session: AsyncSession, before_ms: int) -> int:
    # Delete bodies explicitly first so the cascade also holds for databases
    # that do not enforce foreign keys by default.
    old_ids = select(orm.RequestLogORM.request_id).where(orm.RequestLogORM.created_at < before_ms)
    await session.execute(
        delete(orm.RequestBodyORM).where(orm.RequestBodyORM.request_id.in_(old_ids))
    )
    result = await session.execute(delete(orm.RequestLogORM).where(orm.RequestLogORM.created_at < before_ms))
    await session.commit()
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
async def save_request_body(
    session: AsyncSession,
    *,
    request_id: str,
    request_body: str | None = None,
    upstream_body: str | None = None,
    response_body: str | None = None,
    response_summary: str | None = None,
    reasoning_content: str | None = None,
) -> None:
    """Insert or replace the stored body row in one statement (L1 hot path).

    The previous read-modify-commit-refresh sequence cost four round trips
    (SELECT, INSERT/UPDATE, COMMIT, SELECT). request_id is the primary key
    and every captured column is supplied on each call, so an upsert
    reproduces the same write as INSERT + COMMIT. created_at is only set on
    insert; an update keeps the original row timestamp.
    """
    created_at = now_ms()
    dialect = dialect_name(session)
    stmt = conflict_insert(dialect, orm.RequestBodyORM).values(
        request_id=request_id,
        request_body=request_body,
        upstream_body=upstream_body,
        response_body=response_body,
        response_summary=response_summary,
        reasoning_content=reasoning_content,
        created_at=created_at,
    )
    await execute_with_lock_retry(
        session,
        stmt.on_conflict_do_update(
            index_elements=["request_id"],
            set_={
                "request_body": request_body,
                "upstream_body": upstream_body,
                "response_body": response_body,
                "response_summary": response_summary,
                "reasoning_content": reasoning_content,
            },
        ),
        dialect=dialect,
    )
    await session.commit()


async def get_request_body(session: AsyncSession, request_id: str) -> orm.RequestBodyORM | None:
    return await session.get(orm.RequestBodyORM, request_id)


async def clear_request_bodies_before(session: AsyncSession, before_ms: int) -> int:
    """NULL every stored body older than the cutoff, keeping the row."""
    stmt = (
        update(orm.RequestBodyORM)
        .where(
            orm.RequestBodyORM.created_at < before_ms,
            or_(
                orm.RequestBodyORM.request_body.is_not(None),
                orm.RequestBodyORM.upstream_body.is_not(None),
                orm.RequestBodyORM.response_body.is_not(None),
                orm.RequestBodyORM.response_summary.is_not(None),
                orm.RequestBodyORM.reasoning_content.is_not(None),
            ),
        )
        .values(
            request_body=None,
            upstream_body=None,
            response_body=None,
            response_summary=None,
            reasoning_content=None,
        )
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------
async def get_app_setting(session: AsyncSession, key: str) -> orm.AppSettingORM | None:
    return await session.get(orm.AppSettingORM, key)


async def save_app_setting(session: AsyncSession, setting: orm.AppSettingORM) -> orm.AppSettingORM:
    session.add(setting)
    await bump_catalog_version(session)
    await session.commit()
    await session.refresh(setting)
    return setting


async def delete_app_setting(session: AsyncSession, key: str) -> bool:
    setting = await session.get(orm.AppSettingORM, key)
    if setting is None:
        return False
    await session.delete(setting)
    await bump_catalog_version(session)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# Catalog version (ZS-2)
# ---------------------------------------------------------------------------
# Every management write bumps this key in the same transaction as the row it
# changes, so the background poll cannot miss a write and cannot observe half of
# one. The version lives in app_settings rather than a new table: the migration
# is just a row, and the key is already excluded from the routing snapshot.
CATALOG_VERSION_KEY = "catalog_version"

# Catalog instances bound to a database engine. A management write marks the
# bound snapshot dirty only after its transaction commits, so the same process
# sees its own write on the next request without paying a poll-interval delay.
# The registry is weak: dropping the app drops the engine and the catalog.
_WRITE_CATALOGS: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
_DIRTY_ARMED_KEY = "catalog_dirty_armed"


def bind_write_catalog(engine: Any, catalog: Any) -> None:
    """Bind a catalog so committed write sessions can invalidate it."""
    sync_engine = getattr(engine, "sync_engine", engine)
    try:
        _WRITE_CATALOGS[sync_engine] = catalog
    except TypeError:
        pass


def _mark_bound_catalog_dirty(sync_session: Any) -> None:
    try:
        bind = sync_session.get_bind()
    except Exception:  # noqa: BLE001
        return
    catalog = _WRITE_CATALOGS.get(bind)
    if catalog is not None:
        catalog.mark_dirty()


def _arm_catalog_dirty(session: AsyncSession) -> None:
    """Mark the bound catalog dirty when (and only when) this session commits."""
    sync = session.sync_session
    if sync.info.get(_DIRTY_ARMED_KEY):
        return
    sync.info[_DIRTY_ARMED_KEY] = True

    def _remove() -> None:
        sync.info.pop(_DIRTY_ARMED_KEY, None)
        for name, listener in (
            ("after_commit", _on_commit),
            ("after_rollback", _on_rollback),
        ):
            try:
                event.remove(sync, name, listener)
            except Exception:  # noqa: BLE001
                pass

    def _remove_soon() -> None:
        # SQLAlchemy iterates a live deque while dispatching this event; removing
        # the listener inside the callback raises "deque mutated during iteration".
        try:
            asyncio.get_running_loop().call_soon(_remove)
        except RuntimeError:
            _remove()

    def _on_commit(_session: Any) -> None:
        _mark_bound_catalog_dirty(sync)
        _remove_soon()

    def _on_rollback(_session: Any) -> None:
        _remove_soon()

    event.listen(sync, "after_commit", _on_commit)
    event.listen(sync, "after_rollback", _on_rollback)


def _catalog_token() -> str:
    return f"{now_ms()}-{new_id()}"


async def get_catalog_version(session: AsyncSession) -> str | None:
    row = await session.get(orm.AppSettingORM, CATALOG_VERSION_KEY)
    return row.value_json if row is not None else None


async def ensure_catalog_version(session: AsyncSession) -> str:
    row = await session.get(orm.AppSettingORM, CATALOG_VERSION_KEY)
    if row is None:
        token = _catalog_token()
        session.add(
            orm.AppSettingORM(
                key=CATALOG_VERSION_KEY,
                value_json=token,
                updated_at=now_ms(),
                updated_by="catalog",
            )
        )
        await session.commit()
        return token
    return row.value_json


async def bump_catalog_version(session: AsyncSession) -> str:
    """Advance the catalog version inside the caller's write transaction.

    The UPDATE runs before the caller commits, so the version change and the
    configuration change become visible together. If the row is missing (a
    database created before ZS-2 and loaded outside the app lifespan) it is
    inserted in the same transaction.
    """
    if not runtime.current_settings().zero_sql_pipeline:
        # Classic mode has no catalog consumers: skip the version bump so
        # admin writes keep the exact pre-ZS-2 statement shape.
        return ""
    token = _catalog_token()
    result = await session.execute(
        update(orm.AppSettingORM)
        .where(orm.AppSettingORM.key == CATALOG_VERSION_KEY)
        .values(value_json=token, updated_at=now_ms(), updated_by="catalog")
    )
    if not result.rowcount:
        session.add(
            orm.AppSettingORM(
                key=CATALOG_VERSION_KEY,
                value_json=token,
                updated_at=now_ms(),
                updated_by="catalog",
            )
        )
    _arm_catalog_dirty(session)
    return token


async def batch_touch_api_keys(session: AsyncSession, touches: dict[str, int]) -> int:
    """Persist buffered last_used_at values, one UPDATE per distinct timestamp."""
    if not touches:
        return 0
    grouped: dict[int, list[str]] = {}
    for key_id, touched_at in touches.items():
        grouped.setdefault(int(touched_at), []).append(key_id)
    touched = 0
    for touched_at, key_ids in grouped.items():
        result = await session.execute(
            update(orm.ApiKeyORM)
            .where(orm.ApiKeyORM.id.in_(key_ids))
            .values(last_used_at=touched_at)
            .execution_options(synchronize_session=False)
        )
        touched += int(result.rowcount or 0)
    await session.commit()
    return touched


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Model defaults (ADR-073)
# ---------------------------------------------------------------------------
# These rows are derived from the models.dev snapshot, not from the routing
# configuration: they are read by the create-model prefill and serve as the
# value-compare baseline (ADR-073 decision 6), so a write here must not bump
# the catalog version.
async def get_model_default(session: AsyncSession, model_id: str) -> orm.ModelDefaultORM | None:
    return await session.get(orm.ModelDefaultORM, model_id)


async def list_model_defaults(session: AsyncSession) -> list[orm.ModelDefaultORM]:
    result = await session.execute(select(orm.ModelDefaultORM).order_by(orm.ModelDefaultORM.model_id))
    return list(result.scalars().all())


async def replace_model_defaults(
    session: AsyncSession, rows: list[orm.ModelDefaultORM]
) -> int:
    """Replace the whole derived table in one transaction.

    The pull is a full snapshot, so a row that disappeared upstream must not
    linger: DELETE then INSERT keeps the table equal to the last good snapshot.
    """
    await session.execute(delete(orm.ModelDefaultORM))
    if rows:
        session.add_all(rows)
    await session.commit()
    return len(rows)


async def count_model_defaults(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(orm.ModelDefaultORM))
    return int(result.scalar_one() or 0)


async def list_channel_model_upstream_models(session: AsyncSession) -> list[str]:
    """Distinct upstream model names in use, used to derive default values."""
    result = await session.execute(
        select(orm.ChannelModelORM.upstream_model)
        .distinct()
        .order_by(orm.ChannelModelORM.upstream_model)
    )
    return [str(value) for value in result.scalars().all() if value]
