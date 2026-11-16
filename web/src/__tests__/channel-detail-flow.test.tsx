import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { ChannelDetailPage } from "@/pages/channel-detail";
import { ChannelsPage } from "@/pages/channels";
import type { Channel } from "@/lib/channels";

const NOW = 1762838400000;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: "CH000000000000000000000001",
    slug: "alpha",
    name: "Alpha",
    note: "",
    connection_url: "https://alpha.example.com/v1",
    endpoint_preview: "https://alpha.example.com/v1/chat/completions",
    models_endpoint_preview: "https://alpha.example.com/v1/models",
    api_key_set: true,
    api_key_count: 2,
    api_key_masks: ["••••••1111", "••••••2222"],
    api_keys: [
      {
        id: "CK000000000000000000000001",
        label: "主密钥",
        masked: "••••••1111",
        enabled: true,
        last_used_at: null,
        created_at: NOW,
      },
      {
        id: "CK000000000000000000000002",
        label: "备用",
        masked: "••••••2222",
        enabled: true,
        last_used_at: null,
        created_at: NOW,
      },
    ],
    api_key_masked: "••••••1111",
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
    model_count: 2,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location">{location.pathname + location.search}</span>;
}

function renderApp(ui: React.ReactNode) {
  toast.dismiss();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Toaster />
      <MemoryRouter initialEntries={["/channels"]}>
        <Routes>
          <Route
            path="/channels"
            element={
              <>
                {ui}
                <LocationProbe />
              </>
            }
          />
          <Route path="/channels/new" element={<ChannelDetailPage />} />
          <Route
            path="/channels/:channelId"
            element={
              <>
                <LocationProbe />
                <span>渠道详情占位</span>
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("channel list key badge and test action", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows key count with masked tails and routes the test action to routing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [makeChannel()], total: 1 })),
    );
    const user = userEvent.setup();

    renderApp(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    const badge = screen.getByLabelText(
      "Alpha 共 2 个密钥：••••••1111、••••••2222",
    );
    expect(badge).toHaveAttribute(
      "title",
      "2 个密钥\n••••••1111\n••••••2222",
    );

    await user.click(screen.getByRole("button", { name: "测试连接 Alpha" }));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/channels/CH000000000000000000000001?tab=routing",
      ),
    );
  });
});

