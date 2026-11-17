import { HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  QUERY_EXAMPLES,
  QUERY_FIELDS,
  QUERY_FIELD_GROUPS,
  QUERY_KEYWORD_HINT,
  QUERY_OPERATORS,
  QUERY_QUOTE_HINT,
  QUERY_STRICT_HINT,
} from "@/lib/query-syntax";
import { cn } from "@/lib/utils";

export {
  QUERY_EXAMPLES,
  QUERY_FIELDS,
  QUERY_FIELD_GROUPS,
  QUERY_KEYWORD_HINT,
  QUERY_OPERATORS,
  QUERY_QUOTE_HINT,
  QUERY_STRICT_HINT,
};
export type {
  QueryExampleSpec,
  QueryFieldGroupSpec,
  QueryFieldKind,
  QueryFieldSpec,
  QueryOperatorSpec,
} from "@/lib/query-syntax";

/** 行内代码：--text-2xs（11/16）是项目的 meta 字号层级，别写任意值。 */
const CODE_CLASS = "rounded-sm bg-surface-2 px-1 py-0.5 font-mono text-2xs text-title";
const SECTION_TITLE_CLASS =
  "mb-2 text-2xs font-semibold uppercase tracking-wide text-faint";

/** 弹窗与内联面板共用的正文：操作符表 + 分组字段表 + 示例 + 口径说明。 */
function SyntaxReference() {
  return (
    <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
      <section aria-labelledby="query-syntax-operators">
        <h3 id="query-syntax-operators" className={SECTION_TITLE_CLASS}>
          操作符
        </h3>
        <ul className="divide-y divide-border rounded-control border border-border bg-surface">
          {QUERY_OPERATORS.map((item) => (
            <li key={item.op} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-1.5">
              <code className={cn(CODE_CLASS, "min-w-8 text-center")}>{item.op}</code>
              <span className="text-2sm text-body">{item.label}</span>
              <code className="ml-auto font-mono text-2xs text-faint">{item.example}</code>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="query-syntax-fields">
        <h3 id="query-syntax-fields" className={SECTION_TITLE_CLASS}>
          字段
        </h3>
        <div className="space-y-3">
          {QUERY_FIELD_GROUPS.map((group) => (
            <div key={group.id}>
              <div className="mb-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="text-2sm font-medium text-title">{group.title}</span>
                <span className="font-mono text-2xs text-faint">{group.operators.join(" ")}</span>
              </div>
              <p className="mb-1.5 text-2xs text-faint">{group.hint}</p>
              <ul className="space-y-1">
                {group.fields.map((field) => (
                  <li key={field.name} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <code className={CODE_CLASS}>{field.name}</code>
                    <span className="text-2xs text-body">{field.description}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="query-syntax-examples">
        <h3 id="query-syntax-examples" className={SECTION_TITLE_CLASS}>
          示例
        </h3>
        <ul className="space-y-1.5">
          {QUERY_EXAMPLES.map((example) => (
            <li key={example.query} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <code className={CODE_CLASS}>{example.query}</code>
              <span className="text-2xs text-faint">{example.note}</span>
            </li>
          ))}
        </ul>
      </section>

      <div className="space-y-1 border-t border-border pt-3">
        <p className="text-2xs text-body">{QUERY_KEYWORD_HINT}</p>
        <p className="text-2xs text-faint">{QUERY_STRICT_HINT}</p>
        <p className="text-2xs text-faint">{QUERY_QUOTE_HINT}</p>
        <p className="text-2xs text-faint">
          共 {QUERY_FIELDS.length} 个字段；字段名与操作符大小写不敏感。
        </p>
      </div>
    </div>
  );
}

export interface QuerySyntaxHelpProps {
  /** 触发按钮文案。 */
  label?: string;
  iconOnly?: boolean;
  className?: string;
  /** true 时原地展开、不弹窗（给将来的内嵌面板与测试用，避免依赖 portal）。 */
  inline?: boolean;
}

/**
 * 「语法参考」入口：轻量按钮 + 弹窗。
 *
 * 独立成文件是为了不和正在并行改动的日志页（t2）抢同一个文件；接线时一行
 * `<QuerySyntaxHelp />` 即可，它只依赖 ui/dialog 与 ui/tooltip。
 */
export function QuerySyntaxHelp({
  label = "语法参考",
  iconOnly = false,
  className,
  inline = false,
}: QuerySyntaxHelpProps) {
  const trigger = (
    <Button
      type="button"
      variant="ghost"
      size={iconOnly ? "icon" : "sm"}
      aria-label={iconOnly ? label : undefined}
      className={cn("gap-1.5 text-faint hover:text-title", className)}
    >
      <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
      {iconOnly ? <span className="sr-only">{label}</span> : label}
    </Button>
  );

  if (inline) {
    return (
      <div className="space-y-3">
        {trigger}
        <SyntaxReference />
      </div>
    );
  }

  return (
    <Dialog>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DialogTrigger asChild>{trigger}</DialogTrigger>
          </TooltipTrigger>
          <TooltipContent title="查看查询语法与可用字段" />
        </Tooltip>
      </TooltipProvider>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>查询语法</DialogTitle>
          <DialogDescription>
            用「字段 + 操作符 + 值」组合出精确条件，再用 &&、||、! 与括号连接。
          </DialogDescription>
        </DialogHeader>
        <SyntaxReference />
      </DialogContent>
    </Dialog>
  );
}
