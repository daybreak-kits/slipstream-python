from __future__ import annotations

import uuid

from l04_repo import orm

START = 1_760_000_000_000
END = START + 3_600_000


async def _login(client) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed(client, rows: list[dict]) -> None:
    """播种 request_logs；只用测试关心的列，其余交给 ORM 默认值。"""
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        for index, fields in enumerate(rows):
            payload = {
                "id": str(uuid.uuid4()),
                "request_id": f"flow-{uuid.uuid4().hex}",
                "source": "chat",
                "http_status": 200,
                "created_at": START + 1_000 + index,
            }
            payload.update(fields)
            session.add(orm.RequestLogORM(**payload))
        await session.commit()


async def _flows(client, headers: dict[str, str], **params) -> dict:
    params.setdefault("start_ms", START)
    params.setdefault("end_ms", END)
    response = await client.get("/api/dashboard/flows", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _node(body: dict, node_id: str) -> dict:
    return next(node for node in body["nodes"] if node["id"] == node_id)


def _nodes(body: dict, stage: str) -> list[dict]:
    return [node for node in body["nodes"] if node["stage"] == stage]


def _link(body: dict, source: str, target: str) -> dict:
    return next(
        link
        for link in body["links"]
        if link["source"] == source and link["target"] == target
    )


def _assert_flow_conserved(body: dict) -> None:
    """节点 value 必须等于它的入边和、出边和（首尾阶段只有一侧）。"""
    outgoing: dict[str, int] = {}
    incoming: dict[str, int] = {}
    for link in body["links"]:
        outgoing[link["source"]] = outgoing.get(link["source"], 0) + link["value"]
        incoming[link["target"]] = incoming.get(link["target"], 0) + link["value"]
    for node in body["nodes"]:
        expected_out = 0 if node["stage"] == "channel" else node["value"]
        expected_in = 0 if node["stage"] == "user" else node["value"]
        assert outgoing.get(node["id"], 0) == expected_out, node
        assert incoming.get(node["id"], 0) == expected_in, node


async def test_flow_chain_runs_through_five_stages_and_conserves_values(client) -> None:
    headers = await _login(client)
    async with client._transport.app.state.session_factory() as session:  # type: ignore[attr-defined]
        session.add(orm.UserORM(id="u-1", username="admin"))
        await session.commit()
    await _seed(
        client,
        [
            {
                "user_id": "u-1",
                "api_key_id": "k-1",
                "api_key_label": "default",
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            {
                "user_id": "u-1",
                "api_key_id": "k-1",
                "api_key_label": "default",
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
                "prompt_tokens": 20,
                "completion_tokens": 7,
            },
        ],
    )

    body = await _flows(client, headers)

    assert body["metric"] == "requests"
    assert body["stages"] == ["user", "key", "model", "upstream", "channel"]
    # 同一链路的两条请求：五级各一个节点，value 都是 2，share 都是 1。
    assert len(body["nodes"]) == 5
    for stage, expected_id, expected_label in (
        ("user", "user:u-1", "admin"),
        ("key", "key:k-1", "default"),
        ("model", "model:basis", "basis"),
        ("upstream", "upstream:deepseek-flash", "deepseek-flash"),
        ("channel", "channel:vendorq", "VendorQ"),
    ):
        node = _node(body, expected_id)
        assert node["stage"] == stage
        assert node["label"] == expected_label
        assert node["value"] == 2
        assert node["share"] == 1.0
    # 五级之间正好四条边，每条都是 2，且与两端节点自洽。
    assert len(body["links"]) == 4
    assert _link(body, "user:u-1", "key:k-1")["value"] == 2
    assert _link(body, "upstream:deepseek-flash", "channel:vendorq")["value"] == 2
    _assert_flow_conserved(body)
    assert body["total"] == {"requests": 2, "prompt_tokens": 30, "completion_tokens": 12}


async def test_metric_switches_between_requests_and_tokens(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "api_key_label": "default",
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            {
                "api_key_label": "default",
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
                "prompt_tokens": 20,
                "completion_tokens": 0,
            },
        ],
    )

    requests_body = await _flows(client, headers, metric="requests")
    assert _node(requests_body, "channel:vendorq")["value"] == 2
    assert requests_body["total"]["prompt_tokens"] == 30

    tokens_body = await _flows(client, headers, metric="tokens")
    assert tokens_body["metric"] == "tokens"
    # 35 = 10 + 5 + 20 + 0；总量口径不受 metric 影响。
    assert _node(tokens_body, "channel:vendorq")["value"] == 35
    assert _node(tokens_body, "model:basis")["value"] == 35
    assert tokens_body["total"] == {"requests": 2, "prompt_tokens": 30, "completion_tokens": 5}
    _assert_flow_conserved(tokens_body)


async def test_tail_beyond_twenty_nodes_is_merged_and_links_follow(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "api_key_label": "default",
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": f"渠道-{index:02d}",
            }
            for index in range(23)
        ],
    )

    body = await _flows(client, headers)

    channels = _nodes(body, "channel")
    # 20 个真实节点 + 1 个合并节点。
    assert len(channels) == 21
    other = _node(body, "channel:other")
    assert other["label"] == "其他（3）"
    assert other["value"] == 3
    assert other["share"] == round(3 / 23, 6)
    # 被合并的边改指到合并节点，而不是丢掉。
    assert _link(body, "upstream:deepseek-flash", "channel:other")["value"] == 3
    assert sum(node["value"] for node in channels) == 23
    _assert_flow_conserved(body)
    # 其余四级仍然只有一个节点。
    assert len(_nodes(body, "upstream")) == 1


