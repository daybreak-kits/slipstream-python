import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { AuthProvider } from "@/lib/auth";
import {
  formatCacheHitRate,
  formatCompact,
  formatRate,
  formatSpanMinutes,
  formatUsdCompact,
} from "@/lib/format";
import {
  attemptErrorCount,
  buildLogMetrics,
  failedAttempts,
  formatCacheHitRate as logsFormatCacheHitRate,
  hasFailedAttempts,
  formatCompact as logsFormatCompact,
  formatRate as logsFormatRate,
  formatSpanMinutes as logsFormatSpanMinutes,
  formatUsdCompact as logsFormatUsdCompact,
  logQueryToParams,
  parseAppliedQuery,
  type LogSummary,
} from "@/lib/logs";
import type { LogFilterOptions, LogGroup, RequestLog, RequestLogList } from "@/lib/logs";
import { formatDateTimeParts, TimezoneProvider } from "@/lib/time";
import { LogsPage } from "@/pages/logs";

import { chooseSelect, expectSelectText } from "./select-helpers";

const CREATED_AT = Date.UTC(2026, 10, 11, 1, 2, 3);
const ADMIN = {
  id: "ADM00000000000000000000000",
  username: "admin",
  created_at: CREATED_AT,
  updated_at: CREATED_AT,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeLog(overrides: Partial<RequestLog> = {}): RequestLog {
  return {
    id: "LOG00000000000000000000001",
    request_id: "req-1",
    upstream_request_id: "chatcmpl-up-1",
    user_id: "u1",
    user_name: "Alice",
    api_key_id: "key-1",
    api_key_label: "key-a",
    requested_model: "combo-a",
    channel_name: "channel-a",
    upstream_model: "fake-model",
    response_model: "fake-model",
    is_stream: true,
    http_status: 200,
    error_code: null,
    error_message: "",
    prompt_tokens: 30,
    completion_tokens: 10,
    cached_tokens: 8,
    cache_ratio: 0.25,
    cost_usd: 0.000016,
    ttft_ms: 120,
    ttfa_ms: 150,
    ttlt_ms: 1400,
    total_ms: 1500,
    throughput_tps: 12.5,
    usage_estimated: false,
    attempts: [
      {
        channel_slug: "channel-a",
        channel_name: "channel-a",
        upstream_model: "fake-model",
        status: "success",
        ms: 120,
        round: 1,
        priority_path: [1],
      },
    ],
    rewrite_applied: false,
    rewrite_skipped: "",
    rewrite_diff: {},
    params: { temperature: 0.2, max_tokens: 64, stream: true },
    response_headers: {},
    error_body: null,
    created_at: CREATED_AT,
    ...overrides,
  };
}

/** 详情抽屉还会读取后端已返回、但 RequestLog 尚未登记的新字段。 */
type DetailLogFixture = RequestLog & {
  client_ip?: string;
  forwarded_ips?: string[];
  forwarded_sources?: { header: string; value: string }[];
  request_headers?: Record<string, string>;
};

function makeDetailLog(overrides: Partial<DetailLogFixture> = {}): RequestLog {
  return makeLog(overrides);
}
function makeList(items: RequestLog[], total = items.length, page = 1): RequestLogList {
  return { items, total, page, page_size: 25 };
}

function makeGroup(overrides: Partial<LogGroup> = {}): LogGroup {
  return {
    key: "combo-a",
    label: "combo-a",
    status_code: null,
    status_label: "",
    first_at: CREATED_AT,
    last_at: CREATED_AT + 60_000,
    requests: 3,
    attempts: 4,
    errors: 0,
    prompt_tokens: 1200,
    completion_tokens: 400,
    cached_tokens: 200,
    cache_ratio: 0.25,
    cost_usd: 0.0042,
    ttft_p50_ms: 120,
    total_p50_ms: 1500,
    total_p95_ms: 3200,
    channels: ["channel-a", "channel-b"],
    error_codes: [],
    sample_request_ids: ["req-1"],
    ...overrides,
  };
}

const OPTIONS: LogFilterOptions = {
  channels: ["channel-a", "channel-b"],
  models: ["combo-a", "model-a"],
  key_labels: ["key-a", "key-b"],
  users: [{ id: "u1", name: "Alice" }],
  truncated: {
    channels: false,
    models: false,
    key_labels: false,
    users: false,
  },
};

type LogsResponder = (url: URL) => RequestLogList;

function makeSummary(overrides: Partial<LogSummary> = {}): LogSummary {
  return {
    requests: 3,
    prompt_tokens: 1_200_000,
    completion_tokens: 100_000,
    cached_tokens: 500_000,
    cost_usd: 1.2345,
    cache_hit_rate: 0.1234,
    cache_hit_rate_samples: 42,
    active_minutes: 3,
    span_minutes: 120,
    rpm: 1.5,
    tpm: 650_000,
    peak_rpm: 4,
    estimated_rows: 2,
    ...overrides,
  };
}

function installFetch(options: {
  logs?: RequestLogList | LogsResponder;
  detail?: RequestLog;
  summary?: LogSummary;
} = {}) {
  const calls: string[] = [];
  const logs = options.logs ?? makeList([makeLog()]);
  const detail = options.detail ?? makeLog();
  const summary = options.summary ?? makeSummary();
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://testserver");
    calls.push(`${url.pathname}${url.search}`);
    if (url.pathname === "/api/auth/me") return jsonResponse(ADMIN);
    if (url.pathname === "/api/logs/filters") return jsonResponse(OPTIONS);
    // 必须先于 startsWith("/api/logs/") 判断，否则会被当成详情请求。
    if (url.pathname === "/api/logs/summary") return jsonResponse(summary);
    if (url.pathname === "/api/logs") {
      return jsonResponse(typeof logs === "function" ? logs(url) : logs);
    }
    if (url.pathname.startsWith("/api/logs/")) return jsonResponse(detail);
    return jsonResponse({ code: "NOT_FOUND", message: "not found", request_id: "r" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function queryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-search">{location.search}</span>;
}

function renderLogs(initialEntries: string[] = ["/logs"], withProbe = false) {
  return render(
    <QueryClientProvider client={queryClient()}>
      <TimezoneProvider>
        <MemoryRouter initialEntries={initialEntries}>
          <LogsPage />
          {withProbe ? <LocationProbe /> : null}
        </MemoryRouter>
      </TimezoneProvider>
    </QueryClientProvider>,
  );
}

function renderShell() {
  return render(
    <QueryClientProvider client={queryClient()}>
      <AuthProvider>
        <TimezoneProvider>
          <MemoryRouter initialEntries={["/logs"]}>
            <Routes>
              <Route element={<AppShell />}>
                <Route path="logs" element={<LogsPage />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </TimezoneProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("usage logs page", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("system.storage", JSON.stringify({ token: "stored-token" }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the required columns and backend throughput/cache values", async () => {
    installFetch();
    renderLogs();

    expect(await screen.findByText("时间 · 本地")).toBeInTheDocument();
    for (const heading of ["模型 / 路由", "用户", "密钥", "吞吐", "词元", "费用", "耗时", "详情"]) {
      expect(screen.getByRole("columnheader", { name: heading })).toBeInTheDocument();
    }

    const row = screen.getByTestId("log-row-req-1");
    expect(within(row).getByText("12.5 t/s")).toBeInTheDocument();
    expect(within(row).getByText(/25\.0%/)).toBeInTheDocument();
    expect(within(row).getAllByText("0.12s").length).toBeGreaterThan(0);
    expect(within(row).getByText("1.50s")).toBeInTheDocument();
    for (const label of ["首字", "首答", "总"]) {
      expect(within(row).getByText(label)).toBeInTheDocument();
    }
    expect(within(row).queryByText("尾字")).not.toBeInTheDocument();
    expect(within(row).getByText("200")).toBeInTheDocument();
    expect(within(row).getByText("流式")).toBeInTheDocument();
    expect(within(row).getByText("channel-a / fake-model")).toBeInTheDocument();
    expect(within(row).getByText("Alice")).toBeInTheDocument();
    expect(within(row).getByText("key-a")).toBeInTheDocument();
    expect(within(row).getByText("聊天")).toBeInTheDocument();
  });

  it("combines every filter and search into the request", async () => {
    const { calls } = installFetch();
    const user = userEvent.setup();
    renderLogs();

    await user.click(screen.getByRole("button", { name: "更多筛选" }));
    await user.click(screen.getByRole("combobox", { name: "渠道筛选" }));
    await user.click(await screen.findByRole("option", { name: "channel-a" }));

    await user.click(screen.getByRole("combobox", { name: "模型或组合筛选" }));
    await user.click(await screen.findByRole("option", { name: "combo-a" }));

    await chooseSelect(user, { triggerName: "状态筛选", optionName: "失败" });

    await user.click(screen.getByRole("combobox", { name: "用户筛选" }));
    await user.click(await screen.findByRole("option", { name: /Alice/ }));
    await user.click(screen.getByRole("combobox", { name: "密钥标识筛选" }));
    await user.click(await screen.findByRole("option", { name: "key-a" }));
    await chooseSelect(user, { triggerName: "流式筛选", optionName: "仅流式" });

    await user.type(screen.getByLabelText("搜索请求编号"), "gw-1");

    // 筛选草稿不再自动请求，必须点「搜索」才生效。
    await user.click(screen.getByRole("button", { name: /搜索/ }));

    await waitFor(() => {
      const matched = calls.some((call) => {
        const url = new URL(call, "http://testserver");
        return (
          url.searchParams.get("channel") === "channel-a" &&
          url.searchParams.get("model") === "combo-a" &&
          url.searchParams.get("status") === "error" &&
          url.searchParams.get("user") === "u1" &&
          url.searchParams.get("key_label") === "key-a" &&
          url.searchParams.get("stream") === "stream" &&
          url.searchParams.get("q") === "gw-1"
        );
      });
      expect(matched).toBe(true);
    });
  });

  it("labels a successful row that had a failed attempt and lists it in the drawer", async () => {
    const failover = makeLog({
      attempts: [
        {
          channel_slug: "channel-b",
          channel_name: "channel-b",
          upstream_model: "backup-model",
          status: "error",
          error_code: "INVALID_API_KEY",
          message: "invalid upstream key",
          ms: 40,
          round: 1,
        },
        {
          channel_slug: "channel-a",
          channel_name: "channel-a",
          upstream_model: "fake-model",
          status: "success",
          ms: 120,
          round: 2,
        },
      ],
      attempt_error_count: 1,
    });
    installFetch({ logs: makeList([failover]), detail: failover });
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    expect(within(row).getByText("含 1 次失败尝试")).toBeInTheDocument();
    // 整体成功，不伪造错误状态。
    expect(within(row).getByText("200")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "查看请求 req-1 详情" }));
    const drawer = await screen.findByRole("dialog", { name: "请求详情" });
    expect(within(drawer).getByText("失败尝试")).toBeInTheDocument();
    expect(within(drawer).getAllByText("channel-b").length).toBeGreaterThan(0);
    expect(within(drawer).getAllByText("密钥无效").length).toBeGreaterThan(0);
    expect(within(drawer).getByText("invalid upstream key")).toBeInTheDocument();
  });

  it("filters requests that had a failed attempt", async () => {
    const { calls } = installFetch();
    const user = userEvent.setup();
    renderLogs(["/logs"], true);

    await user.click(screen.getByRole("button", { name: "更多筛选" }));
    expect(screen.getByText("包含该渠道失败的请求")).toBeInTheDocument();
    await chooseSelect(user, { triggerName: "失败尝试筛选", optionName: "仅含失败尝试" });
    await user.click(screen.getByRole("button", { name: /搜索/ }));

    await waitFor(() =>
      expect(
        calls.some((call) => {
          const url = new URL(call, "http://testserver");
          return url.pathname === "/api/logs" && url.searchParams.get("attempt_status") === "error";
        }),
      ).toBe(true),
    );
    expect(screen.getByTestId("location-search").textContent).toContain("attempt_status=error");
    expect(screen.getByText("尝试：含失败尝试")).toBeInTheDocument();
  });

  it("pages through results and keeps page state in the URL", async () => {
    const { calls } = installFetch({
      logs: (url) => {
        const page = Number(url.searchParams.get("page") ?? 1);
        return page === 2
          ? makeList([makeLog({ id: "LOG2", request_id: "req-page-2", channel_name: "channel-page2" })], 50, 2)
          : makeList([makeLog()], 50, 1);
      },
    });
    const user = userEvent.setup();
    renderLogs(["/logs"], true);

    expect(await screen.findByText("第 1 / 2 页 · 共 50 条")).toBeInTheDocument();
    expect(calls.some((call) => call.includes("page_size=25"))).toBe(true);
    expect(screen.getByTestId("location-search").textContent).toBe("");
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(calls.some((call) => call.includes("page=2"))).toBe(true));

    expect(await screen.findByText(/channel-page2/)).toBeInTheDocument();
    expect(screen.getByText("第 2 / 2 页 · 共 50 条")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("location-search").textContent).toContain("page=2"),
    );
    // 默认每页条数不写进地址栏
    expect(screen.getByTestId("location-search").textContent).not.toContain("page_size");
  });

  it("hydrates page and page size from the URL", async () => {
    const { calls } = installFetch({
      logs: (url) => makeList([makeLog()], 120, Number(url.searchParams.get("page") ?? 1)),
    });
    renderLogs(["/logs?page=2&page_size=50"], true);

    await waitFor(() =>
      expect(
        calls.some((call) => call.includes("page=2") && call.includes("page_size=50")),
      ).toBe(true),
    );
  });

  it("expands a row inline and expands the channel routing path", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    await user.click(row);

    const inline = await screen.findByTestId("log-inline-detail");
    expect(within(inline).getByText("chatcmpl-up-1")).toBeInTheDocument();
    expect(within(inline).getByText(/输入 \/ 输出 \/ 缓存/)).toBeInTheDocument();

    await user.click(row);
    await waitFor(() => expect(screen.queryByTestId("log-inline-detail")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "展开路由链" }));
    const path = await screen.findByTestId("routing-path");
    expect(within(path).getByText("第 1 轮", { exact: false })).toBeInTheDocument();
    expect(within(path).getByText(/channel-a \/ fake-model/)).toBeInTheDocument();
  });

  it("opens the full detail drawer with attempts, params, headers and error body", async () => {
    installFetch({
      logs: makeList([
        makeLog({
          request_id: "req-error",
          http_status: 502,
          error_code: "UPSTREAM_ERROR",
          error_message: "upstream failed",
          error_body: '{"error":"upstream boom","api_key":"***"}',
        }),
      ]),
      detail: makeLog({
        request_id: "req-error",
        http_status: 502,
        error_code: "UPSTREAM_ERROR",
        error_message: "upstream failed",
        error_body: '{"error":"upstream boom","api_key":"***"}',
        attempts: [
          {
            channel_slug: "channel-a",
            channel_name: "channel-a",
            upstream_model: "fake-model",
            status: "error",
            error_code: "upstream",
            message: "boom",
            ms: 50,
            round: 1,
            priority_path: [1],
          },
        ],
        response_headers: { "x-upstream": "fake-upstream", "content-type": "application/json" },
      }),
    });
    const user = userEvent.setup();
    renderLogs();

    await user.click(
      await screen.findByRole("button", { name: "查看请求 req-error 详情" }),
    );

    const drawer = await screen.findByRole("dialog", { name: "请求详情" });
    expect(within(drawer).getByText("Attempts 时间线")).toBeInTheDocument();
    expect(within(drawer).getByText("参数摘要")).toBeInTheDocument();
    expect(within(drawer).getByText("请求头 / 响应头")).toBeInTheDocument();
    expect(within(drawer).getByText("脱敏错误原文")).toBeInTheDocument();
    expect(within(drawer).getByText("x-upstream")).toBeInTheDocument();
    expect(within(drawer).getByText(/upstream boom/)).toBeInTheDocument();
    expect(within(drawer).getByText(/temperature/)).toBeInTheDocument();

    await user.click(within(drawer).getByRole("button", { name: "关闭详情" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "请求详情" })).not.toBeInTheDocument());
  });

  it("switches the time column between local time and UTC from the topbar", async () => {
    installFetch();
    const user = userEvent.setup();
    renderShell();

    const local = formatDateTimeParts(CREATED_AT, "local");
    const utc = formatDateTimeParts(CREATED_AT, "utc");
    expect(local).not.toBeNull();
    expect(utc).not.toBeNull();

    expect(await screen.findByText("时间 · 本地")).toBeInTheDocument();
    expect(screen.getByText(`${local!.date} ${local!.time}`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换时间显示为UTC" }));

    expect(await screen.findByText("时间 · UTC")).toBeInTheDocument();
    expect(screen.getByText(`${utc!.date} ${utc!.time}`)).toBeInTheDocument();
    expect(screen.getByText("01:02:03", { exact: false })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换时间显示为本地" }));
    expect(await screen.findByText("时间 · 本地")).toBeInTheDocument();
  });

  it("shows the full time interval and timezone when the picker is hovered", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLogs();

    const trigger = await screen.findByRole("button", { name: /^时间范围：/ });
    await user.hover(trigger);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("时区：本地");
    expect(tooltip).toHaveTextContent(
      /\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}:\d{2}-\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}:\d{2}/,
    );
  });

  it("opens the time interval tooltip on keyboard focus", async () => {
    installFetch();
    renderLogs();

    const trigger = await screen.findByRole("button", { name: /^时间范围：/ });
    fireEvent.focus(trigger);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("时区：本地");
  });

  it("labels thinking-only streams as 仅推理 instead of a blank 首答", async () => {
    const silent = makeLog({ ttfa_ms: null, ttft_ms: 120 });
    installFetch({ logs: makeList([silent]), detail: silent });
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    expect(within(row).getByText("仅推理")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "查看请求 req-1 详情" }));
    const drawer = await screen.findByRole("dialog", { name: "请求详情" });
    expect(within(drawer).getByText("无正文与工具调用（仅推理）")).toBeInTheDocument();
  });

  it("keeps 首答 as a dash when the request never produced streaming timings", async () => {
    installFetch({ logs: makeList([makeLog({ is_stream: false, ttfa_ms: null, ttft_ms: null })]) });
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    expect(within(row).queryByText("仅推理")).not.toBeInTheDocument();
    expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows the probe source for probe-metrics logs", async () => {
    installFetch({ logs: makeList([makeLog({ source: "probe" })]) });
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    expect(within(row).getByText("探测")).toBeInTheDocument();
  });

  it("renders the metric strip with active-minute, sample and estimation hints", async () => {
    installFetch();
    renderLogs();

    const strip = await screen.findByLabelText("日志度量");
    expect(await within(strip).findByText("1.30M")).toBeInTheDocument();
    expect(within(strip).getByText("$1.2345")).toBeInTheDocument();
    expect(within(strip).getByText("1.50")).toBeInTheDocument();
    expect(within(strip).getByText("650.00K")).toBeInTheDocument();
    expect(within(strip).getByText("12.3%")).toBeInTheDocument();
    expect(within(strip).getAllByText("活跃 3 分钟")).toHaveLength(2);
    expect(within(strip).getByText("42 条样本")).toBeInTheDocument();
    expect(within(strip).getByText("输入 1.20M · 输出 100.00K")).toBeInTheDocument();
    expect(within(strip).getByText("3 次请求")).toBeInTheDocument();
    expect(within(strip).getAllByLabelText("其中 2 行无上游 usage，未计入").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle(/活跃口径/).length).toBeGreaterThanOrEqual(2);
  });

  it("only queries after the search button is pressed", async () => {
    const { calls } = installFetch();
    const user = userEvent.setup();
    renderLogs();

    await screen.findByTestId("log-row-req-1");
    const searchButton = screen.getByRole("button", { name: /搜索/ });
    expect(searchButton).toHaveAttribute("data-dirty", "false");

    await user.type(screen.getByLabelText("搜索请求编号"), "gw-1");
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(calls.some((call) => call.includes("q=gw-1"))).toBe(false);
    expect(searchButton).toHaveAttribute("data-dirty", "true");
    expect(searchButton.className).toContain("bg-primary");

    await user.click(searchButton);
    await waitFor(() => expect(calls.some((call) => call.includes("q=gw-1"))).toBe(true));
  });
  it("marks only the filter controls whose draft differs from the applied query", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLogs();

    await screen.findByTestId("log-row-req-1");
    const status = screen.getByLabelText("状态筛选");
    const keyword = screen.getByLabelText("搜索请求编号");
    expect(status.className).not.toContain("filter-dirty");
    expect(keyword.className).not.toContain("filter-dirty");

    await chooseSelect(user, { triggerName: "状态筛选", optionName: "失败" });
    expect(status.className).toContain("filter-dirty");
    expect(keyword.className).not.toContain("filter-dirty");

    await user.click(screen.getByRole("button", { name: /搜索/ }));
    await waitFor(() => expect(status.className).not.toContain("filter-dirty"));
  });

  it("hydrates filters from the URL and writes applied filters back", async () => {
    const { calls } = installFetch();
    const user = userEvent.setup();
    renderLogs(["/logs?q=gw-1&status=error"], true);

    const input = await screen.findByLabelText("搜索请求编号");
    await waitFor(() => expect(input).toHaveValue("gw-1"));
    expectSelectText("状态筛选", "失败");
    await waitFor(() => {
      expect(calls.some((call) => call.includes("q=gw-1") && call.includes("status=error"))).toBe(
        true,
      );
    });

    await user.clear(input);
    await user.type(input, "gw-2");
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByTestId("location-search").textContent).toContain("q=gw-2");
    });
    expect(screen.getByTestId("location-search").textContent).toContain("status=error");
    expect(calls.some((call) => call.includes("q=gw-2"))).toBe(true);
  });

  it("masks user, key and cost columns when sensitive data is hidden", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    expect(within(row).getByText("Alice")).toBeInTheDocument();
    expect(within(row).getByText("key-a")).toBeInTheDocument();
    expect(within(row).getByText(/^\$/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "隐藏敏感信息" }));

    await waitFor(() => expect(within(row).queryByText("Alice")).not.toBeInTheDocument());
    expect(within(row).queryByText("key-a")).not.toBeInTheDocument();
    expect(within(row).queryByText(/^\$/)).not.toBeInTheDocument();
    expect(within(row).getAllByText("•••").length).toBeGreaterThanOrEqual(3);
    expect(JSON.parse(localStorage.getItem("system.storage") ?? "{}").logsHideSensitive).toBe(true);

    await user.click(screen.getByRole("button", { name: "显示敏感信息" }));
    await waitFor(() => expect(within(row).getByText("Alice")).toBeInTheDocument());
    expect(JSON.parse(localStorage.getItem("system.storage") ?? "{}").logsHideSensitive).toBe(false);
  });

  it("masks cost in the expanded row and the full detail drawer", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    await user.click(row);
    const inline = await screen.findByTestId("log-inline-detail");
    expect(within(inline).getByText(/^\$/)).toBeInTheDocument();
    expect(within(inline).getByTitle("首字 0.12s")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "隐藏敏感信息" }));
    await waitFor(() => expect(within(inline).queryByText(/^\$/)).not.toBeInTheDocument());
    expect(within(inline).getAllByText("•••").length).toBeGreaterThan(0);

    await user.click(within(row).getByRole("button", { name: "查看请求 req-1 详情" }));
    const drawer = await screen.findByRole("dialog", { name: "请求详情" });
    await waitFor(() => expect(within(drawer).queryByText("Alice")).not.toBeInTheDocument());
    expect(within(drawer).queryByText("key-a")).not.toBeInTheDocument();
    expect(within(drawer).getAllByText("•••").length).toBeGreaterThanOrEqual(3);
  });

  it("expands a row from the keyboard with aria-expanded", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    // aria-expanded 挂在第一列的行详情按钮上（tr 不允许 aria-expanded），名字随状态变化。
    const toggle = within(row).getByRole("button", { name: "展开行详情" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    toggle.focus();
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(within(row).getByRole("button", { name: "收起行详情" })).toHaveAttribute("aria-expanded", "true"),
    );
    expect(await screen.findByTestId("log-inline-detail")).toBeInTheDocument();
    await user.keyboard(" ");
    await waitFor(() =>
      expect(within(row).getByRole("button", { name: "展开行详情" })).toHaveAttribute("aria-expanded", "false"),
    );
  });

  it("shows a dash when the total duration is missing", async () => {
    const missing = makeLog({ total_ms: 0, ttft_ms: 120, ttfa_ms: 150 });
    installFetch({ logs: makeList([missing]), detail: missing });
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    await user.click(row);
    const inline = await screen.findByTestId("log-inline-detail");
    expect(within(inline).getAllByLabelText("总耗时无数据").length).toBeGreaterThan(0);
  });
});

