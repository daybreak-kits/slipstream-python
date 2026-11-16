"""models.dev 派生默认值的惰性刷新、状态与预填（ADR-073 决策 2/6/7）。

本模块是唯一的 IO 入口：快照在磁盘上，请求路径只读本地派生数据，绝不发网络
请求（决策 1）。刷新由「模型目录列表读取」惰性触发（决策 2 / D3），手动刷新
走同一个函数；/v1/* 永不触发，失败当天不再重试（last_attempt 已写、last_pull
不动）。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.config import Settings
from l01_core.timeutil import now_ms
from l03_domain.model_defaults_match import (
    ModelEntry,
    SnapshotIndex,
    build_index,
    entries_from_payload,
    match_model,
)
from l03_domain.model_defaults_merge import (
    CAPABILITY_FLAGS,
    adopt_value,
    field_sources,
    merge_entries,
    prefill_defaults,
    prefill_from_fields,
    same_value,
)
from l03_domain.model_defaults_rules import (
    DEFAULT_FUZZY_AUTO,
    DEFAULT_FUZZY_REVIEW,
    LABS_MIN_PROVIDERS,
    MATCH_FUZZY_AUTO,
    MATCH_FUZZY_REVIEW,
    MATCH_UNMATCHED,
    MATCH_VERSION_LOOSE,
    RULE_VERSION,
    parse_aliases,
    logical_model_key,
    tier_rank,
)
from l04_repo import orm, repo
from l05_service import channel_models
from l05_service.model_defaults.fetch import FetchResult, fetch_snapshot
from l05_service.model_defaults.snapshot_store import (
    SnapshotError,
    SnapshotMeta,
    SnapshotStore,
)

logger = logging.getLogger(__name__)

MODEL_DEFAULTS_SETTING_KEY = "model_defaults"

# 需要人工看一眼的命中级别（ADR-073 决策 3：版本降级与模糊命中要复核）。
REVIEW_MATCH_LEVELS = (MATCH_VERSION_LOOSE, MATCH_FUZZY_AUTO, MATCH_FUZZY_REVIEW)

# 只有这些级别能直接预填；模糊待复核与未命中只给候选（决策 3 第 6/7 级）。
PREFILL_MATCH_LEVELS = (
    "exact",
    "normalized",
    "prefix_stripped",
    MATCH_VERSION_LOOSE,
    "alias",
    MATCH_FUZZY_AUTO,
)

# 值比对同步的字段全集（决策 5 落点；model_type 永不参与，决策 7）。
_SYNC_PRICE_KEYS = (
    "input_price_per_1m",
    "output_price_per_1m",
    "cache_read_price_per_1m",
    "cache_write_price_per_1m",
)
_SYNC_PARAM_KEYS = (
    "max_input_tokens",
    "max_output_tokens",
    "reasoning_level",
    "input_types",
    "output_types",
) + CAPABILITY_FLAGS

# save_state 哨兵：None 对档位名单是合法值（=继承代码常量），不能当「不动」用。
_KEEP = object()

Fetcher = Callable[[SnapshotMeta], Any]


@dataclass(frozen=True, slots=True)
class ModelDefaultsState:
    """app_settings 单 key 的内容（决策 2 / D4）。"""

    last_attempt: str = ""
    last_pull: str = ""
    fuzzy_auto: float = DEFAULT_FUZZY_AUTO
    fuzzy_review: float = DEFAULT_FUZZY_REVIEW
    tier_official: tuple[str, ...] | None = None
    tier_clouds: tuple[str, ...] | None = None
    labs_min_providers: int = LABS_MIN_PROVIDERS
    updated_at: int | None = None
    updated_by: str = ""


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return max(low, min(high, parsed))


def _tier_ids(value: Any) -> tuple[str, ...] | None:
    """档位名单覆盖：null/缺省=继承代码常量；显式列表=覆盖（可清空）。"""
    if not isinstance(value, (list, tuple)):
        return None
    ids = tuple(dict.fromkeys(str(item).strip().lower() for item in value if str(item).strip()))
    return ids


def state_from_json(payload: Mapping[str, Any] | None) -> ModelDefaultsState:
    data = payload if isinstance(payload, Mapping) else {}
    auto = _clamp(data.get("fuzzy_auto"), 0.5, 1.0, DEFAULT_FUZZY_AUTO)
    review = _clamp(data.get("fuzzy_review"), 0.1, auto, DEFAULT_FUZZY_REVIEW)
    lists = data.get("tier_lists")
    lists = lists if isinstance(lists, Mapping) else {}
    labs_min = _clamp(data.get("labs_min_providers"), 2, 64, LABS_MIN_PROVIDERS)
    updated_at = data.get("updated_at")
    return ModelDefaultsState(
        last_attempt=str(data.get("last_attempt") or ""),
        last_pull=str(data.get("last_pull") or ""),
        fuzzy_auto=auto,
        fuzzy_review=review,
        tier_official=_tier_ids(lists.get("official")),
        tier_clouds=_tier_ids(lists.get("clouds")),
        labs_min_providers=int(labs_min),
        updated_at=int(updated_at) if isinstance(updated_at, int) else None,
        updated_by=str(data.get("updated_by") or ""),
    )


def state_to_json(state: ModelDefaultsState) -> str:
    return json.dumps(
        {
            "last_attempt": state.last_attempt,
            "last_pull": state.last_pull,
            "fuzzy_auto": state.fuzzy_auto,
            "fuzzy_review": state.fuzzy_review,
            "tier_lists": {
                "official": list(state.tier_official) if state.tier_official is not None else None,
                "clouds": list(state.tier_clouds) if state.tier_clouds is not None else None,
            },
            "labs_min_providers": state.labs_min_providers,
            "updated_at": state.updated_at,
            "updated_by": state.updated_by,
        },
        ensure_ascii=False,
    )


async def get_state(session: AsyncSession) -> ModelDefaultsState:
    row = await repo.get_app_setting(session, MODEL_DEFAULTS_SETTING_KEY)
    if row is None:
        return ModelDefaultsState()
    try:
        payload = json.loads(row.value_json or "{}")
    except ValueError:
        payload = {}
    state = state_from_json(payload if isinstance(payload, dict) else None)
    return replace(state, updated_at=row.updated_at, updated_by=row.updated_by or "")


async def save_state(
    session: AsyncSession,
    *,
    last_attempt: str | None = None,
    last_pull: str | None = None,
    fuzzy_auto: float | None = None,
    fuzzy_review: float | None = None,
    tier_official: Any = _KEEP,
    tier_clouds: Any = _KEEP,
    labs_min_providers: int | None = None,
    updated_by: str = "",
) -> ModelDefaultsState:
    """写回 app_settings 单 key；None 表示「这一项不动」（名单用 _KEEP 哨兵）。"""
    current = await get_state(session)
    # 复核阈值不能高于自动阈值；auto 先落定，review 再按新 auto 收敛。
    next_auto = (
        current.fuzzy_auto
        if fuzzy_auto is None
        else _clamp(fuzzy_auto, 0.5, 1.0, current.fuzzy_auto)
    )
    next_review = (
        current.fuzzy_review
        if fuzzy_review is None
        else _clamp(fuzzy_review, 0.1, next_auto, current.fuzzy_review)
    )
    next_labs = (
        current.labs_min_providers
        if labs_min_providers is None
        else int(_clamp(labs_min_providers, 2, 64, current.labs_min_providers))
    )
    next_state = replace(
        current,
        last_attempt=current.last_attempt if last_attempt is None else last_attempt,
        last_pull=current.last_pull if last_pull is None else last_pull,
        fuzzy_auto=next_auto,
        fuzzy_review=next_review,
        tier_official=current.tier_official if tier_official is _KEEP else _tier_ids(tier_official),
        tier_clouds=current.tier_clouds if tier_clouds is _KEEP else _tier_ids(tier_clouds),
        labs_min_providers=next_labs,
        updated_at=now_ms(),
        updated_by=updated_by or current.updated_by,
    )
    row = await repo.get_app_setting(session, MODEL_DEFAULTS_SETTING_KEY)
    payload = state_to_json(next_state)
    if row is None:
        row = orm.AppSettingORM(
            key=MODEL_DEFAULTS_SETTING_KEY,
            value_json=payload,
            updated_at=next_state.updated_at,
            updated_by=next_state.updated_by,
        )
        await repo.save_app_setting(session, row)
    else:
        row.value_json = payload
        row.updated_at = next_state.updated_at
        row.updated_by = next_state.updated_by
        await repo.save_app_setting(session, row)
    return next_state


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """一次刷新尝试的结果（也用于结构化日志与接口返回）。"""

    status: str = "ok"
    trigger: str = "manual"
    attempted: bool = False
    refreshed: bool = False
    http_status: int = 0
    elapsed_ms: int = 0
    not_modified: bool = False
    conditional: bool = False
    providers: int = 0
    entries: int = 0
    bytes: int = 0
    error_summary: str = ""
    pulled_at: str = ""
    last_pull: str = ""
    last_attempt: str = ""
    defaults_count: int = 0
    synced: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelDefaultsService:
    """惰性刷新 + 派生默认值表 + 预填（ADR-073 决策 2/6/7）。"""

    def __init__(
        self,
        session_factory: Any,
        settings: Settings,
        *,
        store: SnapshotStore | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._store = store or SnapshotStore(settings.models_dev_dir)
        self._fetcher = fetcher
        self._lock = threading.Lock()
        self._index_cache: tuple[tuple[Any, ...] | None, SnapshotIndex] | None = None
        self._aliases: dict[str, str] | None = None
        # 刷新成功后由 app_factory 挂上：让目录缓存失效（决策 2 / D3）。
        self.on_refreshed: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # 时间与阈值
    # ------------------------------------------------------------------
    def _tz(self) -> Any:
        try:
            return ZoneInfo(self._settings.default_timezone)
        except Exception:  # noqa: BLE001 - 未知时区退回 UTC，不阻塞同步
            return timezone.utc

    def _today(self) -> str:
        """本地时区的日历日（决策 2 的「本地 00:00 之后」）。"""
        return datetime.now(self._tz()).date().isoformat()

    def _local_date(self, timestamp_ms: int) -> date:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=self._tz()).date()

    @staticmethod
    def _iso(timestamp_ms: int) -> str:
        if timestamp_ms <= 0:
            return ""
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    async def ensure_fresh(self, session: AsyncSession) -> RefreshOutcome | None:
        """目录列表读取时的惰性触发（决策 2 / D3）。

        任何异常都吞掉：目录读取不能因为外网或快照问题而失败。
        """
        try:
            if not self._settings.models_dev_enabled:
                return None
            state = await get_state(session)
            if state.last_attempt == self._today():
                return None
            return await self.refresh(session, trigger="auto")
        except Exception as exc:  # noqa: BLE001 - 同步失败不影响目录读取
            logger.warning("model_defaults.ensure_fresh failed: %s", exc)
            return None

    async def refresh(
        self, session: AsyncSession, *, trigger: str = "manual", force: bool = False
    ) -> RefreshOutcome:
        """拉一次快照并重建派生默认值表。

        force=True 是「立即刷新」按钮：绕过当天判断；自动触发走当天判断。
        锁被占用时直接返回 busy，调用方读旧快照（不排队）。
        """
        if not self._settings.models_dev_enabled:
            return RefreshOutcome(status="disabled", trigger=trigger)
        state = await get_state(session)
        today = self._today()
        if not force and state.last_attempt == today:
            return RefreshOutcome(
                status="skipped",
                trigger=trigger,
                last_pull=state.last_pull,
                last_attempt=state.last_attempt,
            )
        if not self._lock.acquire(blocking=False):
            return RefreshOutcome(
                status="busy",
                trigger=trigger,
                last_pull=state.last_pull,
                last_attempt=state.last_attempt,
            )
        started = now_ms()
        try:
            meta = self._store.load_meta()
            result = await self._fetch(meta)
            outcome = await self._apply(
                session,
                result,
                meta=meta,
                today=today,
                trigger=trigger,
                elapsed_ms=max(0, now_ms() - started),
            )
        finally:
            self._lock.release()
        return outcome

    async def _fetch(self, meta: SnapshotMeta) -> FetchResult:
        if self._fetcher is not None:
            result = self._fetcher(meta)
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
            return result
        async with httpx.AsyncClient() as client:
            return await fetch_snapshot(
                client,
                self._settings.models_dev_url,
                etag=meta.etag,
                last_modified=meta.last_modified,
                timeout_s=self._settings.models_dev_timeout_s,
            )

    async def _apply(
        self,
        session: AsyncSession,
        result: FetchResult,
        *,
        meta: SnapshotMeta,
        today: str,
        trigger: str,
        elapsed_ms: int,
    ) -> RefreshOutcome:
        url = self._settings.models_dev_url
        stamp = now_ms()
        if result.ok and result.body:
            try:
                saved = self._store.save(
                    result.body,
                    meta=replace(
                        meta,
                        source_url=url,
                        fetched_at=result.fetched_at or stamp,
                        attempted_at=stamp,
                        last_attempt_date=today,
                        http_date=result.http_date or meta.http_date,
                        etag=result.etag or meta.etag,
                        last_modified=result.last_modified or meta.last_modified,
                        providers=0,
                        entries=0,
                        error_summary="",
                        error_at=0,
                    ),
                )
            except SnapshotError as exc:
                self._store.save_failure(
                    str(exc), attempted_at=stamp, last_attempt_date=today, source_url=url
                )
                state = await save_state(session, last_attempt=today, updated_by=trigger)
                outcome = RefreshOutcome(
                    status="error",
                    trigger=trigger,
                    attempted=True,
                    elapsed_ms=elapsed_ms,
                    error_summary=str(exc),
                    last_attempt=state.last_attempt,
                    last_pull=state.last_pull,
                )
                self._log(outcome)
                return outcome
            self._index_cache = None
            count, per_upstream, old_rows = await self._materialize(session, saved)
            synced = await self._sync_channel_models(session, per_upstream, old_rows)
            state = await save_state(
                session, last_attempt=today, last_pull=today, updated_by=trigger
            )
            self._notify()
            outcome = RefreshOutcome(
                status="ok",
                trigger=trigger,
                attempted=True,
                refreshed=True,
                http_status=result.status_code,
                elapsed_ms=elapsed_ms,
                not_modified=False,
                conditional=bool(result.etag or result.last_modified),
                providers=saved.providers,
                entries=saved.entries,
                bytes=saved.bytes,
                pulled_at=self._iso(saved.fetched_at),
                last_pull=state.last_pull,
                last_attempt=state.last_attempt,
                defaults_count=count,
                synced=synced,
            )
            self._log(outcome)
            return outcome
        if result.not_modified:
            saved = self._store.save_meta(
                replace(
                    meta,
                    source_url=url or meta.source_url,
                    fetched_at=stamp,
                    attempted_at=stamp,
                    last_attempt_date=today,
                    http_date=result.http_date or meta.http_date,
                    etag=result.etag or meta.etag,
                    last_modified=result.last_modified or meta.last_modified,
                )
            )
            count, per_upstream, old_rows = await self._materialize(session, saved)
            synced = await self._sync_channel_models(session, per_upstream, old_rows)
            state = await save_state(
                session, last_attempt=today, last_pull=today, updated_by=trigger
            )
            self._notify()
            outcome = RefreshOutcome(
                status="not_modified",
                trigger=trigger,
                attempted=True,
                refreshed=False,
                http_status=result.status_code,
                elapsed_ms=elapsed_ms,
                not_modified=True,
                conditional=bool(result.etag or result.last_modified),
                providers=saved.providers,
                entries=saved.entries,
                bytes=saved.bytes,
                pulled_at=self._iso(saved.fetched_at),
                last_pull=state.last_pull,
                last_attempt=state.last_attempt,
                defaults_count=count,
                synced=synced,
            )
            self._log(outcome)
            return outcome
        # 失败：保留旧快照，只写 meta；last_attempt 落今天，当天不再重试。
        saved = self._store.save_failure(
            result.error_summary or "pull failed",
            attempted_at=stamp,
            last_attempt_date=today,
            source_url=url,
        )
        state = await save_state(session, last_attempt=today, updated_by=trigger)
        outcome = RefreshOutcome(
            status="error",
            trigger=trigger,
            attempted=True,
            elapsed_ms=elapsed_ms,
            http_status=result.status_code,
            error_summary=result.error_summary,
            providers=saved.providers,
            entries=saved.entries,
            bytes=saved.bytes,
            last_attempt=state.last_attempt,
            last_pull=state.last_pull,
        )
        self._log(outcome)
        return outcome

    def _log(self, outcome: RefreshOutcome) -> None:
        logger.info(
            "models.dev refresh status=%s trigger=%s elapsed_ms=%s providers=%s "
            "models=%s not_modified=%s conditional=%s synced=%s error=%s",
            outcome.status,
            outcome.trigger,
            outcome.elapsed_ms,
            outcome.providers,
            outcome.entries,
            outcome.not_modified,
            outcome.conditional,
            outcome.synced,
            outcome.error_summary or "-",
        )

    def _notify(self) -> None:
        if self.on_refreshed is None:
            return
        try:
            self.on_refreshed()
        except Exception as exc:  # noqa: BLE001 - 缓存失效失败不影响同步结果
            logger.warning("model_defaults.on_refreshed failed: %s", exc)

    async def _materialize(
        self, session: AsyncSession, meta: SnapshotMeta
    ) -> tuple[int, dict[str, dict[str, Any]], dict[str, str | None]]:
        """按当前渠道模型的 upstream_model 生成派生默认值行（每逻辑模型一行）。

        返回 (行数, upstream_model -> {model_id, fields}, 旧 fields_json 基线表)。
        基线表是 replace 之前的 fields_json（决策 6：上次自动写入值），供值比对
        同步判断「哪些字段被人工改过」。
        """
        payload = self._store.load_api()
        if payload is None:
            return 0, {}, {}
        state = await get_state(session)
        index = self._index_for(payload, state)
        clusters: dict[str, dict[str, Any]] = {}
        per_upstream: dict[str, dict[str, Any]] = {}
        for upstream in await repo.list_channel_model_upstream_models(session):
            result = match_model(
                upstream,
                index,
                fuzzy_auto=state.fuzzy_auto,
                fuzzy_review=state.fuzzy_review,
            )
            if not result.matched or result.matched_by not in PREFILL_MATCH_LEVELS:
                continue
            merged = merge_entries(result.entries, matched_by=result.matched_by)
            key = str(
                merged.fields.get("canonical_model_id")
                or logical_model_key(result.entries)
            )[:160]
            bucket = clusters.get(key)
            if bucket is None or tier_rank(merged.tier) < tier_rank(bucket["merged"].tier):
                clusters[key] = {"merged": merged, "matched_by": result.matched_by}
            per_upstream[upstream] = {"model_id": key, "fields": merged.fields}
        old_rows = {
            row.model_id: row.fields_json
            for row in await repo.list_model_defaults(session)
        }
        rows = [
            orm.ModelDefaultORM(
                model_id=key,
                matched_by=bucket["matched_by"],
                providers_json=json.dumps(bucket["merged"].providers, ensure_ascii=False),
                rule_version=bucket["merged"].rule_version,
                fields_json=json.dumps(bucket["merged"].fields, ensure_ascii=False),
                pulled_at=meta.fetched_at or now_ms(),
            )
            for key, bucket in sorted(clusters.items())
        ]
        count = await repo.replace_model_defaults(session, rows)
        return count, per_upstream, old_rows

    async def _sync_channel_models(
        self,
        session: AsyncSession,
        per_upstream: dict[str, dict[str, Any]],
        old_rows: dict[str, str | None],
    ) -> int:
        """值比对同步（决策 6）：当前值==基线才采用新拉取值，人工改动保留。

        只处理 enabled 行；model_type 永不参与（决策 7：预填仅建议，
        不把来源固化成 manual）。返回发生写入的行数。
        """
        if not per_upstream:
            return 0
        updated = 0
        for model in await repo.list_channel_models_enabled(session):
            entry = per_upstream.get(str(model.upstream_model or "").strip())
            if entry is None:
                continue
            patch = self._diff_fields(
                model, entry["fields"], old_rows.get(entry["model_id"]), force=False
            )
            if patch is None:
                continue
            await channel_models.update_channel_model(session, model, patch, set(patch))
            updated += 1
        return updated

    def _diff_fields(
        self,
        model: Any,
        fields: Mapping[str, Any],
        baseline_fields: str | None,
        *,
        force: bool,
    ) -> dict[str, Any] | None:
        """逐字段值比对，返回 update_channel_model 可用的 payload。

        force=True（恢复自动值，决策 6 边界三）直接采用候选值；force=False 只在
        当前值等于基线时采用。派生键首次出现（无基线）视为全人工值，不动。
        """
        base = prefill_from_fields(json.loads(baseline_fields)) if baseline_fields else None
        new = prefill_from_fields(fields)
        params = channel_models.model_parameters_from_json(model.parameters_json)
        payload: dict[str, Any] = {}
        for key in _SYNC_PRICE_KEYS:
            current = getattr(model, key, None)
            candidate = new.get(key)
            if force:
                if same_value(candidate, current):
                    continue
                if candidate is None and key in ("input_price_per_1m", "output_price_per_1m"):
                    continue  # update_channel_model 不写 in/out None：上游价消失时保留现值
                payload[key] = candidate
                continue
            if base is None:
                continue
            final = adopt_value(current, base.get(key), candidate)
            if not same_value(final, current):
                payload[key] = final
        param_patch: dict[str, Any] = {}
        for key in _SYNC_PARAM_KEYS:
            current = params.get(key)
            candidate = new["parameters"].get(key)
            if force:
                if not same_value(candidate, current):
                    param_patch[key] = candidate
                continue
            if base is None:
                continue
            final = adopt_value(current, base["parameters"].get(key), candidate)
            if not same_value(final, current):
                param_patch[key] = final
        if param_patch:
            payload["parameters"] = param_patch
        return payload or None

    async def adopt_defaults(
        self, session: AsyncSession, model: Any, *, force: bool = False
    ) -> dict[str, Any]:
        """「恢复自动值」（决策 6 边界三）：把可同步字段重置为本次拉取值。

        基线取派生表该逻辑模型行的 fields_json（上次自动写入值）；无基线时仅
        force 生效。派生表下一次 materialize 以新快照重建，基线随之刷新。
        """
        upstream = str(model.upstream_model or "").strip()
        state = await get_state(session)
        result: dict[str, Any] = {
            "model_id": model.id,
            "upstream_model": upstream,
            "matched": False,
            "matched_by": MATCH_UNMATCHED,
            "adopted": [],
            "force": force,
        }
        payload = self._store.load_api()
        if payload is None or not upstream:
            return result
        index = self._index_for(payload, state)
        match = match_model(
            upstream,
            index,
            fuzzy_auto=state.fuzzy_auto,
            fuzzy_review=state.fuzzy_review,
        )
        result["matched_by"] = match.matched_by
        if not match.matched or match.matched_by not in PREFILL_MATCH_LEVELS:
            return result
        result["matched"] = True
        merged = merge_entries(match.entries, matched_by=match.matched_by)
        key = str(merged.fields.get("canonical_model_id") or logical_model_key(match.entries))[:160]
        row = await repo.get_model_default(session, key)
        patch = self._diff_fields(
            model, merged.fields, row.fields_json if row else None, force=force
        )
        if patch is None:
            return result
        await channel_models.update_channel_model(session, model, patch, set(patch))
        result["adopted"] = sorted(patch)
        return result

    def _load_aliases(self) -> dict[str, str]:
        """读取人工别名表：路径来自 Settings；缺失或坏文件返回空表。"""
        target = str(getattr(self._settings, "models_dev_alias_file", "") or "").strip()
        if not target:
            return {}
        try:
            raw = Path(target).read_text(encoding="utf-8")
        except OSError:
            return {}
        return parse_aliases(raw)

    def _index_for(self, payload: Mapping[str, Any], state: ModelDefaultsState) -> SnapshotIndex:
        """构建/复用快照索引；档位覆盖（决策 4 名单与阈值）参与缓存键。"""
        tier_key = (state.tier_official, state.tier_clouds, state.labs_min_providers)
        key: tuple[Any, ...] | None = None
        try:
            stat = self._store.api_path.stat()
            key = (stat.st_mtime_ns, stat.st_size, tier_key)
        except OSError:
            key = None
        if self._index_cache is not None and self._index_cache[0] == key:
            return self._index_cache[1]
        if self._aliases is None:
            self._aliases = self._load_aliases()
        index = build_index(
            entries_from_payload(
                payload,
                official=state.tier_official,
                clouds=state.tier_clouds,
                labs_min=state.labs_min_providers,
            ),
            aliases=self._aliases,
        )
        self._index_cache = (key, index)
        return index

    # ------------------------------------------------------------------
    # 状态与预填
    # ------------------------------------------------------------------
    def _snapshot_info(self, meta: SnapshotMeta, today: str) -> dict[str, Any]:
        age_days: int | None = None
        stale = False
        if meta.fetched_at:
            try:
                today_date = date.fromisoformat(today)
            except ValueError:
                today_date = datetime.now(self._tz()).date()
            age_days = max(0, (today_date - self._local_date(meta.fetched_at)).days)
            stale = age_days >= 1
        return {
            "available": self._store.present(),
            "pulled_at": self._iso(meta.fetched_at) or None,
            "age_days": age_days,
            "stale": stale,
            "providers": meta.providers,
            "entries": meta.entries,
            "bytes": meta.bytes,
            "source_url": meta.source_url,
            "etag": meta.etag,
            "last_modified": meta.last_modified,
            "http_date": meta.http_date,
            "conditional": bool(meta.etag or meta.last_modified),
            "error_summary": meta.error_summary,
            "error_at": meta.error_at or None,
        }

    async def status(self, session: AsyncSession) -> dict[str, Any]:
        """同步状态：快照与派生表两维陈旧度（决策 9）+ 档位覆盖回显。"""
        state = await get_state(session)
        meta = self._store.load_meta()
        today = self._today()
        rows = await repo.list_model_defaults(session)
        registry_updated = ""
        for row in rows:
            try:
                providers = json.loads(row.providers_json or "[]")
            except ValueError:
                continue
            if not isinstance(providers, list):
                continue
            for item in providers:
                text = str(item.get("last_updated") or "") if isinstance(item, dict) else ""
                if text > registry_updated:
                    registry_updated = text
        return {
            "enabled": bool(self._settings.models_dev_enabled),
            "rule_version": str(RULE_VERSION),
            "today": today,
            "last_pull": state.last_pull,
            "last_attempt": state.last_attempt,
            "fuzzy_auto": state.fuzzy_auto,
            "fuzzy_review": state.fuzzy_review,
            "tier_official": list(state.tier_official) if state.tier_official is not None else None,
            "tier_clouds": list(state.tier_clouds) if state.tier_clouds is not None else None,
            "labs_min_providers": state.labs_min_providers,
            "defaults_count": len(rows),
            "snapshot": self._snapshot_info(meta, today),
            "registry": {
                "models": len(rows),
                "last_updated": registry_updated or None,
            },
        }

    async def prefill(self, session: AsyncSession, upstream_model: str) -> dict[str, Any]:
        """新建渠道模型的默认值预填（决策 7）。

        只读已拉快照，绝不触发刷新；没有快照或未命中都返回可渲染的空结果，
        永远不抛异常，所以预填失败不阻塞创建。
        """
        model = str(upstream_model or "").strip()
        state = await get_state(session)
        meta = self._store.load_meta()
        today = self._today()
        snapshot = self._snapshot_info(meta, today)
        result: dict[str, Any] = {
            "upstream_model": model,
            "matched": False,
            "matched_by": MATCH_UNMATCHED,
            "version_loose": False,
            "defaults": None,
            "sources": {},
            "provenance": None,
            "snapshot": snapshot,
            "candidates": [],
            "review_required": True,
        }
        if not model:
            return result
        try:
            payload = self._store.load_api()
        except Exception as exc:  # noqa: BLE001 - 快照坏了也要能打开表单
            logger.warning("model_defaults.prefill cannot read snapshot: %s", exc)
            return result
        if payload is None:
            return result
        index = self._index_for(payload, state)
        matched = match_model(
            model,
            index,
            fuzzy_auto=state.fuzzy_auto,
            fuzzy_review=state.fuzzy_review,
        )
        candidates = [
            {
                "provider": item.provider,
                "model_id": item.model_id,
                "score": item.score,
                "tier": item.tier,
            }
            for item in matched.candidates
        ]
        if not matched.matched or matched.matched_by not in PREFILL_MATCH_LEVELS:
            result["matched_by"] = matched.matched_by
            result["candidates"] = candidates
            return result
        merged = merge_entries(matched.entries, matched_by=matched.matched_by)
        providers = [str(item.get("provider") or "") for item in merged.providers]
        providers = [name for name in providers if name]
        canonical = str(merged.fields.get("canonical_model_id") or "")
        result.update(
            {
                "matched": True,
                "matched_by": matched.matched_by,
                "version_loose": matched.version_loose,
                "defaults": self._prefill_payload(merged, matched.entries),
                "sources": self._sources_payload(merged),
                "provenance": {
                    "model_id": canonical or logical_model_key(matched.entries),
                    "canonical_model_id": canonical or None,
                    "provider": providers[0] if providers else "",
                    "matched_by": matched.matched_by,
                    "rule_version": str(merged.rule_version),
                    "pulled_at": self._iso(meta.fetched_at),
                },
                "candidates": candidates,
                "review_required": matched.matched_by in REVIEW_MATCH_LEVELS,
            }
        )
        return result

    def _prefill_payload(
        self, merged: Any, entries: tuple[ModelEntry, ...]
    ) -> dict[str, Any]:
        prefilled = prefill_defaults(merged)
        parameters = dict(prefilled.get("parameters") or {})
        has_type = any(str(entry.fields.get("type") or "") for entry in entries)
        capabilities: dict[str, bool | None] = {}
        for flag in ("tool_call", "reasoning", "attachment", "structured_output"):
            values = [
                bool(entry.fields.get(flag))
                for entry in entries
                if flag in (entry.fields or {})
            ]
            capabilities[flag] = True if any(values) else (False if values else None)
        return {
            "model_type": str(prefilled.get("model_type") or "") if has_type else None,
            "model_type_source": "upstream" if has_type else None,
            "parameters": {
                "max_input_tokens": parameters.get("max_input_tokens"),
                "max_output_tokens": parameters.get("max_output_tokens"),
                "reasoning_level": parameters.get("reasoning_level") or None,
                "input_types": parameters.get("input_types"),
                "output_types": parameters.get("output_types"),
                **capabilities,
            },
            "input_price_per_1m": prefilled.get("input_price_per_1m"),
            "output_price_per_1m": prefilled.get("output_price_per_1m"),
            "cache_read_price_per_1m": prefilled.get("cache_read_price_per_1m"),
            "cache_write_price_per_1m": prefilled.get("cache_write_price_per_1m"),
        }

    def _sources_payload(self, merged: Any) -> dict[str, dict[str, Any]]:
        sources: dict[str, dict[str, Any]] = {}
        for field, info in field_sources(merged).items():
            providers = [str(name) for name in (info.get("providers") or [])]
            sources[field] = {
                "provider": providers[0] if providers else "",
                "providers": providers,
                "rule": str(info.get("rule") or ""),
            }
        return sources
