import { ApiError, apiRequest } from "@/lib/api";
import type {
  InputType,
  ModelCapability,
  ModelType,
  OutputType,
} from "@/lib/channel-models";

/**
 * 新建模型的默认参数预填（ADR-073 决策 7）。
 *
 * 端点：POST /api/channel-models/prefill（管理员）——只按 `upstream_model` 匹配，
 * 只读已拉取的 models.dev 快照、不触发刷新；没有快照时 200 + matched=false，
 * 绝不 5xx，所以预填失败永远不阻塞创建。
 */

/** 决策 3 的 7 级匹配管线，后端回传的命中级别。 */
export type PrefillMatchLevel =
  | "exact"
  | "normalized"
  | "prefix_stripped"
  | "version_loose"
  | "alias"
  | "fuzzy_auto"
  | "fuzzy_review"
  | "unmatched";

export const PREFILL_MATCH_LABELS: Record<PrefillMatchLevel, string> = {
  exact: "精确匹配",
  normalized: "归一化匹配",
  prefix_stripped: "去前缀匹配",
  version_loose: "版本后缀宽松匹配",
  alias: "别名匹配",
  fuzzy_auto: "模糊自动匹配",
  fuzzy_review: "模糊待复核",
  unmatched: "未匹配",
};

/** 预填参数：null 一律表示「该项不预填」，不是「填 0」。 */
export interface PrefillParameters {
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  reasoning_level: string | null;
  input_types: InputType[] | null;
  output_types: OutputType[] | null;
  tool_call: boolean | null;
  reasoning: boolean | null;
  attachment: boolean | null;
  structured_output: boolean | null;
}

/** 可直接进表单的默认值；键名与 ChannelModelCreate 同名。 */
export interface PrefillDefaults {
  model_type: ModelType | null;
  model_type_source: string | null;
  parameters: PrefillParameters;
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  cache_read_price_per_1m: number | null;
  cache_write_price_per_1m: number | null;
}

/** 逐字段来源（只展示、不落库；D2 裁定）。 */
export interface PrefillFieldSource {
  provider: string;
  providers: string[];
  rule: string;
}

export interface PrefillProvenance {
  model_id: string;
  canonical_model_id: string | null;
  provider: string;
  matched_by: string;
  rule_version: string;
  pulled_at: string;
}

export interface PrefillSnapshot {
  available: boolean;
  pulled_at: string | null;
  age_days: number | null;
  stale: boolean;
}

export interface PrefillCandidate {
  provider: string;
  model_id: string;
  score: number;
  tier: string;
}

export interface ModelPrefill {
  upstream_model: string;
  matched: boolean;
  matched_by: PrefillMatchLevel;
  version_loose: boolean;
  defaults: PrefillDefaults | null;
  sources: Record<string, PrefillFieldSource>;
  provenance: PrefillProvenance | null;
  snapshot: PrefillSnapshot;
  /** 未命中 / 待复核时的候选，最多 5 条。 */
  candidates: PrefillCandidate[];
  review_required: boolean;
}

export const PREFILL_ENDPOINT = "/api/channel-models/prefill";
export const PREFILL_CANDIDATE_LIMIT = 5;

/**
 * 取新建模型的预填默认值。
 * 返回 null 表示「这条路径拿不到默认值」（端点未上线 / 服务端异常）：调用方静默跳过预填。
 */
export async function fetchModelPrefill(upstreamModel: string): Promise<ModelPrefill | null> {
  const model = upstreamModel.trim();
  if (!model) return null;
  try {
    return await apiRequest<ModelPrefill>(PREFILL_ENDPOINT, {
      method: "POST",
      body: JSON.stringify({ upstream_model: model }),
    });
  } catch (error) {
    // 预填只是草稿便利：404/405（端点未上线）与 5xx（契约要求永不发生，兜底）都降级为空结果。
    if (error instanceof ApiError && (error.status === 404 || error.status === 405 || error.status >= 500)) {
      return null;
    }
    throw error;
  }
}

/** 表单里被预填的字段集合；null 的字段不出现。 */
export interface PrefillFormPatch {
  prices: {
    input: number | null;
    output: number | null;
    cacheRead: number | null;
    cacheWrite: number | null;
  };
  maxInputTokens: number | null;
  maxOutputTokens: number | null;
  reasoningLevel: string | null;
  inputTypes: InputType[] | null;
  outputTypes: OutputType[] | null;
  capabilities: Partial<Record<ModelCapability, boolean>>;
  modelType: ModelType | null;
  /** 实际可填的字段数；0 表示没什么可填。 */
  fieldCount: number;
}

