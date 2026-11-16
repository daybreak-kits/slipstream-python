import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { FlowCard } from "@/components/dashboard/flow-card";
import { Card } from "@/components/ui/card";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { assignCategoryColorsByName } from "@/lib/chart-colors";
import {
  formatTrendAxis,
  formatWeightedP95,
  stackTrendSegments,
  TREND_DEFAULT_METRIC,
  TREND_DIMENSIONS,
  TREND_DIMENSION_LABELS,
  TREND_LATENCY_OTHER_LABEL,
  TREND_LATENCY_TOP_MEMBERS,
  TREND_OTHER_COLOR,
  TREND_TOOLTIP_WIDTH,
  trendAreaPaths,
  trendAxisLabelIndexes,
  trendChartGeometry,
  trendLinePaths,
  trendMembers,
  trendScale,
  trendYAxisTickCount,
  weightedAverageP95,
  type DashboardFilters,
  type DashboardTimeseries,
  type DashboardWindow,
  type TimeseriesModelPoint,
  type TimeseriesPoint,
  type TrendChartGeometry,
  type TrendDimension,
  type TrendMetric,
} from "@/lib/dashboard";
import { formatDateTimeParts, useTimezone } from "@/lib/time";
import { cn } from "@/lib/utils";

/** 三种图型：柱形（按成员堆叠）、面积（成员多条曲线，不堆叠，含 P95）、分流（五阶段 Sankey）。 */
type TrendChartType = "bar" | "area" | "flow";

interface MetricOption {
  value: TrendMetric;
  label: string;
}

/** 柱形图只画可加性指标：P95 是分位数，堆叠没有意义（用户裁定 P95 归面积图）。 */
const BAR_METRICS: MetricOption[] = [
  { value: "requests", label: "请求" },
  { value: "tokens", label: "词元" },
  { value: "cost", label: "成本" },
];

/** 面积图默认不堆叠多线：加性指标按成员各画一条曲线，P95 按成员各画一条分位线。 */
const AREA_METRICS: MetricOption[] = [...BAR_METRICS, { value: "latency", label: "P95 延迟" }];

const CHART_TABS: ReadonlyArray<{ value: TrendChartType; label: string }> = [
  { value: "bar", label: "柱形图" },
  { value: "area", label: "面积图" },
  { value: "flow", label: "分流图" },
];

/** 图区在卡片里至少占这么高；更大空间由 flex-1 撑满（见 useChartBox）。 */
const TREND_CHART_MIN_HEIGHT = 320;

/** 没有数据/全部成员被隐藏时的占位：保持与图区同高，卡片不会因为空态忽然塌下去。 */
function ChartEmpty({ text }: { text: string }) {
  return (
    <div className="flex min-h-50 flex-1 items-center justify-center rounded-control border border-dashed border-border">
      <p className="text-xs text-muted-foreground">{text}</p>
    </div>
  );
}

function metricOptions(chartType: TrendChartType): MetricOption[] {
  return chartType === "area" ? AREA_METRICS : BAR_METRICS;
}

function metricLabelOf(metric: TrendMetric): string {
  return [...AREA_METRICS, ...BAR_METRICS].find((item) => item.value === metric)?.label ?? "请求";
}

/** URL 里的 ?tab= 兼容旧值：旧链接的 trend 落到柱形图，flow 仍是分流图。 */
function parseChartType(value: string | null): TrendChartType {
  if (value === "area" || value === "flow") return value;
  return "bar";
}

/** 横轴说明文案：小时粒度保留 MM-DD HH:mm，天粒度只留 MM-DD。 */
function formatAxisMoment(
  ms: number | undefined,
  mode: "local" | "utc",
  granularity: "hour" | "day",
): string {
  const parts = formatDateTimeParts(ms, mode);
  if (!parts) return "—";
  const day = parts.date.slice(5);
  return granularity === "hour" ? day + " " + parts.time.slice(0, 5) : day;
}

/** 每根柱子/每个采样点下方的时间标签：小时只留 HH:mm，天只留 MM-DD。 */
function formatAxisTick(
  ms: number | undefined,
  mode: "local" | "utc",
  granularity: "hour" | "day",
): string {
  const parts = formatDateTimeParts(ms, mode);
  if (!parts) return "—";
  return granularity === "hour" ? parts.time.slice(0, 5) : parts.date.slice(5);
}

