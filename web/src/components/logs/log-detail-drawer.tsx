import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, ChevronRight, RefreshCw, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Sheet, SheetClose, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableRow } from "@/components/ui/table";
import { describeApiError } from "@/lib/channels";
import {
  attemptStatusLabel,
  attemptErrorText,
  attemptErrorCount,
  errorCodeLabel,
  failedAttempts,
  formatPriorityPath,
  hasFailedAttempts,
  formatCachePercent,
  formatThroughput,
  getLog,
  isErrorLog,
  rewriteSkippedLabel,
  sourceLabel,
  TTFA_NO_CONTENT_LABEL,
  ttfaMissing,
  type LogAttempt,
  type RequestLog,
} from "@/lib/logs";
import { formatRequestCost } from "@/lib/format";
import {
  formatLatency,
  isLatencyMissing,
  latencyDescription,
} from "@/lib/latency";
import { formatDateTime, useTimezone } from "@/lib/time";
import { cn } from "@/lib/utils";

const ATTEMPT_STATUS_STYLE: Record<string, string> = {
  success: "bg-success",
  error: "bg-danger",
  skipped: "bg-warning",
  // neutral dot: bg-faint stays visible on surface-2 (semantic colour pass deferred to batch 3)
  stopped: "bg-faint",
};

const MASKED_VALUE = "•••";

/**
 * 请求详情里后端已返回、但 lib/logs.ts 的 RequestLog 尚未登记的新字段。
 * 契约见 llmapi/api/src/l06_api/routers/logs.py 的 serialize_log。
 */
type LogAddressFields = {
  client_ip?: string;
  forwarded_ips?: string[];
  forwarded_sources?: Array<{ header?: string; value?: string }>;
  request_headers?: Record<string, string>;
};

type DetailLog = RequestLog & LogAddressFields;

type DetailAttempt = LogAttempt & {
  request_headers?: Record<string, string>;
  response_headers?: Record<string, string>;
};

/**
 * 请求头 / 响应头展示策略：默认展开下游请求头与上游响应头，上游请求头折叠。
 * 要调整默认展示，只改这一处，不用动 JSX。
 */
const HEADERS_POLICY = {
  downstreamRequest: { label: "下游请求头", defaultOpen: true },
  upstreamRequest: { label: "上游请求头", defaultOpen: false },
  upstreamResponse: { label: "上游响应头", defaultOpen: true },
} as const;

/** 原始转发头对 → 悬停提示，形如 "X-Forwarded-For:1.1.1.1;X-Real-IP:2.2.2.2"。 */
function forwardedSourceText(
  sources: Array<{ header?: string; value?: string }> | undefined,
): string {
  return (sources ?? [])
    .map((item) => {
      const header = (item.header ?? "").trim();
      const value = (item.value ?? "").trim();
      if (!header) return value;
      return value ? `${header}:${value}` : header;
    })
    .filter(Boolean)
    .join(";");
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="border-t border-border px-5 py-4">
      <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-2xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-xs text-title">{children}</dd>
    </div>
  );
}

/** 地址行：单行截断，悬停给出完整地址或原始转发头。 */
function AddressField({
  label,
  addresses,
  hint,
  testId,
  className,
}: {
  label: string;
  addresses: string[];
  hint?: string;
  testId: string;
  className?: string;
}) {
  const text = addresses.length > 0 ? addresses.join(", ") : "—";
  return (
    <Field label={label} className={className}>
      <span
        data-testid={testId}
        className="block max-w-full truncate font-mono text-2xs"
        title={addresses.length > 0 ? hint || text : undefined}
      >
        {text}
      </span>
    </Field>
  );
}

function AttemptRow({ attempt, index }: { attempt: LogAttempt; index: number }) {
  return (
    <li className="relative pl-6">
      <span
        className={cn(
          "absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-surface",
          ATTEMPT_STATUS_STYLE[attempt.status ?? ""] ?? "bg-info",
        )}
        aria-hidden="true"
      />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="font-medium text-title">
          #{index + 1} · 第 {attempt.round ?? 1} 轮
        </span>
        <span className="text-muted-foreground">
          {attempt.channel_name || attempt.channel_slug || "未知渠道"} / {attempt.upstream_model || "—"}
        </span>
        <span className="rounded-badge bg-surface-2 px-1.5 py-0.5 text-2xs text-muted-foreground">
          {attemptStatusLabel(attempt.status)}
        </span>
        <span className="text-2xs text-muted-foreground">{formatLatency(attempt.ms)}</span>
      </div>
      {attempt.priority_path && attempt.priority_path.length > 0 ? (
        <p className="mt-1 flex items-center gap-1 text-2xs text-muted-foreground">
          优先级路径
          <ArrowRight className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
          {formatPriorityPath(attempt.priority_path)}
        </p>
      ) : null}
      {attempt.error_code || attempt.message ? (
        <p className="mt-1 text-2xs text-danger-text" title={attempt.error_code ?? undefined}>
          {attemptErrorText(attempt)}
        </p>
      ) : null}
    </li>
  );
}

