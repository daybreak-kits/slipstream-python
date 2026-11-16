from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode
from l01_core.ids import new_id
from l01_core.timeutil import now_ms
from l03_domain.combos import ComboGraphError, MAX_COMBO_DEPTH, validate_combo_graph
from l03_domain.failover import EMPTY_RULES, normalize_failover_rules, parse_failover_rules
from l03_domain.naming import COMBO_NAME_RE
from l04_repo import orm, repo
from l05_service.channel_models import is_model_enabled, is_published

OPENAI_PROTOCOL = "openai_chat"
TARGET_CHANNEL_MODEL = "channel_model"
TARGET_CHILD_COMBO = "child_combo"


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _max_attempts(value: Any, default: int = 3) -> int:
    try:
        attempts = int(value if value is not None else default)
    except (TypeError, ValueError) as error:
        raise AppError(ErrorCode.INVALID_REQUEST, "max_attempts 必须是整数") from error
    if attempts < 1 or attempts > 50:
        raise AppError(ErrorCode.INVALID_REQUEST, "max_attempts 必须在 1 到 50 之间")
    return attempts


def _failover_json(value: Any) -> str:
    try:
        normalized = normalize_failover_rules(value)
    except ValueError as error:
        raise AppError(ErrorCode.INVALID_REQUEST, str(error)) from error
    return json.dumps(normalized, ensure_ascii=False)


def _bump_affinity_epoch() -> None:
    """Drop session affinity after a write that can change the layer order.

    Imported lazily: routing imports this module's neighbours, so a module-level
    import here would close a cycle.
    """
    from l05_service.routing import bump_affinity_epoch

    bump_affinity_epoch()


def validate_combo_name(value: Any) -> str:
    name = _clean(value)
    if not name:
        raise AppError(ErrorCode.INVALID_REQUEST, "组合名称不能为空")
    if "/" in name:
        raise AppError(ErrorCode.INVALID_REQUEST, "组合名称不能包含斜杠")
    if not COMBO_NAME_RE.match(name):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "组合名称需以字母开头，仅含字母、数字、下划线或中划线，最长 64 位",
        )
    return name


async def _ensure_name_available(
    session: AsyncSession, name: str, exclude_id: str | None = None
) -> None:
    existing = await repo.get_combo_by_name(session, name)
    if existing is not None and existing.id != exclude_id:
        raise AppError(ErrorCode.CONFLICT, f"组合名称 {name} 已被使用", status=409)

    channel = await repo.get_channel_by_slug(session, name)
    if channel is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            f"组合名称 {name} 与渠道标识重名，请改用其他名称",
            status=409,
        )

    # Implicit groups are derived from bare call_model rows. Any such row owns
    # the bare name forever, even while disabled, so enabling it later cannot
    # silently shadow the combo.
    for model in await repo.list_all_channel_models(session):
        if model.call_model == name and "/" not in model.call_model:
            raise AppError(
                ErrorCode.CONFLICT,
                f"组合名称 {name} 与隐式分组名冲突，请改用其他名称",
                status=409,
            )


def _target_type_of(raw: dict[str, Any]) -> str:
    value = _clean(raw.get("target_type")) or TARGET_CHANNEL_MODEL
    if value in {TARGET_CHILD_COMBO, "combo", "public_model"}:
        return TARGET_CHILD_COMBO
    if value == TARGET_CHANNEL_MODEL:
        return TARGET_CHANNEL_MODEL
    raise AppError(ErrorCode.INVALID_REQUEST, "成员类型仅支持渠道模型或子组合")


def _duplicate_message(raw: dict[str, Any], target_type: str) -> str:
    label = _clean(raw.get("display_name")) or _clean(raw.get("channel_model_id")) or _clean(
        raw.get("child_combo_id")
    )
    kind = "渠道模型" if target_type == TARGET_CHANNEL_MODEL else "子组合"
    return f"成员目标重复：{kind} {label}"


