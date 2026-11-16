"""Reconcile the ledger against upstream usage or against request_logs.

With an upstream report (CSV or JSON keyed by request_id) the module compares
tokens and money per request, reports the differences and repairs them: a
request missing from the ledger gets its bill inserted, a usage or amount
difference gets an appended adjustment with reason=reconcile, so history is
never rewritten. Without a report it degrades to a ledger-vs-request_logs self
check that reports coverage and totals.

Differences below the tolerance (0.1% relative, or 1 USD absolute for money)
are ignored.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.timeutil import now_ms
from l04_repo import ledger_repo, orm

USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd")
REL_TOLERANCE = 0.001
ABS_COST_TOLERANCE_USD = 1.0


@dataclass(frozen=True)
class Difference:
    """One field of one request where the ledger and the report disagree."""

    request_id: str
    kind: str
    field: str
    expected: float
    actual: float

    @property
    def delta(self) -> float:
        return self.expected - self.actual

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "field": self.field,
            "expected": round(self.expected, 8),
            "actual": round(self.actual, 8),
            "delta": round(self.delta, 8),
        }


def _number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _normalise(raw: dict[str, Any]) -> tuple[str, dict[str, float]]:
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("report row without request_id")
    return request_id, {field: _number(raw.get(field)) for field in USAGE_FIELDS}


def load_report(path: str | Path) -> dict[str, dict[str, float]]:
    """Read an upstream usage report: JSON (list or {rows: [...]}) or CSV."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    else:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    report: dict[str, dict[str, float]] = {}
    for raw in rows:
        request_id, values = _normalise(raw)
        report[request_id] = values
    return report


def _exceeds(delta: float, expected: float, field: str) -> bool:
    if delta == 0:
        return False
    if field == "cost_usd" and abs(delta) > ABS_COST_TOLERANCE_USD:
        return True
    return abs(delta) > REL_TOLERANCE * max(abs(expected), 1e-9)


async def _extra_request_ids(
    session: AsyncSession, report_ids: set[str], start_ms: int, end_ms: int | None
) -> list[str]:
    """Ledger or adjustment rows in range that the report does not mention."""
    found: list[str] = []
    for model in (orm.RequestLedgerORM, orm.LedgerAdjustmentORM):
        query = select(model.request_id).where(model.created_at >= start_ms)
        if end_ms is not None:
            query = query.where(model.created_at < end_ms)
        found.extend((await session.execute(query)).scalars().all())
    return sorted({request_id for request_id in found if request_id not in report_ids})


async def compare_report(
    session: AsyncSession,
    report: dict[str, dict[str, float]],
    *,
    start_ms: int = 0,
    end_ms: int | None = None,
    include_extras: bool = True,
) -> list[Difference]:
    """Compare a report against ledger+adjustments; optionally flag extra rows."""
    differences: list[Difference] = []
    usage = await ledger_repo.ledger_usage_by_request(session, list(report))
    for request_id, expected in report.items():
        actual = usage.get(request_id)
        if not actual or not actual.get("has_ledger_row"):
            for field in USAGE_FIELDS:
                if expected[field]:
                    differences.append(
                        Difference(request_id, "missing_in_ledger", field, expected[field], 0.0)
                    )
            continue
        for field in USAGE_FIELDS:
            current = float(actual.get(field) or 0.0)
            if _exceeds(expected[field] - current, expected[field], field):
                differences.append(
                    Difference(request_id, "usage_mismatch", field, expected[field], current)
                )
    if include_extras:
        extras = await _extra_request_ids(session, set(report), start_ms, end_ms)
        extra_usage = await ledger_repo.ledger_usage_by_request(session, extras)
        for request_id in extras:
            actual = extra_usage.get(request_id) or {}
            for field in USAGE_FIELDS:
                value = float(actual.get(field) or 0.0)
                if value:
                    differences.append(
                        Difference(request_id, "missing_in_report", field, 0.0, value)
                    )
    return differences


