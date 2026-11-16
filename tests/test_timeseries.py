from __future__ import annotations

import uuid
from datetime import datetime, timezone

from l04_repo import orm
from l05_service import timeseries


async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_timeseries_hour_buckets_and_timezone(client) -> None:
    headers = await _login(client)
    # 2026-01-05 23:30 UTC = 2026-01-06 07:30 Asia/Shanghai
    base = int(datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index in range(2):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"ts-{index}",
                    requested_model="model-a" if index == 0 else "model-b",
                    channel_name="渠道 A",
                    http_status=200 if index == 0 else 500,
                    error_code=None if index == 0 else "UPSTREAM_ERROR",
                    total_ms=1000,
                    ttft_ms=100,
                    created_at=base + index * 1000,
                )
            )
        await session.commit()
    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "Asia/Shanghai",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["granularity"] == "hour"
    assert body["timezone"] == "Asia/Shanghai"
    assert len(body["points"]) == 1
    assert body["points"][0]["requests"] == 2
    assert body["points"][0]["errors"] == 1
    assert body["top_models"][0]["name"] == "model-a"
    assert body["error_distribution"][0]["code"] == "UPSTREAM_ERROR"
    assert body["heatmap"][0] == {"weekday": 2, "hour": 7, "requests": 2}
    by_model = {item["name"]: item for item in body["points"][0]["by_model"]}
    assert by_model["model-a"]["requests"] == 1
    assert by_model["model-b"]["errors"] == 1

    filtered = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "Asia/Shanghai",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
            "channel": "渠道 A",
            "model": "model-b",
            "status": "error",
        },
    )
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert filtered_body["points"][0]["requests"] == 1
    assert filtered_body["points"][0]["errors"] == 1
    assert filtered_body["top_models"][0]["name"] == "model-b"

async def test_timeseries_top_lists_and_by_channel_include_usage(client) -> None:
    headers = await _login(client)
    base = int(datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [
        ("model-a", "渠道 A", 100, 20, 10, 0.001),
        ("model-a", "渠道 A", 50, 5, 0, 0.002),
        ("model-b", "渠道 B", 30, 7, 3, 0.003),
    ]
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, (model, channel, prompt, completion, cached, cost) in enumerate(rows):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"ts-usage-{index}",
                    requested_model=model,
                    channel_name=channel,
                    http_status=200,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cached_tokens=cached,
                    cost_usd=cost,
                    total_ms=1000,
                    created_at=base + index * 1000,
                )
            )
        await session.commit()

    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "UTC",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    point = body["points"][0]

    top_models = {item["name"]: item for item in body["top_models"]}
    assert top_models["model-a"]["requests"] == 2
    assert top_models["model-a"]["prompt_tokens"] == 150
    assert top_models["model-a"]["completion_tokens"] == 25
    assert top_models["model-a"]["cached_tokens"] == 10
    assert top_models["model-a"]["cost_usd"] == 0.003
    assert top_models["model-b"]["prompt_tokens"] == 30
    assert top_models["model-b"]["cost_usd"] == 0.003

    top_channels = {item["name"]: item for item in body["top_channels"]}
    assert top_channels["渠道 A"]["requests"] == 2
    assert top_channels["渠道 A"]["prompt_tokens"] == 150
    assert top_channels["渠道 A"]["cost_usd"] == 0.003
    assert top_channels["渠道 B"]["requests"] == 1

    by_channel = {item["name"]: item for item in point["by_channel"]}
    assert by_channel["渠道 A"]["requests"] == 2
    assert by_channel["渠道 A"]["prompt_tokens"] == 150
    assert by_channel["渠道 B"]["completion_tokens"] == 7
    assert sum(item["requests"] for item in point["by_channel"]) == point["requests"]
    assert sum(item["prompt_tokens"] for item in point["by_channel"]) == point["prompt_tokens"]
    assert sum(item["completion_tokens"] for item in point["by_channel"]) == point["completion_tokens"]
    assert sum(item["cost_usd"] for item in point["by_channel"]) == point["cost_usd"]


