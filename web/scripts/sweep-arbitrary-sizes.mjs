#!/usr/bin/env node
/**
 * sweep-arbitrary-sizes -- lexical sweeper for arbitrary font sizes and box sizes.
 * Batch 4 prep. Zero dependencies, dry-run by default. ADR-066 section "token 映射" is the
 * single authority for the mapping; this script never decides semantics on its own.
 *
 * Rewrites ONLY text inside string / template literals (className is by far the main case);
 * comments, identifiers and regex literals are skipped; CSS files are never scanned.
 *
 * SAFETY
 *   --apply REWRITES src/**. It is a coordinating action: it must only run on an explicit
 *   go-ahead from the lead (or the task owner), never as a guard test. The dry-run exercises
 *   exactly the same guards (prerequisites, token presence, scope protection) without writing
 *   anything, so "does the guard pass?" is a dry-run question, not an --apply question.
 *   Reports are never overwritten: --apply defaults to .scratch/apply-<UTC>.json, dry-runs are
 *   expected to pass --out with their own timestamp, and every run appends one JSON line to
 *   .scratch/sweep-history.jsonl (append-only audit trail). Team-wide rule: docs/ui-standards.md section 10.
 *
 * Usage:
 *   node scripts/sweep-arbitrary-sizes.mjs                     # dry-run + human summary + JSON report
 *   node scripts/sweep-arbitrary-sizes.mjs --json              # JSON report on stdout
 *   node scripts/sweep-arbitrary-sizes.mjs --apply             # REWRITE src (lead go-ahead required; blocks on failed prerequisites/tokens)
 *   node scripts/sweep-arbitrary-sizes.mjs --root <dir>        # limit the scan (default src)
 *   node scripts/sweep-arbitrary-sizes.mjs --out <file>        # report path (dry-run default .scratch/sweep-report.json; --apply default .scratch/apply-<UTC>.json)
 *   node scripts/sweep-arbitrary-sizes.mjs --include-vendored  # let --apply touch src/components/ui (frozen v3)
 *   node scripts/sweep-arbitrary-sizes.mjs --include-protected # let --apply touch t2/t4-owned files (default: skip + count)
 *
 * Exit codes: 0 clean, 2 usage / IO / failed prereq, 3 --apply refused (missing tokens or vendored scope).
 *
 * Confidence:
 *   OK        exact ADR mapping, or a t5 override, or px divisible by 4 -> spacing class.
 *   UNSURE    parseable but needs a human/build decision -> reported, never applied.
 *   UNPARSED  non-px value (var(), clamp(), calc(), rem, %) -> reported only.
 */
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, "..");
const DEFAULT_SRC = resolve(WEB_ROOT, "src");
const DEFAULT_OUT = resolve(WEB_ROOT, "..", ".scratch", "sweep-report.json");
const INDEX_CSS = resolve(WEB_ROOT, "src", "index.css");
const BT = String.fromCharCode(96);

/* ===================================================================== */
/* CONFIG -- single mapping table (ADR-066 "token 映射"); edit here only  */
/* ===================================================================== */

/**
 * Font ladder, ascending. ADR-066 rule: take the LARGEST step whose px is <= the original;
 * values below the smallest step collapse to the smallest step.
 * lh = the line-height the token carries (px). Tokens marked ownToken must exist in
 * index.css @theme (lead adds them); the rest are Tailwind v4 defaults.
 */
const FONT_LADDER = [
  { px: 11, util: "text-2xs", lh: 16, ownToken: true },
  { px: 12, util: "text-xs", lh: 16 },
  { px: 13, util: "text-2sm", lh: 20, ownToken: true },
  { px: 14, util: "text-sm", lh: 20 },
  { px: 16, util: "text-base", lh: 24 },
  { px: 18, util: "text-lg", lh: 28 },
  { px: 19, util: "text-metric", lh: 26, ownToken: true },
  { px: 20, util: "text-xl", lh: 28 },
  { px: 22, util: "text-h1", lh: 28, ownToken: true },
  { px: 28, util: "text-2xl", lh: 36 },
  { px: 40, util: "text-4xl", lh: 40 },
];

/** index.css body { line-height: 1.65 } -- used only to estimate the density delta. */
const BODY_LINE_HEIGHT_FALLBACK = 1.65;

/** Tailwind v4 default --spacing is 0.25rem = 4px at a 16px root font size. */
const SPACING_UNIT_PX = 4;

