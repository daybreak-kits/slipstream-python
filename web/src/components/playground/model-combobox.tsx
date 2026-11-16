import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { Spinner } from "@/components/ui/spinner";
import { filterCatalogEntries, type CatalogEntry } from "@/lib/playground";
import { cn } from "@/lib/utils";

export interface ModelComboboxProps {
  items: CatalogEntry[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  loading?: boolean;
  id?: string;
  className?: string;
}

function optionLabel(item: CatalogEntry): string {
  if (item.kind === "combo") {
    return item.name + " · " + (item.origin === "auto" ? "自动组合" : "手动组合");
  }
  return item.channel_name ? item.name + " · " + item.channel_name : item.name;
}

/**
 * 手写的 ARIA 1.2 combobox：输入框本身就是触发器（role=combobox），
 * 下方浮层是 role=listbox，选项是 role=option。
 * 键盘：ArrowUp/Down 移动、Enter 选中、Escape 关闭、Tab 关闭并离开。
 */
export function ModelCombobox({
  items,
  value,
  onChange,
  ariaLabel = "选择调用模型",
  placeholder = "选择调用模型",
  disabled = false,
  loading = false,
  id,
  className,
}: ModelComboboxProps) {
  const listboxId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  // 输入区贴在页面底部，浮层默认向下会被视口切掉；空间不够时向上弹。
  const [dropUp, setDropUp] = useState(false);

  const filtered = useMemo(() => filterCatalogEntries(items, query), [items, query]);
  const selected = items.find((item) => item.name === value) ?? null;
  const displayValue = open ? query : (selected?.name ?? "");

  // 打开或换关键词后，高亮回到第一条匹配项。
  useEffect(() => {
    if (!open) return;
    setActiveIndex(0);
  }, [open, query]);

  const optionId = (index: number) => listboxId + "-option-" + index;

  /** 打开前量一下上下空间：下方不够就往上弹，别被视口切掉。 */
  const syncPlacement = () => {
    const node = inputRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom;
    const above = rect.top;
    setDropUp(below < 200 && above > below);
  };

  const openList = () => {
    syncPlacement();
    setOpen(true);
  };

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  const choose = (item: CatalogEntry) => {
    onChange(item.name);
    close();
    inputRef.current?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      if (!filtered.length) return;
      setActiveIndex((index) =>
        event.key === "ArrowDown"
          ? Math.min(index + 1, filtered.length - 1)
          : Math.max(index - 1, 0),
      );
      return;
    }
    if (event.key === "Enter") {
      if (!open) return;
      event.preventDefault();
      const item = filtered[activeIndex];
      if (item) choose(item);
      return;
    }
    if (event.key === "Escape") {
      if (!open) return;
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "Tab") close();
  };

  return (
    <div
      className={cn("relative min-w-0", className)}
      onBlur={(event) => {
        const next = event.relatedTarget;
        if (!next || !event.currentTarget.contains(next as Node)) close();
      }}
    >
      <div className="flex h-9 items-center gap-2 rounded-control border border-border-strong bg-surface px-3 focus-within:border-primary focus-within:ring-2 focus-within:ring-focus-ring">
        <input
          ref={inputRef}
          id={id}
          type="text"
          role="combobox"
          aria-label={ariaLabel}
          aria-expanded={open}
          aria-controls={listboxId}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-activedescendant={
            open && filtered.length ? optionId(activeIndex) : undefined
          }
          autoComplete="off"
          disabled={disabled}
          className="min-w-0 flex-1 bg-transparent text-sm text-title outline-hidden placeholder:text-muted-foreground"
          placeholder={placeholder}
          value={displayValue}
          onFocus={openList}
          onClick={openList}
          onChange={(event) => {
            setQuery(event.target.value);
            openList();
          }}
          onKeyDown={handleKeyDown}
        />
        {loading ? (
          <Spinner size="sm" className="shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : null}
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
          strokeWidth={1.75}
          aria-hidden="true"
        />
      </div>

      {open ? (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="模型列表"
          className={cn(
            "absolute left-0 right-0 z-40 max-h-72 overflow-auto rounded-control border border-border bg-popover p-1 shadow-pop",
            dropUp ? "bottom-full mb-1" : "top-full mt-1",
          )}
        >
          {filtered.length === 0 ? (
            <li role="presentation" className="px-2.5 py-2 text-xs text-muted-foreground">
              没有匹配的模型
            </li>
          ) : null}
          {filtered.map((item, index) => {
            const isSelected = item.name === value;
            return (
              <li
                key={item.kind + "-" + item.id}
                id={optionId(index)}
                role="option"
                aria-selected={isSelected}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-control px-2.5 py-1.5 text-sm text-title",
                  index === activeIndex && "bg-surface-2",
                )}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(item)}
              >
                <span className="min-w-0 flex-1 truncate">{optionLabel(item)}</span>
                {isSelected ? (
                  <Check
                    className="h-3.5 w-3.5 shrink-0 text-primary"
                    strokeWidth={2}
                    aria-hidden="true"
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
