from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import update

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


async def test_create_channel_with_multiple_keys_and_masked_listing(client, upstream):
    headers = await _token(client)

    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "多密钥渠道",
            "connection_url": upstream.base_url,
            "api_keys": [
                {"label": "主密钥", "api_key": "sk-primary-1111"},
                {"label": "备用", "api_key": "sk-backup-2222"},
            ],
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["api_key_count"] == 2
    assert body["api_key_set"] is True
    assert len(body["api_keys"]) == 2
    assert set(body["api_key_masks"]) == {"••••••1111", "••••••2222"}
    assert "sk-primary-1111" not in created.text
    assert "sk-backup-2222" not in created.text

    listed = await client.get("/api/channels", headers=headers)
    item = listed.json()["items"][0]
    assert item["api_key_count"] == 2
    assert set(item["api_key_masks"]) == {"••••••1111", "••••••2222"}


async def test_channel_key_endpoints_add_list_and_delete(client, upstream):
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "密钥管理",
            "connection_url": upstream.base_url,
            "api_key": "sk-first-0001",
        },
    )
    channel_id = created.json()["id"]

    added = await client.post(
        f"/api/channels/{channel_id}/keys",
        headers=headers,
        json={"label": "第二把", "api_key": "sk-second-0002"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["masked"] == "••••••0002"

    duplicate = await client.post(
        f"/api/channels/{channel_id}/keys",
        headers=headers,
        json={"label": "重复", "api_key": "sk-second-0002"},
    )
    assert duplicate.status_code == 409

    listed = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert [row["label"] for row in listed.json()["items"]] == ["默认密钥", "第二把"]

    deleted = await client.delete(
        f"/api/channels/{channel_id}/keys/{added.json()['id']}", headers=headers
    )
    assert deleted.status_code == 204
    remaining = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert remaining.json()["total"] == 1


async def test_deleting_last_key_keeps_channel_row(client, upstream):
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "清空密钥",
            "connection_url": upstream.base_url,
            "api_key": "sk-only-9999",
        },
    )
    channel_id = created.json()["id"]
    key_id = created.json()["api_keys"][0]["id"]

    deleted = await client.delete(
        f"/api/channels/{channel_id}/keys/{key_id}", headers=headers
    )
    assert deleted.status_code == 204

    channel = await client.get(f"/api/channels/{channel_id}", headers=headers)
    assert channel.status_code == 200
    assert channel.json()["api_key_count"] == 0
    assert channel.json()["api_key_set"] is False


