"""Append-only ledger persistence.

The ledger is the billing source of truth: rows are insert-only and made
idempotent by the unique request_ledger.request_id. Corrections are appended
to ledger_adjustments, so a corrected bill never rewrites history.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.ids import new_id
from l01_core.timeutil import now_ms
from l02_infra.dialect import POSTGRESQL, SQLITE, execute_with_lock_retry
from l04_repo import orm
from l04_repo.dialects import conflict_insert, dialect_name

# Every ledger row carries exactly these keys; the sink inserts them as a
# multi-row values() list, which requires a uniform shape.
LEDGER_COLUMNS = (
    "id",
    "request_id",
    "user_id",
    "api_key_id",
    "final_channel_model_id",
    "requested_model",
    "source",
    "http_status",
    "error_code",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_ratio",
    "cost_usd",
    "usage_estimated",
    "created_at",
    "written_at",
)

ADJUSTMENT_COLUMNS = (
    "id",
    "request_id",
    "delta_prompt_tokens",
    "delta_completion_tokens",
    "delta_cached_tokens",
    "delta_cost_usd",
    "reason",
    "actor",
    "created_at",
)


def ledger_row(
    *,
    request_id: str,
    user_id: str | None = None,
    api_key_id: str | None = None,
    final_channel_model_id: str | None = None,
    requested_model: str = "",
    source: str = "chat",
    http_status: int = 200,
    error_code: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    cache_ratio: float = 0.0,
    cost_usd: float = 0.0,
    usage_estimated: bool = False,
    created_at: int | None = None,
) -> dict[str, Any]:
    """Build a complete ledger row (all columns present, ready for values())."""
    return {
        "id": new_id(),
        "request_id": request_id,
        "user_id": user_id,
        "api_key_id": api_key_id,
        "final_channel_model_id": final_channel_model_id,
        "requested_model": requested_model,
        "source": source,
        "http_status": http_status,
        "error_code": error_code,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "cached_tokens": int(cached_tokens),
        "cache_ratio": float(cache_ratio),
        "cost_usd": float(cost_usd),
        "usage_estimated": bool(usage_estimated),
        "created_at": int(created_at if created_at is not None else now_ms()),
        "written_at": now_ms(),
    }


def _dialect_name(session: AsyncSession) -> str:
    """The dialect of the session's bind (SQLite by default)."""
    return dialect_name(session)


async def insert_ledger_rows(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> int:
    """Insert billing rows, absorbing duplicates on request_id.

    The caller owns the transaction so a batch is one commit.
    """
    if not rows:
        return 0
    dialect = _dialect_name(session)
    if dialect in (SQLITE, POSTGRESQL):
        statement = (
            conflict_insert(dialect, orm.RequestLedgerORM)
            .values(list(rows))
            .on_conflict_do_nothing(index_elements=["request_id"])
        )
        await execute_with_lock_retry(session, statement, dialect=dialect)
        return len(rows)

    inserted = 0
    for row in rows:
        existing = (
            await session.execute(
                select(orm.RequestLedgerORM.request_id).where(
                    orm.RequestLedgerORM.request_id == row["request_id"]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(orm.RequestLedgerORM(**row))
        inserted += 1
    return inserted


def adjustment_row(
    *,
    request_id: str,
    delta_prompt_tokens: int = 0,
    delta_completion_tokens: int = 0,
    delta_cached_tokens: int = 0,
    delta_cost_usd: float = 0.0,
    reason: str = "reconcile",
    actor: str = "",
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "request_id": request_id,
        "delta_prompt_tokens": int(delta_prompt_tokens),
        "delta_completion_tokens": int(delta_completion_tokens),
        "delta_cached_tokens": int(delta_cached_tokens),
        "delta_cost_usd": float(delta_cost_usd),
        "reason": reason,
        "actor": actor,
        "created_at": now_ms(),
    }


async def add_ledger_adjustment(
    session: AsyncSession, row: dict[str, Any], *, commit: bool = True
) -> dict[str, Any]:
    session.add(orm.LedgerAdjustmentORM(**row))
    if commit:
        await session.commit()
    return row


async def ledger_usage_by_request(
    session: AsyncSession, request_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Effective usage per request: ledger row plus the sum of adjustments."""
    ids = list(dict.fromkeys(request_ids))
    if not ids:
        return {}
    usage: dict[str, dict[str, Any]] = {}
    ledger_rows = (
        (
            await session.execute(
                select(orm.RequestLedgerORM).where(
                    orm.RequestLedgerORM.request_id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    for row in ledger_rows:
        usage[row.request_id] = {
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "cached_tokens": row.cached_tokens,
            "cost_usd": row.cost_usd,
            "cache_ratio": row.cache_ratio or 0.0,
            "usage_estimated": bool(row.usage_estimated),
            "has_ledger_row": True,
        }
    adjustments = (
        (
            await session.execute(
                select(orm.LedgerAdjustmentORM).where(
                    orm.LedgerAdjustmentORM.request_id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    for row in adjustments:
        bucket = usage.setdefault(
            row.request_id,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "cost_usd": 0.0,
                "cache_ratio": 0.0,
                "usage_estimated": False,
                "has_ledger_row": False,
            },
        )
        bucket["prompt_tokens"] += row.delta_prompt_tokens
        bucket["completion_tokens"] += row.delta_completion_tokens
        bucket["cached_tokens"] += row.delta_cached_tokens
        bucket["cost_usd"] += row.delta_cost_usd
    return usage


async def ledger_totals(
    session: AsyncSession, *, start_ms: int = 0, end_ms: int | None = None
) -> dict[str, Any]:
    """Totals over the effective ledger (rows plus adjustments) for reconcile."""
    row_query = select(
        func.count(orm.RequestLedgerORM.id),
        func.coalesce(func.sum(orm.RequestLedgerORM.prompt_tokens), 0),
        func.coalesce(func.sum(orm.RequestLedgerORM.completion_tokens), 0),
        func.coalesce(func.sum(orm.RequestLedgerORM.cached_tokens), 0),
        func.coalesce(func.sum(orm.RequestLedgerORM.cost_usd), 0.0),
    ).where(orm.RequestLedgerORM.created_at >= start_ms)
    adj_query = select(
        func.count(orm.LedgerAdjustmentORM.id),
        func.coalesce(func.sum(orm.LedgerAdjustmentORM.delta_prompt_tokens), 0),
        func.coalesce(func.sum(orm.LedgerAdjustmentORM.delta_completion_tokens), 0),
        func.coalesce(func.sum(orm.LedgerAdjustmentORM.delta_cached_tokens), 0),
        func.coalesce(func.sum(orm.LedgerAdjustmentORM.delta_cost_usd), 0.0),
    ).where(orm.LedgerAdjustmentORM.created_at >= start_ms)
    if end_ms is not None:
        row_query = row_query.where(orm.RequestLedgerORM.created_at < end_ms)
        adj_query = adj_query.where(orm.LedgerAdjustmentORM.created_at < end_ms)
    rows = (await session.execute(row_query)).one()
    adjustments = (await session.execute(adj_query)).one()
    return {
        "requests": int(rows[0] or 0),
        "prompt_tokens": int(rows[1] or 0) + int(adjustments[1] or 0),
        "completion_tokens": int(rows[2] or 0) + int(adjustments[2] or 0),
        "cached_tokens": int(rows[3] or 0) + int(adjustments[3] or 0),
        "cost_usd": float(rows[4] or 0.0) + float(adjustments[4] or 0.0),
        "adjustments": int(adjustments[0] or 0),
    }
