import { formatDuration } from "@/lib/duration";
import {
  formatLatency,
  isLatencyMissing,
  latencyDescription,
  latencyTextClass,
  latencyTone,
  type LatencyKind,
} from "@/lib/latency";
import { cn } from "@/lib/utils";

/** 耗时无数据位：「—」+「{label}无数据」提示；不渲染 0.00s（ui.md §2.4 / ADR-082 同族口径）。 */
export function LatencyMissingText({ label }: { label: string }) {
  return (
    <span className="text-2xs text-muted-foreground" title={`${label}无数据`}>
      —
    </span>
  );
}

/** 「首字 / 首答 / 总」单段耗时的行内渲染；请求行与展开详情共用同一套「—」语义。 */
export function LatencyValue({
  label,
  value,
  kind,
  emptyHint,
  failed = false,
}: {
  label: string;
  value: number | null | undefined;
  kind: LatencyKind;
  emptyHint?: string;
  failed?: boolean;
}) {
  // 失败请求没有「仅推理」可言：输出前就失败了，首字/首答的 0/null 都是「从未发生」。
  const hint = failed ? undefined : emptyHint;
  const showEmptyHint = value == null && Boolean(hint);
  // 总耗时 0 表示上游没有回报；失败请求的首字/首答 0 = 从未发生——
  // 都按无数据处理，不渲染 0.00s（ADR-082 同族口径）。
  const missing = isLatencyMissing(value, kind, failed);
  // 无数据的可访问名用全称（总耗时），行内短标签「总」只用于视觉。
  const missingLabel = kind === "total" ? "总耗时" : label;
  return (
    <span
      className="flex items-baseline gap-1.5 whitespace-nowrap"
      title={
        showEmptyHint
          ? hint
          : missing
            ? `${missingLabel}无数据`
            : `${label} ${formatDuration(value)}`
      }
      aria-label={showEmptyHint ? hint : missing ? `${missingLabel}无数据` : latencyDescription(value, kind)}
    >
      <span className="w-7 shrink-0 text-2xs text-muted-foreground">{label}</span>
      {showEmptyHint ? (
        <span className="text-2xs text-muted-foreground">仅推理</span>
      ) : missing ? (
        <LatencyMissingText label={missingLabel} />
      ) : (
        <span className={cn("tabular-nums text-xs font-medium", latencyTextClass(latencyTone(value, kind)))}>
          {formatLatency(value)}
        </span>
      )}
    </span>
  );
}
