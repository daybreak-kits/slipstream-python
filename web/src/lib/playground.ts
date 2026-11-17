import { apiRequest, getStoredToken } from "@/lib/api";
import type {
  ChannelSchedule,
  HealthStatus,
  ModelParameters,
  VisibleStatus,
} from "@/lib/channel-models";

export interface CatalogItem {
  id: string;
  name: string;
  channel_id: string;
  channel_slug: string;
  channel_name: string;
  protocol: string;
  upstream_model: string;
  call_model: string;
  parameters: ModelParameters;
  input_price_per_1m: number;
  output_price_per_1m: number;
  /** 缓存命中 / 写入价（每百万 tokens）；后端已透传，可能为 null。 */
  cache_read_price_per_1m?: number | null;
  cache_write_price_per_1m?: number | null;
  enabled: boolean;
  created_at: number;
  probe_status?: string;
  probe_message?: string;
  probed_at?: number | null;
  health_status?: HealthStatus | null;
  visible_status?: VisibleStatus | null;
  cooldown_until?: number | null;
  fault_until?: number | null;
  status_message?: string;
}

export interface CatalogList {
  items: CatalogItem[];
  total: number;
}

export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ChatResponse {
  id?: string;
  object?: string;
  model?: string;
  choices?: Array<{
    index?: number;
    message?: ChatMessagePayload;
    delta?: ChatMessagePayload;
    finish_reason?: string | null;
  }>;
  usage?: ChatUsage;
}

export interface RequestLog {
  id: string;
  request_id: string;
  api_key_label: string;
  requested_model: string;
  channel_name: string;
  upstream_model: string;
  response_model: string;
  http_status: number;
  error_code: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  ttft_ms: number | null;
  total_ms: number;
  throughput_tps: number | null;
  usage_estimated: boolean;
  created_at: number;
}

export interface RequestLogList {
  items: RequestLog[];
  total: number;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export type ReasoningEffort = "none" | "low" | "medium" | "high" | "max";
export type ResponseFormatType = "text" | "json_object";
/** 思考控件：off 不指定；none..max 发 reasoning_effort；thinking 发 thinking.enabled；enable_thinking 发开关。 */
export type ThinkingMode =
  | "off"
  | "none"
  | "low"
  | "medium"
  | "high"
  | "max"
  | "thinking"
  | "enable_thinking";

/** 思考内容的载体；顺序即归一化优先级。 */
export type ThinkingCarrier =
  | "reasoning_content"
  | "reasoning"
  | "thinking"
  | "parts"
  | "inline_tag"
  | "none";

/** 部分厂商把 message.content 写成 content parts 数组（Anthropic / Gemini 兼容层）。 */
export interface ChatContentPart {
  type?: string;
  text?: string;
  thinking?: string;
  thought?: boolean;
  [key: string]: unknown;
}

/** 响应里一条 message / delta 的思考相关字段。 */
export interface ChatMessagePayload {
  role?: string;
  content?: string | ChatContentPart[] | null;
  /** DeepSeek / Qwen / Gemini 兼容层。 */
  reasoning_content?: string | null;
  /** Anthropic 聚合层常见。 */
  reasoning?: string | null;
  /** 部分网关只回 thinking 字符串。 */
  thinking?: string | null;
}

export interface ThinkingExtraction {
  /** 去掉思考内容后的正文。 */
  content: string;
  reasoning: string;
  carrier: ThinkingCarrier;
}

export interface ThinkingMarker {
  label: string;
  source: "reasoning_effort" | "thinking" | "enable_thinking" | "think" | "inline_tag";
}

/**
 * 请求级参数：网关只改写 model/messages/stream，其余字段原样透传上游，
 * 所以这里的字段名必须与 OpenAI Chat Completions 一致。
 * 值为 undefined 表示这次请求不发送该参数。
 */
export interface ChatParams {
  temperature?: number;
  top_p?: number;
  top_k?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  seed?: number;
  max_tokens?: number;
  max_completion_tokens?: number;
  stop?: string[];
  n?: number;
  response_format?: { type: ResponseFormatType };
  logprobs?: boolean;
  top_logprobs?: number;
  user?: string;
  reasoning_effort?: ReasoningEffort;
  /** DeepSeek 的 thinking:{type:'enabled'|'disabled'}，优先级高于 reasoning_effort。 */
  thinking?: { type: "enabled" | "disabled" };
  /** Qwen / DashScope 的非标准开关。 */
  enable_thinking?: boolean;
  /** 额外请求字段（JSON 透传框）；标准字段优先，保留键不会被覆盖。 */
  extraFields?: Record<string, unknown>;
}

/** 不允许被额外请求字段覆盖的键：它们由页面和网关决定。 */
const RESERVED_BODY_KEYS = new Set(["model", "messages", "stream", "stream_options"]);

/** 只带上真正设置过的字段：undefined 不写进请求体；额外字段兜底在前。 */
function chatParamsBody(params: ChatParams): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params.extraFields ?? {})) {
    if (!RESERVED_BODY_KEYS.has(key) && value !== undefined) body[key] = value;
  }
  for (const [key, value] of Object.entries(params)) {
    if (key === "extraFields" || value === undefined) continue;
    body[key] = value;
  }
  return body;
}

