import { EqualApproximately, type LucideIcon } from "lucide-react";

import { IconBadge, type IconTone } from "@/components/ui/icon-badge";
import { cn } from "@/lib/utils";

export interface MetricPillItem {
  key: string;
  label: string;
  icon: LucideIcon;
  tone: IconTone;
  value: string;
  hint?: string;
  title?: string;
  estimated?: boolean;
  estimatedTitle?: string;
}

export function MetricStrip({
  items,
  loading = false,
  error,
  className,
}: {
  items: MetricPillItem[];
  loading?: boolean;
  error?: string | null;
  className?: string;
}) {
  return (
    <div
      className={cn("grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5", className)}
      aria-label="日志度量"
    >
      {items.map((item) => (
        <div
          key={item.key}
          className="rounded-card border border-border bg-surface px-3.5 py-3"
          title={item.title}
        >
          <div className="flex items-center gap-2">
            <IconBadge icon={item.icon} tone={item.tone} size="sm" />
            <span className="text-2xs font-medium uppercase tracking-[0.08em] text-muted-foreground">
              {item.label}
            </span>
            {item.estimated ? (
              <span
                className="ml-auto inline-flex"
                title={item.estimatedTitle}
                aria-label={item.estimatedTitle}
              >
                <EqualApproximately
                  className="h-3.5 w-3.5 text-warning-text"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
              </span>
            ) : null}
          </div>
          <p
            className={cn(
              "mt-2 text-metric font-semibold leading-none tracking-[-0.01em] tabular-nums text-title",
              loading && "animate-pulse text-muted-foreground",
            )}
          >
            {loading ? "…" : item.value}
          </p>
          <p className="mt-1.5 truncate text-2xs text-muted-foreground" title={item.hint}>
            {loading ? "正在统计…" : item.hint ?? "—"}
          </p>
        </div>
      ))}
      {error ? (
        <p className="text-[11px] text-danger-text sm:col-span-2 lg:col-span-3 xl:col-span-5">
          统计加载失败：{error}
        </p>
      ) : null}
    </div>
  );
}
