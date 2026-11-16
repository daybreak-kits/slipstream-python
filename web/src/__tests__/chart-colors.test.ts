import { describe, expect, it } from "vitest";

import { assignCategoryColors, CATEGORY_COLORS } from "@/lib/chart-colors";

describe("category colors", () => {
  it("keeps only positive series and assigns in order", () => {
    const result = assignCategoryColors([
      { name: "a", requests: 3 },
      { name: "zero", requests: 0 },
      { name: "b", requests: 2 },
    ]);
    expect(result.map((r) => r.name)).toEqual(["a", "b"]);
    expect(result[0].color).toBe(CATEGORY_COLORS[0]);
  });

  it("cycles the palette once the series outgrow it", () => {
    const items = Array.from({ length: 18 }, (_, i) => ({ name: `m${i}`, requests: 18 - i }));
    const result = assignCategoryColors(items);
    expect(result).toHaveLength(18);
    expect(result.some((item) => item.name === "其他")).toBe(false);
    expect(result[15].color).toBe(CATEGORY_COLORS[15]);
    expect(result[16].color).toBe(CATEGORY_COLORS[0]);
    expect(result[17].color).toBe(CATEGORY_COLORS[1]);
  });
});
