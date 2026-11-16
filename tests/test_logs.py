from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator

import pytest

from l01_core.timeutil import now_ms
from l04_repo import repo
from l05_service.body_logging import render_value
from tests.fake_upstream_server import FakeUpstreamServer

BASE_MS = 1_762_000_000_000
MINUTE = 60_000


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


async def _channel(client, headers, url: str, **overrides) -> dict:
    payload = {
        "name": "日志渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, **overrides) -> dict:
    payload = {"upstream_model": "fake-model", "call_model": "gpt-test", **overrides}
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers, label: str = "ci") -> dict:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


async def _users(app) -> tuple[str, str]:
    async with app.state.session_factory() as session:
        alice = await repo.create_user(session, "alice", "Alice")
        bob = await repo.create_user(session, "bob", "Bob")
        return alice.id, bob.id


def _log_values(index: int, user_a: str, user_b: str, **overrides) -> dict:
    values = {
        "request_id": f"req-{index:04d}",
        "upstream_request_id": f"chatcmpl-{index:04d}",
        "user_id": user_a if index % 2 == 0 else user_b,
        "api_key_label": "key-a" if index % 2 == 0 else "key-b",
        "requested_model": "combo-a" if index % 3 == 0 else "model-a",
        "channel_name": "channel-a" if index % 2 == 0 else "channel-b",
        "upstream_model": "upstream-a",
        "is_stream": index % 4 == 0,
        "http_status": 500 if index % 5 == 0 else 200,
        "created_at": BASE_MS + index * MINUTE,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cache_ratio": 0.25,
        "cost_usd": 0.001,
        "attempts_json": json.dumps(
            [
                {
                    "channel_slug": "channel-a",
                    "upstream_model": "upstream-a",
                    "status": "success" if index % 5 else "error",
                    "ms": 12,
                    "round": 1,
                    "priority_path": [1],
                }
            ]
        ),
        "params_json": json.dumps({"temperature": 0.2, "stream": False}),
        "response_headers_json": json.dumps({"content-type": "application/json"}),
    }
    values.update(overrides)
    return values


async def _seed(app, values: dict) -> None:
    async with app.state.session_factory() as session:
        await repo.add_request_log(session, **values)


async def _seed_many(
    app, count: int = 25, *, created_at_base: int | None = None
) -> tuple[list[dict], str, str]:
    user_a, user_b = await _users(app)
    values = [
        _log_values(
            index,
            user_a,
            user_b,
            **(
                {"created_at": created_at_base + index * MINUTE}
                if created_at_base is not None
                else {}
            ),
        )
        for index in range(count)
    ]
    for item in values:
        await _seed(app, item)
    return values, user_a, user_b


def _matches(item: dict, **filters) -> bool:
    channel = filters.get("channel")
    model = filters.get("model")
    status = filters.get("status")
    user = filters.get("user")
    key_label = filters.get("key_label")
    stream = filters.get("stream")
    start_ms = filters.get("start_ms")
    end_ms = filters.get("end_ms")
    attempts = json.loads(item["attempts_json"]) if item.get("attempts_json") else []
    if channel:
        channels = {item["channel_name"]}
        for attempt in attempts:
            if isinstance(attempt, dict):
                channels.add(attempt.get("channel_name"))
                channels.add(attempt.get("channel_slug"))
        if channel not in channels:
            return False
    if model and item["requested_model"] != model:
        return False
    if status == "success" and item["http_status"] >= 400:
        return False
    if status == "error" and item["http_status"] < 400:
        return False
    if user and item["user_id"] != user:
        return False
    if key_label and item["api_key_label"] != key_label:
        return False
    if stream == "stream" and not item["is_stream"]:
        return False
    if stream == "json" and item["is_stream"]:
        return False
    if start_ms is not None and item["created_at"] < start_ms:
        return False
    if end_ms is not None and item["created_at"] >= end_ms:
        return False
    return True


