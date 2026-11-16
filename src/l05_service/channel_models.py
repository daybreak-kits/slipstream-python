from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode
from l01_core.ids import new_id
from l01_core.timeutil import now_ms
from l03_domain.breaker import (
    apply_to_model,
    config_to_dict,
    member_view,
    on_reset,
    parse_breaker_config,
    state_from_model,
    tick,
)
from l03_domain.model_types import (
    DEFAULT_MODEL_TYPE,
    DEFAULT_MODEL_TYPE_SOURCE,
    infer_model_type_source,
    is_auto_model_type,
    normalize_model_type,
    normalize_model_type_source,
)
from l03_domain.naming import CALL_MODEL_RE, UPSTREAM_MODEL_RE
from l03_domain.schedule import (
    DEFAULT_TZ,
    Schedule,
    is_offline,
    parse_schedule,
    parse_schedule_cached,
    rules_to_dict,
)
from l04_repo import orm, repo
from l05_service.channels import get_channel_or_404

DEFAULT_MODEL_PARAMETERS: dict[str, Any] = {
    "max_input_tokens": 0,
    "max_output_tokens": 0,
    "reasoning_level": "",
    "input_types": ["text"],
    "output_types": ["text"],
}
INPUT_TYPES = ("text", "image", "audio", "video", "file")
OUTPUT_TYPES = ("text", "image", "audio", "embedding")
# Capability flags (ADR-073): tri-state booleans stored inside parameters_json.
# None means "unknown" and is never written, so old rows stay unchanged.
CAPABILITY_FLAGS = ("tool_call", "reasoning", "attachment", "structured_output")

# ADR-070 D3: skip reasons for the ban / cooldown / window axis. They replace the
# ambiguous MODEL_DISABLED / SCHEDULED_OFFLINE pair in routing and attempt logs.
SKIP_MODEL_BANNED = "MODEL_BANNED"
SKIP_MODEL_COOLDOWN = "MODEL_COOLDOWN"
SKIP_COOLDOWN_WINDOW = "COOLDOWN_WINDOW"


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _optional_price(value: Any) -> float | None:
    """None 表示未配置（回退输入原价）；数字统一转 float。"""
    return None if value is None else float(value)


def validate_upstream_model(value: Any) -> str:
    text = _clean(value)
    if not UPSTREAM_MODEL_RE.match(text):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "上游模型名不合法：需字母/数字开头，可含 . _ : - / #，最长 128 位",
        )
    return text


def validate_call_model(value: Any) -> str:
    text = _clean(value)
    if not CALL_MODEL_RE.match(text):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "调用模型不合法：需字母/数字开头，可含 . _ : - / #，最长 128 位",
        )
    return text


def validate_model_type(value: Any, *, default: str = DEFAULT_MODEL_TYPE) -> str:
    try:
        return normalize_model_type(_clean(value) or None, default=default)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def validate_model_type_source(
    value: Any, *, default: str = DEFAULT_MODEL_TYPE_SOURCE
) -> str:
    try:
        return normalize_model_type_source(_clean(value) or None, default=default)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _create_model_type(payload: dict[str, Any]) -> tuple[str, str]:
    """Resolve (model_type, model_type_source) for a create payload.

    An explicit type is a human decision, so it is recorded as ``manual``;
    without one the model keeps the chat default and the ``default`` source.
    ``auto`` is not a stored value: it re-infers both fields from the upstream
    model name.
    """
    requested = payload.get("model_type")
    if requested is None:
        return DEFAULT_MODEL_TYPE, DEFAULT_MODEL_TYPE_SOURCE
    if is_auto_model_type(requested):
        return infer_model_type_source(payload.get("upstream_model") or "")
    return (
        validate_model_type(requested),
        validate_model_type_source(payload.get("model_type_source"), default="manual"),
    )


def _normalize_token_count(value: Any, field: str) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, f"{field}必须是整数") from exc
    if number < 0 or number > 100_000_000:
        raise AppError(ErrorCode.INVALID_REQUEST, f"{field}需在 0–100000000 之间")
    return number


