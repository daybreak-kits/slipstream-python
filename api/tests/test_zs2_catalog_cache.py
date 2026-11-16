from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event

from l01_core import runtime
from l01_core.config import Settings
from l02_infra.crypto import hash_api_key
from l05_service import usage_sink
from l05_service.routing import resolve_target
from tests.fake_upstream_server import FakeUpstreamServer

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def wal_root() -> Iterator[Path]:
    path = SCRATCH_ROOT / f"zs2-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_usage_sink() -> Iterator[None]:
    usage_sink.usage_sink.reset()
    runtime.reset_settings()
    yield
    usage_sink.usage_sink.reset()
    runtime.reset_settings()


@pytest.fixture
def test_settings(database_url: str, wal_root: Path) -> Settings:
    """Memory mode + short poll/TTL so zero-SQL behaviour is observable."""
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        usage_pipeline="memory",
        usage_wal_dir=str(wal_root / "usage"),
        usage_sink_interval_ms=60_000,
        catalog_poll_interval_ms=25,
        catalog_ttl_ms=150,
    )


class _StatementCounter:
    """Record SQL statements issued by the app's engine (commits excluded)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.statements: list[str] = []

    def __enter__(self) -> "_StatementCounter":
        event.listen(
            self._engine.sync_engine, "before_cursor_execute", self._before
        )
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(
            self._engine.sync_engine, "before_cursor_execute", self._before
        )

    def _before(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.statements.append(statement)

    @property
    def read_statements(self) -> list[str]:
        return [
            statement
            for statement in self.statements
            if statement.lstrip().upper().startswith("SELECT")
        ]


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(client, headers, url: str, **overrides) -> dict:
    payload = {
        "name": "zs2-channel",
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


async def _key(client, headers, label: str = "zs2") -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def _configure(client, upstream: FakeUpstreamServer, headers) -> dict:
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"], call_model="gpt-test")
    key = await _key(client, headers)
    return {"channel": channel, "model": model, "key": key}


async def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def _signature(target) -> tuple:
    # Same-priority candidates are deliberately shuffled per request, so compare
    # the layers with their members sorted; ordering across layers is preserved.
    layers: dict[tuple[int, ...], list[tuple]] = {}
    for candidate in target.candidates:
        layers.setdefault(candidate.priority_path, []).append(
            (
                candidate.channel.id,
                candidate.model.id,
                candidate.priority,
                candidate.priority_path,
                candidate.member_enabled,
            )
        )
    return (
        target.kind,
        target.max_attempts,
        tuple(
            (path, tuple(sorted(members)))
            for path, members in sorted(layers.items())
        ),
    )


async def test_steady_state_request_uses_zero_read_sql(
    client, app, upstream, monkeypatch
) -> None:
    headers = await _token(client)
    configured = await _configure(client, upstream, headers)
    policy = await client.patch(
        "/api/settings", headers=headers, json={"body_policy": "all"}
    )
    assert policy.status_code == 200, policy.text

    catalog = app.state.catalog
    await catalog.refresh()

    async def _no_version() -> str:
        return catalog.version or ""

    async def _no_flush() -> None:
        return None

    # The version poll itself is allowed to read SQL; this test measures the
    # request path only, so stub the poll's two DB-touching steps.
    monkeypatch.setattr(catalog, "_read_version", _no_version)
    monkeypatch.setattr(catalog, "_flush_key_touches", _no_flush)
    await asyncio.sleep(0.05)

    with _StatementCounter(app.state.db_engine) as counter:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured['key']['key']}"},
            json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
        )

    assert response.status_code == 200, response.text
    assert counter.read_statements == [], counter.read_statements


async def test_management_write_bumps_version_and_poll_applies(
    client, app, upstream
) -> None:
    headers = await _token(client)
    catalog = app.state.catalog
    before = catalog.version

    configured = await _configure(client, upstream, headers)
    slug = configured["channel"]["slug"]

    await _wait_for(
        lambda: catalog.snapshot is not None
        and (slug, "gpt-test") in catalog.snapshot.direct
    )
    assert catalog.version != before

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['key']['key']}"},
        json=_chat_body(f"{slug}/gpt-test"),
    )
    assert response.status_code == 200, response.text


async def test_poll_failure_keeps_serving_last_snapshot(
    client, app, upstream, monkeypatch
) -> None:
    headers = await _token(client)
    configured = await _configure(client, upstream, headers)
    catalog = app.state.catalog
    await catalog.refresh()
    version = catalog.version

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("version poll unavailable")

    monkeypatch.setattr(catalog, "_read_version", _boom)
    monkeypatch.setattr(catalog, "reload", _boom)

    # Wait past the TTL so the poll also attempts (and fails) its fallback.
    await asyncio.sleep(0.3)
    assert catalog.snapshot is not None
    assert catalog.snapshot.version == version

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {configured['key']['key']}"},
        json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
    )
    assert response.status_code == 200, response.text


async def test_key_revoke_visible_within_poll_interval(client, app, upstream) -> None:
    headers = await _token(client)
    configured = await _configure(client, upstream, headers)
    catalog = app.state.catalog
    await catalog.refresh()

    key = configured["key"]
    key_hash = hash_api_key(key["key"])
    assert key_hash in catalog.snapshot.api_keys

    first = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
    )
    assert first.status_code == 200, first.text

    started = time.monotonic()
    deleted = await client.delete(f"/api/keys/{key['id']}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    await _wait_for(lambda: key_hash not in catalog.snapshot.api_keys)
    assert time.monotonic() - started < 60

    revoked = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
    )
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "INVALID_API_KEY"


async def test_cached_routing_matches_database_routing(client, app, upstream) -> None:
    headers = await _token(client)
    first = await _channel(client, headers, upstream.base_url, name="zs2-a")
    second = await _channel(client, headers, upstream.base_url, name="zs2-b")
    await _model(client, headers, first["id"], call_model="shared")
    await _model(client, headers, second["id"], call_model="shared")
    await app.state.catalog.refresh()

    async with app.state.session_factory() as session:
        db_direct = await resolve_target(session, f"{first['slug']}/shared")
        db_group = await resolve_target(session, "shared")
        session.info["catalog"] = app.state.catalog
        cached_direct = await resolve_target(session, f"{first['slug']}/shared")
        cached_group = await resolve_target(session, "shared")

    assert _signature(cached_direct) == _signature(db_direct)
    assert _signature(cached_group) == _signature(db_group)
    assert cached_group.kind == "combo"
    assert cached_group.origin == "auto"
    assert len(cached_group.candidates) == 2