def test_today_starts_at_local_midnight() -> None:
    # 2026-01-05 23:30 UTC is 2026-01-06 07:30 in Shanghai, so "today"
    # must start at 2026-01-06 00:00 +08:00 = 2026-01-05 16:00 UTC.
    now = datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc)
    start, end, granularity = timeseries.range_window_ms("today", "Asia/Shanghai", now=now)
    assert start == int(datetime(2026, 1, 5, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert end == int(now.timestamp() * 1000)
    assert granularity == "hour"


def test_invalid_timezone_falls_back_to_utc() -> None:
    assert str(timeseries._tz("Not/AZone")) == "UTC"
    assert str(timeseries._tz("Asia/Shanghai")) == "Asia/Shanghai"


async def test_timeseries_excludes_estimated_usage_from_tokens_and_cost(client) -> None:
    # Estimated rows never contribute tokens or money; requests still count them.
    headers = await _login(client)
    base = int(datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, estimated in enumerate((False, True)):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"ts-est-{index}",
                    requested_model="model-a",
                    channel_name="渠道 A",
                    http_status=200,
                    prompt_tokens=100 if not estimated else 1000,
                    completion_tokens=50,
                    cached_tokens=10 if not estimated else 100,
                    cost_usd=0.002 if not estimated else 0.5,
                    usage_estimated=estimated,
                    created_at=base + index * 1000,
                )
            )
        await session.commit()
    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "UTC",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    point = response.json()["points"][0]
    assert point["requests"] == 2
    assert point["prompt_tokens"] == 100
    assert point["cached_tokens"] == 10
    assert point["cost_usd"] == 0.002
    # The per-model legend reads the same cohort: estimated usage must not
    # inflate its token or cost sums either.
    by_model = {item["name"]: item for item in point["by_model"]}
    assert by_model["model-a"]["requests"] == 2
    assert by_model["model-a"]["prompt_tokens"] == 100
    assert by_model["model-a"]["cached_tokens"] == 10
    assert by_model["model-a"]["cost_usd"] == 0.002


async def test_timeseries_labels_unrouted_requests_without_available_channel(client) -> None:
    # Requests that never reached any channel (breaker tripped -> 503) store an
    # empty channel_name; the legend must explain it instead of looking like a
    # lost channel name.
    headers = await _login(client)
    base = int(datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="ts-no-channel",
                requested_model="model-a",
                channel_name="",
                http_status=503,
                error_code="NO_AVAILABLE_CHANNEL",
                total_ms=3,
                created_at=base,
            )
        )
        await session.commit()
    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "UTC",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["name"] for item in body["top_channels"]] == ["请求模型无可用渠道"]
    assert body["top_channels"][0]["requests"] == 1
    assert body["top_channels"][0]["prompt_tokens"] == 0
    assert [item["name"] for item in body["points"][0]["by_channel"]] == ["请求模型无可用渠道"]
    assert body["error_distribution"] == [{"code": "NO_AVAILABLE_CHANNEL", "count": 1}]


async def test_timeseries_by_upstream_model_groups_and_falls_back(client) -> None:
    # The upstream-model legend groups on upstream_model, not on what the client
    # asked for, and an empty upstream_model (routing failure) joins (未命名)
    # instead of showing an empty legend name.
    headers = await _login(client)
    base = int(datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [
        ("model-a", "upstream-a", 200),
        ("model-a", "upstream-a", 200),
        ("model-b", "upstream-b", 200),
        ("model-c", "", 503),
    ]
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, (requested, upstream, status) in enumerate(rows):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"ts-upstream-{index}",
                    requested_model=requested,
                    upstream_model=upstream,
                    channel_name="渠道 A" if upstream else "",
                    http_status=status,
                    total_ms=1000,
                    created_at=base + index * 1000,
                )
            )
        await session.commit()
    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "UTC",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    point = response.json()["points"][0]
    by_upstream_model = {item["name"]: item for item in point["by_upstream_model"]}
    assert set(by_upstream_model) == {"upstream-a", "upstream-b", "(未命名)"}
    assert by_upstream_model["upstream-a"]["requests"] == 2
    assert by_upstream_model["upstream-b"]["requests"] == 1
    assert by_upstream_model["(未命名)"]["requests"] == 1
    assert "" not in by_upstream_model
    assert {item["name"] for item in point["by_model"]} == {"model-a", "model-b", "model-c"}


