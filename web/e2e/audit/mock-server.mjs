// Audit-only static + mock API server for web dist.
// Serves the last production build (dist/) and answers /api/** from memory so the
// visual audit never touches the real gateway. Scenario control: POST /__audit/state
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const ROOT = resolve(process.cwd(), "dist");
const PORT = Number(process.env.PORT || 3691);
const AUDIT_DIR = resolve(process.cwd(), "e2e/audit");
const NOW = Date.parse("2026-11-14T20:00:00+08:00");

const state = { mode: "populated", delayMs: 0, delayPaths: [] };
let settings = {
  body_policy: "errors",
  body_truncate_bytes: 16384,
  body_retention_days: 7,
  summary_max_bytes: 65536,
  updated_at: NOW - 26 * 3600_000,
  updated_by: "admin",
  log_retention_days: 30,
  redaction_forced: true,
  gateway_base_url: "http://192.168.1.10:3681",
  gateway_updated_at: NOW - 26 * 3600_000,
  gateway_updated_by: "admin",
};
let keySeq = 4;
let keys = [
  { id: "key-1", label: "laptop", prefix: "sk-live-ab12", enabled: true, last_used_at: NOW - 32 * 60_000, created_at: NOW - 40 * 24 * 3600_000 },
  { id: "key-2", label: "ci-runner", prefix: "sk-live-cd34", enabled: true, last_used_at: NOW - 5 * 3600_000, created_at: NOW - 12 * 24 * 3600_000 },
  { id: "key-3", label: "旧版脚本（已停用）", prefix: "sk-live-ef56", enabled: false, last_used_at: null, created_at: NOW - 200 * 24 * 3600_000 },
];

// 目录契约：ADR-067 的二元 kind + origin；直连带定时封禁 banned_until（epoch ms）。
const catalog = [
  { id: "cm-1", name: "channel-a/model-a", channel_id: "ch-1", channel_slug: "channel-a", channel_name: "channel-a", protocol: "openai_chat", upstream_model: "gpt-4o-mini", call_model: "model-a", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 0.15, output_price_per_1m: 0.6, cache_read_price_per_1m: 0.075, cache_write_price_per_1m: 0.3, enabled: true, banned_until: null, kind: "direct", origin: null, status: "enabled", health_status: "normal", visible_status: "normal", cooldown_until: null, fault_until: null, consecutive_failures: 0, status_message: "", created_at: NOW - 90 * 24 * 3600_000 },
  { id: "cm-2", name: "channel-a/model-b", channel_id: "ch-1", channel_slug: "channel-a", channel_name: "channel-a", protocol: "anthropic_messages", upstream_model: "claude-3-5-sonnet", call_model: "model-b", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 3, output_price_per_1m: 15, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: true, banned_until: null, kind: "direct", origin: null, status: "open", health_status: "open", visible_status: "open", cooldown_until: Date.now() + 5 * 60_000, fault_until: null, consecutive_failures: 3, status_message: "熔断中，5 分钟后重试", created_at: NOW - 60 * 24 * 3600_000 },
  { id: "cm-3", name: "channel-b/model-c", channel_id: "ch-2", channel_slug: "channel-b", channel_name: "channel-b", protocol: "gemini", upstream_model: "gemini-2.0-flash", call_model: "model-c", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 0.1, output_price_per_1m: 0.4, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: false, banned_until: Date.now() + 2 * 3600_000, kind: "direct", origin: null, status: "disabled", health_status: "normal", visible_status: "disabled", cooldown_until: null, fault_until: null, consecutive_failures: 0, status_message: "定时封禁至 2 小时后", created_at: NOW - 30 * 24 * 3600_000 },
  { id: "cm-4", name: "channel-b/model-d", channel_id: "ch-2", channel_slug: "channel-b", channel_name: "channel-b", protocol: "openai_chat", upstream_model: "gpt-4o", call_model: "model-d", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 5, output_price_per_1m: 25, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: false, banned_until: null, kind: "direct", origin: null, status: "disabled", health_status: "normal", visible_status: "disabled", cooldown_until: null, fault_until: null, consecutive_failures: 0, status_message: "已手动封禁", created_at: NOW - 20 * 24 * 3600_000 },
  { id: "combo-1", name: "fast-chat", channel_id: "", channel_slug: "", channel_name: "手动组合", protocol: "openai_chat", upstream_model: "", call_model: "fast-chat", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 0, output_price_per_1m: 0, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: true, banned_until: null, kind: "combo", origin: "manual", status: "enabled", health_status: "normal", visible_status: "normal", cooldown_until: null, fault_until: null, consecutive_failures: 0, status_message: "", created_at: NOW - 10 * 24 * 3600_000 },
  { id: "combo-2", name: "deep-chat", channel_id: "", channel_slug: "", channel_name: "手动组合", protocol: "anthropic_messages", upstream_model: "", call_model: "deep-chat", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 0, output_price_per_1m: 0, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: false, banned_until: null, kind: "combo", origin: "manual", status: "disabled", health_status: "normal", visible_status: "disabled", cooldown_until: null, fault_until: null, consecutive_failures: 0, status_message: "", created_at: NOW - 8 * 24 * 3600_000 },
  { id: "group:auto-pool", name: "auto-pool", channel_id: "", channel_slug: "", channel_name: "自动组合", protocol: "openai_chat", upstream_model: "", call_model: "auto-pool", model_type: "chat", model_type_source: "default", parameters: {}, input_price_per_1m: 0, output_price_per_1m: 0, cache_read_price_per_1m: null, cache_write_price_per_1m: null, enabled: true, banned_until: null, kind: "combo", origin: "auto", status: "enabled", health_status: "normal", visible_status: "normal", cooldown_until: null, fault_until: null, consecutive_failures: 0, status_message: "", created_at: NOW - 5 * 24 * 3600_000 },
];