async def _normalize_targets(
    session: AsyncSession,
    raw_targets: list[Any],
    combo_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for index, raw in enumerate(raw_targets):
        data = raw if isinstance(raw, dict) else raw.model_dump()
        target_type = _target_type_of(data)
        channel_model_id = _clean(data.get("channel_model_id")) or None
        child_combo_id = _clean(data.get("child_combo_id")) or None

        priority = int(data.get("priority") or 1)
        if priority < 1:
            raise AppError(ErrorCode.INVALID_REQUEST, f"第 {index + 1} 个成员的 priority 最小为 1")
        enabled = bool(data.get("enabled", True))

        if target_type == TARGET_CHANNEL_MODEL:
            if not channel_model_id or child_combo_id:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    f"第 {index + 1} 个成员必须且只能提供 channel_model_id",
                )
            model = await repo.get_channel_model(session, channel_model_id)
            if model is None:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    f"第 {index + 1} 个成员引用的渠道模型不存在",
                )
            key = (TARGET_CHANNEL_MODEL, channel_model_id)
            target = {
                "target_type": TARGET_CHANNEL_MODEL,
                "channel_model_id": channel_model_id,
                "child_combo_id": None,
                "priority": priority,
                "enabled": enabled,
            }
        else:
            if not child_combo_id or channel_model_id:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    f"第 {index + 1} 个成员必须且只能提供 child_combo_id",
                )
            if combo_id is not None and child_combo_id == combo_id:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    "组合不能引用自身，会形成循环引用",
                )
            child = await repo.get_combo(session, child_combo_id)
            if child is None:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    f"第 {index + 1} 个成员引用的子组合不存在",
                )
            if not child.enabled:
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"子组合 {child.name} 已封禁，不能作为启用的成员引用",
                    status=409,
                )
            key = (TARGET_CHILD_COMBO, child_combo_id)
            target = {
                "target_type": TARGET_CHILD_COMBO,
                "channel_model_id": None,
                "child_combo_id": child_combo_id,
                "priority": priority,
                "enabled": enabled,
            }

        if key in seen:
            raise AppError(ErrorCode.INVALID_REQUEST, _duplicate_message(data, target_type))
        seen.add(key)
        normalized.append(target)

    return normalized


async def _combo_edges(
    session: AsyncSession,
    *,
    proposed_id: str | None = None,
    proposed_targets: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, list[str]], list[orm.ComboORM]]:
    combos = await repo.list_combos(session)
    edges: dict[str, list[str]] = {combo.id: [] for combo in combos}
    for target in await repo.list_all_combo_targets(session):
        if target.child_combo_id and target.combo_id in edges:
            edges[target.combo_id].append(target.child_combo_id)

    if proposed_id is not None:
        edges.setdefault(proposed_id, [])
        edges[proposed_id] = [
            target["child_combo_id"]
            for target in (proposed_targets or [])
            if target.get("child_combo_id")
        ]
    return edges, combos


async def _validate_graph(
    session: AsyncSession,
    *,
    proposed_id: str,
    proposed_targets: list[dict[str, Any]],
) -> None:
    edges, _ = await _combo_edges(
        session, proposed_id=proposed_id, proposed_targets=proposed_targets
    )
    validate_combo_graph(edges)


def serialize_combo_target(
    target: orm.ComboTargetORM,
    *,
    model: orm.ChannelModelORM | None = None,
    channel: orm.ChannelORM | None = None,
    child: orm.ComboORM | None = None,
) -> dict[str, Any]:
    if target.target_type == TARGET_CHILD_COMBO or target.child_combo_id:
        return {
            "id": target.id,
            "target_type": TARGET_CHILD_COMBO,
            "priority": int(target.priority or 1),
            "enabled": bool(target.enabled),
            "available": bool(target.enabled and child is not None and child.enabled),
            "channel_model_id": None,
            "child_combo_id": target.child_combo_id,
            "channel_id": None,
            "channel_slug": "",
            "channel_name": "",
            "call_model": "",
            "upstream_model": "",
                "child_combo_name": child.name if child is not None else "",
            "child_combo_enabled": bool(child.enabled) if child is not None else False,
            "protocol": "",
            "created_at": target.created_at,
        }

    available = bool(
        target.enabled
        and channel is not None
        and model is not None
        and channel.enabled
        and is_model_enabled(model, now_ms())
        and channel.protocol == OPENAI_PROTOCOL
    )
    return {
        "id": target.id,
        "target_type": TARGET_CHANNEL_MODEL,
        "priority": int(target.priority or 1),
        "enabled": bool(target.enabled),
        "available": available,
        "channel_model_id": target.channel_model_id,
        "child_combo_id": None,
        "channel_id": channel.id if channel is not None else None,
        "channel_slug": channel.slug if channel is not None else "",
        "channel_name": channel.name if channel is not None else "",
        "call_model": model.call_model if model is not None else "",
        "upstream_model": model.upstream_model if model is not None else "",
        "child_combo_name": "",
        "child_combo_enabled": False,
        "protocol": channel.protocol if channel is not None else "",
        "created_at": target.created_at,
    }