export interface ExtraFieldsResult {
  ok: boolean;
  value: Record<string, unknown>;
  error?: string;
}

/** 「额外请求字段（JSON）」框：空=不发送；非对象或非法 JSON 直接拦下。 */
export function parseExtraFields(raw: string | undefined): ExtraFieldsResult {
  const text = (raw ?? "").trim();
  if (!text) return { ok: true, value: {} };
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { ok: false, value: {}, error: "额外请求字段需是一个 JSON 对象。" };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, value: {}, error: "额外请求字段不是合法 JSON。" };
  }
}

const THINK_OPEN_RE = /<think(?:ing)?>|＜think(?:ing)?＞/i;
const THINK_CLOSE_RE = /<\/think(?:ing)?>|＜\/think(?:ing)?＞/i;
const OPEN_TAG_PREFIXES = [
  "<think>",
  "<thinking>",
  "<reasoning>",
  "＜think＞",
  "＜thinking＞",
  "＜reasoning＞",
];

/** 流式时末尾可能是半个开标签（"<thi"），先扣住不显示，等下一块补全。 */
function trailingPartialTagIndex(text: string): number {
  const lastOpen = Math.max(text.lastIndexOf("<"), text.lastIndexOf("＜"));
  if (lastOpen < 0) return -1;
  const tail = text.slice(lastOpen).toLowerCase();
  return OPEN_TAG_PREFIXES.some((tag) => tag.toLowerCase().startsWith(tail))
    ? lastOpen
    : -1;
}

/**
 * 从正文里拆内联思考标签（<think> /  thinking / <reasoning>，含全角括号）。
 * final=false 用于流式：还没闭合的标签内容算思考，末尾半个标签先不显示。
 */
export function splitInlineThinking(
  text: string,
  final = true,
): { visible: string; thinking: string; hasTag: boolean } {
  let rest = text;
  let visible = "";
  let thinking = "";
  let hasTag = false;
  while (rest.length > 0) {
    const openMatch = THINK_OPEN_RE.exec(rest);
    if (!openMatch || openMatch.index === undefined) {
      if (!final) {
        const partial = trailingPartialTagIndex(rest);
        if (partial >= 0) {
          visible += rest.slice(0, partial);
          break;
        }
      }
      visible += rest;
      break;
    }
    visible += rest.slice(0, openMatch.index);
    const afterOpen = rest.slice(openMatch.index + openMatch[0].length);
    const closeMatch = THINK_CLOSE_RE.exec(afterOpen);
    hasTag = true;
    if (!closeMatch || closeMatch.index === undefined) {
      thinking += afterOpen;
      break;
    }
    thinking += afterOpen.slice(0, closeMatch.index);
    rest = afterOpen.slice(closeMatch.index + closeMatch[0].length);
  }
  return { visible, thinking, hasTag };
}

/** 正文可能是字符串，也可能是 content parts 数组；思考型 part 单独收走。 */
export function splitContentParts(content: string | ChatContentPart[] | null | undefined): {
  text: string;
  thinking: string;
} {
  if (!Array.isArray(content)) {
    return { text: typeof content === "string" ? content : "", thinking: "" };
  }
  let text = "";
  let thinking = "";
  for (const part of content) {
    if (!part || typeof part !== "object") continue;
    const value =
      typeof part.thinking === "string"
        ? part.thinking
        : typeof part.text === "string"
          ? part.text
          : "";
    if (part.type === "thinking" || part.type === "reasoning" || part.thought === true) {
      thinking += value;
    } else {
      text += value;
    }
  }
  return { text, thinking };
}

/**
 * 离线的思考归一化：reasoning_content → reasoning → thinking → content parts
 * → 正文内联标签，只认第一个非空载体。
 */
export function extractThinkingFromMessage(
  message: ChatMessagePayload | null | undefined,
): ThinkingExtraction {
  const payload = message ?? {};
  const directText = (value: string | null | undefined): string =>
    typeof value === "string" ? value : "";
  const body = splitContentParts(payload.content);
  if (directText(payload.reasoning_content).trim()) {
    return {
      content: body.text,
      reasoning: directText(payload.reasoning_content),
      carrier: "reasoning_content",
    };
  }
  if (directText(payload.reasoning).trim()) {
    return { content: body.text, reasoning: directText(payload.reasoning), carrier: "reasoning" };
  }
  if (directText(payload.thinking).trim()) {
    return { content: body.text, reasoning: directText(payload.thinking), carrier: "thinking" };
  }
  if (body.thinking.trim()) {
    return { content: body.text, reasoning: body.thinking, carrier: "parts" };
  }
  const tagged = splitInlineThinking(body.text, true);
  if (tagged.hasTag && tagged.thinking.trim()) {
    return { content: tagged.visible, reasoning: tagged.thinking, carrier: "inline_tag" };
  }
  return { content: tagged.hasTag ? tagged.visible : body.text, reasoning: "", carrier: "none" };
}

