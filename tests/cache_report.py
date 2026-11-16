"""Manual report: cache metrics before/after the cohort change.

Run from api:

    .venv/Scripts/python.exe tests/cache_report.py

Reads api/data/app.db read-only and reports the token-weighted rate the
old code printed (all rows) next to the new cohort (A/B/C/D), so the change can
be re-measured at any time without a running gateway.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from l05_service import aggregates  # noqa: E402

WINDOW_MS = 24 * 60 * 60 * 1000
COLUMNS = (
    "created_at",
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "cost_usd",
    "usage_estimated",
    "error_code",
    "http_status",
    "request_headers_json",
    "final_channel_model_id",
)


def _db_path() -> Path:
    path = Path(__file__).resolve().parents[1] / "data" / "app.db"
    if not path.exists():
        raise SystemExit(f"找不到数据库：{path}")
    return path


def _connect(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        # WAL recovery needs a writable handle even for read-only queries.
        return sqlite3.connect(path)


def main() -> None:
    connection = _connect(_db_path())
    latest = connection.execute("select max(created_at) from request_logs").fetchone()[0]
    if latest is None:
        raise SystemExit("request_logs 为空")
    end_ms = int(latest)
    start_ms = end_ms - WINDOW_MS

    rows = [
        SimpleNamespace(**dict(zip(COLUMNS, values)))
        for values in connection.execute(
            "select " + ", ".join(COLUMNS) + " from request_logs"
            " where created_at >= ? and created_at <= ? order by created_at",
            (start_ms, end_ms),
        )
    ]
    prices = {
        model_id: (input_price or 0.0, cache_price if cache_price is not None else input_price or 0.0)
        for model_id, input_price, cache_price in connection.execute(
            "select id, input_price_per_1m, cache_read_price_per_1m from channel_models"
        )
    }
    connection.close()

    counted = aggregates.counted_rows(rows)
    excluded = [row for row in rows if not aggregates.prompt_tokens_known(row)]
    naive_prompt = sum(int(row.prompt_tokens or 0) for row in rows)
    naive_cached = sum(int(row.cached_tokens or 0) for row in rows)
    usage = aggregates.usage_totals(rows)
    cacheable = aggregates.cacheable_totals(rows)
    interrupted = [row for row in rows if aggregates.prompt_tokens_known(row) and row.usage_estimated]
    measured = [row for row in rows if not row.usage_estimated]
    priced_rows = [
        row for row in counted if row.final_channel_model_id in prices
    ]
    models_priced = sum(1 for input_price, _cache in prices.values() if input_price > 0)

    report = {
        "window": {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_ms / 1000)),
            "end": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end_ms / 1000)),
        },
        "rows": {"total": len(rows), "counted": len(counted), "excluded": len(excluded)},
        "before_token_weighted": {
            "prompt_tokens": naive_prompt,
            "cached_tokens": naive_cached,
            "cache_hit_rate": round(naive_cached / naive_prompt, 6) if naive_prompt else 0.0,
        },
        "after": {
            "A_token_weighted": aggregates.cache_hit_rate(rows),
            "cohort_prompt_tokens": usage.prompt_tokens,
            "cohort_cached_tokens": usage.cached_tokens,
            "cohort_samples": aggregates.cache_hit_rate_samples(rows),
            "B_request_level": aggregates.cache_request_hit_rate(rows),
            "B_request_hits": aggregates.cache_request_hits(rows),
            "C_cacheable_ratio": cacheable.ratio,
            "C_cacheable_tokens": cacheable.tokens,
            "C_sessions": cacheable.sessions,
            "D_saved_tokens": usage.cached_tokens,
            "D_saved_usd": aggregates.cache_saved_usd(rows, prices),
        },
        "cohorts": {
            "measured": {
                "rows": len(measured),
                "prompt_tokens": sum(int(row.prompt_tokens or 0) for row in measured),
            },
            "interrupted": {
                "rows": len(interrupted),
                "prompt_tokens": sum(int(row.prompt_tokens or 0) for row in interrupted),
            },
            "excluded": {
                "rows": len(excluded),
                "prompt_tokens": sum(int(row.prompt_tokens or 0) for row in excluded),
            },
        },
        "prices": {
            "channel_models": len(prices),
            "channel_models_with_input_price": models_priced,
            "counted_rows_with_a_price": len(priced_rows),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
