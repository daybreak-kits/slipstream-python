"""request_logs.resolved_combo_id is written for manual combos only.

The analytics axes key the "combo model" dimension by entity id, so a manual
combo request must carry the id; direct calls and implicit (auto) groups have no
row and stay null, which is what lets the axis fall back to the name.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select

from l04_repo import orm
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


async def _channel(
    client,
    headers: dict[str, str],
    url: str,
    *,
    name: str,
    upstream_model: str = "fake-model",
    call_model: str = "leaf",
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    channel = response.json()
    model = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": upstream_model, "call_model": call_model},
    )
    assert model.status_code == 201, model.text
    return channel, model.json()


async def _key(client, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": "combo-id"})
    assert response.status_code == 201, response.text
    return response.json()


async def _combo(
    client, headers: dict[str, str], name: str, model_id: str
) -> dict[str, Any]:
    response = await client.post(
        "/api/combos",
        headers=headers,
        json={
            "name": name,
            "enabled": True,
            "targets": [
                {"target_type": "channel_model", "channel_model_id": model_id, "priority": 1}
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def _log_row(client, request_id: str, timeout: float = 3.0) -> orm.RequestLogORM:
    factory = client._transport.app.state.session_factory
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        async with factory() as session:
            result = await session.execute(
                select(orm.RequestLogORM).where(orm.RequestLogORM.request_id == request_id)
            )
            row = result.scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"log row {request_id} not found") from last


async def _refresh_catalog(client) -> Any:
    """Reload the in-process catalog snapshot and hand it back.

    Management writes mark the snapshot dirty (``repo._arm_catalog_dirty`` on
    commit), so the next request already sees them. Refreshing here keeps a case
    independent of the snapshot an earlier test in the same process left behind,
    and lets the case assert the row is resolvable before it calls."""
    catalog = client._transport.app.state.catalog
    await catalog.refresh()
    return catalog.snapshot

async def test_combo_request_records_the_combo_id(client, upstream):
    headers = await _token(client)
    _, model = await _channel(client, headers, upstream.base_url, name="combo-chan")
    combo = await _combo(client, headers, "team-chat", model["id"])
    snapshot = await _refresh_catalog(client)
    assert snapshot is not None and combo["id"] in snapshot.combos_by_id
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("team-chat"),
    )
    assert response.status_code == 200, response.text

    row = await _log_row(client, response.headers["X-Request-ID"])
    assert row.resolved_combo_id == combo["id"]
    assert row.final_channel_model_id == model["id"]


async def test_direct_request_leaves_the_combo_id_null(client, upstream):
    headers = await _token(client)
    channel, model = await _channel(client, headers, upstream.base_url, name="direct-chan")
    snapshot = await _refresh_catalog(client)
    assert snapshot is not None and model["id"] in snapshot.models
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{channel['slug']}/{model['call_model']}"),
    )
    assert response.status_code == 200, response.text

    row = await _log_row(client, response.headers["X-Request-ID"])
    assert row.resolved_combo_id is None
    assert row.final_channel_model_id == model["id"]


async def test_auto_group_request_leaves_the_combo_id_null(client, upstream):
    headers = await _token(client)
    # Implicit groups are derived per call_model, so two channels that publish
    # different upstream models under the same call name form one group.
    await _channel(
        client, headers, upstream.base_url, name="auto-a",
        upstream_model="shared-model-a", call_model="shared-leaf",
    )
    await _channel(
        client, headers, upstream.base_url, name="auto-b",
        upstream_model="shared-model-b", call_model="shared-leaf",
    )
    snapshot = await _refresh_catalog(client)
    assert snapshot is not None
    group = snapshot.groups.get("shared-leaf")
    assert group is not None and len(group.members) == 2
    key = await _key(client, headers)

    # No combo row owns this name: it resolves to the implicit group derived
    # from the shared call name, which has no entity id to record.
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("shared-leaf"),
    )
    assert response.status_code == 200, response.text

    row = await _log_row(client, response.headers["X-Request-ID"])
    assert row.resolved_combo_id is None
    assert row.final_channel_model_id is not None


async def test_failed_combo_request_still_records_the_combo_id(client, upstream):
    headers = await _token(client)
    _, model = await _channel(
        client, headers, upstream.base_url + "/broken", name="broken-chan"
    )
    combo = await _combo(client, headers, "broken-combo", model["id"])
    snapshot = await _refresh_catalog(client)
    assert snapshot is not None and combo["id"] in snapshot.combos_by_id
    key = await _key(client, headers)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body("broken-combo"),
    )
    assert response.status_code == 502, response.text

    row = await _log_row(client, response.headers["X-Request-ID"])
    assert row.resolved_combo_id == combo["id"]
    assert row.http_status == 502
