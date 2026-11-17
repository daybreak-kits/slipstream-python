import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { assignCategoryColors, assignCategoryColorsByName, CATEGORY_COLORS } from "@/lib/chart-colors";
import { formatCacheHitRate, type DashboardSummary } from "@/lib/dashboard";
import {
  dashboardKpis,
  DEFAULT_REFRESH_INTERVAL_MS,
  formatP95,
  FLOW_STAGE_ORDER,
  flowMetricTotal,
  flowNodeNeighbors,
  foldFlowNodes,
  formatTrendAxis,
  formatWeightedP95,
  HEATMAP_HOURS,
  heatmapGrid,
  heatmapOpacity,
  layoutFlow,
  REFRESH_INTERVAL_OPTIONS,
  refreshIntervalLabel,
  stackTrendSegments,
  TREND_LATENCY_OTHER_LABEL,
  TREND_LATENCY_TOP_MEMBERS,
  TREND_OTHER_COLOR,
  trendLatencyMembers,
  trendScale,
  trendTooltipRows,
  useAutoRefresh,
  WEEKDAY_LABELS,
  weightedAverageP95,
  type DashboardFlowLink,
  type DashboardFlowNode,
  type TimeseriesModelPoint,
} from "@/lib/dashboard";

describe("formatCacheHitRate", () => {
  it("renders one-decimal percentages", () => {
    expect(formatCacheHitRate(0.1234)).toBe("12.3%");
    expect(formatCacheHitRate(0.5)).toBe("50.0%");
  });

  it("renders 100% for a full cache hit rate", () => {
    expect(formatCacheHitRate(1)).toBe("100.0%");
  });

  it("renders a dash for zero, missing or invalid samples", () => {
    expect(formatCacheHitRate(0)).toBe("—");
    expect(formatCacheHitRate(null)).toBe("—");
    expect(formatCacheHitRate(undefined)).toBe("—");
    expect(formatCacheHitRate(Number.NaN)).toBe("—");
    expect(formatCacheHitRate(-0.1)).toBe("—");
  });
});

const assignments = [
  { name: "gpt-4o", color: "var(--chart-1)", requests: 10 },
  { name: "claude", color: "var(--chart-2)", requests: 6 },
  { name: "qwen", color: "var(--chart-3)", requests: 4 },
];

describe("trendTooltipRows", () => {
  it("lists every visible non-zero series for a bucket hover", () => {
    expect(trendTooltipRows(assignments, [3, 0, 5], new Set<string>(), null)).toEqual([
      { name: "gpt-4o", color: "var(--chart-1)", value: 3 },
      { name: "qwen", color: "var(--chart-3)", value: 5 },
    ]);
  });

  it("lists only the hovered segment, even when its value is zero", () => {
    expect(trendTooltipRows(assignments, [3, 0, 5], new Set<string>(), 1)).toEqual([
      { name: "claude", color: "var(--chart-2)", value: 0 },
    ]);
  });

  it("skips hidden series in both modes", () => {
    expect(trendTooltipRows(assignments, [3, 2, 5], new Set(["gpt-4o"]), null)).toEqual([
      { name: "claude", color: "var(--chart-2)", value: 2 },
      { name: "qwen", color: "var(--chart-3)", value: 5 },
    ]);
    expect(trendTooltipRows(assignments, [3, 2, 5], new Set(["gpt-4o"]), 0)).toEqual([]);
  });

  it("keeps null p95 series out of a bucket listing but shows the hovered one as null", () => {
    expect(trendTooltipRows(assignments, [3, null, 5], new Set<string>(), null)).toEqual([
      { name: "gpt-4o", color: "var(--chart-1)", value: 3 },
      { name: "qwen", color: "var(--chart-3)", value: 5 },
    ]);
    expect(trendTooltipRows(assignments, [3, null, 5], new Set<string>(), 1)).toEqual([
      { name: "claude", color: "var(--chart-2)", value: null },
    ]);
  });
});