const catalogItems = () => catalog;

const comboMember = (id, priority, slug, model) => ({
  id: "member-" + id,
  target_type: "channel_model",
  priority,
  enabled: true,
  available: true,
  channel_model_id: "cm-" + id.split("-")[0],
  child_combo_id: null,
  channel_id: "ch-1",
  channel_slug: slug,
  channel_name: slug,
  call_model: model,
  upstream_model: model,
  child_combo_name: "",
  child_combo_enabled: false,
  protocol: "openai_chat",
  created_at: NOW - 9 * 24 * 3600_000,
});

const combos = [
  {
    id: "combo-1",
    name: "fast-chat",
    description: "低延迟分流：P2 两个同层成员等概率，P1 兜底。",
    enabled: true,
    max_attempts: 3,
    failover_rules: { rules: [], default_action: "next" },
    member_count: 3,
    available_count: 3,
    depth: 1,
    max_depth: 3,
    callable: true,
    created_at: NOW - 10 * 24 * 3600_000,
    updated_at: NOW - 2 * 3600_000,
    members: [
      comboMember("1-1", 2, "channel-a", "model-a"),
      comboMember("1-2", 2, "channel-a", "model-b"),
      comboMember("1-3", 1, "channel-b", "model-c"),
    ],
  },
  {
    id: "combo-2",
    name: "deep-chat",
    description: "单成员组合，用于验证只读态下的降级提示。",
    enabled: false,
    max_attempts: 1,
    failover_rules: { rules: [], default_action: "stop" },
    member_count: 1,
    available_count: 1,
    depth: 1,
    max_depth: 3,
    callable: false,
    created_at: NOW - 8 * 24 * 3600_000,
    updated_at: NOW - 6 * 24 * 3600_000,
    members: [comboMember("2-1", 1, "channel-b", "model-d")],
  },
];

const groups = [
  {
    name: "auto-pool",
    call_model: "auto-pool",
    member_count: 3,
    available_count: 2,
    callable: true,
    created_at: NOW - 5 * 24 * 3600_000,
    members: [
      { channel_model_id: "cm-1", channel_id: "ch-1", channel_slug: "channel-a", channel_name: "channel-a", protocol: "openai_chat", upstream_model: "gpt-4o-mini", call_model: "model-a", priority: 3, enabled: true, channel_enabled: true, available: true },
      { channel_model_id: "cm-2", channel_id: "ch-1", channel_slug: "channel-a", channel_name: "channel-a", protocol: "anthropic_messages", upstream_model: "claude-3-5-sonnet", call_model: "model-b", priority: 2, enabled: true, channel_enabled: true, available: true },
      { channel_model_id: "cm-4", channel_id: "ch-2", channel_slug: "channel-b", channel_name: "channel-b", protocol: "openai_chat", upstream_model: "gpt-4o", call_model: "model-d", priority: 2, enabled: false, channel_enabled: true, available: false },
    ],
  },
];

