import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  normalizeSchedule,
  type ChannelSchedule,
  type ScheduleRule,
  type VisibleStatus,
} from "@/lib/channel-models";
import { formatDateTimeParts, type TimezoneMode } from "@/lib/time";

export type StatusTone = "ok" | "warn" | "danger" | "muted";

/** 状态色调到 Badge 变体的唯一映射，色与文字一起出现，不靠颜色单独表达。 */
const TONE_VARIANT: Record<StatusTone, "success" | "warning" | "danger" | "secondary"> = {
  ok: "success",
  warn: "warning",
  danger: "danger",
  muted: "secondary",
};

/**
 * Minimal shape shared by catalog entries, explicit combos and implicit groups.
 * Every combination view derives its status from these catalog fields so the
 * catalog table and the combos tab can never disagree.
 */
export interface StatusSubject {
  enabled: boolean;
  /** 人工轴到期时刻（epoch ms）：非 null 且未来 = 冷却（定时）。 */
  banned_until?: number | null;
  visible_status?: VisibleStatus | null;
  cooldown_until?: number | null;
  fault_until?: number | null;
  /** 熔断器连续失败次数：未到阈值时仍显示「可用」，用提示暴露近期失败。 */
  consecutive_failures?: number | null;
  status_message?: string;
}

export function formatRecoveryTime(ms: number | null | undefined, mode: TimezoneMode): string {
  const parts = formatDateTimeParts(ms, mode);
  if (!parts) return "";
  const today = formatDateTimeParts(Date.now(), mode);
  return today && today.date === parts.date
    ? parts.time.slice(0, 5)
    : parts.date.slice(5) + " " + parts.time.slice(0, 5);
}

export function statusView(
  subject: StatusSubject,
  mode: TimezoneMode,
): { label: string; tone: StatusTone; message: string } {
  const message = subject.status_message?.trim() ?? "";
  const visible = subject.visible_status ?? null;
  if (!subject.enabled || visible === "disabled") {
    const until = subject.banned_until ?? null;
    if (typeof until === "number" && until > Date.now()) {
      const at = formatCooldownTime(until, mode);
      return {
        label: "冷却（定时）",
        tone: "warn",
        message: message || "人工设置：到 " + at + " 自动恢复",
      };
    }
    return { label: "封禁", tone: "muted", message };
  }
  if (visible === "fault") {
    const until = formatRecoveryTime(subject.fault_until, mode);
    return {
      label: until ? "冷却（故障）· 预计 " + until + " 可用" : "冷却（故障）",
      tone: "danger",
      message: message || "多次熔断已升级为故障冷却，到期自动恢复",
    };
  }
  if (visible === "open") {
    const until = formatRecoveryTime(subject.cooldown_until, mode);
    const failures = subject.consecutive_failures ?? 0;
    return {
      label: until ? "冷却（故障）· 预计 " + until + " 可用" : "冷却（故障）",
      tone: "warn",
      message: [
        failures > 0 ? "连续失败 " + failures + " 次" : "",
        until ? "预计 " + until + " 恢复" : "恢复时间待定",
      ]
        .filter(Boolean)
        .join(" · "),
    };
  }
  if (visible === "half_open") {
    return { label: "半开 · 等待下次调用", tone: "warn", message };
  }
  const failures = subject.consecutive_failures ?? 0;
  const failureHint = failures > 0 ? `近期连续失败 ${failures} 次（达到阈值后进入冷却）` : "";
  return {
    label: "可用",
    tone: "ok",
    message: [failureHint, message].filter(Boolean).join(" · "),
  };
}

/** 冷却/封禁的到期时刻：始终带日期，避免「至 18:30」跨天时看不懂。 */
export function formatCooldownTime(ms: number | null | undefined, mode: TimezoneMode): string {
  const parts = formatDateTimeParts(ms, mode);
  return parts ? parts.date.slice(5) + " " + parts.time.slice(0, 5) : "";
}

/** 恢复时刻写全年/月/日与时:分；缺时刻返回空串，调用方据此改写成「恢复时间待定」。 */
export function formatRecoveryFull(ms: number | null | undefined, mode: TimezoneMode): string {
  const parts = formatDateTimeParts(ms, mode);
  if (!parts) return "";
  return parts.date.replace(/-/g, "/") + " " + parts.time.slice(0, 5);
}

/** 排期窗口求值用的墙钟：按排期时区取「星期几 + 日号 + 当日分钟数」，不做 UTC 换算。 */
interface ClockInZone {
  /** ISO 星期：1=周一 … 7=周日。 */
  weekday: number;
  /** 月内日号 1–31。 */
  dayOfMonth: number;
  /** 当日已过的分钟数。 */
  minutes: number;
}

