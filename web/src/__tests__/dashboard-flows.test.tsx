import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FlowCard } from "@/components/dashboard/flow-card";
import { TrendCard } from "@/components/dashboard/trend-card";
import {
  FLOW_MIN_HIT_HEIGHT,
  FLOW_MIN_NODE_HEIGHT,
  TREND_LATENCY_OTHER_LABEL,
  type DashboardFlows,
  type DashboardTimeseries,
} from "@/lib/dashboard";

const FLOWS: DashboardFlows = {
  metric: "requests",
  stages: ["user", "key", "model", "upstream", "channel"],
  nodes: [
    { id: "u1", stage: "user", label: "alice", value: 10, share: 0.5 },
    { id: "u2", stage: "user", label: "bob", value: 10, share: 0.5 },
    { id: "k1", stage: "key", label: "sk-main", value: 20, share: 1 },
    { id: "m1", stage: "model", label: "gpt-4o", value: 20, share: 1 },
    { id: "p1", stage: "upstream", label: "openai", value: 20, share: 1 },
    { id: "c1", stage: "channel", label: "东区", value: 20, share: 1 },
  ],
  links: [
    { source: "u1", target: "k1", value: 10 },
    { source: "u2", target: "k1", value: 10 },
    { source: "k1", target: "m1", value: 20 },
    { source: "m1", target: "p1", value: 20 },
    { source: "p1", target: "c1", value: 20 },
  ],
  total: { requests: 20, prompt_tokens: 100, completion_tokens: 50 },
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderFlowCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FlowCard range="24h" timezone="Asia/Shanghai" windowReady refreshMs={0} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FlowCard", () => {
  it("renders the Sankey, hides a node, restores it from the chip, and exposes keyboard hiding", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(FLOWS)),
    );
    const user = userEvent.setup();
    renderFlowCard();

    const alice = await screen.findByRole("button", { name: /隐藏节点 alice/ });
    expect(screen.getByText("单位：请求数")).toBeInTheDocument();
    await user.click(alice);
    expect(screen.getByText("已隐藏 1 个节点")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /隐藏节点 alice/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "alice" }));
    expect(screen.queryByText("已隐藏 1 个节点")).not.toBeInTheDocument();
    const restored = screen.getByRole("button", { name: /隐藏节点 alice/ });
    // 聚焦会触发 onFocus 的详情卡 state 更新，必须包在 act 里，否则 React 报 act 告警。
    await act(async () => {
      restored.focus();
    });
    await user.keyboard("{Enter}");
    expect(await screen.findByText("已隐藏 1 个节点")).toBeInTheDocument();
  });

  it("keeps at least one visible node", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...FLOWS,
          nodes: [FLOWS.nodes[0]],
          links: [],
          total: { requests: 10, prompt_tokens: 10, completion_tokens: 10 },
        }),
      ),
    );
    const user = userEvent.setup();
    renderFlowCard();
    const alice = await screen.findByRole("button", { name: /隐藏节点 alice/ });
    await user.click(alice);
    expect(screen.queryByText(/已隐藏 \d+ 个节点/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /隐藏节点 alice/ })).toBeInTheDocument();
  });

  it("zooms with the controls and resets to one times", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(FLOWS)),
    );
    const user = userEvent.setup();
    renderFlowCard();
    await screen.findByRole("button", { name: /隐藏节点 alice/ });
    expect(screen.getByText("1.0×")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "放大分流图" }));
    expect(screen.getByText("1.2×")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复位" }));
    expect(screen.getByText("1.0×")).toBeInTheDocument();
  });

  it("refetches with the token metric when the metric toggle changes", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => jsonResponse(FLOWS));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderFlowCard();
    await screen.findByText("单位：请求数");
    await user.click(screen.getByRole("radio", { name: "按 Token" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("metric=tokens")),
      ).toBe(true),
    );
    expect(await screen.findByText("单位：Token")).toBeInTheDocument();
  });

  it("shows an explicit empty state and an API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...FLOWS,
          nodes: [],
          links: [],
          total: { requests: 0, prompt_tokens: 0, completion_tokens: 0 },
        }),
      ),
    );
    const { unmount } = renderFlowCard();
    expect(await screen.findByText("所选范围内没有分流数据")).toBeInTheDocument();
    unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ message: "上游查询失败" }, 500)),
    );
    renderFlowCard();
    expect(await screen.findByText("分流图加载失败")).toBeInTheDocument();
  });
});

function member(name: string, requests: number, p95: number) {
  return {
    name,
    requests,
    prompt_tokens: requests * 10,
    completion_tokens: requests * 5,
    cached_tokens: 0,
    cache_hit_rate: 0,
    cost_usd: requests * 0.001,
    errors: 0,
    p95_total_ms: p95,
  };
}

