from __future__ import annotations

import json
import uuid

from l04_repo import orm, repo
from l05_service import log_views

BASE_MS = 1_762_000_000_000
MINUTE = 60_000


async def _login(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _attempt(
    model: str = "deepseek-flash",
    status: str = "success",
    *,
    error_code: str | None = None,
    channel: str = "VendorQ",
    ms: int = 10,
    message: str = "",
    at: int | None = None,
    http_status: int | None = None,
) -> dict:
    attempt = {
        "channel_model_id": "cm-1",
        "channel_slug": channel.lower(),
        "channel_name": channel,
        "upstream_model": model,
        "status": status,
        "error_code": error_code,
        "message": message,
        "ms": ms,
        "round": 1,
        "skipped": status == "skipped",
        "priority_path": [1],
    }
    if at is not None:
        attempt["at"] = at
    if http_status is not None:
        attempt["http_status"] = http_status
    return attempt


async def _seed(client, rows: list[dict]) -> None:
    async with client._transport.app.state.session_factory() as session:
        for index, row in enumerate(rows):
            session.add(
                orm.RequestLogORM(
                    id=str(uuid.uuid4()),
                    request_id=row.get("request_id") or f"req-{index:03d}",
                    user_id=row.get("user_id"),
                    api_key_label=row.get("api_key_label", ""),
                    requested_model=row.get("requested_model", "model-a"),
                    channel_name=row.get("channel_name", "VendorQ"),
                    upstream_model=row.get("upstream_model", "deepseek-flash"),
                    http_status=row.get("http_status", 200),
                    error_code=row.get("error_code"),
                    prompt_tokens=row.get("prompt_tokens", 0),
                    completion_tokens=row.get("completion_tokens", 0),
                    cached_tokens=row.get("cached_tokens", 0),
                    cost_usd=row.get("cost_usd", 0.0),
                    ttft_ms=row.get("ttft_ms"),
                    total_ms=row.get("total_ms", 0),
                    attempts_json=json.dumps(row.get("attempts", [])),
                    created_at=row.get("created_at", BASE_MS + index * MINUTE),
                )
            )
        await session.commit()


async def _get(client, headers, **params) -> dict:
    response = await client.get("/api/logs", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_requests_view_keeps_one_row_per_request(client) -> None:
    headers = await _login(client)
    await _seed(client, [{"request_id": f"req-{index}"} for index in range(3)])

    body = await _get(client, headers, page_size=50)

    assert body["view"] == "requests"
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["groups"] == []
    assert body["scan_limited"] is False


async def test_combo_view_aggregates_usage_and_percentiles(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "a-1",
                "requested_model": "combo-a",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "cached_tokens": 40,
                "cost_usd": 0.001,
                "ttft_ms": 100,
                "total_ms": 1000,
                "attempts": [_attempt(channel="VendorQ")],
            },
            {
                "request_id": "a-2",
                "requested_model": "combo-a",
                "prompt_tokens": 200,
                "completion_tokens": 60,
                "cost_usd": 0.002,
                "ttft_ms": 200,
                "total_ms": 2000,
                "attempts": [
                    _attempt("minimax-m3", channel="minimaxi"),
                    _attempt(channel="VendorQ"),
                ],
            },
            {
                "request_id": "a-3",
                "requested_model": "combo-a",
                "prompt_tokens": 300,
                "completion_tokens": 70,
                "cost_usd": 0.003,
                "http_status": 500,
                "error_code": "UPSTREAM_ERROR",
                "ttft_ms": 300,
                "total_ms": 3000,
                "attempts": [_attempt(status="error", error_code="upstream", channel="BeiDou")],
            },
            {
                "request_id": "b-1",
                "requested_model": "combo-b",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": 0.0005,
                "ttft_ms": 50,
                "total_ms": 500,
                "attempts": [_attempt()],
            },
        ],
    )

    body = await _get(client, headers, view="combo")

    assert body["view"] == "combo"
    assert body["items"] == []
    assert body["total"] == 2
    combo_a, combo_b = body["groups"]
    assert combo_a["key"] == "combo-a"
    assert combo_a["label"] == "combo-a"
    assert combo_a["requests"] == 3
    assert combo_a["attempts"] == 4  # two of them failed over inside the combo
    assert combo_a["errors"] == 1
    assert combo_a["prompt_tokens"] == 600
    assert combo_a["completion_tokens"] == 180
    assert combo_a["cached_tokens"] == 40
    assert combo_a["cache_ratio"] == 0.066667
    assert combo_a["cost_usd"] == 0.006
    assert combo_a["ttft_p50_ms"] == 200
    assert combo_a["total_p50_ms"] == 2000
    assert combo_a["total_p95_ms"] == 3000
    assert combo_a["channels"] == ["BeiDou", "VendorQ", "minimaxi"]
    assert combo_a["error_codes"] == ["UPSTREAM_ERROR"]
    assert combo_a["sample_request_ids"] == ["a-3", "a-2", "a-1"]
    assert combo_a["first_at"] == BASE_MS
    assert combo_a["last_at"] == BASE_MS + 2 * MINUTE
    assert combo_b["key"] == "combo-b"
    assert combo_b["requests"] == 1


