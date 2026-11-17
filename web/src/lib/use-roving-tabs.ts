import { useRef, type KeyboardEvent, type RefCallback } from "react";

interface RovingTabProps {
  ref: RefCallback<HTMLButtonElement>;
  tabIndex: number;
  onClick: () => void;
}

/** ←/→ cycle through the tabs, Home/End jump to the first/last one; other keys stay with the browser. */
function nextTabKey<T extends string>(key: string, active: T, order: readonly T[]): T | null {
  const index = Math.max(0, order.indexOf(active));
  if (key === "ArrowLeft") return order[(index - 1 + order.length) % order.length] ?? null;
  if (key === "ArrowRight") return order[(index + 1) % order.length] ?? null;
  if (key === "Home") return order[0] ?? null;
  if (key === "End") return order[order.length - 1] ?? null;
  return null;
}

/**
 * Roving-tabindex model for a `role="tablist"`: the hook owns the single tab stop,
 * the per-tab refs and the ←/→/Home/End keys, so a tab list only describes the
 * buttons it renders (spread `tabProps(key)` on each one, reuse `onKeyDown`).
 */
export function useRovingTabs<T extends string>(
  order: readonly T[],
  active: T,
  select: (key: T) => void,
): {
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  tabProps: (key: T) => RovingTabProps;
} {
  const refs = useRef<Partial<Record<T, HTMLButtonElement | null>>>({});
  return {
    onKeyDown: (event) => {
      const next = nextTabKey(event.key, active, order);
      if (!next) return;
      event.preventDefault();
      select(next);
      refs.current[next]?.focus();
    },
    tabProps: (key) => ({
      ref: (node) => {
        refs.current[key] = node;
      },
      tabIndex: active === key ? 0 : -1,
      onClick: () => select(key),
    }),
  };
}