/**
 * 思考块标记：由「这次请求发了什么」决定，标签内容不参与猜测。
 * 内联标签是唯一例外：它只能从收到的正文里识别。
 */
export function describeThinking(
  params: ChatParams,
  reasoning: string,
  carrier: ThinkingCarrier,
): ThinkingMarker | null {
  if (carrier === "inline_tag") {
    return { label: "think 标签", source: "inline_tag" };
  }
  if (params.reasoning_effort) {
    return { label: "reasoning_effort=" + params.reasoning_effort, source: "reasoning_effort" };
  }
  if (params.thinking?.type === "enabled") {
    return { label: "thinking=on", source: "thinking" };
  }
  if (params.enable_thinking) {
    return { label: "enable_thinking", source: "enable_thinking" };
  }
  if (reasoning.trim()) {
    return { label: "think", source: "think" };
  }
  return null;
}

export interface ChatResult {
  data: ChatResponse;
  requestId: string;
  elapsedMs: number;
  /** 去掉思考内容后的正文。 */
  content: string;
  /** 非流式响应里的思考内容；上游没回就是空串。 */
  reasoning: string;
  /** 思考内容是从哪个字段/载体识别出来的。 */
  carrier: ThinkingCarrier;
}

interface ErrorBody {
  error?: {
    message?: string;
    code?: string;
    type?: string;
    request_id?: string;
  };
  message?: string;
  code?: string;
  request_id?: string;
}

export class ChatError extends Error {
  readonly status: number;
  readonly code: string;
  readonly type: string;
  readonly requestId: string;

  constructor(status: number, body: ErrorBody) {
    const detail = body.error ?? body;
    super(detail.message || `请求失败（HTTP ${status}）`);
    this.name = "ChatError";
    this.status = status;
    this.code = detail.code || "UNKNOWN";
    this.type = ("type" in detail ? detail.type : undefined) || "unknown";
    this.requestId = detail.request_id || "";
  }
}

export async function listCatalog(): Promise<CatalogList> {
  // `/api/catalog` returns direct models, combos and implicit groups together.
  // Callers of this legacy helper only want real channel models, so filter the
  // mixed payload instead of pretending every entry is a channel row.
  const entries = await listCatalogEntries();
  const items = entries.items.filter((item) => item.kind === "direct");
  return { items, total: items.length };
}

export async function listRecentLogs(limit = 20): Promise<RequestLogList> {
  return apiRequest<RequestLogList>(`/api/logs/recent?limit=${limit}`);
}

/** 试验页优先使用临时输入的密钥（仅内存态），留空回落到管理员登录会话。 */
function resolveBearer(apiKey: string): string {
  return apiKey.trim() || getStoredToken() || "";
}

