import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChannelFormDialog } from "@/components/channels/channel-form-dialog";
import { ChannelsPage } from "@/pages/channels";
import type { Channel } from "@/lib/channels";

import { chooseSelect, expectSelectText } from "./select-helpers";

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
    note: "主力渠道",
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
    api_key_masked: "••••••-key",
    protocol: "openai_chat",
    protocol_source: "manual",
    auth_style: "bearer",
    priority: 1,
    enabled: true,
    connection_status: "ok",
    connection_latency_ms: 120,
    connection_message: "连接正常，发现 2 个模型",
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

function renderWithClient(ui: ReactElement, initialEntry = "/channels") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("channels list", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state with a create action", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [], total: 0 })),
    );

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("还没有渠道")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新建第一个渠道" })).toBeInTheDocument();
  });

  it("renders channel rows with status labels, model count and timestamps", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          items: [
            makeChannel({ last_model_probe_status: "ok" }),
            makeChannel({
              id: "CH000000000000000000000002",
              slug: "beta",
              name: "Beta",
              protocol: "anthropic_messages",
              connection_status: "timeout",
              last_model_probe_status: "timeout",
              model_count: 5,
              enabled: false,
            }),
          ],
          total: 2,
        }),
      ),
    );

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("beta")).toBeInTheDocument();
    expect(within(table).getByText("正常运行")).toBeInTheDocument();
    expect(within(table).getByText("连接超时")).toBeInTheDocument();
    expect(within(table).getByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("共 2 个渠道")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "封禁 Alpha" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "启用 Beta" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByLabelText("全选渠道")).not.toBeChecked();
    expect(screen.getByLabelText("选择 Alpha")).not.toBeChecked();
  });

  it("shows probe latency and override markers, and saves priority inline", async () => {
    const alpha = makeChannel({
      last_model_probe_latency_ms: 850,
      last_model_probe_at: NOW,
      last_model_probe_status: "ok",
      override_sections: ["request_headers", "request_body"],
    });
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "PATCH") {
          const body = JSON.parse(String(init.body));
          return jsonResponse({ ...alpha, priority: body.priority });
        }
        return jsonResponse({ items: [alpha], total: 1 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("0.85s")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Alpha 参数覆盖：请求头、请求体"),
    ).toBeInTheDocument();

    const priority = screen.getByLabelText("优先级 Alpha");
    expect(priority).toHaveValue(1);
    await user.clear(priority);
    await user.type(priority, "7");
    await user.tab();

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(patch).toBeDefined();
      expect(String(patch?.[0])).toBe("/api/channels/CH000000000000000000000001");
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ priority: 7 });
    });
  });

  it("batch-disables the selected channels through the single-channel API", async () => {
    const channels = [
      makeChannel(),
      makeChannel({
        id: "CH000000000000000000000002",
        slug: "beta",
        name: "Beta",
        enabled: false,
      }),
    ];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return jsonResponse({ ...channels[0], enabled: body.enabled });
      }
      return jsonResponse({ items: channels, total: 2 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    await user.click(screen.getByLabelText("选择 Alpha"));
    expect(screen.getByLabelText("选择 Alpha")).toBeChecked();
    expect(screen.getByText(/已选 1 个渠道/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "批量封禁" }));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([url, init]) =>
          init?.method === "PATCH" &&
          String(url) === "/api/channels/CH000000000000000000000001",
      );
      expect(patch).toBeDefined();
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ enabled: false });
    });
    await waitFor(() =>
      expect(screen.getByLabelText("选择 Alpha")).not.toBeChecked(),
    );
  });

  it("batch-probes the first model of every selected channel", async () => {
    const channel = makeChannel();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/channels/CH000000000000000000000001/models") {
        return jsonResponse({ items: [{ id: "CM000000000000000000000001" }], total: 1 });
      }
      if (url === "/api/channel-models/CM000000000000000000000001/probe") {
        return jsonResponse({ status: "ok" });
      }
      if (init?.method) return jsonResponse({});
      return jsonResponse({ items: [channel], total: 1 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    await user.click(screen.getByLabelText("全选渠道"));
    expect(screen.getByLabelText("选择 Alpha")).toBeChecked();
    await user.click(screen.getByRole("button", { name: "批量测速" }));

    await waitFor(() => {
      const probe = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/probe"));
      expect(probe).toBeDefined();
      expect(JSON.parse(String(probe?.[1]?.body))).toEqual({ mode: "shallow" });
    });
  });

  it("batch-deletes the selected channels after confirmation", async () => {
    const channels = [
      makeChannel(),
      makeChannel({
        id: "CH000000000000000000000002",
        slug: "beta",
        name: "Beta",
      }),
    ];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return jsonResponse({ items: channels, total: 2 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    await user.click(screen.getByLabelText("选择 Alpha"));
    await user.click(screen.getByLabelText("选择 Beta"));
    await user.click(screen.getByRole("button", { name: "批量删除" }));

    // 危险确认统一 alertdialog 语义 + 问句标题（ui.md §2.3，CRUD-R1/R2）
    const batchDialog = await screen.findByRole("alertdialog");
    expect(
      screen.getByRole("alertdialog", { name: "删除选中的 2 个渠道？" }),
    ).toBeInTheDocument();
    expect(batchDialog).toHaveTextContent(
      "渠道下的模型会一并删除；任一模型被组合引用时，该渠道会被拒绝删除，其余渠道继续执行。",
    );
    await user.click(within(batchDialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      const deletes = fetchMock.mock.calls
        .filter(([, init]) => init?.method === "DELETE")
        .map(([url]) => String(url));
      expect(deletes).toEqual([
        "/api/channels/CH000000000000000000000001",
        "/api/channels/CH000000000000000000000002",
      ]);
    });
  });

  // CRUD-R1 实证：渠道删除确认此前是 role=dialog（第二套实现），必须统一为 alertdialog。
  it("single channel delete confirm is an alertdialog with question title and cascade notice", async () => {
    const channels = [makeChannel()];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return jsonResponse({ items: channels, total: 1 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(<ChannelsPage />);

    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "删除 Alpha" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(
      screen.getByRole("alertdialog", { name: "删除渠道 Alpha？" }),
    ).toBeInTheDocument();
    // 级联说明原样保留（spec 用户故事 9）
    expect(dialog).toHaveTextContent(
      "渠道下的模型会一并删除；如果任一模型已被组合引用，删除会被拒绝，请先解除组合引用。",
    );
    expect(within(dialog).getByRole("button", { name: "取消" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      const deletes = fetchMock.mock.calls
        .filter(([, init]) => init?.method === "DELETE")
        .map(([url]) => String(url));
      expect(deletes).toEqual(["/api/channels/CH000000000000000000000001"]);
    });
  });
});

describe("channel form", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("blocks an empty submit and never calls the API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithClient(
      <ChannelFormDialog open onOpenChange={() => undefined} />,
    );

    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("请输入渠道名称")).toBeInTheDocument();
    expect(await screen.findByText("请输入连接地址")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("previews the exact endpoint and trims values before creating", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse(
        makeChannel({
          name: body.name,
          slug: body.slug,
          connection_url: body.connection_url,
          protocol: body.protocol,
          priority: body.priority,
          model_count: 0,
          connection_status: "unknown",
        }),
        201,
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    renderWithClient(
      <ChannelFormDialog open onOpenChange={onOpenChange} />,
    );

    await user.type(screen.getByLabelText("渠道名称"), "  My Channel  ");
    await user.type(screen.getByLabelText("连接地址"), "  https://api.example.com/custom#  ");
    await user.type(screen.getByLabelText("渠道标识"), "  my-channel  ");
    await user.type(screen.getByLabelText("渠道密钥"), "  secret-key-value  ");
    await chooseSelect(user, { triggerName: "协议", optionName: "OpenAI 兼容" });
    expectSelectText("协议", "OpenAI 兼容");

    expect(await screen.findByText("精确端点（不拼接路径）")).toBeInTheDocument();
    expect(
      screen.getByText(/\/models：https:\/\/api\.example\.com\/custom$/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/channels");
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({
      name: "My Channel",
      slug: "my-channel",
      connection_url: "https://api.example.com/custom#",
      api_key: "secret-key-value",
      protocol: "openai_chat",
      enabled: true,
    });

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("saves in auto mode without probing and closes the dialog", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      return jsonResponse(
        makeChannel({
          name: body.name,
          slug: body.slug,
          connection_url: body.connection_url,
          protocol: "openai_chat",
          protocol_source: "auto",
          model_count: 0,
          connection_status: "unknown",
        }),
        201,
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const onCreated = vi.fn();

    renderWithClient(
      <ChannelFormDialog open onOpenChange={onOpenChange} onCreated={onCreated} />,
    );

    await user.type(screen.getByLabelText("渠道名称"), "Auto Channel");
    await user.type(screen.getByLabelText("连接地址"), "https://api.example.com/v1");
    await user.type(screen.getByLabelText("渠道标识"), "auto-channel");
    await user.type(screen.getByLabelText("渠道密钥"), "secret-key-value");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/channels");
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("test-connection");
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.protocol).toBeUndefined();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onCreated).toHaveBeenCalledWith(
      expect.objectContaining({ id: "CH000000000000000000000001" }),
    );
  });
});