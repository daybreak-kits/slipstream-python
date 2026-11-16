"""Wiring tests for the ADR-071 query split and the filter-options fix.

Each test pins the boundary between the two q paths: a keyword must answer
exactly what repo.list_logs answered before (same ids, same total), and a
query that parses as syntax must filter in SQL and never fall back to the
keyword search.
"""

from __future__ import annotations

import json

from l01_core.timeutil import now_ms
from l04_repo import log_filter_values, orm, repo

BASE_MS = 1_762_000_000_000
MINUTE = 60_000


def _recent_ms(index: int) -> int:
    """A timestamp inside the 30-day window the model list reads."""
    return now_ms() - (index + 1) * MINUTE


async def _token(client) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _values(index: int, **overrides) -> dict:
    values = {
        "request_id": f"req-{index:04d}",
        "upstream_request_id": f"chatcmpl-{index:04d}",
        "user_id": None,
        "api_key_label": "key-a",
        "requested_model": "model-a",
        "channel_name": "channel-a",
        "upstream_model": "upstream-a",
        "response_model": "upstream-a",
        "is_stream": False,
        "http_status": 200,
        "created_at": BASE_MS + index * MINUTE,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cache_ratio": 0.25,
        "cost_usd": 0.001,
        "attempts_json": "[]",
        "params_json": "{}",
        "response_headers_json": "{}",
    }
    values.update(overrides)
    return values


async def _seed(app, values: dict) -> None:
    async with app.state.session_factory() as session:
        await repo.add_request_log(session, **values)


def _attempt_trail(model: str, error_code: str = "") -> str:
    return json.dumps(
        [
            {
                "channel_name": "ch-z",
                "channel_slug": "slug-z",
                "channel_model_id": "cm-z",
                "upstream_model": model,
                "error_code": error_code,
                "status": "error" if error_code else "success",
                "ms": 12,
                "round": 1,
            }
        ]
    )


async def _four_rows(app) -> list[str]:
    """A fixed dataset: one plain hit, one combo-named hit, one attempt-only hit."""
    rows = [
        _values(0, requested_model="MiniMax-M3.1-Flash", upstream_model="MiniMax-M3.1-Flash", channel_name="ch-a"),
        _values(1, requested_model="basis", upstream_model="basis-winner", channel_name="ch-b"),
        _values(
            2,
            requested_model="other-2",
            upstream_model="other-2",
            channel_name="ch-z",
            http_status=500,
            error_code="BOOM",
            error_message="boom",
            attempts_json=_attempt_trail("MiniMax-M3.1-Flash", "BOOM"),
        ),
        _values(3, requested_model="gpt-4o", upstream_model="gpt-4o", channel_name="ch-c"),
    ]
    for row in rows:
        await _seed(app, row)
    return [row["request_id"] for row in rows]


async def test_keyword_search_returns_the_same_rows_as_repo(client, app):
    headers = await _token(client)
    await _four_rows(app)
    for keyword in ("", "flash", "basis", "BOOM", "nomatch"):
        response = await client.get("/api/logs", headers=headers, params={"q": keyword, "page_size": 50})
        assert response.status_code == 200, response.text
        body = response.json()
        async with app.state.session_factory() as session:
            expected, expected_total = await repo.list_logs(
                session, page=1, page_size=50, search=keyword
            )
        assert [item["request_id"] for item in body["items"]] == [row.request_id for row in expected]
        assert body["total"] == expected_total


