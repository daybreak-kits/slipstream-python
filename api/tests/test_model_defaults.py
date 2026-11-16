"""models.dev 每日参数同步的单元测试（ADR-073 决策 1/2）。

步骤②覆盖快照落盘与条件请求：
- 快照是 api.json + meta.json 两文件，载荷校验后才原子落盘；
- 失败只改 meta.json，保留旧快照与 ETag/Last-Modified（当天不重试靠 last_attempt_date）；
- 条件请求带 If-None-Match / If-Modified-Since，304 视为成功；
- 超时、传输错误、非 200、空 body 都是返回值，不抛异常。
索引、匹配、合并、触发与预填端点由后续步骤补测。

沙箱约束：运行期间新建的目录不可写（tests/conftest.py 里 SQLite 用同一绕法），
所以快照测试不用 tmp_path，而是在已存在的 .pytest-dbs 下用唯一文件名。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from l03_domain.model_defaults_match import ModelEntry, build_index, match_model
from l03_domain.model_defaults_merge import (
    adopt_value,
    merge_entries,
    prefill_defaults,
    same_value,
)
from l03_domain.model_defaults_rules import (
    MATCH_ALIAS,
    MATCH_EXACT,
    MATCH_FUZZY_AUTO,
    MATCH_FUZZY_REVIEW,
    MATCH_NORMALIZED,
    MATCH_PREFIX_STRIPPED,
    MATCH_UNMATCHED,
    MATCH_VERSION_LOOSE,
    TIER_CLOUD,
    TIER_OFFICIAL,
    TIER_AGGREGATOR,
    TIER_UNRANKED,
    logical_model_key,
    normalize_model_id,
    provider_tier,
    strip_provider_prefix,
    strip_version_suffix,
)
from l04_repo import orm, repo
from l05_service.model_defaults import (
    FetchResult,
    ModelDefaultsService,
    SnapshotError,
    SnapshotMeta,
    SnapshotStore,
    fetch_snapshot,
    get_state,
    save_state,
)
from l05_service.model_defaults import snapshot_store as snapshot_module
from l05_service.model_defaults.snapshot_store import snapshot_counts

PAYLOAD: dict[str, Any] = {
    "openai": {"id": "openai", "models": {"gpt-4o": {"id": "gpt-4o"}}},
    "google": {"id": "google", "models": {}},
}
PAYLOAD_BYTES = json.dumps(PAYLOAD).encode("utf-8")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[SnapshotStore]:
    """快照仓库实例：已存在目录 + 每次运行唯一的两文件名。"""
    root = Path(os.environ.get("PYTEST_DB_DIR") or Path(__file__).resolve().parents[1] / ".pytest-dbs")
    root.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:10]
    api_name = f"models-dev-{uid}.json"
    meta_name = f"models-dev-{uid}.meta.json"
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_API_NAME", api_name)
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_META_NAME", meta_name)
    instance = SnapshotStore(root)
    yield instance
    for name in (api_name, meta_name):
        (root / name).unlink(missing_ok=True)
    for leftover in root.glob(f"{api_name}.*.tmp"):
        leftover.unlink(missing_ok=True)


def _meta(**overrides: Any) -> SnapshotMeta:
    base: dict[str, Any] = {
        "source_url": "https://models.dev/api.json",
        "fetched_at": 1_700_000_000_000,
        "last_attempt_date": "2026-11-16",
        "etag": "\"v1\"",
        "last_modified": "Mon, 16 Nov 2026 00:00:00 GMT",
        "http_date": "Mon, 16 Nov 2026 00:00:01 GMT",
    }
    base.update(overrides)
    return SnapshotMeta(**base)


# ---------------------------------------------------------------------------
# SnapshotStore
# ---------------------------------------------------------------------------


def test_store_roundtrip_writes_payload_and_meta(store: SnapshotStore) -> None:
    saved = store.save(PAYLOAD_BYTES, meta=_meta())

    assert saved.bytes == len(PAYLOAD_BYTES)
    assert (saved.providers, saved.entries) == (2, 1)
    assert saved.error_summary == ""
    assert store.present() is True
    assert store.load_api() == PAYLOAD
    assert store.load_bytes() == PAYLOAD_BYTES
    loaded = store.load_meta()
    assert loaded.etag == "\"v1\""
    assert loaded.fetched_at == 1_700_000_000_000
    assert loaded.source_url == "https://models.dev/api.json"
    assert list(store.root.glob(f"{store.api_path.name}.*.tmp")) == []


def test_store_refuses_invalid_payload_and_keeps_previous(store: SnapshotStore) -> None:
    store.save(PAYLOAD_BYTES, meta=_meta())

    with pytest.raises(SnapshotError):
        store.save(b"{not json", meta=_meta())
    with pytest.raises(SnapshotError):
        store.save(b"[]", meta=_meta())
    with pytest.raises(SnapshotError):
        store.save(b"", meta=_meta())

    assert store.load_api() == PAYLOAD
    assert store.load_meta().bytes == len(PAYLOAD_BYTES)


def test_store_missing_snapshot_is_empty(store: SnapshotStore) -> None:
    assert store.present() is False
    assert store.load_api() is None
    assert store.load_bytes() is None
    assert store.load_meta() == SnapshotMeta()


def test_store_corrupt_meta_falls_back_to_defaults(store: SnapshotStore) -> None:
    store.save(PAYLOAD_BYTES, meta=_meta())
    store.meta_path.write_text("{oops", encoding="utf-8")

    assert store.load_meta() == SnapshotMeta()
    assert store.load_api() == PAYLOAD
    assert store.present() is True


def test_store_failure_keeps_payload_and_validators(store: SnapshotStore) -> None:
    store.save(PAYLOAD_BYTES, meta=_meta())

    failed = store.save_failure("HTTP 503", attempted_at=1_700_000_100_000, last_attempt_date="2026-11-17")

    assert failed.error_summary == "HTTP 503"
    assert failed.error_at == 1_700_000_100_000
    assert failed.attempted_at == 1_700_000_100_000
    assert failed.last_attempt_date == "2026-11-17"
    assert failed.etag == "\"v1\""
    assert failed.fetched_at == 1_700_000_000_000
    assert store.load_api() == PAYLOAD
    assert store.present() is True


def test_store_failure_keeps_previous_attempt_date(store: SnapshotStore) -> None:
    store.save(PAYLOAD_BYTES, meta=_meta())

    failed = store.save_failure("boom")

    assert failed.last_attempt_date == "2026-11-16"
    assert failed.error_summary == "boom"


def test_snapshot_counts_ignores_malformed_providers() -> None:
    payload = {
        "a": {"models": {"x": {}, "y": {}}},
        "b": "junk",
        "c": {"models": []},
    }

    assert snapshot_counts(payload) == (2, 2)


# ---------------------------------------------------------------------------
# fetch_snapshot
# ---------------------------------------------------------------------------


async def test_fetch_sends_conditional_headers_and_reads_validators() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["if-none-match"] = request.headers.get("if-none-match", "")
        seen["if-modified-since"] = request.headers.get("if-modified-since", "")
        return httpx.Response(
            200,
            content=PAYLOAD_BYTES,
            headers={
                "etag": "\"v10\"",
                "last-modified": "Tue, 17 Nov 2026 00:00:00 GMT",
                "date": "Tue, 17 Nov 2026 00:00:01 GMT",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(
            client,
            "https://models.dev/api.json",
            etag="\"v9\"",
            last_modified="Mon, 16 Nov 2026 00:00:00 GMT",
        )

    assert seen["if-none-match"] == "\"v9\""
    assert seen["if-modified-since"] == "Mon, 16 Nov 2026 00:00:00 GMT"
    assert result.status == "ok"
    assert result.ok is True
    assert result.body == PAYLOAD_BYTES
    assert result.bytes == len(PAYLOAD_BYTES)
    assert result.etag == "\"v10\""
    assert result.last_modified == "Tue, 17 Nov 2026 00:00:00 GMT"
    assert result.http_date == "Tue, 17 Nov 2026 00:00:01 GMT"
    assert result.elapsed_ms >= 0


async def test_fetch_without_validators_omits_conditional_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["if-none-match"] = request.headers.get("if-none-match", "")
        seen["if-modified-since"] = request.headers.get("if-modified-since", "")
        return httpx.Response(200, content=PAYLOAD_BYTES)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(client, "https://models.dev/api.json")

    assert seen["if-none-match"] == ""
    assert seen["if-modified-since"] == ""
    assert result.status == "ok"


async def test_fetch_304_is_success_and_prefers_response_validator() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("if-none-match") == "\"v9\""
        return httpx.Response(304, headers={"etag": "\"v9\"", "date": "Tue, 17 Nov 2026 08:00:00 GMT"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(client, "https://models.dev/api.json", etag="\"v9\"")

    assert result.status == "not_modified"
    assert result.not_modified is True
    assert result.body is None
    assert result.bytes == 0
    assert result.status_code == 304
    assert result.etag == "\"v9\""
    assert result.http_date == "Tue, 17 Nov 2026 08:00:00 GMT"


async def test_fetch_304_without_response_etag_keeps_request_validator() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(
            client,
            "https://models.dev/api.json",
            etag="\"v9\"",
            last_modified="Mon, 16 Nov 2026 00:00:00 GMT",
        )

    assert result.status == "not_modified"
    assert result.etag == "\"v9\""
    assert result.last_modified == "Mon, 16 Nov 2026 00:00:00 GMT"


async def test_fetch_http_error_is_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(client, "https://models.dev/api.json")

    assert result.status == "error"
    assert result.status_code == 503
    assert result.error_summary == "HTTP 503"
    assert result.body is None


async def test_fetch_timeout_is_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(client, "https://models.dev/api.json", timeout_s=5)

    assert result.status == "error"
    assert result.error_summary.startswith("timeout after 5s")


async def test_fetch_transport_error_is_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(client, "https://models.dev/api.json")

    assert result.status == "error"
    assert "ConnectError" in result.error_summary


async def test_fetch_empty_body_is_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_snapshot(client, "https://models.dev/api.json")

    assert result.status == "error"
    assert result.error_summary == "empty body"


# ---------------------------------------------------------------------------
# model_defaults 表与 repo（ADR-073 D1）
# ---------------------------------------------------------------------------


async def test_repo_replace_list_get_and_count(client, app) -> None:
    async with app.state.session_factory() as session:
        assert await repo.count_model_defaults(session) == 0
        assert await repo.list_model_defaults(session) == []
        assert await repo.get_model_default(session, "gpt-4o") is None

    rows = [
        orm.ModelDefaultORM(
            model_id="gpt-4o",
            matched_by="exact",
            providers_json='[{"provider": "openai", "tier": "T1"}]',
            rule_version="1",
            fields_json='{"input_price_per_1m": 2.5}',
            pulled_at=1_700_000_000_000,
        ),
        orm.ModelDefaultORM(
            model_id="claude-sonnet-4",
            matched_by="normalized",
            pulled_at=1_700_000_000_000,
        ),
    ]
    async with app.state.session_factory() as session:
        assert await repo.replace_model_defaults(session, rows) == 2

    async with app.state.session_factory() as session:
        assert await repo.count_model_defaults(session) == 2
        listed = await repo.list_model_defaults(session)
        assert [item.model_id for item in listed] == ["claude-sonnet-4", "gpt-4o"]
        gpt = await repo.get_model_default(session, "gpt-4o")
        assert gpt is not None
        assert gpt.matched_by == "exact"
        assert gpt.providers_json.startswith("[{")
        assert gpt.fields_json == '{"input_price_per_1m": 2.5}'

    async with app.state.session_factory() as session:
        again = await repo.replace_model_defaults(
            session,
            [orm.ModelDefaultORM(model_id="gpt-4o", matched_by="alias", pulled_at=2)],
        )
        assert again == 1

    async with app.state.session_factory() as session:
        assert await repo.count_model_defaults(session) == 1
        listed = await repo.list_model_defaults(session)
        assert [item.model_id for item in listed] == ["gpt-4o"]
        assert listed[0].matched_by == "alias"
        assert listed[0].fields_json == "{}"


def test_model_default_orm_defaults_are_safe() -> None:
    columns = orm.ModelDefaultORM.__table__.c

    assert columns.providers_json.default.arg == "[]"
    assert columns.fields_json.default.arg == "{}"
    assert columns.matched_by.default.arg == ""
    assert columns.pulled_at.default.arg == 0


def _entry(
    provider: str,
    model_id: str,
    *,
    tier: str = TIER_UNRANKED,
    canonical: str = "",
    last_updated: str = "",
    **fields: Any,
) -> ModelEntry:
    return ModelEntry(
        provider=provider,
        model_id=model_id,
        tier=tier,
        canonical_model_id=canonical,
        last_updated=last_updated,
        fields=dict(fields),
    )


def _index(*entries: ModelEntry, aliases: dict[str, str] | None = None):
    return build_index(entries, aliases=aliases)


# ---------------------------------------------------------------------------
# 规则常量（ADR-073 决策 3/4）
# ---------------------------------------------------------------------------


def test_normalize_and_prefix_rules() -> None:
    assert normalize_model_id("GPT_4o") == "gpt-4o"
    assert normalize_model_id("claude:3.5") == "claude-3-5"
    assert strip_provider_prefix("openai/gpt-4o") == "gpt-4o"
    assert strip_provider_prefix("@cf/meta/llama") == "meta/llama"
    assert strip_provider_prefix("gpt-4o") == ""


def test_version_suffix_rules() -> None:
    assert strip_version_suffix("deepseek-v4.1-flash-1017") == "deepseek-v4.1-flash"
    assert strip_version_suffix("gpt-4o-2024-08-06") == "gpt-4o"
    assert strip_version_suffix("qwen3-v1.5") == "qwen3"
    assert strip_version_suffix("claude-sonnet-4-5") == ""
    assert strip_version_suffix("gpt-4o") == ""


def test_provider_tier_rules() -> None:
    assert provider_tier("openai", labs=1) == TIER_OFFICIAL
    assert provider_tier("openai", labs=4) == TIER_AGGREGATOR
    assert provider_tier("azure", labs=7) == TIER_CLOUD
    assert provider_tier("digitalocean", labs=9) == TIER_CLOUD
    assert provider_tier("some-reseller", labs=5) == TIER_AGGREGATOR
    assert provider_tier("x", npm="@ai-sdk/openai-compatible") == TIER_AGGREGATOR
    assert provider_tier("tiny", labs=2, npm="@ai-sdk/openai") == TIER_UNRANKED


def test_logical_model_key_prefers_canonical_majority() -> None:
    entries = [
        _entry("openai", "gpt-4o", canonical="openai/gpt-4o"),
        _entry("azure", "gpt-4o", canonical="openai/gpt-4o"),
        _entry("other", "gpt-4o", canonical="x/other"),
    ]

    assert logical_model_key(entries) == "openai/gpt-4o"
    assert logical_model_key(entries) == "openai/gpt-4o"
    assert logical_model_key([]) == ""


# ---------------------------------------------------------------------------
# 七级匹配（ADR-073 决策 3）
# ---------------------------------------------------------------------------


def test_match_exact_returns_whole_cluster() -> None:
    index = _index(
        _entry("openai", "gpt-4o", tier=TIER_OFFICIAL),
        _entry("azure", "gpt-4o", tier=TIER_CLOUD),
    )

    result = match_model("gpt-4o", index)

    assert result.matched_by == MATCH_EXACT
    assert result.matched is True
    assert {entry.provider for entry in result.entries} == {"openai", "azure"}
    assert result.score == 1.0
    assert result.version_loose is False
    assert result.candidates[0].provider == "openai"


def test_match_normalized_and_prefix_and_version_levels() -> None:
    index = _index(
        _entry("openai", "gpt-4o", tier=TIER_OFFICIAL),
        _entry("deepseek", "deepseek-v4.1-flash", tier=TIER_OFFICIAL),
    )

    normalized = match_model("GPT_4O", index)
    assert normalized.matched_by == MATCH_NORMALIZED
    assert normalized.entries[0].model_id == "gpt-4o"

    prefixed = match_model("openai/gpt-4o", index)
    assert prefixed.matched_by == MATCH_PREFIX_STRIPPED
    assert prefixed.entries[0].model_id == "gpt-4o"

    loose = match_model("deepseek-v4.1-flash-1017", index)
    assert loose.matched_by == MATCH_VERSION_LOOSE
    assert loose.version_loose is True
    assert loose.entries[0].model_id == "deepseek-v4.1-flash"


def test_match_alias_table() -> None:
    index = _index(
        _entry("anthropic", "claude-sonnet-4", tier=TIER_OFFICIAL),
        aliases={"claude-4-sonnet": "claude-sonnet-4"},
    )

    result = match_model("claude-4-sonnet", index)

    assert result.matched_by == MATCH_ALIAS
    assert result.entries[0].model_id == "claude-sonnet-4"


def test_match_fuzzy_auto_review_and_unmatched() -> None:
    index = _index(_entry("openai", "gpt-4o-mini", tier=TIER_OFFICIAL))

    auto = match_model("gpt-4o-minni", index, fuzzy_auto=0.9, fuzzy_review=0.7)
    assert auto.matched_by == MATCH_FUZZY_AUTO
    assert auto.entries[0].model_id == "gpt-4o-mini"

    review = match_model("gpt-4o-medium", index, fuzzy_auto=0.99, fuzzy_review=0.3)
    assert review.matched_by == MATCH_FUZZY_REVIEW
    assert review.entries == ()
    assert review.candidates[0].model_id == "gpt-4o-mini"

    missing = match_model("totally-unrelated-model", index, fuzzy_auto=0.99, fuzzy_review=0.99)
    assert missing.matched_by == MATCH_UNMATCHED
    assert missing.matched is False
    assert missing.candidates[0].model_id == "gpt-4o-mini"


def test_match_empty_index_is_unmatched() -> None:
    assert match_model("gpt-4o", build_index(())).matched_by == MATCH_UNMATCHED
    assert match_model("", _index(_entry("openai", "gpt-4o"))).matched_by == MATCH_UNMATCHED


# ---------------------------------------------------------------------------
# 字段级合并（ADR-073 决策 5）
# ---------------------------------------------------------------------------


def test_merge_prefers_official_tier_over_reseller() -> None:
    entries = [
        _entry("openai", "gpt-4o", tier=TIER_OFFICIAL, cost={"input": 2.5, "output": 10}),
        _entry("reseller", "gpt-4o", tier=TIER_AGGREGATOR, cost={"input": 99, "output": 99}),
    ]

    merged = merge_entries(entries)

    assert merged.tier == TIER_OFFICIAL
    assert merged.fields["cost"]["input"] == 2.5
    assert merged.fields["cost"]["output"] == 10.0
    assert [item["provider"] for item in merged.providers] == ["openai"]
    assert merged.rule_version == "1"


def test_merge_median_prices_and_null_cache() -> None:
    entries = [
        _entry("azure", "m", tier=TIER_CLOUD, cost={"input": 4, "output": 8}),
        _entry("google-vertex", "m", tier=TIER_CLOUD, cost={"input": 8, "output": 20}),
    ]

    merged = merge_entries(entries)

    assert merged.fields["cost"]["input"] == 6.0
    assert merged.fields["cost"]["output"] == 14.0
    assert merged.fields["cost"]["cache_read"] is None
    assert merged.fields["cost"]["cache_write"] is None


def test_merge_limits_max_and_prefill_input_precedence() -> None:
    entries = [
        _entry(
            "openai",
            "m",
            tier=TIER_OFFICIAL,
            limit={"context": 128000, "output": 16384},
        ),
        _entry(
            "anthropic",
            "m",
            tier=TIER_OFFICIAL,
            limit={"context": 200000, "input": 922000, "output": 32000},
        ),
    ]

    merged = merge_entries(entries)
    defaults = prefill_defaults(merged)

    assert merged.fields["limit"] == {"context": 200000, "input": 922000, "output": 32000}
    assert defaults["parameters"]["max_input_tokens"] == 922000
    assert defaults["parameters"]["max_output_tokens"] == 32000
    assert defaults["input_price_per_1m"] is None


def test_merge_limits_are_tier_scoped_but_prices_fall_back() -> None:
    entries = [
        _entry("openai", "m", tier=TIER_OFFICIAL, limit={"context": 128000}),
        _entry(
            "azure",
            "m",
            tier=TIER_CLOUD,
            cost={"input": 4, "output": 8},
            limit={"context": 200000, "input": 922000, "output": 32000},
        ),
    ]

    merged = merge_entries(entries)

    # 上限只在所选档内合并（决策 5「档内最大值」），价格该档全缺才降档。
    assert merged.tier == TIER_OFFICIAL
    assert merged.fields["limit"] == {"context": 128000, "input": None, "output": None}
    assert merged.fields["cost"]["input"] == 4.0
    assert merged.fields["cost"]["output"] == 8.0


def test_merge_modalities_union_and_text_fallback() -> None:
    entries = [
        _entry(
            "openai",
            "m",
            tier=TIER_OFFICIAL,
            modalities={"input": ["text", "image"], "output": ["text"]},
        ),
        _entry(
            "openai",
            "m2",
            tier=TIER_OFFICIAL,
            modalities={"input": ["image", "pdf"], "output": []},
        ),
    ]

    merged = merge_entries(entries)
    defaults = prefill_defaults(merged)

    assert merged.fields["modalities"]["input"] == ["text", "image", "pdf"]
    assert merged.fields["modalities"]["output"] == ["text"]
    assert defaults["parameters"]["input_types"] == ["text", "image", "pdf"]
    assert defaults["parameters"]["output_types"] == ["text"]


def test_merge_capabilities_model_type_and_reasoning_level() -> None:
    entries = [
        _entry(
            "anthropic",
            "claude",
            tier=TIER_OFFICIAL,
            tool_call=True,
            reasoning=False,
            attachment=True,
            structured_output=False,
            type="llm",
            reasoning_options=[{"type": "effort", "values": ["low", "medium"]}],
            status="deprecated",
        ),
        _entry(
            "anthropic",
            "claude",
            tier=TIER_OFFICIAL,
            reasoning=True,
            structured_output=True,
            reasoning_options=[{"type": "effort", "values": ["high"]}, {"type": "budget_tokens"}],
        ),
    ]

    merged = merge_entries(entries)
    defaults = prefill_defaults(merged)

    assert merged.fields["capabilities"] == {
        "tool_call": True,
        "reasoning": True,
        "attachment": True,
        "structured_output": True,
    }
    assert merged.fields["model_type"] == "chat"
    assert merged.fields["reasoning_level"] == "high"
    assert merged.fields["status"] == "deprecated"
    assert defaults["model_type"] == "chat"
    assert defaults["parameters"]["reasoning"] is True


def test_merge_falls_back_to_all_entries_when_unranked() -> None:
    entries = [
        _entry("tiny", "m", cost={"input": 1, "output": 2}),
        _entry("another", "m", cost={"input": 3, "output": 4}),
    ]

    merged = merge_entries(entries)

    assert merged.tier == TIER_UNRANKED
    assert merged.fields["cost"]["input"] == 2.0
    assert len(merged.providers) == 2


# ---------------------------------------------------------------------------
# 值比对保护（ADR-073 决策 6）
# ---------------------------------------------------------------------------


def test_value_compare_treats_equal_numbers_and_lists_as_same() -> None:
    assert same_value(2.0, 2) is True
    assert same_value(["text", "image"], ["text", "image"]) is True
    assert same_value(["text"], ["text", "image"]) is False
    assert same_value(None, None) is True
    assert same_value(0, "") is False


def test_adopt_value_keeps_manual_changes_and_updates_untouched_fields() -> None:
    assert adopt_value(current=2.5, baseline=2.5, candidate=3.0) == 3.0
    assert adopt_value(current=5.0, baseline=2.5, candidate=3.0) == 5.0
    assert adopt_value(current="", baseline=16384, candidate=32000) == ""
    assert adopt_value(current=None, baseline=None, candidate=128000) == 128000
# ---------------------------------------------------------------------------
# 步骤⑤：服务、惰性触发与预填端点（ADR-073 决策 2/6/7）
# ---------------------------------------------------------------------------


SNAPSHOT_PAYLOAD: dict[str, Any] = {
    "openai": {
        "id": "openai",
        "npm": "@ai-sdk/openai",
        "models": {
            "gpt-4o": {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "canonical_model_id": "gpt-4o",
                "last_updated": "2026-11-01",
                "cost": {"input": 2.5, "output": 10.0, "cache_read": 1.25},
                "limit": {"context": 128000, "input": 128000, "output": 16384},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "tool_call": True,
                "reasoning": False,
                "attachment": True,
                "structured_output": True,
                "type": "chat",
                "status": "stable",
            }
        },
    }
}
SNAPSHOT_BYTES = json.dumps(SNAPSHOT_PAYLOAD).encode("utf-8")


def _ok_result() -> FetchResult:
    return FetchResult(
        status="ok",
        body=SNAPSHOT_BYTES,
        etag='"v1"',
        last_modified="Mon, 16 Nov 2026 00:00:00 GMT",
        http_date="Mon, 16 Nov 2026 00:00:01 GMT",
        bytes=len(SNAPSHOT_BYTES),
        status_code=200,
        fetched_at=1_700_000_000_000,
    )


def _service(app, store: SnapshotStore, fetcher, **overrides: Any) -> ModelDefaultsService:
    settings = app.state.settings.model_copy(
        update={"models_dev_enabled": True, **overrides}
    )
    return ModelDefaultsService(
        app.state.session_factory, settings, store=store, fetcher=fetcher
    )


async def _admin_headers(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_channel_model(app, upstream_model: str = "gpt-4o") -> None:
    # 渠道先落库：channel_models 有指向 channels 的外键。
    async with app.state.session_factory() as session:
        session.add(orm.ChannelORM(id="ch-1", slug="ch1", name="CH1"))
        await session.commit()
    async with app.state.session_factory() as session:
        session.add(
            orm.ChannelModelORM(
                id="cm-1",
                channel_id="ch-1",
                upstream_model=upstream_model,
                call_model=upstream_model,
            )
        )
        await session.commit()


async def test_state_roundtrip_and_threshold_clamping(app, client) -> None:
    async with app.state.session_factory() as session:
        state = await get_state(session)
        assert state.last_attempt == ""
        assert state.last_pull == ""
        assert state.fuzzy_auto == pytest.approx(0.92)

        saved = await save_state(
            session,
            last_attempt="2026-11-16",
            last_pull="2026-11-16",
            fuzzy_auto=0.9,
            fuzzy_review=0.95,
            updated_by="tester",
        )
        assert saved.fuzzy_auto == pytest.approx(0.9)
        # 复核阈值不能高于自动阈值
        assert saved.fuzzy_review == pytest.approx(0.9)

    async with app.state.session_factory() as session:
        again = await get_state(session)
        assert again.last_attempt == "2026-11-16"
        assert again.last_pull == "2026-11-16"
        assert again.fuzzy_review == pytest.approx(0.9)
        assert again.updated_by == "tester"


async def test_refresh_pulls_snapshot_writes_state_and_derives_rows(
    app, client, store
) -> None:
    calls: list[SnapshotMeta] = []

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        calls.append(meta)
        return _ok_result()

    service = _service(app, store, fetcher)
    await _seed_channel_model(app)

    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, trigger="manual", force=True)
        assert outcome.status == "ok"
        assert outcome.refreshed is True
        assert outcome.providers == 1
        assert outcome.entries == 1
        assert outcome.defaults_count == 1

    assert len(calls) == 1
    assert store.present() is True

    async with app.state.session_factory() as session:
        rows = await repo.list_model_defaults(session)
        assert [row.model_id for row in rows] == ["gpt-4o"]
        assert rows[0].matched_by == MATCH_EXACT
        state = await get_state(session)
        assert state.last_pull != ""
        assert state.last_attempt == state.last_pull


async def test_refresh_failure_keeps_snapshot_and_skips_the_same_day(
    app, client, store
) -> None:
    store.save(SNAPSHOT_BYTES, meta=_meta())
    calls: list[int] = []

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        calls.append(1)
        return FetchResult(status="error", error_summary="timeout after 60s")

    service = _service(app, store, fetcher)

    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, trigger="manual", force=True)
        assert outcome.status == "error"
        assert outcome.error_summary == "timeout after 60s"
        assert outcome.last_pull == ""

    # 旧快照没被动过
    assert store.present() is True
    assert store.load_api() == SNAPSHOT_PAYLOAD

    async with app.state.session_factory() as session:
        state = await get_state(session)
        assert state.last_attempt != ""
        assert state.last_pull == ""

    # 同一天再触发：不再重试（决策 1）
    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, trigger="auto")
        assert outcome.status == "skipped"

    assert len(calls) == 1


async def test_refresh_retries_the_next_day_after_a_failure(
    app, client, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        calls.append(1)
        return FetchResult(status="error", error_summary="boom")

    service = _service(app, store, fetcher)
    monkeypatch.setattr(service, "_today", lambda: "2026-11-16")

    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, trigger="auto")
        assert outcome.status == "error"

    monkeypatch.setattr(service, "_today", lambda: "2026-11-17")
    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, trigger="auto")
        assert outcome.status == "error"

    assert len(calls) == 2


async def test_not_modified_only_updates_bookkeeping(app, client, store) -> None:
    def fetcher(meta: SnapshotMeta) -> FetchResult:
        return FetchResult(
            status="not_modified",
            status_code=304,
            etag='"v1"',
            http_date="Mon, 16 Nov 2026 00:00:01 GMT",
        )

    service = _service(app, store, fetcher)

    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, trigger="manual", force=True)
        assert outcome.status == "not_modified"
        assert outcome.not_modified is True
        assert outcome.refreshed is False

    async with app.state.session_factory() as session:
        state = await get_state(session)
        assert state.last_pull != ""
        assert state.last_attempt == state.last_pull


async def test_prefill_returns_defaults_from_the_snapshot(app, client, store) -> None:
    store.save(SNAPSHOT_BYTES, meta=_meta())
    service = _service(app, store, lambda meta: _ok_result())

    async with app.state.session_factory() as session:
        result = await service.prefill(session, "gpt-4o")

    assert result["matched"] is True
    assert result["matched_by"] == MATCH_EXACT
    defaults = result["defaults"]
    assert defaults["model_type"] == "chat"
    assert defaults["model_type_source"] == "upstream"
    parameters = defaults["parameters"]
    assert parameters["max_input_tokens"] == 128000
    assert parameters["max_output_tokens"] == 16384
    assert parameters["input_types"] == ["text", "image"]
    assert parameters["output_types"] == ["text"]
    assert parameters["tool_call"] is True
    assert parameters["reasoning"] is False
    assert parameters["attachment"] is True
    assert parameters["structured_output"] is True
    assert defaults["input_price_per_1m"] == 2.5
    assert defaults["output_price_per_1m"] == 10.0
    assert defaults["cache_read_price_per_1m"] == 1.25
    assert defaults["cache_write_price_per_1m"] is None
    assert result["sources"]["input_price_per_1m"]["providers"] == ["openai"]
    assert result["provenance"]["model_id"] == "gpt-4o"
    assert result["provenance"]["provider"] == "openai"
    assert result["review_required"] is False
    assert result["snapshot"]["available"] is True


async def test_prefill_without_snapshot_returns_an_empty_result(app, client, store) -> None:
    service = _service(app, store, lambda meta: _ok_result())

    async with app.state.session_factory() as session:
        result = await service.prefill(session, "gpt-4o")

    assert result["matched"] is False
    assert result["defaults"] is None
    assert result["snapshot"]["available"] is False


async def test_prefill_of_an_unknown_model_is_not_matched(app, client, store) -> None:
    store.save(SNAPSHOT_BYTES, meta=_meta())
    service = _service(app, store, lambda meta: _ok_result())

    async with app.state.session_factory() as session:
        result = await service.prefill(session, "totally-unrelated-model-xyz")

    assert result["matched"] is False
    assert result["defaults"] is None


async def test_prefill_endpoint_returns_defaults(app, client, store) -> None:
    store.save(SNAPSHOT_BYTES, meta=_meta())
    app.state.model_defaults = _service(app, store, lambda meta: _ok_result())
    headers = await _admin_headers(client)

    response = await client.post(
        "/api/channel-models/prefill",
        json={"upstream_model": "gpt-4o"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["matched"] is True
    assert payload["matched_by"] == MATCH_EXACT
    assert payload["defaults"]["parameters"]["max_input_tokens"] == 128000
    assert payload["defaults"]["input_price_per_1m"] == 2.5
    assert payload["snapshot"]["available"] is True


async def test_prefill_endpoint_never_pulls(app, client, store) -> None:
    calls: list[int] = []

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        calls.append(1)
        return _ok_result()

    app.state.model_defaults = _service(app, store, fetcher)
    headers = await _admin_headers(client)

    response = await client.post(
        "/api/channel-models/prefill",
        json={"upstream_model": "gpt-4o"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert calls == []


async def test_status_endpoint_reports_the_sync_state(app, client, store) -> None:
    app.state.model_defaults = _service(app, store, lambda meta: _ok_result())
    headers = await _admin_headers(client)

    response = await client.get("/api/model-defaults/status", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["rule_version"] == "1"
    assert payload["snapshot"]["available"] is False
    assert payload["defaults_count"] == 0


async def test_refresh_endpoint_forces_a_pull(app, client, store) -> None:
    app.state.model_defaults = _service(app, store, lambda meta: _ok_result())
    headers = await _admin_headers(client)

    response = await client.post("/api/model-defaults/refresh", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["refreshed"] is True
    assert payload["providers"] == 1
    assert payload["entries"] == 1
    assert payload["last_pull"] != ""


async def test_catalog_read_triggers_the_daily_pull_once(app, client, store) -> None:
    calls: list[int] = []

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        calls.append(1)
        return _ok_result()

    app.state.model_defaults = _service(app, store, fetcher)
    headers = await _admin_headers(client)

    first = await client.get("/api/catalog", headers=headers)
    assert first.status_code == 200, first.text
    second = await client.get("/api/catalog", headers=headers)
    assert second.status_code == 200, second.text

    assert len(calls) == 1

    async with app.state.session_factory() as session:
        state = await get_state(session)
        assert state.last_pull != ""
        assert state.last_attempt == state.last_pull


async def test_catalog_read_survives_a_failed_pull(app, client, store) -> None:
    def fetcher(meta: SnapshotMeta) -> FetchResult:
        return FetchResult(status="error", error_summary="boom")

    app.state.model_defaults = _service(app, store, fetcher)
    headers = await _admin_headers(client)

    response = await client.get("/api/catalog", headers=headers)

    assert response.status_code == 200, response.text

    async with app.state.session_factory() as session:
        state = await get_state(session)
        assert state.last_attempt != ""
        assert state.last_pull == ""


async def test_manual_refresh_bypasses_the_same_day_check(app, client, store) -> None:
    calls: list[int] = []

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        calls.append(1)
        return _ok_result()

    app.state.model_defaults = _service(app, store, fetcher)
    headers = await _admin_headers(client)

    first = await client.post("/api/model-defaults/refresh", headers=headers)
    second = await client.post("/api/model-defaults/refresh", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(calls) == 2

