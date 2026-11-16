import { Check, ChevronDown, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  WEEKDAY_LABELS,
  emptyRule,
  type ScheduleRule,
  type ScheduleRuleKind,
} from "@/lib/channel-models";
import { matchTimezones, timezoneOptions } from "@/lib/timezone";
import { cn } from "@/lib/utils";

// 这些纯函数放在 lib 里，组件层只做再导出，页面/对话框从哪一层 import 都行。
export {
  describeSchedule,
  describeScheduleRule,
  emptyRule,
  validateScheduleRule,
  validateScheduleRules,
} from "@/lib/channel-models";

const KIND_OPTIONS: Array<{ value: ScheduleRuleKind; label: string }> = [
  { value: "daily", label: "每天" },
  { value: "weekly", label: "每周" },
  { value: "monthly", label: "每月" },
];

const DAY_CELLS = Array.from({ length: 31 }, (_, index) => index + 1);

function toggleValue(values: readonly number[], value: number): number[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value].sort((left, right) => left - right);
}

export interface ScheduleRuleRowProps {
  index: number;
  rule: ScheduleRule;
  onChange: (patch: Partial<ScheduleRule>) => void;
  onRemove: () => void;
  /** false 时删除按钮置灰（至少保留一条的场合）。 */
  removable: boolean;
  /** 仅用于生成稳定的 DOM id 前缀，避免同页多份编辑器撞 id。 */
  idPrefix?: string;
}

/**
 * 一条排期规则：类型（每天/每周/每月）+ 起止墙钟时刻 + 周几或日号。
 * 渠道表单与批量「排期」弹窗共用这一份，别做两套。
 */
export function ScheduleRuleRow({
  index,
  rule,
  onChange,
  onRemove,
  removable,
  idPrefix = "schedule-rule",
}: ScheduleRuleRowProps) {
  const changeKind = (kind: ScheduleRuleKind) => {
    if (kind === "daily") onChange({ kind, days: undefined, days_of_month: undefined });
    else if (kind === "weekly") {
      onChange({ kind, days: rule.days ?? [1, 2, 3, 4, 5], days_of_month: undefined });
    } else {
      onChange({ kind, days: undefined, days_of_month: rule.days_of_month ?? [1] });
    }
  };

  return (
    <div className="rounded-control border border-border bg-surface-2/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={rule.kind} onValueChange={(value) => changeKind(value as ScheduleRuleKind)}>
          <SelectTrigger
            aria-label={"第 " + (index + 1) + " 条规则的类型"}
            className="h-9 w-24 border-border-strong bg-surface"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KIND_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="h-9 w-28"
          type="time"
          aria-label={"第 " + (index + 1) + " 条规则的开始时刻"}
          value={rule.start}
          onChange={(event) => onChange({ start: event.target.value })}
        />
        <span className="text-xs text-muted-foreground">至</span>
        <Input
          className="h-9 w-28"
          type="time"
          aria-label={"第 " + (index + 1) + " 条规则的结束时刻"}
          value={rule.end}
          onChange={(event) => onChange({ end: event.target.value })}
        />
        <span className="text-2xs text-muted-foreground">
          {rule.end <= rule.start ? "跨天：结束落在次日" : "当日结束"}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto h-8 w-8"
          aria-label={"删除第 " + (index + 1) + " 条规则"}
          disabled={!removable}
          onClick={onRemove}
        >
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        </Button>
      </div>

      {rule.kind === "weekly" ? (
        <div
          className="mt-2 flex flex-wrap gap-1"
          role="group"
          aria-label={"第 " + (index + 1) + " 条规则的星期"}
        >
          {WEEKDAY_LABELS.map((day) => {
            const active = (rule.days ?? []).includes(day.value);
            return (
              <Button
                key={day.value}
                type="button"
                size="sm"
                variant={active ? "default" : "outline"}
                aria-pressed={active}
                onClick={() => onChange({ days: toggleValue(rule.days ?? [], day.value) })}
              >
                {day.label}
              </Button>
            );
          })}
        </div>
      ) : null}

      {rule.kind === "monthly" ? (
        <div
          className="mt-2 flex flex-wrap gap-1"
          role="group"
          aria-label={"第 " + (index + 1) + " 条规则的日号"}
        >
          {DAY_CELLS.map((day) => {
            const active = (rule.days_of_month ?? []).includes(day);
            return (
              <Button
                key={day}
                type="button"
                size="sm"
                variant={active ? "default" : "outline"}
                className="h-7 w-8 px-0 tabular-nums"
                aria-label={"第 " + (index + 1) + " 条规则选 " + day + " 日"}
                aria-pressed={active}
                onClick={() =>
                  onChange({ days_of_month: toggleValue(rule.days_of_month ?? [], day) })
                }
              >
                {day}
              </Button>
            );
          })}
        </div>
      ) : null}
      <span className="sr-only">{idPrefix + "-" + index}</span>
    </div>
  );
}

