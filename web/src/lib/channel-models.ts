import { apiRequest } from "@/lib/api";

export type ProbeStatus = "ok" | "timeout" | "error" | "unknown";
export type ProbeMode = "shallow" | "deep";
export type VisibleStatus = "normal" | "open" | "half_open" | "fault" | "disabled";
export type HealthStatus = "normal" | "open" | "half_open" | "fault";
export type EscalateMode = "never" | "after_n" | "immediate";

export interface BreakerConfig {
  failure_threshold: number;
  cooldown_s: number;
  rate_limit_cooldown_s: number;
  half_open_max: number;
  escalate_mode: EscalateMode;
  escalate_after: number;
  fault_cooldown_s: number;
}

/** v1 排期形状：只能表达「按星期几开窗」。只在读旧数据时出现，写入一律 v2。 */
export interface ScheduleSegment {
  /** ISO weekday numbers: 1 = Monday ... 7 = Sunday. */
  days: number[];
  /** HH:MM in the schedule timezone. ``end <= start`` crosses midnight into the next day. */
  start: string;
  end: string;
}

/** v1 排期文档。后端只出 v2，这里留着读历史行并转成 v2 再展示。 */
export interface LegacySchedule {
  /** IANA timezone name, e.g. Asia/Shanghai. No UTC offsets are stored. */
  timezone: string;
  segments: ScheduleSegment[];
}

export type InputType = "text" | "image" | "audio" | "video" | "file";
export type OutputType = "text" | "image" | "audio" | "embedding";

/** Model capability type. Only chat models can serve /chat/completions. */
export type ModelType =
  | "chat"
  | "embedding"
  | "rerank"
  | "image_generation"
  | "audio_tts"
  | "audio_transcription"
  | "video";

/** Where the stored model type came from. */
export type ModelTypeSource = "default" | "upstream" | "manual";

/**
 * 能力位（ADR-073 决策 5）：models.dev 同步落 parameters_json 的可选键。
 * 数据层三态：true = 有 provider 声称支持；false = 明确不支持；undefined = 未声明（不落库）。
 * 表单两态（task-153 拍板 A）：保存时四个键全量提交布尔，取消勾选即显式 false。
 */
export type ModelCapability = "tool_call" | "reasoning" | "attachment" | "structured_output";

export interface ModelParameters {
  max_input_tokens: number;
  max_output_tokens: number;
  reasoning_level: string;
  input_types: InputType[];
  output_types: OutputType[];
  /** 能力位：工具调用。 */
  tool_call?: boolean;
  /** 能力位：推理 / 思考输出。 */
  reasoning?: boolean;
  /** 能力位：附件输入。 */
  attachment?: boolean;
  /** 能力位：结构化输出（JSON schema）。 */
  structured_output?: boolean;
}

export const DEFAULT_MODEL_PARAMETERS: ModelParameters = {
  max_input_tokens: 0,
  max_output_tokens: 0,
  reasoning_level: "",
  input_types: ["text"],
  output_types: ["text"],
};

export const MODEL_TYPE_OPTIONS: Array<{ value: ModelType; label: string }> = [
  { value: "chat", label: "聊天" },
  { value: "embedding", label: "向量" },
  { value: "rerank", label: "重排" },
  { value: "image_generation", label: "图像生成" },
  { value: "audio_tts", label: "语音合成" },
  { value: "audio_transcription", label: "语音转写" },
  { value: "video", label: "视频" },
];

export const MODEL_TYPE_SOURCE_META: Record<
  ModelTypeSource,
  { label: string; hint: string }
> = {
  default: { label: "默认", hint: "上游未声明类型，按聊天处理" },
  upstream: { label: "上游同步", hint: "按上游模型名自动识别" },
  manual: { label: "手动", hint: "人工指定，覆盖自动识别" },
};

