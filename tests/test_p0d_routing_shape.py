"""P0-d: routing query shape (combo batch load, implicit-group point lookup).

These tests pin the behaviour the batched queries must preserve: the resolved
candidate set and its order, the disabled-member rules, and the untouched
401/404/503 semantics. Two statement-count tests keep the N+1 shape from
coming back silently: resolving a combo or a bare group must not scale its
statement count with the member count, and must not fall back to the two
full-table scans the group listing used to perform.

Everything here talks to the repository layer directly (no upstream calls):
the point is routing shape, not HTTP.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import event

from l01_core.errors import ErrorCode, OpenAIError
from l04_repo import orm
from l05_service import keys as keys_service
from l05_service import routing
from l05_service.groups import get_group, list_groups


class _StatementCounter:
    """Count SQL statements emitted on one engine (commits are not counted)."""

    def __init__(self, app: FastAPI) -> None:
        self._engine = app.state.db_engine.sync_engine
        self.statements: list[str] = []

    def _before_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(" ".join(statement.split()))

    def __enter__(self) -> "_StatementCounter":
        event.listen(self._engine, "before_cursor_execute", self._before_execute)
        return self

    def __exit__(self, *exc_info) -> bool:
        event.remove(self._engine, "before_cursor_execute", self._before_execute)
        return False

    @property
    def count(self) -> int:
        return len(self.statements)


def _app(client) -> FastAPI:
    return client._transport.app  # type: ignore[attr-defined]


async def _seed_channel(
    session,
    *,
    slug: str,
    priority: int = 1,
    enabled: bool = True,
    call_model: str = "greet",
    model_enabled: bool = True,
    protocol: str = "openai_chat",
) -> tuple[orm.ChannelORM, orm.ChannelModelORM]:
    channel = orm.ChannelORM(
        id=f"ch-{slug}",
        slug=slug,
        name=slug,
        connection_url="http://127.0.0.1:1/v1",
        protocol=protocol,
        enabled=enabled,
        priority=priority,
    )
    session.add(channel)
    await session.commit()  # FK: the channel must exist before its model
    model = orm.ChannelModelORM(
        id=f"cm-{slug}",
        channel_id=channel.id,
        upstream_model=f"up-{slug}",
        call_model=call_model,
        enabled=model_enabled,
    )
    session.add(model)
    await session.commit()
    return channel, model


async def _seed_combo(
    session,
    *,
    combo_id: str,
    name: str,
    targets: list[dict],
    enabled: bool = True,
    max_attempts: int = 3,
) -> orm.ComboORM:
    combo = orm.ComboORM(
        id=combo_id,
        name=name,
        description="",
        enabled=enabled,
        max_attempts=max_attempts,
    )
    session.add(combo)
    await session.commit()  # FK: the combo must exist before its targets
    for index, target in enumerate(targets, start=1):
        session.add(
            orm.ComboTargetORM(
                id=f"ct-{combo_id}-{index}",
                combo_id=combo_id,
                channel_model_id=target.get("channel_model_id"),
                child_combo_id=target.get("child_combo_id"),
                priority=target.get("priority", index),
                enabled=target.get("enabled", True),
            )
        )
    await session.commit()
    return combo


async def test_combo_resolution_keeps_member_set_and_order(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        _, model_a = await _seed_channel(session, slug="combo-a", priority=3)
        _, model_b = await _seed_channel(session, slug="combo-b", priority=1)
        _, model_c = await _seed_channel(session, slug="combo-c", priority=2)
        await _seed_combo(
            session,
            combo_id="combo-1",
            name="pc",
            targets=[
                {"channel_model_id": model_a.id, "priority": 1},
                {"channel_model_id": model_b.id, "priority": 2},
                {"channel_model_id": model_c.id, "priority": 3},
            ],
        )

    async with app.state.session_factory() as session:
        target = await routing.resolve_target(session, "pc")

    assert target.kind == "combo"
    assert target.origin == "manual"
    assert target.name == "pc"
    # 数值越大越先调用：显式组合成员按 combo_targets.priority 降序排列。
    assert [c.priority_path for c in target.candidates] == [(3,), (2,), (1,)]
    assert [c.channel.slug for c in target.candidates] == ["combo-c", "combo-b", "combo-a"]
    assert [c.model.call_model for c in target.candidates] == ["greet", "greet", "greet"]
    assert [c.member_enabled for c in target.candidates] == [True, True, True]


async def test_combo_keeps_disabled_member_out_of_runnable(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        _, model_on = await _seed_channel(session, slug="combo-on", priority=1)
        _, model_off = await _seed_channel(session, slug="combo-off", priority=2)
        await _seed_combo(
            session,
            combo_id="combo-mixed",
            name="mixed",
            targets=[
                {"channel_model_id": model_on.id, "priority": 1},
                {"channel_model_id": model_off.id, "priority": 2, "enabled": False},
            ],
        )

    async with app.state.session_factory() as session:
        target = await routing.resolve_target(session, "mixed")

    # A manually disabled member stays in the list so the attempt timeline can
    # record the skip, but it is never called.
    assert [c.member_enabled for c in target.candidates] == [False, True]
    assert [c.channel.slug for c in await routing.runnable_candidates(target)] == ["combo-on"]


async def test_combo_expands_child_combo_with_priority_path(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        _, leaf = await _seed_channel(session, slug="child-leaf", priority=1)
        await _seed_combo(
            session,
            combo_id="combo-child",
            name="child",
            targets=[{"channel_model_id": leaf.id, "priority": 1}],
        )
        await _seed_combo(
            session,
            combo_id="combo-parent",
            name="parent",
            targets=[{"child_combo_id": "combo-child", "priority": 1}],
        )

    async with app.state.session_factory() as session:
        target = await routing.resolve_target(session, "parent")

    assert target.kind == "combo"
    assert [c.priority_path for c in target.candidates] == [(1, 1)]
    assert [c.channel.slug for c in target.candidates] == ["child-leaf"]


async def test_bare_group_matches_full_listing(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        await _seed_channel(session, slug="greet-high", priority=3, call_model="greet")
        await _seed_channel(session, slug="greet-first", priority=1, call_model="greet")
        await _seed_channel(session, slug="greet-mid", priority=2, call_model="greet")
        # A disabled model never joins a group, and a slashed name is never one.
        await _seed_channel(
            session, slug="greet-off", priority=1, call_model="greet", model_enabled=False
        )
        await _seed_channel(session, slug="vendor", priority=1, call_model="vendor/model")

    async with app.state.session_factory() as session:
        group = await get_group(session, "greet")
        listed = {candidate.name: candidate for candidate in await list_groups(session)}
        assert group is not None
        assert "vendor/model" not in listed

    # 自动组合（隐式分组）成员按 channels.priority 降序：数值越大越先调用。
    assert [member.channel.slug for member in group.members] == [
        "greet-high",
        "greet-mid",
        "greet-first",
    ]
    assert group.member_count == 3
    assert group.created_at == listed["greet"].created_at
    assert [member.model.id for member in group.members] == [
        member.model.id for member in listed["greet"].members
    ]

    async with app.state.session_factory() as session:
        target = await routing.resolve_target(session, "greet")
        assert await routing.runnable_candidates(target) == list(target.candidates)

    assert target.kind == "combo"
    assert target.origin == "auto"
    assert [c.channel.slug for c in target.candidates] == [
        "greet-high",
        "greet-mid",
        "greet-first",
    ]


async def test_bare_group_keeps_disabled_channel_as_skipped_member(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        await _seed_channel(session, slug="grp-live", priority=1, call_model="hello")
        await _seed_channel(session, slug="grp-dark", priority=2, call_model="hello", enabled=False)

    async with app.state.session_factory() as session:
        group = await get_group(session, "hello")
        assert group is not None
        assert group.member_count == 2
        assert [member.available for member in group.members] == [False, True]
        target = await routing.resolve_target(session, "hello")

    assert [c.channel.slug for c in target.candidates] == ["grp-dark", "grp-live"]
    assert [c.channel.slug for c in await routing.runnable_candidates(target)] == ["grp-live"]


async def test_unknown_targets_keep_their_status_codes(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        _, model = await _seed_channel(session, slug="idle", priority=1, call_model="idle")
        await _seed_combo(
            session,
            combo_id="combo-disabled",
            name="off-combo",
            targets=[{"channel_model_id": model.id, "priority": 1}],
            enabled=False,
        )
        await _seed_combo(session, combo_id="combo-empty", name="empty-combo", targets=[])

    async with app.state.session_factory() as session:
        with pytest.raises(OpenAIError) as missing:
            await routing.resolve_target(session, "no-such-model")
        assert (missing.value.status, missing.value.code) == (404, ErrorCode.MODEL_NOT_FOUND)

        with pytest.raises(OpenAIError) as disabled:
            await routing.resolve_target(session, "off-combo")
        assert (disabled.value.status, disabled.value.code) == (404, ErrorCode.MODEL_NOT_FOUND)

        with pytest.raises(OpenAIError) as empty:
            await routing.resolve_target(session, "empty-combo")
        assert (empty.value.status, empty.value.code) == (503, ErrorCode.NO_AVAILABLE_CHANNEL)

        with pytest.raises(OpenAIError) as key:
            await keys_service.authenticate_api_key(session, "sk-not-a-real-key")
        assert (key.value.status, key.value.code) == (401, ErrorCode.INVALID_API_KEY)


async def test_combo_statement_count_does_not_grow_with_members(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        single = []
        for index in range(1, 2):
            _, model = await _seed_channel(session, slug=f"one-{index}", priority=index)
            single.append({"channel_model_id": model.id, "priority": index})
        await _seed_combo(session, combo_id="combo-one", name="one", targets=single)

        many = []
        for index in range(1, 6):
            _, model = await _seed_channel(session, slug=f"many-{index}", priority=index)
            many.append({"channel_model_id": model.id, "priority": index})
        await _seed_combo(session, combo_id="combo-many", name="many", targets=many)

    async with app.state.session_factory() as session:
        with _StatementCounter(app) as one:
            target = await routing.resolve_target(session, "one")
            for candidate in target.candidates:
                list(candidate.channel.keys)

    async with app.state.session_factory() as session:
        with _StatementCounter(app) as five:
            target = await routing.resolve_target(session, "many")
            for candidate in target.candidates:
                list(candidate.channel.keys)

    # Expected: combo lookup + one member join + one selectin for channel keys.
    assert one.count <= 4, one.statements
    assert five.count == one.count, five.statements
    assert not any("channel_models.id = ?" in statement for statement in five.statements)


async def test_bare_group_resolution_avoids_full_table_scans(client) -> None:
    app = _app(client)
    async with app.state.session_factory() as session:
        for index in range(1, 4):
            await _seed_channel(
                session, slug=f"scan-{index}", priority=index, call_model="scan-me"
            )

    async with app.state.session_factory() as session:
        with _StatementCounter(app) as counter:
            target = await routing.resolve_target(session, "scan-me")
            for candidate in target.candidates:
                list(candidate.channel.keys)

    # Expected: combo miss + one indexed model/channel join + one selectin.
    assert counter.count <= 4, counter.statements
    assert not any("FROM channels" in statement for statement in counter.statements)
    assert not any("ORDER BY channel_models.created_at" in statement for statement in counter.statements)
    assert [c.channel.slug for c in target.candidates] == ["scan-3", "scan-2", "scan-1"]
