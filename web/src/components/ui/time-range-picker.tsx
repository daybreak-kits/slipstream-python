import { zhCN } from "date-fns/locale";
import { CalendarDays, Check, ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import type { DateRange } from "react-day-picker";

import { Calendar } from "@/components/ui/calendar";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useOverflow } from "@/components/ui/use-overflow";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  TIME_RANGE_OPTIONS,
  formatInterval,
  rangeBounds,
  timeRangeLabel,
  type IntervalTimezone,
  type TimeRange,
} from "@/lib/time-range";
import { TIMEZONE_LABEL } from "@/lib/time";
import { cn } from "@/lib/utils";

interface TimeRangePickerProps {
  range: TimeRange;
  customStart: string;
  customEnd: string;
  /** 已生效区间的展示文本，形如 2026/11/11 00:00:00 - 2026/11/11 13:30:34；为空时回退到预设名。 */
  interval?: string;
  onChange: (range: TimeRange, customStart: string, customEnd: string) => void;
  className?: string;
  /** 草稿范围与已生效范围不同：给触发按钮描边。 */
  highlighted?: boolean;
  /** 浮层底部提示；只有调用方真有「搜索」这类提交按钮时才传，别在即时生效的页面上误导用户。 */
  hint?: string;
  /** tooltip 与回退区间文本使用的时区，默认浏览器本地。 */
  timezone?: IntervalTimezone;
}

/** 日历可回看的天数：与日志/看板最长查询窗口对齐，更早的范围走 30 天预设。 */
const LOOKBACK_DAYS = 365;
const DEFAULT_START_TIME = "00:00";
const DEFAULT_END_TIME = "23:59";

const TRIGGER_CLASS =
  "flex h-9 w-full items-center gap-2 rounded-control border border-border-strong bg-surface px-3 text-left text-xs text-title transition-colors hover:bg-surface-2 focus-visible:border-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg";

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/** 日历格子 + 时刻 → 父层用的 datetime-local 值（YYYY-MM-DDTHH:mm，本地时区）。 */
function toValue(day: Date, time: string): string {
  return `${day.getFullYear()}-${pad2(day.getMonth() + 1)}-${pad2(day.getDate())}T${time}`;
}

/** 父层值 → 日历格子与时刻；解析失败返回 null，交给 invalid 提示。 */
function parseValue(value: string): { day: Date; time: string } | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return null;
  return { day: parsed, time: `${pad2(parsed.getHours())}:${pad2(parsed.getMinutes())}` };
}