/** box-size prefixes that ADR-066 S1/S2 rewrite (radius is deliberately absent). */
const SIZE_PREFIXES = ["min-h", "max-h", "min-w", "max-w", "h", "w", "size"];

/** control heights allowed by design-system 3.1 / ADR-066 S5. */
const CONTROL_HEIGHT_STEPS = [7, 8, 9, 10, 11, 12];

/** dense tier: h-7 is allowed for toolbars and dense tables only (ADR-066 S5 amendment). */
const CONTROL_HEIGHT_DENSE = 7;

/** tokens that must be present in src/index.css @theme before --apply runs. */
const REQUIRED_TOKENS = ["--text-2xs", "--text-2sm", "--text-metric", "--text-h1", "w-sidebar"];

/**
 * tw-probe (scripts/tw-probe.mjs) proved that .w-sidebar only generates from one of these
 * variables; a bare --w-sidebar in @theme or :root emits nothing. Checked in order.
 */
const W_SIDEBAR_TOKENS = ["--spacing-sidebar", "--width-sidebar", "--w-sidebar"];

/** S2 non-multiple-of-4 pairs, targets given by t5 (ADR-066 S2). */
const PAIR_OVERRIDES = [
  {
    pattern: /(?<![\w-])h-\[15px\]\s+w-\[15px\]/g,
    to: "size-4",
    confidence: "OK",
    note: "ADR-066 S2: 15px icon -> size-4 (+1px, t5 target)",
  },
  {
    pattern: /(?<![\w-])h-\[18px\]\s+w-\[18px\]/g,
    to: "size-4.5",
    confidence: "OK",
    note: "ADR-066 S2: 18px -> size-4.5 (tw-probe: width/height 18px)",
  },
];

/** S2 single-token overrides (exact class text). */
const SINGLE_OVERRIDES = [
  { from: "w-[3px]", to: "w-0.5", confidence: "OK", note: "ADR-066 S2: -1px" },
  { from: "w-[2px]", to: "w-0.5", confidence: "OK", note: "ADR-066 S2: 0px" },
  { from: "h-[1px]", to: "h-px", confidence: "OK", note: "ADR-066 S2: 0px" },
  { from: "w-[1px]", to: "w-px", confidence: "OK", note: "ADR-066 S2: 0px" },
  { from: "max-w-[130px]", to: "max-w-32", confidence: "OK", note: "ADR-066 S2: -2px" },
  { from: "min-w-[190px]", to: "min-w-48", confidence: "OK", note: "ADR-066 S2: +2px" },
  { from: "min-h-[70px]", to: "min-h-17.5", confidence: "OK", note: "ADR-066 S2 extension: 70px/4 = 17.5 (tw-probe: 70px)" },
];

/** S3 the only named layout token: --w-sidebar 248px -> w-sidebar. */
const NAMED_OVERRIDES = [
  {
    from: "w-[248px]",
    to: "w-sidebar",
    confidence: "OK",
    note: "ADR-066 S3: named layout token --w-sidebar (sidebar / app-shell)",
    files: ["src/components/layout/app-shell.tsx", "src/components/layout/sidebar.tsx"],
  },
];

/**
 * Files another lane owns (t2 frozen vendor originals + t4 pages). --apply skips them unless
 * --include-protected is passed, so a sweep never clobbers a parallel edit.
 */
const APPLY_PROTECTED = [
  "src/components/ui/button.tsx",
  "src/components/ui/badge.tsx",
  "src/components/ui/card.tsx",
  "src/components/ui/input.tsx",
  "src/components/ui/alert.tsx",
  "src/components/ui/label.tsx",
  "src/components/ui/pagination.tsx",
  "src/components/ui/skeleton.tsx",
  "src/components/ui/spinner.tsx",
  "src/components/ui/empty.tsx",
  "src/components/ui/time-range-picker.tsx",
  "src/pages/logs.tsx",
  "src/pages/dashboard.tsx",
];

function isApplyProtected(rel) {
  return APPLY_PROTECTED.indexOf(rel) >= 0;
}

/* ===================================================================== */
/* CLI                                                                    */
/* ===================================================================== */

