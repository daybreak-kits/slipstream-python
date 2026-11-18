import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Combo } from "@/lib/combos";
import type { ImplicitGroup } from "@/lib/groups";
import type { CatalogEntry } from "@/lib/playground";
import { CombosRedirect, ModelsPage } from "@/pages/models";

import { chooseSelect } from "./select-helpers";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function catalogItem(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: "CM000000000000000000000001",
    kind: "direct",
    status: "ok",
    name: "channel-a/A",
    channel_id: "CH000000000000000000000001",
    channel_slug: "channel-a",
    channel_name: "主渠道",
    protocol: "openai_chat",
    upstream_model: "gpt-4o",
    call_model: "A",
    parameters: {
      max_input_tokens: 128000,
      max_output_tokens: 8192,
      reasoning_level: "",
      input_types: ["text"],
      output_types: ["text"],
    },
    input_price_per_1m: 1,
    output_price_per_1m: 2,
    cache_read_price_per_1m: 0.5,
    enabled: true,
    created_at: 1762838400000,
    ...overrides,
  };
}

function comboMember(overrides: Partial<Combo["members"][number]> = {}): Combo["members"][number] {
  return {
    id: "CT000000000000000000000001",
    target_type: "channel_model",
    priority: 1,
    enabled: true,
    available: true,
    channel_model_id: "CM000000000000000000000001",
    child_combo_id: null,
    channel_id: "CH000000000000000000000001",
    channel_slug: "channel-a",
    channel_name: "主渠道",
    call_model: "A",
    upstream_model: "gpt-4o",
    child_combo_name: "",
    child_combo_enabled: false,
    protocol: "openai_chat",
    created_at: 1762838400000,
    ...overrides,
  };
}

function combo(overrides: Partial<Combo> = {}): Combo {
  return {
    id: "CB000000000000000000000001",
    name: "team-chat",
    description: "主备组合",
    enabled: true,
    max_attempts: 3,
    route_strategy: "",
    route_window_minutes: 0,
    failover_rules: { rules: [], default_action: "next" },
    member_count: 2,
    available_count: 2,
    depth: 2,
    max_depth: 3,
    callable: true,
    created_at: 1762838400000,
    updated_at: 1762838400000,
    members: [
      comboMember({ priority: 2 }),
      comboMember({
        id: "CT000000000000000000000002",
        target_type: "child_combo",
        priority: 1,
        channel_model_id: null,
        child_combo_id: "CB000000000000000000000002",
        channel_id: null,
        channel_slug: "",
        channel_name: "",
        call_model: "",
        upstream_model: "",
        child_combo_name: "backup-child",
        child_combo_enabled: true,
      }),
    ],
    ...overrides,
  };
}

function backupChildCombo(): Combo {
  return combo({
    id: "CB000000000000000000000002",
    name: "backup-child",
    description: "备用子组合",
    depth: 1,
    member_count: 0,
    available_count: 1,
    members: [],
  });
}

function manualEntry(item: Combo): CatalogEntry {
  return catalogItem({
    id: item.id,
    name: item.name,
    kind: "combo",
    origin: "manual",
    status: item.enabled ? "enabled" : "disabled",
    channel_id: "",
    channel_slug: "",
    channel_name: "",
    protocol: "",
    upstream_model: "",
    call_model: "",
    enabled: item.enabled,
  });
}

function groupMember(overrides: Partial<ImplicitGroup["members"][number]> = {}) {
  return {
    channel_model_id: "CM000000000000000000000001",
    channel_id: "CH000000000000000000000001",
    channel_slug: "channel-a",
    channel_name: "主渠道",
    protocol: "openai_chat",
    upstream_model: "gpt-4o",
    call_model: "A",
    priority: 1,
    enabled: true,
    channel_enabled: true,
    available: true,
    ...overrides,
  };
}

function group(overrides: Partial<ImplicitGroup> = {}): ImplicitGroup {
  return {
    name: "auto-pool",
    call_model: "auto-pool",
    member_count: 2,
    available_count: 2,
    callable: true,
    created_at: 1762838400000,
    members: [
      groupMember({ priority: 3 }),
      groupMember({
        channel_model_id: "CM000000000000000000000002",
        channel_id: "CH000000000000000000000002",
        channel_slug: "newapi",
        channel_name: "备用渠道",
        upstream_model: "gpt-4o-mini",
        call_model: "B",
        priority: 2,
      }),
    ],
    ...overrides,
  };
}

