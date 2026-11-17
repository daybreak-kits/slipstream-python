export const CATEGORY_COLORS = Array.from(
  { length: 16 },
  (_, index) => `var(--chart-${index + 1})`,
) as readonly string[];

export interface ColorAssignment {
  name: string;
  color: string;
  requests: number;
}

/**
 * 按名字全局分配色板（ADR-040）：把多个视角的成员合并后按总请求数降序统一取色，
 * 同名跨视角同色；成员数超过 16 时色板循环，靠图例区分——绝不合并成「其他」。
 */
export function assignCategoryColorsByName(
  groups: ReadonlyArray<ReadonlyArray<{ name: string; requests: number }>>,
  palette: readonly string[] = CATEGORY_COLORS,
): Map<string, string> {
  const totals = new Map<string, number>();
  for (const group of groups) {
    for (const item of group) {
      const requests = Number.isFinite(item.requests) ? Math.max(0, item.requests) : 0;
      totals.set(item.name, (totals.get(item.name) ?? 0) + requests);
    }
  }
  const ordered = [...totals.entries()].sort(
    ([leftName, leftRequests], [rightName, rightRequests]) =>
      rightRequests - leftRequests || leftName.localeCompare(rightName),
  );
  const colors = new Map<string, string>();
  if (!palette.length) return colors;
  ordered.forEach(([name], index) => colors.set(name, palette[index % palette.length]));
  return colors;
}

/**
 * Assigns the fixed 16-slot categorical palette in descending request order.
 * Models with zero calls consume no slot; once the list outgrows the palette
 * the colours cycle, so every model keeps its own legend entry (no folding).
 */
export function assignCategoryColors(
  items: Array<{ name: string; requests: number }>,
  palette: readonly string[] = CATEGORY_COLORS,
): ColorAssignment[] {
  if (!palette.length) return [];
  return items
    .filter((item) => item.requests > 0)
    .map((item, index) => ({
      name: item.name,
      requests: item.requests,
      color: palette[index % palette.length],
    }));
}
