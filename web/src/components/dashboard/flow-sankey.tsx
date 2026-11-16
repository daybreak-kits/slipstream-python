import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { EyeOff, Minus, Plus, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CATEGORY_COLORS } from "@/lib/chart-colors";
import {
  FLOW_CHART_HEIGHT,
  FLOW_DISPLAY_LIMITS,
  FLOW_MAX_WIDTH,
  FLOW_MIN_HIT_HEIGHT,
  FLOW_MIN_NODE_HEIGHT,
  FLOW_MIN_WIDTH,
  flowMetricTotal,
  flowNodeNeighbors,
  foldFlowNodes,
  layoutFlow,
  type DashboardFlows,
  type FlowDisplayLimit,
  type FlowMetric,
} from "@/lib/dashboard";
import { formatCompact } from "@/lib/format";

const MIN_SCALE = 0.5;
const MAX_SCALE = 8;
const ZOOM_STEP = 1.2;

function clampScale(value: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}

function truncateLabel(label: string, max = 14): string {
  return label.length <= max ? label : `${label.slice(0, max - 1)}…`;
}

function flowStageColor(stageIndex: number): string {
  return CATEGORY_COLORS[stageIndex % CATEGORY_COLORS.length];
}

interface FlowSankeyProps {
  data: DashboardFlows;
  metric: FlowMetric;
}

/**
 * 自绘桑基图：滚轮以光标为锚点缩放，拖拽平移，双击复位。
 * 节点可点击/Enter/Space 隐藏，隐藏后重新布局并移除关联连线；
 * 至少保留一个可见节点，隐藏项在工具栏下方提供恢复入口。
 */