const argv = process.argv.slice(2);
const opts = {
  apply: argv.includes("--apply"),
  json: argv.includes("--json"),
  includeVendored: argv.includes("--include-vendored"),
  includeProtected: argv.includes("--include-protected"),
  root: DEFAULT_SRC,
  out: DEFAULT_OUT,
};
let outExplicit = false;
for (let i = 0; i < argv.length; i += 1) {
  if (argv[i] === "--root" && argv[i + 1]) opts.root = resolve(WEB_ROOT, argv[i + 1]);
  if (argv[i] === "--out" && argv[i + 1]) { opts.out = resolve(WEB_ROOT, argv[i + 1]); outExplicit = true; }
}
// never let a write run clobber the previous attribution: each --apply gets its own report
if (opts.apply && !outExplicit) {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "");
  opts.out = resolve(WEB_ROOT, "..", ".scratch", "apply-" + stamp + ".json");
}
const HISTORY = resolve(WEB_ROOT, "..", ".scratch", "sweep-history.jsonl");
if (!existsSync(opts.root)) {
  console.error("sweep: --root not found: " + opts.root);
  process.exit(2);
}

/* ===================================================================== */
/* prerequisites (ADR-066 S1: dynamic spacing + 16px root)                */
/* ===================================================================== */

function readIndexCss() {
  return existsSync(INDEX_CSS) ? readFileSync(INDEX_CSS, "utf8") : "";
}

function checkPrerequisites() {
  const css = readIndexCss();
  const spacingDefined = /--spacing\s*:/.test(css);
  const htmlBlocks = css.match(/html\s*\{[^}]*\}/g) || [];
  const htmlFontSize = htmlBlocks.some(function (block) { return /font-size\s*:/.test(block); });
  const bodyBlock = /body\s*\{([^}]*)\}/.exec(css);
  const bodyLh = bodyBlock ? /line-height\s*:\s*([\d.]+)/.exec(bodyBlock[1]) : null;
  const problems = [];
  if (spacingDefined) problems.push("index.css defines --spacing; the px/4 premise of ADR-066 S1 no longer holds");
  if (htmlFontSize) problems.push("index.css overrides html font-size; root 16px premise of ADR-066 S1 no longer holds");
  const tokens = new Set();
  const re = /--[a-z0-9-]+\s*:/g;
  let m = re.exec(css);
  while (m !== null) { tokens.add(m[0].replace(/\s*:$/, "").trim()); m = re.exec(css); }
  return {
    spacingTokenDefined: spacingDefined,
    htmlFontSizeOverridden: htmlFontSize,
    bodyLineHeight: bodyLh ? Number(bodyLh[1]) : BODY_LINE_HEIGHT_FALLBACK,
    presentTokens: tokens,
    problems: problems,
  };
}

/* ===================================================================== */
/* lexical string regions (comments / regex / interpolation skipped)      */
/* ===================================================================== */

function skipLineComment(code, i) {
  const nl = code.indexOf("\n", i);
  return nl < 0 ? code.length : nl + 1;
}
function skipBlockComment(code, i) {
  const end = code.indexOf("*/", i + 2);
  return end < 0 ? code.length : end + 2;
}
function skipRegex(code, i) {
  let k = i - 1;
  while (k >= 0 && /\s/.test(code[k])) k -= 1;
  const prev = k >= 0 ? code[k] : "";
  if (prev && "(,=:[!&|?{};".indexOf(prev) < 0) return i;
  let j = i + 1;
  let inClass = false;
  while (j < code.length) {
    const c = code[j];
    if (c === "\\") { j += 2; continue; }
    if (c === "\n") return i;
    if (inClass) { if (c === "]") inClass = false; j += 1; continue; }
    if (c === "[") { inClass = true; j += 1; continue; }
    if (c === "/") {
      j += 1;
      while (j < code.length && /[a-z]/i.test(code[j])) j += 1;
      return j;
    }
    j += 1;
  }
  return i;
}

function scanTemplate(code, i, regions) {
  const n = code.length;
  let j = i + 1;
  let segStart = j;
  while (j < n) {
    const c = code[j];
    if (c === "\\") { j += 2; continue; }
    if (c === BT) { regions.push({ start: segStart, end: j }); return j + 1; }
    if (c === "$" && code[j + 1] === "{") {
      if (j > segStart) regions.push({ start: segStart, end: j });
      j = scanCode(code, j + 2, regions, true);
      segStart = j;
      continue;
    }
    j += 1;
  }
  regions.push({ start: segStart, end: n });
  return n;
}

