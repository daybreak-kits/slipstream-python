import { apiRequest } from "@/lib/api";
import { formatDuration } from "@/lib/duration";
import {
  formatCacheHitRate,
  formatCompact,
  formatCostOrUnconfigured,
  formatRate,
  formatRequestCost,
  formatSpanMinutes,
} from "@/lib/format";
import { TIME_RANGE_OPTIONS, type TimeRange } from "@/lib/time-range";

export interface LogAttempt {
  channel_model_id?: string | null;
  channel_slug?: string;
  channel_name?: string;
  upstream_model?: string;
  status?: "success" | "error" | "skipped" | "stopped" | string;
  error_code?: string | null;
  message?: string;
  ms?: number;
  round?: number;
  skipped?: boolean;
  priority_path?: number[];
}

/**
 * 优先级路径展示：按数值从大到小拼接（数值越大越先调用），
 * 让日志里的层序与实际调用方向一致。
 */
export function formatPriorityPath(path: readonly number[]): string {
  return [...path].sort((left, right) => right - left).join(" → ");
}

/**
 * 用户可见的 attempt 状态标签表。后端 status 是机器枚举，界面不得直接渲染英文原值。
 */
export const ATTEMPT_STATUS_LABEL: Record<string, string> = {
  success: "成功",
  error: "失败",
  skipped: "跳过",
  stopped: "已停止",
};

/** attempt 状态 → 中文标签；空值与未知枚举一律显示「未知」。 */
export function attemptStatusLabel(status: string | null | undefined): string {
  const key = (status ?? "").trim();
  if (!key) return "未知";
  return ATTEMPT_STATUS_LABEL[key] ?? "未知";
}

/** 响应改写跳过原因标签表（后端取值：stream / error）。 */
export const REWRITE_SKIPPED_LABEL: Record<string, string> = {
  stream: "流式响应跳过响应体改写",
  error: "改写执行失败，已保留原始响应",
};

/** 改写跳过原因 → 中文；空值返回空串（表示未跳过），未知枚举返回「未知原因」。 */
export function rewriteSkippedLabel(value: string | null | undefined): string {
  const key = (value ?? "").trim();
  if (!key) return "";
  return REWRITE_SKIPPED_LABEL[key] ?? "未知原因";
}

/**
 * 用户可见的错误码标签表。后端 error_code 是机器枚举
 * （l01_core/errors.py 的 ErrorCode 与 l05_service/routing.py 的跳过原因），界面不得直接渲染英文原值。
 */
export const ERROR_CODE_LABEL: Record<string, string> = {
  // l01_core/errors.py :: ErrorCode
  INVALID_REQUEST: "请求参数无效",
  UNAUTHORIZED: "未通过身份认证",
  FORBIDDEN: "没有访问权限",
  NOT_FOUND: "资源不存在",
  CONFLICT: "资源冲突",
  MODEL_NOT_FOUND: "模型不存在",
  MODEL_SCHEDULED_OFFLINE: "模型处于排期冷却窗口",
  NO_AVAILABLE_CHANNEL: "没有可用渠道",
  SERVICE_UNAVAILABLE: "服务不可用",
  UPSTREAM_TIMEOUT: "上游超时",
  UPSTREAM_ERROR: "上游返回错误",
  UPSTREAM_RATE_LIMITED: "上游触发限流",
  EMPTY_RESPONSE: "上游返回空响应",
  STREAM_ABORTED: "流式响应中断",
  STREAM_STOPPED: "流式响应已停止",
  NOT_IMPLEMENTED: "功能未实现",
  INVALID_API_KEY: "密钥无效",
  BAD_UPSTREAM_RESPONSE: "上游响应格式异常",
  INTERNAL: "服务内部错误",
  // l05_service/routing.py 的跳过原因（MODEL_BANNED / MODEL_COOLDOWN / RATE_LIMIT_COOLDOWN / COOLDOWN_WINDOW）
  MEMBER_DISABLED: "组合成员已封禁",
  CHANNEL_DISABLED: "渠道已封禁",
  MODEL_BANNED: "渠道模型已封禁",
  MODEL_COOLDOWN: "渠道模型处于冷却中",
  PROTOCOL_UNSUPPORTED: "渠道协议不是 OpenAI 兼容协议",
  CIRCUIT_OPEN: "渠道模型处于冷却中",
  CIRCUIT_FAULT: "渠道模型处于冷却升级期",
  COOLDOWN_WINDOW: "渠道模型处于排期冷却窗口",
  RATE_LIMIT_COOLDOWN: "渠道模型处于限流冷却（按上游 Retry-After 退避）",
  // l05_service/probe.py 的探测结果
  PROBE_TIMEOUT: "探测超时",
  PROBE_ERROR: "探测失败",
};

