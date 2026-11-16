import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Braces,
  Play,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  describeApiError,
  previewRewriteRules,
  updateChannel,
  type ConditionMode,
  type RewriteCondition,
  type RewritePreviewResult,
  type RewriteRule,
  type RewriteRules,
  type RewriteSection,
} from "@/lib/channels";
import { cn } from "@/lib/utils";

/** 预览接口目前只会返回 stream；未知值不直接透出英文原值。 */
const PREVIEW_SKIP_LABEL: Record<string, string> = {
  stream: "流式响应不参与改写",
};

const PROTECTED_HEADERS = new Set([
  "authorization",
  "api-key",
  "x-api-key",
  "cookie",
  "set-cookie",
]);
const UNSAFE_RESPONSE_HEADERS = new Set([
  "content-length",
  "transfer-encoding",
  "connection",
  "content-encoding",
]);

const BODY_MODES = [
  { value: "create", label: "新增字段", hint: "路径不存在时写入；已存在则跳过" },
  { value: "update", label: "修改字段", hint: "路径存在时覆盖；不存在则跳过" },
  { value: "delete", label: "删除字段", hint: "路径不存在视为成功 no-op" },
  { value: "copy", label: "复制字段", hint: "从源路径复制到目标路径" },
  { value: "prepend", label: "追加开头", hint: "在字符串开头插入文本" },
  { value: "append", label: "追加结尾", hint: "在字符串结尾追加文本" },
  { value: "regex_replace", label: "正则替换", hint: "按正则替换字符串内容" },
  { value: "to_upper", label: "转大写", hint: "把字符串转为大写" },
  { value: "to_lower", label: "转小写", hint: "把字符串转为小写" },
] as const;

const CONDITION_MODES: Array<{ value: ConditionMode; label: string }> = [
  { value: "full", label: "等于" },
  { value: "regex", label: "正则匹配" },
  { value: "gt", label: "大于" },
  { value: "gte", label: "大于等于" },
  { value: "lt", label: "小于" },
  { value: "lte", label: "小于等于" },
];

const SECTION_META: Array<{
  key: RewriteSection;
  label: string;
  hint: string;
  header: boolean;
  modes: Array<{ value: string; label: string }>;
}> = [
  {
    key: "request_headers",
    label: "请求头",
    hint: "发往上游的请求头支持 set / delete / rename；名称不区分大小写。",
    header: true,
    modes: [
      { value: "set", label: "设置 / 覆盖" },
      { value: "delete", label: "删除" },
      { value: "rename", label: "重命名" },
    ],
  },
  {
    key: "request_body",
    label: "请求体",
    hint: "在门面写入 model / messages / stream 之前改写 JSON 请求体。",
    header: false,
    modes: [...BODY_MODES],
  },
  {
    key: "response_headers",
    label: "响应头",
    hint: "对上游返回的响应头做 set / delete；content-length 等传输头不可改写。",
    header: true,
    modes: [
      { value: "set", label: "设置 / 覆盖" },
      { value: "delete", label: "删除" },
    ],
  },
  {
    key: "response_body",
    label: "响应体",
    hint: "仅对非流式 2xx 响应执行；流式请求跳过响应体改写，并记录跳过原因（rewrite_skipped）。",
    header: false,
    modes: [...BODY_MODES],
  },
];

const BODY_MODE_VALUES = new Set<string>(BODY_MODES.map((item) => item.value));
const CONDITION_MODE_VALUES = new Set<string>(CONDITION_MODES.map((item) => item.value));

function cloneRules(rules: RewriteRules | undefined): RewriteRules {
  const result: RewriteRules = {};
  for (const section of [
    "request_headers",
    "request_body",
    "response_headers",
    "response_body",
  ] as const) {
    const items = rules?.[section];
    if (Array.isArray(items)) {
      result[section] = items.map((rule) => ({
        ...rule,
        conditions: rule.conditions?.map((condition) => ({ ...condition })),
      }));
    }
  }
  return result;
}

function rulesFor(rules: RewriteRules, section: RewriteSection): RewriteRule[] {
  return rules[section] ?? [];
}

function sectionMeta(section: RewriteSection) {
  return SECTION_META.find((item) => item.key === section) ?? SECTION_META[0];
}

function emptyRule(section: RewriteSection): RewriteRule {
  return sectionMeta(section).header
    ? { mode: "set", path: "", value: "" }
    : { mode: "create", path: "", value: "" };
}

function parseValue(input: string): unknown {
  const trimmed = input.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    return input;
  }
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function isLegacyRule(rule: RewriteRule, section: RewriteSection): boolean {
  const mode = String(rule.mode ?? "");
  if (sectionMeta(section).header) {
    return !sectionMeta(section).modes.some((item) => item.value === mode);
  }
  return !BODY_MODE_VALUES.has(mode);
}

