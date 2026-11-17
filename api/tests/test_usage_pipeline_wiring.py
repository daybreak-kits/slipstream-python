"""ZS-3: the single USAGE_PIPELINE switch selects the wiring.

classic (default) keeps the exact pre-catalog request path: per-request SQL
reads and one INSERT for the request log. memory attaches the in-process
catalog snapshot and hands the request log to the usage sink, so a steady
state request runs no statement at all. These tests pin both semantics plus
the vocabulary the operator may write in the environment.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Self

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event

from l01_core import runtime
from l01_core.config import Settings
from l05_service import route_settings, route_stats, usage_sink
from l07_bootstrap.app_factory import create_app
from tests.fake_upstream_server import FakeUpstreamServer

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"


def _base_settings(database_url: str, **overrides: Any) -> Settings:
    values = dict(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        catalog_poll_interval_ms=25,
        catalog_ttl_ms=150,
    )
    values.update(overrides)
    return Settings(**values)


class _StatementCounter:
    """Record the statements the engine runs inside the block."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.statements: list[str] = []

    def _on_execute(self, *args: Any) -> None:
        statement = args[2] if len(args) > 2 else ""
        self.statements.append(" ".join(str(statement).split()))

    def __enter__(self) -> Self:
        event.listen(self._engine.sync_engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info: object) -> bool:
        event.remove(self._engine.sync_engine, "before_cursor_execute", self._on_execute)
        return False


@pytest.fixture
def wal_root() -> Iterator[Path]:
    path = SCRATCH_ROOT / f"zs3-{uuid.uuid4().hex}"
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
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    """Classic mode: no usage_pipeline override, the switch default."""
    return _base_settings(database_url)


@pytest_asyncio.fixture
async def memory_app(database_url: str, wal_root: Path) -> AsyncIterator[FastAPI]:
    settings = _base_settings(
        database_url,
        usage_pipeline="memory",
        usage_wal_dir=str(wal_root / "usage"),
        # The background runner must not race the assertions below.
        usage_sink_interval_ms=60_000,
    )
    app = create_app(settings=settings, database_url=database_url)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def memory_client(memory_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=memory_app), base_url="http://testserver"
    ) as test_client:
        yield test_client


def test_usage_pipeline_vocabulary_and_classic_default() -> None:
    url = "sqlite+aiosqlite:///:memory:"
    # _env_file=None: this case asserts the *code* default. Without it a local
    # api/.env carrying USAGE_PIPELINE=memory overrides that default, and the
    # failure would say nothing about the code.
    def settings(**kw):
        return Settings(_env_file=None, database_url=url, **kw)

    assert settings().usage_pipeline == "classic"
    assert settings().zero_sql_pipeline is False
    for value in ("off", "classic", "", "no", "false", "0"):
        assert settings(usage_pipeline=value).zero_sql_pipeline is False, value
    for value in ("memory", "on", "yes", "true", "1"):
        assert settings(usage_pipeline=value).zero_sql_pipeline is True, value
    with pytest.raises(ValueError):
        settings(usage_pipeline="bogus").zero_sql_pipeline


async def _token(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(client: httpx.AsyncClient, headers: dict, url: str) -> dict:
    response = await client.post("/api/channels", headers=headers, json={
        "name": "zs3-channel",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client: httpx.AsyncClient, headers: dict, channel_id: str) -> dict:
    response = await client.post(
        f"/api/channels/{channel_id}/models",
        headers=headers,
        json={"upstream_model": "fake-model", "call_model": "gpt-test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client: httpx.AsyncClient, headers: dict) -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": "zs3"})
    assert response.status_code == 201, response.text
    return response.json()


async def _configure(client: httpx.AsyncClient, upstream: FakeUpstreamServer, headers: dict) -> dict:
    channel = await _channel(client, headers, upstream.base_url)
    model = await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    return {"channel": channel, "model": model, "key": key}


def _chat_body(model: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


async def test_classic_mode_keeps_the_sql_request_path(client, app, upstream) -> None:
    headers = await _token(client)
    configured = await _configure(client, upstream, headers)
    assert app.state.settings.zero_sql_pipeline is False
    assert usage_sink.usage_sink.enabled() is False

    with _StatementCounter(app.state.db_engine) as counter:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured['key']['key']}"},
            json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
        )

    assert response.status_code == 200, response.text
    assert counter.statements, "classic mode must serve reads and writes through SQL"
    assert usage_sink.usage_sink.stats()["pending"] == 0


async def test_memory_mode_request_runs_zero_sql(
    memory_app, memory_client, upstream, monkeypatch
) -> None:
    client = memory_client
    headers = await _token(client)
    configured = await _configure(client, upstream, headers)
    catalog = memory_app.state.catalog
    await catalog.refresh()

    async def _no_version() -> str:
        return catalog.version or ""

    async def _no_flush() -> None:
        return None

    # The version poll itself is background SQL; only the request path is measured.
    monkeypatch.setattr(catalog, "_read_version", _no_version)
    monkeypatch.setattr(catalog, "_flush_key_touches", _no_flush)
    await asyncio.sleep(0.05)

    with _StatementCounter(memory_app.state.db_engine) as counter:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured['key']['key']}"},
            json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
        )

    assert response.status_code == 200, response.text
    assert counter.statements == [], counter.statements
    stats = usage_sink.usage_sink.stats()
    assert stats["pending"] == 1
    assert stats["written"] == 0


