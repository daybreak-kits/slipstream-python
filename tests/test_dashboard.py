from __future__ import annotations

import uuid

from l04_repo import orm
from l05_service import aggregates, billing
from l05_service.llm import Usage


async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_cache_ratio_containment_and_parallel() -> None:
    assert billing.cache_ratio(Usage(prompt_tokens=100, completion_tokens=1, total_tokens=101, cached_tokens=40)) == 0.4
    assert billing.cache_ratio(Usage(prompt_tokens=20, completion_tokens=1, total_tokens=21, cached_tokens=80)) == 0.8
    assert billing.cache_ratio(Usage(prompt_tokens=10, completion_tokens=1, total_tokens=11)) == 0.0


def test_cost_zero_without_prices_and_throughput_non_stream() -> None:
    usage = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
    assert billing.compute_cost(None, usage) == 0.0
    assert billing.throughput_tps(100, 5000, None) is None


def test_throughput_requires_meaningful_generation_window() -> None:
    # 上游缓冲后单块吐出：152 token 在 5ms 内到齐（首字≈总耗时），没有生成速度可言
    assert billing.throughput_tps(152, 4673, 4668) is None
    # 边界：生成窗口刚好达到 100ms 才计速率
    assert billing.throughput_tps(152, 4768, 4668) == 1520.0


async def test_summary_and_daily_aggregates(client) -> None:
    headers = await _login(client)
    start = 1_760_000_000_000
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, (status, ttft, total, tokens) in enumerate(
            [
                (200, 100, 1000, 10),
                (200, 300, 2000, 20),
                (500, None, 3000, 0),
                (200, 200, 1500, 30),
                (200, 400, 2500, 40),
                (200, 500, 3500, 50),
            ]
        ):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"req-{index}",
                    requested_model="group-a",
                    channel_name="渠道 A",
                    http_status=status,
                    prompt_tokens=tokens,
                    completion_tokens=tokens * 2,
                    cached_tokens=tokens,
                    cost_usd=0.001 * index,
                    ttft_ms=ttft,
                    total_ms=total,
                    created_at=start + index * 1000,
                )
            )
        await session.commit()
    response = await client.get(
        "/api/dashboard/summary",
        headers=headers,
        params={"start_ms": start, "end_ms": start + 86_400_000},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requests"] == 6
    assert body["prompt_tokens"] == 150
    assert body["completion_tokens"] == 300
    assert body["cached_tokens"] == 150
    assert body["errors"] == 1
    assert body["error_rate"] == round(1 / 6, 6)
    assert body["latency"]["samples"] == 5
    assert body["p95_ttft_ms"] == 500
    assert body["p95_total_ms"] == 3500
    assert body["latency"]["ttft"]["p50"] == 300
    assert body["latency"]["ttft"]["p99"] == 500
    assert body["latency"]["total"]["p50"] == 2000
    assert body["latency"]["ttfa"] is None
    assert body["latency"]["ttlt"] is None
    assert body["active_minutes"] == 1
    assert body["peak_rpm"] == 6
    assert body["rpm"] == 6.0
    assert body["tpm"] == 450.0
    assert body["span_minutes"] == 1440.0

    filtered = await client.get(
        "/api/dashboard/summary",
        headers=headers,
        params={
            "start_ms": start,
            "end_ms": start + 86_400_000,
            "channel": "渠道 A",
            "model": "group-a",
            "status": "error",
        },
    )
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert filtered_body["requests"] == 1
    assert filtered_body["errors"] == 1
    assert filtered_body["error_rate"] == 1.0
    assert filtered_body["latency"]["samples"] == 0
    assert filtered_body["latency"]["ttft"] is None

    rebuild = await client.post(
        "/api/dashboard/rebuild",
        headers=headers,
        params={"start_ms": start, "end_ms": start + 86_400_000},
    )
    assert rebuild.status_code == 200
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(orm.DailyAggregateORM.__table__.select())).all()
        assert len(rows) == 1
        assert rows[0].requests == 6
        assert rows[0].errors == 1


def test_percentile_nearest_rank() -> None:
    assert aggregates.percentile([], 95) == 0
    assert aggregates.percentile([10, 20, 30, 40], 50) == 20
    assert aggregates.percentile([10, 20, 30, 40], 95) == 40


def test_cache_hit_rate_uses_upstream_reported_rows_only() -> None:
    """Estimated rows carry no cache information and must not dilute the rate."""
    rows = [
        orm.RequestLogORM(prompt_tokens=1000, cached_tokens=500, usage_estimated=False),
        orm.RequestLogORM(prompt_tokens=1000, cached_tokens=0, usage_estimated=False),
        orm.RequestLogORM(prompt_tokens=1000, cached_tokens=0, usage_estimated=True),
    ]

    assert aggregates.cache_hit_rate_samples(rows) == 2
    assert aggregates.cache_hit_rate(rows) == 0.25

    parallel = [
        orm.RequestLogORM(prompt_tokens=200, cached_tokens=800, usage_estimated=False),
    ]
    assert aggregates.cache_hit_rate(parallel) == 0.8


