import {
  PLAYGROUND_HISTORY_KEY,
  parsePlaygroundHistory,
  serializePlaygroundHistory,
  type PlaygroundMessage,
} from "@/lib/playground";

/**
 * 聊天记录只存在浏览器里：读不出来就退回空会话，页面照常可用。
 * 这里不用 lib/storage.ts 的系统键，避免和登录态 / 主题互相覆盖。
 */
export function loadPlaygroundHistory(): PlaygroundMessage[] {
  try {
    return parsePlaygroundHistory(window.localStorage.getItem(PLAYGROUND_HISTORY_KEY));
  } catch {
    return [];
  }
}

/** 落盘失败（隐私模式、配额用尽）时静默忽略：本次会话仍然可用，只是不留存。 */
export function savePlaygroundHistory(messages: PlaygroundMessage[]): void {
  try {
    window.localStorage.setItem(PLAYGROUND_HISTORY_KEY, serializePlaygroundHistory(messages));
  } catch {
    // 忽略：存储不可用不影响聊天本身。
  }
}

export function clearPlaygroundHistory(): void {
  try {
    window.localStorage.removeItem(PLAYGROUND_HISTORY_KEY);
  } catch {
    // 忽略：同上。
  }
}
