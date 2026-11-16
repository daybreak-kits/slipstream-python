"""看板「分流图」：把 request_logs 聚合成固定五级的流向数据。

图画成桑基流：user → key → model → upstream → channel。每一行按它自己的
度量值（requests=1，tokens=prompt+completion）在五级各记一次，因此

- 每个节点的 value = 经过它的行之和；
- 每条边的 value = 走这条跳转的行之和；
- 中间三级的入边和 = 出边和 = 节点 value，前端按 value 画的带宽永远自洽。

截断不破坏这个不变式：每级保留前 20 个节点，其余节点合并成一个
「其他（N）」节点（N 为被合并的节点数），指向它们的边改指到合并节点、
不丢弃，所以合并后依然满足上面的守恒关系。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from l01_core.textfold import fold_key
from l04_repo import orm, repo
from l05_service import ledger_read
from l05_service.aggregates import prompt_tokens_known, usage_totals
from l05_service.channel_axis import ChannelAxis, channel_axis, model_axis

#: 固定的五级链路，顺序即图的横向顺序。
STAGES: tuple[str, ...] = ("user", "key", "model", "upstream", "channel")

#: 每级保留的真实节点数；其余合并成一个合并节点。
MAX_NODES_PER_STAGE = 20
OTHER_NODE_LABEL = "其他（{count}）"

_METRICS = frozenset({"requests", "tokens"})


def row_metric_value(row: orm.RequestLogORM, metric: str) -> int:
    """一行在所选度量下的贡献。

    requests 把每条 chat 行算 1，中断的流也算一次请求。
    tokens 只统计 prompt 计数可信的行（aggregates.prompt_tokens_known）：中断的
    流只贡献它已经发出去的 prompt，因为 completion 从未到达；字符级估算出来的
    行没有可信计数，贡献 0（仍计入 requests）。
    """
    if metric == "requests":
        return 1
    if not prompt_tokens_known(row):
        return 0
    prompt = int(row.prompt_tokens or 0)
    if bool(row.usage_estimated):
        return prompt
    return prompt + int(row.completion_tokens or 0)


def _node(stage: str, ident: str, label: str) -> tuple[str, str]:
    return f"{stage}:{ident}", label


def _user_node(row: Any, usernames: dict[str, str]) -> tuple[str, str]:
    user_id = str(getattr(row, "user_id", "") or "").strip()
    if not user_id:
        return _node("user", "anonymous", "未登录")
    return _node("user", user_id, usernames.get(user_id) or user_id)


def _key_node(row: Any) -> tuple[str, str]:
    key_id = str(getattr(row, "api_key_id", "") or "").strip()
    label = str(getattr(row, "api_key_label", "") or "").strip() or "无密钥"
    # 同一把密钥可能改过标签，优先用 id 做身份；没有 id 时才退化成标签。
    return _node("key", key_id or f"label-{label}", label)


def _model_node(row: Any, models: Any = None) -> tuple[str, str]:
    if models is not None:
        # 综合模型轴（裁定③）：combo id → 直连 channel-model id → 按名回退，
        # 展示名取实体当前名；无 id 的行退回折叠名、保留用户写法。
        ident, label = models.model_ident(row)
        return _node("model", ident, label)
    name = str(getattr(row, "requested_model", "") or "").strip() or "未命名"
    # 节点 id 折叠大小写、展示名保留用户写法（ADR-071 决策 14），下同。
    return _node("model", fold_key(name), name)


def _upstream_node(row: Any, models: Any = None) -> tuple[str, str]:
    if models is not None:
        # 上游模型轴（裁定②）：分组键 final_channel_model_id，展示名取该
        # channel model 的当前 upstream_model；无 id 的行退回折叠名。
        ident, label = models.upstream_ident(row)
        return _node("upstream", ident, label)
    name = str(getattr(row, "upstream_model", "") or "").strip() or "—"
    return _node("upstream", fold_key(name), name)


def _channel_node(row: Any, axis: ChannelAxis | None = None) -> tuple[str, str]:
    if axis is not None:
        # 渠道身份走 channel_id（改名也合并）；无 id 的行退回折叠名，哨兵
        # （空 channel_name）单独成桶，都不是渠道名折叠的管辖范围。
        ident, label = axis.ident_label(row)
        return _node("channel", ident, label)
    name = str(getattr(row, "channel_name", "") or "").strip() or "无渠道"
    return _node("channel", fold_key(name), name)


def _row_nodes(
    row: Any,
    usernames: dict[str, str],
    axis: ChannelAxis | None = None,
    models: Any = None,
) -> tuple[tuple[str, str], ...]:
    return (
        _user_node(row, usernames),
        _key_node(row),
        _model_node(row, models),
        _upstream_node(row, models),
        _channel_node(row, axis),
    )


async def _usernames(session: AsyncSession, rows: list[Any]) -> dict[str, str]:
    """user_id → username，一次查询取回窗口内出现的所有用户。"""
    ids = {
        str(getattr(row, "user_id", "") or "").strip()
        for row in rows
        if str(getattr(row, "user_id", "") or "").strip()
    }
    if not ids:
        return {}
    result = await session.execute(
        select(orm.UserORM.id, orm.UserORM.username).where(orm.UserORM.id.in_(ids))
    )
    return {str(user_id): str(username) for user_id, username in result.all()}


async def compute_flows(
    session: AsyncSession,
    *,
    start_ms: int,
    end_ms: int,
    metric: str = "requests",
) -> dict[str, Any]:
    """窗口内的 chat 行 → 固定五级链路图的节点、边与总量。"""
    wanted = metric if metric in _METRICS else "requests"
    rows = await repo.list_request_logs(
        session, start_ms=start_ms, end_ms=end_ms, source="chat"
    )
    rows = await ledger_read.apply_overlay(session, rows)
    usernames = await _usernames(session, rows)
    axis = await channel_axis(session, rows, fallback_label="无渠道")
    # 模型两轴（裁定②③）：上游轴按 channel-model id、综合轴按 combo id；
    # catalog 缺失退回行内快照，历史 null 行按名回退、不回填。
    models = await model_axis(
        session, rows, fallback_label="未命名", upstream_fallback="—"
    )

    totals: dict[str, dict[str, int]] = {stage: {} for stage in STAGES}
    labels: dict[str, dict[str, str]] = {stage: {} for stage in STAGES}
    paths: list[tuple[tuple[str, ...], int]] = []
    for row in rows:
        value = row_metric_value(row, wanted)
        if value <= 0:
            continue
        pairs = _row_nodes(row, usernames, axis, models)
        for stage, (node_id, label) in zip(STAGES, pairs):
            totals[stage][node_id] = totals[stage].get(node_id, 0) + value
            labels[stage][node_id] = label
        paths.append((tuple(pair[0] for pair in pairs), value))

    renames: dict[str, dict[str, str]] = {}
    nodes: list[dict[str, Any]] = []
    for stage in STAGES:
        stage_totals = totals[stage]
        ordered = sorted(
            stage_totals.items(), key=lambda item: (-item[1], labels[stage][item[0]])
        )
        kept = ordered[:MAX_NODES_PER_STAGE]
        merged = ordered[MAX_NODES_PER_STAGE:]
        stage_sum = sum(stage_totals.values())
        mapping = {node_id: node_id for node_id, _value in kept}
        for node_id, value in kept:
            nodes.append(
                {
                    "id": node_id,
                    "stage": stage,
                    "label": labels[stage][node_id],
                    "value": value,
                    "share": round(value / stage_sum, 6) if stage_sum else 0.0,
                }
            )
        if merged:
            other_id = f"{stage}:other"
            merged_value = sum(value for _node_id, value in merged)
            mapping.update({node_id: other_id for node_id, _value in merged})
            nodes.append(
                {
                    "id": other_id,
                    "stage": stage,
                    "label": OTHER_NODE_LABEL.format(count=len(merged)),
                    "value": merged_value,
                    "share": round(merged_value / stage_sum, 6) if stage_sum else 0.0,
                }
            )
        renames[stage] = mapping

    link_values: dict[tuple[str, str], int] = {}
    for ids, value in paths:
        final = tuple(renames[stage][ids[index]] for index, stage in enumerate(STAGES))
        for index in range(len(STAGES) - 1):
            hop = (final[index], final[index + 1])
            link_values[hop] = link_values.get(hop, 0) + value
    links = [
        {"source": source, "target": target, "value": value}
        for (source, target), value in sorted(
            link_values.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    usage = usage_totals(rows)
    return {
        "metric": wanted,
        "stages": list(STAGES),
        "nodes": nodes,
        "links": links,
        "total": {
            "requests": len(rows),
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        },
    }