describe("new channel draft flow", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("tests and creates with unsaved keys from the live form", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/test-connection")) {
          return jsonResponse({
            status: "ok",
            latency_ms: 42,
            http_status: 200,
            message: "连接成功",
            model_count: 2,
            models_preview: ["fake-model", "fake-model-2"],
            protocol: "openai_chat",
            auth_style: "bearer",
            tested_at: NOW,
          });
        }
        if (url === "/api/channels" && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return jsonResponse(
            makeChannel({
              id: "CH000000000000000000000009",
              name: body.name,
              slug: body.slug,
              connection_url: body.connection_url,
            }),
            201,
          );
        }
        return jsonResponse({ items: [], total: 0 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApp(<Link to="/channels/new">新建第一个渠道</Link>);
    await user.click(screen.getByRole("link", { name: "新建第一个渠道" }));

    expect(await screen.findByText("新建渠道")).toBeInTheDocument();
    await user.type(screen.getByLabelText("渠道名称"), "Draft Channel");
    await user.type(
      screen.getByLabelText("连接地址"),
      "https://draft.example.com/v1",
    );
    await user.type(screen.getByLabelText("新密钥"), "sk-new-1234");
    await user.click(screen.getByRole("button", { name: "添加" }));

    expect(await screen.findByText("••••••1234")).toBeInTheDocument();
    expect(screen.getByText("待保存")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "测试连接" })[0]);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/api/channels/test-connection"),
        ),
      ).toBe(true),
    );
    const testCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/api/channels/test-connection"),
    );
    const testBody = JSON.parse(String(testCall?.[1]?.body));
    expect(testBody).toMatchObject({
      connection_url: "https://draft.example.com/v1",
      api_key: "sk-new-1234",
    });
    expect(testBody).not.toHaveProperty("channel_id");
    expect(await screen.findByText("连接测试通过")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "创建渠道" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/channels")).toBe(true));
    const createCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/api/channels" && init?.method === "POST",
    );
    const createBody = JSON.parse(String(createCall?.[1]?.body));
    expect(createBody).toMatchObject({
      name: "Draft Channel",
      connection_url: "https://draft.example.com/v1",
      enabled: true,
    });
    expect(createBody.api_keys).toEqual([
      { label: "", api_key: "sk-new-1234" },
    ]);
    expect(await screen.findByText("渠道已创建")).toBeInTheDocument();
  });

  it("batch-tests pasted keys and keeps only passing keys after confirmation", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/test-connection")) {
          const body = JSON.parse(String(init?.body));
          const ok = String(body.api_key).includes("pass");
          return jsonResponse({
            status: ok ? "ok" : "error",
            latency_ms: ok ? 35 : null,
            http_status: ok ? 200 : 401,
            message: ok ? "连接成功" : "密钥无效",
            model_count: ok ? 1 : 0,
            models_preview: ok ? ["fake-model"] : [],
            protocol: "openai_chat",
            auth_style: "bearer",
            tested_at: NOW,
          });
        }
        return jsonResponse({ items: [], total: 0 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApp(<Link to="/channels/new">新建第一个渠道</Link>);
    await user.click(screen.getByRole("link", { name: "新建第一个渠道" }));

    expect(await screen.findByText("新建渠道")).toBeInTheDocument();
    await user.type(screen.getByLabelText("渠道名称"), "Batch Channel");
    await user.type(
      screen.getByLabelText("连接地址"),
      "https://batch.example.com/v1",
    );

    await user.click(screen.getByRole("button", { name: "批量模式" }));
    await user.type(
      screen.getByLabelText("批量密钥"),
      "sk-pass-1111\nsk-fail-2222\nsk-pass-3333",
    );
    await user.click(screen.getByRole("button", { name: "检测并保存" }));

    expect(
      await screen.findByText("清除检测失败的密钥？"),
    ).toBeInTheDocument();
    expect(
      // 描述是一整段（失败数 + 通过数 + 处置说明），按片段匹配。
      screen.getByText(/共有 1 个密钥检测失败，2 个通过。/),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "清除失败并保存通过项" }),
    );

    expect(await screen.findByText("••••••1111")).toBeInTheDocument();
    expect(screen.getByText("••••••3333")).toBeInTheDocument();
    expect(screen.queryByText("••••••2222")).not.toBeInTheDocument();
    expect(screen.getByText("共 2 个")).toBeInTheDocument();

    const testedKeys = fetchMock.mock.calls
      .filter(([url]) => String(url).endsWith("/api/channels/test-connection"))
      .map(([, init]) => JSON.parse(String(init?.body)).api_key);
    expect(testedKeys).toEqual(["sk-pass-1111", "sk-fail-2222", "sk-pass-3333"]);
  });

  it("creates the channel from the discover dialog before bulk-adding models", async () => {
    const createdId = "CH000000000000000000000009";
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/discover-models")) {
          return jsonResponse({
            status: "ok",
            models: [
              { upstream_model: "vendor/alpha", already_added: false },
              { upstream_model: "vendor/beta", already_added: false },
            ],
            protocol: "openai_chat",
            auth_style: "bearer",
            http_status: 200,
            latency_ms: 24,
            message: "OpenAI 兼容 · /models 返回 2 个模型",
          });
        }
        if (url === "/api/channels" && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          return jsonResponse(
            makeChannel({
              id: createdId,
              name: body.name,
              slug: body.slug,
              connection_url: body.connection_url,
            }),
            201,
          );
        }
        if (url.endsWith(`/api/channels/${createdId}/models/bulk`)) {
          return jsonResponse(
            { created: 2, skipped: 0, errors: [], items: [] },
            201,
          );
        }
        return jsonResponse({ items: [], total: 0 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApp(<Link to="/channels/new">新建第一个渠道</Link>);
    await user.click(screen.getByRole("link", { name: "新建第一个渠道" }));
    expect(await screen.findByText("新建渠道")).toBeInTheDocument();

    await user.type(screen.getByLabelText("渠道名称"), "Dialog Channel");
    await user.type(
      screen.getByLabelText("连接地址"),
      "https://dialog.example.com/v1",
    );
    await user.click(screen.getByRole("button", { name: "获取上游模型" }));
    // 这条用例模拟的是「没有密钥的渠道靠匿名访问发现模型」：页面会先弹确认框，
    // 点「仍然匿名尝试」是产品刻意保留的正常路径，不是要删掉那个确认框。
    await user.click(
      await screen.findByRole("button", { name: "仍然匿名尝试" }),
    );

    const alpha = await screen.findByRole("checkbox", {
      name: "选择模型 vendor/alpha",
    });
    await waitFor(() => expect(alpha).toBeChecked());
    await user.click(screen.getByRole("button", { name: "添加 2 个模型" }));

    expect(await screen.findByText("渠道详情占位")).toBeInTheDocument();
    expect(await screen.findByText("模型已加入路由")).toBeInTheDocument();

    const createIndex = fetchMock.mock.calls.findIndex(
      ([url, init]) =>
        String(url) === "/api/channels" && init?.method === "POST",
    );
    const bulkIndex = fetchMock.mock.calls.findIndex(([url]) =>
      String(url).endsWith(`/api/channels/${createdId}/models/bulk`),
    );
    expect(createIndex).toBeGreaterThanOrEqual(0);
    expect(bulkIndex).toBeGreaterThan(createIndex);

    const createBody = JSON.parse(
      String(fetchMock.mock.calls[createIndex]?.[1]?.body),
    );
    expect(createBody).toMatchObject({
      name: "Dialog Channel",
      connection_url: "https://dialog.example.com/v1",
      enabled: true,
    });
    expect(createBody).not.toHaveProperty("api_keys");
  });
});
