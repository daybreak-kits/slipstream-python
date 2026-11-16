import { expect, test, type Page } from "@playwright/test";

const CREATED_AT = Date.parse("2026-11-01T09:30:00Z");
// 时间区间按钮显示的是「现在」推算出的真实区间；冻结时钟后才能稳定录基线。
const FROZEN_NOW = new Date("2026-11-01T09:30:00Z");

const FREEZE_CSS = `
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    caret-color: transparent !important;
  }
  html { scrollbar-width: none; }
`;

interface Attempt {
  channel_model_id: string;
  channel_slug: string;
  channel_name: string;
  upstream_model: string;
  status: "success" | "error" | "skipped" | "stopped";
  error_code?: string | null;
  message?: string;
  ms: number;
  round: number;
  skipped: boolean;
  priority_path: number[];
}

interface LogRow {
  id: string;
  request_id: string;
  upstream_request_id: string;
  user_id: string;
  user_name: string;
  api_key_id: string;
  api_key_label: string;
  requested_model: string;
  channel_name: string;
  upstream_model: string;
  response_model: string;
  is_stream: boolean;
  http_status: number;
  error_code: string | null;
  error_message: string;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_ratio: number;
  cost_usd: number;
  ttft_ms: number | null;
  ttfa_ms: number | null;
  ttlt_ms: number | null;
  total_ms: number;
  throughput_tps: number | null;
  usage_estimated: boolean;
  attempts: Attempt[];
  rewrite_applied: boolean;
  rewrite_skipped: string;
  rewrite_diff: Record<string, unknown>;
  params: Record<string, unknown>;
  response_headers: Record<string, string>;
  error_body: string | null;
  created_at: number;
}

const FAILOVER_ATTEMPTS: Attempt[] = [
  {
    channel_model_id: "cm-1",
    channel_slug: "channel-a",
    channel_name: "channel-a",
    upstream_model: "model-a-001",
    status: "error",
    error_code: "RATE_LIMITED",
    message: "HTTP 429，按优先级换路",
    ms: 640,
    round: 1,
    skipped: false,
    priority_path: [10, 20],
  },
  {
    channel_model_id: "cm-2",
    channel_slug: "channel-b",
    channel_name: "channel-b",
    upstream_model: "model-a-002",
    status: "success",
    error_code: null,
    message: "上游返回 200",
    ms: 8500,
    round: 2,
    skipped: false,
    priority_path: [10, 20],
  },
  {
    channel_model_id: "cm-3",
    channel_slug: "channel-c",
    channel_name: "channel-c",
    upstream_model: "model-b-001",
    status: "skipped",
    error_code: null,
    message: "前序尝试已成功",
    ms: 0,
    round: 3,
    skipped: true,
    priority_path: [10, 20],
  },
];

