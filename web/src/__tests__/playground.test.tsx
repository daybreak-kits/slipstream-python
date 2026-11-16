import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlaygroundPage } from "@/pages/playground";
import type { CatalogItem } from "@/lib/playground";

const NOW = 1762838400000;
const PUBLIC_NAME = "alpha/gpt-mini";
const OTHER_NAME = "beta/claude-mini";
/** 聊天记录只落在浏览器本地，用独立 key，不碰 lib/storage.ts 的系统键。 */
const HISTORY_KEY = "slipstream.playground.history.v1";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function makeCatalogItem(overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    id: "CM000000000000000000000001",
    name: PUBLIC_NAME,
    channel_id: "CH000000000000000000000001",
    channel_slug: "alpha",
    channel_name: "Alpha",
    protocol: "openai_chat",
    upstream_model: "gpt-mini",
    call_model: "gpt-mini",
    parameters: {
      max_input_tokens: 128000,
      max_output_tokens: 8192,
      reasoning_level: "",
      input_types: ["text"],
      output_types: ["text"],
    },
    input_price_per_1m: 1,
    output_price_per_1m: 2,
    enabled: true,
    created_at: NOW,
    ...overrides,
  };
}

function secondCatalogItem(): CatalogItem {
  return makeCatalogItem({
    id: "CM000000000000000000000002",
    name: OTHER_NAME,
    channel_id: "CH000000000000000000000002",
    channel_slug: "beta",
    channel_name: "Beta",
    upstream_model: "claude-mini",
    call_model: "claude-mini",
  });
}

function sseResponse(
  chunks: string[],
  options: { requestId?: string; signal?: AbortSignal | null; holdOpen?: boolean } = {},
) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      if (options.holdOpen) {
        options.signal?.addEventListener("abort", () => {
          try {
            controller.error(new DOMException("Aborted", "AbortError"));
          } catch {
            // The reader may already be closed by the abort path.
          }
        });
      } else {
        controller.close();
      }
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "X-Request-ID": options.requestId ?? "req_stream_1",
    },
  });
}

function deltaChunk(delta: Record<string, unknown>): string {
  return `data: ${JSON.stringify({ id: "c1", choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`;
}

const FINISH =
  'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n';
const DONE = "data: [DONE]\n\n";

/** 统一的聊天请求 mock：目录 + SSE 回复 + 日志；返回捕获的请求体。 */
function mockStreamFetch(chunks: string[]): unknown[] {
  const bodies: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/catalog") {
        return jsonResponse({ items: [makeCatalogItem()], total: 1 });
      }
      if (url === "/v1/chat/completions" && init?.method === "POST") {
        bodies.push(typeof init.body === "string" ? JSON.parse(init.body) : null);
        return sseResponse(chunks.map((chunk) => chunk));
      }
      if (url.startsWith("/api/logs/recent")) {
        return jsonResponse({ items: [], total: 0 });
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
    }),
  );
  return bodies;
}

const STREAM_CHUNKS = [
  'data: {"id":"c1","model":"alpha/gpt-mini","choices":[{"index":0,"delta":{"role":"assistant","content":"你好"},"finish_reason":null}]}\n\n',
  'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"，世界"},"finish_reason":null}]}\n\n',
  'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
  "data: [DONE]\n\n",
];
/** 拼一段 SSE 文本回复：页面默认走流式，所以普通回复也要按 SSE 返回。 */
function sseTextResponse(text: string, model: string) {
  return sseResponse([
    `data: {"id":"c1","model":"${model}","choices":[{"index":0,"delta":{"role":"assistant","content":${JSON.stringify(text)}},"finish_reason":null}]}\n\n`,
    `data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n`,
    "data: [DONE]\n\n",
  ]);
}

function makeLogPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: "LOG00000000000000000000001",
    request_id: "req_stream_1",
    api_key_label: "demo",
    requested_model: PUBLIC_NAME,
    channel_name: "Alpha",
    upstream_model: "gpt-mini",
    response_model: "gpt-mini",
    http_status: 200,
    error_code: null,
    prompt_tokens: 5,
    completion_tokens: 2,
    cached_tokens: 0,
    cost_usd: 0.000009,
    ttft_ms: 118,
    total_ms: 900,
    throughput_tps: 42.5,
    usage_estimated: false,
    created_at: NOW,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/playground"]}>
        <PlaygroundPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 打开折叠的参数面板（系统提示词 / API 密钥 / 采样参数 / 流式开关都在里面）。 */
