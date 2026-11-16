import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RewriteRulesEditor } from "@/components/channels/rewrite-rules-editor";
import type { Channel, RewriteRules } from "@/lib/channels";

import { chooseSelect } from "./select-helpers";

const NOW = 1762838400000;
const CHANNEL_ID = "CH000000000000000000000001";

function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: CHANNEL_ID,
    slug: "alpha",
    name: "Alpha",
    note: "",
    connection_url: "https://alpha.example.com/v1",
    endpoint_preview: "https://alpha.example.com/v1/chat/completions",
    models_endpoint_preview: "https://alpha.example.com/v1/models",
    api_key_set: true,
    api_key_count: 1,
    api_key_masks: ["••••••-key"],
    api_keys: [
      {
        id: "CK000000000000000000000001",
        label: "默认密钥",
        masked: "••••••-key",
        enabled: true,
        last_used_at: null,
        created_at: NOW,
      },
    ],
    api_key_masked: "••••••key",
    protocol: "openai_chat",
    protocol_source: "manual",
    auth_style: "bearer",
    priority: 1,
    enabled: true,
    connection_status: "ok",
    connection_latency_ms: 120,
    connection_message: "连接正常",
    connection_tested_at: NOW,
    last_model_probe_latency_ms: null,
    last_model_probe_at: null,
    last_model_probe_status: "unknown",
    override_sections: [],
    rewrite_rules: {},
    model_count: 1,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setupFetch() {
  const calls: Array<{ method: string; body: unknown }> = [];
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, body });
    const rules = (body as { rewrite_rules?: RewriteRules } | null)?.rewrite_rules ?? {};
    return jsonResponse(makeChannel({ rewrite_rules: rules }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderEditor(rules: RewriteRules = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RewriteRulesEditor channelId={CHANNEL_ID} rules={rules} />
    </QueryClientProvider>,
  );
}

describe("rewrite rules editor", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds a request header rule and saves it", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor();

    await user.click(screen.getByRole("button", { name: "添加规则" }));
    await user.type(screen.getByLabelText("规则 1 备注"), "租户标记");
    await user.type(screen.getByLabelText("规则 1 路径"), "X-Tenant");
    await user.type(screen.getByLabelText("规则 1 值"), "acme");
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("PATCH");
    expect(calls[0].body).toEqual({
      rewrite_rules: {
        request_headers: [{ note: "租户标记", mode: "set", path: "X-Tenant", value: "acme" }],
      },
    });
    expect(await screen.findByText("规则已保存。")).toBeInTheDocument();
  });

  // 这条用例要依次驱动三个 Select 与多组输入，单跑约 7.6s；整仓并行跑时曾超过默认 30s，故显式放宽。
  it("configures a body copy rule with AND/OR conditions", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor();

    await user.click(screen.getByRole("button", { name: /请求体/ }));
    await user.click(screen.getByRole("button", { name: "添加规则" }));
    await chooseSelect(user, { triggerName: "规则 1 模式", optionName: "复制字段" });
    await user.type(screen.getByLabelText("规则 1 源路径"), "messages.0.content");
    await user.type(screen.getByLabelText("规则 1 目标路径"), "messages.-1.content");

    await user.click(screen.getByRole("button", { name: "添加条件" }));
    await chooseSelect(user, { triggerName: "条件 1 模式", optionName: "大于等于" });
    await user.type(screen.getByLabelText("条件 1 值"), "0.5");
    await user.click(screen.getByLabelText("条件 1 取反"));
    await user.click(screen.getByLabelText("条件 1 缺失通过"));

    await user.click(screen.getByRole("button", { name: "添加条件" }));
    await user.clear(screen.getByLabelText("条件 2 路径"));
    await user.type(screen.getByLabelText("条件 2 路径"), "messages.0.content");
    await chooseSelect(user, { triggerName: "条件 2 模式", optionName: "正则匹配" });
    await user.type(screen.getByLabelText("条件 2 值"), "hello");

    await chooseSelect(user, { triggerName: "规则 1 条件逻辑", optionName: "任一满足（OR）" });
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({
      rewrite_rules: {
        request_body: [
          {
            mode: "copy",
            from: "messages.0.content",
            to: "messages.-1.content",
            logic: "OR",
            conditions: [
              {
                path: "model",
                mode: "gte",
                value: 0.5,
                invert: true,
                pass_missing_key: true,
              },
              { path: "messages.0.content", mode: "regex", value: "hello" },
            ],
          },
        ],
      },
    });
  }, 60000);

  it("removes a rule before saving", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor({
      request_headers: [{ mode: "set", path: "X-Tenant", value: "acme" }],
    });

    expect(screen.getByLabelText("规则 1 路径")).toHaveValue("X-Tenant");
    await user.click(screen.getByRole("button", { name: "删除规则 1" }));
    expect(screen.getByText("这个作用段还没有规则。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存规则" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ rewrite_rules: { request_headers: [] } });
  });

  it("configures response header set rules", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor();

    await user.click(screen.getByRole("button", { name: /响应头/ }));
    await user.click(screen.getByRole("button", { name: "添加规则" }));
    await user.type(screen.getByLabelText("规则 1 路径"), "X-Gateway");
    await user.type(screen.getByLabelText("规则 1 值"), "slipstream");
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({
      rewrite_rules: {
        response_headers: [{ mode: "set", path: "X-Gateway", value: "slipstream" }],
      },
    });
  });

  it("configures response body rules and explains streaming skip", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor();

    await user.click(screen.getByRole("button", { name: /响应体/ }));
    expect(screen.getByText(/流式请求跳过响应体改写/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "添加规则" }));
    await user.type(screen.getByLabelText("规则 1 路径"), "choices.0.message.content");
    await user.type(screen.getByLabelText("规则 1 值"), "rewritten");
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({
      rewrite_rules: {
        response_body: [
          { mode: "create", path: "choices.0.message.content", value: "rewritten" },
        ],
      },
    });
  });

  it("blocks credential headers without calling the API", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor();

    await user.click(screen.getByRole("button", { name: "添加规则" }));
    await user.type(screen.getByLabelText("规则 1 路径"), "Authorization");
    await user.type(screen.getByLabelText("规则 1 值"), "evil");
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    expect(await screen.findByText(/凭证类请求头 Authorization/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("blocks transport response headers without calling the API", async () => {
    const user = userEvent.setup();
    const calls = setupFetch();
    renderEditor();

    await user.click(screen.getByRole("button", { name: /响应头/ }));
    await user.click(screen.getByRole("button", { name: "添加规则" }));
    await user.type(screen.getByLabelText("规则 1 路径"), "Content-Length");
    await user.type(screen.getByLabelText("规则 1 值"), "1");
    await user.click(screen.getByRole("button", { name: "保存规则" }));

    expect(await screen.findByText(/传输类响应头 content-length/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });
});
