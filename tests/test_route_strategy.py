"""ADR-077 routing strategy: metric semantics, layer ordering, overrides."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from l01_core.timeutil import now_ms
from l04_repo import orm
from l05_service import route_settings, route_stats
from l05_service.routing import order_candidates
from l05_service.upstream_settings import UPSTREAM_TIMEOUT_SETTING_KEY


def _candidate(member_id: str, path: tuple[int, ...]):
    return SimpleNamespace(
        channel=SimpleNamespace(id="ch"),
        model=SimpleNamespace(id=member_id),
        priority=path[0],
        priority_path=path,
        member_enabled=True,
    )


def _stat(successes: int = 30, total: int = 30, cost: float = 0.0, tokens: int = 0, ttfa=()):
    return route_stats.MemberStat(
        successes=successes,
        total=total,
        cost_usd=cost,
        tokens=tokens,
        ttfa_ms=tuple(ttfa),
    )


def _ids(candidates) -> list[str]:
    return [str(candidate.model.id) for candidate in candidates]


# ---------------------------------------------------------------------------
# MemberStat semantics
# ---------------------------------------------------------------------------


def test_member_stat_observation_period_is_thirty_successes():
    # ADR-077: the gate counts SUCCESSFUL attempts -- 30 successes tune a
    # member even when failures push the total higher.
    assert not _stat(29, 29).tuned
    assert _stat(30, 30).tuned
    assert _stat(30, 60).tuned


def test_member_stat_price_is_per_million_billed_tokens():
    assert _stat(30, 30, cost=1.0, tokens=200).price_per_million == pytest.approx(5000.0)
    # No billed tokens (or a zero-token reply): free, never infinity.
    assert _stat(30, 30, cost=9.0, tokens=0).price_per_million == 0.0


def test_member_stat_p50_handles_even_odd_and_empty():
    assert _stat(ttfa=(100, 300)).p50_ttfa_ms == 200
    assert _stat(ttfa=(100, 200, 900)).p50_ttfa_ms == 200
    assert _stat(ttfa=()).p50_ttfa_ms is None


# ---------------------------------------------------------------------------
# Layer ordering
# ---------------------------------------------------------------------------


def test_uniform_keeps_absolute_layers_and_shuffles_within():
    candidates = [
        _candidate("low", (1,)),
        _candidate("top", (2,)),
        _candidate("low2", (1,)),
    ]
    ordered = order_candidates(candidates)
    assert _ids(ordered)[0] == "top"
    assert sorted(_ids(ordered)[1:]) == ["low", "low2"]


def _strategy_order(strategy: str, stats: dict) -> list[str]:
    candidates = [_candidate(member_id, (1,)) for member_id in ("m1", "m2", "m3")]
    policy = route_stats.RouteOrderPolicy(strategy=strategy, stats=stats)
    return _ids(order_candidates(candidates, policy=policy))


def test_stable_orders_tuned_by_success_rate_and_weaves_rest():
    stats = {
        "m1": _stat(40, 40),  # rate 1.0
        "m2": _stat(30, 60),  # rate 0.5, still tuned
        "m3": _stat(5, 10),  # observation phase
    }
    ordered = _strategy_order("stable", stats)
    assert sorted(ordered) == ["m1", "m2", "m3"]
    # Tuned members keep their metric order wherever pool members land.
    assert ordered.index("m1") < ordered.index("m2")


def test_cost_orders_tuned_by_effective_price():
    stats = {
        "m1": _stat(30, 30, cost=0.5, tokens=1000000),
        "m2": _stat(30, 30, cost=2.0, tokens=1000000),
        "m3": _stat(3, 5),
    }
    ordered = _strategy_order("cost", stats)
    assert sorted(ordered) == ["m1", "m2", "m3"]
    assert ordered.index("m1") < ordered.index("m2")


def test_latency_orders_tuned_by_p50_and_unknown_is_last():
    stats = {
        "m1": _stat(30, 30, ttfa=(100, 300)),
        "m2": _stat(30, 30, ttfa=(800, 900, 1000)),
        # Tuned but without TTFA samples: cannot rank, goes last of the tuned.
        "m3": _stat(30, 30),
    }
    ordered = _strategy_order("latency", stats)
    assert sorted(ordered) == ["m1", "m2", "m3"]
    assert ordered.index("m1") < ordered.index("m2") < ordered.index("m3")


def test_policy_never_crosses_priority_layers():
    stats = {
        "cheap": _stat(30, 30, cost=0.1, tokens=1000000),
        "pricey": _stat(30, 30, cost=9.0, tokens=1000000),
    }
    candidates = [
        _candidate("pricey", (2,)),
        _candidate("cheap", (1,)),
    ]
    policy = route_stats.RouteOrderPolicy(strategy="cost", stats=stats)
    ordered = order_candidates(candidates, policy=policy)
    # Different layers: the higher layer stays first even though it costs more.
    assert _ids(ordered) == ["pricey", "cheap"]


def test_empty_stats_degrade_to_uniform_shuffle():
    candidates = [_candidate("a", (1,)), _candidate("b", (1,))]
    policy = route_stats.RouteOrderPolicy(strategy="cost", stats={})
    ordered = order_candidates(candidates, policy=policy)
    assert sorted(_ids(ordered)) == ["a", "b"]


# ---------------------------------------------------------------------------
# Combo override precedence (memory path)
# ---------------------------------------------------------------------------


def _prime_settings(strategy: str, window: int) -> None:
    route_stats._settings_cache[0] = route_settings.RouteSettings(
        strategy=strategy, window_minutes=window
    )
    route_stats._settings_cache[1] = time.monotonic()


def test_policy_for_combo_field_wise_override():
    _prime_settings("cost", 60)
    policy = route_stats.policy_for_combo(
        SimpleNamespace(route_strategy="latency", route_window_minutes=15)
    )
    assert policy.strategy == "latency"
    # Window override wins over the global 60.
    _prime_settings("cost", 60)
    policy = route_stats.policy_for_combo(
        SimpleNamespace(route_strategy="", route_window_minutes=15)
    )
    assert policy.strategy == "cost"
    # Empty fields follow the global policy.
    policy = route_stats.policy_for_combo(
        SimpleNamespace(route_strategy="", route_window_minutes=0)
    )
    assert policy.strategy == "cost"
    # Out-of-band window values fall back to the global window.
    policy = route_stats.policy_for_combo(
        SimpleNamespace(route_strategy="", route_window_minutes=10)
    )
    assert policy.strategy == "cost"


# ---------------------------------------------------------------------------
# Stats loading from real rows
# ---------------------------------------------------------------------------


async def test_ensure_stats_aggregates_attempts_usage_and_probes(client, app):
    now = now_ms()
    old = now - 40 * 60000  # outside the default 30-minute window
    async with app.state.session_factory() as session:
        session.add(
            orm.RequestLogORM(
                id="rl1", request_id="rq1", requested_model="combo-a",
                final_channel_model_id="cm1", source="chat",
                prompt_tokens=100, completion_tokens=100, cost_usd=1.0,
                ttfa_ms=200, created_at=now,
            )
        )
        session.add(
            orm.AttemptORM(
                id="at1", log_id="rl1", seq=0, status="success",
                channel_model_id="cm1",
            )
        )
        session.add(
            orm.RequestLogORM(
                id="rl2", request_id="rq2", requested_model="combo-a",
                final_channel_model_id="cm2", source="chat",
                prompt_tokens=10, completion_tokens=10, cost_usd=0.0,
                created_at=now,
            )
        )
        session.add(
            orm.AttemptORM(
                id="at2", log_id="rl2", seq=0, status="timeout",
                channel_model_id="cm2",
            )
        )
        # Probe rows must stay out of the stats entirely: the attempts query
        # cuts source == chat just like the usage/ttfa queries below.
        session.add(
            orm.RequestLogORM(
                id="rl3", request_id="rq3", requested_model="probe",
                final_channel_model_id="cm3", source="probe",
                prompt_tokens=1000, completion_tokens=1000, cost_usd=5.0,
                ttfa_ms=50, created_at=now,
            )
        )
        session.add(
            orm.AttemptORM(
                id="at3", log_id="rl3", seq=0, status="success",
                channel_model_id="cm3",
            )
        )
        # Old traffic outside the window is invisible.
        session.add(
            orm.RequestLogORM(
                id="rl4", request_id="rq4", requested_model="combo-a",
                final_channel_model_id="cm_old", source="chat",
                prompt_tokens=1, completion_tokens=1, cost_usd=99.0,
                created_at=old,
            )
        )
        await session.commit()

        # The lifespan boot warmed the stats cache against an empty DB; drop
        # it so this test reads the rows it just wrote.
        route_stats._stats_cache.clear()
        stats = await route_stats.ensure_stats(session, 30)
        assert stats["cm1"].successes == 1
        assert stats["cm1"].price_per_million == pytest.approx(5000.0)
        assert stats["cm1"].p50_ttfa_ms == 200
        assert stats["cm2"].total == 1
        assert stats["cm2"].successes == 0
        assert not stats["cm2"].tuned
        # cm3 has no key at all: probe attempts no longer enter the stats, so
        # the probe contributes no total, no successes, and no tokens/cost
        # (the old tokens/cost exclusion contract, now enforced at the join).
        assert "cm3" not in stats
        assert "cm_old" not in stats


# ---------------------------------------------------------------------------
# Settings API roundtrip
# ---------------------------------------------------------------------------


def _auth(token_response) -> dict:
    return {"Authorization": "Bearer " + token_response["access_token"]}


async def _token(client) -> dict:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return _auth(response.json())


async def test_settings_route_fields_defaults(client):
    headers = await _token(client)
    response = await client.get("/api/settings", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["route_strategy"] == "uniform"
    assert payload["route_window_minutes"] == 30
    assert payload["gateway_inflight_cap"] == 256


async def test_settings_patch_route_fields_persists(client, app):
    headers = await _token(client)
    updated = await client.patch(
        "/api/settings",
        headers=headers,
        json={
            "route_strategy": "cost",
            "route_window_minutes": 45,
            "gateway_inflight_cap": 512,
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["route_strategy"] == "cost"
    assert payload["route_window_minutes"] == 45
    assert payload["gateway_inflight_cap"] == 512

    async with app.state.session_factory() as session:
        row = await session.get(
            orm.AppSettingORM, route_settings.ROUTE_SETTINGS_SETTING_KEY
        )
        assert row is not None
        stored = json.loads(row.value_json)
        assert stored == {
            "strategy": "cost",
            "window_minutes": 45,
            "inflight_cap": 512,
        }
        # The editor identity lives on the row, not on the body-log fields.
        assert row.updated_by == "admin"

    # The save refreshes the process cache the request path reads.
    cached = route_settings._cache_get()
    assert cached is not None and cached.strategy == "cost"

    reread = await client.get("/api/settings", headers=headers)
    assert reread.json()["route_strategy"] == "cost"


async def test_save_and_reset_route_settings_sync_stats_snapshot(client, app):
    # route_stats keeps its own 15s settings copy; save/reset must refresh it
    # immediately (route_stats docstring promises save-is-immediate), or the
    # memory-only snapshot path would serve a stale strategy for one TTL.
    route_stats._settings_cache[0] = route_settings.RouteSettings(
        strategy="stable", window_minutes=15
    )
    route_stats._settings_cache[1] = time.monotonic()

    async with app.state.session_factory() as session:
        saved = await route_settings.save_route_settings(
            session, strategy="cost", updated_by="admin"
        )
        cached, loaded_at = route_stats._settings_cache
        assert cached is saved
        assert cached.strategy == "cost"
        assert loaded_at > 0.0

        defaults = await route_settings.reset_route_settings(session)
    cached, _ = route_stats._settings_cache
    assert cached is defaults
    assert cached.strategy == "uniform"


async def test_settings_patch_route_validation(client):
    headers = await _token(client)
    for payload in (
        {"route_window_minutes": 10},
        {"route_window_minutes": 1441},
        {"gateway_inflight_cap": -1},
        {"route_strategy": "x" * 17},
        {"route_strategy": "stablee"},
    ):
        response = await client.patch(
            "/api/settings", headers=headers, json=payload
        )
        assert response.status_code == 422, (payload, response.text)


async def test_settings_patch_empty_leaves_no_route_row(client, app):
    headers = await _token(client)
    response = await client.patch("/api/settings", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["route_strategy"] == "uniform"
    async with app.state.session_factory() as session:
        row = await session.get(
            orm.AppSettingORM, route_settings.ROUTE_SETTINGS_SETTING_KEY
        )
        assert row is None


# ---------------------------------------------------------------------------
# Combo API roundtrip
# ---------------------------------------------------------------------------


async def test_combo_route_fields_roundtrip(client):
    headers = await _token(client)
    created = await client.post(
        "/api/combos",
        headers=headers,
        json={
            "name": "policy-combo",
            "route_strategy": "latency",
            "route_window_minutes": 60,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["route_strategy"] == "latency"
    assert body["route_window_minutes"] == 60

    combo_url = "/api/combos/" + body["id"]
    cleared = await client.patch(
        combo_url,
        headers=headers,
        json={"route_strategy": "", "route_window_minutes": 0},
    )
    assert cleared.status_code == 200
    assert cleared.json()["route_strategy"] == ""
    assert cleared.json()["route_window_minutes"] == 0

    invalid = await client.patch(
        combo_url,
        headers=headers,
        json={"route_window_minutes": 10},
    )
    assert invalid.status_code == 422

    misspelled = await client.patch(
        combo_url, headers=headers, json={"route_strategy": "stablee"}
    )
    assert misspelled.status_code == 422


# Shared app_settings plumbing sanity: same fixture reset covers both keys.
assert UPSTREAM_TIMEOUT_SETTING_KEY == "upstream_timeouts"
