import { Check, ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SELECT_ALL_VALUE } from "@/lib/select-sentinels";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useOverflow } from "@/components/ui/use-overflow";
import { cn } from "@/lib/utils";

export interface ComboboxOption {
  value: string;
  label: string;
  description?: string;
}

interface ComboboxProps {
  options: ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  searchPlaceholder?: string;
  emptyText?: string;
  loading?: boolean;
  /** 服务端只回了前 N 项：底部提示继续输入缩小范围。 */
  truncated?: boolean;
  onSearch?: (query: string) => void;
  className?: string;
  ariaLabel?: string;
  /** 草稿与已生效条件不同：给控件描边，提示还有未提交的修改。 */
  highlighted?: boolean;
}

/**
 * 本地组合件（不是 vendored 件）：Popover 负责浮层与焦点，cmdk 的 Command 负责
 * 搜索、上下键与 Enter 选中，并保留旧 props 的形状。
 */
export function Combobox({
  options,
  value,
  onChange,
  placeholder,
  searchPlaceholder = "输入关键词搜索",
  emptyText = "没有匹配项",
  loading = false,
  truncated = false,
  onSearch,
  className,
  ariaLabel,
  highlighted = false,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selected = options.find((option) => option.value === value);
  const selectedLabel = selected?.label ?? placeholder;
  const { ref: labelRef, overflowing } = useOverflow<HTMLSpanElement>(selectedLabel);
  const [tooltipOpen, setTooltipOpen] = useState(false);

  // 远端搜索：输入停下 250ms 再问服务端要选项，避免每敲一个字打一次接口。
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => onSearch?.(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query, open, onSearch]);

  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className={cn("min-w-0", className)}>
      <TooltipProvider delayDuration={150}>
        <Tooltip
          open={tooltipOpen}
          onOpenChange={(next) => setTooltipOpen(next && overflowing)}
        >
          <Popover
            open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setQuery("");
        }}
      >
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  role="combobox"
            aria-expanded={open}
            aria-label={ariaLabel ?? placeholder}
            className={cn(
              "flex h-9 w-full items-center gap-2 rounded-control border border-border-strong bg-surface px-3 text-left text-sm text-title transition-colors hover:bg-surface-2 focus-visible:border-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
              highlighted && "filter-dirty border-primary/70 bg-primary-soft/40",
            )}
          >
                <span
                  ref={labelRef}
                  className={cn(
                    "min-w-0 flex-1 truncate",
                    selected ? "text-title" : "text-muted-foreground",
                  )}
                >
                  {selectedLabel}
                </span>
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
                </button>
              </PopoverTrigger>
            </TooltipTrigger>

          <PopoverContent
          align="start"
          sideOffset={6}
          className="w-72 p-0 motion-reduce:animate-none"
        >
          <Command>
            <CommandInput
              value={query}
              onValueChange={setQuery}
              placeholder={searchPlaceholder}
              className="h-9 text-sm"
            />
            <CommandList>
              <CommandEmpty>{loading ? "正在搜索…" : emptyText}</CommandEmpty>
              <CommandGroup>
                <CommandItem
                  value={SELECT_ALL_VALUE}
                  forceMount
                  onSelect={() => choose("")}
                  className="text-muted-foreground"
                >
                  <span className="min-w-0 flex-1 truncate">{placeholder}</span>
                  {value ? null : (
                    <Check className="h-3.5 w-3.5 shrink-0 text-primary" strokeWidth={2} aria-hidden="true" />
                  )}
                </CommandItem>
                {options.map((option) => {
                  const checked = option.value === value;
                  return (
                    <CommandItem
                      key={option.value}
                      value={`${option.label} ${option.description ?? ""} ${option.value}`}
                      onSelect={() => choose(option.value)}
                      className="items-start"
                    >
                      <span className="min-w-0 flex-1">
                        <span
                          className={cn(
                            "block truncate",
                            checked ? "text-primary" : "text-title",
                          )}
                        >
                          {option.label}
                        </span>
                        {option.description ? (
                          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                            {option.description}
                          </span>
                        ) : null}
                      </span>
                      {checked ? (
                        <Check
                          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary"
                          strokeWidth={2}
                          aria-hidden="true"
                        />
                      ) : null}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
            {truncated ? (
              <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
                仅显示前 50 项，继续输入缩小范围
              </p>
            ) : null}
          </Command>
          </PopoverContent>
          </Popover>
          <TooltipContent>{selectedLabel}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
