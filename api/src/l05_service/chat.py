from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core.config import get_settings
from l01_core.errors import ErrorCode, OpenAIError
from l01_core.logging import get_logger, redact_headers
from l01_core.timeutil import now_ms
from l01_core.request_meta import parse_forwarded
from l03_domain.failover import choose_action
from l03_domain.model_types import MODEL_TYPE_CHAT
from l04_repo import orm, repo, usage_repo
from l05_service import (
    billing,
    body_logging,
    channels,
    ledger,
    log_pipeline,
    routing,
    usage_sink,
)
from l05_service import route_stats
from l05_service import upstream_settings as upstream_settings_service
from l05_service.app_settings import BodyLogSettings
from l05_service.body_logging import (
    BodySnapshot,
    cached_body_settings,
    build_body_record,
    empty_stream_summary,
    extract_reasoning_content,
    stream_summary_payload,
    strip_reasoning_content,
)
from l05_service.llm import (
    LLMError,
    TIER_PERMANENT,
    UpstreamStream,
    Usage,
    build_request_body,
    complete,
    content_text,
    open_stream,
)
from l05_service.rewrite import (
    PreparedRequest,
    PreparedResponse,
    prepare_request,
    prepare_response,
    rewrite_log,
)
from l05_service.channel_models import (
    SKIP_COOLDOWN_WINDOW,
    SKIP_MODEL_BANNED,
    SKIP_MODEL_COOLDOWN,
)
from l05_service.routing import (
    CallTarget,
    Candidate,
    admit_candidate,
    apply_affinity,
    cooldown_recovery_at,
    forget_affinity,
    model_cooldown_error,
    next_available_at,
    order_candidates,
    record_failure,
    record_success,
    remember_affinity,
    resolve_target,
    scheduled_offline_error,
    session_key_from_headers,
)

# Nginx-style "client closed request" status; the log keeps the stopped state.
CLIENT_CLOSED_STATUS = 499
STREAM_STOPPED = "STREAM_STOPPED"

# The routing layer returns the same channel/model pair in a Candidate.
DirectTarget = Candidate

logger = get_logger("system.chat")


def _is_chat_model(candidate: Candidate) -> bool:
    """Only chat-typed models may serve /chat/completions."""
    value = getattr(candidate.model, "model_type", None)
    return str(value or MODEL_TYPE_CHAT).strip().lower() == MODEL_TYPE_CHAT


