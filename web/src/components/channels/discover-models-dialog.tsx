import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  KeyRound,
  Layers,
  Plus,
  RefreshCw,
  Search,
  ServerCog,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  bulkAddChannelModels,
  describeApiError,
  discoverChannelModels,
  type Channel,
  type ChannelDiscoverInput,
  type ChannelDiscoverResult,
  type ChannelModelBulkResult,
  type DiscoveredModel,
} from "@/lib/channels";
import { cn } from "@/lib/utils";

interface DiscoverModelsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channelId?: string;
  input: ChannelDiscoverInput;
  /**
   * Creates the channel before the first bulk write when the dialog is opened
   * from an unsaved draft. Must throw a readable error when the draft is invalid.
   */
  onCreateChannel?: () => Promise<Channel>;
  onAdded?: (result: ChannelModelBulkResult) => void;
  /** 这次请求没带密钥：提前说明，免得把上游的 401 读成「密钥不对」。 */
  keyless?: boolean;
}

const UNGROUPED_KEY = "其他";

function groupKeyOf(model: string): string {
  const slash = model.indexOf("/");
  return slash > 0 ? model.slice(0, slash) : UNGROUPED_KEY;
}

function matchesQuery(model: string, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || model.toLowerCase().includes(needle);
}

/** Wide enough to split the model list into two independent columns. */
const WIDE_LAYOUT_QUERY = "(min-width: 1024px)";

function useWideLayout(): boolean {
  const [wide, setWide] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(WIDE_LAYOUT_QUERY);
    if (typeof media.addEventListener !== "function") return;
    const handleChange = (event: MediaQueryListEvent) => setWide(event.matches);
    setWide(media.matches);
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, []);

  return wide;
}

