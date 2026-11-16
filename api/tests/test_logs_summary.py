from __future__ import annotations

import uuid

from l04_repo import orm


async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_logs(client, start: int) -> None:
    rows = [
        (10_000, 100, 50, 40, False, 200, 0.001),
        (20_000, 100, 50, 0, False, 200, 0.002),
        (60_000, 900, 100, 0, True, 200, 0.003),
        (120_000, 0, 0, 0, False, 500, 0.0),
    ]
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, (offset, prompt, completion, cached, estimated, status, cost) in enumerate(rows):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"req-summary-{index}",
                    requested_model="model-a",
                    channel_name="渠道 A",
                    http_status=status,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cached_tokens=cached,
                    usage_estimated=estimated,
                    cost_usd=cost,
                    total_ms=1000,
                    created_at=start + offset,
                )
            )
        await session.commit()


async def test_logs_summary_uses_active_minutes_and_real_usage(client) -> None:
    headers = await _login(client)
    start = 1_760_000_000_000
    await _seed_logs(client, start)
    response = await client.get("/api/logs/summary", headers=headers, params={"start_ms": start, "end_ms": start + 180_000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requests"] == 4
    assert body["prompt_tokens"] == 200
    assert body["completion_tokens"] == 100
    assert body["cached_tokens"] == 40
    assert body["cost_usd"] == 0.003  # estimated row's cost is excluded (ssot §5.8)
    assert body["cache_hit_rate"] == 0.2
    assert body["cache_hit_rate_samples"] == 3
    assert body["estimated_rows"] == 1
    assert body["active_minutes"] == 3
    assert body["peak_rpm"] == 2
    assert body["rpm"] == 1.33
    assert body["tpm"] == 100.0
    assert body["span_minutes"] == 3.0


async def test_logs_summary_respects_filters(client) -> None:
    headers = await _login(client)
    start = 1_760_000_000_000
    await _seed_logs(client, start)
    errors = await client.get("/api/logs/summary", headers=headers, params={"start_ms": start, "end_ms": start + 180_000, "status": "error"})
    assert errors.status_code == 200, errors.text
    body = errors.json()
    assert body["requests"] == 1
    assert body["tpm"] == 0.0

    search = await client.get("/api/logs/summary", headers=headers, params={"start_ms": start, "end_ms": start + 180_000, "q": "req-summary-2"})
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["requests"] == 1
    assert body["estimated_rows"] == 1
    assert body["prompt_tokens"] == 0
    assert body["tpm"] == 0.0


async def test_logs_summary_rejects_inverted_range(client) -> None:
    headers = await _login(client)
    response = await client.get("/api/logs/summary", headers=headers, params={"start_ms": 2_000, "end_ms": 1_000})
    assert response.status_code == 400
    assert "结束时间" in response.text

async def test_logs_summary_counts_probe_rows_in_requests(client) -> None:
    """The logs page keeps probe traffic in requests; tokens/cost stay real-usage gated."""
    headers = await _login(client)
    start = 1_760_000_000_000
    await _seed_logs(client, start)
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="req-probe-0",
                requested_model="",
                channel_name="探测渠道",
                source="probe",
                http_status=200,
                prompt_tokens=700,
                completion_tokens=100,
                cached_tokens=0,
                usage_estimated=False,
                cost_usd=0.004,
                total_ms=1000,
                created_at=start + 240_000,
            )
        )
        await session.commit()

    response = await client.get(
        "/api/logs/summary",
        headers=headers,
        params={"start_ms": start, "end_ms": start + 300_000},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requests"] == 5
    assert body["prompt_tokens"] == 200 + 700
    assert body["completion_tokens"] == 100 + 100
    assert body["cost_usd"] == round(0.003 + 0.004, 8)
    assert body["estimated_rows"] == 1