async def test_status_view_cuts_segments_on_status_change(client) -> None:
    headers = await _login(client)
    rows = [
        {
            "request_id": f"r-429a-{index}",
            "http_status": 429,
            "prompt_tokens": 10,
            "total_ms": 100,
            "attempts": [_attempt(status="error", error_code="rate_limit")],
            "created_at": BASE_MS + index * MINUTE,
        }
        for index in range(3)
    ]
    rows.append(
        {
            "request_id": "r-ok",
            "http_status": 200,
            "prompt_tokens": 20,
            "total_ms": 200,
            "attempts": [_attempt()],
            "created_at": BASE_MS + 3 * MINUTE,
        }
    )
    rows.extend(
        {
            "request_id": f"r-429b-{index}",
            "http_status": 429,
            "prompt_tokens": 30,
            "total_ms": 300,
            "attempts": [_attempt(status="error", error_code="rate_limit")],
            "created_at": BASE_MS + (4 + index) * MINUTE,
        }
        for index in range(2)
    )
    await _seed(client, rows)

    body = await _get(client, headers, view="status")

    assert body["view"] == "status"
    assert body["items"] == []
    assert body["total"] == 3
    newest, middle, oldest = body["groups"]
    assert newest["status_code"] == 429
    assert newest["status_label"] == "429"
    assert newest["attempts"] == 2
    assert newest["requests"] == 2
    assert newest["errors"] == 2
    assert newest["prompt_tokens"] == 60
    assert newest["total_p50_ms"] == 300
    assert middle["status_code"] == 200
    assert middle["attempts"] == 1
    assert middle["errors"] == 0
    assert middle["prompt_tokens"] == 20
    assert oldest["status_code"] == 429
    assert oldest["attempts"] == 3
    assert oldest["requests"] == 3
    assert oldest["prompt_tokens"] == 30
    assert oldest["first_at"] == BASE_MS
    assert oldest["last_at"] == BASE_MS + 2 * MINUTE
    assert {group["label"] for group in body["groups"]} == {"deepseek-flash"}


async def test_status_view_ignores_skipped_attempts(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "skip-1",
                "attempts": [
                    _attempt(status="skipped", error_code="CIRCUIT_OPEN", channel="SenseNova"),
                    _attempt(channel="VendorQ"),
                ],
            }
        ],
    )

    body = await _get(client, headers, view="status")

    assert body["total"] == 1
    group = body["groups"][0]
    assert group["attempts"] == 1
    assert group["status_code"] == 200
    assert group["channels"] == ["VendorQ"]


async def test_status_view_breaks_on_idle_gap(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {"request_id": "g-1", "attempts": [_attempt()], "created_at": BASE_MS},
            {
                "request_id": "g-2",
                "attempts": [_attempt()],
                "created_at": BASE_MS + MINUTE,
            },
            {
                "request_id": "g-3",
                "attempts": [_attempt()],
                "created_at": BASE_MS + 50 * MINUTE,
            },
        ],
    )

    body = await _get(client, headers, view="status")

    assert log_views.STATUS_SEGMENT_IDLE_MS == 5 * MINUTE
    assert body["total"] == 2
    newest, oldest = body["groups"]
    assert oldest["attempts"] == 2
    assert oldest["first_at"] == BASE_MS
    assert oldest["last_at"] == BASE_MS + MINUTE
    assert newest["attempts"] == 1
    assert newest["first_at"] == BASE_MS + 50 * MINUTE