function scanCode(code, i, regions, stopAtCloseBrace) {
  const n = code.length;
  let depth = 0;
  while (i < n) {
    const c = code[i];
    if (stopAtCloseBrace && c === "}" && depth === 0) return i + 1;
    if (c === "/" && code[i + 1] === "/") { i = skipLineComment(code, i); continue; }
    if (c === "/" && code[i + 1] === "*") { i = skipBlockComment(code, i); continue; }
    if (c === "'" || c === '"') {
      let j = i + 1;
      while (j < n) {
        if (code[j] === "\\") { j += 2; continue; }
        if (code[j] === c || code[j] === "\n") break;
        j += 1;
      }
      if (j < n && code[j] === c) { regions.push({ start: i + 1, end: j }); i = j + 1; } else { i = j; }
      continue;
    }
    if (c === BT) { i = scanTemplate(code, i, regions); continue; }
    if (c === "/") { const j = skipRegex(code, i); if (j > i) { i = j; continue; } }
    if (stopAtCloseBrace && c === "{") { depth += 1; i += 1; continue; }
    if (stopAtCloseBrace && c === "}" && depth > 0) { depth -= 1; i += 1; continue; }
    i += 1;
  }
  return i;
}

/* ===================================================================== */
/* classification                                                         */
/* ===================================================================== */

const ARBITRARY = /(?<![\w-])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-\[([^\[\]]+)\]/g;
const PX = /^\s*(\d+(?:\.\d+)?)px\s*$/;

function round2(value) { return Math.round(value * 100) / 100; }

function classifyFont(sizePx, bodyLineHeight) {
  let chosen = FONT_LADDER[0];
  for (const step of FONT_LADDER) if (step.px <= sizePx) chosen = step;
  const lhFrom = round2(sizePx * bodyLineHeight);
  return {
    to: chosen.util,
    confidence: "OK",
    note: "floor to " + chosen.px + "px (ADR-066 1)",
    font: {
      tokenPx: chosen.px,
      ownToken: Boolean(chosen.ownToken),
      fontSizeDeltaPx: round2(chosen.px - sizePx),
      lineHeightFromPx: lhFrom,
      lineHeightToPx: chosen.lh,
      lineHeightDeltaPx: round2(chosen.lh - lhFrom),
    },
  };
}

function findNamedOverride(file, whole) {
  for (const entry of NAMED_OVERRIDES) {
    if (entry.from === whole && entry.files.indexOf(file) >= 0) return entry;
  }
  return null;
}

function findSingleOverride(whole) {
  for (const entry of SINGLE_OVERRIDES) if (entry.from === whole) return entry;
  return null;
}

function classifyBox(prefix, pxValue, file, whole) {
  if (pxValue === null) return { confidence: "UNPARSED", note: "non-px box value (var/calc/rem)" };
  const named = findNamedOverride(file, whole);
  if (named) return named;
  const single = findSingleOverride(whole);
  if (single) return single;
  const px = Number(pxValue);
  if (px % SPACING_UNIT_PX === 0) {
    return {
      to: prefix + "-" + px / SPACING_UNIT_PX,
      confidence: "OK",
      note: "px/4 -> dynamic spacing (ADR-066 S1, zero visual change)",
    };
  }
  return {
    confidence: "UNSURE",
    note: "value is not a multiple of 4 and has no ADR-066 S2 target; keep as is",
  };
}

/**
 * Analyze one class-string slice. Returns edits with slice-relative offsets, so the same
 * pipeline drives dry-run reporting and --apply rewriting.
 */
function analyzeSlice(slice, file, bodyLineHeight) {
  const edits = [];
  const covered = new Array(slice.length).fill(false);
  function cover(start, end) { for (let k = start; k < end; k += 1) covered[k] = true; }
  for (const pair of PAIR_OVERRIDES) {
    pair.pattern.lastIndex = 0;
    let m = pair.pattern.exec(slice);
    while (m !== null) {
      edits.push({ start: m.index, end: m.index + m[0].length, from: m[0], to: pair.to, confidence: pair.confidence, note: pair.note });
      cover(m.index, m.index + m[0].length);
      m = pair.pattern.exec(slice);
    }
  }
  ARBITRARY.lastIndex = 0;
  let m = ARBITRARY.exec(slice);
  while (m !== null) {
    const whole = m[0];
    const prefix = m[1];
    const value = m[2];
    let skip = false;
    for (let k = m.index; k < m.index + whole.length; k += 1) if (covered[k]) { skip = true; break; }
    if (skip) { m = ARBITRARY.exec(slice); continue; }
    const px = PX.exec(value);
    let result = null;
    if (prefix === "text") {
      if (!px) {
        const isColor = /^(var\(--|color:|#|rgb|hsl|oklch)/.test(value);
        result = { confidence: "UNPARSED", note: isColor ? "arbitrary text color, not a font size" : "non-px font value" };
      } else {
        result = classifyFont(Number(px[1]), bodyLineHeight);
      }
    } else if (SIZE_PREFIXES.indexOf(prefix) >= 0) {
      result = classifyBox(prefix, px ? px[1] : null, file, whole);
    } else {
      result = { confidence: "OUT_OF_SCOPE", note: prefix };
    }
    if (result.confidence !== "OUT_OF_SCOPE") {
      edits.push({ start: m.index, end: m.index + whole.length, from: whole, to: result.to || null, confidence: result.confidence, note: result.note, font: result.font || null });
    } else {
      edits.push({ start: m.index, end: m.index + whole.length, from: whole, to: null, confidence: "OUT_OF_SCOPE", note: result.note });
    }
    m = ARBITRARY.exec(slice);
  }
  return edits;
}

/* ===================================================================== */
/* file walking / helpers                                                 */
/* ===================================================================== */

const SKIP_DIRS = new Set(["node_modules", "dist", "build", "coverage", ".git"]);
const SKIP_FILE = /\.(test|spec|stories)\.(ts|tsx)$/;

function walk(dir, acc) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name) || entry.name === "__tests__") continue;
      walk(full, acc);
    } else if (/\.(ts|tsx)$/.test(entry.name) && !SKIP_FILE.test(entry.name)) {
      acc.push(full);
    }
  }
  return acc;
}