export interface ScheduleTimezonePickerProps {
  id?: string;
  value: string;
  invalid: boolean;
  onChange: (timezone: string) => void;
  describedBy?: string;
}

/**
 * 时区只能从候选里选：手打出「Asia/Shanghia」这类值时，规则永远算不中，
 * 冷却等于没配，却没有任何报错。历史值不合法时仍原样展示（红色），
 * 让人看见脏数据，而不是悄悄换成别的时区。
 */
export function ScheduleTimezonePicker({
  id = "schedule-timezone",
  value,
  invalid,
  onChange,
  describedBy,
}: ScheduleTimezonePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const allOptions = useMemo(() => {
    const options = timezoneOptions();
    return value && !options.includes(value) ? [value, ...options] : options;
  }, [value]);
  const filtered = useMemo(() => matchTimezones(query, allOptions), [query, allOptions]);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          id={id}
          role="combobox"
          aria-label="IANA 时区"
          aria-expanded={open}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          className={cn(
            "flex h-9 w-72 items-center gap-2 rounded-control border bg-surface px-3 text-left text-sm transition-colors hover:bg-surface-2 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
            invalid ? "border-danger text-danger-text" : "border-border-strong text-title",
          )}
        >
          <span className="min-w-0 flex-1 truncate">{value || "选择时区"}</span>
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
      <PopoverContent align="start" sideOffset={6} className="w-72 p-0 motion-reduce:animate-none">
        <Command>
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder="搜索时区，如 Tokyo / Europe"
            className="h-9 text-sm"
          />
          <CommandList>
            <CommandEmpty>没有匹配的时区</CommandEmpty>
            <CommandGroup>
              {filtered.map((timezone) => (
                <CommandItem
                  key={timezone}
                  value={timezone}
                  onSelect={() => {
                    onChange(timezone);
                    setOpen(false);
                    setQuery("");
                  }}
                >
                  <span className="min-w-0 flex-1 truncate">{timezone}</span>
                  {timezone === value ? (
                    <Check
                      className="h-3.5 w-3.5 shrink-0 text-primary"
                      strokeWidth={2}
                      aria-hidden="true"
                    />
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export interface ScheduleRulesFieldsetProps {
  rules: ScheduleRule[];
  onChange: (rules: ScheduleRule[]) => void;
  idPrefix?: string;
  addLabel?: string;
  /** 最少保留一条时的删除按钮会置灰；渠道表单允许 0 条（全天可用）。 */
  keepAtLeastOne?: boolean;
}

/** 规则列表 + 「再加一条」按钮；摘要与错误由调用方按自己的排版渲染。 */
export function ScheduleRulesFieldset({
  rules,
  onChange,
  idPrefix,
  addLabel = "再加一条规则",
  keepAtLeastOne = true,
}: ScheduleRulesFieldsetProps) {
  const patchRule = (index: number, patch: Partial<ScheduleRule>) => {
    onChange(rules.map((rule, position) => (position === index ? { ...rule, ...patch } : rule)));
  };
  return (
    <div className="flex flex-col gap-2">
      {rules.map((rule, index) => (
        <ScheduleRuleRow
          key={index}
          index={index}
          rule={rule}
          idPrefix={idPrefix}
          removable={!keepAtLeastOne || rules.length > 1}
          onChange={(patch) => patchRule(index, patch)}
          onRemove={() => onChange(rules.filter((_, position) => position !== index))}
        />
      ))}
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => onChange([...rules, emptyRule()])}>
          <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          {addLabel}
        </Button>
      </div>
    </div>
  );
}
