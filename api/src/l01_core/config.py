from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_name: str = "Slipstream"
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 60 * 24 * 7
    admin_username: str = "admin"
    admin_password: str = "admin"
    encryption_key: str = ""
    cors_origins: str = "http://localhost:3680,http://127.0.0.1:3680"
    default_timezone: str = "Asia/Shanghai"

    # Transport selection. httpx is the production path (ADR-065): the A/B runs
    # showed 26%-55% less CPU per unit of work. LiteLLM stays selectable when a
    # channel needs its SDK-level normalisation, and the switch is one env var,
    # so a regression is a restart away from being reverted.
    upstream_transport: str = "httpx"
    # LiteLLM's own per-request cost math (cost-map lookup + zero-cost
    # diagnostics). Default "off": Slipstream bills from DB prices
    # (billing.compute_cost) and never reads LiteLLM's response_cost, while the
    # calculation costs several ms of CPU per request. LITELLM_COST_CALC=on
    # restores it.
    litellm_cost_calc: str = "off"
    # Non-streaming upstream total timeout. Streaming requests have no total
    # gate (ADR-075): their lifetime is owned by the client, bounded only by
    # the connect / first-byte / read-idle watchdogs below. Those three
    # watchdogs are overridable per deployment via the admin settings page
    # (app_settings.upstream_timeouts; env values remain the defaults).
    # REQUEST_TIMEOUT_S itself is not overridable.
    request_timeout_s: int = 120
    upstream_connect_timeout_s: float = 10.0
    upstream_read_timeout_s: float = 120.0
    upstream_first_byte_timeout_s: float = 30.0
    upstream_max_connections: int = 200
    upstream_max_keepalive_connections: int = 50
    upstream_keepalive_expiry_s: float = 30.0
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_s: float = 30.0
    db_pool_recycle_s: int = 1800
    # pool_pre_ping costs one round trip per checkout but detects a
    # connection the server closed (PostgreSQL behind a pgbouncer, for
    # example). It was always on; the key only makes it configurable.
    db_pool_pre_ping: bool = True
    # PostgreSQL only: statement timeout in milliseconds (0 = no timeout)
    # and the application_name the server reports in pg_stat_activity.
    db_statement_timeout_ms: int = 0
    db_application_name: str = "slipstream"
    global_retry_max_attempts: int = 12
    # Request-wide deadline for the retries of one request. Invariant 11: it
    # must fit inside request_timeout_s, the ceiling for one non-streaming
    # upstream call (enforced by the validator below). Streaming calls are not
    # bounded by it (ADR-074); the budget still caps total retry time.
    global_retry_budget_s: float = 120.0
    shutdown_grace_s: float = 60.0
    probe_timeout_s: int = 20
    probe_max_tokens: int = 16
    breaker_failure_threshold: int = 5
    breaker_cooldown_s: int = 30
    breaker_rate_limit_cooldown_s: int = 5
    breaker_fault_threshold: int = 3
    breaker_fault_cooldown_s: int = 300
    breaker_escalate: bool = True
    max_attempts: int = 3
    max_body_bytes: int = 2_000_000

    # Stage P0-B (billing ledger). Default "off" keeps the single-write
    # request-log path -- one INSERT with every column -- and skips the ledger
    # entirely: no WAL, no background sink, no read-side overlay. Set
    # LEDGER_SPLIT=on to opt into the split path (billing INSERT + observability
    # queue + ledger WAL/sink), which writes three statements per request
    # instead of one and measurably costs CPU per request.
    ledger_split: str = "off"
    ledger_wal_dir: str = "data/ledger"
    ledger_wal_flush_ms: int = 100
    ledger_sink_batch: int = 200
    ledger_sink_interval_ms: int = 1000

    # Stage ZS-2 (zero-SQL read path): the catalog snapshot is replaced when
    # catalog_version changes, and a full reload is the fallback when the version
    # poll itself fails. Both are background-only; requests never wait.
    catalog_poll_interval_ms: int = 30_000
    catalog_ttl_ms: int = 60_000
    # /v1/models serialization is cached briefly: identical between config
    # writes, and 2s bounds staleness for management changes.
    catalog_directory_ttl_ms: int = 2_000
    # Batch probe-all results are reused for 30s; force=true bypasses.
    probe_result_ttl_ms: int = 30_000
    # Model defaults sync (ADR-073). The snapshot lives on disk; requests never
    # block on the network, and a failed pull keeps the previous snapshot for the
    # rest of the day. last_pull/thresholds live in app_settings (single key).
    models_dev_enabled: bool = True
    models_dev_url: str = "https://models.dev/api.json"
    models_dev_dir: str = "data/models-dev"
    models_dev_timeout_s: float = 60.0
    # 人工别名表 JSON 路径；留空=无别名覆盖（ADR-073 决策 3 第 5 级 alias）
    models_dev_alias_file: str = ""
    # Node-level admission cap fallback (ADR-077 decision 7): the admin
    # settings page (app_settings.route_settings) always wins once a row has
    # been saved; GATEWAY_INFLIGHT_CAP is the deployment-side fallback used
    # before that first save. 0 disables admission control.
    gateway_inflight_cap: int | None = None

    # Stage ZS-1 (zero-SQL write path) + Stage ZS-3 (single switch). Default
    # "off" (= "classic") keeps the current single-write request-log path and
    # per-request SQL reads. USAGE_PIPELINE=memory (alias "on") opts into the
    # zero-SQL pipeline: the request only appends one complete row to an
    # append-only WAL and reads routing/auth/body policy from the in-process
    # catalog snapshot, so the request path runs no statement at all.
    usage_pipeline: str = "classic"
    usage_wal_dir: str = "data/usage"
    usage_wal_flush_ms: int = 100
    usage_sink_batch: int = 200
    usage_sink_interval_ms: int = 1000
    # ADR-072 attempt trail: write side json|rows|both, read side json|attempts.
    # Both default to the pre-rowification behaviour; a deployment moves the
    # trail through the stages by flipping one variable at a time.
    attempts_write: str = "json"
    attempts_source: str = "json"

    @property
    def zero_sql_pipeline(self) -> bool:
        """Stage ZS-3 single switch: classic/off = SQL reads + single write."""
        value = self.usage_pipeline.strip().lower()
        if value in ("", "off", "classic", "false", "0", "no"):
            return False
        if value in ("on", "memory", "true", "1", "yes"):
            return True
        raise ValueError(
            f"invalid USAGE_PIPELINE {self.usage_pipeline!r}: use classic|memory"
        )

    @property
    def attempts_write_mode(self) -> str:
        """``json`` (default) | ``rows`` | ``both`` for the attempt trail (ADR-072)."""
        value = self.attempts_write.strip().lower()
        if value in ("", "json", "off", "old", "false", "0", "no"):
            return "json"
        if value in ("rows", "row", "new", "true", "1", "yes"):
            return "rows"
        if value in ("both", "dual", "double"):
            return "both"
        raise ValueError(
            f"invalid ATTEMPTS_WRITE {self.attempts_write!r}: use json|rows|both"
        )

    @property
    def attempts_read_mode(self) -> str:
        """``json`` (default) | ``attempts`` for every attempt-level read (ADR-072)."""
        value = self.attempts_source.strip().lower()
        if value in ("", "json", "off", "old", "false", "0", "no"):
            return "json"
        if value in ("attempts", "rows", "row", "new", "true", "1", "yes"):
            return "attempts"
        raise ValueError(
            f"invalid ATTEMPTS_SOURCE {self.attempts_source!r}: use json|attempts"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @model_validator(mode="after")
    def _check_attempt_switches(self) -> "Settings":
        """Reject an unknown ADR-072 switch while the settings are built.

        The two properties already raise on an unknown value, but they are read
        lazily: without this check a typo in ATTEMPTS_WRITE would surface inside
        the first request that writes a log (a 500 from the repository) instead
        of stopping the process at boot. Touching both properties here makes the
        failure a startup error, which is where a deployment typo belongs.
        """
        self.attempts_write_mode
        self.attempts_read_mode
        return self

    @model_validator(mode="after")
    def _check_retry_budget(self) -> "Settings":
        """Reject a retry budget that outlives the request timeout."""
        if self.global_retry_budget_s > self.request_timeout_s:
            raise ValueError(
                f"GLOBAL_RETRY_BUDGET_S ({self.global_retry_budget_s}) must not exceed "
                f"REQUEST_TIMEOUT_S ({self.request_timeout_s}): the budget is the "
                "request-wide deadline and has to fit inside the request timeout"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