/** 成员隐藏状态按「维度 + 成员名」分开记：同名成员出现在两个维度时互不影响。 */
function memberKey(dimension: TrendDimension, name: string): string {
  return dimension + "::" + name;
}

function memberValue(member: TimeseriesModelPoint | undefined, metric: TrendMetric): number {
  if (!member) return 0;
  if (metric === "tokens") return member.prompt_tokens + member.completion_tokens;
  if (metric === "cost") return member.cost_usd;
  if (metric === "latency") return member.p95_total_ms ?? 0;
  return member.requests;
}

interface MemberRank {
  name: string;
  requests: number;
}

/** 整段区间的成员榜：主题按总请求数降序，保证跨桶的曲线/堆叠顺序稳定。 */
function rankMembers(points: readonly TimeseriesPoint[], dimension: TrendDimension): MemberRank[] {
  const totals = new Map<string, number>();
  for (const point of points) {
    for (const member of trendMembers(point, dimension)) {
      const requests = Number.isFinite(member.requests) ? Math.max(0, member.requests) : 0;
      totals.set(member.name, (totals.get(member.name) ?? 0) + requests);
    }
  }
  return [...totals.entries()]
    .map(([name, requests]) => ({ name, requests }))
    .sort((left, right) => right.requests - left.requests || left.name.localeCompare(right.name));
}

interface TrendSeries {
  name: string;
  color: string;
  /** 合并出来的「其他 · 加权 P95」：颜色中性、顺序固定排在最后。 */
  other?: boolean;
  /** 每个桶的纵值：加性指标缺失补 0；P95 无有效样本留 null（断线，不掉到 0）。 */
  values: Array<number | null>;
  /** 每个桶的请求数：P95 加权与 tooltip 口径共用。 */
  requests: number[];
}

/**
 * 单一维度的系列数据。
 * 加性指标：每个成员一条序列，区间内全量参与堆叠/曲线（不合并成「其他」）。
 * P95：成员分位不可相加，取整段请求数 Top6 各一条分位线，其余成员按请求数加权合并成一条。
 */
function buildSeries(
  points: readonly TimeseriesPoint[],
  dimension: TrendDimension,
  metric: TrendMetric,
  colors: ReadonlyMap<string, string>,
): TrendSeries[] {
  const ranked = rankMembers(points, dimension);
  if (metric !== "latency") {
    return ranked.map((item) => {
      const cells = points.map((point) => {
        const member = trendMembers(point, dimension).find((candidate) => candidate.name === item.name);
        return {
          value: memberValue(member, metric),
          requests: member?.requests ?? 0,
        };
      });
      return {
        name: item.name,
        color: colors.get(item.name) ?? TREND_OTHER_COLOR,
        values: cells.map((cell) => cell.value),
        requests: cells.map((cell) => cell.requests),
      };
    });
  }

  const top = ranked.slice(0, TREND_LATENCY_TOP_MEMBERS);
  const topNames = top.map((item) => item.name);
  const hasOther = ranked.length > TREND_LATENCY_TOP_MEMBERS;
  const latencyOf = (bucket: readonly TimeseriesModelPoint[], name: string) => {
    const member = bucket.find((candidate) => candidate.name === name);
    const value = member?.p95_total_ms ?? null;
    return { value: value !== null && value > 0 ? value : null, requests: member?.requests ?? 0 };
  };
  const series: TrendSeries[] = top.map((item) => {
    const cells = points.map((point) => latencyOf(trendMembers(point, dimension), item.name));
    return {
      name: item.name,
      color: colors.get(item.name) ?? TREND_OTHER_COLOR,
      values: cells.map((cell) => cell.value),
      requests: cells.map((cell) => cell.requests),
    };
  });
  if (!hasOther) return series;
  const otherCells = points.map((point) => {
    const rest = trendMembers(point, dimension).filter((member) => !topNames.includes(member.name));
    const merged = weightedAverageP95(
      rest.map((member) => ({ value: member.p95_total_ms, requests: member.requests })),
    );
    return {
      value: merged > 0 ? merged : null,
      requests: rest.reduce((sum, member) => sum + member.requests, 0),
    };
  });
  return [
    ...series,
    {
      name: TREND_LATENCY_OTHER_LABEL,
      color: TREND_OTHER_COLOR,
      other: true,
      values: otherCells.map((cell) => cell.value),
      requests: otherCells.map((cell) => cell.requests),
    },
  ];
}

