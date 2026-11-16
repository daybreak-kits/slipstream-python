import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Combo } from "@/lib/combos";
import type { ImplicitGroup } from "@/lib/groups";
import type { CatalogEntry } from "@/lib/playground";
import { ComboMarker } from "@/components/catalog/combo-marker";
import { ModelsPage } from "@/pages/models";
import type { ChannelModel } from "@/lib/channel-models";

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
    status: "enabled",
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
    enabled: true,
    created_at: 1762838400000,
    ...overrides,
  };
}

function comboMember(overrides: Partial<Combo["members"][number]> = {}) {
  return {
    id: "CT000000000000000000000001",
    target_type: "channel_model" as const,
    priority: 2,
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
    failover_rules: { rules: [], default_action: "next" },
    member_count: 1,
    available_count: 1,
    depth: 1,
    max_depth: 3,
    callable: true,
    created_at: 1762838400000,
    updated_at: 1762838400000,
    members: [comboMember()],
    ...overrides,
  };
}

function groupMember(overrides: Partial<ImplicitGroup["members"][number]> = {}) {
  return {
    channel_model_id: "CM000000000000000000000002",
    channel_id: "CH000000000000000000000002",
    channel_slug: "newapi",
    channel_name: "备用渠道",
    protocol: "openai_chat",
    upstream_model: "gpt-4o-mini",
    call_model: "B",
    priority: 3,
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
    member_count: 1,
    available_count: 1,
    callable: true,
    created_at: 1762838400000,
    members: [groupMember()],
    ...overrides,
  };
}

function setupFetch() {
  const combos = [combo()];
  const groups = [group()];
  const catalog: CatalogEntry[] = [
    catalogItem(),
    catalogItem({
      id: "CB000000000000000000000001",
      name: "team-chat",
      kind: "combo",
      origin: "manual",
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "",
    }),
    catalogItem({
      id: "group:auto-pool",
      name: "auto-pool",
      kind: "combo",
      origin: "auto",
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "auto-pool",
    }),
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/catalog")) {
      return jsonResponse({ items: catalog, total: catalog.length });
    }
    if (url === "/api/combos") {
      return jsonResponse({ items: combos, total: combos.length });
    }
    if (url === "/api/groups") {
      return jsonResponse({ items: groups, total: groups.length });
    }
    return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/catalog"]}>
        <ModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const MANUAL_LABEL = "手动组合，优先级 P2 channel-a/A";
const AUTO_LABEL = "自动组合，优先级 P3 newapi/B";

describe("models page combo marker", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows only an icon for combo rows and keeps the type in the accessible name", async () => {
    setupFetch();
    renderPage();
    const table = await screen.findByRole("table");

    const manual = screen.getByLabelText(MANUAL_LABEL);
    const auto = screen.getByLabelText(AUTO_LABEL);

    // 只有图标：没有可见文字，字样不再出现在表格里
    expect(manual.textContent).toBe("");
    expect(auto.textContent).toBe("");
    expect(manual.querySelector("svg")).not.toBeNull();
    expect(auto.querySelector("svg")).not.toBeNull();
    // 两种组合类型用不同图标区分（手动 = Layers，自动 = Wand2）
    expect(manual.querySelector("svg")?.innerHTML).not.toBe(auto.querySelector("svg")?.innerHTML);

    expect(within(table).queryByText("手动组合")).toBeNull();
    expect(within(table).queryByText("自动组合")).toBeNull();
  });

  it("puts the combo type and the priority layers into the tooltip", async () => {
    setupFetch();
    renderPage();
    await screen.findByRole("table");
    const manual = screen.getByLabelText(MANUAL_LABEL);
    // tooltip 在 delayDuration 之后才打开，整个「聚焦 → 打开 → 挂载」都放进 act，
    // 否则 Radix 的浮层状态更新会落在 act 之外。
    let tooltip: HTMLElement | null = null;
    await act(async () => {
      manual.focus();
      await new Promise((resolve) => setTimeout(resolve, 300));
      tooltip = document.querySelector('[role="tooltip"]');
    });
    expect(tooltip).not.toBeNull();
    expect(tooltip).toHaveTextContent("手动组合");
    expect(tooltip).toHaveTextContent("P2 channel-a/A");
    expect(tooltip).toHaveTextContent("成员与 priority");
  });

  it("lists every combo member on its own line in the tooltip without truncation", async () => {
    render(
      <ComboMarker
        origin="manual"
        layers={[
          { priority: 50, labels: ["sensenova/deepseek-v4-flash"] },
          { priority: 40, labels: ["alpha/gpt-4o", "beta/claude-mini"] },
        ]}
      />,
    );
    const trigger = screen.getByRole("img", { name: /手动组合/ });
    // aria 兜底仍是一行完整串，不省略
    expect(trigger).toHaveAttribute(
      "aria-label",
      "手动组合，优先级 P50 sensenova/deepseek-v4-flash / P40 alpha/gpt-4o, beta/claude-mini",
    );

    let tooltip: HTMLElement | null = null;
    await act(async () => {
      trigger.focus();
      await new Promise((resolve) => setTimeout(resolve, 300));
      tooltip = document.querySelector('[role="tooltip"]');
    });
    expect(tooltip).not.toBeNull();
    const tip = tooltip as unknown as HTMLElement;
    const items = within(tip).getAllByRole("listitem");
    // 成员逐行、priority 前缀保留、顺序按层从高到低
    expect(items.map((item) => item.textContent)).toEqual([
      "P50 sensenova/deepseek-v4-flash",
      "P40 alpha/gpt-4o",
      "P40 beta/claude-mini",
    ]);
    // 成员多时列表内部滚动，正文不再走 3 行截断
    expect(tip.querySelector("ul")).toHaveClass("overflow-y-auto", "max-h-40");
    expect(items.every((item) => item.classList.contains("whitespace-nowrap"))).toBe(true);
  });

  it("labels the inline row action 组合", async () => {
    setupFetch();
    renderPage();
    await screen.findByRole("table");

    // 行内动作已图标化（32px 方钮）：可见文本不再占列宽，语义保留在可访问名与 Tooltip 里。
    const button = screen.getByRole("button", { name: "将 channel-a/A 加入组合" });
    expect(button.textContent?.trim()).toBe("");
    expect(button.textContent).not.toContain("加入组合");
    // 同组其它行内按钮同样图标化，可访问名仍按行指名
    expect(screen.getByRole("button", { name: "封禁 channel-a/A" })).toBeInTheDocument();
  });
});

