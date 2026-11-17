import { comboTargetKey, type Combo, type ComboTargetType } from "@/lib/combos";
import type { CatalogItem } from "@/lib/playground";

/** 可编辑成员：对话框内的一行，key 仅用于 React 列表与拖拽，不进入保存 payload。 */
export interface EditableMember {
  key: string;
  targetType: ComboTargetType;
  channelModelId: string;
  childComboId: string;
  priority: number;
}

/** 待选成员：右侧两列候选的一条，key 为目标唯一键（targetType:targetId）。 */
export interface MemberCandidate {
  key: string;
  targetType: ComboTargetType;
  targetId: string;
  label: string;
  searchText: string;
  groupKey: string;
  groupLabel: string;
  blocked: boolean;
  blockedReason: string;
  /** 状态标注：被封禁的渠道模型标「已封禁」。封禁只能手动解除、组合是长期配置，故不阻止加入。 */
  note: string;
}

/** 切换规则的可编辑行。 */
export interface EditableRule {
  key: string;
  statuses: string;
  bodyRegex: string;
  action: "next" | "stop";
}

/** 生成成员行 key；sequence 由调用方给出（当前最大序号 + 1）。 */
export function nextKey(sequence: number): string {
  return `member-${sequence}`;
}

/** 生成规则行 key；sequence 由调用方给出（当前最大序号 + 1）。 */
export function ruleKey(sequence: number): string {
  return `rule-${sequence}`;
}

/** 读取一组 key（member-N / rule-N）中的最大序号，空集合返回 0。 */
export function maxKeySequence(keys: Iterable<string>): number {
  let max = 0;
  for (const key of keys) {
    const match = /-(\d+)$/.exec(key);
    if (!match) continue;
    const value = Number.parseInt(match[1], 10);
    if (Number.isFinite(value) && value > max) max = value;
  }
  return max;
}

/**
 * 新成员默认 priority：并入当前最低层（已有成员里最小的 priority），没有成员时取 1。
 * 取最小值而不做 min-1 / 全员 +1：priority 有 ge=1 下限，min-1 会非法；全员 +1 会改写
 * 用户手调过的数字。默认语义只在这里定义，弹窗只调用、不内联算式。
 */
export function nextMemberPriority(members: readonly { priority: number }[]): number {
  if (members.length === 0) return 1;
  return members.reduce(
    (min, member) => (member.priority < min ? member.priority : min),
    members[0].priority,
  );
}

/** 组合 → 可编辑成员，按 priority 降序（数值越大越先调用）并重新编号 key。 */
export function fromCombo(combo: Combo | null | undefined): EditableMember[] {
  if (!combo) return [];
  return combo.members
    .slice()
    .sort((left, right) => right.priority - left.priority)
    .map((member, index) => ({
      key: nextKey(index + 1),
      targetType: member.target_type,
      channelModelId: member.channel_model_id ?? "",
      childComboId: member.child_combo_id ?? "",
      priority: member.priority,
    }));
}

/** 收集 rootId 的全部后代子组合 id（用于禁止把自身或后代选为子组合）。 */
export function descendantIds(combos: Combo[], rootId: string): Set<string> {
  const byId = new Map(combos.map((combo) => [combo.id, combo]));
  const found = new Set<string>();
  // rootId 也算已访问：否则 A→B→A 这种环会把 root 自己当成后代加回来。
  const visited = new Set<string>([rootId]);
  const walk = (id: string) => {
    const combo = byId.get(id);
    if (!combo) return;
    for (const member of combo.members) {
      const childId = member.child_combo_id;
      if (!childId || visited.has(childId)) continue;
      visited.add(childId);
      found.add(childId);
      walk(childId);
    }
  };
  walk(rootId);
  return found;
}

/** 渠道模型 + 子组合 → 待选列表；排除当前组合自身与已阻断的子组合。
 * 被封禁（enabled=false）的渠道模型仍留在候选里，只标注「已封禁」：封禁只能手动解除，组合是长期配置。 */
export function buildCandidates(
  catalog: CatalogItem[],
  combos: Combo[],
  currentComboId: string | null,
  blockedChildren: Set<string>,
): MemberCandidate[] {
  const candidates: MemberCandidate[] = catalog.map((item) => {
    const label = `${item.channel_slug}/${item.call_model}`;
    return {
      key: comboTargetKey("channel_model", item.id),
      targetType: "channel_model",
      targetId: item.id,
      label,
      searchText: [label, item.name, item.upstream_model, item.channel_name].join(" "),
      groupKey: `channel:${item.channel_id}`,
      groupLabel: item.channel_name || item.channel_slug,
      blocked: false,
      blockedReason: "",
      note: item.enabled ? "" : "已封禁",
    };
  });
  for (const item of combos) {
    if (item.id === currentComboId || blockedChildren.has(item.id)) continue;
    candidates.push({
      key: comboTargetKey("child_combo", item.id),
      targetType: "child_combo",
      targetId: item.id,
      label: item.name,
      searchText: [item.name, item.description].join(" "),
      groupKey: "child_combo",
      groupLabel: "子组合",
      blocked: !item.enabled,
      blockedReason: "已封禁",
      note: "",
    });
  }
  return candidates;
}

const HAN_TEXT = /[\u3400-\u9fff]/;
const COLLATOR_LATIN = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });
const COLLATOR_HAN = new Intl.Collator("zh-Hans-CN", { numeric: true, sensitivity: "base" });

/** 按展示名排序：英文名在前（自然序），中文名按拼音，数字按自然序。 */
export function sortCandidatesByName(candidates: MemberCandidate[]): MemberCandidate[] {
  return candidates.slice().sort((left, right) => {
    const bothHan = HAN_TEXT.test(left.label) && HAN_TEXT.test(right.label);
    return (bothHan ? COLLATOR_HAN : COLLATOR_LATIN).compare(left.label, right.label);
  });
}

/** 规则状态码输入 → 数组：三位数字转 number，其余保留字符串。 */
export function parseStatuses(value: string): Array<number | string> {
  return value
    .split(/[,，\s]+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .map((token) => (/^\d{3}$/.test(token) ? Number.parseInt(token, 10) : token));
}