def _normalize_type_list(value: Any, allowed: tuple[str, ...], field: str) -> list[str]:
    if value is None:
        return ["text"]
    if not isinstance(value, (list, tuple)):
        raise AppError(ErrorCode.INVALID_REQUEST, f"{field}必须是数组")
    cleaned: list[str] = []
    for item in value:
        text = _clean(item).lower()
        if not text:
            continue
        if text not in allowed:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                f"{field}仅支持：{'、'.join(allowed)}",
            )
        if text not in cleaned:
            cleaned.append(text)
    return cleaned or ["text"]


def normalize_model_parameters(value: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize the five model parameters shared by API, DB and UI."""
    merged = {**DEFAULT_MODEL_PARAMETERS, **(base or {})}
    if value is None:
        return merged
    if not isinstance(value, dict):
        raise AppError(ErrorCode.INVALID_REQUEST, "模型参数必须是对象")
    for key in ("max_input_tokens", "max_output_tokens"):
        if key in value:
            merged[key] = _normalize_token_count(value.get(key), key)
    if "reasoning_level" in value:
        reasoning = _clean(value.get("reasoning_level"))[:32]
        merged["reasoning_level"] = reasoning
    if "input_types" in value:
        merged["input_types"] = _normalize_type_list(
            value.get("input_types"), INPUT_TYPES, "input_types"
        )
    if "output_types" in value:
        merged["output_types"] = _normalize_type_list(
            value.get("output_types"), OUTPUT_TYPES, "output_types"
        )
    for flag in CAPABILITY_FLAGS:
        if flag not in value:
            continue
        flag_value = value.get(flag)
        if flag_value is None:
            # 未设置：不覆盖已有值，也不落库（None 不写 JSON）。
            continue
        merged[flag] = bool(flag_value)
    return merged


def model_parameters_from_json(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    try:
        return normalize_model_parameters(parsed)
    except AppError:
        return dict(DEFAULT_MODEL_PARAMETERS)


def _schedule_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value


def _schedule_json(value: Any) -> str:
    """Normalize a schedule payload into the stored v2 ``{version, timezone, rules}`` JSON."""
    raw = _schedule_payload(value)
    schedule = parse_schedule(raw if raw is not None else {"timezone": DEFAULT_TZ})
    return json.dumps(rules_to_dict(schedule), ensure_ascii=False)


def schedule_for(model: orm.ChannelModelORM) -> Schedule | None:
    """The model's parsed schedule, or ``None`` when it has no active rules.

    Parsing is memoised on the stored JSON text (``parse_schedule_cached``), so the
    routing hot path pays no JSON decode and no date arithmetic per attempt
    (ADR-070 §2.3).
    """
    raw = model.schedule_json
    if not raw:
        return None
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    schedule = parse_schedule_cached(text)
    return schedule if schedule.rules else None


def schedule_doc_for(model: orm.ChannelModelORM) -> dict[str, Any] | None:
    """The stored schedule as the v2 wire document, or ``None`` without rules.

    Legacy v1 rows are normalized on the way out, so the frontend edit dialog
    always round-trips one shape (ADR-070 §1.3).
    """
    raw = model.schedule_json
    if not raw:
        return None
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    schedule = parse_schedule_cached(text)
    return rules_to_dict(schedule) if schedule.rules else None


def _merge_cooldown(current: str | None, patch: dict[str, Any] | None) -> str:
    base = parse_breaker_config(current)
    data = config_to_dict(base)
    for key, value in (patch or {}).items():
        if value is not None:
            data[key] = value
    merged = parse_breaker_config(data, base)
    return json.dumps(config_to_dict(merged), ensure_ascii=False)


def banned_until_ms(model: orm.ChannelModelORM, at_ms: int) -> int | None:
    """The active timed ban's expiry in epoch ms, or None when there is none."""
    raw = model.banned_until
    if raw is None:
        return None
    value = int(raw)
    return value if value > at_ms else None


def is_model_enabled(model: orm.ChannelModelORM, at_ms: int) -> bool:
    """Whether the model may be called now, honouring lazy ban expiry.

    'enabled' alone cannot express a timed ban: a banned row keeps
    enabled=False on disk and carries banned_until. Reads evaluate the deadline
    here instead of relying on a background unban job, so an expired ban reads
    as enabled again without any write.
    """
    if bool(model.enabled):
        return True
    raw = model.banned_until
    return raw is not None and int(raw) <= at_ms


def is_published(model: orm.ChannelModelORM, at_ms: int) -> bool:
    """Whether the model belongs in the public model directory.

    A permanent ban (``enabled=false`` without ``banned_until``) removes the row
    from ``/v1/models``; a timed cooldown keeps it published, so clients still see
    the name while it recovers on its own (ADR-070 §3.2).
    """
    if is_model_enabled(model, at_ms):
        return True
    return model.banned_until is not None


def ban_reason(model: orm.ChannelModelORM, at_ms: int) -> str | None:
    """The ban-axis skip reason: ``MODEL_BANNED``, ``MODEL_COOLDOWN`` or ``None``."""
    if is_model_enabled(model, at_ms):
        return None
    return SKIP_MODEL_BANNED if model.banned_until is None else SKIP_MODEL_COOLDOWN


def cooldown_reason(model: orm.ChannelModelORM, at_ms: int) -> str | None:
    """Why the model cannot be called right now, on the ban and window axes.

    Breaker states (``open`` / ``fault``) are not covered here; they belong to the
    attempt-log side of :mod:`l05_service.routing`.
    """
    reason = ban_reason(model, at_ms)
    if reason is not None:
        return reason
    schedule = schedule_for(model)
    if schedule is not None and is_offline(at_ms, schedule):
        return SKIP_COOLDOWN_WINDOW
    return None


def parse_ban_until(value: str) -> int:
    """Parse an ISO-8601 instant into epoch ms; a naive value is read as UTC."""
    text = (value or "").strip()
    if not text:
        raise ValueError("banned_until 需为 ISO-8601 时间字符串")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("banned_until 需为 ISO-8601 时间字符串") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def serialize_channel_model(model: orm.ChannelModelORM) -> dict[str, Any]:
    now = now_ms()
    config = parse_breaker_config(model.cooldown_json)
    state, _ = tick(state_from_model(model), now, config)
    # 定时封禁到期即视为启用：enabled 给有效值，banned_until 表明是否仍在封禁期。
    effective = is_model_enabled(model, now)
    view = member_view(state, effective, now, config)
    breaker = view["state"]
    return {
        "id": model.id,
        "channel_id": model.channel_id,
        "upstream_model": model.upstream_model,
        "call_model": model.call_model,
        "model_type": model.model_type or DEFAULT_MODEL_TYPE,
        "model_type_source": model.model_type_source or DEFAULT_MODEL_TYPE_SOURCE,
        "parameters": model_parameters_from_json(model.parameters_json),
        "input_price_per_1m": float(model.input_price_per_1m or 0),
        "output_price_per_1m": float(model.output_price_per_1m or 0),
        "cache_read_price_per_1m": _optional_price(model.cache_read_price_per_1m),
        "cache_write_price_per_1m": _optional_price(model.cache_write_price_per_1m),
        "enabled": effective,
        "banned_until": banned_until_ms(model, now),
        "cooldown_reason": cooldown_reason(model, now),
        "health_status": breaker.health,
        "visible_status": view["visible"],
        "consecutive_failures": breaker.consecutive_failures,
        "consecutive_trip_count": breaker.consecutive_trip_count,
        "escalation_trip_count": breaker.escalation_trip_count,
        "opened_at": breaker.opened_at,
        "cooldown_until": view["cooldown_until"],
        "fault_until": view["fault_until"],
        "next_retry_at": view["retry_at"],
        "schedule": rules_to_dict(parse_schedule(_schedule_payload(model.schedule_json))),
        "cooldown": config_to_dict(config),
        "probe_status": model.probe_status or "unknown",
        "probe_latency_ms": model.probe_latency_ms,
        "probe_message": model.probe_message or "",
        "probed_at": model.probed_at,
        "probe_default": bool(model.probe_default),
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }


async def get_channel_model_or_404(
    session: AsyncSession, model_id: str
) -> orm.ChannelModelORM:
    model = await repo.get_channel_model(session, model_id)
    if model is None:
        raise AppError(ErrorCode.NOT_FOUND, "渠道模型不存在或已被删除", status=404)
    return model


async def _ensure_call_model_available(
    session: AsyncSession,
    channel_id: str,
    call_model: str,
    exclude_id: str | None = None,
) -> None:
    existing = await repo.get_channel_model_by_call(session, channel_id, call_model)
    if existing is not None and existing.id != exclude_id:
        raise AppError(
            ErrorCode.CONFLICT,
            f"调用模型 {call_model} 在本渠道已存在",
            status=409,
        )
    if "/" not in call_model:
        combo = await repo.get_combo_by_name(session, call_model)
        if combo is not None:
            raise AppError(
                ErrorCode.CONFLICT,
                f"调用模型 {call_model} 与显式组合同名，请改用其他名称",
                status=409,
            )
        channel_with_slug = await repo.get_channel_by_slug(session, call_model)
        if channel_with_slug is not None:
            raise AppError(
                ErrorCode.CONFLICT,
                f"调用模型 {call_model} 与渠道标识重名，请改用其他名称",
                status=409,
            )


async def list_channel_models(
    session: AsyncSession, channel_id: str
) -> list[dict[str, Any]]:
    await get_channel_or_404(session, channel_id)
    models = await repo.list_channel_models(session, channel_id=channel_id)
    return [serialize_channel_model(model) for model in models]


def _bump_affinity_epoch() -> None:
    """Drop session affinity after a write that can change the layer order.

    Imported lazily: routing imports is_model_enabled from this module, so a
    module-level import here would close a cycle.
    """
    from l05_service.routing import bump_affinity_epoch

    bump_affinity_epoch()


async def create_channel_model(
    session: AsyncSession, channel_id: str, payload: dict[str, Any]
) -> orm.ChannelModelORM:
    await get_channel_or_404(session, channel_id)
    upstream_model = validate_upstream_model(payload.get("upstream_model"))
    call_model = validate_call_model(_clean(payload.get("call_model")) or upstream_model)
    model_type, model_type_source = _create_model_type(payload)
    await _ensure_call_model_available(session, channel_id, call_model)
    siblings = await repo.list_channel_models(session, channel_id=channel_id)

    model = orm.ChannelModelORM(
        id=new_id(),
        channel_id=channel_id,
        upstream_model=upstream_model,
        call_model=call_model,
        model_type=model_type,
        model_type_source=model_type_source,
        input_price_per_1m=float(payload.get("input_price_per_1m") or 0),
        output_price_per_1m=float(payload.get("output_price_per_1m") or 0),
        cache_read_price_per_1m=_optional_price(payload.get("cache_read_price_per_1m")),
        cache_write_price_per_1m=_optional_price(payload.get("cache_write_price_per_1m")),
        parameters_json=json.dumps(
            normalize_model_parameters(payload.get("parameters")), ensure_ascii=False
        ),
        enabled=bool(payload.get("enabled", True)),
        schedule_json=_schedule_json(payload.get("schedule")),
        cooldown_json=_merge_cooldown(None, payload.get("cooldown")),
        # The first model added to a channel becomes its default probe target.
        probe_default=not any(bool(item.probe_default) for item in siblings),
    )
    try:
        saved = await repo.save_channel_model(session, model)
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            f"调用模型 {call_model} 在本渠道已存在",
            status=409,
        ) from exc
    _bump_affinity_epoch()
    return saved


