import { describe, expect, it } from "vitest";

import { formatDuration, formatDurationAdaptive } from "@/lib/duration";
import { isLatencyMissing } from "@/lib/latency";

describe("formatDuration", () => {
  it("renders seconds with two decimals by default", () => {
    expect(formatDuration(75_765)).toBe("75.77s");
    expect(formatDuration(12_340)).toBe("12.34s");
    expect(formatDuration(1_500)).toBe("1.50s");
    expect(formatDuration(120)).toBe("0.12s");
    expect(formatDuration(0)).toBe("0.00s");
  });

  it("rounds half up at the centisecond boundary", () => {
    expect(formatDuration(59_999)).toBe("60.00s");
    expect(formatDuration(60_000)).toBe("60.00s");
    expect(formatDuration(999_999)).toBe("1000.00s");
    expect(formatDuration(10)).toBe("0.01s");
    expect(formatDuration(4)).toBe("0.00s");
  });

  it("uses a dash for missing or invalid values", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(-1)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
    expect(formatDuration(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatDurationAdaptive", () => {
  it("keeps seconds with two decimals below one minute", () => {
    expect(formatDurationAdaptive(75_765)).toBe("1m15s");
    expect(formatDurationAdaptive(59_999)).toBe("1m00s");
    expect(formatDurationAdaptive(59_000)).toBe("59.00s");
    expect(formatDurationAdaptive(12_340)).toBe("12.34s");
    expect(formatDurationAdaptive(0)).toBe("0.00s");
  });

  it("switches to minutes, hours and days at the scale boundaries", () => {
    expect(formatDurationAdaptive(60_000)).toBe("1m00s");
    expect(formatDurationAdaptive(999_999)).toBe("16m40s");
    expect(formatDurationAdaptive(3_600_000)).toBe("1h00m");
    expect(formatDurationAdaptive(3_725_000)).toBe("1h02m");
    expect(formatDurationAdaptive(86_400_000)).toBe("1d00h");
    expect(formatDurationAdaptive(183_600_000)).toBe("2d03h");
  });

  it("uses a dash for missing or invalid values", () => {
    expect(formatDurationAdaptive(null)).toBe("—");
    expect(formatDurationAdaptive(undefined)).toBe("—");
    expect(formatDurationAdaptive(-5)).toBe("—");
  });
});

describe("isLatencyMissing (ADR-082 口径族：无数据不渲染 0.00s)", () => {
  it("treats 0/null ttft and ttfa on failed requests as no data", () => {
    expect(isLatencyMissing(0, "ttft", true)).toBe(true);
    expect(isLatencyMissing(0, "ttfa", true)).toBe(true);
    expect(isLatencyMissing(null, "ttft", true)).toBe(true);
    expect(isLatencyMissing(Number.NaN, "ttfa", true)).toBe(true);
    // 输出中途才失败的请求：真实测到的首字/首答保留。
    expect(isLatencyMissing(120, "ttft", true)).toBe(false);
    expect(isLatencyMissing(150, "ttfa", true)).toBe(false);
  });

  it("keeps 0 ttft/ttfa meaningful on succeeded requests", () => {
    expect(isLatencyMissing(0, "ttft", false)).toBe(false);
    expect(isLatencyMissing(0, "ttfa", false)).toBe(false);
    expect(isLatencyMissing(null, "ttft", false)).toBe(true);
  });

  it("treats total 0 as no upstream report regardless of outcome", () => {
    expect(isLatencyMissing(0, "total", true)).toBe(true);
    expect(isLatencyMissing(0, "total", false)).toBe(true);
    expect(isLatencyMissing(1500, "total", false)).toBe(false);
    expect(isLatencyMissing(null, "total", false)).toBe(true);
  });
});
