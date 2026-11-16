from __future__ import annotations


async def test_fake_upstream_serves_models(fake_upstream_client):
    response = await fake_upstream_client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "fake-model"


async def test_fake_upstream_serves_non_stream_chat(fake_upstream_client):
    response = await fake_upstream_client.post(
        "/v1/chat/completions",
        json={"model": "fake-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["usage"]["total_tokens"] == 3


async def test_fake_upstream_serves_sse_stream(fake_upstream_client):
    async with fake_upstream_client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "fake-model", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"content":"hel"' in body
    assert body.rstrip().endswith("data: [DONE]")
