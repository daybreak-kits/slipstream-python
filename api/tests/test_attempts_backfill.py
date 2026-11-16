"""``scripts/backfill_attempts.py``: resume, idempotency, damaged rows (ADR-072).

The backfill is the one step that touches every historical row, so the tests
pin the three properties the ADR promises: it resumes from its recorded cursor,
it can be re-run without duplicating a trail, and a row whose JSON is damaged is
counted and skipped instead of aborting the run.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backfill_attempts import backfill_attempts, reset_state  # noqa: E402
from l04_repo import orm  # noqa: E402

BASE_MS = 1_762_000_000_000
REQUESTS = 500
ATTEMPTS_PER_REQUEST = 2


def _payload(index: int) -> list[dict[str, Any]]:
    """Two hops per request, in the payload shape the request path writes."""
    return [
        {
            "channel_model_id": "cm-alpha",
            "channel_slug": "alpha",
            "channel_name": "Alpha",
            "upstream_model": "deepseek-v4-flash",
            "status": "error",
            "error_code": "upstream_timeout",
            "message": f"boom {index}",
            "ms": 5,
            "round": 1,
            "skipped": False,
            "priority_path": [1],
            "at": BASE_MS + index,
            "http_status": 502,
        },
        {
            "channel_model_id": "cm-beta",
            "channel_slug": "beta",
            "channel_name": "Beta",
            "upstream_model": "deepseek-v4-flash",
            "status": "success",
            "error_code": None,
            "message": "",
            "ms": 7,
            "round": 1,
            "skipped": False,
            "priority_path": [1],
            "at": BASE_MS + index + 1,
            "http_status": 200,
        },
    ]


async def _seed(client) -> None:
    """500 request rows with JSON trails, one of them damaged."""
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        for index in range(REQUESTS):
            attempts = _payload(index)
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"req-{index:04d}",
                    requested_model="combo-a",
                    http_status=200,
                    attempts_json=(
                        "{not json" if index == 0 else json.dumps(attempts)
                    ),
                    created_at=BASE_MS + index,
                )
            )
        await session.commit()


async def _attempt_rows(client) -> int:
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        return int(
            (await session.execute(select(func.count(orm.AttemptORM.id)))).scalar_one()
            or 0
        )


async def _run_until_finished(client, batch_size: int = 100) -> dict[str, Any]:
    """Run the backfill in batches the way an interrupted operator would."""
    factory = client._transport.app.state.session_factory
    totals: dict[str, Any] = {
        "runs": 0,
        "scanned": 0,
        "attempts": 0,
        "rows_written": 0,
        "bad_json": 0,
    }
    while True:
        stats = await backfill_attempts(factory, batch_size=batch_size)
        totals["runs"] += 1
        for key in ("scanned", "attempts", "rows_written", "bad_json"):
            totals[key] += stats[key]
        if stats["finished"]:
            return totals
        assert totals["runs"] < 50, "the cursor never advanced"


async def test_backfill_copies_every_trail_and_resumes(client) -> None:
    await _seed(client)
    first = await backfill_attempts(
        client._transport.app.state.session_factory, batch_size=100, limit=200
    )
    # Stopping at the limit leaves a cursor behind, not a finished run.
    assert first["finished"] is False
    assert first["cursor"] is not None
    assert first["scanned"] == 200

    rest = await _run_until_finished(client, batch_size=100)
    assert rest["scanned"] == REQUESTS - 200
    # The damaged row contributes no trail and is counted exactly once, in
    # whichever run reached it.
    assert first["bad_json"] + rest["bad_json"] == 1
    expected = (REQUESTS - 1) * ATTEMPTS_PER_REQUEST
    assert await _attempt_rows(client) == expected


async def test_backfill_is_idempotent(client) -> None:
    await _seed(client)
    await _run_until_finished(client)
    expected = (REQUESTS - 1) * ATTEMPTS_PER_REQUEST
    assert await _attempt_rows(client) == expected

    # Forget the cursor and replay the whole history: ON CONFLICT keeps it at
    # the same row count instead of doubling it.
    async with client._transport.app.state.session_factory() as session:
        await reset_state(session)
    again = await _run_until_finished(client)
    assert again["scanned"] == REQUESTS
    assert await _attempt_rows(client) == expected


async def test_backfill_dry_run_writes_nothing(client) -> None:
    await _seed(client)
    stats = await backfill_attempts(
        client._transport.app.state.session_factory, batch_size=100, dry_run=True
    )
    assert stats["rows_written"] == REQUESTS - 1
    assert stats["bad_json"] == 1
    assert await _attempt_rows(client) == 0