async def test_pasted_text_stays_a_keyword_search(client, app):
    """「429 (Too Many Requests)」这类粘贴文本不再 400（task-126 判定表）。"""
    headers = await _token(client)
    await _four_rows(app)
    for keyword in ("429 (Too Many Requests)", "a=b", "(a)", "R&D"):
        response = await client.get(
            "/api/logs", headers=headers, params={"q": keyword, "page_size": 50}
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0


async def test_real_column_names_work_as_aliases(client, app):
    """upstream_model= 与 upstream= 同结果；别名不再 400（task-126）。"""
    headers = await _token(client)
    await _four_rows(app)
    for alias, main in (("upstream_model", "upstream"), ("requested_model", "model")):
        via_alias = await client.get(
            "/api/logs",
            headers=headers,
            params={"q": f"{alias}==MiniMax-M3.1-Flash", "page_size": 50},
        )
        via_main = await client.get(
            "/api/logs",
            headers=headers,
            params={"q": f"{main}==MiniMax-M3.1-Flash", "page_size": 50},
        )
        assert via_alias.status_code == 200, via_alias.text
        assert via_main.status_code == 200, via_main.text
        assert [item["request_id"] for item in via_alias.json()["items"]] == [
            item["request_id"] for item in via_main.json()["items"]
        ]
    # 数值别名同样走通：http_status==500 只命中 500 那行。
    via_status = await client.get(
        "/api/logs", headers=headers, params={"q": "http_status==500", "page_size": 50}
    )
    assert via_status.status_code == 200, via_status.text
    assert [item["request_id"] for item in via_status.json()["items"]] == ["req-0002"]


async def test_syntax_query_filters_in_sql(client, app):
    headers = await _token(client)
    await _four_rows(app)
    response = await client.get(
        "/api/logs", headers=headers, params={"q": "status>=400 && model*=flash", "page_size": 50}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["request_id"] for item in body["items"]] == ["req-0002"]
    assert body["total"] == 1


async def test_syntax_query_reaches_the_attempt_trail(client, app):
    headers = await _token(client)
    await _four_rows(app)
    response = await client.get(
        "/api/logs", headers=headers, params={"q": 'upstream=="MiniMax-M3.1-Flash"', "page_size": 50}
    )
    assert response.status_code == 200, response.text
    ids = [item["request_id"] for item in response.json()["items"]]
    assert ids == ["req-0002", "req-0000"]


async def test_syntax_query_pages_and_counts(client, app):
    headers = await _token(client)
    await _four_rows(app)
    response = await client.get(
        "/api/logs",
        headers=headers,
        params={"q": "status<400", "page": 2, "page_size": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["request_id"] for item in body["items"]] == ["req-0001"]
    assert body["total"] == 3


async def test_broken_syntax_is_a_400_with_a_column_and_never_falls_back(client, app):
    headers = await _token(client)
    await _four_rows(app)
    cases = ["status>=abc", 'model=="unterminated', "latency<", "model*=flash &&"]
    for query in cases:
        response = await client.get("/api/logs", headers=headers, params={"q": query})
        assert response.status_code == 400, (query, response.text)
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"
        assert body["details"]["column"] >= 0
        assert body["message"]


async def test_plain_text_still_searches_the_keyword_columns(client, app):
    headers = await _token(client)
    await _four_rows(app)
    response = await client.get("/api/logs", headers=headers, params={"q": "hello world"})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


async def test_aggregate_views_refuse_syntax_instead_of_ignoring_it(client, app):
    headers = await _token(client)
    await _four_rows(app)
    for view in ("combo", "status"):
        response = await client.get(
            "/api/logs", headers=headers, params={"view": view, "q": "status>=400"}
        )
        assert response.status_code == 400, (view, response.text)
        assert response.json()["details"]["view"] == "requests"


async def test_summary_refuses_syntax_instead_of_ignoring_it(client, app):
    headers = await _token(client)
    response = await client.get("/api/logs/summary", headers=headers, params={"q": "status>=400"})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_REQUEST"


async def test_filter_options_offer_catalog_models_on_an_empty_log(client, app):
    headers = await _token(client)
    async with app.state.session_factory() as session:
        session.add(orm.ChannelORM(id="ch-1", slug="ch-1", name="渠道一"))
        await session.commit()
    # The foreign key is enforced, so the channel has to exist before its model.
    async with app.state.session_factory() as session:
        session.add(
            orm.ChannelModelORM(
                id="cm-1",
                channel_id="ch-1",
                upstream_model="MiniMax-M3.1-Flash",
                call_model="MiniMax-M3.1-Flash",
            )
        )
        session.add(orm.ComboORM(id="combo-1", name="basis"))
        await session.commit()
    response = await client.get("/api/logs/filters", headers=headers)
    assert response.status_code == 200, response.text
    models = response.json()["models"]
    assert "MiniMax-M3.1-Flash" in models
    assert "basis" in models


async def _seed_combo_catalog(app, *, member_model: str = "MiniMax-M3.1-Flash") -> None:
    """A channel + channel-model + combo, so `model=` has members to widen to."""
    async with app.state.session_factory() as session:
        session.add(orm.ChannelORM(id="ch-catalog", slug="ch-catalog", name="目录渠道"))
        await session.commit()
    # The foreign key is enforced, so the channel has to exist before its model.
    async with app.state.session_factory() as session:
        session.add(
            orm.ChannelModelORM(
                id="cm-catalog",
                channel_id="ch-catalog",
                upstream_model=member_model,
                call_model=member_model,
            )
        )
        session.add(orm.ComboORM(id="combo-flash", name="basis"))
        session.add(
            orm.ComboTargetORM(
                id="ct-flash",
                combo_id="combo-flash",
                target_type="channel_model",
                channel_model_id="cm-catalog",
            )
        )
        await session.commit()


async def test_model_filter_reaches_combo_members(client, app):
    """The reported bug: filtering a member model hid every combo-routed row."""
    headers = await _token(client)
    await _seed_combo_catalog(app)
    await _seed(app, _values(0, requested_model="MiniMax-M3.1-Flash"))
    await _seed(app, _values(1, requested_model="basis", upstream_model="basis-winner"))
    await _seed(app, _values(2, requested_model="gpt-4o"))

    # Same filter either spelling: requested name and combo member both count.
    for model in ("MiniMax-M3.1-Flash", "minimax-m3.1-flash"):
        response = await client.get(
            "/api/logs", headers=headers, params={"model": model, "page_size": 50}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # list_logs is newest-first and req-0001 is the newer row.
        assert [item["request_id"] for item in body["items"]] == [
            "req-0001",
            "req-0000",
        ]
        assert body["total"] == 2

    # An unrelated model keeps the old exact-match behaviour.
    response = await client.get(
        "/api/logs", headers=headers, params={"model": "gpt-4o", "page_size": 50}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["request_id"] for item in body["items"]] == ["req-0002"]
    assert body["total"] == 1


async def test_filter_options_fold_case_and_include_served_models(client, app):
    headers = await _token(client)
    await _seed(
        app,
        _values(
            0,
            requested_model="MiniMax-M3.1-Flash",
            upstream_model="MiniMax-M3.1-Flash",
            created_at=_recent_ms(0),
        ),
    )
    await _seed(
        app,
        _values(
            1,
            requested_model="minimax-m3.1-flash",
            upstream_model="minimax-m3.1-flash",
            created_at=_recent_ms(1),
        ),
    )
    await _seed(
        app,
        _values(2, requested_model="basis", upstream_model="basis-member", created_at=_recent_ms(2)),
    )
    response = await client.get("/api/logs/filters", headers=headers)
    assert response.status_code == 200, response.text
    models = response.json()["models"]
    folded = [value.lower() for value in models]
    assert folded.count("minimax-m3.1-flash") == 1
    assert "basis-member" in folded


async def test_filter_options_keyword_narrows_every_list(client, app):
    headers = await _token(client)
    await _seed(
        app,
        _values(
            0, requested_model="MiniMax-M3.1-Flash", channel_name="ch-a", created_at=_recent_ms(0)
        ),
    )
    await _seed(
        app,
        _values(1, requested_model="gpt-4o", channel_name="ch-b", created_at=_recent_ms(1)),
    )
    response = await client.get("/api/logs/filters", headers=headers, params={"q": "minimax"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["models"] == ["MiniMax-M3.1-Flash"]
    assert body["channels"] == []


def test_fold_values_keeps_the_first_spelling_and_sorts() -> None:
    assert log_filter_values.fold_values(
        ["MiniMax-M3.1-Flash", "minimax-m3.1-flash", None, "", " ", "basis"]
    ) == ["basis", "MiniMax-M3.1-Flash"]