// 日志夹具：验证 priority_path 的展示顺序（数值越大越先调用，应显示降序）。
const logAttempt = (id, priority_path, status) => ({
  channel_model_id: "cm-" + id,
  channel_slug: "channel-a",
  channel_name: "channel-a",
  upstream_model: "gpt-4o-mini",
  status,
  error_code: status === "success" ? null : "CIRCUIT_OPEN",
  message: status === "success" ? "" : "渠道模型处于熔断冷却中",
  ms: 320,
  round: 1,
  skipped: false,
  priority_path,
});

const logs = [
  {
    id: "log-1", request_id: "req-fast-1", upstream_request_id: "up-1",
    user_id: "user-1", user_name: "admin", api_key_id: "key-1", api_key_label: "laptop",
    requested_model: "fast-chat", channel_name: "channel-a", upstream_model: "gpt-4o-mini",
    response_model: "gpt-4o-mini", source: "chat", is_stream: false, http_status: 200,
    error_code: null, error_message: "",
    prompt_tokens: 1240, completion_tokens: 380, cached_tokens: 0, cache_ratio: 0, cost_usd: 0.002,
    ttft_ms: 280, ttfa_ms: 290, ttlt_ms: 1480, total_ms: 1480, throughput_tps: 250.5,
    usage_estimated: false,
    attempts: [logAttempt("1", [1, 2], "success"), logAttempt("2", [2], "error")],
    attempt_error_count: 1,
    rewrite_applied: false, rewrite_skipped: "", rewrite_diff: {}, params: {}, response_headers: {},
    error_body: null, created_at: NOW - 20 * 60_000,
  },
  {
    id: "log-2", request_id: "req-deep-1", upstream_request_id: "up-2",
    user_id: "user-1", user_name: "admin", api_key_id: "key-1", api_key_label: "laptop",
    requested_model: "deep-chat", channel_name: "channel-a", upstream_model: "gpt-4o-mini",
    response_model: "gpt-4o-mini", source: "chat", is_stream: true, http_status: 200,
    error_code: null, error_message: "",
    prompt_tokens: 820, completion_tokens: 160, cached_tokens: 0, cache_ratio: 0, cost_usd: 0.001,
    ttft_ms: 410, ttfa_ms: 420, ttlt_ms: 2100, total_ms: 2100, throughput_tps: 90.2,
    usage_estimated: true,
    attempts: [logAttempt("3", [1, 2, 3], "success")],
    attempt_error_count: 0,
    rewrite_applied: false, rewrite_skipped: "", rewrite_diff: {}, params: {}, response_headers: {},
    error_body: null, created_at: NOW - 55 * 60_000,
  },
];

const logSummary = {
  requests: 2, prompt_tokens: 2060, completion_tokens: 540, cached_tokens: 0, cost_usd: 0.003,
  cache_hit_rate: 0, cache_hit_rate_samples: 0, active_minutes: 35, span_minutes: 35,
  rpm: 0.06, tpm: 74, peak_rpm: 1, estimated_rows: 1,
};

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

