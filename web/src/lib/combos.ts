import { apiRequest } from "@/lib/api";
import type { RouteStrategy } from "@/lib/settings";

export type ComboTargetType = "channel_model" | "child_combo";

/** 组合级路由覆盖：空字符串表示跟随全局策略。 */
export type ComboRouteStrategy = "" | RouteStrategy;

export interface ComboMember {
  id: string;
  target_type: ComboTargetType;
  priority: number;
  enabled: boolean;
  available: boolean;
  channel_model_id: string | null;
  child_combo_id: string | null;
  channel_id: string | null;
  channel_slug: string;
  channel_name: string;
  call_model: string;
  upstream_model: string;
  child_combo_name: string;
  child_combo_enabled: boolean;
  protocol: string;
  created_at: number;
}

export interface ComboFailoverRule {
  status: Array<number | string>;
  body_regex: string | null;
  action: "next" | "stop";
}

export interface ComboFailoverRules {
  rules: ComboFailoverRule[];
  default_action: "next" | "stop";
}

export interface Combo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  max_attempts: number;
  route_strategy?: ComboRouteStrategy;
  route_window_minutes?: number;
  failover_rules: ComboFailoverRules;
  member_count: number;
  available_count: number;
  depth: number;
  max_depth: number;
  callable: boolean;
  created_at: number;
  updated_at: number;
  members: ComboMember[];
}

export interface ComboList {
  items: Combo[];
  total: number;
}

export interface ComboTargetInput {
  target_type: ComboTargetType;
  channel_model_id?: string | null;
  child_combo_id?: string | null;
  priority: number;
  enabled: boolean;
}

export interface ComboInput {
  name: string;
  description: string;
  enabled: boolean;
  max_attempts?: number;
  route_strategy?: ComboRouteStrategy;
  route_window_minutes?: number;
  failover_rules?: ComboFailoverRules;
  targets: ComboTargetInput[];
}

export async function listCombos(): Promise<ComboList> {
  return apiRequest<ComboList>("/api/combos");
}

export async function createCombo(input: ComboInput): Promise<Combo> {
  return apiRequest<Combo>("/api/combos", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateCombo(id: string, input: Partial<ComboInput>): Promise<Combo> {
  return apiRequest<Combo>(`/api/combos/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteCombo(id: string): Promise<void> {
  await apiRequest<null>(`/api/combos/${id}`, { method: "DELETE" });
}

export function comboTargetKey(targetType: ComboTargetType, targetId: string): string {
  return `${targetType}:${targetId}`;
}

/** 成员展示顺序：priority 降序（数值越大越先调用，P40 排在 P1 前面）。 */
export function sortMembersByPriority<T extends { priority: number }>(items: readonly T[]): T[] {
  return items.slice().sort((left, right) => right.priority - left.priority);
}

export function priorityLayerCounts(members: readonly { priority: number }[]): Map<number, number> {
  const counts = new Map<number, number>();
  for (const member of members) {
    counts.set(member.priority, (counts.get(member.priority) ?? 0) + 1);
  }
  return counts;
}
