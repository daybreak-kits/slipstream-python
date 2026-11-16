// Audit-only server for the channel pages. Serves dist-audit and mock /api.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const ROOT = resolve(process.cwd(), "dist-audit");
const PORT = Number(process.env.PORT || 3692);
const NOW = Date.parse("2026-11-15T00:00:00+08:00");
const state = { mode: "populated", testStatus: "ok", probeStatus: "ok" };

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".json": "application/json; charset=utf-8",
};

const json = (res, body, status = 200) => {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(body));
};
const empty = (res) => { res.writeHead(204); res.end(); };
const readBody = (req) => new Promise((done) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    try { done(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}")); } catch { done({}); }
  });
});

const key = (id, label, masked, enabled, last) => ({
  id, label, masked, enabled, last_used_at: last, created_at: NOW - 12 * 86400000,
});
const keyRows = [
  key("key-1", "primary", "••••••abcd", true, NOW - 2 * 3600000),
  key("key-2", "backup-2026-10", "••••••wxyz", true, NOW - 26 * 3600000),
  key("key-3", "旧版脚本（已停用）", "••••••old9", false, null),
];

function channel(id, slug, name, note, status, priority, enabled, models, keys, protocol, overrides) {
  const masks = keys.slice(0, 2).map((k) => k.masked);
  return {
    id, slug, name, note,
    connection_url: "https://upstream.example.com/" + slug + "/v1",
    endpoint_preview: "https://upstream.example.com/" + slug + "/v1/chat/completions",
    models_endpoint_preview: "https://upstream.example.com/" + slug + "/v1/models",
    api_key_set: keys.length > 0,
    api_key_masked: masks[0] || "",
    api_key_count: keys.length,
    api_key_masks: masks,
    api_keys: keys,
    protocol, protocol_source: "manual", auth_style: "bearer", priority, enabled,
    connection_status: status,
    connection_latency_ms: status === "ok" ? 128 : status === "timeout" ? 3000 : null,
    connection_message: status === "ok" ? "连接正常" : status === "timeout" ? "请求 /models 超时（3000 ms）" : status === "error" ? "401 Unauthorized：密钥无效或已过期" : "尚未测试连接",
    connection_tested_at: status === "unknown" ? null : NOW - 18 * 60000,
    last_model_probe_latency_ms: status === "ok" ? 160 : status === "timeout" ? 3000 : null,
    last_model_probe_at: status === "unknown" ? null : NOW - 18 * 60000,
    last_model_probe_status: status,
    override_sections: overrides,
    rewrite_rules: overrides.includes("request_body") ? {
      request_body: [
        { note: "统一温度", mode: "update", path: "temperature", value: 0.2, logic: "AND", conditions: [{ path: "model", mode: "full", value: "openai/gpt-4o" }] },
        { note: "追加系统提示", mode: "append", path: "messages.0.content", value: " 请简洁回答。" },
      ],
    } : {},
    model_count: models,
    created_at: NOW - 40 * 86400000,
    updated_at: NOW - 18 * 60000,
  };
}

const channels = [
  channel("ch-1", "channel-a", "OpenAI 主渠道", "生产环境主入口，承载大部分聊天流量", "ok", 10, true, 4, keyRows, "openai_chat", ["request_body"]),
  channel("ch-2", "channel-b", "Anthropic 备用", "主渠道超时或 429 时按优先级切换", "timeout", 20, true, 2, [key("key-4", "backup", "••••••back", true, NOW - 86400000)], "anthropic_messages", []),
  channel("ch-3", "channel-c", "Gemini 廉价款", "长备注用于验证列表中的截断行为：这是一段明显超过单行宽度的说明文字，用于检查省略号与换行是否得体", "error", 30, false, 7, [], "gemini", ["request_headers"]),
  channel("ch-4", "channel-d", "本地 vLLM（已停用）", null, "unknown", 40, false, 2, [key("key-5", "local", "••••••loc1", true, null)], "openai_chat", []),
  channel("ch-5", "very-long-channel-identifier-for-truncation", "一个非常非常长的渠道名称用来检查表格列宽与省略号是否正常工作", "短备注", "ok", 50, true, 12, [], "openai_chat", []),
];