async def bulk_create_channel_models(
    session: AsyncSession, channel_id: str, upstream_models: list[str]
) -> dict[str, Any]:
    """Add selected upstream models, skipping duplicates and naming conflicts."""
    await get_channel_or_404(session, channel_id)
    existing = await repo.list_channel_models(session, channel_id=channel_id)
    known_upstream = {model.upstream_model for model in existing}
    known_calls = {model.call_model for model in existing}
    created: list[orm.ChannelModelORM] = []
    errors: list[str] = []
    skipped = 0

    for raw in upstream_models:
        try:
            upstream_model = validate_upstream_model(raw)
        except AppError as exc:
            skipped += 1
            errors.append(f"{raw}：{exc.message}")
            continue
        if upstream_model in known_upstream:
            skipped += 1
            continue
        if upstream_model in known_calls:
            skipped += 1
            errors.append(f"{upstream_model}：调用模型名称已被占用")
            continue
        payload: dict[str, Any] = {"upstream_model": upstream_model}
        inferred_type, inferred_source = infer_model_type_source(upstream_model)
        if inferred_source == "upstream":
            # 上游同步：能从模型名判出类型时记录来源，便于和人工改动的行区分。
            payload["model_type"] = inferred_type
            payload["model_type_source"] = inferred_source
        try:
            model = await create_channel_model(session, channel_id, payload)
        except AppError as exc:
            skipped += 1
            errors.append(f"{upstream_model}：{exc.message}")
            continue
        created.append(model)
        known_upstream.add(model.upstream_model)
        known_calls.add(model.call_model)

    return {
        "created": len(created),
        "skipped": skipped,
        "errors": errors[:20],
        "items": [serialize_channel_model(model) for model in created],
    }


