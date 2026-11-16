import { useEffect, useState } from "react";

import {
  ScheduleRulesFieldset,
  ScheduleTimezonePicker,
  describeSchedule,
  validateScheduleRules,
} from "@/components/models/schedule-rules";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { formatCooldownTime } from "@/components/models/model-status";
import {
  DEFAULT_SCHEDULE_TIMEZONE,
  emptyRule,
  localScheduleTimezone,
  normalizeSchedule,
  type ChannelSchedule,
  type ScheduleDocument,
  type ScheduleRule,
} from "@/lib/channel-models";
import { isValidTimezone } from "@/lib/timezone";
import { useTimezone } from "@/lib/time";

export interface CooldownPresetOption {
  value: string;
  label: string;
  /** 0 表示「自定义时刻」。 */
  hours: number;
}

export const COOLDOWN_PRESETS: CooldownPresetOption[] = [
  { value: "1h", label: "1 小时", hours: 1 },
  { value: "24h", label: "24 小时", hours: 24 },
  { value: "7d", label: "7 天", hours: 24 * 7 },
  { value: "custom", label: "自定义时刻", hours: 0 },
];

/** datetime-local 的本地墙钟值 → epoch ms；空串或非法返回 null。 */
export function parseLocalDateTime(value: string): number | null {
  if (!value) return null;
  const parsed = new Date(value);
  const ms = parsed.getTime();
  return Number.isFinite(ms) ? ms : null;
}

/** 预设 + 自定义组合成到期时刻；无法确定时返回 null。 */
export function cooldownUntil(
  preset: string,
  customValue: string,
  nowMs: number = Date.now(),
): number | null {
  const option = COOLDOWN_PRESETS.find((item) => item.value === preset);
  if (!option) return null;
  if (option.hours > 0) return nowMs + option.hours * 60 * 60 * 1000;
  return parseLocalDateTime(customValue);
}

/* ---------------------------------------------------------------------------
 * 两种冷却方式共用的草稿模型（task-136）：
 * - duration 沿用 1h/24h/7d/自定义时刻，提交 op=cooldown（写 banned_until）；
 * - window 复用渠道详情「计划窗口」那套规则编辑器，提交 op=schedule（schedule_json v2）。
 * 行内封禁弹窗与批量冷却弹窗共用同一段 UI 与提交分流，别复制第二套。
 * ------------------------------------------------------------------------- */

export type CooldownMethod = "duration" | "window";

/** 冷却弹窗的提交结果；kind 决定走哪个后端操作。 */
export type CooldownSubmit =
  | { kind: "cooldown"; untilMs: number }
  | { kind: "schedule"; schedule: ScheduleDocument };

export interface CooldownDraft {
  method: CooldownMethod;
  preset: string;
  customValue: string;
  timezone: string;
  rules: ScheduleRule[];
}

/** 打开弹窗时的初始草稿；带既有排期时把窗口方式预填成它，方便在原基础上改。 */
export function initialCooldownDraft(
  schedule?: ChannelSchedule | null,
): CooldownDraft {
  const normalized = normalizeSchedule(schedule);
  const local = localScheduleTimezone();
  return {
    method: "duration",
    preset: "1h",
    customValue: "",
    timezone: normalized?.timezone || (isValidTimezone(local) ? local : DEFAULT_SCHEDULE_TIMEZONE),
    rules: normalized?.rules?.length ? normalized.rules.map((rule) => ({ ...rule })) : [emptyRule()],
  };
}

/** 规则里的 days / days_of_month 排序后再提交，与渠道表单同一形状。 */
function sortedRules(rules: readonly ScheduleRule[]): ScheduleRule[] {
  return rules.map((rule) => ({
    ...rule,
    ...(rule.days ? { days: [...rule.days].sort((left, right) => left - right) } : {}),
    ...(rule.days_of_month
      ? { days_of_month: [...rule.days_of_month].sort((left, right) => left - right) }
      : {}),
  }));
}