function lineStartsOf(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i += 1) if (text[i] === "\n") starts.push(i + 1);
  return starts;
}
function lineOf(starts, index) {
  let lo = 0;
  let hi = starts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (starts[mid] <= index) lo = mid; else hi = mid - 1;
  }
  return lo + 1;
}
function columnOf(starts, index) { return index - starts[lineOf(starts, index) - 1] + 1; }

const CONTROL_TAGS = new Set(["button", "a", "input", "select", "textarea", "label", "summary", "option", "optgroup"]);
const CONTROL_COMPONENT = /(Button|Trigger|Input|Select|Toggle|Tab|Checkbox|Radio|Menu|Item|Tooltip)/;

function enclosingTag(code, index) {
  const from = Math.max(0, index - 3000);
  const lt = code.lastIndexOf("<", index);
  if (lt < from) return null;
  if (code.slice(lt, index).indexOf(">") >= 0) return null;
  const m = /^<([A-Za-z][\w.]*)/.exec(code.slice(lt, lt + 60));
  return m ? m[1] : null;
}
function isControlTag(tag) {
  if (!tag) return false;
  if (CONTROL_TAGS.has(tag.toLowerCase())) return true;
  return CONTROL_COMPONENT.test(tag);
}

/* ===================================================================== */
/* main                                                                   */
/* ===================================================================== */