async def test_status_view_breaks_on_upstream_model_change(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {"request_id": "m-1", "attempts": [_attempt("model-a")], "created_at": BASE_MS},
            {
                "request_id": "m-2",
                "attempts": [_attempt("model-b")],
                "created_at": BASE_MS + MINUTE,
            },
            {
                "request_id": "m-3",
                "attempts": [_attempt("model-a")],
                "created_at": BASE_MS + 2 * MINUTE,
            },
            {
                "request_id": "m-4",
                "attempts": [_attempt("")],
                "created_at": BASE_MS + 3 * MINUTE,
            },
        ],
    )

    body = await _get(client, headers, view="status")

    assert body["total"] == 4
    labels = [group["label"] for group in body["groups"]]
    assert labels == ["未路由", "model-a", "model-b", "model-a"]


async def test_search_reaches_attempt_members(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "s-1",
                "requested_model": "combo-x",
                "channel_name": "VendorQ",
                "upstream_model": "deepseek-flash",
                "attempts": [
                    _attempt(status="skipped", error_code="CIRCUIT_OPEN", channel="SenseNova"),
                    _attempt("MiniMax-M3.1-Flash", channel="minimaxi"),
                ],
            },
            {
                "request_id": "s-2",
                "requested_model": "model-a",
                "channel_name": "BeiDou",
                "upstream_model": "deepseek-pro",
                "attempts": [_attempt("deepseek-pro", channel="BeiDou")],
            },
        ],
    )

    found = await _get(client, headers, q="minimax")
    assert found["total"] == 1
    assert found["items"][0]["request_id"] == "s-1"

    grouped = await _get(client, headers, q="minimax", view="combo")
    assert grouped["total"] == 1
    assert grouped["groups"][0]["key"] == "combo-x"
    assert grouped["groups"][0]["requests"] == 1

    by_message = await _get(client, headers, q="CIRCUIT_OPEN")
    assert by_message["total"] == 1
    assert by_message["items"][0]["request_id"] == "s-1"


async def test_search_reaches_user_name_and_key_label(client) -> None:
    headers = await _login(client)
    async with client._transport.app.state.session_factory() as session:
        user = await repo.create_user(session, "zhangsan", "张三")
    await _seed(
        client,
        [
            {"request_id": "u-1", "user_id": user.id, "api_key_label": "key-special"},
            {"request_id": "u-2", "api_key_label": "key-plain"},
        ],
    )

    by_name = await _get(client, headers, q="张三")
    assert by_name["total"] == 1
    assert by_name["items"][0]["request_id"] == "u-1"

    by_key = await _get(client, headers, q="key-special")
    assert by_key["total"] == 1
    assert by_key["items"][0]["request_id"] == "u-1"


async def test_aggregate_views_page_by_group(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {"request_id": "p-1", "requested_model": "combo-a", "created_at": BASE_MS},
            {
                "request_id": "p-2",
                "requested_model": "combo-b",
                "created_at": BASE_MS + MINUTE,
            },
            {
                "request_id": "p-3",
                "requested_model": "combo-c",
                "created_at": BASE_MS + 2 * MINUTE,
            },
        ],
    )

    first = await _get(client, headers, view="combo", page_size=2)
    assert first["total"] == 3
    assert len(first["groups"]) == 2

    second = await _get(client, headers, view="combo", page_size=2, page=2)
    assert second["total"] == 3
    assert len(second["groups"]) == 1

    empty = await _get(client, headers, view="combo", page_size=2, page=3)
    assert empty["total"] == 3
    assert empty["groups"] == []


async def test_scan_limit_marks_the_answer_truncated(client, monkeypatch) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {"request_id": "l-1", "requested_model": "combo-a", "created_at": BASE_MS},
            {
                "request_id": "l-2",
                "requested_model": "combo-b",
                "created_at": BASE_MS + MINUTE,
            },
            {
                "request_id": "l-3",
                "requested_model": "combo-c",
                "created_at": BASE_MS + 2 * MINUTE,
            },
        ],
    )

    monkeypatch.setattr(log_views, "MAX_SCAN_ROWS", 2)
    limited = await _get(client, headers, view="combo")
    assert limited["scan_limited"] is True
    assert limited["total"] == 2
    assert {group["key"] for group in limited["groups"]} == {"combo-b", "combo-c"}

    monkeypatch.setattr(log_views, "MAX_SCAN_ROWS", 10)
    full = await _get(client, headers, view="combo")
    assert full["scan_limited"] is False
    assert full["total"] == 3


