import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  DatabaseZap,
  DollarSign,
  Eye,
  EyeOff,
  Gauge,
  GitFork,
  Hash,

  RefreshCw,
  RotateCcw,
  ScrollText,
  Search,
  SlidersHorizontal,
  X,
  type LucideIcon,
} from "lucide-react";
import { Spinner } from "@/components/ui/spinner";

import { type IconTone } from "@/components/ui/icon-badge";
import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { LogDetailDrawer } from "@/components/logs/log-detail-drawer";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { MetricStrip, type MetricPillItem } from "@/components/ui/metric-pills";
import { Pagination } from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TimeRangePicker } from "@/components/ui/time-range-picker";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useOverflow } from "@/components/ui/use-overflow";
import { apiRequest } from "@/lib/api";
import { describeApiError } from "@/lib/channels";
import {
  attemptStatusLabel,
  attemptErrorText,
  attemptErrorCount,
  errorCodeLabel,
  buildLogMetrics,
  DEFAULT_LOG_PAGE_SIZE,
  DEFAULT_LOG_RANGE,
  buildLogQuery,
  formatCachePercent,
  formatThroughput,
  formatPriorityPath,
  getLogFilterOptions,
  groupChannelSummary,
  groupStatusText,
  isAggregateLogView,
  isErrorGroup,
  isErrorLog,
  LOG_VIEW_OPTIONS,
  logQueryToParams,
  parseAppliedQuery,
  listLogs,
  rewriteSkippedLabel,
  sourceLabel,
  statusLabel,
  TTFA_NO_CONTENT_LABEL,
  ttfaMissing,
  type LogAttempt,
  type LogAttemptStatusFilter,
  type LogFilterOptions,
  type LogFilters,
  type LogGroup,
  type LogMetric,
  type LogQueryState,
  type LogStatusFilter,
  type LogStreamFilter,
  type LogSummary,
  type LogView,
  type RequestLog,
} from "@/lib/logs";
import { formatCompact, formatCostOrUnconfigured, formatCount, formatPercent } from "@/lib/format";
import {
  formatLatency,
  latencyBarClass,
  latencyTextClass,
  latencyDescription,
  latencyTone,
  type LatencyKind,
} from "@/lib/latency";
import { formatDateTimeParts, TIMEZONE_LABEL, useTimezone } from "@/lib/time";
import { formatInterval, rangeBounds, timeRangeLabel, type TimeRange } from "@/lib/time-range";
import { formatDuration } from "@/lib/duration";
import { SELECT_ALL_VALUE } from "@/lib/select-sentinels";
import { cn } from "@/lib/utils";
import { readValue, writeValue } from "@/lib/storage";

const SELECT_CLASS =
  "h-9 w-full rounded-control border border-border-strong bg-surface px-3 text-xs text-title focus-visible:border-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg";

// 草稿与已生效条件不一致的控件描边提醒；数据源仍是 URL，提交权归「搜索」。
const DIRTY_FIELD_CLASS = "filter-dirty border-primary/70 bg-primary-soft/40";

const MASKED_VALUE = "•••";

const METRIC_VISUALS: Record<LogMetric["key"], { icon: LucideIcon; tone: IconTone }> = {
  usage: { icon: Hash, tone: "token" },
  cost: { icon: DollarSign, tone: "cost" },
  rpm: { icon: Activity, tone: "flow" },
  tpm: { icon: Gauge, tone: "latency" },
  cache: { icon: DatabaseZap, tone: "cache" },
};

function readHideSensitive(): boolean {
  try {
    return readValue<boolean>("logsHideSensitive") === true;
  } catch {
    return false;
  }
}

function maskValue(value: string, hidden: boolean): string {
  return hidden ? MASKED_VALUE : value;
}

function StatusBadge({ log }: { log: Pick<RequestLog, "http_status" | "error_code"> }) {
  const failed = isErrorLog(log);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-badge px-1.5 py-0.5 text-2xs font-medium",
        failed ? "bg-danger-soft text-danger-text" : "bg-success-soft text-success-text",
      )}
      title={statusLabel(log)}
    >
      <span
        className={cn("h-1.5 w-1.5 rounded-full", failed ? "bg-danger" : "bg-success")}
        aria-hidden="true"
      />
      {log.http_status}
    </span>
  );
}

function FailedAttemptsBadge({
  log,
}: {
  log: Pick<RequestLog, "attempts" | "attempt_error_count" | "http_status">;
}) {
  // 整体失败的行由状态徽标表达，这里只标注「整体成功但内部失败过」的情况。
  const count = attemptErrorCount(log);
  if (count <= 0 || isErrorLog(log)) return null;
  return (
    <span
      className="inline-flex items-center gap-1 rounded-badge bg-warning-soft px-1.5 py-0.5 text-2xs font-medium text-warning-text"
      title={`整体请求成功，但路由过程中有 ${count} 次失败尝试（随后换渠道或重试成功）`}
    >
      <AlertTriangle className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
      含 {count} 次失败尝试
    </span>
  );
}

function attemptStatusClass(status: LogAttempt["status"]): string {
  if (status === "success") return "bg-success-soft text-success-text";
  if (status === "skipped" || status === "stopped") return "bg-warning-soft text-warning-text";
  return "bg-danger-soft text-danger-text";
}

/** 失败/跳过尝试折成一行摘要时，耗时位置显示「未发出请求」而不是空白。 */
function attemptTimingText(attempt: LogAttempt): string {
  if (attempt.skipped || attempt.status === "skipped") return "未发出请求";
  return formatDuration(attempt.ms);
}

function attemptPriorityText(attempt: LogAttempt): string {
  return attempt.priority_path && attempt.priority_path.length > 0
    ? formatPriorityPath(attempt.priority_path)
    : "—";
}

function attemptReasonText(attempt: LogAttempt): string {
  if (attempt.error_code || attempt.message) return attemptErrorText(attempt);
  if (attempt.skipped || attempt.status === "skipped") return "跳过";
  if (attempt.status === "error") return "失败";
  if (attempt.status === "stopped") return "已停止";
  return "成功";
}

/**
 * 单次尝试。失败/跳过默认折叠成一行摘要（渠道 · 模型 · 原因 · 优先级 · 耗时），
 * 展开后显示状态、错误码与完整错误信息；成功尝试保持展开。
 */
