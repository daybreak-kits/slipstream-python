import { useEffect, useRef } from "react";

import { apiRequest } from "@/lib/api";
import type { ColorAssignment } from "@/lib/chart-colors";
import {
  formatCacheHitRate,
  formatCompact,
  formatCount,
  formatCostOrUnconfigured,
  formatPercent,
  formatRate,
  formatSpanMinutes,
  formatUsdFine,
} from "@/lib/format";
import { formatMs } from "@/lib/logs";

export interface LatencyPercentiles {
  p50: number | null;
  p90: number | null;
  p95: number | null;
  p99: number | null;
}

export interface DashboardLatency {
  samples: number;
  ttft: LatencyPercentiles | null;
  ttfa: LatencyPercentiles | null;
  ttlt: LatencyPercentiles | null;
  total: LatencyPercentiles | null;
}

export interface DashboardSummary {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_hit_rate: number | null;
  cache_hit_rate_samples: number;
  /** ADR-068 B：cached>0 的请求数 ÷ 计入请求数。 */
  cache_request_hit_rate?: number;
  cache_request_hits?: number;
  /** ADR-068 C：同会话已出现 token 占比；无会话头时 ratio 为 null、sessions 为 0。 */
  cacheable_ratio?: number | null;
  cacheable_tokens?: number;
  cacheable_sessions?: number;
  /** ADR-068 D：节省的缓存 token 与按模型单价估算的金额。 */
  cache_saved_tokens?: number;
  cache_saved_usd?: number;
  cost_usd: number;
  errors: number;
  error_rate: number;
  /** P95 first-token latency; null when fewer than 5 usable samples (never 0). */
  p95_ttft_ms: number | null;
  /** P95 end-to-end latency; null when fewer than 5 usable samples (never 0). */
  p95_total_ms: number | null;
  latency: DashboardLatency;
  start_ms: number;
  end_ms: number;
  /** Minutes inside the window that actually contain requests (UTC minute buckets). */
  active_minutes: number;
  /** Raw window length in minutes, regardless of traffic. */
  span_minutes: number;
  /** requests / max(active_minutes, 1) */
  rpm: number;
  /** (prompt_tokens + completion_tokens) / max(active_minutes, 1) */
  tpm: number;
  /** Highest single-minute request count. */
  peak_rpm: number;
  /** Rows excluded from token/cache accounting because upstream reported no usage. */
  estimated_rows?: number;
}

export interface DashboardWindow {
  startMs?: number;
  endMs?: number;
}

export interface DashboardFilters {
  channel?: string;
  model?: string;
  status?: "" | "success" | "error";
}

function windowQuery(window?: DashboardWindow): string {
  if (window?.startMs === undefined || window?.endMs === undefined) return "";
  return `&start_ms=${window.startMs}&end_ms=${window.endMs}`;
}

function filterQuery(filters?: DashboardFilters): string {
  const params = new URLSearchParams();
  if (filters?.channel) params.set("channel", filters.channel);
  if (filters?.model) params.set("model", filters.model);
  if (filters?.status) params.set("status", filters.status);
  const query = params.toString();
  return query ? `&${query}` : "";
}

// 看板与日志页共用同一命中率实现（全站统一一位小数）；这里只做转出，保持既有导入路径可用。
export { formatCacheHitRate };

export async function getDashboardSummary(
  rangeKey: string,
  timezone: string,
  window?: DashboardWindow,
  filters?: DashboardFilters,
): Promise<DashboardSummary> {
  return apiRequest<DashboardSummary>(
    `/api/dashboard/summary?range=${encodeURIComponent(rangeKey)}&tz=${encodeURIComponent(timezone)}${windowQuery(window)}${filterQuery(filters)}`,
  );
}

export interface TimeseriesModelPoint {
  name: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_hit_rate: number | null;
  cost_usd: number;
  errors: number;
  /** P95 end-to-end latency for this model in this bucket; null when the sample count is below 5. */
  p95_total_ms: number | null;
}

export interface TimeseriesPoint {
  start_ms: number;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_hit_rate: number | null;
  cost_usd: number;
  errors: number;
  /** Per-bucket P95s; null (not 0) whenever the bucket holds fewer than 5 samples. */
  p95_ttft_ms: number | null;
  p95_total_ms: number | null;
  p95_ttfa_ms: number | null;
  p95_ttlt_ms: number | null;
  by_model: TimeseriesModelPoint[];
  by_channel: TimeseriesModelPoint[];
  /** Per-bucket breakdown by upstream model. */
  by_upstream_model: TimeseriesModelPoint[];
}

export interface NamedCount {
  name: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_usd: number;
}

export interface DashboardTimeseries {
  range: string;
  timezone: string;
  granularity: "hour" | "day";
  start_ms: number;
  end_ms: number;
  points: TimeseriesPoint[];
  top_models: NamedCount[];
  top_channels: NamedCount[];
  /** Top upstream models. */
  top_upstream_models: NamedCount[];
  error_distribution: Array<{ code: string; count: number }>;
  heatmap: Array<{ weekday: number; hour: number; requests: number }>;
}

export async function getDashboardTimeseries(
  rangeKey: string,
  timezone: string,
  window?: DashboardWindow,
  filters?: DashboardFilters,
  /**
   * 单维度视角（combo / upstream / channel）：进了 query key 才会在切维度时重取。
   * 后端目前忽略该参数、三份 breakdown 照旧全返回（task-113 / t5 落地后再收窄），
   * 前端按维度只渲染选中的那份，不重复折叠数据。
   */
  dimension?: TrendDimension,
): Promise<DashboardTimeseries> {
  return apiRequest<DashboardTimeseries>(
    `/api/dashboard/timeseries?range=${encodeURIComponent(rangeKey)}&tz=${encodeURIComponent(timezone)}${windowQuery(window)}${filterQuery(filters)}${dimension ? "&dimension=" + encodeURIComponent(dimension) : ""}`,
  );
}

/**
 * 分流图（Sankey）五阶段：用户 → 密钥 → 模型 → 上游模型 → 渠道。
 * 后端返回的 node.id 在 links 里作为 source/target 直接引用。
 */
export type FlowStage = "user" | "key" | "model" | "upstream" | "channel";

export const FLOW_STAGE_ORDER: readonly FlowStage[] = [
  "user",
  "key",
  "model",
  "upstream",
  "channel",
];

export const FLOW_STAGE_LABELS: Record<FlowStage, string> = {
  user: "用户",
  key: "密钥",
  model: "模型",
  upstream: "上游模型",
  channel: "渠道",
};

export type FlowMetric = "requests" | "tokens";

export const FLOW_DISPLAY_LIMITS = [10, 20, 50, 100] as const;
export type FlowDisplayLimit = (typeof FLOW_DISPLAY_LIMITS)[number];

export interface DashboardFlowNode {
  id: string;
  stage: FlowStage;
  label: string;
  value: number;
  share: number;
}

export interface DashboardFlowLink {
  source: string;
  target: string;
  value: number;
}

