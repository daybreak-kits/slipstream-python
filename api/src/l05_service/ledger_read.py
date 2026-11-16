"""Read-side overlay: bill from the ledger while diagnostics stay in request_logs.

Cost and billing views read the append-only request_ledger plus the
ledger_adjustments deltas. Diagnostics views keep reading request_logs.
With LEDGER_SPLIT=off nothing is overlaid, so the old single-write path
behaves exactly as before.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core import runtime
from l04_repo import ledger_repo, orm

BILLING_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_ratio",
    "cost_usd",
    "usage_estimated",
)


def overlay_enabled() -> bool:
    """True when billing must come from the ledger instead of request_logs."""
    return runtime.current_settings().ledger_split != "off"


class LedgerRowView:
    """A request_logs row whose billing fields are overlaid from the ledger.

    Diagnostics columns (attempts, latency, channel names) still come from the
    log row, so the log page and the cost page can share one reading path.
    """

    __slots__ = ("_row", "_billing")

    def __init__(self, row: orm.RequestLogORM, billing: dict[str, Any]) -> None:
        self._row = row
        self._billing = billing

    def __getattr__(self, name: str) -> Any:
        return getattr(self._row, name)

    @property
    def prompt_tokens(self) -> int:
        return int(self._billing.get("prompt_tokens") or 0)

    @property
    def completion_tokens(self) -> int:
        return int(self._billing.get("completion_tokens") or 0)

    @property
    def cached_tokens(self) -> int:
        return int(self._billing.get("cached_tokens") or 0)

    @property
    def cache_ratio(self) -> float:
        return float(self._billing.get("cache_ratio") or 0.0)

    @property
    def cost_usd(self) -> float:
        return float(self._billing.get("cost_usd") or 0.0)

    @property
    def usage_estimated(self) -> bool:
        return bool(self._billing.get("usage_estimated"))


async def apply_overlay(
    session: AsyncSession, rows: list[orm.RequestLogORM]
) -> list[Any]:
    """Return rows carrying ledger billing values, or the rows untouched.

    Rows without a ledger entry (written before the split, or still in the WAL)
    keep their request_logs values: billing never disappears from a view.
    """
    if not rows or not overlay_enabled():
        return list(rows)
    request_ids = [row.request_id for row in rows]
    usage = await ledger_repo.ledger_usage_by_request(session, request_ids)
    return [
        LedgerRowView(row, usage[row.request_id])
        if usage.get(row.request_id, {}).get("has_ledger_row")
        else row
        for row in rows
    ]
