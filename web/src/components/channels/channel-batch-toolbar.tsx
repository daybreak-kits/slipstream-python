import { Power, PowerOff, Trash2, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

export type ChannelBatchAction = "enable" | "disable" | "probe" | "delete";

export function ChannelBatchToolbar({
  count,
  busy,
  onEnable,
  onDisable,
  onProbe,
  onDelete,
  onClear,
}: {
  count: number;
  busy: ChannelBatchAction | null;
  onEnable: () => void;
  onDisable: () => void;
  onProbe: () => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  if (count === 0) return null;
  const disabled = busy !== null;
  return (
    <>
      <span className="text-xs font-medium text-title">已选 {count} 个渠道</span>
      <span className="hidden text-2xs text-muted-foreground sm:inline">
        逐个执行，失败项会单独提示
      </span>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="outline"
          size="md"
          className="gap-1 px-2.5 text-xs"
          aria-label="批量启用"
          disabled={disabled}
          onClick={onEnable}
        >
          {busy === "enable" ? (
            <Spinner size="sm" aria-hidden="true" />
          ) : (
            <Power className="h-3.5 w-3.5 text-success-text" strokeWidth={1.75} aria-hidden="true" />
          )}
          启用
        </Button>
        <Button
          variant="outline"
          size="md"
          className="gap-1 px-2.5 text-xs"
          aria-label="批量封禁"
          disabled={disabled}
          onClick={onDisable}
        >
          {busy === "disable" ? (
            <Spinner size="sm" aria-hidden="true" />
          ) : (
            <PowerOff className="h-3.5 w-3.5 text-danger-text" strokeWidth={1.75} aria-hidden="true" />
          )}
          封禁
        </Button>
        <Button
          variant="outline"
          size="md"
          className="gap-1 px-2.5 text-xs"
          aria-label="批量测速"
          disabled={disabled}
          onClick={onProbe}
        >
          {busy === "probe" ? (
            <Spinner size="sm" aria-hidden="true" />
          ) : (
            <Zap className="h-3.5 w-3.5 text-primary" strokeWidth={1.75} aria-hidden="true" />
          )}
          测速
        </Button>
        <Button
          variant="ghost"
          size="md"
          className="gap-1 px-2.5 text-xs text-danger-text hover:bg-danger-soft hover:text-danger-text"
          aria-label="批量删除"
          disabled={disabled}
          onClick={onDelete}
        >
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          删除
        </Button>
        <Button
          variant="ghost"
          size="md"
          className="px-2.5 text-xs"
          disabled={disabled}
          onClick={onClear}
        >
          取消选择
        </Button>
      </div>
    </>
  );
}