export function TimeRangePicker({
  range,
  customStart,
  customEnd,
  interval,
  onChange,
  className,
  highlighted = false,
  hint,
  timezone = "local",
}: TimeRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [selectedRange, setSelectedRange] = useState<TimeRange>(range);
  const [days, setDays] = useState<DateRange | undefined>(undefined);
  const [startTime, setStartTime] = useState(DEFAULT_START_TIME);
  const [endTime, setEndTime] = useState(DEFAULT_END_TIME);

  // 打开弹窗或父层有效值变化时同步日历与时刻；父层只在区间有效时才更新，
  // 所以选到一半的范围不会被冲掉。
  useEffect(() => {
    if (!open) return;
    setSelectedRange(range);
    const start = parseValue(customStart);
    const end = parseValue(customEnd);
    if (range === "custom" && start && end) {
      setDays({ from: start.day, to: end.day });
      setStartTime(start.time);
      setEndTime(end.time);
      return;
    }
    setDays(undefined);
    setStartTime(DEFAULT_START_TIME);
    setEndTime(DEFAULT_END_TIME);
  }, [open, range, customStart, customEnd]);

  const today = new Date();
  const earliest = new Date(today);
  earliest.setDate(earliest.getDate() - LOOKBACK_DAYS);

  /** 起止都齐、且结束晚于开始，才向上抛；否则父层保留上一次有效值。 */
  const publish = (nextDays: DateRange | undefined, nextStart: string, nextEnd: string) => {
    if (!nextDays?.from || !nextDays.to || !nextStart || !nextEnd) return;
    const start = toValue(nextDays.from, nextStart);
    const end = toValue(nextDays.to, nextEnd);
    if (new Date(end).getTime() > new Date(start).getTime()) {
      onChange("custom", start, end);
    }
  };

  /** 预设即时生效并关窗；「自定义」只是把焦点交回日历，不关窗。 */
  const choosePreset = (value: TimeRange) => {
    setSelectedRange(value);
    if (value === "custom") {
      // 切到自定义时把当前生效区间铺进日历，用户改一处就行，不用从空白重选。
      if (!days?.from) {
        const current = rangeBounds(range, customStart, customEnd);
        if (current.start !== undefined && current.end !== undefined) {
          const from = new Date(current.start);
          const to = new Date(current.end);
          setDays({ from, to });
          setStartTime(`${pad2(from.getHours())}:${pad2(from.getMinutes())}`);
          setEndTime(`${pad2(to.getHours())}:${pad2(to.getMinutes())}`);
        }
      }
      return;
    }
    onChange(value, "", "");
    setOpen(false);
  };

  const chooseDays = (next: DateRange | undefined) => {
    setSelectedRange("custom");
    setDays(next);
    publish(next, startTime, endTime);
  };

  const chooseTime = (nextStart: string, nextEnd: string) => {
    setSelectedRange("custom");
    setStartTime(nextStart);
    setEndTime(nextEnd);
    publish(days, nextStart, nextEnd);
  };

  const customInvalid = (() => {
    if (selectedRange !== "custom") return false;
    // 还没点日期就先别报错，否则一打开「自定义」就是一片红。
    if (!days?.from) return false;
    if (!days.to || !startTime || !endTime) return true;
    const start = new Date(toValue(days.from, startTime)).getTime();
    const end = new Date(toValue(days.to, endTime)).getTime();
    return !Number.isFinite(start) || !Number.isFinite(end) || end <= start;
  })();

  const label = timeRangeLabel(range, customStart, customEnd);
  // 触发按钮优先显示真实区间（秒级、单横线），预设名只在区间算不出来时兜底；
  // 调用方显式给了 interval（草稿值）就以调用方为准。
  const bounds = rangeBounds(range, customStart, customEnd);
  const display = interval ?? formatInterval(bounds.start, bounds.end, timezone) ?? label;
  const { ref: labelRef, overflowing } = useOverflow<HTMLSpanElement>(display);
  const [tooltipOpen, setTooltipOpen] = useState(false);

  return (
    <div className={cn("min-w-0", className)}>
      <TooltipProvider delayDuration={150}>
        <Tooltip
          open={tooltipOpen}
          onOpenChange={(next) => setTooltipOpen(next && overflowing)}
        >
          <Popover open={open} onOpenChange={setOpen}>
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <button
            type="button"
            className={cn(
              TRIGGER_CLASS,
              highlighted && "filter-dirty border-primary/70 bg-primary-soft/40",
            )}
            aria-haspopup="dialog"
            aria-label={`时间范围：${display}`}
          >
            <CalendarDays
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <span
              ref={labelRef}
              className="min-w-0 truncate tabular-nums leading-tight text-body"
            >
              {display}
            </span>
            <ChevronDown
              className={cn(
                "ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
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
          sideOffset={8}
          aria-label="选择时间范围"
          className="w-auto p-0 motion-reduce:animate-none"
        >
          <div className="flex flex-col gap-3 p-3 sm:flex-row">
            <Calendar
              mode="range"
              selected={days}
              onSelect={chooseDays}
              numberOfMonths={1}
              locale={zhCN}
              startMonth={earliest}
              endMonth={today}
              disabled={{ after: today }}
              className="bg-transparent p-0"
            />

            <div className="flex flex-col gap-2 sm:w-52 sm:border-l sm:border-border sm:pl-3">
              <div className="grid grid-cols-2 gap-1.5">
                {TIME_RANGE_OPTIONS.map((option) => {
                  const active = selectedRange === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => choosePreset(option.value)}
                      aria-pressed={active}
                      className={cn(
                        "flex items-center justify-center gap-1 rounded-control border px-2 py-1.5 text-xs transition-colors",
                        active
                          ? "border-primary bg-primary-soft text-primary"
                          : "border-border text-body hover:bg-surface-2 hover:text-title",
                      )}
                    >
                      {option.label}
                      {active ? (
                        <Check className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                      ) : null}
                    </button>
                  );
                })}
              </div>

              <div className="rounded-control border border-border bg-surface-2 p-2">
                <p className="text-xs text-muted-foreground">起止时刻</p>
                <div className="mt-1.5 flex flex-col gap-1.5">
                  <div className="flex items-center gap-2">
                    <span className="w-7 shrink-0 text-2xs text-faint">开始</span>
                    <Input
                      type="time"
                      aria-label="开始时刻"
                      value={startTime}
                      onChange={(event) => chooseTime(event.target.value, endTime)}
                      className="h-9 flex-1 px-2 text-xs"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-7 shrink-0 text-2xs text-faint">结束</span>
                    <Input
                      type="time"
                      aria-label="结束时刻"
                      value={endTime}
                      onChange={(event) => chooseTime(startTime, event.target.value)}
                      className="h-9 flex-1 px-2 text-xs"
                    />
                  </div>
                </div>
                {customInvalid && selectedRange === "custom" ? (
                  <p className="mt-1.5 text-xs text-danger-text">
                    请选择起止日期，且结束时刻晚于开始时刻。
                  </p>
                ) : null}
              </div>

              {hint ? <p className="text-xs text-faint">{hint}</p> : null}
            </div>
          </div>
          </PopoverContent>
          </Popover>
          <TooltipContent title="时间范围">
            <span className="block tabular-nums">{display}</span>
            <span className="mt-0.5 block text-[var(--tooltip-fg-muted)]">
              时区：{TIMEZONE_LABEL[timezone]}
            </span>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