export async function sendChat(
  apiKey: string,
  model: string,
  content: string | ChatMessage[],
  params: ChatParams = {},
): Promise<ChatResult> {
  const messages = typeof content === "string" ? [{ role: "user" as const, content }] : content;
  const started = performance.now();
  const response = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${resolveBearer(apiKey)}`,
    },
    body: JSON.stringify({
      model,
      messages,
      stream: false,
      ...chatParamsBody(params),
    }),
  });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  const elapsedMs = Math.round(performance.now() - started);
  if (!response.ok) {
    throw new ChatError(response.status, (body as ErrorBody) || {});
  }
  const data = (body as ChatResponse) || {};
  const extraction = extractThinkingFromMessage(data.choices?.[0]?.message);
  return {
    data,
    requestId: response.headers.get("x-request-id") || "",
    elapsedMs,
    content: extraction.content,
    reasoning: extraction.reasoning,
    carrier: extraction.carrier,
  };
}

export class StreamAbortedError extends Error {
  constructor() {
    super("已停止生成");
    this.name = "StreamAbortedError";
  }
}

export function isAbortError(reason: unknown): boolean {
  if (reason instanceof StreamAbortedError) return true;
  return (
    typeof reason === "object" &&
    reason !== null &&
    (reason as { name?: string }).name === "AbortError"
  );
}

export interface StreamStats {
  /** 去掉内联思考标签后的正文。 */
  content: string;
  /** 流式回复里累积的思考内容。 */
  reasoning: string;
  /** 思考内容载体（reasoning_content / reasoning / thinking / parts / inline_tag / none）。 */
  carrier: ThinkingCarrier;
  model: string;
  requestId: string;
  ttftMs: number | null;
  elapsedMs: number;
  finishReason: string | null;
  usage: ChatUsage | null;
  sawDone: boolean;
}

export interface StreamOptions {
  signal?: AbortSignal;
  onDelta?: (text: string) => void;
  /** 思考内容增量；参数是累积到当前为止的全文，方便直接渲染。 */
  onReasoning?: (text: string) => void;
  params?: ChatParams;
}

function parseJson(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Calls `/v1/chat/completions` with `stream=true` and parses the SSE body by
 * hand: no third-party client, and an AbortSignal cancels the upstream call
 * from the browser side.
 */
export async function sendChatStream(
  apiKey: string,
  model: string,
  content: string | ChatMessage[],
  options: StreamOptions = {},
): Promise<StreamStats> {
  const messages = typeof content === "string" ? [{ role: "user" as const, content }] : content;
  const started = performance.now();
  const response = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${resolveBearer(apiKey)}`,
    },
    body: JSON.stringify({
      model,
      messages,
      stream: true,
      ...chatParamsBody(options.params ?? {}),
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ChatError(response.status, (parseJson(text) as ErrorBody) || {});
  }
  if (!response.body) {
    throw new ChatError(502, { message: "浏览器无法读取流式响应" });
  }

  const requestId = response.headers.get("x-request-id") || "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = "";
  let rawContent = "";
  let full = "";
  let reasoning = "";
  let carrier: ThinkingCarrier = "none";
  let ttftMs: number | null = null;
  let finishReason: string | null = null;
  let usage: ChatUsage | null = null;
  let sawDone = false;
  let responseModel = model;
  let eventName = "";

  const abortPromise = new Promise<never>((_, reject) => {
    if (options.signal?.aborted) {
      reject(new StreamAbortedError());
      return;
    }
    options.signal?.addEventListener(
      "abort",
      () => reject(new StreamAbortedError()),
      { once: true },
    );
  });
  abortPromise.catch(() => undefined);

  const handleLine = (line: string) => {
    if (line === "") {
      eventName = "";
      return;
    }
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
      return;
    }
    if (!line.startsWith("data:")) return;
    const data = line.slice("data:".length).trim();
    if (eventName === "error") {
      throw new ChatError(502, (parseJson(data) as ErrorBody) || {});
    }
    if (data === "[DONE]") {
      sawDone = true;
      return;
    }
    const payload = parseJson(data) as ChatResponse | null;
    if (!payload) return;
    if (payload.model) responseModel = payload.model;
    if (payload.usage) usage = payload.usage;
    const choice = payload.choices?.[0];
    if (!choice) return;
    if (choice.finish_reason) finishReason = choice.finish_reason;
    const delta = choice.delta ?? {};
    // 思考载体按契约顺序识别：reasoning_content → reasoning → thinking。
    const directReasoning = delta.reasoning_content ?? delta.reasoning ?? delta.thinking;
    if (typeof directReasoning === "string" && directReasoning) {
      if (carrier === "none") {
        carrier = delta.reasoning_content
          ? "reasoning_content"
          : delta.reasoning
            ? "reasoning"
            : "thinking";
      }
      reasoning += directReasoning;
      options.onReasoning?.(reasoning);
    }
    const deltaContent = delta.content;
    if (typeof deltaContent === "string" && deltaContent) {
      if (ttftMs === null) ttftMs = Math.round(performance.now() - started);
      rawContent += deltaContent;
    } else if (Array.isArray(deltaContent)) {
      const parts = splitContentParts(deltaContent);
      if (parts.thinking) {
        if (carrier === "none") carrier = "parts";
        reasoning += parts.thinking;
        options.onReasoning?.(reasoning);
      }
      if (parts.text) {
        if (ttftMs === null) ttftMs = Math.round(performance.now() - started);
        rawContent += parts.text;
      }
    }
    // 正文里可能内联  thinking 标签：跨 chunk 拼接后按完整标签切分。
    // 同一段思考会随 chunk 增长反复重算（闭合标签本身也可能被拆开），
    // 所以内联载体要整体覆盖，不能只在 reasoning 为空时赋值。
    const tagged = splitInlineThinking(rawContent, false);
    if (tagged.hasTag && (carrier === "none" || carrier === "inline_tag")) {
      if (carrier === "inline_tag" || tagged.thinking) {
        carrier = "inline_tag";
        if (tagged.thinking !== reasoning) {
          reasoning = tagged.thinking;
          options.onReasoning?.(reasoning);
        }
      }
    }
    // 末尾被扣住的半个开标签不属于正文，等下一块补全后再显示。
    full = tagged.visible;
    options.onDelta?.(full);
  };

  try {
    while (true) {
      const result = await Promise.race([reader.read(), abortPromise]);
      if (result.done) break;
      buffer += decoder.decode(result.value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        handleLine(line);
        newline = buffer.indexOf("\n");
      }
    }
    if (buffer) handleLine(buffer.replace(/\r$/, ""));
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The stream may already be closed after an abort.
    }
  }

  return {
    content: full,
    reasoning,
    carrier,
    model: responseModel,
    requestId,
    ttftMs,
    elapsedMs: Math.round(performance.now() - started),
    finishReason,
    usage,
    sawDone,
  };
}

export function describeChatError(error: unknown): string {
  if (error instanceof ChatError) {
    const parts = [error.message];
    if (error.code && error.code !== "UNKNOWN") parts.push(`(${error.code})`);
    if (error.requestId) parts.push(`请求编号 ${error.requestId}`);
    return parts.join(" · ");
  }
  if (error instanceof Error) return error.message;
  return "请求失败，请稍后重试";
}