const readBody = (req) =>
  new Promise((done) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try { done(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}")); } catch { done({}); }
    });
  });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(req, res, url) {
  const p = url.pathname;
  const method = req.method || "GET";
  const slow =
    state.delayMs > 0 &&
    (state.delayPaths.length === 0 || state.delayPaths.some((prefix) => p.startsWith(prefix)));
  // Keep /api/auth/me fast so the shell renders and the page-level loading state is observable.
  if (slow && p !== "/api/auth/me") await sleep(state.delayMs);

  if (p === "/__audit/state") {
    if (method === "POST") { const b = await readBody(req); Object.assign(state, b); }
    return json(res, state);
  }
  if (p === "/api/auth/me") return json(res, { id: "admin-1", username: "admin", created_at: NOW, updated_at: NOW });
  if (state.mode === "error" && !p.startsWith("/__audit")) {
    return json(res, { code: "UPSTREAM_ERROR", message: "模拟故障：数据库连接超时" }, 500);
  }
  if (p === "/api/settings") {
    if (method === "PATCH") {
      const b = await readBody(req);
      settings = { ...settings, ...b, updated_at: Date.now(), updated_by: "admin" };
      return json(res, settings);
    }
    return json(res, settings);
  }
  if (p === "/api/keys") {
    if (method === "POST") {
      const b = await readBody(req);
      keySeq += 1;
      const created = { id: "key-" + keySeq, label: b.label || "untitled", prefix: "sk-live-xy" + keySeq, enabled: true, last_used_at: null, created_at: Date.now() };
      keys = [created, ...keys];
      return json(res, { ...created, key: "sk-live-xy" + keySeq + "-plaintext-once-0123456789" });
    }
    return json(res, { items: state.mode === "empty" ? [] : keys, total: state.mode === "empty" ? 0 : keys.length });
  }
  const km = p.match(/^\/api\/keys\/([^/]+)(?:\/(secret|reset))?$/);
  if (km) {
    const id = km[1];
    const target = keys.find((k) => k.id === id) || keys[0];
    if (method === "DELETE") { keys = keys.filter((k) => k.id !== id); res.writeHead(204); return res.end(); }
    if (km[2] === "secret") return json(res, { key: "sk-live-secret-" + id + "-0123456789abcdef" });
    if (km[2] === "reset") return json(res, { ...target, key: "sk-live-reset-" + id + "-0123456789" });
    if (method === "PATCH") { const b = await readBody(req); Object.assign(target, b); return json(res, target); }
    return json(res, target);
  }
  if (p === "/api/channels") return json(res, { items: state.mode === "empty" ? [] : [
    { id: "ch-1", slug: "channel-a", name: "channel-a", enabled: true, connection_status: "ok", model_count: 3, priority: 10 },
  ], total: state.mode === "empty" ? 0 : 4 });
  if (p === "/api/catalog") return json(res, { items: state.mode === "empty" ? [] : catalogItems(), total: state.mode === "empty" ? 0 : catalogItems().length });
  if (p === "/api/combos") {
    if (method === "POST") { const b = await readBody(req); keySeq += 1; return json(res, { ...combos[0], id: "combo-new-" + keySeq, name: b.name || "new-combo" }); }
    return json(res, { items: state.mode === "empty" ? [] : combos, total: state.mode === "empty" ? 0 : combos.length });
  }
  const cm = p.match(new RegExp("^/api/channel-models/([^/]+)$"));
  if (cm) {
    const entry = catalogItems().find((item) => item.id === cm[1]);
    if (method === "PATCH") {
      const b = await readBody(req);
      const bannedUntil = typeof b.banned_until === "string" ? Date.parse(b.banned_until) : null;
      if (b.banned_until !== null && b.banned_until !== undefined) {
        if (entry) { entry.enabled = false; entry.banned_until = bannedUntil; }
      } else if (typeof b.banned_until === "string" && bannedUntil !== null && bannedUntil > Date.now()) {
        if (entry) { entry.enabled = false; entry.banned_until = bannedUntil; }
      }
      if (b.enabled === true) { if (entry) { entry.enabled = true; entry.banned_until = null; } }
      else if (b.enabled === false && b.banned_until == null) { if (entry) { entry.enabled = false; entry.banned_until = null; } }
      return json(res, { id: cm[1], enabled: entry?.enabled ?? true, banned_until: entry?.banned_until ?? null });
    }
    if (entry) return json(res, entry);
  }
  if (p === "/api/groups") return json(res, { items: state.mode === "empty" ? [] : groups, total: state.mode === "empty" ? 0 : groups.length });
  if (p === "/api/logs/summary") return json(res, logSummary);
  if (p === "/api/logs/filters") {
    return json(res, {
      channels: ["channel-a", "channel-b"],
      models: ["fast-chat", "deep-chat"],
      key_labels: ["laptop"],
      users: [{ id: "user-1", name: "admin" }],
      truncated: { channels: false, models: false, key_labels: false, users: false },
    });
  }
  if (p === "/api/logs") {
    return json(res, { items: state.mode === "empty" ? [] : logs, total: state.mode === "empty" ? 0 : logs.length, page: 1, page_size: 25 });
  }
  const lm = p.match(new RegExp("^/api/logs/([^/]+)$"));
  if (lm) {
    const found = logs.find((item) => item.request_id === decodeURIComponent(lm[1])) || logs[0];
    return json(res, found);
  }

  if (p === "/api/dashboard/flows") {
    const metric = (url.searchParams.get("metric") === "tokens") ? "tokens" : "requests";
    const scale = metric === "tokens" ? 50000 : 1;
    const nodes = [
      { id: "user:admin", stage: "user", label: "admin", value: 4821 * scale, share: 1 },
      { id: "key:default", stage: "key", label: "default", value: 4821 * scale, share: 1 },
      { id: "model:basis", stage: "model", label: "basis", value: 3572 * scale, share: 0.741 },
      { id: "model:deepseek-flash", stage: "model", label: "deepseek-flash", value: 1249 * scale, share: 0.259 },
      { id: "upstream:deepseek-flash", stage: "upstream", label: "deepseek-flash", value: 3349 * scale, share: 0.695 },
      { id: "upstream:MiniMax-M3.1-Flash", stage: "upstream", label: "MiniMax-M3.1-Flash", value: 950 * scale, share: 0.197 },
      { id: "upstream:deepseek-v4-flash", stage: "upstream", label: "deepseek-v4-flash", value: 322 * scale, share: 0.067 },
      { id: "upstream:sensenova-6.8-flash", stage: "upstream", label: "sensenova-6.8-flash", value: 200 * scale, share: 0.041 },
      { id: "channel:VendorQ", stage: "channel", label: "VendorQ", value: 3871 * scale, share: 0.803 },
      { id: "channel:minimaxi", stage: "channel", label: "minimaxi", value: 950 * scale, share: 0.197 }
    ];
    const links = [
      { source: "user:admin", target: "key:default", value: 4821 * scale },
      { source: "key:default", target: "model:basis", value: 3572 * scale },
      { source: "key:default", target: "model:deepseek-flash", value: 1249 * scale },
      { source: "model:basis", target: "upstream:deepseek-flash", value: 2100 * scale },
      { source: "model:basis", target: "upstream:MiniMax-M3.1-Flash", value: 950 * scale },
      { source: "model:basis", target: "upstream:deepseek-v4-flash", value: 322 * scale },
      { source: "model:basis", target: "upstream:sensenova-6.8-flash", value: 200 * scale },
      { source: "model:deepseek-flash", target: "upstream:deepseek-flash", value: 1249 * scale },
      { source: "upstream:deepseek-flash", target: "channel:VendorQ", value: 3349 * scale },
      { source: "upstream:deepseek-flash", target: "channel:minimaxi", value: 1 * scale },
      { source: "upstream:MiniMax-M3.1-Flash", target: "channel:minimaxi", value: 949 * scale },
      { source: "upstream:deepseek-v4-flash", target: "channel:VendorQ", value: 322 * scale },
      { source: "upstream:sensenova-6.8-flash", target: "channel:VendorQ", value: 200 * scale }
    ];
    return json(res, { metric, stages: ["user", "key", "model", "upstream", "channel"], nodes, links, total: { requests: 4821, prompt_tokens: 240000000, completion_tokens: 18000000 } });
  }
  if (p === "/api/dashboard/summary") {
    return json(res, buildDashboardSummary(url.searchParams.get("range") || "24h"));
  }
  if (p === "/api/dashboard/timeseries") {
    return json(res, buildDashboardTimeseries(url.searchParams.get("range") || "24h"));
  }
  return json(res, { code: "NOT_MOCKED", message: p }, 404);
}