function makeLogs(): LogRow[] {
  return Array.from({ length: 100 }, (_, index) => {
    const first = index === 0;
    const failed = index % 9 === 4;
    return {
      id: `log-${index + 1}`,
      request_id: `req-20261101-${String(index + 1).padStart(4, "0")}`,
      upstream_request_id: `up-${String(index + 1).padStart(4, "0")}`,
      user_id: `user-${(index % 4) + 1}`,
      user_name: ["alice", "bob", "carol", "dave"][index % 4],
      api_key_id: `key-${(index % 3) + 1}`,
      api_key_label: `key-${(index % 3) + 1}`,
      requested_model: first ? "channel-a/model-a" : `channel-${String.fromCharCode(97 + (index % 3))}/model-${String.fromCharCode(97 + (index % 3))}`,
      channel_name: first ? "channel-b" : `channel-${String.fromCharCode(97 + (index % 3))}`,
      upstream_model: first ? "model-a-002" : `model-${String.fromCharCode(97 + (index % 3))}-00${(index % 3) + 1}`,
      response_model: first ? "model-a-002" : `model-${String.fromCharCode(97 + (index % 3))}-00${(index % 3) + 1}`,
      is_stream: index % 3 !== 2,
      http_status: failed ? 500 : 200,
      error_code: failed ? "UPSTREAM_ERROR" : null,
      error_message: failed ? "上游返回 500，已记录请求体摘要" : "",
      prompt_tokens: 240 + (index % 7) * 13,
      completion_tokens: 80 + (index % 11) * 9,
      cached_tokens: index % 4 === 0 ? 120 : 0,
      cache_ratio: index % 4 === 0 ? 0.5 : 0,
      cost_usd: 0.000012 * (index + 1),
      ttft_ms: first ? 1200 : failed ? null : 900 + (index % 8) * 260,
      ttfa_ms: first ? 1800 : failed ? null : 1300 + (index % 8) * 240,
      ttlt_ms: first ? 8400 : failed ? null : 4200 + (index % 10) * 700,
      total_ms: first ? 8500 : failed ? 6100 : 4300 + (index % 10) * 720,
      throughput_tps: failed ? null : 28.4 + (index % 6) * 1.7,
      usage_estimated: index % 13 === 0,
      attempts: first ? FAILOVER_ATTEMPTS : [],
      rewrite_applied: first,
      rewrite_skipped: first ? "stream" : "",
      rewrite_diff: first
        ? { "messages.0.content": { before: "你好", after: "你好 请简洁回答。" } }
        : {},
      params: { temperature: 0.2, stream: index % 3 !== 2 },
      response_headers: { "x-gateway-route": "channel-b" },
      error_body: null,
      created_at: CREATED_AT - index * 3 * 60_000,
    };
  });
}

function summary() {
  return {
    requests: 1284,
    prompt_tokens: 2_840_112,
    completion_tokens: 984_450,
    cached_tokens: 512_000,
    cost_usd: 12.483921,
    errors: 17,
    error_rate: 0.0132,
    p95_ttft_ms: 3200,
    p95_total_ms: 12_400,
    latency: {
      samples: 128,
      ttft: { p50: 1200, p90: 2600, p95: 3200, p99: 4100 },
      ttfa: { p50: 1800, p90: 3200, p95: 3900, p99: 4800 },
      ttlt: { p50: 8400, p90: 11_200, p95: 12_800, p99: 15_600 },
      total: { p50: 8500, p90: 11_400, p95: 13_100, p99: 16_200 },
    },
    start_ms: Date.parse("2026-10-31T09:30:00Z"),
    end_ms: Date.parse("2026-11-01T09:30:00Z"),
  };
}

function timeseries() {
  const start = Date.parse("2026-10-31T10:00:00Z");
  const points = Array.from({ length: 24 }, (_, index) => ({
    start_ms: start + index * 3_600_000,
    requests: 20 + ((index * 7) % 41),
    prompt_tokens: 40_000 + index * 2500,
    completion_tokens: 12_000 + index * 800,
    cost_usd: 0.28 + index * 0.02,
    errors: index % 6 === 0 ? 2 : 0,
    p95_ttft_ms: 2600 + (index % 5) * 180,
    p95_total_ms: 9800 + (index % 7) * 420,
    p95_ttfa_ms: 3200 + (index % 5) * 210,
    p95_ttlt_ms: 10_600 + (index % 7) * 390,
    by_model: [
      { name: "model-a", requests: 12 + (index % 5), prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, errors: 0, p95_total_ms: 9000 },
      { name: "model-b", requests: 7 + (index % 4), prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, errors: 0, p95_total_ms: 11_000 },
    ],
    // 上游模型与上游渠道是后端 753313d 起必返的字段，看板三个维度都要有数据。
    by_upstream_model: [
      { name: "model-a", requests: 15 + (index % 4), prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, errors: 0, p95_total_ms: 9100 },
      { name: "model-b", requests: 5 + (index % 3), prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, errors: 0, p95_total_ms: 10_900 },
    ],
    by_channel: [
      { name: "channel-a", requests: 14 + (index % 3), prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, errors: 0, p95_total_ms: 9200 },
      { name: "channel-b", requests: 6 + (index % 2), prompt_tokens: 0, completion_tokens: 0, cost_usd: 0, errors: 0, p95_total_ms: 10_400 },
    ],
  }));
  return {
    range: "24h",
    timezone: "Asia/Shanghai",
    granularity: "hour",
    start_ms: start,
    end_ms: start + 24 * 3_600_000,
    points,
    top_models: [
      { name: "model-a", requests: 642 },
      { name: "model-b", requests: 418 },
      { name: "model-c", requests: 224 },
    ],
    top_channels: [
      { name: "channel-a", requests: 712 },
      { name: "channel-b", requests: 396 },
      { name: "channel-c", requests: 176 },
    ],
    top_upstream_models: [
      { name: "model-a", requests: 660 },
      { name: "model-b", requests: 402 },
      { name: "model-c", requests: 190 },
    ],
    error_distribution: [
      { code: "RATE_LIMITED", count: 9 },
      { code: "UPSTREAM_TIMEOUT", count: 5 },
      { code: "UPSTREAM_ERROR", count: 3 },
    ],
    heatmap: Array.from({ length: 7 * 24 }, (_, index) => ({
      weekday: Math.floor(index / 24),
      hour: index % 24,
      requests: (index * 13) % 31,
    })),
  };
}