def _expire_policy_caches(window: int) -> dict[str, Any]:
    """把两份路由缓存的时间戳回拨到 TTL 之外，制造「过期但有值」。"""
    assert route_stats._settings_cache[0] is not None
    route_stats._settings_cache[1] = time.monotonic() - route_settings.CACHE_TTL_S - 1.0
    entry = route_stats._stats_cache.get(window)
    assert entry is not None, "lifespan 启动应已预热统计缓存"
    stale_value, _stale_ts = entry
    route_stats._stats_cache[window] = (
        stale_value,
        time.monotonic() - route_stats.STATS_CACHE_TTL_S - 1.0,
    )
    return stale_value


async def test_memory_mode_stale_policy_keeps_request_sql_free(
    memory_app, memory_client, upstream, monkeypatch
) -> None:
    """TTL 过期瞬间，请求路径回读陈旧值并把聚合刷新挪去后台，仍零 SQL。"""
    client = memory_client
    headers = await _token(client)
    configured = await _configure(client, upstream, headers)
    catalog = memory_app.state.catalog
    await catalog.refresh()

    async def _no_version() -> str:
        return catalog.version or ""

    async def _no_flush() -> None:
        return None

    monkeypatch.setattr(catalog, "_read_version", _no_version)
    monkeypatch.setattr(catalog, "_flush_key_touches", _no_flush)
    await asyncio.sleep(0.05)

    # 后台刷新改成只记录调度的桩：本测试只钉「请求零 SQL + 调度发生」。
    scheduled: list[int] = []

    async def _fake_refresh(factory, window_minutes: int) -> None:
        scheduled.append(window_minutes)

    monkeypatch.setattr(route_stats, "_refresh_route_caches", _fake_refresh)
    stale_value = _expire_policy_caches(30)

    with _StatementCounter(memory_app.state.db_engine) as counter:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured['key']['key']}"},
            json=_chat_body(f"{configured['channel']['slug']}/gpt-test"),
        )

    assert response.status_code == 200, response.text
    assert counter.statements == [], counter.statements
    assert scheduled == [30], scheduled
    # 陈旧值仍在服务（时间戳未被桩改写，值就是过期前那份）。
    assert route_stats._stats_cache[30][0] is stale_value


async def test_memory_mode_policy_refresh_is_single_flight(memory_app, monkeypatch) -> None:
    """过期后第一个发现者创建刷新任务；在飞期间其余请求不重复调度。"""
    catalog = memory_app.state.catalog
    stale_value = _expire_policy_caches(30)

    gate = asyncio.Event()
    load_calls: list[int] = []

    async def _gated_load_stats(session, window_minutes: int):
        load_calls.append(window_minutes)
        await gate.wait()
        return stale_value

    monkeypatch.setattr(route_stats, "_load_stats", _gated_load_stats)

    factory = memory_app.state.session_factory
    async with factory() as session:
        session.info["catalog"] = catalog
        await route_stats.resolve_policy(session)
        # 等刷新任务真的启动并卡在闸门上。
        for _ in range(200):
            if load_calls:
                break
            await asyncio.sleep(0.005)
        assert load_calls == [30], load_calls

        await route_stats.resolve_policy(session)
        assert load_calls == [30], load_calls  # 第二个发现者没有再发起加载
        inflight = [t for t in route_stats._refresh_tasks.values() if not t.done()]
        assert len(inflight) == 1, route_stats._refresh_tasks

        gate.set()
        task = route_stats._refresh_tasks.get(30)
        assert task is not None
        await asyncio.wait_for(task, timeout=5.0)
        # 刷新完成：统计缓存时间戳被更新（拿到的是闸门放的旧值本身）。
        assert route_stats._stats_cache[30][0] is stale_value
        assert time.monotonic() - route_stats._stats_cache[30][1] < 1.0


async def test_memory_mode_refresh_failure_keeps_stale_value(memory_app, monkeypatch) -> None:
    """刷新失败静默保留陈旧值；下一次请求发现仍过期会再调度一轮。"""
    catalog = memory_app.state.catalog
    stale_value = _expire_policy_caches(30)
    stale_ts = route_stats._stats_cache[30][1]

    async def _boom(session, window_minutes: int):
        raise RuntimeError("db down")

    monkeypatch.setattr(route_stats, "_load_stats", _boom)

    factory = memory_app.state.session_factory
    async with factory() as session:
        session.info["catalog"] = catalog
        policy = await route_stats.resolve_policy(session)
        assert policy.stats is stale_value
    task = route_stats._refresh_tasks.get(30)
    assert task is not None
    await asyncio.wait_for(task, timeout=5.0)  # 异常已被 worker 吞掉，不会外抛
    assert route_stats._stats_cache[30][0] is stale_value
    assert route_stats._stats_cache[30][1] == stale_ts  # 失败不改时间戳 → 仍视为过期

    async with factory() as session:
        session.info["catalog"] = catalog
        await route_stats.resolve_policy(session)
    task2 = route_stats._refresh_tasks.get(30)
    assert task2 is not None and task2 is not task and not task2.done()
    task2.cancel()
    try:
        await task2
    except asyncio.CancelledError:
        pass