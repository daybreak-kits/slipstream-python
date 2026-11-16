from __future__ import annotations

import threading
import weakref
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core import runtime
from l01_core.timeutil import now_ms
from l02_infra.ttl_cache import CacheStats, TTLCache
from l03_domain.breaker import (
    member_view,
    parse_breaker_config,
    state_from_model,
    tick,
)
from l03_domain.model_types import DEFAULT_MODEL_TYPE, DEFAULT_MODEL_TYPE_SOURCE
from l04_repo import orm, repo
from l05_service import combos, groups
from l05_service.channel_models import (
    banned_until_ms,
    cooldown_reason,
    is_model_enabled,
    is_published,
    model_parameters_from_json,
    schedule_doc_for,
)

OPENAI_PROTOCOL = "openai_chat"

_MODEL_DIRECTORY_CACHES: "weakref.WeakKeyDictionary[Any, TTLCache[str, list[dict[str, Any]]]]" = (
    weakref.WeakKeyDictionary()
)
_MODEL_DIRECTORY_CACHE_LOCK = threading.Lock()
_MODEL_DIRECTORY_CACHE_KEY = "entries"


def _model_directory_cache(
    session: AsyncSession,
) -> TTLCache[str, list[dict[str, Any]]] | None:
    """Per-engine model directory cache. Weak keys keep test apps isolated."""
    try:
        bind = session.get_bind()
    except Exception:  # noqa: BLE001 - an unbound session simply skips the cache
        return None
    engine = getattr(bind, "sync_engine", bind)
    try:
        with _MODEL_DIRECTORY_CACHE_LOCK:
            cache = _MODEL_DIRECTORY_CACHES.get(engine)
            if cache is None:
                ttl_ms = int(
                    getattr(runtime.current_settings(), "catalog_directory_ttl_ms", 2_000)
                    or 0
                )
                cache = TTLCache(maxsize=8, ttl_ms=ttl_ms)
                _MODEL_DIRECTORY_CACHES[engine] = cache
            return cache
    except TypeError:
        # Ad-hoc Connection binds cannot be weak-referenced; serve uncached.
        return None


def model_directory_cache_stats() -> dict[str, Any]:
    """Aggregate hit/miss counters for the /v1/models directory caches."""
    with _MODEL_DIRECTORY_CACHE_LOCK:
        caches = list(_MODEL_DIRECTORY_CACHES.values())
    if not caches:
        result = CacheStats().as_dict()
        result["caches"] = 0
        return result
    stats = [cache.stats() for cache in caches]
    aggregate = CacheStats(
        hits=sum(item.hits for item in stats),
        misses=sum(item.misses for item in stats),
        sets=sum(item.sets for item in stats),
        evictions=sum(item.evictions for item in stats),
        expirations=sum(item.expirations for item in stats),
        size=sum(item.size for item in stats),
        maxsize=sum(item.maxsize for item in stats),
        default_ttl_ms=max(item.default_ttl_ms for item in stats),
    )
    result = aggregate.as_dict()
    result["caches"] = len(caches)
    return result


def reset_model_directory_cache() -> None:
    """Drop every cached directory and its counters (tests/operator reset)."""
    with _MODEL_DIRECTORY_CACHE_LOCK:
        caches = list(_MODEL_DIRECTORY_CACHES.values())
    for cache in caches:
        cache.clear()
        cache.reset_stats()


def _empty_parameters() -> dict[str, Any]:
    return {
        "max_input_tokens": 0,
        "max_output_tokens": 0,
        "reasoning_level": "",
        "input_types": ["text"],
        "output_types": ["text"],
    }


def _aggregate_parameters(models: list[orm.ChannelModelORM]) -> dict[str, Any]:
    """Widest parameter envelope across every member of a derived name."""
    parameters = _empty_parameters()
    for model in models:
        member = model_parameters_from_json(model.parameters_json)
        parameters["max_input_tokens"] = max(
            parameters["max_input_tokens"], member["max_input_tokens"]
        )
        parameters["max_output_tokens"] = max(
            parameters["max_output_tokens"], member["max_output_tokens"]
        )
        for key in ("input_types", "output_types"):
            for item in member[key]:
                if item not in parameters[key]:
                    parameters[key].append(item)
    return parameters


