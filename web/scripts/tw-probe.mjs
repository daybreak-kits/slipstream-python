#!/usr/bin/env node
/**
 * tw-probe -- answers "does this class actually generate CSS?" with Tailwind's pure-JS
 * compile() API (no vite, no esbuild, no network).
 *
 * Questions it answers (ADR-066 / batch 4):
 *   Q1 custom font tiers: text-2xs / text-2sm / text-metric / text-h1
 *   Q2 sidebar width token namespace: --spacing-sidebar vs --w-sidebar vs :root-only
 *   Q3 px/4 dynamic spacing + the fractional size-4.5 step
 *   Q4 real-page classes with no generated CSS (a "missing classes" list)
 *
 * Usage:
 *   node scripts/tw-probe.mjs            # human summary + .scratch/tw-probe.json
 *   node scripts/tw-probe.mjs --json     # JSON on stdout
 */
import { compile } from "tailwindcss";
import { existsSync, readFileSync, mkdirSync, writeFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, "..");
const NM = join(WEB, "node_modules");
const INDEX_CSS = join(WEB, "src", "index.css");
const OUT = resolve(WEB, "..", ".scratch", "tw-probe.json");
const argv = process.argv.slice(2);
const wantJson = argv.includes("--json");

/* ------------------------------------------------------------------ */
/* stylesheet loader (the only thing compile() cannot do by itself)    */
/* ------------------------------------------------------------------ */

function isFile(p) {
  try { return statSync(p).isFile(); } catch { return false; }
}

function sheetCandidates(id, base) {
  const out = [];
  const push = (p) => { if (p && out.indexOf(p) < 0) out.push(p); };
  if (id.startsWith(".") || id.startsWith("/")) {
    push(resolve(base, id));
    push(resolve(base, id + ".css"));
  } else {
    push(join(base, id));
    push(join(base, id + ".css"));
    push(join(NM, id));
    push(join(NM, id + ".css"));
    push(join(NM, id, "index.css"));
  }
  return out;
}

async function loadStylesheet(id, base) {
  for (const p of sheetCandidates(id, base)) {
    if (isFile(p)) return { path: p, base: dirname(p), content: readFileSync(p, "utf8") };
  }
  throw new Error("tw-probe: cannot resolve stylesheet " + JSON.stringify(id) + " from " + base);
}

const COMPILE_OPTS = { base: WEB, loadStylesheet };

/* ------------------------------------------------------------------ */
/* CSS helpers                                                         */
/* ------------------------------------------------------------------ */

/** regex that matches a generated selector for a class (allows CSS escapes before punctuation) */
function selectorRegex(cls) {
  let src = "";
  for (const ch of cls) {
    if (/[A-Za-z0-9_-]/.test(ch)) src += ch;
    else src += "\\\\?" + ch.replace(/[.*+?^{}()|[\]\\]/g, "\\$&");
  }
  return new RegExp("\\." + src + "(?![A-Za-z0-9_-])");
}

function hasSelector(css, cls) {
  return selectorRegex(cls).test(css);
}

/** pull the rule block that defines cls (nested @media / @supports safe enough) */
function ruleFor(css, cls) {
  const re = selectorRegex(cls);
  const m = re.exec(css);
  if (!m) return null;
  const at = m.index;
  const open = css.indexOf("{", at);
  if (open < 0) return null;
  let depth = 0;
  let end = -1;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") { depth -= 1; if (depth === 0) { end = i; break; } }
  }
  if (end < 0) return null;
  let start = 0;
  for (const c of ["{", "}", ";"]) {
    const p = css.lastIndexOf(c, at);
    if (p > start) start = p;
  }
  return css.slice(start + 1, end + 1).replace(/\s+/g, " ").trim();
}

function declarations(snippet) {
  const out = [];
  if (!snippet) return out;
  const re = /(font-size|line-height|width|height|min-width|min-height|max-width|max-height|gap|padding|margin|border-radius)\s*:\s*([^;}]+)/g;
  let m = re.exec(snippet);
  while (m) { out.push(m[1] + ": " + m[2].trim()); m = re.exec(snippet); }
  return out;
}

function insertIntoFirstTheme(css, decl) {
  const at = css.indexOf("@theme {");
  if (at < 0) throw new Error("tw-probe: index.css has no plain @theme block");
  const close = css.indexOf("\n}", at);
  if (close < 0) throw new Error("tw-probe: cannot find the end of the @theme block");
  return css.slice(0, close) + "\n  " + decl + css.slice(close);
}

