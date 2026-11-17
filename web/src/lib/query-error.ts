/**
 * 查询语法错误的定位辅助（纯函数，无 React / 无别名 import）。
 *
 * 后端 QueryError 的 details 形状（ADR-071 / l03_domain/query_lang/errors.py）：
 * column 是 1-based 的出错列号，0 表示知道出错但定位不到列；suggestion 是下一步建议。
 * 这里只做展示换算，不重复校验——语法是否合法由后端说了算。
 */

export interface QueryParseError {
  message: string;
  column?: number;
  suggestion?: string;
}

const TAB_WIDTH = 4;

/** 把前缀里的制表符按 TAB_WIDTH 展开，否则等宽字体下 ^ 会对不上。 */
function expandTabs(prefix: string): string {
  return prefix.replace(/\t/g, " ".repeat(TAB_WIDTH));
}

/**
 * 1-based 列号 → 0-based 索引；列号缺失、为 0 或越过文本末尾时返回 null。
 * 后端按字符计列，中文与 ASCII 都算一个字符，所以可以直接按索引对齐。
 */
export function caretIndex(column: number | undefined, query: string): number | null {
  if (!column || column < 1) return null;
  const index = column - 1;
  if (index >= query.length) return null;
  return index;
}

/** 生成与 query 对齐的 ^ 指示行；定位不到时返回空串（调用方据此不渲染）。 */
export function caretLine(query: string, column?: number): string {
  const index = caretIndex(column, query);
  if (index === null) return "";
  return " ".repeat(expandTabs(query.slice(0, index)).length) + "^";
}

/** 错误标题：能定位时补上「第 N 列」，方便用户在当前输入框里数位置。 */
export function queryErrorTitle(error: QueryParseError): string {
  if (!error.column || error.column < 1) return error.message;
  return `第 ${error.column} 列：${error.message}`;
}
