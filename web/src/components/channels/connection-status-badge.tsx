import { Badge } from "@/components/ui/badge";
import {
  CONNECTION_STATUS_META,
  type ConnectionStatus,
} from "@/lib/channels";
import { cn } from "@/lib/utils";

const toneVariant = {
  success: "success",
  warning: "warning",
  danger: "danger",
  muted: "secondary",
} as const;

const dotClasses = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  // neutral dot: bg-faint stays visible on surface-2 (semantic colour pass deferred to batch 3)
  muted: "bg-faint",
} as const;

export function ConnectionStatusBadge({
  status,
  className,
}: {
  status: ConnectionStatus;
  className?: string;
}) {
  const meta = CONNECTION_STATUS_META[status] ?? CONNECTION_STATUS_META.unknown;
  return (
    <Badge
      variant={toneVariant[meta.tone]}
      className={cn("gap-1.5 px-2 py-1 text-2xs", className)}
      data-status={status}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", dotClasses[meta.tone])} aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}