/** 错误码 → 中文标签；空值返回空串，未知枚举返回「未知错误」（原始码由调用方放在 title 里备查）。 */
export function errorCodeLabel(code: string | null | undefined): string {
  const key = (code ?? "").trim();
  if (!key) return "";
  return ERROR_CODE_LABEL[key] ?? "未知错误";
}

/** attempt 的错误文案：后端已给中文说明时不再重复拼错误码。 */
export function attemptErrorText(attempt: Pick<LogAttempt, "error_code" | "message">): string {
  const message = (attempt.message ?? "").trim();
  const label = errorCodeLabel(attempt.error_code);
  if (!label) return message;
  if (!message) return label;
  return message.includes(label) ? message : `${label} · ${message}`;
}

export interface RequestLog {
  id: string;
  request_id: string;
  upstream_request_id: string;
  user_id: string | null;
  user_name: string;
  api_key_id: string | null;
  api_key_label: string;
  requested_model: string;
  channel_name: string;
  upstream_model: string;
  response_model: string;
  /** Request source: "chat" (default) or "probe" (active health check). */
  source?: string;
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
  attempts: LogAttempt[];
  /** 后端聚合的失败尝试数；旧响应可能缺失，缺失时回退解析 attempts。 */
  attempt_error_count?: number;
  rewrite_applied: boolean;
  rewrite_skipped: string;
  rewrite_diff: Record<string, unknown>;
  params: Record<string, unknown>;
  response_headers: Record<string, string>;
  error_body: string | null;
  created_at: number;
}

export type LogView = "requests" | "combo" | "status";

/** 视图切换控件的唯一数据源：默认逐次请求，另外两种是同一批请求的聚合视角。 */
export const LOG_VIEW_OPTIONS: ReadonlyArray<{ value: LogView; label: string }> = [
  { value: "requests", label: "逐次请求" },
  { value: "combo", label: "按组合" },
  { value: "status", label: "按状态" },
];

/** URL 里的 view 参数解析；空值与非法值一律回落到默认的逐次请求。 */
export function parseLogView(value: string | null): LogView {
  return value === "combo" || value === "status" ? value : "requests";
}

export function isAggregateLogView(view: LogView): boolean {
  return view !== "requests";
}

/**
 * 聚合视图的一行。combo 的 key 是组合名，status 的 key 是后端给的段标识；
 * channels 是段内涉及的渠道（前端只展示前 3 个）。
 */
export interface LogGroup {
  key: string;
  label: string;
  status_code: number | null;
  status_label: string;
  first_at: number;
  last_at: number;
  requests: number;
  attempts: number;
  errors: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cache_ratio: number;
  cost_usd: number;
  ttft_p50_ms: number | null;
  total_p50_ms: number | null;
  total_p95_ms: number | null;
  channels: string[];
  error_codes: string[];
  sample_request_ids: string[];
}

export interface RequestLogList {
  items: RequestLog[];
  total: number;
  page: number;
  page_size: number;
  /** 聚合视图才有；requests 视图为空。 */
  groups?: LogGroup[];
  view?: LogView;
  /** 聚合扫描到 5 万条上限时为 true。 */
  scan_limited?: boolean;
}

export function isErrorGroup(
  group: Pick<LogGroup, "status_code" | "errors" | "error_codes">,
): boolean {
  if (group.errors > 0 || group.error_codes.length > 0) return true;
  return group.status_code !== null && group.status_code >= 400;
}

/** 状态码徽标文本：有 HTTP 状态码优先显示数字，网络错误显示后端给的伪状态标签。 */
export function groupStatusText(group: Pick<LogGroup, "status_code" | "status_label">): string {
  if (group.status_code !== null) return String(group.status_code);
  return group.status_label || "—";
}