const models = [
  { id: "cm-1", channel_id: "ch-1", upstream_model: "gpt-4o", call_model: "openai/gpt-4o", parameters: { max_input_tokens: 128000, max_output_tokens: 16384, reasoning_level: "", input_types: ["text", "image"], output_types: ["text"] }, input_price_per_1m: 2.5, output_price_per_1m: 10, cache_read_price_per_1m: 1.25, cache_write_price_per_1m: null, enabled: true, health_status: "normal", visible_status: "normal", consecutive_failures: 0, consecutive_trip_count: 0, escalation_trip_count: 0, opened_at: null, cooldown_until: null, fault_until: null, next_retry_at: null, cooldown: { failure_threshold: 3, cooldown_s: 60, rate_limit_cooldown_s: 30, half_open_max: 1, escalate_mode: "after_n", escalate_after: 3, fault_cooldown_s: 300 }, schedule: { timezone: "Asia/Shanghai", segments: [] }, probe_status: "ok", probe_latency_ms: 820, probe_message: "200 OK · 模型可用", probed_at: NOW - 18 * 60000, probe_default: true, model_type: "chat", model_type_source: "manual", created_at: NOW - 30 * 86400000, updated_at: NOW - 18 * 60000 },
  { id: "cm-2", channel_id: "ch-1", upstream_model: "gpt-4o-mini", call_model: "openai/gpt-4o-mini", parameters: { max_input_tokens: 128000, max_output_tokens: 16384, reasoning_level: "", input_types: ["text"], output_types: ["text"] }, input_price_per_1m: 0.15, output_price_per_1m: 0.6, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: true, health_status: "half_open", visible_status: "half_open", consecutive_failures: 3, consecutive_trip_count: 1, escalation_trip_count: 0, opened_at: NOW - 4 * 60000, cooldown_until: NOW + 2 * 60000, fault_until: null, next_retry_at: NOW + 2 * 60000, cooldown: { failure_threshold: 3, cooldown_s: 60, rate_limit_cooldown_s: 30, half_open_max: 1, escalate_mode: "after_n", escalate_after: 3, fault_cooldown_s: 300 }, schedule: { timezone: "Asia/Shanghai", segments: [{ days: [1, 2, 3, 4, 5], start: "09:00", end: "18:00" }] }, probe_status: "timeout", probe_latency_ms: 3000, probe_message: "请求超时：上游 3000 ms 未返回", probed_at: NOW - 6 * 60000, probe_default: false, model_type: "chat", model_type_source: "upstream", created_at: NOW - 25 * 86400000, updated_at: NOW - 6 * 60000 },
  { id: "cm-3", channel_id: "ch-1", upstream_model: "claude-sonnet-4-20250514", call_model: "anthropic/claude-sonnet-4", parameters: { max_input_tokens: 200000, max_output_tokens: 8192, reasoning_level: "high", input_types: ["text", "image"], output_types: ["text"] }, input_price_per_1m: 3, output_price_per_1m: 15, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: false, health_status: "fault", visible_status: "fault", consecutive_failures: 8, consecutive_trip_count: 4, escalation_trip_count: 2, opened_at: NOW - 2 * 86400000, cooldown_until: null, fault_until: NOW + 3600000, next_retry_at: NOW + 3600000, cooldown: { failure_threshold: 3, cooldown_s: 60, rate_limit_cooldown_s: 30, half_open_max: 1, escalate_mode: "after_n", escalate_after: 3, fault_cooldown_s: 300 }, schedule: { timezone: "Asia/Shanghai", segments: [] }, probe_status: "error", probe_latency_ms: 412, probe_message: "401 Unauthorized：API key 无效", probed_at: NOW - 2 * 86400000, probe_default: false, model_type: "chat", model_type_source: "manual", created_at: NOW - 20 * 86400000, updated_at: NOW - 2 * 86400000 },
  { id: "cm-4", channel_id: "ch-1", upstream_model: "text-embedding-3-large", call_model: "openai/text-embedding-3-large", parameters: { max_input_tokens: 8191, max_output_tokens: 0, reasoning_level: "", input_types: ["text"], output_types: ["embedding"] }, input_price_per_1m: 0.13, output_price_per_1m: 0, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: true, health_status: "normal", visible_status: "normal", consecutive_failures: 0, consecutive_trip_count: 0, escalation_trip_count: 0, opened_at: null, cooldown_until: null, fault_until: null, next_retry_at: null, cooldown: { failure_threshold: 3, cooldown_s: 60, rate_limit_cooldown_s: 30, half_open_max: 1, escalate_mode: "after_n", escalate_after: 3, fault_cooldown_s: 300 }, schedule: { timezone: "Asia/Shanghai", segments: [] }, probe_status: "unknown", probe_latency_ms: null, probe_message: "", probed_at: null, probe_default: false, model_type: "embedding", model_type_source: "upstream", created_at: NOW - 10 * 86400000, updated_at: NOW - 10 * 86400000 },
  { id: "cm-5", channel_id: "ch-1", upstream_model: "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", call_model: "meta/llama-4", parameters: { max_input_tokens: 1000000, max_output_tokens: 32768, reasoning_level: "", input_types: ["text", "image"], output_types: ["text"] }, input_price_per_1m: 0.9, output_price_per_1m: 1.2, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: true, health_status: "normal", visible_status: "normal", consecutive_failures: 0, consecutive_trip_count: 0, escalation_trip_count: 0, opened_at: null, cooldown_until: null, fault_until: null, next_retry_at: null, cooldown: { failure_threshold: 3, cooldown_s: 60, rate_limit_cooldown_s: 30, half_open_max: 1, escalate_mode: "after_n", escalate_after: 3, fault_cooldown_s: 300 }, schedule: { timezone: "Asia/Shanghai", segments: [] }, probe_status: "ok", probe_latency_ms: 1560, probe_message: "200 OK", probed_at: NOW - 3600000, probe_default: false, model_type: "chat", model_type_source: "default", created_at: NOW - 5 * 86400000, updated_at: NOW - 3600000 },
];