async function openParams(user: UserEvent) {
  await user.click(screen.getByRole("button", { name: "参数控制" }));
}

/** 参数面板里的 shadcn Select：先点开触发器再选选项。 */
async function chooseOption(user: UserEvent, triggerName: string, optionName: string | RegExp) {
  await user.click(screen.getByRole("combobox", { name: triggerName }));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

/** 等目录加载完、默认模型选中后再操作。 */
async function waitForModel() {
  await waitFor(() => {
    expect(screen.getByRole("combobox", { name: "选择调用模型" })).toHaveValue(PUBLIC_NAME);
  });
}

describe("playground chat page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("sends one non-stream message and shows response, usage and cost", async () => {
    const calls: Array<{ url: string; method: string; body: unknown; auth: string | null }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const headers = new Headers(init?.headers);
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ url, method, body, auth: headers.get("Authorization") });

      if (url === "/api/catalog") {
        return jsonResponse({ items: [makeCatalogItem()], total: 1 });
      }
      if (url === "/api/logs/recent?limit=20") {
        return jsonResponse({
          items: [
            {
              id: "LOG00000000000000000000001",
              request_id: "req_test_123",
              api_key_label: "demo",
              requested_model: PUBLIC_NAME,
              channel_name: "Alpha",
              upstream_model: "gpt-mini",
              response_model: "gpt-mini",
              http_status: 200,
              error_code: null,
              prompt_tokens: 12,
              completion_tokens: 8,
              cached_tokens: 0,
              cost_usd: 0.000028,
              ttft_ms: null,
              total_ms: 321,
              throughput_tps: null,
              usage_estimated: false,
              created_at: NOW,
            },
          ],
          total: 1,
        });
      }
      if (url === "/v1/chat/completions" && method === "POST") {
        return jsonResponse(
          {
            id: "chatcmpl-test",
            object: "chat.completion",
            model: PUBLIC_NAME,
            choices: [
              {
                index: 0,
                message: { role: "assistant", content: "你好，我是测试模型。" },
                finish_reason: "stop",
              },
            ],
            usage: { prompt_tokens: 12, completion_tokens: 8, total_tokens: 20 },
          },
          200,
          { "X-Request-ID": "req_test_123" },
        );
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();

    localStorage.setItem("system.storage", JSON.stringify({ token: "admin-session-jwt" }));
    await waitForModel();
    await openParams(user);
    await user.type(screen.getByLabelText("API 密钥"), "sk-demo-key");
    await user.click(screen.getByLabelText("流式（SSE）"));
    await user.type(screen.getByLabelText("消息内容"), "打个招呼");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("你好，我是测试模型。")).toBeInTheDocument();
    expect(await screen.findByText("$0.000028")).toBeInTheDocument();
    expect(screen.getByText("0.32s")).toBeInTheDocument();
    expect(screen.getByText("req_test_123")).toBeInTheDocument();

    const chatCall = calls.find((call) => call.url === "/v1/chat/completions");
    expect(chatCall).toBeTruthy();
    expect(chatCall?.auth).toBe("Bearer sk-demo-key");
    expect(chatCall?.body).toMatchObject({
      model: PUBLIC_NAME,
      messages: [{ role: "user", content: "打个招呼" }],
      stream: false,
    });
  });

  it("shows an OpenAI-style error when the key is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem()], total: 1 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          return jsonResponse(
            {
              error: {
                message: "API 密钥无效或已封禁",
                type: "invalid_request_error",
                code: "INVALID_API_KEY",
                request_id: "req_bad_key",
              },
            },
            401,
          );
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    localStorage.setItem("system.storage", JSON.stringify({ token: "admin-session-jwt" }));
    await waitForModel();
    await openParams(user);
    await user.type(screen.getByLabelText("API 密钥"), "sk-expired");
    await user.type(screen.getByLabelText("消息内容"), "打个招呼");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(
      await screen.findByText(/API 密钥无效或已封禁 · \(INVALID_API_KEY\) · 请求编号 req_bad_key/),
    ).toBeInTheDocument();
  });

  it("streams SSE deltas and shows TTFT, total time and throughput", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem()], total: 1 });
        }
        if (url === "/api/logs/recent?limit=20") {
          return jsonResponse({ items: [makeLogPayload()], total: 1 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          bodies.push(typeof init.body === "string" ? JSON.parse(init.body) : null);
          return sseResponse(STREAM_CHUNKS);
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    localStorage.setItem("system.storage", JSON.stringify({ token: "admin-session-jwt" }));
    await waitForModel();
    await user.type(screen.getByLabelText("消息内容"), "打个招呼");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("你好，世界")).toBeInTheDocument();
    expect(await screen.findByText("0.12s")).toBeInTheDocument();
    expect(screen.getByText("0.90s")).toBeInTheDocument();
    expect(screen.getByText("42.5 t/s")).toBeInTheDocument();
    expect(screen.getByText("req_stream_1")).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({ model: PUBLIC_NAME, stream: true });
  });

  it("stops an in-flight stream and keeps the partial response", async () => {
    const captured: { signal: AbortSignal | null } = { signal: null };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem()], total: 1 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          captured.signal = init.signal ?? null;
          return sseResponse([STREAM_CHUNKS[0]], {
            requestId: "req_stream_stop",
            signal: init.signal ?? null,
            holdOpen: true,
          });
        }
        if (url.startsWith("/api/logs/recent")) {
          return jsonResponse({ items: [], total: 0 });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    localStorage.setItem("system.storage", JSON.stringify({ token: "admin-session-jwt" }));
    await waitForModel();
    await user.type(screen.getByLabelText("消息内容"), "讲个笑话");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("你好")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "停止" }));

    expect(await screen.findByText("已停止")).toBeInTheDocument();
    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(captured.signal?.aborted).toBe(true);
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "停止" })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("filters models in the combobox and sends with the picked one", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem(), secondCatalogItem()], total: 2 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          bodies.push(typeof init.body === "string" ? JSON.parse(init.body) : null);
          return sseTextResponse("选好了。", OTHER_NAME);
        }
        if (url.startsWith("/api/logs/recent")) {
          return jsonResponse({ items: [], total: 0 });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    const combobox = screen.getByRole("combobox", { name: "选择调用模型" });
    expect(combobox).toHaveAttribute("aria-expanded", "false");

    await user.click(combobox);
    expect(combobox).toHaveAttribute("aria-expanded", "true");
    expect(within(screen.getByRole("listbox", { name: "模型列表" })).getAllByRole("option")).toHaveLength(2);

    await user.type(combobox, "beta");
    expect(
      within(screen.getByRole("listbox", { name: "模型列表" })).getAllByRole("option"),
    ).toHaveLength(1);

    await user.clear(combobox);
    await user.type(combobox, "zzz");
    expect(screen.getByText("没有匹配的模型")).toBeInTheDocument();

    await user.clear(combobox);
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");
    expect(combobox).toHaveValue(OTHER_NAME);
    expect(combobox).toHaveAttribute("aria-expanded", "false");

    await user.type(screen.getByLabelText("消息内容"), "打个招呼");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("选好了。")).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({ model: OTHER_NAME });
  });

  it("keeps the chat history in the browser and never posts it to the backend", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, method: init?.method ?? "GET" });
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem()], total: 1 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          return sseTextResponse("你好，我是测试模型。", PUBLIC_NAME);
        }
        if (url.startsWith("/api/logs/recent")) {
          return jsonResponse({ items: [], total: 0 });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    const view = renderPage();

    await waitForModel();
    await user.type(screen.getByLabelText("消息内容"), "打个招呼");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("你好，我是测试模型。")).toBeInTheDocument();

    const stored = localStorage.getItem(HISTORY_KEY);
    expect(stored).toBeTruthy();
    const parsed = JSON.parse(stored as string) as Array<{ role: string; content: string }>;
    expect(parsed).toHaveLength(2);
    expect(parsed[0].role).toBe("user");
    expect(parsed[1].role).toBe("assistant");

    const writes = calls.filter((call) => call.method === "POST");
    expect(writes.every((call) => call.url === "/v1/chat/completions")).toBe(true);

    // 重新进入页面：记录从浏览器本地恢复，不走后端。
    view.unmount();
    renderPage();
    expect(await screen.findByText("你好，我是测试模型。")).toBeInTheDocument();
  });

  it("clears the local history after confirmation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem()], total: 1 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          return sseTextResponse("你好，我是测试模型。", PUBLIC_NAME);
        }
        if (url.startsWith("/api/logs/recent")) {
          return jsonResponse({ items: [], total: 0 });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await user.type(screen.getByLabelText("消息内容"), "打个招呼");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("你好，我是测试模型。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除记录" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "清除" }));

    await waitFor(() => {
      expect(screen.queryByText("你好，我是测试模型。")).not.toBeInTheDocument();
    });
    expect(JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]")).toHaveLength(0);
  });

  it("sends every sampling/output field plus extra JSON through the request body", async () => {
    const bodies = mockStreamFetch([deltaChunk({ content: "好" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);
    await user.clear(screen.getByLabelText("temperature"));
    await user.type(screen.getByLabelText("temperature"), "0.5");
    await user.type(screen.getByLabelText("top_p"), "0.9");
    await user.type(screen.getByLabelText("top_k"), "40");
    await user.type(screen.getByLabelText("frequency_penalty"), "0.1");
    await user.type(screen.getByLabelText("presence_penalty"), "-0.2");
    await user.type(screen.getByLabelText("seed"), "7");
    await user.type(screen.getByLabelText("max_tokens"), "256");
    await user.type(screen.getByLabelText("max_completion_tokens"), "128");
    await user.click(screen.getByLabelText("stop"));
    await user.paste("STOP\n###");
    await user.type(screen.getByLabelText("n"), "2");
    await chooseOption(user, "response_format", "json_object");

    await user.click(screen.getByLabelText("logprobs"));
    await user.type(screen.getByLabelText("top_logprobs"), "2");
    await user.type(screen.getByLabelText("user"), "u-1");
    await chooseOption(user, "思考", "reasoning_effort=low");
    await user.click(screen.getByLabelText("额外请求字段（JSON）"));
    await user.paste('{"reasoning_split": true}');

    await user.type(screen.getByLabelText("消息内容"), "参数全传");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("好")).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({
      stream: true,
      temperature: 0.5,
      top_p: 0.9,
      top_k: 40,
      frequency_penalty: 0.1,
      presence_penalty: -0.2,
      seed: 7,
      max_tokens: 256,
      max_completion_tokens: 128,
      stop: ["STOP", "###"],
      n: 2,
      response_format: { type: "json_object" },
      logprobs: true,
      top_logprobs: 2,
      user: "u-1",
      reasoning_effort: "low",
      reasoning_split: true,
    });
  });

  it("rejects max_tokens above the model's registered output limit", async () => {
    const bodies = mockStreamFetch([deltaChunk({ content: "好" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);
    await user.type(screen.getByLabelText("max_tokens"), "9000");

    expect(
      screen.getByText("max_tokens 需是 1–8192 的整数（上限取自所选模型）。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();

    await user.clear(screen.getByLabelText("max_tokens"));
    await user.type(screen.getByLabelText("max_tokens"), "8192");
    expect(screen.queryByText(/上限取自所选模型/)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("消息内容"), "边界值");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("好")).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({ max_tokens: 8192 });
  });

  it("marks reasoning_effort and keeps the thinking block collapsed until expanded", async () => {
    mockStreamFetch([
      deltaChunk({ reasoning_content: "先想一想" }),
      deltaChunk({ content: "答案" }),
      FINISH,
      DONE,
    ]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);
    await chooseOption(user, "思考", "reasoning_effort=low");
    await user.type(screen.getByLabelText("消息内容"), "思考一下");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("答案")).toBeInTheDocument();
    const trigger = await screen.findByRole("button", {
      name: "思考过程 · reasoning_effort=low",
    });
    // 默认折叠：思考正文不可见，展开后才出现。
    expect(screen.queryByText("先想一想")).not.toBeInTheDocument();
    await user.click(trigger);
    expect(await screen.findByText("先想一想")).toBeInTheDocument();
  });

  it.each([
    { thinking: "thinking", label: "thinking=on" },
    { thinking: "enable_thinking", label: "enable_thinking" },
    { thinking: "off", label: "think" },
  ])(
    "marks vendor thinking switch %s as %s",
    async ({ thinking, label }) => {
      mockStreamFetch([
        deltaChunk({ reasoning_content: "模型自己的思考" }),
        deltaChunk({ content: "好" }),
        FINISH,
        DONE,
      ]);
      const user = userEvent.setup();
      renderPage();

      await waitForModel();
      await openParams(user);
      if (thinking !== "off") {
        await chooseOption(
          user,
          "思考",
          thinking === "thinking" ? /thinking\.enabled/ : /enable_thinking/,
        );
      }
      await user.type(screen.getByLabelText("消息内容"), "打个招呼");
      await user.click(screen.getByRole("button", { name: "发送" }));

      expect(await screen.findByText("好")).toBeInTheDocument();
      expect(
        await screen.findByRole("button", { name: "思考过程 · " + label }),
      ).toBeInTheDocument();
    },
  );

  it("splits inline think tags across chunks and marks them as think 标签", async () => {
    mockStreamFetch([
      deltaChunk({ content: "<thi" }),
      deltaChunk({ content: "nk>推理过程</think" }),
      deltaChunk({ content: ">正文答案" }),
      FINISH,
      DONE,
    ]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await user.type(screen.getByLabelText("消息内容"), "用标签回");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("正文答案")).toBeInTheDocument();
    expect(screen.queryByText(/<thi/)).not.toBeInTheDocument();
    const trigger = await screen.findByRole("button", { name: "思考过程 · think 标签" });
    await user.click(trigger);
    expect(await screen.findByText("推理过程")).toBeInTheDocument();
  });

  it("shows an explicit 无思考块 marker when the upstream returns no thinking", async () => {
    mockStreamFetch([deltaChunk({ content: "普通回复" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await user.type(screen.getByLabelText("消息内容"), "不用思考");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("普通回复")).toBeInTheDocument();
    expect(screen.getByText("无思考块")).toBeInTheDocument();
  });

  it("normalizes non-stream reasoning_content out of the answer text", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({ items: [makeCatalogItem()], total: 1 });
        }
        if (url === "/v1/chat/completions" && init?.method === "POST") {
          bodies.push(typeof init.body === "string" ? JSON.parse(init.body) : null);
          return jsonResponse({
            id: "chatcmpl-think",
            object: "chat.completion",
            model: PUBLIC_NAME,
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
                  content: "最终答案",
                  reasoning_content: "非流式思考",
                },
                finish_reason: "stop",
              },
            ],
            usage: { prompt_tokens: 4, completion_tokens: 6, total_tokens: 10 },
          });
        }
        if (url.startsWith("/api/logs/recent")) return jsonResponse({ items: [], total: 0 });
        return jsonResponse({ code: "NOT_FOUND" }, 404);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);
    await user.click(screen.getByLabelText("流式（SSE）"));
    await user.type(screen.getByLabelText("消息内容"), "非流式问题");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("最终答案")).toBeInTheDocument();
    expect(screen.queryByText("非流式思考")).not.toBeInTheDocument();
    const trigger = await screen.findByRole("button", { name: "思考过程 · think" });
    await user.click(trigger);
    expect(await screen.findByText("非流式思考")).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({ stream: false });
  });

  it("reveals each parameter's range and usage by hovering its label", async () => {
    mockStreamFetch([deltaChunk({ content: "好" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);

    // 说明不再常驻 label 下方：只有悬停参数名时才出现 tooltip（sr-only 副本供读屏，不算可见文案）。
    expect(
      screen.queryByText("0.0–2.0 · 越高输出越随机，越低越确定", { ignore: ".sr-only" }),
    ).not.toBeInTheDocument();
    const temperatureLabel = screen.getByText("temperature");
    expect(temperatureLabel).toHaveClass("cursor-help");
    expect(temperatureLabel).toHaveAttribute("aria-describedby", "playground-temperature-hint");
    await user.hover(temperatureLabel);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "0.0–2.0 · 越高输出越随机，越低越确定",
    );

    // 一屏多列：采样/输出/高级都是 6 列网格；长内容字段跨 2 列而不是独占整行。
    expect(screen.getByLabelText("temperature").closest(".grid")).toHaveClass(
      "xl:grid-cols-6",
    );
    expect(screen.getByLabelText("系统提示词").closest("[data-slot=field]")).toHaveClass(
      "sm:col-span-2",
    );
    expect(screen.getByLabelText("stop").closest("[data-slot=field]")).toHaveClass(
      "sm:col-span-2",
    );
  });

  it("truncates a long read-only value and shows the full text in a tooltip", async () => {
    const longLevel = "official-reasoning-level-with-a-very-long-registered-label";
    const base = makeCatalogItem();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/catalog") {
          return jsonResponse({
            items: [
              makeCatalogItem({
                parameters: { ...base.parameters, reasoning_level: longLevel },
              }),
            ],
            total: 1,
          });
        }
        if (url.startsWith("/api/logs/recent")) {
          return jsonResponse({ items: [], total: 0 });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);

    const value = screen.getByLabelText("渠道思考等级（只读）");
    // 单行省略号：外层定高，内层文本 truncate；悬停看全文。
    expect(value).toHaveClass("min-w-0");
    expect(value.querySelector(".truncate")).not.toBeNull();
    await user.hover(value);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(longLevel);
  });

  it("shows the advanced group's parameters in the same dense grid with hover hints", async () => {
    mockStreamFetch([deltaChunk({ content: "好" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);

    expect(
      screen.queryByText("0–20 · 返回前 k 个候选词的概率；需先打开 logprobs", {
        ignore: ".sr-only",
      }),
    ).not.toBeInTheDocument();
    const topLogprobsLabel = screen.getByText("top_logprobs");
    expect(topLogprobsLabel).toHaveClass("cursor-help");
    await user.hover(topLogprobsLabel);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "0–20 · 返回前 k 个候选词的概率",
    );

    // 长字段的说明同样挂在参数名上：隐藏副本供 aria-describedby，悬停即可读。
    expect(
      screen.getByLabelText("额外请求字段（JSON）").closest("[data-slot=field]"),
    ).toHaveTextContent("顶层 JSON 对象 · 厂商私有字段原样合并进请求体，后写覆盖先写");
    expect(
      screen.getByLabelText("额外请求字段（JSON）").closest("[data-slot=field]"),
    ).toHaveClass("sm:col-span-2");
  });

  it("keeps all four parameter groups collapsed by default with a caret on every header", async () => {
    mockStreamFetch([deltaChunk({ content: "好" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);

    // 四组统一：标题即折叠按钮、带 caret、默认全展开（高级参数不再需要先点开）。
    for (const name of ["系统", "采样", "输出", "高级参数"]) {
      const header = screen.getByRole("button", { name });
      expect(header).toHaveAttribute("aria-expanded", "true");
      expect(header.querySelector("svg")).not.toBeNull();
    }
    expect(screen.getByLabelText("logprobs")).toBeInTheDocument();

    // 折叠只收起本组控件，展开后输入过的值仍在。
    await user.click(screen.getByRole("button", { name: "输出" }));
    expect(screen.getByRole("button", { name: "输出" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByLabelText("max_tokens")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "输出" }));
    expect(screen.getByLabelText("max_tokens")).toBeInTheDocument();
  });

  it("exposes the full value of a possibly clipped select through its title", async () => {
    mockStreamFetch([deltaChunk({ content: "好" }), FINISH, DONE]);
    const user = userEvent.setup();
    renderPage();

    await waitForModel();
    await openParams(user);

    // 触发器被列宽截断时，title 提供完整选中项（思考选项文案较长）。
    const thinking = screen.getByRole("combobox", { name: "思考" });
    expect(thinking).toHaveAttribute("title", "不指定（沿用渠道默认）");
    expect(thinking.closest("[data-slot=field]")).toHaveClass("sm:col-span-2");
    await chooseOption(user, "思考", "reasoning_effort=low");
    expect(screen.getByRole("combobox", { name: "思考" })).toHaveAttribute(
      "title",
      "reasoning_effort=low",
    );

    const responseFormat = screen.getByRole("combobox", { name: "response_format" });
    expect(responseFormat).toHaveAttribute("title", "text（默认）");
    await chooseOption(user, "response_format", "json_object");
    expect(screen.getByRole("combobox", { name: "response_format" })).toHaveAttribute(
      "title",
      "json_object",
    );
  });
});
