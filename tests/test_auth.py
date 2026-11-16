from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username: str = "admin", password: str = "admin"):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


async def test_login_with_seeded_admin_returns_jwt_and_admin_summary(client):
    response = await _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_at"] > int(datetime.now(timezone.utc).timestamp() * 1000)
    assert body["admin"]["username"] == "admin"
    assert body["admin"]["id"]

    me = await client.get("/api/auth/me", headers=_auth_header(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["id"] == body["admin"]["id"]
    assert me.json()["username"] == "admin"


async def test_login_with_wrong_password_returns_401_with_error_shape(client):
    response = await _login(client, password="not-the-password")

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHORIZED"
    assert isinstance(body["message"], str) and body["message"]
    assert "request_id" in body


async def test_me_without_token_returns_401(client):
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_me_with_invalid_signature_returns_401(client):
    forged = jwt.encode(
        {
            "sub": "someone",
            "username": "admin",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        },
        "not-the-signing-secret-but-long-enough-for-hs256",
        algorithm="HS256",
    )

    response = await client.get("/api/auth/me", headers=_auth_header(forged))

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_me_with_expired_token_returns_401(client, app):
    expired = jwt.encode(
        {
            "sub": "someone",
            "username": "admin",
            "exp": int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp()),
        },
        app.state.settings.jwt_secret,
        algorithm="HS256",
    )

    response = await client.get("/api/auth/me", headers=_auth_header(expired))

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_password_change_invalidates_old_password(client):
    login = await _login(client)
    token = login.json()["access_token"]

    response = await client.post(
        "/api/auth/password",
        headers=_auth_header(token),
        json={"current_password": "admin", "new_password": "new-secret-123"},
    )
    assert response.status_code == 200

    old_login = await _login(client, password="admin")
    assert old_login.status_code == 401

    new_login = await _login(client, password="new-secret-123")
    assert new_login.status_code == 200


async def test_password_change_with_wrong_current_password_returns_401(client):
    login = await _login(client)
    token = login.json()["access_token"]

    response = await client.post(
        "/api/auth/password",
        headers=_auth_header(token),
        json={"current_password": "wrong", "new_password": "new-secret-123"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_default_admin_seed_is_idempotent_across_restarts(database_url):
    from l07_bootstrap.app_factory import create_app

    for _ in range(2):
        application = create_app(database_url=database_url)
        async with application.router.lifespan_context(application):
            pass

    from sqlalchemy import func, select

    from l04_repo.orm import AdminUserORM

    application = create_app(database_url=database_url)
    async with application.state.session_factory() as session:
        count = (await session.execute(select(func.count(AdminUserORM.id)))).scalar_one()
    await application.state.db_engine.dispose()

    assert count == 1


async def test_seeded_password_is_hashed_not_plaintext(client, app):
    from sqlalchemy import select

    from l04_repo.orm import AdminUserORM

    async with app.state.session_factory() as session:
        admin = (await session.execute(select(AdminUserORM))).scalars().one()

    assert admin.password_hash != "admin"
    assert admin.password_hash.startswith("pbkdf2$")
