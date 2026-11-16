from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

ANTHROPIC_VERSION = "2023-06-01"


def _openai_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": "fake-model", "object": "model", "owned_by": "test"},
            {"id": "fake-model-2", "object": "model", "owned_by": "test"},
        ],
    }


def _typed_models() -> dict[str, Any]:
    """Model list mixing chat / embedding / rerank names (type inference)."""
    return {
        "object": "list",
        "data": [
            {"id": "fake-model", "object": "model", "owned_by": "test"},
            {"id": "text-embedding-3-small", "object": "model", "owned_by": "test"},
            {"id": "bge-reranker-v2", "object": "model", "owned_by": "test"},
        ],
    }

def _anthropic_models() -> dict[str, Any]:
    return {
        "data": [
            {"id": "claude-fake", "type": "model", "display_name": "Claude Fake"},
        ],
        "has_more": False,
        "first_id": "claude-fake",
        "last_id": "claude-fake",
    }


def _gemini_models() -> dict[str, Any]:
    return {
        "models": [
            {"name": "models/gemini-fake", "displayName": "Gemini Fake"},
        ]
    }


def _openai_chat(
    body: dict[str, Any], *, include_usage: bool = True
) -> dict[str, Any]:
    payload = {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": body.get("model") or "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }
        ],
    }
    if include_usage:
        payload["usage"] = {
            "prompt_tokens": 3,
            "completion_tokens": 1,
            "total_tokens": 4,
        }
    return payload


