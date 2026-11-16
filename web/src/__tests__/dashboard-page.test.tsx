import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import type {
  DashboardFlows,
  DashboardSummary,
  DashboardTimeseries,
} from "@/lib/dashboard";
import { DashboardPage } from "@/pages/dashboard";

const START = Date.UTC(2026, 10, 14, 0);
const HOUR = 3_600_000;

function member(name: string, requests: number, p95: number | null) {
  return {
    name,
    requests,
    prompt_tokens: requests * 10,
    completion_tokens: requests * 5,
    cached_tokens: 0,
    cache_hit_rate: null,
    cost_usd: requests * 0.001,
    errors: 0,
    p95_total_ms: p95,
  };
}

const SUMMARY = {
  requests: 40,
  prompt_tokens: 400,
  completion_tokens: 200,
  cached_tokens: 0,
  cache_hit_rate: null,
  cache_hit_rate_samples: 0,
  cost_usd: 0.4,
  errors: 1,
  error_rate: 0.025,
  p95_ttft_ms: 300,
  p95_total_ms: 1200,
  latency: {
    samples: 40,
    ttft: { p50: 100, p90: 200, p95: 300, p99: 400 },
    ttfa: null,
    ttlt: null,
    total: { p50: 600, p90: 1000, p95: 1200, p99: 1800 },
  },
  start_ms: START,
  end_ms: START + 24 * HOUR,
  active_minutes: 30,
  span_minutes: 1440,
  rpm: 1.3,
  tpm: 20,
  peak_rpm: 4,
} as unknown as DashboardSummary;

const TIMESERIES = {
  range: "today",
  timezone: "Asia/Shanghai",
  granularity: "hour",
  start_ms: START,
  end_ms: START + 2 * HOUR,
  points: [
    {
      start_ms: START,
      requests: 20,
      prompt_tokens: 200,
      completion_tokens: 100,
      cached_tokens: 0,
      cache_hit_rate: null,
      cost_usd: 0.2,
      errors: 1,
      p95_ttft_ms: 300,
      p95_total_ms: 1200,
      p95_ttfa_ms: 400,
      p95_ttlt_ms: 1500,
      by_model: [member("combo-a", 12, 900), member("combo-b", 8, 2400)],
      by_upstream_model: [member("gpt-4o", 20, 1200)],
      by_channel: [member("ch-a", 12, 900), member("ch-b", 8, 2400)],
    },
    {
      start_ms: START + HOUR,
      requests: 20,
      prompt_tokens: 200,
      completion_tokens: 100,
      cached_tokens: 0,
      cache_hit_rate: null,
      cost_usd: 0.2,
      errors: 0,
      p95_ttft_ms: 320,
      p95_total_ms: 1400,
      p95_ttfa_ms: 420,
      p95_ttlt_ms: 1600,
      by_model: [member("combo-a", 14, 1000), member("combo-b", 6, 2600)],
      by_upstream_model: [member("gpt-4o", 20, 1400)],
      by_channel: [member("ch-a", 14, 1000), member("ch-b", 6, 2600)],
    },
  ],
  top_models: [],
  top_channels: [],
  top_upstream_models: [],
  error_distribution: [{ code: "upstream_5xx", count: 1 }],
  heatmap: [],
} as unknown as DashboardTimeseries;

const FLOWS = {
  metric: "requests",
  stages: ["user", "key", "model", "upstream", "channel"],
  nodes: [],
  links: [],
  total: { requests: 40, prompt_tokens: 400, completion_tokens: 200 },
} as unknown as DashboardFlows;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** 把当前 URL 曝光给测试：图型切换必须只改渲染，维度切换必须改请求参数。 */
function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-search">{location.search}</output>;
}

function renderPage() {
  const requests: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    requests.push(url);
    if (url.includes("/api/dashboard/summary")) return jsonResponse(SUMMARY);
    if (url.includes("/api/dashboard/timeseries")) return jsonResponse(TIMESERIES);
    if (url.includes("/api/dashboard/flows")) return jsonResponse(FLOWS);
    if (url.includes("/api/logs/filters")) return jsonResponse({ channels: [], models: [] });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <DashboardPage />
          <LocationProbe />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { view, fetchMock, requests };
}

/**
 * 把图表容器的实测尺寸钉住：jsdom 没有布局，getBoundingClientRect 恒为 0，
 * 而 ResizeObserver 的回调由测试手动触发，用来验证「容器变了就重算坐标与刻度」。
 */