function normalize(css) {
  return css.replace(/\s+/g, " ");
}

/* ------------------------------------------------------------------ */
/* Q1/Q3: real index.css                                               */
/* ------------------------------------------------------------------ */

const FONT_CANDIDATES = ["text-2xs", "text-2sm", "text-metric", "text-h1", "text-xs", "text-sm", "text-base"];

const SPACING_CANDIDATES = [
  "h-60", "w-24", "min-w-180", "max-h-105", "min-h-45",
  "h-px", "w-px", "w-0.5", "size-4", "size-4.5", "min-h-17.5", "max-w-32", "min-w-48", "w-18",
];

function spacingPx(expr) {
  const m = /calc\(var\(--spacing\)\s*\*\s*([0-9.]+)\)/.exec(expr || "");
  if (!m) return null;
  return round2(Number(m[1]) * 4);
}
function round2(n) { return Math.round(n * 100) / 100; }

function themeVars(source) {
  const map = new Map();
  const re = /(--[a-z0-9-]+)\s*:\s*([^;{}]+);/g;
  let m = re.exec(source);
  while (m) { map.set(m[1], m[2].trim()); m = re.exec(source); }
  return map;
}

async function probeReal() {
  const source = readFileSync(INDEX_CSS, "utf8");
  const vars = themeVars(source);
  const compiler = await compile(source, COMPILE_OPTS);
  const all = FONT_CANDIDATES.concat(SPACING_CANDIDATES);
  const css = compiler.build(all);
  const font = [];
  for (const cls of FONT_CANDIDATES) {
    const snippet = ruleFor(css, cls);
    const token = "--" + cls.replace(/^text-/, "text-");
    const resolved = {
      fontSize: vars.get(token) || null,
      lineHeight: vars.get(token + "--line-height") || null,
    };
    font.push({ class: cls, generated: Boolean(snippet), snippet: snippet, declarations: declarations(snippet), resolved: resolved });
  }
  const spacing = [];
  for (const cls of SPACING_CANDIDATES) {
    const snippet = ruleFor(css, cls);
    const decls = declarations(snippet);
    const px = [];
    for (const d of decls) {
      const p = spacingPx(d);
      if (p !== null) px.push(d.split(":")[0] + "=" + p + "px");
    }
    spacing.push({ class: cls, generated: Boolean(snippet), snippet: snippet, declarations: decls, computedPx: px });
  }
  return { css, font, spacing };
}

/* ------------------------------------------------------------------ */
/* Q2: sidebar token namespace                                         */
/* ------------------------------------------------------------------ */

const SIDEBAR_VARIANTS = [
  { id: "A", where: "@theme", decl: "--spacing-sidebar: 248px;", expect: "class-namespace --spacing-* (px/4 spacing)" },
  { id: "B", where: "@theme", decl: "--w-sidebar: 248px;", expect: "class-namespace --w-* (does not exist upstream)" },
  { id: "C", where: ":root", decl: "--w-sidebar: 248px;", expect: "plain :root variable (no utility generation expected)" },
  { id: "D", where: "@theme", decl: "--width-sidebar: 248px;", expect: "class-namespace --width-* (upstream width namespace)" },
];

async function probeSidebar(realSource) {
  const results = [];
  for (const variant of SIDEBAR_VARIANTS) {
    let source;
    if (variant.where === "@theme") source = insertIntoFirstTheme(realSource, variant.decl);
    else source = realSource + "\n" + variant.where + " { " + variant.decl + " }\n";
    const compiler = await compile(source, COMPILE_OPTS);
    const css = compiler.build(["w-sidebar"]);
    const snippet = ruleFor(css, "w-sidebar");
    results.push({
      variant: variant.id,
      declaration: variant.decl,
      placement: variant.where,
      hypothesis: variant.expect,
      generated: Boolean(snippet),
      snippet: snippet,
    });
  }
  return results;
}

/* ------------------------------------------------------------------ */
/* Q4: real page classes                                               */
/* ------------------------------------------------------------------ */

const PAGE_FILES = [
  "src/pages/logs.tsx",
  "src/pages/channel-detail.tsx",
  "src/components/channels/rewrite-rules-editor.tsx",
];

