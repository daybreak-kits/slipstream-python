"""Tests for :mod:`l04_repo.log_query`.

The fixture is a seven-row operation log built so that each ``model`` arm has
exactly one row only it can reach:

===========  ====================================================
row          why it exists
===========  ====================================================
``req-r1``     plain ``requested_model`` hit (also 12 tokens, 200)
``req-r2``     served by combo ``basis``, upstream is its own model
``req-r3``     combo name as requested model, winner is the member model
``req-r4``     unrelated ``gpt-4o``
``req-r5``     lower-case twin of ``req-r1`` (case-insensitive arms)
``req-r6``     only the attempt trail records the model; channel ch-z
``req-r7``     ``attempts_json`` is not JSON at all
===========  ====================================================
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from l02_infra.db import Base
from l03_domain.query_lang import (
    DEFAULT_SEARCH_COLUMNS,
    FIELDS,
    AttemptMatch,
    QueryError,
    parse,
)
from l04_repo import repo
from l04_repo.log_query import (
    LOG_COLUMNS,
    SqliteAttemptResolver,
    attempt_resolver,
    attempt_rows,
    compile_log_filter,
    load_combo_graph,
    model_match,
)
from l04_repo.orm import ChannelModelORM, ComboORM, ComboTargetORM, RequestLogORM

MODEL = "MiniMax-M3.1-Flash"
BASIS = "combo-basis"
OUTER_ID = "combo-outer"

_ATTEMPT_TRAIL = (
    '[{"channel_name": "ch-z", "channel_slug": "slug-z", "channel_model_id": "cm-z", '
    '"upstream_model": "MiniMax-M3.1-Flash", "error_code": "BOOM", "message": "boom"}]'
)


def _log(
    log_id: str,
    *,
    requested: str,
    upstream: str = "",
    resolved: str | None = None,
    channel: str = "",
    channel_model: str | None = None,
    attempts: str = "[]",
    status: int = 200,
    error_code: str | None = None,
    message: str = "",
    total_ms: int = 0,
    prompt: int = 0,
    completion: int = 0,
) -> RequestLogORM:
    return RequestLogORM(
        id="log-" + log_id,
        request_id="req-" + log_id,
        requested_model=requested,
        upstream_model=upstream,
        response_model=upstream or requested,
        resolved_combo_id=resolved,
        channel_name=channel,
        final_channel_model_id=channel_model,
        attempts_json=attempts,
        http_status=status,
        error_code=error_code,
        error_message=message,
        total_ms=total_ms,
        prompt_tokens=prompt,
        completion_tokens=completion,
        created_at=1_760_000_000_000,
        api_key_label="key-a",
        user_id="u-1",
    )


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            ChannelModelORM(id="cm-mmx", channel_id="ch-mmx", upstream_model=MODEL, call_model=MODEL),
            ChannelModelORM(
                id="cm-basis",
                channel_id="ch-basis",
                upstream_model="basis-member",
                call_model="basis-member",
            ),
            ChannelModelORM(id="cm-z", channel_id="ch-z", upstream_model="z-model", call_model="z-model"),
            ComboORM(id=BASIS, name="basis"),
            ComboTargetORM(
                id="ct-mmx",
                combo_id=BASIS,
                target_type="channel_model",
                channel_model_id="cm-mmx",
            ),
            ComboTargetORM(
                id="ct-basis",
                combo_id=BASIS,
                target_type="channel_model",
                channel_model_id="cm-basis",
            ),
        ]
    )
    session.add_all(
        [
            _log("r1", requested=MODEL, upstream=MODEL, channel="ch-a", prompt=8, completion=4, total_ms=1200),
            _log("r2", requested="basis", upstream="basis-winner", resolved=BASIS, channel="ch-basis", prompt=2, completion=1),
            _log("r3", requested="basis", upstream=MODEL, channel="ch-basis", status=502, prompt=3, completion=2),
            _log("r4", requested="gpt-4o", upstream="gpt-4o", channel="ch-gpt", prompt=1),
            _log("r5", requested="minimax-m3.1-flash", upstream="minimax-m3.1-flash", channel="ch-a", status=201, prompt=2),
            _log(
                "r6",
                requested="other-2",
                upstream="other-2",
                channel="ch-z",
                channel_model="cm-z",
                attempts=_ATTEMPT_TRAIL,
                status=500,
                error_code="BOOM",
                message="boom",
            ),
            _log("r7", requested="gpt-4o-mini", upstream="gpt-4o-mini", attempts="not-json", prompt=7),
        ]
    )
    await session.commit()


@pytest.fixture
async def log_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await _seed(session)
        yield session
    await engine.dispose()


async def _ids(session: AsyncSession, expression) -> list[str]:
    statement = select(RequestLogORM.request_id).where(expression).order_by(RequestLogORM.request_id)
    return [request_id for request_id in (await session.execute(statement)).scalars().all()]


async def _query(session: AsyncSession, text: str, *, resolver: bool = True) -> list[str]:
    attempts = SqliteAttemptResolver() if resolver else None
    expression = await compile_log_filter(session, parse(text), attempts=attempts)
    return await _ids(session, expression)


# --------------------------------------------------------------------------- #
# The column mapping
# --------------------------------------------------------------------------- #


def test_columns_cover_every_field_and_search_column() -> None:
    for field in FIELDS.values():
        assert field.column in LOG_COLUMNS, field.column
        for extra in field.extra_columns:
            assert extra in LOG_COLUMNS, extra
    for name in DEFAULT_SEARCH_COLUMNS:
        assert name in LOG_COLUMNS, name


def test_tokens_column_is_the_sum_not_a_column() -> None:
    expression = LOG_COLUMNS["tokens_total"]
    rendered = str(expression)
    assert "prompt_tokens" in rendered and "completion_tokens" in rendered
    assert not hasattr(RequestLogORM, "tokens_total")


# --------------------------------------------------------------------------- #
# model: requested, upstream, combo member
# --------------------------------------------------------------------------- #


async def test_model_equals_matches_requested_upstream_and_combo_member(log_session: AsyncSession) -> None:
    assert await _query(log_session, 'model=="' + MODEL + '"') == [
        "req-r1",
        "req-r2",
        "req-r3",
        "req-r5",
        "req-r6",
    ]


async def test_model_equals_ignores_case(log_session: AsyncSession) -> None:
    lowered = MODEL.lower()
    assert await _query(log_session, 'model=="' + lowered + '"') == [
        "req-r1",
        "req-r2",
        "req-r3",
        "req-r5",
        "req-r6",
    ]


async def test_model_contains_matches_partial_member(log_session: AsyncSession) -> None:
    assert await _query(log_session, "model=flash") == [
        "req-r1",
        "req-r2",
        "req-r3",
        "req-r5",
        "req-r6",
    ]


async def test_model_not_equal_excludes_every_arm(log_session: AsyncSession) -> None:
    assert await _query(log_session, "model!=" + MODEL) == ["req-r4", "req-r7"]


async def test_model_membership_survives_a_missing_resolved_combo(log_session: AsyncSession) -> None:
    expression = await model_match(log_session, op="==", value=MODEL)
    rows = await _ids(log_session, expression)
    assert "req-r3" in rows  # no resolved_combo_id, only the combo name
    assert "req-r2" in rows  # resolved_combo_id only, upstream is another model


# --------------------------------------------------------------------------- #
# attempt trail
# --------------------------------------------------------------------------- #


async def test_upstream_predicate_reaches_the_attempt_trail(log_session: AsyncSession) -> None:
    assert await _query(log_session, "upstream==" + MODEL) == ["req-r1", "req-r3", "req-r5", "req-r6"]


async def test_attempt_rows_tolerate_malformed_json(log_session: AsyncSession) -> None:
    rows = attempt_rows()
    expression = (
        select(literal(1))
        .select_from(rows)
        .where(func.json_extract(rows.c.value, "$.upstream_model") == MODEL)
        .exists()
    )
    # req-r7 is not JSON at all; the CASE guard makes json_each ignore it
    # instead of raising "malformed JSON" for the whole query.
    assert await _ids(log_session, expression) == ["req-r6"]


async def test_attempt_resolver_degrades_off_sqlite() -> None:
    match = AttemptMatch(paths=("$.upstream_model",), op="==", value=MODEL, field="upstream")
    assert SqliteAttemptResolver().matches(match) is not None
    assert SqliteAttemptResolver("postgresql").matches(match) is None
    assert attempt_resolver("sqlite+aiosqlite") is not None
    assert attempt_resolver("postgresql") is None


async def test_postgres_degrade_keeps_the_request_columns(log_session: AsyncSession) -> None:
    attempts = SqliteAttemptResolver("postgresql")
    expression = await compile_log_filter(log_session, parse("upstream==" + MODEL), attempts=attempts)
    assert "json_each" not in str(expression)
    assert await _ids(log_session, expression) == ["req-r1", "req-r3", "req-r5"]


# --------------------------------------------------------------------------- #
# the rest of the field table over the real table
# --------------------------------------------------------------------------- #


async def test_channel_matches_the_channel_name(log_session: AsyncSession) -> None:
    assert await _query(log_session, "channel==ch-z") == ["req-r6"]


async def test_msg_matches_the_error_text(log_session: AsyncSession) -> None:
    assert await _query(log_session, "msg=boom") == ["req-r6"]


async def test_tokens_compares_the_sum(log_session: AsyncSession) -> None:
    assert await _query(log_session, "tokens>10") == ["req-r1"]


async def test_status_ranges(log_session: AsyncSession) -> None:
    assert await _query(log_session, "status>=500") == ["req-r3", "req-r6"]
    assert await _query(log_session, "status<400") == [
        "req-r1",
        "req-r2",
        "req-r4",
        "req-r5",
        "req-r7",
    ]


async def test_boolean_tree_with_negation(log_session: AsyncSession) -> None:
    assert await _query(log_session, '!(model=="gpt-4o") && status<400') == [
        "req-r1",
        "req-r2",
        "req-r5",
        "req-r7",
    ]


async def test_bareword_keeps_the_keyword_search(log_session: AsyncSession) -> None:
    assert await _query(log_session, "basis") == ["req-r2", "req-r3"]


async def test_values_stay_bound_parameters(log_session: AsyncSession) -> None:
    expression = await compile_log_filter(log_session, parse("msg=boom"), attempts=SqliteAttemptResolver())
    compiled = expression.compile()
    assert "boom" not in str(compiled)
    assert any("boom" in str(value) for value in compiled.params.values())


async def test_type_errors_keep_the_query_error_contract(log_session: AsyncSession) -> None:
    with pytest.raises(QueryError) as caught:
        await compile_log_filter(log_session, parse("latency<abc"))
    assert caught.value.details["column"] > 0


# --------------------------------------------------------------------------- #
# the combo graph
# --------------------------------------------------------------------------- #


async def test_combo_graph_expands_nested_combos(log_session: AsyncSession) -> None:
    log_session.add_all(
        [
            ComboORM(id=OUTER_ID, name="outer"),
            ComboTargetORM(id="ct-outer", combo_id=OUTER_ID, target_type="combo", child_combo_id=BASIS),
        ]
    )
    await log_session.commit()
    graph = await load_combo_graph(log_session)
    assert set(graph.combos_with_member(MODEL, "==")) == {BASIS, OUTER_ID}
    assert graph.members[BASIS] == ("basis-member", MODEL.lower())
    assert await _query(log_session, 'model=="' + MODEL + '"') == [
        "req-r1",
        "req-r2",
        "req-r3",
        "req-r5",
        "req-r6",
    ]


async def test_combo_graph_keeps_disabled_combos(log_session: AsyncSession) -> None:
    log_session.add(ComboORM(id="combo-off", name="off", enabled=False))
    log_session.add(
        ComboTargetORM(id="ct-off", combo_id="combo-off", target_type="channel_model", channel_model_id="cm-mmx")
    )
    await log_session.commit()
    graph = await load_combo_graph(log_session)
    assert "combo-off" in graph.combos_with_member(MODEL, "==")


# --------------------------------------------------------------------------- #
# repo.list_logs: the same widening through the plain `model=` parameter
# --------------------------------------------------------------------------- #


async def test_repo_list_logs_widens_model_to_combo_members(log_session: AsyncSession) -> None:
    """The reported bug: a member model's filter hid the combo's own rows."""
    rows, total = await repo.list_logs(log_session, requested_model=MODEL, page_size=50)
    ids = sorted(row.request_id for row in rows)
    assert ids == ["req-r1", "req-r2", "req-r3", "req-r5", "req-r6"]
    assert total == 5
    # Widen-only guarantee: the pre-fix exact match is a subset, not a shift.
    exact = await _ids(log_session, RequestLogORM.requested_model == MODEL)
    assert set(exact) <= set(ids)


async def test_repo_list_logs_folds_model_case(log_session: AsyncSession) -> None:
    """Same filter either spelling -- the arms fold, not the raw columns."""
    upper, _ = await repo.list_logs(log_session, requested_model=MODEL, page_size=50)
    lower, total = await repo.list_logs(
        log_session, requested_model=MODEL.lower(), page_size=50
    )
    assert sorted(row.request_id for row in lower) == sorted(
        row.request_id for row in upper
    )
    assert total == 5


async def test_repo_list_logs_keeps_the_drilldown_pin_exact(log_session: AsyncSession) -> None:
    """A segment drill-down pins one combo name; it is never widened."""
    rows, total = await repo.list_logs(log_session, model_exact="basis", page_size=50)
    assert sorted(row.request_id for row in rows) == ["req-r2", "req-r3"]
    assert total == 2