async def test_logs_filter_combinations_and_pagination(client, app):
    headers = await _token(client)
    values, user_a, _user_b = await _seed_many(app)

    cases = [
        {"channel": "channel-a"},
        {"model": "combo-a"},
        {"status": "error"},
        {"status": "success"},
        {"user": user_a},
        {"key_label": "key-b"},
        {"stream": "stream"},
        {"stream": "json"},
        {"start_ms": BASE_MS + 5 * MINUTE, "end_ms": BASE_MS + 10 * MINUTE},
        {
            "channel": "channel-a",
            "model": "model-a",
            "status": "success",
            "key_label": "key-a",
            "stream": "json",
        },
    ]
    for case in cases:
        expected = [item for item in values if _matches(item, **case)]
        response = await client.get("/api/logs", headers=headers, params=case)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(expected), case
        assert {item["request_id"] for item in body["items"]} == {
            item["request_id"] for item in expected
        }

    first = (
        await client.get("/api/logs", headers=headers, params={"page": 1, "page_size": 10})
    ).json()
    assert len(first["items"]) == 10
    assert first["total"] == 25
    assert first["page"] == 1
    assert first["page_size"] == 10
    assert [item["created_at"] for item in first["items"]] == sorted(
        [item["created_at"] for item in first["items"]], reverse=True
    )

    third = (
        await client.get("/api/logs", headers=headers, params={"page": 3, "page_size": 10})
    ).json()
    assert len(third["items"]) == 5
    assert third["items"][-1]["request_id"] == "req-0000"

    empty = (
        await client.get("/api/logs", headers=headers, params={"page": 4, "page_size": 10})
    ).json()
    assert empty["items"] == []
    assert empty["total"] == 25


async def test_logs_page_size_limit_is_200(client):
    headers = await _token(client)

    allowed = await client.get("/api/logs", headers=headers, params={"page_size": 200})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["page_size"] == 200

    too_big = await client.get("/api/logs", headers=headers, params={"page_size": 201})
    assert too_big.status_code == 422, too_big.text

    recent = await client.get("/api/logs/recent", headers=headers, params={"limit": 200})
    assert recent.status_code == 200, recent.text

    recent_too_big = await client.get(
        "/api/logs/recent", headers=headers, params={"limit": 201}
    )
    assert recent_too_big.status_code == 422, recent_too_big.text


async def test_logs_search_matches_gateway_or_upstream_id(client, app):
    headers = await _token(client)
    user_a, user_b = await _users(app)
    await _seed(
        app,
        _log_values(
            0,
            user_a,
            user_b,
            request_id="gw-alpha-001",
            upstream_request_id="chatcmpl-abc",
        ),
    )
    await _seed(
        app,
        _log_values(
            1,
            user_a,
            user_b,
            request_id="gw-beta-002",
            upstream_request_id="chatcmpl-zzz",
        ),
    )

    by_gateway = (
        await client.get("/api/logs", headers=headers, params={"q": "gw-alpha"})
    ).json()
    assert by_gateway["total"] == 1
    assert by_gateway["items"][0]["upstream_request_id"] == "chatcmpl-abc"

    by_upstream = (
        await client.get("/api/logs", headers=headers, params={"q": "chatcmpl-abc"})
    ).json()
    assert by_upstream["total"] == 1
    assert by_upstream["items"][0]["request_id"] == "gw-alpha-001"

    both = (
        await client.get("/api/logs", headers=headers, params={"q": "chatcmpl"})
    ).json()

    assert both["total"] == 2


async def test_logs_search_treats_like_wildcards_literally(client, app):
    """A literal % or _ in q must match as text, not as a LIKE wildcard."""
    headers = await _token(client)
    user_a, user_b = await _users(app)
    for index, request_id in enumerate(
        ["gw-pct-100%", "gw-underscore_a", "gw-plain-300"]
    ):
        await _seed(
            app,
            _log_values(index, user_a, user_b, request_id=request_id),
        )

    percent = (await client.get("/api/logs", headers=headers, params={"q": "%"})).json()
    assert percent["total"] == 1
    assert percent["items"][0]["request_id"] == "gw-pct-100%"

    underscore = (
        await client.get("/api/logs", headers=headers, params={"q": "_"})
    ).json()
    assert underscore["total"] == 1
    assert underscore["items"][0]["request_id"] == "gw-underscore_a"

    suffix = (
        await client.get("/api/logs", headers=headers, params={"q": "100%"})
    ).json()
    assert suffix["total"] == 1
    assert suffix["items"][0]["request_id"] == "gw-pct-100%"