// ---- dashboard fixtures（确定性，时间相对请求时刻，供趋势/分流/热力图视觉复核）----
const DASH_MODELS = ["basis", "deepseek-flash", "minimax-m3"];
const DASH_UPSTREAMS = ["deepseek-flash", "MiniMax-M3.1-Flash", "deepseek-v4-flash", "sensenova-6.8-flash"];
const DASH_CHANNELS = ["VendorQ", "minimaxi", "sensenova"];

function dashSplit(requests, weights, bucketIndex, seed) {
  const raw = weights.map((weight, index) =>
    Math.max(0, Math.round(requests * weight * (0.78 + Math.abs(Math.sin((bucketIndex + index * 2 + seed) / 3)) * 0.44))),
  );
  const total = raw.reduce((sum, value) => sum + value, 0);
  const scaled = raw.map((value) => Math.max(0, Math.round((value * requests) / Math.max(1, total))));
  scaled[0] += requests - scaled.reduce((sum, value) => sum + value, 0);
  return scaled.map((value) => Math.max(0, value));
}

function dashMember(name, requests, bucketIndex, seed) {
  const prompt = Math.round(requests * 860);
  const completion = Math.round(requests * 150);
  const cached = Math.round(prompt * 0.34);
  return {
    name,
    requests,
    prompt_tokens: prompt,
    completion_tokens: completion,
    cached_tokens: cached,
    cache_hit_rate: prompt > 0 ? cached / prompt : null,
    cost_usd: Number((requests * 0.0018).toFixed(4)),
    errors: requests > 0 && (bucketIndex + seed) % 7 === 0 ? Math.max(1, Math.round(requests * 0.03)) : 0,
    p95_total_ms: requests > 0 ? 620 + ((bucketIndex * 97 + seed * 211) % 1180) : null,
  };
}

