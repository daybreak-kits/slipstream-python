import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { readValue, writeValue } from "@/lib/storage";

export type TimezoneMode = "local" | "utc";

interface TimezoneContextValue {
  mode: TimezoneMode;
  setMode: (mode: TimezoneMode) => void;
  toggle: () => void;
}

const LOCAL_FALLBACK: TimezoneContextValue = {
  mode: "local",
  setMode: () => undefined,
  toggle: () => undefined,
};

// Defaulting keeps isolated renders (tests, storybook-style previews) working;
// the app root always supplies a real TimezoneProvider.
const TimezoneContext = createContext<TimezoneContextValue>(LOCAL_FALLBACK);

function readInitialMode(): TimezoneMode {
  return readValue<string>("timezone") === "utc" ? "utc" : "local";
}

export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<TimezoneMode>(readInitialMode);

  const setMode = useCallback((next: TimezoneMode) => {
    setModeState(next);
    writeValue("timezone", next);
  }, []);

  const toggle = useCallback(() => {
    setMode(mode === "utc" ? "local" : "utc");
  }, [mode, setMode]);

  const value = useMemo<TimezoneContextValue>(
    () => ({ mode, setMode, toggle }),
    [mode, setMode, toggle],
  );

  return <TimezoneContext.Provider value={value}>{children}</TimezoneContext.Provider>;
}

export function useTimezone(): TimezoneContextValue {
  return useContext(TimezoneContext);
}

export const TIMEZONE_LABEL: Record<TimezoneMode, string> = {
  local: "本地",
  utc: "UTC",
};

interface DateTimeParts {
  date: string;
  time: string;
}

// Intl.DateTimeFormat construction is the expensive part; a 100-row log table
// used to create one formatter per cell. Cache one per timezone mode.
const dateTimeFormatters = new Map<TimezoneMode, Intl.DateTimeFormat>();

function dateTimeFormatter(mode: TimezoneMode): Intl.DateTimeFormat {
  const cached = dateTimeFormatters.get(mode);
  if (cached) return cached;
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    timeZone: mode === "utc" ? "UTC" : undefined,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
  });
  dateTimeFormatters.set(mode, formatter);
  return formatter;
}

export function formatDateTimeParts(
  ms: number | null | undefined,
  mode: TimezoneMode,
): DateTimeParts | null {
  if (!ms) return null;
  const parts = dateTimeFormatter(mode).formatToParts(new Date(ms));
  const values: Record<string, string> = {};
  for (const part of parts) {
    if (part.type !== "literal") values[part.type] = part.value;
  }
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    time: `${values.hour}:${values.minute}:${values.second}`,
  };
}

export function formatDateTime(ms: number | null | undefined, mode: TimezoneMode): string {
  const parts = formatDateTimeParts(ms, mode);
  if (!parts) return "—";
  return `${parts.date} ${parts.time}`;
}