async def test_probe_rows_stay_out_of_the_flow(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
            },
            {
                "source": "probe",
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
            },
        ],
    )

    body = await _flows(client, headers)

    assert body["total"]["requests"] == 1
    assert _node(body, "channel:vendorq")["value"] == 1
    assert len(body["nodes"]) == 5


async def test_custom_window_only_counts_rows_inside_it(client) -> None:
    headers = await _login(client)
    await _seed(client, [{"requested_model": "basis", "channel_name": "VendorQ"}])
    await _seed(
        client,
        [{"requested_model": "basis", "channel_name": "VendorQ", "created_at": START + 7_200_000}],
    )

    body = await _flows(client, headers)

    assert body["total"]["requests"] == 1
    assert _node(body, "channel:vendorq")["value"] == 1

    wide = await _flows(client, headers, start_ms=START, end_ms=START + 7_200_001)
    assert wide["total"]["requests"] == 2
    assert _node(wide, "channel:vendorq")["value"] == 2


async def test_interrupted_stream_counts_as_request_with_known_prompt_only(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "requested_model": "basis",
                "upstream_model": "deepseek-flash",
                "channel_name": "VendorQ",
                "http_status": 499,
                "error_code": "STREAM_STOPPED",
                "usage_estimated": True,
                "prompt_tokens": 100,
                "completion_tokens": 0,
            },
        ],
    )

    requests_body = await _flows(client, headers, metric="requests")
    assert requests_body["total"]["requests"] == 1
    assert _node(requests_body, "channel:vendorq")["value"] == 1

    tokens_body = await _flows(client, headers, metric="tokens")
    # 中断的流只有已发出的 prompt 可信，completion 从未到达。
    assert _node(tokens_body, "channel:vendorq")["value"] == 100
    assert tokens_body["total"]["prompt_tokens"] == 100


async def test_missing_dimensions_get_placeholder_labels(client) -> None:
    headers = await _login(client)
    await _seed(
        client,
        [
            {
                "user_id": None,
                "api_key_id": None,
                "api_key_label": "",
                "requested_model": "basis",
                "upstream_model": "",
                "channel_name": "",
            },
        ],
    )

    body = await _flows(client, headers)

    assert _node(body, "user:anonymous")["label"] == "未登录"
    assert _node(body, "model:basis")["label"] == "basis"
    assert _nodes(body, "upstream")[0]["label"] == "—"
    # 渠道哨兵的身份是 channel:none（不是渠道名折叠的管辖范围）。
    assert _node(body, "channel:channel:none")["label"] == "无渠道"
    assert _nodes(body, "key")[0]["label"] == "无密钥"


async def test_empty_window_still_reports_the_stage_chain(client) -> None:
    headers = await _login(client)

    body = await _flows(client, headers)

    assert body["stages"] == ["user", "key", "model", "upstream", "channel"]
    assert body["nodes"] == []
    assert body["links"] == []
    assert body["total"] == {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}


async def test_unknown_metric_is_rejected(client) -> None:
    headers = await _login(client)

    response = await client.get(
        "/api/dashboard/flows",
        headers=headers,
        params={"start_ms": START, "end_ms": END, "metric": "cost"},
    )

    assert response.status_code == 422
