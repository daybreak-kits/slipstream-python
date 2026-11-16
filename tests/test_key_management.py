from __future__ import annotations


async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create(client, headers, label: str) -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


async def test_key_rename_toggle_and_delete(client) -> None:
    headers = await _login(client)
    created = await _create(client, headers, "alpha")
    key_id = created["id"]

    duplicate = await client.patch(
        f"/api/keys/{key_id}", headers=headers, json={"label": "beta"}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["label"] == "beta"

    other = await _create(client, headers, "gamma")
    conflict = await client.patch(
        f"/api/keys/{key_id}", headers=headers, json={"label": "gamma"}
    )
    assert conflict.status_code == 409

    disabled = await client.patch(
        f"/api/keys/{key_id}", headers=headers, json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    auth = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created['key']}"},
        json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert auth.status_code == 401

    deleted = await client.delete(f"/api/keys/{key_id}", headers=headers)
    assert deleted.status_code == 204
    after = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created['key']}"},
        json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert after.status_code == 401

    listed = await client.get("/api/keys", headers=headers)
    assert all(item["label"] != "beta" for item in listed.json()["items"])
    assert other["key"] not in listed.text
