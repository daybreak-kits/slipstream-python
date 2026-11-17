import { useEffect, useState } from "react";

import { readValue, writeValue } from "@/lib/storage";

export type ThemeMode = "system" | "light" | "dark";

const THEME_EVENT = "slipstream:theme-change";

export function readThemeMode(): ThemeMode {
  const stored = readValue<string>("theme");
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return "system";
}

export function prefersDark(): boolean {
  return Boolean(window.matchMedia?.("(prefers-color-scheme: dark)").matches);
}

export function resolveDark(mode: ThemeMode): boolean {
  if (mode === "dark") return true;
  if (mode === "light") return false;
  return prefersDark();
}

export function applyThemeMode(mode: ThemeMode): void {
  const dark = resolveDark(mode);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
  writeValue("theme", mode);
}

export function setThemeMode(mode: ThemeMode): void {
  applyThemeMode(mode);
  window.dispatchEvent(new CustomEvent<ThemeMode>(THEME_EVENT, { detail: mode }));
}

/**
 * Shared theme state for the topbar toggle and the settings page. The
 * `system` option follows the OS preference and reacts to live changes.
 */
export function useThemeMode() {
  const [mode, setModeState] = useState<ThemeMode>(readThemeMode);
  const [dark, setDark] = useState(() => resolveDark(readThemeMode()));

  useEffect(() => {
    const sync = (event?: Event) => {
      const next = event instanceof CustomEvent ? (event.detail as ThemeMode) : readThemeMode();
      setModeState(next);
      const nextDark = resolveDark(next);
      setDark(nextDark);
      document.documentElement.classList.toggle("dark", nextDark);
      document.documentElement.style.colorScheme = nextDark ? "dark" : "light";
    };
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onMedia = () => {
      if (readThemeMode() === "system") sync();
    };
    window.addEventListener(THEME_EVENT, sync);
    media?.addEventListener?.("change", onMedia);
    return () => {
      window.removeEventListener(THEME_EVENT, sync);
      media?.removeEventListener?.("change", onMedia);
    };
  }, []);

  const setMode = (next: ThemeMode) => {
    setThemeMode(next);
    setModeState(next);
    setDark(resolveDark(next));
  };

  return { mode, dark, setMode };
}
