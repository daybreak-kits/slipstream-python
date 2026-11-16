from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.errors import AppError, ErrorCode
from l01_core.logging import get_logger
from l01_core.ids import new_id
from l02_infra.crypto import decrypt_secret, encrypt_secret
from l03_domain.naming import CHANNEL_SLUG_RE, slug_from_url, unique_slug
from l03_domain.rewrite import validate_rules
from l03_domain.urls import build_chat_url, build_models_url, is_valid_http_url
from l04_repo import orm, repo

PROTOCOL_OPENAI = "openai_chat"
PROTOCOL_ANTHROPIC = "anthropic_messages"
PROTOCOL_GEMINI = "gemini"
SUPPORTED_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC, PROTOCOL_GEMINI)
CONNECTION_STATUSES = ("ok", "timeout", "error", "unknown")
MAX_CHANNEL_KEYS = 20
DEFAULT_KEY_LABEL = "默认密钥"

logger = get_logger("system.channels")


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _bump_affinity_epoch() -> None:
    """Drop session affinity after a write that can change the layer order.

    Imported lazily: routing imports this module's neighbours, so a module-level
    import here would close a cycle.
    """
    from l05_service.routing import bump_affinity_epoch

    bump_affinity_epoch()


def validate_slug(slug: str) -> str:
    value = _clean(slug)
    if not CHANNEL_SLUG_RE.match(value):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "渠道标识需以字母开头，仅含字母、数字、下划线或中划线，最长 64 位",
        )
    return value


def validate_connection_url(url: str) -> str:
    value = _clean(url)
    if not is_valid_http_url(value):
        raise AppError(ErrorCode.INVALID_REQUEST, "连接地址必须是 http:// 或 https:// 开头的完整地址")
    return value


def validate_protocol(protocol: str) -> str:
    value = _clean(protocol)
    if value not in SUPPORTED_PROTOCOLS:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "协议仅支持 openai_chat、anthropic_messages、gemini",
        )
    return value


def validate_rewrite_rules(value: Any) -> dict[str, Any]:
    rules = value if isinstance(value, dict) else {}
    errors = validate_rules(rules)
    if errors:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "改写规则不合法：" + "；".join(errors),
        )
    return rules


