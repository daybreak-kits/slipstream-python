import { apiRequest, ApiError } from "@/lib/api";
import type { ChannelModel } from "@/lib/channel-models";
import { formatDurationAdaptive } from "@/lib/duration";

export type Protocol = "openai_chat" | "anthropic_messages" | "gemini";
export type ConnectionStatus = "ok" | "timeout" | "error" | "unknown";

export type RewriteSection =
  | "request_headers"
  | "request_body"
  | "response_headers"
  | "response_body";

export type HeaderMode = "set" | "delete" | "rename";
export type BodyMode =
  | "create"
  | "update"
  | "delete"
  | "copy"
  | "prepend"
  | "append"
  | "regex_replace"
  | "to_upper"
  | "to_lower";
export type RewriteMode = HeaderMode | BodyMode;
export type ConditionMode = "full" | "regex" | "gt" | "gte" | "lt" | "lte";

export interface RewriteCondition {
  path: string;
  mode: ConditionMode;
  value?: unknown;
  invert?: boolean;
  pass_missing_key?: boolean;
}

export interface RewriteRule {
  /** Human note shown in the editor only; ignored by the rewrite engine. */
  note?: string;
  mode: RewriteMode;
  path?: string;
  from?: string;
  to?: string;
  value?: unknown;
  pattern?: string;
  replacement?: string;
  conditions?: RewriteCondition[];
  logic?: "AND" | "OR";
  keep_origin?: boolean;
}

export type RewriteRules = Partial<Record<RewriteSection, RewriteRule[]>>;

export interface Channel {
  id: string;
  slug: string;
  name: string;
  note: string;
  connection_url: string;
  endpoint_preview: string;
  models_endpoint_preview: string;
  api_key_set: boolean;
  api_key_masked: string;
  api_key_count: number;
  api_key_masks: string[];
  api_keys: ChannelKey[];
  protocol: Protocol;
  protocol_source: string;
  auth_style: string;
  priority: number;
  enabled: boolean;
  connection_status: ConnectionStatus;
  connection_latency_ms: number | null;
  connection_message: string;
  connection_tested_at: number | null;
  last_model_probe_latency_ms: number | null;
  last_model_probe_at: number | null;
  last_model_probe_status: ConnectionStatus;
  override_sections: RewriteSection[];
  rewrite_rules: RewriteRules;
  model_count: number;
  created_at: number;
  updated_at: number;
}

export interface ChannelList {
  items: Channel[];
  total: number;
}

export interface ChannelKey {
  id: string;
  label: string;
  masked: string;
  enabled: boolean;
  last_used_at: number | null;
  created_at: number;
}

export interface ChannelKeyList {
  items: ChannelKey[];
  total: number;
}

export interface ChannelKeyInput {
  label?: string;
  api_key: string;
}

export interface ChannelInput {
  slug?: string;
  name: string;
  note?: string;
  connection_url: string;
  api_key?: string;
  api_keys?: ChannelKeyInput[];
  // Omitted means auto-detect. Existing callers may still pin a protocol.
  protocol?: Protocol;
  priority: number;
  enabled: boolean;
  rewrite_rules?: RewriteRules;
}

export interface ChannelDiscoverInput {
  channel_id?: string;
  connection_url?: string;
  // Omitted keeps the stored key; an empty string means no key.
  api_key?: string;
  protocol?: Protocol;
  rewrite_rules?: RewriteRules;
}

export interface DiscoveredModel {
  upstream_model: string;
  already_added: boolean;
}

export interface ChannelDiscoverResult {
  status: "ok" | "timeout" | "error";
  models: DiscoveredModel[];
  protocol: string;
  auth_style: string;
  http_status: number | null;
  latency_ms: number;
  message: string;
}

export interface ChannelModelBulkResult {
  created: number;
  skipped: number;
  errors: string[];
  items: ChannelModel[];
}

export interface ConnectionTestResult {
  status: ConnectionStatus;
  latency_ms: number;
  http_status: number | null;
  message: string;
  model_count: number;
  models_preview: string[];
  protocol: Protocol;
  auth_style: string;
  tested_at: number | null;
}

