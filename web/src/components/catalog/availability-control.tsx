import { Ban, Power, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import {
  CooldownDraft,
  CooldownFields,
  cooldownDraftSubmit,
  initialCooldownDraft,
} from "@/components/models/cooldown-dialog";
import {
  ScheduleRulesFieldset,
  ScheduleTimezonePicker,
  describeSchedule,
  validateScheduleRules,
} from "@/components/models/schedule-rules";
import { formatCooldownTime } from "@/components/models/model-status";
import { availabilityBody, availabilityPlan } from "@/components/models/model-availability";
import type { ChannelSchedule, HealthStatus, ScheduleDocument } from "@/lib/channel-models";
import { useTimezone } from "@/lib/time";
import { isValidTimezone } from "@/lib/timezone";

/** 封禁弹窗的提交结果：
 * - availability 是「永久封禁 / 启用复位」（写 enabled + banned_until，bannedUntil null = 永久）；
 * - cooldown 是「按持续时间冷却」（走 op=cooldown，写 banned_until）；
 * - schedule 是「按冷却窗口冷却」（走 op=schedule，写 schedule_json v2）。
 */
export type AvailabilitySubmit =
  | { type: "availability"; enabled: boolean; bannedUntil: number | null; reset: boolean }
  | { type: "cooldown"; bannedUntil: number }
  | { type: "schedule"; schedule: ScheduleDocument };

export interface AvailabilityControlProps {
  /** 出现在 aria-label 与确认文案里的名字（调用串或上游模型）。 */
  subject: string;
  enabled: boolean;
  health?: HealthStatus | null;
  bannedUntil?: number | null;
  /** 该模型的既有排期：窗口方式按它预填。 */
  initialSchedule?: ChannelSchedule | null;
  busy?: boolean;
  onConfirm: (submit: AvailabilitySubmit) => void;
}

/** 2026-11-18 用户裁定：停止方式压平成一组三个单选，不再出现嵌套的
 * 「冷却（定时）→ 按持续时间/按冷却窗口」两层同视觉层级的选项。 */
type StopMode = "permanent" | "duration" | "window";

/**
 * 直连行的「封禁 / 启用」控件，口径按用户裁定（ADR-070 + task-136）：
 * - 封禁 = 永久，不出现在 /v1/models，手动启用才恢复；
 * - 冷却 = 定时自愈，仍出现在 /v1/models，路由跳过；
 *   两种方式：定时冷却（1h/24h/7d/自定义时刻）与冷却窗口（复用排期规则编辑器）；
 * - 健康异常时按钮仍是「启用」，行为是复位（与 components/models/model-availability 一致）。
 */
export function AvailabilityControl({
  subject,
  enabled,
  health,
  bannedUntil = null,
  initialSchedule = null,
  busy = false,
  onConfirm,
}: AvailabilityControlProps) {
  const { mode } = useTimezone();
  const [stopOpen, setStopOpen] = useState(false);
  const [enableOpen, setEnableOpen] = useState(false);
  const [stopMode, setStopMode] = useState<StopMode>("permanent");
  const [draft, setDraft] = useState<CooldownDraft>(() => initialCooldownDraft());

  const plan = availabilityPlan({ enabled, health });
  const action = plan.action;
  const label = action === "ban" ? "封禁" : "启用";
  const Icon = action === "ban" ? Ban : action === "reset" ? RotateCcw : Power;

  useEffect(() => {
    if (!stopOpen && !enableOpen) return;
    if (stopOpen) {
      setStopMode("permanent");
      setDraft(initialCooldownDraft(initialSchedule));
    }
  }, [stopOpen, enableOpen, initialSchedule]);

  // 永久封禁不走冷却草稿；两种冷却共用一份草稿，按当前方式校验。
  const modeSubmit =
    stopMode === "permanent" ? null : cooldownDraftSubmit({ ...draft, method: stopMode });
  const modeInvalid = stopMode !== "permanent" && modeSubmit === null;
  const coolingNow = typeof bannedUntil === "number" && bannedUntil > Date.now();
  const timezoneInvalid = !isValidTimezone(draft.timezone.trim());
  const rulesError = stopMode === "window" ? validateScheduleRules(draft.rules) : null;

  const openControl = () => {
    if (action === "ban") setStopOpen(true);
    else setEnableOpen(true);
  };

  const selectStopMode = (value: string) => {
    const mode = value as StopMode;
    setStopMode(mode);
    if (mode !== "permanent") setDraft((previous) => ({ ...previous, method: mode }));
  };

  const confirmStop = () => {
    if (stopMode === "permanent") {
      onConfirm({ type: "availability", enabled: false, bannedUntil: null, reset: false });
      return;
    }
    if (modeSubmit === null) return;
    onConfirm(
      modeSubmit.kind === "cooldown"
        ? { type: "cooldown", bannedUntil: modeSubmit.untilMs }
        : { type: "schedule", schedule: modeSubmit.schedule },
    );
  };

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        aria-label={label + " " + subject}
        disabled={busy}
        onClick={openControl}
        // 危险前景统一 --danger-text（ui.md §2.3：同一控件不混用两种危险色）
        className={action === "ban" ? "text-danger-text" : "text-success-text"}
      >
        {busy ? (
          <Spinner size="sm" label="" aria-hidden="true" />
        ) : (
          <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        )}
        {label}
      </Button>

      <Dialog open={stopOpen} onOpenChange={setStopOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            {/* 封禁是危险动作：标题问句化（audit N7）。含冷却表单故保留 Dialog 形态，只改标题。 */}
            <DialogTitle>封禁 {subject}？</DialogTitle>
            <DialogDescription>
              两种方式都让模型不接流量：封禁是永久的、不出现在 /v1/models；冷却到期自动恢复、期间仍可见。
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <RadioGroup
              value={stopMode}
              onValueChange={selectStopMode}
              aria-label="停止方式"
            >
              <div className="flex items-start gap-2">
                <RadioGroupItem value="permanent" id="row-stop-permanent" className="mt-0.5" />
                <div className="flex flex-col gap-0.5">
                  <label htmlFor="row-stop-permanent" className="cursor-pointer text-xs font-medium text-title">
                    封禁（永久）
                  </label>
                  <span className="text-2xs text-muted-foreground">
                    不再出现在 /v1/models，手动启用才恢复。
                  </span>
                </div>
              </div>
              <div className="flex items-start gap-2">
                <RadioGroupItem value="duration" id="row-stop-duration" className="mt-0.5" />
                <div className="flex flex-col gap-0.5">
                  <label htmlFor="row-stop-duration" className="cursor-pointer text-xs font-medium text-title">
                    定时冷却
                  </label>
                  <span className="text-2xs text-muted-foreground">
                    一次性冷却：到指定时刻自动恢复；期间仍出现在 /v1/models，路由跳过。
                  </span>
                </div>
              </div>
              <div className="flex items-start gap-2">
                <RadioGroupItem value="window" id="row-stop-window" className="mt-0.5" />
                <div className="flex flex-col gap-0.5">
                  <label htmlFor="row-stop-window" className="cursor-pointer text-xs font-medium text-title">
                    冷却窗口
                  </label>
                  <span className="text-2xs text-muted-foreground">
                    重复时段：窗口内自动冷却、窗口外自动恢复；期间同样可见、路由跳过。
                  </span>
                </div>
              </div>
            </RadioGroup>

            {stopMode === "duration" ? (
              <CooldownFields
                idPrefix="row-cooldown"
                preset={draft.preset}
                onPresetChange={(preset) => setDraft((previous) => ({ ...previous, preset }))}
                customValue={draft.customValue}
                onCustomChange={(customValue) => setDraft((previous) => ({ ...previous, customValue }))}
                currentUntil={bannedUntil}
              />
            ) : null}

            {stopMode === "window" ? (
              <>
                <Field>
                  <FieldLabel htmlFor="row-cooldown-timezone">IANA 时区</FieldLabel>
                  <ScheduleTimezonePicker
                    id="row-cooldown-timezone"
                    value={draft.timezone}
                    invalid={timezoneInvalid}
                    onChange={(timezone) => setDraft((previous) => ({ ...previous, timezone }))}
                  />
                  <FieldDescription>
                    窗口按该时区的墙钟生效，不随夏令时漂移；冷却期间模型仍出现在 /v1/models，路由跳过。
                  </FieldDescription>
                </Field>
                <ScheduleRulesFieldset
                  rules={draft.rules}
                  onChange={(rules) => setDraft((previous) => ({ ...previous, rules }))}
                  idPrefix="row-cooldown-rule"
                  addLabel="再加一条规则"
                />
                <FieldDescription aria-live="polite">
                  {timezoneInvalid ? (
                    <span className="text-danger-text">请选择一个 IANA 时区</span>
                  ) : rulesError ? (
                    <span className="text-danger-text">{rulesError}</span>
                  ) : (
                    describeSchedule(draft.rules)
                  )}
                </FieldDescription>
              </>
            ) : null}

            {stopMode !== "permanent" && coolingNow ? (
              <FieldDescription>
                当前冷却到 {formatCooldownTime(bannedUntil as number, mode)}；确认后会覆盖原到期时间。
              </FieldDescription>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setStopOpen(false)}>
              取消
            </Button>
            <Button
              variant={stopMode === "permanent" ? "danger" : "default"}
              disabled={busy || modeInvalid}
              onClick={confirmStop}
            >
              {busy ? "提交中…" : stopMode === "permanent" ? "确认封禁" : "确认冷却"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={enableOpen}
        onOpenChange={(next) => {
          if (!next) setEnableOpen(false);
        }}
        title="启用模型"
        description={
          <>
            <span className="block">{availabilityBody(plan, subject)}</span>
            <span className="mt-2 block">
              启用会同时清除定时冷却；自动组合会立即按新状态重新选择候选。
            </span>
          </>
        }
        confirmLabel="确认启用"
        onConfirm={() => {
          onConfirm({
            type: "availability",
            enabled: true,
            bannedUntil: null,
            reset: plan.action === "reset",
          });
        }}
      />
    </>
  );
}
