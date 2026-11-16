import {
  ArrowDown,
  ArrowLeftRight,
  ArrowUp,
  Boxes,
  ChevronDown,
  GripVertical,
  Info,
  Layers,
  Plus,
  Save,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  comboTargetKey,
  createCombo,
  priorityLayerCounts,
  sortMembersByPriority,
  updateCombo,
  type Combo,
  type ComboFailoverRule,
  type ComboInput,
  type ComboRouteStrategy,
  type ComboTargetType,
} from "@/lib/combos";
import { describeApiError } from "@/lib/channels";
import { parseRouteWindow, ROUTE_STRATEGY_OPTIONS, type RouteStrategy } from "@/lib/settings";
import {
  buildCandidates,
  descendantIds,
  fromCombo,
  maxKeySequence,
  nextKey,
  nextMemberPriority,
  parseStatuses,
  ruleKey,
  sortCandidatesByName,
  type EditableMember,
  type EditableRule,
  type MemberCandidate,
} from "@/lib/combos-editor";
import type { CatalogItem } from "@/lib/playground";
import { useRovingTabs } from "@/lib/use-roving-tabs";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

const COMBO_NAME_RE = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;

/** failover 动作的用户可见文案；底层枚举值 next / stop 保持不变。 */
const FAILOVER_ACTION_LABEL: Record<"next" | "stop", string> = {
  next: "继续下一个成员",
  stop: "立即停止",
};


function selectedDepth(
  members: EditableMember[],
  comboById: Map<string, Combo>,
): number {
  let depth = 1;
  for (const member of members) {
    if (member.targetType !== "child_combo" || !member.childComboId) continue;
    const child = comboById.get(member.childComboId);
    depth = Math.max(depth, 1 + (child?.depth ?? 1));
  }
  return depth;
}

function targetIdOf(member: EditableMember): string {
  return member.targetType === "channel_model" ? member.channelModelId : member.childComboId;
}

function memberTargetKey(member: EditableMember): string | null {
  const targetId = targetIdOf(member);
  return targetId ? comboTargetKey(member.targetType, targetId) : null;
}

function memberLabel(
  member: EditableMember,
  comboById: Map<string, Combo>,
  catalogById: Map<string, CatalogItem>,
): string {
  if (member.targetType === "child_combo") {
    if (!member.childComboId) return "未选择目标";
    return comboById.get(member.childComboId)?.name ?? "已删除的子组合";
  }
  if (!member.channelModelId) return "未选择目标";
  const item = catalogById.get(member.channelModelId);
  if (!item) return "已删除的渠道模型";
  const label = `${item.channel_slug}/${item.call_model}`;
  return item.enabled ? label : `${label}（已封禁）`;
}

const CANDIDATE_TABS: Array<{ key: ComboTargetType; label: string; icon: typeof Layers }> = [
  { key: "channel_model", label: "渠道模型", icon: Boxes },
  { key: "child_combo", label: "子组合", icon: Layers },
];

interface ComboEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  combo?: Combo | null;
  combos: Combo[];
  catalog: CatalogItem[];
  initialChannelModelId?: string | null;
  onSaved?: (combo: Combo) => void;
}