const STATIC_CLASSES = new Set(["flex", "grid", "hidden", "block", "inline", "inline-flex", "inline-block", "contents",
  "truncate", "italic", "underline", "antialiased", "sr-only", "grow", "shrink", "relative", "absolute", "fixed",
  "sticky", "static", "visible", "invisible", "isolate", "container", "uppercase", "lowercase", "capitalize", "table",
  "group", "peer", "transform", "resize"]);

const PREFIXES = ["bg-", "text-", "border", "rounded", "shadow", "ring", "font-", "leading-", "tracking-", "gap-",
  "space-", "w-", "h-", "min-", "max-", "size-", "flex-", "grid-", "col-", "row-", "items-", "justify-", "content-",
  "self-", "place-", "overflow-", "whitespace-", "break-", "line-", "decoration-", "opacity-", "transition",
  "duration-", "delay-", "ease-", "animate-", "cursor-", "select-", "pointer-events-", "scroll-", "snap-", "touch-",
  "z-", "top-", "left-", "right-", "bottom-", "inset-", "aspect-", "object-", "align-", "table-", "caption-",
  "divide-", "outline", "fill-", "stroke-", "from-", "via-", "to-", "blur", "brightness-", "contrast-",
  "drop-shadow", "grayscale", "hue-rotate-", "invert", "saturate-", "sepia", "backdrop-", "scale-", "rotate-",
  "translate-", "skew-", "origin-", "will-change-"];

const P_ = ["p-", "px-", "py-", "pt-", "pr-", "pb-", "pl-", "ps-", "pe-"];
const M_ = ["m-", "mx-", "my-", "mt-", "mr-", "mb-", "ml-", "ms-", "me-"];
const ALL_PREFIXES = PREFIXES.concat(P_, M_);

function stripVariants(token) {
  let t = token;
  let guard = 0;
  while (guard < 6) {
    guard += 1;
    if (t.startsWith("!")) { t = t.slice(1); continue; }
    if (t.startsWith("-")) { t = t.slice(1); continue; }
    const m = /^([a-z0-9][a-z0-9-]*(?:\[[^\]]*\])?)\:(.+)$/.exec(t);
    if (!m) break;
    t = m[2];
  }
  return t;
}

function looksLikeUtility(token) {
  if (!token || token.length > 80) return false;
  const base = stripVariants(token);
  if (!base) return false;
  if (STATIC_CLASSES.has(base)) return true;
  return ALL_PREFIXES.some((p) => base === p.replace(/-$/, "") ? false : base.startsWith(p));
}

function classNameStrings(source) {
  const out = [];
  const n = source.length;
  let i = 0;
  while (i < n) {
    const c = source[i];
    if (c === "/" && source[i + 1] === "/") { const nl = source.indexOf("\n", i); i = nl < 0 ? n : nl + 1; continue; }
    if (c === "/" && source[i + 1] === "*") { const e = source.indexOf("*/", i + 2); i = e < 0 ? n : e + 2; continue; }
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < n && source[j] !== c) { if (source[j] === "\\") j += 1; j += 1; }
      out.push({ start: i + 1, text: source.slice(i + 1, j) });
      i = j + 1;
      continue;
    }
    if (c === String.fromCharCode(96)) {
      // template literal: keep only the literal chunks, never evaluate expressions
      let j = i + 1;
      let segStart = j;
      let depth = 0;
      let safe = true;
      while (j < n) {
        const d = source[j];
        if (d === "\\") { j += 2; continue; }
        if (d === String.fromCharCode(96) && depth === 0) { out.push({ start: segStart, text: source.slice(segStart, j), template: true }); break; }
        if (d === "$" && source[j + 1] === "{") { out.push({ start: segStart, text: source.slice(segStart, j), template: true }); depth += 1; j += 2; continue; }
        if (d === "}" && depth > 0) { depth -= 1; j += 1; segStart = j; continue; }
        j += 1;
      }
      i = j + 1;
      continue;
    }
    i += 1;
  }
  return out;
}

function lineAt(source, index) { return source.slice(0, index).split("\n").length; }

function customSelectorsIn(css) {
  // class names that index.css itself defines (so "not generated" is not a defect)
  const set = new Set();
  const re = /\.(-?[A-Za-z_][\w-]*)/g;
  let m = re.exec(css);
  while (m) { set.add(m[1]); m = re.exec(css); }
  return set;
}

