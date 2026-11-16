import { describe, expect, it } from "vitest";

import { paginationRange } from "@/components/ui/pagination";

describe("paginationRange", () => {
  it("页数不多时全部展示", () => {
    expect(paginationRange(1, 1)).toEqual([1]);
    expect(paginationRange(3, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("首页：贴住左侧，只在右侧省略", () => {
    expect(paginationRange(1, 737)).toEqual([1, 2, 3, 4, 5, "ellipsis", 737]);
    expect(paginationRange(2, 100)).toEqual([1, 2, 3, 4, 5, "ellipsis", 100]);
  });

  it("中间页：首末页都在，两侧都有省略号", () => {
    expect(paginationRange(368, 737)).toEqual([
      1,
      "ellipsis",
      366,
      367,
      368,
      369,
      370,
      "ellipsis",
      737,
    ]);
  });

  it("末页：贴住右侧，只在左侧省略", () => {
    expect(paginationRange(737, 737)).toEqual([1, "ellipsis", 733, 734, 735, 736, 737]);
  });

  it("省略号不与相邻页码重复", () => {
    // totalPages=9、current=2 时窗口是 1..5，紧邻首页，不应该出现 1 … 2。
    expect(paginationRange(2, 9)).toEqual([1, 2, 3, 4, 5, "ellipsis", 9]);
    expect(paginationRange(8, 9)).toEqual([1, "ellipsis", 5, 6, 7, 8, 9]);
  });

  it("越界入参被夹取", () => {
    expect(paginationRange(0, 3)).toEqual([1, 2, 3]);
    expect(paginationRange(99, 2)).toEqual([1, 2]);
    expect(paginationRange(1, 0)).toEqual([1]);
  });

  it("任意页码下都包含首页、末页且页码不重复", () => {
    for (let current = 1; current <= 120; current += 1) {
      const items = paginationRange(current, 120);
      const pages = items.filter((item): item is number => typeof item === "number");
      expect(pages[0]).toBe(1);
      expect(pages[pages.length - 1]).toBe(120);
      expect(new Set(pages).size).toBe(pages.length);
      expect(items.filter((item) => item === "ellipsis").length).toBeLessThanOrEqual(2);
    }
  });
});
