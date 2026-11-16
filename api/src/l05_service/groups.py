from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.timeutil import now_ms
from l04_repo import orm, repo
from l05_service.channel_models import (
    is_model_enabled,
    is_published,
    model_parameters_from_json,
)

OPENAI_PROTOCOL = "openai_chat"


def is_bare_call_model(value: str | None) -> bool:
    """A call model participates in implicit grouping only when it has no slash."""
    text = (value or "").strip()
    return bool(text) and "/" not in text


@dataclass(frozen=True, slots=True)
class GroupMember:
    channel: orm.ChannelORM
    model: orm.ChannelModelORM

    @property
    def available(self) -> bool:
        return bool(
            self.channel.enabled
            # 定时封禁到期即视为启用：成员是否可用按 banned_until 惰性判活。
            and is_model_enabled(self.model, now_ms())
            and self.channel.protocol == OPENAI_PROTOCOL
        )

    @property
    def published(self) -> bool:
        """Whether the member still shows up in the public model directory.

        A cooling member stays published (clients keep seeing the name and are
        told when it comes back); only a permanent ban removes it (ADR-070 §3.2).
        """
        return bool(
            self.channel.enabled
            and is_published(self.model, now_ms())
            and self.channel.protocol == OPENAI_PROTOCOL
        )


@dataclass(frozen=True, slots=True)
class ImplicitGroup:
    name: str
    members: tuple[GroupMember, ...]

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def available_members(self) -> tuple[GroupMember, ...]:
        return tuple(member for member in self.members if member.available)

    @property
    def published_members(self) -> tuple[GroupMember, ...]:
        return tuple(member for member in self.members if member.published)

    @property
    def published(self) -> bool:
        """At least one member is still advertised; a cooling member counts."""
        return bool(self.published_members)

    @property
    def callable(self) -> bool:
        return bool(self.available_members)

    @property
    def created_at(self) -> int:
        return min(int(member.model.created_at or 0) for member in self.members)


async def _channels_by_id(session: AsyncSession) -> dict[str, orm.ChannelORM]:
    return {channel.id: channel for channel in await repo.list_channels(session)}


async def list_groups(
    session: AsyncSession, *, callable_only: bool = False
) -> list[ImplicitGroup]:
    """Derive read-only implicit groups from enabled channel-model rows.

    A group exists for every distinct bare ``call_model``; a single-member group
    is kept so clients can switch from a direct call to a group without renaming.
    Rows whose ``call_model`` contains ``/`` never join a group.

    Members are ordered by descending channel priority (1 = lowest, larger numbers
    are tried first), then channel slug and created_at for a stable order.
    """
    channels = await _channels_by_id(session)
    grouped: dict[str, list[GroupMember]] = {}
    for model in await repo.list_all_channel_models(session):
        if not is_bare_call_model(model.call_model):
            continue
        # 冷却中的行仍留在组里（available=False、published=True）：组名照样广播，
        # 成员到期自动复活；只有永久封禁（enabled=False 且无到期时刻）才剔除
        # （ADR-070 §3.2）。判据统一走 is_published，与目录侧同一套口径。
        if not is_published(model, now_ms()):
            continue
        channel = channels.get(model.channel_id)
        if channel is None:
            continue
        grouped.setdefault(model.call_model, []).append(GroupMember(channel=channel, model=model))

    groups: list[ImplicitGroup] = []
    for name in sorted(grouped):
        members = sorted(
            grouped[name],
            key=lambda member: (
                -int(member.channel.priority or 1),
                member.channel.slug,
                int(member.model.created_at or 0),
            ),
        )
        group = ImplicitGroup(name=name, members=tuple(members))
        if callable_only and not group.callable:
            continue
        groups.append(group)
    return groups


async def get_group(session: AsyncSession, name: str) -> ImplicitGroup | None:
    """One implicit group by exact bare name, without listing every group.

    ``list_groups`` scans all channels and channel models to derive every group;
    resolving a single name only needs the enabled rows that carry it, and
    ``channel_models.call_model`` is indexed. Members are ordered by the same key
    as ``list_groups`` so both paths return an identical group.
    """
    if not is_bare_call_model(name):
        return None
    members = [
        GroupMember(channel=channel, model=model)
        for model, channel in await repo.list_enabled_channel_models_by_call_model(
            session, name
        )
    ]
    if not members:
        return None
    members.sort(
        key=lambda member: (
            -int(member.channel.priority or 1),
            member.channel.slug,
            int(member.model.created_at or 0),
        )
    )
    return ImplicitGroup(name=name, members=tuple(members))


def serialize_member(member: GroupMember) -> dict[str, Any]:
    return {
        "channel_model_id": member.model.id,
        "channel_id": member.channel.id,
        "channel_slug": member.channel.slug,
        "channel_name": member.channel.name,
        "protocol": member.channel.protocol,
        "upstream_model": member.model.upstream_model,
        "call_model": member.model.call_model,
        "parameters": model_parameters_from_json(member.model.parameters_json),
        "priority": int(member.channel.priority or 1),
        "enabled": is_model_enabled(member.model, now_ms()),
        "channel_enabled": bool(member.channel.enabled),
        "available": member.available,
    }


def serialize_group(group: ImplicitGroup) -> dict[str, Any]:
    return {
        "name": group.name,
        "call_model": group.name,
        # 隐式分组属于二元 kind 里的组合：origin=auto 表示查询期派生、只读。
        "kind": "combo",
        "origin": "auto",
        "member_count": group.member_count,
        "available_count": len(group.available_members),
        "callable": group.callable,
        "created_at": group.created_at,
        "members": [serialize_member(member) for member in group.members],
    }