describe("request detail drawer addresses and headers", () => {
  it("shows the client address and forwarded addresses with raw forwarding headers on hover", async () => {
    const detail = makeDetailLog({
      client_ip: "203.0.113.9",
      forwarded_ips: ["1.1.1.1", "2.2.2.2"],
      forwarded_sources: [
        { header: "X-Forwarded-For", value: "1.1.1.1, 2.2.2.2" },
        { header: "X-Real-IP", value: "2.2.2.2" },
      ],
    });
    installFetch({ detail });
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    await user.click(within(row).getByRole("button", { name: "查看请求 req-1 详情" }));
    const drawer = await screen.findByRole("dialog", { name: "请求详情" });

    expect(within(drawer).getByTestId("log-source-address")).toHaveTextContent("203.0.113.9");
    const forwarded = within(drawer).getByTestId("log-forwarded-address");
    expect(forwarded).toHaveTextContent("1.1.1.1, 2.2.2.2");
    expect(forwarded).toHaveAttribute("title", "X-Forwarded-For:1.1.1.1, 2.2.2.2;X-Real-IP:2.2.2.2");
  });

  it("falls back to a dash when no client or forwarded address was recorded", async () => {
    installFetch({ detail: makeDetailLog() });
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    await user.click(within(row).getByRole("button", { name: "查看请求 req-1 详情" }));
    const drawer = await screen.findByRole("dialog", { name: "请求详情" });

    expect(within(drawer).getByTestId("log-source-address")).toHaveTextContent("—");
    expect(within(drawer).getByTestId("log-forwarded-address")).toHaveTextContent("—");
    expect(within(drawer).getByTestId("log-forwarded-address")).not.toHaveAttribute("title");
  });

  it("collapses the upstream request headers by default and expands them on demand", async () => {
    const attempt = {
      ...makeLog().attempts[0],
      request_headers: { authorization: "Bearer ***", "x-upstream-header": "kept" },
    };
    const detail = makeDetailLog({
      request_headers: { "x-client": "slipstream-web" },
      response_headers: { "x-upstream": "fake-upstream" },
      attempts: [attempt],
    });
    installFetch({ detail });
    const user = userEvent.setup();
    renderLogs();

    const row = await screen.findByTestId("log-row-req-1");
    await user.click(within(row).getByRole("button", { name: "查看请求 req-1 详情" }));
    const drawer = await screen.findByRole("dialog", { name: "请求详情" });

    expect(within(drawer).getByText("请求头 / 响应头")).toBeInTheDocument();
    expect(within(drawer).getByText("x-client")).toBeInTheDocument();
    expect(within(drawer).getByText("x-upstream")).toBeInTheDocument();

    const upstreamRequest = within(drawer).getByRole("button", { name: /上游请求头/ });
    expect(upstreamRequest).toHaveAttribute("aria-expanded", "false");
    expect(upstreamRequest).toHaveAttribute("aria-controls", "upstream-request-headers-body");
    expect(document.getElementById("upstream-request-headers-body")).toBeNull();
    expect(within(drawer).queryByText("x-upstream-header")).not.toBeInTheDocument();

    await user.click(upstreamRequest);
    expect(upstreamRequest).toHaveAttribute("aria-expanded", "true");
    expect(within(drawer).getByText("x-upstream-header")).toBeInTheDocument();
    expect(within(drawer).getByText("kept")).toBeInTheDocument();

    await user.click(upstreamRequest);
    expect(upstreamRequest).toHaveAttribute("aria-expanded", "false");
    expect(within(drawer).queryByText("x-upstream-header")).not.toBeInTheDocument();
  });
});


