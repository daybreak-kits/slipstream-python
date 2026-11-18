import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Blocks,
  Pencil,
  Plus,
  Radar,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";


import { ChannelModelFormDialog } from "@/components/channels/channel-model-form-dialog";
import { ProbeStatusBadge } from "@/components/channels/probe-status-badge";
import { ModelAvailabilityButton, type AvailabilityPlan } from "@/components/models/model-availability";
import { formatCooldownTime } from "@/components/models/model-status";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { notify } from "@/lib/notify";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  deleteChannelModel,
  INPUT_TYPE_OPTIONS,
  listChannelModels,
  MODEL_TYPE_SOURCE_META,
  modelTypeLabel,
  OUTPUT_TYPE_OPTIONS,
  probeAllChannelModels,
  probeChannelModel,
  PROBE_MODES,
  PROBE_STATUS_META,
  resetChannelModel,
  setDefaultProbeModel,
  scheduleSummary,
  updateChannelModel,
  type ChannelModel,
  type ModelParameters,
  type ProbeMode,
  type ProbeStatus,
} from "@/lib/channel-models";
import { describeApiError } from "@/lib/channels";
import { formatDuration } from "@/lib/duration";
import { formatCount, formatPricePer1m } from "@/lib/format";
import { formatDateTime, useTimezone } from "@/lib/time";
import { cn } from "@/lib/utils";

const STATUS_ORDER: ProbeStatus[] = ["ok", "timeout", "error", "unknown"];

function typeLabels(values: string[], options: Array<{ value: string; label: string }>): string {
  return values
    .map((value) => options.find((option) => option.value === value)?.label ?? value)
    .join(" / ");
}

function ParameterSummary({ parameters }: { parameters: ModelParameters }) {
  return (
    <div className="space-y-1">
      <p className="whitespace-nowrap text-2xs tabular-nums text-body">
        输入 {formatCount(parameters.max_input_tokens)} · 输出 {formatCount(parameters.max_output_tokens)}
        {parameters.reasoning_level ? ` · 推理 ${parameters.reasoning_level}` : ""}
      </p>
      <div className="flex flex-nowrap items-center gap-1 whitespace-nowrap">
        <span className="whitespace-nowrap rounded-badge border border-border bg-surface-2 px-1.5 py-0.5 text-2xs text-body">
          {typeLabels(parameters.input_types, INPUT_TYPE_OPTIONS)}
        </span>
        <span className="text-2xs text-muted-foreground">→</span>
        <span className="whitespace-nowrap rounded-badge border border-border bg-surface-2 px-1.5 py-0.5 text-2xs text-body">
          {typeLabels(parameters.output_types, OUTPUT_TYPE_OPTIONS)}
        </span>
      </div>
    </div>
  );
}

/** 排期摘要：v2 优先，v1（segments）先折成 v2 再描述；没有窗口时返回 null。 */
function formatSchedule(model: ChannelModel): string | null {
  return scheduleSummary(model.schedule);
}

function countByStatus(models: ChannelModel[]): Record<ProbeStatus, number> {
  const counts: Record<ProbeStatus, number> = { ok: 0, timeout: 0, error: 0, unknown: 0 };
  for (const model of models) {
    counts[model.probe_status] = (counts[model.probe_status] ?? 0) + 1;
  }
  return counts;
}

interface ChannelModelsPanelProps {
  channelId: string;
}

