"use client"

import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"

import { cn } from "@/lib/utils"

const TooltipProvider = TooltipPrimitive.Provider

const Tooltip = TooltipPrimitive.Root

const TooltipTrigger = TooltipPrimitive.Trigger

/**
 * Tailwind 的 line-clamp 是静态类名，动态拼出来的名字不会被扫描器生成，
 * 所以行数上限逐档写死。
 */
const LINE_CLAMP: Record<number, string> = {
  1: "line-clamp-1",
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
  5: "line-clamp-5",
  6: "line-clamp-6",
}

/** 正文是不是纯文本（纯文本才套用行数与宽度上限）。 */
function isPlainText(children: React.ReactNode): boolean {
  const nodes = React.Children.toArray(children)
  return nodes.length > 0 && nodes.every((node) => typeof node === "string" || typeof node === "number")
}

export interface TooltipContentProps
  extends React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content> {
  /** 标题行：加粗显示在正文上方，标题行不计入正文行数。 */
  title?: string
  /**
   * 正文最多行数。纯文本默认 3 行（标题行 + 正文合计不超过 3 行）；
   * 显式传数字则强制生效，传 0 关闭行数上限（富文本面板用）。
   */
  maxLines?: number
}

/**
 * 统一 tooltip：12px 正文（继承 body 字体）、深色浮层（--tooltip-* token）、
 * 最大 280px、默认出现在上方，空间不足时由 Radix 自动翻到下方。
 * 纯文本正文最多 3 行；富文本（面板型）保持调用方自己的排版。
 */
const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  TooltipContentProps
>(({ className, sideOffset = 4, title, maxLines, children, ...props }, ref) => {
  const plain = isPlainText(children)
  const requested = maxLines ?? (plain ? 3 : 0)
  const bodyLines = title ? Math.max(requested - 1, 0) : requested
  const clampClass = bodyLines > 0 ? LINE_CLAMP[bodyLines] : undefined
  const wrap = Boolean(title) || Boolean(clampClass)

  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        className={cn(
          "z-50 rounded-md border border-[var(--tooltip-border)] bg-[var(--tooltip-bg)] px-3 py-1.5 text-xs leading-snug text-[var(--tooltip-fg)] shadow-pop animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 origin-[var(--radix-tooltip-content-transform-origin)]",
          clampClass ? "max-w-70" : undefined,
          className
        )}
        {...props}
      >
        {title ? (
          <span className="mb-0.5 block font-semibold text-[var(--tooltip-fg)]">{title}</span>
        ) : null}
        {wrap ? <div className={clampClass}>{children}</div> : children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  )
})
TooltipContent.displayName = TooltipPrimitive.Content.displayName

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