async def test_group_key_drills_a_group_back_into_rows(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "d-1",
                "requested_model": "combo-a",
                "http_status": 429,
                "attempts": [_attempt(status="error", error_code="rate_limit")],
                "created_at": BASE_MS,
            },
            {
                "request_id": "d-2",
                "requested_model": "combo-a",
                "http_status": 429,
                "attempts": [_attempt(status="error", error_code="rate_limit")],
                "created_at": BASE_MS + MINUTE,
            },
            {
                "request_id": "d-3",
                "requested_model": "combo-b",
                "http_status": 200,
                "attempts": [_attempt()],
                "created_at": BASE_MS + 2 * MINUTE,
            },
        ],
    )

    combo = await _get(client, headers, view="combo")
    combo_key = combo["groups"][0]["key"]
    assert combo_key == "combo-a"
    drilled = await _get(client, headers, group_key=combo_key)
    assert drilled["view"] == "requests"
    assert drilled["total"] == 2
    assert {item["request_id"] for item in drilled["items"]} == {"d-1", "d-2"}

    status_body = await _get(client, headers, view="status")
    segment = next(
        group for group in status_body["groups"] if group["status_code"] == 429
    )
    assert segment["attempts"] == 2
    segment_rows = await _get(client, headers, group_key=segment["key"])
    assert segment_rows["total"] == 2
    assert {item["request_id"] for item in segment_rows["items"]} == {"d-1", "d-2"}

async def test_attempt_status_prefers_the_recorded_http_status(client) -> None:
    """A stored status beats the one its error kind would imply (401 is not 502)."""
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "h-1",
                "http_status": 200,
                "attempts": [
                    _attempt(status="error", error_code="auth", http_status=401)
                ],
                "created_at": BASE_MS,
            },
        ],
    )

    body = await _get(client, headers, view="status")
    segment = body["groups"][0]
    assert segment["status_code"] == 401
    assert segment["status_label"] == "401"
    assert segment["errors"] == 1

    drilled = await _get(client, headers, group_key=segment["key"])
    assert {item["request_id"] for item in drilled["items"]} == {"h-1"}


async def test_attempt_at_dates_the_hop_and_the_key_still_finds_its_rows(
    client,
) -> None:
    """A hop is stamped before its row is written; the drill-down must follow."""
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "t-1",
                "created_at": BASE_MS + MINUTE,
                "attempts": [_attempt(at=BASE_MS, ms=1200)],
            },
            {
                "request_id": "t-2",
                "created_at": BASE_MS + 2 * MINUTE,
                "attempts": [_attempt(at=BASE_MS + 30_000, ms=900)],
            },
        ],
    )

    body = await _get(client, headers, view="status")
    assert len(body["groups"]) == 1
    segment = body["groups"][0]
    assert segment["attempts"] == 2
    assert segment["first_at"] == BASE_MS
    assert segment["last_at"] == BASE_MS + 30_000

    drilled = await _get(client, headers, group_key=segment["key"])
    assert {item["request_id"] for item in drilled["items"]} == {"t-1", "t-2"}


async def test_search_matches_the_middle_of_an_attempt_field(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "m-1",
                "attempts": [_attempt("MiniMax-M3.1-Flash", channel="minimaxi")],
            },
            {
                "request_id": "m-2",
                "attempts": [_attempt("deepseek-pro", channel="BeiDou")],
            },
        ],
    )

    for query in ("imax", "inimaxi"):
        found = await _get(client, headers, q=query)
        assert found["total"] == 1, query
        assert found["items"][0]["request_id"] == "m-1"


async def test_numeric_status_drill_matches_the_typed_value_only(client) -> None:
    """数值等值必须类型化：502 的钻取不得命中 5020（子串匹配会）。"""
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "n-1",
                "http_status": 200,
                "attempts": [_attempt(status="error", http_status=502)],
                "created_at": BASE_MS,
            },
            {
                "request_id": "n-2",
                "http_status": 200,
                "attempts": [_attempt(status="error", http_status=5020)],
                "created_at": BASE_MS + MINUTE,
            },
        ],
    )
    async with client._transport.app.state.session_factory() as session:
        rows, total = await repo.list_logs_for_segment(session, status_code=502)
    assert total == 1
    assert [row.request_id for row in rows] == ["n-1"]


