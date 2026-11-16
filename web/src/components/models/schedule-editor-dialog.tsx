import { useEffect, useState } from "react";

import {
  ScheduleRulesFieldset,
  ScheduleTimezonePicker,
  describeSchedule,
  emptyRule,
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
import {
  DEFAULT_SCHEDULE_TIMEZONE,
  localScheduleTimezone,
  type ScheduleDocument,
  type ScheduleRule,
} from "@/lib/channel-models";
import { isValidTimezone } from "@/lib/timezone";

// 纯函数住在 lib，共享组件层再导出；这里继续转出，保持既有 import 路径可用。
export {
  describeSchedule,
  describeScheduleRule,
  emptyRule,
  validateScheduleRule,
  validateScheduleRules,
} from "@/components/models/schedule-rules";

export interface ScheduleEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  subject: string;
  busy?: boolean;
  /** 编辑既有排期时传入：非法的历史时区会原样显示并阻止提交。 */
  initialSchedule?: ScheduleDocument | null;
  onConfirm: (schedule: ScheduleDocument) => void;
}

/**
 * 排期（定时冷却窗口）编辑器：每天 / 每周 / 每月三种规则，可加多条，规则之间是并集。
 * 只写「什么时候该关」，永久封禁不在这里（ADR-070 §1.1）。
 */
export function ScheduleEditorDialog({
  open,
  onOpenChange,
  subject,
  busy = false,
  initialSchedule = null,
  onConfirm,
}: ScheduleEditorDialogProps) {
  const [timezone, setTimezone] = useState(DEFAULT_SCHEDULE_TIMEZONE);
  const [timezoneNotice, setTimezoneNotice] = useState<string | null>(null);
  const [rules, setRules] = useState<ScheduleRule[]>([emptyRule()]);

  useEffect(() => {
    if (!open) return;
    const stored = initialSchedule?.timezone?.trim() ?? "";
    if (stored && !isValidTimezone(stored)) {
      setTimezone(stored);
      setTimezoneNotice("历史值「" + stored + "」不是合法 IANA 时区，冷却不会生效，请重新选择");
    } else {
      // 浏览器给的本地时区若是别名/大小写变体，校验会判非法；此时回落到默认时区，
      // 不让用户一打开看到红字（历史值非法的情况走上面的分支，必须显式提示）。
      const local = localScheduleTimezone();
      setTimezone(stored || (isValidTimezone(local) ? local : DEFAULT_SCHEDULE_TIMEZONE));
      setTimezoneNotice(null);
    }
    setRules(
      initialSchedule?.rules?.length
        ? initialSchedule.rules.map((rule) => ({ ...rule }))
        : [emptyRule()],
    );
  }, [open, initialSchedule]);

  const rulesError = validateScheduleRules(rules);
  const timezoneInvalid = !isValidTimezone(timezone);
  const timezoneError = timezoneInvalid
    ? timezone.trim()
      ? "时区「" + timezone.trim() + "」不是合法 IANA 时区"
      : "请选择一个 IANA 时区"
    : null;
  const error = timezoneError ?? rulesError;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>排期 {subject}</DialogTitle>
          <DialogDescription>
            落在规则窗口内的时间里，{subject} 自动冷却（仍在 /v1/models，路由跳过），窗口结束后自动恢复。
            多条规则是并集：命中任意一条就冷却；结束时刻早于开始时刻表示跨天，窗口归属起始日。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <Field>
            <FieldLabel htmlFor="schedule-timezone">IANA 时区</FieldLabel>
            <ScheduleTimezonePicker
              value={timezone}
              invalid={timezoneInvalid}
              onChange={(next) => {
                setTimezone(next);
                setTimezoneNotice(null);
              }}
            />
            <FieldDescription>
              规则里的时刻都是该时区的当地墙钟时间，不随夏令时漂移；一次批量共用一个时区。
            </FieldDescription>
            {timezoneNotice || timezoneError ? (
              <FieldDescription aria-live="polite">
                {timezoneNotice ? (
                  <span className={timezoneInvalid ? "text-danger-text" : "text-warning-text"}>
                    {timezoneNotice}
                  </span>
                ) : (
                  <span className="text-danger-text">{timezoneError}</span>
                )}
              </FieldDescription>
            ) : null}
          </Field>

          <ScheduleRulesFieldset rules={rules} onChange={setRules} />

          <FieldDescription aria-live="polite">
            {rulesError ? (
              <span className="text-danger-text">{rulesError}</span>
            ) : (
              describeSchedule(rules)
            )}
          </FieldDescription>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={busy || error !== null}
            onClick={() => {
              if (error !== null) return;
              onConfirm({
                version: 2,
                timezone: timezone.trim(),
                rules,
              });
            }}
          >
            {busy ? "提交中…" : "保存排期"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
