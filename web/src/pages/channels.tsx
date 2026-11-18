import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  Gauge,
  KeyRound,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  ChannelBatchToolbar,
  type ChannelBatchAction,
} from "@/components/channels/channel-batch-toolbar";
import { ConnectionStatusBadge } from "@/components/channels/connection-status-badge";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Empty } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { notify } from "@/lib/notify";
import { listChannelModels, probeChannelModel, PROBE_STATUS_META } from "@/lib/channel-models";
import { SELECT_ALL_VALUE } from "@/lib/select-sentinels";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { cn } from "@/lib/utils";
import {
  CONNECTION_STATUS_META,
  deleteChannel,
  describeApiError,
  formatLatencyAdaptive,
  listChannels,
  PROTOCOL_LABELS,
  REWRITE_SECTION_LABELS,
  updateChannel,
  type Channel,
  type ConnectionStatus,
} from "@/lib/channels";
import { formatDateTime, useTimezone } from "@/lib/time";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

const BATCH_ACTION_LABELS: Record<ChannelBatchAction, string> = {
  enable: "启用",
  disable: "封禁",
  probe: "测速",
  delete: "删除",
};


const STATUS_OPTIONS: Array<{ value: ConnectionStatus | ""; label: string }> = [
  { value: "", label: "全部状态" },
  { value: "ok", label: CONNECTION_STATUS_META.ok.label },
  { value: "timeout", label: CONNECTION_STATUS_META.timeout.label },
  { value: "error", label: CONNECTION_STATUS_META.error.label },
  { value: "unknown", label: CONNECTION_STATUS_META.unknown.label },
];

function KeyCountBadge({ channel }: { channel: Channel }) {
  const count = channel.api_key_count ?? channel.api_key_masks?.length ?? 0;
  const masks = channel.api_key_masks?.filter(Boolean) ?? [];
  const tooltip = masks.length
    ? `${count} 个密钥\n${masks.join("\n")}`
    : "未设置密钥";
  return (
    <span
      tabIndex={count > 0 ? 0 : -1}
      title={tooltip}
      aria-label={`${channel.name} 共 ${count} 个密钥${masks.length ? `：${masks.join("、")}` : ""}`}
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-badge border px-2 py-0.5 text-2xs tabular-nums",
        count > 0
          ? "border-border bg-surface-2 text-body"
          : "border-dashed border-border-strong text-muted-foreground",
      )}
    >
      <KeyRound className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
      {count}
    </span>
  );
}

