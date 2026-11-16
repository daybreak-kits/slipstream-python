"""可执行的稳定性不变量（规格：design/PIPELINE_OPTIMIZATION.md 第 3 节，共 16 条）。

约定：
- 函数名带编号（test_inv01_...），一条不变量至少一个可执行断言。
- 今天成立的用普通断言钉住；已确认的违例用 pytest.mark.xfail(strict=False) 记录。
- 只读产品行为：不修改任何产品代码。
- 无法单测的条目（背压槽位、降级顺序、lossy 登记、96 KB 上限、空帧语义）不写测试，
  在 runner/STABILITY_INVARIANTS.md 里写「未实现/不可测」两栏。
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from l01_core.config import Settings
from l01_core.errors import ErrorCode
from l04_repo import ledger_repo, orm
from l05_service import aggregates, log_pipeline, rewrite
from l05_service.ledger import ledger_row, ledger_service
from l05_service.log_pipeline import ObservabilityPipeline
from tests.fake_upstream_server import FakeUpstreamServer

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-dbs"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings(database_url: str) -> Iterator[Settings]:
    """账本 WAL 落在仓库内的临时目录（沙箱不允许往系统临时目录写）。

    传输沿用默认的 httpx（ADR-065）：gateway._use_litellm 读全局 get_settings()，
    这里给的 Settings 只绑定 runtime，不改变传输选择。

    LEDGER_SPLIT 的产品默认值是 off；这里显式开(on)，因为不变量
    13/14/15 测的就是 split 路径（WAL + 账本 + 对账）。
    """
    wal_dir = SCRATCH_ROOT / f"ledger-{uuid.uuid4().hex}"
    wal_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield Settings(
            database_url=database_url,
            jwt_secret="test-jwt-secret-0123456789abcdef0123456789abcdef",
            jwt_expire_minutes=60,
            admin_username="admin",
            admin_password="admin",
            ledger_split="on",
            ledger_wal_dir=str(wal_dir),
        )
    finally:
        shutil.rmtree(wal_dir, ignore_errors=True)


@pytest.fixture
def upstream() -> Iterator[FakeUpstreamServer]:
    server = FakeUpstreamServer().start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _channel(client, headers, url: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "不变量测试渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "upstream_model": "fake-model",
        "call_model": "gpt-test",
        **overrides,
    }
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers, label: str = "invariants") -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def _chat_body(model: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    body.update(overrides)
    return body


def _post_chat(client, key: str, body: dict[str, Any]):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )


def _upstream_posts(upstream: FakeUpstreamServer, prefix: str) -> int:
    return sum(
        1
        for request in upstream.requests
        if request["method"] == "POST" and request["path"].startswith(prefix)
    )


class _RawUpstream:
    """最小 HTTP 上游：GET 回一个合法模型列表，POST 回配置好的状态码与字节。"""

    def __init__(
        self, status: int, body: bytes, content_type: str = "text/plain"
    ) -> None:
        self.status = status
        self.body = body
        self.content_type = content_type
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> _RawUpstream:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                payload = json.dumps(
                    {"object": "list", "data": [{"id": "fake-model"}]}
                ).encode()
                self._send(200, payload, "application/json")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self._send(owner.status, owner.body, owner.content_type)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"


async def _raw_channel(client, raw: _RawUpstream) -> dict[str, Any]:
    headers = await _token(client)
    channel = await _channel(client, headers, raw.base_url, name="非 JSON 上游")
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    return {"headers": headers, "channel": channel, "key": key}


# ---------------------------------------------------------------------------
# 不变量 1：首字节前失败可重试/换渠道；首字节后失败只能断流
# ---------------------------------------------------------------------------


async def test_inv01_pre_first_byte_failure_rotates_to_another_channel(
    client, upstream
):
    headers = await _token(client)
    bad = await _channel(
        client, headers, upstream.base_url + "/broken", name="坏渠道", priority=2
    )
    await _model(client, headers, bad["id"], call_model="inv-rotate")
    good = await _channel(client, headers, upstream.base_url, name="好渠道", priority=1)
    await _model(client, headers, good["id"], call_model="inv-rotate")
    key = await _key(client, headers)

    response = await _post_chat(client, key["key"], _chat_body("inv-rotate"))

    assert response.status_code == 200, response.text
    assert _upstream_posts(upstream, "/broken") >= 1
    assert _upstream_posts(upstream, "/v1/chat/completions") >= 1


async def test_inv01_post_first_byte_failure_only_breaks_the_stream(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client,
        headers,
        upstream.base_url + "/openai-stream-broken",
        name="首字节后断流",
    )
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)

    response = await _post_chat(
        client, key["key"], _chat_body(f"{channel['slug']}/gpt-test", stream=True)
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "[DONE]" not in response.text
    assert _upstream_posts(upstream, "/openai-stream-broken") == 1


# ---------------------------------------------------------------------------
# 不变量 2：上游 200 但空流 -> empty；上游 200 非 JSON -> bad_response
# ---------------------------------------------------------------------------


async def test_inv02_empty_upstream_stream_is_empty_response(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/openai-stream-empty", name="空流渠道"
    )
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)

    response = await _post_chat(
        client, key["key"], _chat_body(f"{channel['slug']}/gpt-test", stream=True)
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == ErrorCode.EMPTY_RESPONSE


async def test_inv02_non_json_upstream_is_502_and_not_forwarded(client):
    raw = _RawUpstream(200, b"definitely not json").start()
    try:
        prepared = await _raw_channel(client, raw)
        response = await _post_chat(
            client,
            prepared["key"]["key"],
            _chat_body(f"{prepared['channel']['slug']}/gpt-test"),
        )
        assert response.status_code == 502
        payload = response.json()
        # 200 + 非 JSON 是"上游答复了但我们读不懂"，不是上游 5xx。
        assert payload["error"]["code"] == ErrorCode.BAD_UPSTREAM_RESPONSE
        # 上游原文只能出现在错误详情里，不能被当成成功响应转发。
        assert "choices" not in payload
    finally:
        raw.stop()


async def test_inv02_non_json_upstream_uses_bad_response_code(client):
    raw = _RawUpstream(200, b"<html>gateway</html>", "text/html").start()
    try:
        prepared = await _raw_channel(client, raw)
        response = await _post_chat(
            client,
            prepared["key"]["key"],
            _chat_body(f"{prepared['channel']['slug']}/gpt-test"),
        )
        assert response.json()["error"]["code"] == ErrorCode.BAD_UPSTREAM_RESPONSE
    finally:
        raw.stop()


# ---------------------------------------------------------------------------
# 不变量 3 / 4：改写异常原响应胜出；流式只改头不动 body
# ---------------------------------------------------------------------------


def test_inv03_rewrite_error_keeps_the_original_response(monkeypatch):
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("rewrite exploded")

    monkeypatch.setattr(rewrite, "apply_body_rules_report", boom)
    channel = SimpleNamespace(
        rewrite_rules_json=json.dumps(
            {"response_body": [{"mode": "set", "path": "id", "value": "rewritten"}]}
        )
    )
    body = {"id": "original", "choices": [{"message": {"content": "hi"}}]}

    prepared = rewrite.prepare_response(
        channel,
        body,
        {"content-type": "application/json"},
        upstream_model="fake-model",
        requested_model="gpt-test",
    )

    assert prepared.body == body
    assert prepared.skipped == "error"
    assert "RuntimeError" in prepared.error
    assert prepared.headers.get("content-type") == "application/json"


def test_inv04_stream_rewrite_touches_headers_only(monkeypatch):
    calls: list[Any] = []

    def spy(body: dict[str, Any], rules: Any, variables: Any) -> Any:
        calls.append(rules)
        return body, []

    monkeypatch.setattr(rewrite, "apply_body_rules_report", spy)
    rules = {
        "response_body": [{"mode": "set", "path": "id", "value": "rewritten"}],
        "response_headers": [{"mode": "set", "path": "X-Inv", "value": "1"}],
    }
    channel = SimpleNamespace(rewrite_rules_json=json.dumps(rules))
    body = {"id": "original", "choices": []}

    prepared = rewrite.prepare_response(
        channel,
        body,
        {"content-type": "application/json"},
        upstream_model="fake-model",
        requested_model="gpt-test",
        is_stream=True,
    )

    assert calls == []
    assert prepared.body == body
    assert prepared.skipped == "stream"
    assert prepared.headers.get("X-Inv") == "1"
    assert prepared.applied is True


# ---------------------------------------------------------------------------
# 不变量 5：未命中转换计划 -> 400 NOT_IMPLEMENTED，不做 best-effort
# ---------------------------------------------------------------------------


async def test_inv05_unsupported_protocol_is_refused_with_not_implemented(
    client, upstream
):
    headers = await _token(client)
    channel = await _channel(
        client,
        headers,
        upstream.base_url,
        name="anthropic 渠道",
        protocol="anthropic_messages",
    )
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)

    response = await _post_chat(
        client, key["key"], _chat_body(f"{channel['slug']}/gpt-test")
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.NOT_IMPLEMENTED


# ---------------------------------------------------------------------------
# 不变量 9：观测队列满 -> 丢观测、计数、不阻塞请求
# ---------------------------------------------------------------------------


def test_inv09_observability_queue_full_drops_and_counts():
    pipeline = ObservabilityPipeline(
        maxsize=2, batch=2, interval_s=60.0, session_factory=object(), autostart=False
    )

    assert pipeline.submit("log-a", {}, {}) is True
    assert pipeline.submit("log-b", {}, {}) is True
    assert pipeline.submit("log-c", {}, {}) is False
    stats = pipeline.stats()
    assert stats["dropped"] == 1
    assert stats["queued"] == 2


# ---------------------------------------------------------------------------
# 不变量 10：限流/背压响应必须带 Retry-After（今天只覆盖上游驱动的 429/503）
# ---------------------------------------------------------------------------


async def test_inv10_throttled_response_carries_retry_after(client, upstream):
    headers = await _token(client)
    channel = await _channel(
        client, headers, upstream.base_url + "/rate-limited", name="限流渠道"
    )
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)

    response = await _post_chat(
        client, key["key"], _chat_body(f"{channel['slug']}/gpt-test")
    )

    assert response.status_code in (429, 503), response.text
    assert int(response.headers["retry-after"]) >= 1


# ---------------------------------------------------------------------------
# 不变量 11：global_retry_budget_s 不得大于 request_timeout_s
# 配置层由 model_validator 拒绝、运行层由「单次尝试超时按剩余预算封顶」保证。
# ---------------------------------------------------------------------------


def test_inv11_retry_budget_must_not_exceed_request_timeout():
    settings = Settings(database_url="sqlite+aiosqlite://")
    assert settings.global_retry_budget_s <= settings.request_timeout_s
    # An env override that breaks the invariant must fail at startup instead of
    # letting one request outlive the timeout the client was promised.
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite+aiosqlite://", global_retry_budget_s=180.0)


# ---------------------------------------------------------------------------
# 不变量 13 / 14：计费记录不可丢；写失败降级同步写 + 告警
# ---------------------------------------------------------------------------


def test_inv13_d1_wal_flush_window_is_within_100ms():
    # 显式 opt-in：D1 WAL 前提要求 split 打开（产品默认已是 off）
    settings = Settings(database_url="sqlite+aiosqlite://", ledger_split="on")
    assert settings.ledger_split != "off"
    assert settings.ledger_wal_flush_ms <= 100


async def test_inv13_billing_row_survives_observability_drop(
    client, app, upstream, monkeypatch
):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url, name="计费渠道")
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    # 观测队列满：提交一律返回 False（丢观测、计数），请求本身不受影响。
    monkeypatch.setattr(log_pipeline.observability, "submit", lambda *a, **k: False)

    response = await _post_chat(
        client, key["key"], _chat_body(f"{channel['slug']}/gpt-test")
    )

    assert response.status_code == 200, response.text
    assert ledger_service._wal is not None
    await ledger_service.flush_once()
    async with app.state.session_factory() as session:
        usage = await ledger_repo.ledger_usage_by_request(
            session, [response.headers["X-Request-ID"]]
        )
    assert usage, "降级路径没有留下计费记录"
    assert next(iter(usage.values()))["has_ledger_row"] is True


async def test_inv14_wal_failure_degrades_to_sync_write(client, app, monkeypatch):
    service = ledger_service
    assert service._wal is not None
    wal_cls = type(service._wal)

    def boom(self, record: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(wal_cls, "append", boom)
    request_id = "inv14-" + uuid.uuid4().hex
    row = ledger_row(
        request_id=request_id,
        requested_model="gpt-test",
        prompt_tokens=7,
        completion_tokens=3,
        cost_usd=0.25,
    )
    degraded_before = service.degraded

    await service.charge(row)

    assert service.degraded == degraded_before + 1
    assert "disk full" in service.last_error
    async with app.state.session_factory() as session:
        usage = await ledger_repo.ledger_usage_by_request(session, [request_id])
    assert usage[request_id]["prompt_tokens"] == 7
    assert usage[request_id]["cost_usd"] == 0.25


# ---------------------------------------------------------------------------
# 不变量 15：request_id 是幂等键（重试/补记不产生重复计费）
# ---------------------------------------------------------------------------


async def test_inv15_request_id_is_the_idempotency_key(client, app):
    request_id = "inv15-" + uuid.uuid4().hex
    row = ledger_row(
        request_id=request_id,
        requested_model="gpt-test",
        prompt_tokens=11,
        cost_usd=0.5,
    )
    async with app.state.session_factory() as session:
        await ledger_repo.insert_ledger_rows(session, [row])
        await ledger_repo.insert_ledger_rows(session, [row])
        await session.commit()
        count = (
            await session.execute(
                select(func.count())
                .select_from(orm.RequestLedgerORM)
                .where(orm.RequestLedgerORM.request_id == request_id)
            )
        ).scalar_one()
    assert count == 1

    # 热路径同样幂等：同一 request_id 连续入账两次，落库只有一条。
    service = ledger_service
    assert service._wal is not None
    await service.charge(row)
    await service.charge(row)
    await service.flush_once()
    async with app.state.session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(orm.RequestLedgerORM)
                .where(orm.RequestLedgerORM.request_id == request_id)
            )
        ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# 不变量 16：估算值必须带标记且不计入聚合；账目只追加、可修正
# ---------------------------------------------------------------------------


def test_inv16_estimated_rows_are_excluded_from_usage_totals():
    rows = [
        SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=0,
            cost_usd=0.5,
            usage_estimated=False,
        ),
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            cached_tokens=0,
            cost_usd=5.0,
            usage_estimated=True,
        ),
    ]

    totals = aggregates.usage_totals(rows)

    assert len(totals.counted) == 1
    assert totals.prompt_tokens == 10
    assert totals.completion_tokens == 5
    assert totals.cost_usd == 0.5
    assert aggregates.cache_hit_rate(rows) == 0.0


async def test_inv16_ledger_is_append_only_and_adjustable(client, app):
    request_id = "inv16-" + uuid.uuid4().hex
    row = ledger_row(
        request_id=request_id,
        requested_model="gpt-test",
        prompt_tokens=10,
        cost_usd=0.5,
        usage_estimated=True,
    )
    async with app.state.session_factory() as session:
        await ledger_repo.insert_ledger_rows(session, [row])
        await session.commit()
        await ledger_repo.add_ledger_adjustment(
            session,
            ledger_repo.adjustment_row(
                request_id=request_id,
                delta_prompt_tokens=5,
                delta_cost_usd=0.25,
                reason="reconcile",
            ),
        )
        stored = (
            await session.execute(
                select(orm.RequestLedgerORM).where(
                    orm.RequestLedgerORM.request_id == request_id
                )
            )
        ).scalar_one()
        usage = await ledger_repo.ledger_usage_by_request(session, [request_id])

    assert stored.prompt_tokens == 10
    assert bool(stored.usage_estimated) is True
    assert usage[request_id]["prompt_tokens"] == 15
    assert usage[request_id]["cost_usd"] == 0.75
    assert usage[request_id]["usage_estimated"] is True