function isLegacyCondition(condition: RewriteCondition): boolean {
  return !CONDITION_MODE_VALUES.has(String(condition.mode ?? ""));
}

function isLegacyRules(rules: RewriteRules): number {
  let count = 0;
  for (const section of SECTION_META) {
    for (const rule of rulesFor(rules, section.key)) {
      if (isLegacyRule(rule, section.key)) count += 1;
      count += (rule.conditions ?? []).filter(isLegacyCondition).length;
    }
  }
  return count;
}

function protectedHeaderError(
  name: string | undefined,
  kind: "请求头" | "响应头" = "请求头",
): string | null {
  const clean = (name ?? "").trim();
  if (!clean) return `请填写${kind}名称`;
  if (PROTECTED_HEADERS.has(clean.toLowerCase())) {
    return `凭证类${kind} ${clean} 不能由规则覆盖、删除或改名`;
  }
  return null;
}

function ruleProblem(rule: RewriteRule, section: RewriteSection): string | null {
  const mode = String(rule.mode ?? "");
  const meta = sectionMeta(section);
  if (isLegacyRule(rule, section)) {
    return `旧规则不再支持动作 ${mode || "(空)"}，请改用新的 9 个动作。`;
  }
  if (meta.header) {
    const kind = section === "request_headers" ? "请求头" : "响应头";
    const clean = (rule.path ?? "").trim().toLowerCase();
    if (section === "response_headers" && UNSAFE_RESPONSE_HEADERS.has(clean)) {
      return `传输类响应头 ${clean} 不能由规则改写`;
    }
    const source = protectedHeaderError(rule.path, kind);
    if (source) return source;
    if (section === "request_headers" && mode === "rename") {
      const target = protectedHeaderError(rule.to, kind);
      if (target) return target;
    }
    return null;
  }

  if (mode === "copy") {
    if (!(rule.from ?? "").trim()) return "复制动作需要填写源路径";
    if (!(rule.to ?? "").trim()) return "复制动作需要填写目标路径";
    if (rule.from === rule.to) return "复制动作的源路径和目标路径不能相同";
  } else if (!(rule.path ?? "").trim()) {
    return "请填写路径";
  }

  if (mode === "create" || mode === "update") {
    if (rule.value === undefined) return "请填写要写入的值";
  }
  if (mode === "prepend" || mode === "append") {
    if (typeof rule.value !== "string") return "追加动作的值必须是字符串";
  }
  if (mode === "regex_replace") {
    const pattern = rule.pattern ?? "";
    if (!pattern) return "正则替换需要填写正则";
    try {
      new RegExp(pattern);
    } catch {
      return "替换正则不合法";
    }
  }

  for (const [index, condition] of (rule.conditions ?? []).entries()) {
    const conditionMode = String(condition.mode ?? "full");
    if (isLegacyCondition(condition)) {
      return `条件 ${index + 1} 的旧模式 ${conditionMode} 不再支持，请改用 6 种新条件。`;
    }
    if (!(condition.path ?? "").trim()) return `条件 ${index + 1} 缺少路径`;
    if (conditionMode === "regex") {
      try {
        new RegExp(String(condition.value ?? ""));
      } catch {
        return `条件 ${index + 1} 的正则不合法`;
      }
    }
  }
  return null;
}

interface RewriteRulesEditorProps {
  channelId: string;
  rules: RewriteRules | undefined;
  value?: RewriteRules;
  onChange?: (rules: RewriteRules) => void;
}

