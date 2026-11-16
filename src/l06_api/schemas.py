from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from l03_domain.naming import CALL_MODEL_RE, UPSTREAM_MODEL_RE
from l03_domain.schedule import DEFAULT_TZ, MAX_RULES, is_known_timezone

CAPABILITY_FLAG_KEYS = ("tool_call", "reasoning", "attachment", "structured_output")
CHANNEL_SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
PUBLIC_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
SCHEDULE_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# ADR-077 routing strategies; mirrors route_settings.ROUTE_STRATEGIES (kept
# local so this schema module stays free of service-layer imports).
ROUTE_STRATEGY_VALUES = ("uniform", "stable", "cost", "latency")


def _normalized_route_strategy(value: str | None) -> str | None:
    """Lowercase and validate a route_strategy value; None passes."""
    if value is None:
        return value
    text = value.strip().lower()
    if text in ROUTE_STRATEGY_VALUES:
        return text
    raise ValueError("route_strategy 仅支持 uniform/stable/cost/latency")


def _combo_route_strategy(value: str | None) -> str | None:
    """Combo override: None untouched, an empty value follows the global."""
    if value is None:
        return value
    text = value.strip()
    if not text:
        return ""
    return _normalized_route_strategy(text)


def _strip(value: str) -> str:
    return value.strip()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        return value.strip()


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class AdminSummary(BaseModel):
    id: str
    username: str
    created_at: int
    updated_at: int


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: int
    admin: AdminSummary


class ChannelKeyInput(BaseModel):
    label: str = Field(default="", max_length=64)
    api_key: str = Field(min_length=1, max_length=4096)

    @field_validator("label", "api_key", mode="before")
    @classmethod
    def clean_key_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ChannelKeyUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None

    @field_validator("label", mode="before")
    @classmethod
    def clean_label(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ChannelKeyOut(BaseModel):
    id: str
    label: str = ""
    masked: str
    enabled: bool = True
    last_used_at: int | None = None
    created_at: int


class ChannelKeyList(BaseModel):
    items: list[ChannelKeyOut]
    total: int


class ChannelCreate(BaseModel):
    slug: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=500)
    connection_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=4096)
    api_keys: list[ChannelKeyInput] = Field(default_factory=list)
    # Omitted means "detect automatically"; an explicit value pins the protocol.
    protocol: str | None = None
    priority: int = Field(default=1, ge=1, le=1000)
    enabled: bool = True
    rewrite_rules: dict[str, Any] | None = None

    @field_validator(
        "slug", "name", "note", "connection_url", "api_key", "protocol", mode="before"
    )
    @classmethod
    def clean_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ChannelUpdate(BaseModel):
    slug: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=500)
    connection_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    protocol: str | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    enabled: bool | None = None
    rewrite_rules: dict[str, Any] | None = None

    @field_validator(
        "slug", "name", "note", "connection_url", "api_key", "protocol", mode="before"
    )
    @classmethod
    def clean_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ChannelOut(BaseModel):
    id: str
    slug: str
    name: str
    note: str = ""
    connection_url: str
    endpoint_preview: str
    models_endpoint_preview: str
    api_key_set: bool
    api_key_masked: str
    api_key_count: int = 0
    api_key_masks: list[str] = Field(default_factory=list)
    api_keys: list[ChannelKeyOut] = Field(default_factory=list)
    protocol: str
    protocol_source: str
    auth_style: str
    priority: int = 1
    enabled: bool
    connection_status: str
    connection_latency_ms: int | None
    connection_message: str
    connection_tested_at: int | None
    last_model_probe_latency_ms: int | None = None
    last_model_probe_at: int | None = None
    last_model_probe_status: str = "unknown"
    override_sections: list[str] = Field(default_factory=list)
    rewrite_rules: dict[str, Any] = Field(default_factory=dict)
    model_count: int
    created_at: int
    updated_at: int


class ChannelList(BaseModel):
    items: list[ChannelOut]
    total: int


class ChannelDiscoverRequest(BaseModel):
    # Existing channels can reuse the stored key/rules by id while sending
    # unsaved form overrides for address/protocol/rewrite rules.
    channel_id: str | None = None
    connection_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    protocol: str | None = None
    rewrite_rules: dict[str, Any] | None = None

    @field_validator(
        "channel_id", "connection_url", "api_key", "protocol", mode="before"
    )
    @classmethod
    def clean_discover_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ChannelDraftTestRequest(ChannelDiscoverRequest):
    """Test the exact form values without persisting the verdict."""


class DiscoveredModel(BaseModel):
    upstream_model: str
    already_added: bool = False
    # 从上游模型名推断的类型（推断不出时保持 chat/default），前端可据此预勾选。
    model_type: str = "chat"
    model_type_source: str = "default"