type FetchCall = { url: string; method: string; body: unknown };

function setupBulkFetch(
  extra: CatalogEntry[] = [],
  batchStatus = 200,
  channelModels: ChannelModel[] = [channelModel()],
) {
  const calls: FetchCall[] = [];
  const combos = [combo()];
  const groups = [group()];
  const catalog: CatalogEntry[] = [
    catalogItem(),
    catalogItem({
      id: "CB000000000000000000000001",
      name: "team-chat",
      kind: "combo",
      origin: "manual",
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "",
    }),
    catalogItem({
      id: "group:auto-pool",
      name: "auto-pool",
      kind: "combo",
      origin: "auto",
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "auto-pool",
    }),
    ...extra,
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    // 行内「编辑」会按渠道拉模型列表；PATCH /api/channel-models/{id} 是渠道表单的保存端点。
    if (method === "GET" && url === "/api/channels/CH000000000000000000000001/models") {
      return jsonResponse({ items: channelModels, total: channelModels.length });
    }
    if (method === "PATCH" && /^\/api\/channel-models\/CM/.test(url)) {
      return jsonResponse(channelModels[0] ?? channelModel());
    }
    // 「解除冷却」的故障轴：POST /api/channel-models/{id}/reset 清熔断与冷却。
    if (method === "POST" && /^\/api\/channel-models\/CM.*\/reset$/.test(url)) {
      return jsonResponse(channelModels[0] ?? channelModel());
    }
    if (method === "PATCH" && url === "/api/channel-models/batch") {
      const ids = (body as { model_ids?: string[] } | null)?.model_ids ?? [];
      if (batchStatus !== 200) {
        return jsonResponse({ code: "NOT_FOUND", message: "batch endpoint missing" }, batchStatus);
      }
      return jsonResponse({
        results: ids.map((id) => ({ model_id: id, ok: true })),
        succeeded: ids.length,
        failed: 0,
      });
    }
    if (method === "PATCH" && url.startsWith("/api/combos/")) return jsonResponse(combo());
    if (url.startsWith("/api/catalog")) {
      return jsonResponse({ items: catalog, total: catalog.length });
    }
    if (url === "/api/combos") return jsonResponse({ items: combos, total: combos.length });
    if (url === "/api/groups") return jsonResponse({ items: groups, total: groups.length });
    return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const SECOND_DIRECT = catalogItem({
  id: "CM000000000000000000000009",
  name: "beta/B",
  channel_id: "CH000000000000000000000009",
  channel_slug: "beta",
  channel_name: "备用渠道",
  call_model: "B",
  upstream_model: "gpt-4o-mini",
});

/** 渠道模型行（编辑弹窗的数据源）；字段按 ChannelModel 全量给出。 */
function channelModel(overrides: Partial<ChannelModel> = {}): ChannelModel {
  const now = 1_762_838_400_000;
  return {
    id: "CM000000000000000000000001",
    channel_id: "CH000000000000000000000001",
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
    cooldown: {
      failure_threshold: 3,
      cooldown_s: 60,
      rate_limit_cooldown_s: 300,
      half_open_max: 1,
      escalate_mode: "never",
      escalate_after: 5,
      fault_cooldown_s: 120,
    },
    schedule: { version: 2, timezone: "Asia/Shanghai", rules: [] },
    probe_status: "unknown",
    probe_latency_ms: null,
    probe_message: "",
    probed_at: null,
    probe_default: false,
    model_type: "chat",
    model_type_source: "manual",
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function rowNames(): string[] {
  return screen
    .getAllByRole("checkbox")
    .map((el) => el.getAttribute("aria-label") ?? "")
    .filter((label) => label.startsWith("选择 "))
    .map((label) => label.slice(3));
}

describe("models page bulk actions", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("mirrors the row selection into the bulk toolbar and clears it", async () => {
    setupBulkFetch();
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 channel-a/A" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    expect(toolbar).toHaveTextContent("已选 1 项");
    // 部分选中时页级全选是 indeterminate，而不是勾上
    expect(screen.getByRole("checkbox", { name: "全选当前页" })).toHaveAttribute(
      "aria-checked",
      "mixed",
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "全选当前页" }));
    expect(toolbar).toHaveTextContent("已选 3 项");
    expect(screen.getByRole("checkbox", { name: "全选当前页" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    fireEvent.click(within(toolbar).getByRole("button", { name: "清除" }));
    expect(screen.queryByRole("toolbar", { name: "批量操作" })).toBeNull();
  });

  it("keeps the table header sticky and visible above scrolling rows", async () => {
    setupBulkFetch();
    const { container } = renderPage();
    await screen.findByRole("table");

    const thead = container.querySelector("thead");
    expect(thead).toHaveClass("sticky", "top-0", "z-10");
    // Card 与表格包装层都不再是滚动容器，sticky 相对 main 生效
    expect(thead?.closest("table")?.parentElement).toHaveClass("overflow-visible");
    const headers = within(screen.getByRole("table")).getAllByRole("columnheader");
    expect(headers).toHaveLength(7);
    // 每列名都有自己的底色，行从下面滚过时不透字
    expect(headers.every((header) => header.classList.contains("bg-surface-2"))).toBe(true);
  });

  it("truncates the price cell to one line and exposes the full text via title", async () => {
    setupBulkFetch();
    renderPage();
    await screen.findByRole("table");

    const price = screen.getByTitle("入 $1.00 / 出 $2.00 / 缓存 —");
    expect(price).toHaveClass("truncate");
    expect(price.textContent).toBe("入 $1.00 / 出 $2.00 / 缓存 —");
  });

  it("sorts by status inside the direct group and exposes aria-sort", async () => {
    setupBulkFetch([
      catalogItem({ id: "CM000000000000000000000009", name: "zeta/B", enabled: false }),
      catalogItem({
        id: "CM000000000000000000000010",
        name: "alpha/C",
        enabled: false,
        banned_until: Date.now() + 3 * 3600_000,
      }),
    ]);
    renderPage();
    await screen.findByRole("table");

    // 类型恒在前且手动组合 → 自动组合，直连在后；默认按名称升序
    expect(rowNames()).toEqual(["team-chat", "auto-pool", "alpha/C", "channel-a/A", "zeta/B"]);

    const statusHeader = screen.getByRole("columnheader", { name: /状态/ });
    const nameHeader = screen.getByRole("columnheader", { name: /名称/ });
    expect(statusHeader).toHaveAttribute("aria-sort", "none");

    fireEvent.click(screen.getByRole("button", { name: "按状态排序" }));
    expect(statusHeader).toHaveAttribute("aria-sort", "ascending");
    expect(nameHeader).toHaveAttribute("aria-sort", "none");
    // 危险（封禁）在前，其次冷却，最后可用
    expect(rowNames().slice(2)).toEqual(["zeta/B", "alpha/C", "channel-a/A"]);

    fireEvent.click(screen.getByRole("button", { name: "按状态排序" }));
    expect(statusHeader).toHaveAttribute("aria-sort", "descending");
    expect(rowNames().slice(2)).toEqual(["channel-a/A", "alpha/C", "zeta/B"]);
  });

  it("bans the selected direct models through the batch endpoint", async () => {
    const calls = setupBulkFetch([SECOND_DIRECT]);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 beta/B" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "封禁" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("封禁已选的 1 项？");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认封禁" }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "PATCH" && call.url === "/api/channel-models/batch")).toBe(
        true,
      );
    });
    const batch = calls.find(
      (call) => call.method === "PATCH" && call.url === "/api/channel-models/batch",
    );
    expect(batch?.body).toEqual({ model_ids: [SECOND_DIRECT.id], op: "ban" });
  });

  it("submits a cooldown as an absolute banned_until through the batch endpoint", async () => {
    const calls = setupBulkFetch([SECOND_DIRECT]);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 beta/B" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "冷却" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("仍出现在 /v1/models");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认冷却" }));

    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/channel-models/batch")).toBe(true);
    });
    const batch = calls.find((call) => call.url === "/api/channel-models/batch");
    const body = batch?.body as { op: string; banned_until: string; model_ids: string[] };
    expect(body.op).toBe("cooldown");
    expect(body.model_ids).toEqual([SECOND_DIRECT.id]);
    // 默认预设 1 小时：到期时刻是绝对时间戳，而不是「1h」这类相对值
    expect(new Date(body.banned_until).getTime()).toBeGreaterThan(Date.now() + 50 * 60_000);
  });

  it("keeps timed actions off for combos and lifecycle actions off for auto combos", async () => {
    setupBulkFetch();
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 team-chat" }));
    const manualBar = await screen.findByRole("toolbar", { name: "批量操作" });
    expect(within(manualBar).getByRole("button", { name: "冷却" })).toBeDisabled();
    expect(within(manualBar).getByRole("button", { name: "排期" })).toBeDisabled();
    expect(within(manualBar).getByRole("button", { name: "封禁" })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 auto-pool" }));
    const autoBar = await screen.findByRole("toolbar", { name: "批量操作" });
    expect(within(autoBar).getByRole("button", { name: "启用" })).toBeDisabled();
    expect(within(autoBar).getByRole("button", { name: "封禁" })).toBeDisabled();
  });

  it("adds the selected models to an existing manual combo", async () => {
    const calls = setupBulkFetch([SECOND_DIRECT]);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 beta/B" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "加入组合" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "选择组合 team-chat" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "加入组合" }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "PATCH" && call.url.startsWith("/api/combos/"))).toBe(
        true,
      );
    });
    const patch = calls.find((call) => call.method === "PATCH" && call.url.startsWith("/api/combos/"));
    const targets = (patch?.body as { targets: Array<Record<string, unknown>> }).targets;
    expect(targets).toHaveLength(2);
    expect(targets[0]).toMatchObject({ channel_model_id: "CM000000000000000000000001", priority: 2 });
    expect(targets[1]).toMatchObject({
      target_type: "channel_model",
      channel_model_id: SECOND_DIRECT.id,
      child_combo_id: null,
      priority: 1,
      enabled: true,
    });
  });

  it("renders the ban / cooldown / breaker tones in the status column", async () => {
    setupBulkFetch([
      catalogItem({ id: "CM000000000000000000000009", name: "zeta/B", enabled: false }),
      catalogItem({
        id: "CM000000000000000000000010",
        name: "alpha/C",
        enabled: false,
        banned_until: Date.now() + 3 * 3600_000,
      }),
      catalogItem({ id: "CM000000000000000000000011", name: "fault/D", visible_status: "fault" }),
    ]);
    renderPage();
    await screen.findByRole("table");

    // 永久封禁：无 banned_until 的 disabled 模型
    const bannedRow = screen.getByRole("checkbox", { name: "选择 zeta/B" }).closest("tr");
    expect(within(bannedRow as HTMLElement).getByText("封禁")).toBeInTheDocument();
    // 定时类统一写「冷却（定时）」，到期时刻挪到小字里
    expect(screen.getByText("冷却（定时）")).toBeInTheDocument();
    expect(
      screen.getByText(/^人工设置：到 \d{2}-\d{2} \d{2}:\d{2} 自动恢复；/),
    ).toBeInTheDocument();
    // 熔断/故障归到自动冷却，不单开一档
    expect(screen.getAllByText("冷却（故障）")).toHaveLength(1);
    // 正常可用仍是「可用」
    expect(screen.getAllByText("可用")).toHaveLength(3);
  });
});


  it("prefills the schedule dialog from the catalog entry and overwrites the whole schedule", async () => {
    const scheduled = catalogItem({
      id: SECOND_DIRECT.id,
      name: SECOND_DIRECT.name,
      schedule: {
        version: 2,
        timezone: "Asia/Shanghai",
        rules: [{ kind: "weekly", days: [2, 4], start: "10:00", end: "12:00" }],
      },
    });
    const calls = setupBulkFetch([scheduled]);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 beta/B" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "排期" }));

    const dialog = await screen.findByRole("dialog");
    // 既有排期要回填到编辑器：时区、规则行与摘要都应直接可见。
    expect(within(dialog).getByRole("combobox", { name: "IANA 时区" })).toHaveTextContent(
      "Asia/Shanghai",
    );
    expect(within(dialog).getByLabelText("第 1 条规则的开始时刻")).toHaveValue("10:00");
    expect(within(dialog).getByLabelText("第 1 条规则的结束时刻")).toHaveValue("12:00");
    expect(within(dialog).getByText("每周 周二、周四 10:00–12:00")).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("第 1 条规则的结束时刻"), {
      target: { value: "13:00" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存排期" }));

    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/channel-models/batch")).toBe(true);
    });
    const batch = calls.find((call) => call.url === "/api/channel-models/batch");
    // 保存是整份覆盖：rules 只有编辑后的那一条，不会把回填的旧规则再追加一份。
    expect(batch?.body).toEqual({
      model_ids: [SECOND_DIRECT.id],
      op: "schedule",
      schedule: {
        version: 2,
        timezone: "Asia/Shanghai",
        rules: [{ kind: "weekly", days: [2, 4], start: "10:00", end: "13:00" }],
      },
    });
  });

  it("refuses to degrade a schedule into a permanent ban when the batch endpoint is missing", async () => {
    // 后端还没上线批量端点时，封禁/冷却可以退化串行，但排期不能：
    // 退化只会写 enabled:false 且无 banned_until，等于把模型永久封掉。
    const calls = setupBulkFetch([SECOND_DIRECT], 404);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 beta/B" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "排期" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "保存排期" }));

    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/channel-models/batch")).toBe(true);
    });
    const single = calls.filter(
      (call) =>
        call.method === "PATCH" &&
        call.url.startsWith("/api/channel-models/") &&
        call.url !== "/api/channel-models/batch",
    );
    expect(single).toHaveLength(0);
    // 失败不清选择，用户可以直接重试/换操作
    expect(screen.getByRole("checkbox", { name: "选择 beta/B" })).toBeChecked();
  });

  it("routes a row cooldown through the schedule window to the batch endpoint", async () => {
    const calls = setupBulkFetch();
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "封禁 channel-a/A" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: "冷却（定时）" }));
    // 切到按计划窗口：默认草稿（本地时区 + 一条每天 22:00–02:00）即可提交
    fireEvent.click(within(dialog).getByRole("radio", { name: "按计划窗口" }));
    const confirm = within(dialog).getByRole("button", { name: "确认冷却" });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => {
      expect(calls.some((call) => call.method === "PATCH" && call.url === "/api/channel-models/batch")).toBe(
        true,
      );
    });
    const batch = calls.find((call) => call.method === "PATCH" && call.url === "/api/channel-models/batch");
    const body = batch?.body as {
      model_ids: string[];
      op: string;
      schedule: { version: number; timezone: string; rules: Array<{ kind: string; start: string; end: string }> };
    };
    expect(body.model_ids).toEqual(["CM000000000000000000000001"]);
    expect(body.op).toBe("schedule");
    expect(body.schedule.version).toBe(2);
    expect(body.schedule.timezone).toBeTruthy();
    expect(body.schedule.rules[0]).toEqual({ kind: "daily", start: "22:00", end: "02:00" });
    // 计划窗口不退化为单模型 PATCH（更不会写 banned_until）
    const single = calls.filter(
      (call) =>
        call.method === "PATCH" &&
        call.url.startsWith("/api/channel-models/") &&
        call.url !== "/api/channel-models/batch",
    );
    expect(single).toHaveLength(0);
  });

  it("disables confirm while the schedule window is invalid and recovers after fixing it", async () => {
    const calls = setupBulkFetch();
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "封禁 channel-a/A" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: "冷却（定时）" }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "按计划窗口" }));

    const start = within(dialog).getByLabelText("第 1 条规则的开始时刻");
    fireEvent.change(start, { target: { value: "" } });
    expect(within(dialog).getByRole("button", { name: "确认冷却" })).toBeDisabled();

    fireEvent.change(start, { target: { value: "23:30" } });
    expect(within(dialog).getByRole("button", { name: "确认冷却" })).toBeEnabled();
    // 非法阶段没发出任何请求
    expect(calls.some((call) => call.method === "PATCH")).toBe(false);
  });

  it("submits a schedule window through the bulk cooldown dialog", async () => {
    const calls = setupBulkFetch([SECOND_DIRECT]);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 beta/B" }));
    const toolbar = await screen.findByRole("toolbar", { name: "批量操作" });
    fireEvent.click(within(toolbar).getByRole("button", { name: "冷却" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("radio", { name: "按计划窗口" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "确认冷却" }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "PATCH" && call.url === "/api/channel-models/batch")).toBe(
        true,
      );
    });
    const batch = calls.find((call) => call.method === "PATCH" && call.url === "/api/channel-models/batch");
    const body = batch?.body as { model_ids: string[]; op: string; schedule?: { timezone: string } };
    expect(body.model_ids).toEqual([SECOND_DIRECT.id]);
    expect(body.op).toBe("schedule");
    expect(body.schedule?.timezone).toBeTruthy();
  });

  it("opens the shared channel model form from the direct row edit action", async () => {
    const calls = setupBulkFetch();
    renderPage();
    await screen.findByRole("table");
    expect(calls.some((call) => call.url === "/api/channels/CH000000000000000000000001/models")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "编辑 channel-a/A" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("编辑模型");

    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === "GET" && call.url === "/api/channels/CH000000000000000000000001/models",
        ),
      ).toBe(true);
    });
    // 回显的是渠道模型列表里的值（上游 gpt-4o / 调用串 A），不是目录条目的近似值
    expect(await within(dialog).findByLabelText("上游模型名")).toHaveValue("gpt-4o");
  });

  it("saves through the channel model form and refreshes the catalog", async () => {
    const calls = setupBulkFetch();
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "编辑 channel-a/A" }));
    const dialog = await screen.findByRole("dialog");
    const save = await within(dialog).findByRole("button", { name: "保存" });
    fireEvent.click(save);

    await waitFor(() => {
      expect(
        calls.some(
          (call) =>
            call.method === "PATCH" &&
            call.url === "/api/channel-models/CM000000000000000000000001",
        ),
      ).toBe(true);
    });
    const patch = calls.find(
      (call) => call.method === "PATCH" && call.url === "/api/channel-models/CM000000000000000000000001",
    );
    expect(patch?.body).toMatchObject({
      upstream_model: "gpt-4o",
      call_model: "A",
      input_price_per_1m: 1,
      output_price_per_1m: 2,
      enabled: true,
    });
    // 保存成功后失效目录：/api/catalog 至少被重新拉取一次
    await waitFor(() => {
      expect(
        calls.filter((call) => call.method === "GET" && call.url.startsWith("/api/catalog")).length,
      ).toBeGreaterThanOrEqual(2);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("defaults to type-first ordering with case- and accent-insensitive names", async () => {
    const empty = {
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "",
    };
    setupBulkFetch([
      catalogItem({ id: "CM000000000000000000000011", name: "café/Y" }),
      catalogItem({ id: "CM000000000000000000000012", name: "cafe/Z" }),
      catalogItem({ id: "CM000000000000000000000013", name: "bravo/W" }),
      catalogItem({
        id: "CB000000000000000000000002",
        name: "zz-manual",
        kind: "combo",
        origin: "manual",
        ...empty,
      }),
      catalogItem({
        id: "group:zz-auto",
        name: "zz-auto",
        kind: "combo",
        origin: "auto",
        ...empty,
      }),
    ]);
    renderPage();
    await screen.findByRole("table");

    // 手动组合 → 自动组合 → 直连；直连内名称升序。
    // café 与 cafe 在 base 敏感度下同名，保持条目原有顺序（variant 会把 cafe 排到 café 前面）。
    expect(rowNames()).toEqual([
      "team-chat",
      "zz-manual",
      "auto-pool",
      "zz-auto",
      "bravo/W",
      "café/Y",
      "cafe/Z",
      "channel-a/A",
    ]);
  });

  it("keeps the type order while status sorting and restores name ascending", async () => {
    setupBulkFetch([catalogItem({ id: "CM000000000000000000000009", name: "zeta/B", enabled: false })]);
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "按状态排序" }));
    // 状态升序只打乱直连组内顺序；手动组合仍在最前、自动组合次之
    expect(rowNames()).toEqual(["team-chat", "auto-pool", "zeta/B", "channel-a/A"]);

    fireEvent.click(screen.getByRole("button", { name: "按名称排序" }));
    expect(rowNames()).toEqual(["team-chat", "auto-pool", "channel-a/A", "zeta/B"]);
    expect(screen.getByRole("columnheader", { name: /名称/ })).toHaveAttribute("aria-sort", "ascending");

    fireEvent.click(screen.getByRole("button", { name: "按名称排序" }));
    expect(rowNames()).toEqual(["team-chat", "auto-pool", "zeta/B", "channel-a/A"]);

    // 恢复名称升序
    fireEvent.click(screen.getByRole("button", { name: "按名称排序" }));
    expect(rowNames()).toEqual(["team-chat", "auto-pool", "channel-a/A", "zeta/B"]);
    expect(screen.getByRole("columnheader", { name: /名称/ })).toHaveAttribute("aria-sort", "ascending");
  });