export interface ChannelFilters {
  search?: string;
  status?: ConnectionStatus | "";
}

export const AUTO_PROTOCOL_HINT =
  "获取上游模型或测试连接时自动识别 OpenAI / Anthropic / Gemini，并记住可用的认证方式。";

export const PROTOCOLS: Array<{ value: Protocol; label: string; hint: string }> = [
  { value: "openai_chat", label: "OpenAI 兼容", hint: "Authorization: Bearer + /v1/models" },
  { value: "anthropic_messages", label: "Anthropic", hint: "x-api-key + anthropic-version" },
  { value: "gemini", label: "Gemini", hint: "查询参数 ?key=" },
];

export const PROTOCOL_LABELS: Record<Protocol, string> = {
  openai_chat: "OpenAI 兼容",
  anthropic_messages: "Anthropic",
  gemini: "Gemini",
};

export const CONNECTION_STATUS_META: Record<
  ConnectionStatus,
  { label: string; tone: "success" | "warning" | "danger" | "muted" }
> = {
  ok: { label: "正常运行", tone: "success" },
  timeout: { label: "连接超时", tone: "warning" },
  error: { label: "探测失败", tone: "danger" },
  unknown: { label: "未知状态", tone: "muted" },
};

export const AUTH_STYLE_LABELS: Record<string, string> = {
  bearer: "Bearer",
  "api-key": "api-key",
  "x-api-key": "x-api-key",
  "query-key": "query key",
};

export async function listChannels(filters: ChannelFilters = {}): Promise<ChannelList> {
  const params = new URLSearchParams();
  if (filters.search?.trim()) params.set("search", filters.search.trim());
  if (filters.status) params.set("status", filters.status);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return apiRequest<ChannelList>(`/api/channels${suffix}`);
}

export async function getChannel(id: string): Promise<Channel> {
  return apiRequest<Channel>(`/api/channels/${id}`);
}