function ParamsSummary({ params }: { params: Record<string, unknown> }) {
  const entries = Object.entries(params);
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">未记录参数摘要。</p>;
  }
  return (
    <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <Field key={key} label={key}>
          <span className="font-mono text-xs">{displayValue(value)}</span>
        </Field>
      ))}
    </dl>
  );
}

function HeaderTable({ headers, emptyText }: { headers: Record<string, string>; emptyText: string }) {
  const entries = Object.entries(headers);
  if (entries.length === 0) {
    return <p className="px-3 py-2.5 text-xs text-muted-foreground">{emptyText}</p>;
  }
  return (
    <Table className="table-fixed text-left text-xs">
      <TableBody>
        {entries.map(([name, value]) => (
          <TableRow key={name} className="hover:bg-transparent">
            <TableHead
              scope="row"
              className="h-auto w-2/5 break-all bg-surface-2 px-3 py-2 align-top font-mono font-medium text-muted-foreground"
            >
              {name}
            </TableHead>
            <TableCell className="break-all px-3 py-2 font-mono text-title">{value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** 可折叠的头部区块：折叠态不渲染表体，标题行始终可见。 */
function HeadersGroup({
  id,
  title,
  headers,
  emptyText,
  defaultOpen,
}: {
  id: string;
  title: string;
  headers: Record<string, string>;
  emptyText: string;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = `${id}-body`;
  const count = Object.keys(headers).length;
  return (
    <div className="overflow-hidden rounded-control border border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-2"
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
          strokeWidth={1.75}
          aria-hidden="true"
        />
        <span className="text-xs font-medium text-title">{title}</span>
        <span className="text-2xs text-muted-foreground">{count > 0 ? `${count} 项` : "未记录"}</span>
        <span className="ml-auto text-2xs text-muted-foreground">{open ? "收起" : "展开"}</span>
      </button>
      {open ? (
        <div id={bodyId} className="border-t border-border">
          <HeaderTable headers={headers} emptyText={emptyText} />
        </div>
      ) : null}
    </div>
  );
}

/** 请求头 / 响应头区块：下游请求头 + 上游请求头（折叠）+ 上游响应头。 */
function RequestResponseHeaders({ log }: { log: DetailLog }) {
  const attempts = log.attempts as DetailAttempt[];
  const lastWithHeaders = [...attempts]
    .reverse()
    .find((attempt) => Object.keys(attempt.request_headers ?? {}).length > 0);
  const attemptIndex = lastWithHeaders ? attempts.indexOf(lastWithHeaders) : -1;
  return (
    <div className="space-y-2">
      <HeadersGroup
        id="downstream-request-headers"
        title={HEADERS_POLICY.downstreamRequest.label}
        headers={log.request_headers ?? {}}
        emptyText="未记录请求头。"
        defaultOpen={HEADERS_POLICY.downstreamRequest.defaultOpen}
      />
      <HeadersGroup
        id="upstream-request-headers"
        title={HEADERS_POLICY.upstreamRequest.label}
        headers={lastWithHeaders?.request_headers ?? {}}
        emptyText="未记录上游请求头。"
        defaultOpen={HEADERS_POLICY.upstreamRequest.defaultOpen}
      />
      <HeadersGroup
        id="upstream-response-headers"
        title={HEADERS_POLICY.upstreamResponse.label}
        headers={log.response_headers ?? {}}
        emptyText="未记录响应头。"
        defaultOpen={HEADERS_POLICY.upstreamResponse.defaultOpen}
      />
      {attemptIndex >= 0 ? (
        <p className="text-2xs text-muted-foreground">
          上游请求头来自最后一次尝试（#{attemptIndex + 1} · 第 {lastWithHeaders?.round ?? 1} 轮）。
        </p>
      ) : null}
    </div>
  );
}

function DrawerBody({ log, hideSensitive }: { log: RequestLog; hideSensitive: boolean }) {
  const { mode } = useTimezone();
  const detail = log as DetailLog;
  const errorText = log.error_body || log.error_message;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-4">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-badge px-2 py-1 text-2xs font-medium",
            isErrorLog(log)
              ? "bg-danger-soft text-danger-text"
              : "bg-success-soft text-success-text",
          )}
        >
          <span
            className={cn("h-1.5 w-1.5 rounded-full", isErrorLog(log) ? "bg-danger" : "bg-success")}
            aria-hidden="true"
          />
          {log.http_status}
          {log.error_code ? ` · ${errorCodeLabel(log.error_code)}` : ""}
        </span>
        {!isErrorLog(log) && hasFailedAttempts(log) ? (
          <span className="inline-flex items-center gap-1 rounded-badge bg-warning-soft px-2 py-1 text-2xs font-medium text-warning-text">
            <AlertTriangle className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />含{" "}
            {attemptErrorCount(log)} 次失败尝试
          </span>
        ) : null}
        <span className="text-xs text-muted-foreground">
          {log.is_stream ? "流式" : "非流式"} · 创建于 {formatDateTime(log.created_at, mode)}
        </span>
      </div>

      <Section title="基本信息">
        <dl className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
          <Field label="网关请求编号">
            <span className="font-mono text-2xs">{log.request_id}</span>
          </Field>
          <Field label="上游响应 id">
            <span className="font-mono text-2xs">{log.upstream_request_id || "—"}</span>
          </Field>
          <Field label="用户">
            <span title={hideSensitive ? "敏感信息已隐藏" : undefined}>
              {hideSensitive ? MASKED_VALUE : log.user_name || log.user_id || "—"}
            </span>
          </Field>
          <Field label="密钥标识">
            <span title={hideSensitive ? "敏感信息已隐藏" : undefined}>
              {hideSensitive ? MASKED_VALUE : log.api_key_label || "—"}
            </span>
          </Field>
          <Field label="请求模型 / 组合">{log.requested_model || "—"}</Field>
          <Field label="渠道">{log.channel_name || "—"}</Field>
          <Field label="来源">{sourceLabel(log.source)}</Field>
          <Field label="上游模型">{log.upstream_model || "—"}</Field>
          <Field label="响应模型">{log.response_model || "—"}</Field>
        </dl>
      </Section>

      <Section title="用量与耗时">
        <dl className="grid gap-x-4 gap-y-3 sm:grid-cols-3">
          <Field label="输入词元">{log.prompt_tokens}</Field>
          <Field label="输出词元">{log.completion_tokens}</Field>
          <Field label="缓存词元">{log.cached_tokens}</Field>
          <Field label="缓存比例">{formatCachePercent(log.cache_ratio)}</Field>
          <Field label="费用">
            <span title={hideSensitive ? "敏感信息已隐藏" : undefined}>
              {hideSensitive
                ? MASKED_VALUE
                : formatRequestCost(
                    log.cost_usd,
                    log.prompt_tokens + log.completion_tokens + log.cached_tokens,
                    isErrorLog(log),
                  )}
            </span>
          </Field>
          <Field label="吞吐">{formatThroughput(log.throughput_tps)}</Field>
          <Field label="首字耗时">
            {isLatencyMissing(log.ttft_ms, "ttft", isErrorLog(log)) ? (
              <span className="text-2xs text-muted-foreground" title="首字无数据">—</span>
            ) : (
              <span title={latencyDescription(log.ttft_ms, "ttft")}>{formatLatency(log.ttft_ms)}</span>
            )}
          </Field>
          <Field label="首答耗时">
            {!isErrorLog(log) && ttfaMissing(log) ? (
              <span className="text-2xs text-muted-foreground">{TTFA_NO_CONTENT_LABEL}</span>
            ) : isLatencyMissing(log.ttfa_ms, "ttfa", isErrorLog(log)) ? (
              <span className="text-2xs text-muted-foreground" title="首答无数据">—</span>
            ) : (
              <span title={latencyDescription(log.ttfa_ms, "ttfa")}>{formatLatency(log.ttfa_ms)}</span>
            )}
          </Field>
          <Field label="总耗时">
            {isLatencyMissing(log.total_ms, "total") ? (
              <span className="text-2xs text-muted-foreground" title="总耗时无数据">—</span>
            ) : (
              <span title={latencyDescription(log.total_ms, "total")}>{formatLatency(log.total_ms)}</span>
            )}
          </Field>
          <Field label="用量来源">{log.usage_estimated ? "估算" : "上游返回"}</Field>
          <AddressField
            className="sm:col-span-3"
            label="来源地址"
            testId="log-source-address"
            addresses={detail.client_ip ? [detail.client_ip] : []}
          />
          <AddressField
            className="sm:col-span-3"
            label="转发地址"
            testId="log-forwarded-address"
            addresses={detail.forwarded_ips ?? []}
            hint={forwardedSourceText(detail.forwarded_sources)}
          />
        </dl>
      </Section>

      <Section title="参数摘要">
        <ParamsSummary params={log.params} />
      </Section>

      <Section title="Attempts 时间线">
        {log.attempts.length === 0 ? (
          <p className="text-xs text-muted-foreground">没有尝试记录。</p>
        ) : (
          <ol className="space-y-3">
            {log.attempts.map((attempt, index) => (
              <AttemptRow key={`${attempt.channel_model_id ?? "attempt"}-${index}`} attempt={attempt} index={index} />
            ))}
          </ol>
        )}
      </Section>

      <Section title="请求头 / 响应头">
        <RequestResponseHeaders log={detail} />
      </Section>

      {hasFailedAttempts(log) ? (
        <Section title="失败尝试">
          <ul className="space-y-2">
            {failedAttempts(log).map((attempt, index) => (
              <li
                key={`${attempt.channel_model_id ?? "failed"}-${index}`}
                className="rounded-control border border-border bg-surface-2 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                  <span className="font-medium text-title">
                    {attempt.channel_name || attempt.channel_slug || "未知渠道"}
                  </span>
                  <span className="text-muted-foreground">{attempt.upstream_model || "—"}</span>
                  <span className="rounded-badge bg-danger-soft px-1.5 py-0.5 text-2xs text-danger-text">
                    失败
                  </span>
                  {attempt.error_code ? (
                    <span className="text-2xs text-body" title={attempt.error_code}>
                      {errorCodeLabel(attempt.error_code)}
                    </span>
                  ) : null}
                  <span className="ml-auto text-2xs text-muted-foreground">{formatLatency(attempt.ms)}</span>
                </div>
                {attempt.message ? (
                  <p className="mt-1 text-2xs text-danger-text">{attempt.message}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}


      {isErrorLog(log) || log.error_message ? (
        <Section title="脱敏错误原文">
          {errorText ? (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-surface-2 p-3 font-mono text-2xs leading-5 text-body">
              {errorText}
            </pre>
          ) : (
            <p className="text-xs text-muted-foreground">
              未捕获错误正文，当前正文日志策略可能未记录该请求。
            </p>
          )}
        </Section>
      ) : null}

      {log.rewrite_applied || log.rewrite_skipped ? (
        <Section title="改写">
          <p className="text-xs text-muted-foreground">
            {log.rewrite_applied ? "响应/请求改写已应用。" : "改写未应用。"}
            {log.rewrite_skipped ? ` 跳过原因：${rewriteSkippedLabel(log.rewrite_skipped)}` : ""}
          </p>
        </Section>
      ) : null}
    </>
  );
}

export function LogDetailDrawer({
  requestId,
  onClose,
  hideSensitive = false,
}: {
  requestId: string | null;
  onClose: () => void;
  hideSensitive?: boolean;
}) {
  const detailQuery = useQuery({
    queryKey: ["log-detail", requestId],
    queryFn: () => getLog(requestId as string),
    enabled: Boolean(requestId),
  });
  if (!requestId) return null;

  return (
    <Sheet
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        aria-describedby={undefined}
        className="flex w-full max-w-3xl flex-col gap-0 overflow-y-auto overscroll-contain border-l border-border bg-surface p-0 shadow-pop sm:max-w-3xl"
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border bg-surface px-5 py-4 backdrop-blur-sm">
          <div className="min-w-0">
            <SheetTitle className="text-sm font-semibold text-title">请求详情</SheetTitle>
            <p className="mt-0.5 truncate font-mono text-2xs text-muted-foreground">{requestId}</p>
          </div>
          <SheetClose asChild>
            <button
              type="button"
              aria-label="关闭详情"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface-2 hover:text-title"
            >
              <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          </SheetClose>
        </div>

        {detailQuery.isLoading ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-2sm text-muted-foreground">
            <Spinner aria-hidden="true" />
            正在加载详情…
          </div>
        ) : detailQuery.isError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="text-2sm text-danger-text">{describeApiError(detailQuery.error)}</p>
            <Button variant="outline" size="sm" onClick={() => detailQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        ) : detailQuery.data ? (
          <DrawerBody log={detailQuery.data} hideSensitive={hideSensitive} />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
