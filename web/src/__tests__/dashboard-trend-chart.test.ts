import { describe, expect, it } from "vitest";

import {
  monotoneCubicPath,
  TREND_DEFAULT_DIMENSION,
  TREND_DEFAULT_METRIC,
  trendAreaPaths,
  trendAxisLabelIndexes,
  trendChartGeometry,
  trendLinePaths,
  trendYAxisTickCount,
} from "@/lib/dashboard";

const xOf = (bucket: number) => 46 + (bucket + 0.5) * 60;
const yOf = (value: number) => 400 - value * 2;

describe("trendChartGeometry", () => {
  it("derives the plot box from the measured pixel size instead of a fixed viewBox", () => {
    const geometry = trendChartGeometry(1440, 420, 24);
    expect(geometry.width).toBe(1440);
    expect(geometry.height).toBe(420);
    expect(geometry.plotLeft).toBe(46);
    expect(geometry.plotBottom).toBe(394);
    expect(geometry.plotHeight).toBe(372);
    expect(geometry.slot).toBeCloseTo((1440 - 46) / 24, 5);
    expect(geometry.axisLabelY).toBe(412);
  });

  it("narrows the axis gutter on small screens and never falls below the minimum box", () => {
    expect(trendChartGeometry(390, 320, 24).plotLeft).toBe(40);
    const tiny = trendChartGeometry(120, 80, 0);
    expect(tiny.width).toBeGreaterThanOrEqual(320);
    expect(tiny.height).toBeGreaterThanOrEqual(220);
    expect(tiny.slot).toBeGreaterThan(0);
  });
});

describe("trendYAxisTickCount", () => {
  it("grows with the plot height but stays between four and eight ticks", () => {
    expect(trendYAxisTickCount(120)).toBe(4);
    expect(trendYAxisTickCount(252)).toBe(6);
    expect(trendYAxisTickCount(400)).toBe(8);
    expect(trendYAxisTickCount(Number.NaN)).toBe(4);
  });
});

describe("trendAxisLabelIndexes", () => {
  it("thins time labels as the slot shrinks and keeps every bucket when there is room", () => {
    expect(trendAxisLabelIndexes(24, 58)).toEqual([...Array(24).keys()]);
    expect(trendAxisLabelIndexes(24, 24)).toEqual([0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]);
    expect(trendAxisLabelIndexes(24, 12)).toEqual([0, 4, 8, 12, 16, 20]);
  });
});

describe("monotoneCubicPath", () => {
  it("passes through every point with cubic segments", () => {
    const path = monotoneCubicPath([
      { x: 0, y: 100 },
      { x: 10, y: 60 },
      { x: 20, y: 70 },
    ]);
    expect(path.startsWith("M 0 100")).toBe(true);
    expect(path).toContain(" C ");
    expect(path.endsWith("20 70")).toBe(true);
  });

  it("does not overshoot: control points stay inside the local value range", () => {
    const path = monotoneCubicPath([
      { x: 0, y: 0 },
      { x: 10, y: 100 },
      { x: 20, y: 100 },
      { x: 30, y: 40 },
    ]);
    const numbers = (path.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
    // 路径里只有坐标：x, y 交替出现，所有 y 都必须落在数据的值域内。
    const ys = numbers.filter((_, index) => index % 2 === 1);
    expect(ys.length).toBeGreaterThan(0);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...ys)).toBeLessThanOrEqual(100);
  });

  it("degrades to a move for a single point and to nothing for no points", () => {
    expect(monotoneCubicPath([{ x: 3, y: 4 }])).toBe("M 3 4");
    expect(monotoneCubicPath([])).toBe("");
  });
});

describe("trendLinePaths", () => {
  it("bridges a missing bucket with a dashed connector instead of dropping to zero", () => {
    const paths = trendLinePaths([10, null, 30, 40], xOf, yOf);
    expect(paths.runs).toEqual([
      { start: 0, end: 0 },
      { start: 2, end: 3 },
    ]);
    // 两段实线各自从自己的 M 起笔，缺样本桶处没有数据点。
    expect(paths.solid.match(/M /g)?.length).toBe(2);
    // 缺口两端由一条直线虚线连起来，而不是把曲线掉到 0。
    expect(paths.dashed).toBe("M " + xOf(0) + " " + yOf(10) + " L " + xOf(2) + " " + yOf(30));
  });

  it("keeps trailing gaps unbridged and returns empty paths for an all-null series", () => {
    const trailing = trendLinePaths([null, null, 5], xOf, yOf);
    expect(trailing.runs).toEqual([{ start: 2, end: 2 }]);
    expect(trailing.solid.startsWith("M ")).toBe(true);
    expect(trailing.dashed).toBe("");
    expect(trendLinePaths([null, null], xOf, yOf)).toEqual({ solid: "", dashed: "", runs: [] });
  });
});

describe("trendAreaPaths", () => {
  it("fills only contiguous runs so a missing bucket leaves a real hole in the area", () => {
    const paths = trendAreaPaths([10, 20, null, 30, 40], xOf, yOf, 400);
    expect(paths).toHaveLength(2);
    expect(paths.every((path) => path.startsWith("M "))).toBe(true);
    expect(paths.every((path) => path.endsWith(" Z"))).toBe(true);
    // 填充落到基线上，而不是把两段连成一片
    expect(paths[0]).toContain("400");
  });

  it("skips runs that cannot enclose an area", () => {
    expect(trendAreaPaths([null, null], xOf, yOf, 400)).toEqual([]);
    expect(trendAreaPaths([7, null], xOf, yOf, 400)).toEqual([]);
  });
});

describe("trend defaults", () => {
  it("defaults to combo + tokens so the first paint matches the backend dimension", () => {
    expect(TREND_DEFAULT_DIMENSION).toBe("combo");
    expect(TREND_DEFAULT_METRIC).toBe("tokens");
  });
});