export function DiscoverModelsDialog({
  open,
  onOpenChange,
  channelId,
  input,
  onCreateChannel,
  onAdded,
  keyless = false,
}: DiscoverModelsDialogProps) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [addError, setAddError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [grouped, setGrouped] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const createdChannelRef = useRef<Channel | null>(null);
  const wideLayout = useWideLayout();

  const discoverMutation = useMutation({
    mutationFn: () => discoverChannelModels(input),
  });

  const addMutation = useMutation({
    mutationFn: async (models: string[]) => {
      setAddError(null);
      let targetId = channelId ?? createdChannelRef.current?.id ?? "";
      if (!targetId) {
        if (!onCreateChannel) throw new Error("请先保存渠道，再添加模型。");
        let created: Channel;
        try {
          created = await onCreateChannel();
        } catch (error) {
          const message = error instanceof Error ? error.message : describeApiError(error);
          throw new Error(`创建渠道失败：${message}`);
        }
        createdChannelRef.current = created;
        targetId = created.id;
      }
      try {
        const result = await bulkAddChannelModels(targetId, models);
        return { result, channelId: targetId };
      } catch (error) {
        const prefix = createdChannelRef.current ? "渠道已创建，但添加模型失败" : "添加模型失败";
        throw new Error(`${prefix}：${describeApiError(error)}。已勾选的模型已保留，可重试。`);
      }
    },
    onSuccess: async ({ result, channelId: targetId }) => {
      await queryClient.invalidateQueries({ queryKey: ["channel-models", targetId] });
      await queryClient.invalidateQueries({ queryKey: ["channel", targetId] });
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      // Catalog entries and implicit groups are derived from channel-model rows,
      // so they go stale as soon as a bulk add succeeds.
      await queryClient.invalidateQueries({ queryKey: ["catalog-entries"] });
      await queryClient.invalidateQueries({ queryKey: ["implicit-groups"] });
      onAdded?.(result);
      onOpenChange(false);
    },
    onError: (error) =>
      setAddError(error instanceof Error ? error.message : describeApiError(error)),
  });

  useEffect(() => {
    if (!open) return;
    createdChannelRef.current = null;
    setSelected(new Set());
    setAddError(null);
    setQuery("");
    setGrouped(false);
    setCollapsedGroups(new Set());
    discoverMutation.mutate();
    // The request is intentionally fired only when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const result: ChannelDiscoverResult | undefined = discoverMutation.data;
  const newModels = result?.models.filter((item) => !item.already_added) ?? [];
  const visibleModels = useMemo(
    () =>
      (result?.models ?? []).filter((item) => matchesQuery(item.upstream_model, query)),
    [result, query],
  );
  const visibleNewModels = visibleModels.filter((item) => !item.already_added);
  const allVisibleSelected =
    visibleNewModels.length > 0 &&
    visibleNewModels.every((item) => selected.has(item.upstream_model));

  const groups = useMemo(() => {
    const map = new Map<string, DiscoveredModel[]>();
    for (const item of visibleModels) {
      const key = groupKeyOf(item.upstream_model);
      const items = map.get(key);
      if (items) items.push(item);
      else map.set(key, [item]);
    }
    return Array.from(map.entries()).sort(([left], [right]) => {
      if (left === UNGROUPED_KEY) return 1;
      if (right === UNGROUPED_KEY) return -1;
      return left.localeCompare(right);
    });
  }, [visibleModels]);

  // Two side-by-side columns that scroll independently in the wide layout.
  const modelColumns = useMemo(() => {
    const half = Math.ceil(visibleModels.length / 2);
    return [visibleModels.slice(0, half), visibleModels.slice(half)];
  }, [visibleModels]);

  useEffect(() => {
    if (!result || result.status !== "ok") return;
    setSelected(new Set(newModels.map((item) => item.upstream_model)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  const toggle = (name: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const toggleAll = () => {
    const names = visibleNewModels.map((item) => item.upstream_model);
    setSelected((current) => {
      const next = new Set(current);
      if (names.every((name) => next.has(name))) {
        for (const name of names) next.delete(name);
      } else {
        for (const name of names) next.add(name);
      }
      return next;
    });
  };

  const toggleGroup = (key: string) => {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const renderModelRow = (item: DiscoveredModel) => {
    const disabled = item.already_added;
    return (
      <label
        key={item.upstream_model}
        className={cn(
          "flex cursor-pointer items-center gap-3 px-3.5 py-2.5 text-xs transition-colors hover:bg-surface-2 has-[:disabled]:cursor-default",
          wideLayout && "border-b border-border last:border-b-0",
        )}
      >
        <Checkbox
          aria-label={`选择模型 ${item.upstream_model}`}
          checked={disabled || selected.has(item.upstream_model)}
          disabled={disabled}
          onCheckedChange={() => toggle(item.upstream_model)}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-title">
          {item.upstream_model}
        </span>
        {disabled ? (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-badge bg-surface-2 px-2 py-0.5 text-2xs text-muted-foreground">
            <CheckCircle2 className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
            已添加
          </span>
        ) : null}
      </label>
    );
  };

  const canAdd = Boolean(channelId || onCreateChannel);
  const filtered = Boolean(query.trim());

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && addMutation.isPending) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-h-[calc(100dvh-5rem)] sm:max-w-3xl">
        <DialogHeader className="shrink-0 gap-0 space-y-0 border-b border-border px-5 py-4 pr-12 text-left">
          <DialogTitle className="text-sm">获取上游模型</DialogTitle>
          <DialogDescription className="mt-0.5 text-xs">
            {channelId
              ? "使用当前表单里的地址、密钥、协议与改写规则请求上游 /models。勾选后只写入选中的模型，不会发起聊天测试。"
              : "正在使用当前表单最新填写的地址、密钥、协议与改写规则请求上游 /models。勾选并添加时会先创建渠道，再写入选中的模型。"}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4 sm:px-5">
          {keyless ? (
            <p className="flex items-start gap-1.5 rounded-control border border-border bg-surface-2 px-2.5 py-2 text-2xs leading-relaxed text-muted-foreground">
              <KeyRound className="mt-px h-3 w-3 shrink-0" strokeWidth={1.75} aria-hidden="true" />
              <span>
                本次请求没有携带密钥：匿名可访问的上游照常返回模型，其余会以 HTTP 401 拒绝。
                需要带密钥请先在「渠道密钥」里添加，再回来重试。
              </span>
            </p>
          ) : null}

          {discoverMutation.isPending ? (
            <div className="flex min-h-45 flex-col items-center justify-center gap-2 text-2sm text-muted-foreground">
              <Spinner size="lg" className="text-primary" aria-hidden="true" />
              正在请求上游模型列表…
            </div>
          ) : null}

          {discoverMutation.isError ? (
            <Alert variant="destructive">{describeApiError(discoverMutation.error)}</Alert>
          ) : null}

          {result && result.status !== "ok" ? (
            <Alert variant={result.status === "timeout" ? "warning" : "destructive"}>
              <p className="font-medium">{result.message}</p>
              <p className="mt-1 text-2xs opacity-80">
                HTTP {result.http_status ?? "—"} · {result.latency_ms} ms
              </p>
            </Alert>
          ) : null}

          {result?.status === "ok" ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-45 flex-1">
                  <Search
                    className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                  <Input
                    aria-label="搜索模型"
                    className="h-9 pl-9 pr-9 text-xs"
                    placeholder="按模型名称筛选"
                    value={query}
                    disabled={result.models.length === 0}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                  {query ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="清空搜索"
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 text-muted-foreground"
                      onClick={() => setQuery("")}
                    >
                      <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    </Button>
                  ) : null}
                </div>
                <Button
                  type="button"
                  variant={grouped ? "secondary" : "outline"}
                  size="md"
                  aria-pressed={grouped}
                  disabled={result.models.length === 0}
                  onClick={() => setGrouped((current) => !current)}
                >
                  <Layers className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  {grouped ? "平铺展示" : "按供应商分组"}
                </Button>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 rounded-control border border-border bg-surface-2 px-3.5 py-2.5">
                <div className="text-xs text-body">
                  <span className="font-medium text-title">{result.models.length}</span> 个模型
                  <span className="mx-1.5 text-faint">·</span>
                  已有 <span className="tabular-nums">{result.models.length - newModels.length}</span> 个
                  {filtered ? (
                    <>
                      <span className="mx-1.5 text-faint">·</span>
                      筛出 <span className="tabular-nums">{visibleModels.length}</span> 个
                    </>
                  ) : null}
                  <span className="mx-1.5 text-faint">·</span>
                  {result.message}
                </div>
                {visibleNewModels.length > 0 ? (
                  <Button
                    variant="link"
                    size="sm"
                    className="h-auto p-0 text-xs"
                    onClick={toggleAll}
                  >
                    {allVisibleSelected ? "取消全选" : "全选未添加"}
                  </Button>
                ) : null}
              </div>

              {result.models.length === 0 ? (
                <div className="flex min-h-40 flex-col items-center justify-center gap-2 text-center text-2sm text-muted-foreground">
                  <ServerCog className="h-6 w-6 text-faint" strokeWidth={1.5} aria-hidden="true" />
                  上游 /models 返回了空列表。
                </div>
              ) : visibleModels.length === 0 ? (
                <div className="flex min-h-35 flex-col items-center justify-center gap-2 text-center text-2sm text-muted-foreground">
                  没有匹配「{query.trim()}」的模型。
                  <Button
                    variant="link"
                    size="sm"
                    className="h-auto p-0 text-xs"
                    onClick={() => setQuery("")}
                  >
                    清空搜索
                  </Button>
                </div>
              ) : grouped ? (
                <div className="max-h-105 overflow-y-auto rounded-control border border-border">
                  {groups.map(([prefix, items]) => {
                    const collapsed = collapsedGroups.has(prefix);
                    return (
                      <div key={prefix} className="border-b border-border last:border-b-0">
                        <button
                          type="button"
                          className="flex w-full items-center gap-2 bg-surface-2 px-3.5 py-2 text-left text-2xs font-medium text-body transition-colors hover:bg-surface"
                          aria-expanded={!collapsed}
                          aria-label={`${prefix} 分组，共 ${items.length} 个模型`}
                          onClick={() => toggleGroup(prefix)}
                        >
                          {collapsed ? (
                            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
                          ) : (
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
                          )}
                          <span className="min-w-0 flex-1 truncate font-mono text-title">{prefix}</span>
                          <span className="shrink-0 tabular-nums text-muted-foreground">{items.length} 个</span>
                        </button>
                        {collapsed ? null : (
                          <div
                            className={
                              wideLayout ? "grid grid-cols-2" : "divide-y divide-border"
                            }
                          >
                            {items.map(renderModelRow)}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : wideLayout && visibleModels.length > 1 ? (
                <div className="grid grid-cols-2 divide-x divide-border rounded-control border border-border">
                  {modelColumns.map((column, index) => (
                    <div key={index} className="max-h-105 overflow-y-auto">
                      {column.map(renderModelRow)}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="max-h-105 divide-y divide-border overflow-y-auto rounded-control border border-border">
                  {visibleModels.map(renderModelRow)}
                </div>
              )}
            </>
          ) : null}

          {addError ? <Alert variant="destructive">{addError}</Alert> : null}
        </div>
        <DialogFooter className="shrink-0 gap-2 border-t border-border bg-surface-2 px-5 py-3.5">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={addMutation.isPending}
          >
            取消
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setAddError(null);
              discoverMutation.mutate();
            }}
            disabled={discoverMutation.isPending || addMutation.isPending}
          >
            <RefreshCw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            重新获取
          </Button>
          <Button
            onClick={() => addMutation.mutate([...selected])}
            disabled={
              !canAdd ||
              addMutation.isPending ||
              discoverMutation.isPending ||
              result?.status !== "ok" ||
              selected.size === 0
            }
          >
            {addMutation.isPending ? (
              <Spinner aria-hidden="true" />
            ) : (
              <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            {canAdd ? `添加 ${selected.size} 个模型` : "保存后可添加"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