export function FlowSankey({ data, metric }: FlowSankeyProps) {
  const [limit, setLimit] = useState<FlowDisplayLimit>(20);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  /** 当前悬停/聚焦的节点：悬停与键盘聚焦共用同一张详情卡。 */
  const [active, setActive] = useState<{ id: string; source: "hover" | "focus" } | null>(null);
  const [chartWidth, setChartWidth] = useState(FLOW_MIN_WIDTH);
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);

  const folded = useMemo(() => foldFlowNodes(data.nodes, data.links, limit), [data.nodes, data.links, limit]);
  const nodeKey = useMemo(() => data.nodes.map((node) => node.id).join("\u0001"), [data.nodes]);

  // 数据/指标/显示数量变化时清空隐藏项，避免旧 id 把新图误隐藏。
  useEffect(() => {
    setHidden((previous) => (previous.size ? new Set() : previous));
  }, [nodeKey, data.metric, limit]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.width ?? 0;
      if (measured <= 0) return;
      setChartWidth(Math.round(Math.min(FLOW_MAX_WIDTH, Math.max(FLOW_MIN_WIDTH, measured))));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const hiddenIds = hidden;
  const visibleNodes = useMemo(
    () => folded.nodes.filter((node) => !hiddenIds.has(node.id)),
    [folded.nodes, hiddenIds],
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleLinks = useMemo(
    () => folded.links.filter((link) => visibleIds.has(link.source) && visibleIds.has(link.target)),
    [folded.links, visibleIds],
  );
  const layout = useMemo(
    () => layoutFlow(visibleNodes, visibleLinks, { width: chartWidth, height: FLOW_CHART_HEIGHT }),
    [visibleNodes, visibleLinks, chartWidth],
  );

  const toggleNode = (id: string) => {
    setHidden((previous) => {
      if (previous.has(id)) {
        const next = new Set(previous);
        next.delete(id);
        return next;
      }
      // 至少保留一个可见节点，否则整张图空白且无法通过图内恢复。
      if (folded.nodes.length - previous.size <= 1) return previous;
      const next = new Set(previous);
      next.add(id);
      return next;
    });
  };

  const zoomAt = (anchorX: number, anchorY: number, factor: number) => {
    setView((previous) => {
      const scale = clampScale(previous.scale * factor);
      if (scale === previous.scale) return previous;
      const ratio = scale / previous.scale;
      return {
        scale,
        x: anchorX - (anchorX - previous.x) * ratio,
        y: anchorY - (anchorY - previous.y) * ratio,
      };
    });
  };

  const zoomBy = (factor: number) => zoomAt(chartWidth / 2, FLOW_CHART_HEIGHT / 2, factor);
  const resetView = () => setView({ scale: 1, x: 0, y: 0 });

  // 滚轮不能用 React 的 passive 合成事件，否则 preventDefault 会被浏览器忽略。
  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const anchorX = ((event.clientX - rect.left) / rect.width) * chartWidth;
      const anchorY = ((event.clientY - rect.top) / rect.height) * FLOW_CHART_HEIGHT;
      zoomAt(anchorX, anchorY, event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [chartWidth]);

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: view.x,
      originY: view.y,
    };
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const scaleX = chartWidth / rect.width;
    const scaleY = FLOW_CHART_HEIGHT / rect.height;
    setView((previous) => ({
      ...previous,
      x: drag.originX + (event.clientX - drag.startX) * scaleX,
      y: drag.originY + (event.clientY - drag.startY) * scaleY,
    }));
  };

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const activeNode = active ? (visibleNodes.find((node) => node.id === active.id) ?? null) : null;
  // 几何取布局节点（含 x/y/width/centerY），文案与相邻关系统一取可见节点。
  const activeLayoutNode = activeNode
    ? (layout.nodes.find((node) => node.id === activeNode.id) ?? null)
    : null;
  const activeNeighbors = activeNode ? flowNodeNeighbors(visibleNodes, visibleLinks, activeNode.id) : null;
  const unit = metric === "requests" ? "次请求" : "token";
  /** 详情卡贴着节点中心：把视图平移/缩放换算成画布内像素后再夹在画布范围内。 */
  // 详情卡宽 240（w-60）且用 -translate-x-1/2 居中，夹取边界时按半宽留白，避免溢出画布。
  const FLOW_TOOLTIP_HALF = 120;
  const tooltipLeft = activeLayoutNode
    ? Math.min(
        chartWidth - FLOW_TOOLTIP_HALF - 8,
        Math.max(
          FLOW_TOOLTIP_HALF + 8,
          view.x + activeLayoutNode.x * view.scale + (activeLayoutNode.width * view.scale) / 2,
        ),
      )
    : 0;
  const tooltipTop = activeLayoutNode
    ? Math.min(
        FLOW_CHART_HEIGHT - 150,
        Math.max(8, view.y + activeLayoutNode.centerY * view.scale),
      )
    : 0;
  const hiddenEntries = [...hidden].map((id) => {
    const node = folded.nodes.find((candidate) => candidate.id === id);
    return { id, label: node?.label ?? id };
  });
  const total = flowMetricTotal(data);

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-2xs text-faint">
          <label className="flex items-center gap-1.5">
            显示
            <Select
              value={String(limit)}
              onValueChange={(next) => setLimit(Number(next) as FlowDisplayLimit)}
            >
              <SelectTrigger
                aria-label="分流图每阶段显示数量"
                className="h-8 w-auto gap-1 rounded-badge px-2 text-2xs text-title"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FLOW_DISPLAY_LIMITS.map((value) => (
                  <SelectItem key={value} value={String(value)} className="text-2xs">
                    前 {value}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <span>单位：{metric === "requests" ? "请求数" : "Token"}</span>
          <span className="tabular-nums">总量 {formatCompact(total)}</span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 px-0"
            aria-label="缩小分流图"
            onClick={() => zoomBy(1 / ZOOM_STEP)}
          >
            <Minus className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          <span
            className="w-10 text-center text-2xs tabular-nums text-muted-foreground"
            aria-live="polite"
          >
            {view.scale.toFixed(1)}×
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 px-0"
            aria-label="放大分流图"
            onClick={() => zoomBy(ZOOM_STEP)}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1 px-2 text-2xs"
            onClick={resetView}
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            复位
          </Button>
        </div>
      </div>

      {hiddenEntries.length ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 text-2xs text-faint">
            <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
            已隐藏 {hiddenEntries.length} 个节点
          </span>
          {hiddenEntries.map((entry) => (
            <Button
              key={entry.id}
              variant="outline"
              size="sm"
              className="h-8 max-w-48 gap-1 px-2 text-2xs"
              onClick={() => toggleNode(entry.id)}
            >
              <Plus className="h-3 w-3 shrink-0" aria-hidden="true" />
              <span className="truncate">{entry.label}</span>
            </Button>
          ))}
        </div>
      ) : null}

      <div className="mt-2 rounded-control border border-border bg-surface-2/40 p-2">
        <div ref={containerRef} className="relative overflow-x-auto">
          <div ref={canvasRef} className="relative" style={{ width: chartWidth }}>
          <svg
            width={chartWidth}
            height={FLOW_CHART_HEIGHT}
            viewBox={`0 0 ${chartWidth} ${FLOW_CHART_HEIGHT}`}
            className="block touch-none select-none"
            aria-label="请求分流桑基图"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onDoubleClick={(event) => {
              event.preventDefault();
              resetView();
            }}
          >
            <rect
              width={chartWidth}
              height={FLOW_CHART_HEIGHT}
              fill="var(--surface)"
              fillOpacity={0}
              pointerEvents="all"
            />
            <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
              {layout.stages.map((stage) => (
                <text
                  key={stage.stage}
                  x={stage.x}
                  y={stage.y}
                  textAnchor="middle"
                  className="fill-muted-foreground text-2xs"
                  pointerEvents="none"
                >
                  {stage.label}
                </text>
              ))}
              {layout.links.map((link) => (
                <path
                  key={`${link.source}->${link.target}`}
                  d={link.path}
                  fill={flowStageColor(link.colorIndex)}
                  fillOpacity={0.22}
                  pointerEvents="none"
                />
              ))}
              {layout.nodes.map((node) => {
                const color = flowStageColor(node.stageIndex);
                // 份额极小的节点也至少画 8px 高；中心不变，避免堆叠顺序错位。
                const nodeHeight = Math.max(FLOW_MIN_NODE_HEIGHT, node.height);
                const nodeY = node.centerY - nodeHeight / 2;
                // 命中热区比视觉节点再大一圈，最少 12px，保证小节点也能点中/悬停到。
                const hitHeight = Math.max(FLOW_MIN_HIT_HEIGHT, nodeHeight);
                const hitY = node.centerY - hitHeight / 2;
                const showLabel = node.height * view.scale >= 12;
                const showValue = node.height * view.scale >= 28;
                const amount = formatCompact(node.value);
                return (
                  <g key={node.id}>
                    <rect
                      x={node.x}
                      y={nodeY}
                      width={node.width}
                      height={nodeHeight}
                      rx={2}
                      fill={color}
                      pointerEvents="none"
                    />
                    <rect
                      x={node.x - 2}
                      y={hitY}
                      width={node.width + 4}
                      height={hitHeight}
                      fill="transparent"
                      tabIndex={0}
                      role="button"
                      data-testid={`flow-node-${node.id}`}
                      aria-label={`隐藏节点 ${node.label}，${amount} ${unit}`}
                      className="cursor-pointer outline-hidden focus-visible:stroke-2 focus-visible:stroke-primary"
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleNode(node.id);
                      }}
                      onPointerDown={(event) => event.stopPropagation()}
                      onDoubleClick={(event) => event.stopPropagation()}
                      onMouseEnter={() => setActive({ id: node.id, source: "hover" })}
                      onMouseLeave={() =>
                        setActive((previous) =>
                          previous?.id === node.id && previous.source === "hover" ? null : previous,
                        )
                      }
                      onFocus={() => setActive({ id: node.id, source: "focus" })}
                      onBlur={() =>
                        setActive((previous) =>
                          previous?.id === node.id && previous.source === "focus" ? null : previous,
                        )
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          toggleNode(node.id);
                        }
                      }}
                    />
                    {showLabel ? (
                      <text
                        x={node.labelX}
                        y={node.centerY - (showValue ? 5 : 0)}
                        textAnchor={node.labelAnchor}
                        dominantBaseline="middle"
                        stroke="var(--surface)"
                        strokeWidth={3}
                        paintOrder="stroke"
                        pointerEvents="none"
                        className="fill-title text-2xs"
                      >
                        {truncateLabel(node.label)}
                      </text>
                    ) : null}
                    {showLabel && showValue ? (
                      <text
                        x={node.labelX}
                        y={node.centerY + 8}
                        textAnchor={node.labelAnchor}
                        dominantBaseline="middle"
                        stroke="var(--surface)"
                        strokeWidth={3}
                        paintOrder="stroke"
                        pointerEvents="none"
                        className="fill-faint text-2xs"
                      >
                        {amount}
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </g>
          </svg>
          {activeNode && activeNeighbors ? (
            <div
              className="pointer-events-none absolute z-20 w-60 rounded-control border border-[var(--tooltip-border)] bg-[var(--tooltip-bg)] p-2 text-2xs leading-snug text-[var(--tooltip-fg)] shadow-pop"
              style={{ left: tooltipLeft, top: tooltipTop, transform: "translate(-50%, 10px)" }}
              role="status"
              data-testid="flow-node-tooltip"
            >
              <p className="truncate text-2xs font-medium">{activeNode.label}</p>
              <p className="mt-0.5 text-[var(--tooltip-fg-muted)]">
                {formatCompact(activeNode.value)} {unit} · 占比 {(activeNode.share * 100).toFixed(1)}%
              </p>
              {activeNeighbors.upstream.length ? (
                <div className="mt-1 border-t border-[var(--tooltip-border)] pt-1">
                  <p className="text-[var(--tooltip-fg-muted)]">上游</p>
                  {activeNeighbors.upstream.map((item) => (
                    <p key={item.id} className="flex justify-between gap-2">
                      <span className="min-w-0 truncate">{item.label}</span>
                      <span className="shrink-0 tabular-nums">{formatCompact(item.value)}</span>
                    </p>
                  ))}
                </div>
              ) : null}
              {activeNeighbors.downstream.length ? (
                <div className="mt-1 border-t border-[var(--tooltip-border)] pt-1">
                  <p className="text-[var(--tooltip-fg-muted)]">下游</p>
                  {activeNeighbors.downstream.map((item) => (
                    <p key={item.id} className="flex justify-between gap-2">
                      <span className="min-w-0 truncate">{item.label}</span>
                      <span className="shrink-0 tabular-nums">{formatCompact(item.value)}</span>
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        </div>
      </div>
      <p className="mt-1 text-2xs text-faint">
        悬停或聚焦节点查看详情 · 滚轮缩放 · 拖拽平移 · 双击复位 · 点击节点或按 Enter 隐藏；至少保留一个节点。
      </p>
    </div>
  );
}