def _available_leaf(
    model: orm.ChannelModelORM | None, channel: orm.ChannelORM | None
) -> bool:
    """A leaf that can serve a request right now."""
    return bool(
        model is not None
        and channel is not None
        and is_model_enabled(model, now_ms())
        and channel.enabled
        and channel.protocol == OPENAI_PROTOCOL
    )


def _published_leaf(
    model: orm.ChannelModelORM | None, channel: orm.ChannelORM | None
) -> bool:
    """A leaf that still deserves to be advertised (a cooling one counts)."""
    return bool(
        model is not None
        and channel is not None
        and is_published(model, now_ms())
        and channel.enabled
        and channel.protocol == OPENAI_PROTOCOL
    )


def _has_leaf(
    combo_id: str,
    targets: dict[str, list[orm.ComboTargetORM]],
    models: dict[str, orm.ChannelModelORM],
    channels: dict[str, orm.ChannelORM],
    combos: dict[str, orm.ComboORM],
    visiting: set[str],
    leaf_ok: Callable[[orm.ChannelModelORM | None, orm.ChannelORM | None], bool],
) -> bool:
    if combo_id in visiting:
        return False
    visiting.add(combo_id)
    try:
        for target in targets.get(combo_id, []):
            if not target.enabled:
                continue
            if target.child_combo_id:
                child = combos.get(target.child_combo_id)
                if child is not None and child.enabled and _has_leaf(
                    child.id, targets, models, channels, combos, visiting, leaf_ok
                ):
                    return True
                continue
            model = models.get(target.channel_model_id or "")
            channel = channels.get(model.channel_id) if model is not None else None
            if leaf_ok(model, channel):
                return True
        return False
    finally:
        visiting.discard(combo_id)


def _has_available_leaf(
    combo_id: str,
    targets: dict[str, list[orm.ComboTargetORM]],
    models: dict[str, orm.ChannelModelORM],
    channels: dict[str, orm.ChannelORM],
    combos: dict[str, orm.ComboORM],
    visiting: set[str],
) -> bool:
    """Whether the combo can serve a request through any reachable leaf."""
    return _has_leaf(
        combo_id, targets, models, channels, combos, visiting, _available_leaf
    )


async def published_combo_ids(session: AsyncSession) -> set[str]:
    """Combos that still deserve a row in the model directory (ADR-070 §3.2).

    The advertised set is wider than the callable set: a combo whose leaves are
    all cooling is still advertised, because the members revive on their own.
    """
    channels = {channel.id: channel for channel in await repo.list_channels(session)}
    models = {model.id: model for model in await repo.list_all_channel_models(session)}
    combos = {combo.id: combo for combo in await repo.list_combos(session)}
    targets: dict[str, list[orm.ComboTargetORM]] = {}
    for target in await repo.list_all_combo_targets(session):
        targets.setdefault(target.combo_id, []).append(target)
    return {
        combo.id
        for combo in combos.values()
        if combo.enabled
        and _has_leaf(
            combo.id, targets, models, channels, combos, set(), _published_leaf
        )
    }


async def serialize_combos(
    session: AsyncSession, combos: list[orm.ComboORM]
) -> list[dict[str, Any]]:
    channels = {channel.id: channel for channel in await repo.list_channels(session)}
    models = {model.id: model for model in await repo.list_all_channel_models(session)}
    combos_by_id = {combo.id: combo for combo in combos}
    edges, all_combos = await _combo_edges(session)
    depths = validate_combo_graph(edges)

    all_targets: dict[str, list[orm.ComboTargetORM]] = {combo.id: [] for combo in all_combos}
    for target in await repo.list_all_combo_targets(session):
        all_targets.setdefault(target.combo_id, []).append(target)
    all_by_id = {**combos_by_id, **{item.id: item for item in all_combos}}

    items: list[dict[str, Any]] = []
    for combo in combos:
        targets = all_targets.get(combo.id, [])
        members: list[dict[str, Any]] = []
        for target in targets:
            if target.child_combo_id:
                child = combos_by_id.get(target.child_combo_id) or await repo.get_combo(
                    session, target.child_combo_id
                )
                members.append(serialize_combo_target(target, child=child))
            else:
                model = models.get(target.channel_model_id or "")
                channel = channels.get(model.channel_id) if model is not None else None
                members.append(
                    serialize_combo_target(target, model=model, channel=channel)
                )

        failover_rules = parse_failover_rules(combo.failover_rules_json).to_dict()

        callable_ = _has_available_leaf(
            combo.id, all_targets, models, channels, all_by_id, set()
        )
        items.append(
            {
                "id": combo.id,
                "name": combo.name,
                "description": combo.description or "",
                "enabled": bool(combo.enabled),
                "max_attempts": max(1, int(combo.max_attempts or 1)),
                "route_strategy": combo.route_strategy or "",
                "route_window_minutes": int(combo.route_window_minutes or 0),
                "failover_rules": failover_rules,
                "member_count": len(targets),
                "available_count": sum(1 for member in members if member["available"]),
                "depth": depths.get(combo.id, 1),
                "max_depth": MAX_COMBO_DEPTH,
                "callable": bool(combo.enabled and callable_),
                "created_at": combo.created_at,
                "updated_at": combo.updated_at,
                "members": members,
            }
        )
    return items