function flows() {
  return {
    metric: "requests",
    stages: ["user", "key", "model", "upstream", "channel"],
    nodes: [
      { id: "user:admin", stage: "user", label: "admin", value: 1284, share: 1 },
      { id: "key:key-1", stage: "key", label: "key-1", value: 1284, share: 1 },
      { id: "model:model-a", stage: "model", label: "model-a", value: 712, share: 0.554517 },
      { id: "model:model-b", stage: "model", label: "model-b", value: 572, share: 0.445483 },
      { id: "upstream:model-a", stage: "upstream", label: "model-a", value: 712, share: 0.554517 },
      { id: "upstream:model-b", stage: "upstream", label: "model-b", value: 572, share: 0.445483 },
      { id: "channel:channel-a", stage: "channel", label: "channel-a", value: 516, share: 0.401869 },
      { id: "channel:channel-b", stage: "channel", label: "channel-b", value: 596, share: 0.464174 },
      { id: "channel:channel-c", stage: "channel", label: "channel-c", value: 172, share: 0.133957 },
    ],
    links: [
      { source: "user:admin", target: "key:key-1", value: 1284 },
      { source: "key:key-1", target: "model:model-a", value: 712 },
      { source: "key:key-1", target: "model:model-b", value: 572 },
      { source: "model:model-a", target: "upstream:model-a", value: 712 },
      { source: "model:model-b", target: "upstream:model-b", value: 572 },
      { source: "upstream:model-a", target: "channel:channel-a", value: 516 },
      { source: "upstream:model-a", target: "channel:channel-b", value: 196 },
      { source: "upstream:model-b", target: "channel:channel-b", value: 400 },
      { source: "upstream:model-b", target: "channel:channel-c", value: 172 },
    ],
    total: { requests: 1284, prompt_tokens: 2_840_112, completion_tokens: 984_450 },
  };
}

