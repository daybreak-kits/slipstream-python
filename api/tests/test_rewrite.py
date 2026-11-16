from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from l03_domain.rewrite import (
    MISSING,
    apply_body_rules,
    apply_header_rules,
    diff_body,
    get_path,
    validate_rules,
)
from l04_repo import orm, repo
from tests.fake_upstream_server import FakeUpstreamServer


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello world"}],
        "metadata": {"user": {"name": "alice"}},
        "temperature": 0.7,
        "options": {"seed": 1},
    }
    body.update(overrides)
    return body


def _apply(rule: dict[str, Any], body: dict[str, Any] | None = None, variables: dict[str, Any] | None = None):
    return apply_body_rules(body or _body(), [rule], variables or {})


# ---------------------------------------------------------------------------
# 9 body actions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mode", "path", "value", "extra", "check"),
    [
        ("create", "new_field", "x", {}, lambda b: b["new_field"] == "x"),
        ("update", "temperature", 0.2, {}, lambda b: b["temperature"] == 0.2),
        ("delete", "temperature", None, {}, lambda b: "temperature" not in b),
        ("copy", "", None, {"from": "temperature", "to": "options.temperature"},
         lambda b: b["temperature"] == 0.7 and b["options"]["temperature"] == 0.7),
        ("prepend", "messages.0.content", ">> ", {},
         lambda b: b["messages"][0]["content"] == ">> Hello world"),
        ("append", "messages.0.content", "!", {},
         lambda b: b["messages"][0]["content"] == "Hello world!"),
        ("regex_replace", "messages.0.content", None, {"pattern": r"l+", "replacement": "L"},
         lambda b: b["messages"][0]["content"] == "HeLo worLd"),
        ("to_upper", "messages.0.content", None, {}, lambda b: b["messages"][0]["content"] == "HELLO WORLD"),
        ("to_lower", "messages.0.content", None, {}, lambda b: b["messages"][0]["content"] == "hello world"),
    ],
)
def test_each_body_mode(mode, path, value, extra, check):
    body = _body()
    rule = {"mode": mode, "path": path, **extra}
    if value is not None:
        rule["value"] = value

    result = _apply(rule, body)

    assert check(result), result


def test_delete_removes_last_message_and_missing_is_noop():
    body = _body(messages=[{"content": "a"}, {"content": "b"}])
    result = _apply({"mode": "delete", "path": "messages.-1"}, body)
    assert result["messages"] == [{"content": "a"}]

    unchanged = _apply({"mode": "delete", "path": "metadata.nope"}, body)
    assert unchanged == body


def test_copy_keeps_types_and_nested_paths():
    result = _apply(
        {"mode": "copy", "from": "metadata.user.name", "to": "metadata.user.display"}
    )
    assert result["metadata"]["user"] == {"name": "alice", "display": "alice"}


def test_create_skips_existing_and_update_skips_missing():
    body = _body(temperature=0.7)
    assert apply_body_rules(
        body, [{"mode": "create", "path": "temperature", "value": 0.2}], {}
    )["temperature"] == 0.7
    assert "new_field" not in apply_body_rules(
        body, [{"mode": "update", "path": "new_field", "value": 1}], {}
    )


# ---------------------------------------------------------------------------
# Conditions: 6 modes, invert, pass_missing_key, AND/OR
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"path": "model", "mode": "full", "value": "gpt-4o"}, True),
        ({"path": "temperature", "mode": "gt", "value": 0.5}, True),
        ({"path": "temperature", "mode": "gte", "value": 0.7}, True),
        ({"path": "temperature", "mode": "lt", "value": 0.8}, True),
        ({"path": "temperature", "mode": "lte", "value": 0.7}, True),
        ({"path": "model", "mode": "regex", "value": "^gpt-[0-9]"}, True),
        ({"path": "model", "mode": "full", "value": "claude"}, False),
        ({"path": "model", "mode": "full", "value": "claude", "invert": True}, True),
    ],
)
def test_condition_modes(condition, expected):
    rule = {
        "mode": "create",
        "path": "rewritten",
        "value": True,
        "conditions": [condition],
    }
    result = _apply(rule)
    assert ("rewritten" in result) is expected


def test_missing_key_requires_pass_missing_key():
    rule = {
        "mode": "create",
        "path": "rewritten",
        "value": True,
        "conditions": [{"path": "metadata.user.email", "mode": "full", "value": None}],
    }
    assert "rewritten" not in _apply(rule)

    rule["conditions"][0]["pass_missing_key"] = True
    assert _apply(rule)["rewritten"] is True