async def test_summary_reports_token_weighted_cache_hit_rate(client) -> None:
    """The dashboard must expose one token-weighted hit rate, not a per-row mean."""
    headers = await _login(client)
    start = 1_760_100_000_000
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, (prompt, cached, estimated) in enumerate(
            [
                (1000, 800, False),
                (1000, 0, False),
                (500, 0, True),
            ]
        ):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"req-cache-{index}",
                    requested_model="group-a",
                    channel_name="渠道 A",
                    http_status=200,
                    prompt_tokens=prompt,
                    completion_tokens=10,
                    cached_tokens=cached,
                    usage_estimated=estimated,
                    total_ms=1000,
                    created_at=start + index * 1000,
                )
            )
        await session.commit()
    response = await client.get(
        "/api/dashboard/summary",
        headers=headers,
        params={"start_ms": start, "end_ms": start + 86_400_000},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cache_hit_rate_samples"] == 2
    assert body["cache_hit_rate"] == round(800 / 2000, 6)
    assert body["estimated_rows"] == 1


async def test_summary_and_daily_ignore_estimated_rows_for_tokens_and_cost(client) -> None:
    """ssot §5.8: tokens/cost count only real usage; estimated rows only add estimated_rows."""
    headers = await _login(client)
    start = 1_760_200_000_000
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="req-real-0",
                requested_model="group-a",
                channel_name="渠道 A",
                source="chat",
                http_status=200,
                prompt_tokens=100,
                completion_tokens=200,
                cached_tokens=50,
                cost_usd=0.01,
                usage_estimated=False,
                total_ms=1000,
                created_at=start,
            )
        )
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="req-est-0",
                requested_model="group-a",
                channel_name="渠道 A",
                source="chat",
                http_status=200,
                prompt_tokens=1000,
                completion_tokens=2000,
                cached_tokens=500,
                cost_usd=0.5,
                usage_estimated=True,
                total_ms=1000,
                created_at=start + 1000,
            )
        )
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="req-probe-0",
                requested_model="",
                channel_name="探测渠道",
                source="probe",
                http_status=200,
                prompt_tokens=700,
                completion_tokens=0,
                cost_usd=0.7,
                usage_estimated=False,
                total_ms=1000,
                created_at=start + 2000,
            )
        )
        await session.commit()

    window = {"start_ms": start, "end_ms": start + 60_000}
    summary = await client.get("/api/dashboard/summary", headers=headers, params=window)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    # source=chat pins the dashboard: probe row is out, estimated row still counts as a request.
    assert body["requests"] == 2
    assert body["prompt_tokens"] == 100
    assert body["completion_tokens"] == 200
    assert body["cached_tokens"] == 50
    assert body["cost_usd"] == 0.01
    assert body["estimated_rows"] == 1
    assert body["tpm"] == 300.0

    rebuild = await client.post("/api/dashboard/rebuild", headers=headers, params=window)
    assert rebuild.status_code == 200
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(orm.DailyAggregateORM.__table__.select())).all()
    assert len(rows) == 1
    assert rows[0].requests == 2
    assert rows[0].prompt_tokens == 100
    assert rows[0].completion_tokens == 200
    assert rows[0].cached_tokens == 50
    assert round(float(rows[0].cost_usd), 8) == 0.01


async def test_dashboard_excludes_probe_traffic(client) -> None:
    headers = await _login(client)
    start = 1_760_000_000_000
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index in range(2):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"req-chat-{index}",
                    requested_model="group-a",
                    channel_name="渠道 A",
                    source="chat",
                    http_status=200,
                    prompt_tokens=10,
                    completion_tokens=20,
                    cached_tokens=10,
                    cost_usd=0.001,
                    total_ms=1000,
                    created_at=start + index * 1000,
                )
            )
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="req-probe-0",
                requested_model="",
                channel_name="探测渠道",
                source="probe",
                http_status=200,
                prompt_tokens=500,
                completion_tokens=500,
                cost_usd=0.5,
                total_ms=1000,
                created_at=start + 2000,
            )
        )
        await session.commit()

    window = {"start_ms": start, "end_ms": start + 60_000}
    summary = await client.get("/api/dashboard/summary", headers=headers, params=window)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["requests"] == 2
    assert body["prompt_tokens"] == 20
    assert body["cost_usd"] == 0.002
    assert body["estimated_rows"] == 0

    timeseries = await client.get("/api/dashboard/timeseries", headers=headers, params=window)
    assert timeseries.status_code == 200, timeseries.text
    ts_body = timeseries.json()
    assert sum(point["requests"] for point in ts_body["points"]) == 2
    assert [item["name"] for item in ts_body["top_models"]] == ["group-a"]
    assert [item["name"] for item in ts_body["top_channels"]] == ["渠道 A"]

    logs_summary = await client.get("/api/logs/summary", headers=headers, params=window)
    assert logs_summary.status_code == 200, logs_summary.text
    logs_body = logs_summary.json()
    assert logs_body["requests"] == 3
    assert logs_body["prompt_tokens"] == 520
    assert logs_body["cost_usd"] == 0.502


async def test_timeseries_model_p95_requires_five_samples(client) -> None:
    headers = await _login(client)
    start = 1_760_000_000_000
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for model_name, totals in (
            ("model-few", [1000, 2000, 3000, 4000]),
            ("model-enough", [1000, 2000, 3000, 4000, 5000]),
        ):
            for index, total in enumerate(totals):
                session.add(
                    orm.RequestLogORM(
                        id=str(uuid.uuid4()),
                        request_id=f"req-{model_name}-{index}",
                        requested_model=model_name,
                        channel_name="渠道 A",
                        source="chat",
                        http_status=200,
                        prompt_tokens=1,
                        completion_tokens=2,
                        ttft_ms=100,
                        total_ms=total,
                        created_at=start + index * 1000,
                    )
                )
        await session.commit()

    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={"range": "custom", "start_ms": start, "end_ms": start + 86_400_000},
    )
    assert response.status_code == 200, response.text
    models: dict[str, dict] = {}
    for point in response.json()["points"]:
        for item in point["by_model"]:
            models[item["name"]] = item
    assert models["model-few"]["p95_total_ms"] is None
    assert models["model-enough"]["p95_total_ms"] == 5000