function autoEntry(item: ImplicitGroup): CatalogEntry {
  return catalogItem({
    id: "group:" + item.name,
    name: item.name,
    kind: "combo",
    origin: "auto",
    status: "enabled",
    channel_id: "",
    channel_slug: "",
    channel_name: "",
    protocol: "",
    upstream_model: "",
    call_model: item.call_model,
  });
}

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location" data-state={JSON.stringify(location.state ?? null)}>
      {location.pathname + location.search}
    </div>
  );
}

interface RecordedCall {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

function bodyOf(init?: RequestInit): Record<string, unknown> | null {
  if (!init?.body) return null;
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

/**
 * 目录接口 + 组合/分组接口的最小桩：默认把传入的 combos / groups 映射成对应目录条目，
 * 这样页面计数、行内操作与组合标记都能在同一个夹具上断言。
 */
function setupFetch(
  state: Partial<{ groups: ImplicitGroup[]; combos: Combo[]; catalog: CatalogEntry[] }> = {},
) {
  const groups = state.groups ?? [];
  const combos = state.combos ?? [];
  const catalog =
    state.catalog ?? [catalogItem(), ...combos.map(manualEntry), ...groups.map(autoEntry)];
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "GET" && url.startsWith("/api/catalog")) {
      return jsonResponse({ items: catalog, total: catalog.length });
    }
    if (method === "GET" && url === "/api/combos") {
      return jsonResponse({ items: combos, total: combos.length });
    }
    if (method === "GET" && url === "/api/groups") {
      return jsonResponse({ items: groups, total: groups.length });
    }
    if (method === "POST" && url === "/api/combos") {
      const body = bodyOf(init);
      calls.push({ method, url, body });
      return jsonResponse(combo({ id: "CB000000000000000000000009", name: String(body?.name ?? "created"), members: [] }), 201);
    }
    if (method === "PATCH" && url.startsWith("/api/combos/")) {
      calls.push({ method, url, body: bodyOf(init) });
      return jsonResponse(combo());
    }
    if (method === "DELETE" && url.startsWith("/api/combos/")) {
      calls.push({ method, url, body: null });
      return jsonResponse({ ok: true });
    }
    if (method === "PATCH" && url.startsWith("/api/channel-models/")) {
      calls.push({ method, url, body: bodyOf(init) });
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function renderPage(
  initialEntries: Array<string | { pathname: string; search?: string; state?: unknown }> = [
    "/catalog",
  ],
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/catalog" element={<ModelsPage />} />
          <Route path="/combos" element={<CombosRedirect />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 等目录首屏加载完（标题 + 表格）。 */
async function renderCatalog() {
  renderPage();
  await screen.findByRole("heading", { name: "模型管理" });
  return await screen.findByRole("table");
}

describe("model management page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders direct, manual and auto entries on one page without legacy tabs or columns", async () => {
    setupFetch({ combos: [combo()], groups: [group()] });
    const table = await renderCatalog();

    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByText(/组合模型/)).toBeNull();

    const headers = within(table)
      .getAllByRole("columnheader")
      .map((header) => header.textContent?.trim());
    // 第一列是行选择框（无标题），名称/状态两列可排序。
    expect(headers).toEqual(["", "名称", "渠道", "协议", "价格", "状态", "操作"]);

    expect(screen.getByText(/1 个直连 · 1 个手动组合 · 1 个自动组合/)).toBeInTheDocument();

    // 直连行：加入组合 + 封禁
    expect(screen.getByRole("button", { name: "将 channel-a/A 加入组合" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "封禁 channel-a/A" })).toBeInTheDocument();
    // 手动组合行：编辑 / 删除
    expect(screen.getByRole("button", { name: "编辑 team-chat" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除 team-chat" })).toBeInTheDocument();
    // 自动组合行：只读（实时派生语义移入 title 提示）
    expect(screen.getByText("只读")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑 auto-pool" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除 auto-pool" })).toBeNull();

    // 价格合并成一列：输入 / 输出 / 缓存命中
    expect(screen.getByText(/入 \$1\.00 \/ 出 \$2\.00 \/ 缓存 \$0\.50/)).toBeInTheDocument();
  });

  it("labels combo rows with their priority layers and member hints", async () => {
    setupFetch({ combos: [combo()], groups: [group()] });
    await renderCatalog();

    expect(
      screen.getByLabelText("手动组合，优先级 P2 channel-a/A / P1 backup-child"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("自动组合，优先级 P3 channel-a/A / P2 newapi/B"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/直连，优先级/)).toBeNull();
  });

  it("rewrites the legacy tab parameter into the combo filter", async () => {
    setupFetch({ combos: [combo()], groups: [group()] });
    renderPage(["/catalog?tab=combos"]);

    await screen.findByRole("heading", { name: "模型管理" });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/catalog?kind=combo"),
    );
    expect(screen.getByRole("button", { name: "编辑 team-chat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "将 channel-a/A 加入组合" })).toBeNull();
  });

  it("filters to manual combos from the kind query parameter", async () => {
    setupFetch({ combos: [combo()], groups: [group()] });
    renderPage(["/catalog?kind=manual"]);
    await screen.findByRole("heading", { name: "模型管理" });

    expect(await screen.findByRole("button", { name: "编辑 team-chat" })).toBeInTheDocument();
    expect(screen.queryByText("只读")).toBeNull();
    expect(screen.queryByRole("button", { name: "将 channel-a/A 加入组合" })).toBeNull();
  });

  it("redirects the legacy /combos bookmark to the combo filter", async () => {
    setupFetch({ combos: [combo()], groups: [group()] });
    renderPage(["/combos"]);

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/catalog?kind=combo"),
    );
    expect(await screen.findByRole("heading", { name: "模型管理" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "编辑 team-chat" })).toBeInTheDocument();
  });

  it("opens the editor from addMember state with the model preselected in the lowest layer", async () => {
    setupFetch();
    renderPage([{ pathname: "/catalog", state: { addMember: "CM000000000000000000000001" } }]);

    expect(await screen.findByRole("heading", { name: "新建手动组合" })).toBeInTheDocument();
    // 从 location.state 打开时编辑器先渲染，模型目录查询可能仍在路上；成员标签来自目录，等它到位。
    expect(await screen.findByLabelText("channel-a/A priority")).toHaveValue(1);
    expect(await screen.findByRole("button", { name: "添加 channel-a/A" })).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveAttribute("data-state", "null"));
  });

  it("prepends the member picked from a direct row action", async () => {
    setupFetch();
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "将 channel-a/A 加入组合" }));
    expect(await screen.findByRole("heading", { name: "新建手动组合" })).toBeInTheDocument();
    expect(screen.getByLabelText("channel-a/A priority")).toHaveValue(1);
  });

  it("creates a combo and merges new members into the current lowest layer", async () => {
    const second = catalogItem({
      id: "CM000000000000000000000002",
      name: "channel-a/B",
      call_model: "B",
      upstream_model: "gpt-4o-mini",
    });
    const { calls } = setupFetch({ catalog: [catalogItem(), second], combos: [], groups: [] });
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "新建组合" }));
    expect(await screen.findByRole("heading", { name: "新建手动组合" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("手动组合名称"), "custom-combo");
    await user.click(await screen.findByRole("button", { name: "添加 channel-a/A" }));
    await user.click(await screen.findByRole("button", { name: "添加 channel-a/B" }));

    expect(screen.getByLabelText("channel-a/A priority")).toHaveValue(1);
    expect(screen.getByLabelText("channel-a/B priority")).toHaveValue(1);
    expect(screen.getAllByText(/数值越大越先调用/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "保存组合" }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === "POST" && call.url === "/api/combos")).toBe(true);
    });
    const post = calls.find((call) => call.method === "POST" && call.url === "/api/combos");
    const targets = post?.body?.targets as Array<{ target_type: string; priority: number }> | undefined;
    expect(post?.body?.name).toBe("custom-combo");
    expect(post?.body?.route_strategy).toBe("");
    expect(post?.body?.route_window_minutes).toBe(0);
    expect(targets?.map((target) => target.priority)).toEqual([1, 1]);
    expect(targets?.map((target) => target.target_type)).toEqual(["channel_model", "channel_model"]);
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "新建手动组合" })).not.toBeInTheDocument(),
    );
  });

  it("switches candidate member type via the tabs (click + arrow keys)", async () => {
    setupFetch({ catalog: [catalogItem()], combos: [], groups: [] });
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "新建组合" }));
    expect(await screen.findByRole("heading", { name: "新建手动组合" })).toBeInTheDocument();

    const modelTab = screen.getByRole("tab", { name: "渠道模型" });
    const comboTab = screen.getByRole("tab", { name: "子组合" });
    expect(modelTab).toHaveAttribute("aria-selected", "true");
    expect(comboTab).toHaveAttribute("aria-selected", "false");
    // 渠道模型 tab 下有可加入的候选芯片
    expect(screen.getByRole("button", { name: "添加 channel-a/A" })).toBeInTheDocument();

    await user.click(comboTab);
    expect(comboTab).toHaveAttribute("aria-selected", "true");
    expect(modelTab).toHaveAttribute("aria-selected", "false");
    // 没有子组合时给空态；候选芯片随之消失
    expect(screen.getByText("没有可用的子组合")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加 channel-a/A" })).toBeNull();

    // ←/→ 循环切换：焦点落在子组合 tab 上，右箭头绕回渠道模型
    await user.keyboard("{ArrowRight}");
    expect(modelTab).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowRight}");
    expect(comboTab).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(modelTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "添加 channel-a/A" })).toBeInTheDocument();
  });

  it("overrides routes per combo and rejects an invalid custom window", async () => {
    const { calls } = setupFetch({ catalog: [catalogItem()], combos: [], groups: [] });
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "新建组合" }));
    expect(await screen.findByRole("heading", { name: "新建手动组合" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("手动组合名称"), "routed-combo");
    await user.click(await screen.findByRole("button", { name: "添加 channel-a/A" }));

    await chooseSelect(user, { triggerName: "路由策略覆盖", optionName: "延迟优先" });
    await chooseSelect(user, { triggerName: "观察窗口覆盖", optionName: "自定义" });
    const customWindow = screen.getByLabelText("组合自定义窗口（分钟）");
    await user.clear(customWindow);
    await user.type(customWindow, "10");
    await user.click(screen.getByRole("button", { name: "保存组合" }));

    expect(await screen.findByText(/组合窗口不能小于 15 分钟/)).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST")).toBe(false);

    await user.clear(customWindow);
    await user.type(customWindow, "45");
    await user.click(screen.getByRole("button", { name: "保存组合" }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === "POST" && call.url === "/api/combos")).toBe(true);
    });
    const post = calls.find((call) => call.method === "POST" && call.url === "/api/combos");
    expect(post?.body?.route_strategy).toBe("latency");
    expect(post?.body?.route_window_minutes).toBe(45);
  });

  it("edits a manual combo, keeps members sorted by descending priority and swaps adjacent values", async () => {
    const { calls } = setupFetch({
      combos: [combo({ route_strategy: "cost", route_window_minutes: 15 }), backupChildCombo()],
      groups: [],
    });
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "编辑 team-chat" }));
    expect(await screen.findByRole("heading", { name: "编辑手动组合" })).toBeInTheDocument();
    expect(screen.getByLabelText("channel-a/A priority")).toHaveValue(2);
    expect(screen.getByLabelText("backup-child priority")).toHaveValue(1);
    expect(screen.getByRole("combobox", { name: "路由策略覆盖" })).toHaveTextContent("成本优先");
    expect(screen.getByRole("combobox", { name: "观察窗口覆盖" })).toHaveTextContent("15 分钟");

    // 上移仍是「数值互换」：低层成员拿到高层成员的 priority。
    await user.click(screen.getByRole("button", { name: "上移成员 2" }));
    expect(screen.getByLabelText("channel-a/A priority")).toHaveValue(1);
    expect(screen.getByLabelText("backup-child priority")).toHaveValue(2);

    await user.click(screen.getByRole("button", { name: "保存组合" }));
    await waitFor(() => {
      const patch = calls.find((call) => call.method === "PATCH" && call.url.startsWith("/api/combos/"));
      expect(patch).toBeDefined();
    });

    const patch = calls.find((call) => call.method === "PATCH" && call.url.startsWith("/api/combos/"));
    const targets = patch?.body?.targets as Array<{ target_type: string; priority: number }> | undefined;
    expect(targets?.map((target) => target.priority)).toEqual([2, 1]);
    expect(targets?.[0]?.target_type).toBe("child_combo");
    expect(patch?.body?.route_strategy).toBe("cost");
    expect(patch?.body?.route_window_minutes).toBe(15);
  });

  it("deletes a manual combo after confirmation", async () => {
    const { calls } = setupFetch({ combos: [combo()] });
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "删除 team-chat" }));
    expect(await screen.findByText("删除手动组合 team-chat？")).toBeInTheDocument();
    expect(screen.getByText("引用它的子组合必须先解除引用。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() =>
      expect(
        calls.some((call) => call.method === "DELETE" && call.url === "/api/combos/CB000000000000000000000001"),
      ).toBe(true),
    );
  });

  it("bans a direct model permanently from its row", async () => {
    const { calls } = setupFetch();
    await renderCatalog();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "封禁 channel-a/A" }));
    // 新版停用弹窗把「永久封禁」与「定时冷却」放在一起选（ADR-070），默认是永久封禁。
    // 封禁是危险动作：标题问句化（ui.md §2.3）
    expect(await screen.findByRole("dialog", { name: "封禁 channel-a/A？" })).toBeInTheDocument();
    expect(screen.getByText("封禁（永久）")).toBeInTheDocument();
    // 2026-11-18 压平后三种方式同一组单选：定时冷却与冷却窗口取代嵌套的「冷却（定时）」层
    expect(screen.getByText("定时冷却")).toBeInTheDocument();
    expect(screen.getByText("冷却窗口")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认封禁" }));

    await waitFor(() => {
      const patch = calls.find(
        (call) => call.method === "PATCH" && call.url.startsWith("/api/channel-models/"),
      );
      expect(patch).toBeDefined();
    });
    const patch = calls.find(
      (call) => call.method === "PATCH" && call.url.startsWith("/api/channel-models/"),
    );
    expect(patch?.url).toBe("/api/channel-models/CM000000000000000000000001");
    expect(patch?.body).toEqual({ enabled: false, banned_until: null });
  });

  it("shows timed ban copy and clears the timer when the model is enabled again", async () => {
    const banned = catalogItem({
      enabled: false,
      status: "disabled",
      visible_status: "disabled",
      banned_until: Date.now() + 60 * 60 * 1000,
    });
    const { calls } = setupFetch({ catalog: [banned], combos: [], groups: [] });
    await renderCatalog();
    const user = userEvent.setup();

    // 定时停用现在叫「冷却（定时）」：到期时刻写在小字里，说明写清它仍在 /v1/models。
    expect(screen.getByText("冷却（定时）")).toBeInTheDocument();
    expect(
      screen.getByText(
        /^人工设置：到 \d{2}-\d{2} \d{2}:\d{2} 自动恢复；冷却期间仍在 \/v1\/models，路由跳过$/,
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "启用 channel-a/A" }));
    expect(await screen.findByText("启用模型")).toBeInTheDocument();
    expect(screen.getByText(/启用会同时清除定时冷却/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() => {
      const patch = calls.find(
        (call) => call.method === "PATCH" && call.url.startsWith("/api/channel-models/"),
      );
      expect(patch).toBeDefined();
    });
    const patch = calls.find(
      (call) => call.method === "PATCH" && call.url.startsWith("/api/channel-models/"),
    );
    expect(patch?.body).toEqual({ enabled: true, banned_until: null });
  });

  it("shows the permanent-ban hint when there is no timer", async () => {
    setupFetch({
      catalog: [catalogItem({ enabled: false, status: "disabled", visible_status: "disabled" })],
      combos: [],
      groups: [],
    });
    await renderCatalog();

    expect(screen.getByText("永久封禁，不出现在 /v1/models")).toBeInTheDocument();
  });

  it("jumps to a page by number and clamps to the available range", async () => {
    const many = Array.from({ length: 30 }, (_, index) =>
      catalogItem({
        id: "CM" + String(index).padStart(3, "0"),
        name: "channel-a/m" + String(index).padStart(2, "0"),
        call_model: "m" + String(index).padStart(2, "0"),
        upstream_model: "m" + String(index).padStart(2, "0"),
      }),
    );
    setupFetch({ catalog: many, combos: [], groups: [] });
    await renderCatalog();
    const user = userEvent.setup();

    // 表格现在恒按名称排序（组合优先），所以夹具用补零的名称，让分页边界稳定在 m24 / m25。
    expect(await screen.findByText("channel-a/m00")).toBeInTheDocument();
    expect(screen.queryByText("channel-a/m25")).toBeNull();
    // 分页条是仓库共享的 Pagination（components/ui/pagination.tsx）：总页数在 sr-only 状态里播报，
    // 跳页输入是唯一一个 aria-label「跳到页码」的控件（页面不再自建第二个跳页框）。
    expect(screen.getAllByLabelText("跳到页码")).toHaveLength(1);
    expect(screen.getByText(/第 1 \/ 2 页 · 共 30 条/)).toBeInTheDocument();

    const jump = screen.getByLabelText("跳到页码");
    await user.clear(jump);
    await user.type(jump, "2{Enter}");
    expect(await screen.findByText("channel-a/m25")).toBeInTheDocument();

    const clampInput = screen.getByLabelText("跳到页码");
    await user.clear(clampInput);
    await user.type(clampInput, "99{Enter}");
    expect(await screen.findByText("channel-a/m25")).toBeInTheDocument();
  });

  it("keeps a manual combo row read-only until its detail loads", async () => {
    setupFetch({ catalog: [manualEntry(combo())], combos: [], groups: [] });
    await renderCatalog();

    expect(screen.getByText("组合详情加载中…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑 team-chat" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除 team-chat" })).toBeNull();
  });
});
