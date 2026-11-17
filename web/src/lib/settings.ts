import { apiRequest } from "@/lib/api";

export type BodyPolicy = "off" | "errors" | "all";

export type RouteStrategy = "uniform" | "stable" | "cost" | "latency";

export const DEFAULT_ROUTE_STRATEGY: RouteStrategy = "uniform";
export const DEFAULT_ROUTE_WINDOW_MINUTES = 30;
export const DEFAULT_INFLIGHT_CAP = 256;

export const ROUTE_STRATEGY_OPTIONS: ReadonlyArray<{ value: RouteStrategy; label: string }> = [
  { value: "uniform", label: "同层负载均衡" },
  { value: "stable", label: "稳定优先" },
  { value: "cost", label: "成本优先" },
  { value: "latency", label: "延迟优先" },
];

export interface SystemSettings {
  body_policy: BodyPolicy;
  body_truncate_bytes: number;
  body_retention_days: number;
  summary_max_bytes: number;
  updated_at: number | null;
  updated_by: string;
  log_retention_days: number;
  redaction_forced: boolean;
  gateway_base_url: string;
  gateway_updated_at: number | null;
  gateway_updated_by: string;
  upstream_connect_timeout_s: number;
  upstream_first_byte_timeout_s: number;
  upstream_read_timeout_s: number;
  upstream_timeouts_updated_at: number | null;
  upstream_timeouts_updated_by: string;
  upstream_timeouts_is_default: boolean;
  // 路由策略与准入控制字段：后端 SettingsOut 落地前可能缺省，读取方按默认值回退。
  route_strategy?: RouteStrategy;
  route_window_minutes?: number;
  gateway_inflight_cap?: number;
}

export async function getSettings(): Promise<SystemSettings> {
  return apiRequest<SystemSettings>("/api/settings");
}

export async function updateSettings(
  input:
    | BodyPolicy
    | { body_policy?: BodyPolicy; gateway_base_url?: string }
    | {
        upstream_connect_timeout_s?: number;
        upstream_first_byte_timeout_s?: number;
        upstream_read_timeout_s?: number;
        upstream_timeouts_reset?: boolean;
      }
    | {
        route_strategy?: RouteStrategy;
        route_window_minutes?: number;
        gateway_inflight_cap?: number;
      },
): Promise<SystemSettings> {
  const payload = typeof input === "string" ? { body_policy: input } : input;
  return apiRequest<SystemSettings>("/api/settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function validateGatewayBaseUrl(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) {
    return "请填写完整地址，例如 http://192.168.1.10:3681";
  }
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "网关地址必须以 http:// 或 https:// 开头";
    }
    if (!url.hostname) return "网关地址缺少主机名";
    if (url.username || url.password) return "网关地址不能包含账号密码";
    if (url.search || url.hash) return "网关地址不能包含查询参数或片段";
    return null;
  } catch {
    return "请填写完整地址，例如 http://192.168.1.10:3681";
  }
}

export function parseUpstreamTimeout(value: string): number | string {
  const trimmed = value.trim();
  if (!trimmed) return "请填写秒数";
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return "必须大于 0";
  if (parsed > 86_400) return "不能超过 86400 秒";
  return parsed;
}

export function parseRouteWindow(value: string): number | string {
  const trimmed = value.trim();
  if (!trimmed) return "请填写分钟数";
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return "必须是整数分钟";
  if (parsed < 15) return "不能小于 15 分钟";
  if (parsed > 1440) return "不能超过 1440 分钟（24 小时）";
  return parsed;
}

export function parseInflightCap(value: string): number | string {
  const trimmed = value.trim();
  if (!trimmed) return "请填写在飞上限";
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return "必须是整数";
  if (parsed < 0) return "不能小于 0（0 = 关闭）";
  return parsed;
}

export function effectiveGatewayBaseUrl(configured: string): {
  url: string;
  source: "configured" | "browser";
} {
  const trimmed = configured.trim().replace(/\/+$/, "");
  if (trimmed) return { url: trimmed, source: "configured" };
  const origin = typeof window === "undefined" ? "http://localhost:3681" : window.location.origin;
  return { url: origin.replace(/\/+$/, ""), source: "browser" };
}

export async function changeAdminPassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await apiRequest<{ ok: boolean }>("/api/auth/password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}