def _chat_candidates(candidates: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
    return tuple(candidate for candidate in candidates if _is_chat_model(candidate))


def _model_type_conflict(target: CallTarget) -> OpenAIError:
    """Every candidate is a non-chat model: say so instead of failing upstream.

    Forwarding a chat request to an embedding endpoint would burn a fault and
    cool the channel down for requests that can never succeed.
    """
    types = sorted(
        {
            str(getattr(candidate.model, "model_type", None) or MODEL_TYPE_CHAT)
            .strip()
            .lower()
            for candidate in target.candidates
        }
    )
    return OpenAIError(
        400,
        f"模型 {target.name} 的类型是 {'/'.join(types)}，不支持 /chat/completions",
        ErrorCode.NOT_IMPLEMENTED,
        "invalid_request_error",
    )


@dataclass(slots=True)
class ChatCompletion:
    """A non-stream response body plus the headers safe to relay to the client."""

    payload: dict[str, Any]
    headers: dict[str, str]


def _error_type(status: int) -> str:
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "server_error"
    return "invalid_request_error"


def map_error(error: LLMError) -> tuple[int, str, str]:
    """Return ``(http_status, code, message)`` for an upstream failure."""
    if error.tier == TIER_PERMANENT:
        # A credential the upstream rejects, an exhausted quota and a model the
        # key cannot reach are not the client's fault and will not fix
        # themselves on retry; name them instead of hiding them behind a
        # generic upstream error.
        if error.kind == "auth":
            return 502, ErrorCode.UPSTREAM_AUTH_FAILED, error.message
        if error.kind == "rate_limit":
            return 503, ErrorCode.UPSTREAM_QUOTA_EXHAUSTED, error.message
        if error.kind == "not_found":
            return 404, ErrorCode.MODEL_NOT_FOUND, error.message
    if error.kind == "timeout":
        return 504, ErrorCode.UPSTREAM_TIMEOUT, error.message
    if error.kind == "rate_limit":
        return 429, ErrorCode.UPSTREAM_RATE_LIMITED, error.message
    if error.kind == "empty":
        return 502, ErrorCode.EMPTY_RESPONSE, error.message
    if error.kind == "bad_response":
        # The upstream answered, but the body is not a usable chat completion
        # (an HTML error page from a reverse proxy, a truncated JSON, ...).
        return 502, ErrorCode.BAD_UPSTREAM_RESPONSE, error.message
    if error.kind == "bad_request":
        return 400, ErrorCode.INVALID_REQUEST, error.message
    if error.kind == "not_found":
        return 404, ErrorCode.MODEL_NOT_FOUND, error.message
    if error.kind == "stream_abort":
        return 502, ErrorCode.STREAM_ABORTED, error.message
    return 502, ErrorCode.UPSTREAM_ERROR, error.message


def error_details(error: LLMError) -> dict[str, Any]:
    """Machine-readable extras for the error envelope (Retry-After seconds)."""
    if not error.retry_after_ms:
        return {}
    return {"retry_after_s": max(1, int(round(error.retry_after_ms / 1000.0)))}


def _attempt(
    target: Candidate | None,
    status: str,
    *,
    error_code: str | None = None,
    message: str = "",
    ms: int = 0,
    round_index: int = 1,
    skipped: bool = False,
    request_headers: dict[str, str] | None = None,
    response_headers: dict[str, str] | None = None,
    at: int | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    """One hop of the attempt timeline.

    ``at`` is the wall-clock millisecond the hop started and ``http_status``
    is the status the upstream actually answered with. Both stay optional on
    purpose: a skipped candidate never reached the upstream, a connection
    failure has no HTTP status at all, and rows written before these fields
    existed carry neither -- readers fall back to the request row
    (see log_views.attempt_status).
    """
    attempt = {
        "channel_model_id": target.model.id if target else None,
        "channel_slug": target.channel.slug if target else "",
        "channel_name": target.channel.name if target else "",
        "upstream_model": target.model.upstream_model if target else "",
        "status": status,
        "error_code": error_code,
        "message": message[:2000],
        "ms": ms,
        "round": round_index,
        "skipped": skipped,
        "priority_path": list(target.priority_path or (target.priority,)) if target else [],
    }
    if at is not None:
        attempt["at"] = int(at)
    if http_status is not None:
        attempt["http_status"] = int(http_status)
    # Attempt-level headers make a failed hop diagnosable from the log detail;
    # credentials are redacted before anything reaches the database.
    if request_headers:
        attempt["request_headers"] = _log_headers(request_headers)
    if response_headers:
        attempt["response_headers"] = _log_headers(response_headers)
    return attempt


def _stream_upstream_status(stream: UpstreamStream) -> int | None:
    """The status a primed stream was opened with, when it is still attached."""
    status = getattr(getattr(stream, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


# ADR-070 D3 wording: ban is permanent, cooldown/broken-circuit recover on their own.
_SKIP_MESSAGES = {
    "MEMBER_DISABLED": "组合成员已封禁",
    "CHANNEL_DISABLED": "渠道已封禁",
    SKIP_MODEL_BANNED: "渠道模型已封禁",
    SKIP_MODEL_COOLDOWN: "渠道模型处于冷却中",
    "PROTOCOL_UNSUPPORTED": "渠道协议不是 OpenAI 兼容协议",
    "CIRCUIT_OPEN": "渠道模型处于冷却中",
    "CIRCUIT_FAULT": "渠道模型处于冷却升级期",
    SKIP_COOLDOWN_WINDOW: "渠道模型处于排期冷却窗口",
}


class FailoverFailed(Exception):
    """Every attempted candidate failed (or a stop rule ended the request)."""

    def __init__(self, error: LLMError, target: Candidate | None) -> None:
        super().__init__(str(error))
        self.error = error
        self.target = target


class _RetryBudget:
    """A single request-wide cap on attempts and elapsed retry time.

    ``max_attempts`` on a combo remains the round count; this budget prevents
    several rounds times several candidates from multiplying into an unbounded
    number of upstream calls.
    """

    def __init__(self, max_attempts: int, budget_s: float) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.deadline = time.perf_counter() + max(0.1, float(budget_s))
        self.used = 0

    def consume(self) -> bool:
        if self.used >= self.max_attempts:
            return False
        if time.perf_counter() >= self.deadline:
            return False
        self.used += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_attempts - self.used)

    @property
    def remaining_s(self) -> float:
        """Seconds left before the request-wide deadline."""
        return max(0.0, self.deadline - time.perf_counter())

    def refund(self) -> None:
        """Give one attempt back: a permanent failure is not the request's fault."""
        if self.used > 0:
            self.used -= 1


class _RoundAttempts:
    """Run a top-level target's rounds while recording the attempts timeline."""

    def __init__(
        self,
        session: AsyncSession,
        target: CallTarget,
        *,
        attempts: list[dict[str, Any]],
        is_stream: bool,
        log_success: bool = True,
        session_key: str | None = None,
    ) -> None:
        self.session = session
        self.target = target
        self.attempts = attempts
        self.is_stream = is_stream
        self.log_success = log_success
        # Affinity hint for this conversation; only reorders same-priority
        # candidates, never the layers themselves.
        self.session_key = session_key or ""
        self._skipped: set[str] = set()
        # A request may switch away from a permanently failing candidate at most
        # once: two permanent failures already indicate a site-wide problem.
        self.max_permanent_switches = 1
        settings_obj = get_settings()
        self.budget = _RetryBudget(
            int(getattr(settings_obj, "global_retry_max_attempts", 12)),
            float(getattr(settings_obj, "global_retry_budget_s", 120.0)),
        )
        # Upper bound for a single upstream call. The attempt may not outlive
        # the request-wide deadline the budget enforces.
        self.timeout_s = float(getattr(settings_obj, "request_timeout_s", 120.0))

    async def _record_skips(
        self, round_index: int, candidates: tuple[Candidate, ...]
    ) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
        available: list[Candidate] = []
        skipped: list[tuple[Candidate, str]] = []
        # ADR-077: same strategy ordering as the initial resolution, refreshed
        # per round so a member that went unstable mid-request sinks in the
        # retry order. Layers stay absolute. Session-less loops (probes, unit
        # tests) keep the default layer policy.
        if self.session is not None:
            policy = await route_stats.resolve_policy(self.session)
        else:
            policy = route_stats.RouteOrderPolicy()
        for candidate in order_candidates(candidates, policy=policy):
            # Admission also promotes an expired cooldown and claims the single
            # half-open probe slot, so it has to run through the database.
            reason = await admit_candidate(self.session, candidate)
            if reason is None:
                available.append(candidate)
                continue
            skipped.append((candidate, reason))
            if candidate.model.id not in self._skipped:
                self._skipped.add(candidate.model.id)
                self.attempts.append(
                    _attempt(
                        candidate,
                        "skipped",
                        error_code=reason,
                        message=_SKIP_MESSAGES.get(reason, ""),
                        round_index=round_index,
                        skipped=True,
                        # A skip never called the upstream, so it has no status;
                        # its timestamp is the moment it was rejected in the queue.
                        at=now_ms(),
                    )
                )
        return apply_affinity(available, self.session_key), skipped

    async def run(
        self, invoke: Callable[[Candidate, dict[str, Any]], Awaitable[Any]]
    ) -> tuple[Candidate, int, Any]:
        last_error: LLMError | None = None
        last_target: Candidate | None = None
        rounds = max(1, int(self.target.max_attempts or 1))
        # Permanent failures mean a dead credential or an exhausted account.
        # One switch is worth trying (another channel may use another key);
        # two in a row mean a site-wide incident, so the request fails fast
        # instead of walking every candidate and logging in nothing.
        permanent_failures = 0
        # A target may mix model types (a combo, an implicit group); a chat
        # request can only ever be served by the chat-typed members.
        candidates = _chat_candidates(self.target.candidates)
        if not candidates:
            raise _model_type_conflict(self.target)

        for round_index in range(1, rounds + 1):
            available, skipped = await self._record_skips(round_index, candidates)
            if not available:
                if last_error is not None:
                    # A previous round already produced a real upstream failure;
                    # reporting "no channel available" would hide it (and, for a
                    # parked candidate, invite a retry that changes nothing).
                    raise FailoverFailed(last_error, last_target)
                # A round with no callable member ends the request immediately;
                # skipped members never consume a round. When every candidate is
                # parked for the same time-driven reason, answer with that reason
                # plus the moment it clears instead of a generic capacity error.
                reasons = {reason for _candidate, reason in skipped}
                if reasons == {SKIP_COOLDOWN_WINDOW}:
                    raise scheduled_offline_error(
                        self.target.name,
                        next_available_at(
                            (candidate for candidate, _reason in skipped),
                            routing.now_ms(),
                        ),
                    )
                if reasons == {SKIP_MODEL_COOLDOWN}:
                    raise model_cooldown_error(
                        self.target.name,
                        cooldown_recovery_at(
                            candidate.model for candidate, _reason in skipped
                        ),
                    )
                raise OpenAIError(
                    503,
                    f"模型 {self.target.name} 当前没有可用渠道",
                    ErrorCode.NO_AVAILABLE_CHANNEL,
                    "server_error",
                )
            for candidate in available:
                if not self.budget.consume():
                    if last_error is not None:
                        raise FailoverFailed(last_error, last_target)
                    raise OpenAIError(
                        503,
                        "重试预算已耗尽，请稍后再试",
                        ErrorCode.SERVICE_UNAVAILABLE,
                        "server_error",
                    )
                last_target = candidate
                # Filled by ``invoke`` with the headers this hop sent and
                # received, so both success and failure can log them.
                captured: dict[str, Any] = {}
                # Invariant 11: the retry budget fits inside request_timeout_s,
                # so an attempt may not outlive what is left of it -- otherwise
                # one slow hop plus one retry could exceed the request timeout.
                captured["timeout_s"] = max(
                    1.0, min(self.timeout_s, self.budget.remaining_s)
                )
                attempt_started = time.perf_counter()
                attempt_started_ms = now_ms()
                try:
                    value = await invoke(candidate, captured)
                except LLMError as error:
                    elapsed = int((time.perf_counter() - attempt_started) * 1000)
                    self.attempts.append(
                        _attempt(
                            candidate,
                            "error",
                            error_code=error.kind,
                            message=error.message,
                            ms=elapsed,
                            round_index=round_index,
                            request_headers=captured.get("request"),
                            response_headers=(
                                captured.get("response") or error.headers
                            ),
                            at=attempt_started_ms,
                            http_status=error.status_code,
                        )
                    )
                    permanent = error.tier == TIER_PERMANENT
                    park_until_ms = (
                        _quota_park_until_ms()
                        if permanent and error.status_code == 429
                        else None
                    )
                    await record_failure(
                        self.session,
                        candidate,
                        rate_limited=error.status_code == 429
                        or error.kind == "rate_limit",
                        permanent=permanent,
                        retry_after_ms=error.retry_after_ms,
                        park_until_ms=park_until_ms,
                    )
                    # This channel just failed, so drop it from this session's
                    # memory: the next request must pick again rather than
                    # retrying a broken favourite first.
                    forget_affinity(self.session_key, candidate.model.id)
                    if permanent:
                        # A dead key or an exhausted account is the channel's
                        # problem, not the request's: rotate without spending
                        # the request's retry budget.
                        self.budget.refund()
                        permanent_failures += 1
                        if permanent_failures >= self.max_permanent_switches + 1:
                            # Stop enumerating, and do not cool down candidates
                            # that were never tried: the operator needs to see
                            # this as one incident, not as a global outage.
                            raise FailoverFailed(error, candidate) from error
                    last_error = error
                    action = choose_action(
                        status_code=error.status_code,
                        retryable=error.retryable,
                        body=error.raw or error.message,
                        rules=self.target.failover_rules,
                        is_stream=self.is_stream,
                        tier=error.tier,
                    )
                    if action == "stop":
                        raise FailoverFailed(error, candidate) from error
                    continue

                elapsed = int((time.perf_counter() - attempt_started) * 1000)
                if self.log_success:
                    self.attempts.append(
                        _attempt(
                            candidate,
                            "success",
                            ms=elapsed,
                            round_index=round_index,
                            request_headers=captured.get("request"),
                            response_headers=captured.get("response"),
                            at=attempt_started_ms,
                            http_status=captured.get("status_code"),
                        )
                    )
                    # Only a request that ran to completion may clear the
                    # channel's failure streak. A streaming request only
                    # primes the upstream connection here, so it reports the
                    # real outcome later, in ``_finalize_impl_inner``.
                    await record_success(self.session, candidate)
                    # Remember the winner so the next request from the same
                    # conversation prefers this channel model (and its cache).
                    remember_affinity(self.session_key, candidate.model.id)
                return candidate, round_index, value

        assert last_error is not None
        raise FailoverFailed(last_error, last_target)


def _resolved_combo_id(target: Any) -> str | None:
    """The explicit combo row the request resolved to, if any.

    Only a manual combo has an entity row (``CallTarget.combo``); implicit
    groups are derived at query time, so their rows keep the column null and
    the analytics axis falls back to the requested name. Direct calls carry no
    combo at all.
    """
    combo = getattr(target, "combo", None)
    return str(getattr(combo, "id", "") or "") or None


async def _write_log(
    session: AsyncSession,
    *,
    request_id: str,
    requested_model: str,
    api_key: orm.ApiKeyORM,
    user: orm.UserORM,
    target: DirectTarget | None,
    combo_id: str | None = None,
    http_status: int,
    usage: Usage,
    total_ms: int,
    attempts: list[dict[str, Any]],
    response_model: str = "",
    error_code: str | None = None,
    error_message: str = "",
    is_stream: bool = False,
    ttft_ms: int | None = None,
    ttfa_ms: int | None = None,
    ttlt_ms: int | None = None,
    throughput_tps: float | None = None,
    usage_estimated: bool = False,
    rewrite_applied: bool = False,
    rewrite_skipped: str = "",
    rewrite_diff: str = "{}",
    upstream_request_id: str = "",
    response_headers: dict[str, str] | None = None,
    client_ip: str = "",
    request_headers: dict[str, str] | None = None,
    body_snapshot: BodySnapshot | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    # Billing columns: what a bill, an export or a reconciliation can be built
    # from. They go into the request's own transaction and never wait on a task.
    billing_values: dict[str, Any] = {
        "request_id": request_id,
        "upstream_request_id": upstream_request_id[:128],
        "user_id": user.id,
        "api_key_id": api_key.id,
        "requested_model": requested_model,
        "final_channel_model_id": target.model.id if target else None,
        # Manual combo id for the analytics axes (GLOSSARY §5.2): null for
        # direct calls and implicit groups, whose axis key falls back to name.
        "resolved_combo_id": combo_id,
        "http_status": http_status,
        "error_code": error_code,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cached_tokens": usage.cached_tokens,
        "cache_ratio": billing.cache_ratio(usage),
        "cost_usd": billing.compute_cost(target.model if target else None, usage),
        "usage_estimated": usage_estimated,
        "created_at": now_ms(),
    }
    # Diagnostics columns: nine tenths of the row width and all three json.dumps.
    # A lost UPDATE costs this request's attempt trail, never its money.
    observability_values: dict[str, Any] = {
        "api_key_label": api_key.label,
        "channel_name": target.channel.name if target else "",
        "upstream_model": target.model.upstream_model if target else "",
        "response_model": response_model,
        "is_stream": is_stream,
        "error_message": error_message[:2000],
        "ttft_ms": ttft_ms,
        "ttfa_ms": ttfa_ms,
        "ttlt_ms": ttlt_ms,
        "total_ms": total_ms,
        "throughput_tps": throughput_tps,
        "attempts_json": json.dumps(attempts, ensure_ascii=False),
        "rewrite_applied": rewrite_applied,
        "rewrite_skipped": rewrite_skipped,
        "rewrite_diff_json": rewrite_diff,
        "response_headers_json": json.dumps(_log_headers(response_headers), ensure_ascii=False),
        "params_json": json.dumps(
            _params_summary(body_snapshot.request_body if body_snapshot else None),
            ensure_ascii=False,
            default=str,
        ),
    }
    if usage_sink.usage_sink.enabled():
        # ZS-1: the request only appends one complete row to the WAL; the
        # background sink batches it into request_logs. No statement runs here
        # and a broken sink can never fail the request.
        # Body rows are rendered (redacted + bounded) BEFORE the WAL append:
        # the raw snapshot can hold the full prompt twice, and dumping it
        # verbatim used to dominate the per-request CPU on large bodies.
        # The sink still replays capture policy (should_capture) downstream;
        # only the serialization cost moved off the unbounded raw objects.
        memory_body: dict[str, str | None] | None = None
        if body_snapshot is not None:
            body_policy = (
                await cached_body_settings(session_factory)
                if session_factory is not None
                else None
            ) or BodyLogSettings()
            memory_body = build_body_record(body_snapshot, body_policy)
        usage_sink.usage_sink.record(
            usage_repo.build_log_row(
                billing=billing_values,
                observability=observability_values,
                client_ip=client_ip,
                request_headers=request_headers,
            ),
            body=memory_body,
        )
        if log_pipeline.split_enabled():
            # LEDGER_SPLIT=on keeps billing the ledger: the charge appends to
            # its own WAL (still no statement), so turning on the usage
            # pipeline must not silently stop the request_ledger rows.
            try:
                await ledger.ledger_service.charge(
                    ledger.ledger_row(
                        request_id=request_id,
                        user_id=user.id,
                        api_key_id=api_key.id,
                        final_channel_model_id=billing_values["final_channel_model_id"],
                        requested_model=requested_model,
                        source="chat",
                        http_status=http_status,
                        error_code=error_code,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        cached_tokens=usage.cached_tokens,
                        cache_ratio=billing_values["cache_ratio"],
                        cost_usd=billing_values["cost_usd"],
                        usage_estimated=usage_estimated,
                        created_at=billing_values["created_at"],
                    )
                )
            except Exception:  # noqa: BLE001 - logging must not break the reply
                logger.warning("账本记账失败 request_id=%s", request_id, exc_info=True)
        return
    logged = False
    try:
        if log_pipeline.split_enabled():
            log = await repo.add_request_log(session, **billing_values)
            log_pipeline.submit_observability(
                log.id, billing_values, observability_values
            )
        else:
            # LEDGER_SPLIT=off: the pre-P0-B single write, kept as the rollback.
            await repo.add_request_log(
                session, **billing_values, **observability_values
            )
        logged = True
    except Exception:
        await session.rollback()
    if not logged:
        # request_bodies has a foreign key to request_logs; without the parent
        # row the body would be orphaned (or the write would fail anyway).
        return
    if log_pipeline.split_enabled():
        # Billing ledger: append-only and WAL-durable, idempotent on request_id.
        # A ledger hiccup must never fail the request or lose the log row.
        try:
            await ledger.ledger_service.charge(
                ledger.ledger_row(
                    request_id=request_id,
                    user_id=user.id,
                    api_key_id=api_key.id,
                    final_channel_model_id=billing_values["final_channel_model_id"],
                    requested_model=requested_model,
                    source="chat",
                    http_status=http_status,
                    error_code=error_code,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cached_tokens=usage.cached_tokens,
                    cache_ratio=billing_values["cache_ratio"],
                    cost_usd=billing_values["cost_usd"],
                    usage_estimated=usage_estimated,
                    created_at=billing_values["created_at"],
                )
            )
        except Exception:  # noqa: BLE001 - logging must not break the reply
            logger.warning("账本记账失败 request_id=%s", request_id, exc_info=True)
    # Best-effort and fully async: a body write can never fail the request.
    body_logging.schedule_body_write(
        session_factory,
        request_id,
        body_snapshot,
        http_status=http_status,
        error_code=error_code,
    )


def _validate_body(body: dict[str, Any]) -> None:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise OpenAIError(
            400,
            "messages 必须是非空数组",
            ErrorCode.INVALID_REQUEST,
            "invalid_request_error",
            param="messages",
        )


# Values that describe how a request was sampled. They are stored separately
# from body logging so the detail drawer always has a compact parameter summary.
_PARAM_KEYS = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "n",
    "stop",
    "response_format",
)


def _params_summary(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in _PARAM_KEYS:
        if key in body and body[key] is not None:
            summary[key] = body[key]
    messages = body.get("messages")
    if isinstance(messages, list):
        summary["message_count"] = len(messages)
    tools = body.get("tools")
    if isinstance(tools, list):
        summary["tool_count"] = len(tools)
    summary["stream"] = bool(body.get("stream"))
    return summary


def _log_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Redact credentials and keep header values small before persisting."""
    if not headers:
        return {}
    redacted = redact_headers(dict(headers))
    return {
        str(name): str(value)[:1024]
        for name, value in redacted.items()
        if value is not None
    }


def _messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    return messages if isinstance(messages, list) else []


def _completion_text(payload: dict[str, Any]) -> str:
    """Plain text of the first choice's message in a non-stream response."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return ""
    return content_text(message.get("content"))


async def complete_direct(
    session: AsyncSession,
    *,
    api_key: orm.ApiKeyORM,
    user: orm.UserORM,
    body: dict[str, Any],
    request_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    client_ip: str = "",
    request_headers: dict[str, str] | None = None,
) -> ChatCompletion:
    """Run one non-stream request over rounds of top-level candidates.

    A round tries every currently available member once. Only a fully failed
    round counts as one attempt, so ``max_attempts`` is a round count rather
    than a candidate count.
    """
    settings = get_settings()
    started = time.perf_counter()
    requested = str(body.get("model") or "").strip()
    target: CallTarget | None = None
    attempts: list[dict[str, Any]] = []
    usage = Usage()
    body_snapshot = BodySnapshot(request_body=body)

    try:
        _validate_body(body)
        if body.get("stream"):
            raise OpenAIError(
                400,
                "流式请求请使用 SSE 入口",
                ErrorCode.NOT_IMPLEMENTED,
                "invalid_request_error",
            )
        target = await resolve_target(session, requested)
        upstream_timeouts = await upstream_settings_service.get_upstream_timeouts(session)

        async def invoke(candidate: Candidate, captured: dict[str, Any]):
            with channels.key_session_scope(
                session_key_from_headers(request_headers)
            ):
                prepared = prepare_request(
                    candidate.channel,
                    body,
                    upstream_model=candidate.model.upstream_model,
                    requested_model=requested,
                    request_id=request_id,
                    client_headers=request_headers,
                )
            body_snapshot.upstream_body = build_request_body(
                candidate.channel, candidate.model.upstream_model, prepared.body
            )
            captured["request"] = dict(prepared.headers)
            try:
                payload, usage, _latency, upstream_headers = await complete(
                    candidate.channel,
                    candidate.model.upstream_model,
                    body,
                    timeout_s=int(captured.get("timeout_s") or settings.request_timeout_s),
                    request_id=request_id,
                    prepared=prepared,
                    upstream_timeouts=upstream_timeouts,
                )
                captured["response"] = dict(upstream_headers)
                # complete() raises for any status >= 400 and for a 2xx whose body
                # is not a chat completion, so reaching this line means the upstream
                # answered 2xx -- and chat completions only define 200.
                captured["status_code"] = 200
            except LLMError as error:
                if error.raw:
                    body_snapshot.response_body = error.raw
                raise
            # A non-stream upstream may omit the usage object entirely; the
            # httpx arm then reports all zeros, which would be booked as a real
            # measurement (undercharged tokens, cache ratio pinned to 0).
            # Estimate from the text we actually relayed and let the ledger
            # carry the flag. Streams already do this in _usage().
            if usage.total_tokens == 0:
                usage = billing.estimate_usage(
                    _messages(body), _completion_text(payload)
                )
                usage_estimated = True
            else:
                usage_estimated = False
            body_snapshot.response_body = strip_reasoning_content(payload)
            body_snapshot.reasoning_content = extract_reasoning_content(payload)
            upstream_response_model = str(payload.get("model") or "")
            response = prepare_response(
                candidate.channel,
                payload,
                upstream_headers,
                upstream_model=candidate.model.upstream_model,
                requested_model=requested,
                is_stream=False,
            )
            upstream_request_id = str(payload.get("id") or "")
            return payload, usage, response, prepared, upstream_response_model, upstream_request_id, usage_estimated

        winning, _round, result = await _RoundAttempts(
            session,
            target,
            attempts=attempts,
            is_stream=False,
            session_key=session_key_from_headers(request_headers),
        ).run(invoke)
        (
            payload,
            usage,
            prepared_response,
            prepared,
            response_model,
            upstream_request_id,
            usage_estimated,
        ) = result
    except OpenAIError as error:
        total_ms = int((time.perf_counter() - started) * 1000)
        await _write_log(
            session,
            request_id=request_id,
            requested_model=requested,
            api_key=api_key,
            user=user,
            target=None,
            combo_id=_resolved_combo_id(target),
            http_status=error.status,
            usage=usage,
            total_ms=total_ms,
            attempts=attempts,
            error_code=error.code,
            error_message=error.message,
            client_ip=client_ip,
            request_headers=request_headers,
            body_snapshot=body_snapshot,
            session_factory=session_factory,
        )
        raise
    except FailoverFailed as failure:
        total_ms = int((time.perf_counter() - started) * 1000)
        status, code, message = map_error(failure.error)
        await _write_log(
            session,
            request_id=request_id,
            requested_model=requested,
            api_key=api_key,
            user=user,
            target=failure.target,
            combo_id=_resolved_combo_id(target),
            http_status=status,
            usage=usage,
            total_ms=total_ms,
            attempts=attempts,
            error_code=code,
            error_message=message,
            client_ip=client_ip,
            request_headers=request_headers,
            body_snapshot=body_snapshot,
            session_factory=session_factory,
        )
        raise OpenAIError(
            status,
            message,
            code,
            _error_type(status),
            details=error_details(failure.error),
        ) from failure.error
    except Exception as error:  # noqa: BLE001
        total_ms = int((time.perf_counter() - started) * 1000)
        await _write_log(
            session,
            request_id=request_id,
            requested_model=requested,
            api_key=api_key,
            user=user,
            target=None,
            combo_id=_resolved_combo_id(target),
            http_status=500,
            usage=usage,
            total_ms=total_ms,
            attempts=attempts,
            error_code=ErrorCode.INTERNAL,
            error_message=str(error),
            client_ip=client_ip,
            request_headers=request_headers,
            body_snapshot=body_snapshot,
            session_factory=session_factory,
        )
        raise OpenAIError(
            500, "网关内部错误", ErrorCode.INTERNAL, "server_error"
        ) from error

    payload = prepared_response.body
    payload["model"] = requested
    applied, skipped, rewrite_diff = rewrite_log(prepared, prepared_response)
    total_ms = int((time.perf_counter() - started) * 1000)
    await _write_log(
        session,
        request_id=request_id,
        requested_model=requested,
        api_key=api_key,
        user=user,
        target=winning,
        combo_id=_resolved_combo_id(target),
        http_status=200,
        usage=usage,
        total_ms=total_ms,
        attempts=attempts,
        response_model=response_model,
        upstream_request_id=upstream_request_id,
        response_headers=prepared_response.headers,
        client_ip=client_ip,
        request_headers=request_headers,
        usage_estimated=usage_estimated,
        rewrite_applied=applied,
        rewrite_skipped=skipped,
        rewrite_diff=rewrite_diff,
        body_snapshot=body_snapshot,
        session_factory=session_factory,
    )
    return ChatCompletion(payload=payload, headers=prepared_response.headers)


def _sse_error(error: LLMError, request_id: str) -> str:
    status, code, message = map_error(error)
    payload = {
        "error": {
            "message": message,
            "type": _error_type(status),
            "code": code,
            "request_id": request_id,
        }
    }
    return "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _quota_park_until_ms() -> int:
    """Quota exhaustion parks the channel until the next local midnight."""
    t = time.localtime()
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    return int(midnight * 1000)


class StreamRun:
    """A primed upstream stream plus the SSE relay and its final request log.

    The upstream is only sent to the client after ``prime()`` succeeded, so a
    pre-first-byte failure still produces a normal JSON error response. Once
    relaying starts, failures are reported as an SSE ``error`` event and the
    stream is never patched with ``[DONE]``.
    """

    def __init__(
        self,
        *,
        stream: UpstreamStream,
        target: DirectTarget,
        session_factory: async_sessionmaker[AsyncSession],
        request_id: str,
        requested_model: str,
        api_key_id: str,
        api_key_label: str,
        user_id: str,
        messages: list[dict[str, Any]],
        started: float,
        started_ms: int | None = None,
        client_body: dict[str, Any] | None = None,
        upstream_body: dict[str, Any] | None = None,
        attempts: list[dict[str, Any]] | None = None,
        round_index: int = 1,
        rewrite: PreparedRequest | None = None,
        response: PreparedResponse | None = None,
        client_ip: str = "",
        request_headers: dict[str, str] | None = None,
        session_key: str | None = None,
    ) -> None:
        self.stream = stream
        self.target = target
        self.session_key = session_key or ""
        # Failed/skipped pre-first-byte attempts that happened before this
        # candidate won.
        self.attempts = list(attempts or [])
        self.round_index = round_index
        self.rewrite = rewrite
        self.response = response
        self.client_ip = client_ip
        self.request_headers = dict(request_headers) if request_headers else None
        self.session_factory = session_factory
        self.request_id = request_id
        self.requested_model = requested_model
        self.api_key_id = api_key_id
        self.api_key_label = api_key_label
        self.user_id = user_id
        self.messages = messages
        self.client_body = client_body
        self.upstream_body = upstream_body
        self.started = started
        # Wall clock of the same moment (``started`` is monotonic), so the hop
        # can be placed on the request timeline.
        self.started_ms = started_ms
        self.completed = False
        self.cancelled = False
        self.error: LLMError | None = None
        self.finished_at: float | None = None
        self._finalize_task: asyncio.Task[None] | None = None
        # Assigned by the HTTP layer after the stream is admitted. Kept as a
        # plain attribute to avoid coupling the service to FastAPI.
        self.registry = None
        self._released = False

    @property
    def ttft_ms(self) -> int | None:
        if self.stream.first_content_at is None:
            return None
        return max(0, int((self.stream.first_content_at - self.started) * 1000))

    @property
    def ttfa_ms(self) -> int | None:
        if self.stream.first_answer_at is None:
            return None
        return max(0, int((self.stream.first_answer_at - self.started) * 1000))

    @property
    def ttlt_ms(self) -> int | None:
        if self.stream.last_delta_at is None:
            return None
        return max(0, int((self.stream.last_delta_at - self.started) * 1000))

    def _schedule_finalize(self) -> None:
        """Safety net for servers that drop the generator without running background."""
        if self._finalize_task is not None:
            return
        try:
            self._finalize_task = asyncio.create_task(self._finalize_impl())
        except RuntimeError:
            self._finalize_task = None

    async def finalize(self) -> None:
        if self._finalize_task is None:
            self._finalize_task = asyncio.create_task(self._finalize_impl())
        await asyncio.shield(self._finalize_task)

    async def events(self):  # type: ignore[no-untyped-def]
        try:
            async for line in self.stream.iter_sse():
                # The upstream has already delivered its completion marker.
                # Mark the run complete before yielding that line, because the
                # client may close the connection as soon as it sees it.
                if getattr(self.stream, "terminal", False):
                    self.completed = True
                yield line
            self.completed = True
            if not self.stream.saw_done:
                yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            # The client went away; the background/finalizer records "stopped".
            self.cancelled = True
            raise
        except LLMError as error:
            self.error = error
            self.stream.error_events.append(
                {"kind": error.kind, "message": error.message[:2000]}
            )
            yield _sse_error(error, self.request_id)
        except Exception as error:  # noqa: BLE001
            self.error = LLMError(
                "stream_abort", f"流式转发中断：{error}", retryable=False
            )
            self.stream.error_events.append(
                {"kind": self.error.kind, "message": self.error.message[:2000]}
            )
            yield _sse_error(self.error, self.request_id)
        finally:
            if self.finished_at is None:
                self.finished_at = time.perf_counter()
            self._schedule_finalize()

    def _usage(self) -> tuple[Usage, bool]:
        # LiteLLM fills in usage with its tokenizer when the provider sent no
        # usage chunk. Only trust it when the transport saw a real upstream
        # usage object; otherwise estimate from the text we actually relayed.
        if self.stream.usage.total_tokens and getattr(
            self.stream, "usage_from_upstream", False
        ):
            return self.stream.usage, False
        # ADR-074/075: relayed thinking counts toward the output estimate too --
        # the upstream generated it (and billed providers charged for it)
        # even when the client never received a final answer. The observers
        # buffer the relayed text (bounded); billing 折算是有界的：阈值内的
        # 文本用真分词器（tiktoken/o200k_base）计数，超出走脚本折算
        # （ASCII/4、非 ASCII ×1.5），长流收尾的估算成本不随输出长度膨胀。
        relayed_chars = self.stream.content_chars + self.stream.reasoning_chars
        text = self.stream.relay_text
        overflow = max(0, relayed_chars - len(text))
        return (
            billing.estimate_usage_from_text(self.messages, text, completion_overflow_chars=overflow),
            True,
        )

    async def _report_breaker_outcome(
        self, session: AsyncSession, *, http_status: int
    ) -> None:
        """Report the real stream outcome to the channel's breaker.

        A streaming request only *primes* the upstream connection when the
        channel is picked, so the channel must not be credited with a success
        before the stream really finished. The opposite mistake is just as
        harmful: a channel that breaks mid-stream has to be punished, otherwise
        it keeps receiving traffic while it is broken.
        """
        if self.error is not None:
            rate_limited = (
                self.error.kind == "rate_limit" or self.error.status_code == 429
            )
            try:
                await record_failure(session, self.target, rate_limited=rate_limited)
            except Exception:  # noqa: BLE001 - the request log matters more
                logger.warning(
                    "记录渠道失败状态失败 request_id=%s",
                    self.request_id,
                    exc_info=True,
                )
            # A mid-stream failure poisons the cached prefix too, so forget the
            # target as soon as it breaks.
            forget_affinity(self.session_key, self.target.model.id)
            return
        if http_status == 200:
            try:
                await record_success(session, self.target)
            except Exception:  # noqa: BLE001 - the request log matters more
                logger.warning(
                    "记录渠道成功状态失败 request_id=%s",
                    self.request_id,
                    exc_info=True,
                )
            # The stream only counts as a success once its terminal marker
            # arrived, so affinity is remembered here rather than at prime time.
            remember_affinity(self.session_key, self.target.model.id)

    async def _finalize_impl(self) -> None:
        try:
            await self._finalize_impl_inner()
        finally:
            if self.registry is not None and not self._released:
                self._released = True
                await self.registry.release()

    async def _finalize_impl_inner(self) -> None:
        try:
            await self.stream.close()
        except Exception:  # noqa: BLE001
            pass

        finished_at = self.finished_at or time.perf_counter()
        self.finished_at = finished_at
        total_ms = max(0, int((finished_at - self.started) * 1000))
        usage, estimated = self._usage()
        ttft_ms = self.ttft_ms
        ttfa_ms = self.ttfa_ms
        ttlt_ms = self.ttlt_ms
        throughput = billing.throughput_tps(usage.completion_tokens, total_ms, ttft_ms)

        summary_error_kind: str | None = None
        summary_error_message = ""
        # ``finish_reason`` and ``[DONE]`` both mean the upstream completed its
        # response. The relay can be cancelled by the client immediately after
        # those markers are sent; that is normal teardown, not a 499.
        terminal_seen = self.completed or getattr(self.stream, "terminal", False)
        # An upstream error outranks the completion markers: when the stream
        # breaks *after* the upstream sent ``finish_reason``/``[DONE]`` the
        # client already received an error event, so the request must not be
        # recorded as a 200 success.
        if self.error is not None:
            http_status, error_code, error_message = map_error(self.error)
            summary_error_kind = self.error.kind
            summary_error_message = self.error.message
            attempts = self.attempts + [
                _attempt(
                    self.target,
                    "error",
                    error_code=self.error.kind,
                    message=self.error.message,
                    ms=total_ms,
                    round_index=self.round_index,
                    request_headers=self.rewrite.headers if self.rewrite else None,
                    response_headers=self.stream.upstream_headers or None,
                    at=self.started_ms,
                    http_status=self.error.status_code,
                )
            ]
        elif terminal_seen:
            http_status = 200
            error_code = None
            error_message = ""
            attempts = self.attempts + [
                _attempt(
                    self.target,
                    "success",
                    ms=total_ms,
                    round_index=self.round_index,
                    request_headers=self.rewrite.headers if self.rewrite else None,
                    response_headers=self.stream.upstream_headers or None,
                    at=self.started_ms,
                    http_status=_stream_upstream_status(self.stream),
                )
            ]
        else:
            http_status = CLIENT_CLOSED_STATUS
            error_code = STREAM_STOPPED
            error_message = "客户端提前结束或取消了流式请求"
            summary_error_kind = "client_closed"
            summary_error_message = error_message
            attempts = self.attempts + [
                _attempt(
                    self.target,
                    "stopped",
                    error_code=STREAM_STOPPED,
                    message=error_message,
                    ms=total_ms,
                    round_index=self.round_index,
                    request_headers=self.rewrite.headers if self.rewrite else None,
                    response_headers=self.stream.upstream_headers or None,
                    # The client closed the stream. A pseudo status keeps the
                    # cancellation visible instead of folding it into the 200 the
                    # stream was opened with.
                    at=self.started_ms,
                )
            ]

        applied, skipped, rewrite_diff = rewrite_log(self.rewrite, self.response)
        if self.response is not None and self.response.skipped == "stream":
            skipped = "stream"
        # Streaming requests store a compact summary only, never the body.
        body_snapshot = BodySnapshot(
            request_body=self.client_body,
            upstream_body=self.upstream_body,
            response_summary=stream_summary_payload(
                self.stream,
                error_kind=summary_error_kind,
                error_message=summary_error_message,
            ),
            reasoning_content=self.stream.reasoning_content,
        )
        async with self.session_factory() as session:
            await self._report_breaker_outcome(session, http_status=http_status)
            await _write_log(
                session,
                request_id=self.request_id,
                requested_model=self.requested_model,
                api_key=SimpleNamespace(id=self.api_key_id, label=self.api_key_label),
                user=SimpleNamespace(id=self.user_id),
                target=self.target,
                combo_id=_resolved_combo_id(self.target),
                http_status=http_status,
                usage=usage,
                total_ms=total_ms,
                attempts=attempts,
                error_code=error_code,
                error_message=error_message,
                is_stream=True,
                client_ip=self.client_ip,
                request_headers=self.request_headers,
                upstream_request_id=self.stream.response_id,
                response_headers=(self.response.headers if self.response else self.stream.upstream_headers),
                ttft_ms=ttft_ms,
                ttfa_ms=ttfa_ms,
                ttlt_ms=ttlt_ms,
                throughput_tps=throughput,
                usage_estimated=estimated,
                rewrite_applied=applied,
                rewrite_skipped=skipped,
                rewrite_diff=rewrite_diff,
                body_snapshot=body_snapshot,
                session_factory=self.session_factory,
            )


async def start_direct_stream(
    session: AsyncSession,
    *,
    api_key: orm.ApiKeyORM,
    user: orm.UserORM,
    body: dict[str, Any],
    request_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    client_ip: str = "",
    request_headers: dict[str, str] | None = None,
) -> StreamRun:
    """Open and prime an upstream SSE stream, or raise a normal JSON error.

    A pre-first-byte failure follows the same round and failover rules as a
    non-stream request. Once the stream is primed and returned to the client,
    no further failover happens.
    """
    settings = get_settings()
    started = time.perf_counter()
    # Wall clock of the same moment, for the attempt timeline (``started`` is
    # monotonic and only comparable inside this process).
    started_ms = now_ms()
    requested = str(body.get("model") or "").strip()
    target: CallTarget | None = None
    attempts: list[dict[str, Any]] = []
    usage = Usage()
    body_snapshot = BodySnapshot(request_body=body)

    try:
        _validate_body(body)
        target = await resolve_target(session, requested)
        upstream_timeouts = await upstream_settings_service.get_upstream_timeouts(session)

        async def invoke(candidate: Candidate, captured: dict[str, Any]):
            with channels.key_session_scope(
                session_key_from_headers(request_headers)
            ):
                prepared = prepare_request(
                    candidate.channel,
                    body,
                    upstream_model=candidate.model.upstream_model,
                    requested_model=requested,
                    request_id=request_id,
                    client_headers=request_headers,
                )
            body_snapshot.upstream_body = build_request_body(
                candidate.channel, candidate.model.upstream_model, prepared.body
            )
            captured["request"] = dict(prepared.headers)
            stream = await open_stream(
                candidate.channel,
                candidate.model.upstream_model,
                body,
                requested,
                timeout_s=int(captured.get("timeout_s") or settings.request_timeout_s),
                request_id=request_id,
                prepared=prepared,
                upstream_timeouts=upstream_timeouts,
            )
            captured["response"] = dict(stream.upstream_headers)
            # open_stream() already raised for a non-2xx, so the primed stream
            # still carries the response the upstream answered with.
            captured["status_code"] = _stream_upstream_status(stream)
            response = prepare_response(
                candidate.channel,
                {},
                stream.upstream_headers,
                upstream_model=candidate.model.upstream_model,
                requested_model=requested,
                is_stream=True,
            )
            return stream, prepared, response

        winning, winning_round, opened = await _RoundAttempts(
            session,
            target,
            attempts=attempts,
            is_stream=True,
            log_success=False,
            session_key=session_key_from_headers(request_headers),
        ).run(invoke)
        stream, prepared, prepared_response = opened
    except OpenAIError as error:
        total_ms = int((time.perf_counter() - started) * 1000)
        body_snapshot.response_summary = empty_stream_summary(error.code, error.message)
        await _write_log(
            session,
            request_id=request_id,
            requested_model=requested,
            api_key=api_key,
            user=user,
            target=None,
            combo_id=_resolved_combo_id(target),
            http_status=error.status,
            usage=usage,
            total_ms=total_ms,
            attempts=attempts,
            error_code=error.code,
            error_message=error.message,
            is_stream=True,
            body_snapshot=body_snapshot,
            session_factory=session_factory,
        )
        raise
    except FailoverFailed as failure:
        total_ms = int((time.perf_counter() - started) * 1000)
        status, code, message = map_error(failure.error)
        body_snapshot.response_summary = empty_stream_summary(code, message)
        await _write_log(
            session,
            request_id=request_id,
            requested_model=requested,
            api_key=api_key,
            user=user,
            target=failure.target,
            combo_id=_resolved_combo_id(target),
            http_status=status,
            usage=usage,
            total_ms=total_ms,
            attempts=attempts,
            error_code=code,
            error_message=message,
            is_stream=True,
            body_snapshot=body_snapshot,
            session_factory=session_factory,
        )
        raise OpenAIError(
            status,
            message,
            code,
            _error_type(status),
            details=error_details(failure.error),
        ) from failure.error
    except Exception as error:  # noqa: BLE001
        total_ms = int((time.perf_counter() - started) * 1000)
        body_snapshot.response_summary = empty_stream_summary(ErrorCode.INTERNAL, str(error))
        await _write_log(
            session,
            request_id=request_id,
            requested_model=requested,
            api_key=api_key,
            user=user,
            target=None,
            combo_id=_resolved_combo_id(target),
            http_status=500,
            usage=usage,
            total_ms=total_ms,
            attempts=attempts,
            error_code=ErrorCode.INTERNAL,
            error_message=str(error),
            is_stream=True,
            body_snapshot=body_snapshot,
            session_factory=session_factory,
        )
        raise OpenAIError(
            500, "网关内部错误", ErrorCode.INTERNAL, "server_error"
        ) from error

    return StreamRun(
        stream=stream,
        target=winning,
        session_factory=session_factory,
        request_id=request_id,
        requested_model=requested,
        api_key_id=api_key.id,
        api_key_label=api_key.label,
        user_id=user.id,
        messages=_messages(body),
        started=started,
        started_ms=started_ms,
        client_body=body,
        upstream_body=body_snapshot.upstream_body,
        attempts=attempts,
        round_index=winning_round,
        rewrite=prepared,
        response=prepared_response,
        client_ip=client_ip,
        request_headers=request_headers,
        session_key=session_key_from_headers(request_headers),
    )
