from __future__ import annotations

from l01_core.timeutil import now_ms
from l04_repo import orm, repo
from l05_service import retention

DAY_MS = 86_400_000


async def _log_with_body(session, *, request_id: str, age_days: int):
    created_at = now_ms() - age_days * DAY_MS
    log = await repo.add_request_log(
        session,
        request_id=request_id,
        requested_model="gpt-test",
        created_at=created_at,
    )
    await repo.save_request_body(
        session,
        request_id=request_id,
        request_body="request",
        upstream_body="upstream",
        response_body="response",
        response_summary='{"delta_count": 1}',
        reasoning_content="reasoning",
    )
    row = await session.get(orm.RequestBodyORM, request_id)
    row.created_at = created_at
    await session.commit()
    return log


async def test_retention_nulls_bodies_older_than_seven_days_but_keeps_rows(
    client, app
):
    now = now_ms()
    async with app.state.session_factory() as session:
        old_log = await _log_with_body(session, request_id="req-old-body", age_days=8)
        await _log_with_body(session, request_id="req-fresh-body", age_days=6)

    async with app.state.session_factory() as session:
        changed = await retention.expire_body_text(session, retention_days=7, now=now)

    assert changed == 1
    async with app.state.session_factory() as session:
        old = await repo.get_request_body(session, "req-old-body")
        fresh = await repo.get_request_body(session, "req-fresh-body")
        surviving_log = await session.get(orm.RequestLogORM, old_log.id)
    assert old is not None and fresh is not None
    assert old.request_body is None
    assert old.upstream_body is None
    assert old.response_body is None
    assert old.response_summary is None
    assert old.reasoning_content is None
    assert fresh.request_body == "request"
    assert fresh.reasoning_content == "reasoning"
    # The metadata log survives body expiry.
    assert surviving_log is not None


async def test_purge_deletes_logs_and_bodies_older_than_thirty_days(client, app):
    now = now_ms()
    async with app.state.session_factory() as session:
        purged_log = await _log_with_body(session, request_id="req-purged", age_days=31)
        kept_log = await _log_with_body(session, request_id="req-kept", age_days=29)

    async with app.state.session_factory() as session:
        deleted = await retention.purge_log_metadata(session, now=now)

    assert deleted == 1
    async with app.state.session_factory() as session:
        assert await session.get(orm.RequestLogORM, purged_log.id) is None
        assert await repo.get_request_body(session, "req-purged") is None
        assert await session.get(orm.RequestLogORM, kept_log.id) is not None
        assert await repo.get_request_body(session, "req-kept") is not None


async def test_daily_retention_runs_both_rules_with_current_settings(client, app):
    now = now_ms()
    async with app.state.session_factory() as session:
        old_log = await _log_with_body(session, request_id="req-old", age_days=40)
        await _log_with_body(session, request_id="req-expired-body", age_days=8)
        await _log_with_body(session, request_id="req-new", age_days=1)

    result = await retention.run_daily_retention(app.state.session_factory, now=now)

    assert result["logs_purged"] == 1
    # Body expiry runs first, so both the 40-day and 8-day rows are cleared;
    # the 40-day row is then removed entirely by the metadata purge.
    assert result["bodies_expired"] == 2
    assert result["body_retention_days"] == 7
    assert result["log_retention_days"] == 30

    async with app.state.session_factory() as session:
        assert await session.get(orm.RequestLogORM, old_log.id) is None
        expired = await repo.get_request_body(session, "req-expired-body")
        fresh = await repo.get_request_body(session, "req-new")
    assert expired.request_body is None
    assert fresh.request_body == "request"
