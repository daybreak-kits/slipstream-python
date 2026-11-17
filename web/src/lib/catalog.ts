import { ApiError, apiRequest } from "@/lib/api";
import {
  BATCH_LIMIT,
  batchUpdateChannelModels,
  type ScheduleDocument,
} from "@/lib/channel-models";
import { describeApiError } from "@/lib/channels";
import type { Combo, ComboMember } from "@/lib/combos";
import type { ImplicitGroup, ImplicitGroupMember } from "@/lib/groups";
import type { CatalogEntry } from "@/lib/playground";

/**
 * 模型管理页的条目口径（ADR-067）：
 * - kind 只有 direct | combo，旧 kind=group 已取消，本页不做兼容分支；
 * - origin 只是组合的属性（手动组合 / 自动组合），直连没有 origin；
 * - 定时封禁是 enabled=false + banned_until（epoch ms），读取时惰性判活，不引定时器。
 */
export type EntryKind = "direct" | "combo";
export type ComboOrigin = "manual" | "auto";

/** 目录条目的前端规范化结果。 */
export interface ManagementEntry extends Omit<CatalogEntry, "kind"> {
  kind: EntryKind;
  /** 仅组合有来源；直连恒为 null。 */
  origin: ComboOrigin | null;
  /** 定时封禁到期时间（epoch ms）；null 表示非定时封禁（含手动封禁）。 */
  banned_until: number | null;
}

interface RawOrigin {
  origin?: string | null;
  banned_until?: number | null;
}

export function isEntryKind(value: unknown): value is EntryKind {
  return value === "direct" || value === "combo";
}

/**
 * 目录接口 → 可渲染条目。按 ADR-067 只接受 direct / combo；
 * 组合的来源优先读接口的 origin，缺省时用「手动组合 id 集合」推断；
 * 其它 kind（含历史 group）视为契约违约，直接跳过，不猜成组合。
 */
export function normalizeEntries(
  entries: readonly CatalogEntry[],
  manualComboIds: ReadonlySet<string> = new Set(),
): ManagementEntry[] {
  const normalized: ManagementEntry[] = [];
  for (const entry of entries) {
    if (!isEntryKind(entry.kind)) continue;
    const raw = entry as CatalogEntry & RawOrigin;
    normalized.push({
      ...(entry as Omit<CatalogEntry, "kind">),
      kind: entry.kind,
      origin:
        entry.kind === "combo"
          ? raw.origin === "auto"
            ? "auto"
            : raw.origin === "manual"
              ? "manual"
              : manualComboIds.has(entry.id)
                ? "manual"
                : "auto"
          : null,
      banned_until: entry.kind === "direct" ? (raw.banned_until ?? null) : null,
    });
  }
  return normalized;
}

export interface PriorityLayerText {
  priority: number;
  labels: string[];
}

/**
 * 优先级分层 → 一行提示串，形如 "P2 model-a, model-b / P1 model-c"。
 * 层按 priority 从大到小排列（数值越大越先调用），同层成员逗号并列。
 */
export function priorityLayerText(layers: readonly PriorityLayerText[]): string {
  return layers
    .slice()
    .sort((left, right) => right.priority - left.priority)
    .map((layer) => "P" + layer.priority + " " + layer.labels.join(", "))
    .join(" / ");
}

export function comboLayerTexts(combo: Combo): PriorityLayerText[] {
  const layers = new Map<number, string[]>();
  for (const member of combo.members) {
    const bucket = layers.get(member.priority) ?? [];
    bucket.push(memberLabel(member));
    layers.set(member.priority, bucket);
  }
  return [...layers.entries()].map(([priority, labels]) => ({ priority, labels }));
}

export function groupLayerTexts(group: ImplicitGroup): PriorityLayerText[] {
  const layers = new Map<number, string[]>();
  for (const member of group.members) {
    const bucket = layers.get(member.priority) ?? [];
    bucket.push(groupMemberLabel(member));
    layers.set(member.priority, bucket);
  }
  return [...layers.entries()].map(([priority, labels]) => ({ priority, labels }));
}

function memberLabel(member: ComboMember): string {
  if (member.target_type === "child_combo") {
    return member.child_combo_name || member.call_model || "子组合";
  }
  return member.channel_slug + "/" + member.call_model;
}

function groupMemberLabel(member: ImplicitGroupMember): string {
  return member.channel_slug + "/" + member.call_model;
}