async def test_timeseries_top_upstream_models_names_come_from_upstream_model(client) -> None:
    # Two different upstream models behind one requested model must stay two
    # groups: the legend name is upstream_model, never requested_model.
    headers = await _login(client)
    base = int(datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [("upstream-a", 100, 0.001), ("upstream-b", 50, 0.002)]
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, (upstream, prompt, cost) in enumerate(rows):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=f"ts-top-upstream-{index}",
                    requested_model="model-a",
                    upstream_model=upstream,
                    channel_name="渠道 A",
                    http_status=200,
                    prompt_tokens=prompt,
                    completion_tokens=10,
                    cost_usd=cost,
                    total_ms=1000,
                    created_at=base + index * 1000,
                )
            )
        await session.commit()
    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "UTC",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    top_upstream_models = {item["name"]: item for item in body["top_upstream_models"]}
    assert len(body["top_upstream_models"]) == 2
    assert set(top_upstream_models) == {"upstream-a", "upstream-b"}
    assert top_upstream_models["upstream-a"]["requests"] == 1
    assert top_upstream_models["upstream-a"]["prompt_tokens"] == 100
    assert top_upstream_models["upstream-b"]["prompt_tokens"] == 50
    assert "model-a" not in top_upstream_models

    # The requested-model dimension still sees one model for both rows.
    top_models = {item["name"]: item for item in body["top_models"]}
    assert set(top_models) == {"model-a"}
    assert top_models["model-a"]["requests"] == 2

    point = body["points"][0]
    assert len(point["by_upstream_model"]) == 2
    assert sum(item["requests"] for item in point["by_upstream_model"]) == point["requests"]


async def _seed_one_dimension_row(client) -> int:
    # One routed request shared by the dimension-trimming tests.
    base = int(datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="ts-dimension",
                requested_model="model-a",
                upstream_model="upstream-a",
                channel_name="渠道 A",
                http_status=200,
                total_ms=1000,
                created_at=base,
            )
        )
        await session.commit()
    return base


async def test_timeseries_without_dimension_returns_all_by_lists(client) -> None:
    headers = await _login(client)
    base = await _seed_one_dimension_row(client)
    response = await client.get(
        "/api/dashboard/timeseries",
        headers=headers,
        params={
            "range": "custom",
            "tz": "UTC",
            "start_ms": base - 3_600_000,
            "end_ms": base + 3_600_000,
        },
    )
    assert response.status_code == 200, response.text
    point = response.json()["points"][0]
    assert [item["name"] for item in point["by_model"]] == ["model-a"]
    assert [item["name"] for item in point["by_upstream_model"]] == ["upstream-a"]
    assert [item["name"] for item in point["by_channel"]] == ["渠道 A"]


async def test_timeseries_dimension_upstream_fills_only_by_upstream_model(client) -> None:
    headers = await _login(client)
    base = await _seed_one_dimension_row(client)
    params = {
        "range": "custom",
        "tz": "UTC",
        "start_ms": base - 3_600_000,
        "end_ms": base + 3_600_000,
        "dimension": "upstream",
    }
    response = await client.get("/api/dashboard/timeseries", headers=headers, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    point = body["points"][0]
    assert [item["name"] for item in point["by_upstream_model"]] == ["upstream-a"]
    assert point["by_model"] == []
    assert point["by_channel"] == []
    assert point["requests"] == 1  # 桶本身不受 dimension 影响，形状只裁 by_*

    rejected = await client.get(
        "/api/dashboard/timeseries", headers=headers, params={**params, "dimension": "bogus"}
    )
    assert rejected.status_code == 422
