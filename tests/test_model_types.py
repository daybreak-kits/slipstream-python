from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from l03_domain.model_types import (
    MODEL_TYPE_CHAT,
    MODEL_TYPE_EMBEDDING,
    MODEL_TYPE_RERANK,
    infer_model_type,
    infer_model_type_source,
    normalize_model_type,
    normalize_model_type_source,
)
from tests.fake_upstream_server import FakeUpstreamServer


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(client, headers, url: str, **overrides) -> dict:
    payload = {
        "name": "模型类型渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, **overrides) -> dict:
    payload = {"upstream_model": "fake-model", **overrides}
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 纯函数：类型推断与校验
# ---------------------------------------------------------------------------
def test_infer_model_type_recognises_embedding_and_rerank_names() -> None:
    assert infer_model_type("text-embedding-3-small") == MODEL_TYPE_EMBEDDING
    assert infer_model_type("bge-large-en") == MODEL_TYPE_EMBEDDING
    assert infer_model_type("bge-reranker-v2") == MODEL_TYPE_RERANK
    assert infer_model_type("rerank-v3") == MODEL_TYPE_RERANK
    assert infer_model_type("gpt-4o") is None


def test_infer_model_type_source_marks_upstream_only_when_inferred() -> None:
    assert infer_model_type_source("text-embedding-3-small") == (
        MODEL_TYPE_EMBEDDING,
        "upstream",
    )
    assert infer_model_type_source("gpt-4o") == (MODEL_TYPE_CHAT, "default")


def test_normalize_rejects_unknown_model_types() -> None:
    assert normalize_model_type("Embedding") == MODEL_TYPE_EMBEDDING
    assert normalize_model_type_source("MANUAL") == "manual"
    with pytest.raises(ValueError):
        normalize_model_type("banana")
    with pytest.raises(ValueError):
        normalize_model_type_source("guessed")


# ---------------------------------------------------------------------------
# 存储层：默认类型、来源、非法值
# ---------------------------------------------------------------------------
async def test_model_type_defaults_to_chat_on_create(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")

    created = await _model(client, headers, channel["id"])

    assert created["model_type"] == MODEL_TYPE_CHAT
    assert created["model_type_source"] == "default"

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.status_code == 200, listed.text
    item = next(
        entry for entry in listed.json()["items"] if entry["id"] == created["id"]
    )
    assert item["model_type"] == MODEL_TYPE_CHAT
    assert item["model_type_source"] == "default"


async def test_create_with_explicit_type_marks_source_manual(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")

    created = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="text-embedding-3-small",
        model_type=MODEL_TYPE_EMBEDDING,
    )

    assert created["model_type"] == MODEL_TYPE_EMBEDDING
    assert created["model_type_source"] == "manual"


async def test_patch_model_type_switches_source_to_manual(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    created = await _model(client, headers, channel["id"])

    patched = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"model_type": MODEL_TYPE_RERANK},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["model_type"] == MODEL_TYPE_RERANK
    assert patched.json()["model_type_source"] == "manual"


async def test_invalid_model_type_is_rejected(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")

    created = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "model_type": "banana"},
    )
    assert created.status_code == 400, created.text
    assert created.json()["code"] == "INVALID_REQUEST"

    existing = await _model(client, headers, channel["id"])
    patched = await client.patch(
        f"/api/channel-models/{existing['id']}",
        headers=headers,
        json={"model_type": "banana"},
    )
    assert patched.status_code == 400, patched.text
    assert patched.json()["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 探测：embedding / rerank 走自己的端点
# ---------------------------------------------------------------------------
async def test_embedding_probe_calls_embeddings_endpoint(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="text-embedding-3-small",
        model_type=MODEL_TYPE_EMBEDDING,
    )

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_type"] == MODEL_TYPE_EMBEDDING
    assert upstream.find("POST", "/embeddings")
    assert not upstream.find("POST", "/chat/completions")


async def test_rerank_probe_calls_rerank_endpoint(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="bge-reranker-v2",
        model_type=MODEL_TYPE_RERANK,
    )

    response = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_type"] == MODEL_TYPE_RERANK
    assert upstream.find("POST", "/rerank")
    assert not upstream.find("POST", "/chat/completions")


async def test_rerank_probe_falls_back_to_ranking_dialect_and_caches_it(
    client, upstream
) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai-ranking")
    model = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="bge-reranker-v2",
        model_type=MODEL_TYPE_RERANK,
    )

    first = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ok"
    assert upstream.find("POST", "/rerank"), "应该先试过 /rerank 方言"
    assert upstream.find("POST", "/ranking"), "应该回退到 /ranking 方言"

    before = len(upstream.requests)
    second = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ok"

    new_paths = [item["path"] for item in upstream.requests[before:]]
    assert new_paths, "第二次探测应该有请求"
    assert not any(path.endswith("/rerank") for path in new_paths), new_paths
    assert any(path.endswith("/ranking") for path in new_paths), new_paths


async def test_image_model_probe_requires_force_true(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    model = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="fake-image",
        model_type="image_generation",
    )

    blocked = await client.post(
        f"/api/channel-models/{model['id']}/probe", headers=headers
    )
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["code"] == "INVALID_REQUEST"
    assert "force=true" in json.dumps(blocked.json(), ensure_ascii=False)

    forced = await client.post(
        f"/api/channel-models/{model['id']}/probe",
        headers=headers,
        json={"force": True},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["status"] == "ok"


async def test_probe_all_skips_models_that_cannot_be_probed(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    await _model(client, headers, channel["id"], upstream_model="fake-model")
    await _model(
        client,
        headers,
        channel["id"],
        upstream_model="fake-video",
        model_type="video",
    )

    response = await client.post(
        f"/api/channels/{channel['id']}/probe-all", headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["skipped"] == 1
    assert len(body["items"]) == 1


# ---------------------------------------------------------------------------
# 上游同步与目录：类型字段要带出来
# ---------------------------------------------------------------------------
async def test_discover_models_reports_inferred_model_types(client, upstream) -> None:
    headers = await _token(client)

    response = await client.post(
        "/api/channels/discover-models",
        headers=headers,
        json={
            "connection_url": f"{upstream.base_url}/openai-typed",
            "api_key": "channel-secret-key",
        },
    )

    assert response.status_code == 200, response.text
    models = {
        item["upstream_model"]: item for item in response.json()["models"]
    }
    assert models["text-embedding-3-small"]["model_type"] == MODEL_TYPE_EMBEDDING
    assert models["text-embedding-3-small"]["model_type_source"] == "upstream"
    assert models["bge-reranker-v2"]["model_type"] == MODEL_TYPE_RERANK
    assert models["bge-reranker-v2"]["model_type_source"] == "upstream"
    assert models["fake-model"]["model_type"] == MODEL_TYPE_CHAT
    assert models["fake-model"]["model_type_source"] == "default"


async def test_catalog_entries_expose_model_type(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    embedding = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="text-embedding-3-small",
        model_type=MODEL_TYPE_EMBEDDING,
    )
    chat = await _model(client, headers, channel["id"], upstream_model="fake-model")

    response = await client.get("/api/catalog", headers=headers)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    direct = {
        item["name"]: item for item in items if item.get("kind") == "direct"
    }
    embed_entry = direct[f"{channel['slug']}/{embedding['call_model']}"]
    chat_entry = direct[f"{channel['slug']}/{chat['call_model']}"]
    assert embed_entry["model_type"] == MODEL_TYPE_EMBEDDING
    assert embed_entry["model_type_source"] == "manual"
    assert chat_entry["model_type"] == MODEL_TYPE_CHAT
    assert chat_entry["model_type_source"] == "default"


async def test_bulk_create_infers_model_type_from_upstream_name(
    client, upstream
) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")

    response = await client.post(
        f"/api/channels/{channel['id']}/models/bulk",
        headers=headers,
        json={
            "upstream_models": [
                "text-embedding-3-small",
                "bge-reranker-v2",
                "gpt-4o",
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 3
    items = {item["upstream_model"]: item for item in body["items"]}
    assert items["text-embedding-3-small"]["model_type"] == MODEL_TYPE_EMBEDDING
    assert items["text-embedding-3-small"]["model_type_source"] == "upstream"
    assert items["bge-reranker-v2"]["model_type"] == MODEL_TYPE_RERANK
    assert items["bge-reranker-v2"]["model_type_source"] == "upstream"
    assert items["gpt-4o"]["model_type"] == MODEL_TYPE_CHAT
    assert items["gpt-4o"]["model_type_source"] == "default"


def test_legacy_channel_models_get_model_type_backfilled() -> None:
    from sqlalchemy import create_engine

    from l02_infra.db import _migrate_legacy_schema

    root = Path(__file__).resolve().parents[1] / ".pytest-dbs"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"legacy-model-types-{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE channel_models ("
                "id TEXT PRIMARY KEY, channel_id TEXT, "
                "upstream_model TEXT, call_model TEXT)"
            )
            for index, name in enumerate(
                ["gpt-4o", "text-embedding-3-small", "bge-reranker-v2"]
            ):
                conn.exec_driver_sql(
                    "INSERT INTO channel_models "
                    "(id, channel_id, upstream_model, call_model) "
                    "VALUES (?, ?, ?, ?)",
                    (f"m{index}", "c1", name, name),
                )
            _migrate_legacy_schema(conn)
            rows = conn.exec_driver_sql(
                "SELECT upstream_model, model_type, model_type_source "
                "FROM channel_models ORDER BY upstream_model"
            ).fetchall()
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)

    assert rows == [
        ("bge-reranker-v2", "rerank", "upstream"),
        ("gpt-4o", "chat", "default"),
        ("text-embedding-3-small", "embedding", "upstream"),
    ]

# ---------------------------------------------------------------------------
# 编辑弹窗的「自动」语义：model_type=auto 按上游模型名重新推断
# ---------------------------------------------------------------------------
async def test_patch_auto_reinfers_type_from_upstream_name(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    created = await _model(
        client, headers, channel["id"], upstream_model="text-embedding-3-small"
    )
    # 不显式给类型时按约定落 chat/default，即便名字看起来像 embedding。
    assert created["model_type"] == MODEL_TYPE_CHAT
    assert created["model_type_source"] == "default"

    manual = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"model_type": MODEL_TYPE_EMBEDDING},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["model_type_source"] == "manual"

    reset = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"model_type": "auto"},
    )
    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert body["model_type"] == MODEL_TYPE_EMBEDDING
    assert body["model_type_source"] == "upstream"


async def test_patch_auto_falls_back_to_chat_default_for_plain_names(client, upstream) -> None:
    headers = await _token(client)
    channel = await _channel(client, headers, f"{upstream.base_url}/openai")
    created = await _model(
        client,
        headers,
        channel["id"],
        upstream_model="gpt-4o",
        model_type=MODEL_TYPE_RERANK,
    )
    assert created["model_type_source"] == "manual"

    reset = await client.patch(
        f"/api/channel-models/{created['id']}",
        headers=headers,
        json={"model_type": "auto"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["model_type"] == MODEL_TYPE_CHAT
    assert reset.json()["model_type_source"] == "default"

    listed = await client.get(f"/api/channels/{channel['id']}/models", headers=headers)
    assert listed.status_code == 200, listed.text
    item = next(
        entry for entry in listed.json()["items"] if entry["id"] == created["id"]
    )
    assert item["model_type"] == MODEL_TYPE_CHAT
    assert item["model_type_source"] == "default"