describe("weightedAverageP95", () => {
  it("weights every p95 by its request count", () => {
    expect(
      weightedAverageP95([
        { value: 100, requests: 1 },
        { value: 200, requests: 3 },
      ]),
    ).toBe(175);
  });

  it("ignores empty samples and zero weights", () => {
    expect(
      weightedAverageP95([
        { value: 0, requests: 10 },
        { value: 500, requests: 0 },
        { value: 120, requests: 2 },
      ]),
    ).toBe(120);
    expect(weightedAverageP95([])).toBe(0);
    expect(weightedAverageP95([{ value: 500, requests: 0 }])).toBe(0);
  });

  it("treats null p95 as missing instead of a zero-millisecond sample", () => {
    expect(
      weightedAverageP95([
        { value: null, requests: 10 },
        { value: 200, requests: 3 },
      ]),
    ).toBe(200);
    expect(weightedAverageP95([{ value: null, requests: 10 }, { value: null, requests: 4 }])).toBe(0);
  });
});

describe("assignCategoryColors", () => {
  it("cycles the palette so long model lists keep their own series", () => {
    const items = Array.from({ length: 18 }, (_, i) => ({ name: `m${i}`, requests: 18 - i }));
    const result = assignCategoryColors(items);
    expect(result).toHaveLength(18);
    expect(result[0].color).toBe(CATEGORY_COLORS[0]);
    expect(result[16].color).toBe(CATEGORY_COLORS[0]);
    expect(result.some((item) => item.name === "其他")).toBe(false);
  });
});

const summary: DashboardSummary = {
  requests: 120,
  prompt_tokens: 1_200_000,
  completion_tokens: 3_400,
  cached_tokens: 600_000,
  cache_hit_rate: 0.5,
  cache_hit_rate_samples: 8,
  cache_request_hit_rate: 0.75,
  cache_request_hits: 6,
  cacheable_ratio: 0.4,
  cacheable_tokens: 480_000,
  cacheable_sessions: 3,
  cache_saved_tokens: 600_000,
  cache_saved_usd: 0.02,
  cost_usd: 0.125,
  errors: 2,
  error_rate: 0.0167,
  p95_ttft_ms: 480,
  p95_total_ms: 2_100,
  latency: { samples: 7, ttft: null, ttfa: null, ttlt: null, total: null },
  start_ms: 1_700_000_000_000,
  end_ms: 1_700_003_600_000,
  active_minutes: 12,
  span_minutes: 90,
  rpm: 10,
  tpm: 1_234.5,
  peak_rpm: 27,
};