function main() {
  const prereq = checkPrerequisites();
  const files = walk(opts.root, []).sort();
  const missingTokens = REQUIRED_TOKENS.filter(function (t) {
    if (t === "w-sidebar") return !W_SIDEBAR_TOKENS.some(function (name) { return prereq.presentTokens.has(name); });
    return !prereq.presentTokens.has(t);
  });
  const sidebarToken = W_SIDEBAR_TOKENS.find(function (name) { return prereq.presentTokens.has(name); }) || null;

  const replacements = [];
  const outOfScope = new Map();
  const controlHeightAudit = [];
  const controlHeightIcons = [];
  const controlHeightDense = [];
  const fileContents = new Map();
  let stringRegions = 0;
  let filesScanned = 0;

  for (const abs of files) {
    const text = readFileSync(abs, "utf8");
    if (!text) continue;
    filesScanned += 1;
    fileContents.set(abs, text);
    const rel = relative(WEB_ROOT, abs).split(sep).join("/");
    const isVendored = rel.indexOf("src/components/ui/") === 0;
    const regions = [];
    scanCode(text, 0, regions, false);
    stringRegions += regions.length;
    const starts = lineStartsOf(text);
    for (const region of regions) {
      const slice = text.slice(region.start, region.end);
      const tag = enclosingTag(text, region.start);
      const controlTag = isControlTag(tag);
      if (controlTag) {
        const re = /(?<![\w-])h-(\d+(?:\.\d+)?)(?![\w-])/g;
        let hm = re.exec(slice);
        while (hm !== null) {
          const step = Number(hm[1]);
          if (step === CONTROL_HEIGHT_DENSE || CONTROL_HEIGHT_STEPS.indexOf(step) < 0) {
            const row = {
              file: rel,
              line: lineOf(starts, region.start + hm.index),
              tag: tag,
              className: "h-" + hm[1],
            };
            // equal h/w (or h/size) at the same value means an icon/glyph box, not a control height
            const pairRe = new RegExp("(?<![\\w-])(?:w|size)-" + hm[1].replace(".", "\\.") + "(?![\\w.-])");
            if (pairRe.test(slice)) {
              row.likely = "icon";
              row.note = "square pair: icon/glyph size, not a control height";
              controlHeightIcons.push(row);
            } else if (step === CONTROL_HEIGHT_DENSE) {
              row.likely = "dense";
              row.note = "dense tier h-7: toolbars / dense tables only (ADR-066 S5 amendment) - listed, never converted";
              controlHeightDense.push(row);
            } else {
              row.likely = "control";
              row.note = "control height off the ladder h-7..h-12 (ADR-066 S5)";
              controlHeightAudit.push(row);
            }
          }
          hm = re.exec(slice);
        }
      }
      if (slice.indexOf("-[") < 0) continue;
      const edits = analyzeSlice(slice, rel, prereq.bodyLineHeight);
      for (const edit of edits) {
        const absIndex = region.start + edit.start;
        if (edit.confidence === "OUT_OF_SCOPE") {
          const bucket = outOfScope.get(edit.note) || { prefix: edit.note, count: 0, samples: [] };
          bucket.count += 1;
          if (bucket.samples.length < 3 && bucket.samples.indexOf(edit.from) < 0) bucket.samples.push(edit.from);
          outOfScope.set(edit.note, bucket);
          continue;
        }
        const row = {
          file: rel,
          line: lineOf(starts, absIndex),
          column: columnOf(starts, absIndex),
          from: edit.from,
          to: edit.to,
          confidence: edit.confidence,
          scope: isVendored ? "vendored" : "app",
          note: edit.note,
          _abs: abs,
          _start: absIndex,
          _end: absIndex + edit.from.length,
        };
        if (edit.font) row.font = edit.font;
        if (controlTag && (edit.to || "").indexOf("h-") === 0) {
          const step = Number((edit.to || "").replace("h-", ""));
          if (CONTROL_HEIGHT_STEPS.indexOf(step) < 0) row.controlHeightRisk = true;
        }
        replacements.push(row);
      }
    }
  }

  const totals = { filesScanned: filesScanned, stringRegions: stringRegions, candidates: replacements.length, ok: 0, unsure: 0, unparsed: 0, outOfScope: 0 };
  for (const r of replacements) {
    if (r.confidence === "OK") totals.ok += 1;
    else if (r.confidence === "UNSURE") totals.unsure += 1;
    else totals.unparsed += 1;
  }
  for (const bucket of outOfScope.values()) totals.outOfScope += bucket.count;

  const byFile = new Map();
  const unsureByFile = new Map();
  const fontLines = new Set();
  const fontByToken = new Map();
  let lineHeightDeltaSum = 0;
  for (const r of replacements) {
    const row = byFile.get(r.file) || { file: r.file, ok: 0, unsure: 0, unparsed: 0 };
    if (r.confidence === "OK") row.ok += 1;
    else if (r.confidence === "UNSURE") row.unsure += 1;
    else row.unparsed += 1;
    byFile.set(r.file, row);
    if (r.confidence === "UNSURE") {
      const list = unsureByFile.get(r.file) || [];
      list.push({ line: r.line, column: r.column, from: r.from, note: r.note, scope: r.scope });
      unsureByFile.set(r.file, list);
    }
    if (r.font && r.confidence === "OK") {
      fontLines.add(r.file + ":" + r.line);
      lineHeightDeltaSum += r.font.lineHeightDeltaPx;
      const stats = fontByToken.get(r.to) || { token: r.to, count: 0, lineHeightDeltaPx: 0 };
      stats.count += 1;
      stats.lineHeightDeltaPx = round2(stats.lineHeightDeltaPx + r.font.lineHeightDeltaPx);
      fontByToken.set(r.to, stats);
    }
  }
  const unsureGrouped = [...unsureByFile.entries()]
    .sort(function (a, b) { return b[1].length - a[1].length; })
    .map(function (entry) { return { file: entry[0], count: entry[1].length, items: entry[1] }; });
  const unsureTop40 = [];
  for (const group of unsureGrouped) {
    for (const item of group.items) {
      if (unsureTop40.length >= 40) break;
      unsureTop40.push({ file: group.file, line: item.line, column: item.column, from: item.from, note: item.note, scope: item.scope });
    }
    if (unsureTop40.length >= 40) break;
  }

  const report = {
    generatedAt: new Date().toISOString(),
    mode: opts.apply ? "apply" : "dry-run",
    authority: "docs/adr/ADR-066-token-semantics-adoption.md",
    root: relative(WEB_ROOT, opts.root).split(sep).join("/") || ".",
    prerequisites: {
      spacingTokenDefined: prereq.spacingTokenDefined,
      htmlFontSizeOverridden: prereq.htmlFontSizeOverridden,
      bodyLineHeight: prereq.bodyLineHeight,
      problems: prereq.problems,
    },
    mapping: {
      fontLadder: FONT_LADDER.map(function (s) { return s.px + "px=" + s.util + "/" + s.lh + "lh"; }),
      fontRule: "floor to the largest step whose px <= original; below the smallest step -> smallest step",
      boxRule: "px divisible by 4 -> prefix-px/4; non-multiples follow ADR-066 S2 targets",
      spacingUnitPx: SPACING_UNIT_PX,
      namedToken: "--w-sidebar (w-sidebar)",
      radius: "out of scope (ADR-066 3)",
    },
    tokens: { checked: [...prereq.presentTokens].sort(), required: REQUIRED_TOKENS, sidebarToken: sidebarToken, missing: missingTokens },
    totals: totals,
    fontDensity: {
      affectedLines: fontLines.size,
      totalLineHeightDeltaPx: round2(lineHeightDeltaSum),
      byToken: [...fontByToken.values()].sort(function (a, b) { return b.count - a.count; }),
    },
    byFile: [...byFile.values()].sort(function (a, b) { return (b.ok + b.unsure + b.unparsed) - (a.ok + a.unsure + a.unparsed); }),
    outOfScopeGroups: [...outOfScope.values()].map(function (b) { return { prefix: b.prefix, count: b.count, samples: b.samples }; }).sort(function (a, b) { return b.count - a.count; }),
    controlHeightAudit: controlHeightAudit,
    controlHeightIcons: controlHeightIcons,
    controlHeightDense: controlHeightDense,
    controlHeightRisks: replacements.filter(function (r) { return r.controlHeightRisk; }).map(function (r) { return { file: r.file, line: r.line, from: r.from, to: r.to, note: "generic spacing class on a control-height slot (ADR-066 S5)" }; }),
    replacements: replacements,
    unsureGrouped: unsureGrouped,
    unsureTop40: unsureTop40,
    applied: 0,
    skippedVendored: 0,
    skippedProtected: 0,
  };

  if (opts.apply) {
    const blocking = [];
    if (prereq.problems.length) blocking.push.apply(blocking, prereq.problems);
    if (missingTokens.length) blocking.push("missing tokens in src/index.css @theme: " + missingTokens.join(", "));
    // the two scope guards are soft: skipped rows are counted in the report, not fatal
    if (blocking.length) {
      report.applyBlocked = blocking;
      writeReport(report);
      console.error("sweep: --apply refused:" + "\n  - " + blocking.join("\n  - "));
      process.exit(3);
    }
    applyEdits(report, fileContents);
  }

  writeReport(report);
  if (opts.json) process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  else printSummary(report);
  return 0;
}