export function modelTypeLabel(value: string | null | undefined): string {
  if (!value) return "聊天";
  return MODEL_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

export const INPUT_TYPE_OPTIONS: Array<{ value: InputType; label: string }> = [
  { value: "text", label: "文本" },
  { value: "image", label: "图片" },
  { value: "audio", label: "音频" },
  { value: "video", label: "视频" },
  { value: "file", label: "文件" },
];

export const OUTPUT_TYPE_OPTIONS: Array<{ value: OutputType; label: string }> = [
  { value: "text", label: "文本" },
  { value: "image", label: "图片" },
  { value: "audio", label: "音频" },
  { value: "embedding", label: "向量" },
];

/** 能力位展示元数据：表单复选框与组合并集都读这一份。 */
export const MODEL_CAPABILITY_OPTIONS: Array<{
  value: ModelCapability;
  label: string;
  hint: string;
}> = [
  { value: "tool_call", label: "工具调用", hint: "function / tool calling" },
  { value: "reasoning", label: "推理", hint: "思考内容输出" },
  { value: "attachment", label: "附件", hint: "文件 / 附件输入" },
  { value: "structured_output", label: "结构化输出", hint: "JSON schema 约束" },
];

export interface ChannelModel {
  id: string;
  channel_id: string;
  upstream_model: string;
  call_model: string;
  parameters: ModelParameters;
  input_price_per_1m: number;
  output_price_per_1m: number;
  cache_read_price_per_1m: number | null;
  cache_write_price_per_1m: number | null;
  enabled: boolean;
  /** 人工轴的冷却到期时刻（epoch ms）；缺省/空表示永久封禁。 */
  banned_until?: number | null;
  health_status: HealthStatus;
  visible_status: VisibleStatus;
  consecutive_failures: number;
  consecutive_trip_count: number;
  escalation_trip_count: number;
  opened_at: number | null;
  cooldown_until: number | null;
  fault_until: number | null;
  next_retry_at: number | null;
  cooldown: BreakerConfig;
  schedule: ChannelSchedule;
  probe_status: ProbeStatus;
  probe_latency_ms: number | null;
  probe_message: string;
  probed_at: number | null;
  probe_default: boolean;
  model_type: ModelType;
  model_type_source: ModelTypeSource;
  created_at: number;
  updated_at: number;
}

export interface ChannelModelList {
  items: ChannelModel[];
  total: number;
}

export interface ChannelModelInput {
  upstream_model: string;
  call_model?: string;
  parameters: ModelParameters;
  input_price_per_1m: number;
  output_price_per_1m: number;
  cache_read_price_per_1m?: number | null;
  cache_write_price_per_1m?: number | null;
  enabled: boolean;
  /** Omit to keep the automatic value; "auto" resets a manual override upstream. */
  model_type?: ModelType | "auto";
  schedule?: ScheduleDocument;
}

export interface ProbeResult {
  model_id: string;
  channel_id: string;
  upstream_model: string;
  call_model: string;
  status: ProbeStatus;
  latency_ms: number;
  message: string;
  probed_at: number;
  mode: ProbeMode;
  protocol: string;
  auth_style: string;
  http_status: number | null;
}

export interface ProbeAllResult {
  channel_id: string;
  mode: ProbeMode;
  total: number;
  ok: number;
  timeout: number;
  error: number;
  unknown: number;
  items: ProbeResult[];
  protocol: string;
  auth_style: string;
  protocol_source: string;
}

export const PROBE_STATUS_META: Record<
  ProbeStatus,
  { label: string; className: string; dot: string }
> = {
  ok: {
    label: "正常运行",
    className: "border-success bg-success-soft text-success-text",
    dot: "bg-success",
  },
  timeout: {
    label: "连接超时",
    className: "border-warning bg-warning-soft text-warning-text",
    dot: "bg-warning",
  },
  error: {
    label: "探测失败",
    className: "border-danger bg-danger-soft text-danger-text",
    dot: "bg-danger",
  },
  unknown: {
    label: "未知状态",
    className: "border-border bg-surface-2 text-muted-foreground",
    // neutral dot: bg-faint stays visible on surface-2 (semantic colour pass deferred to batch 3)
    dot: "bg-faint",
  },
};

export const HEALTH_STATUS_META: Record<
  VisibleStatus,
  { label: string; className: string; dot: string; description: string }
> = {
  normal: {
    label: "正常",
    className: "border-success bg-success-soft text-success-text",
    dot: "bg-success",
    description: "可正常接收流量",
  },
  open: {
    label: "冷却（故障）",
    className: "border-warning bg-warning-soft text-warning-text",
    dot: "bg-warning",
    description: "熔断打开的冷却窗口，暂不接流量；到期自动恢复",
  },
  half_open: {
    label: "半开",
    className: "border-warning bg-warning-soft text-warning-text",
    dot: "bg-warning",
    description: "冷却结束进入半开：下一次调用试探决定恢复还是继续冷却",
  },
  fault: {
    label: "冷却（故障）",
    className: "border-danger bg-danger-soft text-danger-text",
    dot: "bg-danger",
    description: "多次熔断已升级为故障冷却，到期自动恢复",
  },
  disabled: {
    label: "封禁 / 冷却",
    className: "border-border bg-surface-2 text-muted-foreground",
    // neutral dot: bg-faint stays visible on surface-2 (semantic colour pass deferred to batch 3)
    dot: "bg-faint",
    description: "人工设置：封禁只能手动解除；定时冷却到期自动恢复。",
  },
};

export const WEEKDAY_LABELS: Array<{ value: number; label: string }> = [
  { value: 1, label: "周一" },
  { value: 2, label: "周二" },
  { value: 3, label: "周三" },
  { value: 4, label: "周四" },
  { value: 5, label: "周五" },
  { value: 6, label: "周六" },
  { value: 7, label: "周日" },
];

/** 排期规则文档 v2（ADR-070 §1.3）。整份覆盖写；空规则集 = 永不冷却。 */
export type ScheduleRuleKind = "daily" | "weekly" | "monthly";

export interface ScheduleRule {
  kind: ScheduleRuleKind;
  /** weekly 用 ISO 星期（1=周一）。daily / monthly 省略。 */
  days?: number[];
  /** monthly 用日号 1–31。daily / weekly 省略。 */
  days_of_month?: number[];
  /** HH:MM 本地墙钟；end <= start 表示跨天窗口（结束落在次日）。 */
  start: string;
  end: string;
}

export interface ScheduleDocument {
  version: 2;
  /** IANA 时区名：窗口按该时区的墙钟求值，不存 UTC 时刻。 */
  timezone: string;
  rules: ScheduleRule[];
}

export const DEFAULT_SCHEDULE_TIMEZONE = "Asia/Shanghai";

/** 浏览器时区；拿不到时回落默认时区，不让界面报错。 */
export function localScheduleTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_SCHEDULE_TIMEZONE;
  } catch {
    return DEFAULT_SCHEDULE_TIMEZONE;
  }
}