async function probePages(compiler, indexCssText) {
  const baseline = compiler.build([]);
  const custom = customSelectorsIn(indexCssText);
  const all = new Map(); // token -> { files: Set, arbitary: bool }
  for (const rel of PAGE_FILES) {
    const abs = join(WEB, rel);
    if (!existsSync(abs)) continue;
    const source = readFileSync(abs, "utf8");
    for (const chunk of classNameStrings(source)) {
      const tokens = chunk.text.split(/\s+/).filter(Boolean);
      if (!tokens.length) continue;
      const line = lineAt(source, chunk.start);
      for (const token of tokens) {
        if (token.indexOf("$") >= 0 || token.indexOf("{") >= 0) continue;
        if (!looksLikeUtility(token)) continue;
        const entry = all.get(token) || { token: token, files: [], arbitrary: token.indexOf("-[") >= 0 };
        entry.files.push(rel + ":" + line);
        all.set(token, entry);
      }
    }
  }
  const tokens = [...all.keys()];
  const missing = [];
  const generated = [];
  for (const token of tokens) {
    const out = compiler.build([token]);
    if (hasSelector(out, token)) generated.push(token);
    else missing.push(all.get(token));
  }
  // drop classes that index.css defines itself (custom utilities / component classes)
  const known = (tok) => custom.has(stripVariants(tok).replace(/^!?-?/, "")) || custom.has(tok);
  const missingFiltered = missing.filter((e) => !known(e.token));
  missingFiltered.sort((a, b) => (b.arbitrary ? 1 : 0) - (a.arbitrary ? 1 : 0) || a.token.localeCompare(b.token));
  return {
    files: PAGE_FILES,
    tokens: tokens.length,
    generated: generated.length,
    missingCount: missingFiltered.length,
    missing: missingFiltered.map((e) => ({ token: e.token, arbitrary: e.arbitrary, seen: e.files.slice(0, 3) })),
    droppedCustom: missing.filter((e) => known(e.token)).map((e) => e.token).sort(),
  };
}

/* ------------------------------------------------------------------ */
/* main                                                               */
/* ------------------------------------------------------------------ */

async function main() {
  const source = readFileSync(INDEX_CSS, "utf8");
  const real = await probeReal();
  const sidebar = await probeSidebar(source);
  const sidebarWinner = sidebar.filter((s) => s.generated).map((s) => s.variant);
  const compiler = await compile(source, COMPILE_OPTS);
  const pages = await probePages(compiler, source);
  const report = {
    generatedAt: new Date().toISOString(),
    source: "src/index.css",
    tailwindVersion: JSON.parse(readFileSync(join(NM, "tailwindcss", "package.json"), "utf8")).version,
    q1Fonts: real.font,
    q2Sidebar: sidebar,
    q2Winner: sidebarWinner,
    q3Spacing: real.spacing,
    q4Pages: pages,
  };
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(report, null, 2) + "\n", "utf8");
  if (wantJson) {
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
    return;
  }
  console.log("tw-probe  tailwindcss " + report.tailwindVersion + "  source=src/index.css");
  console.log("");
  console.log("Q1 custom font tiers");
  for (const row of real.font) {
    const resolved = row.resolved.fontSize ? "  -> font-size " + row.resolved.fontSize + ", line-height " + row.resolved.lineHeight : "";
    console.log("  " + pad(row.class, 12) + (row.generated ? row.declarations.join("; ") + resolved : "NOT GENERATED"));
  }
  console.log("");
  console.log("Q2 sidebar token namespace (candidate w-sidebar)");
  for (const row of sidebar) {
    console.log("  " + row.variant + "  " + pad(row.placement, 7) + pad(row.declaration, 28) + (row.generated ? "GENERATED  " + row.snippet : "not generated"));
  }
  console.log("");
  console.log("Q3 dynamic spacing (px/4) and fractional steps");
  for (const row of real.spacing) {
    const value = row.computedPx.length ? row.computedPx.join(", ") : row.declarations.join(", ");
    console.log("  " + pad(row.class, 12) + (row.generated ? value : "NOT GENERATED"));
  }
  console.log("");
  console.log("Q4 page classes without generated CSS: " + pages.missingCount + " (of " + pages.tokens + " tried in " + pages.files.length + " files)");
  for (const row of pages.missing) console.log("  " + pad(row.token, 34) + (row.arbitrary ? "[arbitrary] " : "") + row.seen.join(", "));
  if (!pages.missing.length) console.log("  (none)");
  console.log("");
  console.log("report: " + OUT);
}

function pad(value, width) {
  const s = String(value);
  return s.length >= width ? s + " " : s + " ".repeat(width - s.length);
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