async def test_log_detail_by_upstream_id_includes_params_headers_and_redacted_error(client, app):
    headers = await _token(client)
    user_a, user_b = await _users(app)
    values = _log_values(
        7,
        user_a,
        user_b,
        request_id="gw-error-007",
        upstream_request_id="chatcmpl-error",
        http_status=502,
        error_code="UPSTREAM_ERROR",
        error_message="upstream failed",
    )
    await _seed(app, values)
    redacted = render_value({"api_key": "sk-live-should-not-leak", "detail": "boom"}, 16384)
    assert redacted is not None and "sk-live-should-not-leak" not in redacted
    async with app.state.session_factory() as session:
        await repo.save_request_body(session, request_id="gw-error-007", response_body=redacted)

    for identifier in ("gw-error-007", "chatcmpl-error"):
        response = await client.get(f"/api/logs/{identifier}", headers=headers)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["request_id"] == "gw-error-007"
        assert detail["upstream_request_id"] == "chatcmpl-error"
        assert detail["params"]["temperature"] == 0.2
        assert detail["response_headers"]["content-type"] == "application/json"
        assert detail["error_body"]
        assert "sk-live-should-not-leak" not in detail["error_body"]
        assert "***" in detail["error_body"]
        assert detail["attempts"][0]["round"] == 1

    missing = await client.get("/api/logs/does-not-exist", headers=headers)
    assert missing.status_code == 404


async def test_logs_filter_options_are_distinct(client, app):
    headers = await _token(client)
    # The model list is built from the catalog plus the last 30 days, so this
    # fixture has to land inside that window; the other lists read all time.
    values, _user_a, _user_b = await _seed_many(
        app, count=6, created_at_base=now_ms() - 60 * MINUTE
    )

    response = await client.get("/api/logs/filters", headers=headers)
    assert response.status_code == 200, response.text
    options = response.json()
    assert options["channels"] == sorted({item["channel_name"] for item in values})
    # The list offers what a model filter would actually match: the requested
    # name (a direct model or a combo) and the upstream model that served it.
    assert options["models"] == sorted(
        {item["requested_model"] for item in values}
        | {item["upstream_model"] for item in values}
    )
    assert options["key_labels"] == ["key-a", "key-b"]
    assert [user["name"] for user in options["users"]] == ["Alice", "Bob"]
    assert options["truncated"] == {
        "channels": False,
        "models": False,
        "key_labels": False,
        "users": False,
    }


async def test_filter_options_cap_at_fifty_and_report_truncated(client, app):
    headers = await _token(client)
    async with app.state.session_factory() as session:
        for index in range(55):
            user = await repo.create_user(session, f"user-{index:03d}", f"User {index:03d}")
            await repo.add_request_log(
                session,
                **_log_values(
                    index,
                    user.id,
                    user.id,
                    channel_name=f"channel-{index:03d}",
                    requested_model=f"model-{index:03d}",
                    api_key_label=f"key-{index:03d}",
                    created_at=now_ms() - index * MINUTE,
                ),
            )

    options = (await client.get("/api/logs/filters", headers=headers)).json()
    assert len(options["channels"]) == 50
    assert len(options["models"]) == 50
    assert len(options["key_labels"]) == 50
    assert len(options["users"]) == 50
    assert options["channels"][0] == "channel-000"
    assert options["channels"][-1] == "channel-049"
    assert options["truncated"] == {
        "channels": True,
        "models": True,
        "key_labels": True,
        "users": True,
    }


async def test_filter_options_search_narrows_values(client, app):
    headers = await _token(client)
    values, _user_a, _user_b = await _seed_many(app, count=6)

    response = await client.get("/api/logs/filters", headers=headers, params={"q": "channel-b"})
    assert response.status_code == 200, response.text
    options = response.json()
    assert options["channels"] == ["channel-b"]
    assert options["models"] == []
    assert options["key_labels"] == []
    assert options["users"] == []
    assert not any(options["truncated"].values())

    by_user = (
        await client.get("/api/logs/filters", headers=headers, params={"q": "ali"})
    ).json()
    assert [user["name"] for user in by_user["users"]] == ["Alice"]
    assert by_user["channels"] == []
    assert by_user["models"] == []


