import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PageHeaderProps {
  /** 页面主标题，渲染为唯一的 h1（text-h1 字阶全站仅此处使用）。 */
  title: string;
  /** 标题下方的说明文字；缺省时不渲染说明段落。 */
  description?: ReactNode;
  /** 右侧动作区（按钮、筛选控件等），窄屏随标题换行。 */
  actions?: ReactNode;
  /** 标题旁徽标插槽（状态徽章、「有未保存修改」等）。 */
  badge?: ReactNode;
  /** 追加到 header 根元素的布局类名（如 items-end、mt-2）；组件不自带 margin。 */
  className?: string;
}

/**
 * 页头基元（B 类并入）：统一各页手抄的 h1 + 说明 + 右侧动作区布局。
 * 字号字距口径：`text-h1` + `-0.01em` 只在此出现（docs/ui.md §2.2）。
 */
export function PageHeader({
  title,
  description,
  actions,
  badge,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn("flex flex-wrap items-start justify-between gap-3", className)}
    >
      <div className="min-w-0">
        {badge ? (
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-h1 font-semibold tracking-[-0.01em] text-title">
              {title}
            </h1>
            {badge}
          </div>
        ) : (
          <h1 className="text-h1 font-semibold tracking-[-0.01em] text-title">{title}</h1>
        )}
        {description ? (
          <p className="mt-1.5 text-2sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