const summary = {
  requests: 1284, prompt_tokens: 2840112, completion_tokens: 984450, cached_tokens: 512000,
  cost_usd: 12.483921, errors: 17, error_rate: 0.0132, p95_ttft_ms: 3200, p95_total_ms: 12400,
  latency: { samples: 128, ttft: { p50: 1200, p90: 2600, p95: 3200, p99: 4100 }, ttfa: { p50: 1800, p90: 3200, p95: 3900, p99: 4800 }, ttlt: { p50: 8400, p90: 11200, p95: 12800, p99: 15600 }, total: { p50: 8500, p90: 11400, p95: 13100, p99: 16200 } },
  start_ms: NOW - 7 * 86400000, end_ms: NOW,
};
const series = {
  range: "7d", timezone: "Asia/Shanghai", granularity: "hour", start_ms: NOW - 24 * 3600000, end_ms: NOW,
  points: Array.from({ length: 24 }, (_, i) => ({ start_ms: NOW - (23 - i) * 3600000, requests: 20 + ((i * 7) % 41), prompt_tokens: 40000 + i * 2500, completion_tokens: 12000 + i * 800, cost_usd: 0.28 + i * 0.02, errors: i % 6 === 0 ? 2 : 0, p95_ttft_ms: 2600 + (i % 5) * 180, p95_total_ms: 9800 + (i % 7) * 420, p95_ttfa_ms: 3200 + (i % 5) * 210, p95_ttlt_ms: 10600 + (i % 7) * 390, by_model: [] })),
  top_models: [{ name: "gpt-4o", requests: 642 }, { name: "gpt-4o-mini", requests: 418 }],
  top_channels: [{ name: "channel-a", requests: 712 }],
  error_distribution: [{ code: "RATE_LIMITED", count: 9 }],
  heatmap: Array.from({ length: 168 }, (_, i) => ({ weekday: Math.floor(i / 24), hour: i % 24, requests: (i * 13) % 31 })),
};
const logs = Array.from({ length: 6 }, (_, i) => ({ id: "log-" + i, request_id: "req-" + i, upstream_request_id: "up-" + i, user_id: "user-1", user_name: "alice", api_key_id: "key-1", api_key_label: "primary", requested_model: i === 1 ? "anthropic/claude-sonnet-4" : "openai/gpt-4o", channel_name: "channel-a", upstream_model: "gpt-4o", response_model: "gpt-4o", is_stream: i % 2 === 0, http_status: i === 2 ? 500 : 200, error_code: i === 2 ? "UPSTREAM_ERROR" : null, error_message: "", prompt_tokens: 240, completion_tokens: 80, cached_tokens: 0, cache_ratio: 0, cost_usd: 0.0001, ttft_ms: 900, ttfa_ms: 1200, ttlt_ms: 2000, total_ms: 2100 + i * 100, throughput_tps: 30, usage_estimated: false, attempts: [], rewrite_applied: false, rewrite_skipped: "", rewrite_diff: {}, params: {}, response_headers: {}, error_body: null, created_at: NOW - i * 180000 }));