export interface DashboardFlowTotal {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface DashboardFlows {
  metric: FlowMetric;
  stages: FlowStage[];
  nodes: DashboardFlowNode[];
  links: DashboardFlowLink[];
  total: DashboardFlowTotal;
}

export async function getDashboardFlows(
  rangeKey: string,
  timezone: string,
  metric: FlowMetric,
  window?: DashboardWindow,
  filters?: DashboardFilters,
): Promise<DashboardFlows> {
  return apiRequest<DashboardFlows>(
    `/api/dashboard/flows?range=${encodeURIComponent(rangeKey)}&tz=${encodeURIComponent(timezone)}&metric=${encodeURIComponent(metric)}${windowQuery(window)}${filterQuery(filters)}`,
  );
}

/** 分流图当前指标的总量：requests 用请求数，tokens 用输入 + 输出词元。 */
export function flowMetricTotal(flows: Pick<DashboardFlows, "metric" | "total">): number {
  const total = flows.total;
  const value =
    flows.metric === "tokens"
      ? (total?.prompt_tokens ?? 0) + (total?.completion_tokens ?? 0)
      : (total?.requests ?? 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * 把只有大小写差异的同名节点归并成一个：id/label 保留第一次出现的写法，
 * value 累加，links 重指到归并后的 id 并聚合。服务端已经按 ADR-071 决策 14
 * （fold_key，ASCII 小写）合并过节点，这里是防线：旧缓存或旧响应仍可能带着
 * 两种拼写的节点，不能让桑基图上出现两个「同一个模型」。
 */
/**
 * 兼容层：服务端 flows 节点 id 已按 fold_key 折叠（ADR-071 决策 14），label 保留原始大小写；
 * 这里再按 stage + 小写 id 合并一次，只为容忍旧缓存/旧服务端响应里仍带原始大小写 id 的数据。
 * 正常响应不会走到合并分支——它是兜底，不是主路径，不要据此扩大折叠规则。
 */
function mergeCaseVariants(
  nodes: readonly DashboardFlowNode[],
  links: readonly DashboardFlowLink[],
): { nodes: DashboardFlowNode[]; links: DashboardFlowLink[] } {
  const keptIdByKey = new Map<string, string>();
  const mergedNodes = new Map<string, DashboardFlowNode>();
  for (const node of nodes) {
    const key = `${node.stage}\u0000${node.id.toLowerCase()}`;
    const kept = keptIdByKey.get(key);
    if (kept === undefined) {
      keptIdByKey.set(key, node.id);
      mergedNodes.set(node.id, { ...node });
    } else {
      const target = mergedNodes.get(kept);
      if (target) target.value += Number.isFinite(node.value) && node.value > 0 ? node.value : 0;
    }
  }
  const nodeStage = new Map<string, string>();
  for (const node of nodes) nodeStage.set(node.id, node.stage);
  const canonicalId = (id: string) => {
    const stage = nodeStage.get(id) ?? "";
    return keptIdByKey.get(`${stage}\u0000${id.toLowerCase()}`) ?? id;
  };
  const aggregated = new Map<string, DashboardFlowLink>();
  for (const link of links) {
    const source = canonicalId(link.source);
    const target = canonicalId(link.target);
    if (source === target) continue;
    if (!Number.isFinite(link.value) || link.value <= 0) continue;
    const key = `${source}\u0000${target}`;
    const existing = aggregated.get(key);
    if (existing) existing.value += link.value;
    else aggregated.set(key, { source, target, value: link.value });
  }
  return { nodes: [...mergedNodes.values()], links: [...aggregated.values()] };
}

/**
 * 每个阶段只保留前 N 个节点，剩余节点折叠成「其他 N 项」。
 * 折叠发生在同一阶段内，links 会同步聚合，避免图上出现被删掉的节点连线。
 */
export function foldFlowNodes(
  nodes: readonly DashboardFlowNode[],
  links: readonly DashboardFlowLink[],
  limit: number,
): { nodes: DashboardFlowNode[]; links: DashboardFlowLink[] } {
  const { nodes: deduped, links: dedupedLinks } = mergeCaseVariants(nodes, links);
  const max = Math.max(1, Math.floor(limit));
  const mapped = new Map<string, string>();
  const keptByStage = new Map<FlowStage, DashboardFlowNode[]>();
  const foldedByStage = new Map<FlowStage, { value: number; count: number }>();
  const totalsByStage = new Map<FlowStage, number>();
  const seen = new Set<string>();

  for (const stage of FLOW_STAGE_ORDER) {
    const stageNodes = deduped
      .filter((node) => {
        if (node.stage !== stage || !Number.isFinite(node.value) || node.value <= 0) return false;
        if (seen.has(node.id)) return false;
        seen.add(node.id);
        return true;
      })
      .sort((left, right) => right.value - left.value || left.label.localeCompare(right.label));
    const total = stageNodes.reduce((sum, node) => sum + node.value, 0);
    totalsByStage.set(stage, total);
    const kept = stageNodes.slice(0, max);
    for (const node of kept) {
      keptByStage.set(stage, [...(keptByStage.get(stage) ?? []), node]);
      mapped.set(node.id, node.id);
    }
    const rest = stageNodes.slice(max);
    if (rest.length) {
      const id = `__other__:${stage}`;
      for (const node of rest) mapped.set(node.id, id);
      foldedByStage.set(stage, {
        value: rest.reduce((sum, node) => sum + node.value, 0),
        count: rest.length,
      });
    }
  }

  const foldedNodes: DashboardFlowNode[] = [];
  for (const stage of FLOW_STAGE_ORDER) {
    const total = totalsByStage.get(stage) ?? 0;
    for (const node of keptByStage.get(stage) ?? []) {
      foldedNodes.push({ ...node, share: total > 0 ? node.value / total : 0 });
    }
    const folded = foldedByStage.get(stage);
    if (folded) {
      foldedNodes.push({
        id: `__other__:${stage}`,
        stage,
        label: `其他 ${folded.count} 项`,
        value: folded.value,
        share: total > 0 ? folded.value / total : 0,
      });
    }
  }

  const aggregated = new Map<string, DashboardFlowLink>();
  for (const link of dedupedLinks) {
    const source = mapped.get(link.source);
    const target = mapped.get(link.target);
    if (!source || !target || source === target) continue;
    if (!Number.isFinite(link.value) || link.value <= 0) continue;
    const key = `${source}\u0000${target}`;
    const existing = aggregated.get(key);
    if (existing) existing.value += link.value;
    else aggregated.set(key, { source, target, value: link.value });
  }

  return { nodes: foldedNodes, links: [...aggregated.values()] };
}

/** 桑基图固定高度与可读宽度下限；窄屏由外层横向滚动，不压缩节点。 */
export const FLOW_CHART_HEIGHT = 560;
export const FLOW_MIN_WIDTH = 900;
export const FLOW_MAX_WIDTH = 1400;

const FLOW_NODE_WIDTH = 14;
const FLOW_PADDING_X = 24;
const FLOW_TOP = 32;
const FLOW_BOTTOM = 22;
const FLOW_MAX_GAP = 6;
const FLOW_MIN_GAP = 1;

export interface FlowLayoutOptions {
  width: number;
  height: number;
  stages?: readonly FlowStage[];
}

export interface FlowLayoutNode extends DashboardFlowNode {
  stageIndex: number;
  x: number;
  y: number;
  width: number;
  height: number;
  centerY: number;
  labelX: number;
  labelAnchor: "start" | "end";
}

export interface FlowLayoutLink extends DashboardFlowLink {
  path: string;
  colorIndex: number;
}

export interface FlowLayoutStage {
  stage: FlowStage;
  label: string;
  x: number;
  y: number;
  colorIndex: number;
}

export interface FlowLayout {
  width: number;
  height: number;
  nodes: FlowLayoutNode[];
  links: FlowLayoutLink[];
  stages: FlowLayoutStage[];
}

/**
 * 分层布局：
 * 1. 每个阶段按 value 降序铺满绘图区高度，阶段内节点共享同一套 gap；
 * 2. 用出入边重心做 前向 → 后向 → 前向 三轮排序，减少 ribbon 交叉；
 * 3. 连线端口在节点内按另一端中心 y 排序后顺序分配，带宽与 value 成正比。
 * 纯函数，不依赖 DOM，方便单测直接锁定高度与连线数量。
 */
export function layoutFlow(
  nodes: readonly DashboardFlowNode[],
  links: readonly DashboardFlowLink[],
  options: FlowLayoutOptions,
): FlowLayout {
  const stages = options.stages?.length ? options.stages : FLOW_STAGE_ORDER;
  const width = Math.max(FLOW_MIN_WIDTH, Number.isFinite(options.width) ? options.width : FLOW_MIN_WIDTH);
  const height = Math.max(240, Number.isFinite(options.height) ? options.height : FLOW_CHART_HEIGHT);
  const stageIndex = new Map<FlowStage, number>(stages.map((stage, index) => [stage, index]));
  const order: DashboardFlowNode[][] = stages.map(() => []);
  const seen = new Set<string>();
  for (const node of nodes) {
    const index = stageIndex.get(node.stage);
    if (index === undefined || seen.has(node.id)) continue;
    if (!Number.isFinite(node.value) || node.value <= 0) continue;
    seen.add(node.id);
    order[index].push(node);
  }
  for (const stageNodes of order) {
    stageNodes.sort((left, right) => right.value - left.value || left.label.localeCompare(right.label));
  }

  const left = FLOW_PADDING_X;
  const right = width - FLOW_PADDING_X;
  const columnStep =
    stages.length > 1 ? (right - left - FLOW_NODE_WIDTH) / (stages.length - 1) : 0;
  const xOf = (index: number) => left + index * columnStep;
  const top = FLOW_TOP;
  const plotHeight = height - FLOW_TOP - FLOW_BOTTOM;
  const placed = new Map<string, { y: number; height: number; centerY: number }>();

  const placeStage = (index: number) => {
    const stageNodes = order[index];
    const total = stageNodes.reduce((sum, node) => sum + node.value, 0);
    const gap =
      stageNodes.length > 1
        ? Math.min(FLOW_MAX_GAP, Math.max(FLOW_MIN_GAP, (plotHeight - stageNodes.length * 2) / (stageNodes.length - 1)))
        : 0;
    const available = plotHeight - gap * Math.max(0, stageNodes.length - 1);
    const rawHeights = stageNodes.map((node) =>
      total > 0 ? Math.max(0.25, (available * node.value) / total) : 1,
    );
    const rawTotal = rawHeights.reduce((sum, nodeHeight) => sum + nodeHeight, 0);
    // 极小节点被抬到最小高度后可能撑破绘图区：整体等比缩回，保证底边仍落在 plotBottom。
    const shrink = rawTotal > available && rawTotal > 0 ? available / rawTotal : 1;
    // 节点最小高度 8px：份额极小的节点也要能看见、能悬停；极端节点数放不下时
    // 退化为「按可用高度均分」，优先保证布局不越界（不会重叠）。
    const minHeight = Math.min(FLOW_MIN_NODE_HEIGHT, available / Math.max(1, stageNodes.length));
    const heights = rawHeights.map((nodeHeight) => Math.max(minHeight, nodeHeight * shrink));
    const heightsTotal = heights.reduce((sum, nodeHeight) => sum + nodeHeight, 0);
    if (heightsTotal > available && heightsTotal > 0) {
      const scale = available / heightsTotal;
      for (let index = 0; index < heights.length; index += 1) heights[index] *= scale;
    }
    let cursor = top;
    for (let index = 0; index < stageNodes.length; index += 1) {
      const node = stageNodes[index];
      const nodeHeight = heights[index];
      placed.set(node.id, { y: cursor, height: nodeHeight, centerY: cursor + nodeHeight / 2 });
      cursor += nodeHeight + gap;
    }
  };
  for (let index = 0; index < stages.length; index += 1) placeStage(index);

  const incoming = new Map<string, DashboardFlowLink[]>();
  const outgoing = new Map<string, DashboardFlowLink[]>();
  for (const link of links) {
    if (!seen.has(link.source) || !seen.has(link.target)) continue;
    if (!Number.isFinite(link.value) || link.value <= 0) continue;
    incoming.set(link.target, [...(incoming.get(link.target) ?? []), link]);
    outgoing.set(link.source, [...(outgoing.get(link.source) ?? []), link]);
  }

  const barycenter = (id: string, direction: "in" | "out"): number => {
    const list = direction === "in" ? incoming.get(id) : outgoing.get(id);
    let weighted = 0;
    let total = 0;
    for (const link of list ?? []) {
      const other = direction === "in" ? placed.get(link.source) : placed.get(link.target);
      if (!other) continue;
      weighted += other.centerY * link.value;
      total += link.value;
    }
    return total > 0 ? weighted / total : (placed.get(id)?.centerY ?? 0);
  };
  const reorder = (index: number, direction: "in" | "out") => {
    const stageNodes = order[index];
    const original = new Map(stageNodes.map((node, orderIndex) => [node.id, orderIndex]));
    order[index] = [...stageNodes].sort((leftNode, rightNode) => {
      const score = barycenter(leftNode.id, direction) - barycenter(rightNode.id, direction);
      return score || (original.get(leftNode.id) ?? 0) - (original.get(rightNode.id) ?? 0);
    });
    placeStage(index);
  };
  for (let index = 1; index < stages.length; index += 1) reorder(index, "in");
  for (let index = stages.length - 2; index >= 0; index -= 1) reorder(index, "out");
  for (let index = 1; index < stages.length; index += 1) reorder(index, "in");

  const layoutNodes: FlowLayoutNode[] = [];
  for (let index = 0; index < stages.length; index += 1) {
    for (const node of order[index]) {
      const position = placed.get(node.id);
      if (!position) continue;
      layoutNodes.push({
        ...node,
        stageIndex: index,
        x: xOf(index),
        y: position.y,
        width: FLOW_NODE_WIDTH,
        height: position.height,
        centerY: position.centerY,
        labelX: index === stages.length - 1 ? xOf(index) - 6 : xOf(index) + FLOW_NODE_WIDTH + 6,
        labelAnchor: index === stages.length - 1 ? "end" : "start",
      });
    }
  }
  const byId = new Map(layoutNodes.map((node) => [node.id, node]));
  const outgoingLinks = new Map<string, DashboardFlowLink[]>();
  const incomingLinks = new Map<string, DashboardFlowLink[]>();
  for (const link of links) {
    if (!byId.has(link.source) || !byId.has(link.target)) continue;
    if (!Number.isFinite(link.value) || link.value <= 0) continue;
    outgoingLinks.set(link.source, [...(outgoingLinks.get(link.source) ?? []), link]);
    incomingLinks.set(link.target, [...(incomingLinks.get(link.target) ?? []), link]);
  }
  for (const list of outgoingLinks.values()) {
    list.sort((leftLink, rightLink) => {
      const leftTarget = byId.get(leftLink.target)?.centerY ?? 0;
      const rightTarget = byId.get(rightLink.target)?.centerY ?? 0;
      return leftTarget - rightTarget || leftLink.target.localeCompare(rightLink.target);
    });
  }
  for (const list of incomingLinks.values()) {
    list.sort((leftLink, rightLink) => {
      const leftSource = byId.get(leftLink.source)?.centerY ?? 0;
      const rightSource = byId.get(rightLink.source)?.centerY ?? 0;
      return leftSource - rightSource || leftLink.source.localeCompare(rightLink.source);
    });
  }

  const outPorts = new Map<DashboardFlowLink, { y0: number; y1: number }>();
  for (const [id, list] of outgoingLinks) {
    const source = byId.get(id);
    if (!source) continue;
    const total = list.reduce((sum, link) => sum + link.value, 0);
    let cursor = source.y;
    for (const link of list) {
      const portHeight = total > 0 ? (source.height * link.value) / total : 0;
      outPorts.set(link, { y0: cursor, y1: cursor + portHeight });
      cursor += portHeight;
    }
  }
  const inPorts = new Map<DashboardFlowLink, { y0: number; y1: number }>();
  for (const [id, list] of incomingLinks) {
    const target = byId.get(id);
    if (!target) continue;
    const total = list.reduce((sum, link) => sum + link.value, 0);
    let cursor = target.y;
    for (const link of list) {
      const portHeight = total > 0 ? (target.height * link.value) / total : 0;
      inPorts.set(link, { y0: cursor, y1: cursor + portHeight });
      cursor += portHeight;
    }
  }

  const round = (value: number) => Math.round(value * 100) / 100;
  const layoutLinks: FlowLayoutLink[] = [];
  for (const link of links) {
    const source = byId.get(link.source);
    const target = byId.get(link.target);
    const from = outPorts.get(link);
    const to = inPorts.get(link);
    if (!source || !target || !from || !to) continue;
    const sx = round(source.x + source.width);
    const tx = round(target.x);
    const midX = round((sx + tx) / 2);
    const path =
      `M ${sx} ${round(from.y0)} C ${midX} ${round(from.y0)}, ${midX} ${round(to.y0)}, ${tx} ${round(to.y0)} ` +
      `L ${tx} ${round(to.y1)} C ${midX} ${round(to.y1)}, ${midX} ${round(from.y1)}, ${sx} ${round(from.y1)} Z`;
    layoutLinks.push({ ...link, path, colorIndex: source.stageIndex });
  }

  return {
    width,
    height,
    nodes: layoutNodes,
    links: layoutLinks,
    stages: stages.map((stage, index) => ({
      stage,
      label: FLOW_STAGE_LABELS[stage],
      x: xOf(index) + FLOW_NODE_WIDTH / 2,
      y: 14,
      colorIndex: index,
    })),
  };
}

/**
 * 看板展示层视图模型：KPI 卡文案与趋势图聚合（不依赖 React 与组件库）。
 * 页面只负责渲染，测试直接覆盖本节。
 */

/** 悬停提示宽度（px）：必须与 tooltip 的 w-56、left 的 clamp 边界保持一致。 */
export const TREND_TOOLTIP_WIDTH = 224;

/** 趋势图指标的取值口径；纵轴刻度文案与 tooltip 共用同一套格式化。 */
export type TrendMetric = "requests" | "tokens" | "cost" | "latency";

/**
 * 趋势图纵轴刻度：把最大值抬到「好读」的台阶（1 / 2 / 2.5 / 5 × 10ⁿ），
 * 让刻度落在 5/10/15 或 50M/100M/200M 这类整量级上，而不是按原始最大值均分出的 7.3/14.6。
 * integerOnly 给请求数这类不可分割的指标：步长至少 1，刻度不出小数。
 * 返回的 max 是顶部刻度，柱高与刻度线共用它，柱顶才能正好落在刻度上。
 */
export function trendScale(
  maxValue: number,
  tickCount = 4,
  integerOnly = false,
): { max: number; ticks: number[] } {
  if (!Number.isFinite(maxValue) || maxValue <= 0) return { max: 1, ticks: [0, 1] };
  const rawStep = maxValue / Math.max(1, tickCount);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  const step = integerOnly ? Math.max(1, Math.ceil(factor * magnitude)) : factor * magnitude;
  const max = Math.ceil(maxValue / step) * step;
  const ticks: number[] = [];
  // 2.5 这类步长反复累加会积出 7.500000000000001，出栈前统一抹掉浮点噪声。
  for (let value = 0; value <= max + step / 2; value += step) {
    ticks.push(Number(value.toFixed(6)));
  }
  return { max, ticks };
}

/**
 * 纵轴刻度文案：与柱高、tooltip 同口径，只是不含 null 分支。
 * 整量级去掉多余的 .00（200M、20s 而不是 200.00M、20.00s）——刻度是标尺不是精确读数，
 * 后缀省略的零只会把标签挤宽；1.50M 这类真小数保留。成本仍按美元两位小数。
 */
export function formatTrendAxis(value: number, metric: TrendMetric): string {
  if (metric === "cost") return formatUsdFine(value);
  const text = metric === "latency" ? formatP95(value) : formatCompact(value);
  return text.replace(/\.00(?=[KMB]?$|s$)/, "");
}

/**
 * P95 分位展示：样本不足时后端返回 null，非正值同样视为「没有有效样本」，
 * 统一显示破折号，避免把 0ms 当成真实首字/总耗时。
 */
export function formatP95(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) return "—";
  return formatMs(value);
}

/**
 * 加权平均 P95 的展示：权重（可用样本的请求数合计）为 0 时没有可信的加权结果，
 * 显示破折号，而不是 0.00s 或 NaN。
 */
export function formatWeightedP95(value: number | null, weight: number): string {
  if (!Number.isFinite(weight) || weight <= 0) return "—";
  return formatP95(value);
}

/**
 * 延迟类聚合：把各模型的 p95 按请求数加权平均。
 * p95 是分位数，直接相加或取算术平均都会失真，所以 hover 合计、图例合计统一走这里。
 * 空样本（value 为 null 或 <=0）与零权重（requests<=0）不参与，不能把 null 当作 0ms 计入。
 */
export function weightedAverageP95(rows: Array<{ value: number | null; requests: number }>): number {
  let sum = 0;
  let weight = 0;
  for (const row of rows) {
    if (row.value === null) continue;
    if (!Number.isFinite(row.value) || !Number.isFinite(row.requests)) continue;
    if (row.value <= 0 || row.requests <= 0) continue;
    sum += row.value * row.requests;
    weight += row.requests;
  }
  return weight > 0 ? Math.round((sum / weight) * 100) / 100 : 0;
}

export type TrendTooltipRow = { name: string; color: string; value: number | null };

/**
 * 悬停某个时段时 tooltip 要展示的行。
 * series 为 null 表示落在列的空白区：展示该时段所有可见且有值的模型；
 * 否则只展示被悬停的那个色块。
 */
export function trendTooltipRows(
  assignments: ColorAssignment[],
  values: Array<number | null>,
  hidden: Set<string>,
  series: number | null,
): TrendTooltipRow[] {
  const rows: TrendTooltipRow[] = [];
  assignments.forEach((item, index) => {
    if (hidden.has(item.name)) return;
    if (series !== null && index !== series) return;
    const value = values[index] ?? null;
    if (series === null && (value === null || value <= 0)) return;
    rows.push({ name: item.name, color: item.color, value });
  });
  return rows;
}

/* --------------------------- 趋势图：成员级堆叠 --------------------------- */

/**
 * 趋势图的三个分组视角：综合模型（请求里写的 combo）、上游模型（实际命中的
 * upstream_model）、上游渠道（channel_name）。三者是同一批请求的不同切法，
 * 跨视角不可相加；看板一次只画一个维度，切换维度就换一份数据（进 query key）。
 */
export const TREND_DIMENSIONS = [
  { value: "combo", label: "综合模型" },
  { value: "upstream", label: "上游模型" },
  { value: "channel", label: "上游渠道" },
] as const;

export type TrendDimension = (typeof TREND_DIMENSIONS)[number]["value"];

export const TREND_DIMENSION_LABELS: Record<TrendDimension, string> = {
  combo: "综合模型",
  upstream: "上游模型",
  channel: "上游渠道",
};

/**
 * 看板的默认口径：URL 不带 ?dimension= / 指标参数时按「综合模型 + 词元」出图。
 * 默认维度必须是后端认识的 dimension 值（combo）：页面每次请求都显式带上它，
 * 不依赖后端 dimension default=""（那个空默认只是给旧客户端保留的全量兼容）。
 */
export const TREND_DEFAULT_DIMENSION: TrendDimension = "combo";
export const TREND_DEFAULT_METRIC: TrendMetric = "tokens";

/** 某个时段里，某个视角的成员明细（后端每个桶都返回三份，不截断）。 */
export function trendMembers(
  point: TimeseriesPoint,
  dimension: TrendDimension,
): TimeseriesModelPoint[] {
  if (dimension === "combo") return point.by_model;
  if (dimension === "upstream") return point.by_upstream_model;
  return point.by_channel;
}

/** 延迟类指标并排展示的成员上限：第 7 名起合并成「其他 · 加权 P95」。 */
export const TREND_LATENCY_TOP_MEMBERS = 6;

/** 延迟类指标的合并条目文案（ADR-040 的显式范围例外：P95 不可相加）。 */
export const TREND_LATENCY_OTHER_LABEL = "其他 · 加权 P95";

/** 合并条目的中性色：不属于 16 槽分类色板，避免和真实成员抢色。 */
export const TREND_OTHER_COLOR = "var(--muted-foreground)";

export interface TrendStackSegment {
  name: string;
  color: string;
  value: number;
  y: number;
  height: number;
}

/**
 * 堆叠分段：从基线往上按顺序累加，返回每段的 y 与 height（SVG 坐标）。
 * 只依赖一个比例函数，纯数学，方便单测锁定「分段顺序 + 段高之和 = 合计高」。
 */
export function stackTrendSegments(
  members: ReadonlyArray<{ name: string; color: string; value: number }>,
  ratio: (value: number) => number,
  plotBottom: number,
): TrendStackSegment[] {
  const segments: TrendStackSegment[] = [];
  let cursor = plotBottom;
  for (const member of members) {
    if (!Number.isFinite(member.value) || member.value <= 0) continue;
    const height = Math.max(0, ratio(member.value));
    cursor -= height;
    segments.push({ name: member.name, color: member.color, value: member.value, y: cursor, height });
  }
  return segments;
}

/* ------------------------ 趋势图：响应式几何与曲线 ------------------------ */

/** 图区最小尺寸（px）：再小坐标轴就没有放标签的地方了。 */
export const TREND_MIN_CHART_WIDTH = 320;
export const TREND_MIN_CHART_HEIGHT = 220;

/** 横轴相邻两个时间标签至少留的像素宽度：低于它就隔一个抽稀一个，避免文字叠字。 */
export const TREND_AXIS_LABEL_MIN_WIDTH = 46;

/** 纵轴每条约占的高度（px）：容器越高刻度越密；数量夹在 4–8 条之间，太密会糊成一片。 */
const TREND_AXIS_TICK_SPACING = 44;

/** 趋势图坐标系：一切坐标都由容器的真实像素尺寸推出来，不再依赖固定 viewBox。 */
export interface TrendChartGeometry {
  /** 容器真实宽度（px），也是 SVG 的 viewBox 宽度。 */
  width: number;
  /** 容器真实高度（px），也是 SVG 的 viewBox 高度。 */
  height: number;
  plotLeft: number;
  plotTop: number;
  plotBottom: number;
  plotWidth: number;
  plotHeight: number;
  /** 横轴时间标签的基线 y。 */
  axisLabelY: number;
  /** 每个时间桶的横向宽度。 */
  slot: number;
}

/**
 * 按容器真实宽高算坐标：左边留给纵轴刻度，底部留给时间标签，其余全部给绘图区。
 * bucketCount 只影响槽宽，纵轴与左右边距跟桶数无关——换时间范围不该让图跳一下高度。
 */
export function trendChartGeometry(
  width: number,
  height: number,
  bucketCount: number,
): TrendChartGeometry {
  const safeWidth = Number.isFinite(width) && width > 0 ? Math.round(width) : 760;
  const safeHeight = Number.isFinite(height) && height > 0 ? Math.round(height) : 300;
  const chartWidth = Math.max(TREND_MIN_CHART_WIDTH, safeWidth);
  const chartHeight = Math.max(TREND_MIN_CHART_HEIGHT, safeHeight);
  // 窄屏把纵轴标签列收窄 6px，多出来的宽度留给曲线。
  const plotLeft = chartWidth < 480 ? 40 : 46;
  const plotTop = 22;
  const plotBottom = chartHeight - 26;
  const plotWidth = Math.max(1, chartWidth - plotLeft);
  const buckets = Math.max(1, Math.round(Number.isFinite(bucketCount) ? bucketCount : 1));
  return {
    width: chartWidth,
    height: chartHeight,
    plotLeft,
    plotTop,
    plotBottom,
    plotWidth,
    plotHeight: Math.max(1, plotBottom - plotTop),
    axisLabelY: chartHeight - 8,
    slot: plotWidth / buckets,
  };
}

/** 纵轴刻度数量随高度自适应：高度越大刻度越密，至少 4 条、最多 8 条。 */
export function trendYAxisTickCount(plotHeight: number): number {
  if (!Number.isFinite(plotHeight) || plotHeight <= 0) return 4;
  return Math.min(8, Math.max(4, Math.round(plotHeight / TREND_AXIS_TICK_SPACING)));
}

/** 横轴标签抽稀：按真实槽宽决定隔几个桶放一个标签，窄屏不会重叠。 */
export function trendAxisLabelIndexes(
  bucketCount: number,
  slot: number,
  labelWidth = TREND_AXIS_LABEL_MIN_WIDTH,
): number[] {
  const count = Math.max(0, Math.round(Number.isFinite(bucketCount) ? bucketCount : 0));
  const step = Math.max(1, Math.ceil(labelWidth / Math.max(slot, 1)));
  const indexes: number[] = [];
  for (let index = 0; index < count; index += step) indexes.push(index);
  return indexes;
}

export interface TrendChartPoint {
  x: number;
  y: number;
}

const round2 = (value: number) => Math.round(value * 100) / 100;

/**
 * 单调三次曲线（Fritsch–Carlson / monotone cubic）：曲线经过每个数据点，
 * 且在相邻两点之间不会越出这两点的值域——分位线、词元曲线都不会「凭空冲到负数」或拱过峰顶。
 * 返回 SVG path：M 起点 + 每段一个 C。
 */
export function monotoneCubicPath(points: readonly TrendChartPoint[]): string {
  const usable = points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!usable.length) return "";
  if (usable.length === 1) return "M " + round2(usable[0].x) + " " + round2(usable[0].y);
  const count = usable.length;
  const spans: number[] = [];
  const slopes: number[] = [];
  for (let index = 0; index < count - 1; index += 1) {
    const span = usable[index + 1].x - usable[index].x;
    spans.push(span);
    slopes.push(span === 0 ? 0 : (usable[index + 1].y - usable[index].y) / span);
  }
  const tangents = new Array<number>(count).fill(0);
  tangents[0] = slopes[0];
  tangents[count - 1] = slopes[count - 2];
  for (let index = 1; index < count - 1; index += 1) {
    const before = slopes[index - 1];
    const after = slopes[index];
    // 拐点（前后斜率异号）切线归零：曲线在这里自然成峰/成谷，不会过冲。
    if (before * after <= 0) {
      tangents[index] = 0;
      continue;
    }
    const spanBefore = spans[index - 1];
    const spanAfter = spans[index];
    tangents[index] =
      (2 * spanAfter + spanBefore + (spanAfter + 2 * spanBefore)) /
      ((2 * spanAfter + spanBefore) / before + (spanAfter + 2 * spanBefore) / after);
  }
  // Fritsch–Carlson 限幅：切线不超过相邻割线斜率的 3 倍，保证段内单调。
  for (let index = 0; index < count - 1; index += 1) {
    const slope = slopes[index];
    if (slope === 0) {
      tangents[index] = 0;
      tangents[index + 1] = 0;
      continue;
    }
    const limit = 3 * Math.abs(slope);
    tangents[index] = Math.max(-limit, Math.min(limit, tangents[index]));
    tangents[index + 1] = Math.max(-limit, Math.min(limit, tangents[index + 1]));
  }
  let path = "M " + round2(usable[0].x) + " " + round2(usable[0].y);
  for (let index = 0; index < count - 1; index += 1) {
    const handle = spans[index] / 3;
    const c1x = usable[index].x + handle;
    const c1y = usable[index].y + tangents[index] * handle;
    const c2x = usable[index + 1].x - handle;
    const c2y = usable[index + 1].y - tangents[index + 1] * handle;
    path +=
      " C " +
      round2(c1x) +
      " " +
      round2(c1y) +
      ", " +
      round2(c2x) +
      " " +
      round2(c2y) +
      ", " +
      round2(usable[index + 1].x) +
      " " +
      round2(usable[index + 1].y);
  }
  return path;
}

/** 一条曲线的两段路径：实线是「连续有样本」的平滑段，虚线是跨过空桶的连接段。 */
export interface TrendLinePaths {
  solid: string;
  dashed: string;
  /** 连续有值的桶区间，测试与面积填充共用同一份断点判定。 */
  runs: Array<{ start: number; end: number }>;
}

/**
 * 把「带 null 的序列」拆成连续段：段内用单调三次曲线平滑连接，
 * 段与段之间（缺样本桶）只画一条虚线把它们连起来——走势连贯，但不假装那里有数据。
 * 全空序列返回两条空字符串，调用方据此跳过渲染。
 */
export function trendLinePaths(
  values: ReadonlyArray<number | null>,
  xOf: (bucket: number) => number,
  yOf: (value: number) => number,
): TrendLinePaths {
  const runs: Array<{ start: number; end: number }> = [];
  let cursor = -1;
  values.forEach((value, bucket) => {
    const valid = value !== null && Number.isFinite(value);
    if (valid && cursor < 0) cursor = bucket;
    if (!valid && cursor >= 0) {
      runs.push({ start: cursor, end: bucket - 1 });
      cursor = -1;
    }
  });
  if (cursor >= 0) runs.push({ start: cursor, end: values.length - 1 });
  const solid = runs
    .map((run) => {
      const points: TrendChartPoint[] = [];
      for (let bucket = run.start; bucket <= run.end; bucket += 1) {
        const value = values[bucket];
        if (value === null || !Number.isFinite(value)) continue;
        points.push({ x: xOf(bucket), y: yOf(value) });
      }
      return monotoneCubicPath(points);
    })
    .filter(Boolean)
    .join(" ");
  const bridges: string[] = [];
  for (let index = 1; index < runs.length; index += 1) {
    const previous = runs[index - 1];
    const next = runs[index];
    const fromValue = values[previous.end];
    const toValue = values[next.start];
    if (fromValue === null || toValue === null) continue;
    bridges.push(
      "M " +
        round2(xOf(previous.end)) +
        " " +
        round2(yOf(fromValue)) +
        " L " +
        round2(xOf(next.start)) +
        " " +
        round2(yOf(toValue)),
    );
  }
  return { solid, dashed: bridges.join(" "), runs };
}

/**
 * 面积填充路径：只填「连续有样本」的段——缺样本桶处断开，不会把两端硬连成一片涂满。
 * 每段 = 平滑顶边 + 落到基线的两条竖边 + Z 闭合；单点段无法围出面积，直接跳过。
 */
export function trendAreaPaths(
  values: ReadonlyArray<number | null>,
  xOf: (bucket: number) => number,
  yOf: (value: number) => number,
  baseline: number,
): string[] {
  const paths: string[] = [];
  const { runs } = trendLinePaths(values, xOf, yOf);
  for (const run of runs) {
    if (run.end <= run.start) continue;
    const points: TrendChartPoint[] = [];
    for (let bucket = run.start; bucket <= run.end; bucket += 1) {
      const value = values[bucket];
      if (value === null || !Number.isFinite(value)) continue;
      points.push({ x: xOf(bucket), y: yOf(value) });
    }
    if (points.length < 2) continue;
    const top = monotoneCubicPath(points);
    if (!top) continue;
    paths.push(
      top +
        " L " +
        round2(points[points.length - 1].x) +
        " " +
        round2(baseline) +
        " L " +
        round2(points[0].x) +
        " " +
        round2(baseline) +
        " Z",
    );
  }
  return paths;
}

export interface TrendLatencyMember {
  name: string;
  color: string;
  value: number | null;
  requests: number;
  /** 合并出来的「其他」条目：颜色中性、名字固定。 */
  other?: boolean;
}

/**
 * 延迟类指标不能堆叠（P95 不是加性量）：按请求数取 Top6 成员并排，
 * 其余成员用请求数加权平均合并成一条「其他 · 加权 P95」。
 */
export function trendLatencyMembers(
  members: readonly TimeseriesModelPoint[],
  colors: ReadonlyMap<string, string>,
  limit = TREND_LATENCY_TOP_MEMBERS,
): TrendLatencyMember[] {
  const usable = members
    .filter((member) => Number.isFinite(member.requests) && member.requests > 0)
    .sort((left, right) => right.requests - left.requests || left.name.localeCompare(right.name));
  const top = usable.slice(0, Math.max(0, limit)).map((member) => ({
    name: member.name,
    color: colors.get(member.name) ?? TREND_OTHER_COLOR,
    value: member.p95_total_ms,
    requests: member.requests,
  }));
  const rest = usable.slice(Math.max(0, limit));
  if (!rest.length) return top;
  const merged = weightedAverageP95(rest.map((member) => ({ value: member.p95_total_ms, requests: member.requests })));
  const requests = rest.reduce((sum, member) => sum + member.requests, 0);
  return [
    ...top,
    {
      name: TREND_LATENCY_OTHER_LABEL,
      color: TREND_OTHER_COLOR,
      value: merged > 0 ? merged : null,
      requests,
      other: true,
    },
  ];
}

/* ------------------------------ 分流图：详情卡 ------------------------------ */

/** 节点命中热区的最小高度（px）：小节点也要点得到、悬停得到。 */
export const FLOW_MIN_NODE_HEIGHT = 8;
export const FLOW_MIN_HIT_HEIGHT = 12;

export interface FlowNeighbor {
  id: string;
  label: string;
  value: number;
}

export interface FlowNodeNeighbors {
  upstream: FlowNeighbor[];
  downstream: FlowNeighbor[];
}

/**
 * 节点详情卡的「上下游前 N」：按连线流量降序取相邻节点，
 * 已隐藏的节点不参与（传入的是可见节点与可见连线）。
 */
export function flowNodeNeighbors(
  nodes: readonly DashboardFlowNode[],
  links: readonly DashboardFlowLink[],
  id: string,
  limit = 3,
): FlowNodeNeighbors {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const upstream: FlowNeighbor[] = [];
  const downstream: FlowNeighbor[] = [];
  for (const link of links) {
    if (!Number.isFinite(link.value) || link.value <= 0) continue;
    if (link.target === id) {
      const node = byId.get(link.source);
      if (node) upstream.push({ id: node.id, label: node.label, value: link.value });
    } else if (link.source === id) {
      const node = byId.get(link.target);
      if (node) downstream.push({ id: node.id, label: node.label, value: link.value });
    }
  }
  const rank = (list: FlowNeighbor[]) =>
    list
      .sort((left, right) => right.value - left.value || left.label.localeCompare(right.label))
      .slice(0, Math.max(0, limit));
  return { upstream: rank(upstream), downstream: rank(downstream) };
}

export type DashboardKpiKey =
  | "requests"
  | "prompt_tokens"
  | "completion_tokens"
  | "cached_tokens"
  | "cache_hit_rate"
  | "cost"
  | "error_rate"
  | "rpm"
  | "tpm"
  | "p95_ttft"
  | "p95_total";

/**
 * KPI 卡的语义色：与 IconTone 的子集一一对应（flow/token/cache/cost/error/latency），
 * 这里单独声明是为了让 lib 层不依赖组件库。
 */
export type KpiTone = "flow" | "token" | "cache" | "cost" | "error" | "latency";

export type DashboardKpiDetailRow = { label: string; value: string };

export type DashboardKpiDetail = {
  title: string;
  rows: DashboardKpiDetailRow[];
  note: string;
};

export type DashboardKpi = {
  key: DashboardKpiKey;
  label: string;
  value: string;
  hint: string;
  tone: KpiTone;
  /** 卡面只放一个数；完整口径与分子/分母在 hover/focus 的详情里展开。 */
  detail?: DashboardKpiDetail;
};

/**
 * 看板 KPI 卡数据，顺序即展示顺序（task-14 起为 11 张）。
 * 纯函数：样式与图标映射留在 JSX 侧，这里只负责文案与数值。
 */
export function dashboardKpis(data: DashboardSummary | undefined): DashboardKpi[] {
  const activeMinutes = data?.active_minutes ?? 0;
  const samples = data?.cache_hit_rate_samples ?? 0;
  const estimated = data?.estimated_rows ?? 0;
  const latencySamples = data?.latency?.samples ?? 0;
  const latencyHint = latencySamples < 5 ? `样本不足（${latencySamples}/5）` : "";
  // 卡面 hint 在 1440 宽五列合并卡里只有 ~100px，会被 truncate：文案短到截断后
  // 头部（加权口径 + 样本数）仍完整可读；估算明细留在下方 note 与悬停弹层里。
  const cacheHint =
    estimated > 0
      ? `token 加权 · ${samples} 样本 · ${estimated} 条估算未计入`
      : `token 加权 · ${samples} 样本`;
  // 卡面一行只放 A，hover/focus 才展开 ADR-068 的 A/B/C/D 与各自分母、样本。
  const cacheableSessions = data?.cacheable_sessions ?? 0;
  const cacheSavedUsd = data?.cache_saved_usd ?? 0;
  const cacheDetail: DashboardKpiDetail = {
    title: "缓存命中率 · A/B/C/D 口径",
    rows: [
      {
        label: "A token 加权",
        value: `${formatCacheHitRate(data?.cache_hit_rate)} · ${formatCompact(data?.cached_tokens ?? 0)} / ${formatCompact(data?.prompt_tokens ?? 0)} token`,
      },
      {
        label: "B 请求级",
        value: `${formatCacheHitRate(data?.cache_request_hit_rate)} · ${formatCount(data?.cache_request_hits ?? 0)} / ${formatCount(samples)} 条`,
      },
      {
        label: "C 可缓存占比",
        value:
          cacheableSessions > 0
            ? `${formatPercent(data?.cacheable_ratio ?? 0, 1)} · ${formatCompact(data?.cacheable_tokens ?? 0)} / ${formatCompact(data?.prompt_tokens ?? 0)} token · ${formatCount(cacheableSessions)} 会话`
            : "—（无会话头，无法计算）",
      },
      {
        label: "D 已节省",
        value: `${formatCompact(data?.cache_saved_tokens ?? 0)} token · ${cacheSavedUsd > 0 ? formatUsdFine(cacheSavedUsd) : "缓存单价未配置"}`,
      },
    ],
    note: `样本 ${samples} 条${estimated > 0 ? ` · ${estimated} 条估算未计入` : ""}。A = Σ缓存词元 ÷ Σ输入词元；B = 命中请求数 ÷ 计入请求数；C = 同会话已出现词元 ÷ 输入词元；D = Σ缓存词元及按模型单价估算的节省。`,
  };
  // 渠道模型的每百万 token 单价为 0 时，后端算出来的成本必然是 0；
  // 这种「有词元但成本为 0」应显示成未配置单价，而不是误导性的 $0.00。
  const costTokens =
    (data?.prompt_tokens ?? 0) + (data?.completion_tokens ?? 0) + (data?.cached_tokens ?? 0);
  const costText = formatCostOrUnconfigured(data?.cost_usd, costTokens);
  const costUnconfigured = costText === "未配置单价";
  return [
    { key: "requests", label: "请求数", value: formatCompact(data?.requests ?? 0), hint: "所选时间范围内", tone: "flow" },
    { key: "prompt_tokens", label: "输入词元", value: formatCompact(data?.prompt_tokens ?? 0), hint: "prompt tokens", tone: "token" },
    { key: "completion_tokens", label: "输出词元", value: formatCompact(data?.completion_tokens ?? 0), hint: "completion tokens", tone: "token" },
    { key: "cached_tokens", label: "缓存词元", value: formatCompact(data?.cached_tokens ?? 0), hint: "cached tokens", tone: "cache" },
    { key: "cache_hit_rate", label: "缓存命中率", value: formatCacheHitRate(data?.cache_hit_rate), hint: cacheHint, tone: "cache", detail: cacheDetail },
    {
      key: "cost",
      label: "成本",
      value: costText,
      hint: costUnconfigured ? "渠道模型单价未配置" : "USD",
      tone: "cost",
    },
    {
      key: "error_rate",
      label: "错误率",
      // 错误率常是个位数百分比，保留两位小数才能区分 0.1% 与 0.01%。
      value: formatPercent(data?.error_rate ?? 0, 2),
      hint: `${data?.errors ?? 0} 个错误`,
      tone: "error",
    },
    {
      key: "rpm",
      label: "RPM",
      value: formatRate(data?.rpm),
      hint: `活跃 ${activeMinutes} 分钟 · 峰值 ${data?.peak_rpm ?? 0}/分`,
      tone: "flow",
    },
    {
      key: "tpm",
      label: "TPM",
      value: formatCompact(data?.tpm ?? 0),
      hint: `跨度 ${formatSpanMinutes(data?.span_minutes)}`,
      tone: "token",
    },
    { key: "p95_ttft", label: "P95 首字", value: formatP95(data?.p95_ttft_ms), hint: latencyHint || "TTFT", tone: "latency" },
    { key: "p95_total", label: "P95 总耗时", value: formatP95(data?.p95_total_ms), hint: latencyHint || "端到端", tone: "latency" },
  ];
}

/**
 * 热力图视图模型：后端返回的是「当前时间窗口内」的稀疏 (weekday, hour) 计数，
 * 窗口外的天根本不会出现，直接按范围渲染会让整行变成灰格。页面固定用近 7 天窗口取数，
 * 这里只负责把稀疏单元摊成 7×24 矩阵与配色，方便测试覆盖 weekday 映射。
 */

/** 行标签：后端用 datetime.isoweekday()，1=周一 … 7=周日。 */
export const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"] as const;

/** 热力图列数：0–23 时。 */
export const HEATMAP_HOURS = 24;

export type HeatmapCell = { weekday: number; hour: number; requests: number };

/** 稀疏单元 → 7 行（周一→周日）× 24 列（0–23 时）矩阵；越界的脏数据忽略而不是抛错。 */
export function heatmapGrid(cells: ReadonlyArray<HeatmapCell>): number[][] {
  const grid = Array.from({ length: 7 }, () => new Array<number>(HEATMAP_HOURS).fill(0));
  for (const cell of cells) {
    if (!Number.isInteger(cell.weekday) || cell.weekday < 1 || cell.weekday > 7) continue;
    if (!Number.isInteger(cell.hour) || cell.hour < 0 || cell.hour >= HEATMAP_HOURS) continue;
    grid[cell.weekday - 1][cell.hour] += cell.requests;
  }
  return grid;
}

/** 单格透明度：0 次压到 0.04 当底纹，有数据从 0.15 起按该窗口峰值线性加深，最深 1。 */
export function heatmapOpacity(value: number, max: number): number {
  if (!(value > 0)) return 0.04;
  const peak = max > 0 ? max : value;
  return Math.min(1, 0.15 + (value / peak) * 0.85);
}

/**
 * 看板自动刷新：只活在组件内存里，不写 localStorage（存储键由 Lead 统一治理）。
 * 默认 1 分钟——看板不是秒级监控，15 秒会反复扫一遍本地 SQLite；挂机场景可选 30 分钟。
 */
export const REFRESH_INTERVAL_OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 15_000, label: "15 秒" },
  { value: 30_000, label: "30 秒" },
  { value: 60_000, label: "1 分钟" },
  { value: 300_000, label: "5 分钟" },
  { value: 600_000, label: "10 分钟" },
  { value: 1_800_000, label: "30 分钟" },
];

export const DEFAULT_REFRESH_INTERVAL_MS = 60_000;

export function refreshIntervalLabel(intervalMs: number): string {
  const option = REFRESH_INTERVAL_OPTIONS.find((item) => item.value === intervalMs);
  return option ? `每 ${option.label}` : "已暂停";
}

/**
 * 定时器只在 intervalMs 变化时重建；回调走 ref 保存最新闭包，
 * 避免因依赖数组带回调而每个渲染都重排一次定时器。
 */
export function useAutoRefresh(refresh: () => void, intervalMs: number): void {
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  }, [refresh]);
  useEffect(() => {
    if (!Number.isFinite(intervalMs) || intervalMs <= 0) return;
    const timer = setInterval(() => latest.current(), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
}