function channel() {
  return {
    id: "ch-1",
    slug: "channel-a",
    name: "channel-a",
    note: "演示渠道，用于视觉回归",
    connection_url: "https://upstream.example.com/v1",
    endpoint_preview: "https://upstream.example.com/v1/chat/completions",
    models_endpoint_preview: "https://upstream.example.com/v1/models",
    api_key_set: true,
    api_key_masked: "••••••abcd",
    api_key_count: 2,
    api_key_masks: ["••••••abcd", "••••••wxyz"],
    api_keys: [
      { id: "key-1", label: "primary", masked: "••••••abcd", enabled: true, last_used_at: CREATED_AT, created_at: CREATED_AT },
    ],
    protocol: "openai_chat",
    protocol_source: "manual",
    auth_style: "bearer",
    priority: 10,
    enabled: true,
    connection_status: "ok",
    connection_latency_ms: 128,
    connection_message: "连接正常",
    connection_tested_at: CREATED_AT,
    last_model_probe_latency_ms: 160,
    last_model_probe_at: CREATED_AT,
    last_model_probe_status: "ok",
    override_sections: ["request_body"],
    rewrite_rules: {
      request_body: [
        {
          note: "统一温度",
          mode: "update",
          path: "temperature",
          value: 0.2,
          logic: "AND",
          conditions: [
            { path: "model", mode: "full", value: "channel-a/model-a" },
          ],
        },
        {
          note: "追加系统提示",
          mode: "append",
          path: "messages.0.content",
          value: " 请简洁回答。",
        },
      ],
    },
    model_count: 3,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
  };
}

async function json(route: import("@playwright/test").Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installApiMocks(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      return json(route, {
        id: "admin-1",
        username: "admin",
        created_at: CREATED_AT,
        updated_at: CREATED_AT,
      });
    }
    if (path === "/api/logs/filters") {
      return json(route, {
        channels: ["channel-a", "channel-b", "channel-c"],
        models: ["channel-a/model-a", "channel-b/model-b", "channel-c/model-c"],
        key_labels: ["key-1", "key-2", "key-3"],
        users: [
          { id: "user-1", name: "alice" },
          { id: "user-2", name: "bob" },
          { id: "user-3", name: "carol" },
          { id: "user-4", name: "dave" },
        ],
        truncated: { channels: false, models: false, key_labels: false, users: false },
      });
    }
    if (path === "/api/logs") {
      const logs = makeLogs();
      const pageSize = Number(url.searchParams.get("page_size") ?? 20);
      const page = Number(url.searchParams.get("page") ?? 1);
      return json(route, {
        items: logs.slice((page - 1) * pageSize, page * pageSize),
        total: logs.length,
        page,
        page_size: pageSize,
      });
    }
    if (path === "/api/logs/summary") {
      return json(route, {
        requests: 42,
        prompt_tokens: 240,
        completion_tokens: 80,
        cached_tokens: 120,
        cost_usd: 0.000012,
        cache_hit_rate: 0.5,
        cache_hit_rate_samples: 24,
        active_minutes: 12,
        span_minutes: 60,
        rpm: 3,
        tpm: 320,
        peak_rpm: 7,
        estimated_rows: 3,
      });
    }
    if (path.startsWith("/api/logs/")) {
      return json(route, makeLogs()[0]);
    }
    if (path === "/api/dashboard/summary") {
      return json(route, summary());
    }
    if (path === "/api/dashboard/timeseries") {
      return json(route, timeseries());
    }
    if (path === "/api/dashboard/flows") {
      return json(route, flows());
    }
    if (path === "/api/channels/ch-1") {
      return json(route, channel());
    }
    if (path === "/api/channels") {
      return json(route, { items: [channel()], total: 1 });
    }
    if (path === "/api/settings") {
      return json(route, {
        body_policy: "off",
        body_truncate_bytes: 16384,
        body_retention_days: 7,
        summary_max_bytes: 65536,
        updated_at: null,
        updated_by: "",
        log_retention_days: 30,
        redaction_forced: true,
        gateway_base_url: "",
        gateway_updated_at: null,
        gateway_updated_by: "",
      });
    }
    return json(route, { code: "NOT_MOCKED", message: path }, 404);
  });
}

async function signIn(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("system.storage", JSON.stringify({ token: "e2e-token" }));
  });
}

async function freezeUi(page: Page) {
  await page.addStyleTag({ content: FREEZE_CSS });
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
  await page.clock.setFixedTime(FROZEN_NOW);
});

test("login light", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("system.storage", JSON.stringify({ theme: "light" }));
  });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await freezeUi(page);
  await expect(page).toHaveScreenshot("login-light.png");
});

