import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

/** 每页行数可选项；与后端 page_size 上限 200 对齐。 */
export const PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;

export type PaginationRangeItem = number | "ellipsis";

/**
 * 计算分页条上要显示的页码序列（纯函数，便于单测）。
 *
 * 规则：
 * 1. totalPages <= 7：全部页码直出，不做省略；
 * 2. 否则显示「首页 + 当前页附近 5 个连续页码 + 末页」，窗口尽量以当前页为中心：
 *    当前页靠左时窗口贴住左侧（1..5），靠右时贴住右侧，其余情况当前页居中（current-2 .. current+2）；
 * 3. 只有窗口与首页 / 末页之间「还隔着至少一页」时才插入 "ellipsis"，
 *    所以省略号永远不会和相邻页码重复（例如 totalPages=9、current=2 时不会出现 1 … 2）；
 * 4. 入参越界会被夹取：current 夹到 [1, totalPages]，totalPages < 1 视作 1。
 *
 * 例：totalPages=737，current=1   -> [1, 2, 3, 4, 5, "ellipsis", 737]
 *     totalPages=737，current=368 -> [1, "ellipsis", 366, 367, 368, 369, 370, "ellipsis", 737]
 *     totalPages=737，current=737 -> [1, "ellipsis", 733, 734, 735, 736, 737]
 */
export function paginationRange(current: number, totalPages: number): PaginationRangeItem[] {
  const lastPage = Math.max(1, Math.floor(totalPages) || 1);
  const currentPage = Math.min(Math.max(1, Math.floor(current) || 1), lastPage);

  if (lastPage <= 7) {
    return Array.from({ length: lastPage }, (_, index) => index + 1);
  }

  const start = Math.max(1, Math.min(currentPage - 2, lastPage - 4));
  const end = start + 4;
  const items: PaginationRangeItem[] = [];

  if (start > 1) {
    items.push(1);
    if (start > 2) items.push("ellipsis");
  }

  for (let item = start; item <= end; item += 1) items.push(item);

  if (end < lastPage) {
    if (end < lastPage - 1) items.push("ellipsis");
    items.push(lastPage);
  }

  return items;
}

export interface PaginationProps {
  /** 当前页，从 1 开始。 */
  page: number;
  /** 每页行数。 */
  pageSize: number;
  /** 总条数（后端返回的 total）。 */
  total: number;
  /** 页码变化回调；组件本身不托管状态。 */
  onPageChange: (page: number) => void;
  /** 每页行数变化回调；不传则下拉框禁用。 */
  onPageSizeChange?: (pageSize: number) => void;
  /** 每页行数可选项，默认 PAGE_SIZE_OPTIONS。 */
  pageSizeOptions?: readonly number[];
  /** 追加到最外层容器的类名。 */
  className?: string;
}

/**
 * 受控分页条：左侧「总计 N 条 + 每页行数」，右侧首页 / 上一页 / 页码（含省略号）/ 下一页 / 末页。
 * 单页时四个翻页按钮全部禁用；页码变更与每页行数变更都通过回调交给调用方处理。
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const currentPage = Math.min(Math.max(1, Math.floor(page) || 1), totalPages);
  const items = paginationRange(currentPage, totalPages);
  const atFirst = currentPage <= 1;
  const atLast = currentPage >= totalPages;
  /** 输入框只做本地草稿；成败都以受控的 page 为准，组件不托管页码。 */
  const [jumpPage, setJumpPage] = useState(String(currentPage));

  useEffect(() => {
    setJumpPage(String(currentPage));
  }, [currentPage]);

  // 统一夹取目标页；目标与当前页相同就不回调，避免无意义的重复请求。
  const go = (next: number) => {
    const target = Math.min(Math.max(1, next), totalPages);
    if (target !== currentPage) onPageChange(target);
  };

  /** 回车/失焦提交：非正整数回退当前页，越界钳到首/末页。 */
  const applyJump = () => {
    const raw = jumpPage.trim();
    if (!/^\d+$/.test(raw)) {
      setJumpPage(String(currentPage));
      return;
    }
    const target = Math.min(Math.max(1, Number(raw)), totalPages);
    setJumpPage(String(target));
    go(target);
  };

  return (
    <nav
      role="navigation"
      aria-label="分页"
      data-slot="pagination"
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border px-4 py-3",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="text-xs text-muted-foreground">总计 {formatCount(total)} 条</p>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>每页行数</span>
          <Select
            value={String(pageSize)}
            onValueChange={(value) => onPageSizeChange?.(Number(value))}
            disabled={!onPageSizeChange}
          >
            <SelectTrigger
              aria-label="每页行数"
              className="h-8 w-auto min-w-14 gap-1 rounded-badge border-border-strong bg-surface px-2 py-0 text-xs text-title"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {pageSizeOptions.map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>跳到</span>
          <Input
            aria-label="跳到页码"
            inputMode="numeric"
            value={jumpPage}
            onChange={(event) => setJumpPage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                applyJump();
              }
            }}
            onBlur={applyJump}
            className="h-8 w-14 rounded-badge px-2 py-0 text-center text-xs tabular-nums"
          />
          <span>页</span>
        </label>
        {/* 小屏放不下时这段只在读屏软件里播报，避免和左侧「总计」重复展示。 */}
        <p className="sr-only" role="status" aria-live="polite">
          第 {currentPage} / {totalPages} 页 · 共 {total} 条
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          className="w-8 px-0"
          aria-label="首页"
          disabled={atFirst}
          onClick={() => go(1)}
        >
          <ChevronsLeft aria-hidden="true" className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-8 px-0"
          aria-label="上一页"
          disabled={atFirst}
          onClick={() => go(currentPage - 1)}
        >
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
        </Button>

        {items.map((item, index) =>
          item === "ellipsis" ? (
            <span
              key={"ellipsis-" + index}
              aria-hidden="true"
              className="px-1 text-xs text-faint"
            >
              …
            </span>
          ) : (
            <Button
              key={item}
              variant={item === currentPage ? "default" : "outline"}
              size="sm"
              className="min-w-[2rem] px-2 tabular-nums"
              aria-label={"第 " + item + " 页"}
              aria-current={item === currentPage ? "page" : undefined}
              onClick={() => go(item)}
            >
              {item}
            </Button>
          ),
        )}

        <Button
          variant="outline"
          size="sm"
          className="w-8 px-0"
          aria-label="下一页"
          disabled={atLast}
          onClick={() => go(currentPage + 1)}
        >
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-8 px-0"
          aria-label="末页"
          disabled={atLast}
          onClick={() => go(totalPages)}
        >
          <ChevronsRight aria-hidden="true" className="h-4 w-4" />
        </Button>
      </div>
    </nav>
  );
}