export interface CatalogEntry extends CatalogItem {
  /** ADR-067：条目只有直连与组合两种；组合的来源由 origin 区分。 */
  kind: "direct" | "combo";
  origin?: "manual" | "auto" | null;
  /** 定时封禁到期时刻（epoch ms）；null/缺省表示没有定时封禁。 */
  banned_until?: number | null;
  status: string;
  /** 后端派生状态（api/src/l06_api/schemas.py 的 CatalogEntry，与 channel-models.ts 共享 Literal）。 */
  health_status?: HealthStatus | null;
  visible_status?: VisibleStatus | null;
  cooldown_until?: number | null;
  fault_until?: number | null;
  /** 熔断器连续失败次数（后端目录接口透传后用于「可用但近期失败」提示）。 */
  consecutive_failures?: number | null;
  status_message?: string;
  /** 目录下发的排期（v2；v1 历史行后端出口已归一成 weekly）。null/缺省表示没有排期。 */
  schedule?: ChannelSchedule | null;
  /** 目录下发的原因码；null 表示按普通状态展示，值域与后端 Literal 一致。 */
  cooldown_reason?: "MODEL_BANNED" | "MODEL_COOLDOWN" | "COOLDOWN_WINDOW" | null;
}

export interface CatalogEntries {
  items: CatalogEntry[];
  total: number;
}

export async function listCatalogEntries(
  options: { includeDisabled?: boolean } = {},
): Promise<CatalogEntries> {
  const query = options.includeDisabled ? "?include_disabled=true" : "";
  return apiRequest<CatalogEntries>("/api/catalog" + query);
}

/**
 * 模型管理页专用：连已停用的直连条目一起取。否则封禁后条目直接从目录消失，
 * 「启用」入口无从点起——封禁会变成单向门。
 */
export const managementCatalogQuery = {
  queryKey: ["catalog-entries", "management"] as const,
  queryFn: () => listCatalogEntries({ includeDisabled: true }),
};

/* ---------------------------------------------------------------------------
 * 模型试验（聊天界面）用的纯函数。
 *
 * 聊天记录只保存在浏览器本地（lib 侧只提供纯函数，存储读写在
 * components/playground/history-store.ts），不经过任何后端接口。
 * ------------------------------------------------------------------------- */

/** 聊天记录在浏览器里的存储键；与 lib/storage.ts 的系统键分开，互不覆盖。 */
export const PLAYGROUND_HISTORY_KEY = "slipstream.playground.history.v1";

/** 只保留最近若干条，避免单次会话把 localStorage 撑爆。 */
export const PLAYGROUND_HISTORY_LIMIT = 200;

export interface PlaygroundAttachment {
  name: string;
  size: number;
  /** 附件的文本内容；后端没有多模态入口，附件只能以文本形式随消息发送。 */
  text: string;
}

export interface PlaygroundUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  /** 首字耗时（毫秒）；非流式请求拿不到时为 null。 */
  ttftMs: number | null;
  /** 本次请求总耗时（毫秒）。 */
  elapsedMs: number | null;
  /** 输出吞吐（token/s）。 */
  throughput: number | null;
  costUsd: number | null;
  /** 用量是按字符估算的（上游没回 usage）。 */
  estimated: boolean;
}

export interface PlaygroundMessage {
  id: string;
  role: "system" | "user" | "assistant";
  content: string;
  model?: string;
  createdAt: number;
  /** 助手消息仍在流式接收中。 */
  streaming?: boolean;
  /** 用户手动停了生成，已收到的部分内容保留。 */
  stopped?: boolean;
  /** 这条回复是流式（SSE）逐字渲染出来的。 */
  streamed?: boolean;
  /** 思考型模型返回的思考内容；为空表示这次回复没有思考块。 */
  reasoning?: string;
  /** 思考块来源标记（reasoning_effort=low / thinking=on / think / think 标签）。 */
  thinkingLabel?: string;
  /** 请求失败的原因（只在前端展示，不入请求体）。 */
  error?: string;
  requestId?: string;
  usage?: PlaygroundUsage;
  attachments?: PlaygroundAttachment[];
}

/** 空串 → undefined；非数字 → undefined（由 validateChatParams 报错）。 */
export function parseOptionalNumber(raw: string | undefined): number | undefined {
  const text = (raw ?? "").trim();
  if (!text) return undefined;
  const value = Number(text);
  return Number.isFinite(value) ? value : undefined;
}

export interface ChatParamInput {
  temperature?: string;
  topP?: string;
  topK?: string;
  frequencyPenalty?: string;
  presencePenalty?: string;
  seed?: string;
  maxTokens?: string;
  maxCompletionTokens?: string;
  /** stop 多值：每行一个，空行忽略。 */
  stop?: string;
  n?: string;
  responseFormat?: ResponseFormatType;
  logprobs?: boolean;
  topLogprobs?: string;
  user?: string;
  /** 思考控件：off 不指定；none..max 发 reasoning_effort；thinking / enable_thinking 发厂商开关。 */
  thinkingMode?: ThinkingMode;
  /** 额外请求字段（JSON 对象）；厂商私有字段的兜底透传。 */
  extraFields?: string;
}