export async function createChannel(input: ChannelInput): Promise<Channel> {
  return apiRequest<Channel>("/api/channels", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateChannel(id: string, input: Partial<ChannelInput>): Promise<Channel> {
  return apiRequest<Channel>(`/api/channels/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteChannel(id: string): Promise<void> {
  await apiRequest<null>(`/api/channels/${id}`, { method: "DELETE" });
}

export async function testChannelConnection(id: string): Promise<ConnectionTestResult> {
  return apiRequest<ConnectionTestResult>(`/api/channels/${id}/test-connection`, {
    method: "POST",
  });
}

export async function testChannelDraft(
  input: ChannelDiscoverInput,
): Promise<ConnectionTestResult> {
  return apiRequest<ConnectionTestResult>("/api/channels/test-connection", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function listChannelKeys(channelId: string): Promise<ChannelKeyList> {
  return apiRequest<ChannelKeyList>(`/api/channels/${channelId}/keys`);
}

export async function addChannelKey(
  channelId: string,
  input: ChannelKeyInput,
): Promise<ChannelKey> {
  return apiRequest<ChannelKey>(`/api/channels/${channelId}/keys`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function deleteChannelKey(channelId: string, keyId: string): Promise<void> {
  await apiRequest<null>(`/api/channels/${channelId}/keys/${keyId}`, { method: "DELETE" });
}

export interface ChannelKeyUpdateInput {
  label?: string;
  enabled?: boolean;
}

export async function updateChannelKey(
  channelId: string,
  keyId: string,
  input: ChannelKeyUpdateInput,
): Promise<ChannelKey> {
  return apiRequest<ChannelKey>(`/api/channels/${channelId}/keys/${keyId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function maskSecret(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  if (trimmed.length <= 4) return "*".repeat(trimmed.length);
  return `\u2022\u2022\u2022\u2022\u2022\u2022${trimmed.slice(-4)}`;
}

export async function discoverChannelModels(
  input: ChannelDiscoverInput,
): Promise<ChannelDiscoverResult> {
  return apiRequest<ChannelDiscoverResult>("/api/channels/discover-models", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function bulkAddChannelModels(
  channelId: string,
  upstreamModels: string[],
): Promise<ChannelModelBulkResult> {
  return apiRequest<ChannelModelBulkResult>(`/api/channels/${channelId}/models/bulk`, {
    method: "POST",
    body: JSON.stringify({ upstream_models: upstreamModels }),
  });
}

export interface RewritePreviewResult {
  section: RewriteSection;
  before: unknown;
  after: unknown;
  diff: Array<{ path: string; before: unknown; after: unknown }>;
  warnings: string[];
  skipped: string;
  rule_results?: Array<{
    index: number;
    mode: string;
    path: string;
    reason: string;
    message?: string;
  }>;
}

export async function previewRewriteRules(
  id: string,
  input: {
    section: RewriteSection;
    rules: RewriteRules;
    body?: Record<string, unknown>;
    headers?: Record<string, string>;
  },
): Promise<RewritePreviewResult> {
  return apiRequest<RewritePreviewResult>(`/api/channels/${id}/rewrite-preview`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.details as
      | { errors?: Array<{ loc?: Array<string | number>; msg?: string }> }
      | undefined;
    const fieldErrors = details?.errors
      ?.map((item) => (item.msg ?? "").replace(/^Value error,\s*/i, ""))
      .filter(Boolean);
    if (fieldErrors && fieldErrors.length > 0) {
      return fieldErrors.join("；");
    }
    return error.message;
  }
  return "网络异常，请确认 API 服务已启动后重试。";
}

// ---------------------------------------------------------------------------
// URL preview helpers (kept in sync with l03_domain/urls.py)
// ---------------------------------------------------------------------------
const VERSION_SEGMENT = /^v\d+(?:alpha|beta)?\d*$/i;

function splitExact(url: string): { base: string; exact: boolean } {
  const value = url.trim();
  const exact = value.endsWith("#");
  const withoutHash = exact ? value.slice(0, -1) : value;
  return { base: withoutHash.replace(/\/+$/, ""), exact };
}

export function isExactEndpoint(url: string): boolean {
  return url.trim().endsWith("#");
}

export function chatUrlFor(url: string): string {
  if (!url.trim()) return "";
  const { base, exact } = splitExact(url);
  if (!base) return "";
  if (exact) return base;
  if (base.endsWith("/chat/completions")) return base;
  if (VERSION_SEGMENT.test(base.split("/").pop() ?? "")) return `${base}/chat/completions`;
  return `${base}/v1/chat/completions`;
}

export function modelsUrlFor(url: string): string {
  if (!url.trim()) return "";
  const { base, exact } = splitExact(url);
  if (!base) return "";
  if (exact) return base;
  if (base.endsWith("/models")) return base;
  if (VERSION_SEGMENT.test(base.split("/").pop() ?? "")) return `${base}/models`;
  return `${base}/v1/models`;
}

export function slugFromUrl(value: string): string {
  let raw = value.trim().replace(/#$/, "").replace(/\/+$/, "");
  if (!raw) return "channel";
  if (!raw.includes("://")) raw = `https://${raw}`;

  let host = "";
  let port = "";
  let path = "";
  try {
    const parsed = new URL(raw);
    host = parsed.hostname.toLowerCase().replace(/\./g, "-").replace(/[^a-z0-9-]/g, "-");
    port = parsed.port && parsed.port !== "80" && parsed.port !== "443" ? parsed.port : "";
    path = parsed.pathname.replace(/^\/+|\/+$/g, "");
  } catch {
    host = raw.toLowerCase().replace(/[^a-z0-9-]/g, "-");
  }

  host = host.replace(/-+/g, "-").replace(/^-+|-+$/g, "") || "channel";
  if (!/^[a-z]/.test(host)) host = `ch-${host}`;
  if (port) host = `${host}-${port}`;
  if (path && path !== "v1" && path !== "api/v1") {
    const suffix = path.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
    if (suffix) host = `${host}-${suffix}`;
  }
  return host.slice(0, 64);
}

export function formatLatencyAdaptive(ms: number | null | undefined): string {
  return formatDurationAdaptive(ms);
}

export const REWRITE_SECTION_LABELS: Record<RewriteSection, string> = {
  request_headers: "请求头",
  request_body: "请求体",
  response_headers: "响应头",
  response_body: "响应体",
};
