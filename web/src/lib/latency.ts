import { formatDuration } from "@/lib/duration";

export type LatencyKind = "ttft" | "ttfa" | "ttlt" | "total";

export type LatencyTone = "good" | "warn" | "bad" | "muted";

export interface LatencyThreshold {
  good: number;
  warn: number;
}

/**
 * Fixed thresholds approved for the log table. We deliberately do not use
 * window-relative percentiles: with a handful of rows they are noise, and with
 * millions of rows they are an avoidable per-request cost.
 */
export const FIXED_THRESHOLDS: Record<LatencyKind, LatencyThreshold> = {
  ttft: { good: 10_000, warn: 30_000 },
  ttfa: { good: 10_000, warn: 30_000 },
  ttlt: { good: 30_000, warn: 60_000 },
  total: { good: 30_000, warn: 60_000 },
};

export const LATENCY_LABEL: Record<LatencyKind, string> = {
  ttft: "首字",
  ttfa: "首答",
  ttlt: "尾字",
  total: "总耗时",
};

export const LATENCY_DEFINITION: Record<LatencyKind, string> = {
  ttft: "首个有意义的输出，包含思考/推理和工具调用",
  ttfa: "首个可见正文，不包含思考/推理",
  ttlt: "最后一个输出令牌到达",
  total: "请求开始到流结束",
};

export function latencyTone(ms: number | null | undefined, kind: LatencyKind): LatencyTone {
  if (ms == null || !Number.isFinite(ms)) return "muted";
  const { good, warn } = FIXED_THRESHOLDS[kind];
  if (ms <= good) return "good";
  if (ms <= warn) return "warn";
  return "bad";
}

export function latencyTextClass(tone: LatencyTone): string {
  switch (tone) {
    case "good":
      return "text-success-text";
    case "warn":
      return "text-warning-text";
    case "bad":
      return "text-danger-text";
    default:
      return "text-muted-foreground";
  }
}

export function latencyBarClass(tone: LatencyTone): string {
  switch (tone) {
    case "good":
      return "bg-success";
    case "warn":
      return "bg-warning";
    case "bad":
      return "bg-danger";
    default:
      return "bg-faint";
  }
}

export function formatLatency(ms: number | null | undefined): string {
  return formatDuration(ms);
}

export function latencyDescription(ms: number | null | undefined, kind: LatencyKind): string {
  if (ms == null || !Number.isFinite(ms)) return `${LATENCY_LABEL[kind]}无数据`;
  const tone = latencyTone(ms, kind);
  const toneLabel = tone === "good" ? "正常" : tone === "warn" ? "偏慢" : tone === "bad" ? "慢" : "无数据";
  return `${LATENCY_LABEL[kind]} ${formatLatency(ms)}，${toneLabel}`;
}