describe("usage logs aggregate views", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("system.storage", JSON.stringify({ token: "stored-token" }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("switches to combo view through the URL and renders group rows", async () => {
    const group = makeGroup();
    const failing = makeGroup({
      key: "combo-b",
      label: "combo-b",
      requests: 6,
      errors: 1,
      prompt_tokens: 2_400_000,
      completion_tokens: 12_000,
    });
    const comboList: RequestLogList = {
      items: [],
      total: 2,
      page: 1,
      page_size: 25,
      groups: [group, failing],
      view: "combo",
    };
    const { calls } = installFetch({
      logs: (url) => (url.searchParams.get("view") === "combo" ? comboList : makeList([makeLog()])),
    });
    const user = userEvent.setup();
    renderLogs(["/logs?q=minimax&channel=channel-a"], true);

    await user.click(screen.getByRole("radio", { name: "按组合" }));
    await waitFor(() => {
      const search = screen.getByTestId("location-search");
      expect(search).toHaveTextContent("view=combo");
      expect(search).toHaveTextContent("q=minimax");
      expect(search).toHaveTextContent("channel=channel-a");
    });
    const row = await screen.findByTestId("log-group-row-combo-a");
    expect(within(row).getByText("combo-a")).toBeInTheDocument();
    expect(within(row).getByText("0 / 3")).toBeInTheDocument();
    // 副行不再重复请求数（徽标分母与模型列各写了一次），只补徽标没有的失败率。
    expect(within(row).getByText("无失败")).toBeInTheDocument();
    // 聚合行词元只给量级，精确值留在 title 上。
    expect(within(row).getByText("1.20K / 400")).toBeInTheDocument();
    expect(within(row).getByTitle("输入 1,200 · 输出 400")).toBeInTheDocument();

    const failingRow = await screen.findByTestId("log-group-row-combo-b");
    expect(within(failingRow).getByText("失败率 16.67%")).toBeInTheDocument();
    expect(within(failingRow).getByText("2.40M / 12.00K")).toBeInTheDocument();
    expect(
      calls.some(
        (url) =>
          url.includes("view=combo") &&
          url.includes("q=minimax") &&
          url.includes("channel=channel-a"),
      ),
    ).toBe(true);
  });

  it("hydrates status view and passes the backend segment key through unchanged", async () => {
    const segmentKey = "status:429:1794719000000:1794719400000:deepseek-flash";
    const group = makeGroup({
      key: segmentKey,
      label: "deepseek-flash",
      status_code: 429,
      status_label: "触发限流",
      attempts: 42,
      errors: 17,
      channels: ["channel-a", "channel-b", "channel-c", "channel-d"],
      error_codes: ["UPSTREAM_RATE_LIMITED"],
    });
    const list: RequestLogList = {
      items: [],
      total: 1,
      page: 1,
      page_size: 25,
      groups: [group],
      view: "status",
    };
    const { calls } = installFetch({
      logs: (url) =>
        url.searchParams.get("view") === "status" ? list : makeList([makeLog()]),
    });
    const user = userEvent.setup();
    renderLogs(["/logs?view=status&q=minimax"]);

    const row = await screen.findByTestId(`log-group-row-${segmentKey}`);
    expect(within(row).getByText("429")).toBeInTheDocument();
    expect(within(row).getByText("channel-a · channel-b · channel-c")).toBeInTheDocument();
    expect(within(row).getByText("段内 42 次尝试")).toBeInTheDocument();
    expect(
      calls.some(
        (url) => url.includes("view=status") && url.includes("q=minimax"),
      ),
    ).toBe(true);

    await user.click(
      within(row).getByRole("button", { name: "展开 deepseek-flash 组内请求" }),
    );
    expect(await screen.findByTestId("log-row-req-1")).toBeInTheDocument();
    expect(
      calls.some(
        (url) =>
          url.includes("view=requests") &&
          url.includes("group_key=" + encodeURIComponent(segmentKey)) &&
          url.includes("q=minimax"),
      ),
    ).toBe(true);
  });

  it("expands a group with view=requests and group_key, reusing request rows", async () => {
    const comboList: RequestLogList = {
      items: [],
      total: 1,
      page: 1,
      page_size: 25,
      groups: [makeGroup()],
      view: "combo",
    };
    const { calls } = installFetch({
      logs: (url) => {
        if (url.searchParams.get("view") === "combo") return comboList;
        if (url.searchParams.get("group_key")) return makeList([makeLog()]);
        return makeList([makeLog()]);
      },
    });
    const user = userEvent.setup();
    renderLogs(["/logs?view=combo&q=minimax"]);

    const row = await screen.findByTestId("log-group-row-combo-a");
    await user.click(within(row).getByRole("button", { name: "展开 combo-a 组内请求" }));
    expect(await screen.findByTestId("log-row-req-1")).toBeInTheDocument();
    expect(
      calls.some(
        (url) =>
          url.includes("view=requests") &&
          url.includes("group_key=combo-a") &&
          url.includes("q=minimax"),
      ),
    ).toBe(true);
  });

  it("shows the scan limited warning above the aggregate table", async () => {
    const list: RequestLogList = {
      items: [],
      total: 1,
      page: 1,
      page_size: 25,
      groups: [makeGroup()],
      view: "combo",
      scan_limited: true,
    };
    installFetch({ logs: list });
    renderLogs(["/logs?view=combo"]);

    expect(
      await screen.findByText("命中超过 5 万条，已按最近 5 万条聚合，请收窄时间范围"),
    ).toBeInTheDocument();
  });

  it("folds failed and skipped attempts and keeps their real durations", async () => {
    const log = makeLog({
      attempts: [
        {
          channel_slug: "channel-a",
          channel_name: "channel-a",
          upstream_model: "fake-model",
          status: "success",
          ms: 120,
          round: 1,
          priority_path: [1],
        },
        {
          channel_slug: "channel-b",
          channel_name: "channel-b",
          upstream_model: "backup-model",
          status: "error",
          error_code: "UPSTREAM_ERROR",
          message: "boom",
          ms: 44_750,
          round: 2,
          priority_path: [2],
        },
        {
          channel_slug: "channel-c",
          channel_name: "channel-c",
          upstream_model: "fallback-model",
          status: "skipped",
          error_code: "MEMBER_DISABLED",
          skipped: true,
          round: 3,
          priority_path: [3],
        },
      ],
    });
    installFetch({ logs: makeList([log]) });
    const user = userEvent.setup();
    renderLogs(["/logs"]);

    await user.click(await screen.findByRole("button", { name: "展开路由链" }));
    const dialog = screen.getByTestId("routing-path");
    const failedSummary = within(dialog).getByRole("button", { name: "展开第 2 轮失败尝试详情" });
    const detailId = failedSummary.getAttribute("aria-controls");
    expect(detailId).toBeTruthy();
    expect(failedSummary).toHaveAttribute("aria-expanded", "false");
    expect(failedSummary).toHaveTextContent(/channel-b · backup-model/);
    expect(failedSummary).toHaveTextContent(/上游返回错误 · boom/);
    expect(failedSummary).toHaveTextContent("优先级 2 · 44.75s");
    expect(within(dialog).getByRole("button", { name: "展开第 3 轮失败尝试详情" })).toHaveTextContent(
      "未发出请求",
    );
    expect(within(dialog).queryByText("收起")).not.toBeInTheDocument();

    await user.click(failedSummary);
    expect(within(dialog).getByText("优先级 2 · 44.75s")).toBeInTheDocument();
    expect(within(dialog).getByText("上游返回错误 · boom")).toBeInTheDocument();
    const expandedSummary = within(dialog).getByRole("button", { name: "收起第 2 轮失败尝试详情" });
    expect(expandedSummary).toHaveAttribute("aria-expanded", "true");
    expect(expandedSummary).toHaveAttribute("aria-controls", detailId!);

    await user.click(within(dialog).getByText("优先级 2 · 44.75s"));
    const collapsedAgain = within(dialog).getByRole("button", { name: "展开第 2 轮失败尝试详情" });
    expect(collapsedAgain).toHaveAttribute("aria-expanded", "false");
    expect(collapsedAgain).toHaveAttribute("aria-controls", detailId!);
    expect(document.getElementById(detailId!)).toHaveAttribute("hidden");
  });
});

describe("log metric formatting", () => {
  it("re-exports the shared formatters from lib/format verbatim", () => {
    expect(logsFormatCompact).toBe(formatCompact);
    expect(logsFormatUsdCompact).toBe(formatUsdCompact);
    expect(logsFormatRate).toBe(formatRate);
    expect(logsFormatCacheHitRate).toBe(formatCacheHitRate);
    expect(logsFormatSpanMinutes).toBe(formatSpanMinutes);
  });
});

describe("log query state", () => {
  it("round-trips filters and omits default values", () => {
    const defaults = parseAppliedQuery(new URLSearchParams(""));
    expect(defaults.page).toBe(1);
    expect(defaults.pageSize).toBe(25);
    expect(defaults.range).toBe("today");
    expect(logQueryToParams(defaults).toString()).toBe("");

    const params = new URLSearchParams(
      "q=gw-1&range=custom&start=2026-11-10&end=2026-11-11&status=error&attempt_status=error&stream=stream&user=u1&key=key-a&channel=channel-a&model=model-a&page=3&page_size=50",
    );
    const state = parseAppliedQuery(params);
    expect(state).toMatchObject({
      q: "gw-1",
      range: "custom",
      customStart: "2026-11-10",
      customEnd: "2026-11-11",
      status: "error",
      attemptStatus: "error",
      stream: "stream",
      user: "u1",
      keyLabel: "key-a",
      channel: "channel-a",
      model: "model-a",
      page: 3,
      pageSize: 50,
    });
    const round = logQueryToParams(state).toString();
    expect(round).toContain("q=gw-1");
    expect(round).toContain("start=2026-11-10");
    expect(round).toContain("page=3");
    expect(round).toContain("page_size=50");
  });

  it("round-trips the aggregate view and omits the default", () => {
    const defaults = parseAppliedQuery(new URLSearchParams(""));
    expect(defaults.view).toBe("requests");
    expect(logQueryToParams(defaults).toString()).toBe("");

    const combo = parseAppliedQuery(new URLSearchParams("view=combo"));
    expect(combo.view).toBe("combo");
    expect(logQueryToParams(combo).toString()).toContain("view=combo");

    const status = parseAppliedQuery(new URLSearchParams("view=status"));
    expect(status.view).toBe("status");
    expect(logQueryToParams(status).toString()).toContain("view=status");

    expect(parseAppliedQuery(new URLSearchParams("view=bogus")).view).toBe("requests");
  });

  it("falls back to defaults for invalid values", () => {
    const state = parseAppliedQuery(
      new URLSearchParams("status=weird&stream=sse&page=0&page_size=abc&range=nope"),
    );
    expect(state.status).toBe("");
    expect(state.attemptStatus).toBe("");
    expect(state.stream).toBe("");
    expect(state.page).toBe(1);
    expect(state.pageSize).toBe(25);
    expect(state.range).toBe("today");
  });
});

describe("failed attempt helpers", () => {
  it("prefers the aggregated count and falls back to parsing attempts", () => {
    const attempts = [
      { status: "error", error_code: "UPSTREAM_ERROR" },
      { status: "skipped" },
      { status: "success" },
    ];
    expect(failedAttempts({ attempts }).length).toBe(1);
    expect(attemptErrorCount({ attempts })).toBe(1);
    expect(hasFailedAttempts({ attempts })).toBe(true);
    expect(attemptErrorCount({ attempts, attempt_error_count: 3 })).toBe(3);
    expect(hasFailedAttempts({ attempts: [{ status: "success" }] })).toBe(false);
    expect(hasFailedAttempts({ attempts: [] })).toBe(false);
  });
});

describe("log metric builder", () => {
  it("builds the five metrics from the summary and flags estimated rows", () => {
    const byKey = new Map(
      buildLogMetrics(makeSummary({ estimated_rows: 7 })).map((metric) => [metric.key, metric]),
    );
    expect(byKey.get("usage")?.value).toBe("1.30M");
    expect(byKey.get("usage")?.estimated).toBe(true);
    expect(byKey.get("usage")?.estimatedTitle).toBe("其中 7 行无上游 usage，未计入");
    expect(byKey.get("cost")?.value).toBe("$1.2345");
    expect(byKey.get("rpm")?.value).toBe("1.50");
    expect(byKey.get("rpm")?.hint).toBe("活跃 3 分钟");
    expect(byKey.get("tpm")?.value).toBe("650.00K");
    expect(byKey.get("tpm")?.estimated).toBe(true);
    expect(byKey.get("cache")?.value).toBe("12.3%");
    expect(byKey.get("cache")?.hint).toBe("42 条样本");
    expect(byKey.get("cache")?.title).toContain("7 条估算行未计入");
  });
});