export function ChannelModelsPanel({ channelId }: ChannelModelsPanelProps) {
  const queryClient = useQueryClient();
  const { mode: timezoneMode } = useTimezone();
  const [mode, setMode] = useState<ProbeMode>("deep");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ChannelModel | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ChannelModel | null>(null);

  const modelsQuery = useQuery({
    queryKey: ["channel-models", channelId],
    queryFn: () => listChannelModels(channelId),
    enabled: Boolean(channelId),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["channel-models", channelId] });
    await queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
    await queryClient.invalidateQueries({ queryKey: ["channels"] });
  };

  const defaultProbeMutation = useMutation({
    mutationFn: (id: string) => setDefaultProbeModel(id),
    onSuccess: async () => {
      await refresh();
      notify.success("已设为默认探测模型", { description: "「全部探测」会优先探测该模型" });
    },
    onError: (error) => notify.error("设置默认探测模型失败", { description: describeApiError(error) }),
  });

  const probeMutation = useMutation({
    mutationFn: (input: { id: string; mode: ProbeMode }) =>
      probeChannelModel(input.id, input.mode),
    onSuccess: async (result) => {
      await refresh();
      const description = `${result.latency_ms} ms · ${result.message}`;
      if (result.status === "ok") {
        notify.success(`${result.upstream_model} · 探测通过`, { description });
      } else if (result.status === "timeout") {
        notify.warning(`${result.upstream_model} · 探测超时`, { description });
      } else {
        notify.error(`${result.upstream_model} · 探测失败`, { description });
      }
    },
    onError: (error) => notify.error("探测失败", { description: describeApiError(error) }),
  });

  const probeAllMutation = useMutation({
    mutationFn: (probeMode: ProbeMode) => probeAllChannelModels(channelId, probeMode),
    onSuccess: async (result) => {
      await refresh();
      const title = `全部探测完成（${result.mode === "deep" ? "模型连接探测" : "模型拉取探测"}）`;
      const description = `共 ${result.total} 个：成功 ${result.ok}，超时 ${result.timeout}，失败 ${result.error}，未知 ${result.unknown}`;
      if (result.error > 0 || result.timeout > 0) {
        notify.warning(title, { description });
      } else {
        notify.success(title, { description });
      }
    },
    onError: (error) => notify.error("全部探测失败", { description: describeApiError(error) }),
  });

  const resetMutation = useMutation({
    mutationFn: (id: string) => resetChannelModel(id),
    onSuccess: async () => {
      await refresh();
      notify.success("冷却已复位");
    },
    onError: (error) => notify.error("复位冷却失败", { description: describeApiError(error) }),
  });

  const enableMutation = useMutation({
    mutationFn: async (input: { id: string; reset: boolean }) => {
      await updateChannelModel(input.id, { enabled: true });
      if (input.reset) await resetChannelModel(input.id);
    },
    onSuccess: async (_result, input) => {
      await refresh();
      notify.success("模型已启用", input.reset ? { description: "冷却与半开状态已清除" } : undefined);
    },
    onError: (error) => notify.error("启用模型失败", { description: describeApiError(error) }),
  });

  const banMutation = useMutation({
    mutationFn: (id: string) => updateChannelModel(id, { enabled: false }),
    onSuccess: async () => {
      await refresh();
      notify.success("模型已封禁");
    },
    onError: (error) => notify.error("封禁模型失败", { description: describeApiError(error) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteChannelModel(id),
    onSuccess: async () => {
      setDeleteTarget(null);
      await refresh();
      notify.success("模型已删除");
    },
    onError: (error) => notify.error("删除模型失败", { description: describeApiError(error) }),
  });

  const availabilityPending = (model: ChannelModel): boolean =>
    (banMutation.isPending && banMutation.variables === model.id) ||
    (resetMutation.isPending && resetMutation.variables === model.id) ||
    (enableMutation.isPending && enableMutation.variables?.id === model.id);

  const runAvailability = (model: ChannelModel, plan: AvailabilityPlan) => {
    if (plan.action === "ban") {
      banMutation.mutate(model.id);
      return;
    }
    if (plan.action === "enable") {
      enableMutation.mutate({ id: model.id, reset: plan.reset });
      return;
    }
    resetMutation.mutate(model.id);
  };

  const models = modelsQuery.data?.items ?? [];
  const counts = countByStatus(models);
  // Legacy channels may have no row flagged yet: fall back to the first model.
  const defaultModelId = models.find((model) => model.probe_default)?.id ?? models[0]?.id ?? null;
  const total = modelsQuery.data?.total ?? models.length;
  const busy = probeAllMutation.isPending;

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const openEdit = (model: ChannelModel) => {
    setEditing(model);
    setFormOpen(true);
  };

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-title">模型映射</h2>
              <span className="rounded-badge bg-surface-2 px-2 py-0.5 text-2xs tabular-nums text-muted-foreground">
                {total} 个
              </span>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              上游模型名为渠道真实模型；调用模型为客户端请求使用的名字。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ToggleGroup
              type="single"
              aria-label="探测方式"
              value={mode}
              onValueChange={(value) => {
                if (value) setMode(value as ProbeMode);
              }}
              className="inline-flex h-10 items-center gap-0 rounded-control border border-border bg-surface-2 p-0.5"
            >
              {PROBE_MODES.map((item) => (
                <ToggleGroupItem
                  key={item.value}
                  value={item.value}
                  className="h-8 min-w-0 rounded-sm px-2.5 text-xs hover:bg-transparent hover:text-title data-[state=off]:text-muted-foreground data-[state=on]:bg-surface data-[state=on]:font-medium data-[state=on]:text-title data-[state=on]:shadow-xs"
                >
                  {item.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <Button
              variant="outline"
              onClick={() => probeAllMutation.mutate(mode)}
              disabled={busy || total === 0}
            >
              {busy ? (
                <Spinner aria-hidden="true" />
              ) : (
                <Radar className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              )}
              全部探测
            </Button>
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              添加模型
            </Button>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <div className="rounded-control border border-border bg-surface-2 px-3 py-2">
            <p className="text-2xs text-muted-foreground">模型总数</p>
            <p className="mt-0.5 text-lg font-semibold tabular-nums text-title">{total}</p>
          </div>
          {STATUS_ORDER.map((status) => (
            <div
              key={status}
              className="rounded-control border border-border bg-surface-2 px-3 py-2"
            >
              <p className="text-2xs text-muted-foreground">
                {status === "ok" ? "成功" : PROBE_STATUS_META[status].label}
              </p>
              <p className="mt-0.5 text-lg font-semibold tabular-nums text-title">
                {counts[status]}
              </p>
            </div>
          ))}
        </div>

        {modelsQuery.isLoading ? (
          <div className="flex min-h-40 items-center justify-center gap-2 text-2sm text-muted-foreground">
            <Spinner aria-hidden="true" />
            正在加载模型…
          </div>
        ) : modelsQuery.isError ? (
          <Alert variant="destructive" className="mt-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>{describeApiError(modelsQuery.error)}</span>
              <Button variant="outline" size="sm" onClick={() => modelsQuery.refetch()}>
                <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                重试
              </Button>
            </div>
          </Alert>
        ) : models.length === 0 ? (
          <div className="mt-4 flex min-h-50 flex-col items-center justify-center rounded-control border border-dashed border-border bg-surface px-6 py-8 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-soft text-primary">
              <Blocks className="size-4.5" strokeWidth={1.75} aria-hidden="true" />
            </span>
            <h3 className="mt-3 text-sm font-semibold text-title">还没有模型</h3>
            <p className="mt-1 max-w-md text-2sm leading-6 text-muted-foreground">
              添加上游模型并映射调用名，然后逐个或全部探测可用性。
            </p>
            <Button className="mt-3" onClick={openCreate}>
              <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              添加第一个模型
            </Button>
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-control border border-border">
            <RadioGroup
              aria-label="默认探测模型"
              value={defaultModelId ?? ""}
              onValueChange={(value) => defaultProbeMutation.mutate(value)}
              className="contents"
            >
              <Table className="min-w-270 border-collapse text-left text-2sm">
                <TableHeader className="whitespace-nowrap bg-surface-2 text-2xs uppercase tracking-[0.08em] text-muted-foreground">
                  <TableRow>
                    <TableHead className="w-20 px-3 py-2.5 font-medium">默认探测</TableHead>
                    <TableHead className="px-3 py-2.5 font-medium">上游模型</TableHead>
                    <TableHead className="px-3 py-2.5 font-medium">调用模型</TableHead>
                    <TableHead className="px-3 py-2.5 font-medium">类型</TableHead>
                    <TableHead className="px-3 py-2.5 font-medium">模型参数</TableHead>
                    <TableHead className="px-3 py-2.5 font-medium">输入 / 输出单价</TableHead>
                    <TableHead className="px-3 py-2.5 font-medium">探测状态</TableHead>
                    <TableHead className="px-3 py-2.5 text-right font-medium">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {models.map((model) => {
                    const rowProbing =
                      probeMutation.isPending && probeMutation.variables?.id === model.id;
                    const scheduleLabel = formatSchedule(model);
                    return (
                      <TableRow
                        key={model.id}
                        className="border-t border-border align-middle transition-colors hover:bg-surface-2"
                      >
                        <TableCell className="px-3 py-3">
                          <RadioGroupItem
                            value={model.id}
                            className="cursor-pointer"
                            aria-label={`将 ${model.upstream_model} 设为默认探测模型`}
                            title="默认探测模型：全部探测时优先使用"
                          />
                        </TableCell>
                        <TableCell className="px-3 py-3">
                          <p className="break-words font-mono text-xs text-title">{model.upstream_model}</p>
                          <p className="mt-0.5 text-2xs text-muted-foreground">
                            {scheduleLabel ? (
                              <span className="ml-1.5 text-2xs text-muted-foreground" title="冷却窗口">
                                · 冷却窗口 {scheduleLabel}
                              </span>
                            ) : null}
                            {model.enabled ? null : (
                              <span className="ml-1.5 rounded-badge bg-surface-2 px-1.5 py-0.5 text-2xs text-muted-foreground">
                                {typeof model.banned_until === "number" && model.banned_until > Date.now()
                                  ? "冷却至 " + formatCooldownTime(model.banned_until, timezoneMode)
                                  : "已封禁"}
                              </span>
                            )}
                          </p>
                        </TableCell>
                        <TableCell className="break-words px-3 py-3 font-mono text-xs text-body">{model.call_model}</TableCell>
                        <TableCell className="px-3 py-3">
                          <span
                            className={cn(
                              "inline-flex whitespace-nowrap rounded-badge border px-1.5 py-0.5 text-2xs",
                              model.model_type === "chat"
                                ? "border-border bg-surface-2 text-body"
                                : "border-primary bg-primary-soft text-primary",
                            )}
                            title={
                              model.model_type_source
                                ? MODEL_TYPE_SOURCE_META[model.model_type_source]?.hint
                                : undefined
                            }
                          >
                            {modelTypeLabel(model.model_type)}
                          </span>
                          <p className="mt-1 whitespace-nowrap text-2xs text-muted-foreground">
                            {model.model_type_source
                              ? MODEL_TYPE_SOURCE_META[model.model_type_source]?.label
                              : "默认"}
                          </p>
                        </TableCell>
                        <TableCell className="px-3 py-3">
                          <ParameterSummary parameters={model.parameters} />
                        </TableCell>
                        <TableCell className="px-3 py-3 text-xs tabular-nums text-body">
                          <p className="whitespace-nowrap">
                            {formatPricePer1m(model.input_price_per_1m)} /{" "}
                            {formatPricePer1m(model.output_price_per_1m)}
                          </p>
                          <p className="mt-1 whitespace-nowrap text-2xs text-muted-foreground">
                            {/* 单价 0 = 未配置（ADR-082）：数值位「—」，原因放 hint。 */}
                            {model.input_price_per_1m === 0 && model.output_price_per_1m === 0
                              ? "未配置单价"
                              : model.cache_read_price_per_1m === null &&
                                  model.cache_write_price_per_1m === null
                                ? "缓存按输入价"
                                : `缓存读 ${formatPricePer1m(model.cache_read_price_per_1m)} / 写 ${formatPricePer1m(model.cache_write_price_per_1m)}`}
                          </p>
                        </TableCell>
                        <TableCell className="px-3 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <ProbeStatusBadge
                              status={model.probe_status}
                              message={model.probe_message}
                            />
                            <span className="text-2xs tabular-nums text-muted-foreground">
                              {model.probe_latency_ms === null
                                ? "尚未探测"
                                : formatDuration(model.probe_latency_ms)}
                            </span>
                          </div>
                          <p className="mt-1 whitespace-nowrap text-2xs tabular-nums text-muted-foreground">
                            最近 {formatDateTime(model.probed_at, timezoneMode)}
                          </p>
                        </TableCell>
                        <TableCell className="px-3 py-3">
                          <div className="flex items-center justify-end gap-1 [&>button:first-child]:px-2">
                            <ModelAvailabilityButton
                              subject={model.upstream_model}
                              enabled={model.enabled}
                              health={model.health_status}
                              busy={availabilityPending(model)}
                              onConfirm={(plan) => runAvailability(model, plan)}
                            />
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label={`探测 ${model.upstream_model}`}
                              className="w-8 px-0"
                              onClick={() => probeMutation.mutate({ id: model.id, mode })}
                              disabled={rowProbing || busy}
                            >
                              {rowProbing ? (
                                <Spinner size="sm" aria-hidden="true" />
                              ) : (
                                <Radar className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                              )}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label={`编辑 ${model.upstream_model}`}
                              onClick={() => openEdit(model)}
                            >
                              <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-danger-text hover:text-danger-text"
                              aria-label={`删除 ${model.upstream_model}`}
                              onClick={() => {
                                setDeleteTarget(model);
                              }}
                            >
                              <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </RadioGroup>
          </div>
        )}

        <p className="mt-3 text-2xs leading-5 text-muted-foreground">
          “未知状态”只表示从未探测，不会因时间自动变化。模型拉取探测只请求 <code className="font-mono">/models</code>
          ；模型连接探测在识别协议后发送一次最小 chat，不触碰熔断计数器。熔断中的模型会自动冷却恢复，人工封禁的模型需手动启用。
          「默认探测」每个渠道只有一个，全部探测时会优先探测它。
        </p>
      </Card>

      <ChannelModelFormDialog
        open={formOpen}
        onOpenChange={(next) => setFormOpen(next)}
        channelId={channelId}
        model={editing}
        onSaved={(saved) =>
          notify.success(editing ? "模型已保存" : "模型已添加", {
            description: `${saved.upstream_model} → ${saved.call_model}`,
          })
        }
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        // 危险确认标题问句化（ui.md §2.3）
        title={deleteTarget ? `删除模型 ${deleteTarget.upstream_model}？` : "删除模型？"}
        description={`删除后，使用该调用名的请求将无法路由。确定删除「${deleteTarget?.upstream_model ?? ""}」吗？`}
        confirmLabel="确认删除"
        destructive
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
        }}
      />
    </div>
  );
}
