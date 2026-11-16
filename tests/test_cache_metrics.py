"""Cache cohorts: interrupted streams, request-level rate, cacheable share, savings."""

from __future__ import annotations

import json
import uuid

from l04_repo import orm
from l05_service import aggregates


def _row(
    *,
    created_at: int = 1_000,
    prompt: int = 100,
    cached: int = 0,
    usage_estimated: bool = False,
    error_code: str | None = None,
    http_status: int = 200,
    session: str = "",
    channel_model_id: str | None = None,
    cost_usd: float = 0.0,
) -> orm.RequestLogORM:
    headers = {"x-session-id": session} if session else {}
    return orm.RequestLogORM(
        id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        requested_model="group-a",
        final_channel_model_id=channel_model_id,
        channel_name="渠道 A",
        http_status=http_status,
        error_code=error_code,
        prompt_tokens=prompt,
        completion_tokens=0,
        cached_tokens=cached,
        cost_usd=cost_usd,
        usage_estimated=usage_estimated,
        total_ms=10,
        created_at=created_at,
        request_headers_json=json.dumps(headers),
    )


def test_interrupted_stream_enters_cohort_with_zero_cache() -> None:
    rows = [
        _row(prompt=80_000, usage_estimated=True, error_code="STREAM_STOPPED", http_status=499),
        _row(prompt=5_000, cached=1_000),
    ]
    counted = aggregates.counted_rows(rows)
    assert len(counted) == 2
    usage = aggregates.usage_totals(rows)
    assert usage.prompt_tokens == 85_000
    # The interrupted row claims no hit even if a cached number sneaks in.
    assert usage.cached_tokens == 1_000
    assert aggregates.cache_hit_rate(rows) == round(1_000 / 85_000, 6)


def test_stream_aborted_502_counts_but_tokenizer_rows_do_not() -> None:
    interrupted = _row(prompt=521_204, usage_estimated=True, error_code="STREAM_ABORTED", http_status=502)
    tokenizer_guess = _row(prompt=700, usage_estimated=True, http_status=200)
    probe = _row(prompt=0, usage_estimated=True, http_status=200)
    assert aggregates.prompt_tokens_known(interrupted) is True
    assert aggregates.prompt_tokens_known(tokenizer_guess) is False
    assert aggregates.prompt_tokens_known(probe) is False
    assert aggregates.counted_rows([interrupted, tokenizer_guess, probe]) == [interrupted]


def test_request_level_hit_rate_and_saved_money() -> None:
    rows = [
        _row(created_at=1, prompt=1_000, cached=400, channel_model_id="m1"),
        _row(created_at=2, prompt=1_000, cached=0, channel_model_id="m1"),
        _row(created_at=3, prompt=1_000, cached=0, channel_model_id="m1"),
        _row(created_at=4, prompt=1_000, cached=0, channel_model_id="m1"),
    ]
    assert aggregates.cache_hit_rate(rows) == 0.1
    assert aggregates.cache_request_hits(rows) == 1
    assert aggregates.cache_request_hit_rate(rows) == 0.25
    # 400 cached tokens at (2.0 - 0.5) USD per 1M input/cache read.
    prices = {"m1": (2.0, 0.5)}
    assert aggregates.cache_saved_usd(rows, prices) == round(400 / 1_000_000 * 1.5, 8)
    # A model without a configured cache price saves nothing.
    assert aggregates.cache_saved_usd(rows, {"m1": (2.0, 2.0)}) == 0.0


def test_cacheable_ratio_follows_session_prompt_sequence() -> None:
    rows = [
        _row(created_at=1, prompt=100, cached=0, session="s1"),
        _row(created_at=2, prompt=160, cached=60, session="s1"),
        _row(created_at=3, prompt=220, cached=60, session="s1"),
    ]
    totals = aggregates.cacheable_totals(rows)
    # 0 + min(160, 100) + min(220, 160) = 260 of 480 prompt tokens.
    assert totals.tokens == 260
    assert totals.prompt_tokens == 480
    assert totals.sessions == 1
    assert totals.ratio == round(260 / 480, 6)
    # The second header name is a fallback for the first one.
    fallback = _row(created_at=1, prompt=10)
    fallback.request_headers_json = json.dumps({"x-bili-plugin-conversation": "conv-9"})
    assert aggregates.session_key(fallback) == "conv-9"
    assert aggregates.session_key(_row(prompt=10)) == ""
    assert aggregates.session_key(
        _row(prompt=10).__class__(
            id="x",
            request_id="y",
            requested_model="group-a",
            created_at=1,
            request_headers_json="{not json",
        )
    ) == ""


async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_dashboard_summary_exposes_the_three_rates(client) -> None:
    headers = await _login(client)
    start = 1_770_000_000_000
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add_all(
            [
                _row(
                    created_at=start,
                    prompt=1_000,
                    cached=800,
                    session="sess-1",
                    channel_model_id="model-a",
                    cost_usd=0.01,
                ),
                _row(
                    created_at=start + 1,
                    prompt=1_000,
                    cached=0,
                    usage_estimated=True,
                    error_code="STREAM_STOPPED",
                    http_status=499,
                    session="sess-1",
                ),
                _row(
                    created_at=start + 2,
                    prompt=200,
                    cached=0,
                    usage_estimated=True,
                    http_status=200,
                ),
            ]
        )
        session.add(
            orm.ChannelORM(
                id="channel-a",
                slug="channel-a",
                name="渠道 A",
                connection_url="http://127.0.0.1:1",
            )
        )
        await session.flush()
        session.add(
            orm.ChannelModelORM(
                id="model-a",
                channel_id="channel-a",
                upstream_model="gpt-4o-mini",
                call_model="group-a",
                input_price_per_1m=2.0,
                cache_read_price_per_1m=0.5,
                output_price_per_1m=4.0,
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
    assert body["requests"] == 3
    # A: 800 cached / 2000 prompt over the two counted rows.
    assert body["cache_hit_rate"] == 0.4
    assert body["cache_hit_rate_samples"] == 2
    # B: one of the two requests hit the cache.
    assert body["cache_request_hits"] == 1
    assert body["cache_request_hit_rate"] == 0.5
    # C: second session request reuses the first request's 1000 prompt tokens.
    assert body["cacheable_tokens"] == 1_000
    assert body["cacheable_ratio"] == 0.5
    assert body["cacheable_sessions"] == 1
    # D: 800 tokens saved at (2.0 - 0.5)/1M.
    assert body["cache_saved_tokens"] == 800
    assert body["cache_saved_usd"] == round(800 / 1_000_000 * 1.5, 8)
    # The tokenizer-only row is the only excluded one.
    assert body["estimated_rows"] == 1
