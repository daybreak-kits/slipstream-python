"""看板聚合轴的身份与标签（ADR-071 决策 14 的最终裁定：渠道与模型都按 id）。

三个轴，同一套规则——分组键用实体 id，展示名取实体当前名，历史写法只留在行内：

- 渠道轴（ChannelAxis）：request_logs 没有 channel_id，行上的 final_channel_model_id
  （orm.py:215，有索引）join channel_models 得到 channel_id；无 id 的行退回
  fold_key(channel_name)。空 channel_name 的哨兵（请求模型无可用渠道）不是渠道名：
  单独成桶，绝不参与折叠，也绝不并入任何真实渠道。
- 上游模型轴（ModelAxis.upstream_ident）：分组键 final_channel_model_id，展示名取
  该 channel model 的当前 upstream_model，实体被删退回行内字符串；无 id 的行退回
  fold_key(upstream_model)。
- 综合模型轴（ModelAxis.model_ident）：分组键 resolved_combo_id（手动组合，
  task-128 起写入）；直连请求回退 final_channel_model_id；自动组合与历史 null 行
  回退 fold_key(requested_model)——按名回退，不做回填。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.textfold import fold_key
from l04_repo.orm import ChannelModelORM, ChannelORM, ComboORM, RequestLogORM

#: 空 channel_name（请求从未命中任何渠道）的哨兵身份，不是渠道名。
UNAVAILABLE = "channel:none"


class ChannelAxis:
    """The channel identity of every row: id first, folded name as fallback."""

    def __init__(
        self,
        cm_to_channel: dict[str, str],
        channel_names: dict[str, str],
        fallback_label: str,
    ) -> None:
        self._cm = cm_to_channel
        self._names = channel_names
        self._fallback = fallback_label

    def ident_label(self, row: Any) -> tuple[str, str]:
        """(stable identity, display label) for one request row."""
        name = str(getattr(row, "channel_name", "") or "")
        if not name:
            return UNAVAILABLE, self._fallback
        cm_id = str(getattr(row, "final_channel_model_id", "") or "")
        channel_id = self._cm.get(cm_id, "")
        if channel_id:
            # 渠道当前名优先；历史拼写留在行里，身份不随改名漂移。
            return channel_id, self._names.get(channel_id) or name
        return fold_key(name), name


async def channel_axis(
    session: AsyncSession,
    rows: list[RequestLogORM],
    *,
    fallback_label: str,
) -> ChannelAxis:
    """Resolve the rows' channel-model ids once for the whole window."""
    cm_ids = {
        str(getattr(row, "final_channel_model_id", "") or "") for row in rows
    } - {""}
    cm_to_channel: dict[str, str] = {}
    channel_names: dict[str, str] = {}
    if cm_ids:
        result = await session.execute(
            select(ChannelModelORM.id, ChannelModelORM.channel_id).where(
                ChannelModelORM.id.in_(cm_ids)
            )
        )
        cm_to_channel = {str(cm_id): str(ch_id) for cm_id, ch_id in result.all()}
        channel_ids = set(cm_to_channel.values())
        if channel_ids:
            result = await session.execute(
                select(ChannelORM.id, ChannelORM.name).where(
                    ChannelORM.id.in_(channel_ids)
                )
            )
            channel_names = {
                str(ch_id): str(name) for ch_id, name in result.all()
            }
    return ChannelAxis(cm_to_channel, channel_names, fallback_label)


class ModelAxis:
    """模型两轴的身份与标签（裁定②③）：id 优先，名字只是回退。"""

    def __init__(
        self,
        cm_to_upstream: dict[str, str],
        combo_names: dict[str, str],
        fallback_label: str,
        upstream_fallback: str | None = None,
        name_fallback: str | None = None,
    ) -> None:
        self._cm = cm_to_upstream
        self._combos = combo_names
        self._fallback = fallback_label
        self._upstream_fallback = upstream_fallback or fallback_label
        self._name_fallback = name_fallback or fallback_label

    def upstream_ident(self, row: Any) -> tuple[str, str]:
        """上游模型轴：channel-model id 分组，展示当前 upstream_model。"""
        inline = str(getattr(row, "upstream_model", "") or "")
        cm_id = str(getattr(row, "final_channel_model_id", "") or "")
        if cm_id:
            # 实体被删：身份仍是 id，展示名退回行内快照（裁定④的反面边界）。
            return cm_id, self._cm.get(cm_id) or inline or self._upstream_fallback
        if not inline:
            return "", self._upstream_fallback
        return fold_key(inline), inline

    def model_ident(self, row: Any) -> tuple[str, str]:
        """综合模型轴：combo id → channel-model id → 按名回退（不回填）。"""
        requested = str(getattr(row, "requested_model", "") or "")
        combo_id = str(getattr(row, "resolved_combo_id", "") or "")
        if combo_id:
            # 手动组合：展示组合当前名，组合被删退回行内快照。历史 null 行
            # 走下面的名字回退，绝不按名回填成组合 id（裁定③）。
            return (
                f"combo:{combo_id}",
                self._combos.get(combo_id) or requested or self._fallback,
            )
        cm_id = str(getattr(row, "final_channel_model_id", "") or "")
        if cm_id:
            # 直连请求：分组键回退 channel-model id，展示名用它的当前
            # upstream_model（裁定③）。
            return f"cm:{cm_id}", self._cm.get(cm_id) or requested or self._fallback
        if not requested:
            return "", self._name_fallback
        # 自动组合没有实体 id：按名回退（折叠），不回填。
        return fold_key(requested), requested


async def model_axis(
    session: AsyncSession,
    rows: list[RequestLogORM],
    *,
    fallback_label: str,
    upstream_fallback: str | None = None,
    name_fallback: str | None = None,
) -> ModelAxis:
    """Resolve the rows' channel-model and combo ids once for the window."""
    cm_ids = {
        str(getattr(row, "final_channel_model_id", "") or "") for row in rows
    } - {""}
    combo_ids = {
        str(getattr(row, "resolved_combo_id", "") or "") for row in rows
    } - {""}
    cm_to_upstream: dict[str, str] = {}
    if cm_ids:
        result = await session.execute(
            select(ChannelModelORM.id, ChannelModelORM.upstream_model).where(
                ChannelModelORM.id.in_(cm_ids)
            )
        )
        cm_to_upstream = {
            str(cm_id): str(upstream) for cm_id, upstream in result.all()
        }
    combo_names: dict[str, str] = {}
    if combo_ids:
        result = await session.execute(
            select(ComboORM.id, ComboORM.name).where(ComboORM.id.in_(combo_ids))
        )
        combo_names = {str(combo_id): str(name) for combo_id, name in result.all()}
    return ModelAxis(
        cm_to_upstream,
        combo_names,
        fallback_label,
        upstream_fallback=upstream_fallback,
        name_fallback=name_fallback,
    )


__all__ = [
    "UNAVAILABLE",
    "ChannelAxis",
    "ModelAxis",
    "channel_axis",
    "model_axis",
]