test("login dark", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.removeItem("system.storage");
  });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await page.getByRole("button", { name: /切换主题/ }).click();
  await page.getByRole("menuitemradio", { name: /深色/ }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await freezeUi(page);
  await expect(page).toHaveScreenshot("login-dark.png");
});

test("logs 100 rows with sticky header", async ({ page }) => {
  await signIn(page);
  await page.goto("/logs");
  await expect(page.getByRole("heading", { name: "使用日志" })).toBeVisible();
  // Radix Select：点触发器再点选项，不再是原生 select。
  await page.getByRole("combobox", { name: "每页行数" }).click();
  await page.getByRole("option", { name: "100", exact: true }).click();
  await expect(page.locator('[data-testid^="log-row-"]')).toHaveCount(100);
  await expect(page.getByText(/共 100 条/)).toBeVisible();
  await freezeUi(page);
  await expect(page).toHaveScreenshot("logs-100-rows.png");
});

test("expanded log with failover route and three latencies", async ({ page }) => {
  await signIn(page);
  await page.goto("/logs");
  await expect(page.getByRole("heading", { name: "使用日志" })).toBeVisible();
  const row = page.getByTestId("log-row-req-20261101-0001");
  await expect(row).toBeVisible();
  await row.click();
  const detail = page.getByTestId("log-inline-detail");
  await expect(detail).toBeVisible();
  await expect(detail.getByText(/channel-a \/ model-a-001 → channel-b \/ model-a-002/)).toBeVisible();
  await expect(detail.getByText(/首字/).first()).toBeVisible();
  await expect(detail.getByText(/首答/).first()).toBeVisible();
  await expect(detail.getByText(/总耗时/).first()).toBeVisible();
  await expect(detail.getByText(/尾字/)).toHaveCount(0);
  await freezeUi(page);
  await expect(page).toHaveScreenshot("logs-expanded-failover.png");
});

test("time range popover with presets and custom inputs", async ({ page }) => {
  await signIn(page);
  await page.goto("/logs");
  await expect(page.getByRole("heading", { name: "使用日志" })).toBeVisible();
  await page.getByRole("button", { name: /时间范围：/ }).click();
  const dialog = page.getByRole("dialog", { name: "选择时间范围" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "自定义" }).click();
  await expect(dialog.getByRole("textbox", { name: "开始时刻" })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "结束时刻" })).toBeVisible();
  await freezeUi(page);
  await expect(page).toHaveScreenshot("logs-time-range.png");
});

test("dashboard latency percentiles", async ({ page }) => {
  await signIn(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "数据看板" })).toBeVisible();
  const heading = page.getByRole("heading", { name: "延迟分位" });
  await expect(heading).toBeVisible();
  await heading.scrollIntoViewIfNeeded();
  await expect(page.getByText(/首字 \/ 首答 \/ 尾字 \/ 总耗时/)).toBeVisible();
  await freezeUi(page);
  await expect(page).toHaveScreenshot("dashboard-latency.png");
});

test("rewrite editor request body section", async ({ page }) => {
  await signIn(page);
  await page.goto("/channels/ch-1?tab=rewrite");
  await expect(page.getByRole("heading", { name: "channel-a" })).toBeVisible();
  await page.getByRole("button", { name: /^请求体/ }).click();
  await expect(page.getByRole("heading", { name: "请求体规则" })).toBeVisible();
  // Radix Select：触发器的可访问名来自 aria-label，选项在 Portal 里。
  const modeSelect = page.getByRole("combobox", { name: "规则 1 模式" });
  await expect(modeSelect).toBeVisible();
  await modeSelect.click();
  await expect(page.getByRole("option")).toHaveCount(9);
  await expect(page.getByRole("option", { selected: true })).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("textbox", { name: "条件 1 路径" })).toBeVisible();
  await freezeUi(page);
  await expect(page).toHaveScreenshot("rewrite-body.png", { fullPage: true });
});
