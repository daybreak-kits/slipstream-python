from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from l01_core.timeutil import now_ms
from l02_infra.db import Base


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class ApiKeyORM(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "label", name="uq_api_key_user_label"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(64), default="default")
    key_prefix: Mapped[str] = mapped_column(String(16), default="")
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Encrypted plaintext so admins can re-copy the key from the UI.
    key_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class AdminUserORM(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, onupdate=now_ms)


class ChannelORM(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    connection_url: Mapped[str] = mapped_column(Text, default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    protocol: Mapped[str] = mapped_column(String(32), default="openai_chat")
    auth_style: Mapped[str] = mapped_column(String(32), default="bearer")
    protocol_source: Mapped[str] = mapped_column(String(16), default="auto")
    # Default routing priority for every model in this channel. Higher numbers
    # are tried first; 1 is the lowest priority. Only automatic combos (implicit
    # groups) read this column: explicit combo members carry their own override.
    priority: Mapped[int] = mapped_column(Integer, default=1)
    # Legacy compatibility: older databases created this column as
    # "INTEGER NOT NULL" without a server default, so every insert has to
    # write it. The gateway no longer reads it; 60s mirrors the historical
    # upstream timeout. Keep it mapped so old databases stay writable.
    timeout_s: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    rewrite_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    connection_status: Mapped[str] = mapped_column(String(16), default="unknown")
    connection_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_message: Mapped[str] = mapped_column(Text, default="")
    connection_tested_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, onupdate=now_ms)

    # Multiple upstream credentials per channel. ``api_key_enc`` is kept as the
    # legacy/first-key mirror so older code paths and databases stay usable.
    keys: Mapped[list["ChannelKeyORM"]] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ChannelKeyORM.created_at",
    )


class ChannelKeyORM(Base):
    __tablename__ = "channel_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(64), default="")
    key_enc: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, onupdate=now_ms)

    channel: Mapped[ChannelORM] = relationship(back_populates="keys")