async def test_chat_log_records_upstream_id_headers_and_params(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers, label="logs")

    chat = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.2,
            "max_tokens": 64,
        },
    )
    assert chat.status_code == 200, chat.text
    gateway_id = chat.headers["X-Request-ID"]

    found = (
        await client.get(
            "/api/logs", headers=headers, params={"q": "chatcmpl-fake"}
        )
    ).json()
    assert found["total"] == 1
    assert found["items"][0]["request_id"] == gateway_id

    detail = (await client.get(f"/api/logs/{gateway_id}", headers=headers)).json()
    assert detail["upstream_request_id"] == "chatcmpl-fake"
    assert detail["response_headers"]["content-type"].startswith("application/json")
    assert detail["params"]["temperature"] == 0.2
    assert detail["params"]["max_tokens"] == 64
    assert detail["params"]["message_count"] == 1
    assert detail["params"]["stream"] is False


async def _wait_for_log(client, headers, request_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = (await client.get("/api/logs/recent?limit=20", headers=headers)).json()
        for item in logs["items"]:
            if item["request_id"] == request_id:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(f"未找到请求 {request_id} 的日志")


async def test_stream_chat_log_records_upstream_id_and_params(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers, label="stream-logs")

    chat = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "temperature": 0.3,
        },
    )
    assert chat.status_code == 200, chat.text
    gateway_id = chat.headers["X-Request-ID"]
    logged = await _wait_for_log(client, headers, gateway_id)
    assert logged["upstream_request_id"] == "chatcmpl-stream"

    found = (
        await client.get(
            "/api/logs", headers=headers, params={"q": "chatcmpl-stream"}
        )
    ).json()
    assert found["total"] == 1
    assert found["items"][0]["request_id"] == gateway_id

    detail = (await client.get("/api/logs/chatcmpl-stream", headers=headers)).json()
    assert detail["request_id"] == gateway_id
    assert detail["is_stream"] is True
    assert detail["params"]["temperature"] == 0.3
    assert detail["params"]["stream"] is True


async def test_logs_require_admin_session(client):
    assert (await client.get("/api/logs")).status_code == 401
    assert (await client.get("/api/logs/filters")).status_code == 401
    assert (await client.get("/api/logs/anything")).status_code == 401




async def test_logs_attempt_status_finds_failover_rows(client, app):
    headers = await _token(client)
    user_a, user_b = await _users(app)
    failed = {
        "channel_slug": "fail-slug",
        "channel_name": "失败渠道",
        "upstream_model": "upstream-fail",
        "status": "error",
        "error_code": "UPSTREAM_ERROR",
        "ms": 8,
        "round": 1,
        "priority_path": [1],
    }
    succeeded = {
        "channel_slug": "channel-a",
        "channel_name": "channel-a",
        "upstream_model": "upstream-a",
        "status": "success",
        "ms": 12,
        "round": 2,
        "priority_path": [1, 2],
    }
    await _seed(
        app,
        _log_values(
            10,
            user_a,
            user_b,
            request_id="req-failover",
            http_status=200,
            attempts_json=json.dumps([failed, succeeded], ensure_ascii=False),
        ),
    )
    await _seed(
        app,
        _log_values(
            11,
            user_a,
            user_b,
            request_id="req-clean",
            http_status=200,
            attempts_json=json.dumps([succeeded], ensure_ascii=False),
        ),
    )
    await _seed(
        app,
        _log_values(
            12,
            user_a,
            user_b,
            request_id="req-hard-error",
            http_status=502,
            attempts_json=json.dumps([failed], ensure_ascii=False),
        ),
    )

    errored = (
        await client.get("/api/logs", headers=headers, params={"attempt_status": "error"})
    ).json()
    assert errored["total"] == 2
    assert {item["request_id"] for item in errored["items"]} == {
        "req-failover",
        "req-hard-error",
    }
    assert {item["request_id"]: item["attempt_error_count"] for item in errored["items"]} == {
        "req-failover": 1,
        "req-hard-error": 1,
    }

    failover_only = (
        await client.get(
            "/api/logs",
            headers=headers,
            params={"attempt_status": "error", "status": "success"},
        )
    ).json()
    assert failover_only["total"] == 1
    assert failover_only["items"][0]["request_id"] == "req-failover"

    clean = (
        await client.get("/api/logs", headers=headers, params={"attempt_status": "success"})
    ).json()
    assert clean["total"] == 1
    assert clean["items"][0]["request_id"] == "req-clean"
    assert clean["items"][0]["attempt_error_count"] == 0

    everything = (await client.get("/api/logs", headers=headers)).json()
    assert everything["total"] == 3


