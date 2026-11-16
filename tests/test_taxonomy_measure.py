"""Measure upstream attempts per request for the error taxonomy.

Self-contained on purpose: it does not import the shared fake upstream helper,
so the same file runs against both the patched tree and a pristine HEAD tree.

    pytest tests/test_taxonomy_measure.py -q -s
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from l01_core.config import Settings
from l05_service.llm import gateway

N_REQUESTS = 100


class _Upstream:
    def __init__(self) -> None:
        self.hits: list[float] = []
        self.lock = threading.Lock()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path.startswith("/v1/models"):
                    self._send(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {"id": "fake-model", "object": "model", "owned_by": "test"}
                            ],
                        },
                    )
                    return
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                with outer.lock:
                    outer.hits.append(time.monotonic())
                if self.path.startswith("/quota"):
                    self._send(
                        429,
                        {
                            "error": {
                                "message": "You exceeded your current quota, please check your plan and billing details.",
                                "type": "insufficient_quota",
                                "code": "insufficient_quota",
                            }
                        },
                        {"Retry-After": "5"},
                    )
                    return
                if self.path.startswith("/ratelimit"):
                    self._send(
                        429,
                        {
                            "error": {
                                "message": "Rate limit reached for requests",
                                "type": "requests",
                                "code": "rate_limit_exceeded",
                            }
                        },
                        {"Retry-After": "5"},
                    )
                    return
                if self.path.startswith("/auth"):
                    self._send(
                        401,
                        {
                            "error": {
                                "message": "Invalid API key provided",
                                "type": "invalid_request_error",
                                "code": "invalid_api_key",
                            }
                        },
                    )
                    return
                if self.path.startswith("/boom"):
                    self._send(
                        500,
                        {
                            "error": {
                                "message": "internal upstream failure",
                                "type": "server_error",
                                "code": "server_error",
                            }
                        },
                    )
                    return
                self._send(
                    200,
                    {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion",
                        "model": "fake-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "OK"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                    },
                )

            def _send(
                self,
                status: int,
                payload: dict[str, Any],
                headers: dict[str, str] | None = None,
            ) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "_Upstream":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def count(self) -> int:
        with self.lock:
            return len(self.hits)


@pytest.fixture
def upstream() -> Iterator[_Upstream]:
    server = _Upstream().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
        jwt_expire_minutes=60,
        admin_username="admin",
        admin_password="admin",
        upstream_transport="httpx",
    )


@pytest.fixture(autouse=True)
def _httpx_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "_use_litellm", lambda *args, **kwargs: False)


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def _channel(client, headers, url: str, *, name: str) -> dict[str, Any]:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "name": name,
            "connection_url": url,
            "api_key": "channel-secret-key",
            "protocol": "openai_chat",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, *, call_model: str, priority: int) -> None:
    response = await client.post(
        "/api/channels/" + channel_id + "/models",
        headers=headers,
        json={
            "upstream_model": "fake-model",
            "call_model": call_model,
            "priority": priority,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text


async def _api_key(client, headers, label: str) -> str:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return float(ordered[index])


async def _measure(
    client,
    upstream: _Upstream,
    *,
    label: str,
    failure_path: str,
    channels: int,
    n: int = N_REQUESTS,
) -> dict[str, Any]:
    headers = await _token(client)
    key = await _api_key(client, headers, label)
    call_model = "bench-" + label
    for index in range(channels):
        channel = await _channel(
            client, headers, upstream.base_url + failure_path, name=f"{label}-{index}"
        )
        await _model(client, headers, channel["id"], call_model=call_model, priority=index + 1)

    attempts: list[int] = []
    statuses: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    retry_after: Counter[str] = Counter()
    started = time.monotonic()
    for _ in range(n):
        before = upstream.count()
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={"model": call_model, "messages": [{"role": "user", "content": "hello"}]},
        )
        attempts.append(upstream.count() - before)
        statuses[str(response.status_code)] += 1
        retry_after[response.headers.get("retry-after") or "-"] += 1
        try:
            body = response.json()
        except Exception:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            codes[str(error.get("code"))] += 1
        else:
            codes["<none>"] += 1
    elapsed = time.monotonic() - started

    return {
        "label": label,
        "failure_path": failure_path,
        "channels": channels,
        "requests": n,
        "upstream_hits": sum(attempts),
        "attempts_histogram": dict(sorted(Counter(attempts).items())),
        "attempts_p50": _percentile(attempts, 0.5),
        "attempts_p95": _percentile(attempts, 0.95),
        "attempts_max": max(attempts) if attempts else 0,
        "attempts_mean": round(statistics.fmean(attempts), 3) if attempts else 0.0,
        "statuses": dict(statuses),
        "error_codes": dict(codes),
        "retry_after_headers": dict(retry_after),
        "elapsed_s": round(elapsed, 2),
        "hits_per_second": round(sum(attempts) / elapsed, 2) if elapsed else 0.0,
    }


async def test_measure_quota_429_four_channels(client, upstream) -> None:
    result = await _measure(
        client, upstream, label="quota", failure_path="/quota", channels=4
    )
    print("MEASURE " + json.dumps(result, ensure_ascii=False))


async def test_measure_ratelimit_429_one_channel(client, upstream) -> None:
    result = await _measure(
        client, upstream, label="ratelimit", failure_path="/ratelimit", channels=1
    )
    print("MEASURE " + json.dumps(result, ensure_ascii=False))


async def test_measure_auth_401_two_channels(client, upstream) -> None:
    result = await _measure(
        client, upstream, label="auth", failure_path="/auth", channels=2
    )
    print("MEASURE " + json.dumps(result, ensure_ascii=False))


async def test_measure_server_error_500_one_channel(client, upstream) -> None:
    result = await _measure(
        client, upstream, label="boom", failure_path="/boom", channels=1
    )
    print("MEASURE " + json.dumps(result, ensure_ascii=False))