/**
 * 定时封禁或启用：沿用 channel-models 的 PATCH 路由。
 * banned_until 与 enabled 一起提交；契约要求 ISO-8601 时刻，null 表示永久封禁
 * （后端 enabled=false 缺省 banned_until 即永久封禁，enabled=true 会清掉封禁）。
 */
export async function patchChannelModel(
  modelId: string,
  body: Record<string, unknown>,
): Promise<void> {
  await apiRequest<unknown>("/api/channel-models/" + modelId, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** 单条写可用性：enabled + banned_until（null = 永久封禁；启用会清掉封禁）。 */
export async function setModelAvailability(
  modelId: string,
  input: { enabled: boolean; bannedUntil: number | null },
): Promise<void> {
  await patchChannelModel(modelId, {
    enabled: input.enabled,
    banned_until: input.bannedUntil === null ? null : new Date(input.bannedUntil).toISOString(),
  });
}

export interface BulkAvailabilityOutcome {
  succeeded: number;
  failed: number;
  /** 逐条失败原因，交给 toast 汇总。 */
  errors: Array<{ modelId: string; reason: string }>;
  /** 走的哪条路径：批量端点在时 true，退化串行时 false。 */
  viaBatch: boolean;
}

export type BulkAvailabilityInput =
  | { op: "ban" | "unban" }
  | { op: "cooldown"; bannedUntil: number }
  | { op: "schedule"; schedule: ScheduleDocument };

/**
 * 批量写可用性（ADR-070 §4.1）。
 *
 * 优先走 PATCH /api/channel-models/batch：一次事务改多行，失败条目逐条回报。
 * 该端点尚未上线时（404/405）退化为串行单条 PATCH，界面照常可用——
 * 后端落地后自动切回批量路径，前端不需要再改。
 */
export async function applyModelAvailabilityBulk(
  modelIds: readonly string[],
  input: BulkAvailabilityInput,
): Promise<BulkAvailabilityOutcome> {
  const ids = [...new Set(modelIds)].filter(Boolean);
  if (ids.length === 0) return { succeeded: 0, failed: 0, errors: [], viaBatch: true };

  try {
    const outcome = await runBatch(ids, input);
    return { ...outcome, viaBatch: true };
  } catch (error) {
    if (!isBatchEndpointMissing(error)) {
      throw error;
    }
    // 排期只有批量端点这一种写法：退化到单条 PATCH 会被当成「永久封禁」，
    // 端点缺失时宁可报错，也不静默把模型停掉。
    if (input.op === "schedule") {
      throw new ApiError(404, {
        code: "BATCH_ENDPOINT_MISSING",
        message: "当前后端不支持排期：缺少 PATCH /api/channel-models/batch",
      });
    }
    const errors: Array<{ modelId: string; reason: string }> = [];
    const results = await Promise.all(
      ids.map(async (modelId) => {
        try {
          await setModelAvailability(modelId, fallbackBody(input));
          return true;
        } catch (singleError) {
          errors.push({ modelId, reason: describeApiError(singleError) });
          return false;
        }
      }),
    );
    const succeeded = results.filter(Boolean).length;
    return { succeeded, failed: ids.length - succeeded, errors, viaBatch: false };
  }
}

async function runBatch(
  ids: readonly string[],
  input: BulkAvailabilityInput,
): Promise<Omit<BulkAvailabilityOutcome, "viaBatch">> {
  const errors: Array<{ modelId: string; reason: string }> = [];
  let succeeded = 0;
  for (let start = 0; start < ids.length; start += BATCH_LIMIT) {
    const chunk = ids.slice(start, start + BATCH_LIMIT);
    const result = await batchUpdateChannelModels({
      modelIds: chunk,
      op: input.op,
      bannedUntil: input.op === "cooldown" ? new Date(input.bannedUntil).toISOString() : null,
      schedule: input.op === "schedule" ? input.schedule : undefined,
    });
    succeeded += result.succeeded;
    for (const item of result.results) {
      if (!item.ok) errors.push({ modelId: item.model_id, reason: item.error || "操作失败" });
    }
  }
  return { succeeded, failed: errors.length, errors };
}

function fallbackBody(
  input: Exclude<BulkAvailabilityInput, { op: "schedule" }>,
): { enabled: boolean; bannedUntil: number | null } {
  if (input.op === "unban") return { enabled: true, bannedUntil: null };
  if (input.op === "cooldown") return { enabled: false, bannedUntil: input.bannedUntil };
  return { enabled: false, bannedUntil: null };
}

function isBatchEndpointMissing(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}
