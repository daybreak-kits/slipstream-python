import { Badge } from "@/components/ui/badge";
import { PROBE_STATUS_META, type ProbeStatus } from "@/lib/channel-models";
import { cn } from "@/lib/utils";

const STATUS_VARIANT = {
  ok: "success",
  timeout: "warning",
  error: "danger",
  unknown: "secondary",
} as const;

interface ProbeStatusBadgeProps {
  status: ProbeStatus;
  message?: string;
  className?: string;
}

export function ProbeStatusBadge({ status, message, className }: ProbeStatusBadgeProps) {
  const meta = PROBE_STATUS_META[status] ?? PROBE_STATUS_META.unknown;
  return (
    <Badge
      variant={STATUS_VARIANT[status] ?? STATUS_VARIANT.unknown}
      title={message || undefined}
      className={cn("gap-1.5 whitespace-nowrap px-2 py-0.5 text-2xs", className)}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}
