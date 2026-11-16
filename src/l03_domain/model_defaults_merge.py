"""models.dev 字段级合并（ADR-073 决策 5）与值比对保护（决策 6）。

合并语义来自「上限与能力是厂商事实，价格是供应商报价」：

* cost.input / cost.output：取该档位内中位数（抑制转售商离群价）；
* cost.cache_read / cache_write：缺就留 None（None != 0，0 是真实价格）；
* limit.context / output：取最大值（保守上限是厂商能力的上界）；
* modalities：并集，text 兜底；
* tool_call / reasoning / attachment / structured_output：任一为真即真；
* type -> model_type，reasoning_options -> reasoning_level，status 只记录。

值比对保护只影响后续同步（预填是草稿，落库后才成为基线），所以这里是纯函数：
same_value 判断「当前值是否等于上次自动写入值」，adopt_value 决定要不要覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping

from l03_domain.model_defaults_rules import (
    RULE_VERSION,
    TIER_ORDER,
    TIER_UNRANKED,
    logical_model_key,
    tier_rank,
)
from l03_domain.model_defaults_match import ModelEntry
from l03_domain.model_types import MODEL_TYPE_CHAT, normalize_model_type

CAPABILITY_FLAGS = ("tool_call", "reasoning", "attachment", "structured_output")
PROVIDER_FLAGS = ("structured_output",)
_MODALITY_KEYS = ("input", "output")
_REASONING_RANK = {"low": 1, "medium": 2, "high": 3}
_MODEL_TYPE_ALIASES = {
    "llm": MODEL_TYPE_CHAT,
    "chat": MODEL_TYPE_CHAT,
    "completion": MODEL_TYPE_CHAT,
    "text": MODEL_TYPE_CHAT,
}


@dataclass(frozen=True, slots=True)
class MergedDefaults:
    """一个逻辑模型簇的合并结果；fields 同时是值比对基线。"""

    fields: dict[str, Any]
    providers: tuple[dict[str, Any], ...] = ()
    tier: str = TIER_UNRANKED
    rule_version: str = RULE_VERSION
    matched_by: str = ""


def _numbers(values: Iterable[Any]) -> list[float]:
    parsed: list[float] = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            parsed.append(float(value))
    return parsed


def _median(values: Iterable[Any]) -> float | None:
    parsed = _numbers(values)
    if not parsed:
        return None
    return round(float(median(parsed)), 6)


def _max_int(values: Iterable[Any]) -> int | None:
    parsed = [int(value) for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return max(parsed) if parsed else None


def _most_common(values: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda value: (counts[value], value))


def _pick_group(entries: list[ModelEntry]) -> tuple[list[ModelEntry], str]:
    """取最可信档位；三档都没有时退化为全部条目。"""
    for tier in TIER_ORDER:
        group = [entry for entry in entries if entry.tier == tier]
        if group:
            return group, tier
    return list(entries), TIER_UNRANKED


def _tier_groups(entries: list[ModelEntry]) -> list[list[ModelEntry]]:
    """由高到低的档位分组；未定档条目排最后。"""
    groups: list[list[ModelEntry]] = []
    for tier in TIER_ORDER:
        group = [entry for entry in entries if entry.tier == tier]
        if group:
            groups.append(group)
    rest = [entry for entry in entries if entry.tier == TIER_UNRANKED]
    if rest:
        groups.append(rest)
    return groups


def _cost_field(entries: list[ModelEntry], field: str) -> float | None:
    """价格按档位取中位数；该档全缺才降一档（ADR-073 决策 5）。"""
    for group in _tier_groups(entries):
        costs = [entry.fields.get("cost") for entry in group]
        value = _median(cost.get(field) for cost in costs if isinstance(cost, dict))
        if value is not None:
            return value
    return None


def _modality_union(entries: list[ModelEntry], key: str) -> list[str]:
    merged: list[str] = []
    for entry in entries:
        modalities = entry.fields.get("modalities")
        if not isinstance(modalities, dict):
            continue
        values = modalities.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value or "").strip()
            if text and text not in merged:
                merged.append(text)
    if "text" not in merged:
        merged.insert(0, "text")
    return merged


def _model_type(entries: list[ModelEntry]) -> str:
    for entry in entries:
        raw = entry.fields.get("type")
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if text in _MODEL_TYPE_ALIASES:
            return _MODEL_TYPE_ALIASES[text]
        try:
            return normalize_model_type(text)
        except ValueError:
            continue
    return MODEL_TYPE_CHAT


def _reasoning_level(entries: list[ModelEntry]) -> str:
    best = ""
    for entry in entries:
        options = entry.fields.get("reasoning_options")
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            if str(option.get("type") or "").strip().lower() != "effort":
                continue
            values = option.get("values")
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value or "").strip().lower()
                if text in _REASONING_RANK and _REASONING_RANK[text] > _REASONING_RANK.get(best, 0):
                    best = text
    return best


def merge_entries(
    entries: Iterable[ModelEntry],
    *,
    rule_version: str = RULE_VERSION,
    matched_by: str = "",
) -> MergedDefaults:
    """按决策 5 合并一个逻辑模型簇的条目。"""
    items = list(entries)
    if not items:
        return MergedDefaults(fields={}, rule_version=rule_version, matched_by=matched_by)
    group, tier = _pick_group(items)

    group_costs = [entry.fields.get("cost") for entry in group]
    group_costs = [cost for cost in group_costs if isinstance(cost, dict)]
    limits = [entry.fields.get("limit") for entry in group]
    limits = [limit for limit in limits if isinstance(limit, dict)]

    cost = {
        "input": _cost_field(items, "input"),
        "output": _cost_field(items, "output"),
        "cache_read": _median(cost.get("cache_read") for cost in group_costs),
        "cache_write": _median(cost.get("cache_write") for cost in group_costs),
    }
    limit = {
        "context": _max_int(limit.get("context") for limit in limits),
        "input": _max_int(limit.get("input") for limit in limits),
        "output": _max_int(limit.get("output") for limit in limits),
    }
    capabilities = {
        flag: any(bool(entry.fields.get(flag)) for entry in items) for flag in CAPABILITY_FLAGS
    }
    fields: dict[str, Any] = {
        "cost": cost,
        "limit": limit,
        "modalities": {key: _modality_union(group, key) for key in _MODALITY_KEYS},
        "capabilities": capabilities,
        "model_type": _model_type(group),
        "reasoning_level": _reasoning_level(group),
        "status": _most_common(entry.fields.get("status") for entry in group),
        "canonical_model_id": _most_common(entry.canonical_model_id for entry in group)
        or logical_model_key(group),
    }
    providers = tuple(
        {
            "provider": entry.provider,
            "tier": entry.tier,
            "model_id": entry.model_id,
            "canonical_model_id": entry.canonical_model_id,
            "last_updated": entry.last_updated,
        }
        for entry in sorted(group, key=lambda entry: (tier_rank(entry.tier), entry.provider))
    )
    return MergedDefaults(
        fields=fields,
        providers=providers,
        tier=tier,
        rule_version=rule_version,
        matched_by=matched_by,
    )


def prefill_from_fields(fields: Mapping[str, Any] | None) -> dict[str, Any]:
    """把一份 fields_json 映射成表单补丁（值比对同步复用同一映射）。"""
    fields = fields or {}
    cost = fields.get("cost") if isinstance(fields.get("cost"), dict) else {}
    limit = fields.get("limit") if isinstance(fields.get("limit"), dict) else {}
    modalities = fields.get("modalities") if isinstance(fields.get("modalities"), dict) else {}
    capabilities = fields.get("capabilities") if isinstance(fields.get("capabilities"), dict) else {}
    max_input = limit.get("input") or limit.get("context")
    parameters: dict[str, Any] = {
        "max_input_tokens": int(max_input) if max_input else None,
        "max_output_tokens": int(limit["output"]) if limit.get("output") else None,
        "reasoning_level": str(fields.get("reasoning_level") or ""),
        "input_types": list(modalities.get("input") or ["text"]),
        "output_types": list(modalities.get("output") or ["text"]),
    }
    for flag in CAPABILITY_FLAGS:
        parameters[flag] = bool(capabilities.get(flag))
    return {
        "model_type": str(fields.get("model_type") or MODEL_TYPE_CHAT),
        "parameters": parameters,
        "input_price_per_1m": cost.get("input"),
        "output_price_per_1m": cost.get("output"),
        "cache_read_price_per_1m": cost.get("cache_read"),
        "cache_write_price_per_1m": cost.get("cache_write"),
    }


def prefill_defaults(merged: MergedDefaults) -> dict[str, Any]:
    """把合并结果映射成渠道模型表单可用的默认值；None 表示不预填该字段。"""
    return prefill_from_fields(merged.fields)


_RULES = {
    "input_price_per_1m": "median",
    "output_price_per_1m": "median",
    "cache_read_price_per_1m": "median",
    "cache_write_price_per_1m": "median",
    "max_input_tokens": "max",
    "max_output_tokens": "max",
    "input_types": "union",
    "output_types": "union",
    "tool_call": "any",
    "reasoning": "any",
    "attachment": "any",
    "structured_output": "any",
    "model_type": "mode",
    "reasoning_level": "max",
}


def field_sources(merged: MergedDefaults) -> dict[str, dict[str, Any]]:
    """逐字段来源提示（只用于表单展示，不落库）。"""
    providers = [str(item.get("provider") or "") for item in merged.providers]
    providers = [name for name in providers if name]
    return {
        field: {"providers": providers, "rule": rule}
        for field, rule in _RULES.items()
    }


def same_value(current: Any, baseline: Any) -> bool:
    """值比对：数字按数值比，列表/字典递归比，None 与空值各自相等。"""
    if isinstance(current, bool) or isinstance(baseline, bool):
        return bool(current) == bool(baseline)
    if isinstance(current, (int, float)) and isinstance(baseline, (int, float)):
        return float(current) == float(baseline)
    if isinstance(current, list) and isinstance(baseline, list):
        return len(current) == len(baseline) and all(
            same_value(left, right) for left, right in zip(current, baseline)
        )
    if isinstance(current, dict) and isinstance(baseline, dict):
        return set(current) == set(baseline) and all(
            same_value(current[key], baseline[key]) for key in current
        )
    return current == baseline


def adopt_value(current: Any, baseline: Any, candidate: Any) -> Any:
    """决策 6：当前值等于基线（视为未手改）才采用拉取值，否则保留当前值。"""
    return candidate if same_value(current, baseline) else current