async def update_channel_model(
    session: AsyncSession,
    model: orm.ChannelModelORM,
    payload: dict[str, Any],
    provided: set[str],
) -> orm.ChannelModelORM:
    if "upstream_model" in provided and payload.get("upstream_model") is not None:
        model.upstream_model = validate_upstream_model(payload.get("upstream_model"))

    if "call_model" in provided and payload.get("call_model") is not None:
        call_model = validate_call_model(payload.get("call_model"))
        await _ensure_call_model_available(
            session, model.channel_id, call_model, exclude_id=model.id
        )
        model.call_model = call_model

    if "model_type" in provided and payload.get("model_type") is not None:
        requested_type = payload.get("model_type")
        if is_auto_model_type(requested_type):
            # "auto" 不是存储值：按上游模型名重新推断类型与来源。
            model.model_type, model.model_type_source = infer_model_type_source(
                model.upstream_model
            )
        else:
            model.model_type = validate_model_type(requested_type)
            # 人工改过类型：来源一律转 manual（除非调用方显式声明了来源）。
            model.model_type_source = validate_model_type_source(
                payload.get("model_type_source"), default="manual"
            ) if payload.get("model_type_source") is not None else "manual"

    if "input_price_per_1m" in provided and payload.get("input_price_per_1m") is not None:
        model.input_price_per_1m = float(payload["input_price_per_1m"])

    if "output_price_per_1m" in provided and payload.get("output_price_per_1m") is not None:
        model.output_price_per_1m = float(payload["output_price_per_1m"])

    # 缓存价允许显式 null（清空配置 → 回退输入原价）；未提交该键时保持原值。
    if "cache_read_price_per_1m" in provided:
        model.cache_read_price_per_1m = _optional_price(payload.get("cache_read_price_per_1m"))

    if "cache_write_price_per_1m" in provided:
        model.cache_write_price_per_1m = _optional_price(
            payload.get("cache_write_price_per_1m")
        )

    if "parameters" in provided and payload.get("parameters") is not None:
        current = model_parameters_from_json(model.parameters_json)
        model.parameters_json = json.dumps(
            normalize_model_parameters(payload.get("parameters"), current),
            ensure_ascii=False,
        )

    # 启停与定时封禁：enabled=true 一律解封；enabled=false 缺省 = 永久封禁；
    # 单独提交 banned_until（ISO 时刻）= 定时封禁，null = 永久封禁，过去的时刻 =
    # 立即解封。读取侧按 banned_until 惰性判活，这里只负责落库这个时刻。
    ban_provided = "banned_until" in provided
    if ban_provided:
        raw_ban = payload.get("banned_until")
        if raw_ban is None:
            model.banned_until = None
        else:
            try:
                model.banned_until = parse_ban_until(str(raw_ban))
            except ValueError as error:
                raise AppError(ErrorCode.INVALID_REQUEST, str(error)) from error
        if model.banned_until is not None and model.banned_until <= now_ms():
            model.banned_until = None
            model.enabled = True
        else:
            model.enabled = False

    if "enabled" in provided and payload.get("enabled") is not None:
        model.enabled = bool(payload["enabled"])
        if model.enabled or not ban_provided:
            # 打开开关清掉封禁；只关开关（未给 banned_until）按永久封禁处理。
            model.banned_until = None

    if "schedule" in provided:
        # A PATCH replaces the whole weekly schedule; ``null`` clears it.
        model.schedule_json = _schedule_json(payload.get("schedule"))

    if "cooldown" in provided and payload.get("cooldown") is not None:
        model.cooldown_json = _merge_cooldown(
            model.cooldown_json, payload.get("cooldown")
        )

    try:
        saved = await repo.save_channel_model(session, model)
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            f"调用模型 {model.call_model} 在本渠道已存在",
            status=409,
        ) from exc
    _bump_affinity_epoch()
    return saved