/**
 * 目录行里可能还是 v1（segments），也可能是 v2（rules）。
 * 读的一律先过 normalizeSchedule，写的一律是 v2。
 */
export type ChannelSchedule = ScheduleDocument | LegacySchedule;

/** 单条规则的人话描述；end <= start 明说「跨天」，别让人以为时间写反了。 */
export function describeScheduleRule(rule: ScheduleRule): string {
  const window = rule.start + "–" + rule.end + (rule.end <= rule.start ? "（跨天）" : "");
  if (rule.kind === "daily") return "每天 " + window;
  if (rule.kind === "weekly") {
    const days = (rule.days ?? [])
      .slice()
      .sort((left, right) => left - right)
      .map((day) => WEEKDAY_LABELS.find((item) => item.value === day)?.label ?? "周" + day)
      .join("、");
    return "每周 " + (days || "（未选）") + " " + window;
  }
  const days = (rule.days_of_month ?? [])
    .slice()
    .sort((left, right) => left - right)
    .join("、");
  return "每月 " + (days || "（未选）") + " 日 " + window;
}

/** 多条规则是并集（OR），用分号隔开；空规则集表示不限时段。 */
export function describeSchedule(rules: readonly ScheduleRule[]): string {
  if (rules.length === 0) return "全天可用";
  return rules.map(describeScheduleRule).join("；");
}

/** 新建一条规则时的默认值：每天 22:00–02:00 的跨天窗口，最常用的夜间冷却。 */
export function emptyRule(): ScheduleRule {
  return { kind: "daily", start: "22:00", end: "02:00" };
}

/** 校验单条规则；index 从 0 开始，只用于错误文案里的序号。 */
export function validateScheduleRule(rule: ScheduleRule, index: number): string | null {
  if (!rule.start || !rule.end) return "第 " + (index + 1) + " 条规则缺开始或结束时刻";
  if (rule.kind === "weekly" && (rule.days ?? []).length === 0) {
    return "第 " + (index + 1) + " 条规则至少选一个星期";
  }
  if (rule.kind === "monthly" && (rule.days_of_month ?? []).length === 0) {
    return "第 " + (index + 1) + " 条规则至少选一个日号";
  }
  return null;
}

/**
 * 校验整份规则集。allowEmpty 用于渠道表单：零条规则表示全天可用；
 * 批量排期弹窗不允许空提交（空提交会静默取消已有冷却窗口）。
 */
export function validateScheduleRules(
  rules: readonly ScheduleRule[],
  options: { allowEmpty?: boolean } = {},
): string | null {
  if (rules.length === 0) {
    return options.allowEmpty ? null : "至少加一条规则；一条都没有表示全天可用";
  }
  for (let index = 0; index < rules.length; index += 1) {
    const error = validateScheduleRule(rules[index], index);
    if (error) return error;
  }
  return null;
}

