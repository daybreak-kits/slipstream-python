import { describe, expect, it } from "vitest";

import {
  TIME_RANGE_OPTIONS,
  TODAY_TAIL_MS,
  formatInterval,
  formatIntervalDateTime,
  rangeBounds,
  timeRangeLabel,
  type TimeRange,
} from "@/lib/time-range";

const DAY = 24 * 3_600_000;

describe("TIME_RANGE_OPTIONS", () => {
  it("has exactly the six agreed presets in the agreed order", () => {
    expect(TIME_RANGE_OPTIONS.map((option) => option.value)).toEqual([
      "today",
      "week",
      "24h",
      "7d",
      "30d",
      "custom",
    ]);
  });

  it("labels the 24h preset as 24 时", () => {
    expect(TIME_RANGE_OPTIONS.map((option) => option.label)).toEqual([
      "今天",
      "本周",
      "24 时",
      "7 天",
      "30 天",
      "自定义",
    ]);
  });

  it("drops the removed presets", () => {
    const values = TIME_RANGE_OPTIONS.map((option) => option.value) as string[];
    expect(values).not.toContain("1h");
    expect(values).not.toContain("yesterday");
    expect(values).not.toContain("month");
  });
});

describe("rangeBounds", () => {
  const now = new Date(2026, 10, 12, 15, 30, 0, 0).getTime();

  it("24h is a rolling window ending now", () => {
    expect(rangeBounds("24h", "", "", now)).toEqual({ start: now - DAY, end: now });
  });

  it("today starts at local midnight and reserves a one-hour tail", () => {
    expect(rangeBounds("today", "", "", now)).toEqual({
      start: new Date(2026, 10, 12, 0, 0, 0, 0).getTime(),
      end: now + TODAY_TAIL_MS,
    });
  });

  it("week starts at Monday 00:00", () => {
    const monday = new Date(now);
    monday.setHours(0, 0, 0, 0);
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    expect(rangeBounds("week", "", "", now)).toEqual({ start: monday.getTime(), end: now });
  });

  it("7d and 30d are rolling windows", () => {
    expect(rangeBounds("7d", "", "", now)).toEqual({ start: now - 7 * DAY, end: now });
    expect(rangeBounds("30d", "", "", now)).toEqual({ start: now - 30 * DAY, end: now });
  });

  it("custom parses both bounds and ignores invalid input", () => {
    expect(
      rangeBounds("custom", "2026-11-01T08:00", "2026-11-02T09:30", now),
    ).toEqual({
      start: new Date("2026-11-01T08:00").getTime(),
      end: new Date("2026-11-02T09:30").getTime(),
    });
    expect(rangeBounds("custom", "", "", now)).toEqual({ start: undefined, end: undefined });
    expect(rangeBounds("custom", "not-a-date", "", now)).toEqual({
      start: undefined,
      end: undefined,
    });
  });
});

describe("timeRangeLabel", () => {
  const cases: Array<[TimeRange, string]> = [
    ["today", "今天"],
    ["week", "本周"],
    ["24h", "24 时"],
    ["7d", "7 天"],
    ["30d", "30 天"],
  ];

  it.each(cases)("labels %s as %s", (range, label) => {
    expect(timeRangeLabel(range)).toBe(label);
  });

  it("formats a valid custom range", () => {
    expect(timeRangeLabel("custom", "2026-11-01T08:05", "2026-11-02T09:30")).toBe(
      "11/01 08:05 – 11/02 09:30",
    );
  });

  it("falls back for an incomplete custom range", () => {
    expect(timeRangeLabel("custom", "", "")).toBe("自定义时间");
  });
});

describe("interval formatting", () => {
  it("formats a full second-precision timestamp", () => {
    const ms = new Date(2026, 10, 12, 15, 30, 45).getTime();
    expect(formatIntervalDateTime(ms)).toBe("2026/11/12 15:30:45");
    expect(formatIntervalDateTime(Date.UTC(2026, 10, 12, 15, 30, 45), "utc")).toBe(
      "2026/11/12 15:30:45",
    );
  });

  it("joins both bounds and skips incomplete ones", () => {
    const start = new Date(2026, 10, 12, 0, 0, 0).getTime();
    const end = new Date(2026, 10, 12, 13, 30, 34).getTime();
    // 触发按钮上的区间文案：单横线、无空格，秒级（用户 2026-11-14 反馈）。
    expect(formatInterval(start, end)).toBe("2026/11/12 00:00:00-2026/11/12 13:30:34");
    expect(formatInterval(undefined, end)).toBeUndefined();
    expect(formatInterval(start, undefined)).toBeUndefined();
  });
});