/** status 视图的模型列副标题：段内涉及的渠道 top3；没有渠道说明请求未路由。 */
export function groupChannelSummary(group: Pick<LogGroup, "channels">): string {
  const channels = group.channels.filter(Boolean).slice(0, 3);
  return channels.length > 0 ? channels.join(" · ") : "未路由";
}

export interface LogUserOption {
  id: string;
  name: string;
}

export interface LogFilterOptions {
  channels: string[];
  models: string[];
  key_labels: string[];
  users: LogUserOption[];
  truncated: Record<string, boolean>;
}

export type LogStatusFilter = "" | "success" | "error";
export type LogStreamFilter = "" | "stream" | "json";
/** 按「请求内部是否发生过失败尝试」筛选；当前界面只暴露失败，故只允许 error。 */
export type LogAttemptStatusFilter = "" | "error";

export interface LogFilters {
  page?: number;
  page_size?: number;
  q?: string;
  channel?: string;
  model?: string;
  status?: LogStatusFilter;
  attempt_status?: LogAttemptStatusFilter;
  user?: string;
  key_label?: string;
  stream?: LogStreamFilter;
  start_ms?: number;
  end_ms?: number;
  view?: LogView;
  /** 聚合视图下钻时强制某个组；只与 view=requests 组合使用。 */
  group_key?: string;
}

/** URL query 里的筛选状态；默认状态不会写进地址栏。 */
export interface LogQueryState {
  q: string;
  range: TimeRange;
  customStart: string;
  customEnd: string;
  channel: string;
  model: string;
  status: LogStatusFilter;
  attemptStatus: LogAttemptStatusFilter;
  user: string;
  keyLabel: string;
  stream: LogStreamFilter;
  view: LogView;
  page: number;
  pageSize: number;
}

export const DEFAULT_LOG_RANGE: TimeRange = "today";
export const DEFAULT_LOG_PAGE_SIZE = 25;

/** 从 URL query 解析已生效筛选；非法值回落到默认值。 */
export function parseAppliedQuery(params: URLSearchParams): LogQueryState {
  const rawRange = params.get("range");
  const range = TIME_RANGE_OPTIONS.some((option) => option.value === rawRange)
    ? (rawRange as TimeRange)
    : DEFAULT_LOG_RANGE;
  const rawStatus = params.get("status");
  const rawAttemptStatus = params.get("attempt_status");
  const rawStream = params.get("stream");
  const rawPage = Number(params.get("page"));
  const rawPageSize = Number(params.get("page_size"));
  return {
    q: params.get("q") ?? "",
    range,
    customStart: params.get("start") ?? "",
    customEnd: params.get("end") ?? "",
    channel: params.get("channel") ?? "",
    model: params.get("model") ?? "",
    status: rawStatus === "success" || rawStatus === "error" ? rawStatus : "",
    attemptStatus: rawAttemptStatus === "error" ? "error" : "",
    user: params.get("user") ?? "",
    keyLabel: params.get("key") ?? "",
    stream: rawStream === "stream" || rawStream === "json" ? rawStream : "",
    view: parseLogView(params.get("view")),
    page: Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1,
    pageSize: Number.isInteger(rawPageSize) && rawPageSize > 0 ? rawPageSize : DEFAULT_LOG_PAGE_SIZE,
  };
}

/** 默认状态不写脏参数：地址栏只保留用户真正改过的条件。 */
export function logQueryToParams(state: LogQueryState): URLSearchParams {
  const params = new URLSearchParams();
  const q = state.q.trim();
  if (q) params.set("q", q);
  if (state.range !== DEFAULT_LOG_RANGE) params.set("range", state.range);
  if (state.range === "custom") {
    if (state.customStart) params.set("start", state.customStart);
    if (state.customEnd) params.set("end", state.customEnd);
  }
  if (state.channel) params.set("channel", state.channel);
  if (state.model) params.set("model", state.model);
  if (state.status) params.set("status", state.status);
  if (state.attemptStatus) params.set("attempt_status", state.attemptStatus);
  if (state.user) params.set("user", state.user);
  if (state.keyLabel) params.set("key", state.keyLabel);
  if (state.stream) params.set("stream", state.stream);
  if (state.view !== "requests") params.set("view", state.view);
  if (state.page > 1) params.set("page", String(state.page));
  if (state.pageSize !== DEFAULT_LOG_PAGE_SIZE) params.set("page_size", String(state.pageSize));
  return params;
}