describe("dashboardKpis", () => {
  it("renders eleven tiles in reading order", () => {
    const kpis = dashboardKpis(summary);
    expect(kpis).toHaveLength(11);
    expect(kpis.map((kpi) => kpi.label)).toEqual([
      "请求数",
      "输入词元",
      "输出词元",
      "缓存词元",
      "缓存命中率",
      "成本",
      "错误率",
      "RPM",
      "TPM",
      "P95 首字",
      "P95 总耗时",
    ]);
  });

  it("reports the active minutes and peak rate behind RPM/TPM", () => {
    const byKey = new Map(dashboardKpis(summary).map((kpi) => [kpi.key, kpi]));
    expect(byKey.get("rpm")?.value).toBe("10.00");
    expect(byKey.get("rpm")?.hint).toBe("活跃 12 分钟 · 峰值 27/分");
    expect(byKey.get("tpm")?.value).toBe("1.23K");
    expect(byKey.get("cost")?.value).toBe("$0.125");
    expect(byKey.get("error_rate")?.value).toBe("1.67%");
    expect(byKey.get("tpm")?.hint).toBe("跨度 1.5 小时");
    expect(byKey.get("rpm")?.tone).toBe("flow");
    expect(byKey.get("tpm")?.tone).toBe("token");
    expect(byKey.get("cost")?.tone).toBe("cost");
    expect(byKey.get("error_rate")?.tone).toBe("error");
    expect(byKey.get("p95_ttft")?.tone).toBe("latency");
  });

  it("mentions estimated rows only when the backend reports them", () => {
    // hint 在 1440 宽五列合并卡里被 truncate：短到截断后头部仍可读，完整口径走悬停弹层。
    expect(dashboardKpis(summary).find((kpi) => kpi.key === "cache_hit_rate")?.hint).toBe(
      "token 加权 · 8 样本",
    );
    expect(
      dashboardKpis({ ...summary, estimated_rows: 3 }).find((kpi) => kpi.key === "cache_hit_rate")?.hint,
    ).toBe("token 加权 · 8 样本 · 3 条估算未计入");
  });

  it("expands the cache tile into ADR-068 cohorts A/B/C/D", () => {
    const cache = dashboardKpis(summary).find((kpi) => kpi.key === "cache_hit_rate");
    expect(cache?.detail?.title).toBe("缓存命中率 · A/B/C/D 口径");
    expect(cache?.detail?.rows.map((row) => row.label)).toEqual([
      "A token 加权",
      "B 请求级",
      "C 可缓存占比",
      "D 已节省",
    ]);
    expect(cache?.detail?.rows[0]?.value).toBe("50.0% · 600.00K / 1.20M token");
    expect(cache?.detail?.rows[1]?.value).toBe("75.0% · 6 / 8 条");
    expect(cache?.detail?.rows[2]?.value).toBe("40.0% · 480.00K / 1.20M token · 3 会话");
    expect(cache?.detail?.rows[3]?.value).toBe("600.00K token · $0.02");
    expect(cache?.detail?.note).toContain("样本 8 条");
  });

  it("degrades gracefully without data", () => {
    const kpis = dashboardKpis(undefined);
    expect(kpis).toHaveLength(11);
    expect(kpis.find((kpi) => kpi.key === "rpm")?.value).toBe("—");
    // TPM 与 RPM 同卡并列，「活跃 X 分钟」只保留在 RPM 那一格，避免重复前缀。
    expect(kpis.find((kpi) => kpi.key === "tpm")?.hint).toBe("跨度 —");
    expect(kpis.find((kpi) => kpi.key === "p95_ttft")?.hint).toBe("样本不足（0/5）");
    expect(kpis.find((kpi) => kpi.key === "p95_ttft")?.value).toBe("—");
    expect(kpis.find((kpi) => kpi.key === "p95_total")?.value).toBe("—");
  });

  it("dashes the latency tiles once the backend reports null percentiles", () => {
    const kpis = dashboardKpis({ ...summary, p95_ttft_ms: null, p95_total_ms: null });
    expect(kpis.find((kpi) => kpi.key === "p95_ttft")?.value).toBe("—");
    expect(kpis.find((kpi) => kpi.key === "p95_total")?.value).toBe("—");
  });
});

