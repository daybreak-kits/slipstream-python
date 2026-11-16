from __future__ import annotations

import json

from l01_core.config import get_settings
from l04_repo import orm
from l05_service.app_settings import BODY_LOG_SETTING_KEY, LOG_METADATA_RETENTION_DAYS
from l05_service.upstream_settings import UPSTREAM_TIMEOUT_SETTING_KEY


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_settings_defaults_to_off_with_forced_redaction(client):
    headers = await _token(client)

    response = await client.get("/api/settings", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["body_policy"] == "off"
    assert payload["body_truncate_bytes"] == 16 * 1024
    assert payload["body_retention_days"] == 7
    assert payload["summary_max_bytes"] == 64 * 1024
    assert payload["log_retention_days"] == LOG_METADATA_RETENTION_DAYS == 30
    assert payload["redaction_forced"] is True
    assert payload["updated_at"] is None
    assert payload["updated_by"] == ""


async def test_settings_patch_persists_policy_and_records_editor(client, app):
    headers = await _token(client)

    updated = await client.patch(
        "/api/settings", headers=headers, json={"body_policy": "errors"}
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["body_policy"] == "errors"
    assert payload["updated_by"] == "admin"
    assert isinstance(payload["updated_at"], int)
    assert payload["updated_at"] > 0

    reread = await client.get("/api/settings", headers=headers)
    assert reread.json() == payload

    async with app.state.session_factory() as session:
        row = await session.get(orm.AppSettingORM, BODY_LOG_SETTING_KEY)
        assert row is not None
        assert row.updated_by == "admin"
        assert json.loads(row.value_json)["mode"] == "errors"


async def test_settings_patch_validates_policy_and_numeric_limits(client):
    headers = await _token(client)

    invalid_mode = await client.patch(
        "/api/settings", headers=headers, json={"body_policy": "everything"}
    )
    assert invalid_mode.status_code == 422

    too_small = await client.patch(
        "/api/settings", headers=headers, json={"body_truncate_bytes": 10}
    )
    assert too_small.status_code == 422

    too_long = await client.patch(
        "/api/settings", headers=headers, json={"body_retention_days": 365}
    )
    assert too_long.status_code == 422


async def test_gateway_address_defaults_empty_and_persists_normalized(client, app):
    headers = await _token(client)

    initial = await client.get("/api/settings", headers=headers)
    assert initial.json()["gateway_base_url"] == ""
    assert initial.json()["gateway_updated_at"] is None

    updated = await client.patch(
        "/api/settings",
        headers=headers,
        json={"gateway_base_url": "https://llm.example.com:8443/"},
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["gateway_base_url"] == "https://llm.example.com:8443"
    assert payload["gateway_updated_by"] == "admin"
    assert isinstance(payload["gateway_updated_at"], int)

    # Editing the gateway address must not reset the body logging policy.
    await client.patch("/api/settings", headers=headers, json={"body_policy": "errors"})
    reread = await client.get("/api/settings", headers=headers)
    assert reread.json()["gateway_base_url"] == "https://llm.example.com:8443"
    assert reread.json()["body_policy"] == "errors"

    async with app.state.session_factory() as session:
        row = await session.get(orm.AppSettingORM, "gateway")
        assert row is not None
        assert json.loads(row.value_json) == {"base_url": "https://llm.example.com:8443"}


async def test_gateway_address_rejects_unsafe_values_and_allows_clearing(client):
    headers = await _token(client)

    for value in (
        "llm.example.com",
        "ftp://llm.example.com",
        "https://user:pass@llm.example.com",
        "https://llm.example.com?debug=1",
        "https://llm.example.com#frag",
    ):
        response = await client.patch(
            "/api/settings", headers=headers, json={"gateway_base_url": value}
        )
        assert response.status_code == 400, (value, response.text)

    await client.patch(
        "/api/settings", headers=headers, json={"gateway_base_url": "https://llm.example.com"}
    )
    cleared = await client.patch(
        "/api/settings", headers=headers, json={"gateway_base_url": ""}
    )
    assert cleared.status_code == 200
    assert cleared.json()["gateway_base_url"] == ""


async def test_settings_require_admin_session(client):
    assert (await client.get("/api/settings")).status_code == 401
    assert (
        await client.patch("/api/settings", json={"body_policy": "all"})
    ).status_code == 401


async def test_upstream_timeouts_default_to_env_and_patch_persists(client, app):
    headers = await _token(client)

    initial = await client.get("/api/settings", headers=headers)
    payload = initial.json()
    settings = get_settings()
    assert payload["upstream_connect_timeout_s"] == float(settings.upstream_connect_timeout_s)
    assert payload["upstream_first_byte_timeout_s"] == float(settings.upstream_first_byte_timeout_s)
    assert payload["upstream_read_timeout_s"] == float(settings.upstream_read_timeout_s)
    assert payload["upstream_timeouts_is_default"] is True
    assert payload["upstream_timeouts_updated_at"] is None

    updated = await client.patch(
        "/api/settings",
        headers=headers,
        json={"upstream_connect_timeout_s": 5, "upstream_read_timeout_s": 300},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["upstream_connect_timeout_s"] == 5.0
    assert body["upstream_first_byte_timeout_s"] == float(settings.upstream_first_byte_timeout_s)
    assert body["upstream_read_timeout_s"] == 300.0
    assert body["upstream_timeouts_is_default"] is False
    assert body["upstream_timeouts_updated_by"] == "admin"

    reread = await client.get("/api/settings", headers=headers)
    assert reread.json() == body

    async with app.state.session_factory() as session:
        row = await session.get(orm.AppSettingORM, UPSTREAM_TIMEOUT_SETTING_KEY)
        assert row is not None
        stored = json.loads(row.value_json)
        assert stored["connect_timeout_s"] == 5.0
        assert stored["read_timeout_s"] == 300.0


async def test_upstream_timeouts_reject_invalid_values(client):
    headers = await _token(client)

    for bad in (
        {"upstream_connect_timeout_s": 0},
        {"upstream_connect_timeout_s": -1},
        {"upstream_first_byte_timeout_s": 86401},
        {"upstream_read_timeout_s": "abc"},
    ):
        response = await client.patch("/api/settings", headers=headers, json=bad)
        assert response.status_code == 422, bad


async def test_upstream_timeouts_reset_restores_env_defaults(client, app):
    headers = await _token(client)

    await client.patch(
        "/api/settings", headers=headers, json={"upstream_read_timeout_s": 300}
    )
    reread = await client.get("/api/settings", headers=headers)
    assert reread.json()["upstream_timeouts_is_default"] is False

    reset = await client.patch(
        "/api/settings", headers=headers, json={"upstream_timeouts_reset": True}
    )
    assert reset.status_code == 200, reset.text
    body = reset.json()
    settings = get_settings()
    assert body["upstream_read_timeout_s"] == float(settings.upstream_read_timeout_s)
    assert body["upstream_timeouts_is_default"] is True
    assert body["upstream_timeouts_updated_at"] is None

    async with app.state.session_factory() as session:
        row = await session.get(orm.AppSettingORM, UPSTREAM_TIMEOUT_SETTING_KEY)
        assert row is None


async def test_upstream_timeouts_cache_expiry_rereads_db(client, app, monkeypatch):
    """缓存按 TTL 过期后重读 DB；请求路径靠启动预热与保存即刷保持零 SQL。"""
    from l05_service import upstream_settings as us

    class _FakeTime:
        def __init__(self) -> None:
            self.now = 1000.0

        def monotonic(self) -> float:
            return self.now

    fake = _FakeTime()
    monkeypatch.setattr(us, "time", fake)
    headers = await _token(client)

    await client.patch(
        "/api/settings", headers=headers, json={"upstream_connect_timeout_s": 5}
    )

    async with app.state.session_factory() as session:
        current = await us.get_upstream_timeouts(session)
        assert current.connect_timeout_s == 5.0

    # 直接改写 DB 行（绕开 save 的缓存写入），模拟另一个进程的修改。
    async with app.state.session_factory() as session:
        row = await session.get(orm.AppSettingORM, UPSTREAM_TIMEOUT_SETTING_KEY)
        payload = json.loads(row.value_json)
        payload["connect_timeout_s"] = 7.0
        row.value_json = json.dumps(payload)
        await session.commit()

    async with app.state.session_factory() as session:
        still_cached = await us.get_upstream_timeouts(session)
        assert still_cached.connect_timeout_s == 5.0  # TTL 内仍是旧缓存值

    fake.now += us.UPSTREAM_TIMEOUTS_CACHE_TTL_S + 1.0
    async with app.state.session_factory() as session:
        refreshed = await us.get_upstream_timeouts(session)
        assert refreshed.connect_timeout_s == 7.0  # TTL 过期后重读 DB