async def test_logs_channel_filter_matches_failed_attempts(client, app):
    headers = await _token(client)
    user_a, user_b = await _users(app)
    failed = {
        "channel_slug": "fail-slug",
        "channel_name": "失败渠道",
        "upstream_model": "upstream-fail",
        "status": "error",
        "ms": 8,
        "round": 1,
        "priority_path": [1],
    }
    succeeded = {
        "channel_slug": "channel-a",
        "channel_name": "channel-a",
        "upstream_model": "upstream-a",
        "status": "success",
        "ms": 12,
        "round": 2,
        "priority_path": [1, 2],
    }
    await _seed(
        app,
        _log_values(
            20,
            user_a,
            user_b,
            request_id="req-via-fail",
            channel_name="final-channel",
            attempts_json=json.dumps([failed, succeeded], ensure_ascii=False),
        ),
    )
    await _seed(
        app,
        _log_values(
            21,
            user_a,
            user_b,
            request_id="req-plain",
            channel_name="plain-channel",
            attempts_json=json.dumps([succeeded], ensure_ascii=False),
        ),
    )

    by_display_name = (
        await client.get("/api/logs", headers=headers, params={"channel": "失败渠道"})
    ).json()
    assert by_display_name["total"] == 1
    assert by_display_name["items"][0]["request_id"] == "req-via-fail"

    by_slug = (
        await client.get("/api/logs", headers=headers, params={"channel": "fail-slug"})
    ).json()
    assert by_slug["total"] == 1
    assert by_slug["items"][0]["request_id"] == "req-via-fail"

    by_final_channel = (
        await client.get("/api/logs", headers=headers, params={"channel": "final-channel"})
    ).json()
    assert by_final_channel["total"] == 1
    assert by_final_channel["items"][0]["request_id"] == "req-via-fail"

    literal_wildcard = (
        await client.get("/api/logs", headers=headers, params={"channel": "fail%"})
    ).json()
    assert literal_wildcard["total"] == 0


async def test_logs_attempt_error_count_only_counts_errors(client, app):
    headers = await _token(client)
    user_a, user_b = await _users(app)
    mixed_attempts = [
        {"channel_slug": "channel-a", "status": "error", "ms": 5, "round": 1},
        {"channel_slug": "channel-b", "status": "skipped", "skipped": True, "round": 2},
        {"channel_slug": "channel-c", "status": "success", "ms": 9, "round": 3},
        {"channel_slug": "channel-d", "status": "error", "ms": 7, "round": 4},
    ]
    single_success = [{"channel_slug": "channel-a", "status": "success", "ms": 10, "round": 1}]
    await _seed(
        app,
        _log_values(
            30,
            user_a,
            user_b,
            request_id="req-mixed",
            http_status=200,
            attempts_json=json.dumps(mixed_attempts, ensure_ascii=False),
        ),
    )
    await _seed(
        app,
        _log_values(
            31,
            user_a,
            user_b,
            request_id="req-empty",
            attempts_json="[]",
        ),
    )
    await _seed(
        app,
        _log_values(
            32,
            user_a,
            user_b,
            request_id="req-single-ok",
            attempts_json=json.dumps(single_success, ensure_ascii=False),
        ),
    )

    mixed = (
        await client.get("/api/logs", headers=headers, params={"q": "req-mixed"})
    ).json()
    assert mixed["items"][0]["attempt_error_count"] == 2

    empty = (
        await client.get("/api/logs", headers=headers, params={"q": "req-empty"})
    ).json()
    assert empty["items"][0]["attempt_error_count"] == 0

    errored = (
        await client.get("/api/logs", headers=headers, params={"attempt_status": "error"})
    ).json()
    assert errored["total"] == 1
    assert errored["items"][0]["request_id"] == "req-mixed"

    clean = (
        await client.get("/api/logs", headers=headers, params={"attempt_status": "success"})
    ).json()
    assert clean["total"] == 1
    assert clean["items"][0]["request_id"] == "req-single-ok"