describe("formatP95", () => {
  it("formats real percentiles and dashes missing samples", () => {
    expect(formatP95(480)).toBe("0.48s");
    expect(formatP95(2_100)).toBe("2.10s");
    expect(formatP95(null)).toBe("—");
    expect(formatP95(undefined)).toBe("—");
    expect(formatP95(0)).toBe("—");
    expect(formatP95(Number.NaN)).toBe("—");
    expect(formatP95(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatWeightedP95", () => {
  it("dashes the weighted total when there is no usable sample weight", () => {
    expect(formatWeightedP95(480, 0)).toBe("—");
    expect(formatWeightedP95(480, Number.NaN)).toBe("—");
    expect(formatWeightedP95(480, -3)).toBe("—");
  });

  it("dashes the weighted total when the percentile itself is missing", () => {
    expect(formatWeightedP95(null, 12)).toBe("—");
    expect(formatWeightedP95(0, 12)).toBe("—");
  });

  it("renders the weighted total when both value and weight are usable", () => {
    expect(formatWeightedP95(480, 3)).toBe("0.48s");
    expect(formatWeightedP95(2_100, 12)).toBe("2.10s");
  });
});

describe("trendScale", () => {
  it("picks a readable step for a small token total", () => {
    expect(trendScale(20)).toEqual({ max: 20, ticks: [0, 5, 10, 15, 20] });
  });

  it("keeps million-scale labels on 50M steps", () => {
    expect(trendScale(200_000_000)).toEqual({
      max: 200_000_000,
      ticks: [0, 50_000_000, 100_000_000, 150_000_000, 200_000_000],
    });
  });

  it("never emits fractional ticks for request counts", () => {
    expect(trendScale(10, 4, true)).toEqual({ max: 12, ticks: [0, 3, 6, 9, 12] });
    expect(trendScale(3, 4, true)).toEqual({ max: 3, ticks: [0, 1, 2, 3] });
  });

  it("falls back to a single unit when the window has no data", () => {
    expect(trendScale(0)).toEqual({ max: 1, ticks: [0, 1] });
    expect(trendScale(Number.NaN)).toEqual({ max: 1, ticks: [0, 1] });
  });
});

describe("formatTrendAxis", () => {
  it("formats every metric with the same unit rules as the tooltip", () => {
    expect(formatTrendAxis(1_500_000, "tokens")).toBe("1.50M");
    expect(formatTrendAxis(1.2, "cost")).toBe("$1.20");
    expect(formatTrendAxis(2_100, "latency")).toBe("2.10s");
    expect(formatTrendAxis(12, "requests")).toBe("12");
  });

  it("drops the redundant .00 on whole magnitudes", () => {
    expect(formatTrendAxis(200_000_000, "tokens")).toBe("200M");
    expect(formatTrendAxis(5_000_000, "tokens")).toBe("5M");
    expect(formatTrendAxis(20_000, "latency")).toBe("20s");
  });
});

describe("heatmapGrid", () => {
  it("maps isoweekday 1..7 onto rows Monday..Sunday and hour onto columns", () => {
    const grid = heatmapGrid([
      { weekday: 1, hour: 0, requests: 3 },
      { weekday: 3, hour: 12, requests: 65 },
      { weekday: 7, hour: 23, requests: 4 },
    ]);
    expect(grid).toHaveLength(7);
    expect(grid.every((row) => row.length === HEATMAP_HOURS)).toBe(true);
    expect(grid[0][0]).toBe(3);
    expect(grid[2][12]).toBe(65);
    expect(grid[6][23]).toBe(4);
    expect(WEEKDAY_LABELS[2]).toBe("三");
    expect(grid.flat().reduce((sum, value) => sum + value, 0)).toBe(72);
  });

  it("accumulates duplicate cells and ignores out-of-range rows or columns", () => {
    const grid = heatmapGrid([
      { weekday: 2, hour: 5, requests: 1 },
      { weekday: 2, hour: 5, requests: 2 },
      { weekday: 0, hour: 5, requests: 9 },
      { weekday: 8, hour: 5, requests: 9 },
      { weekday: 2, hour: 24, requests: 9 },
      { weekday: 2, hour: -1, requests: 9 },
      { weekday: 2.5, hour: 5, requests: 9 },
    ]);
    expect(grid[1][5]).toBe(3);
    expect(grid.flat().reduce((sum, value) => sum + value, 0)).toBe(3);
  });
});

describe("heatmapOpacity", () => {
  it("keeps empty cells at the faint floor and the peak at full strength", () => {
    expect(heatmapOpacity(0, 10)).toBe(0.04);
    expect(heatmapOpacity(10, 10)).toBe(1);
  });

  it("starts non-empty cells at 0.15 and deepens monotonically", () => {
    const low = heatmapOpacity(1, 100);
    const mid = heatmapOpacity(50, 100);
    expect(low).toBeCloseTo(0.1585, 4);
    expect(mid).toBeGreaterThan(low);
    expect(mid).toBeLessThan(1);
  });

  it("falls back to the value itself when the peak is missing", () => {
    expect(heatmapOpacity(5, 0)).toBe(1);
  });
});

describe("refreshIntervalLabel", () => {
  it("offers the cadences the lead asked for, 1 minute by default", () => {
    expect(REFRESH_INTERVAL_OPTIONS.map((option) => option.value)).toEqual([
      15_000, 30_000, 60_000, 300_000, 600_000, 1_800_000,
    ]);
    expect(DEFAULT_REFRESH_INTERVAL_MS).toBe(60_000);
  });

  it("renders a label for known cadences and pauses unknown ones", () => {
    expect(refreshIntervalLabel(15_000)).toBe("每 15 秒");
    expect(refreshIntervalLabel(60_000)).toBe("每 1 分钟");
    expect(refreshIntervalLabel(1_800_000)).toBe("每 30 分钟");
    expect(refreshIntervalLabel(0)).toBe("已暂停");
  });
});

describe("useAutoRefresh", () => {
  it("ticks on the interval and always calls the latest callback", () => {
    vi.useFakeTimers();
    try {
      const first = vi.fn();
      const second = vi.fn();
      const { rerender } = renderHook(({ cb }: { cb: () => void }) => useAutoRefresh(cb, 30_000), {
        initialProps: { cb: first },
      });
      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      expect(first).toHaveBeenCalledTimes(1);
      rerender({ cb: second });
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(second).toHaveBeenCalledTimes(2);
      expect(first).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("restarts when the interval changes and stops after unmount", () => {
    vi.useFakeTimers();
    try {
      const refresh = vi.fn();
      const { rerender, unmount } = renderHook(({ ms }: { ms: number }) => useAutoRefresh(refresh, ms), {
        initialProps: { ms: 15_000 },
      });
      act(() => {
        vi.advanceTimersByTime(15_000);
      });
      expect(refresh).toHaveBeenCalledTimes(1);
      rerender({ ms: 60_000 });
      act(() => {
        vi.advanceTimersByTime(45_000);
      });
      expect(refresh).toHaveBeenCalledTimes(1);
      act(() => {
        vi.advanceTimersByTime(15_000);
      });
      expect(refresh).toHaveBeenCalledTimes(2);
      unmount();
      act(() => {
        vi.advanceTimersByTime(300_000);
      });
      expect(refresh).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("schedules nothing when the interval is paused", () => {
    vi.useFakeTimers();
    try {
      const refresh = vi.fn();
      renderHook(() => useAutoRefresh(refresh, 0));
      act(() => {
        vi.advanceTimersByTime(600_000);
      });
      expect(refresh).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("flowMetricTotal", () => {
  it("uses requests or prompt+completion tokens for the selected metric", () => {
    expect(
      flowMetricTotal({
        metric: "requests",
        total: { requests: 42, prompt_tokens: 100, completion_tokens: 50 },
      }),
    ).toBe(42);
    expect(
      flowMetricTotal({
        metric: "tokens",
        total: { requests: 42, prompt_tokens: 100, completion_tokens: 50 },
      }),
    ).toBe(150);
    expect(
      flowMetricTotal({
        metric: "tokens",
        total: { requests: 0, prompt_tokens: 0, completion_tokens: 0 },
      }),
    ).toBe(0);
  });
});

describe("foldFlowNodes", () => {
  const nodes: DashboardFlowNode[] = [
    { id: "u1", stage: "user", label: "alice", value: 10, share: 0 },
    { id: "u2", stage: "user", label: "bob", value: 6, share: 0 },
    { id: "u3", stage: "user", label: "carol", value: 3, share: 0 },
    { id: "u4", stage: "user", label: "dave", value: 1, share: 0 },
    { id: "m1", stage: "model", label: "gpt-4o", value: 20, share: 0 },
  ];
  const links: DashboardFlowLink[] = [
    { source: "u1", target: "m1", value: 10 },
    { source: "u2", target: "m1", value: 6 },
    { source: "u3", target: "m1", value: 3 },
    { source: "u4", target: "m1", value: 1 },
  ];

  it("keeps the top N per stage and folds the rest into one other node", () => {
    const folded = foldFlowNodes(nodes, links, 2);
    const users = folded.nodes.filter((node) => node.stage === "user");
    expect(users.map((node) => node.id)).toEqual(["u1", "u2", "__other__:user"]);
    const other = users.find((node) => node.id === "__other__:user");
    expect(other?.label).toBe("其他 2 项");
    expect(other?.value).toBe(4);
    expect(other?.share).toBeCloseTo(4 / 20, 6);
    expect(users.reduce((sum, node) => sum + node.share, 0)).toBeCloseTo(1, 6);
    expect(folded.links).toHaveLength(3);
    expect(folded.links.find((link) => link.source === "__other__:user")?.value).toBe(4);
  });

  it("keeps every node when the limit is large enough", () => {
    const folded = foldFlowNodes(nodes, links, 10);
    expect(folded.nodes).toHaveLength(nodes.length);
    expect(folded.nodes.some((node) => node.id.startsWith("__other__"))).toBe(false);
    expect(folded.links).toHaveLength(links.length);
  });

  it("merges a legacy original-case id into the folded key and re-points links", () => {
    // 服务端形状（ADR-071 决策 14）：id 是 fold_key(name)，label 保留原始大小写。
    // 旧缓存里可能还留着原始大小写的 id——客户端兼容层要把它们并回折叠键，一个模型只留一个节点。
    const variants: DashboardFlowNode[] = [
      { id: "minimax-m3.1-flash", stage: "model", label: "MiniMax-M3.1-Flash", value: 7, share: 0 },
      { id: "MiniMax-M3.1-Flash", stage: "model", label: "MiniMax-M3.1-Flash", value: 3, share: 0 },
      { id: "u1", stage: "user", label: "alice", value: 10, share: 0 },
    ];
    const variantLinks: DashboardFlowLink[] = [
      { source: "u1", target: "minimax-m3.1-flash", value: 7 },
      { source: "u1", target: "MiniMax-M3.1-Flash", value: 3 },
    ];
    const folded = foldFlowNodes(variants, variantLinks, 10);
    const models = folded.nodes.filter((node) => node.stage === "model");
    expect(models).toHaveLength(1);
    expect(models[0].id).toBe("minimax-m3.1-flash");
    expect(models[0].label).toBe("MiniMax-M3.1-Flash");
    expect(models[0].value).toBe(10);
    expect(folded.links).toEqual([{ source: "u1", target: "minimax-m3.1-flash", value: 10 }]);
  });

  it("does not merge non-ascii twins -- same rule as the server fold", () => {
    const variants: DashboardFlowNode[] = [
      { id: "a1", stage: "model", label: "ÄBC", value: 1, share: 0 },
      { id: "a2", stage: "model", label: "äbc", value: 2, share: 0 },
    ];
    const folded = foldFlowNodes(variants, [], 10);
    expect(folded.nodes).toHaveLength(2);
  });
});

describe("layoutFlow", () => {
  it("lays out five stages inside the requested height and links every edge", () => {
    const nodes: DashboardFlowNode[] = [
      { id: "u1", stage: "user", label: "alice", value: 10, share: 0 },
      { id: "k1", stage: "key", label: "sk-1", value: 10, share: 0 },
      { id: "m1", stage: "model", label: "gpt-4o", value: 10, share: 0 },
      { id: "p1", stage: "upstream", label: "openai", value: 10, share: 0 },
      { id: "c1", stage: "channel", label: "东区", value: 10, share: 0 },
    ];
    const links: DashboardFlowLink[] = [
      { source: "u1", target: "k1", value: 10 },
      { source: "k1", target: "m1", value: 10 },
      { source: "m1", target: "p1", value: 10 },
      { source: "p1", target: "c1", value: 10 },
    ];
    const layout = layoutFlow(nodes, links, { width: 900, height: 560 });
    expect(layout.width).toBe(900);
    expect(layout.stages.map((stage) => stage.stage)).toEqual([...FLOW_STAGE_ORDER]);
    expect(layout.nodes).toHaveLength(5);
    expect(layout.links).toHaveLength(4);
    for (const node of layout.nodes) {
      expect(node.y).toBeGreaterThanOrEqual(0);
      expect(node.y + node.height).toBeLessThanOrEqual(560 - 22 + 0.001);
    }
    const xs = layout.nodes.map((node) => node.x);
    expect(xs).toEqual([...xs].sort((left, right) => left - right));
    for (const link of layout.links) {
      expect(link.path.startsWith("M ")).toBe(true);
      expect(link.path).toContain(" C ");
      expect(link.path.endsWith(" Z")).toBe(true);
    }
  });

  it("keeps dense stages inside the plot and preserves positive heights", () => {
    const nodes: DashboardFlowNode[] = Array.from({ length: 60 }, (_, index) => ({
      id: `u${index}`,
      stage: "user" as const,
      label: `user-${index}`,
      value: 60 - index,
      share: 0,
    }));
    const layout = layoutFlow(nodes, [], { width: 900, height: 560 });
    expect(layout.nodes).toHaveLength(60);
    const lowest = Math.max(...layout.nodes.map((node) => node.y + node.height));
    expect(lowest).toBeLessThanOrEqual(560 - 22 + 0.001);
    expect(Math.min(...layout.nodes.map((node) => node.height))).toBeGreaterThan(0);
  });
});

describe("assignCategoryColorsByName", () => {
  it("assigns one color per name across views and orders by merged requests", () => {
    const colors = assignCategoryColorsByName([
      [
        { name: "alpha", requests: 10 },
        { name: "shared", requests: 5 },
      ],
      [
        { name: "shared", requests: 30 },
        { name: "beta", requests: 1 },
      ],
    ]);
    expect(colors.get("shared")).toBe(CATEGORY_COLORS[0]);
    expect(colors.get("alpha")).toBe(CATEGORY_COLORS[1]);
    expect(colors.get("beta")).toBe(CATEGORY_COLORS[2]);
    expect(colors.size).toBe(3);
  });

  it("cycles the palette when there are more than 16 members", () => {
    const names = Array.from({ length: 18 }, (_, index) => ({ name: "m" + index, requests: 18 - index }));
    const colors = assignCategoryColorsByName([names]);
    expect(colors.size).toBe(18);
    expect(colors.get("m0")).toBe(CATEGORY_COLORS[0]);
    expect(colors.get("m16")).toBe(CATEGORY_COLORS[0]);
    expect(colors.get("m17")).toBe(CATEGORY_COLORS[1]);
  });

  it("returns an empty map for an empty palette", () => {
    expect(assignCategoryColorsByName([[{ name: "a", requests: 1 }]], []).size).toBe(0);
  });
});

describe("stackTrendSegments", () => {
  it("stacks members upward from the plot bottom in the given order", () => {
    const segments = stackTrendSegments(
      [
        { name: "a", color: "c1", value: 1 },
        { name: "b", color: "c2", value: 2 },
      ],
      (value) => value * 10,
      100,
    );
    expect(segments.map((segment) => segment.name)).toEqual(["a", "b"]);
    expect(segments[0]).toMatchObject({ y: 90, height: 10 });
    expect(segments[1]).toMatchObject({ y: 70, height: 20 });
  });

  it("skips non-positive and invalid values", () => {
    const segments = stackTrendSegments(
      [
        { name: "zero", color: "c1", value: 0 },
        { name: "neg", color: "c2", value: -2 },
        { name: "nan", color: "c3", value: Number.NaN },
        { name: "ok", color: "c4", value: 3 },
      ],
      (value) => value,
      50,
    );
    expect(segments).toHaveLength(1);
    expect(segments[0]).toMatchObject({ name: "ok", y: 47, height: 3 });
  });
});

describe("trendLatencyMembers", () => {
  const member = (name: string, requests: number, p95: number | null): TimeseriesModelPoint => ({
    name,
    requests,
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_tokens: 0,
    cache_hit_rate: 0,
    cost_usd: 0,
    errors: 0,
    p95_total_ms: p95,
  });

  it("keeps the request Top6 and merges the rest into a weighted P95 row", () => {
    const members = Array.from({ length: 9 }, (_, index) =>
      member("model-" + index, 100 - index * 10, 100 + index * 10),
    );
    const colors = new Map(members.map((item, index) => [item.name, "var(--chart-" + (index + 1) + ")"]));
    const rows = trendLatencyMembers(members, colors);
    expect(rows).toHaveLength(TREND_LATENCY_TOP_MEMBERS + 1);
    expect(rows.slice(0, TREND_LATENCY_TOP_MEMBERS).map((row) => row.name)).toEqual([
      "model-0",
      "model-1",
      "model-2",
      "model-3",
      "model-4",
      "model-5",
    ]);
    expect(rows[0].color).toBe("var(--chart-1)");
    const other = rows[rows.length - 1];
    expect(other.name).toBe(TREND_LATENCY_OTHER_LABEL);
    expect(other.color).toBe(TREND_OTHER_COLOR);
    expect(other.other).toBe(true);
    const rest = members.slice(TREND_LATENCY_TOP_MEMBERS);
    expect(other.value).toBe(
      weightedAverageP95(rest.map((item) => ({ value: item.p95_total_ms, requests: item.requests }))),
    );
    expect(other.requests).toBe(rest.reduce((sum, item) => sum + item.requests, 0));
  });

  it("drops members without requests and returns a null other row when there is no sample", () => {
    const rows = trendLatencyMembers(
      [member("idle", 0, 999), member("a", 10, null), member("b", 5, 200)],
      new Map(),
    );
    expect(rows.map((row) => row.name)).toEqual(["a", "b"]);
    expect(rows[0].color).toBe(TREND_OTHER_COLOR);
    expect(rows[0].value).toBeNull();
  });
});

describe("flowNodeNeighbors", () => {
  const nodes: DashboardFlowNode[] = [
    { id: "u1", stage: "user", label: "alice", value: 10, share: 0 },
    { id: "u2", stage: "user", label: "bob", value: 8, share: 0 },
    { id: "k1", stage: "key", label: "sk-main", value: 18, share: 0 },
    { id: "k2", stage: "key", label: "sk-alt", value: 2, share: 0 },
  ];
  const links: DashboardFlowLink[] = [
    { source: "u1", target: "k1", value: 10 },
    { source: "u2", target: "k1", value: 8 },
    { source: "u2", target: "k2", value: 2 },
  ];

  it("returns at most three upstream and downstream neighbours by value", () => {
    const neighbours = flowNodeNeighbors(nodes, links, "k1");
    expect(neighbours.upstream.map((item) => item.id)).toEqual(["u1", "u2"]);
    expect(neighbours.downstream).toEqual([]);
    expect(neighbours.upstream[0]).toMatchObject({ label: "alice", value: 10 });
  });

  it("caps each side at three neighbours", () => {
    const many = Array.from({ length: 6 }, (_, index) => ({
      id: "n" + index,
      stage: "user" as const,
      label: "n" + index,
      value: 6 - index,
      share: 0,
    }));
    const fanIn = many.map((node) => ({ source: node.id, target: "k1", value: node.value }));
    const neighbours = flowNodeNeighbors([...many, nodes[2]], fanIn, "k1");
    expect(neighbours.upstream).toHaveLength(3);
    expect(neighbours.upstream.map((item) => item.id)).toEqual(["n0", "n1", "n2"]);
  });
});


