import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { Ban, CalendarClock, Layers, Power, PowerOff, X } from "lucide-react";

import { Button, type ButtonProps } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export interface BulkActionBarProps {
  /** 已选条目数；0 时不渲染。 */
  count: number;
  onClear: () => void;
  onEnable: () => void;
  onBan: () => void;
  onCooldown: () => void;
  onSchedule: () => void;
  onAddToCombo: () => void;
  /** 选中项里有组合时，冷却/排期不可用（组合只有启用/封禁）。 */
  timedDisabled: boolean;
  /** 选中自动组合时不能单独启停：它的可见性由成员实时派生。 */
  lifecycleDisabled?: boolean;
  busy?: boolean;
}

/**
 * 禁用原因统一走 Tooltip：title= 既不是提示也不是可访问名（lint no-title-only-tooltip）。
 * 被禁用的 Button 不派发鼠标事件，所以用 span 承载触发与键盘焦点。
 */
function HintedButton({
  hint,
  children,
  ...props
}: ButtonProps & { hint?: string; children: ReactNode }) {
  const button = <Button {...props}>{children}</Button>;
  if (!hint) return button;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span tabIndex={0} className="inline-flex rounded-control" aria-label={hint}>
            {button}
          </span>
        </TooltipTrigger>
        <TooltipContent maxLines={2}>{hint}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * 批量工具条：吸底浮在表格上方，观感与渠道页的批量操作一致（深色面板 + 明确数量）。
 * 操作按「人工轴」分组：启用 / 封禁（永久）/ 冷却（定时）/ 排期 / 加入组合。
 */
export function BulkActionBar({
  count,
  onClear,
  onEnable,
  onBan,
  onCooldown,
  onSchedule,
  onAddToCombo,
  timedDisabled,
  lifecycleDisabled = false,
  busy = false,
}: BulkActionBarProps) {
  if (count === 0) return null;
  const timedHint = timedDisabled ? "组合条目不支持定时：请只选直连模型" : undefined;
  const lifecycleHint = lifecycleDisabled ? "自动组合由成员派生，不能单独启停" : undefined;

  // 吸底条必须挂到 body：页面外层有 animate-fade-up（transform），会让 fixed 相对它定位、
  // 并落进 main 的滚动裁剪里，既贴不到视口底部，也会被内容挡住点击。
  return createPortal(
    <div className="pointer-events-none fixed inset-x-0 bottom-6 z-40 flex justify-center px-4">
      <div
        role="toolbar"
        aria-label="批量操作"
        className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-card border border-border bg-surface px-3 py-2 shadow-pop"
      >
        <span className="px-1 text-xs font-semibold text-title">已选 {count} 项</span>
        <span className="mx-1 h-4 w-px bg-border" aria-hidden="true" />
        <HintedButton
          size="sm"
          variant="outline"
          disabled={busy || lifecycleDisabled}
          hint={lifecycleHint}
          onClick={onEnable}
        >
          <Power className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          启用
        </HintedButton>
        <HintedButton
          size="sm"
          variant="outline"
          disabled={busy || lifecycleDisabled}
          hint={lifecycleHint}
          onClick={onBan}
        >
          <Ban className="h-3.5 w-3.5 text-danger" strokeWidth={1.75} aria-hidden="true" />
          封禁
        </HintedButton>
        <HintedButton
          size="sm"
          variant="outline"
          disabled={busy || timedDisabled}
          hint={timedHint}
          onClick={onCooldown}
        >
          <PowerOff className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          冷却
        </HintedButton>
        <HintedButton
          size="sm"
          variant="outline"
          disabled={busy || timedDisabled}
          hint={timedHint}
          onClick={onSchedule}
        >
          <CalendarClock className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          排期
        </HintedButton>
        <HintedButton size="sm" variant="outline" disabled={busy} onClick={onAddToCombo}>
          <Layers className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          加入组合
        </HintedButton>
        <Button size="sm" variant="ghost" onClick={onClear}>
          <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          清除
        </Button>
      </div>
    </div>,
    document.body,
  );
}
