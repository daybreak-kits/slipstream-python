#!/usr/bin/env node
/**
 * lint:ui -- UI discipline gate for web. Zero dependencies.
 *
 * Rules and levels follow docs/ui-standards.md section 6; the per-batch gate ladder lives in
 * docs/ui-adoption-map.md section 9 (formerly docs/ui-migration-map.md section 7.6, now archived).
 * error rules decide the exit code; warn rules only report counts.
 * A rule may declare relax: { dir, level } to downgrade its severity inside a scope
 * (used for vendored shadcn registry sources under src/components/ui/).
 *
 * Usage:
 *   node scripts/lint-ui.mjs                 # human summary, exit 1 on error-level violations
 *   node scripts/lint-ui.mjs --json          # machine readable
 *   node scripts/lint-ui.mjs --max-warnings 700
 *   node scripts/lint-ui.mjs --verbose       # print every violation line, not just top files
 *
 * Exit codes: 0 clean, 1 gate failed, 2 usage or IO error.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, "..");
const DEFAULT_SRC = resolve(WEB_ROOT, "src");

const INTERACTIVE_TAGS = new Set(["button", "a", "input", "select", "textarea", "label", "summary"]);

function regexScanner(re) {
  return function scan(code) {
    const found = [];
    re.lastIndex = 0;
    let m = re.exec(code);
    while (m !== null) {
      found.push({ index: m.index, snippet: m[0] });
      if (m.index === re.lastIndex) re.lastIndex += 1;
      m = re.exec(code);
    }
    return found;
  };
}

// title= on an interactive host element: a tooltip nobody can reach, and not an accessible name.
function scanTitleOnInteractive(code) {
  const found = [];
  const re = /\btitle\s*=\s*(?:"[^"]*"|\{[^}]*\})/g;
  let m = re.exec(code);
  while (m !== null) {
    const lt = code.lastIndexOf("<", m.index);
    if (lt >= 0) {
      const head = code.slice(lt, lt + 40);
      const tagMatch = /^<([A-Za-z][\w.-]*)/.exec(head);
      if (tagMatch) {
        const tag = tagMatch[1].toLowerCase();
        if (INTERACTIVE_TAGS.has(tag)) {
          found.push({ index: m.index, snippet: "<" + tag + " " + m[0].trim() });
        }
      }
    }
    m = re.exec(code);
  }
  return found;
}

const RULES = [
  {
    id: "no-native-select",
    level: "warn",
    why: "原生 <select> 由 components/ui/select 取代（批 2）",
    scan: regexScanner(/<select\b/g),
    allow: [/^src\/components\/ui\/select\.tsx$/],
  },
  {
    id: "no-native-table",
    level: "warn",
    why: "原生 <table> 由 components/ui/table 取代（批 4）",
    scan: regexScanner(/<table\b/g),
    allow: [/^src\/components\/ui\/table\.tsx$/],
  },
  {
    // NOTE: the id stays no-native-dialog for CI/doc stability, but this rule detects
    // ONLY window.confirm / window.alert calls -- it does not look for <dialog> elements.
    id: "no-native-dialog",
    level: "error",
    why: "禁止 window.confirm / window.alert，走 ConfirmDialog（ui-standards 第 9 条）",
    scan: regexScanner(/window\.(confirm|alert)\s*\(/g),
    allow: [],
  },
  {
    id: "no-inline-hex",
    level: "error",
    why: "颜色只走 index.css 的 token，不在 tsx 里写死 hex（ui-standards 第 5 条）",
    scan: regexScanner(/#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/g),
    allow: [],
  },
  {
    id: "no-arbitrary-font-size",
    // Vendored registry sources carry their upstream text sizes; rewriting them
    // would break the "re-vendor verbatim" property. App code must stay at 0.
    relax: { dir: "src/components/ui/", level: "warn" },
    level: "warn",
    why: "字号只用四档（text-[13px] 之类属临时态，批 4 清）",
    scan: regexScanner(/\btext-\[\d+(?:\.\d+)?px\]/g),
    allow: [],
  },
  {
    id: "no-arbitrary-size",
    // Same rationale as no-arbitrary-font-size: frozen inside src/components/ui/.
    relax: { dir: "src/components/ui/", level: "warn" },
    level: "warn",
    why: "高度/宽度走 h-control-* 阶梯（批 2/4 清）",
    scan: regexScanner(/\b[hw]-\[\d+(?:\.\d+)?px\]/g),
    allow: [],
  },
  {
    id: "no-dark-prefix",
    // Vendored registry sources legitimately use dark: variants (our @custom-variant IS
    // the class strategy), so inside src/components/ui/ this rule is a warning only.
    relax: { dir: "src/components/ui/", level: "warn" },
    level: "error",
    why: "暗色只走 .dark class 与 index.css token，禁止 dark: 前缀（硬约束，应为 0）",
    scan: regexScanner(/\bdark:/g),
    allow: [],
  },
  {
    id: "no-title-only-tooltip",
    level: "warn",
    why: "可交互元素上的 title= 既不是提示也不是可访问名：改 Tooltip 或 aria-label（批 1/4）",
    scan: scanTitleOnInteractive,
    allow: [],
  },
  {
    id: "no-native-checkbox",
    level: "warn",
    why: "原生 checkbox 由 components/ui/checkbox 取代（批 2）",
    scan: regexScanner(/<input\b[^>]*?type\s*=\s*\{?\s*["']checkbox["']/g),
    allow: [/^src\/components\/ui\/checkbox\.tsx$/],
  },
  {
    id: "no-native-radio",
    level: "warn",
    why: "原生 radio 由 components/ui/radio-group 取代（批 2）",
    scan: regexScanner(/<input\b[^>]*?type\s*=\s*\{?\s*["']radio["']/g),
    allow: [/^src\/components\/ui\/radio-group\.tsx$/],
  },
];

const SKIP_DIRS = new Set(["node_modules", "dist", "build", "__tests__", ".git"]);

function collectFiles(dir, out) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) continue;
    if (entry.name.startsWith(".")) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      collectFiles(full, out);
    } else if (entry.isFile() && entry.name.endsWith(".tsx") && !/\.(test|spec)\.tsx$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function toPosix(p) {
  return p.split("\\").join("/");
}

function newlineOffsets(code) {
  const offsets = [];
  for (let i = 0; i < code.length; i += 1) {
    if (code.charCodeAt(i) === 10) offsets.push(i);
  }
  return offsets;
}

function lineAt(offsets, index) {
  let lo = 0;
  let hi = offsets.length - 1;
  let line = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid] < index) {
      line = mid + 1;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return line + 1;
}

function parseArgs(argv) {
  const opts = {
    json: false,
    verbose: false,
    root: DEFAULT_SRC,
    maxWarnings: Number.POSITIVE_INFINITY,
    maxErrors: 0,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--json") opts.json = true;
    else if (arg === "--verbose") opts.verbose = true;
    else if (arg === "--help" || arg === "-h") opts.help = true;
    else if (arg === "--max-warnings") opts.maxWarnings = Number(argv[++i]);
    else if (arg === "--max-errors") opts.maxErrors = Number(argv[++i]);
    else if (arg === "--root") opts.root = resolve(process.cwd(), String(argv[++i]));
    else if (arg.startsWith("--max-warnings=")) opts.maxWarnings = Number(arg.split("=")[1]);
    else if (arg.startsWith("--max-errors=")) opts.maxErrors = Number(arg.split("=")[1]);
    else if (arg.startsWith("--root=")) opts.root = resolve(process.cwd(), arg.split("=")[1]);
    else {
      process.stderr.write("lint:ui unknown argument: " + arg + "\n");
      opts.usageError = true;
    }
  }
  if (Number.isNaN(opts.maxWarnings)) opts.usageError = true;
  if (Number.isNaN(opts.maxErrors)) opts.usageError = true;
  return opts;
}

const HELP = [
  "lint:ui -- UI discipline gate (zero dependency)",
  "",
  "  node scripts/lint-ui.mjs [options]",
  "",
  "  --json                machine readable report on stdout",
  "  --verbose             print every violation, not just top files",
  "  --max-warnings <n>    fail when warnings exceed n (default: never)",
  "  --max-errors <n>      fail when errors exceed n (default: 0)",
  "  --root <dir>          scan another source root (default: src)",
  "",
  "  exit 0 clean / within budgets, exit 1 gate failed, exit 2 usage or IO error",
].join("\n");

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    process.stdout.write(HELP + "\n");
    return 0;
  }
  if (opts.usageError) {
    process.stderr.write(HELP + "\n");
    return 2;
  }

  let files;
  try {
    files = collectFiles(opts.root, []).sort();
  } catch (err) {
    process.stderr.write("lint:ui cannot read " + opts.root + ": " + err.message + "\n");
    return 2;
  }

  const report = RULES.map(function (rule) {
    return { id: rule.id, level: rule.level, why: rule.why, count: 0, files: [] };
  });
  const relaxedReport = RULES.map(function (rule) {
    return { id: rule.id, level: rule.relax ? rule.relax.level : "warn", count: 0, files: [] };
  });
  const detail = RULES.map(function () {
    return [];
  });
  const relaxedDetail = RULES.map(function () {
    return [];
  });

  files.forEach(function (full) {
    const rel = toPosix(relative(WEB_ROOT, full));
    let code;
    try {
      code = readFileSync(full, "utf8");
    } catch (err) {
      process.stderr.write("lint:ui cannot read " + rel + ": " + err.message + "\n");
      return;
    }
    const offsets = newlineOffsets(code);
    RULES.forEach(function (rule, ruleIndex) {
      const allowed = rule.allow.some(function (re) {
        return re.test(rel);
      });
      if (allowed) return;
      const hits = rule.scan(code);
      if (hits.length === 0) return;
      const isRelaxed = Boolean(rule.relax) && rel.startsWith(rule.relax.dir);
      const target = isRelaxed ? relaxedReport[ruleIndex] : report[ruleIndex];
      const targetDetail = isRelaxed ? relaxedDetail[ruleIndex] : detail[ruleIndex];
      const bucket = { path: rel, count: hits.length, samples: [] };
      hits.forEach(function (hit) {
        const line = lineAt(offsets, hit.index);
        if (bucket.samples.length < 5) bucket.samples.push({ line: line, snippet: hit.snippet.slice(0, 120) });
        if (targetDetail.length < 500) targetDetail.push({ path: rel, line: line, snippet: hit.snippet.slice(0, 120) });
      });
      target.count += hits.length;
      target.files.push(bucket);
    });
  });

  report.forEach(function (entry, i) {
    entry.files.sort(function (a, b) {
      return b.count - a.count || (a.path < b.path ? -1 : 1);
    });
    relaxedReport[i].files.sort(function (a, b) {
      return b.count - a.count || (a.path < b.path ? -1 : 1);
    });
  });

  const errors = report.filter(function (r) { return r.level === "error"; }).reduce(function (sum, r) { return sum + r.count; }, 0);
  const warnings = report.filter(function (r) { return r.level === "warn"; }).reduce(function (sum, r) { return sum + r.count; }, 0)
    + relaxedReport.reduce(function (sum, r) { return sum + r.count; }, 0);
  const failed = errors > opts.maxErrors || warnings > opts.maxWarnings;
  const payload = {
    ok: !failed,
    root: toPosix(relative(WEB_ROOT, opts.root)),
    filesScanned: files.length,
    totals: {
      errors: errors,
      warnings: warnings,
      maxErrors: opts.maxErrors,
      maxWarnings: Number.isFinite(opts.maxWarnings) ? opts.maxWarnings : null,
    },
    rules: report.map(function (entry, i) {
      const out = {
        id: entry.id,
        level: entry.level,
        why: entry.why,
        count: entry.count,
        files: entry.files,
        violations: opts.verbose || entry.level === "error" ? detail[i] : [],
      };
      if (RULES[i].relax) {
        out.relaxed = {
          scope: RULES[i].relax.dir,
          level: relaxedReport[i].level,
          count: relaxedReport[i].count,
          files: relaxedReport[i].files,
          violations: opts.verbose ? relaxedDetail[i] : [],
        };
      }
      return out;
    }),
  };

  if (opts.json) {
    process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
    return failed ? 1 : 0;
  }

  const out = [];
  out.push("lint:ui  root=" + payload.root + "  files=" + files.length + "  (skip: __tests__, *.test.tsx, *.spec.tsx, per-rule allowlist)");
  out.push("");
  out.push(pad("LEVEL", 6) + pad("RULE", 24) + pad("COUNT", 7) + "TOP FILES");
  report.forEach(function (entry, i) {
    const top = entry.files.slice(0, 3).map(function (f) { return f.path + " (" + f.count + ")"; }).join(", ");
    out.push(pad(entry.level, 6) + pad(entry.id, 24) + pad(String(entry.count), 7) + (top || "-"));
    if (!RULES[i].relax || relaxedReport[i].count === 0) return;
    const rTop = relaxedReport[i].files.slice(0, 3).map(function (f) { return f.path + " (" + f.count + ")"; }).join(", ");
    out.push(pad(relaxedReport[i].level, 6) + pad(entry.id + " (vend)", 24) + pad(String(relaxedReport[i].count), 7) + (rTop || "-"));
  });
  out.push("");
  report.forEach(function (entry, i) {
    if (entry.level !== "error" || detail[i].length === 0) return;
    out.push("error details: " + entry.id);
    detail[i].slice(0, 20).forEach(function (hit) {
      out.push("  " + hit.path + ":" + hit.line + "  " + hit.snippet);
    });
    if (detail[i].length > 20) out.push("  ... " + (detail[i].length - 20) + " more");
  });
  if (opts.verbose) {
    RULES.forEach(function (rule, i) {
      if (rule.level === "error") return;
      detail[i].forEach(function (hit) {
        out.push("  " + rule.id + "  " + hit.path + ":" + hit.line + "  " + hit.snippet);
      });
      if (!rule.relax) return;
      relaxedDetail[i].forEach(function (hit) {
        out.push("  " + rule.id + " (vendored)  " + hit.path + ":" + hit.line + "  " + hit.snippet);
      });
    });
  }
  out.push("");
  out.push("totals: " + errors + " error, " + warnings + " warning (max-errors " + opts.maxErrors + ", max-warnings " + (Number.isFinite(opts.maxWarnings) ? opts.maxWarnings : "none") + ")");
  if (failed) {
    if (errors > opts.maxErrors) out.push("result: FAIL -- " + errors + " error-level violation(s), budget " + opts.maxErrors);
    if (warnings > opts.maxWarnings) out.push("result: FAIL -- " + warnings + " warning(s), budget " + opts.maxWarnings);
  } else {
    out.push("result: PASS");
  }
  process.stdout.write(out.join("\n") + "\n");
  return failed ? 1 : 0;
}

function pad(text, width) {
  let s = String(text);
  while (s.length < width) s += " ";
  return s;
}

process.exitCode = main();
