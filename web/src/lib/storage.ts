/**
 * Console persistence, kept to a single localStorage entry.
 *
 * Everything the browser remembers (session token, theme, timezone, log
 * preferences, sidebar state) lives inside one JSON object under
 * `system.storage`, so renaming the product or adding a field only ever
 * touches this module.
 *
 * Known exceptions — legacy standalone keys, documented here rather than
 * migrated (audit N8):
 * - `slipstream.playground.history.v1` (PLAYGROUND_HISTORY_KEY in
 *   `lib/playground.ts`): playground chat history, kept on its own key so
 *   large transcripts never re-serialize the shared store.
 *
 * New keys must join the `StoredKey` union below (ui.md §2.4 客户端存储单源);
 * do not introduce additional standalone keys.
 */
export const STORAGE_KEY = "system.storage";

export type StoredKey =
  | "token"
  | "theme"
  | "timezone"
  | "logsHideSensitive"
  | "navCollapsed";

export type StoredValue = string | number | boolean | null;

type Store = Record<string, StoredValue>;

function isStore(value: unknown): value is Store {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readStore(): Store {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return isStore(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeStore(store: Store): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Storage can be unavailable (private mode); in-memory state still works.
  }
}

export function readValue<T extends StoredValue>(key: StoredKey): T | undefined {
  const value = readStore()[key];
  return value === undefined ? undefined : (value as T);
}

export function writeValue(key: StoredKey, value: StoredValue): void {
  const store = readStore();
  if (store[key] === value) return;
  store[key] = value;
  writeStore(store);
}

export function removeValue(key: StoredKey): void {
  const store = readStore();
  if (!(key in store)) return;
  delete store[key];
  writeStore(store);
}
