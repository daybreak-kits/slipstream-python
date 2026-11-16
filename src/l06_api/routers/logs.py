from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.config import get_settings
from l01_core.errors import AppError, ErrorCode
from l01_core.request_meta import forwarded_ips
from l03_domain.query_lang import has_syntax, parse
from l04_repo import attempt_filters, log_filter_values, log_query, orm, repo
from l04_repo.dialects import dialect_name
from l05_service import aggregates, log_views
from l05_service import timeseries as timeseries_service
from l06_api.deps import get_current_admin, get_db
from l06_api.schemas import (
    LogFilterOptions,
    LogGroup,
    LogSummary,
    LogUserOption,
    RequestLogList,
    RequestLogOut,
)

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    dependencies=[Depends(get_current_admin)],
)


def _user_name(user: orm.UserORM | None) -> str:
    if user is None:
        return ""
    return user.display_name or user.username or ""


async def _user_names(session: AsyncSession) -> dict[str, str]:
    users = await repo.list_users(session)
    return {user.id: _user_name(user) for user in users}


def _syntax_view_unsupported() -> AppError:
    """Refuse a syntax query on a view that cannot apply it yet.

    The aggregate views and the summary scan the log table through their own
    repo call. Until that seam takes a predicate (task-113 step 3), the honest
    answers are "apply it" or "say you cannot": answering with the keyword
    search instead would quietly show a different question's numbers.
    """
    return AppError(
        ErrorCode.INVALID_REQUEST,
        "语法查询只在「请求」视图生效；请切回请求视图，或改用关键词搜索",
        status=400,
        details={"view": "requests"},
    )


async def _search_arguments(session: AsyncSession, q: str) -> tuple[str, Any | None]:
    """Split q into the keyword it still is and the predicate it compiles to.

    No syntax means the keyword path stays exactly as it was. Syntax means a
    strict parse: a query the language rejects raises QueryError, an AppError
    400 that already carries details.column and details.suggestion. It is
    deliberately not caught here -- falling back to the keyword search would
    silently answer a different question than the one that was typed.
    """
    if not has_syntax(q):
        return q, None
    predicate = await log_query.compile_log_filter(
        session,
        parse(q),
        attempts=log_query.attempt_resolver(dialect_name(session)),
    )
    return "", predicate


def serialize_log(
    log,  # type: ignore[no-untyped-def]
    *,
    user_name: str = "",
    detail: bool = False,
    error_body: str | None = None,
) -> RequestLogOut:
    params = repo.json_loads(log.params_json, {}) if detail else {}
    headers = repo.json_loads(log.response_headers_json, {}) if detail else {}
    # Rows mode: the eager-loaded relationship is the trail. Falling back to
    # the column keeps an un-loaded row (and JSON mode) readable.
    loaded_attempts = attempt_filters.log_attempts(log)
    if loaded_attempts is not None:
        attempts = loaded_attempts
    else:
        attempts_value = repo.json_loads(log.attempts_json, [])
        attempts = attempts_value if isinstance(attempts_value, list) else []
    forwarded_value = repo.json_loads(log.forwarded_json, [])
    forwarded_sources = (
        [item for item in forwarded_value if isinstance(item, dict)]
        if isinstance(forwarded_value, list)
        else []
    )
    client_headers = repo.json_loads(log.request_headers_json, {}) if detail else {}
    return RequestLogOut(
        id=log.id,
        request_id=log.request_id,
        upstream_request_id=log.upstream_request_id or "",
        user_id=log.user_id,
        user_name=user_name,
        api_key_id=log.api_key_id,
        api_key_label=log.api_key_label or "",
        requested_model=log.requested_model or None,
        channel_name=log.channel_name or "",
        upstream_model=log.upstream_model or "",
        response_model=log.response_model or "",
        is_stream=bool(log.is_stream),
        source=log.source or "chat",
        client_ip=log.client_ip or "",
        forwarded_ips=forwarded_ips(forwarded_sources),
        forwarded_sources=forwarded_sources,
        request_headers=(
            client_headers if isinstance(client_headers, dict) else {}
        ),
        http_status=int(log.http_status),
        error_code=log.error_code,
        error_message=log.error_message or "",
        prompt_tokens=int(log.prompt_tokens or 0),
        completion_tokens=int(log.completion_tokens or 0),
        cached_tokens=int(log.cached_tokens or 0),
        cache_ratio=float(log.cache_ratio or 0),
        cost_usd=float(log.cost_usd or 0),
        ttft_ms=log.ttft_ms,
        ttfa_ms=log.ttfa_ms,
        ttlt_ms=log.ttlt_ms,
        total_ms=int(log.total_ms or 0),
        throughput_tps=(
            float(log.throughput_tps) if log.throughput_tps is not None else None
        ),
        usage_estimated=bool(log.usage_estimated),
        attempts=attempts,
        attempt_error_count=sum(
            1
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("status") == "error"
        ),
        rewrite_applied=bool(log.rewrite_applied),
        rewrite_skipped=log.rewrite_skipped or "",
        rewrite_diff=repo.json_loads(log.rewrite_diff_json, {}),
        params=params if isinstance(params, dict) else {},
        response_headers=headers if isinstance(headers, dict) else {},
        error_body=error_body,
        created_at=int(log.created_at),
    )