def stored_rewrite_rules(channel: orm.ChannelORM) -> dict[str, Any]:
    try:
        parsed = json.loads(channel.rewrite_rules_json or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def rewrite_override_sections(rules: dict[str, Any]) -> list[str]:
    """Sections that carry at least one parameter-override rule."""
    return [
        section
        for section in (
            "request_headers",
            "request_body",
            "response_headers",
            "response_body",
        )
        if isinstance(rules.get(section), list) and rules.get(section)
    ]


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "••••••" + value[-4:]


def _decrypt_key_row(row: orm.ChannelKeyORM) -> str:
    """Decrypt one key row, memoizing the plaintext on the ORM row."""
    ciphertext = row.key_enc or ""
    cache = getattr(row, "_decrypted_key_cache", None)
    if cache is None:
        cache = {}
        try:
            row._decrypted_key_cache = cache
        except AttributeError:
            pass
    if ciphertext in cache:
        return cache[ciphertext]
    try:
        value = decrypt_secret(ciphertext) if ciphertext else ""
    except ValueError:
        value = ""
    cache[ciphertext] = value
    return value


def _decrypt_secret_safe(value: str) -> str:
    """Decrypt a legacy ciphertext, tolerating a rotated encryption key."""
    try:
        return decrypt_secret(value)
    except ValueError:
        return ""


def _primary_first(
    rows: list[orm.ChannelKeyORM], channel: orm.ChannelORM
) -> list[orm.ChannelKeyORM]:
    """Put the key that backs ``channel.api_key_enc`` first.

    ``channel_keys.created_at`` only has millisecond precision, so keys saved
    in the same transaction tie and ``ORDER BY created_at, id`` degrades to
    random ULIDs.  The legacy column is the only durable record of which key
    is the primary one, so every caller that treats ``rows[0]`` as the
    primary key must go through this helper.
    """
    legacy = channel.api_key_enc
    if not legacy or not rows:
        return rows
    for index, row in enumerate(rows):
        if row.key_enc == legacy:
            return rows if index == 0 else [row, *rows[:index], *rows[index + 1 :]]
    # Rows written before the legacy column was pinned to the primary row hold
    # a different ciphertext of the same secret: Fernet is non-deterministic.
    legacy_value = _decrypt_secret_safe(legacy)
    if not legacy_value:
        return rows
    for index, row in enumerate(rows):
        if _decrypt_key_row(row) == legacy_value:
            return rows if index == 0 else [row, *rows[:index], *rows[index + 1 :]]
    return rows


def channel_key_rows(channel: orm.ChannelORM) -> list[orm.ChannelKeyORM]:
    """Return persisted key rows, tolerating detached/legacy channel objects."""
    rows = getattr(channel, "keys", None)
    if rows is None:
        return []
    try:
        return _primary_first(list(rows), channel)
    except Exception:  # pragma: no cover - defensive for detached instances
        return []


def channel_api_keys(channel: orm.ChannelORM, *, enabled_only: bool = True) -> list[str]:
    rows = channel_key_rows(channel)
    if rows:
        values = [
            _decrypt_key_row(row)
            for row in rows
            if not enabled_only or row.enabled
        ]
        return [value for value in values if value]
    # Legacy fallback for a channel that has not been backfilled yet.
    try:
        legacy = decrypt_secret(channel.api_key_enc)
    except ValueError:
        legacy = ""
    return [legacy] if legacy else []


def channel_api_key(channel: orm.ChannelORM) -> str:
    keys = channel_api_keys(channel)
    return keys[0] if keys else ""


_KEY_SESSION: ContextVar[str] = ContextVar("channel_key_session", default="")


@contextmanager
def key_session_scope(session_key: str | None) -> Iterator[None]:
    """Bind the conversation key that selects a sticky channel credential.

    Providers isolate their prompt cache per account (per key), so changing keys
    per request means changing cache domains per request. chat.py opens this
    scope around the code that builds the upstream headers, using the very same
    session key as routing affinity, so one conversation keeps one account on
    one channel for as long as it stays there.
    """
    token = _KEY_SESSION.set((session_key or "").strip())
    try:
        yield
    finally:
        _KEY_SESSION.reset(token)


def current_key_session_key() -> str:
    """The conversation key currently bound by key_session_scope."""
    return _KEY_SESSION.get()


def _key_index(seed: str, count: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def select_channel_api_key(
    channel: orm.ChannelORM, request_id: str, session_key: str | None = None
) -> str:
    """Pick one key deterministically for a request.

    With a conversation key (the affinity key: x-session-id or
    x-bili-plugin-conversation) the same conversation keeps the same key, which
    keeps the provider's prefix cache warm: that cache is isolated per account,
    so changing keys per request is changing cache domains per request. Without
    a conversation key the gateway request id spreads traffic across the enabled
    keys as before, and probes keep using the first key.
    """
    keys = channel_api_keys(channel)
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]
    scope = session_key if session_key is not None else _KEY_SESSION.get()
    scope = (scope or "").strip()
    if scope:
        return keys[_key_index(f"session:{scope}", len(keys))]
    return keys[_key_index(request_id or "probe", len(keys))]


def serialize_channel_key(row: orm.ChannelKeyORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label or "",
        "masked": mask_secret(_decrypt_key_row(row)),
        "enabled": row.enabled,
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
    }


def serialize_channel(
    channel: orm.ChannelORM,
    model_count: int = 0,
    key_rows: list[orm.ChannelKeyORM] | None = None,
    probe_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = stored_rewrite_rules(channel)
    probe_summary = probe_summary or {}
    rows = key_rows if key_rows is not None else channel_key_rows(channel)
    summaries = [serialize_channel_key(row) for row in rows]
    masks = [item["masked"] for item in summaries if item["masked"]]
    if not summaries:
        legacy = channel_api_key(channel)
        masks = [mask_secret(legacy)] if legacy else []
    return {
        "id": channel.id,
        "slug": channel.slug,
        "name": channel.name,
        "note": channel.note or "",
        "connection_url": channel.connection_url,
        "endpoint_preview": build_chat_url(channel.connection_url),
        "models_endpoint_preview": build_models_url(channel.connection_url),
        "api_key_set": bool(rows) or bool(channel.api_key_enc),
        "api_key_masked": masks[0] if masks else "",
        "api_key_count": len(rows) if rows else (1 if channel.api_key_enc else 0),
        "api_key_masks": masks,
        "api_keys": summaries,
        "protocol": channel.protocol,
        "protocol_source": channel.protocol_source,
        "auth_style": channel.auth_style,
        "priority": int(channel.priority or 1),
        "enabled": channel.enabled,
        "connection_status": channel.connection_status,
        "connection_latency_ms": channel.connection_latency_ms,
        "connection_message": channel.connection_message,
        "connection_tested_at": channel.connection_tested_at,
        "last_model_probe_latency_ms": probe_summary.get("latency_ms"),
        "last_model_probe_at": probe_summary.get("probed_at"),
        "last_model_probe_status": probe_summary.get("status", "unknown"),
        "override_sections": rewrite_override_sections(rules),
        "rewrite_rules": rules,
        "model_count": model_count,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


async def get_channel_or_404(session: AsyncSession, channel_id: str) -> orm.ChannelORM:
    channel = await repo.get_channel(session, channel_id)
    if channel is None:
        raise AppError(ErrorCode.NOT_FOUND, "渠道不存在或已被删除", status=404)
    return channel


async def _read_channel_keys(
    session: AsyncSession, channel: orm.ChannelORM
) -> list[orm.ChannelKeyORM]:
    """Return the channel's key rows without creating anything (read paths)."""
    rows = await repo.list_channel_keys(session, channel.id)
    return _primary_first(rows, channel)


async def ensure_legacy_channel_keys(
    session: AsyncSession, channel: orm.ChannelORM
) -> list[orm.ChannelKeyORM]:
    """Backfill the legacy single-key column into ``channel_keys`` lazily.

    Write paths only: GET list/detail reads must not INSERT or COMMIT, so
    they call :func:`_read_channel_keys` instead.
    """
    rows = await repo.list_channel_keys(session, channel.id)
    if rows:
        return _primary_first(rows, channel)
    if not channel.api_key_enc:
        return []
    row = orm.ChannelKeyORM(
        id=new_id(),
        channel_id=channel.id,
        label=DEFAULT_KEY_LABEL,
        key_enc=channel.api_key_enc,
        enabled=True,
    )
    await repo.save_channel_key(session, row)
    return [row]


async def serialize_channel_with_count(
    session: AsyncSession, channel: orm.ChannelORM
) -> dict[str, Any]:
    counts = await repo.channel_model_counts(session)
    keys = await _read_channel_keys(session, channel)
    summaries = await repo.channel_probe_summaries(session, channel.id)
    return serialize_channel(
        channel,
        counts.get(channel.id, {}).get("total", 0),
        keys,
        summaries.get(channel.id, {}),
    )


async def _ensure_unique_slug(session: AsyncSession, slug: str, exclude_id: str | None = None) -> None:
    existing = await repo.get_channel_by_slug(session, slug)
    if existing is not None and existing.id != exclude_id:
        raise AppError(ErrorCode.CONFLICT, f"渠道标识 {slug} 已被使用", status=409)


async def _resolve_slug(session: AsyncSession, requested: str | None, connection_url: str) -> str:
    if _clean(requested):
        slug = validate_slug(requested or "")
        await _ensure_unique_slug(session, slug)
        return slug
    existing = {channel.slug for channel in await repo.list_channels(session)}
    return unique_slug(slug_from_url(connection_url), existing)


async def record_connection_result(
    session: AsyncSession,
    channel: orm.ChannelORM,
    *,
    status: str,
    latency_ms: int,
    message: str,
    tested_at: int | None,
) -> None:
    """Persist a connection-test verdict; routers stay thin."""
    channel.connection_status = status
    channel.connection_latency_ms = int(latency_ms)
    channel.connection_message = str(message)[:400]
    channel.connection_tested_at = tested_at
    # Connection-test telemetry is not routing configuration: bumping the
    # catalog version here would reload the whole cache after every probe.
    await repo.save_channel(session, channel, bump_catalog=False)


def _clean_key_inputs(payload: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    raw_items = payload.get("api_keys")
    if isinstance(raw_items, list):
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            value = _clean(item.get("api_key"))
            if not value:
                continue
            label = _clean(item.get("label"))[:64] or f"密钥 {index + 1}"
            items.append({"label": label, "api_key": value})
    if not items:
        legacy = _clean(payload.get("api_key"))
        if legacy:
            items.append({"label": DEFAULT_KEY_LABEL, "api_key": legacy})
    if len(items) > MAX_CHANNEL_KEYS:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"单个渠道最多保存 {MAX_CHANNEL_KEYS} 个密钥",
        )
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in items:
        if item["api_key"] in seen:
            masked = f"{item['api_key'][:3]}…{item['api_key'][-4:]}"
            raise AppError(
                ErrorCode.CONFLICT,
                f"批量添加的密钥存在重复：{masked}，请去除重复后再提交",
                status=409,
            )
        seen.add(item["api_key"])
        unique.append(item)
    return unique


def _channel_integrity_error(exc: IntegrityError, slug: str) -> AppError:
    """Map a channels write failure onto the correct user-facing error.

    Only the unique slug index may be reported as a slug conflict. Every other
    integrity failure (a legacy NOT NULL column, for example) used to be
    mislabelled as "渠道标识已被使用" and hid the real cause.
    """
    detail = str(getattr(exc, "orig", exc)).lower()
    if "channels.slug" in detail:
        return AppError(ErrorCode.CONFLICT, f"渠道标识 {slug} 已被使用", status=409)
    logger.exception("保存渠道失败（slug=%s）：%s", slug, detail)
    if "not null constraint failed" in detail:
        column = detail.rsplit(":", 1)[-1].strip()
        return AppError(
            ErrorCode.INTERNAL,
            f"渠道保存失败：数据库字段 {column} 为必填，请先完成数据迁移",
            status=500,
        )
    return AppError(ErrorCode.INTERNAL, "渠道保存失败，请稍后重试", status=500)


async def create_channel(session: AsyncSession, payload: dict[str, Any]) -> orm.ChannelORM:
    name = _clean(payload.get("name"))
    if not name:
        raise AppError(ErrorCode.INVALID_REQUEST, "渠道名称不能为空")
    if len(name) > 128:
        raise AppError(ErrorCode.INVALID_REQUEST, "渠道名称最长 128 位")

    connection_url = validate_connection_url(payload.get("connection_url", ""))
    protocol = validate_protocol(payload.get("protocol") or PROTOCOL_OPENAI)
    slug = await _resolve_slug(session, payload.get("slug"), connection_url)
    rewrite_rules = validate_rewrite_rules(payload.get("rewrite_rules"))
    key_inputs = _clean_key_inputs(payload)
    channel = orm.ChannelORM(
        id=new_id(),
        slug=slug,
        name=name,
        note=_clean(payload.get("note"))[:500],
        connection_url=connection_url,
        api_key_enc="",
        protocol=protocol,
        protocol_source="manual" if _clean(payload.get("protocol")) else "auto",
        priority=int(payload.get("priority") or 1),
        rewrite_rules_json=json.dumps(rewrite_rules, ensure_ascii=False),
        enabled=bool(payload.get("enabled", True)),
    )
    channel.keys = [
        orm.ChannelKeyORM(
            id=new_id(),
            channel_id=channel.id,
            label=item["label"],
            key_enc=encrypt_secret(item["api_key"]),
            enabled=True,
        )
        for item in key_inputs
    ]
    # Pin the legacy column to the primary row's own ciphertext: Fernet is
    # non-deterministic, so encrypting the same secret twice yields two blobs
    # and the primary row would no longer be identifiable.
    channel.api_key_enc = channel.keys[0].key_enc if channel.keys else encrypt_secret("")
    try:
        saved = await repo.save_channel(session, channel)
    except IntegrityError as exc:
        await session.rollback()
        raise _channel_integrity_error(exc, slug) from exc
    _bump_affinity_epoch()
    return saved


async def update_channel(
    session: AsyncSession, channel: orm.ChannelORM, payload: dict[str, Any], provided: set[str]
) -> orm.ChannelORM:
    if "name" in provided:
        name = _clean(payload.get("name"))
        if not name:
            raise AppError(ErrorCode.INVALID_REQUEST, "渠道名称不能为空")
        if len(name) > 128:
            raise AppError(ErrorCode.INVALID_REQUEST, "渠道名称最长 128 位")
        channel.name = name

    if "note" in provided:
        channel.note = _clean(payload.get("note"))[:500]

    if "slug" in provided and payload.get("slug") is not None:
        slug = validate_slug(payload.get("slug") or "")
        await _ensure_unique_slug(session, slug, exclude_id=channel.id)
        channel.slug = slug

    if "connection_url" in provided and payload.get("connection_url") is not None:
        channel.connection_url = validate_connection_url(payload.get("connection_url") or "")

    if "protocol" in provided and payload.get("protocol") is not None:
        channel.protocol = validate_protocol(payload.get("protocol") or "")
        channel.protocol_source = "manual"

    if "api_key" in provided and payload.get("api_key") is not None:
        raw = _clean(payload.get("api_key"))
        rows = await ensure_legacy_channel_keys(session, channel)
        if not raw:
            for row in rows:
                await repo.delete_channel_key(session, row)
            channel.api_key_enc = encrypt_secret("")
        else:
            # The primary key must not silently overwrite another key.
            if any(_decrypt_key_row(row) == raw for row in rows[1:]):
                raise AppError(
                    ErrorCode.CONFLICT, "该密钥已存在于当前渠道", status=409
                )
            channel.api_key_enc = encrypt_secret(raw)
            if rows:
                rows[0].key_enc = channel.api_key_enc
                rows[0].enabled = True
                await repo.save_channel_key(session, rows[0])
            else:
                await repo.save_channel_key(
                    session,
                    orm.ChannelKeyORM(
                        id=new_id(),
                        channel_id=channel.id,
                        label=DEFAULT_KEY_LABEL,
                        key_enc=channel.api_key_enc,
                        enabled=True,
                    ),
                )

    if "priority" in provided and payload.get("priority") is not None:
        channel.priority = max(1, min(int(payload["priority"]), 1000))

    if "enabled" in provided and payload.get("enabled") is not None:
        channel.enabled = bool(payload["enabled"])

    if "rewrite_rules" in provided:
        rules = validate_rewrite_rules(payload.get("rewrite_rules"))
        channel.rewrite_rules_json = json.dumps(rules, ensure_ascii=False)

    try:
        saved = await repo.save_channel(session, channel)
    except IntegrityError as exc:
        await session.rollback()
        raise _channel_integrity_error(exc, channel.slug) from exc
    _bump_affinity_epoch()
    return saved


async def list_channel_keys(
    session: AsyncSession, channel: orm.ChannelORM
) -> list[dict[str, Any]]:
    rows = await _read_channel_keys(session, channel)
    return [serialize_channel_key(row) for row in rows]


async def add_channel_key(
    session: AsyncSession,
    channel: orm.ChannelORM,
    *,
    label: str,
    api_key: str,
) -> orm.ChannelKeyORM:
    raw = _clean(api_key)
    if not raw:
        raise AppError(ErrorCode.INVALID_REQUEST, "密钥不能为空")
    rows = await ensure_legacy_channel_keys(session, channel)
    if len(rows) >= MAX_CHANNEL_KEYS:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"单个渠道最多保存 {MAX_CHANNEL_KEYS} 个密钥",
        )
    if any(_decrypt_key_row(row) == raw for row in rows):
        raise AppError(ErrorCode.CONFLICT, "该密钥已存在于当前渠道", status=409)
    key = orm.ChannelKeyORM(
        id=new_id(),
        channel_id=channel.id,
        label=_clean(label)[:64] or f"密钥 {len(rows) + 1}",
        key_enc=encrypt_secret(raw),
        enabled=True,
    )
    await repo.save_channel_key(session, key)
    if not channel.api_key_enc:
        channel.api_key_enc = key.key_enc
        await repo.save_channel(session, channel)
    return key


async def update_channel_key(
    session: AsyncSession,
    channel: orm.ChannelORM,
    key_id: str,
    *,
    label: str | None = None,
    enabled: bool | None = None,
) -> orm.ChannelKeyORM:
    """Update the remark label or the enabled flag of one channel key."""
    row = await repo.get_channel_key(session, key_id)
    if row is None or row.channel_id != channel.id:
        raise AppError(ErrorCode.NOT_FOUND, "密钥不存在或已被删除", status=404)
    if label is not None:
        row.label = _clean(label)[:64] or DEFAULT_KEY_LABEL
    if enabled is not None:
        row.enabled = bool(enabled)
    await repo.save_channel_key(session, row)
    if not channel.api_key_enc and row.enabled:
        channel.api_key_enc = row.key_enc
        await repo.save_channel(session, channel)
    return row


async def delete_channel_key(
    session: AsyncSession, channel: orm.ChannelORM, key_id: str
) -> None:
    row = await repo.get_channel_key(session, key_id)
    if row is None or row.channel_id != channel.id:
        raise AppError(ErrorCode.NOT_FOUND, "密钥不存在或已被删除", status=404)
    await repo.delete_channel_key(session, row)
    rows = _primary_first(await repo.list_channel_keys(session, channel.id), channel)
    # Never promote a disabled key to the channel's primary slot.
    enabled_rows = [item for item in rows if item.enabled]
    replacement = enabled_rows[0] if enabled_rows else (rows[0] if rows else None)
    channel.api_key_enc = replacement.key_enc if replacement else encrypt_secret("")
    await repo.save_channel(session, channel)


async def delete_channel(session: AsyncSession, channel: orm.ChannelORM) -> None:
    references = await repo.channel_combo_reference_count(session, channel.id)
    if references:
        raise AppError(
            ErrorCode.CONFLICT,
            f"该渠道下有 {references} 个渠道模型被组合引用，请先解除组合引用后再删除",
            status=409,
        )
    await repo.delete_channel(session, channel)
    _bump_affinity_epoch()


async def list_channels(
    session: AsyncSession, search: str = "", status: str = ""
) -> list[dict[str, Any]]:
    status_value = _clean(status)
    if status_value and status_value not in CONNECTION_STATUSES:
        raise AppError(ErrorCode.INVALID_REQUEST, "状态筛选仅支持 ok、timeout、error、unknown")

    search_value = _clean(search)
    channels = await repo.list_channels(session, search=search_value, status=status_value)
    counts = await repo.channel_model_counts(session)
    summaries = await repo.channel_probe_summaries(session)
    return [
        serialize_channel(
            channel,
            counts.get(channel.id, {}).get("total", 0),
            probe_summary=summaries.get(channel.id, {}),
        )
        for channel in channels
    ]


def draft_probe_channel(
    existing: orm.ChannelORM | None,
    *,
    connection_url: str | None,
    api_key: str | None,
    protocol: str | None,
    rewrite_rules: dict[str, Any] | None,
) -> orm.ChannelORM:
    """Build an unsaved channel snapshot for discovery/test requests.

    ``api_key=None`` keeps the stored key, while an explicit empty string
    means "no key". This mirrors what the edit form shows without exposing
    the secret to the browser.
    """
    url = validate_connection_url(
        _clean(connection_url) or (existing.connection_url if existing else "")
    )
    raw_key = channel_api_key(existing) if existing and api_key is None else _clean(api_key)
    if protocol:
        protocol_value = validate_protocol(protocol)
        protocol_source = "manual"
    else:
        protocol_value = existing.protocol if existing else PROTOCOL_OPENAI
        protocol_source = existing.protocol_source if existing else "auto"
    rules = validate_rewrite_rules(
        rewrite_rules
        if rewrite_rules is not None
        else (stored_rewrite_rules(existing) if existing else {})
    )
    return orm.ChannelORM(
        id=existing.id if existing else new_id(),
        slug=existing.slug if existing else "draft-channel",
        name=existing.name if existing else "draft-channel",
        note=existing.note if existing else "",
        connection_url=url,
        api_key_enc=encrypt_secret(raw_key),
        protocol=protocol_value,
        protocol_source=protocol_source,
        auth_style=existing.auth_style if existing else "bearer",
        rewrite_rules_json=json.dumps(rules, ensure_ascii=False),
        enabled=True,
    )