export function ComboEditorDialog({
  open,
  onOpenChange,
  combo,
  combos,
  catalog,
  initialChannelModelId,
  onSaved,
}: ComboEditorDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [routeStrategy, setRouteStrategy] = useState<ComboRouteStrategy>("");
  const [routeWindowChoice, setRouteWindowChoice] = useState<string>("global");
  const [routeWindowCustom, setRouteWindowCustom] = useState("30");
  const [defaultAction, setDefaultAction] = useState<"next" | "stop">("next");
  const [rules, setRules] = useState<EditableRule[]>([]);
  const [members, setMembers] = useState<EditableMember[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [selectedQuery, setSelectedQuery] = useState("");
  const [availableQuery, setAvailableQuery] = useState("");
  const [availableTab, setAvailableTab] = useState<ComboTargetType>("channel_model");
  const { onKeyDown, tabProps } = useRovingTabs(
    CANDIDATE_TABS.map((tab) => tab.key),
    availableTab,
    setAvailableTab,
  );
  const [availableGrouped, setAvailableGrouped] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const [draggingKey, setDraggingKey] = useState<string | null>(null);
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(combo?.name ?? "");
    setDescription(combo?.description ?? "");
    setEnabled(combo?.enabled ?? true);
    setMaxAttempts(combo?.max_attempts ?? 3);
    setRouteStrategy(combo?.route_strategy ?? "");
    const savedWindow = combo?.route_window_minutes ?? 0;
    setRouteWindowChoice(
      savedWindow === 0
        ? "global"
        : ([15, 30, 60] as readonly number[]).includes(savedWindow)
          ? String(savedWindow)
          : "custom",
    );
    setRouteWindowCustom(String(savedWindow === 0 ? 30 : savedWindow));
    setDefaultAction(combo?.failover_rules?.default_action ?? "next");
    setRules(
      (combo?.failover_rules?.rules ?? []).map((rule, index) => ({
        key: ruleKey(index + 1),
        statuses: (rule.status ?? []).join(", "),
        bodyRegex: rule.body_regex ?? "",
        action: rule.action ?? "next",
      })),
    );
    if (!combo && initialChannelModelId) {
      setMembers([
        {
          key: nextKey(1),
          targetType: "channel_model",
          channelModelId: initialChannelModelId,
          childComboId: "",
          priority: 1,
        },
      ]);
    } else {
      setMembers(fromCombo(combo));
    }
    setSelectedQuery("");
    setAvailableQuery("");
    setAvailableTab("channel_model");
    setAvailableGrouped(true);
    setCollapsedGroups(new Set());
    setDraggingKey(null);
    setDragOverKey(null);
    setFormError(null);
    setSaving(false);
  }, [open, combo, initialChannelModelId]);

  const comboById = useMemo(() => new Map(combos.map((item) => [item.id, item])), [combos]);
  const blockedChildren = useMemo(
    () => (combo ? descendantIds(combos, combo.id) : new Set<string>()),
    [combos, combo],
  );
  const depth = useMemo(() => selectedDepth(members, comboById), [members, comboById]);

  const catalogById = useMemo(() => new Map(catalog.map((item) => [item.id, item])), [catalog]);
  const candidates = useMemo(
    () => buildCandidates(catalog, combos, combo?.id ?? null, blockedChildren),
    [catalog, combos, combo?.id, blockedChildren],
  );
  const sortedMembers = useMemo(() => sortMembersByPriority(members), [members]);
  const layerCounts = useMemo(() => priorityLayerCounts(members), [members]);
  const selectedKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const member of members) {
      const key = memberTargetKey(member);
      if (key) keys.add(key);
    }
    return keys;
  }, [members]);
  const draggingMember = useMemo(
    () => (draggingKey ? sortedMembers.find((member) => member.key === draggingKey) ?? null : null),
    [sortedMembers, draggingKey],
  );
  const visibleSelected = useMemo(() => {
    const needle = selectedQuery.trim().toLowerCase();
    if (!needle) return sortedMembers;
    return sortedMembers.filter((member) =>
      memberLabel(member, comboById, catalogById).toLowerCase().includes(needle),
    );
  }, [sortedMembers, selectedQuery, comboById, catalogById]);
  const visibleCandidates = useMemo(() => {
    const needle = availableQuery.trim().toLowerCase();
    const filtered = candidates.filter((candidate) => {
      if (candidate.targetType !== availableTab) return false;
      if (!needle) return true;
      return candidate.searchText.toLowerCase().includes(needle);
    });
    return sortCandidatesByName(filtered);
  }, [candidates, availableTab, availableQuery]);
  const candidateGroups = useMemo(() => {
    const groups = new Map<string, { key: string; label: string; items: MemberCandidate[] }>();
    for (const candidate of visibleCandidates) {
      const bucket = groups.get(candidate.groupKey) ?? {
        key: candidate.groupKey,
        label: candidate.groupLabel,
        items: [],
      };
      bucket.items.push(candidate);
      groups.set(candidate.groupKey, bucket);
    }
    return [...groups.values()].sort((left, right) => left.label.localeCompare(right.label, "zh-Hans-CN"));
  }, [visibleCandidates]);
  const updateMember = (key: string, patch: Partial<EditableMember>) => {
    setMembers((current) =>
      current.map((member) => (member.key === key ? { ...member, ...patch } : member)),
    );
  };

  const addCandidate = (candidate: MemberCandidate) => {
    setMembers((current) => {
      if (current.some((member) => memberTargetKey(member) === candidate.key)) return current;
      return [
        ...current,
        {
          key: nextKey(maxKeySequence(current.map((member) => member.key)) + 1),
          targetType: candidate.targetType,
          channelModelId: candidate.targetType === "channel_model" ? candidate.targetId : "",
          childComboId: candidate.targetType === "child_combo" ? candidate.targetId : "",
          priority: nextMemberPriority(current),
        },
      ];
    });
  };

  const removeMember = (key: string) => {
    setMembers((current) => current.filter((member) => member.key !== key));
  };

  const swapPriorities = (fromKey: string, toKey: string) => {
    if (fromKey === toKey) return;
    setMembers((current) => {
      const from = current.find((member) => member.key === fromKey);
      const to = current.find((member) => member.key === toKey);
      if (!from || !to) return current;
      return current.map((member) => {
        if (member.key === fromKey) return { ...member, priority: to.priority };
        if (member.key === toKey) return { ...member, priority: from.priority };
        return member;
      });
    });
  };

  const moveMember = (key: string, direction: -1 | 1) => {
    const index = sortedMembers.findIndex((member) => member.key === key);
    if (index < 0) return;
    const current = sortedMembers[index];
    const neighbour = sortedMembers[index + direction];
    if (!neighbour || neighbour.priority === current.priority) return;
    swapPriorities(key, neighbour.key);
  };

  const toggleCandidateGroup = (groupKey: string) => {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      return next;
    });
  };

  const renderCandidateChip = (candidate: MemberCandidate) => {
    const added = selectedKeys.has(candidate.key);
    const disabled = added || candidate.blocked;
    return (
        <TooltipProvider key={candidate.key}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                disabled={disabled}
                aria-label={`添加 ${candidate.label}`}
                onClick={() => addCandidate(candidate)}
                className={cn(
                  "inline-flex max-w-full items-center gap-1 rounded-badge border px-2 py-1 text-2xs transition-colors",
                  disabled
                    ? "cursor-not-allowed border-border bg-surface-2 text-muted-foreground"
                    : "border-border bg-surface text-body hover:border-primary hover:bg-primary-soft hover:text-primary",
                )}
              >
                {candidate.targetType === "child_combo" ? (
                  <Layers className="h-3 w-3 shrink-0 text-primary" strokeWidth={1.75} aria-hidden="true" />
                ) : null}
                <span className="truncate font-mono">{candidate.label}</span>
                {added ? <span className="shrink-0 text-2xs text-muted-foreground">已添加</span> : null}
                {!added && candidate.blocked ? (
                  <span className="shrink-0 text-2xs text-warning-text">{candidate.blockedReason}</span>
                ) : null}
                {!added && !candidate.blocked && candidate.note ? (
                  <span className="shrink-0 text-2xs text-warning-text">{candidate.note}</span>
                ) : null}
              </button>
            </TooltipTrigger>
            <TooltipContent>
              {added
                ? `${candidate.label} 已在组合中`
                : candidate.blocked
                  ? candidate.blockedReason
                  : candidate.note
                    ? `${candidate.label} ${candidate.note}——封禁与冷却只是不可用，成员仍可加入组合`
                    : `加入组合，priority 取 ${nextMemberPriority(members)}`}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
    );
  };

  const save = async () => {
    setFormError(null);
    const cleanName = name.trim();
    if (!cleanName) {
      setFormError("请输入组合名称");
      return;
    }
    if (cleanName.includes("/")) {
      setFormError("组合名称不能包含斜杠");
      return;
    }
    if (!COMBO_NAME_RE.test(cleanName)) {
      setFormError("组合名称需以字母开头，仅含字母、数字、下划线或中划线，最长 64 位");
      return;
    }
    if (depth > 3) {
      setFormError("组合嵌套最多 3 层，请先调整子组合");
      return;
    }

    if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 50) {
      setFormError("最大轮数必须是 1 到 50 的整数");
      return;
    }

    let routeWindowMinutes = 0;
    if (routeWindowChoice === "custom") {
      const parsedWindow = parseRouteWindow(routeWindowCustom);
      if (typeof parsedWindow === "string") {
        setFormError(`组合窗口${parsedWindow}`);
        return;
      }
      routeWindowMinutes = parsedWindow;
    } else if (routeWindowChoice !== "global") {
      routeWindowMinutes = Number(routeWindowChoice);
    }

    const targets: ComboInput["targets"] = [];
    const seen = new Set<string>();
    for (const [index, member] of sortedMembers.entries()) {
      const targetId = targetIdOf(member);
      if (!targetId) {
        setFormError(`请选择第 ${index + 1} 个成员的目标`);
        return;
      }
      if (member.targetType === "child_combo") {
        const child = comboById.get(targetId);
        if (child && !child.enabled) {
          setFormError(`子组合 ${child.name} 已封禁，不能引用`);
          return;
        }
      }
      const dedupeKey = comboTargetKey(member.targetType, targetId);
      if (seen.has(dedupeKey)) {
        setFormError("成员目标重复，请移除重复项");
        return;
      }
      seen.add(dedupeKey);
      targets.push({
        target_type: member.targetType,
        channel_model_id: member.targetType === "channel_model" ? targetId : null,
        child_combo_id: member.targetType === "child_combo" ? targetId : null,
        priority: Math.max(1, Number.isFinite(member.priority) ? Math.trunc(member.priority) : 1),
        // 成员行不再提供启用开关；组合内的成员始终参与调度，封禁请移除该成员。
        enabled: true,
      });
    }

    setSaving(true);
    try {
      const failoverRules: ComboFailoverRule[] = rules.map((rule) => ({
        status: parseStatuses(rule.statuses),
        body_regex: rule.bodyRegex.trim() || null,
        action: rule.action,
      }));
      const payload: ComboInput = {
        name: cleanName,
        description: description.trim(),
        enabled,
        max_attempts: maxAttempts,
        route_strategy: routeStrategy,
        route_window_minutes: routeWindowMinutes,
        failover_rules: { rules: failoverRules, default_action: defaultAction },
        targets,
      };
      const saved = combo ? await updateCombo(combo.id, payload) : await createCombo(payload);
      onSaved?.(saved);
      onOpenChange(false);
    } catch (error) {
      setFormError(describeApiError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && saving) return;
        onOpenChange(next);
      }}
    >
      <DialogContent
        className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-h-[calc(100dvh-5rem)] sm:max-w-5xl"
      >
      <DialogHeader className="relative shrink-0 gap-0 space-y-0 border-b border-border px-5 py-4 pr-12 text-left">
        <DialogTitle className="text-sm">{combo ? "编辑手动组合" : "新建手动组合"}</DialogTitle>
        <DialogDescription className="mt-0.5 text-xs">
          本组合的切换规则与最大轮数管整次请求；子组合只贡献成员与 priority。
        </DialogDescription>
      </DialogHeader>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {formError ? (
              <Alert variant="destructive">
                {formError}
              </Alert>
            ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="combo-name">手动组合名称</FieldLabel>
            <Input
              id="combo-name"
              aria-label="手动组合名称"
              className="font-mono"
              placeholder="team-chat"
              autoComplete="off"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <FieldDescription>裸名（不含斜杠），不能与渠道标识或自动组合重名。</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="combo-description">描述</FieldLabel>
            <Input
              id="combo-description"
              placeholder="可选，例如：国内优先"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <label className="flex w-fit cursor-pointer items-center gap-2 text-2sm text-body">
            <Checkbox
              id="combo-enabled"
              checked={enabled}
              onCheckedChange={(checked) => setEnabled(checked === true)}
            />
            启用该组合
          </label>
          <span
            className={cn(
              "rounded-badge px-2 py-1 font-mono text-2xs",
              depth > 3 ? "bg-danger-soft text-danger-text" : "bg-surface-2 text-muted-foreground",
            )}
          >
            嵌套深度 {depth}/3
          </span>
        </div>

        <div className="grid gap-3 rounded-control border border-border bg-surface-2 p-3 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="combo-max-attempts">最大轮数 max_attempts</FieldLabel>
            <Input
              id="combo-max-attempts"
              className="tabular-nums"
              type="number"
              min={1}
              max={50}
              value={maxAttempts}
              onChange={(event) => setMaxAttempts(Number.parseInt(event.target.value || "1", 10) || 1)}
            />
            <FieldDescription>一轮 = 当前可用成员各试一次；1–50 轮。</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="combo-default-action">未命中规则时</FieldLabel>
            <Select
              value={defaultAction}
              onValueChange={(value) => setDefaultAction(value as "next" | "stop")}
            >
              <SelectTrigger id="combo-default-action" className="h-10 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="next">{FAILOVER_ACTION_LABEL.next}</SelectItem>
                <SelectItem value="stop">{FAILOVER_ACTION_LABEL.stop}</SelectItem>
              </SelectContent>
            </Select>
            <FieldDescription>配置规则后，未命中项按此动作处理。</FieldDescription>
          </Field>
        </div>

        <div className="grid gap-3 rounded-control border border-border bg-surface-2 p-3 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="combo-route-strategy">路由策略覆盖</FieldLabel>
            <Select
              value={routeStrategy || "global"}
              onValueChange={(value) =>
                setRouteStrategy(value === "global" ? "" : (value as RouteStrategy))
              }
            >
              <SelectTrigger
                id="combo-route-strategy"
                className="h-10 w-full"
                aria-label="路由策略覆盖"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="global">跟随全局</SelectItem>
                {ROUTE_STRATEGY_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FieldDescription>
              留空跟随全局策略；设置后仅改变本组合同层成员的排序方式。
            </FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="combo-route-window">观察窗口覆盖</FieldLabel>
            <div className="flex gap-2">
              <Select value={routeWindowChoice} onValueChange={setRouteWindowChoice}>
                <SelectTrigger
                  id="combo-route-window"
                  className="h-10 flex-1"
                  aria-label="观察窗口覆盖"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="global">跟随全局</SelectItem>
                  <SelectItem value="15">15 分钟</SelectItem>
                  <SelectItem value="30">30 分钟</SelectItem>
                  <SelectItem value="60">60 分钟</SelectItem>
                  <SelectItem value="custom">自定义</SelectItem>
                </SelectContent>
              </Select>
              {routeWindowChoice === "custom" ? (
                <Input
                  aria-label="组合自定义窗口（分钟）"
                  inputMode="numeric"
                  className="w-24 font-mono text-xs"
                  value={routeWindowCustom}
                  onChange={(event) => setRouteWindowCustom(event.target.value)}
                />
              ) : null}
            </div>
            <FieldDescription>
              跟随全局时使用系统设置里的窗口；自定义范围 15–1440 分钟。
            </FieldDescription>
          </Field>
        </div>

        <div className="rounded-control border border-border">
          <div className="flex items-center justify-between border-b border-border bg-surface-2 px-3 py-2">
            <span className="text-xs font-medium text-title">切换规则 failover_rules</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setRules((current) => [
                  ...current,
                  {
                    key: ruleKey(maxKeySequence(current.map((rule) => rule.key)) + 1),
                    statuses: "429, 500-599",
                    bodyRegex: "",
                    action: "next",
                  },
                ])
              }
            >
              <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              添加规则
            </Button>
          </div>
          {rules.length === 0 ? (
            <p className="px-3 py-3 text-xs text-muted-foreground">
              未配置切换规则时，可重试的传输错误自动切换到下一个成员；命中 429/5xx 等可自定义 stop。
            </p>
          ) : (
            <div className="divide-y divide-border">
              {rules.map((rule, index) => (
                <div
                  key={rule.key}
                  className="grid gap-2 px-3 py-3 sm:grid-cols-[120px_minmax(0,1fr)_110px_auto] sm:items-center"
                >
                  <Input
                    aria-label={`规则 ${index + 1} 状态码`}
                    placeholder="429, 500-599"
                    value={rule.statuses}
                    onChange={(event) =>
                      setRules((current) =>
                        current.map((item) =>
                          item.key === rule.key ? { ...item, statuses: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <Input
                    aria-label={`规则 ${index + 1} 响应正文正则`}
                    placeholder="可选，响应正文正则（不区分大小写）"
                    value={rule.bodyRegex}
                    onChange={(event) =>
                      setRules((current) =>
                        current.map((item) =>
                          item.key === rule.key ? { ...item, bodyRegex: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <Select
                    value={rule.action}
                    onValueChange={(value) =>
                      setRules((current) =>
                        current.map((item) =>
                          item.key === rule.key ? { ...item, action: value as "next" | "stop" } : item,
                        ),
                      )
                    }
                  >
                    <SelectTrigger aria-label={`规则 ${index + 1} 动作`} className="h-10 w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="next">{FAILOVER_ACTION_LABEL.next}</SelectItem>
                      <SelectItem value="stop">{FAILOVER_ACTION_LABEL.stop}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`移除规则 ${index + 1}`}
                    onClick={() =>
                      setRules((current) => current.filter((item) => item.key !== rule.key))
                    }
                  >
                    <Trash2 className="h-4 w-4 text-danger" strokeWidth={1.75} aria-hidden="true" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-title">成员与 priority</span>
            <span className="text-2xs text-muted-foreground">
              拖动左列成员到另一成员，或点行尾的上移/下移，可直接互换 priority
            </span>
          </div>

          <div className="rounded-control border border-border bg-surface-2 px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-xs font-medium text-title">
              <Info className="h-3.5 w-3.5 text-primary" strokeWidth={1.75} aria-hidden="true" />
              priority 是「层」，不是序号
            </p>
            <p className="mt-1 text-2xs text-body">
              方向：数值<b className="text-title">越大越先调用</b>；同一层的成员等概率随机分流。
            </p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-2xs text-body">
              <li>
                <span className="font-mono text-title">priority 不同</span>：数值<b>越大越先调用</b>，本层成员全部失败才进入下一层。
              </li>
              <li>
                <span className="font-mono text-title">priority 相同</span>：同一层内<b>随机负载均衡</b>，成员之间没有先后顺序。
              </li>
              <li>priority 不表示成员序号，也不表示调用次数，它只决定成员落在哪一层。</li>
            </ul>
          </div>

          <div className="flex flex-col gap-3 lg:flex-row">
            <div className="flex min-w-0 flex-1 flex-col rounded-control border border-border">
              <div className="flex flex-col gap-2 border-b border-border bg-surface-2 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-title">已选成员</span>
                  <span className="rounded-badge bg-surface px-1.5 py-0.5 font-mono text-2xs tabular-nums text-muted-foreground">
                    {members.length} 个
                  </span>
                </div>
                <InputGroup className="h-8">
                  <InputGroupAddon>
                    <Search className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  </InputGroupAddon>
                  <InputGroupInput
                    aria-label="搜索已选成员"
                    className="h-8 text-xs"
                    placeholder="按名称筛选已选成员"
                    value={selectedQuery}
                    onChange={(event) => setSelectedQuery(event.target.value)}
                  />
                  {selectedQuery ? (
                    <InputGroupAddon align="inline-end">
                      <InputGroupButton
                        aria-label="清空已选搜索"
                        className="h-6 w-6"
                        onClick={() => setSelectedQuery("")}
                      >
                        <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                      </InputGroupButton>
                    </InputGroupAddon>
                  ) : null}
                </InputGroup>
              </div>

              {members.length === 0 ? (
                <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                  还没有成员。在右侧点击模型或子组合即可加入，priority 默认并入当前最低层（取已有成员的最小值；空组合从 1 开始）。
                </p>
              ) : visibleSelected.length === 0 ? (
                <p className="px-3 py-6 text-center text-xs text-muted-foreground">没有匹配的已选成员。</p>
              ) : (
                <ul role="list" aria-label="已选成员列表" className="flex flex-col">
                  {visibleSelected.map((member) => {
                    const index = sortedMembers.findIndex((item) => item.key === member.key);
                    const label = memberLabel(member, comboById, catalogById);
                    const layerSize = layerCounts.get(member.priority) ?? 1;
                    const previous = sortedMembers[index - 1];
                    const next = sortedMembers[index + 1];
                    const isDropTarget =
                      draggingMember !== null && dragOverKey === member.key && draggingMember.key !== member.key;
                    return (
                      <li
                        key={member.key}
                        onDragOver={(event) => {
                          if (!draggingMember || draggingMember.key === member.key) return;
                          event.preventDefault();
                          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
                          if (dragOverKey !== member.key) setDragOverKey(member.key);
                        }}
                        onDragLeave={(event) => {
                          const nextTarget = event.relatedTarget as Node | null;
                          if (nextTarget && event.currentTarget.contains(nextTarget)) return;
                          if (dragOverKey === member.key) setDragOverKey(null);
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          if (draggingMember && draggingMember.key !== member.key) {
                            swapPriorities(draggingMember.key, member.key);
                          }
                          setDraggingKey(null);
                          setDragOverKey(null);
                        }}
                        className={cn(
                          "flex flex-wrap items-center gap-1.5 border-b border-border px-2 py-2 last:border-b-0",
                          isDropTarget && "bg-primary-soft ring-1 ring-inset ring-primary",
                        )}
                      >
                        <span
                          draggable
                          aria-label={`拖动 ${label} 调整优先级`}
                          title="按住拖动到另一成员，互换两者的 priority"
                          onDragStart={(event) => {
                            setDraggingKey(member.key);
                            setDragOverKey(null);
                            const transfer = event.dataTransfer;
                            if (transfer && typeof transfer.setData === "function") {
                              transfer.effectAllowed = "move";
                              transfer.setData("text/plain", member.key);
                            }
                          }}
                          onDragEnd={() => {
                            setDraggingKey(null);
                            setDragOverKey(null);
                          }}
                          className={cn(
                            "inline-flex h-8 w-6 shrink-0 cursor-grab items-center justify-center rounded-md text-faint transition-colors hover:bg-surface-2 hover:text-muted-foreground",
                            draggingMember?.key === member.key && "cursor-grabbing text-primary",
                          )}
                        >
                          <GripVertical className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        </span>

                        <Input
                          aria-label={`${label} priority`}
                          type="number"
                          min={1}
                          max={1000}
                          className="h-8 w-17 shrink-0 tabular-nums"
                          value={member.priority}
                          onChange={(event) =>
                            updateMember(member.key, {
                              priority: Math.max(1, Number.parseInt(event.target.value || "1", 10) || 1),
                            })
                          }
                        />

                        <span className="flex min-w-0 flex-1 items-center gap-1.5">
                          {member.targetType === "child_combo" ? (
                            <Layers
                              className="h-3 w-3 shrink-0 text-primary"
                              strokeWidth={1.75}
                              aria-hidden="true"
                            />
                          ) : null}
                          <span className="truncate font-mono text-xs text-title" title={label}>
                            {label}
                          </span>
                          {member.targetType === "child_combo" ? (
                            <span className="shrink-0 text-2xs text-muted-foreground">子组合</span>
                          ) : null}
                          {layerSize > 1 ? (
                            <Badge
                              tone="default"
                              title={`本层共 ${layerSize} 个成员，请求时随机负载均衡，不分先后`}
                              className="shrink-0 px-1.5 py-0.5"
                            >
                              同层 ×{layerSize}
                            </Badge>
                          ) : null}
                        </span>

                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          aria-label={`上移成员 ${index + 1}`}

                          disabled={!previous || previous.priority === member.priority}
                          onClick={() => moveMember(member.key, -1)}
                        >
                          <ArrowUp className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        </Button>

                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          aria-label={`下移成员 ${index + 1}`}

                          disabled={!next || next.priority === member.priority}
                          onClick={() => moveMember(member.key, 1)}
                        >
                          <ArrowDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        </Button>

                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          aria-label={`移除成员 ${index + 1}`}
                          onClick={() => removeMember(member.key)}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-danger" strokeWidth={1.75} aria-hidden="true" />
                        </Button>

                        {isDropTarget && draggingMember ? (
                          <span className="ml-auto flex w-full animate-fade-in items-center gap-1 rounded-badge bg-surface px-2 py-1 text-2xs font-medium text-primary sm:w-auto">
                            <ArrowLeftRight className="h-3 w-3 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                            {`将互换 ${memberLabel(draggingMember, comboById, catalogById)} / ${label} 优先级 ${draggingMember.priority} ↔ ${member.priority}`}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="flex min-w-0 flex-1 flex-col rounded-control border border-border">
              <div className="border-b border-border bg-surface-2">
                <div
                  role="tablist"
                  aria-label="待选成员类型"
                  className="flex items-center gap-1 border-b border-border px-2"
                  onKeyDown={onKeyDown}
                >
                  {CANDIDATE_TABS.map((tab) => {
                    const active = availableTab === tab.key;
                    const Icon = tab.icon;
                    return (
                      <button
                        key={tab.key}
                        {...tabProps(tab.key)}
                        id={`candidate-tab-${tab.key}`}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        aria-controls="candidate-tabpanel"
                        className={cn(
                          "relative inline-flex shrink-0 items-center gap-1.5 px-2.5 py-2 text-xs transition-colors",
                          active ? "font-medium text-primary" : "text-muted-foreground hover:text-title",
                        )}
                      >
                        <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                        {tab.label}
                        {active ? (
                          <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-primary" aria-hidden="true" />
                        ) : null}
                      </button>
                    );
                  })}
                  <span className="ml-auto pr-1 font-mono text-2xs tabular-nums text-muted-foreground">
                    {visibleCandidates.length} /
                    {candidates.filter((item) => item.targetType === availableTab).length}
                  </span>
                </div>
                <div
                  id="candidate-tabpanel"
                  role="tabpanel"
                  aria-labelledby={`candidate-tab-${availableTab}`}
                  className="flex min-h-0 flex-1 flex-col"
                >
                <div className="space-y-2 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <InputGroup className="h-8 min-w-0 flex-1">
                      <InputGroupAddon>
                        <Search className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                      </InputGroupAddon>
                      <InputGroupInput
                        aria-label="搜索待选成员"
                        className="h-8 text-xs"
                        placeholder="按名称 / 上游模型 / 渠道筛选"
                        value={availableQuery}
                        onChange={(event) => setAvailableQuery(event.target.value)}
                      />
                      {availableQuery ? (
                        <InputGroupAddon align="inline-end">
                          <InputGroupButton
                            aria-label="清空待选搜索"
                            className="h-6 w-6"
                            onClick={() => setAvailableQuery("")}
                          >
                            <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                          </InputGroupButton>
                        </InputGroupAddon>
                      ) : null}
                    </InputGroup>
                    <Button
                      variant={availableGrouped ? "secondary" : "outline"}
                      size="sm"
                      className="h-8 shrink-0"
                      aria-pressed={availableGrouped}
                      aria-label={availableGrouped ? "切换为平铺展示" : "切换为按渠道分组"}
                      onClick={() => setAvailableGrouped((current) => !current)}
                    >
                      <Layers className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                      {availableGrouped ? "平铺" : "分组"}
                    </Button>
                  </div>
                  <p className="text-2xs text-muted-foreground">
                    点击加入；已加入的条目置灰不可重复加入，priority 自动并入当前最低层（数值越大越先调用，同层等概率随机）。
                  </p>
                </div>
              </div>

              <div className="max-h-80 min-h-0 flex-1 overflow-y-auto px-2.5 py-2.5">
                {visibleCandidates.length === 0 ? (
                  <p className="px-1 py-5 text-center text-xs text-muted-foreground">
                    {availableQuery.trim()
                      ? "没有匹配的成员"
                      : availableTab === "child_combo"
                        ? "没有可用的子组合"
                        : "没有可用的渠道模型"}
                  </p>
                ) : availableGrouped ? (
                  <div className="space-y-2">
                    {candidateGroups.map((group) => {
                      const collapsed = collapsedGroups.has(group.key);
                      return (
                        <Collapsible
                          key={group.key}
                          open={!collapsed}
                          onOpenChange={() => toggleCandidateGroup(group.key)}
                        >
                          <CollapsibleTrigger className="flex w-full items-center gap-1.5 rounded-badge px-1.5 py-1 text-left text-2xs text-title transition-colors hover:bg-surface-2">
                            <ChevronDown
                              className={cn(
                                "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                                collapsed && "-rotate-90",
                              )}
                              strokeWidth={1.75}
                              aria-hidden="true"
                            />
                            <span className="truncate font-medium">{group.label}</span>
                            <span className="shrink-0 rounded-badge bg-surface-2 px-1 font-mono text-2xs tabular-nums text-muted-foreground">
                              {group.items.length}
                            </span>
                          </CollapsibleTrigger>
                          <CollapsibleContent>
                            <div className="flex flex-wrap gap-1.5 pb-1 pl-5 pt-1">
                              {group.items.map(renderCandidateChip)}
                            </div>
                          </CollapsibleContent>
                        </Collapsible>
                      );
                    })}
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">{visibleCandidates.map(renderCandidateChip)}</div>
                )}
              </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <DialogFooter className="shrink-0 gap-2 border-t border-border bg-surface-2 px-5 py-3.5">
        <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
          取消
        </Button>
        <Button onClick={save} disabled={saving}>
          {saving ? (
            <Spinner aria-hidden="true" />
          ) : (
            <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          )}
          保存组合
        </Button>
      </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