interface LegendRow {
  name: string;
  color: string;
  other: boolean;
  /** 加性指标为区间合计；P95 为请求数加权分位（null 表示没有有效样本）。 */
  value: number | null;
  requests: number;
  text: string;
  /** 加性指标的占比；P95 不能求占比，留 null。 */
  share: number | null;
}

function legendRowsOf(series: TrendSeries[], metric: TrendMetric): LegendRow[] {
  const rows = series.map<LegendRow>((item) => {
    if (metric === "latency") {
      const requests = item.requests.reduce((sum, value) => sum + value, 0);
      const merged = weightedAverageP95(
        item.values.map((value, index) => ({ value, requests: item.requests[index] })),
      );
      return {
        name: item.name,
        color: item.color,
        other: Boolean(item.other),
        value: merged > 0 ? merged : null,
        requests,
        text: formatWeightedP95(merged > 0 ? merged : null, requests),
        share: null,
      };
    }
    const value = item.values.reduce<number>((sum, current) => sum + (current ?? 0), 0);
    return {
      name: item.name,
      color: item.color,
      other: Boolean(item.other),
      value,
      requests: item.requests.reduce((sum, current) => sum + current, 0),
      text: formatTrendAxis(value, metric),
      share: value,
    };
  });
  const total = rows.reduce((sum, row) => sum + (row.share ?? 0), 0);
  return rows.map((row) => ({
    ...row,
    share: row.share === null || total <= 0 ? null : row.share / total,
  }));
}

/**
 * 图区尺寸：用 ResizeObserver 量容器的真实像素宽高，坐标与刻度全部按真实尺寸重算。
 * 不用 preserveAspectRatio="none" 拉伸：那会把文字与描边一起拉变形；这里 viewBox 就是
 * 容器的像素尺寸，1:1 映射，窗口变高变矮都只是重新算坐标。
 */
function useChartBox() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => {
      const rect = node.getBoundingClientRect();
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      if (!(width > 0) || !(height > 0)) return;
      setBox((previous) =>
        previous.width === width && previous.height === height ? previous : { width, height },
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, box };
}

/** 坐标几何：宽高都来自容器实测值，桶数只决定槽宽。 */
function useChartGeometry(bucketCount: number) {
  const { ref, box } = useChartBox();
  const geometry = useMemo(
    () => trendChartGeometry(box.width, box.height, bucketCount),
    [box.width, box.height, bucketCount],
  );
  return { ref, geometry };
}

/** 纵轴网格 + 时间轴文案：柱形图与面积图共用，保证刻度字号与抽稀口径一致。 */
function TrendAxes({
  geometry,
  startMs,
  endMs,
  granularity,
  metric,
  points,
  ratio,
  scale,
}: {
  geometry: TrendChartGeometry;
  startMs: number;
  endMs: number;
  granularity: "hour" | "day";
  metric: TrendMetric;
  points: readonly TimeseriesPoint[];
  ratio: (value: number) => number;
  scale: ReturnType<typeof trendScale>;
}) {
  const { mode } = useTimezone();
  return (
    <>
      {scale.ticks
        .filter((tick) => tick > 0)
        .map((tick) => (
          <g key={tick}>
            <line
              x1={geometry.plotLeft}
              x2={geometry.width}
              y1={geometry.plotBottom - ratio(tick)}
              y2={geometry.plotBottom - ratio(tick)}
              className="stroke-border"
              strokeDasharray="3 5"
            />
            <text
              x={geometry.plotLeft - 6}
              y={geometry.plotBottom - ratio(tick) + 3}
              textAnchor="end"
              fontSize={9}
              className="fill-faint"
            >
              {formatTrendAxis(tick, metric)}
            </text>
          </g>
        ))}
      <text x={geometry.width} y={12} textAnchor="end" className="fill-faint text-2xs">
        {formatAxisMoment(startMs, mode, granularity)} → {formatAxisMoment(endMs, mode, granularity)} · 每
        {granularity === "hour" ? "小时" : "天"}
      </text>
      {trendAxisLabelIndexes(points.length, geometry.slot).map((bucketIndex) => {
        const point = points[bucketIndex];
        if (!point) return null;
        return (
          <text
            key={"axis-" + bucketIndex + "-" + point.start_ms}
            x={geometry.plotLeft + (bucketIndex + 0.5) * geometry.slot}
            y={geometry.axisLabelY}
            textAnchor="middle"
            className="fill-faint text-2xs"
          >
            {formatAxisTick(point.start_ms, mode, granularity)}
          </text>
        );
      })}
    </>
  );
}