function stubChartBox(initial: { width: number; height: number }) {
  const size = { ...initial };
  const callbacks: ResizeObserverCallback[] = [];
  class ResizeObserverStub {
    constructor(callback: ResizeObserverCallback) {
      callbacks.push(callback);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub as unknown as typeof ResizeObserver);
  const original = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function measuredBox() {
    return {
      width: size.width,
      height: size.height,
      top: 0,
      left: 0,
      right: size.width,
      bottom: size.height,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect;
  };
  return {
    resize(next: { width: number; height: number }) {
      size.width = next.width;
      size.height = next.height;
      for (const callback of callbacks) callback([], undefined as unknown as ResizeObserver);
    },
    restore() {
      Element.prototype.getBoundingClientRect = original;
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("dashboard trend panel", () => {
  it("opens on combo models by token volume when the URL has no parameters", async () => {
    const { requests } = renderPage();
    await screen.findByRole("img", { name: "词元趋势（按综合模型成员堆叠）" });

    // 默认值与后端 dimension 参数一致：第一次点开就显式请求 combo。
    await waitFor(() =>
      expect(
        requests.some(
          (url) => url.includes("/api/dashboard/timeseries") && url.includes("dimension=combo"),
        ),
      ).toBe(true),
    );
    expect(screen.getByRole("radio", { name: "综合模型" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "词元" })).toHaveAttribute("aria-checked", "true");
    // 默认维度不写进 URL：链接保持干净。
    expect(screen.getByTestId("location-search").textContent).toBe("");
  });

  it("switches chart type as a rendering-only change: same data, URL keeps only the chart tab", async () => {
    const { requests } = renderPage();
    await screen.findByRole("img", { name: "词元趋势（按综合模型成员堆叠）" });
    const before = requests.length;
    const user = userEvent.setup();

    await user.click(screen.getByRole("tab", { name: "面积图" }));

    await screen.findByRole("img", { name: "词元趋势（按综合模型成员曲线，不堆叠多线）" });
    // 换图型不重取数据（query key 与图型无关），也不往 URL 里塞别的语义。
    expect(requests.length).toBe(before);
    expect(screen.getByTestId("location-search").textContent).toBe("?tab=area");

    await user.click(screen.getByRole("tab", { name: "柱形图" }));
    await screen.findByRole("img", { name: "词元趋势（按综合模型成员堆叠）" });
    expect(screen.getByTestId("location-search").textContent).toBe("");
    expect(requests.length).toBe(before);
  });

  it("refetches with the picked dimension and keeps it in the URL", async () => {
    const { requests } = renderPage();
    await screen.findByRole("img", { name: "词元趋势（按综合模型成员堆叠）" });
    const timeseriesCalls = () => requests.filter((url) => url.includes("/api/dashboard/timeseries")).length;
    const before = timeseriesCalls();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: "上游模型" }));

    await screen.findByRole("img", { name: "词元趋势（按上游模型成员堆叠）" });
    await waitFor(() =>
      expect(requests.some((url) => url.includes("/api/dashboard/timeseries") && url.includes("dimension=upstream"))).toBe(
        true,
      ),
    );
    expect(timeseriesCalls()).toBeGreaterThan(before);
    expect(screen.getByTestId("location-search").textContent).toContain("dimension=upstream");
    // 只画一个维度：另外两份 breakdown 不再出现在图例里。
    expect(screen.queryByText("combo-a")).not.toBeInTheDocument();
    expect(screen.queryByText("ch-a")).not.toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("re-measures the container and redraws the geometry and y ticks when it resizes", async () => {
    const box = stubChartBox({ width: 1440, height: 300 });
    try {
      renderPage();
      const svg = await screen.findByRole("img", { name: "词元趋势（按综合模型成员堆叠）" });
      await waitFor(() => expect(svg.getAttribute("viewBox")).toBe("0 0 1440 300"));
      const gridYs = () =>
        [...svg.querySelectorAll("line")].map((line) => Number(line.getAttribute("y1")));
      const beforeYs = gridYs();
      expect(beforeYs.length).toBeGreaterThan(0);

      act(() => box.resize({ width: 900, height: 460 }));

      await waitFor(() => expect(svg.getAttribute("viewBox")).toBe("0 0 900 460"));
      expect(svg.getAttribute("width")).toBe("900");
      // 画布变高后同一刻度落在更低的位置：坐标是按实测尺寸重算的，不是拉伸一张固定比例的图。
      expect(gridYs()).not.toEqual(beforeYs);
    } finally {
      box.restore();
    }
  });

  it("drops the Top 综合模型 block without leaving a dead request behind", async () => {
    const { requests } = renderPage();
    await screen.findByRole("img", { name: "词元趋势（按综合模型成员堆叠）" });

    expect(screen.queryByText(/Top 综合模型/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Top 榜维度/ })).not.toBeInTheDocument();
    // 合并后的聚合视图仍在（图例 + 合计），只是不再有第三张并列的视角图。
    expect(screen.getByText("图例")).toBeInTheDocument();
    expect(screen.getByText("合计")).toBeInTheDocument();

    const dashboardPaths = [
      ...new Set(
        requests
          .filter((url) => url.includes("/api/dashboard/"))
          .map((url) => new URL(url, "http://localhost").pathname),
      ),
    ].sort();
    // 顶部只应打 summary + timeseries（热力图复用 timeseries）；Top 块删掉后没有第五条请求。
    expect(dashboardPaths).toEqual(["/api/dashboard/summary", "/api/dashboard/timeseries"]);
    expect(requests.some((url) => url.includes("/api/dashboard/flows"))).toBe(false);
  });
});