class ChannelModelORM(Base):
    __tablename__ = "channel_models"
    __table_args__ = (UniqueConstraint("channel_id", "call_model", name="uq_channel_call_model"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(40), ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    upstream_model: Mapped[str] = mapped_column(String(128))
    call_model: Mapped[str] = mapped_column(String(128), index=True)
    # 模型类型：chat / embedding / rerank / image_generation / audio_tts /
    # audio_transcription / video。探测按类型走不同 endpoint，未识别的类型按 chat 兜底。
    model_type: Mapped[str] = mapped_column(
        String(32), default="chat", server_default="chat"
    )
    # 类型来源：default（默认 chat）/ upstream（上游同步推断）/ manual（人工指定）。
    model_type_source: Mapped[str] = mapped_column(
        String(16), default="default", server_default="default"
    )
    input_price_per_1m: Mapped[float] = mapped_column(Float, default=0)
    output_price_per_1m: Mapped[float] = mapped_column(Float, default=0)
    # NULL = 未配置：缓存读取按 input_price_per_1m 原价计费（阶段 2 才启用写入价）。
    cache_read_price_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_write_price_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Unified model parameters:
    # {"max_input_tokens", "max_output_tokens", "reasoning_level",
    #  "input_types", "output_types"}
    parameters_json: Mapped[str] = mapped_column(
        Text,
        default='{"max_input_tokens":0,"max_output_tokens":0,"reasoning_level":"","input_types":["text"],"output_types":["text"]}',
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Direct-entry timed ban. NULL means no timed ban, so enabled=False is a
    # permanent ban. A timestamp bans the model until that instant; the row
    # stays enabled=False on disk and reads as enabled again once the moment
    # passes (lazy expiry, no scheduler). enabled=True always wins.
    banned_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Format: {"timezone": "Asia/Shanghai", "segments": [{"days", "start", "end"}]}
    schedule_json: Mapped[str] = mapped_column(Text, default='{"segments": []}')
    cooldown_json: Mapped[str] = mapped_column(Text, default="{}")
    health_status: Mapped[str] = mapped_column(String(16), default="normal")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_trip_count: Mapped[int] = mapped_column(Integer, default=0)
    escalation_trip_count: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cooldown_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # When the current half-open probe was claimed; NULL on legacy rows.
    half_open_since: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fault_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    probe_status: Mapped[str] = mapped_column(String(16), default="unknown")
    probe_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_message: Mapped[str] = mapped_column(Text, default="")
    probed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # At most one row per channel is the default probe target (UI radio).
    probe_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, onupdate=now_ms)


class ComboORM(Base):
    __tablename__ = "combos"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failover_rules_json: Mapped[str] = mapped_column(Text, default="[]")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    # ADR-077 per-combo routing override: "" follows the global strategy,
    # 0 follows the global observation window.
    route_strategy: Mapped[str] = mapped_column(String(16), default="")
    route_window_minutes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, onupdate=now_ms)


class ComboTargetORM(Base):
    __tablename__ = "combo_targets"
    __table_args__ = (UniqueConstraint("combo_id", "channel_model_id", "child_combo_id", name="uq_combo_target"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    combo_id: Mapped[str] = mapped_column(String(40), ForeignKey("combos.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(16), default="channel_model")
    channel_model_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("channel_models.id", ondelete="RESTRICT"), nullable=True
    )
    child_combo_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("combos.id", ondelete="RESTRICT"), nullable=True
    )
    # Explicit combo member priority: higher numbers are tried first, 1 is the
    # lowest. Candidate order and failover read only this column; channels
    # .priority never leaks into an explicit combo member.
    priority: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class RequestLogORM(Base):
    __tablename__ = "request_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    upstream_request_id: Mapped[str] = mapped_column(String(128), default="")
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    api_key_label: Mapped[str] = mapped_column(String(64), default="")
    requested_model: Mapped[str] = mapped_column(String(160), index=True)
    resolved_combo_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    final_channel_model_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    channel_name: Mapped[str] = mapped_column(String(128), default="")
    upstream_model: Mapped[str] = mapped_column(String(128), default="")
    response_model: Mapped[str] = mapped_column(String(128), default="")
    is_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    # "chat" for gateway traffic, "probe" for channel/model probes. Probes also
    # land here so the usage log and dashboard count them, but they never bill.
    source: Mapped[str] = mapped_column(String(16), default="chat")
    http_status: Mapped[int] = mapped_column(Integer, default=200)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_ratio: Mapped[float] = mapped_column(Float, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttfa_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttlt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Non-stream responses leave throughput empty: TTFT is only defined for streams.
    throughput_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts_json: Mapped[str] = mapped_column(Text, default="[]")
    rewrite_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    rewrite_skipped: Mapped[str] = mapped_column(String(32), default="")
    rewrite_diff_json: Mapped[str] = mapped_column(Text, default="{}")
    # Safe (non-framing) upstream response headers and a compact sampling-param
    # summary, kept regardless of the body-logging policy so the detail drawer
    # can show them without storing full bodies.
    response_headers_json: Mapped[str] = mapped_column(Text, default="{}")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    # Peer address of the accepted connection, the raw forwarding headers
    # and their flattened addresses. Display-only audit data.
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    request_headers_json: Mapped[str] = mapped_column(Text, default="{}")
    forwarded_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)
    # Row-mode attempt trail (ADR-072). JSON-mode deployments leave it empty;
    # readers consult ATTEMPTS_SOURCE before trusting it, and every write that
    # inserts attempts also flushes the parent so the FK is satisfiable.
    attempts: Mapped[list[AttemptORM]] = relationship(
        back_populates="log",
        order_by="AttemptORM.seq",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AttemptORM(Base):
    """One hop of a request's fail-over trail (ADR-072).

    The object ``attempts_json`` used to carry per entry, split into columns so
    the log filters compile to ordinary ``EXISTS`` equality / LIKE predicates
    instead of ``json_each`` over every candidate row. ``seq`` is the position
    in the original array: ``(log_id, seq)`` is unique, which makes a replayed
    batch insert idempotent.
    """

    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("log_id", "seq", name="uq_attempts_log_seq"),
        Index("ix_attempts_error_code", "error_code"),
        Index("ix_attempts_channel_name", "channel_name"),
        Index("ix_attempts_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    log_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("request_logs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="")
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    # Nullable: a request that failed before routing names no member at all,
    # and old rows may not carry the id even when they name the channel.
    channel_model_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel_slug: Mapped[str] = mapped_column(String(128), default="")
    channel_name: Mapped[str] = mapped_column(String(128), default="")
    upstream_model: Mapped[str] = mapped_column(String(128), default="")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    ms: Mapped[int] = mapped_column(Integer, default=0)
    # The column keeps the payload name (attempt["round"]) even though it
    # shadows the builtin inside this class body; nothing here calls round().
    round: Mapped[int] = mapped_column(Integer, default=1)
    # The wall-clock millisecond the hop started. NULL means "written before
    # the field existed"; readers fall back to the parent row's created_at.
    at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    priority_path_json: Mapped[str] = mapped_column(Text, default="[]")
    request_headers_json: Mapped[str] = mapped_column(Text, default="{}")
    response_headers_json: Mapped[str] = mapped_column(Text, default="{}")

    log: Mapped[RequestLogORM] = relationship(back_populates="attempts")


class RequestLedgerORM(Base):
    """Append-only billing ledger: one insert-only row per request.

    It carries only what a bill can be built from, so the row is narrow and the
    write is cheap. Corrections never update a row; they are appended to
    ledger_adjustments.
    """

    __tablename__ = "request_ledger"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    final_channel_model_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requested_model: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(16), default="chat")
    http_status: Mapped[int] = mapped_column(Integer, default=200)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_ratio: Mapped[float] = mapped_column(Float, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    usage_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    written_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class LedgerAdjustmentORM(Base):
    """Append-only delta against a ledger row (reconcile / manual / estimate fix).

    The effective bill for a request is the ledger row plus the sum of its
    adjustments, so a correction never rewrites history.
    """

    __tablename__ = "ledger_adjustments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    delta_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    delta_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    delta_cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    delta_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str] = mapped_column(String(32), default="reconcile")
    actor: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)


class RequestBodyORM(Base):
    __tablename__ = "request_bodies"

    request_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("request_logs.request_id", ondelete="CASCADE"), primary_key=True
    )
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class AppSettingORM(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms, onupdate=now_ms)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class ModelDefaultORM(Base):
    """Derived defaults for one logical model (ADR-073 D1).

    One row per logical model cluster in models.dev: fields_json holds the
    merged values and doubles as the baseline for the value-compare protection
    (decision 6); providers_json keeps which provider entries were merged and
    at which tier. pulled_at is the snapshot fetch this row was built from.
    """

    __tablename__ = "model_defaults"

    model_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    matched_by: Mapped[str] = mapped_column(String(24), default="")
    providers_json: Mapped[str] = mapped_column(Text, default="[]")
    rule_version: Mapped[str] = mapped_column(String(16), default="")
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    pulled_at: Mapped[int] = mapped_column(BigInteger, default=0)


class DailyAggregateORM(Base):
    __tablename__ = "daily_aggregates"
    __table_args__ = (UniqueConstraint("day_ms", "channel_id", "model_key", name="uq_daily_aggregate"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    day_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_key: Mapped[str] = mapped_column(String(160), default="")
    requests: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    ttft_p95_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_p95_ms: Mapped[int] = mapped_column(Integer, default=0)


Index("ix_request_logs_created_status", RequestLogORM.created_at, RequestLogORM.http_status)

# ADR-072: the one expression index -- "which requests used model X" has to be
# case-insensitive, and a lower() index is the only way to serve it without a
# scan. The other attempt filters are plain equality on four scalar columns.
Index("ix_attempts_upstream_model_nocase", func.lower(AttemptORM.upstream_model))
