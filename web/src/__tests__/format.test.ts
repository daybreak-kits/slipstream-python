import { describe, expect, it } from "vitest";

import {
  formatCacheHitRate,
  formatCachePercent,
  formatCompact,
  formatPercent,
  formatRate,
  formatSpanMinutes,
  formatThroughput,
  formatUsdCompact,
  formatUsdFine,
} from "@/lib/format";

describe("formatPercent", () => {
  it("renders one decimal by default and honours the requested precision", () => {
    expect(formatPercent(0.7234)).toBe("72.3%");
    expect(formatPercent(0.5, 2)).toBe("50.00%");
    expect(formatPercent(0.0167, 2)).toBe("1.67%");
  });

  it("renders a dash for missing or invalid ratios", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatPercent(Number.NaN)).toBe("—");
  });
});

describe("formatCacheHitRate", () => {
  it("uses one decimal everywhere and hides zero or missing samples", () => {
    expect(formatCacheHitRate(0.1234)).toBe("12.3%");
    expect(formatCacheHitRate(1)).toBe("100.0%");
    expect(formatCacheHitRate(0)).toBe("—");
    expect(formatCacheHitRate(null)).toBe("—");
  });

  it("keeps the short alias in step with the dashboard wording", () => {
    expect(formatCachePercent(0.1234)).toBe(formatCacheHitRate(0.1234));
    expect(formatCachePercent(0)).toBe("—");
  });
});

describe("compact numbers", () => {
  it("keeps two decimals on every K/M/B step", () => {
    expect(formatCompact(1500)).toBe("1.50K");
    expect(formatCompact(2_500_000)).toBe("2.50M");
    expect(formatCompact(3_400_000_000)).toBe("3.40B");
    expect(formatCompact(1234.5)).toBe("1.23K");
    expect(formatCompact(999.5)).toBe("999.5");
    expect(formatCompact(null)).toBe("—");
  });

  it("renders dollars with two decimals", () => {
    expect(formatUsdCompact(1.2345)).toBe("$1.23");
    expect(formatUsdCompact(null)).toBe("—");
  });

  it("uses one 2–6 decimal rule for every cost magnitude", () => {
    expect(formatUsdFine(0.03)).toBe("$0.03");
    expect(formatUsdFine(0.030042)).toBe("$0.030042");
    expect(formatUsdFine(0.008551)).toBe("$0.008551");
    expect(formatUsdFine(1.5)).toBe("$1.50");
    expect(formatUsdFine(0.000028)).toBe("$0.000028");
    expect(formatUsdFine(0.00057495)).toBe("$0.000575");
    expect(formatUsdFine(0.125)).toBe("$0.125");
    expect(formatUsdFine(1.2345)).toBe("$1.2345");
  });

  it("renders a dash for missing or invalid costs", () => {
    expect(formatUsdFine(null)).toBe("—");
    expect(formatUsdFine(undefined)).toBe("—");
    expect(formatUsdFine(Number.NaN)).toBe("—");
  });
});

describe("rates and spans", () => {
  it("merges the former metric-rate and rate formatters into one", () => {
    expect(formatRate(12.345)).toBe("12.35");
    expect(formatRate(1.5)).toBe("1.50");
    expect(formatRate(2500)).toBe("2.50K");
    expect(formatRate(0)).toBe("—");
    expect(formatRate(null)).toBe("—");
    expect(formatRate(Number.POSITIVE_INFINITY)).toBe("—");
  });

  it("formats throughput with one decimal and a t/s suffix", () => {
    expect(formatThroughput(42.42)).toBe("42.4 t/s");
    expect(formatThroughput(null)).toBe("—");
  });

  it("dashes missing spans while keeping a real zero distinguishable", () => {
    expect(formatSpanMinutes(12)).toBe("12 分钟");
    expect(formatSpanMinutes(90)).toBe("1.5 小时");
    expect(formatSpanMinutes(2880)).toBe("2.0 天");
    expect(formatSpanMinutes(0)).toBe("0 分钟");
    expect(formatSpanMinutes(null)).toBe("—");
  });
});