class ChannelDiscoverResult(BaseModel):
    status: Literal["ok", "timeout", "error"]
    models: list[DiscoveredModel]
    protocol: str
    auth_style: str
    http_status: int | None = None
    latency_ms: int
    message: str


class ConnectionTestResult(BaseModel):
    status: Literal["ok", "timeout", "error", "unknown"]
    latency_ms: int
    http_status: int | None = None
    message: str
    model_count: int = 0
    models_preview: list[str] = Field(default_factory=list)
    protocol: str
    auth_style: str
    tested_at: int | None = None


class RewritePreviewRequest(BaseModel):
    section: Literal["request_headers", "request_body", "response_headers", "response_body"]
    # The editor previews unsaved rules; omitted means use the stored ruleset.
    rules: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


class RewritePreviewResult(BaseModel):
    section: str
    before: Any
    after: Any
    diff: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skipped: str = ""


class ModelParametersIn(BaseModel):
    max_input_tokens: int = Field(default=0, ge=0, le=100_000_000)
    max_output_tokens: int = Field(default=0, ge=0, le=100_000_000)
    reasoning_level: str = Field(default="", max_length=32)
    input_types: list[str] = Field(default_factory=lambda: ["text"])
    output_types: list[str] = Field(default_factory=lambda: ["text"])
    # Capability flags (ADR-073): tri-state booleans. None = unknown, and the
    # write path drops them instead of storing a null.
    tool_call: bool | None = None
    reasoning: bool | None = None
    attachment: bool | None = None
    structured_output: bool | None = None

    @field_validator("reasoning_level", mode="before")
    @classmethod
    def clean_reasoning(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("input_types", "output_types")
    @classmethod
    def clean_types(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip().lower()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned or ["text"]


class ModelParametersOut(BaseModel):
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    reasoning_level: str = ""
    input_types: list[str] = Field(default_factory=lambda: ["text"])
    output_types: list[str] = Field(default_factory=lambda: ["text"])
    # Capability flags (ADR-073): tri-state booleans. None means "unknown" and
    # is left out of the response so rows written before ADR-073 keep the very
    # same shape.
    tool_call: bool | None = None
    reasoning: bool | None = None
    attachment: bool | None = None
    structured_output: bool | None = None

    @model_serializer(mode="wrap")
    def drop_unknown_capabilities(self, handler) -> dict[str, Any]:
        data = handler(self)
        for key in CAPABILITY_FLAG_KEYS:
            if data.get(key) is None:
                data.pop(key, None)
        return data


class BreakerConfigIn(BaseModel):
    failure_threshold: int | None = Field(default=None, ge=1, le=1000)
    cooldown_s: int | None = Field(default=None, ge=0, le=86400)
    rate_limit_cooldown_s: int | None = Field(default=None, ge=0, le=86400)
    half_open_max: int | None = Field(default=None, ge=1, le=100)
    escalate_mode: Literal["never", "after_n", "immediate"] | None = None
    escalate_after: int | None = Field(default=None, ge=1, le=1000)
    fault_cooldown_s: int | None = Field(default=None, ge=0, le=604800)


class BreakerConfigOut(BaseModel):
    failure_threshold: int
    cooldown_s: int
    rate_limit_cooldown_s: int
    half_open_max: int
    escalate_mode: Literal["never", "after_n", "immediate"]
    escalate_after: int
    fault_cooldown_s: int


class ScheduleSegmentIn(BaseModel):
    days: list[int] = Field(min_length=1)
    start: str
    end: str

    @field_validator("days")
    @classmethod
    def clean_days(cls, value: list[int]) -> list[int]:
        days = sorted({int(day) for day in value})
        if any(day < 1 or day > 7 for day in days):
            raise ValueError("星期需用 ISO 编号 1-7（1=周一）")
        return days

    @field_validator("start", "end")
    @classmethod
    def clean_time(cls, value: str) -> str:
        text = value.strip()
        if not SCHEDULE_TIME_RE.match(text):
            raise ValueError("时间需为 24 小时制 HH:MM")
        return text


class ScheduleRuleIn(BaseModel):
    """One recurring window of the v2 schedule document (ADR-070 §1.3)."""

    kind: Literal["daily", "weekly", "monthly"] = "weekly"
    days: list[int] = Field(default_factory=list)
    days_of_month: list[int] = Field(default_factory=list)
    start: str
    end: str

    @field_validator("days")
    @classmethod
    def clean_days(cls, value: list[int]) -> list[int]:
        days = sorted({int(day) for day in value})
        if any(day < 1 or day > 7 for day in days):
            raise ValueError("星期需用 ISO 编号 1-7（1=周一）")
        return days

    @field_validator("days_of_month")
    @classmethod
    def clean_days_of_month(cls, value: list[int]) -> list[int]:
        days = sorted({int(day) for day in value})
        if any(day < 1 or day > 31 for day in days):
            raise ValueError("每月日期需为 1-31")
        return days

    @field_validator("start", "end")
    @classmethod
    def clean_time(cls, value: str) -> str:
        text = value.strip()
        if not SCHEDULE_TIME_RE.match(text):
            raise ValueError("时间需为 24 小时制 HH:MM")
        return text

    @model_validator(mode="after")
    def clean_kind(self) -> "ScheduleRuleIn":
        if self.kind == "weekly" and not self.days:
            raise ValueError("weekly 规则需要 days（ISO 1-7）")
        if self.kind == "monthly" and not self.days_of_month:
            raise ValueError("monthly 规则需要 days_of_month（1-31）")
        return self


class ScheduleIn(BaseModel):
    version: int = 2
    timezone: str = DEFAULT_TZ
    # v1 的 segments 仍可读入（等价于 weekly 规则）；写入一律落 v2 的 rules。
    # 条数超限在写侧拒绝（ADR-070 §2.4）：静默截断会变成「保存成功但只生效前 10 条」。
    segments: list[ScheduleSegmentIn] = Field(default_factory=list, max_length=MAX_RULES)
    rules: list[ScheduleRuleIn] = Field(default_factory=list, max_length=MAX_RULES)

    @field_validator("timezone")
    @classmethod
    def clean_timezone(cls, value: str) -> str:
        text = value.strip()
        if not is_known_timezone(text):
            raise ValueError("时区必须是有效的 IANA 名称，例如 Asia/Shanghai")
        return text


class ScheduleRuleOut(BaseModel):
    kind: str
    days: list[int] = Field(default_factory=list)
    days_of_month: list[int] = Field(default_factory=list)
    start: str
    end: str

    @model_serializer(mode="wrap")
    def _omit_empty_day_fields(self, handler):
        """Mirror the stored document: a rule carries only its day selector."""
        data = handler(self)
        if not self.days:
            data.pop("days", None)
        if not self.days_of_month:
            data.pop("days_of_month", None)
        return data


class ScheduleOut(BaseModel):
    version: int = 2
    timezone: str
    rules: list[ScheduleRuleOut]


class ModelPrefillRequest(BaseModel):
    upstream_model: str


class PrefillParameters(BaseModel):
    """Pre-filled parameter values; null means "do not prefill"."""

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    reasoning_level: str | None = None
    input_types: list[str] | None = None
    output_types: list[str] | None = None
    tool_call: bool | None = None
    reasoning: bool | None = None
    attachment: bool | None = None
    structured_output: bool | None = None


class PrefillDefaults(BaseModel):
    model_type: str | None = None
    model_type_source: str | None = None
    parameters: PrefillParameters = Field(default_factory=PrefillParameters)
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    cache_read_price_per_1m: float | None = None
    cache_write_price_per_1m: float | None = None


class PrefillFieldSource(BaseModel):
    """Per-field provenance; shown in the UI only, never persisted."""

    provider: str = ""
    providers: list[str] = Field(default_factory=list)
    rule: str = ""


class PrefillProvenance(BaseModel):
    model_id: str = ""
    canonical_model_id: str | None = None
    provider: str = ""
    matched_by: str = "unmatched"
    rule_version: str = "1"
    pulled_at: str = ""


class PrefillSnapshot(BaseModel):
    available: bool = False
    pulled_at: str | None = None
    age_days: int | None = None
    stale: bool = False


class PrefillCandidate(BaseModel):
    provider: str = ""
    model_id: str = ""
    score: float = 0.0
    tier: str = ""


class ModelPrefillOut(BaseModel):
    """Response of POST /api/channel-models/prefill (ADR-073 decision 7)."""

    upstream_model: str = ""
    matched: bool = False
    matched_by: str = "unmatched"
    version_loose: bool = False
    defaults: PrefillDefaults | None = None
    sources: dict[str, PrefillFieldSource] = Field(default_factory=dict)
    provenance: PrefillProvenance | None = None
    snapshot: PrefillSnapshot = Field(default_factory=PrefillSnapshot)
    candidates: list[PrefillCandidate] = Field(default_factory=list)
    review_required: bool = False


class ModelDefaultsSnapshotOut(BaseModel):
    available: bool = False
    pulled_at: str | None = None
    age_days: int | None = None
    stale: bool = False
    providers: int = 0
    entries: int = 0
    bytes: int = 0
    source_url: str = ""
    etag: str = ""
    last_modified: str = ""
    http_date: str = ""
    conditional: bool = False
    error_summary: str = ""
    error_at: int | None = None


class ModelDefaultsRegistryOut(BaseModel):
    """Derived-table staleness dimension (ADR-073 decision 9)."""

    models: int = 0
    last_updated: str | None = None


class ModelDefaultsStatusOut(BaseModel):
    enabled: bool = False
    rule_version: str = "1"
    today: str = ""
    last_pull: str = ""
    last_attempt: str = ""
    fuzzy_auto: float = 0.92
    fuzzy_review: float = 0.75
    tier_official: list[str] | None = None
    tier_clouds: list[str] | None = None
    labs_min_providers: int = 4
    defaults_count: int = 0
    snapshot: ModelDefaultsSnapshotOut = Field(default_factory=ModelDefaultsSnapshotOut)
    registry: ModelDefaultsRegistryOut | None = None


class ModelDefaultsRefreshOut(BaseModel):
    status: str = ""
    trigger: str = ""
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


class ModelDefaultsAdoptRequest(BaseModel):
    """Body of POST /api/channel-models/{id}/adopt-defaults (ADR-073 decision 6)."""

    force: bool = False


class ModelDefaultsAdoptOut(BaseModel):
    model_id: str = ""
    upstream_model: str = ""
    matched: bool = False
    matched_by: str = "unmatched"
    adopted: list[str] = Field(default_factory=list)
    force: bool = False


class ChannelModelCreate(BaseModel):
    upstream_model: str
    call_model: str | None = None
    # 模型类型：chat / embedding / rerank / image_generation / audio_tts /
    # audio_transcription / video。显式指定时来源记为 manual。
    model_type: str | None = None
    model_type_source: str | None = None
    parameters: ModelParametersIn = Field(default_factory=ModelParametersIn)
    input_price_per_1m: float = Field(default=0, ge=0)
    output_price_per_1m: float = Field(default=0, ge=0)
    cache_read_price_per_1m: float | None = Field(default=None, ge=0)
    cache_write_price_per_1m: float | None = Field(default=None, ge=0)
    enabled: bool = True
    schedule: ScheduleIn | None = None
    cooldown: BreakerConfigIn | None = None

    @field_validator("upstream_model")
    @classmethod
    def clean_upstream(cls, value: str) -> str:
        value = value.strip()
        if not UPSTREAM_MODEL_RE.match(value):
            raise ValueError("上游模型名不合法：需字母/数字开头，可含 . _ : - / #，最长 128 位")
        return value

    @field_validator("call_model")
    @classmethod
    def clean_call(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not CALL_MODEL_RE.match(value):
            raise ValueError("调用模型不合法：需字母/数字开头，可含 . _ : - / #，最长 128 位")
        return value

class ChannelModelUpdate(BaseModel):
    upstream_model: str | None = None
    call_model: str | None = None
    model_type: str | None = None
    model_type_source: str | None = None
    parameters: ModelParametersIn | None = None
    input_price_per_1m: float | None = Field(default=None, ge=0)
    output_price_per_1m: float | None = Field(default=None, ge=0)
    cache_read_price_per_1m: float | None = Field(default=None, ge=0)
    cache_write_price_per_1m: float | None = Field(default=None, ge=0)
    enabled: bool | None = None
    # 直连条目定时封禁：ISO-8601 时间点 = 封禁到该时刻；null = 永久封禁（与
    # enabled=false 搭配）；字段缺省 = 不改动。读取侧惰性判活，到期即视为启用。
    banned_until: str | None = None
    schedule: ScheduleIn | None = None
    cooldown: BreakerConfigIn | None = None

    @field_validator("banned_until")
    @classmethod
    def clean_banned_until(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("banned_until 需为 ISO-8601 时间字符串，或 null 表示永久封禁")
        return value

    @field_validator("upstream_model")
    @classmethod
    def clean_upstream(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not UPSTREAM_MODEL_RE.match(value):
            raise ValueError("上游模型名不合法：需字母/数字开头，可含 . _ : - / #，最长 128 位")
        return value

    @field_validator("call_model")
    @classmethod
    def clean_call(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("调用模型不能为空")
        if not CALL_MODEL_RE.match(value):
            raise ValueError("调用模型不合法：需字母/数字开头，可含 . _ : - / #，最长 128 位")
        return value

class ChannelModelBulkCreate(BaseModel):
    upstream_models: list[str] = Field(min_length=1, max_length=500)

    @field_validator("upstream_models")
    @classmethod
    def clean_upstream_models(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            raise ValueError("请选择至少一个上游模型")
        return cleaned


class ChannelModelOut(BaseModel):
    id: str
    channel_id: str
    upstream_model: str
    call_model: str
    model_type: str
    model_type_source: str
    parameters: ModelParametersOut
    input_price_per_1m: float
    output_price_per_1m: float
    cache_read_price_per_1m: float | None
    cache_write_price_per_1m: float | None
    # effective view: a timed ban whose moment passed reads as enabled again.
    enabled: bool
    # epoch ms of the active timed ban, or null when there is none.
    banned_until: int | None = None
    # 读侧判定的不可调用原因：MODEL_BANNED / MODEL_COOLDOWN / COOLDOWN_WINDOW。
    cooldown_reason: str | None = None
    health_status: Literal["normal", "open", "half_open", "fault"]
    visible_status: Literal["normal", "open", "half_open", "fault", "disabled"]
    consecutive_failures: int
    consecutive_trip_count: int
    escalation_trip_count: int
    opened_at: int | None
    cooldown_until: int | None
    fault_until: int | None
    next_retry_at: int | None
    schedule: ScheduleOut
    cooldown: BreakerConfigOut
    probe_status: Literal["ok", "timeout", "error", "unknown"]
    probe_latency_ms: int | None
    probe_message: str
    probed_at: int | None
    probe_default: bool = False
    created_at: int
    updated_at: int


class ChannelModelBulkResult(BaseModel):
    created: int
    skipped: int
    errors: list[str] = Field(default_factory=list)
    items: list[ChannelModelOut] = Field(default_factory=list)


class ChannelModelList(BaseModel):
    items: list[ChannelModelOut]
    total: int


class ChannelModelBatchUpdate(BaseModel):
    """One bulk operation across many channel models (ADR-070 §4.1)."""

    model_ids: list[str] = Field(min_length=1, max_length=200)
    op: Literal["ban", "unban", "cooldown", "schedule"]
    # op=cooldown: ISO-8601 instant the cooldown ends.
    banned_until: str | None = None
    # op=schedule: the whole v2 document that replaces the stored one.
    schedule: ScheduleIn | None = None

    @field_validator("model_ids")
    @classmethod
    def clean_ids(cls, value: list[str]) -> list[str]:
        ids = [text for text in dict.fromkeys(str(item).strip() for item in value) if text]
        if not ids:
            raise ValueError("model_ids 不能为空")
        return ids

    @model_validator(mode="after")
    def clean_op(self) -> "ChannelModelBatchUpdate":
        if self.op == "cooldown" and not (self.banned_until or "").strip():
            raise ValueError("op=cooldown 需要 banned_until（ISO-8601 时间字符串）")
        if self.op == "schedule" and self.schedule is None:
            raise ValueError("op=schedule 需要 schedule")
        return self


class ChannelModelBatchItem(BaseModel):
    model_id: str
    ok: bool
    error: str | None = None


class ChannelModelBatchResult(BaseModel):
    results: list[ChannelModelBatchItem]
    succeeded: int
    failed: int


class ProbeResult(BaseModel):
    model_id: str
    channel_id: str
    upstream_model: str
    call_model: str
    status: Literal["ok", "timeout", "error", "unknown"]
    latency_ms: int
    message: str
    probed_at: int
    mode: Literal["shallow", "deep"]
    protocol: str
    auth_style: str
    http_status: int | None = None
    # 本次探测走的分支：chat / embedding / rerank，其余类型需 force=true。
    model_type: str = "chat"


class ProbeAllResult(BaseModel):
    channel_id: str
    mode: Literal["shallow", "deep"]
    total: int
    # image/audio/video 默认不自动探测，这些模型只计数不进 items。
    skipped: int = 0
    # True when this batch result came from the short-TTL process cache.
    cached: bool = False
    ok: int
    timeout: int
    error: int
    unknown: int
    items: list[ProbeResult]
    protocol: str
    auth_style: str
    protocol_source: str


class TargetInput(BaseModel):
    target_type: Literal["channel_model", "public_model"]
    channel_model_id: str | None = None
    child_public_model_id: str | None = None
    priority: int = Field(default=1, ge=1, le=1000)
    enabled: bool = True


class PublicModelCreate(BaseModel):
    name: str
    kind: Literal["single", "combo"] = "single"
    description: str = ""
    strategy: str = "priority"
    enabled: bool = True
    targets: list[TargetInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not PUBLIC_MODEL_RE.match(value):
            raise ValueError("公开模型名需字母/数字开头，仅含字母/数字/点/下划线/中划线，最长 64 位")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()


class PublicModelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    strategy: str | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not PUBLIC_MODEL_RE.match(value):
            raise ValueError("公开模型名需字母/数字开头，仅含字母/数字/点/下划线/中划线，最长 64 位")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class TargetsReplace(BaseModel):
    targets: list[TargetInput]


class ComboTargetInput(BaseModel):
    target_type: Literal["channel_model", "child_combo", "combo", "public_model"] = "channel_model"
    channel_model_id: str | None = Field(default=None, max_length=40)
    child_combo_id: str | None = Field(default=None, max_length=40)
    priority: int = Field(default=1, ge=1, le=1000)
    enabled: bool = True

    @field_validator("channel_model_id", "child_combo_id", mode="before")
    @classmethod
    def clean_id(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ComboCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=50)
    # ADR-077 per-combo routing override: "" follows the global strategy,
    # 0 follows the global observation window.
    route_strategy: str = Field(default="", max_length=16)
    route_window_minutes: int = Field(default=0, ge=0, le=1440)
    # Object {rules:[...], default_action} or the legacy bare rule list.
    failover_rules: Any = None
    targets: list[ComboTargetInput] = Field(default_factory=list, max_length=200)

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("route_window_minutes")
    @classmethod
    def check_route_window(cls, value: int) -> int:
        # 0 = follow the global window; otherwise the override must be a
        # full ADR-077 observation window (15..1440 minutes).
        if value == 0 or 15 <= value <= 1440:
            return value
        raise ValueError("route_window_minutes 必须为 0 或 15–1440")

    @field_validator("route_strategy")
    @classmethod
    def check_route_strategy(cls, value: str) -> str:
        return _combo_route_strategy(value)


class ComboUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=50)
    route_strategy: str | None = Field(default=None, max_length=16)
    route_window_minutes: int | None = Field(default=None, ge=0, le=1440)
    failover_rules: Any = None
    targets: list[ComboTargetInput] | None = Field(default=None, max_length=200)

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_optional_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("route_window_minutes")
    @classmethod
    def check_route_window(cls, value: int | None) -> int | None:
        # 0 = follow the global window; otherwise 15..1440 (see ComboCreate).
        if value is None or value == 0 or 15 <= value <= 1440:
            return value
        raise ValueError("route_window_minutes 必须为 0 或 15–1440")

    @field_validator("route_strategy")
    @classmethod
    def check_route_strategy(cls, value: str | None) -> str | None:
        return _combo_route_strategy(value)


class ComboMemberOut(BaseModel):
    id: str
    target_type: str
    priority: int
    enabled: bool
    available: bool
    channel_model_id: str | None
    child_combo_id: str | None
    channel_id: str | None
    channel_slug: str
    channel_name: str
    call_model: str
    upstream_model: str
    child_combo_name: str
    child_combo_enabled: bool
    protocol: str
    created_at: int


class ComboOut(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    max_attempts: int
    route_strategy: str = ""
    route_window_minutes: int = 0
    failover_rules: dict[str, Any]
    member_count: int
    available_count: int
    depth: int
    max_depth: int
    callable: bool
    created_at: int
    updated_at: int
    members: list[ComboMemberOut]


class ComboList(BaseModel):
    items: list[ComboOut]
    total: int


class ApiKeyCreate(BaseModel):
    name: str = "default"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip() or "default"
        if len(value) > 64:
            raise ValueError("密钥标识最长 64 位")
        return value


class ProbeRequest(BaseModel):
    mode: Literal["shallow", "deep"] = "deep"
    # image/audio/video 这类只存不探的模型，必须显式 force=true 才发起探测。
    force: bool = False


class ChatMessage(BaseModel):
    role: str
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    user: str | None = None
    stream_options: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None

    def to_upstream_body(self) -> dict[str, Any]:
        body = self.model_dump(exclude_none=True)
        body.pop("extra_body", None)
        if self.extra_body:
            body.update(self.extra_body)
        return body


class KeyCreate(BaseModel):
    label: str = Field(default="default", max_length=64)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        return value.strip() or "default"


class KeyOut(BaseModel):
    id: str
    label: str
    prefix: str
    enabled: bool
    last_used_at: int | None
    created_at: int


class KeyCreatedOut(KeyOut):
    key: str


class KeyList(BaseModel):
    items: list[KeyOut]
    total: int


class CatalogItem(BaseModel):
    id: str
    name: str
    channel_id: str
    channel_slug: str
    channel_name: str
    protocol: str
    upstream_model: str
    call_model: str
    # 组合/分组条目没有单一模型类型，统一按 chat 报（direct 条目用真实类型）。
    model_type: str = "chat"
    model_type_source: str = "default"
    parameters: ModelParametersOut
    input_price_per_1m: float
    output_price_per_1m: float
    cache_read_price_per_1m: float | None
    cache_write_price_per_1m: float | None
    enabled: bool
    # 定时封禁到期时刻（epoch ms）：管理目录据此显示「封禁至 …」，null 表示未定时封禁。
    banned_until: int | None = None
    # 读侧判定的不可调用原因：MODEL_BANNED / MODEL_COOLDOWN / COOLDOWN_WINDOW，
    # null 表示当下可调用（ADR-070 D3）。
    cooldown_reason: str | None = None
    # 排期只下发 v2 文档（历史 v1 行归一后输出），无排期为 null。
    schedule: ScheduleOut | None = None
    created_at: int
    probe_status: str = ""
    probe_message: str = ""
    probed_at: int | None = None


class CatalogList(BaseModel):
    items: list[CatalogItem]
    total: int


class RequestLogOut(BaseModel):
    id: str
    request_id: str
    upstream_request_id: str = ""
    user_id: str | None
    user_name: str = ""
    api_key_id: str | None
    api_key_label: str
    requested_model: str | None = None
    channel_name: str
    upstream_model: str
    response_model: str
    is_stream: bool
    source: str = "chat"
    # Request-side audit data: peer address, the forwarding headers exactly
    # as received, their flattened addresses, and redacted client headers.
    client_ip: str = ""
    forwarded_ips: list[str] = []
    forwarded_sources: list[dict[str, str]] = []
    request_headers: dict[str, str] = {}
    http_status: int
    error_code: str | None
    error_message: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_ratio: float
    cost_usd: float
    ttft_ms: int | None
    ttfa_ms: int | None = None
    ttlt_ms: int | None = None
    total_ms: int
    throughput_tps: float | None = None
    usage_estimated: bool = False
    attempts: list[dict[str, Any]]
    attempt_error_count: int = 0
    rewrite_applied: bool = False
    rewrite_skipped: str = ""
    rewrite_diff: dict[str, Any] = {}
    params: dict[str, Any] = {}
    response_headers: dict[str, str] = {}
    error_body: str | None = None
    created_at: int


class LogGroup(BaseModel):
    """One aggregate row: a combo bucket or a contiguous status segment."""

    key: str
    label: str
    status_code: int | None = None
    status_label: str = ""
    first_at: int
    last_at: int
    requests: int = 0
    attempts: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_ratio: float = 0.0
    cost_usd: float = 0.0
    ttft_p50_ms: int = 0
    total_p50_ms: int = 0
    total_p95_ms: int = 0
    channels: list[str] = []
    error_codes: list[str] = []
    sample_request_ids: list[str] = []


class RequestLogList(BaseModel):
    items: list[RequestLogOut]
    total: int
    page: int = 1
    page_size: int = 20
    groups: list[LogGroup] = []
    view: str = "requests"
    scan_limited: bool = False


class LogUserOption(BaseModel):
    id: str
    name: str


class LogFilterOptions(BaseModel):
    channels: list[str]
    models: list[str]
    key_labels: list[str]
    users: list[LogUserOption]
    truncated: dict[str, bool] = {}

BodyPolicy = Literal["off", "errors", "all"]


class SettingsOut(BaseModel):
    body_policy: BodyPolicy
    body_truncate_bytes: int
    body_retention_days: int
    summary_max_bytes: int
    updated_at: int | None
    updated_by: str
    log_retention_days: int = 30
    redaction_forced: bool = True
    gateway_base_url: str = ""
    gateway_updated_at: int | None = None
    gateway_updated_by: str = ""
    upstream_connect_timeout_s: float
    upstream_first_byte_timeout_s: float
    upstream_read_timeout_s: float
    upstream_timeouts_updated_at: int | None = None
    upstream_timeouts_updated_by: str = ""
    upstream_timeouts_is_default: bool = True
    # ADR-077 routing strategy + observation window + admission cap.
    route_strategy: str = "uniform"
    route_window_minutes: int = 30
    gateway_inflight_cap: int = 256


class SettingsUpdate(BaseModel):
    body_policy: BodyPolicy | None = None
    gateway_base_url: str | None = Field(default=None, max_length=512)
    upstream_connect_timeout_s: float | None = Field(default=None, gt=0, le=86_400)
    upstream_first_byte_timeout_s: float | None = Field(default=None, gt=0, le=86_400)
    upstream_read_timeout_s: float | None = Field(default=None, gt=0, le=86_400)
    upstream_timeouts_reset: bool = False
    route_strategy: str | None = Field(default=None, max_length=16)
    route_window_minutes: int | None = Field(default=None, ge=15, le=1440)
    gateway_inflight_cap: int | None = Field(default=None, ge=0, le=100_000)
    body_truncate_bytes: int | None = Field(default=None, ge=1024, le=1_048_576)
    body_retention_days: int | None = Field(default=None, ge=1, le=90)
    summary_max_bytes: int | None = Field(default=None, ge=1024, le=4_194_304)

    @field_validator("route_strategy")
    @classmethod
    def check_route_strategy(cls, value: str | None) -> str | None:
        return _normalized_route_strategy(value)

class LatencyPercentiles(BaseModel):
    samples: int = 0
    ttft: dict[str, int] | None = None
    ttfa: dict[str, int] | None = None
    ttlt: dict[str, int] | None = None
    total: dict[str, int] | None = None


class LogSummary(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float
    # Cache panel: A token-weighted rate, B request-level rate, C cacheable
    # share, D saved tokens/money. All four read one cohort (known prompt,
    # interrupted streams included with zero cache hits).
    cache_hit_rate: float = 0.0
    cache_hit_rate_samples: int = 0
    cache_request_hit_rate: float = 0.0
    cache_request_hits: int = 0
    cacheable_ratio: float = 0.0
    cacheable_tokens: int = 0
    cacheable_sessions: int = 0
    cache_saved_tokens: int = 0
    cache_saved_usd: float = 0.0
    active_minutes: int
    span_minutes: float
    rpm: float
    tpm: float
    peak_rpm: int
    estimated_rows: int


class DashboardSummary(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_hit_rate: float = 0.0
    cache_hit_rate_samples: int = 0
    cache_request_hit_rate: float = 0.0
    cache_request_hits: int = 0
    cacheable_ratio: float = 0.0
    cacheable_tokens: int = 0
    cacheable_sessions: int = 0
    cache_saved_tokens: int = 0
    cache_saved_usd: float = 0.0
    cost_usd: float
    errors: int
    error_rate: float
    p95_ttft_ms: int | None = None
    p95_total_ms: int | None = None
    latency: LatencyPercentiles
    start_ms: int
    end_ms: int
    active_minutes: int = 0
    span_minutes: float = 0.0
    rpm: float = 0.0
    tpm: float = 0.0
    peak_rpm: int = 0
    estimated_rows: int = 0


class TimeseriesModelPoint(BaseModel):
    name: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    cache_hit_rate: float = 0.0
    cost_usd: float
    errors: int
    p95_total_ms: int | None = None


class TimeseriesPoint(BaseModel):
    start_ms: int
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    cache_hit_rate: float = 0.0
    cost_usd: float
    errors: int
    p95_ttft_ms: int | None = None
    p95_ttfa_ms: int | None = None
    p95_ttlt_ms: int | None = None
    p95_total_ms: int | None = None
    by_model: list[TimeseriesModelPoint] = []
    by_upstream_model: list[TimeseriesModelPoint] = []
    by_channel: list[TimeseriesModelPoint] = []


class NamedCount(BaseModel):
    name: str
    requests: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0


class ErrorCount(BaseModel):
    code: str
    count: int


class HeatmapCell(BaseModel):
    weekday: int
    hour: int
    requests: int


class DashboardTimeseries(BaseModel):
    range: str
    timezone: str
    granularity: str
    start_ms: int
    end_ms: int
    points: list[TimeseriesPoint]
    top_models: list[NamedCount]
    top_upstream_models: list[NamedCount] = []
    top_channels: list[NamedCount]
    error_distribution: list[ErrorCount]
    heatmap: list[HeatmapCell]

class DashboardFlowNode(BaseModel):
    id: str
    stage: str
    label: str
    value: int
    share: float


class DashboardFlowLink(BaseModel):
    source: str
    target: str
    value: int


class DashboardFlowTotal(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int


class DashboardFlows(BaseModel):
    # 分流图：固定五级链路上的节点、边与窗口总量；metric 决定 value 的口径
    # （requests = 请求条数，tokens = prompt + completion）。
    metric: Literal["requests", "tokens"]
    stages: list[str]
    nodes: list[DashboardFlowNode]
    links: list[DashboardFlowLink]
    total: DashboardFlowTotal


class KeyUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None

    @field_validator("label")
    @classmethod
    def clean_optional_label(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

class CatalogEntry(CatalogItem):
    # 条目二元化：direct = 渠道模型直连；combo = 组合（manual 显式组合可编辑，
    # auto 隐式分组查询期派生只读）。
    kind: Literal["direct", "combo"] = "direct"
    origin: Literal["manual", "auto"] | None = None
    status: str = "enabled"
    health_status: Literal["normal", "open", "half_open", "fault"] | None = None
    visible_status: Literal["normal", "open", "half_open", "fault", "disabled"] | None = None
    cooldown_until: int | None = None
    fault_until: int | None = None
    consecutive_failures: int = 0
    status_message: str = ""


class CatalogEntries(BaseModel):
    items: list[CatalogEntry]
    total: int