function AttemptItem({ attempt, index }: { attempt: LogAttempt; index: number }) {
  const collapsible =
    attempt.skipped === true ||
    attempt.status === "error" ||
    attempt.status === "stopped" ||
    attempt.status === "skipped";
  const [open, setOpen] = useState(!collapsible);
  const detailId = useId();
  const channel = attempt.channel_name || attempt.channel_slug || "未知渠道";
  const model = attempt.upstream_model || "—";
  const round = attempt.round ?? 1;
  const priority = attemptPriorityText(attempt);
  const timing = attemptTimingText(attempt);
  const reason = attemptReasonText(attempt);
  const skipped = attempt.skipped === true || attempt.status === "skipped";
  const reasonClass = skipped ? "text-warning-text" : "text-danger-text";
  const toggle = () => setOpen((value) => !value);

  if (!collapsible) {
    return (
      <li className="rounded-control border border-border bg-surface-2 px-2.5 py-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="font-medium text-title">
            {index + 1}. 第 {round} 轮
          </span>
          <ChevronRight className="h-3 w-3 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          <span className="min-w-0 truncate text-title">
            {channel} / {model}
          </span>
          <span className={cn("rounded-badge px-1.5 py-0.5 text-2xs", attemptStatusClass(attempt.status))}>
            {attemptStatusLabel(attempt.status)}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
          <span>
            优先级 {priority} · {timing}
          </span>
          {attempt.error_code || attempt.message ? (
            <span className={cn("min-w-0 truncate", reasonClass)} title={attempt.error_code ?? undefined}>
              {attemptErrorText(attempt)}
            </span>
          ) : null}
        </div>
      </li>
    );
  }

  return (
    <li className="cursor-pointer rounded-control border border-border bg-surface-2 px-2.5 py-2" onClick={toggle}>
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        aria-expanded={open}
        aria-controls={detailId}
        aria-label={`${open ? "收起" : "展开"}第 ${round} 轮失败尝试详情`}
        onClick={(event) => {
          event.stopPropagation();
          toggle();
        }}
      >
        {open ? (
          <>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <span className="font-medium text-title">
                {index + 1}. 第 {round} 轮
              </span>
              <ChevronRight className="h-3 w-3 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
              <span className="min-w-0 truncate text-title">
                {channel} / {model}
              </span>
              <span className={cn("rounded-badge px-1.5 py-0.5 text-2xs", attemptStatusClass(attempt.status))}>
                {attemptStatusLabel(attempt.status)}
              </span>
            </div>
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          </>
        ) : (
          <>
            <span
              className="min-w-0 flex-1 truncate text-2xs text-title"
              title={`第 ${round} 轮 · ${channel} · ${model} · ${reason} · 优先级 ${priority} · ${timing}`}
            >
              {channel} · {model} · <span className={reasonClass}>{reason}</span> · 优先级 {priority} · {timing}
            </span>
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          </>
        )}
      </button>
      <div id={detailId} hidden={!open}>
        {open ? (
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
            <span>
              优先级 {priority} · {timing}
            </span>
            {attempt.error_code || attempt.message ? (
              <span className={cn("min-w-0 truncate", reasonClass)} title={attempt.error_code ?? undefined}>
                {attemptErrorText(attempt)}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

function RoutePopover({ log, onClose }: { log: RequestLog; onClose: () => void }) {
  const attempts = [...log.attempts].sort((a, b) => (a.round ?? 1) - (b.round ?? 1));
  return (
    <div
      data-testid="routing-path"
      role="dialog"
      aria-label="路由链"
      className="absolute left-0 top-[calc(100%+6px)] z-30 w-90 max-w-[80vw] animate-fade-in rounded-card border border-border bg-surface p-3 shadow-pop"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-2xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">路由链</p>
          <p className="mt-1 truncate text-xs font-medium text-title" title={log.requested_model}>
            {log.requested_model || "—"}
            {log.response_model ? (
              <span className="font-normal text-muted-foreground"> → {log.response_model}</span>
            ) : null}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-title"
          aria-label="关闭路由链"
        >
          <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        </button>
      </div>

      {attempts.length === 0 ? (
        <p className="mt-3 rounded-control bg-surface-2 px-3 py-2 text-xs text-muted-foreground">
          单次直接调用 · {log.channel_name || "—"} / {log.upstream_model || "—"}
        </p>
      ) : (
        <ol className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
          {attempts.map((attempt, index) => (
            <AttemptItem
              key={`${attempt.channel_model_id ?? "attempt"}-${index}`}
              attempt={attempt}
              index={index}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

function LatencyValue({
  label,
  value,
  kind,
  emptyHint,
}: {
  label: string;
  value: number | null | undefined;
  kind: LatencyKind;
  emptyHint?: string;
}) {
  const showEmptyHint = value == null && Boolean(emptyHint);
  // 总耗时 0 表示上游没有回报，不能显示成 0.00s。
  const missingTotal = kind === "total" && (value === null || value === undefined || value === 0);
  return (
    <span
      className="flex items-baseline gap-1.5 whitespace-nowrap"
      title={
        showEmptyHint
          ? emptyHint
          : missingTotal
            ? "总耗时无数据"
            : value == null
              ? `${label}无数据`
              : `${label} ${formatDuration(value)}`
      }
      aria-label={showEmptyHint ? emptyHint : missingTotal ? "总耗时无数据" : latencyDescription(value, kind)}
    >
      <span className="w-7 shrink-0 text-2xs text-muted-foreground">{label}</span>
      {showEmptyHint ? (
        <span className="text-2xs text-muted-foreground">仅推理</span>
      ) : missingTotal ? (
        <span className="text-2xs text-muted-foreground" title="总耗时无数据">—</span>
      ) : (
        <span className={cn("tabular-nums text-xs font-medium", latencyTextClass(latencyTone(value, kind)))}>
          {formatLatency(value)}
        </span>
      )}
    </span>
  );
}

function LatencyCell({ log }: { log: RequestLog }) {
  // newapi-style full-height bar: each metric contributes an equal segment,
  // so one slow phase is visible without turning the whole column red.
  const toneFor = (value: number | null | undefined, kind: LatencyKind) =>
    isErrorLog(log) ? "bad" : latencyTone(value, kind);
  const tones = [
    toneFor(log.ttft_ms, "ttft"),
    toneFor(log.ttfa_ms, "ttfa"),
    toneFor(log.total_ms, "total"),
  ];
  return (
    <div className="flex items-stretch gap-2.5">
      <span
        className="flex w-1 shrink-0 flex-col overflow-hidden rounded-full"
        aria-hidden="true"
      >
        {tones.map((tone, index) => (
          <span key={index} className={cn("flex-1", latencyBarClass(tone))} />
        ))}
      </span>
      <div className="flex min-w-0 flex-col justify-center gap-0.5 text-2xs leading-[1.15]">
        <LatencyValue label="首字" value={log.ttft_ms} kind="ttft" />
        <LatencyValue
          label="首答"
          value={log.ttfa_ms}
          kind="ttfa"
          emptyHint={ttfaMissing(log) ? TTFA_NO_CONTENT_LABEL : undefined}
        />
        <LatencyValue label="总" value={log.total_ms} kind="total" />
      </div>
    </div>
  );
}

function InlineLogSummary({ log, hideSensitive }: { log: RequestLog; hideSensitive: boolean }) {
  const { mode } = useTimezone();
  const parts = formatDateTimeParts(log.created_at, mode);
  const attempts = [...log.attempts].sort((a, b) => (a.round ?? 1) - (b.round ?? 1));
  const chain = attempts
    .map((attempt) =>
      [attempt.channel_name || attempt.channel_slug, attempt.upstream_model]
        .filter(Boolean)
        .join(" / "),
    )
    .filter(Boolean);
  const failedAttempts = attempts.filter((attempt) =>
    ["error", "stopped"].includes(attempt.status ?? ""),
  ).length;
  const skippedAttempts = attempts.filter((attempt) => attempt.status === "skipped").length;
  const rewriteSkipped = rewriteSkippedLabel(log.rewrite_skipped);
  return (
    <div
      data-testid="log-inline-detail"
      className="min-w-0 rounded-control border border-border bg-surface-2 px-4 py-3.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge log={log} />
        <FailedAttemptsBadge log={log} />
        <span className="text-xs text-muted-foreground">
          {parts ? `${parts.date} ${parts.time}` : "—"} · {TIMEZONE_LABEL[mode]}
        </span>
        {log.usage_estimated ? (
          <span className="rounded-badge bg-warning-soft px-1.5 py-0.5 text-2xs text-warning-text">
            估算用量
          </span>
        ) : null}
        {log.rewrite_applied ? (
          <span className="rounded-badge bg-primary-soft px-1.5 py-0.5 text-2xs text-primary">
            已改写
          </span>
        ) : null}
      </div>

      <dl className="mt-3 grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <div className="min-w-0">
          <dt className="text-2xs text-muted-foreground">网关请求编号</dt>
          <dd className="truncate font-mono text-2xs text-title" title={log.request_id}>
            {log.request_id}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-muted-foreground">上游响应 id</dt>
          <dd className="truncate font-mono text-2xs text-title" title={log.upstream_request_id}>
            {log.upstream_request_id || "—"}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-muted-foreground">请求模型 / 上游模型 / 响应模型</dt>
          <dd className="truncate font-mono text-2xs text-title">
            {log.requested_model || "—"} / {log.upstream_model || "—"} / {log.response_model || "—"}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-muted-foreground">路由链</dt>
          <dd className="truncate text-2xs text-title" title={chain.join(" → ")}>
            {chain.length ? chain.join(" → ") : "单次直接调用"}
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-muted-foreground">尝试次数</dt>
          <dd className="tabular-nums text-title">
            {attempts.length || 1}
            {failedAttempts || skippedAttempts ? (
              <span className="ml-1 text-2xs text-muted-foreground">
                （失败 {failedAttempts} / 跳过 {skippedAttempts}）
              </span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-muted-foreground">词元（输入 / 输出 / 缓存）</dt>
          <dd className="tabular-nums text-title">
            {log.prompt_tokens} / {log.completion_tokens} / {log.cached_tokens}
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-muted-foreground">缓存比例</dt>
          <dd className="tabular-nums text-title">{formatCachePercent(log.cache_ratio)}</dd>
        </div>
        <div>
          <dt className="text-2xs text-muted-foreground">吞吐</dt>
          <dd className="tabular-nums text-title">{formatThroughput(log.throughput_tps)}</dd>
        </div>
        {/* 用户 / 密钥两列在 xl(1280) 以下不显示，展开行里保留入口。 */}
        <div className="min-w-0">
          <dt className="text-2xs text-muted-foreground">用户 / 密钥</dt>
          <dd className="truncate text-2xs text-title" title={hideSensitive ? "敏感信息已隐藏" : undefined}>
            {maskValue(log.user_name || log.user_id || "—", hideSensitive)} ·{" "}
            <span className="font-mono">{maskValue(log.api_key_label || "—", hideSensitive)}</span>
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-muted-foreground">首字 / 首答 / 总耗时</dt>
          <dd className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs">
            <LatencyValue label="首字" value={log.ttft_ms} kind="ttft" />
            <LatencyValue
              label="首答"
              value={log.ttfa_ms}
              kind="ttfa"
              emptyHint={ttfaMissing(log) ? TTFA_NO_CONTENT_LABEL : undefined}
            />
            <LatencyValue label="总" value={log.total_ms} kind="total" />
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-muted-foreground">费用</dt>
          <dd
            className="tabular-nums text-title"
            title={hideSensitive ? "敏感信息已隐藏" : undefined}
          >
            {maskValue(
              formatCostOrUnconfigured(
                log.cost_usd,
                log.prompt_tokens + log.completion_tokens + log.cached_tokens,
              ),
              hideSensitive,
            )}
          </dd>
        </div>
        <div>
          <dt className="text-2xs text-muted-foreground">流式</dt>
          <dd className="text-title">{log.is_stream ? "是" : "否"}</dd>
        </div>
      </dl>

      {log.error_code || log.error_message ? (
        <p className="mt-3 rounded-control bg-danger-soft px-3 py-2 text-xs text-danger-text">
          {log.error_code ? `${errorCodeLabel(log.error_code)} · ` : ""}
          {log.error_message || "请求失败"}
        </p>
      ) : null}

      {rewriteSkipped ? (
        <p className="mt-3 rounded-control bg-warning-soft px-3 py-2 text-xs text-warning-text">
          改写跳过：{rewriteSkipped}
        </p>
      ) : null}

      {log.rewrite_applied || Object.keys(log.rewrite_diff ?? {}).length ? (
        <details className="mt-3 rounded-control border border-border bg-surface px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-title">改写详情</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-2xs leading-5 text-muted-foreground">
            {JSON.stringify(log.rewrite_diff, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

type TimezoneMode = ReturnType<typeof useTimezone>["mode"];

/** 逐次请求视图与聚合展开面板共用的表头，保证两处列宽与语义一致。 */
function RequestLogTableHead({ mode, sticky = true }: { mode: TimezoneMode; sticky?: boolean }) {
  return (
    <TableHeader className={sticky ? "sticky top-0 z-10" : undefined}>
      <TableRow className="border-b border-border bg-surface-2 text-2xs uppercase tracking-[0.08em] text-muted-foreground">
        <TableHead className="w-44 px-4 py-3 font-semibold">时间 · {TIMEZONE_LABEL[mode]}</TableHead>
        <TableHead className="w-52 px-3 py-3 font-semibold">模型 / 路由</TableHead>
        <TableHead className="hidden px-3 py-3 font-semibold xl:table-cell xl:w-22">用户</TableHead>
        <TableHead className="hidden px-3 py-3 font-semibold xl:table-cell xl:w-22">密钥</TableHead>
        <TableHead className="hidden px-3 py-3 font-semibold xl:table-cell xl:w-25">吞吐</TableHead>
        <TableHead className="hidden px-3 py-3 font-semibold xl:table-cell xl:w-30">词元</TableHead>
        <TableHead className="w-20 px-3 py-3 font-semibold xl:w-25">费用</TableHead>
        <TableHead className="w-32 px-3 py-3 font-semibold xl:w-40" title="首字 / 首答 / 总耗时">
          耗时
        </TableHead>
        <TableHead className="sticky right-0 z-10 w-20 bg-surface-2 px-4 py-3 text-right font-semibold">
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 -left-3 w-3 bg-gradient-to-r from-border/70 to-transparent"
          />
          详情
        </TableHead>
      </TableRow>
    </TableHeader>
  );
}

/**
 * 单条请求行（含就地展开的详情）。主列表和聚合展开面板复用同一份渲染，
 * 让路由链、详情抽屉、敏感信息遮罩和键盘行为不需要各写一套。
 */
function RequestLogRow({
  log,
  mode,
  hideSensitive,
  expanded,
  pathOpen,
  onToggleExpanded,
  onTogglePath,
  onOpenDetail,
}: {
  log: RequestLog;
  mode: TimezoneMode;
  hideSensitive: boolean;
  expanded: boolean;
  pathOpen: boolean;
  onToggleExpanded: () => void;
  onTogglePath: () => void;
  onOpenDetail: () => void;
}) {
  const time = formatDateTimeParts(log.created_at, mode);
  const responseMismatch = Boolean(
    log.response_model && log.upstream_model && log.response_model !== log.upstream_model,
  );
  const { toggle } = useTimezone();
  const attempts = log.attempts ?? [];
  const hasFailover = attempts.length > 1 || attempts.some((attempt) => attempt.status === "skipped");
  return (
    <Fragment>
      <TableRow
        data-testid={`log-row-${log.request_id}`}
        data-expanded={expanded ? "true" : undefined}
        className={cn(
          "group cursor-pointer transition-colors hover:bg-surface-2",
          expanded && "bg-surface-2",
        )}
        onClick={onToggleExpanded}
      >
        <TableCell className="whitespace-nowrap px-4 py-2">
          <div className="flex items-start gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-5 w-5 shrink-0 text-muted-foreground"
              aria-expanded={expanded}
              aria-label={expanded ? "收起行详情" : "展开行详情"}
              onClick={(event) => {
                event.stopPropagation();
                onToggleExpanded();
              }}
            >
              <ChevronRight
                className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-90")}
                strokeWidth={1.75}
                aria-hidden="true"
              />
            </Button>
            <button
              type="button"
              className="text-left"
              aria-label={`切换${mode === "local" ? " UTC" : "本地"}时间`}
              onClick={(event) => {
                event.stopPropagation();
                toggle();
              }}
            >
              <div className="font-mono text-xs text-title hover:text-primary">
                {time ? `${time.date} ${time.time}` : "—"}
              </div>
            </button>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <StatusBadge log={log} />
            <FailedAttemptsBadge log={log} />
          </div>
        </TableCell>
        <TableCell
          className="relative px-3 py-2"
          data-route-open={pathOpen ? "true" : undefined}
        >
          <button
            type="button"
            aria-label={pathOpen ? "收起路由链" : "展开路由链"}
            aria-expanded={pathOpen}
            className="block w-full text-left"
            onClick={(event) => {
              event.stopPropagation();
              onTogglePath();
            }}
          >
            <div
              className="flex min-w-0 items-center gap-1 text-xs font-medium text-title hover:text-primary"
              title={log.requested_model}
            >
              <span className="truncate">{log.requested_model || "—"}</span>
              {responseMismatch ? (
                <AlertTriangle
                  className="h-3 w-3 shrink-0 text-warning-text"
                  strokeWidth={1.75}
                  aria-label={`上游返回模型 ${log.response_model} 与配置不一致`}
                />
              ) : null}
            </div>
            <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-2xs text-muted-foreground">
              <span className="truncate">
                {log.channel_name || "—"} / {log.upstream_model || "—"}
              </span>
              <span
                className="shrink-0 rounded-badge border border-border px-1 text-2xs text-muted-foreground"
                title={`来源：${sourceLabel(log.source)}`}
              >
                {sourceLabel(log.source)}
              </span>
              {hasFailover ? (
                <span
                  className="inline-flex shrink-0 items-center gap-0.5 text-warning-text"
                  title="发生转移"
                >
                  <GitFork className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                  {attempts.length} 次尝试
                </span>
              ) : null}
            </div>
          </button>
          {pathOpen ? <RoutePopover log={log} onClose={onTogglePath} /> : null}
        </TableCell>
        <TableCell
          className="hidden truncate px-3 py-2 text-xs text-title xl:table-cell"
          title={hideSensitive ? "敏感信息已隐藏" : undefined}
        >
          {maskValue(log.user_name || log.user_id || "—", hideSensitive)}
        </TableCell>
        <TableCell className="hidden px-3 py-2 xl:table-cell">
          <span
            className="rounded-badge bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-body"
            title={hideSensitive ? "敏感信息已隐藏" : undefined}
          >
            {maskValue(log.api_key_label || "—", hideSensitive)}
          </span>
        </TableCell>
        <TableCell className="hidden whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title xl:table-cell">
          <div>{formatThroughput(log.throughput_tps)}</div>
          <div className="mt-0.5 text-2xs font-normal text-muted-foreground">
            {log.is_stream ? "流式" : "非流式"}
          </div>
        </TableCell>
        <TableCell className="hidden whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title xl:table-cell">
          <span title="输入 / 输出">
            {log.prompt_tokens} / {log.completion_tokens}
          </span>
          <div className="text-2xs text-muted-foreground">缓存 {formatCachePercent(log.cache_ratio)}</div>
        </TableCell>
        <TableCell
          className="whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title"
          title={hideSensitive ? "敏感信息已隐藏" : undefined}
        >
          {maskValue(
            formatCostOrUnconfigured(
              log.cost_usd,
              log.prompt_tokens + log.completion_tokens + log.cached_tokens,
            ),
            hideSensitive,
          )}
        </TableCell>
        <TableCell className="px-3 py-2">
          <LatencyCell log={log} />
        </TableCell>
        <TableCell className="sticky right-0 z-[5] bg-surface px-4 py-2 text-right group-hover:bg-surface-2 group-data-[expanded=true]:bg-surface-2">
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 -left-3 w-3 bg-gradient-to-r from-border/70 to-transparent"
          />
          <Button
            variant="outline"
            size="sm"
            aria-label={`查看请求 ${log.request_id} 详情`}
            onClick={(event) => {
              event.stopPropagation();
              onOpenDetail();
            }}
          >
            详情
          </Button>
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow className="bg-surface-2">
          <TableCell colSpan={9} className="p-0">
            <div className="max-w-full overflow-hidden p-4">
              <InlineLogSummary log={log} hideSensitive={hideSensitive} />
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </Fragment>
  );
}

/** combo 显示错误数/总数；status 显示状态码徽标（网络错误显示后端伪状态标签）。 */
function GroupStatusBadge({ group, view }: { group: LogGroup; view: LogView }) {
  const failed = isErrorGroup(group);
  if (view === "combo") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 rounded-badge px-1.5 py-0.5 text-2xs font-medium tabular-nums",
          failed ? "bg-danger-soft text-danger-text" : "bg-success-soft text-success-text",
        )}
        title={`错误 ${group.errors} 次 / 共 ${group.requests} 次请求`}
      >
        <span className={cn("h-1.5 w-1.5 rounded-full", failed ? "bg-danger" : "bg-success")} aria-hidden="true" />
        {group.errors} / {group.requests}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-badge px-1.5 py-0.5 text-2xs font-medium",
        failed ? "bg-danger-soft text-danger-text" : "bg-success-soft text-success-text",
      )}
      title={group.status_label || undefined}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", failed ? "bg-danger" : "bg-success")} aria-hidden="true" />
      {groupStatusText(group)}
    </span>
  );
}

function GroupTimeCell({ group, mode }: { group: LogGroup; mode: TimezoneMode }) {
  const first = formatDateTimeParts(group.first_at, mode);
  const last = formatDateTimeParts(group.last_at, mode);
  const firstText = first ? `${first.date} ${first.time}` : "—";
  const lastText = !last ? "—" : first && last.date === first.date ? last.time : `${last.date} ${last.time}`;
  return (
    <div className="whitespace-nowrap">
      <div className="font-mono text-xs text-title">{firstText}</div>
      <div className="mt-0.5 font-mono text-2xs text-muted-foreground">— {lastText}</div>
    </div>
  );
}

function GroupLatencyCell({ group }: { group: LogGroup }) {
  return (
    <div className="flex flex-col gap-0.5 text-2xs">
      <span className="flex items-baseline gap-1.5 whitespace-nowrap">
        <span className="w-7 shrink-0 text-muted-foreground">P50</span>
        <span className="tabular-nums text-xs font-medium text-title">{formatLatency(group.total_p50_ms)}</span>
      </span>
      <span className="flex items-baseline gap-1.5 whitespace-nowrap">
        <span className="w-7 shrink-0 text-muted-foreground">P95</span>
        <span className="tabular-nums text-xs font-medium text-title">{formatLatency(group.total_p95_ms)}</span>
      </span>
    </div>
  );
}

/**
 * combo 行的副行只说徽标没说的那件事：失败率。
 * 请求数已经出现两次（徽标分母、模型列「N 次尝试 · M 次请求」），这里再写一遍只是噪音。
 */
function comboFailureRate(group: LogGroup): string {
  if (group.requests <= 0) return "—";
  if (group.errors <= 0) return "无失败";
  return `失败率 ${formatPercent(group.errors / group.requests, 2)}`;
}

function AggregateLogRow({
  group,
  view,
  mode,
  hideSensitive,
  expanded,
  onToggle,
}: {
  group: LogGroup;
  view: LogView;
  mode: TimezoneMode;
  hideSensitive: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const cost = maskValue(
    formatCostOrUnconfigured(
      group.cost_usd,
      group.prompt_tokens + group.completion_tokens + group.cached_tokens,
    ),
    hideSensitive,
  );
  return (
    <TableRow
      data-testid={`log-group-row-${group.key}`}
      data-expanded={expanded ? "true" : undefined}
      className={cn(
        "group cursor-pointer transition-colors hover:bg-surface-2",
        expanded && "bg-surface-2",
      )}
      onClick={onToggle}
    >
      <TableCell className="px-4 py-2">
        <GroupTimeCell group={group} mode={mode} />
      </TableCell>
      <TableCell className="px-3 py-2">
        <GroupStatusBadge group={group} view={view} />
        <div className="mt-1 text-2xs text-muted-foreground">
          {view === "combo" ? comboFailureRate(group) : `段内 ${group.attempts} 次尝试`}
        </div>
      </TableCell>
      <TableCell className="px-3 py-2" title={group.channels.join(" · ") || undefined}>
        <div className="truncate text-xs font-medium text-title">{group.label || "—"}</div>
        <div className="mt-0.5 truncate text-2xs text-muted-foreground">
          {view === "status" ? groupChannelSummary(group) : `${group.attempts} 次尝试 · ${group.requests} 次请求`}
        </div>
      </TableCell>
      <TableCell className="hidden whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title xl:table-cell">
        {/* 聚合行只给量级：一亿 token 的原始数字读不出大小，精确值放 title 与展开面板。 */}
        <span title={`输入 ${formatCount(group.prompt_tokens)} · 输出 ${formatCount(group.completion_tokens)}`}>
          {formatCompact(group.prompt_tokens)} / {formatCompact(group.completion_tokens)}
        </span>
        <div className="text-2xs text-muted-foreground">缓存 {formatCachePercent(group.cache_ratio)}</div>
      </TableCell>
      <TableCell
        className="hidden whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title xl:table-cell"
        title={hideSensitive ? "敏感信息已隐藏" : undefined}
      >
        {cost}
      </TableCell>
      <TableCell className="px-3 py-2">
        <GroupLatencyCell group={group} />
      </TableCell>
      <TableCell className="px-4 py-2 text-right">
        <Button
          variant="outline"
          size="sm"
          aria-expanded={expanded}
          aria-label={expanded ? `收起 ${group.label} 组内请求` : `展开 ${group.label} 组内请求`}
          onClick={(event) => {
            event.stopPropagation();
            onToggle();
          }}
        >
          {expanded ? "收起" : "展开"}
        </Button>
      </TableCell>
    </TableRow>
  );
}

/**
 * 聚合行的就地展开：用 view=requests + group_key 复用现有请求行渲染与分页。
 * group_key 由后端强制该组条件，其他筛选条件（搜索、时间等）继续沿用。
 */
function GroupRequestsPanel({
  group,
  baseFilters,
  mode,
  hideSensitive,
  onOpenDetail,
}: {
  group: LogGroup;
  baseFilters: LogFilters;
  mode: TimezoneMode;
  hideSensitive: boolean;
  onOpenDetail: (requestId: string) => void;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_LOG_PAGE_SIZE);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [pathOpenId, setPathOpenId] = useState<string | null>(null);

  const filters = useMemo<LogFilters>(
    () => ({
      ...baseFilters,
      view: "requests",
      group_key: group.key,
      page,
      page_size: pageSize,
    }),
    [baseFilters, group.key, page, pageSize],
  );

  const query = useQuery({
    queryKey: ["logs-group", filters],
    queryFn: () => listLogs(filters),
    placeholderData: (previous) => previous,
  });

  useEffect(() => {
    if (!pathOpenId) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-route-open="true"]')) setPathOpenId(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPathOpenId(null);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [pathOpenId]);

  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;

  return (
    <div className="border-t border-border bg-surface-2 px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-2xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          组内请求
          <span className="ml-2 font-normal normal-case tracking-normal">共 {total} 条</span>
        </p>
      </div>
      {query.isLoading ? (
        <div className="flex min-h-20 items-center justify-center gap-2 text-2xs text-muted-foreground">
          <Spinner aria-hidden="true" />
          正在加载组内请求…
        </div>
      ) : query.isError ? (
        <Alert variant="destructive">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{describeApiError(query.error)}</span>
            <Button variant="outline" size="sm" onClick={() => query.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        </Alert>
      ) : rows.length === 0 ? (
        <p className="rounded-control border border-border bg-surface px-3 py-3 text-xs text-muted-foreground">
          该组内没有可展示的请求。
        </p>
      ) : (
        <>
          <div className="max-h-96 overflow-auto rounded-control border border-border bg-surface">
            <Table className="min-w-full table-fixed text-left xl:min-w-280">
              <RequestLogTableHead mode={mode} sticky={false} />
              <TableBody className="leading-tight">
                {rows.map((log) => (
                  <RequestLogRow
                    key={log.id}
                    log={log}
                    mode={mode}
                    hideSensitive={hideSensitive}
                    expanded={expandedId === log.id}
                    pathOpen={pathOpenId === log.id}
                    onToggleExpanded={() => setExpandedId(expandedId === log.id ? null : log.id)}
                    onTogglePath={() => setPathOpenId(pathOpenId === log.id ? null : log.id)}
                    onOpenDetail={() => onOpenDetail(log.request_id)}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        </>
      )}
    </div>
  );
}

function valueOptions(values: string[] | undefined): ComboboxOption[] {
  return (values ?? []).map((value) => ({ value, label: value }));
}

function userOptions(options: LogFilterOptions | undefined): ComboboxOption[] {
  return (options?.users ?? []).map((user) => ({
    value: user.id,
    label: user.name || user.id,
    description: user.id,
  }));
}

export function LogsPage() {
  const { mode, toggle } = useTimezone();
  const [searchParams, setSearchParams] = useSearchParams();
  // URL query 是已生效筛选的唯一来源；工具栏里的状态都只是未应用的草稿。
  const applied = useMemo(() => parseAppliedQuery(searchParams), [searchParams]);
  const appliedKey = useMemo(() => logQueryToParams(applied).toString(), [applied]);
  const [searchInput, setSearchInput] = useState(applied.q);
  const [range, setRange] = useState<TimeRange>(applied.range);
  const [customStart, setCustomStart] = useState(applied.customStart);
  const [customEnd, setCustomEnd] = useState(applied.customEnd);
  const [channel, setChannel] = useState(applied.channel);
  const [model, setModel] = useState(applied.model);
  const [status, setStatus] = useState<LogStatusFilter>(applied.status);
  const statusFilterLabel =
    status === "success" ? "成功" : status === "error" ? "失败" : "全部状态";
  const { ref: statusLabelRef, overflowing: statusOverflowing } =
    useOverflow<HTMLSpanElement>(statusFilterLabel);
  const [statusTooltipOpen, setStatusTooltipOpen] = useState(false);
  const [attemptStatus, setAttemptStatus] = useState<LogAttemptStatusFilter>(applied.attemptStatus);
  const [user, setUser] = useState(applied.user);
  const [keyLabel, setKeyLabel] = useState(applied.keyLabel);
  const [stream, setStream] = useState<LogStreamFilter>(applied.stream);
  const [moreOpen, setMoreOpen] = useState(false);
  const [optionsSearch, setOptionsSearch] = useState("");
  const [hideSensitive, setHideSensitive] = useState(readHideSensitive);
  // 页码也来自 URL：刷新、分享与后退都能复现同一次翻页。
  const page = applied.page;
  const pageSize = applied.pageSize;
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [pathOpenId, setPathOpenId] = useState<string | null>(null);
  const [expandedGroupKey, setExpandedGroupKey] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const tableScrollRef = useRef<HTMLDivElement | null>(null);

  // 浏览器后退、前进或手动改地址后，把草稿同步回 URL 里的已生效条件。
  useEffect(() => {
    setSearchInput(applied.q);
    setRange(applied.range);
    setCustomStart(applied.customStart);
    setCustomEnd(applied.customEnd);
    setChannel(applied.channel);
    setModel(applied.model);
    setStatus(applied.status);
    setAttemptStatus(applied.attemptStatus);
    setUser(applied.user);
    setKeyLabel(applied.keyLabel);
    setStream(applied.stream);
  }, [appliedKey]);

  useEffect(() => {
    try {
      writeValue("logsHideSensitive", hideSensitive);
    } catch {
      // 隐私模式下存储不可用时，隐藏状态仍在本页生效。
    }
  }, [hideSensitive]);

  // 切到另一种聚合视角时，旧的行展开/路由链/组展开都失去意义，统一收起。
  useEffect(() => {
    setExpandedId(null);
    setPathOpenId(null);
    setExpandedGroupKey(null);
  }, [applied.view]);

  // 翻页或改每页行数后，把日志列表滚回顶部，避免停留在上一页的滚动位置。
  useEffect(() => {
    const node = tableScrollRef.current;
    if (node) node.scrollTop = 0;
  }, [page, pageSize]);

  useEffect(() => {
    setOptionsSearch("");
  }, [moreOpen]);

  useEffect(() => {
    if (!pathOpenId) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-route-open="true"]')) setPathOpenId(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPathOpenId(null);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [pathOpenId]);

  const bounds = useMemo(
    () => rangeBounds(applied.range, applied.customStart, applied.customEnd),
    [applied],
  );

  // 按钮上的区间跟随草稿（与按钮内的预设名同一口径）：点「应用」后、点「搜索」前二者一致，
  // 避免出现「24 时」却显示今天区间这种自相矛盾的展示。
  const draftBounds = useMemo(() => rangeBounds(range, customStart, customEnd), [range, customStart, customEnd]);
  const draftInterval = useMemo(() => formatInterval(draftBounds.start, draftBounds.end, mode), [draftBounds, mode]);
  // 每个控件单独判断是否偏离已生效条件：只给被改过的框描边，提交后随 applied 同步自然消失。
  const dirtyFields = useMemo(
    () => ({
      search: searchInput !== applied.q,
      range:
        range !== applied.range || customStart !== applied.customStart || customEnd !== applied.customEnd,
      channel: channel !== applied.channel,
      model: model !== applied.model,
      status: status !== applied.status,
      attemptStatus: attemptStatus !== applied.attemptStatus,
      user: user !== applied.user,
      keyLabel: keyLabel !== applied.keyLabel,
      stream: stream !== applied.stream,
    }),
    [searchInput, range, customStart, customEnd, channel, model, status, attemptStatus, user, keyLabel, stream, applied],
  );

  const filters = useMemo<LogFilters>(
    () => ({
      page,
      page_size: pageSize,
      q: applied.q,
      channel: applied.channel,
      model: applied.model,
      status: applied.status,
      attempt_status: applied.attemptStatus,
      user: applied.user,
      key_label: applied.keyLabel,
      stream: applied.stream,
      view: isAggregateLogView(applied.view) ? applied.view : undefined,
      start_ms: bounds.start,
      end_ms: bounds.end,
    }),
    [page, pageSize, applied, bounds],
  );

  // 度量条与列表使用同一组筛选，但不受分页影响。
  const summaryFilters = useMemo<LogFilters>(
    () => ({
      q: applied.q,
      channel: applied.channel,
      model: applied.model,
      status: applied.status,
      attempt_status: applied.attemptStatus,
      user: applied.user,
      key_label: applied.keyLabel,
      stream: applied.stream,
      start_ms: bounds.start,
      end_ms: bounds.end,
    }),
    [applied, bounds],
  );

  const logsQuery = useQuery({
    queryKey: ["logs", filters],
    queryFn: () => listLogs(filters),
    placeholderData: (previous) => previous,
  });

  const summaryQuery = useQuery({
    queryKey: ["logs-summary", summaryFilters],
    queryFn: () => apiRequest<LogSummary>(`/api/logs/summary?${buildLogQuery(summaryFilters)}`),
    placeholderData: (previous) => previous,
  });
  const summary = summaryQuery.data;
  const summaryError = summaryQuery.isError ? describeApiError(summaryQuery.error) : null;

  // 口径统一在 lib/logs 的 buildLogMetrics 里，页面只补图标与颜色。
  const metricItems = useMemo<MetricPillItem[]>(
    () =>
      buildLogMetrics(summary).map((metric) => ({
        ...metric,
        ...METRIC_VISUALS[metric.key],
      })),
    [summary],
  );

  const optionsQuery = useQuery({
    queryKey: ["log-filter-options", optionsSearch],
    queryFn: () => getLogFilterOptions(optionsSearch),
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  });

  const handleOptionsSearch = useCallback((value: string) => setOptionsSearch(value), []);

  const items = logsQuery.data?.items ?? [];
  const groups = logsQuery.data?.groups ?? [];
  const total = logsQuery.data?.total ?? 0;
  const isAggregate = isAggregateLogView(applied.view);
  const options = optionsQuery.data;
  const moreFilterCount = [
    applied.channel,
    applied.user,
    applied.keyLabel,
    applied.stream,
    applied.attemptStatus,
  ].filter(Boolean).length;

  // 草稿只在点「搜索」或回车时生效；生效后 URL 变化，上面的 effect 再把草稿同步回来。
  const draftQuery = useMemo<LogQueryState>(
    () => ({
      q: searchInput,
      range,
      customStart,
      customEnd,
      channel,
      model,
      status,
      attemptStatus,
      user,
      keyLabel,
      stream,
      view: applied.view,
      page: applied.page,
      pageSize: applied.pageSize,
    }),
    [searchInput, range, customStart, customEnd, channel, model, status, attemptStatus, user, keyLabel, stream, applied.view, applied.page, applied.pageSize],
  );

  const dirty = logQueryToParams(draftQuery).toString() !== appliedKey;
  // 视图不是筛选条件：只看聚合视角时不应让「清除筛选」亮起，也不该把空态说成筛选无结果。
  const hasFilters = logQueryToParams({ ...applied, view: "requests" }).toString().length > 0;

  const applyFilters = () => {
    setSearchParams(logQueryToParams({ ...draftQuery, page: 1 }));
  };

  const patchApplied = (patch: Partial<LogQueryState>) => {
    setSearchParams(logQueryToParams({ ...applied, page: 1, ...patch }));
  };

  const clearFilters = () => {
    setSearchInput("");
    setRange(DEFAULT_LOG_RANGE);
    setCustomStart("");
    setCustomEnd("");
    setChannel("");
    setModel("");
    setStatus("");
    setAttemptStatus("");
    setUser("");
    setKeyLabel("");
    setStream("");
    setMoreOpen(false);
    // 「清除筛选」只清筛选条件，不把用户正在看的聚合视角一起重置。
    setSearchParams(
      logQueryToParams({
        ...applied,
        q: "",
        range: DEFAULT_LOG_RANGE,
        customStart: "",
        customEnd: "",
        channel: "",
        model: "",
        status: "",
        attemptStatus: "",
        user: "",
        keyLabel: "",
        stream: "",
        page: 1,
        pageSize: DEFAULT_LOG_PAGE_SIZE,
      }),
    );
  };

  const chips = useMemo(() => {
    const result: Array<{ key: string; label: string; onRemove: () => void }> = [];
    if (applied.q) {
      result.push({
        key: "search",
        label: `搜索：${applied.q}`,
        onRemove: () => patchApplied({ q: "" }),
      });
    }
    if (applied.range !== DEFAULT_LOG_RANGE || applied.customStart || applied.customEnd) {
      result.push({
        key: "range",
        label: `时间：${timeRangeLabel(applied.range, applied.customStart, applied.customEnd)}`,
        onRemove: () => patchApplied({ range: DEFAULT_LOG_RANGE, customStart: "", customEnd: "" }),
      });
    }
    if (applied.model) {
      result.push({ key: "model", label: `模型：${applied.model}`, onRemove: () => patchApplied({ model: "" }) });
    }
    if (applied.channel) {
      result.push({
        key: "channel",
        label: `渠道：${applied.channel}`,
        onRemove: () => patchApplied({ channel: "" }),
      });
    }
    if (applied.user) {
      const name = options?.users?.find((item) => item.id === applied.user)?.name ?? applied.user;
      result.push({ key: "user", label: `用户：${name}`, onRemove: () => patchApplied({ user: "" }) });
    }
    if (applied.keyLabel) {
      result.push({ key: "key", label: `密钥：${applied.keyLabel}`, onRemove: () => patchApplied({ keyLabel: "" }) });
    }
    if (applied.status) {
      result.push({
        key: "status",
        label: `状态：${applied.status === "success" ? "成功" : "失败"}`,
        onRemove: () => patchApplied({ status: "" }),
      });
    }
    if (applied.attemptStatus) {
      result.push({
        key: "attempt",
        label: "尝试：含失败尝试",
        onRemove: () => patchApplied({ attemptStatus: "" }),
      });
    }
    if (applied.stream) {
      result.push({
        key: "stream",
        label: `调用：${applied.stream === "stream" ? "仅流式" : "仅非流式"}`,
        onRemove: () => patchApplied({ stream: "" }),
      });
    }
    return result;
  }, [applied, options]);


  return (
    <div className="animate-fade-up">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-h1 font-semibold tracking-[-0.02em] text-title">使用日志</h1>
          <p className="mt-1.5 max-w-2xl text-2sm text-muted-foreground">
                            按渠道、模型、状态与调用方筛选每一次请求；展开行查看首字、首答、总耗时、路由链与改写结果。
          </p>
        </div>
      </div>

      <MetricStrip
        className="mt-6"
        items={metricItems}
        loading={summaryQuery.isLoading}
        error={summaryError}
      />

      <Card className="mt-4 p-4">
        <div className="flex flex-col gap-2.5 xl:flex-row xl:items-start">
          <div className="grid min-w-0 gap-2.5 sm:grid-cols-2 lg:grid-cols-4 xl:flex xl:flex-1 xl:flex-wrap xl:items-start">
            <div className="min-w-0 sm:col-span-2 lg:col-span-2 xl:basis-80 xl:grow">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                strokeWidth={1.75}
                aria-hidden="true"
              />
              <Input
                aria-label="搜索请求编号"
                aria-describedby="log-search-help"
                className={cn("h-9 pl-9 text-xs", dirtyFields.search && DIRTY_FIELD_CLASS)}
                placeholder="搜索编号、成员模型 / 渠道或错误信息"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    applyFilters();
                  }
                }}
              />
            </div>
            <p id="log-search-help" className="mt-1 text-2xs leading-4 text-muted-foreground">
              组合视图会同时搜组内实际命中的成员模型、渠道与错误信息
            </p>
          </div>

          <TimeRangePicker
            range={range}
            customStart={customStart}
            customEnd={customEnd}
            interval={draftInterval}
            timezone={mode}
            highlighted={dirtyFields.range}
            hint="切换后点「搜索」应用筛选"
            onChange={(nextRange, nextStart, nextEnd) => {
              setRange(nextRange);
              setCustomStart(nextStart);
              setCustomEnd(nextEnd);
            }}
            className="xl:w-52 xl:flex-none"
          />

          <Combobox
            ariaLabel="模型或组合筛选"
            highlighted={dirtyFields.model}
            placeholder="全部模型 / 组合"
            options={valueOptions(options?.models)}
            value={model}
            onChange={setModel}
            onSearch={handleOptionsSearch}
            loading={optionsQuery.isFetching}
            truncated={options?.truncated?.models ?? false}
            className="xl:w-44 xl:flex-none"
          />

          <div className="min-w-0 xl:w-36 xl:flex-none">
            <Select
              value={status || SELECT_ALL_VALUE}
              onValueChange={(value) => setStatus((value === SELECT_ALL_VALUE ? "" : value) as LogStatusFilter)}
            >
            <TooltipProvider delayDuration={150}>
              <Tooltip
                open={statusTooltipOpen}
                onOpenChange={(next) => setStatusTooltipOpen(next && statusOverflowing)}
              >
                <TooltipTrigger asChild>
                  <SelectTrigger
                    aria-label="状态筛选"
                    className={cn(SELECT_CLASS, dirtyFields.status && DIRTY_FIELD_CLASS)}
                  >
                    <SelectValue>
                      <span ref={statusLabelRef}>{statusFilterLabel}</span>
                    </SelectValue>
                  </SelectTrigger>
                </TooltipTrigger>
                <TooltipContent>{statusFilterLabel}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <SelectContent>
              <SelectItem value={SELECT_ALL_VALUE}>全部状态</SelectItem>
              <SelectItem value="success">成功</SelectItem>
              <SelectItem value="error">失败</SelectItem>
            </SelectContent>
            </Select>
          </div>


          {moreOpen ? (
            <div className="grid gap-2.5 rounded-control border border-border bg-surface-2 p-2.5 sm:col-span-2 lg:col-span-4 xl:col-span-8 xl:basis-full xl:flex-none xl:grid-cols-4">
              <div className="min-w-0">
                <Combobox
                  ariaLabel="渠道筛选"
                  highlighted={dirtyFields.channel}
                  placeholder="全部渠道"
                  options={valueOptions(options?.channels)}
                  value={channel}
                  onChange={setChannel}
                  onSearch={handleOptionsSearch}
                  loading={optionsQuery.isFetching}
                  truncated={options?.truncated?.channels ?? false}
                />
                <p className="mt-1 text-2xs leading-4 text-muted-foreground">包含该渠道失败的请求</p>
              </div>
              <Combobox
                ariaLabel="用户筛选"
                highlighted={dirtyFields.user}
                placeholder="全部用户"
                options={userOptions(options)}
                value={user}
                onChange={setUser}
                onSearch={handleOptionsSearch}
                loading={optionsQuery.isFetching}
                truncated={options?.truncated?.users ?? false}
              />
              <Combobox
                ariaLabel="密钥标识筛选"
                highlighted={dirtyFields.keyLabel}
                placeholder="全部密钥标识"
                options={valueOptions(options?.key_labels)}
                value={keyLabel}
                onChange={setKeyLabel}
                onSearch={handleOptionsSearch}
                loading={optionsQuery.isFetching}
                truncated={options?.truncated?.key_labels ?? false}
              />
              <Select
                value={stream || SELECT_ALL_VALUE}
                onValueChange={(value) => setStream((value === SELECT_ALL_VALUE ? "" : value) as LogStreamFilter)}
              >
                <SelectTrigger
                  aria-label="流式筛选"
                  className={cn(SELECT_CLASS, dirtyFields.stream && DIRTY_FIELD_CLASS)}
                >
                  <SelectValue>
                    {stream === "stream" ? "仅流式" : stream === "json" ? "仅非流式" : "流式 / 非流式"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={SELECT_ALL_VALUE}>流式 / 非流式</SelectItem>
                  <SelectItem value="stream">仅流式</SelectItem>
                  <SelectItem value="json">仅非流式</SelectItem>
                </SelectContent>
              </Select>
              <Select
                value={attemptStatus || SELECT_ALL_VALUE}
                onValueChange={(value) =>
                  setAttemptStatus((value === SELECT_ALL_VALUE ? "" : value) as LogAttemptStatusFilter)
                }
              >
                <SelectTrigger
                  aria-label="失败尝试筛选"
                  className={cn(SELECT_CLASS, dirtyFields.attemptStatus && DIRTY_FIELD_CLASS)}
                >
                  <SelectValue>{attemptStatus ? "仅含失败尝试" : "失败尝试不限"}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={SELECT_ALL_VALUE}>失败尝试不限</SelectItem>
                  <SelectItem value="error">仅含失败尝试</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : null}
          </div>

          <div className="order-first flex items-center justify-end gap-2 xl:order-last xl:shrink-0">
            <span className="text-2xs text-muted-foreground xl:hidden">视图</span>
            <ToggleGroup
              type="single"
              value={applied.view}
              aria-label="日志视图"
              className="inline-flex h-9 shrink-0 items-center rounded-control border border-border bg-surface-2 p-0.5"
              onValueChange={(value) => {
                if (!value) return;
                patchApplied({ view: value as LogView });
              }}
            >
              {LOG_VIEW_OPTIONS.map((option) => (
                <ToggleGroupItem
                  key={option.value}
                  value={option.value}
                  className="h-7 rounded-badge px-2 text-2xs"
                >
                  {option.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button
            variant={dirty ? "default" : "outline"}
            size="md"
            className="gap-1.5"
            data-dirty={dirty ? "true" : "false"}
            aria-label={dirty ? "搜索（有未应用的筛选修改）" : "搜索"}
            onClick={applyFilters}
          >
            <Search className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            搜索
          </Button>
          <Button
            variant="outline"
            size="md"
            className="justify-between gap-2"
            onClick={() => setMoreOpen((value) => !value)}
            aria-expanded={moreOpen}
          >
            <span className="flex items-center gap-1.5">
              <SlidersHorizontal className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              更多筛选
            </span>
            {moreFilterCount ? (
              <span className="rounded-full bg-primary-soft px-1.5 text-2xs font-semibold text-primary">
                {moreFilterCount}
              </span>
            ) : (
              <ChevronDown
                className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", moreOpen && "rotate-180")}
                strokeWidth={1.75}
                aria-hidden="true"
              />
            )}
          </Button>
          <span className="text-2xs text-muted-foreground">
            时间列按 {TIMEZONE_LABEL[mode]} 显示，可在顶栏切换
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-8"
            onClick={clearFilters}
            disabled={!hasFilters && !dirty}
          >
            <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            清除
          </Button>
          <Button
            variant={hideSensitive ? "outline" : "ghost"}
            size="sm"
            className="h-8"
            aria-pressed={hideSensitive}
            onClick={() => setHideSensitive((value) => !value)}
          >
            {hideSensitive ? (
              <EyeOff className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            ) : (
              <Eye className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            )}
            {hideSensitive ? "显示敏感信息" : "隐藏敏感信息"}
          </Button>
          {chips.length ? (
            <div className="flex flex-wrap items-center gap-1.5">
              {chips.map((chip) => (
                <span
                  key={chip.key}
                  className="inline-flex items-center gap-1 rounded-badge border border-border bg-surface-2 px-2 py-0.5 text-2xs text-body"
                >
                  {chip.label}
                  <button
                    type="button"
                    onClick={chip.onRemove}
                    className="text-muted-foreground hover:text-title"
                    aria-label={`移除筛选 ${chip.label}`}
                  >
                    <X className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </Card>

      {logsQuery.isError ? (
        <Alert variant="destructive" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{describeApiError(logsQuery.error)}</span>
            <Button variant="outline" size="sm" onClick={() => logsQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        </Alert>
      ) : null}

      <Card className="mt-4 overflow-hidden">
        {logsQuery.data?.scan_limited ? (
          <div
            role="status"
            className="flex items-start gap-2 border-b border-border bg-warning-soft px-4 py-2.5 text-2xs text-warning-text"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            <span>命中超过 5 万条，已按最近 5 万条聚合，请收窄时间范围</span>
          </div>
        ) : null}
        {logsQuery.isLoading ? (
          <div className="flex min-h-65 items-center justify-center gap-2 text-2sm text-muted-foreground">
            <Spinner aria-hidden="true" />
            正在加载使用日志…
          </div>
        ) : (isAggregate ? groups.length === 0 : items.length === 0) ? (
          <div className="flex min-h-80 flex-col items-center justify-center px-6 py-14 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-soft text-primary">
              <ScrollText className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
            </span>
            <h2 className="mt-4 text-sm font-semibold text-title">
              {hasFilters ? "没有匹配的日志" : "还没有请求日志"}
            </h2>
            <p className="mt-1.5 max-w-md text-2sm leading-6 text-muted-foreground">
              {hasFilters
                ? "调整时间范围或筛选条件，也可以换一个请求编号再试。"
                : "通过网关发起请求后，这里会逐条记录模型、渠道、耗时与费用。"}
            </p>
            {hasFilters ? (
              <Button variant="outline" className="mt-5" onClick={clearFilters}>
                清除筛选
              </Button>
            ) : null}
          </div>
        ) : isAggregate ? (
          <>
            <div
              ref={tableScrollRef}
              className="max-h-[calc(100vh-15rem)] overflow-auto [&>div]:overflow-visible"
            >
              <Table className="min-w-full table-fixed text-left xl:min-w-280">
                <TableHeader className="sticky top-0 z-10">
                  <TableRow className="border-b border-border bg-surface-2 text-2xs uppercase tracking-[0.08em] text-muted-foreground">
                    <TableHead className="w-36 px-4 py-3 font-semibold sm:w-44">时间 · {TIMEZONE_LABEL[mode]}</TableHead>
                    <TableHead className="w-20 px-3 py-3 font-semibold sm:w-28">状态</TableHead>
                    <TableHead className="w-40 px-3 py-3 font-semibold sm:w-56">模型 / 渠道</TableHead>
                    <TableHead className="hidden w-30 px-3 py-3 font-semibold xl:table-cell">词元</TableHead>
                    <TableHead className="hidden w-25 px-3 py-3 font-semibold xl:table-cell">费用</TableHead>
                    <TableHead className="w-28 px-3 py-3 font-semibold sm:w-36" title="组内请求总耗时的分位数">
                      耗时
                    </TableHead>
                    <TableHead className="w-16 px-4 py-3 text-right font-semibold sm:w-20">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody className="leading-tight">
                  {groups.map((group) => (
                    <Fragment key={group.key}>
                      <AggregateLogRow
                        group={group}
                        view={applied.view}
                        mode={mode}
                        hideSensitive={hideSensitive}
                        expanded={expandedGroupKey === group.key}
                        onToggle={() =>
                          setExpandedGroupKey(expandedGroupKey === group.key ? null : group.key)
                        }
                      />
                      {expandedGroupKey === group.key ? (
                        <TableRow className="bg-surface-2">
                          <TableCell colSpan={7} className="p-0">
                            <GroupRequestsPanel
                              key={group.key}
                              group={group}
                              baseFilters={filters}
                              mode={mode}
                              hideSensitive={hideSensitive}
                              onOpenDetail={setDetailId}
                            />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            </div>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={(next) => setSearchParams(logQueryToParams({ ...applied, page: next }))}
              onPageSizeChange={(size) =>
                setSearchParams(logQueryToParams({ ...applied, page: 1, pageSize: size }))
              }
            />
          </>
        ) : (
          <>
            {/* shadcn Table 自带一层 overflow-auto 包裹：把它掰成 visible，让这层 div
                继续做唯一滚动容器，表头的 sticky 才生效。 */}
            <div
              ref={tableScrollRef}
              className="max-h-[calc(100vh-15rem)] overflow-auto [&>div]:overflow-visible"
            >
              {/* 详情列 sticky 固定在右侧；「用户 / 密钥 / 吞吐 / 词元」在 xl(1280) 以下隐藏，
                  xl 以上总宽 1120px 不溢出 1440 容器，xl 以下仅剩 时间/模型/费用/耗时/详情 五列，
                  1024 起零横向滚动，详情列始终固定可见。 */}
              <Table className="min-w-full table-fixed text-left xl:min-w-280">
                <RequestLogTableHead mode={mode} />
                <TableBody className="leading-tight">
                  {items.map((log) => {
                    const time = formatDateTimeParts(log.created_at, mode);
                    const responseMismatch = Boolean(
                      log.response_model && log.upstream_model && log.response_model !== log.upstream_model,
                    );
                    const expanded = expandedId === log.id;
                    const pathOpen = pathOpenId === log.id;
                    const attempts = log.attempts ?? [];
                    const hasFailover =
                      attempts.length > 1 || attempts.some((attempt) => attempt.status === "skipped");
                    return (
                      <Fragment key={log.id}>
                        <TableRow
                          data-testid={`log-row-${log.request_id}`}
                          data-expanded={expanded ? "true" : undefined}
                          className={cn(
                            "group cursor-pointer transition-colors hover:bg-surface-2",
                            expanded && "bg-surface-2",
                          )}
                          onClick={() => setExpandedId(expanded ? null : log.id)}
                        >
                          <TableCell className="whitespace-nowrap px-4 py-2">
                            <div className="flex items-start gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-5 w-5 shrink-0 text-muted-foreground"
                                aria-expanded={expanded}
                                aria-label={expanded ? "收起行详情" : "展开行详情"}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setExpandedId(expanded ? null : log.id);
                                }}
                              >
                                <ChevronRight
                                  className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-90")}
                                  strokeWidth={1.75}
                                  aria-hidden="true"
                                />
                              </Button>
                              <button
                                type="button"
                                className="text-left"
                                aria-label={`切换${mode === "local" ? " UTC" : "本地"}时间`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  toggle();
                                }}
                              >
                                <div className="font-mono text-xs text-title hover:text-primary">
                                  {time ? `${time.date} ${time.time}` : "—"}
                                </div>
                              </button>
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-1">
                              <StatusBadge log={log} />
                              <FailedAttemptsBadge log={log} />
                            </div>
                          </TableCell>
                          <TableCell
                            className="relative px-3 py-2"
                            data-route-open={pathOpen ? "true" : undefined}
                          >
                            <button
                              type="button"
                              aria-label={pathOpen ? "收起路由链" : "展开路由链"}
                              aria-expanded={pathOpen}
                              className="block w-full text-left"
                              onClick={(event) => {
                                event.stopPropagation();
                                setPathOpenId(pathOpen ? null : log.id);
                              }}
                            >
                              <div
                                className="flex min-w-0 items-center gap-1 text-xs font-medium text-title hover:text-primary"
                                title={log.requested_model}
                              >
                                <span className="truncate">{log.requested_model || "—"}</span>
                                {responseMismatch ? (
                                  <AlertTriangle
                                    className="h-3 w-3 shrink-0 text-warning-text"
                                    strokeWidth={1.75}
                                    aria-label={`上游返回模型 ${log.response_model} 与配置不一致`}
                                  />
                                ) : null}
                              </div>
                              <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-2xs text-muted-foreground">
                                <span className="truncate">
                                  {log.channel_name || "—"} / {log.upstream_model || "—"}
                                </span>
                                <span
                                  className="shrink-0 rounded-badge border border-border px-1 text-2xs text-muted-foreground"
                                  title={`来源：${sourceLabel(log.source)}`}
                                >
                                  {sourceLabel(log.source)}
                                </span>
                                {hasFailover ? (
                                  <span
                                    className="inline-flex shrink-0 items-center gap-0.5 text-warning-text"
                                    title="发生转移"
                                  >
                                    <GitFork className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                                    {attempts.length} 次尝试
                                  </span>
                                ) : null}
                              </div>
                            </button>
                            {pathOpen ? <RoutePopover log={log} onClose={() => setPathOpenId(null)} /> : null}
                          </TableCell>
                          <TableCell
                            className="hidden truncate px-3 py-2 text-xs text-title xl:table-cell"
                            title={hideSensitive ? "敏感信息已隐藏" : undefined}
                          >
                            {maskValue(log.user_name || log.user_id || "—", hideSensitive)}
                          </TableCell>
                          <TableCell className="hidden px-3 py-2 xl:table-cell">
                            <span
                              className="rounded-badge bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-body"
                              title={hideSensitive ? "敏感信息已隐藏" : undefined}
                            >
                              {maskValue(log.api_key_label || "—", hideSensitive)}
                            </span>
                          </TableCell>
                          <TableCell className="hidden whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title xl:table-cell">
                            <div>{formatThroughput(log.throughput_tps)}</div>
                            <div className="mt-0.5 text-2xs font-normal text-muted-foreground">
                              {log.is_stream ? "流式" : "非流式"}
                            </div>
                          </TableCell>
                          <TableCell className="hidden whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title xl:table-cell">
                            <span title="输入 / 输出">
                              {log.prompt_tokens} / {log.completion_tokens}
                            </span>
                            <div className="text-2xs text-muted-foreground">缓存 {formatCachePercent(log.cache_ratio)}</div>
                          </TableCell>
                          <TableCell
                            className="whitespace-nowrap px-3 py-2 text-xs tabular-nums text-title"
                            title={hideSensitive ? "敏感信息已隐藏" : undefined}
                          >
                            {maskValue(
              formatCostOrUnconfigured(
                log.cost_usd,
                log.prompt_tokens + log.completion_tokens + log.cached_tokens,
              ),
              hideSensitive,
            )}
                          </TableCell>
                          <TableCell className="px-3 py-2">
                            <LatencyCell log={log} />
                          </TableCell>
                          <TableCell className="sticky right-0 z-[5] bg-surface px-4 py-2 text-right group-hover:bg-surface-2 group-data-[expanded=true]:bg-surface-2">
                            <span
                              aria-hidden="true"
                              className="pointer-events-none absolute inset-y-0 -left-3 w-3 bg-gradient-to-r from-border/70 to-transparent"
                            />
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label={`查看请求 ${log.request_id} 详情`}
                              onClick={(event) => {
                                event.stopPropagation();
                                setDetailId(log.request_id);
                              }}
                            >
                              详情
                            </Button>
                          </TableCell>
                        </TableRow>
                        {expanded ? (
                          <TableRow className="bg-surface-2">
                            <TableCell colSpan={9} className="p-0">
                              <div className="max-w-full overflow-hidden p-4">
                                <InlineLogSummary log={log} hideSensitive={hideSensitive} />
                              </div>
                            </TableCell>
                          </TableRow>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={(next) => setSearchParams(logQueryToParams({ ...applied, page: next }))}
              onPageSizeChange={(size) =>
                setSearchParams(logQueryToParams({ ...applied, page: 1, pageSize: size }))
              }
            />
          </>
        )}
      </Card>

      <LogDetailDrawer
        requestId={detailId}
        onClose={() => setDetailId(null)}
        hideSensitive={hideSensitive}
      />
    </div>
  );
}