import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CalendarOff, Check, ChevronDown, ChevronUp, Copy, Pencil, Plus, RefreshCw, Search, Server, Trash2, Unlock } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { AvailabilityControl } from "@/components/catalog/availability-control";
import { ChannelModelFormDialog } from "@/components/channels/channel-model-form-dialog";
import { ComboMarker } from "@/components/catalog/combo-marker";
import { ComboEditorDialog } from "@/components/combos/combo-editor-dialog";
import { AddToComboDialog, type ComboTargetRef } from "@/components/models/add-to-combo-dialog";
import { BulkActionBar } from "@/components/models/bulk-action-bar";
import { cooldownAxes, type CooldownAxes } from "@/components/models/cooldown-axes";
import { CooldownDialog, type CooldownSubmit } from "@/components/models/cooldown-dialog";
import { AvailabilityStatusCell, availabilityView } from "@/components/models/model-status";
import { ScheduleEditorDialog } from "@/components/models/schedule-editor-dialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Empty } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { copyText } from "@/lib/clipboard";
import {
  applyModelAvailabilityBulk,
  comboLayerTexts,
  groupLayerTexts,
  normalizeEntries,
  setModelAvailability,
  type BulkAvailabilityInput,
  type BulkAvailabilityOutcome,
  type ManagementEntry,
  type PriorityLayerText,
} from "@/lib/catalog";
import { describeApiError, PROTOCOL_LABELS } from "@/lib/channels";
import {
  describeDefaultsStatus,
  describeRefreshResult,
  fetchModelDefaultsStatus,
  refreshModelDefaults,
} from "@/lib/model-defaults";
import {
  listChannelModels,
  localScheduleTimezone,
  normalizeSchedule,
  resetChannelModel,
  type ChannelModel,
  type ScheduleDocument,
} from "@/lib/channel-models";
import {
  createCombo,
  deleteCombo,
  listCombos,
  updateCombo,
  type Combo,
  type ComboTargetInput,
} from "@/lib/combos";
import {
  aggregateComboParams,
  comboParamLines,
  comboPriceRangeText,
  type ComboParamSource,
  type ComboParamUnion,
} from "@/lib/combo-params";
import { formatPricePer1m } from "@/lib/format";
import { listImplicitGroups, type ImplicitGroup } from "@/lib/groups";
import { notify } from "@/lib/notify";
import { managementCatalogQuery, type CatalogItem } from "@/lib/playground";
import { SELECT_ALL_VALUE } from "@/lib/select-sentinels";
import { useTimezone } from "@/lib/time";

type KindFilter = "all" | "direct" | "combo" | "manual" | "auto";

const KIND_FILTERS: Array<{ value: KindFilter; label: string }> = [
  { value: "all", label: "全部类型" },
  { value: "direct", label: "直连" },
  { value: "combo", label: "全部组合" },
  { value: "manual", label: "手动组合" },
  { value: "auto", label: "自动组合" },
];

const PROTOCOL_FILTERS = [
  { value: "", label: "全部协议" },
  { value: "openai_chat", label: PROTOCOL_LABELS.openai_chat },
  { value: "anthropic_messages", label: PROTOCOL_LABELS.anthropic_messages },
  { value: "gemini", label: PROTOCOL_LABELS.gemini },
];

const PAGE_SIZE = 25;

/** 多选的稳定键：直连与组合的 id 各自的命名空间互不冲突。 */
function rowKeyOf(entry: ManagementEntry): string {
  return entry.kind + ":" + entry.id;
}

/** 名称超长时截断，全文放 tooltip；不超长就不挂浮层，避免每行两个 Portal。 */
function TruncatedName({ text }: { text: string }) {
  const short = text.length <= 24;
  const body = (
    <span className="max-w-64 truncate" title={short ? undefined : text}>
      {text}
    </span>
  );
  if (short) return body;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{body}</TooltipTrigger>
        <TooltipContent maxLines={2}>{text}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** 复制调用串：成功给出对勾，失败走通知（不静默）。 */
function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await copyText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      notify.error("复制失败，请手动选择文本");
    }
  };
  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-8 w-8"
      aria-label={"复制调用串 " + value}
      onClick={() => void copy()}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-success-text" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </Button>
  );
}

interface ManagementRow {
  entry: ManagementEntry;
  /** 手动组合的详情（编辑/删除用）。 */
  combo: Combo | null;
  layers: PriorityLayerText[];
  /** 组合行的参数并集（能力或 / 上限 max / 价格区间）；直连行为 null。 */
  comboParams: ComboParamUnion | null;
}

interface AvailabilityTarget {
  id: string;
  enabled: boolean;
  bannedUntil: number | null;
  reset: boolean;
}

/** 「解除冷却」的目标行：一次清掉该行命中的冷却轴。 */
interface ReleaseCooldownTarget {
  id: string;
  name: string;
  axes: CooldownAxes;
  /** 写空规则集时沿用的排期时区；该行没有排期就退回浏览器时区。 */
  timezone: string;
}

/** 旧链接 /combos 并入模型管理页；location.state 需保留给 addMember / create 预设。 */
export function CombosRedirect() {
  const location = useLocation();
  return <Navigate to="/catalog?kind=combo" replace state={location.state} />;
}

/**
 * 模型管理：直连、手动组合、自动组合统一到一页。
 * 类型筛选由 ?kind= 驱动（旧书签 /combos 与 ?tab=combos 都会落到组合），
 * 行内操作按类型分流：直连 复制/组合/封禁·启用；手动组合 编辑/删除；自动组合只读。
 */