function writeReport(report) {
  mkdirSync(dirname(opts.out), { recursive: true });
  writeFileSync(opts.out, JSON.stringify(report, null, 2) + "\n", "utf8");
  appendHistory(report);
}

/** append-only audit line; a later dry-run can never erase an earlier apply */
function appendHistory(report) {
  const line = JSON.stringify({
    at: report.generatedAt,
    mode: report.mode,
    report: relative(WEB_ROOT, opts.out).split(sep).join("/"),
    totals: report.totals,
    applied: report.applied,
    skippedVendored: report.skippedVendored,
    skippedProtected: report.skippedProtected,
    applyBlocked: report.applyBlocked || null,
  });
  try {
    appendFileSync(HISTORY, line + "\n", "utf8");
  } catch (error) {
    console.error("sweep: history append failed: " + error.message);
  }
}

function applyEdits(report, fileContents) {
  const byFile = new Map();
  for (const r of report.replacements) {
    if (r.confidence !== "OK") continue;
    if (r.scope === "vendored" && !opts.includeVendored) { report.skippedVendored += 1; continue; }
    if (isApplyProtected(r.file) && !opts.includeProtected) { report.skippedProtected += 1; continue; }
    const list = byFile.get(r._abs) || [];
    list.push(r);
    byFile.set(r._abs, list);
  }
  for (const entry of byFile) {
    const abs = entry[0];
    const list = entry[1].sort(function (a, b) { return b._start - a._start; });
    let text = fileContents.get(abs);
    let applied = 0;
    for (const r of list) {
      if (text.slice(r._start, r._end) !== r.from) continue;
      text = text.slice(0, r._start) + r.to + text.slice(r._end);
      applied += 1;
    }
    if (applied > 0) {
      writeFileSync(abs, text, "utf8");
      report.applied += applied;
    }
  }
}

