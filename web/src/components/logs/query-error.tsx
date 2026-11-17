import { AlertTriangle } from "lucide-react";

import { caretLine, queryErrorTitle, type QueryParseError } from "@/lib/query-error";
import { cn } from "@/lib/utils";

export { caretIndex, caretLine, queryErrorTitle } from "@/lib/query-error";
export type { QueryParseError } from "@/lib/query-error";

export interface QueryErrorProps {
  /** null / undefined 时不渲染，调用方可以直接按「有无错误」条件渲染。 */
  error: QueryParseError | null | undefined;
  /** 用户输入的原文，用于画出 ^ 指示行；不传则只显示错误文字。 */
  query?: string;
  className?: string;
}

/**
 * 查询语法错误提示。
 *
 * role="alert" 让读屏软件立刻读出错误；^ 指示行只是视觉对齐辅助、标了 aria-hidden：
 * 列号已经写在标题里，再朗读一串空格没有价值。
 */
export function QueryError({ error, query = "", className }: QueryErrorProps) {
  if (!error) return null;
  const caret = caretLine(query, error.column);
  return (
    <div
      role="alert"
      className={cn(
        "flex gap-2 rounded-control border border-danger/40 bg-danger-soft px-3 py-2 text-2sm text-danger-text",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        <p className="font-medium">{queryErrorTitle(error)}</p>
        {caret ? (
          <pre
            aria-hidden="true"
            className="overflow-x-auto whitespace-pre font-mono text-2xs leading-4"
          >
            {query + "\n" + caret}
          </pre>
        ) : null}
        {error.suggestion ? <p className="text-2xs">建议：{error.suggestion}</p> : null}
      </div>
    </div>
  );
}