export function ModelsPage() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { mode } = useTimezone();
  const tableRef = useRef<HTMLDivElement>(null);

  const [kindFilter, setKindFilter] = useState<KindFilter>(() => {
    if (searchParams.get("tab") === "combos") return "combo";
    const kind = searchParams.get("kind");
    if (kind === "direct" || kind === "combo" || kind === "manual" || kind === "auto") return kind;
    return "all";
  });
  const [protocolFilter, setProtocolFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Combo | null>(null);
  const [initialMember, setInitialMember] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Combo | null>(null);
  const [selected, setSelected] = useState<Record<string, true>>({});
  const [sort, setSort] = useState<{ key: "name" | "status"; dir: "asc" | "desc" }>({
    key: "name",
    dir: "asc",
  });
  const [cooldownOpen, setCooldownOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  // 打开弹窗时把既有排期取成快照：normalizeSchedule 每次返回新对象，
  // 若随渲染重算会把用户正在编辑的规则冲掉。
  const [scheduleInitial, setScheduleInitial] = useState<ScheduleDocument | null>(null);
  const [addToComboOpen, setAddToComboOpen] = useState(false);
  const [bulkConfirm, setBulkConfirm] = useState<"ban" | "enable" | null>(null);
  const [bulkPending, setBulkPending] = useState(false);
  // 待确认的「解除冷却」行；弹窗确认后才真正发请求。
  const [releaseTarget, setReleaseTarget] = useState<ReleaseCooldownTarget | null>(null);

  // 旧 ?tab=combos 书签：落到组合筛选，并清掉 tab 参数。
  useEffect(() => {
    if (searchParams.get("tab") !== "combos") return;
    const params = new URLSearchParams(searchParams);
    params.delete("tab");
    params.set("kind", "combo");
    setSearchParams(params, { replace: true });
  }, [searchParams, setSearchParams]);

  // 从旧 /combos 进来时把表格滚到视口内（书签「定位到组合」）。
  useEffect(() => {
    if (searchParams.get("kind") === "combo" || searchParams.get("tab") === "combos") {
      tableRef.current?.scrollIntoView({ block: "start" });
    }
  }, [searchParams]);

  const catalogQuery = useQuery(managementCatalogQuery);
  const combosQuery = useQuery({ queryKey: ["combos"], queryFn: listCombos });
  const groupsQuery = useQuery({ queryKey: ["implicit-groups"], queryFn: listImplicitGroups });
  // 状态排序与徽标都以本次渲染的时刻为准；数据刷新后重新取值，避免长期停在旧时刻。
  const nowMs = useMemo(() => Date.now(), [catalogQuery.dataUpdatedAt]);

  const combos = combosQuery.data?.items ?? [];
  const groups = groupsQuery.data?.items ?? [];
  const manualComboIds = useMemo(() => new Set(combos.map((combo) => combo.id)), [combos]);
  const entries = useMemo(
    () => normalizeEntries(catalogQuery.data?.items ?? [], manualComboIds),
    [catalogQuery.data, manualComboIds],
  );

  const rows = useMemo<ManagementRow[]>(() => {
    const comboById = new Map(combos.map((combo) => [combo.id, combo]));
    const comboByName = new Map(combos.map((combo) => [combo.name, combo]));
    const groupByName = new Map(groups.map((group) => [group.name, group]));
    const directById = new Map(
      entries.filter((entry) => entry.kind === "direct").map((entry) => [entry.id, entry]),
    );
    // 组合成员的渠道模型 id：手动组合按 members 递归展开子组合，自动组合按隐式分组成员。
    const channelModelIds = (combo: Combo | null, group: ImplicitGroup | null): string[] => {
      const ids: string[] = [];
      const visited = new Set<string>();
      const visitCombo = (id: string): void => {
        if (visited.has(id)) return;
        visited.add(id);
        const current = comboById.get(id);
        if (!current) return;
        for (const member of current.members) {
          if (member.target_type === "child_combo" && member.child_combo_id) {
            visitCombo(member.child_combo_id);
          } else if (member.channel_model_id) {
            ids.push(member.channel_model_id);
          }
        }
      };
      if (combo) visitCombo(combo.id);
      if (group) for (const member of group.members) ids.push(member.channel_model_id);
      return ids;
    };
    return entries.map((entry) => {
      if (entry.kind === "direct") return { entry, combo: null, layers: [], comboParams: null };
      const combo = comboById.get(entry.id) ?? comboByName.get(entry.name) ?? null;
      const group = groupByName.get(entry.name) ?? null;
      const sources: ComboParamSource[] = [];
      for (const id of channelModelIds(combo, group)) {
        const direct = directById.get(id);
        if (!direct) continue;
        sources.push({
          parameters: direct.parameters,
          input_price_per_1m: direct.input_price_per_1m,
          output_price_per_1m: direct.output_price_per_1m,
        });
      }
      return {
        entry,
        combo,
        layers: combo ? comboLayerTexts(combo) : group ? groupLayerTexts(group) : [],
        // 组合本身不落库参数：并集与价格区间都在查询期按成员算（ADR-073 决策 8）。
        comboParams: sources.length > 0 ? aggregateComboParams(sources) : null,
      };
    });
  }, [entries, combos, groups]);

  // 编辑器只接受真实渠道模型；目录里还混着组合条目。
  const directCatalog = useMemo<CatalogItem[]>(
    () => entries.filter((entry) => entry.kind === "direct"),
    [entries],
  );
  const directCount = entries.filter((entry) => entry.kind === "direct").length;
  const manualCount = entries.filter(
    (entry) => entry.kind === "combo" && entry.origin === "manual",
  ).length;
  const autoCount = entries.filter(
    (entry) => entry.kind === "combo" && entry.origin === "auto",
  ).length;

  // 排序（task-138）：类型恒为主序（手动组合 → 自动组合 → 直连），表头只在同类内切换名称 / 状态。
  // 「类型」读作 ADR-067 的 kind（组合 / 直连），组合再按 origin 拆手动、自动——
  // 用户裁定给了「手动组合 → 自动组合 → 直连」的完整秩序，比只分两档更贴合「自动/手动不互相穿插」。
  // 名称用 base 敏感度：大小写与重音不敏感（café 与 cafe 视为同名，按原顺序稳定输出）。
  const nameCollator = useMemo(
    () => new Intl.Collator("zh-Hans-CN", { sensitivity: "base", numeric: true }),
    [],
  );
  const sortedRows = useMemo<ManagementRow[]>(() => {
    const kindRank = (row: ManagementRow) => {
      if (row.entry.kind !== "combo") return 2;
      return row.entry.origin === "manual" ? 0 : 1;
    };
    const toneRank = (row: ManagementRow) => {
      const tone = availabilityView(row.entry, nowMs, mode).tone;
      return tone === "danger" ? 0 : tone === "warn" ? 1 : tone === "muted" ? 2 : 3;
    };
    const factor = sort.dir === "asc" ? 1 : -1;
    return rows.slice().sort((left, right) => {
      const kindDelta = kindRank(left) - kindRank(right);
      if (kindDelta !== 0) return kindDelta;
      if (sort.key === "status") {
        const statusDelta = toneRank(left) - toneRank(right);
        if (statusDelta !== 0) return statusDelta * factor;
      }
      return nameCollator.compare(left.entry.name, right.entry.name) * factor;
    });
  }, [rows, sort, nowMs, mode, nameCollator]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return sortedRows.filter((row) => {
      if (kindFilter === "direct" && row.entry.kind !== "direct") return false;
      if (kindFilter === "combo" && row.entry.kind !== "combo") return false;
      if (kindFilter === "manual" && !(row.entry.kind === "combo" && row.entry.origin === "manual")) {
        return false;
      }
      if (kindFilter === "auto" && !(row.entry.kind === "combo" && row.entry.origin === "auto")) {
        return false;
      }
      if (protocolFilter && row.entry.protocol !== protocolFilter) return false;
      if (!needle) return true;
      return (
        row.entry.name.toLowerCase().includes(needle) ||
        row.entry.upstream_model.toLowerCase().includes(needle) ||
        row.entry.channel_name.toLowerCase().includes(needle)
      );
    });
  }, [sortedRows, kindFilter, protocolFilter, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(Math.max(1, page), totalPages);
  const pageRows = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  // 多选按 kind:id 记录，跨页与排序变化都不丢（排序/翻页不影响已选集合）。
  const pageKeys = pageRows.map((row) => rowKeyOf(row.entry));
  const allPageSelected = pageKeys.length > 0 && pageKeys.every((key) => selected[key]);
  const somePageSelected = pageKeys.some((key) => selected[key]);

  const toggleRow = (entry: ManagementEntry) => {
    const key = rowKeyOf(entry);
    setSelected((current) => {
      const next = { ...current };
      if (next[key]) delete next[key];
      else next[key] = true;
      return next;
    });
  };
  const togglePage = () => {
    setSelected((current) => {
      const next = { ...current };
      for (const key of pageKeys) {
        if (allPageSelected) delete next[key];
        else next[key] = true;
      }
      return next;
    });
  };
  const selectAllFiltered = () => {
    setSelected((current) => {
      const next = { ...current };
      for (const row of filtered) next[rowKeyOf(row.entry)] = true;
      return next;
    });
  };
  const clearSelection = () => setSelected({});

  const toggleSort = (key: "name" | "status") => {
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
    );
  };

  // 筛选条件变化后回到第一页；当前页越界时也夹回最后一页。
  useEffect(() => {
    setPage(1);
  }, [kindFilter, protocolFilter, search, pageSize]);
  useEffect(() => {
    if (page !== currentPage) setPage(currentPage);
  }, [page, currentPage]);

  useEffect(() => {
    const state = location.state as { addMember?: string; create?: boolean } | null;
    if (!state?.addMember && !state?.create) return;
    setEditing(null);
    setInitialMember(state.addMember ?? null);
    setEditorOpen(true);
    navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: null });
  }, [location.pathname, location.search, location.state, navigate]);

  const availabilityMutation = useMutation({
    mutationFn: async (input: AvailabilityTarget) => {
      await setModelAvailability(input.id, {
        enabled: input.enabled,
        bannedUntil: input.bannedUntil,
      });
      if (input.reset) await resetChannelModel(input.id);
    },
    onSuccess: async (_result, input) => {
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      notify.success(input.enabled ? "模型已启用" : "模型已封禁");
    },
    onError: (error, input) =>
      notify.error(input.enabled ? "启用模型失败" : "封禁模型失败", {
        description: describeApiError(error),
      }),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteCombo,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["combos"] });
      await queryClient.invalidateQueries({ queryKey: ["implicit-groups"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      notify.success("组合已删除");
    },
    onError: (error) => notify.error("删除组合失败", { description: describeApiError(error) }),
  });

  const availabilityPending = (id: string): boolean =>
    availabilityMutation.isPending && availabilityMutation.variables?.id === id;

  // 冷却（持续时间 / 计划窗口）统一走批量端点 op=cooldown / op=schedule；
  // 批量端点未落地时 applyModelAvailabilityBulk 会按操作退化（排期不退化，直接报错）。
  const measureMutation = useMutation({
    mutationFn: async (input: { id: string; submit: CooldownSubmit }) => {
      await applyModelAvailabilityBulk(
        [input.id],
        input.submit.kind === "cooldown"
          ? { op: "cooldown", bannedUntil: input.submit.untilMs }
          : { op: "schedule", schedule: input.submit.schedule },
      );
    },
    onSuccess: async (_result, input) => {
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      notify.success(input.submit.kind === "cooldown" ? "已设置冷却" : "已保存排期");
    },
    onError: (error, input) =>
      notify.error(input.submit.kind === "cooldown" ? "冷却设置失败" : "排期保存失败", {
        description: describeApiError(error),
      }),
  });

  const measurePending = (id: string): boolean =>
    measureMutation.isPending && measureMutation.variables?.id === id;

  // 「清除排期」：排期是窗口规则、不是封禁，写空规则集即永不冷却（后端整份覆盖写）。
  // 只改 schedule_json，不动 enabled / banned_until，与启用（复位冷却）互不代替。
  const clearScheduleMutation = useMutation({
    mutationFn: async (input: { id: string; timezone: string }) => {
      await applyModelAvailabilityBulk([input.id], {
        op: "schedule",
        schedule: { version: 2, timezone: input.timezone, rules: [] },
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      notify.success("已清除排期", { description: "该模型立即恢复全天可用" });
    },
    onError: (error) =>
      notify.error("清除排期失败", {
        description: describeApiError(error),
      }),
  });

  const clearSchedulePending = (id: string): boolean =>
    clearScheduleMutation.isPending && clearScheduleMutation.variables?.id === id;

  // 「解除冷却」：一次清掉该行命中的冷却轴——熔断/故障走 POST /reset，人工定时走批量端点
  // op=unban，排期走批量端点 op=schedule 写空规则集（永不冷却）。
  // 三轴互不依赖：熔断先清，其余两轴并行；单轴失败不影响别的轴，最后统一刷新目录。
  const releaseCooldownMutation = useMutation({
    mutationFn: async (input: ReleaseCooldownTarget) => {
      const failures: string[] = [];
      if (input.axes.fault) {
        try {
          await resetChannelModel(input.id);
        } catch (error) {
          failures.push("熔断冷却：" + describeApiError(error));
        }
      }
      const runAxis = async (
        label: string,
        run: () => Promise<BulkAvailabilityOutcome>,
      ): Promise<void> => {
        try {
          const outcome = await run();
          if (outcome.failed > 0) {
            failures.push(label + "：" + (outcome.errors[0]?.reason ?? "操作失败"));
          }
        } catch (error) {
          failures.push(label + "：" + describeApiError(error));
        }
      };
      const steps: Array<Promise<void>> = [];
      if (input.axes.timed) {
        steps.push(
          runAxis("定时冷却", () => applyModelAvailabilityBulk([input.id], { op: "unban" })),
        );
      }
      if (input.axes.schedule) {
        steps.push(
          runAxis("排期冷却", () =>
            applyModelAvailabilityBulk([input.id], {
              op: "schedule",
              schedule: { version: 2, timezone: input.timezone, rules: [] },
            }),
          ),
        );
      }
      await Promise.all(steps);
      return { failures };
    },
    onSuccess: async (result, input) => {
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      if (result.failures.length === 0) {
        notify.success("已解除 " + input.name + " 的冷却，恢复可用");
        return;
      }
      notify.error("解除冷却未全部成功", { description: result.failures.join("；") });
    },
    onError: (error) => notify.error("解除冷却失败", { description: describeApiError(error) }),
  });

  const releaseCooldownPending = (id: string): boolean =>
    releaseCooldownMutation.isPending && releaseCooldownMutation.variables?.id === id;

  // 行内「编辑」（task-137）：按 entry.channel_id 拉渠道模型列表，找到同 id 的模型再打开渠道表单。
  // 目录条目缺表单需要的完整字段，不能拿 CatalogItem 硬凑 ChannelModel。
  const [editTarget, setEditTarget] = useState<ManagementEntry | null>(null);
  const editModelQuery = useQuery({
    queryKey: ["channel-models", "edit-target", editTarget?.channel_id ?? ""],
    queryFn: () => listChannelModels(editTarget!.channel_id),
    enabled: Boolean(editTarget),
  });
  const editingModel = useMemo<ChannelModel | null>(() => {
    if (!editTarget) return null;
    return editModelQuery.data?.items.find((model) => model.id === editTarget.id) ?? null;
  }, [editTarget, editModelQuery.data]);
  useEffect(() => {
    if (!editTarget || !editModelQuery.isSuccess) return;
    if (editingModel === null) {
      notify.error("找不到渠道模型", {
        description: editTarget.name + " 已不在渠道 " + editTarget.channel_name + " 中，请刷新后重试",
      });
      setEditTarget(null);
    }
  }, [editTarget, editModelQuery.isSuccess, editingModel]);

  const selectedRows = useMemo(
    () => rows.filter((row) => Boolean(selected[rowKeyOf(row.entry)])),
    [rows, selected],
  );
  const selectedEntries = selectedRows.map((row) => row.entry);
  const selectedCount = selectedEntries.length;
  const selectedDirectIds = selectedEntries
    .filter((entry) => entry.kind === "direct")
    .map((entry) => entry.id);
  const selectedManualCombos = selectedRows
    .map((row) => row.combo)
    .filter((combo): combo is Combo => combo !== null);
  // 自动组合由成员实时派生，不能单独启停；组合整体也不能加定时（口径见 ADR-070 背景 1）。
  const hasAutoCombo = selectedRows.some(
    (row) => row.entry.kind === "combo" && row.entry.origin === "auto",
  );
  const hasComboSelected = selectedEntries.some((entry) => entry.kind === "combo");
  const comboTargetRefs = useMemo<ComboTargetRef[]>(
    () => selectedEntries.map((entry) => ({ kind: entry.kind, id: entry.id, label: entry.name })),
    // selectedEntries 由 selectedRows 派生，selectedRows 变化即代表选择变化
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedRows],
  );

  /** 批量写可用性：直连走批量端点（未落地时自动退化串行），手动组合逐条 PATCH。 */
  const runBulkAvailability = async (input: BulkAvailabilityInput) => {
    setBulkPending(true);
    const parts: string[] = [];
    const errors: string[] = [];
    try {
      if (selectedDirectIds.length > 0) {
        const outcome = await applyModelAvailabilityBulk(selectedDirectIds, input);
        if (outcome.succeeded > 0) parts.push(outcome.succeeded + " 个直连模型");
        errors.push(...outcome.errors.map((item) => item.reason));
      }
      if (selectedManualCombos.length > 0 && (input.op === "ban" || input.op === "unban")) {
        const enabled = input.op === "unban";
        const settled = await Promise.allSettled(
          selectedManualCombos.map((combo) => updateCombo(combo.id, { enabled })),
        );
        const ok = settled.filter((item) => item.status === "fulfilled").length;
        if (ok > 0) parts.push(ok + " 个手动组合");
        for (const item of settled) {
          if (item.status === "rejected") errors.push(describeApiError(item.reason));
        }
      }
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      await queryClient.invalidateQueries({ queryKey: ["combos"] });
      const verb =
        input.op === "unban"
          ? "已启用"
          : input.op === "ban"
            ? "已封禁（永久）"
            : input.op === "cooldown"
              ? "已设置冷却"
              : "已保存排期";
      if (parts.length > 0) notify.success(verb + " " + parts.join("、"));
      if (errors.length > 0) {
        notify.error(errors.length + " 项失败", { description: errors[0] });
      } else {
        clearSelection();
      }
    } catch (error) {
      notify.error("批量操作失败", { description: describeApiError(error) });
    } finally {
      setBulkPending(false);
      setBulkConfirm(null);
      setCooldownOpen(false);
      setScheduleOpen(false);
    }
  };

  const comboMemberInput = (member: Combo["members"][number]): ComboTargetInput => ({
    target_type: member.target_type,
    channel_model_id: member.channel_model_id,
    child_combo_id: member.child_combo_id,
    priority: member.priority,
    enabled: member.enabled,
  });

  const addToCombos = async (comboIds: string[]) => {
    setBulkPending(true);
    const errors: string[] = [];
    let added = 0;
    try {
      for (const comboId of comboIds) {
        const combo = combos.find((item) => item.id === comboId);
        if (!combo) continue;
        const additions = comboTargetRefs
          .filter(
            (target) =>
              !combo.members.some((member) =>
                target.kind === "direct"
                  ? member.channel_model_id === target.id
                  : member.child_combo_id === target.id,
              ),
          )
          .map<ComboTargetInput>((target) => ({
            target_type: target.kind === "direct" ? "channel_model" : "child_combo",
            channel_model_id: target.kind === "direct" ? target.id : null,
            child_combo_id: target.kind === "combo" ? target.id : null,
            priority: 1,
            enabled: true,
          }));
        if (additions.length === 0) continue;
        await updateCombo(combo.id, {
          targets: [...combo.members.map(comboMemberInput), ...additions],
        });
        added += additions.length;
      }
      await queryClient.invalidateQueries({ queryKey: ["combos"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      if (added > 0) notify.success("已加入 " + added + " 项到组合");
      if (errors.length > 0) notify.error(errors.length + " 项失败", { description: errors[0] });
      setAddToComboOpen(false);
      clearSelection();
    } catch (error) {
      notify.error("加入组合失败", { description: describeApiError(error) });
    } finally {
      setBulkPending(false);
    }
  };

  const createComboWithTargets = async (name: string, targets: readonly ComboTargetRef[]) => {
    setBulkPending(true);
    try {
      await createCombo({
        name,
        description: "",
        enabled: true,
        targets: targets.map<ComboTargetInput>((target) => ({
          target_type: target.kind === "direct" ? "channel_model" : "child_combo",
          channel_model_id: target.kind === "direct" ? target.id : null,
          child_combo_id: target.kind === "combo" ? target.id : null,
          priority: 1,
          enabled: true,
        })),
      });
      await queryClient.invalidateQueries({ queryKey: ["combos"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      notify.success("已新建组合「" + name + "」");
      setAddToComboOpen(false);
      clearSelection();
    } catch (error) {
      notify.error("新建组合失败", { description: describeApiError(error) });
    } finally {
      setBulkPending(false);
    }
  };

  const openCreate = () => {
    setEditing(null);
    setInitialMember(null);
    setEditorOpen(true);
  };
  const openAddMember = (channelModelId: string) => {
    setEditing(null);
    setInitialMember(channelModelId);
    setEditorOpen(true);
  };
  const openEdit = (combo: Combo) => {
    setEditing(combo);
    setInitialMember(null);
    setEditorOpen(true);
  };

  // models.dev 手动同步（ADR-073）：快照状态常驻一行，按钮触发一次刷新并回显结果。
  const defaultsStatusQuery = useQuery({
    queryKey: ["model-defaults", "status"],
    queryFn: fetchModelDefaultsStatus,
    staleTime: 60_000,
    retry: false,
  });
  const defaultsRefreshMutation = useMutation({
    mutationFn: refreshModelDefaults,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["model-defaults", "status"] });
      void queryClient.invalidateQueries({ queryKey: ["channel-models", "prefill"] });
    },
  });

  const isLoading = catalogQuery.isLoading || combosQuery.isLoading;

  return (
    <div className="animate-fade-up">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-h1 font-semibold tracking-[-0.02em] text-title">模型管理</h1>
          <p className="mt-1.5 max-w-2xl text-2sm leading-6 text-muted-foreground">
            全部可调用模型：直连、手动组合与自动组合。优先级按层从大到小排列，数值越大越先调用，同层等概率随机。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            onClick={() => defaultsRefreshMutation.mutate()}
            disabled={defaultsRefreshMutation.isPending}
          >
            <RefreshCw
              className={"h-4 w-4" + (defaultsRefreshMutation.isPending ? " animate-spin" : "")}
              strokeWidth={1.75}
              aria-hidden="true"
            />
            同步 models.dev
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            新建组合
          </Button>
        </div>
      </div>

      {defaultsStatusQuery.data || defaultsRefreshMutation.data || defaultsRefreshMutation.isError ? (
        <div className="mt-2 space-y-0.5 text-2xs text-muted-foreground" aria-live="polite">
          {defaultsStatusQuery.data ? (
            <p>{describeDefaultsStatus(defaultsStatusQuery.data)}</p>
          ) : null}
          {defaultsRefreshMutation.data ? (
            <p>{describeRefreshResult(defaultsRefreshMutation.data)}</p>
          ) : null}
          {defaultsRefreshMutation.isError ? (
            <p className="text-danger-text">{describeApiError(defaultsRefreshMutation.error)}</p>
          ) : null}
        </div>
      ) : null}

      {combosQuery.isError ? (
        <Alert variant="destructive" className="mt-4">
          {describeApiError(combosQuery.error)}
        </Alert>
      ) : null}

      <Card className="mt-4 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
        <Select
          value={kindFilter}
          onValueChange={(value) => setKindFilter(value as KindFilter)}
        >
          <SelectTrigger
            aria-label="按类型筛选"
            className="h-9 w-40 border-border-strong bg-surface"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KIND_FILTERS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={protocolFilter || SELECT_ALL_VALUE}
          onValueChange={(value) => setProtocolFilter(value === SELECT_ALL_VALUE ? "" : value)}
        >
          <SelectTrigger
            aria-label="协议筛选"
            className="h-9 w-40 border-border-strong bg-surface"
          >
            <SelectValue placeholder="全部协议" />
          </SelectTrigger>
          <SelectContent>
            {PROTOCOL_FILTERS.map((item) => (
              <SelectItem key={item.value || SELECT_ALL_VALUE} value={item.value || SELECT_ALL_VALUE}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative w-full min-w-0 sm:w-70">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-9 pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索调用串、上游模型或渠道"
            aria-label="搜索模型"
          />
        </div>
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">
            {directCount} 个直连 · {manualCount} 个手动组合 · {autoCount} 个自动组合
          </span>
        </div>
      </Card>

      {/* 不再 overflow-hidden：它会把 sticky 表头困在 Card 里，滚 main 时表头不动。 */}
      <Card ref={tableRef} className="mt-4">
        {isLoading ? (
          <div className="flex min-h-60 items-center justify-center gap-2 text-2sm text-muted-foreground">
            <Spinner label="正在加载模型目录" /> 正在加载模型目录…
          </div>
        ) : filtered.length === 0 ? (
          <Empty
            className="min-h-80"
            icon={BookOpen}
            title={catalogQuery.isError ? "模型目录加载失败" : "没有匹配的模型"}
            description={
              catalogQuery.isError
                ? describeApiError(catalogQuery.error)
                : kindFilter !== "all" || protocolFilter || search.trim()
                  ? "换个关键词，或清空当前的类型、协议筛选。"
                  : "先在渠道管理里添加渠道模型，或创建一个手动组合。"
            }
            action={
              catalogQuery.isError ? (
                <Button variant="outline" size="sm" onClick={() => catalogQuery.refetch()}>
                  重试
                </Button>
              ) : kindFilter !== "all" || protocolFilter || search.trim() ? (
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0"
                  onClick={() => {
                    setKindFilter("all");
                    setProtocolFilter("");
                    setSearch("");
                  }}
                >
                  清空筛选条件
                </Button>
              ) : (
                <div className="flex flex-wrap items-center justify-center gap-3">
                  <Button variant="link" size="sm" asChild className="h-auto p-0">
                    <Link to="/channels">去创建渠道</Link>
                  </Button>
                  <Button variant="outline" size="sm" onClick={openCreate}>
                    <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    新建组合
                  </Button>
                </div>
              )
            }
          />
        ) : (
          <div>
            <Table className="w-full min-w-190 text-left" wrapperClassName="overflow-visible">
              <TableCaption className="border-b border-border px-5 py-2.5 text-left text-xs text-muted-foreground">
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    价格单位：每百万 tokens（美元）；组合在前、直连在后，表头可切换名称 / 状态排序。
                  </span>
                  {filtered.length > pageRows.length ? (
                    <Button variant="link" size="sm" className="h-auto p-0" onClick={selectAllFiltered}>
                      全选已筛选的 {filtered.length} 项
                    </Button>
                  ) : null}
                </span>
              </TableCaption>
              <TableHeader className="sticky top-0 z-10">
                <TableRow className="border-b border-border text-2xs text-muted-foreground hover:bg-transparent whitespace-nowrap">
                  <TableHead className="w-10 rounded-tl-card bg-surface-2 py-3 pl-5 pr-0 font-semibold">
                    <Checkbox
                      aria-label="全选当前页"
                      checked={allPageSelected ? true : somePageSelected ? "indeterminate" : false}
                      onCheckedChange={togglePage}
                    />
                  </TableHead>
                  <TableHead
                    className="bg-surface-2 px-3 py-3 font-semibold"
                    aria-sort={
                      sort.key === "name"
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-title"
                      aria-label="按名称排序"
                      onClick={() => toggleSort("name")}
                    >
                      名称
                      {sort.key === "name" ? (
                        sort.dir === "asc" ? (
                          <ChevronUp className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                        ) : (
                          <ChevronDown className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                        )
                      ) : null}
                    </button>
                  </TableHead>
                  <TableHead className="bg-surface-2 px-3 py-3 font-semibold">渠道</TableHead>
                  <TableHead className="bg-surface-2 px-3 py-3 font-semibold">协议</TableHead>
                  <TableHead
                    className="bg-surface-2 whitespace-nowrap px-3 py-3 font-semibold"
                    title="输入 / 输出 / 缓存命中价格"
                  >
                    价格
                  </TableHead>
                  <TableHead
                    className="bg-surface-2 px-3 py-3 font-semibold"
                    aria-sort={
                      sort.key === "status"
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 hover:text-title"
                      aria-label="按状态排序"
                      onClick={() => toggleSort("status")}
                    >
                      状态
                      {sort.key === "status" ? (
                        sort.dir === "asc" ? (
                          <ChevronUp className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                        ) : (
                          <ChevronDown className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                        )
                      ) : null}
                    </button>
                  </TableHead>
                  <TableHead className="rounded-tr-card bg-surface-2 px-5 py-3 text-right font-semibold">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className="divide-y divide-border">
                {pageRows.map((row) => {
                  const entry = row.entry;
                  // 三条冷却轴互相独立：熔断/故障、人工定时、排期窗口各自成轴。
                  const axes = cooldownAxes({
                    health: entry.health_status,
                    bannedUntil: entry.banned_until,
                    schedule: entry.schedule,
                    cooldownReason: entry.cooldown_reason,
                    nowMs,
                  });
                  return (
                    <TableRow key={entry.kind + "-" + entry.id} className="hover:bg-surface-2">
                      <TableCell className="w-10 py-2 pl-5 pr-0">
                        <Checkbox
                          aria-label={"选择 " + entry.name}
                          checked={Boolean(selected[rowKeyOf(entry)])}
                          onCheckedChange={() => toggleRow(entry)}
                        />
                      </TableCell>
                      <TableCell className="whitespace-normal px-3 py-2">
                        <div className="flex flex-col gap-1">
                          <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-xs text-title">
                            <TruncatedName text={entry.name} />
                            {entry.kind === "direct" && entry.upstream_model ? (
                              <span
                                role="img"
                                className="inline-flex text-muted-foreground"
                                aria-label={"上游模型：" + entry.upstream_model}
                                title={"上游模型：" + entry.upstream_model}
                              >
                                <Server className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                              </span>
                            ) : null}
                            {entry.kind === "combo" && entry.origin ? (
                              <ComboMarker
                                origin={entry.origin}
                                layers={row.layers}
                                params={row.comboParams ? comboParamLines(row.comboParams) : undefined}
                              />
                            ) : null}
                          </span>
                          {row.combo?.description ? (
                            <span className="text-2xs text-muted-foreground">{row.combo.description}</span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="max-w-40 px-3 py-2 text-xs text-body">
                        {entry.kind === "direct" ? (
                          <span
                            className="block truncate"
                            title={entry.channel_name || entry.channel_slug || undefined}
                          >
                            {entry.channel_name || entry.channel_slug || "—"}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap px-3 py-2 text-xs text-body">
                        {entry.kind === "direct"
                          ? entry.protocol
                            ? ((PROTOCOL_LABELS as Record<string, string>)[entry.protocol] ?? entry.protocol)
                            : "—"
                          : "—"}
                      </TableCell>
                      <TableCell className="max-w-56 px-3 py-2 text-xs text-body tabular-nums">
                        {entry.kind === "direct" ? (
                          // 单行省略：列被挤窄时不再裁字，悬停 title 给全文。
                          <span className="block truncate" title={priceText(entry)}>
                            {priceText(entry)}
                          </span>
                        ) : row.comboParams ? (
                          // 组合不合成单一价格：给成员区间，并注明按实际选中成员计费。
                          <span
                            className="block truncate"
                            title={"按实际选中成员计费，不合成单一价格：" + (comboPriceRangeText(row.comboParams) ?? "")}
                          >
                            {comboPriceRangeText(row.comboParams)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="min-w-52 px-3 py-2">
                        <AvailabilityStatusCell subject={entry} nowMs={nowMs} mode={mode} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap px-5 py-2 text-right">
                        <div className="flex flex-nowrap items-center justify-end gap-0.5">
                          <CopyButton value={entry.name} />
                          {entry.kind === "direct" ? (
                            <>
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="h-8 w-8"
                                      aria-label={"将 " + entry.name + " 加入组合"}
                                      onClick={() => openAddMember(entry.id)}
                                    >
                                      <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>加入组合</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="h-8 w-8"
                                      aria-label={"编辑 " + entry.name}
                                      onClick={() => setEditTarget(entry)}
                                    >
                                      <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>编辑</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                              <AvailabilityControl
                                subject={entry.name}
                                enabled={entry.enabled}
                                health={entry.health_status}
                                bannedUntil={entry.banned_until}
                                initialSchedule={normalizeSchedule(entry.schedule)}
                                busy={availabilityPending(entry.id) || measurePending(entry.id)}
                                onConfirm={(submit) => {
                                  if (submit.type === "availability") {
                                    availabilityMutation.mutate({
                                      id: entry.id,
                                      enabled: submit.enabled,
                                      bannedUntil: submit.bannedUntil,
                                      reset: submit.reset,
                                    });
                                    return;
                                  }
                                  measureMutation.mutate({
                                    id: entry.id,
                                    submit:
                                      submit.type === "cooldown"
                                        ? { kind: "cooldown", untilMs: submit.bannedUntil }
                                        : { kind: "schedule", schedule: submit.schedule },
                                  });
                                }}
                              />
                              {axes.fault || axes.timed ? (
                                <TooltipProvider>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8"
                                        aria-label={"解除冷却 " + entry.name}
                                        disabled={releaseCooldownPending(entry.id)}
                                        onClick={() =>
                                          setReleaseTarget({
                                            id: entry.id,
                                            name: entry.name,
                                            axes,
                                            timezone:
                                              normalizeSchedule(entry.schedule)?.timezone ??
                                              localScheduleTimezone(),
                                          })
                                        }
                                      >
                                        <Unlock
                                          className="h-3.5 w-3.5"
                                          strokeWidth={1.75}
                                          aria-hidden="true"
                                        />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                      解除冷却：一次清掉熔断冷却、人工定时冷却与排期窗口，解除后该模型立即恢复路由
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                              ) : axes.schedule ? (
                                <TooltipProvider>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8"
                                        aria-label={"清除排期 " + entry.name}
                                        disabled={clearSchedulePending(entry.id)}
                                        onClick={() => {
                                          // 只清规则、保留时区：空规则集 = 全天可用，不动启用与冷却。
                                          const timezone =
                                            normalizeSchedule(entry.schedule)?.timezone ??
                                            localScheduleTimezone();
                                          clearScheduleMutation.mutate({ id: entry.id, timezone });
                                        }}
                                      >
                                        <CalendarOff
                                          className="h-3.5 w-3.5"
                                          strokeWidth={1.75}
                                          aria-hidden="true"
                                        />
                                      </Button>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                      清除排期：排期是窗口规则、不是封禁；清除后该模型立即恢复全天可用
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                              ) : null}
                            </>
                          ) : row.entry.origin === "manual" && row.combo ? (
                            <>
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="h-8 w-8"
                                      aria-label={"编辑 " + entry.name}
                                      onClick={() => openEdit(row.combo!)}
                                    >
                                      <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>编辑</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="h-8 w-8"
                                      aria-label={"删除 " + entry.name}
                                      disabled={deleteMutation.isPending}
                                      onClick={() => setPendingDelete(row.combo)}
                                    >
                                      <Trash2
                                        className="h-3.5 w-3.5 text-danger"
                                        strokeWidth={1.75}
                                        aria-hidden="true"
                                      />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>删除</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            </>
                          ) : (
                            <span
                              className="text-2xs text-muted-foreground"
                              title={row.entry.origin === "manual" ? undefined : "自动组合按成员实时派生，不可直接编辑"}
                            >
                              {row.entry.origin === "manual" ? "组合详情加载中…" : "只读"}
                            </span>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
        {!isLoading && filtered.length > 0 ? (
          <Pagination
            page={currentPage}
            pageSize={pageSize}
            total={filtered.length}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        ) : null}
      </Card>

      <BulkActionBar
        count={selectedCount}
        busy={bulkPending}
        timedDisabled={hasComboSelected}
        lifecycleDisabled={hasAutoCombo}
        onClear={clearSelection}
        onEnable={() => setBulkConfirm("enable")}
        onBan={() => setBulkConfirm("ban")}
        onCooldown={() => {
          // 单选时带既有排期打开：窗口方式按它预填（与排期按钮同一份快照）。
          setScheduleInitial(
            selectedCount === 1 ? normalizeSchedule(selectedEntries[0]?.schedule) : null,
          );
          setCooldownOpen(true);
        }}
        onSchedule={() => {
          setScheduleInitial(
            selectedCount === 1 ? normalizeSchedule(selectedEntries[0]?.schedule) : null,
          );
          setScheduleOpen(true);
        }}
        onAddToCombo={() => setAddToComboOpen(true)}
      />

      <ConfirmDialog
        open={bulkConfirm !== null}
        onOpenChange={(open) => {
          if (!open) setBulkConfirm(null);
        }}
        title={
          bulkConfirm === "enable"
            ? `启用已选的 ${selectedCount} 项？`
            : `封禁已选的 ${selectedCount} 项？`
        }
        description={
          bulkConfirm === "enable"
            ? "启用后立即重新出现在 /v1/models 并参与路由。"
            : "封禁是永久措施：直连模型不再出现在 /v1/models，手动组合整条不可用。定时恢复请用「冷却」或「排期」。"
        }
        confirmLabel={bulkConfirm === "enable" ? "确认启用" : "确认封禁"}
        destructive={bulkConfirm !== "enable"}
        onConfirm={() => {
          void runBulkAvailability({ op: bulkConfirm === "enable" ? "unban" : "ban" });
        }}
      />

      <ConfirmDialog
        open={releaseTarget !== null}
        onOpenChange={(open) => {
          if (!open) setReleaseTarget(null);
        }}
        title={"解除 " + (releaseTarget?.name ?? "") + " 的冷却？"}
        description="解除后该模型立即恢复路由：清掉熔断冷却、人工定时冷却与排期窗口（只清当前命中的冷却轴）。"
        confirmLabel="确认解除冷却"
        onConfirm={() => {
          if (!releaseTarget) return;
          releaseCooldownMutation.mutate(releaseTarget);
        }}
      />

      <CooldownDialog
        open={cooldownOpen}
        onOpenChange={setCooldownOpen}
        subject={selectedCount === 1 ? (selectedEntries[0]?.name ?? "1 项") : `${selectedCount} 个模型`}
        busy={bulkPending}
        initialSchedule={scheduleInitial}
        onConfirm={(submit) => {
          void runBulkAvailability(
            submit.kind === "cooldown"
              ? { op: "cooldown", bannedUntil: submit.untilMs }
              : { op: "schedule", schedule: submit.schedule },
          );
        }}
      />

      <ScheduleEditorDialog
        open={scheduleOpen}
        onOpenChange={setScheduleOpen}
        subject={selectedCount === 1 ? (selectedEntries[0]?.name ?? "1 项") : `${selectedCount} 个模型`}
        busy={bulkPending}
        // 目录下发既有排期时回填；批量选择或组合条目没有单一份排期，按空规则打开。
        initialSchedule={scheduleInitial}
        onConfirm={(schedule) => {
          void runBulkAvailability({ op: "schedule", schedule });
        }}
      />

      {editTarget ? (
        <ChannelModelFormDialog
          key={editTarget.id}
          // 模型列表拉到、且能在其中定位到该模型时才打开，避免闪现「添加模型」。
          open={editingModel !== null}
          onOpenChange={(next) => {
            if (!next) setEditTarget(null);
          }}
          channelId={editTarget.channel_id}
          model={editingModel}
          onSaved={async () => {
            // 表单自己会失效 channel-models / channel 查询；这里补目录，让列表立即反映新值。
            await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
            notify.success("模型已保存", { description: editTarget.call_model + " 已更新" });
          }}
        />
      ) : null}

      <AddToComboDialog
        open={addToComboOpen}
        onOpenChange={setAddToComboOpen}
        targets={comboTargetRefs}
        combos={combos}
        busy={bulkPending}
        onAdd={(comboIds) => {
          void addToCombos(comboIds);
        }}
        onCreate={(name, targets) => {
          void createComboWithTargets(name, targets);
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title={pendingDelete ? `删除手动组合 ${pendingDelete.name}？` : ""}
        description="引用它的子组合必须先解除引用。"
        confirmLabel="删除"
        destructive
        onConfirm={() => {
          if (pendingDelete) deleteMutation.mutate(pendingDelete.id);
        }}
      />

      <ComboEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        combo={editing}
        combos={combos}
        catalog={directCatalog}
        initialChannelModelId={editing ? null : initialMember}
        onSaved={async () => {
          await queryClient.invalidateQueries({ queryKey: ["combos"] });
          await queryClient.invalidateQueries({ queryKey: ["implicit-groups"] });
          await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
        }}
      />
    </div>
  );
}

function priceText(entry: ManagementEntry): string {
  return [
    "入 " + formatPricePer1m(entry.input_price_per_1m),
    "出 " + formatPricePer1m(entry.output_price_per_1m),
    "缓存 " + formatPricePer1m(entry.cache_read_price_per_1m ?? null),
  ].join(" / ");
}

