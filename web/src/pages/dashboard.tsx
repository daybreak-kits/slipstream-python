import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Clock3,
  Coins,
  DatabaseZap,
  Gauge,
  Hourglass,
  Percent,
  Timer,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { TrendCard } from "@/components/dashboard/trend-card";
import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { Empty } from "@/components/ui/empty";
import { Field, FieldLabel } from "@/components/ui/field";
import { IconBadge } from "@/components/ui/icon-badge";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { TimeRangePicker } from "@/components/ui/time-range-picker";
import { describeApiError } from "@/lib/channels";
import { SELECT_ALL_VALUE } from "@/lib/select-sentinels";
import {
  dashboardKpis,
  DEFAULT_REFRESH_INTERVAL_MS,
  getDashboardSummary,
  getDashboardTimeseries,
  heatmapGrid,
  heatmapOpacity,
  REFRESH_INTERVAL_OPTIONS,
  refreshIntervalLabel,
  TREND_DEFAULT_DIMENSION,
  useAutoRefresh,
  WEEKDAY_LABELS,
  type DashboardFilters,
  type DashboardKpi,
  type DashboardKpiKey,
  type DashboardSummary,
  type DashboardTimeseries,
  type TrendDimension,
} from "@/lib/dashboard";
import { formatMs, getLogFilterOptions } from "@/lib/logs";
import { useTimezone } from "@/lib/time";
import { formatInterval, rangeBounds, timeRangeLabel, type TimeRange } from "@/lib/time-range";
import { cn } from "@/lib/utils";

const KPI_ICONS: Record<DashboardKpiKey, LucideIcon> = {
  requests: Activity,
  prompt_tokens: ArrowDownToLine,
  completion_tokens: ArrowUpFromLine,
  cached_tokens: DatabaseZap,
  cache_hit_rate: Percent,
  cost: Coins,
  error_rate: AlertTriangle,
  rpm: Gauge,
  tpm: Timer,
  p95_ttft: Clock3,
  p95_total: Hourglass,
};

/** 合并卡的指标配对：每张主卡两个指标，成本单独一张卡。 */
const KPI_PAIRS: Array<[DashboardKpiKey, DashboardKpiKey]> = [
  ["requests", "error_rate"],
  ["prompt_tokens", "completion_tokens"],
  ["cached_tokens", "cache_hit_rate"],
  ["rpm", "tpm"],
];

/** 合并卡里的一个指标：图标 + 名称一行，数值一行，口径提示一行。 */
function KpiMetric({ kpi }: { kpi: DashboardKpi }) {
  const Icon = KPI_ICONS[kpi.key];
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      {/* 图标与名称的间距收到 gap-1：1440 宽五列时「缓存命中率」只差 1px 就会截成「缓存命…」。 */}
      <div className="flex min-w-0 items-center gap-1">
        <IconBadge icon={Icon} tone={kpi.tone} size="sm" />
        <span className="truncate text-2xs font-medium text-muted-foreground">{kpi.label}</span>
      </div>
      <p className="truncate text-metric font-semibold tracking-[-0.01em] tabular-nums text-title">
        {kpi.value}
      </p>
      {kpi.detail ? (
        <TooltipProvider delayDuration={120}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="flex w-full min-w-0 items-center text-left text-2xs leading-tight text-faint underline decoration-dotted underline-offset-2 hover:text-muted-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                aria-label={`${kpi.label}详情`}
              >
                <span className="truncate">{kpi.hint}</span>
              </button>
            </TooltipTrigger>
            <TooltipContent
              side="bottom"
              align="start"
              sideOffset={6}
              className="w-80 space-y-2 border border-border bg-surface p-3 text-left text-2xs leading-snug text-body shadow-pop"
            >
              <p className="font-semibold text-title">{kpi.detail.title}</p>
              <dl className="space-y-1.5">
                {kpi.detail.rows.map((row) => (
                  <div key={row.label} className="flex items-start justify-between gap-3">
                    <dt className="shrink-0 text-muted-foreground">{row.label}</dt>
                    <dd className="min-w-0 text-right tabular-nums text-title">{row.value}</dd>
                  </div>
                ))}
              </dl>
              <p className="border-t border-border pt-2 text-faint">{kpi.detail.note}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        <p className="line-clamp-2 text-2xs leading-tight text-faint" title={kpi.hint}>
          {kpi.hint}
        </p>
      )}
    </div>
  );
}