async def test_error_kind_drill_matches_both_json_separators(client) -> None:
    """error_code 钻取同时命中默认与紧凑分隔符写法。"""
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "s-1",
                "http_status": 500,
                "attempts": [_attempt(status="error", error_code="rate_limit")],
                "created_at": BASE_MS,
            },
        ],
    )
    async with client._transport.app.state.session_factory() as session:
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="s-2",
                requested_model="deepseek-flash",
                http_status=500,
                # 紧凑分隔符（无空格）是 json.dumps 的另一种常见输出。
                attempts_json=json.dumps(
                    [_attempt(status="error", error_code="rate_limit")],
                    separators=(",", ":"),
                ),
                created_at=BASE_MS + MINUTE,
            )
        )
        await session.commit()
        rows, total = await repo.list_logs_for_segment(
            session, error_kinds=["rate_limit"]
        )
    assert total == 2
    assert {row.request_id for row in rows} == {"s-1", "s-2"}


async def test_search_survives_a_malformed_attempts_blob(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "b-1",
                "attempts": [_attempt("deepseek-flash", channel="BeiDou")],
            },
        ],
    )
    async with client._transport.app.state.session_factory() as session:
        session.add(
            orm.RequestLogORM(
                id=str(uuid.uuid4()),
                request_id="b-broken",
                requested_model="model-a",
                http_status=500,
                attempts_json="not-json{",
                created_at=BASE_MS + 5 * MINUTE,
            )
        )
        await session.commit()

    found = await _get(client, headers, q="eidou")
    assert found["total"] == 1
    assert found["items"][0]["request_id"] == "b-1"

    view = await _get(client, headers, view="status")
    assert view["total"] == 1
    assert view["groups"][0]["requests"] == 1

def test_attempt_records_when_and_what_the_upstream_answered() -> None:
    """The writer stamps the hop; the reader prefers that stamp over guessing."""
    from l05_service.chat import _attempt as record_attempt

    attempt = record_attempt(
        None, "error", error_code="rate_limit", at=BASE_MS, http_status=429
    )
    assert attempt["at"] == BASE_MS
    assert attempt["http_status"] == 429
    # A 429 recorded by the upstream wins over the one the kind implies only
    # in ordering, never in value; a stored 401 is where it shows.
    assert log_views.attempt_status(200, attempt) == (429, "429")
    assert log_views.attempt_status(
        200, {"status": "error", "error_code": "auth", "http_status": 401}
    ) == (401, "401")

    # Both fields stay absent when the caller has nothing to say, so a skipped
    # or a pre-upgrade row never pretends to know them.
    plain = record_attempt(None, "skipped", error_code="CIRCUIT_OPEN", skipped=True)
    assert "at" not in plain
    assert "http_status" not in plain


async def test_historical_attempts_without_the_timeline_fields_still_work(
    client,
) -> None:
    """Rows written before the fields existed keep the kind-based inference."""
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "o-1",
                "http_status": 429,
                "attempts": [_attempt(status="error", error_code="rate_limit")],
                "created_at": BASE_MS,
            },
        ],
    )

    body = await _get(client, headers, view="status")
    segment = body["groups"][0]
    assert segment["status_code"] == 429
    assert segment["first_at"] == BASE_MS
    assert segment["last_at"] == BASE_MS
    drilled = await _get(client, headers, group_key=segment["key"])
    assert {item["request_id"] for item in drilled["items"]} == {"o-1"}


async def test_search_matches_an_error_message_substring(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "request_id": "x-1",
                "http_status": 502,
                "attempts": [
                    _attempt(
                        status="error",
                        error_code="upstream",
                        message="upstream connection reset by peer",
                    )
                ],
            },
            {"request_id": "x-2", "attempts": [_attempt("deepseek-pro", channel="BeiDou")]},
        ],
    )

    found = await _get(client, headers, q="connection reset")
    assert found["total"] == 1
    assert found["items"][0]["request_id"] == "x-1"