/** defaults → 表单补丁；null 字段按契约跳过（null = 不预填）。 */
export function prefillFormPatch(defaults: PrefillDefaults | null | undefined): PrefillFormPatch | null {
  if (!defaults) return null;
  const parameters = defaults.parameters ?? {};
  const capabilities: Partial<Record<ModelCapability, boolean>> = {};
  for (const key of ["tool_call", "reasoning", "attachment", "structured_output"] as const) {
    const value = parameters[key];
    if (typeof value === "boolean") capabilities[key] = value;
  }
  const patch: PrefillFormPatch = {
    prices: {
      input: numberOrNull(defaults.input_price_per_1m),
      output: numberOrNull(defaults.output_price_per_1m),
      cacheRead: numberOrNull(defaults.cache_read_price_per_1m),
      cacheWrite: numberOrNull(defaults.cache_write_price_per_1m),
    },
    maxInputTokens: numberOrNull(parameters.max_input_tokens),
    maxOutputTokens: numberOrNull(parameters.max_output_tokens),
    reasoningLevel:
      typeof parameters.reasoning_level === "string" && parameters.reasoning_level.trim()
        ? parameters.reasoning_level.trim()
        : null,
    inputTypes: arrayOrNull(parameters.input_types),
    outputTypes: arrayOrNull(parameters.output_types),
    capabilities,
    modelType: defaults.model_type ?? null,
    fieldCount: 0,
  };
  patch.fieldCount = countFields(patch);
  return patch.fieldCount > 0 ? patch : null;
}

function numberOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function arrayOrNull<T extends string>(value: readonly T[] | null | undefined): T[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  return [...value];
}

function countFields(patch: PrefillFormPatch): number {
  let count = 0;
  for (const price of Object.values(patch.prices)) if (price !== null) count += 1;
  if (patch.maxInputTokens !== null) count += 1;
  if (patch.maxOutputTokens !== null) count += 1;
  if (patch.reasoningLevel !== null) count += 1;
  if (patch.inputTypes) count += 1;
  if (patch.outputTypes) count += 1;
  if (patch.modelType) count += 1;
  count += Object.keys(patch.capabilities).length;
  return count;
}

/** 快照日期：只展示到日，避免一条提示里塞时区与毫秒。 */
export function prefillSnapshotDate(snapshot: PrefillSnapshot | null | undefined): string | null {
  const value = snapshot?.pulled_at;
  if (!value) return null;
  return value.slice(0, 10);
}

/** 合并了哪几家供应商：来源表里出现过的 provider 并集。 */
export function prefillProviderCount(prefill: ModelPrefill | null | undefined): number {
  const providers = new Set<string>();
  for (const source of Object.values(prefill?.sources ?? {})) {
    for (const provider of source.providers ?? []) providers.add(provider);
    if (!source.providers?.length && source.provider) providers.add(source.provider);
  }
  if (providers.size === 0 && prefill?.provenance?.provider) providers.add(prefill.provenance.provider);
  return providers.size;
}

/** 候选列表的一行文本：provider/model_id。 */
export function prefillCandidateLabels(
  candidates: readonly PrefillCandidate[] | null | undefined,
): string[] {
  return (candidates ?? []).slice(0, PREFILL_CANDIDATE_LIMIT).map((item) => {
    const model = item.provider ? item.provider + "/" + item.model_id : item.model_id;
    return item.tier ? model + "（" + item.tier + "）" : model;
  });
}

/**
 * 来源提示的一行只读文案（D2：只展示、不落库）。
 *
 * - 有快照且命中：日期 + 合并供应商数 + 命中级别 + 「计费以渠道配置为准」；
 * - 快照过期：追加「已过期 N 天」；
 * - 未命中 / 待复核：给候选列表（最多 5 条）；
 * - 当天没拉过快照：说明可直接手填，不阻塞保存。
 */
export function describePrefillSource(prefill: ModelPrefill | null | undefined): string {
  if (!prefill) return "";
  if (!prefill.snapshot?.available) {
    return "暂无 models.dev 默认值：当天还没有拉取快照，可直接手填。";
  }
  if (!prefill.matched || !prefill.defaults) {
    const candidates = prefillCandidateLabels(prefill.candidates);
    if (candidates.length > 0) {
      return "models.dev 未确认该模型，候选：" + candidates.join("、") + "。可先手填，不影响保存。";
    }
    return "models.dev 未收录该模型，可直接手填，不影响保存。";
  }
  const parts: string[] = [];
  const date = prefillSnapshotDate(prefill.snapshot);
  const providers = prefillProviderCount(prefill);
  const providerText = providers > 0 ? "，合并 " + providers + " 家供应商" : "";
  parts.push("参数来自 models.dev 快照 " + (date ?? "（日期未知）") + providerText);
  if (prefill.snapshot.stale) {
    const age = prefill.snapshot.age_days;
    parts.push(typeof age === "number" && age > 0 ? "已过期 " + age + " 天" : "快照已过期");
  }
  const matchLabel = PREFILL_MATCH_LABELS[prefill.matched_by] ?? prefill.matched_by;
  parts.push(matchLabel + (prefill.version_loose ? "（未合版本后缀，请核对）" : ""));
  parts.push("仅作默认值，计费以渠道配置为准");
  return parts.join(" · ");
}

/* ===================== 手动同步入口（ADR-073 改动点） ===================== */

