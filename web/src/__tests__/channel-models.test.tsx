import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChannelModelsPanel } from "@/components/channels/channel-models-panel";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import type { ChannelModel, ProbeAllResult, ProbeResult } from "@/lib/channel-models";

const NOW = 1762838400000;
const CHANNEL_ID = "CH000000000000000000000001";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeModel(overrides: Partial<ChannelModel> = {}): ChannelModel {
  return {
    id: "CM000000000000000000000001",
    channel_id: CHANNEL_ID,
    upstream_model: "alpha-model",
    call_model: "alpha",
    parameters: {
      max_input_tokens: 128000,
      max_output_tokens: 8192,
      reasoning_level: "",
      input_types: ["text"],
      output_types: ["text"],
    },
    input_price_per_1m: 0.5,
    output_price_per_1m: 1.5,
    cache_read_price_per_1m: null,
    cache_write_price_per_1m: null,
    enabled: true,
    health_status: "normal",
    visible_status: "normal",
    consecutive_failures: 0,
    consecutive_trip_count: 0,
    escalation_trip_count: 0,
    opened_at: null,
    cooldown_until: null,
    fault_until: null,
    next_retry_at: null,
    schedule: { version: 2, timezone: "Asia/Shanghai", rules: [] },
    cooldown: {
      failure_threshold: 5,
      cooldown_s: 30,
      rate_limit_cooldown_s: 5,
      half_open_max: 1,
      escalate_mode: "after_n",
      escalate_after: 3,
      fault_cooldown_s: 300,
    },
    probe_status: "unknown",
    probe_default: false,
    model_type: "chat",
    model_type_source: "default",
    probe_latency_ms: null,
    probe_message: "",
    probed_at: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function renderPanel() {
  toast.dismiss();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Toaster />
        <ChannelModelsPanel channelId={CHANNEL_ID} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function setupFetch(models: ChannelModel[]) {
  const state = { models: [...models] };
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    calls.push({ url, method, body });

    if (method === "GET" && url === `/api/channels/${CHANNEL_ID}/models`) {
      return jsonResponse({ items: state.models, total: state.models.length });
    }
    if (method === "POST" && url === `/api/channels/${CHANNEL_ID}/models`) {
      const created = makeModel({
        id: "CM000000000000000000000003",
        upstream_model: body.upstream_model,
        call_model: body.call_model,
        parameters: body.parameters,
        schedule: body.schedule ?? { version: 2, timezone: "Asia/Shanghai", rules: [] },
      });
      state.models = [...state.models, created];
      return jsonResponse(created, 201);
    }
    if (method === "POST" && url.endsWith("/probe")) {
      const model = state.models.find((item) => url.includes(item.id)) ?? state.models[0];
      const result: ProbeResult = {
        model_id: model.id,
        channel_id: CHANNEL_ID,
        upstream_model: model.upstream_model,
        call_model: model.call_model,
        status: "ok",
        latency_ms: 42,
        message: "探测成功：OpenAI 兼容 返回「ping」",
        probed_at: NOW,
        mode: body.mode,
        protocol: "openai_chat",
        auth_style: "bearer",
        http_status: 200,
      };
      state.models = state.models.map((item) =>
        item.id === model.id
          ? { ...item, probe_status: "ok", probe_latency_ms: 42, probed_at: NOW }
          : item,
      );
      return jsonResponse(result);
    }
    if (method === "POST" && url.endsWith("/reset")) {
      const model = state.models.find((item) => url.includes(item.id)) ?? state.models[0];
      const reset = {
        ...model,
        health_status: "normal" as const,
        visible_status: "normal" as const,
        consecutive_failures: 0,
        consecutive_trip_count: 0,
        escalation_trip_count: 0,
        opened_at: null,
        cooldown_until: null,
        fault_until: null,
        next_retry_at: null,
      };
      state.models = state.models.map((item) => (item.id === reset.id ? reset : item));
      return jsonResponse(reset);
    }
    if (method === "PATCH" && url.startsWith("/api/channel-models/")) {
      const model = state.models.find((item) => url.includes(item.id)) ?? state.models[0];
      const updated = {
        ...model,
        enabled: body.enabled ?? model.enabled,
        visible_status:
          (body.enabled ?? model.enabled) && model.health_status === "normal"
            ? ("normal" as const)
            : model.visible_status,
      };
      state.models = state.models.map((item) => (item.id === updated.id ? updated : item));
      return jsonResponse(updated);
    }
    if (method === "POST" && url === `/api/channels/${CHANNEL_ID}/probe-all`) {
      state.models = state.models.map((item, index) => ({
        ...item,
        probe_status: index === 0 ? "ok" : "timeout",
        probe_latency_ms: 30 + index,
        probed_at: NOW,
      }));
      const result: ProbeAllResult = {
        channel_id: CHANNEL_ID,
        mode: body.mode,
        total: state.models.length,
        ok: 1,
        timeout: state.models.length - 1,
        error: 0,
        unknown: 0,
        items: [],
        protocol: "openai_chat",
        auth_style: "bearer",
        protocol_source: "auto",
      };
      return jsonResponse(result);
    }
    if (method === "POST" && url.endsWith("/probe-default")) {
      const target = state.models.find((item) => url.includes(item.id)) ?? state.models[0];
      state.models = state.models.map((item) => ({
        ...item,
        probe_default: item.id === target.id,
      }));
      return jsonResponse(target);
    }
    return jsonResponse({ code: "NOT_FOUND", message: "not found", request_id: "r" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, state };
}

describe("channel models panel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the mapping table with status badges and counts", async () => {
    setupFetch([
      makeModel({ probe_status: "ok", probe_latency_ms: 120, probed_at: NOW }),
      makeModel({
        id: "CM000000000000000000000002",
        upstream_model: "beta-model",
        call_model: "beta",
        probe_status: "error",
        probe_message: "上游返回 HTTP 500",
      }),
    ]);

    renderPanel();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("alpha-model")).toBeInTheDocument();
    expect(within(table).getByText("beta")).toBeInTheDocument();
    expect(within(table).getByText("正常运行")).toBeInTheDocument();
    expect(within(table).getByText("探测失败")).toBeInTheDocument();
    expect(within(table).getAllByText((_, element) => element?.textContent?.includes("128,000") ?? false).length).toBeGreaterThan(0);
    expect(within(table).getAllByText((_, element) => (element?.textContent?.includes("$0.5") && element?.textContent?.includes("$1.5")) ?? false).length).toBeGreaterThan(0);
  });

  it("switches the default probe model from the table radio", async () => {
    const { calls } = setupFetch([
      makeModel({ probe_default: true }),
      makeModel({
        id: "CM000000000000000000000002",
        upstream_model: "beta-model",
        call_model: "beta",
      }),
    ]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");

    const alphaRadio = screen.getByRole("radio", { name: "将 alpha-model 设为默认探测模型" });
    expect(alphaRadio).toBeChecked();
    const betaRadio = screen.getByRole("radio", { name: "将 beta-model 设为默认探测模型" });
    expect(betaRadio).not.toBeChecked();

    await user.click(betaRadio);

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/probe-default"),
      );
      expect(posted?.url).toBe("/api/channel-models/CM000000000000000000000002/probe-default");
    });
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "将 beta-model 设为默认探测模型" })).toBeChecked(),
    );
  });

  it("creates a model and defaults call_model to the upstream name", async () => {
    const { calls } = setupFetch([makeModel()]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "添加模型" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("上游模型名"), "gamma-model");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url === `/api/channels/${CHANNEL_ID}/models`,
      );
      expect(posted).toBeTruthy();
      expect(posted?.body).toMatchObject({
        upstream_model: "gamma-model",
        call_model: "gamma-model",
        parameters: expect.objectContaining({
          input_types: ["text"],
          output_types: ["text"],
        }),
      });
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("edits v2 schedule rules in the configured IANA timezone", async () => {
    const { calls } = setupFetch([makeModel()]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "添加模型" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("上游模型名"), "scheduled-model");

    // 时区走可搜索下拉，不能手打；搜索并选中 America/New_York
    await user.click(within(dialog).getByRole("combobox", { name: "IANA 时区" }));
    await user.type(screen.getByPlaceholderText("搜索时区，如 Tokyo / Europe"), "New_York");
    await user.click(await screen.findByRole("option", { name: /America\/New_York/ }));

    expect(within(dialog).getByText(/浏览器时区/)).toBeInTheDocument();
    // 新建模型默认零规则（全天可用）；加一条默认是每天 22:00–02:00，跨天要显式提示
    expect(within(dialog).getByText(/当前未设置规则，该模型全天可用/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "添加规则" }));
    expect(within(dialog).getByText(/跨天：结束落在次日/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("combobox", { name: "第 1 条规则的类型" }));
    await user.click(await screen.findByRole("option", { name: "每周" }));
    fireEvent.change(within(dialog).getByLabelText("第 1 条规则的开始时刻"), {
      target: { value: "09:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("第 1 条规则的结束时刻"), {
      target: { value: "18:00" },
    });
    expect(
      within(dialog).getByText(/当前规则：每周 周一、周二、周三、周四、周五 09:00–18:00/),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url === `/api/channels/${CHANNEL_ID}/models`,
      );
      expect(posted?.body).toMatchObject({
        upstream_model: "scheduled-model",
        schedule: {
          version: 2,
          timezone: "America/New_York",
          rules: [{ kind: "weekly", days: [1, 2, 3, 4, 5], start: "09:00", end: "18:00" }],
        },
      });
    });
  });

  it("shows a v2 schedule summary in the mapping table, including crossing midnight", async () => {
    setupFetch([
      makeModel({
        schedule: {
          version: 2,
          timezone: "America/New_York",
          rules: [{ kind: "weekly", days: [1, 3], start: "23:00", end: "08:00" }],
        },
      }),
    ]);

    renderPanel();
    await screen.findByRole("table");
    expect(
      screen.getByText(/America\/New_York · 每周 周一、周三 23:00–08:00（跨天）/),
    ).toBeInTheDocument();
  });

  it("renders legacy v1 segments by folding them into v2 rules", async () => {
    setupFetch([
      makeModel({
        schedule: {
          timezone: "Asia/Shanghai",
          segments: [{ days: [6, 7], start: "01:00", end: "06:00" }],
        } as unknown as ChannelModel["schedule"],
      }),
    ]);

    renderPanel();
    await screen.findByRole("table");
    expect(screen.getByText(/Asia\/Shanghai · 每周 周六、周日 01:00–06:00/)).toBeInTheDocument();
  });

  it("probes one model using the selected 模型拉取探测 mode and refreshes the row", async () => {
    const { calls } = setupFetch([makeModel()]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");

    await user.click(screen.getByRole("radio", { name: "模型拉取探测" }));
    await user.click(screen.getByRole("button", { name: "探测 alpha-model" }));

    await waitFor(() => {
      const posted = calls.find((call) => call.method === "POST" && call.url.endsWith("/probe"));
      expect(posted?.body).toEqual({ mode: "shallow" });
    });
    expect(await screen.findByText(/探测成功：OpenAI 兼容/)).toBeInTheDocument();
    expect(screen.queryByRole("alert", { name: /探测成功/ })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByText("正常运行").length).toBeGreaterThan(0),
    );
  });

  it("offers ban/enable per state and confirms before acting", async () => {
    const { calls } = setupFetch([
      makeModel({
        id: "CM000000000000000000000010",
        upstream_model: "healthy-model",
        call_model: "healthy",
      }),
      makeModel({
        id: "CM000000000000000000000011",
        upstream_model: "fault-model",
        call_model: "fault",
        health_status: "fault",
        visible_status: "fault",
        consecutive_failures: 5,
        consecutive_trip_count: 3,
        escalation_trip_count: 3,
        fault_until: NOW + 60_000,
        next_retry_at: NOW + 60_000,
      }),
      makeModel({
        id: "CM000000000000000000000012",
        upstream_model: "off-model",
        call_model: "off",
        enabled: false,
        visible_status: "disabled",
      }),
    ]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");
    expect(screen.getByRole("button", { name: "封禁 healthy-model" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用 fault-model" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用 off-model" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "封禁 healthy-model" }));
    expect(screen.getByRole("heading", { name: "封禁模型" })).toBeInTheDocument();
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
    await user.click(screen.getByRole("button", { name: "确认封禁" }));
    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            (call.body as { enabled?: boolean } | null)?.enabled === false,
        ),
      ).toBe(true);
    });

    await user.click(screen.getByRole("button", { name: "启用 fault-model" }));
    await user.click(screen.getByRole("button", { name: "确认启用" }));
    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.method === "POST" && call.url.endsWith("/reset"),
        ),
      ).toBe(true);
    });

    await user.click(screen.getByRole("button", { name: "启用 off-model" }));
    await user.click(screen.getByRole("button", { name: "确认启用" }));
    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            (call.body as { enabled?: boolean } | null)?.enabled === true,
        ),
      ).toBe(true);
    });
  });

  it("probes all models and shows the success/failure summary", async () => {
    const { calls } = setupFetch([
      makeModel({ probe_status: "unknown" }),
      makeModel({
        id: "CM000000000000000000000002",
        upstream_model: "beta-model",
        call_model: "beta",
        probe_status: "unknown",
      }),
    ]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "全部探测" }));

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url === `/api/channels/${CHANNEL_ID}/probe-all`,
      );
      expect(posted?.body).toEqual({ mode: "deep" });
    });
    expect(await screen.findByText(/共 2 个：成功 1，超时 1/)).toBeInTheDocument();
    expect(screen.queryByText(/全部探测完成/)).toBeInTheDocument();
  });

  it("shows configured cache prices and the input-price fallback", async () => {
    setupFetch([
      makeModel(),
      makeModel({
        id: "CM000000000000000000000002",
        upstream_model: "cached-model",
        call_model: "cached",
        cache_read_price_per_1m: 0.05,
        cache_write_price_per_1m: 0.15,
      }),
    ]);

    renderPanel();
    const table = await screen.findByRole("table");

    expect(within(table).getByText("缓存按输入价")).toBeInTheDocument();
    expect(within(table).getByText("缓存读 $0.05 / 写 $0.15")).toBeInTheDocument();
  });

  it("submits cache prices from the model form", async () => {
    const { calls } = setupFetch([makeModel()]);
    const user = userEvent.setup();

    renderPanel();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "添加模型" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("上游模型名"), "cache-model");
    await user.type(within(dialog).getByLabelText("缓存读取单价 / 1M"), "0.03");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url === `/api/channels/${CHANNEL_ID}/models`,
      );
      expect(posted?.body).toMatchObject({
        cache_read_price_per_1m: 0.03,
        cache_write_price_per_1m: null,
      });
    });
  });
});