const WEEKDAY_INDEX: Record<string, number> = {
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
  Sun: 7,
};

function clockInZone(timezone: string, ms: number): ClockInZone | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      weekday: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(ms));
    const values: Record<string, string> = {};
    for (const part of parts) {
      if (part.type !== "literal") values[part.type] = part.value;
    }
    const weekday = WEEKDAY_INDEX[values.weekday ?? ""];
    const dayOfMonth = Number(values.day);
    const hour = Number(values.hour);
    const minute = Number(values.minute);
    if (!weekday || !Number.isFinite(dayOfMonth) || !Number.isFinite(hour) || !Number.isFinite(minute)) {
      return null;
    }
    return { weekday, dayOfMonth, minutes: hour * 60 + minute };
  } catch {
    return null;
  }
}

/** HH:MM 墙钟换成当日分钟数；写坏的规则不参与求值。 */
function clockMinutes(value: string | undefined): number | null {
  if (typeof value !== "string") return null;
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return hour * 60 + minute;
}

/** 规则里的日期条件：daily 每天，weekly 看星期，monthly 看日号。 */
function ruleCoversDay(rule: ScheduleRule, clock: ClockInZone): boolean {
  if (rule.kind === "daily") return true;
  if (rule.kind === "weekly") return (rule.days ?? []).includes(clock.weekday);
  return (rule.days_of_month ?? []).includes(clock.dayOfMonth);
}

/** 单条规则是否命中该时刻：end <= start 视为跨天，跨天窗口要回看前一天的日期条件。 */
function ruleActiveAt(rule: ScheduleRule, today: ClockInZone, yesterday: ClockInZone): boolean {
  const start = clockMinutes(rule.start);
  const end = clockMinutes(rule.end);
  if (start === null || end === null) return false;
  if (end > start) {
    return ruleCoversDay(rule, today) && today.minutes >= start && today.minutes < end;
  }
  // 跨天：当天到 start 起开窗，或昨天开的窗延续到今天 end 之前（00:00–00:00 即整天）。
  return (
    (ruleCoversDay(rule, today) && today.minutes >= start) ||
    (ruleCoversDay(rule, yesterday) && today.minutes < end)
  );
}

/**
 * 排期窗口是否命中当前时刻——与后端「排期冷却窗口中」同一口径。
 * 只看窗口规则，不看封禁与熔断：排期不是封禁，三条轴各自独立生效。
 */
export function scheduleWindowActive(
  schedule: ChannelSchedule | null | undefined,
  nowMs: number,
): boolean {
  const document = normalizeSchedule(schedule);
  if (!document || document.rules.length === 0) return false;
  const today = clockInZone(document.timezone, nowMs);
  const yesterday = clockInZone(document.timezone, nowMs - 86_400_000);
  if (!today || !yesterday) return false;
  return document.rules.some((rule) => ruleActiveAt(rule, today, yesterday));
}

/**
 * 人工轴（enabled + banned_until）与自动轴（熔断/故障）合起来的展示口径：
 * - 封禁 = 永久，不出现在 /v1/models；
 * - 冷却 = 定时自愈，仍在 /v1/models，但路由跳过；
 * - 熔断 / 故障 = 自动冷却，展示上归到冷却档（标签带来源）。
 * 术语由用户裁定（ADR-070 背景 1），不要再写回「定时封禁」「停用」这些旧词。
 */
export interface AvailabilitySubject {
  enabled: boolean;
  /** 人工轴到期时刻（epoch ms）：非 null 且未来 = 定时冷却。 */
  banned_until?: number | null;
  visible_status?: VisibleStatus | null;
  cooldown_until?: number | null;
  fault_until?: number | null;
  consecutive_failures?: number | null;
  status_message?: string;
  /** 排期规则（v2）：窗口命中时按定时冷却展示。 */
  schedule?: ChannelSchedule | null;
}

export interface AvailabilityView {
  label: string;
  tone: StatusTone;
  detail: string;
  /** 悬停全文：detail 被截断时也读得到完整口径。 */
  title: string;
}