describe("models page availability wording", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // 固定到期时刻（UTC 18:37）：任何整刻钟时区偏移都落不到 00:00，
  // 「不再出现 00:00」的断言不受跑测试的时区与钟点影响。
  const OPEN_UNTIL = Date.UTC(2026, 10, 16, 18, 37);

  it("labels an open breaker as 冷却（故障）with a full recovery time and never 00:00", async () => {
    setupBulkFetch([
      catalogItem({
        id: "CM000000000000000000000020",
        name: "sensenova/deepseek-v4-flash",
        visible_status: "open",
        cooldown_until: OPEN_UNTIL,
        consecutive_failures: 2,
        // 后端旧文案只给时分（跨天就无从判断），界面必须改写成带日期的恢复时刻
        status_message: "连续失败 2 次，预计 00:00 重试",
      }),
    ]);
    renderPage();
    await screen.findByRole("table");

    const row = screen
      .getByRole("checkbox", { name: "选择 sensenova/deepseek-v4-flash" })
      .closest("tr") as HTMLElement;
    expect(within(row).getByText("冷却（故障）")).toBeInTheDocument();
    // 恢复时刻写全年/月/日 + 时:分
    expect(within(row).getByText(/预计 \d{4}\/\d{2}\/\d{2} \d{2}:\d{2} 恢复/)).toBeInTheDocument();
    // 连续失败信息保留，但不顶掉恢复时刻
    expect(within(row).getByText(/连续失败 2 次/)).toBeInTheDocument();
    expect(within(row).queryByText(/00:00/)).toBeNull();
  });

  it("offers 清除排期 inside a schedule window and submits an empty rule set", async () => {
    const scheduled = catalogItem({
      id: SECOND_DIRECT.id,
      name: SECOND_DIRECT.name,
      schedule: {
        version: 2,
        timezone: "Asia/Shanghai",
        // 00:00–00:00 是跨天的整天窗口：任何钟点都命中，测试不依赖运行时间
        rules: [{ kind: "daily", start: "00:00", end: "00:00" }],
      },
      cooldown_reason: "COOLDOWN_WINDOW",
      status_message: "排期冷却窗口中",
    });
    const calls = setupBulkFetch([scheduled]);
    renderPage();
    await screen.findByRole("table");

    // 排期窗口命中：定时类徽标，小字写明来源是排期规则
    const row = screen.getByRole("checkbox", { name: "选择 beta/B" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("冷却（定时）")).toBeInTheDocument();
    expect(within(row).getByText("排期规则命中，窗口结束自动恢复")).toBeInTheDocument();

    // 没有排期窗口的行不给这个入口
    expect(screen.queryByRole("button", { name: "清除排期 channel-a/A" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "清除排期 beta/B" }));

    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/channel-models/batch")).toBe(true);
    });
    const batch = calls.find((call) => call.url === "/api/channel-models/batch");
    // 空规则集 = 永不冷却；只写 schedule，不动 enabled / banned_until
    expect(batch?.body).toEqual({
      model_ids: [SECOND_DIRECT.id],
      op: "schedule",
      schedule: { version: 2, timezone: "Asia/Shanghai", rules: [] },
    });
  });
});