function dashPointMembers(names, weights, requests, bucketIndex, seed) {
  const split = dashSplit(requests, weights, bucketIndex, seed);
  return names.map((name, index) => dashMember(name, split[index], bucketIndex, seed + index * 5));
}

function dashSum(points, getMembers) {
  const totals = new Map();
  for (const point of points) {
    for (const member of getMembers(point)) {
      const found = totals.get(member.name) || {
        name: member.name,
        requests: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        cached_tokens: 0,
        cost_usd: 0,
      };
      found.requests += member.requests;
      found.prompt_tokens += member.prompt_tokens;
      found.completion_tokens += member.completion_tokens;
      found.cached_tokens += member.cached_tokens;
      found.cost_usd += member.cost_usd;
      totals.set(member.name, found);
    }
  }
  return [...totals.values()].sort((left, right) => right.requests - left.requests);
}

function buildDashboardTimeseries(rangeKey) {
  const daily = rangeKey === "7d" || rangeKey === "30d";
  const buckets = daily ? 7 : 24;
  const stepMs = daily ? 24 * 3600_000 : 3600_000;
  const endMs = Date.now();
  const startMs = endMs - buckets * stepMs;
  const points = [];
  for (let bucketIndex = 0; bucketIndex < buckets; bucketIndex += 1) {
    // 工作日白天高、夜里低的双峰曲线，保证堆叠图有起伏。
    const phase = daily
      ? 0.55 + 0.45 * Math.abs(Math.sin(bucketIndex / 2))
      : 0.28 + 0.62 * Math.abs(Math.sin((bucketIndex - 6) / 3.4)) + 0.1 * Math.abs(Math.sin(bucketIndex / 1.7));
    const requests = Math.max(6, Math.round(70 * phase));
    const byModel = dashPointMembers(DASH_MODELS, [0.6, 0.26, 0.14], requests, bucketIndex, 1);
    const byUpstream = dashPointMembers(DASH_UPSTREAMS, [0.5, 0.22, 0.17, 0.11], requests, bucketIndex, 3);
    const byChannel = dashPointMembers(DASH_CHANNELS, [0.68, 0.2, 0.12], requests, bucketIndex, 5);
    points.push({
      start_ms: startMs + bucketIndex * stepMs,
      requests,
      prompt_tokens: byModel.reduce((sum, member) => sum + member.prompt_tokens, 0),
      completion_tokens: byModel.reduce((sum, member) => sum + member.completion_tokens, 0),
      cached_tokens: byModel.reduce((sum, member) => sum + member.cached_tokens, 0),
      cache_hit_rate: 0.34,
      cost_usd: Number(byModel.reduce((sum, member) => sum + member.cost_usd, 0).toFixed(4)),
      errors: byModel.reduce((sum, member) => sum + member.errors, 0),
      p95_ttft_ms: requests > 0 ? 230 + ((bucketIndex * 41) % 260) : null,
      p95_total_ms: requests > 0 ? 1480 + ((bucketIndex * 173) % 1400) : null,
      p95_ttfa_ms: requests > 0 ? 900 + ((bucketIndex * 89) % 700) : null,
      p95_ttlt_ms: requests > 0 ? 2100 + ((bucketIndex * 211) % 1900) : null,
      by_model: byModel,
      by_channel: byChannel,
      by_upstream_model: byUpstream,
    });
  }
  const heatmap = [];
  for (let weekday = 1; weekday <= 7; weekday += 1) {
    for (let hour = 0; hour < 24; hour += 1) {
      const workday = weekday <= 5 ? 1 : 0.55;
      const curve = 0.18 + 0.82 * Math.abs(Math.sin((hour - 5) / 3.6));
      heatmap.push({ weekday, hour, requests: Math.round(90 * workday * curve + ((weekday * 7 + hour * 3) % 9)) });
    }
  }
  return {
    range: rangeKey,
    timezone: "Asia/Shanghai",
    granularity: daily ? "day" : "hour",
    start_ms: startMs,
    end_ms: endMs,
    points,
    top_models: dashSum(points, (point) => point.by_model),
    top_channels: dashSum(points, (point) => point.by_channel),
    top_upstream_models: dashSum(points, (point) => point.by_upstream_model),
    error_distribution: [
      { code: "upstream_timeout", count: 14 },
      { code: "rate_limited", count: 6 },
      { code: "invalid_request", count: 3 },
    ],
    heatmap,
  };
}

