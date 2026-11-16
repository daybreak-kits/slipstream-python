"""Reconcile the billing ledger against an upstream usage report.

Usage (from api):
    python scripts/reconcile_ledger.py --report usage.csv
    python scripts/reconcile_ledger.py --report usage.json --dry-run
    python scripts/reconcile_ledger.py                # self-check, no report

With a report the script compares ledger+adjustments per request_id, prints
the differences as JSON, then repairs them: a bill missing from the ledger is
inserted, a token or amount difference is appended as a reason=reconcile
adjustment. Without a report it prints the ledger-vs-request_logs coverage
self check instead.

Exit code is 1 when differences were found (reportable condition), 0 when the
ledger already matches.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from l01_core.config import get_settings  # noqa: E402
from l02_infra.db import build_engine, build_sessionmaker, init_db  # noqa: E402
from l05_service.ledger import reconcile  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile the billing ledger.")
    parser.add_argument("--report", type=Path, default=None, help="upstream usage CSV or JSON")
    parser.add_argument("--from-ms", type=int, default=0, help="range start, epoch ms inclusive")
    parser.add_argument("--to-ms", type=int, default=None, help="range end, epoch ms exclusive")
    parser.add_argument("--actor", default="", help="written to the adjustment actor column")
    parser.add_argument("--dry-run", action="store_true", help="report differences, do not repair")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    await init_db(engine)
    try:
        async with session_factory() as session:
            if args.report is None:
                report = await reconcile.self_check(
                    session, start_ms=args.from_ms, end_ms=args.to_ms
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0 if report["balanced"] else 1
            expected = reconcile.load_report(args.report)
            differences = await reconcile.compare_report(
                session, expected, start_ms=args.from_ms, end_ms=args.to_ms
            )
            print(
                json.dumps(
                    {"differences": [d.as_dict() for d in differences]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if not differences:
                return 0
            if not args.dry_run:
                repaired = await reconcile.apply_differences(
                    session, differences, actor=args.actor
                )
                print(json.dumps({"repaired_requests": repaired}, ensure_ascii=False))
            return 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())