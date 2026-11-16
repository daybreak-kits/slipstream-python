import { CircleAlert, CircleCheck, CircleDashed, CircleSlash, OctagonAlert } from "lucide-react";
import type { ComponentType } from "react";

import { Badge } from "@/components/ui/badge";
import { HEALTH_STATUS_META, type VisibleStatus } from "@/lib/channel-models";
import { cn } from "@/lib/utils";

const ICONS: Record<VisibleStatus, ComponentType<{ className?: string; strokeWidth?: number }>> = {
  normal: CircleCheck,
  open: CircleAlert,
  half_open: CircleDashed,
  fault: OctagonAlert,
  disabled: CircleSlash,
};

const STATUS_VARIANT = {
  normal: "success",
  open: "warning",
  half_open: "warning",
  fault: "danger",
  disabled: "secondary",
} as const;

interface ModelHealthBadgeProps {
  status: VisibleStatus;
  className?: string;
}

/** Dot + icon + text, so the state never relies on colour alone. */
export function ModelHealthBadge({ status, className }: ModelHealthBadgeProps) {
  const meta = HEALTH_STATUS_META[status] ?? HEALTH_STATUS_META.normal;
  const Icon = ICONS[status] ?? ICONS.normal;
  return (
    <Badge
      variant={STATUS_VARIANT[status] ?? STATUS_VARIANT.normal}
      title={meta.description}
      className={cn("gap-1.5 whitespace-nowrap px-2 py-0.5 text-2xs", className)}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} aria-hidden="true" />
      <Icon className="h-3 w-3" strokeWidth={1.9} aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}
