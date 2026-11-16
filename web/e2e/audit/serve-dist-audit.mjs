// Audit-only server: serves dist-audit/ and forwards /api/** to the fixture mock on 3693,
// except /api/logs/summary which gets a complete LogSummary fixture.
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const ROOT = resolve(process.cwd(), "dist-audit");
const UPSTREAM = { host: "127.0.0.1", port: 3692 };
const PORT = Number(process.env.PORT || 3696);
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
const SUMMARY = {
  requests: 320,
  prompt_tokens: 240,
  completion_tokens: 80,
  cached_tokens: 60,
  cost_usd: 0.000012,
  cache_hit_rate: 0.5,
  cache_hit_rate_samples: 168,
  active_minutes: 3,
  span_minutes: 79.55,
  rpm: 106.67,
  tpm: 106.67,
  peak_rpm: 4,
  estimated_rows: 2,
};
const server = createServer(async (req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  try {
    if (url.pathname === "/api/logs/summary") {
      res.writeHead(200, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
      res.end(JSON.stringify(SUMMARY));
      return;
    }
    if (url.pathname === "/api/dashboard/timeseries") {
      const body = await new Promise((resolveBody, rejectBody) => {
        const up = httpRequest({ host: UPSTREAM.host, port: UPSTREAM.port, path: req.url, method: req.method, headers: req.headers }, (upres) => {
          let text = "";
          upres.on("data", (chunk) => (text += chunk));
          upres.on("end", () => resolveBody(text));
        });
        up.on("error", rejectBody);
        req.pipe(up);
      });
      let payload;
      try { payload = JSON.parse(body); } catch { payload = null; }
      if (payload && Array.isArray(payload.points)) {
        const CHANNELS = [["channel-a", 0.5], ["channel-b", 0.3], ["channel-c", 0.2]];
        const MODELS = [["gpt-4o-mini", 0.6], ["claude-sonnet-4-20250514", 0.25], ["gemini-2.0-flash", 0.15]];
        for (const point of payload.points) {
          const bucket = (table) => table.map(([name, share]) => {
            const requests = Math.round((point.requests || 0) * share);
            return { name, requests, prompt_tokens: Math.round((point.prompt_tokens || 0) * share), completion_tokens: Math.round((point.completion_tokens || 0) * share), cached_tokens: Math.round((point.cached_tokens || 0) * share), cache_hit_rate: point.cache_hit_rate, cost_usd: +((point.cost_usd || 0) * share).toFixed(6), errors: Math.round((point.errors || 0) * share), p95_total_ms: point.p95_total_ms };
          });
          if (!Array.isArray(point.by_channel) || point.by_channel.length === 0) point.by_channel = bucket(CHANNELS);
          if (!Array.isArray(point.by_upstream_model) || point.by_upstream_model.length === 0) point.by_upstream_model = bucket(MODELS.map(([name, share]) => [name + (name.includes("gpt") ? "-2024-07-18" : ""), share]));
          if (!Array.isArray(point.by_model) || point.by_model.length === 0) point.by_model = bucket(MODELS);
        }
        if (!Array.isArray(payload.top_upstream_models) || payload.top_upstream_models.length === 0) payload.top_upstream_models = [];
      }
      res.writeHead(200, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
      res.end(JSON.stringify(payload ?? {}));
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      const upstream = httpRequest(
        { host: UPSTREAM.host, port: UPSTREAM.port, path: req.url, method: req.method, headers: req.headers },
        (upres) => {
          res.writeHead(upres.statusCode || 502, upres.headers);
          upres.pipe(res);
        },
      );
      upstream.on("error", (error) => { res.writeHead(502, { "content-type": "text/plain" }); res.end(String(error)); });
      req.pipe(upstream);
      return;
    }
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
server.listen(PORT, "127.0.0.1", () => console.log("audit dist server on http://127.0.0.1:" + PORT + " root=" + ROOT));
