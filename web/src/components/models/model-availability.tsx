import { Ban, Power, RotateCcw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { HealthStatus } from "@/lib/channel-models";

/**
 * 「封禁 / 启用」三态口径（模型目录与渠道模型面板共用）：
 * - 已启用且健康正常 → 封禁：只把 enabled 写成 false；
 * - 已停用 → 启用：enabled 写成 true，健康非正常时再串联一次复位；
 * - 已启用且熔断 / 半开 / 故障 → 启用（自动复位）：按钮标「启用」，行为仍是复位——
 *   只 POST /reset 清熔断与冷却，不改 enabled，也不会把模型标成「已停用」。
 *   （用户决议：没有独立的「复位」按钮，故障等状态下点「启用」自动重置。）
 */
export type AvailabilityAction = "ban" | "enable" | "reset";

export interface AvailabilityPlan {
  action: AvailabilityAction;
  /** 要写入的 enabled 值；undefined 表示不改。 */
  enabled?: boolean;
  /** 是否串联一次 POST /reset 清熔断。 */
  reset: boolean;
}

export interface AvailabilitySubject {
  enabled: boolean;
  health?: HealthStatus | null;
}

const BROKEN_HEALTH: HealthStatus[] = ["open", "half_open", "fault"];

export function isBrokenHealth(health?: HealthStatus | null): boolean {
  return health ? BROKEN_HEALTH.includes(health) : false;
}

export function availabilityPlan({ enabled, health }: AvailabilitySubject): AvailabilityPlan {
  if (!enabled) {
    return { action: "enable", enabled: true, reset: isBrokenHealth(health) };
  }
  if (isBrokenHealth(health)) {
    return { action: "reset", reset: true };
  }
  return { action: "ban", enabled: false, reset: false };
}

interface ActionCopy {
  label: string;
  title: string;
  confirm: string;
  Icon: typeof Ban;
}

const ACTION_COPY: Record<AvailabilityAction, ActionCopy> = {
  ban: {
    label: "封禁",
    title: "封禁模型",
    confirm: "确认封禁",
    Icon: Ban,
  },
  enable: {
    label: "启用",
    title: "启用模型",
    confirm: "确认启用",
    Icon: Power,
  },
  reset: {
    label: "启用",
    title: "启用模型",
    confirm: "确认启用",
    Icon: RotateCcw,
  },
};

export function availabilityBody(plan: AvailabilityPlan, subject: string): string {
  if (plan.action === "ban") {
    return `封禁后，${subject} 不再参与路由，直到手动启用。`;
  }
  if (plan.action === "enable") {
    return plan.reset
      ? `启用后，${subject} 重新参与路由，并清除其冷却状态。`
      : `启用后，${subject} 重新参与路由。`;
  }
  return `启用后，${subject} 清除冷却状态，立即重新参与路由。`;
}

export interface ModelAvailabilityButtonProps {
  /** 出现在 aria-label 与确认文案里的名字（上游模型或调用串）。 */
  subject: string;
  enabled: boolean;
  health?: HealthStatus | null;
  busy?: boolean;
  onConfirm: (plan: AvailabilityPlan) => void;
}

export function ModelAvailabilityButton({
  subject,
  enabled,
  health,
  busy = false,
  onConfirm,
}: ModelAvailabilityButtonProps) {
  const [open, setOpen] = useState(false);
  const plan = availabilityPlan({ enabled, health });
  const copy = ACTION_COPY[plan.action];
  const Icon = copy.Icon;
  const actionLabel = `${copy.label} ${subject}`;

  return (
    <>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-label={actionLabel}
              disabled={busy}
              onClick={() => setOpen(true)}
            >
              {busy ? (
                <Spinner size="sm" label="" aria-hidden="true" />
              ) : (
                <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              )}
              {copy.label}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{copy.title}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={copy.title}
        description={
          <>
            <span className="block">{availabilityBody(plan, subject)}</span>
            <span className="mt-2 block">
              人工操作只改这一个模型，手动组合与自动组合会立即按新状态重新选择候选。
            </span>
          </>
        }
        confirmLabel={copy.confirm}
        cancelLabel="取消"
        destructive={plan.action === "ban"}
        onConfirm={() => onConfirm(plan)}
      />
    </>
  );
}
