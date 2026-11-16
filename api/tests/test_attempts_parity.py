"""ADR-072 parity: the JSON trail and the attempts rows answer the same question.

Each test seeds both representations at once (what the stage-1 dual write does),
reads the same view through ``ATTEMPTS_SOURCE=json`` and ``ATTEMPTS_SOURCE=
attempts``, and compares the result: a row array is only an acceptable storage
change if every existing reader cannot tell the difference.

The six named scenarios are the representative read paths from the task: a
substring search inside an attempt message, an exact error-code filter, the
combo roll-up, the status-segment drill-down, the P95 of a segment, and the
failure distribution. One extra test per switch combination pins which shape
each ``ATTEMPTS_WRITE``/``ATTEMPTS_SOURCE`` pair writes and reads.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from l01_core.config import Settings
from l04_repo import attempt_filters, orm, repo
from l05_service import log_views

BASE_MS = 1_762_000_000_000
MINUTE = 60_000


def _attempt(
    model: str = "deepseek-v4-flash",
    status: str = "success",
    *,
    error_code: str | None = None,
    channel: str = "Alpha",
    ms: int = 10,
    message: str = "",
    at: int | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    """One hop payload in the exact shape ``chat._attempt`` builds."""
    attempt: dict[str, Any] = {
        "channel_model_id": f"cm-{channel.lower()}",
        "channel_slug": channel.lower(),
        "channel_name": channel,
        "upstream_model": model,
        "status": status,
        "error_code": error_code,
        "message": message,
        "ms": ms,
        "round": 1,
        "skipped": status == "skipped",
        "priority_path": [1],
    }
    if at is not None:
        attempt["at"] = at
    if http_status is not None:
        attempt["http_status"] = http_status
    return attempt


def _dataset() -> list[dict[str, Any]]:
    """Six rows that exercise every scenario once.

    req-substring is the only row whose *attempt message* carries the search
    needle; req-limited and req-substring are the two failure shapes; the two
    combo-a rows make the roll-up non-trivial.
    """
    return [
        {
            "request_id": "req-ok",
            "requested_model": "combo-a",
            "created_at": BASE_MS,
            "attempts": [_attempt(at=BASE_MS, http_status=200)],
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "ttft_ms": 30,
            "total_ms": 100,
        },
        {
            "request_id": "req-substring",
            "requested_model": "combo-a",
            "created_at": BASE_MS + MINUTE,
            "http_status": 502,
            "error_code": "upstream_timeout",
            "prompt_tokens": 20,
            "completion_tokens": 7,
            "ttft_ms": 120,
            "total_ms": 700,
            "attempts": [
                _attempt(
                    status="error",
                    error_code="upstream_timeout",
                    channel="Alpha",
                    message="upstream said deadbeef then died",
                    at=BASE_MS + MINUTE,
                    http_status=502,
                ),
                _attempt(
                    channel="Beta",
                    at=BASE_MS + MINUTE + 5,
                    http_status=200,
                ),
            ],
        },
        {
            "request_id": "req-latency",
            "requested_model": "combo-b",
            "created_at": BASE_MS + 2 * MINUTE,
            "attempts": [_attempt(channel="Beta", at=BASE_MS + 2 * MINUTE, http_status=200)],
            "prompt_tokens": 30,
            "completion_tokens": 9,
            "ttft_ms": 40,
            "total_ms": 2500,
        },
        {
            "request_id": "req-limited",
            "requested_model": "combo-c",
            "created_at": BASE_MS + 3 * MINUTE,
            "http_status": 429,
            "error_code": "rate_limited",
            "attempts": [
                _attempt(
                    status="error",
                    error_code="rate_limited",
                    channel="Beta",
                    message="too many requests",
                    at=BASE_MS + 3 * MINUTE,
                    http_status=429,
                )
            ],
            "total_ms": 90,
        },
        {
            "request_id": "req-empty",
            "requested_model": "combo-b",
            "created_at": BASE_MS + 4 * MINUTE,
            "attempts": [],
        },
    ]


async def _seed(client, rows: list[dict[str, Any]]) -> None:
    """Insert each row plus its trail: the dual-write state, seeded directly."""
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        for index, row in enumerate(rows):
            log_id = str(uuid.uuid4())
            attempts = row.get("attempts", [])
            session.add(
                orm.RequestLogORM(
                    id=log_id,
                    request_id=row.get("request_id") or f"req-{index:03d}",
                    requested_model=row.get("requested_model", "model-a"),
                    channel_name=row.get("channel_name", "Alpha"),
                    upstream_model=row.get("upstream_model", "deepseek-v4-flash"),
                    http_status=row.get("http_status", 200),
                    error_code=row.get("error_code"),
                    prompt_tokens=row.get("prompt_tokens", 0),
                    completion_tokens=row.get("completion_tokens", 0),
                    cached_tokens=row.get("cached_tokens", 0),
                    cost_usd=row.get("cost_usd", 0.0),
                    ttft_ms=row.get("ttft_ms"),
                    total_ms=row.get("total_ms", 0),
                    attempts_json=row.get("attempts_json", json.dumps(attempts)),
                    created_at=row.get("created_at", BASE_MS + index * MINUTE),
                )
            )
            await session.flush()
            if attempts:
                await attempt_filters.insert_attempts(session, log_id, attempts)
        await session.commit()


def _use_modes(
    monkeypatch, client, *, write: str = "both", source: str = "json"
) -> None:
    """Point the repository at one switch combination for this test."""
    app = client._transport.app
    settings = app.state.settings.model_copy(
        update={"attempts_write": write, "attempts_source": source}
    )
    monkeypatch.setattr(attempt_filters, "_settings", lambda: settings)


async def _snapshot(client) -> dict[str, Any]:
    """Every read path the parity scenarios need, in one comparable dict."""
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        scan, truncated = await repo.scan_request_log_facts(session, limit=500)
        page, _ = await repo.list_logs(session, page_size=50)
        search, _ = await repo.list_logs(session, page_size=50, search="deadbeef")
        error_rows, _ = await repo.list_logs_for_segment(
            session,
            page_size=50,
            error_kinds=["upstream_timeout"],
            start_ms=BASE_MS,
            end_ms=BASE_MS + 10 * MINUTE,
        )
        drill_rows, _ = await repo.list_logs_for_segment(
            session,
            page_size=50,
            upstream_model="deepseek-v4-flash",
            status_code=502,
            start_ms=BASE_MS,
            end_ms=BASE_MS + 10 * MINUTE,
        )
        combo, _, _ = await log_views.compute_groups(
            session, view="combo", page=1, page_size=50, filters={}
        )
        status, _, _ = await log_views.compute_groups(
            session, view="status", page=1, page_size=50, filters={}
        )
        return {
            "page": [log.id for log in page],
            "scan": [
                (row["id"], attempt_filters.attempts_from_json(row["attempts_json"]))
                for row in scan
            ],
            "truncated": truncated,
            "search": [log.id for log in search],
            "error_rows": [log.id for log in error_rows],
            "drill_rows": [log.id for log in drill_rows],
            "combo": combo,
            "status": status,
        }


SCENARIOS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "search-substring": lambda snap: snap["search"],
    "error-code-exact": lambda snap: snap["error_rows"],
    "rollup-combo": lambda snap: snap["combo"],
    "drilldown-segment": lambda snap: snap["drill_rows"],
    "latency-p95": lambda snap: [
        (group["label"], group["total_p95_ms"]) for group in snap["status"]
    ],
    "failure-distribution": lambda snap: [
        (group["label"], group["status_label"], group["requests"], group["errors"])
        for group in snap["status"]
    ],
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
async def test_json_and_rows_sources_are_equivalent(client, monkeypatch, scenario: str) -> None:
    await _seed(client, _dataset())
    _use_modes(monkeypatch, client, source="json")
    json_snapshot = await _snapshot(client)
    _use_modes(monkeypatch, client, source="attempts")
    rows_snapshot = await _snapshot(client)

    # The whole snapshot, not just the named slice: any divergence in another
    # reader would mean the two sources already answered differently.
    assert json_snapshot == rows_snapshot
    value = SCENARIOS[scenario](rows_snapshot)
    assert value, f"{scenario} matched nothing, the parity check would be vacuous"


@pytest.mark.parametrize(
    "write,source,expected_rows,keeps_json",
    [
        ("json", "json", 0, True),
        ("json", "attempts", 0, True),
        ("rows", "json", 2, False),
        ("rows", "attempts", 2, False),
        ("both", "json", 2, True),
        ("both", "attempts", 2, True),
    ],
)
async def test_write_read_switch_matrix(
    client, monkeypatch, write: str, source: str, expected_rows: int, keeps_json: bool
) -> None:
    """Every ATTEMPTS_WRITE/ATTEMPTS_SOURCE pair writes and reads as documented."""
    _use_modes(monkeypatch, client, write=write, source=source)
    factory = client._transport.app.state.session_factory
    payload = [_attempt(at=BASE_MS, http_status=200), _attempt(channel="Beta", at=BASE_MS + 1)]
    async with factory() as session:
        log = await repo.add_request_log(
            session,
            request_id=f"req-{write}-{source}",
            requested_model="combo-a",
            created_at=BASE_MS,
            attempts_json=json.dumps(payload),
        )
        log_id = log.id
    async with factory() as session:
        trails = (
            await session.execute(
                select(orm.AttemptORM)
                .where(orm.AttemptORM.log_id == log_id)
                .order_by(orm.AttemptORM.seq)
            )
        ).scalars().all()
        stored = (
            await session.execute(
                select(orm.RequestLogORM.attempts_json).where(
                    orm.RequestLogORM.id == log_id
                )
            )
        ).scalar_one()
    assert len(trails) == expected_rows
    assert (stored != "[]") is keeps_json
    if expected_rows:
        assert [attempt_filters.row_to_payload(row) for row in trails] == payload


async def test_repeated_writes_do_not_duplicate_the_trail(client, monkeypatch) -> None:
    """A replayed insert and a re-delivered patch both stay idempotent."""
    _use_modes(monkeypatch, client, write="rows", source="attempts")
    factory = client._transport.app.state.session_factory
    payload = [_attempt(at=BASE_MS), _attempt(channel="Beta", at=BASE_MS + 1)]
    async with factory() as session:
        log = await repo.add_request_log(
            session,
            request_id="req-replay",
            requested_model="combo-a",
            created_at=BASE_MS,
            attempts_json=json.dumps(payload),
        )
        log_id = log.id
    async with factory() as session:
        # Same batch again, as a WAL replay would deliver it.
        await attempt_filters.insert_attempts(session, log_id, payload)
        await session.commit()
        count = len(
            (
                await session.execute(
                    select(orm.AttemptORM.id).where(orm.AttemptORM.log_id == log_id)
                )
            )
            .scalars()
            .all()
        )
        assert count == 2

    async with factory() as session:
        # A deferred patch delivered twice must not double the trail either.
        await attempt_filters.replace_attempts(session, log_id, payload[:1])
        await session.commit()
        await attempt_filters.replace_attempts(session, log_id, payload[:1])
        await session.commit()
        rows = (
            await session.execute(
                select(orm.AttemptORM)
                .where(orm.AttemptORM.log_id == log_id)
                .order_by(orm.AttemptORM.seq)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].seq == 0


def test_invalid_switch_values_raise_at_startup() -> None:
    """A typo fails when the settings are built, not inside the first request.

    N-10: the properties are read lazily by the repository, so before the
    settings validator existed a bad ATTEMPTS_WRITE surfaced as a 500 from the
    first log write. Boot-time validation is the only place a deployment typo
    can be reported without a request.
    """
    with pytest.raises(ValidationError):
        Settings(attempts_write="sometimes")
    with pytest.raises(ValidationError):
        Settings(attempts_source="maybe")
    # The properties still validate on their own: defence in depth for a caller
    # that mutates a live settings object.
    settings = Settings()
    settings.attempts_write = "bogus"
    with pytest.raises(ValueError):
        settings.attempts_write_mode
    settings = Settings()
    settings.attempts_source = "bogus"
    with pytest.raises(ValueError):
        settings.attempts_read_mode
    assert Settings().attempts_write_mode == "json"
    assert Settings().attempts_read_mode == "json"
    assert Settings(attempts_write="both").attempts_write_mode == "both"
    assert Settings(attempts_write="new").attempts_write_mode == "rows"
    assert Settings(attempts_source="rows").attempts_read_mode == "attempts"


async def test_empty_database_reads_empty_in_both_sources(client, monkeypatch) -> None:
    _use_modes(monkeypatch, client, source="attempts")
    rows_snapshot = await _snapshot(client)
    _use_modes(monkeypatch, client, source="json")
    json_snapshot = await _snapshot(client)
    assert rows_snapshot == json_snapshot
    assert rows_snapshot["page"] == []
    assert rows_snapshot["scan"] == []
    assert rows_snapshot["combo"] == []
    assert rows_snapshot["status"] == []


async def test_malformed_attempts_json_is_tolerated(client, monkeypatch) -> None:
    """A damaged blob must not break either source or either view."""
    await _seed(
        client,
        [
            {
                "request_id": "req-damaged",
                "requested_model": "combo-a",
                "created_at": BASE_MS,
                "attempts_json": "{not json",
                "http_status": 500,
            }
        ],
    )
    _use_modes(monkeypatch, client, source="json")
    json_snapshot = await _snapshot(client)
    _use_modes(monkeypatch, client, source="attempts")
    rows_snapshot = await _snapshot(client)
    assert json_snapshot == rows_snapshot
    assert json_snapshot["scan"][0][1] == []
    assert json_snapshot["status"] == []