def direct_model_name(channel: orm.ChannelORM, model: orm.ChannelModelORM) -> str:
    return f"{channel.slug}/{model.call_model}"


async def list_direct_models(
    session: AsyncSession,
    *,
    openai_only: bool = False,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    """Channel models exposed as ``channel_slug/call_model`` direct entries.

    Callable surfaces keep the default (enabled models only). The management
    catalog passes ``include_disabled=True`` so a banned model stays listed and
    can be enabled again from the model directory.
    """
    channels = {channel.id: channel for channel in await repo.list_channels(session)}
    items: list[dict[str, Any]] = []
    for model in await repo.list_all_channel_models(session):
        channel = channels.get(model.channel_id)
        if channel is None or not channel.enabled:
            continue
        # 定时封禁到期即视为启用（惰性判活）。冷却中的条目照样可见，客户端因此
        # 知道名字仍在、何时恢复；只有永久封禁（无到期时刻）的条目才消失
        # （ADR-070 §3.2）。
        if not is_published(model, now_ms()) and not include_disabled:
            continue
        if openai_only and channel.protocol != OPENAI_PROTOCOL:
            continue
        items.append(
            {
                "id": model.id,
                "name": direct_model_name(channel, model),
                "channel_id": channel.id,
                "channel_slug": channel.slug,
                "channel_name": channel.name,
                "protocol": channel.protocol,
                "upstream_model": model.upstream_model,
                "call_model": model.call_model,
                "model_type": model.model_type or DEFAULT_MODEL_TYPE,
                "model_type_source": model.model_type_source or DEFAULT_MODEL_TYPE_SOURCE,
                "parameters": model_parameters_from_json(model.parameters_json),
                "input_price_per_1m": float(model.input_price_per_1m or 0),
                "output_price_per_1m": float(model.output_price_per_1m or 0),
                "cache_read_price_per_1m": (
                    None
                    if model.cache_read_price_per_1m is None
                    else float(model.cache_read_price_per_1m)
                ),
                "cache_write_price_per_1m": (
                    None
                    if model.cache_write_price_per_1m is None
                    else float(model.cache_write_price_per_1m)
                ),
                "enabled": is_model_enabled(model, now_ms()),
                # 定时封禁到期时刻（epoch ms）；管理目录据此展示「封禁至 …」，null 表示未定时封禁。
                "banned_until": banned_until_ms(model, now_ms()),
                "cooldown_reason": cooldown_reason(model, now_ms()),
                # 排期只下发 v2 文档（历史 v1 行归一后输出），无排期为 null。
                "schedule": schedule_doc_for(model),
                "created_at": model.created_at,
                "probe_status": model.probe_status or "",
                "probe_message": model.probe_message or "",
                "probed_at": model.probed_at,
            }
        )
    items.sort(key=lambda item: str(item["name"]))
    return items


async def list_callable_bare_models(session: AsyncSession) -> list[dict[str, Any]]:
    """Bare names that resolve to an explicit combo or an implicit group.

    Explicit combos come first, matching the resolver. A legacy name collision
    therefore exposes the combo rather than the derived group.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    published = await combos.published_combo_ids(session)
    for combo in await repo.list_combos(session):
        if not combo.enabled or "/" in combo.name or combo.name in seen:
            continue
        # ADR-070 §3.2: advertise while at least one leaf is published. A cooling
        # member counts (it revives on its own); a permanently banned one does not.
        if combo.id not in published:
            continue
        seen.add(combo.name)
        items.append(
            {
                "name": combo.name,
                "kind": "combo",
                "origin": "manual",
                "owned_by": "combo",
                "parameters": _empty_parameters(),
                "created_at": int(combo.created_at or 0),
            }
        )

    for group in await groups.list_groups(session):
        if group.name in seen:
            continue
        # Same rule as combos: cooling members keep the group advertised.
        if not group.published:
            continue
        seen.add(group.name)
        parameters = _aggregate_parameters(
            [member.model for member in group.published_members]
        )
        items.append(
            {
                "name": group.name,
                # 隐式分组在二元 kind 里属于组合，origin=auto 表示查询期派生、只读。
                "kind": "combo",
                "origin": "auto",
                "owned_by": "implicit-group",
                "parameters": parameters,
                "created_at": group.created_at,
            }
        )
    items.sort(key=lambda item: str(item["name"]))
    return items


async def list_model_entries(session: AsyncSession) -> list[dict[str, Any]]:
    """All enabled direct names followed by all callable bare names.

    The serialized directory is identical between config changes, so a short
    process-local TTL avoids rebuilding it and re-running the SELECTs on every
    GET /v1/models call.
    """
    cache = _model_directory_cache(session)
    if cache is not None:
        cached = cache.get(_MODEL_DIRECTORY_CACHE_KEY)
        if cached is not None:
            return list(cached)
    entries = await _build_model_entries(session)
    if cache is not None:
        cache.set(_MODEL_DIRECTORY_CACHE_KEY, entries)
    return entries


async def _build_model_entries(session: AsyncSession) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in await list_direct_models(session, openai_only=True):
        entries.append(
            {
                "id": item["name"],
                "kind": "direct",
                "owned_by": item["channel_slug"],
                "parameters": item["parameters"],
                "created_at": item["created_at"],
            }
        )
    for item in await list_callable_bare_models(session):
        entries.append(
            {
                "id": item["name"],
                "kind": item["kind"],
                "origin": item.get("origin"),
                "owned_by": item["owned_by"],
                "parameters": item["parameters"],
                "created_at": item["created_at"],
            }
        )
    return entries


def _duration_hint(until_ms: int | None, now: int) -> str:
    """剩余时长的中文说法。

    绝对时刻落在午夜时会显示成 00:00，看着像一个坏值（前端把缺失时刻同样
    渲染成 00:00），所以这里只说还要多久；算不出剩余时长就返回空串。
    """
    if until_ms is None or until_ms <= 0:
        return ""
    seconds = (int(until_ms) - int(now)) // 1000
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds} 秒后"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟后"
    hours, rest_minutes = divmod(minutes, 60)
    if hours < 24:
        if rest_minutes == 0:
            return f"{hours} 小时后"
        return f"{hours} 小时 {rest_minutes} 分钟后"
    days, rest_hours = divmod(hours, 24)
    if rest_hours == 0:
        return f"{days} 天后"
    return f"{days} 天 {rest_hours} 小时后"


def _direct_status_message(
    state,
    visible: str,
    now: int,
    reason: str | None = None,
    until: int | None = None,
) -> str:
    """One-line reason shown next to a direct catalog entry.

    The ban axis speaks for itself (ADR-070 D3): a permanent ban says 封禁, a
    timed cooldown says 冷却 and when it clears.
    """
    if reason == "MODEL_BANNED":
        return "已封禁"
    if reason == "MODEL_COOLDOWN":
        hint = _duration_hint(until, now)
        return f"冷却中，预计 {hint}恢复" if hint else "冷却中，恢复时间待定"
    if reason == "COOLDOWN_WINDOW":
        return "排期冷却窗口中"
    if visible == "disabled":
        return "已封禁"
    if state.health == "fault":
        hint = _duration_hint(state.fault_until, now)
        if hint:
            return f"冷却中（故障），预计 {hint}恢复"
        return "冷却中（故障），恢复时间待定"
    if state.health == "half_open":
        return "冷却已到，等待下次调用探测"
    if state.health == "open":
        # 失败计数为 0 说明这不是连续失败跳闸，而是额度或上游拒绝之后的冷却
        # 驻留；写成「连续失败 0 次」是假话，直接说冷却中。
        failures = max(0, int(state.consecutive_failures or 0))
        prefix = f"连续失败 {failures} 次，" if failures > 0 else ""
        hint = _duration_hint(state.cooldown_until, now)
        if not hint:
            return f"{prefix}熔断冷却中，恢复时间待定"
        return f"{prefix}预计 {hint}重试"
    return ""


def _member_status(
    model: orm.ChannelModelORM,
    channel: orm.ChannelORM | None,
    enabled: bool,
    now: int,
) -> tuple[str, str, int | None, int | None, int | None]:
    """Return (health, visible, cooldown_until, fault_until, retry_at) for one member."""
    config = parse_breaker_config(getattr(model, "cooldown_json", None))
    state, _ = tick(state_from_model(model), now, config)
    view = member_view(state, enabled, now, config)
    return (
        view["health"],
        view["visible"],
        view["cooldown_until"],
        view["fault_until"],
        view["retry_at"],
    )


# Ledger order for derived names: the worst visible state wins.
# fault > open > half_open > disabled > normal, so one disabled member
# already disables a combo unless something worse is happening.
_WORST_FIRST = ("fault", "open", "half_open", "disabled", "normal")


def _aggregate_members(
    members: list[tuple[str, str, int | None, int | None, int | None]], total: int
) -> dict[str, Any]:
    """Worst member state plus the earliest recovery time for a derived name."""
    if not members:
        return {
            "health_status": None,
            "visible_status": None,
            "cooldown_until": None,
            "fault_until": None,
            "status_message": "",
        }
    health = "normal"
    if any(member[0] in {"open", "half_open"} for member in members):
        health = "open"
    if any(member[0] == "fault" for member in members):
        health = "fault"
    visible = min((member[1] for member in members), key=_WORST_FIRST.index)
    retries = [member[4] for member in members if member[4] is not None]
    fault_untils = [member[3] for member in members if member[3] is not None]
    counts = {
        state: sum(1 for member in members if member[1] == state)
        for state in _WORST_FIRST
    }
    labels = (
        ("fault", "个故障冷却"),
        ("open", "个熔断冷却"),
        ("half_open", "个半开试探"),
        ("disabled", "个已封禁"),
    )
    parts = [f"{counts[state]} {label}" for state, label in labels if counts[state]]
    return {
        "health_status": health,
        "visible_status": visible,
        "cooldown_until": min(retries) if retries else None,
        "fault_until": min(fault_untils) if fault_untils else None,
        "status_message": (
            f"{total} 个成员中 " + "、".join(parts) if parts else ""
        ),
    }


def _combo_member_statuses(
    combo_id: str,
    targets: dict[str, list[orm.ComboTargetORM]],
    models: dict[str, orm.ChannelModelORM],
    channels: dict[str, orm.ChannelORM],
    enabled: bool,
    now: int,
    visiting: set[str] | None = None,
) -> list[tuple[str, str, int | None, int | None, int | None]]:
    """Resolve a combo's leaf channel models, following nested child combos."""
    visiting = set() if visiting is None else visiting
    if combo_id in visiting:
        return []
    visiting.add(combo_id)
    try:
        statuses: list[tuple[str, str, int | None, int | None, int | None]] = []
        for target in targets.get(combo_id, []):
            member_enabled = enabled and bool(target.enabled)
            if target.child_combo_id:
                statuses.extend(
                    _combo_member_statuses(
                        target.child_combo_id,
                        targets,
                        models,
                        channels,
                        member_enabled,
                        now,
                        visiting,
                    )
                )
                continue
            model = models.get(target.channel_model_id or "")
            if model is None:
                continue
            channel = channels.get(model.channel_id)
            statuses.append(
                _member_status(model, channel, member_enabled and is_model_enabled(model, now), now)
            )
        return statuses
    finally:
        visiting.discard(combo_id)


def _derived_entry(
    kind: str,
    name: str,
    enabled: bool,
    created_at: int | None,
    parameters: dict[str, Any],
    call_model: str = "",
    origin: str = "manual",
) -> dict[str, Any]:
    """Shared shape for combo entries; callers add id and health.

    Both origins report kind="combo"; origin tells the UI whether the row is an
    editable explicit combo (manual) or a read-only derived group (auto).
    """
    return {
        "name": name,
        "kind": kind,
        "origin": origin,
        "status": "enabled" if enabled else "disabled",
        "channel_id": "",
        "channel_slug": "",
        # 我们自己拼给日志/目录的文案：两种都是组合，来源不同。
        "channel_name": "手动组合" if origin == "manual" else "自动组合",
        "protocol": "",
        "upstream_model": "",
        "call_model": call_model,
        # 组合/分组由多个模型聚合而来，没有单一模型类型。
        "model_type": DEFAULT_MODEL_TYPE,
        "model_type_source": DEFAULT_MODEL_TYPE_SOURCE,
        "parameters": parameters,
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
        # 组合/分组没有单一渠道价目，缓存价给 None 而不是 0，避免显示成"命中免费"。
        "cache_read_price_per_1m": None,
        "cache_write_price_per_1m": None,
        "enabled": bool(enabled),
        "created_at": int(created_at or 0),
        "probe_status": "",
        "probe_message": "",
        "probed_at": None,
    }


async def list_catalog_entries(
    session: AsyncSession, *, include_disabled: bool = False
) -> list[dict[str, Any]]:
    """Unified read-only catalog: direct models, explicit combos, implicit groups.

    Combos already stay listed while disabled; ``include_disabled`` gives direct
    rows the same treatment so the model directory can re-enable them.
    """
    now = now_ms()
    channels = {channel.id: channel for channel in await repo.list_channels(session)}
    models = {model.id: model for model in await repo.list_all_channel_models(session)}
    targets: dict[str, list[orm.ComboTargetORM]] = {}
    for target in await repo.list_all_combo_targets(session):
        targets.setdefault(target.combo_id, []).append(target)

    entries: list[dict[str, Any]] = []
    for item in await list_direct_models(session, include_disabled=include_disabled):
        model = models.get(item["id"])
        channel = channels.get(model.channel_id) if model is not None else None
        failures = 0
        if model is not None:
            config = parse_breaker_config(getattr(model, "cooldown_json", None))
            state, _ = tick(state_from_model(model), now, config)
            view = member_view(
                state,
                bool(is_model_enabled(model, now) and channel is not None and channel.enabled),
                now,
                config,
            )
            failures = state.consecutive_failures
            health = view["health"]
            visible = view["visible"]
            cooldown_until = view["cooldown_until"]
            fault_until = view["fault_until"]
        else:
            health, visible, cooldown_until, fault_until = "normal", "normal", None, None
        entries.append(
            {
                **item,
                "kind": "direct",
                "status": "enabled" if item["enabled"] else "disabled",
                "health_status": health,
                "visible_status": visible,
                "consecutive_failures": failures,
                "cooldown_until": cooldown_until,
                "fault_until": fault_until,
                "status_message": (
                    _direct_status_message(
                        view["state"],
                        visible,
                        now,
                        reason=item.get("cooldown_reason"),
                        until=item.get("banned_until"),
                    )
                    if model is not None
                    else ""
                ),
            }
        )
    for combo in await repo.list_combos(session):
        if "/" in combo.name:
            continue
        members = _combo_member_statuses(
            combo.id, targets, models, channels, bool(combo.enabled), now
        )
        health = _aggregate_members(members, total=len(members))
        entries.append(
            {
                "id": combo.id,
                **health,
                **_derived_entry(
                    "combo",
                    combo.name,
                    bool(combo.enabled),
                    combo.created_at,
                    _empty_parameters(),
                    origin="manual",
                ),
            }
        )
    for group in await groups.list_groups(session, callable_only=False):
        member_statuses = [
            _member_status(member.model, member.channel, member.available, now)
            for member in group.members
        ]
        health = _aggregate_members(member_statuses, total=len(member_statuses))
        entries.append(
            {
                "id": f"group:{group.name}",
                **health,
                **_derived_entry(
                    # 隐式分组对外也是 kind="combo"，只读（origin="auto"）。
                    "combo",
                    group.name,
                    bool(group.available_members),
                    group.created_at,
                    _aggregate_parameters([member.model for member in group.members]),
                    group.name,
                    "auto",
                ),
            }
        )
    # kind 只有 direct/combo 两个值，组合整体排在直连之前，组内按名字稳定排序。
    entries.sort(key=lambda item: (str(item["kind"]), str(item["name"])))
    return entries
