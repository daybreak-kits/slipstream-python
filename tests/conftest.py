from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from l01_core.config import Settings  # noqa: E402
from l07_bootstrap.app_factory import create_app  # noqa: E402

_HASH_CACHE: dict[str, str] = {}


@pytest.fixture(scope="session", autouse=True)
def _share_password_hashes() -> Iterator[None]:
    """Reuse PBKDF2 digests for repeated seed passwords.

    Seeding a fresh admin costs one PBKDF2-HMAC-SHA256 hash (240k iterations).
    Every test starts from an empty database and seeds the same one or two
    passwords, so caching the real digest per password removes that per-test
    cost. Hashing is not weakened: the cached value is still produced by the
    production function, and verification still checks the stored digest.
    """
    if os.environ.get("PYTEST_NO_FAST_FIXTURES"):
        # A/B escape hatch: run with the pre-2026-11-14 fixture behaviour so the
        # speedup of the two optimisations can be measured in one time window.
        yield
        return

    from l02_infra.crypto import hash_password as real_hash
    from l05_service import auth as auth_module

    def cached_hash(password: str) -> str:
        digest = _HASH_CACHE.get(password)
        if digest is None:
            digest = real_hash(password)
            _HASH_CACHE[password] = digest
        return digest

    auth_module.hash_password = cached_hash
    try:
        yield
    finally:
        auth_module.hash_password = real_hash


@pytest.fixture(autouse=True)
def _offline_token_estimate(monkeypatch) -> Iterator[None]:
    """Force the usage-estimate fallback to the script heuristic in tests.

    Loading the real tiktoken vocabulary would download ~MBs on the first
    estimation (about 7s, network), making the suite non-hermetic; the
    tokenizer path itself is unit-tested with a stub in test_billing_estimate.py.
    """
    from l05_service import billing as billing_module

    monkeypatch.setattr(billing_module, "_get_tiktoken_encoding", lambda: None)
    yield


@pytest.fixture(autouse=True)
def _reset_upstream_timeouts_cache() -> Iterator[None]:
    """The module-level timeouts cache must not leak across fresh-DB tests."""
    from l05_service import route_settings as route_settings_module
    from l05_service import route_stats as route_stats_module
    from l05_service import upstream_settings as upstream_settings_module

    upstream_settings_module._cache_invalidate()
    route_settings_module._cache_invalidate()
    route_stats_module.reset_caches()
    yield
    upstream_settings_module._cache_invalidate()
    route_settings_module._cache_invalidate()
    route_stats_module.reset_caches()


@pytest.fixture
def database_url() -> Iterator[str]:
    """Each test gets its own throwaway SQLite database.

    The database file is placed in an already existing directory inside the
    repository instead of a freshly created temporary directory: some sandboxes
    refuse to open SQLite files inside directories created during the run.
    """
    root = Path(os.environ.get("PYTEST_DB_DIR") or Path(__file__).resolve().parents[1] / ".pytest-dbs")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"test-{uuid.uuid4().hex}.db"
    try:
        yield f"sqlite+aiosqlite:///{path.as_posix()}"
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        # The daily models.dev pull is a network action; tests must never make it.
        models_dev_enabled=False,
    )


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    return create_app(settings=test_settings, database_url=test_settings.database_url)


@pytest_asyncio.fixture
async def client(app: FastAPI):
    """ASGI client whose lifespan has run, so tables exist."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield test_client


def _fake_upstream() -> FastAPI:
    """Minimal OpenAI-compatible upstream used by later gateway tickets."""
    upstream = FastAPI(title="fake-upstream")

    @upstream.get("/v1/models")
    async def list_models() -> JSONResponse:
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": "fake-model", "object": "model", "owned_by": "test"}],
            }
        )

    @upstream.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        payload = await request.json()
        if payload.get("stream"):
            async def events():
                yield b'data: {"id":"chatcmpl-fake","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hel"}}]}\n\n'
                yield b'data: {"id":"chatcmpl-fake","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"lo"}}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n'
                yield b"data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")
        return JSONResponse(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": payload.get("model") or "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        )

    return upstream


@pytest.fixture
def fake_upstream() -> FastAPI:
    return _fake_upstream()


@pytest_asyncio.fixture
async def fake_upstream_client(fake_upstream: FastAPI):
    transport = httpx.ASGITransport(app=fake_upstream)
    async with httpx.AsyncClient(transport=transport, base_url="http://upstream.test") as upstream_client:
        yield upstream_client