/** 草稿 → 提交载荷；任一方式不合法时返回 null（按钮据此禁用）。 */
export function cooldownDraftSubmit(draft: CooldownDraft): CooldownSubmit | null {
  if (draft.method === "duration") {
    const until = cooldownUntil(draft.preset, draft.customValue);
    return until !== null && until > Date.now() ? { kind: "cooldown", untilMs: until } : null;
  }
  const timezone = draft.timezone.trim();
  if (!timezone || !isValidTimezone(timezone)) return null;
  if (validateScheduleRules(draft.rules) !== null) return null;
  return { kind: "schedule", schedule: { version: 2, timezone, rules: sortedRules(draft.rules) } };
}

export interface CooldownFieldsProps {
  idPrefix: string;
  preset: string;
  onPresetChange: (value: string) => void;
  customValue: string;
  onCustomChange: (value: string) => void;
  /** 已经在冷却中时提示「会覆盖原到期时间」。 */
  currentUntil?: number | null;
}

/**
 * 「多久之后恢复」的控件：1 小时 / 24 小时 / 7 天 / 自定义时刻。
 * 现在是持续时间方式的内层控件，由 CooldownDraftFields 包着使用。
 */
export function CooldownFields({
  idPrefix,
  preset,
  onPresetChange,
  customValue,
  onCustomChange,
  currentUntil = null,
}: CooldownFieldsProps) {
  const { mode } = useTimezone();
  const until = cooldownUntil(preset, customValue);
  const invalid = preset === "custom" && (until === null || until <= Date.now());
  const preview = until !== null ? formatCooldownTime(until, mode) : "";

  return (
    <Field>
      <FieldLabel htmlFor={idPrefix + "-preset"}>冷却时长</FieldLabel>
      <div id={idPrefix + "-preset"} className="flex flex-wrap gap-1.5" role="group" aria-label="冷却时长">
        {COOLDOWN_PRESETS.map((option) => (
          <Button
            key={option.value}
            type="button"
            size="sm"
            variant={preset === option.value ? "default" : "outline"}
            aria-pressed={preset === option.value}
            onClick={() => onPresetChange(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>
      {preset === "custom" ? (
        <Input
          className="h-10"
          type="datetime-local"
          aria-label="自定义恢复时刻"
          value={customValue}
          onChange={(event) => onCustomChange(event.target.value)}
        />
      ) : null}
      <FieldDescription>
        {invalid
          ? "请选择一个未来的时刻。"
          : preview
            ? "到期时刻 " + preview + "（本地时区），到点自动恢复启用。"
            : "到期后自动恢复启用。"}
        {typeof currentUntil === "number" && currentUntil > Date.now()
          ? " 当前已在冷却中，确认后会覆盖原到期时间。"
          : ""}
      </FieldDescription>
    </Field>
  );
}

export interface CooldownDraftFieldsProps {
  idPrefix: string;
  draft: CooldownDraft;
  onChange: (draft: CooldownDraft) => void;
  /** 已经在冷却中时提示「会覆盖原到期时间」（持续时间方式）。 */
  currentUntil?: number | null;
}

/**
 * 冷却方式的完整选择 UI：按持续时间 / 按计划窗口。
 * 行内「封禁」弹窗与批量「冷却」弹窗都渲染这一份，差异说明与校验也在这里。
 */
export function CooldownDraftFields({
  idPrefix,
  draft,
  onChange,
  currentUntil = null,
}: CooldownDraftFieldsProps) {
  const timezoneInvalid = !isValidTimezone(draft.timezone.trim());
  const rulesError =
    draft.method === "window" ? validateScheduleRules(draft.rules) : null;

  return (
    <div className="flex flex-col gap-3">
      <RadioGroup
        value={draft.method}
        onValueChange={(value) => onChange({ ...draft, method: value as CooldownMethod })}
        aria-label="冷却方式"
      >
        <div className="flex items-start gap-2">
          <RadioGroupItem value="duration" id={idPrefix + "-method-duration"} className="mt-0.5" />
          <div className="flex flex-col gap-0.5">
            <label htmlFor={idPrefix + "-method-duration"} className="cursor-pointer text-xs font-medium text-title">
              按持续时间
            </label>
            <span className="text-2xs text-muted-foreground">
              一次性冷却：到指定时刻自动恢复（提交 op=cooldown）。
            </span>
          </div>
        </div>
        <div className="flex items-start gap-2">
          <RadioGroupItem value="window" id={idPrefix + "-method-window"} className="mt-0.5" />
          <div className="flex flex-col gap-0.5">
            <label htmlFor={idPrefix + "-method-window"} className="cursor-pointer text-xs font-medium text-title">
              按计划窗口
            </label>
            <span className="text-2xs text-muted-foreground">
              重复时间窗：窗口内自动冷却、窗口外自动恢复（提交为排期规则）。
            </span>
          </div>
        </div>
      </RadioGroup>

      {draft.method === "duration" ? (
        <CooldownFields
          idPrefix={idPrefix}
          preset={draft.preset}
          onPresetChange={(preset) => onChange({ ...draft, preset })}
          customValue={draft.customValue}
          onCustomChange={(customValue) => onChange({ ...draft, customValue })}
          currentUntil={currentUntil}
        />
      ) : (
        <>
          <Field>
            <FieldLabel htmlFor={idPrefix + "-timezone"}>IANA 时区</FieldLabel>
            <ScheduleTimezonePicker
              id={idPrefix + "-timezone"}
              value={draft.timezone}
              invalid={timezoneInvalid}
              onChange={(timezone) => onChange({ ...draft, timezone })}
            />
            <FieldDescription>
              窗口按该时区的墙钟生效，不随夏令时漂移；冷却期间模型仍出现在 /v1/models，路由跳过。
            </FieldDescription>
          </Field>
          <ScheduleRulesFieldset
            rules={draft.rules}
            onChange={(rules) => onChange({ ...draft, rules })}
            idPrefix={idPrefix + "-rule"}
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
      )}
    </div>
  );
}

export interface CooldownDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 出现在标题与描述里的对象名，如「3 个模型」或「channel-a/A」。 */
  subject: string;
  currentUntil?: number | null;
  /** 单选某个模型时传入它的既有排期，窗口方式按它预填。 */
  initialSchedule?: ChannelSchedule | null;
  busy?: boolean;
  onConfirm: (submit: CooldownSubmit) => void;
}

/** 批量/行内「定时冷却」弹窗：持续时间与计划窗口两种方式共用。 */
export function CooldownDialog({
  open,
  onOpenChange,
  subject,
  currentUntil = null,
  initialSchedule = null,
  busy = false,
  onConfirm,
}: CooldownDialogProps) {
  const [draft, setDraft] = useState<CooldownDraft>(() => initialCooldownDraft());

  useEffect(() => {
    if (!open) return;
    setDraft(initialCooldownDraft(initialSchedule));
  }, [open, initialSchedule]);

  const submit = cooldownDraftSubmit(draft);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>定时冷却 {subject}</DialogTitle>
          <DialogDescription>
            冷却期间 {subject} 仍出现在 /v1/models，但路由会跳过它；两种方式都是到点自动恢复启用（冷却 =
            定时自愈，封禁才是永久）。
          </DialogDescription>
        </DialogHeader>
        <CooldownDraftFields
          idPrefix="cooldown"
          draft={draft}
          onChange={setDraft}
          currentUntil={currentUntil}
        />
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={submit === null || busy}
            onClick={() => {
              if (submit === null) return;
              onConfirm(submit);
            }}
          >
            {busy ? "提交中…" : "确认冷却"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