/** GET /api/model-defaults/status 里的快照维度信息。 */
export interface ModelDefaultsSnapshotInfo {
  available: boolean;
  pulled_at: string | null;
  age_days: number | null;
  stale: boolean;
  providers: number;
  entries: number;
  bytes: number;
  error_summary: string;
  error_at: number | null;
}

/** GET /api/model-defaults/status 的响应（管理员）。 */
export interface ModelDefaultsStatus {
  enabled: boolean;
  rule_version: string;
  today: string;
  last_pull: string;
  last_attempt: string;
  fuzzy_auto: number;
  fuzzy_review: number;
  defaults_count: number;
  snapshot: ModelDefaultsSnapshotInfo;
}

/** POST /api/model-defaults/refresh 的响应（管理员手动触发）。 */
export interface ModelDefaultsRefreshResult {
  status: string;
  trigger: string;
  attempted: boolean;
  refreshed: boolean;
  http_status: number;
  elapsed_ms: number;
  not_modified: boolean;
  conditional: boolean;
  providers: number;
  entries: number;
  bytes: number;
  error_summary: string;
  pulled_at: string;
  last_pull: string;
  last_attempt: string;
  defaults_count: number;
}

export const MODEL_DEFAULTS_STATUS_ENDPOINT = "/api/model-defaults/status";
export const MODEL_DEFAULTS_REFRESH_ENDPOINT = "/api/model-defaults/refresh";

/** 读取 models.dev 同步状态（快照日期/陈旧度/供应商与条目数）。 */
export async function fetchModelDefaultsStatus(): Promise<ModelDefaultsStatus> {
  return apiRequest<ModelDefaultsStatus>(MODEL_DEFAULTS_STATUS_ENDPOINT, { method: "GET" });
}

/** 手动触发一次 models.dev 拉取（304 / 每日一次 / 忙碌由服务端裁决）。 */
export async function refreshModelDefaults(): Promise<ModelDefaultsRefreshResult> {
  return apiRequest<ModelDefaultsRefreshResult>(MODEL_DEFAULTS_REFRESH_ENDPOINT, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** 逐字段合并规则（后端 _RULES）→ 中文动词短语。 */
export const PREFILL_RULE_LABELS: Record<string, string> = {
  median: "多家取中位数",
  max: "多家取最大",
  union: "多家取并集",
  any: "任一家支持",
  mode: "多家取众数",
};

/** 字段级来源提示（决策 7：逐字段来源，只展示、不落库）。 */
export function describeFieldSource(
  prefill: ModelPrefill | null | undefined,
  field: string,
): string {
  const source = prefill?.sources?.[field];
  if (!source) return "";
  const names = [...new Set([...(source.providers ?? []), source.provider].filter(Boolean))];
  const providerText = names.length > 0 ? names.join("、") : "models.dev";
  const rule = PREFILL_RULE_LABELS[source.rule] ?? (source.rule || "合并");
  return "来源 models.dev：" + providerText + " · " + rule + "；仅作默认值";
}

/** 快照状态的一行回显：日期 / 陈旧度 / 供应商与条目数 / 入库默认值条数。 */
export function describeDefaultsStatus(status: ModelDefaultsStatus | null | undefined): string {
  const snap = status?.snapshot;
  if (!status || !snap?.available) {
    const summary = snap?.error_summary;
    return summary ? "models.dev 快照不可用：" + summary : "models.dev 快照不可用，还没有成功拉取过。";
  }
  const parts: string[] = ["快照 " + (snap.pulled_at ? snap.pulled_at.slice(0, 10) : "日期未知")];
  if (typeof snap.age_days === "number" && snap.age_days > 0) {
    parts.push(snap.stale ? "已过期 " + snap.age_days + " 天" : snap.age_days + " 天前拉取");
  }
  parts.push(snap.providers + " 家供应商 · " + snap.entries + " 个条目");
  parts.push("入库默认值 " + (status.defaults_count ?? 0) + " 条");
  return parts.join(" · ");
}

/** 手动刷新结果的一行回显：条目数 / 304 / 错误摘要（含 disabled/busy/skipped 服务端态）。 */
export function describeRefreshResult(result: ModelDefaultsRefreshResult): string {
  if (result.status === "disabled") return "models.dev 同步未启用，请在服务端开启后重试。";
  if (result.status === "busy") return "上一次同步还在进行中，请稍后再试。";
  if (result.status === "skipped") return "今天已经同步过，服务端跳过了本次请求。";
  if (result.not_modified || result.status === "not_modified") {
    return "快照未变化（304 Not Modified），沿用现有数据。";
  }
  if (result.status === "error") {
    return "同步失败：" + (result.error_summary || "HTTP " + (result.http_status || "未知"));
  }
  if (result.refreshed || result.status === "ok") {
    const parts = ["已同步 " + (result.entries ?? 0) + " 个条目（" + (result.providers ?? 0) + " 家供应商）"];
    if (result.elapsed_ms > 0) parts.push(result.elapsed_ms + " ms");
    parts.push("入库默认值 " + (result.defaults_count ?? 0) + " 条");
    return parts.join(" · ");
  }
  return "同步状态：" + (result.status || "未知");
}