/** 参数面板 label 下方那行小字的文案：取值范围 + 一句话用途。 */
export interface ParamHint {
  /** OpenAI 兼容口径的取值区间；注明「上游未校验」表示越界由上游自行决定。 */
  range: string;
  /** 一句话用途，写给第一次用这个参数的人。 */
  usage: string;
}

/**
 * 参数说明文案的唯一来源：面板只负责排版，文案集中在这里，
 * 和 validateChatParams 的区间一起校对（改口径先改这里）。
 */
export const PARAM_HINTS = {
  temperature: { range: "0.0–2.0", usage: "越高输出越随机，越低越确定" },
  topP: { range: "0.0–1.0", usage: "与 temperature 二选一调整，越小候选越少" },
  topK: { range: "≥ 0 的整数", usage: "只从概率最高的前 k 个候选里挑；上游未校验" },
  frequencyPenalty: { range: "−2.0–2.0", usage: "按出现次数压低重复词，越高越不容易复读" },
  presencePenalty: { range: "−2.0–2.0", usage: "按是否出现过压低，越高越愿意换话题" },
  seed: { range: "整数", usage: "固定随机种子便于复现；上游未校验，可能被忽略" },
  maxTokens: { range: "1–模型上限", usage: "本次回复最多生成的 token 数" },
  maxCompletionTokens: { range: "1–模型上限", usage: "与 max_tokens 二选一，新版接口用它替代" },
  stop: { range: "最多 4 条，每行一条", usage: "命中任一条就停止生成，停止词不进回复" },
  n: { range: "1–128", usage: "一次生成几份候选回复；多数渠道只支持 1" },
  responseFormat: { range: "text / json_object", usage: "json_object 要求提示词里明确要求 JSON" },
  logprobs: { range: "开 / 关", usage: "返回每个生成 token 的对数概率，用于排查" },
  topLogprobs: { range: "0–20", usage: "返回前 k 个候选词的概率；需先打开 logprobs" },
  user: { range: "≤ 256 字符", usage: "给上游的稳定用户标识，用于滥用追踪" },
  thinkingMode: {
    range: "不指定 / effort 五档 / thinking / enable_thinking",
    usage: "原样发给上游；DeepSeek thinking 优先于 effort，都不发时默认开思考",
  },
  extraFields: { range: "顶层 JSON 对象", usage: "厂商私有字段原样合并进请求体，后写覆盖先写" },
  systemPrompt: { range: "可选", usage: "作为 system 消息置顶，本会话每次请求都带" },
  apiKey: { range: "留空用登录令牌", usage: "只留在浏览器内存，随请求发 Authorization" },
  streamMode: { range: "开 / 关", usage: "开=逐条收 SSE；关=等完整回复一次返回" },
  reasoningLevel: { range: "只读", usage: "渠道模型登记值，仅作默认提示，不改请求" },
} satisfies Record<string, ParamHint>;

export type ParamHintKey = keyof typeof PARAM_HINTS;

/** 组装面板那行小字：取值范围 · 用途；range 可覆盖（如 max_tokens 的实际上限）。 */
export function paramHintText(key: ParamHintKey, range?: string): string {
  const hint = PARAM_HINTS[key];
  return (range ?? hint.range) + " · " + hint.usage;
}

/** 模型没登记 max_output_tokens 时沿用旧页的输出上限。 */
export const DEFAULT_MAX_OUTPUT_TOKENS = 32768;

/** 输出上限：优先用渠道模型登记的 parameters.max_output_tokens，0=未登记则回退。 */
export function maxOutputTokensFor(registered: number | null | undefined): number {
  return registered && registered > 0 ? registered : DEFAULT_MAX_OUTPUT_TOKENS;
}

export interface ChatParamValidation {
  valid: boolean;
  message?: string;
}

export interface ChatParamValidationOptions {
  /** 所选渠道模型登记的 parameters.max_output_tokens；0/缺省表示未登记。 */
  maxOutputTokens?: number | null;
}

