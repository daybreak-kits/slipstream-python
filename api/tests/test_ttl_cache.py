from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import event

from l01_core.config import Settings
from l02_infra.ttl_cache import TTLCache
from l05_service import catalog


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_cache_records_hits_misses_expiries_and_hit_rate() -> None:
    clock = _Clock()
    cache: TTLCache[str, int] = TTLCache(maxsize=8, ttl_ms=100, clock=clock)

    assert cache.get("missing") is None
    cache.set("a", 1)
    assert cache.get("a") == 1

    stats = cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.sets == 1
    assert stats.hit_rate == pytest.approx(0.5)
    assert stats.size == 1

    clock.advance(0.101)
    assert cache.get("a") is None
    stats = cache.stats()
    assert stats.expirations == 1
    assert stats.misses == 2
    assert stats.size == 0


def test_cache_evicts_least_recently_used_entry() -> None:
    cache: TTLCache[str, int] = TTLCache(maxsize=2, ttl_ms=10_000)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)

    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.stats().evictions == 1


def test_cache_supports_per_entry_ttl_and_none_values() -> None:
    clock = _Clock()
    cache: TTLCache[str, Any] = TTLCache(maxsize=8, ttl_ms=1_000, clock=clock)
    cache.set("short", 1, ttl_ms=50)
    cache.set("none", None)
    cache.set("long", 2)

    assert cache.get("none") is None
    assert cache.contains("none") is True
    clock.advance(0.06)
    assert cache.get("short") is None
    assert cache.get("long") == 2


def test_cache_delete_and_clear() -> None:
    cache: TTLCache[str, int] = TTLCache(maxsize=4, ttl_ms=1_000)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.delete("a") is True
    assert cache.delete("a") is False
    cache.clear()
    assert len(cache) == 0
    assert cache.stats().size == 0


async def test_get_or_set_is_single_flight() -> None:
    cache: TTLCache[str, int] = TTLCache(maxsize=8, ttl_ms=1_000)
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return 42

    results = await asyncio.gather(
        *[cache.get_or_set("key", factory) for _ in range(6)]
    )
    assert results == [42] * 6
    assert calls == 1
    assert cache.stats().hits >= 1


def test_cache_is_thread_safe() -> None:
    cache: TTLCache[int, int] = TTLCache(maxsize=64, ttl_ms=10_000)

    def worker(offset: int) -> None:
        for index in range(200):
            key = (offset * 200 + index) % 128
            cache.get(key)
            cache.set(key, key)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    assert len(cache) <= 64
    assert cache.stats().hits > 0
    assert cache.stats().misses > 0


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        catalog_directory_ttl_ms=60,
    )


class _StatementCounter:
    def __init__(self, app: Any) -> None:
        self._app = app
        self.statements: list[str] = []

    def __enter__(self) -> "_StatementCounter":
        event.listen(
            self._app.state.db_engine.sync_engine,
            "before_cursor_execute",
            self._before,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        event.remove(
            self._app.state.db_engine.sync_engine,
            "before_cursor_execute",
            self._before,
        )

    def _before(self, conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        self.statements.append(statement)

    def reset(self) -> None:
        self.statements.clear()

    @property
    def selects(self) -> int:
        return sum(
            1 for statement in self.statements if statement.lstrip().upper().startswith("SELECT")
        )


async def _token(client) -> dict[str, str]:  # noqa: ANN001
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_models_directory_cache_reuses_entries_within_ttl(client, app) -> None:  # noqa: ANN001
    headers = await _token(client)
    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "cache-directory",
            "connection_url": "http://upstream.invalid",
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert channel.status_code == 201, channel.text
    channel = channel.json()
    model = await client.post(
        f"/api/channels/{channel['id']}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "gpt-cached"},
    )
    assert model.status_code == 201, model.text
    catalog.reset_model_directory_cache()

    with _StatementCounter(app) as counter:
        async with app.state.session_factory() as session:
            first = await catalog.list_model_entries(session)
        first_selects = counter.selects

        counter.reset()
        async with app.state.session_factory() as session:
            second = await catalog.list_model_entries(session)
        second_selects = counter.selects

        await asyncio.sleep(0.08)
        counter.reset()
        async with app.state.session_factory() as session:
            third = await catalog.list_model_entries(session)
        third_selects = counter.selects

    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert [item["id"] for item in first] == [item["id"] for item in third]
    assert first_selects > 0
    assert second_selects == 0
    assert third_selects > 0
    stats = catalog.model_directory_cache_stats()
    assert stats["hits"] >= 1
    assert stats["misses"] >= 1


async def test_probe_all_cache_hit_marker_and_force_bypass(monkeypatch) -> None:  # noqa: ANN001
    from l05_service import probe, probe_cache

    calls = 0
    result = {
        "channel_id": "chan-1",
        "mode": "deep",
        "total": 0,
        "skipped": 0,
        "ok": 0,
        "timeout": 0,
        "error": 0,
        "unknown": 0,
        "items": [],
        "protocol": "openai_chat",
        "auth_style": "bearer",
        "protocol_source": "explicit",
    }

    async def fake_probe_all(session, channel, mode="deep", force=False):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return dict(result, channel_id=channel.id, mode=mode)

    class _Channel:
        id = "chan-1"

    monkeypatch.setattr(probe, "probe_all_models", fake_probe_all)
    probe_cache.reset_probe_cache()

    first = await probe_cache.probe_all_models_cached(None, _Channel(), "deep")
    second = await probe_cache.probe_all_models_cached(None, _Channel(), "deep")
    assert calls == 1
    assert first["cached"] is False
    assert second["cached"] is True

    forced = await probe_cache.probe_all_models_cached(
        None, _Channel(), "deep", force=True
    )
    assert calls == 2
    assert forced["cached"] is False
    stats = probe_cache.probe_cache_stats()
    assert stats["hits"] >= 1
    assert stats["misses"] >= 1


async def test_probe_all_route_marks_cache_hits(client, monkeypatch) -> None:  # noqa: ANN001
    from l05_service import probe, probe_cache

    calls = 0

    async def fake_probe_all(session, channel, mode="deep", force=False):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return {
            "channel_id": channel.id,
            "mode": mode,
            "total": 0,
            "skipped": 0,
            "ok": 0,
            "timeout": 0,
            "error": 0,
            "unknown": 0,
            "items": [],
            "protocol": channel.protocol,
            "auth_style": channel.auth_style,
            "protocol_source": channel.protocol_source,
        }

    headers = await _token(client)
    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "probe-cache",
            "connection_url": "http://upstream.invalid",
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert channel.status_code == 201, channel.text
    channel = channel.json()

    monkeypatch.setattr(probe, "probe_all_models", fake_probe_all)
    probe_cache.reset_probe_cache()

    first = await client.post(
        f"/api/channels/{channel['id']}/probe-all",
        headers=headers,
        json={"mode": "deep"},
    )
    second = await client.post(
        f"/api/channels/{channel['id']}/probe-all",
        headers=headers,
        json={"mode": "deep"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert calls == 1
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
