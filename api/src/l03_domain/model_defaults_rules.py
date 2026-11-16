"""models.dev 同步的规则常量与纯函数（ADR-073 决策 3/4）。

本模块不读写文件、不发网络：名单、别名表与档位判定都只依赖入参。
名单默认值是代码常量，可由 app_settings 单 key 的 models_dev_json
（tier_lists / labs_min_providers）覆盖；别名表文件的读取在
l05_service/model_defaults（路径来自 Settings.models_dev_alias_file），
本模块只提供 parse_aliases 纯解析。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

RULE_VERSION = "1"

# 三档可信度 + 未定档（不参与合并，只在 providers_json 里留证）。
TIER_OFFICIAL = "T1"
TIER_CLOUD = "T2"
TIER_AGGREGATOR = "T3"
TIER_UNRANKED = "unranked"

TIER_RANK = {TIER_OFFICIAL: 0, TIER_CLOUD: 1, TIER_AGGREGATOR: 2, TIER_UNRANKED: 3}
TIER_ORDER = (TIER_OFFICIAL, TIER_CLOUD, TIER_AGGREGATOR)

# T1：已知的第一方实验室 id。labs 计数（该 provider 的 canonical 前缀去重数）
# 用来排除「名字像官方、实际是聚合站」的 provider。
OFFICIAL_LABS = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "xai",
        "deepseek",
        "moonshotai",
        "minimax",
        "zhipuai",
        "alibaba",
        "mistral",
        "cohere",
        "meta",
        "ai21",
        "qwen",
    }
)

# T2：官方云/托管平台（同一模型由云厂商转售，参数与厂商一致）。
OFFICIAL_CLOUDS = frozenset(
    {
        "amazon-bedrock",
        "azure",
        "azure-cognitive-services",
        "google-vertex",
        "cloudflare-ai-gateway",
        "snowflake-cortex",
        "sap-ai-core",
        "digitalocean",
    }
)

LABS_MIN_PROVIDERS = 4
OPENAI_COMPATIBLE_NPM = "@ai-sdk/openai-compatible"

DEFAULT_FUZZY_AUTO = 0.92
DEFAULT_FUZZY_REVIEW = 0.75
MAX_CANDIDATES = 5

MATCH_EXACT = "exact"
MATCH_NORMALIZED = "normalized"
MATCH_PREFIX_STRIPPED = "prefix_stripped"
MATCH_VERSION_LOOSE = "version_loose"
MATCH_ALIAS = "alias"
MATCH_FUZZY_AUTO = "fuzzy_auto"
MATCH_FUZZY_REVIEW = "fuzzy_review"
MATCH_UNMATCHED = "unmatched"

# 命中级别由高到低；matched_by 直接落库，顺序也用于展示。
MATCH_LEVELS = (
    MATCH_EXACT,
    MATCH_NORMALIZED,
    MATCH_PREFIX_STRIPPED,
    MATCH_VERSION_LOOSE,
    MATCH_ALIAS,
    MATCH_FUZZY_AUTO,
    MATCH_FUZZY_REVIEW,
    MATCH_UNMATCHED,
)

_SEPARATORS = str.maketrans({"_": "-", ".": "-", ":": "-"})
_VERSION_SUFFIX = re.compile(
    r"(?:[-_.](?:\d{4}-\d{2}-\d{2}|\d{4,8}|v\d+(?:\.\d+)*))$",
    re.IGNORECASE,
)


def normalize_model_id(value: str | None) -> str:
    """归一化模型 id：小写 + 分隔符统一为 -（ADR-073 决策 3 第 2 级）。"""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text.translate(_SEPARATORS)


def strip_provider_prefix(model_id: str | None) -> str:
    """剥掉 lab/provider 前缀：openai/gpt-4o、@cf/meta/llama、minimax/xxx。

    没有前缀时返回空串（与「剥完为空」区分：调用方只在非空时降级匹配）。
    """
    text = str(model_id or "").strip()
    if not text:
        return ""
    if text.startswith("@"):
        _, _, rest = text.partition("/")
        return rest.strip()
    if "/" in text:
        head, _, rest = text.partition("/")
        return rest.strip() if head else ""
    return ""


def strip_version_suffix(model_id: str | None) -> str:
    """去掉末尾的日期/版本后缀；没有可剥后缀时返回空串。

    ADR-073 R4：不合并日期/版本后缀，只允许「精确失败后降级裸名」，
    且降级命中必须记 version_loose=true 供复核。单数字结尾（claude-sonnet-4-5）
    不算版本后缀，避免把 4.5 拆成 4。
    """
    text = str(model_id or "").strip()
    if not text:
        return ""
    match = _VERSION_SUFFIX.search(text)
    if match is None:
        return ""
    bare = text[: match.start()].strip()
    return bare if bare and bare != text else ""


def provider_tier(
    provider_id: str | None,
    *,
    labs: int = 0,
    npm: str = "",
    official: Iterable[str] | None = None,
    clouds: Iterable[str] | None = None,
    labs_min: int | None = None,
) -> str:
    """按名单+信号判定 provider 档位（ADR-073 决策 4）。

    名单与阈值默认取代码常量；调用方可传 official / clouds / labs_min
    覆盖（值来自 app_settings 单 key 的 models_dev_json，决策 4「全部落配置」）。
    """
    ident = str(provider_id or "").strip().lower()
    lab_set = frozenset(part.strip().lower() for part in official) if official is not None else OFFICIAL_LABS
    cloud_set = frozenset(part.strip().lower() for part in clouds) if clouds is not None else OFFICIAL_CLOUDS
    threshold = LABS_MIN_PROVIDERS if labs_min is None else int(labs_min)
    if ident in lab_set and int(labs) <= 1:
        return TIER_OFFICIAL
    if ident in cloud_set:
        return TIER_CLOUD
    if int(labs) >= threshold or str(npm or "").strip().lower() == OPENAI_COMPATIBLE_NPM:
        return TIER_AGGREGATOR
    return TIER_UNRANKED


def tier_rank(tier: str) -> int:
    return TIER_RANK.get(str(tier or ""), TIER_RANK[TIER_UNRANKED])


def logical_model_key(entries: Any) -> str:
    """一簇模型共用的逻辑键：canonical_model_id 优先，其次代表条目 id。"""
    items = list(entries or ())
    if not items:
        return ""
    canonicals = [str(getattr(item, "canonical_model_id", "") or "") for item in items]
    canonicals = [value for value in canonicals if value]
    if canonicals:
        counts: dict[str, int] = {}
        for value in canonicals:
            counts[value] = counts.get(value, 0) + 1
        return max(counts, key=lambda value: (counts[value], value))
    return str(getattr(items[0], "model_id", "") or "")


def parse_aliases(raw: str) -> dict[str, str]:
    """解析人工别名表 JSON 文本；空文本或格式不对时返回空表（不阻塞同步）。

    支持两种形状：{"aliases": {"alias": "model-id"}} 或 {"alias": "model-id"}。
    键与值都归一到小写，便于按 normalize_model_id 查询。文件读取由
    l05_service/model_defaults 负责（Settings.models_dev_alias_file）。
    """
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    table = payload.get("aliases") if isinstance(payload.get("aliases"), dict) else payload
    aliases: dict[str, str] = {}
    for key, value in table.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        name = normalize_model_id(key)
        identity = normalize_model_id(value)
        if name and identity:
            aliases[name] = value.strip()
    return aliases