async def apply_differences(
    session: AsyncSession,
    differences: list[Difference],
    *,
    actor: str = "",
    commit: bool = True,
) -> int:
    """Repair the ledger: insert missing bills, append adjustments for the rest.

    Returns the number of requests touched.
    """
    missing: dict[str, dict[str, float]] = {}
    deltas: dict[str, dict[str, float]] = {}
    for difference in differences:
        if difference.kind == "missing_in_ledger":
            bucket = missing.setdefault(difference.request_id, {})
            bucket[difference.field] = max(bucket.get(difference.field, 0.0), difference.expected)
        else:
            bucket = deltas.setdefault(difference.request_id, {})
            bucket[difference.field] = bucket.get(difference.field, 0.0) + difference.delta
    if missing:
        rows = [
            ledger_repo.ledger_row(
                request_id=request_id,
                prompt_tokens=int(round(values.get("prompt_tokens", 0.0))),
                completion_tokens=int(round(values.get("completion_tokens", 0.0))),
                cached_tokens=int(round(values.get("cached_tokens", 0.0))),
                cost_usd=round(values.get("cost_usd", 0.0), 8),
            )
            for request_id, values in missing.items()
        ]
        await ledger_repo.insert_ledger_rows(session, rows)
    for request_id, fields in deltas.items():
        row = ledger_repo.adjustment_row(
            request_id=request_id,
            delta_prompt_tokens=int(round(fields.get("prompt_tokens", 0.0))),
            delta_completion_tokens=int(round(fields.get("completion_tokens", 0.0))),
            delta_cached_tokens=int(round(fields.get("cached_tokens", 0.0))),
            delta_cost_usd=round(fields.get("cost_usd", 0.0), 8),
            reason="reconcile",
            actor=actor,
        )
        await ledger_repo.add_ledger_adjustment(session, row, commit=False)
    if commit:
        await session.commit()
    return len(missing) + len(deltas)


async def self_check(
    session: AsyncSession, *, start_ms: int = 0, end_ms: int | None = None
) -> dict[str, Any]:
    """Ledger vs request_logs coverage and totals when no report is available."""
    upper = end_ms if end_ms is not None else 9_999_999_999_999

    async def _ids(model) -> set[str]:
        query = select(model.request_id).where(
            model.created_at >= start_ms, model.created_at < upper
        )
        return set((await session.execute(query)).scalars().all())

    log_ids = await _ids(orm.RequestLogORM)
    ledger_ids = await _ids(orm.RequestLedgerORM)
    totals = await ledger_repo.ledger_totals(session, start_ms=start_ms, end_ms=end_ms)
    logs = (
        await session.execute(
            select(
                func.count(orm.RequestLogORM.id),
                func.coalesce(
                    func.sum(orm.RequestLogORM.prompt_tokens), 0
                ),
                func.coalesce(
                    func.sum(orm.RequestLogORM.completion_tokens), 0
                ),
                func.coalesce(
                    func.sum(orm.RequestLogORM.cached_tokens), 0
                ),
                func.coalesce(
                    func.sum(orm.RequestLogORM.cost_usd), 0.0
                ),
            ).where(orm.RequestLogORM.created_at >= start_ms, orm.RequestLogORM.created_at < upper)
        )
    ).one()
    log_count = int(logs[0] or 0)
    ledger_count = int(totals["requests"] or 0)
    shared = len(log_ids & ledger_ids)
    return {
        "checked_at": now_ms(),
        "requests": {"request_logs": log_count, "request_ledger": ledger_count},
        "coverage": round(shared / log_count, 6) if log_count else 1.0,
        "missing_in_ledger": sorted(log_ids - ledger_ids)[:20],
        "ledger_only": sorted(ledger_ids - log_ids)[:20],
        "totals": {
            "prompt_tokens": {"request_logs": int(logs[1] or 0), "request_ledger": totals["prompt_tokens"]},
            "completion_tokens": {
                "request_logs": int(logs[2] or 0),
                "request_ledger": totals["completion_tokens"],
            },
            "cached_tokens": {"request_logs": int(logs[3] or 0), "request_ledger": totals["cached_tokens"]},
            "cost_usd": {
                "request_logs": round(float(logs[4] or 0.0), 8),
                "request_ledger": round(float(totals["cost_usd"] or 0.0), 8),
            },
        },
        "balanced": (
            log_ids == ledger_ids
            and int(logs[1] or 0) == totals["prompt_tokens"]
            and int(logs[2] or 0) == totals["completion_tokens"]
            and int(logs[3] or 0) == totals["cached_tokens"]
            and round(float(logs[4] or 0.0), 8) == round(float(totals["cost_usd"] or 0.0), 8)
        ),
    }