async def test_draft_connection_test_does_not_persist_channel(client, upstream):
    headers = await _token(client)

    response = await client.post(
        "/api/channels/test-connection",
        headers=headers,
        json={
            "connection_url": upstream.base_url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    listed = await client.get("/api/channels", headers=headers)
    assert listed.json()["total"] == 0
    assert upstream.find("GET", "/models")


async def test_batch_duplicate_keys_are_rejected(client, upstream):
    """A batch that repeats a key must fail loudly instead of dropping it."""
    headers = await _token(client)
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "重复密钥渠道",
            "connection_url": upstream.base_url,
            "api_keys": [
                {"label": "主密钥", "api_key": "sk-duplicate-3333"},
                {"label": "重复", "api_key": "sk-duplicate-3333"},
            ],
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CONFLICT"
    message = response.json()["message"]
    assert "重复" in message
    assert "sk-…3333" in message

    listed = await client.get("/api/channels", headers=headers)
    assert listed.json()["total"] == 0


async def test_channel_key_label_and_enabled_can_be_updated(client, upstream):
    """Key remarks must be editable after the key was added."""
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "可改备注渠道",
            "connection_url": upstream.base_url,
            "api_keys": [{"label": "旧备注", "api_key": "sk-label-4444"}],
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    key_id = created.json()["api_keys"][0]["id"]

    updated = await client.patch(
        f"/api/channels/{channel_id}/keys/{key_id}",
        headers=headers,
        json={"label": "生产密钥", "enabled": False},
    )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["label"] == "生产密钥"
    assert body["enabled"] is False
    assert body["masked"] == "••••••4444"

    listed = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert listed.json()["items"][0]["label"] == "生产密钥"
    assert listed.json()["items"][0]["enabled"] is False


async def test_channel_key_update_rejects_key_from_another_channel(client, upstream):
    """A key id from another channel must not be editable through this route."""
    headers = await _token(client)
    first = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "甲渠道",
            "connection_url": upstream.base_url,
            "api_keys": [{"label": "甲", "api_key": "sk-first-5555"}],
        },
    )
    second = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "乙渠道",
            "connection_url": upstream.base_url,
            "api_keys": [{"label": "乙", "api_key": "sk-second-6666"}],
        },
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    foreign_key_id = second.json()["api_keys"][0]["id"]

    response = await client.patch(
        f"/api/channels/{first.json()['id']}/keys/{foreign_key_id}",
        headers=headers,
        json={"label": "越权"},
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    assert "密钥不存在" in response.json()["message"]

async def test_channel_patch_rejects_duplicate_api_key(client, upstream):
    """PATCHing the primary key must not silently collide with another key."""
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "主密钥防覆盖",
            "connection_url": upstream.base_url,
            "api_keys": [
                {"label": "主密钥", "api_key": "sk-primary-7777"},
                {"label": "备用", "api_key": "sk-backup-8888"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]

    duplicate = await client.patch(
        f"/api/channels/{channel_id}",
        headers=headers,
        json={"api_key": "sk-backup-8888"},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "CONFLICT"

    listed = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert [row["masked"] for row in listed.json()["items"]] == [
        "••••••7777",
        "••••••8888",
    ]


async def test_channel_primary_key_survives_same_millisecond_ordering(
    client, app, upstream
):
    """Keys saved in one call share ``created_at``; ULID order must not pick the primary.

    ``channel_keys.created_at`` only has millisecond precision, so two keys
    created by the same POST tie and ``ORDER BY created_at, id`` falls back to
    random ULIDs.  The row that backs ``channel.api_key_enc`` is the primary
    key and must be reported first no matter how that ordering lands.
    """
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "同毫秒主密钥",
            "connection_url": upstream.base_url,
            "api_keys": [
                {"label": "主密钥", "api_key": "sk-primary-5555"},
                {"label": "备用", "api_key": "sk-backup-6666"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    primary_id = created.json()["api_keys"][0]["id"]
    backup_id = created.json()["api_keys"][1]["id"]

    # Force the worst case: identical created_at plus a backup id that sorts first.
    tie = created.json()["api_keys"][0].get("created_at") or 1_794_510_855_435
    async with app.state.session_factory() as session:
        await session.execute(
            update(orm.ChannelKeyORM)
            .where(orm.ChannelKeyORM.id == primary_id)
            .values(id="z" + primary_id[1:], created_at=tie)
        )
        await session.execute(
            update(orm.ChannelKeyORM)
            .where(orm.ChannelKeyORM.id == backup_id)
            .values(id="0" + backup_id[1:], created_at=tie)
        )
        await session.commit()

    listed = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert [row["masked"] for row in listed.json()["items"]] == [
        "••••••5555",
        "••••••6666",
    ]

    channel = await client.get(f"/api/channels/{channel_id}", headers=headers)
    assert channel.json()["api_key_masked"] == "••••••5555"
    assert channel.json()["api_key_masks"][0] == "••••••5555"

    duplicate = await client.patch(
        f"/api/channels/{channel_id}",
        headers=headers,
        json={"api_key": "sk-backup-6666"},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "CONFLICT"

    # Deleting the backup must not move the primary pointer to another row.
    backup_row_id = next(
        row["id"] for row in listed.json()["items"] if row["masked"] == "••••••6666"
    )
    deleted = await client.delete(
        f"/api/channels/{channel_id}/keys/{backup_row_id}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text
    after = await client.get(f"/api/channels/{channel_id}", headers=headers)
    assert after.json()["api_key_masks"] == ["••••••5555"]
    assert after.json()["api_key_masked"] == "••••••5555"

    replaced = await client.patch(
        f"/api/channels/{channel_id}",
        headers=headers,
        json={"api_key": "sk-new-9999"},
    )
    assert replaced.status_code == 200, replaced.text
    # The backup row was deleted above, so the replacement swaps the only key.
    assert replaced.json()["api_key_masks"] == ["••••••9999"]
    assert replaced.json()["api_key_masked"] == "••••••9999"


async def test_channel_key_blank_label_falls_back_to_default_name(client, upstream):
    """A blank remark must fall back to the default key label."""
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "空备注渠道",
            "connection_url": upstream.base_url,
            "api_keys": [{"label": "旧备注", "api_key": "sk-label-7777"}],
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    key_id = created.json()["api_keys"][0]["id"]

    blanked = await client.patch(
        f"/api/channels/{channel_id}/keys/{key_id}",
        headers=headers,
        json={"label": "   "},
    )
    assert blanked.status_code == 200, blanked.text
    assert blanked.json()["label"] == "默认密钥"

    listed = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert listed.json()["items"][0]["label"] == "默认密钥"


async def test_delete_primary_key_promotes_enabled_key_not_disabled_one(client, upstream):
    """Deleting the primary key must never hand the channel a disabled key."""
    headers = await _token(client)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": "回填启用密钥渠道",
            "connection_url": upstream.base_url,
            "api_keys": [
                {"label": "主密钥", "api_key": "sk-primary-4444"},
                {"label": "已停用", "api_key": "sk-disabled-5555"},
                {"label": "启用备用", "api_key": "sk-enabled-6666"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]

    listed = await client.get(f"/api/channels/{channel_id}/keys", headers=headers)
    assert listed.status_code == 200, listed.text
    rows = {row["masked"]: row["id"] for row in listed.json()["items"]}

    disabled = await client.patch(
        f"/api/channels/{channel_id}/keys/{rows['••••••5555']}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text

    deleted = await client.delete(
        f"/api/channels/{channel_id}/keys/{rows['••••••4444']}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text
    after = await client.get(f"/api/channels/{channel_id}", headers=headers)
    # The disabled row sorts first by created_at, but the enabled backup must
    # become the primary credential (ssot §5.9).
    assert after.json()["api_key_masked"] == "••••••6666"
    assert after.json()["api_key_masks"][0] == "••••••6666"

    # With no enabled key left, fall back to the remaining row instead of clearing.
    disabled_backup = await client.patch(
        f"/api/channels/{channel_id}/keys/{rows['••••••6666']}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled_backup.status_code == 200, disabled_backup.text
    removed = await client.delete(
        f"/api/channels/{channel_id}/keys/{rows['••••••6666']}", headers=headers
    )
    assert removed.status_code == 204, removed.text
    fallback = await client.get(f"/api/channels/{channel_id}", headers=headers)
    assert fallback.json()["api_key_masked"] == "••••••5555"
