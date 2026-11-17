/**
 * 全站统一的计时格式化：默认以秒为单位、保留 2 位小数。
 * 后端所有 *_ms 字段都是毫秒整数，页面展示统一经过这里，避免各页各写一套。
 */

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

/**
 * 默认计时格式："(75.77s)"、"12.34s"、"0.00s"。
 * null / undefined / NaN / 负数统一显示破折号占位。
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  // 先取 0.01s 整数再格式化，避免 75.765 一类浮点边界被舍成 75.76。
  return `${(Math.round(ms / 10) / 100).toFixed(2)}s`;
}

/**
 * 自适应计时格式：不足 60s 仍用秒（2 位小数），更长时自动转 m/s、h/m、d/h，
 * 例如 75.765s → "1m15s"、1h2m → "1h02m"、2d3h → "2d03h"。
 * 60s 以上内部取整到秒，不再带小数，保证可读性。
 */
export function formatDurationAdaptive(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  const centiseconds = Math.round(ms / 10);
  if (centiseconds < 6000) return `${(centiseconds / 100).toFixed(2)}s`;
  const seconds = Math.floor(centiseconds / 100);
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m${pad2(seconds % 60)}s`;
  if (seconds < 86400) {
    return `${Math.floor(seconds / 3600)}h${pad2(Math.floor((seconds % 3600) / 60))}m`;
  }
  return `${Math.floor(seconds / 86400)}d${pad2(Math.floor((seconds % 86400) / 3600))}h`;
}
