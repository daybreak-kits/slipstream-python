import { useQuery } from "@tanstack/react-query";
import { Waypoints } from "lucide-react";
import { useState } from "react";

import { FlowSankey } from "@/components/dashboard/flow-sankey";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Empty } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { describeApiError } from "@/lib/channels";
import {
  flowMetricTotal,
  getDashboardFlows,
  useAutoRefresh,
  type DashboardFilters,
  type DashboardWindow,
  type FlowMetric,
} from "@/lib/dashboard";

interface FlowCardProps {
  range: string;
  timezone: string;
  window?: DashboardWindow;
  filters?: DashboardFilters;
  windowReady: boolean;
  refreshMs: number;
  /** 趋势/分流标签页：只有切到分流时才拉数据（默认趋势不预取）。 */
  enabled?: boolean;
  /** 前端缓存时长：重复切标签不重复请求（任务要求 60s）。 */
  staleTimeMs?: number;
}

/**
 * 分流图查询与状态外壳（只渲染内容，外层卡片与标签页由 TrendCard 提供）。
 * 数据拉取放在这里，刷新沿用页面同一个自动刷新间隔；标签页未激活时禁用查询，
 * 切回来命中 React Query 缓存（staleTime 默认 60s），因此不会重复打后端。
 */
export function FlowCard({
  range,
  timezone,
  window,
  filters,
  windowReady,
  refreshMs,
  enabled = true,
  staleTimeMs = 60_000,
}: FlowCardProps) {
  const [metric, setMetric] = useState<FlowMetric>("requests");
  const query = useQuery({
    queryKey: [
      "dashboard-flows",
      range,
      timezone,
      window?.startMs,
      window?.endMs,
      filters?.channel,
      filters?.model,
      filters?.status,
      metric,
    ],
    queryFn: () => getDashboardFlows(range, timezone, metric, window, filters),
    enabled: windowReady && enabled,
    staleTime: staleTimeMs,
  });
  useAutoRefresh(() => {
    if (!enabled) return;
    void query.refetch();
  }, refreshMs);

  const total = query.data ? flowMetricTotal(query.data) : 0;
  const empty = query.data && (query.data.nodes.length === 0 || total <= 0);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-2xs text-faint">
          用户 → 密钥 → 模型 → 上游模型 → 渠道 · 同一批请求的五阶段去向
        </p>
        <ToggleGroup
          type="single"
          value={metric}
          onValueChange={(next) => {
            if (next === "requests" || next === "tokens") setMetric(next);
          }}
          className="shrink-0 gap-1"
        >
          <ToggleGroupItem value="requests" className="h-8 px-3 text-2xs">
            按请求数
          </ToggleGroupItem>
          <ToggleGroupItem value="tokens" className="h-8 px-3 text-2xs">
            按 Token
          </ToggleGroupItem>
        </ToggleGroup>
      </div>

      {query.isError ? (
        <Alert variant="destructive" className="mt-3">
          <AlertTitle>分流图加载失败</AlertTitle>
          <AlertDescription className="gap-1">
            <span>{describeApiError(query.error)}</span>
            <button
              type="button"
              className="text-2xs underline underline-offset-2"
              onClick={() => void query.refetch()}
            >
              重试
            </button>
          </AlertDescription>
        </Alert>
      ) : query.isLoading || (!query.data && !query.isError) ? (
        <div className="flex items-center justify-center gap-2 py-12 text-xs text-muted-foreground">
          <Spinner size="sm" />
          正在加载分流数据…
        </div>
      ) : empty ? (
        <Empty
          icon={Waypoints}
          title="所选范围内没有分流数据"
          description="调整时间范围或筛选条件后重试。"
          className="py-10"
        />
      ) : query.data ? (
        <FlowSankey data={query.data} metric={metric} />
      ) : (
        <Empty
          icon={Waypoints}
          title="分流数据尚未就绪"
          description="等待时间窗口就绪后自动加载。"
          className="py-10"
        />
      )}
    </div>
  );
}
