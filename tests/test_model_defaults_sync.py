"""models.dev 值比对同步与「恢复自动值」的单元测试（ADR-073 决策 6）。

覆盖：首次物化不写（无基线）、等于基线才采用新值、人工改动保留、
force 恢复全部字段、status 两维陈旧度、档位名单与阈值进配置（决策 4）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from l03_domain.model_defaults_rules import (
    LABS_MIN_PROVIDERS,
    TIER_AGGREGATOR,
    TIER_CLOUD,
    TIER_OFFICIAL,
    provider_tier,
)
from l04_repo import orm, repo
from l05_service.model_defaults import (
    FetchResult,
    ModelDefaultsService,
    SnapshotMeta,
    SnapshotStore,
    get_state,
    save_state,
)
from l05_service.model_defaults import snapshot_store as snapshot_module

BASE_FIELDS: dict = {
    "id": "gpt-4o",
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


def _payload(fields: dict) -> dict:
    return {"openai": {"id": "openai", "npm": "@ai-sdk/openai", "models": {"gpt-4o": fields}}}


SNAPSHOT_V1 = _payload(BASE_FIELDS)
SNAPSHOT_V2 = _payload(
    dict(BASE_FIELDS, cost={"input": 3.0, "output": 10.0, "cache_read": 1.25},
         limit={"context": 128000, "input": 128000, "output": 32768}),
)

_SEED_PARAMS: dict = {
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "reasoning_level": "",
    "input_types": ["text", "image"],
    "output_types": ["text"],
    "tool_call": True,
    "reasoning": False,
    "attachment": True,
    "structured_output": True,
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[SnapshotStore]:
    root = Path(os.environ.get("PYTEST_DB_DIR") or Path(__file__).resolve().parents[1] / ".pytest-dbs")
    root.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:10]
    api_name = "models-dev-sync-" + uid + ".json"
    meta_name = "models-dev-sync-" + uid + ".meta.json"
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_API_NAME", api_name)
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_META_NAME", meta_name)
    instance = SnapshotStore(root)
    yield instance
    for name in (api_name, meta_name):
        (root / name).unlink(missing_ok=True)


def _meta() -> SnapshotMeta:
    return SnapshotMeta(
        source_url="https://models.dev/api.json",
        fetched_at=1_700_000_000_000,
        last_attempt_date="2026-11-16",
    )


def _service(app, store: SnapshotStore, payloads: list) -> ModelDefaultsService:
    """fetcher 按调用次序返回 payloads（最后一次重复），模拟快照演进。"""
    settings = app.state.settings.model_copy(update={"models_dev_enabled": True})
    calls = {"n": 0}

    def fetcher(meta: SnapshotMeta) -> FetchResult:
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        body = json.dumps(payload).encode("utf-8")
        return FetchResult(
            status="ok",
            body=body,
            bytes=len(body),
            status_code=200,
            fetched_at=1_700_000_000_000,
        )

    return ModelDefaultsService(
        app.state.session_factory, settings, store=store, fetcher=fetcher
    )


async def _seed_channel_model(app, params: dict = None, prices: dict = None) -> None:
    async with app.state.session_factory() as session:
        session.add(orm.ChannelORM(id="ch-1", slug="ch1", name="CH1"))
        await session.commit()
    async with app.state.session_factory() as session:
        row = orm.ChannelModelORM(
            id="cm-1",
            channel_id="ch-1",
            upstream_model="gpt-4o",
            call_model="gpt-4o",
            parameters_json=json.dumps(params) if params is not None else None,
        )
        for key, value in (prices or {}).items():
            setattr(row, key, value)
        session.add(row)
        await session.commit()


async def _load_cm(app) -> orm.ChannelModelORM:
    async with app.state.session_factory() as session:
        model = await repo.get_channel_model(session, "cm-1")
        assert model is not None
        session.expunge(model)
        return model


# ---------------------------------------------------------------------------
# 值比对同步
# ---------------------------------------------------------------------------


async def test_first_refresh_writes_nothing_without_baseline(app, client, store) -> None:
    await _seed_channel_model(app, params=_SEED_PARAMS, prices={"input_price_per_1m": 9.9})
    service = _service(app, store, [SNAPSHOT_V1])

    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, force=True)

    assert outcome.status == "ok"
    assert outcome.synced == 0
    model = await _load_cm(app)
    assert model.input_price_per_1m == 9.9


async def test_sync_adopts_equal_baseline_and_preserves_manual_values(app, client, store) -> None:
    # 种子值与快照一致（视为自动值），input_types 手改成 ["text"]（人工值）。
    manual = dict(_SEED_PARAMS)
    manual["input_types"] = ["text"]
    await _seed_channel_model(app, params=manual, prices={"input_price_per_1m": 2.5, "output_price_per_1m": 10.0})
    service = _service(app, store, [SNAPSHOT_V1, SNAPSHOT_V2])
    async with app.state.session_factory() as session:
        await service.refresh(session, force=True)  # 建立基线，本次不写

    async with app.state.session_factory() as session:
        outcome = await service.refresh(session, force=True)  # 第二次拉到 V2

    assert outcome.status == "ok"
    assert outcome.synced == 1
    model = await _load_cm(app)
    params = json.loads(model.parameters_json)
    # 等于基线的字段跟新拉取值
    assert model.input_price_per_1m == 3.0
    assert params["max_output_tokens"] == 32768
    # 人工值 != 基线 → 保留
    assert params["input_types"] == ["text"]
    assert model.output_price_per_1m == 10.0


async def test_adopt_defaults_force_restores_all_fields(app, client, store) -> None:
    await _seed_channel_model(app, params=_SEED_PARAMS, prices={"input_price_per_1m": 2.5})
    service = _service(app, store, [SNAPSHOT_V1])
    async with app.state.session_factory() as session:
        await service.refresh(session, force=True)
        model = await repo.get_channel_model(session, "cm-1")
        result = await service.adopt_defaults(session, model, force=False)
    assert result["matched"] is True
    assert result["matched_by"] == "exact"
    model = await _load_cm(app)
    assert model.input_price_per_1m == 2.5  # 与基线相等，无变化

    # 管理员手改价格后：非 force 保留，force 恢复快照值
    async with app.state.session_factory() as session:
        model = await repo.get_channel_model(session, "cm-1")
        model.input_price_per_1m = 9.9
        await repo.save_channel_model(session, model)
    async with app.state.session_factory() as session:
        model = await repo.get_channel_model(session, "cm-1")
        result = await service.adopt_defaults(session, model, force=False)
    model = await _load_cm(app)
    assert model.input_price_per_1m == 9.9
    assert "input_price_per_1m" not in result["adopted"]

    async with app.state.session_factory() as session:
        model = await repo.get_channel_model(session, "cm-1")
        result = await service.adopt_defaults(session, model, force=True)
    model = await _load_cm(app)
    assert model.input_price_per_1m == 2.5
    assert "input_price_per_1m" in result["adopted"]
    assert json.loads(model.parameters_json)["max_output_tokens"] == 16384


# ---------------------------------------------------------------------------
# status 两维陈旧度与档位配置
# ---------------------------------------------------------------------------


async def test_status_reports_snapshot_and_registry_dimensions(app, client, store) -> None:
    await _seed_channel_model(app)
    service = _service(app, store, [SNAPSHOT_V1])
    async with app.state.session_factory() as session:
        await service.refresh(session, force=True)
        payload = await service.status(session)

    assert payload["snapshot"]["available"] is True
    assert payload["registry"]["models"] == 1
    assert payload["registry"]["last_updated"] == "2026-11-01"
    assert payload["tier_official"] is None
    assert payload["tier_clouds"] is None
    assert payload["labs_min_providers"] == LABS_MIN_PROVIDERS

    async with app.state.session_factory() as session:
        await save_state(
            session,
            last_attempt="2026-11-16",
            tier_official=("openai", "custom-lab"),
            tier_clouds=(),
            labs_min_providers=6,
            updated_by="tester",
        )
        again = await service.status(session)
    assert again["tier_official"] == ["openai", "custom-lab"]
    assert again["tier_clouds"] == []
    assert again["labs_min_providers"] == 6


async def test_tier_overrides_reach_the_index(app, client, store) -> None:
    await _seed_channel_model(app)
    service = _service(app, store, [SNAPSHOT_V1])
    async with app.state.session_factory() as session:
        await save_state(
            session,
            tier_official=("acme-lab",),
            labs_min_providers=2,
        )
        await service.refresh(session, force=True)
        rows = await repo.list_model_defaults(session)
    # openai 被覆盖名单顶掉 T1（labs=1 也不够 T3）后仍唯一成行。
    assert len(rows) == 1
    providers = json.loads(rows[0].providers_json)
    assert providers[0]["provider"] == "openai"


# ---------------------------------------------------------------------------
# 纯函数：档位覆盖参数
# ---------------------------------------------------------------------------


def test_provider_tier_accepts_list_and_threshold_overrides() -> None:
    assert provider_tier("acme-lab", labs=0, official=("acme-lab",)) == TIER_OFFICIAL
    assert provider_tier("openai", labs=0, official=()) == "unranked"
    assert provider_tier("acme-cloud", labs=0, clouds=("acme-cloud",)) == TIER_CLOUD
    assert provider_tier("aggregator", labs=2, labs_min=2) == TIER_AGGREGATOR
    assert provider_tier("aggregator", labs=2) == "unranked"


async def _admin_headers(client) -> dict:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def test_state_roundtrips_tier_overrides(app, client) -> None:
    async with app.state.session_factory() as session:
        await save_state(session, last_attempt="2026-11-16", tier_official=("a", "b"), labs_min_providers=8)
        state = await get_state(session)
        assert state.tier_official == ("a", "b")
        assert state.tier_clouds is None
        assert state.labs_min_providers == 8


# ---------------------------------------------------------------------------
# 端点：恢复自动值
# ---------------------------------------------------------------------------


async def test_adopt_defaults_endpoint_restores_values(app, client, store) -> None:
    await _seed_channel_model(app, params=_SEED_PARAMS, prices={"input_price_per_1m": 2.5})
    service = _service(app, store, [SNAPSHOT_V1])
    app.state.model_defaults = service
    async with app.state.session_factory() as session:
        await service.refresh(session, force=True)
    async with app.state.session_factory() as session:
        model = await repo.get_channel_model(session, "cm-1")
        model.input_price_per_1m = 9.9
        await repo.save_channel_model(session, model)
    headers = await _admin_headers(client)

    non_force = await client.post(
        "/api/channel-models/cm-1/adopt-defaults",
        json={"force": False},
        headers=headers,
    )
    assert non_force.status_code == 200, non_force.text
    body = non_force.json()
    assert body["matched"] is True
    assert body["adopted"] == []

    forced = await client.post(
        "/api/channel-models/cm-1/adopt-defaults",
        json={"force": True},
        headers=headers,
    )
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert "input_price_per_1m" in body["adopted"]
    model = await _load_cm(app)
    assert model.input_price_per_1m == 2.5
