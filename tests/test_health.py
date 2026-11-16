from __future__ import annotations


async def test_health_returns_ok(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Slipstream"


async def test_cors_preflight_allows_configured_origin(client):
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3680",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3680"


async def test_openapi_docs_available(client):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