export function ChannelsPage() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const { mode } = useTimezone();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<ConnectionStatus | "">("");
  const debouncedSearch = useDebouncedValue(search);
  const searching = search !== debouncedSearch;
  const [deleting, setDeleting] = useState<Channel | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchBusy, setBatchBusy] = useState<ChannelBatchAction | null>(null);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);

  useEffect(() => {
    if (!(location.state as { create?: boolean } | null)?.create) return;
    navigate("/channels/new", { replace: true });
  }, [location.state, navigate]);

  const channelsQuery = useQuery({
    queryKey: ["channels", { search: debouncedSearch, status }],
    queryFn: () => listChannels({ search: debouncedSearch, status }),
    placeholderData: keepPreviousData,
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      updateChannel(input.id, { enabled: input.enabled }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
    },
    onError: (error) => notify.error("更新渠道状态失败", { description: describeApiError(error) }),
  });

  const priorityMutation = useMutation({
    mutationFn: (input: { id: string; priority: number }) =>
      updateChannel(input.id, { priority: input.priority }),
    onSuccess: async (_result, input) => {
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      await queryClient.invalidateQueries({ queryKey: ["channel", input.id] });
    },
    onError: (error) => notify.error("更新优先级失败", { description: describeApiError(error) }),
  });

  const deleteMutation = useMutation({
    // ConfirmDialog 点确认即关闭：把渠道整个传进来，成功 toast 才拿得到名字
    mutationFn: (channel: Channel) => deleteChannel(channel.id),
    onSuccess: async (_result, channel) => {
      notify.success(`已删除渠道 ${channel.name}`);
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      queryClient.removeQueries({ queryKey: ["channel", channel.id] });
    },
    onError: (error) => notify.error("删除渠道失败", { description: describeApiError(error) }),
  });

  const items = channelsQuery.data?.items ?? [];
  const total = channelsQuery.data?.total ?? 0;
  const filtered = useMemo(
    () => Boolean(debouncedSearch.trim() || status),
    [debouncedSearch, status],
  );
  const filtering = searching || (channelsQuery.isFetching && filtered);
  const selectedChannels = useMemo(
    () => items.filter((channel) => selectedIds.includes(channel.id)),
    [items, selectedIds],
  );
  const allSelected = items.length > 0 && selectedChannels.length === items.length;
  const someSelected = selectedChannels.length > 0 && !allSelected;

  function toggleSelected(id: string) {
    setSelectedIds((previous) =>
      previous.includes(id)
        ? previous.filter((value) => value !== id)
        : [...previous, id],
    );
  }

  function toggleSelectAll(selectAll: boolean) {
    setSelectedIds(selectAll ? items.map((channel) => channel.id) : []);
  }

  async function runBatch(action: ChannelBatchAction) {
    if (batchBusy || selectedChannels.length === 0) return;
    const targets = selectedChannels;
    setBatchBusy(action);
    const failures: string[] = [];
    let succeeded = 0;
    try {
      for (const channel of targets) {
        try {
          if (action === "enable" || action === "disable") {
            await updateChannel(channel.id, { enabled: action === "enable" });
          } else if (action === "delete") {
            await deleteChannel(channel.id);
            queryClient.removeQueries({ queryKey: ["channel", channel.id] });
          } else {
            const models = await listChannelModels(channel.id);
            const firstModel = models.items[0];
            if (!firstModel) {
              failures.push(channel.name + "（未配置模型）");
              continue;
            }
            const result = await probeChannelModel(firstModel.id, "shallow");
            if (result.status !== "ok") {
              failures.push(
                channel.name + "（" + PROBE_STATUS_META[result.status].label + "）",
              );
              continue;
            }
          }
          succeeded += 1;
        } catch (error) {
          failures.push(channel.name + "（" + describeApiError(error) + "）");
        }
      }
    } finally {
      setBatchBusy(null);
      setBatchDeleteOpen(false);
      setSelectedIds((previous) =>
        previous.filter((id) => !targets.some((channel) => channel.id === id)),
      );
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      await queryClient.invalidateQueries({ queryKey: ["channel-models"] });
    }
    const label = BATCH_ACTION_LABELS[action];
    if (succeeded > 0) notify.success("已" + label + " " + succeeded + " 个渠道");
    if (failures.length > 0) {
      notify.error(failures.length + " 个渠道" + label + "失败", {
        description:
          failures.length > 3
            ? failures.slice(0, 3).join("、") + " 等"
            : failures.join("、"),
      });
    }
  }

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="渠道管理"
        description="点击名称进入编辑页。保存写入配置但不会探测上游；测试连接与获取模型始终使用页面上最新填写的值。"
        actions={
          <Button asChild>
            <Link to="/channels/new">
              <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              新建渠道
            </Link>
          </Button>
        }
      />

      <Card className="mt-6 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full min-w-0 sm:w-70">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <Input
              aria-label="搜索渠道"
              className="h-9 pl-9"
              placeholder="搜索名称、标识或连接地址"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <Select
            value={status || SELECT_ALL_VALUE}
            onValueChange={(value) => setStatus(value === SELECT_ALL_VALUE ? "" : (value as ConnectionStatus))}
          >
            <SelectTrigger aria-label="状态筛选" className="h-9 w-40 border-border-strong bg-surface">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value || SELECT_ALL_VALUE} value={option.value || SELECT_ALL_VALUE}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="shrink-0 text-xs text-muted-foreground">
              {filtering ? "搜索中…" : "共 " + total + " 个渠道"}
            </span>
            {selectedChannels.length > 0 ? (
              <ChannelBatchToolbar
                count={selectedChannels.length}
                busy={batchBusy}
                onEnable={() => runBatch("enable")}
                onDisable={() => runBatch("disable")}
                onProbe={() => runBatch("probe")}
                onDelete={() => {
                  setBatchDeleteOpen(true);
                }}
                onClear={() => setSelectedIds([])}
              />
            ) : null}
          </div>
        </div>
      </Card>

      {channelsQuery.isError ? (
        <Alert variant="destructive" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{describeApiError(channelsQuery.error)}</span>
            <Button variant="outline" size="sm" onClick={() => channelsQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        </Alert>
      ) : null}

      <Card className="mt-4 overflow-hidden">
        {channelsQuery.isLoading ? (
          <div className="flex min-h-60 items-center justify-center gap-2 text-2sm text-muted-foreground">
            <Spinner label="正在加载渠道" />
            正在加载渠道…
          </div>
        ) : items.length === 0 ? (
          <Empty
            icon={Network}
            className="min-h-80"
            title={filtered ? "没有匹配的渠道" : "还没有渠道"}
            description={
              filtered
                ? "换个关键词或状态筛选试试。"
                : "先填写上游连接配置，保存后再进入路由与映射页获取模型。"
            }
            action={
              filtered ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setSearch("");
                    setStatus("");
                  }}
                >
                  清除筛选
                </Button>
              ) : (
                <Button asChild>
                  <Link to="/channels/new">
                    <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    新建第一个渠道
                  </Link>
                </Button>
              )
            }
          />
        ) : (
          <Table className="min-w-245 text-left">
            <TableHeader>
              <TableRow className="border-b border-border bg-surface-2 text-2xs uppercase tracking-[0.08em] text-muted-foreground hover:bg-transparent whitespace-nowrap">
                <TableHead className="w-9 px-3 py-2">
                  <Checkbox
                    aria-label="全选渠道"
                    className="cursor-pointer"
                    checked={allSelected ? true : someSelected ? "indeterminate" : false}
                    onCheckedChange={(checked) => toggleSelectAll(checked === "indeterminate" ? true : checked)}
                  />
                </TableHead>
                <TableHead className="px-2 py-2 font-semibold">渠道</TableHead>
                <TableHead className="px-3 py-2 font-semibold">类型</TableHead>
                <TableHead className="px-3 py-2 font-semibold">密钥数量</TableHead>
                <TableHead className="px-3 py-2 font-semibold">模型</TableHead>
                <TableHead className="px-3 py-2 font-semibold">优先级</TableHead>
                <TableHead className="px-3 py-2 font-semibold">探测记录</TableHead>
                <TableHead className="px-4 py-2 text-right font-semibold">快捷操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
                {items.map((channel) => {
                  const toggling =
                    toggleMutation.isPending && toggleMutation.variables?.id === channel.id;
                  const selected = selectedIds.includes(channel.id);
                  return (
                    <TableRow
                      key={channel.id}
                      className={cn(
                        "transition-colors hover:bg-surface-2",
                        selected && "bg-primary-soft/40",
                      )}
                    >
                      <TableCell className="px-3 py-2">
                        <Checkbox
                          aria-label={"选择 " + channel.name}
                          className="cursor-pointer"
                          checked={selected}
                          onCheckedChange={() => toggleSelected(channel.id)}
                        />
                      </TableCell>
                      <TableCell className="px-2 py-2">
                        <Link
                          to={"/channels/" + channel.id}
                          className="group inline-flex max-w-80 flex-col"
                        >
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate text-2sm font-medium text-title group-hover:text-primary">
                              {channel.name}
                            </span>
                            {channel.enabled ? null : (
                              <Badge
                                variant="outline"
                                className="shrink-0 px-1.5 text-2xs leading-4"
                              >
                                已封禁
                              </Badge>
                            )}
                          </span>
                          <span className="mt-0.5 flex min-w-0 items-center gap-2">
                            <span className="truncate font-mono text-2xs text-muted-foreground">
                              {channel.slug}
                            </span>
                            {channel.note ? (
                              <span className="truncate text-2xs text-faint">
                                · {channel.note}
                              </span>
                            ) : null}
                          </span>
                        </Link>
                      </TableCell>
                      <TableCell className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <span className="inline-flex whitespace-nowrap rounded-badge border border-border bg-surface-2 px-2 py-0.5 text-2xs text-body">
                            {PROTOCOL_LABELS[channel.protocol] ?? channel.protocol}
                          </span>
                          {channel.override_sections.length > 0 ? (
                            <span
                              className="inline-flex text-primary"
                              title={
                                "参数覆盖：" +
                                channel.override_sections
                                  .map((section) => REWRITE_SECTION_LABELS[section])
                                  .join("、")
                              }
                              aria-label={
                                channel.name +
                                " 参数覆盖：" +
                                channel.override_sections
                                  .map((section) => REWRITE_SECTION_LABELS[section])
                                  .join("、")
                              }
                            >
                              <SlidersHorizontal
                                className="h-3.5 w-3.5"
                                strokeWidth={1.75}
                                aria-hidden="true"
                              />
                            </span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="px-3 py-2">
                        <KeyCountBadge channel={channel} />
                      </TableCell>
                      <TableCell className="px-3 py-2">
                        <span className="inline-flex min-w-13 items-center gap-1 text-xs tabular-nums text-body">
                          <span className="font-medium text-title">{channel.model_count}</span>
                          <span className="text-2xs text-muted-foreground">个</span>
                        </span>
                      </TableCell>
                      <TableCell className="px-3 py-2">
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <input
                                key={channel.id + "-" + channel.priority}
                                type="number"
                                min={1}
                                max={1000}
                                defaultValue={channel.priority}
                                aria-label={"优先级 " + channel.name}
                                disabled={
                                  priorityMutation.isPending &&
                                  priorityMutation.variables?.id === channel.id
                                }
                                className="h-8 w-16 rounded-control border border-border-strong bg-surface px-2 text-xs tabular-nums text-title focus-visible:border-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring disabled:opacity-60"
                                onBlur={(event) => {
                                  const next = Math.max(
                                    1,
                                    Math.min(1000, Number.parseInt(event.target.value, 10) || 1),
                                  );
                                  event.target.value = String(next);
                                  if (next !== channel.priority) {
                                    priorityMutation.mutate({ id: channel.id, priority: next });
                                  }
                                }}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") event.currentTarget.blur();
                                }}
                              />
                            </TooltipTrigger>
                            <TooltipContent>数值越大越先调用；自动组合按层等概率随机分流</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </TableCell>
                      <TableCell className="px-3 py-2">
                        <div className="flex items-center gap-1.5 whitespace-nowrap">
                          <ConnectionStatusBadge status={channel.last_model_probe_status} />
                          <span className="text-xs tabular-nums text-body">
                            {formatLatencyAdaptive(channel.last_model_probe_latency_ms)}
                          </span>
                        </div>
                        <p className="mt-0.5 whitespace-nowrap text-2xs tabular-nums text-muted-foreground">
                          {channel.last_model_probe_at
                            ? formatDateTime(channel.last_model_probe_at, mode)
                            : "尚未测试"}
                        </p>
                      </TableCell>
                      <TableCell className="px-4 py-2">
                        <div className="flex items-center justify-end gap-0.5">
                          {/* 行内动作统一文字按钮，语义色按动作：封禁红 / 启用绿（ui.md §2.4，对齐 keys 行口径） */}
                          <Button
                            variant="ghost"
                            size="sm"
                            className={channel.enabled ? "text-danger-text" : "text-success-text"}
                            aria-label={(channel.enabled ? "封禁 " : "启用 ") + channel.name}
                            disabled={toggling}
                            onClick={() =>
                              toggleMutation.mutate({ id: channel.id, enabled: !channel.enabled })
                            }
                          >
                            {channel.enabled ? (
                              <Ban className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                            ) : (
                              <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                            )}
                            {channel.enabled ? "封禁" : "启用"}
                          </Button>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  aria-label={"测试连接 " + channel.name}
                                  onClick={() => navigate("/channels/" + channel.id + "?tab=routing")}
                                >
                                  <Gauge className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                                  测试连接
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>打开路由与映射</TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                          <Button
                            asChild
                            variant="ghost"
                            size="sm"
                            aria-label={"编辑 " + channel.name}
                          >
                            <Link to={"/channels/" + channel.id}>
                              <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                              编辑
                            </Link>
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            // 危险前景统一 --danger-text（ui.md §2.3 / §2.4）
                            className="text-danger-text"
                            aria-label={"删除 " + channel.name}
                            onClick={() => {
                              setDeleting(channel);
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
            </TableBody>
          </Table>
        )}
      </Card>

      {/* 危险确认统一走 ui/confirm-dialog（ui.md §2.3）：alertdialog 语义 + 问句标题。
          级联说明（模型一并删除 / 组合引用拒绝）原样保留。 */}
      <ConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleting(null);
        }}
        title={deleting ? `删除渠道 ${deleting.name}？` : ""}
        description={
          <>
            <span className="block">
              {deleting ? `即将删除 ${deleting.name}（${deleting.slug}）。` : undefined}
            </span>
            <span className="block">
              渠道下的模型会一并删除；如果任一模型已被组合引用，删除会被拒绝，请先解除组合引用。
            </span>
          </>
        }
        confirmLabel="确认删除"
        destructive
        onConfirm={() => {
          if (deleting) deleteMutation.mutate(deleting);
        }}
      />

      <ConfirmDialog
        open={batchDeleteOpen}
        onOpenChange={(open) => {
          if (!open && !batchBusy) setBatchDeleteOpen(false);
        }}
        title={
          selectedChannels.length > 0
            ? `删除选中的 ${selectedChannels.length} 个渠道？`
            : "删除选中的渠道？"
        }
        description={
          <>
            {selectedChannels.length > 0 ? (
              <span className="block">即将删除选中的 {selectedChannels.length} 个渠道。</span>
            ) : null}
            <span className="block">
              渠道下的模型会一并删除；任一模型被组合引用时，该渠道会被拒绝删除，其余渠道继续执行。
            </span>
            {selectedChannels.length > 0 ? (
              <span className="mt-2 block text-xs leading-5 text-muted-foreground">
                选中的渠道：{selectedChannels.map((channel) => channel.name).join("、")}
              </span>
            ) : null}
          </>
        }
        confirmLabel="确认删除"
        destructive
        onConfirm={() => runBatch("delete")}
      />
    </div>
  );
}