describe("models page 解除冷却", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const BREAKER_ROW = catalogItem({
    id: "CM000000000000000000000030",
    name: "sensenova/breaker",
    health_status: "open",
    visible_status: "open",
  });

  it("clears the breaker first and then the timed cooldown of a cooled down row", async () => {
    const calls = setupBulkFetch([
      catalogItem({
        ...BREAKER_ROW,
        banned_until: Date.now() + 3_600_000,
      }),
    ]);
    renderPage();
    await screen.findByRole("table");

    const row = screen
      .getByRole("checkbox", { name: "选择 sensenova/breaker" })
      .closest("tr") as HTMLElement;
    // 故障轴在冷却：给解封入口，不再给「清除排期」（解封会一并清掉排期）
    expect(within(row).getByRole("button", { name: "解除冷却 sensenova/breaker" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "清除排期 sensenova/breaker" })).toBeNull();

    fireEvent.click(within(row).getByRole("button", { name: "解除冷却 sensenova/breaker" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("解除后该模型立即恢复路由");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认解除冷却" }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/reset"))).toBe(true);
    });
    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/channel-models/batch")).toBe(true);
    });
    const resetIndex = calls.findIndex(
      (call) => call.method === "POST" && call.url.endsWith("/reset"),
    );
    const batchIndex = calls.findIndex((call) => call.url === "/api/channel-models/batch");
    // 熔断先清（reset），随后才走批量端点清人工定时冷却
    expect(resetIndex).toBeGreaterThanOrEqual(0);
    expect(resetIndex).toBeLessThan(batchIndex);
    expect(calls[batchIndex]?.body).toEqual({
      model_ids: [BREAKER_ROW.id],
      op: "unban",
    });
  });

  it("keeps 清除排期 and hides 解除冷却 when only the schedule window is cooling", async () => {
    setupBulkFetch([
      catalogItem({
        id: SECOND_DIRECT.id,
        name: SECOND_DIRECT.name,
        schedule: {
          version: 2,
          timezone: "Asia/Shanghai",
          // 00:00–00:00 是跨天的整天窗口：任何钟点都命中，测试不依赖运行时间
          rules: [{ kind: "daily", start: "00:00", end: "00:00" }],
        },
        cooldown_reason: "COOLDOWN_WINDOW",
      }),
    ]);
    renderPage();
    await screen.findByRole("table");

    const row = screen.getByRole("checkbox", { name: "选择 beta/B" }).closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "清除排期 beta/B" })).toBeInTheDocument();
    // 只有排期轴在冷却时，入口让位给「清除排期」
    expect(within(row).queryByRole("button", { name: "解除冷却 beta/B" })).toBeNull();
  });

  it("clears the breaker and the schedule window together on a doubly cooling row", async () => {
    const calls = setupBulkFetch([
      catalogItem({
        id: "CM000000000000000000000031",
        name: "sensenova/both",
        health_status: "fault",
        visible_status: "fault",
        schedule: {
          version: 2,
          timezone: "Asia/Shanghai",
          rules: [{ kind: "daily", start: "00:00", end: "00:00" }],
        },
        cooldown_reason: "COOLDOWN_WINDOW",
      }),
    ]);
    renderPage();
    await screen.findByRole("table");

    const row = screen.getByRole("checkbox", { name: "选择 sensenova/both" }).closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "解除冷却 sensenova/both" })).toBeInTheDocument();
    // 排期轴也在冷却，但解封入口会一并清掉它，不再重复给「清除排期」
    expect(within(row).queryByRole("button", { name: "清除排期 sensenova/both" })).toBeNull();

    fireEvent.click(within(row).getByRole("button", { name: "解除冷却 sensenova/both" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认解除冷却" }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/reset"))).toBe(true);
    });
    await waitFor(() => {
      expect(calls.some((call) => call.url === "/api/channel-models/batch")).toBe(true);
    });
    const batch = calls.find((call) => call.url === "/api/channel-models/batch");
    // 排期轴写空规则集：整份覆盖 schedule_json，之后永不冷却
    expect(batch?.body).toEqual({
      model_ids: ["CM000000000000000000000031"],
      op: "schedule",
      schedule: { version: 2, timezone: "Asia/Shanghai", rules: [] },
    });
  });
});