/** stop 多值输入 → 真正发出去的数组；一行一条，保序去空行。 */
export function parseStopSequences(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function isIntegerInRange(value: number | undefined, min: number, max: number): boolean {
  return value !== undefined && Number.isInteger(value) && value >= min && value <= max;
}

/**
 * 请求前的内联校验：只要有一条不合法就拦住发送，错误文案直接显示在输入区。
 * 输出去上限来自所选模型（见 maxOutputTokensFor），不再写死 32768。
 */
export function validateChatParams(
  input: ChatParamInput,
  options: ChatParamValidationOptions = {},
): ChatParamValidation {
  const limit = maxOutputTokensFor(options.maxOutputTokens);
  const temperature = parseOptionalNumber(input.temperature);
  const topP = parseOptionalNumber(input.topP);
  const topK = parseOptionalNumber(input.topK);
  const frequencyPenalty = parseOptionalNumber(input.frequencyPenalty);
  const presencePenalty = parseOptionalNumber(input.presencePenalty);
  const seed = parseOptionalNumber(input.seed);
  const maxTokens = parseOptionalNumber(input.maxTokens);
  const maxCompletionTokens = parseOptionalNumber(input.maxCompletionTokens);
  const n = parseOptionalNumber(input.n);
  const topLogprobs = parseOptionalNumber(input.topLogprobs);

  if (input.temperature?.trim() && (temperature === undefined || temperature < 0 || temperature > 2)) {
    return { valid: false, message: "temperature 需在 0–2 之间。" };
  }
  if (input.topP?.trim() && (topP === undefined || topP < 0 || topP > 1)) {
    return { valid: false, message: "top_p 需在 0–1 之间。" };
  }
  if (input.topK?.trim() && !isIntegerInRange(topK, 0, Number.MAX_SAFE_INTEGER)) {
    return { valid: false, message: "top_k 需是 ≥0 的整数。" };
  }
  if (
    input.frequencyPenalty?.trim() &&
    (frequencyPenalty === undefined || frequencyPenalty < -2 || frequencyPenalty > 2)
  ) {
    return { valid: false, message: "frequency_penalty 需在 −2–2 之间。" };
  }
  if (
    input.presencePenalty?.trim() &&
    (presencePenalty === undefined || presencePenalty < -2 || presencePenalty > 2)
  ) {
    return { valid: false, message: "presence_penalty 需在 −2–2 之间。" };
  }
  if (input.seed?.trim() && !isIntegerInRange(seed, Number.MIN_SAFE_INTEGER, Number.MAX_SAFE_INTEGER)) {
    return { valid: false, message: "seed 需是整数。" };
  }
  if (input.maxTokens?.trim() && !isIntegerInRange(maxTokens, 1, limit)) {
    return { valid: false, message: `max_tokens 需是 1–${limit} 的整数（上限取自所选模型）。` };
  }
  if (
    input.maxCompletionTokens?.trim() &&
    !isIntegerInRange(maxCompletionTokens, 1, limit)
  ) {
    return {
      valid: false,
      message: `max_completion_tokens 需是 1–${limit} 的整数（上限取自所选模型）。`,
    };
  }
  if (input.n?.trim() && !isIntegerInRange(n, 1, 128)) {
    return { valid: false, message: "n 需是 1–128 的整数。" };
  }
  if (parseStopSequences(input.stop).length > 4) {
    return { valid: false, message: "stop 最多 4 条，每行一条。" };
  }
  if (input.topLogprobs?.trim()) {
    if (!input.logprobs) {
      return { valid: false, message: "top_logprobs 需要先打开 logprobs。" };
    }
    if (!isIntegerInRange(topLogprobs, 0, 20)) {
      return { valid: false, message: "top_logprobs 需是 0–20 的整数。" };
    }
  }
  if (input.user?.trim() && input.user.trim().length > 256) {
    return { valid: false, message: "user 最多 256 个字符。" };
  }
  const extra = parseExtraFields(input.extraFields);
  if (!extra.ok) {
    return { valid: false, message: extra.error };
  }
  return { valid: true };
}

/** 输入框里的字符串 → 上游认识的请求参数；留空的一律不发送。 */
export function chatParamsFromInput(input: ChatParamInput): ChatParams {
  const stop = parseStopSequences(input.stop);
  const mode = input.thinkingMode ?? "off";
  const effort =
    mode === "none" || mode === "low" || mode === "medium" || mode === "high" || mode === "max"
      ? mode
      : undefined;
  return {
    temperature: parseOptionalNumber(input.temperature),
    top_p: parseOptionalNumber(input.topP),
    top_k: parseOptionalNumber(input.topK),
    frequency_penalty: parseOptionalNumber(input.frequencyPenalty),
    presence_penalty: parseOptionalNumber(input.presencePenalty),
    seed: parseOptionalNumber(input.seed),
    max_tokens: parseOptionalNumber(input.maxTokens),
    max_completion_tokens: parseOptionalNumber(input.maxCompletionTokens),
    stop: stop.length ? stop : undefined,
    n: parseOptionalNumber(input.n),
    response_format:
      input.responseFormat === "json_object" ? { type: "json_object" } : undefined,
    logprobs: input.logprobs ? true : undefined,
    top_logprobs: input.logprobs ? parseOptionalNumber(input.topLogprobs) : undefined,
    user: input.user?.trim() || undefined,
    reasoning_effort: effort,
    thinking: mode === "thinking" ? { type: "enabled" } : undefined,
    enable_thinking: mode === "enable_thinking" ? true : undefined,
    extraFields: parseExtraFields(input.extraFields).value,
  };
}

/** 输入区顶部的一句话摘要：只列这次真正会发送的字段。 */
export function summarizeChatParams(input: ChatParamInput): string {
  const thinkingMode = input.thinkingMode ?? "off";
  const fields: Array<[string, string | undefined]> = [
    ["temperature", input.temperature],
    ["top_p", input.topP],
    ["top_k", input.topK],
    ["max_tokens", input.maxTokens],
    ["stop", parseStopSequences(input.stop).length ? String(parseStopSequences(input.stop).length) + " 条" : undefined],
    [
      "思考",
      thinkingMode === "off"
        ? undefined
        : thinkingMode === "thinking" || thinkingMode === "enable_thinking"
          ? thinkingMode
          : "reasoning_effort=" + thinkingMode,
    ],
    [
      "额外字段",
      Object.keys(parseExtraFields(input.extraFields).value).length
        ? String(Object.keys(parseExtraFields(input.extraFields).value).length) + " 个"
        : undefined,
    ],
  ];
  const parts = fields
    .filter(([, value]) => Boolean(value?.trim()))
    .map(([name, value]) => name + " " + value?.trim());
  return parts.length ? parts.join(" · ") : "全部默认";
}

/**
 * 附件以文本块的形式附在用户消息后面：后端没有多模态入口，
 * 图片/二进制文件无法直接送上游，只保留文件名。
 */
export function buildUserContent(text: string, attachments: PlaygroundAttachment[] = []): string {
  const base = text.trim();
  if (!attachments.length) return base;
  const blocks = attachments.map((item) =>
    item.text ? `[附件 ${item.name}]\n${item.text}` : `[附件 ${item.name}]（无文本内容）`,
  );
  return base ? `${base}\n\n${blocks.join("\n\n")}` : blocks.join("\n\n");
}

/**
 * 聊天记录 → 上游请求的 messages：系统提示词置顶，
 * 没有产出内容的助手消息（失败或空响应）不进上下文。
 */
export function toRequestMessages(messages: PlaygroundMessage[], systemPrompt: string): ChatMessage[] {
  const result: ChatMessage[] = [];
  const system = systemPrompt.trim();
  if (system) result.push({ role: "system", content: system });
  for (const message of messages) {
    if (message.role === "system") continue;
    if (message.role === "assistant" && !message.content.trim()) continue;
    result.push({ role: message.role, content: message.content });
  }
  return result;
}

export interface UsageInput {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  ttftMs?: number | null;
  elapsedMs?: number | null;
  throughput?: number | null;
  costUsd?: number | null;
  estimated?: boolean;
}

/** 把上游 usage / 计时打散成消息下面要展示的一组指标。 */
export function buildUsage(input: UsageInput): PlaygroundUsage {
  const promptTokens = input.promptTokens ?? 0;
  const completionTokens = input.completionTokens ?? 0;
  return {
    promptTokens,
    completionTokens,
    totalTokens: input.totalTokens ?? promptTokens + completionTokens,
    ttftMs: input.ttftMs ?? null,
    elapsedMs: input.elapsedMs ?? null,
    throughput: input.throughput ?? null,
    costUsd: input.costUsd ?? null,
    estimated: input.estimated ?? false,
  };
}

/**
 * 管理端日志里的成本 / TTFT / 吞吐比浏览器拿到的 usage 更准，
 * 有日志时用日志覆盖，没有的字段保留原值。
 */
export function mergeLogUsage(
  usage: PlaygroundUsage | undefined,
  log: RequestLog | null | undefined,
): PlaygroundUsage | undefined {
  if (!usage) return undefined;
  if (!log) return usage;
  return {
    promptTokens: log.prompt_tokens ?? usage.promptTokens,
    completionTokens: log.completion_tokens ?? usage.completionTokens,
    totalTokens: log.prompt_tokens + log.completion_tokens || usage.totalTokens,
    ttftMs: log.ttft_ms ?? usage.ttftMs,
    elapsedMs: log.total_ms ?? usage.elapsedMs,
    throughput: log.throughput_tps ?? usage.throughput,
    costUsd: log.cost_usd ?? usage.costUsd,
    estimated: log.usage_estimated || usage.estimated,
  };
}

/** 解析浏览器里存的聊天记录；坏数据整段丢弃，不让旧格式把页面打挂。 */
export function parsePlaygroundHistory(raw: string | null): PlaygroundMessage[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isPlaygroundMessage).slice(-PLAYGROUND_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function isPlaygroundMessage(value: unknown): value is PlaygroundMessage {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<PlaygroundMessage>;
  return (
    typeof item.id === "string" &&
    (item.role === "user" || item.role === "assistant" || item.role === "system") &&
    typeof item.content === "string" &&
    typeof item.createdAt === "number"
  );
}

/** 落盘前的序列化：去掉「正在流式」这类临时状态，并截断到上限条数。 */
export function serializePlaygroundHistory(messages: PlaygroundMessage[]): string {
  const trimmed = messages.slice(-PLAYGROUND_HISTORY_LIMIT).map((message) => ({
    ...message,
    streaming: false,
  }));
  return JSON.stringify(trimmed);
}