export function buildLogQuery(filters: LogFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  return params.toString();
}

export async function listLogs(filters: LogFilters = {}): Promise<RequestLogList> {
  const query = buildLogQuery(filters);
  return apiRequest<RequestLogList>(`/api/logs${query ? `?${query}` : ""}`);
}

export async function getLog(requestId: string): Promise<RequestLog> {
  return apiRequest<RequestLog>(`/api/logs/${encodeURIComponent(requestId)}`);
}

export async function getLogFilterOptions(q = ""): Promise<LogFilterOptions> {
  const query = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  return apiRequest<LogFilterOptions>(`/api/logs/filters${query}`);
}

// Pure display helpers. Ratios and throughput are computed by the backend;
// these functions only format the values for the UI.
/** GET /api/logs/summary 的响应；统计口径全部由后端计算。 */
export interface LogSummary {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  cache_hit_rate: number;
  cache_hit_rate_samples: number;
  active_minutes: number;
  span_minutes: number;
  rpm: number;
  tpm: number;
  peak_rpm: number;
  estimated_rows: number;
}

export type LogMetricKey = "usage" | "cost" | "rpm" | "tpm" | "cache";

export interface LogMetric {
  key: LogMetricKey;
  label: string;
  value: string;
  hint: string;
  title?: string;
  estimated?: boolean;
  estimatedTitle?: string;
}

// 展示层格式化器统一收拢在 @/lib/format；这里只做转出，页面/抽屉保持旧导入路径。
export {
  formatCacheHitRate,
  formatCachePercent,
  formatCompact,
  formatRate,
  formatSpanMinutes,
  formatThroughput,
  formatUsdCompact,
  formatUsdFine,
} from "@/lib/format";

/** 度量条五项的口径全部在这里收拢，页面只负责配图标与颜色。 */
export function buildLogMetrics(summary: LogSummary | undefined): LogMetric[] {
  const estimatedRows = summary?.estimated_rows ?? 0;
  const estimatedTitle = estimatedRows > 0 ? `其中 ${estimatedRows} 行无上游 usage，未计入` : undefined;
  // Every field below is part of the summary contract (ssot.md §6), but the KPI
  // strip is the first thing a half-migrated fixture renders, so a missing field
  // must degrade to 0 instead of printing "undefined" at the user.
  const activeMinutes = summary?.active_minutes ?? 0;
  const cacheSamples = summary?.cache_hit_rate_samples ?? 0;
  const activeHint = summary ? `活跃 ${activeMinutes} 分钟` : "—";
  const span = summary ? formatSpanMinutes(summary.span_minutes) : "0 分钟";
  const usage = summary ? summary.prompt_tokens + summary.completion_tokens : 0;
  return [
    {
      key: "usage",
      label: "用量",
      value: summary ? formatCompact(usage) : "—",
      hint: summary
        ? `输入 ${formatCompact(summary.prompt_tokens)} · 输出 ${formatCompact(summary.completion_tokens)}`
        : "—",
      estimated: Boolean(estimatedTitle),
      estimatedTitle,
    },
    {
      key: "cost",
      label: "开销",
      value: summary
        ? formatCostOrUnconfigured(
            summary.cost_usd,
            summary.prompt_tokens + summary.completion_tokens + summary.cached_tokens,
          )
        : "—",
      hint: summary ? `${formatCompact(summary.requests)} 次请求` : "—",
    },
    {
      key: "rpm",
      label: "RPM",
      value: summary ? formatRate(summary.rpm) : "—",
      hint: activeHint,
      title: summary
        ? `活跃口径：${summary.requests} 次 ÷ ${summary.active_minutes} 分钟 = ${formatRate(summary.rpm)}；全窗口跨度 ${span}；峰值 ${summary.peak_rpm} 次/分钟`
        : undefined,
    },
    {
      key: "tpm",
      label: "TPM",
      value: summary ? formatCompact(summary.tpm) : "—",
      hint: activeHint,
      estimated: Boolean(estimatedTitle),
      estimatedTitle,
      title: summary
        ? `活跃口径：${formatCompact(summary.tpm)} 词元/分钟；全窗口跨度 ${span}`
        : undefined,
    },
    {
      key: "cache",
      label: "缓存命中率",
      value: summary ? formatCacheHitRate(summary.cache_hit_rate) : "—",
      hint: summary ? `${cacheSamples} 条样本` : "—",
      title: summary
        ? estimatedRows > 0
          ? `${summary.cache_hit_rate_samples} 条样本计入命中率；${estimatedRows} 条估算行未计入`
          : `${summary.cache_hit_rate_samples} 条样本计入命中率`
        : undefined,
    },
  ];
}
/** Shown when a streamed request produced a first token but no answer (text or tool call). */
export const TTFA_NO_CONTENT_LABEL = "无正文与工具调用（仅推理）";