async def reset_breaker(
    session: AsyncSession, model: orm.ChannelModelORM
) -> orm.ChannelModelORM:
    """Manual reset: close the breaker and clear every counter/timer."""
    apply_to_model(model, on_reset())
    # Breaker state is deliberately excluded from the catalog version diff.
    return await repo.save_channel_model(session, model, bump_catalog=False)


async def set_default_probe_model(
    session: AsyncSession, model: orm.ChannelModelORM
) -> orm.ChannelModelORM:
    """Make one model the channel's default probe target; exactly one survives."""
    channel_id = model.channel_id
    for item in await repo.list_channel_models(session, channel_id=channel_id):
        wanted = item.id == model.id
        if bool(item.probe_default) != wanted:
            item.probe_default = wanted
            await repo.save_channel_model(session, item, bump_catalog=False)
    return model


async def batch_update_channel_models(
    session: AsyncSession,
    model_ids: list[str],
    op: str,
    *,
    banned_until: str | None = None,
    schedule: Any = None,
) -> dict[str, Any]:
    """Apply one ban / unban / cooldown / schedule op to many models.

    Items are independent (ADR-070 §4.1): a missing id is reported as
    ``ok: false`` while every other row still commits, and the whole batch is a
    single transaction with a single catalog bump.
    """
    moment: int | None = None
    if op == "cooldown":
        try:
            moment = parse_ban_until(str(banned_until))
        except ValueError as error:
            raise AppError(ErrorCode.INVALID_REQUEST, str(error)) from error
    schedule_json = _schedule_json(schedule) if op == "schedule" else None
    now = now_ms()
    results: list[dict[str, Any]] = []
    changed: list[orm.ChannelModelORM] = []
    for model_id in model_ids:
        model = await repo.get_channel_model(session, model_id)
        if model is None:
            results.append({"model_id": model_id, "ok": False, "error": "MODEL_NOT_FOUND"})
            continue
        if op == "ban":
            model.enabled = False
            model.banned_until = None
        elif op == "unban":
            model.enabled = True
            model.banned_until = None
        elif op == "cooldown":
            # An instant already in the past means "no cooldown": revive now, just
            # like a single PATCH carrying a past moment.
            if moment is not None and moment > now:
                model.enabled = False
                model.banned_until = moment
            else:
                model.enabled = True
                model.banned_until = None
        else:
            model.schedule_json = schedule_json
        changed.append(model)
        results.append({"model_id": model_id, "ok": True})
    if changed:
        await repo.save_channel_models(session, changed)
        _bump_affinity_epoch()
    succeeded = sum(1 for item in results if item["ok"])
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}


async def delete_channel_model(session: AsyncSession, model: orm.ChannelModelORM) -> None:
    references = await repo.channel_model_ref_count(session, model.id)
    if references:
        raise AppError(
            ErrorCode.CONFLICT,
            f"该模型被 {references} 个组合引用，请先解除组合引用后再删除",
            status=409,
        )
    was_default = bool(model.probe_default)
    channel_id = model.channel_id
    await repo.delete_channel_model(session, model)
    if was_default:
        remaining = await repo.list_channel_models(session, channel_id=channel_id)
        if remaining:
            remaining[0].probe_default = True
            await repo.save_channel_model(session, remaining[0])
    _bump_affinity_epoch()