@router.get("", response_model=RequestLogList)
async def list_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    q: str = Query(default=""),
    channel: str = Query(default=""),
    model: str = Query(default=""),
    status: str = Query(default=""),
    attempt_status: str = Query(default=""),
    user: str = Query(default=""),
    key_label: str = Query(default=""),
    stream: str = Query(default=""),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=0),
    view: str = Query(default="requests", pattern="^(requests|combo|status)$"),
    group_key: str = Query(default=""),
    session: AsyncSession = Depends(get_db),
) -> RequestLogList:
    """Filtered request log list in one of three views.

    requests (the default) keeps the row-per-request list. combo and status
    answer with aggregate groups instead: the filters still select the facts,
    the view only decides how those facts are grouped for display. group_key
    drills one group back into its rows, which is why it only applies to
    requests.

    q reaches the whole request: ids, model and channel names, the member
    names stored inside attempts_json, the key label and the user name. A q that
    parses as query-language syntax is compiled instead and never falls back.
    """
    search, predicate = await _search_arguments(session, q)
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise AppError(ErrorCode.INVALID_REQUEST, "结束时间必须晚于开始时间", status=400)
    filters: dict[str, object] = {
        "channel": channel,
        "model": model,
        "status": status,
        "attempt_status": attempt_status,
        "search": search,
        "api_key_label": key_label,
        "user_id": user,
        "stream": stream,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }
    if view != "requests":
        if predicate is not None:
            raise _syntax_view_unsupported()
        groups, total, scan_limited = await log_views.compute_groups(
            session, view=view, page=page, page_size=page_size, filters=filters
        )
        return RequestLogList(
            items=[],
            total=total,
            page=page,
            page_size=page_size,
            groups=[LogGroup(**group) for group in groups],
            view=view,
            scan_limited=scan_limited,
        )
    segment = log_views.parse_segment_key(group_key)
    if segment is not None:
        logs, total = await repo.list_logs_for_segment(
            session,
            page=page,
            page_size=page_size,
            upstream_model=segment.model,
            status_code=segment.status_code,
            error_kinds=segment.error_kinds,
            start_ms=segment.first_at,
            end_ms=segment.last_at,
            channel=channel,
            model=model,
            status=status,
            attempt_status=attempt_status,
            search=search,
            predicate=predicate,
            api_key_label=key_label,
            user_id=user,
            stream=stream,
        )
    else:
        logs, total = await repo.list_logs(
            session,
            page=page,
            page_size=page_size,
            requested_model=model,
            model_exact=group_key,
            channel=channel,
            status=status,
            attempt_status=attempt_status,
            search=search,
            predicate=predicate,
            api_key_label=key_label,
            user_id=user,
            start_ms=start_ms,
            end_ms=end_ms,
            stream=stream,
        )
    names = await _user_names(session)
    return RequestLogList(
        items=[serialize_log(log, user_name=names.get(log.user_id or "", "")) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
        view="requests",
    )


@router.get("/recent", response_model=RequestLogList)
async def recent_logs(
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> RequestLogList:
    logs, total = await repo.list_logs(session, page=1, page_size=limit)
    names = await _user_names(session)
    return RequestLogList(
        items=[serialize_log(log, user_name=names.get(log.user_id or "", "")) for log in logs],
        total=total,
        page=1,
        page_size=limit,
    )


@router.get("/filters", response_model=LogFilterOptions)
async def filter_options(
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db),
) -> LogFilterOptions:
    # Catalog first, then the last 30 days of the log: an empty log table still
    # offers the models that exist, and a combo-served model shows up under the
    # spelling the catalog uses.
    channels, models, labels, users, truncated = await log_filter_values.filter_options(
        session, q=q
    )
    return LogFilterOptions(
        channels=channels,
        models=models,
        key_labels=labels,
        users=[LogUserOption(id=user.id, name=_user_name(user)) for user in users],
        truncated=truncated,
    )


@router.get("/summary", response_model=LogSummary)
async def summary(
    q: str = Query(default=""),
    user: str = Query(default=""),
    key_label: str = Query(default=""),
    stream: str = Query(default=""),
    model: str = Query(default=""),
    status: str = Query(default="", pattern="^(success|error)?$"),
    channel: str = Query(default=""),
    range: str = Query(default="today", pattern="^(today|24h|7d|30d|custom)$"),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=0),
    tz: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> LogSummary:
    """Aggregated statistics for the current log-page filter set."""
    if has_syntax(q):
        raise _syntax_view_unsupported()
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise AppError(ErrorCode.INVALID_REQUEST, "结束时间必须晚于开始时间", status=400)
    timezone = tz or get_settings().default_timezone
    start, end, _ = timeseries_service.range_window_ms(
        range, timezone, start_ms, end_ms
    )
    data = await aggregates.compute_log_summary(
        session,
        start,
        end,
        channel=channel,
        model=model,
        status=status,
        search=q,
        api_key_label=key_label,
        user_id=user,
        stream=stream,
    )
    return LogSummary(**data)


@router.get("/{request_id}", response_model=RequestLogOut)
async def log_detail(
    request_id: str,
    session: AsyncSession = Depends(get_db),
) -> RequestLogOut:
    """One request including attempts, params, response headers and the
    redacted error body when the body policy captured it.

    ``request_id`` may be either the gateway id or the upstream response id.
    """
    log = await repo.get_log_by_any_id(session, request_id)
    if log is None:
        raise AppError(ErrorCode.NOT_FOUND, "请求日志不存在", status=404)
    names = await _user_names(session)
    body = await repo.get_request_body(session, log.request_id)
    error_body = None
    if body is not None and int(log.http_status or 0) >= 400:
        error_body = body.response_body
    return serialize_log(
        log,
        user_name=names.get(log.user_id or "", ""),
        detail=True,
        error_body=error_body,
    )