/** True when a stream has TTFT but no TTFA: thinking-only (no text, no tool call). */
export function ttfaMissing(log: Pick<RequestLog, "ttfa_ms" | "ttft_ms">): boolean {
  return (
    (log.ttfa_ms === null || log.ttfa_ms === undefined) &&
    log.ttft_ms !== null &&
    log.ttft_ms !== undefined
  );
}

/** Log source label: probe requests come from active health checks. */
export function sourceLabel(source?: string): string {
  return source === "probe" ? "探测" : "聊天";
}

export function formatMs(value: number | null | undefined): string {
  // 0 表示上游没有回报耗时，不能显示成 0.00s。
  if (value === 0) return "—";
  return formatDuration(value);
}

export function statusLabel(log: Pick<RequestLog, "http_status" | "error_code">): string {
  if (log.http_status >= 400) return errorCodeLabel(log.error_code) || `HTTP ${log.http_status}`;
  return `HTTP ${log.http_status}`;
}

export function isErrorLog(log: Pick<RequestLog, "http_status">): boolean {
  return log.http_status >= 400;
}

/**
 * 费用位的单一口径：词元合计（输入 + 输出 + 缓存）与失败判定都收拢在这里，
 * 请求行、聚合组与详情抽屉共用。失败行的费用没有计费数据可言，
 * 显示「—」不渲染 $0.00（ADR-082 同族口径）；请求行按 http_status 判失败，
 * 聚合组按 errors / error_codes 判失败。
 */
export function requestCostLabel(
  log:
    | Pick<RequestLog, "cost_usd" | "prompt_tokens" | "completion_tokens" | "cached_tokens" | "http_status">
    | Pick<LogGroup, "cost_usd" | "prompt_tokens" | "completion_tokens" | "cached_tokens" | "status_code" | "errors" | "error_codes">,
): string {
  const tokens = log.prompt_tokens + log.completion_tokens + log.cached_tokens;
  return "http_status" in log
    ? formatRequestCost(log.cost_usd, tokens, isErrorLog(log))
    : formatRequestCost(log.cost_usd, tokens, isErrorGroup(log));
}

/** 请求内部的失败尝试（不含 stopped/skipped）。 */
export function failedAttempts(log: Pick<RequestLog, "attempts">): LogAttempt[] {
  return (log.attempts ?? []).filter((attempt) => attempt.status === "error");
}

/**
 * 请求内部的失败尝试数：优先用后端聚合值 attempt_error_count，
 * 旧响应没有该字段时回退解析 attempts（换渠道成功后的行 http_status 仍是 200）。
 */
export function attemptErrorCount(
  log: Pick<RequestLog, "attempts" | "attempt_error_count">,
): number {
  const aggregated = log.attempt_error_count;
  if (typeof aggregated === "number" && Number.isFinite(aggregated) && aggregated >= 0) {
    return Math.floor(aggregated);
  }
  return failedAttempts(log).length;
}

/** 整体成功但内部发生过失败尝试（例如换渠道重试成功）。 */
export function hasFailedAttempts(log: Pick<RequestLog, "attempts" | "attempt_error_count">): boolean {
  return attemptErrorCount(log) > 0;
}
