from __future__ import annotations

import json
import logging

import pytest

from l01_core.logging import redact, redact_headers, redact_text
from l04_repo import repo
from l05_service.app_settings import BodyLogSettings
from l05_service.body_logging import (
    BodySnapshot,
    REASONING_MAX_BYTES,
    build_body_record,
    extract_reasoning_content,
    should_capture,
    strip_reasoning_content,
    truncate_text,
    write_body_log,
)


def test_redact_masks_credentials_everywhere_without_touching_safe_values():
    payload = {
        "api_key": "BODY_SECRET",
        "Authorization": "Bearer HEADER_SECRET",
        "nested": {
            "x-api-key": "X_SECRET",
            "token": "TOKEN_SECRET",
            "refresh_token": "REFRESH_SECRET",
            "keep": "visible",
        },
        "headers": {
            "Authorization": "Bearer HEADER_SECRET",
            "Cookie": "session=COOKIE_SECRET",
            "Set-Cookie": "session=SET_COOKIE_SECRET",
            "Content-Type": "application/json",
        },
        "url": "https://example.test/callback?key=QUERY_KEY_SECRET&api_key=QUERY_API_SECRET"
        "&token=QUERY_TOKEN_SECRET&keep=1",
        "messages": [{"role": "user", "content": "hello"}],
    }

    rendered = json.dumps(redact(payload), ensure_ascii=False)

    for secret in (
        "BODY_SECRET",
        "HEADER_SECRET",
        "X_SECRET",
        "TOKEN_SECRET",
        "REFRESH_SECRET",
        "COOKIE_SECRET",
        "SET_COOKIE_SECRET",
        "QUERY_KEY_SECRET",
        "QUERY_API_SECRET",
        "QUERY_TOKEN_SECRET",
    ):
        assert secret not in rendered
    assert "visible" in rendered
    assert "keep=1" in rendered
    assert "application/json" in rendered


def test_redact_headers_is_case_insensitive():
    headers = redact_headers(
        {
            "authorization": "Bearer A",
            "API-KEY": "B",
            "X-Api-Key": "C",
            "cookie": "D",
            "SET-COOKIE": "E",
            "accept": "application/json",
        }
    )

    assert headers == {
        "authorization": "***",
        "API-KEY": "***",
        "X-Api-Key": "***",
        "cookie": "***",
        "SET-COOKIE": "***",
        "accept": "application/json",
    }


def test_redact_text_masks_header_lines_and_query_parameters():
    text = (
        "Authorization: Bearer SECRET_HEADER\n"
        "x-api-key: SECRET_X\n"
        "{\"refresh_token\": \"SECRET_JSON_REFRESH\"}\n"
        "visit https://example.test/x?token=SECRET_TOKEN&api_key=SECRET_API"
        "&refresh_token=SECRET_REFRESH&keep=1"
    )

    rendered = redact_text(text)

    assert "SECRET_HEADER" not in rendered
    assert "SECRET_X" not in rendered
    assert "SECRET_TOKEN" not in rendered
    assert "SECRET_API" not in rendered
    assert "SECRET_JSON_REFRESH" not in rendered
    assert "SECRET_REFRESH" not in rendered
    assert "keep=1" in rendered


def test_truncate_text_respects_utf8_budget_without_splitting_characters():
    result = truncate_text("汉" * 10_000, 16 * 1024)

    assert len(result.encode("utf-8")) <= 16 * 1024
    assert result.endswith("[truncated]")
    assert result.startswith("汉")


def test_body_record_keeps_reasoning_in_its_own_16k_budget():
    payload = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "c" * 40_000,
                    "reasoning_content": "r" * 40_000,
                },
                "finish_reason": "stop",
            }
        ]
    }
    settings = BodyLogSettings(mode="all")
    snapshot = BodySnapshot(
        request_body={"messages": [{"role": "user", "content": "b" * 40_000}]},
        upstream_body={"messages": [{"role": "user", "content": "u" * 40_000}]},
        response_body=strip_reasoning_content(payload),
        reasoning_content=extract_reasoning_content(payload),
    )

    record = build_body_record(snapshot, settings)

    assert record["request_body"].endswith("[truncated]")
    assert record["upstream_body"].endswith("[truncated]")
    assert len(record["request_body"].encode("utf-8")) <= 16 * 1024
    assert len(record["upstream_body"].encode("utf-8")) <= 16 * 1024
    assert len(record["response_body"].encode("utf-8")) <= 16 * 1024
    assert "r" * 100 not in record["response_body"]
    assert len(record["reasoning_content"].encode("utf-8")) <= REASONING_MAX_BYTES
    assert record["reasoning_content"].startswith("r" * 100)
    assert "c" * 100 not in record["reasoning_content"]
    # The caller's payload must stay intact for the actual response.
    assert payload["choices"][0]["message"]["reasoning_content"] == "r" * 40_000


def test_stream_summary_is_capped_at_the_configured_64k_budget():
    snapshot = BodySnapshot(
        response_summary={
            "delta_count": 1,
            "first_preview": "a" * 100_000,
            "last_preview": "b" * 100_000,
            "finish_reason": "stop",
            "saw_done": True,
            "error_events": [],
        }
    )

    record = build_body_record(snapshot, BodyLogSettings(mode="all"))

    assert len(record["response_summary"].encode("utf-8")) <= 64 * 1024
    assert record["response_summary"].endswith("[truncated]")


@pytest.mark.parametrize(
    ("mode", "status", "error_code", "expected"),
    [
        ("off", 500, "UPSTREAM_ERROR", False),
        ("off", 200, None, False),
        ("errors", 400, None, True),
        ("errors", 500, "UPSTREAM_ERROR", True),
        ("errors", 502, "EMPTY_RESPONSE", True),
        ("errors", 200, "STREAM_ABORTED", True),
        ("errors", 200, None, False),
        ("all", 200, None, True),
        ("all", 500, "UPSTREAM_ERROR", True),
    ],
)
def test_should_capture_policy_matrix(mode, status, error_code, expected):
    assert (
        should_capture(
            BodyLogSettings(mode=mode), http_status=status, error_code=error_code
        )
        is expected
    )


async def test_body_write_failure_only_logs_a_warning(
    app, monkeypatch, caplog
):
    async def failing_save(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repo, "save_request_body", failing_save)
    caplog.set_level(logging.WARNING)

    stored = await write_body_log(
        app.state.session_factory,
        "req-write-failure",
        BodySnapshot(request_body={"api_key": "SECRET"}),
        http_status=200,
        settings=BodyLogSettings(mode="all"),
    )

    assert stored is False
    assert "request body log write failed" in caplog.text
    assert "req-write-failure" in caplog.text