export function RewriteRulesEditor({
  channelId,
  rules,
  value,
  onChange,
}: RewriteRulesEditorProps) {
  const queryClient = useQueryClient();
  const controlled = Boolean(onChange);
  const lastEmitted = useRef("");
  const [section, setSection] = useState<RewriteSection>("request_headers");
  const [activeRule, setActiveRule] = useState(0);
  const [draft, setDraft] = useState<RewriteRules>(() => cloneRules(value ?? rules));
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewProblem, setPreviewProblem] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<RewritePreviewResult | null>(null);
  const [previewBody, setPreviewBody] = useState(
    JSON.stringify(
      {
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: "你好" }],
        temperature: 0.7,
        stream: false,
      },
      null,
      2,
    ),
  );
  const [previewHeaders, setPreviewHeaders] = useState(
    JSON.stringify({ "content-type": "application/json", "x-client": "slipstream" }, null, 2),
  );

  useEffect(() => {
    const incoming = cloneRules(value ?? rules);
    const serialized = JSON.stringify(incoming);
    if (serialized === lastEmitted.current) return;
    lastEmitted.current = serialized;
    setDraft(incoming);
  }, [rules, value]);

  const commit = (next: RewriteRules) => {
    setDraft(next);
    if (onChange) {
      lastEmitted.current = JSON.stringify(next);
      onChange(next);
    }
  };

  const mutation = useMutation({
    mutationFn: (next: RewriteRules) => updateChannel(channelId, { rewrite_rules: next }),
    onSuccess: async (channel) => {
      setDraft(cloneRules(channel.rewrite_rules));
      setProblem(null);
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
    },
    onError: (error) => setProblem(describeApiError(error)),
  });

  const meta = sectionMeta(section);
  const items = rulesFor(draft, section);
  const legacyCount = isLegacyRules(draft);

  const setItems = (next: RewriteRule[]) => {
    setSaved(false);
    setPreviewResult(null);
    commit({ ...draft, [section]: next });
  };

  const addRule = () => {
    setItems([...items, emptyRule(section)]);
    setActiveRule(items.length);
  };

  const removeRule = (index: number) => {
    setItems(items.filter((_, i) => i !== index));
    setActiveRule(Math.max(0, Math.min(index, items.length - 2)));
  };

  const selectSection = (key: RewriteSection) => {
    setSection(key);
    setActiveRule(0);
    setProblem(null);
    setPreviewResult(null);
    setPreviewProblem(null);
  };

  const moveRule = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    [next[index], next[target]] = [next[target], next[index]];
    setItems(next);
    setActiveRule(target);
  };

  const updateRule = (index: number, patch: Partial<RewriteRule>) => {
    setItems(items.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
  };

  const changeMode = (index: number, mode: string) => {
    const rule = items[index];
    const keep: Partial<RewriteRule> = {
      mode: mode as RewriteRule["mode"],
      conditions: rule.conditions,
      logic: rule.logic,
      note: rule.note,
    };
    let next: RewriteRule;
    if (meta.header) {
      next = { ...keep, mode: mode as RewriteRule["mode"], path: rule.path ?? "" } as RewriteRule;
      if (mode === "set") next.value = rule.value ?? "";
      if (mode === "rename") next.to = rule.to ?? "";
    } else if (mode === "copy") {
      next = {
        ...keep,
        mode: "copy",
        from: rule.from ?? rule.path ?? "",
        to: rule.to ?? "",
      } as RewriteRule;
    } else if (mode === "regex_replace") {
      next = {
        ...keep,
        mode: "regex_replace",
        path: rule.path ?? rule.from ?? "",
        pattern: rule.pattern ?? "",
        replacement: rule.replacement ?? "",
      } as RewriteRule;
    } else if (mode === "prepend" || mode === "append") {
      next = {
        ...keep,
        mode: mode as RewriteRule["mode"],
        path: rule.path ?? rule.from ?? "",
        value: typeof rule.value === "string" ? rule.value : "",
      } as RewriteRule;
    } else if (mode === "delete" || mode === "to_upper" || mode === "to_lower") {
      next = {
        ...keep,
        mode: mode as RewriteRule["mode"],
        path: rule.path ?? rule.from ?? "",
      } as RewriteRule;
    } else {
      next = {
        ...keep,
        mode: mode as RewriteRule["mode"],
        path: rule.path ?? rule.from ?? "",
        value: rule.value ?? "",
      } as RewriteRule;
    }
    setItems(items.map((rule, i) => (i === index ? next : rule)));
  };

  const addCondition = (index: number) => {
    const conditions = items[index].conditions ?? [];
    updateRule(index, {
      conditions: [...conditions, { path: "model", mode: "full", value: "" }],
    });
  };

  const updateCondition = (
    index: number,
    conditionIndex: number,
    patch: Partial<RewriteCondition>,
  ) => {
    const conditions = (items[index].conditions ?? []).map((condition, i) =>
      i === conditionIndex ? { ...condition, ...patch } : condition,
    );
    updateRule(index, { conditions });
  };

  const removeCondition = (index: number, conditionIndex: number) => {
    const conditions = (items[index].conditions ?? []).filter((_, i) => i !== conditionIndex);
    updateRule(index, { conditions: conditions.length ? conditions : undefined });
  };

  const save = () => {
    setSaved(false);
    if (legacyCount > 0) {
      setProblem(`检测到 ${legacyCount} 条不兼容旧规则，保存前必须修正。`);
      return;
    }
    for (const item of SECTION_META) {
      const list = rulesFor(draft, item.key);
      for (let index = 0; index < list.length; index += 1) {
        const issue = ruleProblem(list[index], item.key);
        if (issue) {
          setSection(item.key);
          setActiveRule(index);
          setProblem(`规则 ${index + 1}：${issue}`);
          return;
        }
      }
    }
    setProblem(null);
    mutation.mutate(draft);
  };

  const openJson = () => {
    setJsonText(JSON.stringify(draft, null, 2));
    setProblem(null);
    setJsonMode(true);
  };

  const applyJson = () => {
    try {
      const parsed = JSON.parse(jsonText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setProblem("JSON 顶层必须是对象。");
        return;
      }
      const next: RewriteRules = {};
      for (const item of SECTION_META) {
        const raw = (parsed as Record<string, unknown>)[item.key];
        if (raw === undefined) continue;
        if (!Array.isArray(raw)) {
          setProblem(`${item.label} 必须是规则数组。`);
          return;
        }
        next[item.key] = raw as RewriteRule[];
      }
      commit(next);
      setSaved(false);
      setProblem(null);
      setJsonMode(false);
    } catch (error) {
      setProblem(`JSON 解析失败：${error instanceof Error ? error.message : "格式错误"}`);
    }
  };

  const runPreview = async () => {
    setPreviewLoading(true);
    setPreviewProblem(null);
    setPreviewResult(null);
    try {
      const isHeader = section.endsWith("_headers");
      const parsed = JSON.parse(isHeader ? previewHeaders : previewBody) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(isHeader ? "请求头必须是 JSON 对象。" : "请求体必须是 JSON 对象。");
      }
      const result = await previewRewriteRules(channelId, {
        section,
        rules: draft,
        ...(isHeader
          ? { headers: parsed as Record<string, string> }
          : { body: parsed as Record<string, unknown> }),
      });
      setPreviewResult(result);
    } catch (error) {
      setPreviewProblem(error instanceof Error ? error.message : "试运行失败");
    } finally {
      setPreviewLoading(false);
    }
  };

  const active = items[Math.min(activeRule, Math.max(items.length - 1, 0))];
  const activeIndex = items.length ? Math.min(activeRule, items.length - 1) : 0;

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-title">改写规则</h2>
            <p className="mt-1 max-w-2xl text-xs leading-6 text-muted-foreground">
              规则按顺序执行，分为请求头 / 请求体 / 响应头 / 响应体四段。响应体只改写非流式
              2xx；流式请求会明确跳过并记录原因。凭证类请求头
              <span className="mx-1 font-mono text-2xs">
                Authorization / api-key / x-api-key / cookie / set-cookie
              </span>
              永远不能被覆盖、删除或改名。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => (jsonMode ? setJsonMode(false) : openJson())}>
              <Braces className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              {jsonMode ? "可视化模式" : "JSON 模式"}
            </Button>
            <Button variant="outline" onClick={() => setPreviewOpen((open) => !open)}>
              <Play className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              试运行
            </Button>
            <Button onClick={addRule} disabled={jsonMode}>
              <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              添加规则
            </Button>
          </div>
        </div>

        {legacyCount > 0 ? (
          <Alert variant="warning" className="mt-4">
            <span className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
              检测到 {legacyCount} 条不兼容旧规则，保存前必须修正；不会自动迁移或删除。
            </span>
          </Alert>
        ) : null}

        <div className="mt-5 grid gap-4 lg:grid-cols-[230px_minmax(0,1fr)]">
          <nav
            aria-label="改写规则导航"
            className="min-w-0 self-start rounded-control border border-border bg-surface-2 p-2 lg:max-h-180 lg:overflow-y-auto"
          >
            {SECTION_META.map((item) => {
              const sectionRules = rulesFor(draft, item.key);
              return (
                <div key={item.key} className="mb-2 last:mb-0">
                  <button
                    type="button"
                    aria-current={section === item.key ? "true" : undefined}
                    onClick={() => selectSection(item.key)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-xs font-semibold transition-colors",
                      section === item.key
                        ? "bg-primary-soft text-primary"
                        : "text-title hover:bg-surface",
                    )}
                  >
                    <span>{item.label}</span>
                    <span className="rounded-full bg-surface px-1.5 text-2xs tabular-nums text-muted-foreground">
                      {sectionRules.length}
                    </span>
                  </button>
                  {section === item.key ? (
                    <p className="px-2.5 pb-2 pt-1 text-2xs leading-4 text-muted-foreground">{item.hint}</p>
                  ) : null}
                </div>
              );
            })}
          </nav>

          <div className="min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-2sm font-semibold text-title">{meta.label}规则</h3>
              <span className="text-2xs text-muted-foreground">按列表顺序执行</span>
            </div>

            {items.length === 0 ? (
              <div className="mt-3 rounded-control border border-dashed border-border bg-surface-2 px-4 py-8 text-center">
                <p className="text-2sm text-muted-foreground">这个作用段还没有规则。</p>
                <Button variant="outline" size="sm" className="mt-3" onClick={addRule}>
                  <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  添加第一条规则
                </Button>
              </div>
            ) : (
              <>
                <ol className="mt-3 space-y-2">
                  {items.map((rule, index) => {
                    const legacy = isLegacyRule(rule, section);
                    const actionLabel =
                      meta.modes.find((mode) => mode.value === String(rule.mode))?.label ??
                      (legacy ? `旧动作：${String(rule.mode)}` : String(rule.mode));
                    return (
                      <li
                        key={index}
                        className={cn(
                          "rounded-control border bg-surface transition-colors",
                          index === activeIndex ? "border-primary" : "border-border",
                        )}
                      >
                        <div className="flex items-center gap-1 px-2 py-1.5">
                          <button
                            type="button"
                            onClick={() => setActiveRule(index)}
                            className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-left"
                          >
                            <span className="shrink-0 text-2xs font-semibold tabular-nums text-muted-foreground">
                              #{index + 1}
                            </span>
                            <span className={cn("truncate text-xs", legacy ? "text-warning-text" : "text-title")}>
                              {actionLabel}
                              {rule.path || rule.from ? (
                                <span className="ml-1 font-mono text-2xs font-normal text-muted-foreground">
                                  → {rule.path || rule.from}
                                </span>
                              ) : null}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={() => moveRule(index, -1)}
                            disabled={index === 0}
                            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-title disabled:opacity-30"
                            aria-label={`上移规则 ${index + 1}`}
                          >
                            <ArrowUp className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveRule(index, 1)}
                            disabled={index === items.length - 1}
                            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-title disabled:opacity-30"
                            aria-label={`下移规则 ${index + 1}`}
                          >
                            <ArrowDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            onClick={() => removeRule(index)}
                            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-danger-soft hover:text-danger-text"
                            aria-label={`删除规则 ${index + 1}`}
                          >
                            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ol>

                {active ? (
                  <RuleEditor
                    rule={active}
                    index={activeIndex}
                    section={section}
                    meta={meta}
                    onChange={(patch) => updateRule(activeIndex, patch)}
                    onModeChange={(mode) => changeMode(activeIndex, mode)}
                    onAddCondition={() => addCondition(activeIndex)}
                    onUpdateCondition={(conditionIndex, patch) =>
                      updateCondition(activeIndex, conditionIndex, patch)
                    }
                    onRemoveCondition={(conditionIndex) =>
                      removeCondition(activeIndex, conditionIndex)
                    }
                  />
                ) : null}
              </>
            )}
          </div>
        </div>

        {previewOpen ? (
          <div className="mt-5 rounded-control border border-border bg-surface-2 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-2sm font-semibold text-title">试运行</h3>
              <span className="text-2xs text-muted-foreground">{meta.label} · 不会保存或调用上游</span>
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <div className="lg:col-span-2">
                <p className="text-2xs text-muted-foreground">
                  输入一段样例 JSON，点击“运行”查看规则命中、字段变化和跳过原因。
                  {section === "response_body" ? " 流式样例会跳过响应体改写。" : ""}
                </p>
              </div>
              <Textarea
                aria-label={section.endsWith("_headers") ? "试运行请求头" : "试运行请求体"}
                className="min-h-45 w-full resize-y rounded-control border-border-strong bg-surface px-3 py-2.5 font-mono text-2xs leading-5 text-title focus-visible:border-primary focus-visible:outline-hidden md:text-2xs"
                value={section.endsWith("_headers") ? previewHeaders : previewBody}
                onChange={(event) =>
                  section.endsWith("_headers")
                    ? setPreviewHeaders(event.target.value)
                    : setPreviewBody(event.target.value)
                }
                spellCheck={false}
              />
              <div className="min-w-0">
                <Button onClick={runPreview} disabled={previewLoading}>
                  {previewLoading ? (
                    <Spinner aria-hidden="true" />
                  ) : (
                    <Play className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  )}
                  运行
                </Button>
                {previewProblem ? (
                  <p className="mt-3 rounded-control bg-danger-soft px-3 py-2 text-xs text-danger-text">
                    {previewProblem}
                  </p>
                ) : null}
                {previewResult ? (
                  <div className="mt-3 space-y-2">
                    {previewResult.warnings.length ? (
                      <div className="rounded-control bg-warning-soft px-3 py-2 text-2xs text-warning-text">
                        {previewResult.warnings.map((warning) => (
                          <p key={warning}>{warning}</p>
                        ))}
                      </div>
                    ) : null}
                    {previewResult.skipped ? (
                      <p className="rounded-control bg-warning-soft px-3 py-2 text-2xs text-warning-text">
                        跳过原因：{PREVIEW_SKIP_LABEL[previewResult.skipped] ?? "—"}
                      </p>
                    ) : null}
                    <PreviewTable result={previewResult} rules={items} />
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}

        {problem ? (
          <Alert variant="destructive" className="mt-3">
            <span className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
              {problem}
            </span>
          </Alert>
        ) : null}
        {saved && !problem ? (
          <Alert variant="success" className="mt-3">
            规则已保存。
          </Alert>
        ) : null}

        {jsonMode ? (
          <div className="mt-4 space-y-3">
            <Textarea
              aria-label="改写规则 JSON"
              className="min-h-90 w-full resize-y rounded-control border-border-strong bg-surface px-3 py-2.5 font-mono text-2xs leading-5 text-title focus-visible:border-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring md:text-2xs"
              value={jsonText}
              onChange={(event) => setJsonText(event.target.value)}
              spellCheck={false}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-2xs text-muted-foreground">
                顶层支持 request_headers / request_body / response_headers / response_body 四个数组。
              </p>
              <div className="flex items-center gap-2">
                <Button variant="ghost" onClick={() => setJsonMode(false)}>
                  取消
                </Button>
                <Button onClick={applyJson}>应用 JSON</Button>
              </div>
            </div>
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
          <p className="text-2xs text-muted-foreground">
            {controlled ? "规则会随渠道表单一起保存。" : "保存后立即对后续请求生效。"}
          </p>
          <Button
            onClick={save}
            disabled={mutation.isPending || legacyCount > 0}
            aria-describedby={legacyCount > 0 ? "rewrite-save-blocked" : undefined}
          >
            {mutation.isPending ? (
              <Spinner aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            保存规则
          </Button>
        </div>
        {legacyCount > 0 ? (
          <p id="rewrite-save-blocked" className="mt-2 text-right text-2xs text-warning-text">
            存在不兼容旧规则，修正后才能保存。
          </p>
        ) : null}
      </Card>
    </div>
  );
}

function RuleEditor({
  rule,
  index,
  section,
  meta,
  onChange,
  onModeChange,
  onAddCondition,
  onUpdateCondition,
  onRemoveCondition,
}: {
  rule: RewriteRule;
  index: number;
  section: RewriteSection;
  meta: (typeof SECTION_META)[number];
  onChange: (patch: Partial<RewriteRule>) => void;
  onModeChange: (mode: string) => void;
  onAddCondition: () => void;
  onUpdateCondition: (index: number, patch: Partial<RewriteCondition>) => void;
  onRemoveCondition: (index: number) => void;
}) {
  const mode = String(rule.mode ?? "");
  const legacy = isLegacyRule(rule, section);
  const isCopy = mode === "copy";
  const isRegex = mode === "regex_replace";
  const needsValue =
    mode === "create" || mode === "update" || mode === "set";
  const isTextAppend = mode === "prepend" || mode === "append";
  const conditions = rule.conditions ?? [];

  return (
    <div className="mt-3 rounded-control border border-border bg-surface p-3">
      {legacy ? (
        <p className="mb-3 rounded-control bg-warning-soft px-3 py-2 text-2xs text-warning-text">
          这条规则使用了已废弃的动作 {mode}，请改成新动作后再保存。
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="block text-2xs text-muted-foreground">
          <span className="block">规则 {index + 1} 模式</span>
          <Select value={mode} onValueChange={onModeChange}>
            <SelectTrigger
              aria-label={`规则 ${index + 1} 模式`}
              className="mt-1 h-8 w-full rounded-control border-border-strong bg-surface px-2 text-xs text-title"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {legacy ? <SelectItem value={mode}>旧动作：{mode}</SelectItem> : null}
              {meta.modes.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="block text-2xs text-muted-foreground">
          规则 {index + 1} 备注
          <Input
            aria-label={`规则 ${index + 1} 备注`}
            className="mt-1 h-8 text-xs"
            value={rule.note ?? ""}
            onChange={(event) => onChange({ note: event.target.value || undefined })}
            placeholder="仅编辑器可见"
          />
        </label>

        {isCopy ? (
          <>
            <label className="block text-2xs text-muted-foreground">
              规则 {index + 1} 源路径
              <Input
                aria-label={`规则 ${index + 1} 源路径`}
                className="mt-1 h-8 font-mono text-xs"
                value={rule.from ?? ""}
                onChange={(event) => onChange({ from: event.target.value })}
                placeholder="messages.0.content"
              />
            </label>
            <label className="block text-2xs text-muted-foreground">
              规则 {index + 1} 目标路径
              <Input
                aria-label={`规则 ${index + 1} 目标路径`}
                className="mt-1 h-8 font-mono text-xs"
                value={rule.to ?? ""}
                onChange={(event) => onChange({ to: event.target.value })}
                placeholder="messages.1.content"
              />
            </label>
          </>
        ) : (
          <label className="block text-2xs text-muted-foreground">
            规则 {index + 1} 路径
            <Input
              aria-label={`规则 ${index + 1} 路径`}
              className="mt-1 h-8 font-mono text-xs"
              value={rule.path ?? ""}
              onChange={(event) => onChange({ path: event.target.value })}
              placeholder="messages.0.content"
            />
          </label>
        )}

        {needsValue ? (
          <label className="block text-2xs text-muted-foreground">
            规则 {index + 1} 值
            <Input
              aria-label={`规则 ${index + 1} 值`}
              className="mt-1 h-8 font-mono text-xs"
              value={formatValue(rule.value)}
              onChange={(event) => onChange({ value: parseValue(event.target.value) })}
              placeholder="文本，或 JSON（对象 / 数组 / 数字 / 布尔）"
            />
          </label>
        ) : null}

        {isTextAppend ? (
          <label className="block text-2xs text-muted-foreground">
            规则 {index + 1} 值
            <Input
              aria-label={`规则 ${index + 1} 值`}
              className="mt-1 h-8 font-mono text-xs"
              value={String(rule.value ?? "")}
              onChange={(event) => onChange({ value: event.target.value })}
              placeholder="要追加的文本"
            />
          </label>
        ) : null}

        {isRegex ? (
          <>
            <label className="block text-2xs text-muted-foreground">
              规则 {index + 1} 正则
              <Input
                aria-label={`规则 ${index + 1} 正则`}
                className="mt-1 h-8 font-mono text-xs"
                value={rule.pattern ?? ""}
                onChange={(event) => onChange({ pattern: event.target.value })}
                placeholder="\\s+"
              />
            </label>
            <label className="block text-2xs text-muted-foreground">
              规则 {index + 1} 替换文本
              <Input
                aria-label={`规则 ${index + 1} 替换文本`}
                className="mt-1 h-8 font-mono text-xs"
                value={rule.replacement ?? ""}
                onChange={(event) => onChange({ replacement: event.target.value })}
                placeholder="用空格替换空白"
              />
            </label>
          </>
        ) : null}

        {section === "request_headers" && mode === "rename" ? (
          <label className="block text-2xs text-muted-foreground">
            规则 {index + 1} 目标名称
            <Input
              aria-label={`规则 ${index + 1} 目标名称`}
              className="mt-1 h-8 font-mono text-xs"
              value={rule.to ?? ""}
              onChange={(event) => onChange({ to: event.target.value })}
              placeholder="X-New-Name"
            />
          </label>
        ) : null}
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-2xs font-medium text-title">条件</span>
            <Select
              value={rule.logic ?? "AND"}
              onValueChange={(value) => onChange({ logic: value as "AND" | "OR" })}
            >
              <SelectTrigger
                aria-label={`规则 ${index + 1} 条件逻辑`}
                className="h-8 w-auto gap-1 rounded-badge border-border-strong bg-surface px-2 text-2xs text-title"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="AND">全部满足（AND）</SelectItem>
                <SelectItem value="OR">任一满足（OR）</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button variant="outline" size="sm" onClick={onAddCondition}>
            <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            添加条件
          </Button>
        </div>
        {conditions.length === 0 ? (
          <p className="mt-2 text-2xs text-muted-foreground">没有条件时总是执行。</p>
        ) : (
          <div className="mt-2 space-y-2">
            {conditions.map((condition, conditionIndex) => (
              <div
                key={conditionIndex}
                className="grid gap-2 rounded-control border border-border bg-surface-2 p-2.5 sm:grid-cols-2 lg:grid-cols-[1fr_120px_1fr_auto]"
              >
                <Input
                  aria-label={`条件 ${conditionIndex + 1} 路径`}
                  className="h-8 font-mono text-xs"
                  value={condition.path}
                  onChange={(event) =>
                    onUpdateCondition(conditionIndex, { path: event.target.value })
                  }
                  placeholder="model"
                />
                <Select
                  value={String(condition.mode)}
                  onValueChange={(value) =>
                    onUpdateCondition(conditionIndex, {
                      mode: value as ConditionMode,
                    })
                  }
                >
                  <SelectTrigger
                    aria-label={`条件 ${conditionIndex + 1} 模式`}
                    className="h-8 w-full rounded-control border-border-strong bg-surface px-2 text-xs text-title"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {isLegacyCondition(condition) ? (
                      <SelectItem value={String(condition.mode)}>
                        旧条件：{String(condition.mode)}
                      </SelectItem>
                    ) : null}
                    {CONDITION_MODES.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  aria-label={`条件 ${conditionIndex + 1} 值`}
                  className="h-8 font-mono text-xs"
                  value={formatValue(condition.value)}
                  onChange={(event) =>
                    onUpdateCondition(conditionIndex, { value: parseValue(event.target.value) })
                  }
                  placeholder="比较值"
                />
                <button
                  type="button"
                  onClick={() => onRemoveCondition(conditionIndex)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-danger-soft hover:text-danger-text"
                  aria-label={`删除条件 ${conditionIndex + 1}`}
                >
                  <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                </button>
                <label className="flex items-center gap-1.5 text-2xs text-muted-foreground">
                  <Checkbox
                    aria-label={`条件 ${conditionIndex + 1} 取反`}
                    checked={Boolean(condition.invert)}
                    onCheckedChange={(checked) =>
                      onUpdateCondition(conditionIndex, { invert: checked === true || undefined })
                    }
                  />
                  取反
                </label>
                <label className="flex items-center gap-1.5 text-2xs text-muted-foreground">
                  <Checkbox
                    aria-label={`条件 ${conditionIndex + 1} 缺失通过`}
                    checked={Boolean(condition.pass_missing_key)}
                    onCheckedChange={(checked) =>
                      onUpdateCondition(conditionIndex, {
                        pass_missing_key: checked === true || undefined,
                      })
                    }
                  />
                  缺失通过
                </label>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PreviewTable({
  result,
  rules,
}: {
  result: RewritePreviewResult;
  rules: RewriteRule[];
}) {
  const ruleResults = result.rule_results ?? [];
  const rows = ruleResults.map((item) => {
    const change = result.diff.find((entry) => entry.path === item.path);
    const rule = rules[item.index];
    return {
      key: `${item.index}-${item.path}-${item.reason}`,
      index: item.index + 1,
      mode: item.mode,
      path: item.path,
      before: change?.before,
      after: change?.after,
      condition: rule?.conditions?.length
        ? `${rule.logic ?? "AND"} · ${rule.conditions.length} 条`
        : "无条件",
      outcome: item.reason || "已执行",
    };
  });
  const matchedPaths = new Set(ruleResults.map((item) => item.path));
  const extraRows = result.diff
    .filter((change) => !matchedPaths.has(change.path))
    .map((change, index) => ({
      key: `diff-${change.path}-${index}`,
      index: "—",
      mode: "—",
      path: change.path,
      before: change.before,
      after: change.after,
      condition: "—",
      outcome: "字段变化",
    }));
  const allRows = [...rows, ...extraRows];

  if (allRows.length === 0) {
    return (
      <p className="rounded-control bg-surface px-3 py-2 text-2xs text-muted-foreground">
        没有字段变化（规则可能未命中或无操作）。
      </p>
    );
  }

  return (
    <div className="rounded-control border border-border bg-surface [&>div]:max-h-90">
      <Table className="min-w-160 text-left text-2xs">
        <TableHeader className="sticky top-0 bg-surface-2 text-2xs uppercase tracking-[0.08em] text-muted-foreground">
          <TableRow>
            <TableHead className="px-2 py-2 font-semibold">规则</TableHead>
            <TableHead className="px-2 py-2 font-semibold">动作</TableHead>
            <TableHead className="px-2 py-2 font-semibold">路径</TableHead>
            <TableHead className="px-2 py-2 font-semibold">原值</TableHead>
            <TableHead className="px-2 py-2 font-semibold">新值</TableHead>
            <TableHead className="px-2 py-2 font-semibold">条件</TableHead>
            <TableHead className="px-2 py-2 font-semibold">结果</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="divide-y divide-border">
          {allRows.map((row) => (
            <TableRow key={row.key}>
              <TableCell className="px-2 py-1.5 tabular-nums text-muted-foreground">{row.index}</TableCell>
              <TableCell className="px-2 py-1.5 font-mono text-title">{row.mode}</TableCell>
              <TableCell className="max-w-45 break-all px-2 py-1.5 font-mono text-title">{row.path || "—"}</TableCell>
              <TableCell className="max-w-32 break-all px-2 py-1.5 font-mono text-muted-foreground">
                {row.before === undefined ? "—" : JSON.stringify(row.before)}
              </TableCell>
              <TableCell className="max-w-32 break-all px-2 py-1.5 font-mono text-title">
                {row.after === undefined ? "—" : JSON.stringify(row.after)}
              </TableCell>
              <TableCell className="px-2 py-1.5 text-muted-foreground">{row.condition}</TableCell>
              <TableCell className="px-2 py-1.5 text-muted-foreground">{row.outcome}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