function pad(value, width) {
  const str = String(value);
  return str.length >= width ? str : str + " ".repeat(width - str.length);
}

function printSummary(report) {
  const t = report.totals;
  console.log("sweep:arbitrary-sizes  root=" + report.root + "  mode=" + report.mode + (opts.apply ? "" : " (no files written)"));
  console.log("authority: ADR-066 token mapping");
  console.log("");
  console.log("prerequisites: --spacing defined=" + report.prerequisites.spacingTokenDefined + "  html font-size overridden=" + report.prerequisites.htmlFontSizeOverridden + "  body line-height=" + report.prerequisites.bodyLineHeight);
  if (report.prerequisites.problems.length) {
    for (const p of report.prerequisites.problems) console.log("  FAIL: " + p);
  } else {
    console.log("  OK (dynamic spacing + 16px root hold)");
  }
  console.log("");
  console.log("files=" + t.filesScanned + "  string-regions=" + t.stringRegions + "  candidates=" + t.candidates);
  console.log(pad("OK", 10) + pad("UNSURE", 10) + pad("UNPARSED", 10) + "OUT_OF_SCOPE");
  console.log(pad(t.ok, 10) + pad(t.unsure, 10) + pad(t.unparsed, 10) + t.outOfScope);
  if (report.tokens.missing.length) {
    console.log("");
    console.log("BLOCKER: --apply needs tokens in src/index.css @theme: " + report.tokens.missing.join(", "));
    console.log("         (text-xs/sm/base/lg/xl/2xl/4xl are Tailwind defaults and already exist)");
  }
  console.log("");
  const fd = report.fontDensity;
  console.log("font density convergence: " + fd.affectedLines + " lines affected, sum(line-height delta)=" + fd.totalLineHeightDeltaPx + "px");
  for (const row of fd.byToken) console.log("  " + pad(row.token, 14) + pad(row.count, 5) + " occurrences, line-height delta " + row.lineHeightDeltaPx + "px");
  console.log("");
  const topOk = report.byFile.filter(function (f) { return f.ok > 0; }).slice(0, 8);
  if (topOk.length) {
    console.log("OK by file (top 8):");
    for (const f of topOk) console.log("  " + pad(f.ok, 4) + f.file);
  }
  console.log("");
  console.log("UNSURE grouped by file (first 40):");
  let shown = 0;
  for (const group of report.unsureGrouped) {
    if (shown >= 40) break;
    console.log("  " + group.file + " (" + group.count + ")");
    for (const item of group.items) {
      if (shown >= 40) break;
      console.log("    " + pad("L" + item.line, 7) + pad(item.from, 20) + item.note + (item.scope === "vendored" ? "  [vendored]" : ""));
      shown += 1;
    }
  }
  if (!report.unsureGrouped.length) console.log("  (none)");
  console.log("");
  if (report.controlHeightAudit.length || report.controlHeightRisks.length) {
    console.log("control heights off the ladder (ADR-066 S5: h-7..h-12):");
    for (const item of report.controlHeightAudit) console.log("  " + item.file + ":" + item.line + "  <" + item.tag + ">  " + item.className + "  " + item.note);
    for (const item of report.controlHeightRisks) console.log("  " + item.file + ":" + item.line + "  " + item.from + " -> " + item.to + "  " + item.note);
  } else {
    console.log("control heights off the ladder (ADR-066 S5): none found");
  }
  if (report.controlHeightDense.length) {
    console.log("dense tier h-7 (allowed; listed for the spec, never converted):");
    for (const item of report.controlHeightDense) console.log("  " + item.file + ":" + item.line + "  <" + item.tag + ">  " + item.className);
  }
  if (report.controlHeightIcons.length) {
    console.log("icon-sized boxes on control-ish tags (informational, not control heights):");
    for (const item of report.controlHeightIcons) console.log("  " + item.file + ":" + item.line + "  <" + item.tag + ">  " + item.className + "  " + item.note);
  }
  console.log("");
  console.log("report: " + relative(process.cwd(), opts.out).split(sep).join("/") + "  (--json prints it; --apply is refused while tokens are missing)");
}

process.exit(main());
