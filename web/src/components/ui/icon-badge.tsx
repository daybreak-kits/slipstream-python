import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Semantic tint used by KPI cards and card titles.
 * Each tone pairs a colored icon with a soft tinted backdrop so a row of
 * tiles reads as a set of categories instead of nine identical glyphs.
 *
 * Colors come from the --badge-*-bg / --badge-*-fg pairs in index.css, which
 * ship a light-theme wash and a dark-theme variant, so the badges stay
 * readable in both themes (ssot §9.2: tinted backdrop, never solid + white).
 */
export type IconTone =
  | "flow"
  | "token"
  | "cache"
  | "cost"
  | "error"
  | "latency"
  | "brand"
  | "neutral";

const TONE_CLASS: Record<IconTone, string> = {
  flow: "bg-[var(--badge-flow-bg)] text-[var(--badge-flow-fg)]",
  token: "bg-[var(--badge-token-bg)] text-[var(--badge-token-fg)]",
  cache: "bg-[var(--badge-cache-bg)] text-[var(--badge-cache-fg)]",
  cost: "bg-[var(--badge-cost-bg)] text-[var(--badge-cost-fg)]",
  error: "bg-[var(--badge-error-bg)] text-[var(--badge-error-fg)]",
  latency: "bg-[var(--badge-latency-bg)] text-[var(--badge-latency-fg)]",
  brand: "bg-primary-soft text-primary",
  neutral: "bg-surface-2 text-muted-foreground",
};

const SIZE_CLASS = {
  sm: "h-7 w-7",
  md: "h-9 w-9",
  lg: "h-10 w-10",
} as const;

const ICON_SIZE_CLASS = {
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-5 w-5",
} as const;

export function IconBadge({
  icon: Icon,
  tone = "neutral",
  size = "md",
  className,
  strokeWidth = 1.75,
}: {
  icon: LucideIcon;
  tone?: IconTone;
  size?: keyof typeof SIZE_CLASS;
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center rounded-control",
        TONE_CLASS[tone],
        SIZE_CLASS[size],
        className,
      )}
    >
      <Icon className={ICON_SIZE_CLASS[size]} strokeWidth={strokeWidth} aria-hidden="true" />
    </span>
  );
}