def _openai_embeddings(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "model": body.get("model") or "fake-embedding",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


def _openai_rerank(body: dict[str, Any]) -> dict[str, Any]:
    documents = body.get("documents") or []
    return {
        "object": "list",
        "model": body.get("model") or "fake-rerank",
        "results": [
            {"index": index, "relevance_score": 1.0 - index * 0.1}
            for index in range(len(documents))
        ],
    }


def _gemini_embedding() -> dict[str, Any]:
    return {"embedding": {"values": [0.1, 0.2, 0.3]}}


def _anthropic_chat(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": body.get("model") or "claude-fake",
        "content": [{"type": "text", "text": "OK"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }


def _stream_chunk(
    content: Any = None,
    *,
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "chatcmpl-stream",
        "object": "chat.completion.chunk",
        "model": "fake-model",
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
    }
    if content is not None:
        payload["choices"][0]["delta"]["content"] = content
    if reasoning is not None:
        payload["choices"][0]["delta"]["reasoning_content"] = reasoning
    if tool_calls is not None:
        payload["choices"][0]["delta"]["tool_calls"] = tool_calls
    if usage is not None:
        payload["usage"] = usage
    return payload


def _gemini_chat() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "OK"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
    }


class _Handler(BaseHTTPRequestHandler):
    server: "FakeUpstreamServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _json(
        self,
        status: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Upstream-Trace", "upstream-123")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("X-Upstream-Trace", "upstream-123")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _sse_raw(self, data: str) -> bool:
        try:
            self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _sse_json(self, payload: dict[str, Any]) -> bool:
        return self._sse_raw(json.dumps(payload, ensure_ascii=False))

    def _stream_openai(self, path: str) -> None:
        """Emit deterministic SSE streams for the streaming tests."""
        self._sse_start()
        if path.startswith("/openai-stream-hold"):
            # Keep the request primed-but-pending long enough to expose any
            # database write lock held across the upstream call.
            time.sleep(6)
        if path.startswith("/openai-stream-empty"):
            self._sse_raw("[DONE]")
            return
        if path.startswith("/openai-stream-reasoning-only"):
            # Thinking-only stream: no content and no tool call, so TTFT is set
            # but TTFA must stay null.
            if not self._sse_json(_stream_chunk(reasoning="thinking hard")):
                return
            if not self._sse_json(
                _stream_chunk(
                    usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                )
            ):
                return
            if not self._sse_json(_stream_chunk(finish="stop")):
                return
            self._sse_raw("[DONE]")
            return
        if path.startswith("/openai-stream-reasoning"):
            if not self._sse_json(_stream_chunk(reasoning="thinking about it")):
                return
            time.sleep(0.2)
            if not self._sse_json(_stream_chunk("hel")):
                return
            time.sleep(0.05)
            if not self._sse_json(_stream_chunk("lo")):
                return
            if not self._sse_json(
                _stream_chunk(
                    usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                )
            ):
                return
            if not self._sse_json(_stream_chunk(finish="stop")):
                return
            self._sse_raw("[DONE]")
            return
        if path.startswith("/openai-stream-parts"):
            # Some OpenAI-compatible providers stream content as typed parts.
            if not self._sse_json(_stream_chunk([{"type": "text", "text": ""}])):
                return
            if not self._sse_json(_stream_chunk([{"type": "text", "text": "你"}, "好"])):
                return
            if not self._sse_json(
                _stream_chunk(
                    usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                )
            ):
                return
            if not self._sse_json(_stream_chunk(finish="stop")):
                return
            self._sse_raw("[DONE]")
            return
        if path.startswith("/openai-stream-tools"):
            # Pure tool-call stream: no visible text, but the tool call itself is
            # the first answer, so TTFA must be set (equal to TTFT).
            if not self._sse_json(
                _stream_chunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": ""},
                        }
                    ]
                )
            ):
                return
            if not self._sse_json(
                _stream_chunk(
                    tool_calls=[{"index": 0, "function": {"arguments": '{"command": "ls"}'}}]
                )
            ):
                return
            if not self._sse_json(_stream_chunk(finish="tool_calls")):
                return
            self._sse_raw("[DONE]")
            return
        if path.startswith("/openai-stream-relayed-thinking"):
            # 思考块 + 正文，但无 usage 块：输出估算必须把已转发的思考计入
            # （ADR-074）。放在默认 "hel" 之前，正文长度完全由本分支控制。
            if not self._sse_json(_stream_chunk(reasoning="think think")):
                return
            if not self._sse_json(_stream_chunk("lo")):
                return
            if not self._sse_json(_stream_chunk(finish="stop")):
                return
            self._sse_raw("[DONE]")
            return
        if not self._sse_json(_stream_chunk("hel")):
            return
        if path.startswith("/openai-stream-finish-slow"):
            # Finish quickly, then hold the connection open. The client can
            # close as soon as it sees finish_reason; that is normal teardown.
            if not self._sse_json(_stream_chunk("lo")):
                return
            if not self._sse_json(
                _stream_chunk(
                    usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                )
            ):
                return
            if not self._sse_json(_stream_chunk(finish="stop")):
                return
            time.sleep(1.5)
            self._sse_raw("[DONE]")
            return
        if path.startswith("/openai-stream-slow"):
            time.sleep(1.5)
        else:
            time.sleep(0.05)
        if path.startswith("/openai-stream-broken"):
            # First byte already sent, then the socket closes without a finish
            # marker or [DONE]: the gateway must report STREAM_ABORTED.
            return
        if path.startswith("/openai-stream-upstream-error"):
            self._sse_json({"error": {"message": "upstream stream exploded"}})
            return
        if not self._sse_json(_stream_chunk("lo")):
            return
        # 内容与收尾之间留 150ms：真实流式的「生成窗口」不该是 0；
        # 否则 throughput_tps 的最小窗口护栏（<100ms 视为单块吐出）会把速率判为无。
        time.sleep(0.15)
        if not path.startswith("/openai-stream-nousage"):
            if not self._sse_json(
                _stream_chunk(
                    usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                )
            ):
                return
        if not self._sse_json(_stream_chunk(finish="stop")):
            return
        self._sse_raw("[DONE]")

    def _record(self, method: str, parsed, body: dict[str, Any] | None = None) -> None:
        self.server.requests.append(
            {
                "method": method,
                "path": parsed.path,
                "headers": dict(self.headers),
                "query": parse_qs(parsed.query),
                "json": body,
            }
        )

    def _authorized(self, path: str, query: dict[str, list[str]]) -> bool:
        key = self.server.good_key
        if path.startswith("/anthropic"):
            return (
                self.headers.get("x-api-key") == key
                and self.headers.get("anthropic-version") == ANTHROPIC_VERSION
            )
        if path.startswith("/gemini"):
            return query.get("key", [""])[0] == key
        if path.startswith("/openai-apikey"):
            return self.headers.get("api-key") == key
        if path.startswith("/openai-xapikey"):
            return self.headers.get("x-api-key") == key
        return self.headers.get("Authorization") == f"Bearer {key}"

    def _failure_prefix(self, path: str) -> bool:
        if path.startswith(("/required-header", "/required-body")):
            if self.headers.get("X-Codegen-Client-Name") != "qagent":
                self._json(403, {"error": {"message": "missing X-Codegen-Client-Name"}})
                return True
        if path.startswith("/all-broken"):
            self._json(500, {"error": {"message": "upstream exploded"}})
            return True
        if path.startswith("/unauthorized"):
            self._json(401, {"error": {"message": "invalid api key"}})
            return True
        if path.startswith("/quota-exhausted"):
            self._json(
                429,
                {
                    "error": {
                        "message": "You exceeded your current quota",
                        "type": "insufficient_quota",
                        "code": "insufficient_quota",
                    }
                },
                headers={"Retry-After": "5"},
            )
            return True
        if path.startswith("/rate-limited"):
            self._json(
                429,
                {"error": {"message": "rate limit exceeded"}},
                headers={"Retry-After": "5"},
            )
            return True
        if path.startswith("/content-policy"):
            self._json(400, {"error": {"message": "blocked by content policy"}})
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        self._record("GET", parsed)

        if path.startswith("/slow"):
            time.sleep(1.5)

        if self._failure_prefix(path):
            return

        if path.startswith("/openai-nomodels"):
            self._json(404, {"error": {"message": "models endpoint is not implemented"}})
            return

        if path.startswith("/empty-models"):
            self._json(200, {"object": "list", "data": []})
            return

        if path.startswith("/broken"):
            self._json(500, {"error": {"message": "models endpoint failed"}})
            return

        if not self._authorized(path, query):
            self._json(401, {"error": {"message": "missing or invalid credentials"}})
            return

        if path.startswith("/openai-typed"):
            self._json(200, _typed_models())
            return
        if path.startswith("/anthropic"):
            self._json(200, _anthropic_models())
            return
        if path.startswith("/gemini"):
            self._json(200, _gemini_models())
            return
        self._json(200, _openai_models())

    def _echo_headers(self, path: str, body: dict[str, Any]) -> None:
        """Echo request headers back; deliberately conflicting response headers.

        Header-passthrough tests read the upstream-visible request headers from
        the JSON body and check that a gateway-owned SSE header (Cache-Control)
        wins over the upstream copy (max-age=999).
        """
        echoed = {name.lower(): value for name, value in self.headers.items()}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("X-Upstream-Trace", "upstream-123")
            self.send_header("X-Upstream-Echo", "stream")
            self.send_header("Cache-Control", "max-age=999")
            self.send_header("x-request-id", "upstream-rid")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if not self._sse_json(_stream_chunk("hi")):
                return
            self._sse_json(_stream_chunk(finish="stop"))
            self._sse_raw("[DONE]")
            return
        self._json(
            200,
            {
                "id": "chatcmpl-echo",
                "object": "chat.completion",
                "model": "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "echoed_headers": echoed,
            },
            headers={
                "Cache-Control": "max-age=999",
                "X-Upstream-Echo": "json",
                "x-request-id": "upstream-rid",
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        body = self._read_json() or {}
        self._record("POST", parsed, body)

        if path.startswith("/echo-headers"):
            self._echo_headers(path, body)
            return

        if path.startswith("/slow"):
            time.sleep(1.5)

        if self._failure_prefix(path):
            return

        if path.startswith("/broken"):
            self._json(500, {"error": {"message": "chat endpoint failed"}})
            return

        if not self._authorized(path, query):
            self._json(401, {"error": {"message": "missing or invalid credentials"}})
            return

        if path.startswith("/required-body") and body.get("client") != "qagent":
            self._json(403, {"error": {"message": "missing body client marker"}})
            return

        if body.get("stream"):
            self._stream_openai(path)
            return

        if path.startswith("/openai-reasoning-chat"):
            self._json(
                200,
                {
                    "id": "chatcmpl-reasoning",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "OK",
                                "reasoning_content": "R" * 40_000,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
            return

        if path.startswith("/openai-empty-chat"):
            self._json(
                200,
                {
                    "id": "chatcmpl-empty",
                    "object": "chat.completion",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": ""}}
                    ],
                },
            )
            return

        if path.startswith("/openai-json-tools"):
            self._json(
                200,
                {
                    "id": "chatcmpl-tools",
                    "object": "chat.completion",
                    "model": body.get("model") or "fake-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "bash", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                },
            )
            return

        if path.startswith("/openai-json-function-call"):
            self._json(
                200,
                {
                    "id": "chatcmpl-function-call",
                    "object": "chat.completion",
                    "model": body.get("model") or "fake-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "function_call": {"name": "bash", "arguments": "{}"},
                            },
                            "finish_reason": "function_call",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                },
            )
            return

        if path.startswith("/openai-no-usage"):
            self._json(200, _openai_chat(body, include_usage=False))
            return

        if path.startswith("/anthropic") and path.endswith("/messages"):
            self._json(200, _anthropic_chat(body))
            return

        if path.startswith("/gemini") and ":generateContent" in path:
            self._json(200, _gemini_chat())
            return

        if path.startswith("/gemini") and ":embedContent" in path:
            self._json(200, _gemini_embedding())
            return

        if path.endswith("/embeddings"):
            self._json(200, _openai_embeddings(body))
            return

        if path.endswith("/rerank"):
            # /openai-ranking 只实现 /ranking 方言：用来验证方言探测与缓存。
            if path.startswith("/openai-ranking"):
                self._json(404, {"error": {"message": "unknown endpoint"}})
                return
            self._json(200, _openai_rerank(body))
            return

        if path.endswith("/ranking"):
            self._json(200, _openai_rerank(body))
            return

        self._json(200, _openai_chat(body))


class FakeUpstreamServer:
    """A real HTTP upstream bound to an ephemeral localhost port."""

    def __init__(self, good_key: str = "channel-secret-key") -> None:
        self.good_key = good_key
        self.requests: list[dict[str, Any]] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.good_key = good_key
        self._httpd.requests = self.requests
        # Teardown used to wait up to the serve_forever default poll interval
        # (0.5 s) per test. A tighter poll plus daemon request threads cuts that
        # to a few milliseconds without changing what the tests observe.
        # PYTEST_NO_FAST_FIXTURES=1 restores the old 0.5 s poll so the saving can
        # be measured in the same time window (A/B, see docs/testing.md).
        slow = bool(os.environ.get("PYTEST_NO_FAST_FIXTURES"))
        self._httpd.daemon_threads = not slow
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.5 if slow else 0.002),
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "FakeUpstreamServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def find(self, method: str, path_suffix: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.requests
            if item["method"] == method and item["path"].endswith(path_suffix)
        ]