/** 把 v1 的 segments 折成 v2 的 weekly 规则；v2 原样返回（深拷贝一层）。 */
export function normalizeSchedule(
  schedule: ChannelSchedule | null | undefined,
): ScheduleDocument | null {
  if (!schedule) return null;
  const timezone = typeof schedule.timezone === "string" ? schedule.timezone.trim() : "";
  if (!timezone) return null;
  if ("rules" in schedule) {
    return {
      version: 2,
      timezone,
      rules: (schedule.rules ?? []).map((rule) => ({
        ...rule,
        ...(rule.days ? { days: [...rule.days].sort((left, right) => left - right) } : {}),
        ...(rule.days_of_month
          ? { days_of_month: [...rule.days_of_month].sort((left, right) => left - right) }
          : {}),
      })),
    };
  }
  return {
    version: 2,
    timezone,
    rules: (schedule.segments ?? []).map((segment) => ({
      kind: "weekly" as const,
      days: [...segment.days].sort((left, right) => left - right),
      start: segment.start,
      end: segment.end,
    })),
  };
}

/** 表格里的一行人话；没有窗口时返回 null，别在每行都写「全天可用」。 */
export function scheduleSummary(
  schedule: ChannelSchedule | null | undefined,
): string | null {
  const document = normalizeSchedule(schedule);
  if (!document || document.rules.length === 0) return null;
  return document.timezone + " · " + describeSchedule(document.rules);
}

/** 批量操作码（ADR-070 §4.1）：一封信改多行，单事务提交。 */
export type BatchOp = "ban" | "unban" | "cooldown" | "schedule";

export interface BatchOpResultItem {
  model_id: string;
  ok: boolean;
  error?: string;
}

export interface BatchOpResult {
  results: BatchOpResultItem[];
  succeeded: number;
  failed: number;
}

export interface BatchOpInput {
  modelIds: readonly string[];
  op: BatchOp;
  /** op=cooldown 必填：ISO-8601 绝对时刻。 */
  bannedUntil?: string | null;
  /** op=schedule 必填。 */
  schedule?: ScheduleDocument;
}

/** 单次批量请求的条目上限（ADR-070 §4.1：≤ 200）。 */
export const BATCH_LIMIT = 200;

export async function batchUpdateChannelModels(
  input: BatchOpInput,
): Promise<BatchOpResult> {
  return apiRequest<BatchOpResult>("/api/channel-models/batch", {
    method: "PATCH",
    body: JSON.stringify({
      model_ids: input.modelIds,
      op: input.op,
      ...(input.op === "cooldown" ? { banned_until: input.bannedUntil ?? null } : {}),
      ...(input.op === "schedule" ? { schedule: input.schedule } : {}),
    }),
  });
}

export const PROBE_MODES: Array<{ value: ProbeMode; label: string; hint: string }> = [
  { value: "shallow", label: "模型拉取探测", hint: "只请求 /models，确认协议与鉴权" },
  { value: "deep", label: "模型连接探测", hint: "识别协议后发一次最小 chat（max_tokens=16）" },
];

export async function listChannelModels(channelId: string): Promise<ChannelModelList> {
  return apiRequest<ChannelModelList>(`/api/channels/${channelId}/models`);
}

export async function createChannelModel(
  channelId: string,
  input: ChannelModelInput,
): Promise<ChannelModel> {
  return apiRequest<ChannelModel>(`/api/channels/${channelId}/models`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateChannelModel(
  modelId: string,
  input: Partial<ChannelModelInput>,
): Promise<ChannelModel> {
  return apiRequest<ChannelModel>(`/api/channel-models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteChannelModel(modelId: string): Promise<void> {
  await apiRequest<null>(`/api/channel-models/${modelId}`, { method: "DELETE" });
}

export async function resetChannelModel(modelId: string): Promise<ChannelModel> {
  return apiRequest<ChannelModel>(`/api/channel-models/${modelId}/reset`, {
    method: "POST",
  });
}

export type ModelDefaultsAdopt = {
  model_id: string;
  upstream_model: string;
  matched: boolean;
  matched_by: string;
  adopted: string[];
  force: boolean;
};

export async function adoptModelDefaults(
  modelId: string,
  body: { force: boolean },
): Promise<ModelDefaultsAdopt> {
  return apiRequest<ModelDefaultsAdopt>(`/api/channel-models/${modelId}/adopt-defaults`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function setDefaultProbeModel(modelId: string): Promise<ChannelModel> {
  return apiRequest<ChannelModel>(`/api/channel-models/${modelId}/probe-default`, {
    method: "POST",
  });
}

export async function probeChannelModel(
  modelId: string,
  mode: ProbeMode,
): Promise<ProbeResult> {
  return apiRequest<ProbeResult>(`/api/channel-models/${modelId}/probe`, {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

export async function probeAllChannelModels(
  channelId: string,
  mode: ProbeMode,
): Promise<ProbeAllResult> {
  return apiRequest<ProbeAllResult>(`/api/channels/${channelId}/probe-all`, {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}