function buildDashboardSummary(rangeKey) {
  const ts = buildDashboardTimeseries(rangeKey);
  const requests = ts.points.reduce((sum, point) => sum + point.requests, 0);
  const promptTokens = ts.points.reduce((sum, point) => sum + point.prompt_tokens, 0);
  const completionTokens = ts.points.reduce((sum, point) => sum + point.completion_tokens, 0);
  const cachedTokens = ts.points.reduce((sum, point) => sum + point.cached_tokens, 0);
  const cost = ts.points.reduce((sum, point) => sum + point.cost_usd, 0);
  const errors = ts.points.reduce((sum, point) => sum + point.errors, 0);
  const spanMinutes = Math.round((ts.end_ms - ts.start_ms) / 60_000);
  const percentiles = { p50: 480, p90: 1320, p95: 1860, p99: 3400 };
  return {
    requests,
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    cached_tokens: cachedTokens,
    cache_hit_rate: cachedTokens / Math.max(1, promptTokens),
    cache_hit_rate_samples: 240,
    cache_request_hit_rate: 0.41,
    cache_request_hits: Math.round(requests * 0.41),
    cacheable_ratio: 0.58,
    cacheable_tokens: Math.round(promptTokens * 0.58),
    cacheable_sessions: 96,
    cache_saved_tokens: cachedTokens,
    cache_saved_usd: Number((cachedTokens * 0.0000006).toFixed(2)),
    cost_usd: Number(cost.toFixed(2)),
    errors,
    error_rate: errors / Math.max(1, requests),
    p95_ttft_ms: 260,
    p95_total_ms: 1860,
    latency: { samples: 240, ttft: percentiles, ttfa: { p50: 900, p90: 1500, p95: 1800, p99: 2400 }, ttlt: { p50: 2100, p90: 2900, p95: 3400, p99: 4600 }, total: percentiles },
    start_ms: ts.start_ms,
    end_ms: ts.end_ms,
    active_minutes: Math.min(spanMinutes, 480),
    span_minutes: spanMinutes,
    rpm: requests / Math.max(1, Math.min(spanMinutes, 480)),
    tpm: (promptTokens + completionTokens) / Math.max(1, Math.min(spanMinutes, 480)),
    peak_rpm: 190,
    estimated_rows: 4,
  };
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
  console.log("audit server on http://127.0.0.1:" + PORT + " root=" + ROOT);
});
