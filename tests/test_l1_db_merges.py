"""Regression tests for the P0-a L1 DB round-trip merges.

Covers the semantics that the three merges must preserve:
  * authentication resolves the key and its owner with one joined SELECT (401 unchanged)
  * a direct target resolves channel+model with one joined SELECT (404 unchanged)
  * the request body upsert is idempotent: one row, last write wins, created_at kept

The statement counters mirror the E7a acceptance probe (before_cursor_execute).
"""
from __future__ import annotations

import pytest
from sqlalchemy import event, func, select

from l01_core.errors import ErrorCode, OpenAIError
from l02_infra.crypto import hash_api_key
from l04_repo import orm, repo
from l05_service import keys as keys_service, routing


class _StatementCounter:
    """Count statements executed by the app engine inside the block."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self.count = 0
        self.statements: list[str] = []

    def _on_execute(self, *args, **kwargs) -> None:
        self.count += 1
        statement = args[2] if len(args) > 2 else ""
        self.statements.append(" ".join(str(statement).split()))

    def __enter__(self) -> "_StatementCounter":
        event.listen(self._engine.sync_engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info) -> bool:
        event.remove(self._engine.sync_engine, "before_cursor_execute", self._on_execute)
        return False


async def _seed_user_and_key(session, *, enabled: bool = True) -> tuple[str, str]:
    user = orm.UserORM(id="u-l1", username="l1-user", display_name="l1")
    session.add(user)
    await session.commit()  # FK: the users row must exist before api_keys
    raw_key = "sk-l1-" + "a" * 24
    session.add(
        orm.ApiKeyORM(
            id="k-l1",
            user_id=user.id,
            label="l1",
            key_hash=hash_api_key(raw_key),
            enabled=enabled,
        )
    )
    await session.commit()
    return user.id, raw_key


async def _seed_channel(
    session,
    *,
    slug: str,
    call_model: str = "gpt-test",
    channel_enabled: bool = True,
    model_enabled: bool = True,
    protocol: str = "openai_chat",
) -> tuple[str, str]:
    channel = orm.ChannelORM(
        id=f"ch-{slug}",
        slug=slug,
        name=slug,
        connection_url="http://127.0.0.1:1/v1",
        protocol=protocol,
        enabled=channel_enabled,
        priority=1,
    )
    session.add(channel)
    await session.commit()  # FK: the channels row must exist before channel_models
    model = orm.ChannelModelORM(
        id=f"cm-{slug}",
        channel_id=channel.id,
        upstream_model="upstream-model",
        call_model=call_model,
        enabled=model_enabled,
    )
    session.add(model)
    await session.commit()
    return channel.id, model.id


async def test_authenticate_api_key_resolves_key_and_owner_in_two_statements(client, app):
    async with app.state.session_factory() as session:
        user_id, raw_key = await _seed_user_and_key(session)

    async with app.state.session_factory() as session:
        with _StatementCounter(app.state.db_engine) as counter:
            key, user = await keys_service.authenticate_api_key(session, raw_key)

    # one joined SELECT for key+owner, one UPDATE for last_used_at (cold key)
    assert counter.count == 2
    assert (key.id, user.id) == ("k-l1", user_id)
    assert key.last_used_at is not None


async def test_authenticate_api_key_keeps_401_for_unknown_and_disabled_keys(client, app):
    async with app.state.session_factory() as session:
        await _seed_user_and_key(session, enabled=False)

    async with app.state.session_factory() as session:
        with pytest.raises(OpenAIError) as unknown:
            await keys_service.authenticate_api_key(session, "sk-does-not-exist")
    assert (unknown.value.status, unknown.value.code) == (401, ErrorCode.INVALID_API_KEY)

    async with app.state.session_factory() as session:
        with pytest.raises(OpenAIError) as disabled:
            await keys_service.authenticate_api_key(session, "sk-l1-" + "a" * 24)
    assert (disabled.value.status, disabled.value.code) == (401, ErrorCode.INVALID_API_KEY)


async def test_direct_target_resolves_channel_and_model_in_one_statement(client, app):
    async with app.state.session_factory() as session:
        channel_id, model_id = await _seed_channel(session, slug="alpha")

    async with app.state.session_factory() as session:
        with _StatementCounter(app.state.db_engine) as counter:
            target = await routing.resolve_target(session, "alpha/gpt-test")

    # one joined SELECT for channel+model, plus the selectin load of channel_keys
    statements = [sql.lower() for sql in counter.statements]
    assert len(statements) == 2, statements
    assert sum("from channel_keys" in sql for sql in statements) == 1, statements
    assert sum("from channels" in sql and "channel_models" in sql for sql in statements) == 1, statements
    assert any(" join " in sql for sql in statements), statements
    assert target.kind == "direct"
    candidate = target.candidates[0]
    assert (candidate.channel.id, candidate.model.id) == (channel_id, model_id)
    assert candidate.priority == 1


async def test_direct_target_keeps_404_for_unknown_disabled_and_wrong_protocol(client, app):
    async with app.state.session_factory() as session:
        await _seed_channel(session, slug="beta", channel_enabled=False)
        await _seed_channel(session, slug="gamma", model_enabled=False)
        await _seed_channel(session, slug="delta", protocol="anthropic_chat")

    for requested, status, code in (
        ("nope/gpt-test", 404, ErrorCode.MODEL_NOT_FOUND),
        ("beta/gpt-test", 404, ErrorCode.MODEL_NOT_FOUND),
        ("gamma/gpt-test", 404, ErrorCode.MODEL_NOT_FOUND),
        ("delta/gpt-test", 400, ErrorCode.NOT_IMPLEMENTED),
    ):
        async with app.state.session_factory() as session:
            with pytest.raises(OpenAIError) as raised:
                await routing.resolve_target(session, requested)
        assert (raised.value.status, raised.value.code) == (status, code), requested


async def test_save_request_body_twice_keeps_one_row_last_write_and_first_created_at(client, app):
    async with app.state.session_factory() as session:
        user_id, _ = await _seed_user_and_key(session)
        await repo.add_request_log(
            session,
            request_id="req-l1-body",
            user_id=user_id,
            requested_model="alpha/gpt-test",
            http_status=200,
            prompt_tokens=1,
            completion_tokens=2,
            cost_usd=0.0,
        )

    async with app.state.session_factory() as session:
        await repo.save_request_body(session, request_id="req-l1-body", response_body='{"v":1}')
    async with app.state.session_factory() as session:
        first = await repo.get_request_body(session, "req-l1-body")
        assert first is not None and first.response_body == '{"v":1}'
        first_created = first.created_at

    async with app.state.session_factory() as session:
        await repo.save_request_body(session, request_id="req-l1-body", response_body='{"v":2}')
    async with app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(func.count())
                .select_from(orm.RequestBodyORM)
                .where(orm.RequestBodyORM.request_id == "req-l1-body")
            )
        ).scalar_one()
        second = await repo.get_request_body(session, "req-l1-body")

    assert rows == 1
    assert second is not None
    assert second.response_body == '{"v":2}'
    assert second.created_at == first_created