async function api(req, res, url) {
  const p = url.pathname;
  const method = req.method || "GET";
  if (p === "/__audit/state") {
    if (method === "POST") Object.assign(state, await readBody(req));
    return json(res, state);
  }
  if (p === "/api/auth/me") return json(res, { id: "admin-1", username: "admin", created_at: NOW, updated_at: NOW });
  if (p === "/api/settings") return json(res, { body_policy: "errors", body_truncate_bytes: 16384, body_retention_days: 7, summary_max_bytes: 65536, updated_at: NOW, updated_by: "admin", log_retention_days: 30, redaction_forced: true, gateway_base_url: "http://192.168.1.10:3681", gateway_updated_at: NOW, gateway_updated_by: "admin" });
  if (p === "/api/dashboard/summary") return json(res, summary);
  if (p === "/api/dashboard/timeseries") return json(res, series);
  if (p === "/api/logs/filters") return json(res, { channels: ["channel-a"], models: ["openai/gpt-4o"], key_labels: ["primary"], users: [{ id: "user-1", name: "alice" }], truncated: { channels: false, models: false, key_labels: false, users: false } });
  if (p === "/api/logs") return json(res, { items: logs, total: logs.length, page: 1, page_size: 20 });
  if (p === "/api/channels/test-connection" && method === "POST") return json(res, testResult());
  if (p === "/api/channels/discover-models" && method === "POST") return json(res, discoverResult());
  if (p === "/api/channels" && method === "GET") {
    let items = state.mode === "empty" ? [] : channels.slice();
    const search = (url.searchParams.get("search") || "").toLowerCase();
    const status = url.searchParams.get("status") || "";
    if (search) items = items.filter((c) => (c.name + " " + c.slug + " " + c.connection_url).toLowerCase().includes(search));
    if (status) items = items.filter((c) => c.connection_status === status);
    return json(res, { items, total: state.mode === "empty" ? 0 : items.length });
  }
  if (p === "/api/channels" && method === "POST") return json(res, { ...channels[0], id: "ch-new", name: "新建渠道", slug: "channel-new" });
  const channelMatch = p.match(/^\/api\/channels\/([^/]+)$/);
  if (channelMatch) {
    const c = channels.find((item) => item.id === channelMatch[1]) || channels[0];
    if (method === "DELETE") return empty(res);
    if (method === "PATCH") { const body = await readBody(req); return json(res, { ...c, ...body, updated_at: NOW }); }
    return json(res, c);
  }
  const keyList = p.match(/^\/api\/channels\/([^/]+)\/keys$/);
  if (keyList) {
    if (method === "POST") { const body = await readBody(req); const created = { ...keyRows[0], id: "key-new-" + Date.now(), label: body.label || "新密钥" }; keyRows.push(created); return json(res, created); }
    return json(res, { items: keyRows, total: keyRows.length });
  }
  const keyOne = p.match(/^\/api\/channels\/([^/]+)\/keys\/([^/]+)$/);
  if (keyOne) {
    if (method === "DELETE") { const idx = keyRows.findIndex((k) => k.id === keyOne[2]); if (idx >= 0) keyRows.splice(idx, 1); return empty(res); }
    const body = await readBody(req); const target = keyRows.find((k) => k.id === keyOne[2]); return json(res, { ...target, ...body });
  }
  const modelList = p.match(/^\/api\/channels\/([^/]+)\/models$/);
  if (modelList) {
    if (method === "POST") return json(res, { ...models[0], id: "cm-new" });
    return json(res, { items: state.mode === "emptyModels" ? [] : models, total: state.mode === "emptyModels" ? 0 : models.length });
  }
  const probeAll = p.match(/^\/api\/channels\/([^/]+)\/probe-all$/);
  if (probeAll) {
    const mode = (await readBody(req)).mode || "deep";
    return json(res, { channel_id: "ch-1", mode, total: models.length, ok: 3, timeout: 1, error: 1, unknown: 0, items: [], protocol: "openai_chat", auth_style: "bearer", protocol_source: "manual" });
  }
  const rewrite = p.match(/^\/api\/channels\/([^/]+)\/rewrite-preview$/);
  if (rewrite) return json(res, { section: "request_body", before: { model: "gpt-4o", temperature: 0.7 }, after: { model: "gpt-4o", temperature: 0.2 }, diff: [{ path: "temperature", before: 0.7, after: 0.2 }], warnings: [], skipped: "", rule_results: [] });
  const modelOne = p.match(/^\/api\/channel-models\/([^/]+)(\/probe|\/reset|\/probe-default)?$/);
  if (modelOne) {
    const model = models.find((item) => item.id === modelOne[1]) || models[0];
    if (modelOne[2] === "/probe") return json(res, { model_id: model.id, channel_id: "ch-1", upstream_model: model.upstream_model, call_model: model.call_model, status: state.probeStatus, latency_ms: 640, message: state.probeStatus === "ok" ? "200 OK" : "模拟探测失败", probed_at: NOW, mode: "deep", protocol: "openai_chat", auth_style: "bearer", http_status: state.probeStatus === "ok" ? 200 : 500 });
    if (modelOne[2] === "/reset") return json(res, { ...model, health_status: "normal", visible_status: "normal", consecutive_failures: 0, opened_at: null, cooldown_until: null, fault_until: null });
    if (modelOne[2] === "/probe-default") return json(res, { ...model, probe_default: true });
    if (method === "DELETE") return empty(res);
    if (method === "PATCH") return json(res, { ...model, ...(await readBody(req)) });
    return json(res, model);
  }
  return json(res, { code: "NOT_MOCKED", message: p }, 404);
}

