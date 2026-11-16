from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from l01_core.config import Settings
from l02_infra.crypto import encrypt_secret
from l02_infra.db import init_db
from l07_bootstrap.app_factory import create_app

DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-dbs"

# The shape shipped before timeout_s/priority/ttfa_ms/ttlt_ms/source existed.
_LEGACY_CHANNELS = """
CREATE TABLE channels (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    connection_url TEXT NOT NULL,
    api_key_enc TEXT NOT NULL,
    protocol VARCHAR(32) NOT NULL,
    auth_style VARCHAR(32) NOT NULL,
    protocol_source VARCHAR(16) NOT NULL,
    rewrite_rules_json TEXT NOT NULL,
    enabled BOOLEAN NOT NULL,
    note TEXT NOT NULL,
    connection_status VARCHAR(16) NOT NULL,
    connection_latency_ms INTEGER,
    connection_message TEXT NOT NULL,
    connection_tested_at BIGINT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1
)
"""

_LEGACY_REQUEST_LOGS = """
CREATE TABLE request_logs (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL UNIQUE,
    upstream_request_id VARCHAR(128),
    user_id VARCHAR(40),
    api_key_id VARCHAR(40),
    api_key_label VARCHAR(64),
    requested_model VARCHAR(128),
    resolved_combo_id VARCHAR(40),
    final_channel_model_id VARCHAR(40),
    channel_name VARCHAR(128),
    upstream_model VARCHAR(128),
    response_model VARCHAR(128),
    is_stream BOOLEAN,
    http_status INTEGER,
    error_code VARCHAR(64),
    error_message TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cached_tokens INTEGER,
    cache_ratio FLOAT,
    cost_usd FLOAT,
    ttft_ms INTEGER,
    total_ms INTEGER,
    throughput_tps FLOAT,
    usage_estimated BOOLEAN,
    attempts_json TEXT,
    rewrite_applied BOOLEAN,
    rewrite_skipped BOOLEAN,
    rewrite_diff_json TEXT,
    response_headers_json TEXT,
    params_json TEXT,
    created_at BIGINT NOT NULL
)
"""

_INSERT_LEGACY_CHANNEL = """
INSERT INTO channels (
    id, slug, name, connection_url, api_key_enc, protocol, auth_style,
    protocol_source, rewrite_rules_json, enabled, note, connection_status,
    connection_message, created_at, updated_at, priority
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@pytest.fixture
def legacy_db() -> Iterator[Path]:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    path = DB_DIR / f"legacy-{uuid4().hex}.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_LEGACY_CHANNELS + ";\n" + _LEGACY_REQUEST_LOGS + ";\n")
        connection.execute(
            _INSERT_LEGACY_CHANNEL,
            (
                "legacy-channel-id-0001",
                "legacy-channel",
                "历史渠道",
                "https://legacy.example.com/v1",
                encrypt_secret("sk-legacy-0001"),
                "openai_chat",
                "bearer",
                "manual",
                "{}",
                1,
                "",
                "unknown",
                "",
                1,
                1,
                1,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


async def test_legacy_schema_is_migrated_and_channels_stay_writable(legacy_db: Path) -> None:
    """An old database must boot, read its channels and accept a new channel."""
    url = f"sqlite+aiosqlite:///{legacy_db.as_posix()}"
    settings = Settings(
        database_url=url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
    )
    app = create_app(settings=settings, database_url=url)

    async with app.router.lifespan_context(app):
        # Running the bootstrap twice proves the migration is idempotent.
        await init_db(app.state.db_engine)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            login = await client.post(
                "/api/auth/login", json={"username": "admin", "password": "admin"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            listed = await client.get("/api/channels", headers=headers)
            assert listed.status_code == 200, listed.text
            assert [item["slug"] for item in listed.json()["items"]] == ["legacy-channel"]
            assert listed.json()["items"][0]["api_key_count"] == 1

            created = await client.post(
                "/api/channels",
                headers=headers,
                json={
                    "name": "迁移后新建",
                    "connection_url": "https://example.com/v1",
                    "api_key": "sk-new-0002",
                    "protocol": "openai_chat",
                },
            )
            assert created.status_code == 201, created.text

    connection = sqlite3.connect(legacy_db)
    try:
        channel_columns = {row[1] for row in connection.execute("PRAGMA table_info(channels)")}
        log_columns = {row[1] for row in connection.execute("PRAGMA table_info(request_logs)")}
        legacy_timeout = connection.execute(
            "SELECT timeout_s FROM channels WHERE slug = 'legacy-channel'"
        ).fetchone()[0]
        new_timeout = connection.execute(
            "SELECT timeout_s FROM channels WHERE name = '迁移后新建'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert {"priority", "timeout_s"} <= channel_columns
    assert {"source", "ttfa_ms", "ttlt_ms"} <= log_columns
    assert legacy_timeout == 60
    assert new_timeout == 60
