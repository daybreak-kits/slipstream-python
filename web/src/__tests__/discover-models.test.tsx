import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryRouter, Route, Routes } from "react-router-dom";

import { DiscoverModelsDialog } from "@/components/channels/discover-models-dialog";
import { ChannelDetailPage } from "@/pages/channel-detail";
import type { Channel } from "@/lib/channels";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
  return { queryClient, ...result };
}

describe("discover models dialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("selects the new models and writes only the checked ones", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/discover-models")) {
          return jsonResponse({
            status: "ok",
            models: [
              { upstream_model: "auto", already_added: false },
              { upstream_model: "QGPT-MoE-latest", already_added: false },
              { upstream_model: "deepseek-flash", already_added: true },
            ],
            protocol: "openai_chat",
            auth_style: "bearer",
            http_status: 200,
            latency_ms: 42,
            message: "OpenAI 兼容 · /models 返回 3 个模型",
          });
        }
        if (url.endsWith("/api/channels/CH1/models/bulk")) {
          return jsonResponse({
            added: 1,
            skipped: 0,
            models: [],
          }, 201);
        }
        throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onAdded = vi.fn();
    const onOpenChange = vi.fn();

    renderWithClient(
      <DiscoverModelsDialog
        open
        onOpenChange={onOpenChange}
        channelId="CH1"
        input={{
          channel_id: "CH1",
          connection_url: "https://upstream.example.com/v1",
          protocol: "openai_chat",
          rewrite_rules: {
            request_headers: [
              { mode: "set", path: "X-Codegen-Client-Name", value: "qagent" },
            ],
          },
        }}
        onAdded={onAdded}
      />,
    );

    const auto = await screen.findByRole("checkbox", { name: "选择模型 auto" });
    await waitFor(() => expect(auto).toBeChecked());
    expect(
      screen.getByRole("checkbox", { name: "选择模型 QGPT-MoE-latest" }),
    ).toBeChecked();
    const existing = screen.getByRole("checkbox", {
      name: "选择模型 deepseek-flash",
    });
    expect(existing).toBeChecked();
    expect(existing).toBeDisabled();

    await user.click(
      screen.getByRole("checkbox", { name: "选择模型 QGPT-MoE-latest" }),
    );
    await user.click(screen.getByRole("button", { name: "添加 1 个模型" }));

    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
    expect(onOpenChange).toHaveBeenCalledWith(false);

    const discoverCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/channels/discover-models"),
    );
    expect(discoverCall).toBeTruthy();
    const discoverBody = JSON.parse(String(discoverCall?.[1]?.body ?? "{}"));
    expect(discoverBody.channel_id).toBe("CH1");
    expect(discoverBody.rewrite_rules.request_headers[0].path).toBe(
      "X-Codegen-Client-Name",
    );

    const bulkCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/channels/CH1/models/bulk"),
    );
    expect(bulkCall).toBeTruthy();
    expect(JSON.parse(String(bulkCall?.[1]?.body ?? "{}"))).toEqual({
      upstream_models: ["auto"],
    });
  });
  const statusOk = (models: { upstream_model: string; already_added: boolean }[]) => ({
    status: "ok",
    models,
    protocol: "openai_chat",
    auth_style: "bearer",
    http_status: 200,
    latency_ms: 30,
    message: `OpenAI 兼容 · /models 返回 ${models.length} 个模型`,
  });

  const threeModels = [
    { upstream_model: "vendor/alpha", already_added: false },
    { upstream_model: "vendor/beta", already_added: false },
    { upstream_model: "standalone-model", already_added: false },
  ];

  it("filters models by name and keeps the checked set across filtering", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/channels/discover-models")) {
        return jsonResponse(statusOk(threeModels));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(
      <DiscoverModelsDialog
        open
        onOpenChange={vi.fn()}
        channelId="CH1"
        input={{
          channel_id: "CH1",
          connection_url: "https://upstream.example.com/v1",
          protocol: "openai_chat",
        }}
      />,
    );

    const standalone = await screen.findByRole("checkbox", {
      name: "选择模型 standalone-model",
    });
    await waitFor(() => expect(standalone).toBeChecked());

    await user.type(screen.getByLabelText("搜索模型"), "VENDOR");

    expect(
      screen.getByRole("checkbox", { name: "选择模型 vendor/alpha" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "选择模型 vendor/beta" }),
    ).toBeChecked();
    expect(
      screen.queryByRole("checkbox", { name: "选择模型 standalone-model" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清空搜索" }));

    expect(
      await screen.findByRole("checkbox", { name: "选择模型 standalone-model" }),
    ).toBeChecked();
  });

  it("groups models by the prefix before the first slash and keeps the checked set", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/channels/discover-models")) {
        return jsonResponse(statusOk(threeModels));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(
      <DiscoverModelsDialog
        open
        onOpenChange={vi.fn()}
        channelId="CH1"
        input={{ channel_id: "CH1", connection_url: "https://upstream.example.com/v1" }}
      />,
    );

    const alpha = await screen.findByRole("checkbox", {
      name: "选择模型 vendor/alpha",
    });
    await waitFor(() => expect(alpha).toBeChecked());

    await user.click(screen.getByRole("button", { name: "按供应商分组" }));

    const vendorGroup = screen.getByRole("button", {
      name: "vendor 分组，共 2 个模型",
    });
    expect(vendorGroup).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("button", { name: "其他 分组，共 1 个模型" }),
    ).toBeInTheDocument();

    await user.click(vendorGroup);

    expect(
      screen.queryByRole("checkbox", { name: "选择模型 vendor/alpha" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "选择模型 standalone-model" }),
    ).toBeChecked();

    await user.click(
      screen.getByRole("button", { name: "vendor 分组，共 2 个模型" }),
    );
    expect(
      await screen.findByRole("checkbox", { name: "选择模型 vendor/alpha" }),
    ).toBeChecked();
    expect(
      screen.getByRole("button", { name: "平铺展示" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("creates the channel before bulk-adding when opened from an unsaved draft", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/discover-models")) {
          return jsonResponse(
            statusOk([
              { upstream_model: "vendor/alpha", already_added: false },
              { upstream_model: "vendor/beta", already_added: false },
            ]),
          );
        }
        if (url.endsWith("/api/channels/CH2/models/bulk")) {
          return jsonResponse({ created: 2, skipped: 0, errors: [], items: [] }, 201);
        }
        throw new Error(`unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onCreateChannel = vi.fn(
      async () => ({ id: "CH2" }) as unknown as Channel,
    );
    const onAdded = vi.fn();
    const onOpenChange = vi.fn();

    renderWithClient(
      <DiscoverModelsDialog
        open
        onOpenChange={onOpenChange}
        input={{ connection_url: "https://draft.example.com/v1" }}
        onCreateChannel={onCreateChannel}
        onAdded={onAdded}
      />,
    );

    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "选择模型 vendor/alpha" }),
      ).toBeChecked(),
    );

    await user.click(screen.getByRole("button", { name: "添加 2 个模型" }));

    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
    expect(onCreateChannel).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);

    const bulkCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/channels/CH2/models/bulk"),
    );
    expect(JSON.parse(String(bulkCall?.[1]?.body ?? "{}"))).toEqual({
      upstream_models: ["vendor/alpha", "vendor/beta"],
    });
  });

  it("keeps the checked models when creating the channel fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/channels/discover-models")) {
        return jsonResponse(
          statusOk([{ upstream_model: "vendor/alpha", already_added: false }]),
        );
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onCreateChannel = vi.fn(async () => {
      throw new Error("请先补全渠道名称与连接地址");
    });

    renderWithClient(
      <DiscoverModelsDialog
        open
        onOpenChange={vi.fn()}
        input={{ connection_url: "https://draft.example.com/v1" }}
        onCreateChannel={onCreateChannel}
      />,
    );

    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "选择模型 vendor/alpha" }),
      ).toBeChecked(),
    );

    await user.click(screen.getByRole("button", { name: "添加 1 个模型" }));

    expect(
      await screen.findByText("创建渠道失败：请先补全渠道名称与连接地址"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "选择模型 vendor/alpha" }),
    ).toBeChecked();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/models/bulk")),
    ).toBe(false);
  });

  it("invalidates catalog-entries and implicit-groups after a bulk add", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/channels/discover-models")) {
        return jsonResponse(
          statusOk([{ upstream_model: "vendor/new-model", already_added: false }]),
        );
      }
      if (url.endsWith("/api/channels/CH1/models/bulk")) {
        return jsonResponse({ added: 1, skipped: 0, models: [] }, 201);
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onAdded = vi.fn();
    const { queryClient } = renderWithClient(
      <DiscoverModelsDialog
        open
        onOpenChange={vi.fn()}
        channelId="CH1"
        input={{
          channel_id: "CH1",
          connection_url: "https://upstream.example.com/v1",
          protocol: "openai_chat",
        }}
        onAdded={onAdded}
      />,
    );
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const checkbox = await screen.findByRole("checkbox", {
      name: "选择模型 vendor/new-model",
    });
    await waitFor(() => expect(checkbox).toBeChecked());
    await user.click(screen.getByRole("button", { name: "添加 1 个模型" }));

    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
    const keys = invalidateSpy.mock.calls.map((call) =>
      JSON.stringify((call[0] as { queryKey?: unknown } | undefined)?.queryKey),
    );
    expect(keys).toContain(JSON.stringify(["catalog-entries"]));
    expect(keys).toContain(JSON.stringify(["implicit-groups"]));
  });
});

function renderChannelPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/channels/new"]}>
        <Routes>
          <Route path="/channels/new" element={<ChannelDetailPage />} />
          <Route path="/channels/:channelId" element={<span>渠道详情</span>} />
          <Route path="/channels" element={<span>渠道列表</span>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const discoveredModels = [
  { upstream_model: "vendor/alpha", already_added: false },
  { upstream_model: "vendor/beta", already_added: false },
];

function discoveredOk() {
  return {
    status: "ok",
    models: discoveredModels,
    protocol: "openai_chat",
    auth_style: "bearer",
    http_status: 200,
    latency_ms: 30,
    message: "OpenAI 兼容 · /models 返回 2 个模型",
  };
}

describe("discovering models from a keyless draft", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks before requesting upstream when the draft carries no key", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      throw new Error("unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderChannelPage();

    await user.type(
      screen.getByLabelText("连接地址"),
      "https://upstream.example.com/v1",
    );
    await user.click(screen.getByRole("button", { name: "获取上游模型" }));

    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("这个渠道还没有密钥")).toBeInTheDocument();
    expect(screen.getByText(/不是密钥失效/)).toBeInTheDocument();
    expect(screen.getByText(/先在「渠道密钥」里粘贴密钥/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requests upstream without an api_key once the operator continues anonymously", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/discover-models")) {
          return jsonResponse(discoveredOk());
        }
        throw new Error("unexpected fetch: " + url);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderChannelPage();
    await user.type(
      screen.getByLabelText("连接地址"),
      "https://upstream.example.com/v1",
    );
    await user.click(screen.getByRole("button", { name: "获取上游模型" }));
    await user.click(
      await screen.findByRole("button", { name: "仍然匿名尝试" }),
    );

    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "选择模型 vendor/alpha" }),
      ).toBeChecked(),
    );
    // 对话框里提前说明这次没带密钥，别让人把 401 读成「密钥不对」。
    expect(screen.getByText(/本次请求没有携带密钥/)).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/channels/discover-models"),
    );
    const body = JSON.parse(String(call?.[1]?.body ?? "{}"));
    expect(body.connection_url).toBe("https://upstream.example.com/v1");
    expect(body.api_key).toBeUndefined();
    expect(body.channel_id).toBeUndefined();
  });

  it("skips the prompt when a key is already typed into the draft", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/discover-models")) {
          return jsonResponse(discoveredOk());
        }
        throw new Error("unexpected fetch: " + url);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderChannelPage();
    await user.type(
      screen.getByLabelText("连接地址"),
      "https://upstream.example.com/v1",
    );
    await user.type(screen.getByLabelText("新密钥"), "sk-live-123");
    await user.click(screen.getByRole("button", { name: "添加" }));
    await user.click(screen.getByRole("button", { name: "获取上游模型" }));

    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "选择模型 vendor/alpha" }),
      ).toBeChecked(),
    );
    expect(screen.queryByText("这个渠道还没有密钥")).not.toBeInTheDocument();
    expect(screen.queryByText(/本次请求没有携带密钥/)).not.toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/channels/discover-models"),
    );
    expect(JSON.parse(String(call?.[1]?.body ?? "{}")).api_key).toBe(
      "sk-live-123",
    );
  });
});