async def serialize_combo(session: AsyncSession, combo: orm.ComboORM) -> dict[str, Any]:
    return (await serialize_combos(session, [combo]))[0]


async def list_combos(session: AsyncSession) -> list[dict[str, Any]]:
    return await serialize_combos(session, await repo.list_combos(session))


async def get_combo_or_404(session: AsyncSession, combo_id: str) -> orm.ComboORM:
    combo = await repo.get_combo(session, combo_id)
    if combo is None:
        raise AppError(ErrorCode.NOT_FOUND, "组合不存在或已被删除", status=404)
    return combo


async def create_combo(session: AsyncSession, payload: dict[str, Any]) -> orm.ComboORM:
    name = validate_combo_name(payload.get("name"))
    await _ensure_name_available(session, name)
    targets = await _normalize_targets(session, payload.get("targets") or [])
    new_combo_id = new_id()
    try:
        await _validate_graph(session, proposed_id=new_combo_id, proposed_targets=targets)
    except ComboGraphError as error:
        raise AppError(ErrorCode.INVALID_REQUEST, str(error)) from error

    combo = orm.ComboORM(
        id=new_combo_id,
        name=name,
        description=_clean(payload.get("description")),
        enabled=bool(payload.get("enabled", True)),
        max_attempts=_max_attempts(payload.get("max_attempts")),
        route_strategy=str(_clean(payload.get("route_strategy")) or "")[:16],
        route_window_minutes=int(payload.get("route_window_minutes") or 0),
        failover_rules_json=_failover_json(payload.get("failover_rules", EMPTY_RULES.to_dict())),
    )
    session.add(combo)
    await session.flush()
    await repo.replace_combo_targets(session, combo.id, targets)
    await session.refresh(combo)
    _bump_affinity_epoch()
    return combo


async def update_combo(
    session: AsyncSession,
    combo: orm.ComboORM,
    payload: dict[str, Any],
    provided: set[str],
) -> orm.ComboORM:
    if "name" in provided and payload.get("name") is not None:
        name = validate_combo_name(payload.get("name"))
        await _ensure_name_available(session, name, exclude_id=combo.id)
        combo.name = name

    if "description" in provided and payload.get("description") is not None:
        combo.description = _clean(payload.get("description"))

    if "enabled" in provided and payload.get("enabled") is not None:
        combo.enabled = bool(payload["enabled"])

    if "max_attempts" in provided and payload.get("max_attempts") is not None:
        combo.max_attempts = _max_attempts(payload["max_attempts"], combo.max_attempts or 3)

    if "failover_rules" in provided and payload.get("failover_rules") is not None:
        combo.failover_rules_json = _failover_json(payload["failover_rules"])

    if "route_strategy" in provided:
        combo.route_strategy = str(_clean(payload.get("route_strategy")) or "")[:16]

    if "route_window_minutes" in provided:
        combo.route_window_minutes = int(payload.get("route_window_minutes") or 0)

    if "targets" in provided and payload.get("targets") is not None:
        targets = await _normalize_targets(session, payload.get("targets") or [], combo.id)
        try:
            await _validate_graph(session, proposed_id=combo.id, proposed_targets=targets)
        except ComboGraphError as error:
            raise AppError(ErrorCode.INVALID_REQUEST, str(error)) from error
        await repo.save_combo(session, combo)
        await repo.replace_combo_targets(session, combo.id, targets)
        await session.refresh(combo)
        _bump_affinity_epoch()
        return combo

    saved = await repo.save_combo(session, combo)
    _bump_affinity_epoch()
    return saved


async def delete_combo(session: AsyncSession, combo: orm.ComboORM) -> None:
    references = await repo.combo_ref_count(session, combo.id)
    if references:
        raise AppError(
            ErrorCode.CONFLICT,
            f"该组合被 {references} 个子组合引用，请先解除引用后再删除",
            status=409,
        )
    await repo.delete_combo(session, combo)
    _bump_affinity_epoch()