export function availabilityView(
  subject: AvailabilitySubject,
  nowMs: number,
  mode: TimezoneMode,
): AvailabilityView {
  const message = subject.status_message?.trim() ?? "";

  if (!subject.enabled) {
    const until = subject.banned_until ?? null;
    if (typeof until === "number" && until > nowMs) {
      const at = formatCooldownTime(until, mode);
      return {
        label: "冷却（定时）",
        tone: "warn",
        detail: "人工设置：到 " + at + " 自动恢复；冷却期间仍在 /v1/models，路由跳过",
        title:
          "冷却（定时）：人工按持续时间设置，到 " + at + " 自动恢复启用。冷却期间该模型仍出现在 /v1/models，但路由会跳过它。",
      };
    }
    return {
      label: "封禁",
      tone: "danger",
      detail: message || "永久封禁，不出现在 /v1/models",
      title: "封禁（永久）：不出现在 /v1/models，需要手动点「启用」才恢复。",
    };
  }

  const visible = subject.visible_status ?? null;
  if (visible === "fault") {
    const at = formatCooldownTime(subject.fault_until, mode);
    return {
      label: "冷却（故障）",
      tone: "danger",
      detail: at
        ? "多次熔断已升级为故障冷却，到 " + at + " 自动恢复"
        : "多次熔断已升级为故障冷却，到期自动恢复",
      title:
        "冷却（故障）：多次熔断已升级为故障冷却，到期自动恢复。" +
        (at ? "预计 " + at + " 恢复。" : ""),
    };
  }
  if (visible === "open") {
    // 用户口径（ADR-070 背景 1 的延伸）：熔断打开与升级为故障同属故障类，
    // 徽标统一写「冷却（故障）」；小字写全「预计 YYYY/MM/DD HH:mm 恢复」。
    // 后端 status_message 里的「预计 00:00 重试」只给时分、跨天就无从判断，
    // 这里一律以 cooldown_until 为准；没有到期时刻时如实写「恢复时间待定」，
    // 绝不拿 00:00 充数。tooltip 写「熔断冷却窗口」，与故障档的措辞区分来源。
    const at = formatRecoveryFull(subject.cooldown_until, mode);
    const failures = subject.consecutive_failures ?? 0;
    return {
      label: "冷却（故障）",
      tone: "warn",
      detail: [failures > 0 ? "连续失败 " + failures + " 次" : "", at ? "预计 " + at + " 恢复" : "恢复时间待定"]
        .filter(Boolean)
        .join(" · "),
      title:
        "冷却（故障）：熔断冷却窗口，到期自动恢复。" +
        (at ? "预计 " + at + " 恢复。" : "后端暂未给出到期时刻，恢复时间待定。"),
    };
  }
  if (visible === "half_open") {
    return {
      label: "半开",
      tone: "warn",
      detail: message || "半开：等下一次调用试探",
      title: "自动冷却：冷却结束进入半开，下一次调用决定恢复还是继续冷却。",
    };
  }

  // 排期窗口命中：与人工定时冷却同属定时类，徽标一致，靠小字区分来源。
  if (scheduleWindowActive(subject.schedule, nowMs)) {
    return {
      label: "冷却（定时）",
      tone: "warn",
      detail: "排期规则命中，窗口结束自动恢复",
      title:
        "冷却（定时）：排期规则命中，窗口结束自动恢复；窗口内该模型仍出现在 /v1/models，但路由会跳过它。",
    };
  }

  const failures = subject.consecutive_failures ?? 0;
  return {
    label: "可用",
    tone: "ok",
    detail: message || (failures > 0 ? "近期连续失败 " + failures + " 次（达到阈值后进入冷却）" : ""),
    title: failures > 0 ? "近期连续失败 " + failures + " 次；达到阈值后自动进入冷却。" : "",
  };
}

interface AvailabilityStatusCellProps {
  subject: AvailabilitySubject;
  nowMs: number;
  mode: TimezoneMode;
}

/** 状态列：徽标 + 一行说明。说明一律截断，全文在 tooltip 里。 */
export function AvailabilityStatusCell({ subject, nowMs, mode }: AvailabilityStatusCellProps) {
  const view = availabilityView(subject, nowMs, mode);
  return (
    <span className="flex min-w-0 flex-col justify-center gap-0.5">
      <Badge variant={TONE_VARIANT[view.tone]} className="w-fit max-w-full truncate">
        {view.label}
      </Badge>
      {view.detail ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="line-clamp-1 max-w-55 text-2xs leading-4 text-muted-foreground">
                {view.detail}
              </span>
            </TooltipTrigger>
            <TooltipContent title={view.label} maxLines={3}>
              {view.title || view.detail}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
    </span>
  );
}

export function StatusCell({ subject, mode }: { subject: StatusSubject; mode: TimezoneMode }) {
  const view = statusView(subject, mode);
  return (
    <span className="flex min-h-8 flex-col justify-center gap-0.5">
      <Badge variant={TONE_VARIANT[view.tone]} className="w-fit">
        {view.label}
      </Badge>
      {view.message ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="line-clamp-1 max-w-55 text-2xs leading-4 text-muted-foreground">
                {view.message}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{view.message}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
    </span>
  );
}