def test_condition_paths_messages_first_last_and_metadata():
    rule = {
        "mode": "create",
        "path": "rewritten",
        "value": True,
        "conditions": [
            {"path": "messages.0.content", "mode": "regex", "value": "^Hello"},
            {"path": "messages.-1.content", "mode": "regex", "value": "world$"},
            {"path": "metadata.user.name", "mode": "full", "value": "alice"},
        ],
    }
    assert _apply(rule)["rewritten"] is True


@pytest.mark.parametrize("logic", ["AND", "OR"])
def test_and_or_logic(logic):
    rule = {
        "mode": "create",
        "path": "rewritten",
        "value": True,
        "logic": logic,
        "conditions": [
            {"path": "model", "mode": "full", "value": "gpt-4o"},
            {"path": "model", "mode": "full", "value": "claude"},
        ],
    }
    result = _apply(rule)
    assert ("rewritten" in result) is (logic == "OR")


def test_and_requires_all_conditions():
    rule = {
        "mode": "create",
        "path": "rewritten",
        "value": True,
        "logic": "AND",
        "conditions": [
            {"path": "model", "mode": "regex", "value": "^gpt"},
            {"path": "temperature", "mode": "gte", "value": 0.7},
        ],
    }
    assert _apply(rule)["rewritten"] is True

    rule["conditions"][1]["value"] = 0.9
    assert "rewritten" not in _apply(rule)


def test_variables_resolve_in_values_and_replacements():
    result = _apply(
        {
            "mode": "update",
            "path": "metadata.user.name",
            "value": "${upstream_model}:${original_model}:${model}",
        },
        variables={
            "model": "public/model",
            "original_model": "public/model",
            "upstream_model": "real-model",
        },
    )
    assert result["metadata"]["user"]["name"] == "real-model:public/model:public/model"

    replaced = _apply(
        {
            "mode": "regex_replace",
            "path": "messages.0.content",
            "pattern": "world",
            "replacement": "${upstream_model}",
        },
        variables={"upstream_model": "real-model"},
    )
    assert replaced["messages"][0]["content"] == "Hello real-model"


# ---------------------------------------------------------------------------
# Header modes and credential protection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "Content-Type",
        "Content-Length",
        "Host",
        "X-Request-Id",
    ],
)
def test_protected_headers_cannot_be_set_deleted_or_renamed(header):
    original = {header: "channel-secret"}
    rules = [
        {"mode": "set", "path": header, "value": "attacker"},
        {"mode": "delete", "path": header},
        {"mode": "rename", "path": header, "to": "X-Stolen"},
    ]
    result = apply_header_rules(original, rules, {})
    assert result == original


def test_protected_header_protection_is_case_insensitive():
    original = {"authorization": "Bearer key", "Cookie": "a=1"}
    result = apply_header_rules(
        original,
        [
            {"mode": "set", "path": "AUTHORIZATION", "value": "evil"},
            {"mode": "delete", "path": "cookie"},
            {"mode": "rename", "path": "COOKIE", "to": "X-Cookie"},
        ],
        {},
    )
    assert result == original


def test_protocol_and_identity_headers_are_protected_case_insensitively():
    original = {"Content-Type": "application/json", "X-Request-Id": "req_1"}
    result = apply_header_rules(
        original,
        [
            {"mode": "set", "path": "content-type", "value": "text/plain"},
            {"mode": "delete", "path": "X-REQUEST-ID"},
            {"mode": "rename", "path": "CONTENT-TYPE", "to": "X-Content-Type"},
        ],
        {},
    )
    assert result == original


@pytest.mark.parametrize(
    "target",
    [
        "Authorization",
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "Content-Type",
        "Content-Length",
        "Host",
        "X-Request-Id",
    ],
)
def test_header_cannot_be_renamed_onto_protected_name(target):
    result = apply_header_rules(
        {"X-Tenant": "acme"},
        [{"mode": "rename", "path": "X-Tenant", "to": target}],
        {},
    )
    assert result == {"X-Tenant": "acme"}


def test_header_set_delete_rename_and_case_insensitive_match():
    result = apply_header_rules(
        {"X-Tenant": "old", "X-Drop": "1", "X-Old": "kept"},
        [
            {"mode": "set", "path": "x-tenant", "value": "acme"},
            {"mode": "delete", "path": "x-drop"},
            {"mode": "rename", "path": "x-old", "to": "X-New"},
        ],
        {},
    )
    assert result == {"X-Tenant": "acme", "X-New": "kept"}


