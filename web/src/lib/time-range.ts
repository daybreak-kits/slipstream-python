export type TimeRange = "today" | "week" | "24h" | "7d" | "30d" | "custom";

export const TIME_RANGE_OPTIONS: Array<{ value: TimeRange; label: string }> = [
  { value: "today", label: "今天" },
  { value: "week", label: "本周" },
  { value: "24h", label: "24 时" },
  { value: "7d", label: "7 天" },
  { value: "30d", label: "30 天" },
  { value: "custom", label: "自定义" },
];

const DAY = 24 * 3_600_000;
/** 「今天」尾部余量：刚搜索完再看时，最近 1 小时内的新日志仍落在区间内，避免漏行。 */
export const TODAY_TAIL_MS = 3_600_000;

export interface TimeBounds {
  start?: number;
  end?: number;
}

export function rangeBounds(
  range: TimeRange,
  customStart = "",
  customEnd = "",
  now = Date.now(),
): TimeBounds {
  if (range === "24h") return { start: now - DAY, end: now };
  if (range === "7d") return { start: now - 7 * DAY, end: now };
  if (range === "30d") return { start: now - 30 * DAY, end: now };
  if (range === "today") {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    // 尾部留 1 小时余量，其余相对区间（week / 24h / 7d / 30d）仍以 now 收尾。
    return { start: start.getTime(), end: now + TODAY_TAIL_MS };
  }
  if (range === "week") {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    const mondayOffset = (start.getDay() + 6) % 7;
    start.setDate(start.getDate() - mondayOffset);
    return { start: start.getTime(), end: now };
  }
  if (range === "custom") {
    const start = customStart ? new Date(customStart).getTime() : undefined;
    const end = customEnd ? new Date(customEnd).getTime() : undefined;
    return {
      start: Number.isFinite(start) ? start : undefined,
      end: Number.isFinite(end) ? end : undefined,
    };
  }
  return {};
}

export function timeRangeLabel(
  range: TimeRange,
  customStart = "",
  customEnd = "",
): string {
  const preset = TIME_RANGE_OPTIONS.find((option) => option.value === range);
  if (range !== "custom") return preset?.label ?? "24 时";
  const start = customStart ? new Date(customStart) : null;
  const end = customEnd ? new Date(customEnd) : null;
  if (!start || !end || !Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) {
    return "自定义时间";
  }
  const fmt = (date: Date) =>
    `${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")} ${String(
      date.getHours(),
    ).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return `${fmt(start)} – ${fmt(end)}`;
}

export type IntervalTimezone = "local" | "utc";

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/** 秒级完整时刻，形如 2026/11/11 13:30:34；默认按浏览器本地时区展示。 */
export function formatIntervalDateTime(ms: number, timezone: IntervalTimezone = "local"): string {
  const date = new Date(ms);
  const utc = timezone === "utc";
  const year = utc ? date.getUTCFullYear() : date.getFullYear();
  const month = (utc ? date.getUTCMonth() : date.getMonth()) + 1;
  const day = utc ? date.getUTCDate() : date.getDate();
  const hours = utc ? date.getUTCHours() : date.getHours();
  const minutes = utc ? date.getUTCMinutes() : date.getMinutes();
  const seconds = utc ? date.getUTCSeconds() : date.getSeconds();
  return `${year}/${pad2(month)}/${pad2(day)} ${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
}

/** 把区间两端拼成「开始-结束」（单横线，无空格）；缺任一端时返回 undefined。 */
export function formatInterval(
  start: number | undefined,
  end: number | undefined,
  timezone: IntervalTimezone = "local",
): string | undefined {
  if (start === undefined || end === undefined) return undefined;
  return `${formatIntervalDateTime(start, timezone)}-${formatIntervalDateTime(end, timezone)}`;
}