function testResult() {
  return { status: state.testStatus, latency_ms: state.testStatus === "timeout" ? 3000 : 128, http_status: state.testStatus === "ok" ? 200 : null, message: state.testStatus === "ok" ? "连接正常，识别到 4 个模型" : "模拟连接失败：上游返回 401", model_count: 4, models_preview: ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4", "text-embedding-3-large"], protocol: "openai_chat", auth_style: "bearer", tested_at: NOW };
}
function discoverResult() {
  return { status: "ok", models: [{ upstream_model: "gpt-4o", already_added: false }, { upstream_model: "gpt-4o-mini", already_added: true }, { upstream_model: "o3", already_added: false }], protocol: "openai_chat", auth_style: "bearer", http_status: 200, latency_ms: 128, message: "识别到 3 个模型" };
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/__audit/")) return await api(req, res, url);
    let file = join(ROOT, decodeURIComponent(url.pathname));
    if (!file.startsWith(ROOT)) { res.writeHead(403); return res.end(); }
    if (!existsSync(file) || statSync(file).isDirectory()) file = join(ROOT, "index.html");
    const body = await readFile(file);
    res.writeHead(200, { "content-type": MIME[extname(file)] || "application/octet-stream", "cache-control": "no-store" });
    res.end(body);
  } catch (error) {
    res.writeHead(500, { "content-type": "text/plain" });
    res.end(String(error));
  }
});
server.listen(PORT, "127.0.0.1", () => {
  console.log("channel audit server on http://127.0.0.1:" + PORT + " root=" + ROOT);
});