def test_custom_protocol_headers_are_still_settable():
    # x-api-version / anthropic-version style protocol headers stay the
    # channel's job: only the wire contract and the trace id are reserved.
    result = apply_header_rules(
        {"content-type": "application/json", "x-request-id": "req_1"},
        [{"mode": "set", "path": "x-api-version", "value": "2024-06-01"}],
        {},
    )
    assert result == {
        "content-type": "application/json",
        "x-request-id": "req_1",
        "x-api-version": "2024-06-01",
    }


def test_response_scope_keeps_protocol_header_rules_working():
    # Response rules keep their pre-task reach: only credentials are protected.
    result = apply_header_rules(
        {"content-type": "application/json", "authorization": "secret"},
        [
            {"mode": "set", "path": "content-type", "value": "text/custom"},
            {"mode": "set", "path": "authorization", "value": "evil"},
        ],
        {},
        scope="response",
    )
    assert result == {"content-type": "text/custom", "authorization": "secret"}


def test_header_rule_conditions_use_body_and_variables():
    rules = [
        {
            "mode": "set",
            "path": "X-Model",
            "value": "${upstream_model}",
            "conditions": [{"path": "model", "mode": "regex", "value": "^gpt"}],
        }
    ]
    assert apply_header_rules({}, rules, {"upstream_model": "real"}, _body()) == {"X-Model": "real"}

    rules[0]["conditions"][0]["value"] = "claude"
    assert apply_header_rules({}, rules, {"upstream_model": "real"}, _body()) == {}


# ---------------------------------------------------------------------------
# Validation and diff
# ---------------------------------------------------------------------------
def test_validate_rules_accepts_every_body_mode():
    rules = {
        "request_body": [
            {"mode": "create", "path": "a", "value": 1},
            {"mode": "update", "path": "a", "value": 2},
            {"mode": "delete", "path": "a"},
            {"mode": "copy", "from": "a", "to": "b"},
            {"mode": "prepend", "path": "a", "value": "x"},
            {"mode": "append", "path": "a", "value": "y"},
            {"mode": "regex_replace", "path": "a", "pattern": "x+", "replacement": "y"},
            {"mode": "to_lower", "path": "a"},
            {"mode": "to_upper", "path": "a"},
        ],
        "request_headers": [{"mode": "rename", "path": "X-A", "to": "X-B"}],
    }
    assert validate_rules(rules) == []


@pytest.mark.parametrize(
    "rules",
    [
        {"request_headers": [{"mode": "set", "path": "Authorization", "value": "x"}]},
        {"request_headers": [{"mode": "delete", "path": "authorization"}]},
        {"request_headers": [{"mode": "rename", "path": "authorization", "to": "X-A"}]},
        {"request_headers": [{"mode": "rename", "path": "X-A", "to": "cookie"}]},
        {"request_body": [{"mode": "explode", "path": "a"}]},
        {"request_body": [{"mode": "create", "path": "a"}]},
        {"request_body": [{"mode": "create", "value": 1}]},
        {"request_body": [{"mode": "copy", "from": "a"}]},
        {"request_body": [{"mode": "copy", "from": "a", "to": "a"}]},
        {"request_body": [{"mode": "set", "path": "a", "value": 1}]},
        {
            "request_body": [
                {"mode": "create", "path": "a", "value": 1, "conditions": [{"path": "a", "mode": "regex", "value": "["}]}
            ]
        },
        {
            "request_body": [
                {"mode": "create", "path": "a", "value": 1, "logic": "XOR", "conditions": []}
            ]
        },
        {"request_body": [{"mode": "regex_replace", "path": "a", "pattern": "["}]},
        {"request_body": [{"mode": "prepend", "path": "a", "value": 1}]},
    ],
)
def test_validate_rules_rejects_invalid_rules(rules):
    assert validate_rules(rules)


def test_diff_body_reports_nested_changes():
    before = _body()
    after = _apply(
        {
            "mode": "update",
            "path": "messages.-1.content",
            "value": "hi",
        },
        before,
    )
    changes = diff_body(before, after)
    assert changes == [{"path": "messages.0.content", "before": "Hello world", "after": "hi"}]


# ---------------------------------------------------------------------------
# End-to-end: gateway applies rules, logs the diff and protects credentials
# ---------------------------------------------------------------------------
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


