"""models.dev 七级匹配管线（ADR-073 决策 3）。

匹配对象只有 upstream_model：同一个 model 可能挂在多个渠道，provider 不构成
身份。entries_from_payload / index_from_payload 在本模块把 api.json
解析成纯内存索引；解析与匹配都不读写文件、不发网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from l03_domain.model_defaults_rules import (
    DEFAULT_FUZZY_AUTO,
    DEFAULT_FUZZY_REVIEW,
    MATCH_ALIAS,
    MATCH_EXACT,
    MATCH_FUZZY_AUTO,
    MATCH_FUZZY_REVIEW,
    MATCH_NORMALIZED,
    MATCH_PREFIX_STRIPPED,
    MATCH_UNMATCHED,
    MATCH_VERSION_LOOSE,
    MAX_CANDIDATES,
    TIER_UNRANKED,
    normalize_model_id,
    provider_tier,
    strip_provider_prefix,
    strip_version_suffix,
    tier_rank,
)


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """一个 provider 下的一条 models.dev 记录。"""

    provider: str
    model_id: str
    tier: str = TIER_UNRANKED
    canonical_model_id: str = ""
    last_updated: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    provider: str
    model_id: str
    tier: str
    score: float
    canonical_model_id: str = ""
    last_updated: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotIndex:
    """按精确/归一化/剥前缀/剥版本/别名/模糊建立的只读索引。"""

    entries: tuple[ModelEntry, ...] = ()
    by_exact: Mapping[str, tuple[ModelEntry, ...]] = field(default_factory=dict)
    by_norm: Mapping[str, tuple[ModelEntry, ...]] = field(default_factory=dict)
    by_stripped: Mapping[str, tuple[ModelEntry, ...]] = field(default_factory=dict)
    by_bare: Mapping[str, tuple[ModelEntry, ...]] = field(default_factory=dict)
    by_alias: Mapping[str, tuple[ModelEntry, ...]] = field(default_factory=dict)
    names: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.entries)


@dataclass(frozen=True, slots=True)
class MatchResult:
    """一次匹配的结果：命中级别 + 该逻辑模型簇的全部条目 + 候选列表。"""

    matched_by: str = MATCH_UNMATCHED
    entries: tuple[ModelEntry, ...] = ()
    candidates: tuple[MatchCandidate, ...] = ()
    score: float = 0.0
    version_loose: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.entries)


def _append(table: dict[str, list[ModelEntry]], key: str, entry: ModelEntry) -> None:
    if not key:
        return
    bucket = table.setdefault(key, [])
    if entry not in bucket:
        bucket.append(entry)


def build_index(
    entries: Iterable[ModelEntry],
    *,
    aliases: Mapping[str, str] | None = None,
) -> SnapshotIndex:
    """从条目列表建索引；aliases 的键已归一化，值是 models.dev 的 model id。"""
    items = tuple(entries)
    exact: dict[str, list[ModelEntry]] = {}
    norm: dict[str, list[ModelEntry]] = {}
    stripped: dict[str, list[ModelEntry]] = {}
    bare: dict[str, list[ModelEntry]] = {}
    for entry in items:
        _append(exact, entry.model_id, entry)
        _append(norm, normalize_model_id(entry.model_id), entry)
        without_prefix = strip_provider_prefix(entry.model_id)
        # 裸名表同时收「本来就无后缀的 id」：查询带日期/版本后缀时降级命中它。
        _append(bare, normalize_model_id(strip_version_suffix(entry.model_id) or entry.model_id), entry)
        if without_prefix:
            _append(stripped, normalize_model_id(without_prefix), entry)
            _append(bare, normalize_model_id(strip_version_suffix(without_prefix) or without_prefix), entry)
    alias_table: dict[str, list[ModelEntry]] = {}
    for alias, target in (aliases or {}).items():
        hits = exact.get(target, []) or norm.get(normalize_model_id(target), [])
        for entry in hits:
            _append(alias_table, alias, entry)
    names = tuple(dict.fromkeys(normalize_model_id(entry.model_id) for entry in items))
    return SnapshotIndex(
        entries=items,
        by_exact={key: tuple(value) for key, value in exact.items()},
        by_norm={key: tuple(value) for key, value in norm.items()},
        by_stripped={key: tuple(value) for key, value in stripped.items()},
        by_bare={key: tuple(value) for key, value in bare.items()},
        by_alias={key: tuple(value) for key, value in alias_table.items()},
        names=names,
    )

def _lab_count(models: Mapping[str, Any]) -> int:
    """R2 信号：一个 provider 背后挂着多少个不同的 canonical lab 前缀。"""
    labs: set[str] = set()
    for model in models.values():
        if not isinstance(model, Mapping):
            continue
        canonical = str(model.get("canonical_model_id") or "")
        if not canonical:
            continue
        labs.add(canonical.split("/", 1)[0].strip().lower())
    return len(labs)


def entries_from_payload(
    payload: Mapping[str, Any],
    *,
    official: Iterable[str] | None = None,
    clouds: Iterable[str] | None = None,
    labs_min: int | None = None,
) -> list[ModelEntry]:
    """把 api.json（provider -> models）转成带档位的条目列表。"""
    entries: list[ModelEntry] = []
    for provider_id, provider in payload.items():
        if not isinstance(provider, Mapping):
            continue
        models = provider.get("models")
        if not isinstance(models, Mapping):
            continue
        tier = provider_tier(
            str(provider_id),
            labs=_lab_count(models),
            npm=str(provider.get("npm") or ""),
            official=official,
            clouds=clouds,
            labs_min=labs_min,
        )
        for model_id, model in models.items():
            if not isinstance(model, Mapping):
                continue
            entries.append(
                ModelEntry(
                    provider=str(provider_id),
                    model_id=str(model.get("id") or model_id),
                    tier=tier,
                    canonical_model_id=str(model.get("canonical_model_id") or ""),
                    last_updated=str(model.get("last_updated") or ""),
                    fields=dict(model),
                )
            )
    return entries


def index_from_payload(
    payload: Mapping[str, Any], *, aliases: Mapping[str, str] | None = None
) -> SnapshotIndex:
    """一步建索引：快照载荷 -> 条目 -> 索引。"""
    return build_index(entries_from_payload(payload), aliases=aliases)


def _candidate(entry: ModelEntry, score: float) -> MatchCandidate:
    return MatchCandidate(
        provider=entry.provider,
        model_id=entry.model_id,
        tier=entry.tier,
        score=round(float(score), 4),
        canonical_model_id=entry.canonical_model_id,
        last_updated=entry.last_updated,
    )


def _cluster_candidates(entries: Iterable[ModelEntry], score: float = 1.0) -> tuple[MatchCandidate, ...]:
    items = list(entries)
    items.sort(key=lambda entry: (tier_rank(entry.tier), entry.provider))
    return tuple(_candidate(entry, score) for entry in items[:MAX_CANDIDATES])


def _review_hits(
    index: SnapshotIndex, scored: list[tuple[float, str]]
) -> list[tuple[float, ModelEntry]]:
    hits: list[tuple[float, ModelEntry]] = []
    for score, name in scored:
        entries = index.by_norm[name]
        if not entries:
            continue
        best = min(entries, key=lambda entry: tier_rank(entry.tier))
        hits.append((score, best))
        if len(hits) >= MAX_CANDIDATES:
            break
    return hits


def _fuzzy(
    index: SnapshotIndex,
    text: str,
    *,
    fuzzy_auto: float,
    fuzzy_review: float,
) -> MatchResult:
    target = normalize_model_id(text)
    scored: list[tuple[float, str]] = []
    for name in index.names:
        score = SequenceMatcher(None, target, name).ratio()
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= fuzzy_auto:
        best = index.by_norm[scored[0][1]]
        return MatchResult(
            matched_by=MATCH_FUZZY_AUTO,
            entries=tuple(best),
            candidates=_cluster_candidates(best, scored[0][0]),
            score=round(scored[0][0], 4),
        )
    hits = _review_hits(index, scored)
    if scored and scored[0][0] >= fuzzy_review:
        return MatchResult(
            matched_by=MATCH_FUZZY_REVIEW,
            candidates=tuple(_candidate(entry, score) for score, entry in hits),
            score=round(scored[0][0], 4),
        )
    return MatchResult(
        matched_by=MATCH_UNMATCHED,
        candidates=tuple(_candidate(entry, score) for score, entry in hits),
    )


def match_model(
    upstream_model: str | None,
    index: SnapshotIndex,
    *,
    fuzzy_auto: float = DEFAULT_FUZZY_AUTO,
    fuzzy_review: float = DEFAULT_FUZZY_REVIEW,
) -> MatchResult:
    """按 ADR-073 决策 3 的七级顺序匹配；未命中时仍返回候选供人工选择。"""
    text = str(upstream_model or "").strip()
    if not text or not index:
        return MatchResult(matched_by=MATCH_UNMATCHED)

    exact = index.by_exact.get(text)
    if exact:
        return MatchResult(
            matched_by=MATCH_EXACT,
            entries=exact,
            candidates=_cluster_candidates(exact),
            score=1.0,
        )

    normalized = index.by_norm.get(normalize_model_id(text))
    if normalized:
        return MatchResult(
            matched_by=MATCH_NORMALIZED,
            entries=normalized,
            candidates=_cluster_candidates(normalized),
            score=1.0,
        )

    without_prefix = strip_provider_prefix(text)
    if without_prefix:
        hits = (
            index.by_stripped.get(normalize_model_id(without_prefix))
            or index.by_exact.get(without_prefix)
            or index.by_norm.get(normalize_model_id(without_prefix))
        )
        if hits:
            return MatchResult(
                matched_by=MATCH_PREFIX_STRIPPED,
                entries=hits,
                candidates=_cluster_candidates(hits),
                score=1.0,
            )

    for candidate_text in (text, without_prefix):
        bare = strip_version_suffix(candidate_text)
        if not bare:
            continue
        hits = index.by_bare.get(normalize_model_id(bare))
        if hits:
            return MatchResult(
                matched_by=MATCH_VERSION_LOOSE,
                entries=hits,
                candidates=_cluster_candidates(hits),
                score=1.0,
                version_loose=True,
            )

    aliased = index.by_alias.get(normalize_model_id(text))
    if aliased:
        return MatchResult(
            matched_by=MATCH_ALIAS,
            entries=aliased,
            candidates=_cluster_candidates(aliased),
            score=1.0,
        )

    return _fuzzy(index, text, fuzzy_auto=fuzzy_auto, fuzzy_review=fuzzy_review)
