"""Filter-bar values for the operation log (ADR-071 wiring, step 2).

The bar used to read the log table alone, which is why it came up empty on a
fresh install and why the model list missed everything a combo served under its
own name. Two sources instead:

* the catalog (channel_models.upstream_model and combos.name) so the bar offers
  the models that exist before any request has been made;
* the last 30 days of request_logs (requested_model and upstream_model) so it
  offers what was really served, including a combo member that won a fail-over.

Values are de-duplicated case-insensitively (str.lower) keeping the spelling
seen first -- catalog before log -- which is the same folding the aggregation
axes use.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.textfold import fold_key
from l01_core.timeutil import now_ms
from l04_repo import orm
from l04_repo.search import like_contains

#: Same cap the endpoint has always had; the 51st row is only a truncation flag.
FILTER_LIMIT = 50
#: How far back "what the log actually served" reaches for the model list.
OBSERVED_WINDOW_MS = 30 * 24 * 60 * 60 * 1000
#: Headroom over the cap: case folding can shrink the raw value list.
_RAW_LIMIT = FILTER_LIMIT * 4


def fold_values(values: Iterable[str | None]) -> list[str]:
    """De-duplicate case-insensitively, keep the first spelling, then sort.

    The fold is ```fold_key``` (ASCII only, = SQLite ```lower()```), not
    ```str.lower()``` -- the candidate list must agree with the ```model=```
    predicate and the aggregate buckets (ADR-071 decision 14).
    """
    seen: dict[str, str] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            seen.setdefault(fold_key(text), text)
    return sorted(seen.values(), key=fold_key)


async def _observed(
    session: AsyncSession,
    column: Any,
    *,
    needle: str,
    since_ms: int | None = None,
) -> list[str]:
    conditions: list[Any] = [column.is_not(None), column != ""]
    if since_ms is not None:
        conditions.append(orm.RequestLogORM.created_at >= since_ms)
    if needle:
        conditions.append(like_contains(column, needle))
    statement = select(column).where(*conditions).distinct().limit(_RAW_LIMIT)
    return [
        str(value)
        for value in (await session.execute(statement)).scalars().all()
        if value
    ]


async def _catalog_names(session: AsyncSession) -> list[str]:
    names: list[str] = []
    for statement in (
        select(orm.ChannelModelORM.upstream_model).distinct(),
        select(orm.ComboORM.name).distinct(),
    ):
        names.extend(
            str(value)
            for value in (await session.execute(statement)).scalars().all()
            if value
        )
    return names


def _capped(values: list[str], needle: str) -> tuple[list[str], bool]:
    if needle:
        values = [value for value in values if needle in value.lower()]
    return values[:FILTER_LIMIT], len(values) > FILTER_LIMIT


async def model_options(session: AsyncSession, *, q: str = "") -> tuple[list[str], bool]:
    """Models the bar may offer: catalog spelling first, then what was served."""
    needle = q.strip().lower()
    since = now_ms() - OBSERVED_WINDOW_MS
    observed = await _observed(
        session, orm.RequestLogORM.requested_model, needle=needle, since_ms=since
    )
    observed += await _observed(
        session, orm.RequestLogORM.upstream_model, needle=needle, since_ms=since
    )
    catalog = await _catalog_names(session)
    return _capped(fold_values([*catalog, *observed]), needle)


async def filter_options(
    session: AsyncSession,
    *,
    q: str = "",
) -> tuple[list[str], list[str], list[str], list[orm.UserORM], dict[str, bool]]:
    """(channels, models, key_labels, users, truncated) for the filter bar.

    q narrows every list the same way it always did: a case-insensitive
    substring. The log is also where the two keyword-only lists (channels, key
    labels) come from -- a channel that never served a request is not a filter
    the operator can act on.
    """
    needle = q.strip()
    lowered = needle.lower()
    # 渠道当前名优先（目录），历史拼写靠 fold_values 合并去重（ADR-071 决策 14）。
    catalog_channels: list[str] = [
        str(value)
        for value in (
            (await session.execute(select(orm.ChannelORM.name))).scalars().all()
        )
        if value
    ]
    channels, channels_truncated = _capped(
        fold_values([
            *catalog_channels,
            *await _observed(session, orm.RequestLogORM.channel_name, needle=needle),
        ]),
        lowered,
    )
    labels, labels_truncated = _capped(
        fold_values(await _observed(session, orm.RequestLogORM.api_key_label, needle=needle)),
        lowered,
    )
    models, models_truncated = await model_options(session, q=q)

    user_statement = select(orm.UserORM)
    if needle:
        user_statement = user_statement.where(
            or_(
                like_contains(orm.UserORM.username, needle),
                like_contains(orm.UserORM.display_name, needle),
            )
        )
    user_statement = user_statement.order_by(orm.UserORM.username).limit(FILTER_LIMIT + 1)
    users = list((await session.execute(user_statement)).scalars().all())
    users_truncated = len(users) > FILTER_LIMIT

    return (
        channels,
        models,
        labels,
        users[:FILTER_LIMIT],
        {
            "channels": channels_truncated,
            "models": models_truncated,
            "key_labels": labels_truncated,
            "users": users_truncated,
        },
    )


__all__ = [
    "FILTER_LIMIT",
    "OBSERVED_WINDOW_MS",
    "filter_options",
    "fold_values",
    "model_options",
]