interface TooltipRow {
  name: string;
  color: string;
  value: number;
}

/** 悬停卡：柱形与面积共用，位置按当前桶在画布里的真实横向比例 clamp。 */
function TrendTooltip({
  point,
  rows,
  metric,
  totalLabel,
  totalText,
  left,
}: {
  point: TimeseriesPoint;
  rows: TooltipRow[];
  metric: TrendMetric;
  totalLabel: string;
  totalText: string;
  left: string;
}) {
  const { mode } = useTimezone();
  const parts = formatDateTimeParts(point.start_ms, mode);
  return (
    <div
      className="pointer-events-none absolute top-2 z-10 w-56 -translate-x-1/2 rounded-control border border-border bg-surface p-2.5 shadow-lg"
      style={{ left }}
      role="status"
    >
      <p className="text-2xs font-medium text-title">
        {parts ? parts.date + " " + parts.time.slice(0, 5) : "—"}
      </p>
      {rows.length ? (
        <ul className="mt-1.5 space-y-1">
          {rows.map((row) => (
            <li key={row.name} className="flex items-center gap-1.5 text-2xs">
              <span className="h-2 w-2 shrink-0 rounded-xs" style={{ backgroundColor: row.color }} />
              <span className="min-w-0 flex-1 truncate text-body">{row.name}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatTrendAxis(row.value, metric)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-2xs text-faint">该时段没有样本</p>
      )}
      <p className="mt-1.5 flex items-baseline justify-between gap-2 border-t border-border pt-1 text-2xs text-faint">
        <span>{totalLabel}</span>
        <span className="tabular-nums text-title">{totalText}</span>
      </p>
    </div>
  );
}

/** 悬停卡的水平位置：按当前桶在画布里的横向比例 clamp 到可视区中间。 */
function trendTooltipLeft(bucket: number, geometry: TrendChartGeometry): string {
  const center = geometry.plotLeft + (bucket + 0.5) * geometry.slot;
  const percent = ((center / geometry.width) * 100).toFixed(3);
  return (
    "clamp(" +
    TREND_TOOLTIP_WIDTH / 2 +
    "px, " +
    percent +
    "%, calc(100% - " +
    TREND_TOOLTIP_WIDTH / 2 +
    "px))"
  );
}

/**
 * 柱形图：当前维度的成员按请求数降序自下而上堆叠。
 * 只有可加性指标（请求/词元/成本）能堆叠；P95 在面积图里按成员画线。
 */
function BarChart({
  data,
  dimension,
  metric,
  series,
}: {
  data: DashboardTimeseries;
  dimension: TrendDimension;
  metric: TrendMetric;
  series: TrendSeries[];
}) {
  const [hover, setHover] = useState<{ bucket: number; series: number | null } | null>(null);
  const points = data.points;
  const { ref, geometry } = useChartGeometry(points.length);
  const dimensionLabel = TREND_DIMENSION_LABELS[dimension];
  const label = metricLabelOf(metric);
  if (!points.length || !series.length) {
    return <ChartEmpty text={series.length ? "所选范围内没有趋势数据。" : "暂无可见成员，点图例恢复。"} />;
  }

  const bucketTotals = points.map((_, bucket) =>
    series.reduce((sum, item) => sum + (item.values[bucket] ?? 0), 0),
  );
  const scale = trendScale(
    Math.max(1, ...bucketTotals),
    trendYAxisTickCount(geometry.plotHeight),
    metric === "requests",
  );
  const ratio = (value: number) => (value / scale.max) * geometry.plotHeight;
  const barWidth = Math.max(2, Math.min(26, geometry.slot * 0.62));
  const activeBucket = hover && hover.bucket < points.length ? hover.bucket : -1;
  const activePoint = activeBucket >= 0 ? points[activeBucket] : null;
  const activeRows: TooltipRow[] = [];
  if (activeBucket >= 0) {
    series.forEach((item, index) => {
      const value = item.values[activeBucket];
      if (value === null || value <= 0) return;
      if (hover && hover.series !== null && hover.series !== index) return;
      activeRows.push({ name: item.name, color: item.color, value });
    });
  }

  return (
    <div ref={ref} className="relative w-full min-w-0 flex-1" style={{ minHeight: TREND_CHART_MIN_HEIGHT }}>
      <svg
        width={geometry.width}
        height={geometry.height}
        viewBox={"0 0 " + geometry.width + " " + geometry.height}
        className="block h-full w-full"
        role="img"
        aria-label={label + "趋势（按" + dimensionLabel + "成员堆叠）"}
        onMouseLeave={() => setHover(null)}
      >
        <TrendAxes
          geometry={geometry}
          startMs={data.start_ms}
          endMs={data.end_ms}
          granularity={data.granularity}
          metric={metric}
          points={points}
          ratio={ratio}
          scale={scale}
        />
        {points.map((point, bucket) => (
          <rect
            key={"hit-" + point.start_ms}
            x={geometry.plotLeft + bucket * geometry.slot}
            y={geometry.plotTop}
            width={geometry.slot}
            height={geometry.plotHeight}
            fill="transparent"
            onMouseEnter={() => setHover({ bucket, series: null })}
          />
        ))}
        {points.map((point, bucket) => {
          const segments = stackTrendSegments(
            series.map((item) => ({
              name: item.name,
              color: item.color,
              value: item.values[bucket] ?? 0,
            })),
            ratio,
            geometry.plotBottom,
          );
          const x = geometry.plotLeft + bucket * geometry.slot + (geometry.slot - barWidth) / 2;
          return segments.map((segment) => {
            const seriesIndex = series.findIndex((item) => item.name === segment.name);
            return (
              <rect
                key={"bar-" + point.start_ms + "-" + segment.name}
                x={x}
                y={segment.y}
                width={barWidth}
                height={Math.max(0, segment.height)}
                rx={1}
                fill={segment.color}
                opacity={hover && hover.series === seriesIndex ? 0.82 : 1}
                onMouseEnter={() => setHover({ bucket, series: seriesIndex })}
              />
            );
          });
        })}
        {activeBucket >= 0 ? (
          <line
            x1={geometry.plotLeft + (activeBucket + 0.5) * geometry.slot}
            x2={geometry.plotLeft + (activeBucket + 0.5) * geometry.slot}
            y1={geometry.plotTop}
            y2={geometry.plotBottom}
            className="stroke-border-strong"
            strokeDasharray="3 4"
          />
        ) : null}
      </svg>
      {activePoint ? (
        <TrendTooltip
          point={activePoint}
          rows={activeRows}
          metric={metric}
          totalLabel={"合计 · " + dimensionLabel}
          totalText={formatTrendAxis(bucketTotals[activeBucket] ?? 0, metric)}
          left={trendTooltipLeft(activeBucket, geometry)}
        />
      ) : null}
    </div>
  );
}

/**
 * 面积图：默认不堆叠多线——当前维度的成员各画一条曲线，面积只是视觉提示。
 * 曲线用单调三次插值平滑；P95 同样平滑且不填充。某桶没有样本时曲线断开，
 * 两段之间用细虚线连起来（走势连贯，但不假装那里有数据），面积填充也不跨断点。
 */
function AreaChart({
  data,
  dimension,
  metric,
  series,
}: {
  data: DashboardTimeseries;
  dimension: TrendDimension;
  metric: TrendMetric;
  series: TrendSeries[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const points = data.points;
  const { ref, geometry } = useChartGeometry(points.length);
  const dimensionLabel = TREND_DIMENSION_LABELS[dimension];
  const label = metricLabelOf(metric);
  const weighted = metric === "latency";
  if (!points.length || !series.length) {
    return <ChartEmpty text={series.length ? "所选范围内没有趋势数据。" : "暂无可见成员，点图例恢复。"} />;
  }

  const peak = Math.max(
    1,
    ...series.flatMap((item) => item.values.map((value) => value ?? 0)),
  );
  const scale = trendScale(
    peak,
    trendYAxisTickCount(geometry.plotHeight),
    metric === "requests",
  );
  const ratio = (value: number) => (value / scale.max) * geometry.plotHeight;
  const xOf = (bucket: number) => geometry.plotLeft + (bucket + 0.5) * geometry.slot;
  const yOf = (value: number) => geometry.plotBottom - ratio(value);

  const activeBucket = hover !== null && hover < points.length ? hover : -1;
  const activePoint = activeBucket >= 0 ? points[activeBucket] : null;
  const activeRows: TooltipRow[] = [];
  let activeWeight = 0;
  if (activeBucket >= 0) {
    for (const item of series) {
      const value = item.values[activeBucket];
      if (value === null || (!weighted && value <= 0)) continue;
      activeRows.push({ name: item.name, color: item.color, value });
      if (weighted) activeWeight += item.requests[activeBucket] ?? 0;
    }
  }
  const activeTotal = weighted
    ? formatWeightedP95(
        weightedAverageP95(
          activeRows.map((row) => ({
            value: row.value,
            requests: activeWeight / Math.max(1, activeRows.length),
          })),
        ),
        activeWeight,
      )
    : formatTrendAxis(
        activeRows.reduce((sum, row) => sum + row.value, 0),
        metric,
      );

  return (
    <div ref={ref} className="relative w-full min-w-0 flex-1" style={{ minHeight: TREND_CHART_MIN_HEIGHT }}>
      <svg
        width={geometry.width}
        height={geometry.height}
        viewBox={"0 0 " + geometry.width + " " + geometry.height}
        className="block h-full w-full"
        role="img"
        aria-label={
          weighted
            ? "P95 延迟趋势（按" + dimensionLabel + "成员并排，不堆叠）"
            : label + "趋势（按" + dimensionLabel + "成员曲线，不堆叠多线）"
        }
        onMouseLeave={() => setHover(null)}
      >
        <TrendAxes
          geometry={geometry}
          startMs={data.start_ms}
          endMs={data.end_ms}
          granularity={data.granularity}
          metric={metric}
          points={points}
          ratio={ratio}
          scale={scale}
        />
        {weighted
          ? null
          : series.map((item) =>
              trendAreaPaths(item.values, xOf, yOf, geometry.plotBottom).map((path, index) => (
                <path
                  key={"area-" + item.name + "-" + index}
                  d={path}
                  fill={item.color}
                  fillOpacity={0.16}
                  stroke="none"
                />
              )),
            )}
        {series.map((item) => {
          const line = trendLinePaths(item.values, xOf, yOf);
          return (
            <g key={"line-" + item.name}>
              {line.dashed ? (
                <path
                  d={line.dashed}
                  fill="none"
                  stroke={item.color}
                  strokeWidth={1.2}
                  strokeDasharray="2 4"
                  strokeLinecap="round"
                  opacity={0.75}
                />
              ) : null}
              <path
                d={line.solid}
                fill="none"
                stroke={item.color}
                strokeWidth={item.other ? 1 : 1.6}
                strokeDasharray={item.other ? "4 3" : undefined}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            </g>
          );
        })}
        {activeBucket >= 0 ? (
          <line
            x1={xOf(activeBucket)}
            x2={xOf(activeBucket)}
            y1={geometry.plotTop}
            y2={geometry.plotBottom}
            className="stroke-border-strong"
            strokeDasharray="3 4"
          />
        ) : null}
        {activeBucket >= 0
          ? series.map((item) => {
              const value = item.values[activeBucket];
              if (value === null) return null;
              return (
                <circle
                  key={"dot-" + item.name}
                  cx={xOf(activeBucket)}
                  cy={yOf(value)}
                  r={2.6}
                  fill={item.color}
                />
              );
            })
          : null}
        {points.map((point, bucket) => (
          <rect
            key={"hit-" + point.start_ms}
            x={geometry.plotLeft + bucket * geometry.slot}
            y={geometry.plotTop}
            width={geometry.slot}
            height={geometry.plotHeight}
            fill="transparent"
            onMouseEnter={() => setHover(bucket)}
          />
        ))}
      </svg>
      {activePoint ? (
        <TrendTooltip
          point={activePoint}
          rows={activeRows}
          metric={metric}
          totalLabel={weighted ? "加权 P95 · " + dimensionLabel : "合计 · " + dimensionLabel}
          totalText={activeTotal}
          left={trendTooltipLeft(activeBucket, geometry)}
        />
      ) : null}
    </div>
  );
}

export interface TrendCardProps {
  data: DashboardTimeseries | undefined;
  range: string;
  timezone: string;
  timeWindow?: DashboardWindow;
  filters?: DashboardFilters;
  windowReady: boolean;
  refreshMs: number;
  /** 当前维度由页面持有：它同时驱动 timeseries 请求参数与 query key。 */
  dimension: TrendDimension;
  onDimensionChange: (next: TrendDimension) => void;
}

/**
 * 看板主图：一张卡里三种图型（柱形/面积/分流），一次只画一个维度。
 * 图型与维度都进 URL（?tab=bar|area|flow、?dimension=...）：刷新后回到同一视图；
 * 图例点击、指标切换是纯渲染状态，不落 URL；指标默认「词元」。
 */
export function TrendCard({
  data,
  range,
  timezone,
  timeWindow,
  filters,
  windowReady,
  refreshMs,
  dimension,
  onDimensionChange,
}: TrendCardProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const chartType = parseChartType(searchParams.get("tab"));
  const [flowVisited, setFlowVisited] = useState(chartType === "flow");
  const [metric, setMetric] = useState<TrendMetric>(TREND_DEFAULT_METRIC);
  const [hiddenMembers, setHiddenMembers] = useState<Set<string>>(() => new Set());

  const points = useMemo(() => data?.points ?? [], [data]);
  const ranked = useMemo(() => rankMembers(points, dimension), [points, dimension]);
  const colors = useMemo(() => assignCategoryColorsByName([ranked]), [ranked]);
  /** 柱形图不提供 P95：从面积图切回来时把指标退回默认「词元」，避免出现空柱。 */
  const activeMetric: TrendMetric =
    chartType === "bar" && metric === "latency" ? TREND_DEFAULT_METRIC : metric;
  const series = useMemo(
    () => (data ? buildSeries(data.points, dimension, activeMetric, colors) : []),
    [data, dimension, activeMetric, colors],
  );
  const legendRows = useMemo(() => legendRowsOf(series, activeMetric), [series, activeMetric]);
  const hiddenForDimension = useMemo(
    () =>
      new Set(
        [...hiddenMembers]
          .filter((key) => key.startsWith(dimension + "::"))
          .map((key) => key.slice(dimension.length + 2)),
      ),
    [hiddenMembers, dimension],
  );
  const visibleSeries = series.filter((item) => !hiddenForDimension.has(item.name));
  const granularityText = "按" + (data?.granularity === "hour" ? "小时" : "天");

  const selectChart = (next: string) => {
    const value = parseChartType(next);
    if (value === "flow") setFlowVisited(true);
    if (value !== "area" && metric === "latency") setMetric(TREND_DEFAULT_METRIC);
    const params = new URLSearchParams(searchParams);
    if (value === "bar") params.delete("tab");
    else params.set("tab", value);
    setSearchParams(params, { replace: true });
  };

  const toggleMember = (name: string) => {
    const key = memberKey(dimension, name);
    setHiddenMembers((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isFlow = chartType === "flow";

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs value={chartType} onValueChange={selectChart}>
          <TabsList aria-label="图表类型" className="h-8 gap-0.5">
            {CHART_TABS.map((item) => (
              <TabsTrigger key={item.value} value={item.value} className="h-6 px-3 text-2xs">
                {item.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <span className="text-2xs text-faint">
          {isFlow
            ? "同一批请求的五阶段去向"
            : granularityText + " · " + TREND_DIMENSION_LABELS[dimension]}
        </span>
      </div>

      {isFlow ? (
        flowVisited ? (
          <div className="mt-3">
            <FlowCard
              range={range}
              timezone={timezone}
              window={timeWindow}
              filters={filters}
              windowReady={windowReady}
              refreshMs={refreshMs}
              enabled={isFlow}
            />
          </div>
        ) : null
      ) : (
        <div className="mt-3 grid gap-4 xl:grid-cols-[minmax(0,1fr)_296px]">
          <div className="flex min-h-80 min-w-0 flex-col">
            {data ? (
              chartType === "area" ? (
                <AreaChart
                  data={data}
                  dimension={dimension}
                  metric={activeMetric}
                  series={visibleSeries}
                />
              ) : (
                <BarChart
                  data={data}
                  dimension={dimension}
                  metric={activeMetric}
                  series={visibleSeries}
                />
              )
            ) : (
              <div className="flex flex-1 items-center justify-center rounded-control border border-dashed border-border">
                <p className="text-xs text-muted-foreground">暂无可绘制的趋势数据。</p>
              </div>
            )}
          </div>

          <div className="min-w-0 self-start rounded-control border border-border bg-surface-2/30 p-3 xl:self-auto">
            <div className="flex min-w-0 flex-col gap-3.5">
              <section>
                <h3 className="text-2xs font-medium text-faint">维度（一次只看一个）</h3>
                <RadioGroup
                  value={dimension}
                  onValueChange={(next) => {
                    if (next) onDimensionChange(next as TrendDimension);
                  }}
                  aria-label="趋势维度"
                  className="mt-1.5 gap-0.5"
                >
                  {TREND_DIMENSIONS.map((item) => (
                    <label
                      key={item.value}
                      className={cn(
                        "flex h-8 cursor-pointer items-center gap-2 rounded-control px-2 text-xs transition-colors",
                        dimension === item.value
                          ? "bg-primary-soft text-primary"
                          : "text-muted-foreground hover:bg-surface-2 hover:text-title",
                      )}
                    >
                      <RadioGroupItem value={item.value} className="h-3.5 w-3.5" />
                      <span className="min-w-0 truncate">{item.label}</span>
                    </label>
                  ))}
                </RadioGroup>
              </section>

              <section>
                <h3 className="text-2xs font-medium text-faint">指标</h3>
                <ToggleGroup
                  type="single"
                  value={activeMetric}
                  onValueChange={(next) => {
                    if (next) setMetric(next as TrendMetric);
                  }}
                  className="mt-1.5 grid grid-cols-2 gap-1"
                >
                  {metricOptions(chartType).map((item) => (
                    <ToggleGroupItem
                      key={item.value}
                      value={item.value}
                      className="h-7 min-w-0 rounded-[6px] px-1 text-2xs font-medium data-[state=on]:bg-primary-soft data-[state=on]:text-primary"
                    >
                      {item.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                {chartType === "bar" ? (
                  <p className="mt-1 text-2xs leading-4 text-faint">P95 不可堆叠，切到面积图查看。</p>
                ) : null}
              </section>

              <section className="min-w-0">
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="text-2xs font-medium text-faint">图例</h3>
                  <span className="truncate text-2xs text-faint">
                    {TREND_DIMENSION_LABELS[dimension]}
                  </span>
                </div>
                <div className="mt-1 max-h-56 space-y-0.5 overflow-y-auto pr-0.5">
                  <div className="flex items-center gap-1.5 px-1 text-2xs text-faint">
                    <span className="min-w-0 flex-1">成员</span>
                    <span className="w-14 shrink-0 text-right">合计</span>
                    <span className="w-9 shrink-0 text-right">
                      {activeMetric === "latency" ? "样本" : "占比"}
                    </span>
                  </div>
                  {legendRows.length ? (
                    legendRows.map((row) => {
                      const hidden = hiddenForDimension.has(row.name);
                      return (
                        <div
                          key={row.name}
                          className="flex items-center gap-1.5 rounded-control px-1 py-1 text-2xs hover:bg-surface-2"
                        >
                          <button
                            type="button"
                            onClick={() => toggleMember(row.name)}
                            aria-pressed={!hidden}
                            aria-label={(hidden ? "显示 " : "隐藏 ") + row.name}
                            className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                          >
                            <span
                              className="h-2 w-2 shrink-0 rounded-xs"
                              style={{ backgroundColor: row.color }}
                            />
                            <span
                              className={cn(
                                "min-w-0 truncate",
                                hidden ? "text-faint line-through" : "text-body",
                              )}
                            >
                              {row.name}
                            </span>
                          </button>
                          <span className="w-14 shrink-0 text-right tabular-nums text-muted-foreground">
                            {row.text}
                          </span>
                          <span className="w-9 shrink-0 text-right tabular-nums text-faint">
                            {row.share === null
                              ? row.requests.toLocaleString()
                              : Math.round(row.share * 100) + "%"}
                          </span>
                        </div>
                      );
                    })
                  ) : (
                    <p className="px-1 py-2 text-2xs text-muted-foreground">暂无数据</p>
                  )}
                </div>
                <p className="mt-1.5 text-2xs leading-4 text-faint">
                  {activeMetric === "latency"
                    ? "虚线连接的两端之间没有样本（不按 0 处理）；悬停看单时段明细，点成员可隐藏或恢复。"
                    : "悬停图区看单时段明细；点成员可隐藏或恢复。"}
                </p>
              </section>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