/**
 * 一张合并卡：同一块留白里放两个相关指标，中间一条竖细线分隔。
 * 用户 2026-11-14 反馈「大屏一行 6 个格子太空」，所以不再一格一个指标。
 */
function KpiPairCard({
  primary,
  secondary,
  className,
}: {
  primary: DashboardKpi;
  secondary?: DashboardKpi;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 items-stretch gap-3 bg-surface p-3", className)}>
      <KpiMetric kpi={primary} />
      {secondary ? (
        <>
          <span aria-hidden="true" className="w-px shrink-0 self-stretch bg-border" />
          <KpiMetric kpi={secondary} />
        </>
      ) : null}
    </div>
  );
}

function LatencyPercentiles({ latency }: { latency: DashboardSummary["latency"] }) {
  const rows = [
    { label: "首字", key: "ttft" as const, color: "var(--chart-1)" },
    { label: "首答", key: "ttfa" as const, color: "var(--chart-4)" },
    { label: "尾字", key: "ttlt" as const, color: "var(--chart-7)" },
    { label: "总耗时", key: "total" as const, color: "var(--chart-2)" },
  ];
  const percentiles = ["p50", "p90", "p95", "p99"] as const;
  const values = rows.flatMap((row) => {
    const metric = latency[row.key];
    if (!metric) return [];
    return percentiles
      .map((percentile) => metric[percentile])
      .filter((value): value is number => value !== null);
  });
  const max = Math.max(...values, 1);
  if (!values.length) {
    return (
      <p className="rounded-control bg-surface-2 px-3 py-4 text-xs text-muted-foreground">
        样本不足（{latency.samples}/5），暂不计算分位数。累计至少 5 条带首字耗时的流式请求后自动展示。
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <p className={cn("text-2xs", latency.samples < 5 ? "text-warning-text" : "text-muted-foreground")}>
        成对样本 {latency.samples} 条（需同时具备首字耗时与总耗时）
        {latency.samples < 5 ? `，样本不足（${latency.samples}/5）` : ""}
      </p>
      <div className="flex flex-wrap items-center gap-4 text-2xs text-muted-foreground">
        {rows.map((row) => (
          <span key={row.key} className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-xs" style={{ backgroundColor: row.color }} />
            {row.label}
          </span>
        ))}
      </div>
      {/* 分位名只出现一次（表头），避免每组都重复 P50/P90/P95/P99 标签。 */}
      <div className="grid grid-cols-[44px_repeat(4,minmax(0,1fr))] items-center gap-2 border-b border-border pb-1 text-2xs font-medium uppercase tracking-[0.08em] text-faint">
        <span aria-hidden="true" />
        {percentiles.map((percentile) => (
          <span key={percentile} className="text-right">
            {percentile}
          </span>
        ))}
      </div>
      {rows.map((row) => (
        <div key={row.key} className="grid grid-cols-[44px_repeat(4,minmax(0,1fr))] items-center gap-2">
          <span className="text-2xs text-muted-foreground">{row.label}</span>
          {percentiles.map((percentile) => {
            const value = latency[row.key]?.[percentile] ?? null;
            return (
              <div key={percentile} className="min-w-0">
                <div className="flex items-center justify-end text-2xs text-faint">
                  <span className="tabular-nums text-body">{formatMs(value)}</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2">
                  {value === null ? (
                    <div className="h-full w-0" />
                  ) : (
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${Math.max(2, (value / max) * 100)}%`, backgroundColor: row.color }}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

const HEATMAP_WEEKDAY_INDEX: Record<string, number> = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};

const heatmapDateFormatters = new Map<string, Intl.DateTimeFormat>();

function heatmapDateFormatter(timezone: string): Intl.DateTimeFormat {
  const cached = heatmapDateFormatters.get(timezone);
  if (cached) return cached;
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  });
  heatmapDateFormatters.set(timezone, formatter);
  return formatter;
}

function formatUtcDate(ms: number): string {
  const date = new Date(ms);
  return `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

/**
 * 近 7 天热力图是滚动的 7×24 窗口：后端只给 (isoweekday, hour)，不给日期。
 * 每个星期取窗口最后一天往前最近的一次同星期日期；这样每行恰好一个日期，
 * 也不会因为跨 8 个自然日而让同一星期对应两个日期。
 */
function buildHeatmapCalendar(endMs: number, timezone: string) {
  const parts = heatmapDateFormatter(timezone).formatToParts(new Date(endMs));
  const values: Record<string, string> = {};
  for (const part of parts) {
    if (part.type !== "literal") values[part.type] = part.value;
  }
  const endDay = Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day));
  const endWeekday = HEATMAP_WEEKDAY_INDEX[values.weekday] ?? 0;
  const labels = Array.from({ length: 7 }, (_, index) => {
    const weekday = index + 1; // 1=周一 … 7=周日，与 WEEKDAY_LABELS 对齐。
    const diff = (endWeekday - weekday + 7) % 7;
    return formatUtcDate(endDay - diff * 86_400_000);
  });
  return {
    labels,
    range: `${formatUtcDate(endDay - 6 * 86_400_000)} – ${formatUtcDate(endDay)}`,
  };
}

function timezoneLabel(timezone: string): string {
  return timezone === "Asia/Shanghai" || timezone === "Asia/Chongqing" ? "北京时间" : timezone;
}

function padHour(hour: number): string {
  return String(hour).padStart(2, "0");
}

function Heatmap({ data }: { data: DashboardTimeseries }) {
  const calendar = buildHeatmapCalendar(data.end_ms || Date.now(), data.timezone);
  const grid = heatmapGrid(data.heatmap);
  const max = Math.max(1, ...grid.map((row) => Math.max(...row)));
  return (
    <div>
      <div className="grid grid-cols-[24px_repeat(24,minmax(0,1fr))] gap-[2px]">
        {grid.map((row, index) => (
          <div key={WEEKDAY_LABELS[index]} className="contents">
            <span className="text-2xs leading-4 text-faint">{WEEKDAY_LABELS[index]}</span>
            {row.map((value, hour) => (
              <span
                key={hour}
                title={`${calendar.labels[index]} ${padHour(hour)}:00-${padHour(hour + 1)}:00 周${WEEKDAY_LABELS[index]} · ${value} 次`}
                className="h-4 rounded-[2px] bg-primary"
                style={{ opacity: heatmapOpacity(value, max) }}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-2xs text-faint">
        <span>0 次</span>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
          <span
            key={ratio}
            className="h-3 w-3 rounded-[2px] bg-primary"
            style={{ opacity: heatmapOpacity(ratio * max, max) }}
          />
        ))}
        <span>峰值 {max} 次/格</span>
      </div>
    </div>
  );
}

export function DashboardPage() {
  const { mode } = useTimezone();
  const [range, setRange] = useState<TimeRange>("today");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  /** 自动刷新间隔只存在内存里：不改 localStorage 键（存储键由 Lead 统一治理）。 */
  const [refreshMs, setRefreshMs] = useState(DEFAULT_REFRESH_INTERVAL_MS);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const bounds = rangeBounds(range, customStart, customEnd);
  // 触发按钮的区间文本跟随页面时区开关，和图表 tooltip 的口径一致。
  const intervalText = formatInterval(bounds.start, bounds.end, mode === "utc" ? "utc" : "local");
  const customWindow = {
    startMs: customStart ? new Date(customStart).getTime() : undefined,
    endMs: customEnd ? new Date(customEnd).getTime() : undefined,
  };
  const window = range === "custom" ? customWindow : undefined;
  const windowReady =
    range !== "custom" ||
    (window?.startMs !== undefined &&
      window.endMs !== undefined &&
      Number.isFinite(window.startMs) &&
      Number.isFinite(window.endMs) &&
      window.startMs < window.endMs);

  const [channel, setChannel] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<"" | "success" | "error">("");

  const filters: DashboardFilters = { channel, model, status };
  /**
   * 趋势维度进 URL：图型已经用 ?tab= 序列化，维度沿用同一套惯例（刷新后回到同一视图）。
   * 它同时进 timeseries 的请求参数与 query key —— 切维度就是换一份数据，不是重画。
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const dimensionParam = searchParams.get("dimension");
  /**
   * URL 不带维度时默认「综合模型」（combo）：它同时是发给后端的 dimension 值，
   * 所以默认视图与后端返回的分解口径始终一致。
   */
  const dimension: TrendDimension =
    dimensionParam === "combo" || dimensionParam === "upstream" || dimensionParam === "channel"
      ? dimensionParam
      : TREND_DEFAULT_DIMENSION;
  const selectDimension = (next: TrendDimension) => {
    const params = new URLSearchParams(searchParams);
    // 默认维度不写进 URL：清掉参数即回到默认视图，链接保持干净。
    if (next === TREND_DEFAULT_DIMENSION) params.delete("dimension");
    else params.set("dimension", next);
    setSearchParams(params, { replace: true });
  };
  const filterOptionsQuery = useQuery({
    queryKey: ["log-filter-options"],
    queryFn: () => getLogFilterOptions(),
  });
  const summaryQuery = useQuery({
    queryKey: [
      "dashboard-summary",
      range,
      timezone,
      window?.startMs,
      window?.endMs,
      channel,
      model,
      status,
    ],
    queryFn: () => getDashboardSummary(range, timezone, window, filters),
    enabled: windowReady,
  });
  /** 趋势与热力图共用同一 query key 前缀：range=7d 时两者 key 完全相同，热力图直接命中趋势缓存。 */
  const timeseriesKey = [
    "dashboard-timeseries",
    timezone,
    channel,
    model,
    status,
  ] as const;
  const tsQuery = useQuery({
    queryKey: [...timeseriesKey, range, window?.startMs, window?.endMs, dimension],
    queryFn: () => getDashboardTimeseries(range, timezone, window, filters, dimension),
    enabled: windowReady,
  });
  // 热力图固定取「近 7 天」：后端只统计窗口内的 (星期, 小时)，跟随 today 时只会剩今天一行有值。
  // range=7d 时主趋势的 timeseries 就是同一窗口、同一份 heatmap 数据，直接复用，少打一次接口。
  const heatmapQuery = useQuery({
    queryKey: [...timeseriesKey, "7d", undefined, undefined],
    queryFn: () => getDashboardTimeseries("7d", timezone, undefined, filters),
    enabled: windowReady && range !== "7d",
  });
  const data = summaryQuery.data;
  const ts = tsQuery.data;
  const heatmapTs = range === "7d" && ts ? ts : heatmapQuery.data;
  const heatmapTimezone = heatmapTs?.timezone || timezone;
  const heatmapCalendar = heatmapTs
    ? buildHeatmapCalendar(heatmapTs.end_ms || Date.now(), heatmapTimezone)
    : null;
  const hasFilters = Boolean(channel || model || status);
  const clearFilters = () => {
    setChannel("");
    setModel("");
    setStatus("");
  };
  useAutoRefresh(() => {
    void summaryQuery.refetch();
    void tsQuery.refetch();
    void heatmapQuery.refetch();
  }, refreshMs);

  // P95 只在「延迟分位」卡与面积图里出现，顶部不再重复两块磁贴。
  const kpis = dashboardKpis(data).filter(
    (kpi) => kpi.key !== "p95_ttft" && kpi.key !== "p95_total",
  );
  /** KPI 按 key 索引：合并卡按配对取值，缺失时该卡不渲染。 */
  const kpiByKey = new Map(kpis.map((kpi) => [kpi.key, kpi]));
  const costKpi = kpiByKey.get("cost");

  return (
    <div className="animate-fade-up">
      <PageHeader
        className="items-end"
        title="数据看板"
        description={`${timeRangeLabel(range, customStart, customEnd)} · 按 ${timezone} 分桶。`}
        actions={
          <>
            <TimeRangePicker
              range={range}
              customStart={customStart}
              customEnd={customEnd}
              interval={intervalText}
              onChange={(nextRange, nextStart, nextEnd) => {
                setRange(nextRange);
                setCustomStart(nextStart);
                setCustomEnd(nextEnd);
              }}
              className="w-full sm:w-88"
            />
            <Select value={String(refreshMs)} onValueChange={(next) => setRefreshMs(Number(next))}>
              <SelectTrigger
                aria-label="自动刷新间隔"
                className="h-9 w-full gap-2 rounded-control border border-border-strong bg-surface px-3 text-xs font-medium text-title shadow-none transition-colors hover:bg-surface-2 focus-visible:border-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg sm:w-52"
              >
                <span className="shrink-0 text-2xs font-normal text-faint">刷新间隔</span>
                <SelectValue className="flex-1 text-left" />
              </SelectTrigger>
              <SelectContent>
                {REFRESH_INTERVAL_OPTIONS.map((item) => (
                  <SelectItem key={item.value} value={String(item.value)}>
                    {refreshIntervalLabel(item.value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
      />

      <div className="mt-3 flex flex-wrap items-end gap-2 rounded-control border border-border bg-surface px-3 py-2 text-xs">
        <Field className="w-auto min-w-0 gap-1">
          <FieldLabel htmlFor="dashboard-channel" className="text-2xs font-normal text-faint">
            渠道
          </FieldLabel>
          <Select
            value={channel || SELECT_ALL_VALUE}
            onValueChange={(next) => setChannel(next === SELECT_ALL_VALUE ? "" : next)}
          >
            <SelectTrigger
              id="dashboard-channel"
              aria-label="按渠道筛选"
              className="h-8 min-w-36 rounded-sm border-border bg-surface-2 px-2 text-xs text-body"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SELECT_ALL_VALUE}>全部渠道</SelectItem>
              {(filterOptionsQuery.data?.channels ?? []).map((item) => (
                <SelectItem key={item} value={item}>
                  {item}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field className="w-auto min-w-0 gap-1">
          <FieldLabel htmlFor="dashboard-model" className="text-2xs font-normal text-faint">
            模型 / 组合
          </FieldLabel>
          <Select
            value={model || SELECT_ALL_VALUE}
            onValueChange={(next) => setModel(next === SELECT_ALL_VALUE ? "" : next)}
          >
            <SelectTrigger
              id="dashboard-model"
              aria-label="按模型或组合筛选"
              className="h-8 min-w-44 rounded-sm border-border bg-surface-2 px-2 text-xs text-body"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SELECT_ALL_VALUE}>全部模型 / 组合</SelectItem>
              {(filterOptionsQuery.data?.models ?? []).map((item) => (
                <SelectItem key={item} value={item}>
                  {item}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field className="w-auto min-w-0 gap-1">
          <FieldLabel htmlFor="dashboard-status" className="text-2xs font-normal text-faint">
            状态
          </FieldLabel>
          <Select
            value={status || SELECT_ALL_VALUE}
            onValueChange={(next) =>
              setStatus((next === SELECT_ALL_VALUE ? "" : next) as "" | "success" | "error")
            }
          >
            <SelectTrigger
              id="dashboard-status"
              aria-label="按状态筛选"
              className="h-8 rounded-sm border-border bg-surface-2 px-2 text-xs text-body"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SELECT_ALL_VALUE}>全部状态</SelectItem>
              <SelectItem value="success">成功</SelectItem>
              <SelectItem value="error">失败</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        {hasFilters ? (
          <button type="button" onClick={clearFilters} className="h-8 rounded-sm px-2 text-xs text-primary hover:underline">
            清除筛选
          </button>
        ) : (
          <span className="pb-2 text-2xs text-faint">筛选会同时作用于指标、趋势、分流、热力图与错误分布</span>
        )}
      </div>

      {summaryQuery.isError ? (
        <Alert variant="destructive" className="mt-5">
          <span className="flex items-center justify-between gap-3">
            {describeApiError(summaryQuery.error)}
            <button type="button" className="shrink-0 underline" onClick={() => summaryQuery.refetch()}>
              重试
            </button>
          </span>
        </Alert>
      ) : null}
      {tsQuery.isError ? (
        <Alert variant="warning" className="mt-3">
          <span className="flex items-center justify-between gap-3">
            趋势数据加载失败：{describeApiError(tsQuery.error)}
            <button type="button" className="shrink-0 underline" onClick={() => tsQuery.refetch()}>
              重试
            </button>
          </span>
        </Alert>
      ) : null}

      {!windowReady ? (
        <Card className="mt-6 p-6 text-center text-2sm text-muted-foreground">
          请选择有效的起止时间后查看数据。
        </Card>
      ) : summaryQuery.isLoading ? (
        <div className="mt-10 flex items-center justify-center gap-2 text-2sm text-muted-foreground">
          <Spinner size="md" label="正在加载指标" /> 正在加载指标…
        </div>
      ) : (data?.requests ?? 0) === 0 ? (
        <Empty
          className="mt-6 rounded-card border border-border bg-surface"
          icon={Activity}
          title={hasFilters ? "当前筛选条件下没有请求" : "所选时间范围内还没有请求"}
          description={
            hasFilters
              ? "换一个渠道、模型或状态，或清除筛选后查看全部数据。"
              : "创建渠道或组合后，用用户密钥发起一次调用即可看到趋势。"
          }
          action={
            <div className="flex justify-center gap-3 text-2sm">
              {hasFilters ? (
                <button type="button" className="text-primary hover:underline" onClick={clearFilters}>
                  清除筛选
                </button>
              ) : (
                <>
                  <Link className="text-primary hover:underline" to="/channels">去创建渠道</Link>
                  <Link className="text-primary hover:underline" to="/combos">去创建组合</Link>
                </>
              )}
            </div>
          }
        />
      ) : (
        <>
          {/* 4 张合并卡（每张两个指标）+ 成本单卡；大屏一行 5 张，不再拆成一排小格子。 */}
          <Card className="mt-6 overflow-hidden">
            <div className="grid grid-cols-1 gap-px bg-border sm:grid-cols-2 xl:grid-cols-5">
              {KPI_PAIRS.map(([primary, secondary]) => {
                const left = kpiByKey.get(primary);
                const right = kpiByKey.get(secondary);
                if (!left && !right) return null;
                return (
                  <KpiPairCard key={primary} primary={(left ?? right)!} secondary={left ? right : undefined} />
                );
              })}
              {costKpi ? <KpiPairCard primary={costKpi} className="sm:col-span-2 xl:col-span-1" /> : null}
            </div>
          </Card>

          <TrendCard
            data={ts}
            range={range}
            timezone={timezone}
            timeWindow={window}
            filters={filters}
            windowReady={windowReady}
            refreshMs={refreshMs}
            dimension={dimension}
            onDimensionChange={selectDimension}
          />

          {data?.latency ? (
            <Card className="mt-4 p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold text-title">延迟分位</h2>
                <span className="text-2xs text-faint">首字 / 首答 / 尾字 / 总耗时 · 成对样本 · 单位毫秒</span>
              </div>
              <div className="mt-4">
                <LatencyPercentiles latency={data.latency} />
              </div>
            </Card>
          ) : null}

          <div className="mt-4 grid gap-4 lg:grid-cols-[1.6fr_1fr]">
            <Card className="p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold text-title">小时热力图</h2>
                <span className="text-2xs text-faint">近 7 天</span>
              </div>
              <p className="mt-1 text-2xs text-faint">
                {heatmapCalendar ? `近 7 天：${heatmapCalendar.range}，` : "近 7 天，"}
                按{timezoneLabel(heatmapTimezone)}的星期与小时统计；固定取最近 7 天，不随上方时间范围收窄（否则只有今天一行有数据）。
              </p>
              <div className="mt-3 overflow-x-auto">{heatmapTs ? <Heatmap data={heatmapTs} /> : null}</div>
            </Card>
            <Card className="p-5">
              <h2 className="text-sm font-semibold text-title">错误类型分布</h2>
              {ts?.error_distribution.length ? (
                <div className="mt-3 space-y-2">
                  {ts.error_distribution.map((item) => {
                    const warning = /TIMEOUT|RATE|429/i.test(item.code);
                    return (
                      <div key={item.code} className="flex items-center justify-between gap-2 text-xs">
                        <span
                          className={cn(
                            "inline-flex min-w-0 items-center gap-1.5 rounded-badge px-2 py-0.5 text-2xs font-medium",
                            warning
                              ? "bg-warning-soft text-warning-text"
                              : "bg-danger-soft text-danger-text",
                          )}
                        >
                          <span aria-hidden="true">{warning ? "△" : "●"}</span>
                          <span className="truncate">{item.code}</span>
                        </span>
                        <span className="text-muted-foreground">{item.count}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="mt-3 text-xs text-muted-foreground">所选范围内没有错误。</p>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