const TIMESERIES = {
  range: "24h",
  timezone: "Asia/Shanghai",
  granularity: "hour",
  start_ms: Date.UTC(2026, 10, 14, 0),
  end_ms: Date.UTC(2026, 10, 15, 0),
  points: [
    {
      start_ms: Date.UTC(2026, 10, 14, 1),
      requests: 30,
      by_model: [],
      by_upstream_model: [],
      by_channel: [
        member("ch-a", 20, 100),
        member("ch-b", 10, 300),
        member("ch-c", 8, 120),
        member("ch-d", 7, 130),
        member("ch-e", 6, 140),
        member("ch-f", 5, 150),
        member("ch-g", 4, 160),
        member("ch-h", 3, 170),
      ],
    },
    {
      start_ms: Date.UTC(2026, 10, 14, 2),
      requests: 20,
      by_model: [],
      by_upstream_model: [],
      by_channel: [member("ch-a", 12, 110), member("ch-b", 8, 310)],
    },
  ],
  top_models: [],
  top_channels: [
    { name: "ch-a", requests: 32, prompt_tokens: 320, completion_tokens: 160, cached_tokens: 0, cost_usd: 0.032 },
    { name: "ch-b", requests: 18, prompt_tokens: 180, completion_tokens: 90, cached_tokens: 0, cost_usd: 0.018 },
  ],
  top_upstream_models: [],
  error_distribution: [],
  heatmap: [],
} as unknown as DashboardTimeseries;

function renderTrendCard(onFetch?: (url: string) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    onFetch?.(String(input));
    return jsonResponse(FLOWS);
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <TrendCard
          data={TIMESERIES}
          range="24h"
          timezone="Asia/Shanghai"
          windowReady
          refreshMs={0}
          dimension="channel"
          onDimensionChange={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { view, fetchMock };
}

describe("FlowSankey hit areas and hover card", () => {
  it("draws an 8px minimum node with a 12px hit area", async () => {
    const nodes = Array.from({ length: 30 }, (_, index) => ({
      id: "u" + index,
      stage: "user" as const,
      label: "user-" + index,
      value: 30 - index,
      share: 0,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...FLOWS,
          nodes,
          links: [],
          total: { requests: 465, prompt_tokens: 0, completion_tokens: 0 },
        }),
      ),
    );
    renderFlowCard();
    const hit = await screen.findByTestId("flow-node-u0");
    expect(Number(hit.getAttribute("height"))).toBeGreaterThanOrEqual(FLOW_MIN_HIT_HEIGHT);
    const visible = hit.previousElementSibling as SVGRectElement;
    expect(visible.tagName.toLowerCase()).toBe("rect");
    expect(Number(visible.getAttribute("height"))).toBeGreaterThanOrEqual(FLOW_MIN_NODE_HEIGHT);
  });

  it("shows name, share and neighbours in a hover card", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(FLOWS)),
    );
    const user = userEvent.setup();
    renderFlowCard();
    const alice = await screen.findByRole("button", { name: /隐藏节点 alice/ });
    await user.hover(alice);
    const card = await screen.findByTestId("flow-node-tooltip");
    expect(within(card).getByText("alice")).toBeInTheDocument();
    expect(card.textContent).toContain("占比");
    expect(within(card).getByText("下游")).toBeInTheDocument();
    expect(within(card).getByText("sk-main")).toBeInTheDocument();
    await user.unhover(alice);
    await waitFor(() => expect(screen.queryByTestId("flow-node-tooltip")).not.toBeInTheDocument());
  });
});

describe("TrendCard tabs", () => {
  it("does not load flows until the flow tab is opened and reuses the cache afterwards", async () => {
    const called: string[] = [];
    const { fetchMock } = renderTrendCard((url) => called.push(url));
    const flowCalls = () => called.filter((url) => url.includes("/dashboard/flows")).length;

    expect(await screen.findByRole("radio", { name: "上游渠道" })).toBeInTheDocument();
    expect(flowCalls()).toBe(0);

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "分流图" }));
    await screen.findByRole("button", { name: /隐藏节点 alice/ });
    expect(flowCalls()).toBe(1);

    await user.click(screen.getByRole("tab", { name: "柱形图" }));
    await user.click(screen.getByRole("tab", { name: "分流图" }));
    await waitFor(() => expect(flowCalls()).toBe(1));
    expect(fetchMock.mock.calls.every(([input]) => String(input).includes("/dashboard/flows"))).toBe(true);
  });

  it("switches a member off and back on from the trend legend", async () => {
    renderTrendCard();
    const user = userEvent.setup();
    const hide = await screen.findByRole("button", { name: "隐藏 ch-a" });
    await user.click(hide);
    expect(screen.getByRole("button", { name: "显示 ch-a" })).toHaveAttribute("aria-pressed", "false");
    await user.click(screen.getByRole("button", { name: "显示 ch-a" }));
    expect(screen.getByRole("button", { name: "隐藏 ch-a" })).toHaveAttribute("aria-pressed", "true");
  });

  it("moves P95 to the area chart with an explicit weighted other series", async () => {
    renderTrendCard();
    const user = userEvent.setup();
    // 柱形图不提供 P95：先切到面积图，再选 P95 指标。
    await user.click(await screen.findByRole("tab", { name: "面积图" }));
    await user.click(await screen.findByRole("radio", { name: "P95 延迟" }));
    expect(screen.getAllByText(TREND_LATENCY_OTHER_LABEL).length).toBeGreaterThan(0);
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("成员并排");
  });
});

