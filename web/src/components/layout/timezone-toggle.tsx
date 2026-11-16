import { Globe2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { TIMEZONE_LABEL, useTimezone } from "@/lib/time";
import { cn } from "@/lib/utils";

export function TimezoneToggle({ className }: { className?: string }) {
  const { mode, toggle } = useTimezone();
  const next = mode === "utc" ? "本地" : "UTC";
  return (
    <Button
      variant="outline"
      size="md"
      onClick={toggle}
      aria-label={`切换时间显示为${next}`}
      className={cn("gap-1.5 px-2.5 font-medium text-muted-foreground hover:text-title", className)}
    >
      <Globe2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
      <span className="text-xs">{TIMEZONE_LABEL[mode]}</span>
    </Button>
  );
}