async def _channel(client, headers, url: str, **overrides) -> dict[str, Any]:
    payload = {
        "name": "改写测试渠道",
        "connection_url": url,
        "api_key": "channel-secret-key",
        "protocol": "openai_chat",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/channels", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _model(client, headers, channel_id: str, **overrides) -> dict[str, Any]:
    payload = {"upstream_model": "fake-model", "call_model": "gpt-test", **overrides}
    response = await client.post(
        f"/api/channels/{channel_id}/models", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _key(client, headers, label: str = "rw") -> dict[str, Any]:
    response = await client.post("/api/keys", headers=headers, json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


async def test_request_rules_rewrite_headers_and_body_and_log_diff(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    rules = {
        "request_headers": [
            {"mode": "set", "path": "X-Tenant", "value": "acme"},
            {"mode": "set", "path": "X-Internal", "value": "drop-me"},
            {"mode": "delete", "path": "x-internal"},
            {"mode": "rename", "path": "X-Tenant", "to": "X-Org"},
        ],
        "request_body": [
            {"mode": "update", "path": "messages.-1.content", "value": "rewritten hello"},
            {
                "mode": "create",
                "path": "metadata.user.name",
                "value": "${original_model}|${upstream_model}",
            },
                {
                    "mode": "create",
                    "path": "temperature",
                    "value": 0.3,
                    "conditions": [{"path": "model", "mode": "regex", "value": "/gpt-test$"}],
                },
        ],
    }
    patched = await client.patch(
        f"/api/channels/{channel['id']}", headers=headers, json={"rewrite_rules": rules}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["rewrite_rules"] == rules

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text

    posted = upstream.find("POST", "/v1/chat/completions")
    assert len(posted) == 1
    sent = posted[0]
    assert sent["json"]["model"] == "fake-model"
    assert sent["json"]["messages"] == [{"role": "user", "content": "rewritten hello"}]
    assert sent["json"]["metadata"]["user"]["name"] == (
        f"{channel['slug']}/gpt-test|fake-model"
    )
    assert sent["json"]["temperature"] == 0.3
    assert sent["headers"].get("X-Org") == "acme"
    assert "X-Tenant" not in sent["headers"]
    assert "X-Internal" not in sent["headers"]
    assert sent["headers"]["authorization"] == "Bearer channel-secret-key"

    async with app.state.session_factory() as session:
        logs, _total = await repo.list_logs(session, page=1, page_size=5)
        log = next(item for item in logs if item.request_id == response.headers["X-Request-ID"])
        assert log.rewrite_applied is True
        diff = json.loads(log.rewrite_diff_json)
        assert "x-org" in diff["request_headers"]
        paths = {change["path"] for change in diff["request_body"]}
        assert {"messages.0.content", "metadata", "temperature"} <= paths


async def test_rules_that_do_not_match_leave_request_untouched(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    rules = {
        "request_body": [
            {
                "mode": "update",
                "path": "temperature",
                "value": 0.1,
                "logic": "AND",
                "conditions": [
                    {"path": "model", "mode": "regex", "value": "^other/"},
                    {"path": "messages.0.content", "mode": "regex", "value": "hello"},
                ],
            },
            {
                "mode": "create",
                "path": "top_p",
                "value": 0.5,
                "conditions": [{"path": "metadata.user.email", "mode": "full", "value": None}],
            },
        ]
    }
    await client.patch(
        f"/api/channels/{channel['id']}", headers=headers, json={"rewrite_rules": rules}
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    sent = upstream.find("POST", "/v1/chat/completions")[0]["json"]
    assert "temperature" not in sent
    assert "top_p" not in sent

    async with app.state.session_factory() as session:
        logs, _total = await repo.list_logs(session, page=1, page_size=5)
        log = next(item for item in logs if item.request_id == response.headers["X-Request-ID"])
        assert log.rewrite_applied is False
        assert json.loads(log.rewrite_diff_json) == {}


async def test_body_rules_cannot_change_model_or_stream_semantics(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    rules = {
        "request_body": [
            {"mode": "update", "path": "model", "value": "evil-model"},
            {"mode": "create", "path": "stream", "value": True},
        ]
    }
    await client.patch(
        f"/api/channels/{channel['id']}", headers=headers, json={"rewrite_rules": rules}
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    sent = upstream.find("POST", "/v1/chat/completions")[0]["json"]
    assert sent["model"] == "fake-model"
    assert "stream" not in sent


async def test_stored_rules_can_never_replace_the_channel_credential(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    malicious = {
        "request_headers": [
            {"mode": "set", "path": "Authorization", "value": "Bearer attacker"},
            {"mode": "set", "path": "api-key", "value": "attacker"},
            {"mode": "set", "path": "x-api-key", "value": "attacker"},
            {"mode": "set", "path": "Cookie", "value": "session=attacker"},
            {"mode": "set", "path": "Set-Cookie", "value": "session=attacker"},
            {"mode": "delete", "path": "authorization"},
            {"mode": "rename", "path": "authorization", "to": "X-Stolen"},
        ]
    }
    async with app.state.session_factory() as session:
        stored = await session.get(orm.ChannelORM, channel["id"])
        assert stored is not None
        stored.rewrite_rules_json = json.dumps(malicious)
        await session.commit()

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    sent_headers = upstream.find("POST", "/v1/chat/completions")[0]["headers"]
    assert sent_headers["authorization"] == "Bearer channel-secret-key"
    assert "X-Stolen" not in sent_headers
    assert "Cookie" not in sent_headers
    assert not any(name.lower() == "api-key" for name in sent_headers)


async def test_rewrite_rules_are_applied_to_streaming_requests(client, app, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    await _model(client, headers, channel["id"])
    key = await _key(client, headers)
    rules = {
        "request_body": [
            {"mode": "update", "path": "messages.0.content", "value": "stream rewrite"},
        ]
    }
    await client.patch(
        f"/api/channels/{channel['id']}", headers=headers, json={"rewrite_rules": rules}
    )

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={
            "model": f"{channel['slug']}/gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200, await response.aread()
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "data:" in text
    sent = upstream.find("POST", "/v1/chat/completions")[0]
    assert sent["json"]["messages"][0]["content"] == "stream rewrite"

    async with app.state.session_factory() as session:
        logs, _total = await repo.list_logs(session, page=1, page_size=5)
        assert logs[0].rewrite_applied is True


async def test_saving_protected_or_invalid_rules_is_rejected(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    protected = await client.patch(
        f"/api/channels/{channel['id']}",
        headers=headers,
        json={
            "rewrite_rules": {
                "request_headers": [
                    {"mode": "set", "path": "Authorization", "value": "evil"}
                ]
            }
        },
    )
    assert protected.status_code == 400
    assert "Authorization" in protected.json()["message"]

    invalid = await client.patch(
        f"/api/channels/{channel['id']}",
        headers=headers,
        json={"rewrite_rules": {"request_body": [{"mode": "explode", "path": "a"}]}},
    )
    assert invalid.status_code == 400

    fetched = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert fetched.json()["rewrite_rules"] == {}


async def test_request_protocol_headers_are_rejected_but_response_headers_are_allowed(
    client, upstream
):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)

    for header in ["Content-Type", "Content-Length", "Host", "X-Request-Id"]:
        rejected = await client.patch(
            f"/api/channels/{channel['id']}",
            headers=headers,
            json={
                "rewrite_rules": {
                    "request_headers": [{"mode": "set", "path": header, "value": "x"}]
                }
            },
        )
        assert rejected.status_code == 400
        assert header.lower() in rejected.json()["message"].lower()

    renamed = await client.patch(
        f"/api/channels/{channel['id']}",
        headers=headers,
        json={
            "rewrite_rules": {
                "request_headers": [
                    {"mode": "rename", "path": "X-Custom", "to": "x-request-id"}
                ]
            }
        },
    )
    assert renamed.status_code == 400

    allowed = await client.patch(
        f"/api/channels/{channel['id']}",
        headers=headers,
        json={
            "rewrite_rules": {
                "response_headers": [
                    {"mode": "set", "path": "content-type", "value": "text/custom"}
                ]
            }
        },
    )
    assert allowed.status_code == 200, allowed.text
    stored = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert stored.json()["rewrite_rules"]["response_headers"][0]["path"] == "content-type"


async def test_rewrite_preview_uses_draft_rules_without_persisting(client, upstream):
    headers = await _token(client)
    channel = await _channel(client, headers, upstream.base_url)
    draft = {
        "request_body": [
            {"mode": "update", "path": "temperature", "value": 0.2},
            {"mode": "create", "path": "top_p", "value": 0.9},
        ]
    }

    response = await client.post(
        f"/api/channels/{channel['id']}/rewrite-preview",
        headers=headers,
        json={
            "section": "request_body",
            "rules": draft,
            "body": {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "你好"}],
                "temperature": 0.7,
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warnings"] == []
    assert body["after"]["temperature"] == 0.2
    assert body["after"]["top_p"] == 0.9
    assert {change["path"] for change in body["diff"]} >= {"temperature", "top_p"}

    fetched = await client.get(f"/api/channels/{channel['id']}", headers=headers)
    assert fetched.json()["rewrite_rules"] == {}


def test_get_path_supports_negative_index():
    assert get_path(_body(), "messages.-1.content") == "Hello world"
    assert get_path(_body(), "messages.5.content") is MISSING
