import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Blocks,
  Cable,
  CloudDownload,
  Gauge,
  Hash,
  RefreshCw,
  Save,
  ScrollText,
  Settings2,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ChannelKeysPanel, type PendingChannelKey } from "@/components/channels/channel-keys-panel";
import { ChannelModelsPanel } from "@/components/channels/channel-models-panel";
import { ConnectionStatusBadge } from "@/components/channels/connection-status-badge";
import { DiscoverModelsDialog } from "@/components/channels/discover-models-dialog";
import { EnabledToggle } from "@/components/channels/enabled-toggle";
import { RewriteRulesEditor } from "@/components/channels/rewrite-rules-editor";
import { Alert } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { notify } from "@/lib/notify";
import {
  AUTH_STYLE_LABELS,
  AUTO_PROTOCOL_HINT,
  chatUrlFor,
  createChannel,
  describeApiError,
  getChannel,
  isExactEndpoint,
  modelsUrlFor,
  PROTOCOLS,
  PROTOCOL_LABELS,
  slugFromUrl,
  testChannelDraft,
  updateChannel,
  type Channel,
  type ChannelDiscoverInput,
  type ChannelInput,
  type ConnectionTestResult,
  type Protocol,
  type RewriteRules,
} from "@/lib/channels";
import { formatDuration } from "@/lib/duration";
import { getDashboardSummary, getDashboardTimeseries, type DashboardTimeseries } from "@/lib/dashboard";
import { formatCompact, formatCostOrUnconfigured, formatPercent } from "@/lib/format";
import { formatMs, listLogs } from "@/lib/logs";
import { formatDateTime, useTimezone } from "@/lib/time";
import { cn } from "@/lib/utils";

type TabKey = "connection" | "routing" | "rewrite" | "settings";

const TABS: Array<{ key: TabKey; label: string; icon: typeof Cable }> = [
  { key: "connection", label: "连接与模型", icon: Cable },
  { key: "routing", label: "路由与映射", icon: Blocks },
  { key: "rewrite", label: "请求与响应", icon: ScrollText },
  { key: "settings", label: "其他设置", icon: Settings2 },
];

interface ChannelDraft {
  name: string;
  note: string;
  slug: string;
  connection_url: string;
  protocol: Protocol | "auto";
  priority: number;
  enabled: boolean;
}

const EMPTY_DRAFT: ChannelDraft = {
  name: "",
  note: "",
  slug: "",
  connection_url: "",
  protocol: "auto",
  priority: 1,
  enabled: true,
};

function draftFrom(channel: Channel): ChannelDraft {
  return {
    name: channel.name,
    note: channel.note ?? "",
    slug: channel.slug,
    connection_url: channel.connection_url,
    protocol: channel.protocol_source === "auto" ? "auto" : channel.protocol,
    priority: channel.priority,
    enabled: channel.enabled,
  };
}

function sameDraft(left: ChannelDraft, right: ChannelDraft): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-border py-3 last:border-0 sm:grid-cols-[150px_1fr] sm:items-start sm:gap-4">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-2sm text-body">{children}</dd>
    </div>
  );
}

function MiniBars({ data }: { data: DashboardTimeseries }) {
  const { mode } = useTimezone();
  const points = data.points.slice(-24);
  const max = Math.max(...points.map((point) => point.requests), 1);
  const width = 600;
  const height = 92;
  const slot = width / Math.max(points.length, 1);
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-24 w-full" role="img" aria-label="渠道请求趋势">
        {points.map((point, index) => {
          const barHeight = (point.requests / max) * (height - 8);
          return (
            <rect
              key={point.start_ms}
              x={index * slot + slot * 0.18}
              y={height - barHeight}
              width={Math.max(2, slot * 0.64)}
              height={barHeight}
              rx={1.5}
              className="fill-primary"
            >
              <title>
                {formatDateTime(point.start_ms, mode)} · {point.requests} 请求
              </title>
            </rect>
          );
        })}
      </svg>
      <div className="mt-1 flex justify-between text-2xs text-faint">
        <span>{points[0] ? formatDateTime(points[0].start_ms, mode) : ""}</span>
        <span>请求数（按{data.granularity === "hour" ? "小时" : "天"}）</span>
      </div>
    </div>
  );
}

function ChannelOverview({ channel }: { channel: Channel }) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const { mode } = useTimezone();
  const filters = { channel: channel.name };
  const summaryQuery = useQuery({
    queryKey: ["channel-overview-summary", channel.id],
    queryFn: () => getDashboardSummary("7d", timezone, undefined, filters),
  });
  const timeseriesQuery = useQuery({
    queryKey: ["channel-overview-timeseries", channel.id],
    queryFn: () => getDashboardTimeseries("7d", timezone, undefined, filters),
  });
  const logsQuery = useQuery({
    queryKey: ["channel-overview-logs", channel.id],
    queryFn: () => listLogs({ channel: channel.name, page_size: 6 }),
  });
  const summary = summaryQuery.data;
  const stats = [
    { label: "近 7 天请求", value: summary ? formatCompact(summary.requests) : "—" },
    { label: "错误率", value: summary ? formatPercent(summary.error_rate, 2) : "—" },
    { label: "P95 总耗时", value: summary ? formatMs(summary.p95_total_ms) : "—" },
    {
      label: "成本",
      value: summary
        ? formatCostOrUnconfigured(
            summary.cost_usd,
            summary.prompt_tokens + summary.completion_tokens + summary.cached_tokens,
          )
        : "—",
    },
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map((item) => (
          <Card key={item.label} className="p-4">
            <p className="text-xs text-muted-foreground">{item.label}</p>
            <p className="mt-1.5 text-xl font-semibold tracking-[-0.02em] text-title">{item.value}</p>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Card className="p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold text-title">请求趋势</h2>
            <span className="text-2xs text-faint">近 7 天 · 按请求数</span>
          </div>
          <div className="mt-4">
            {timeseriesQuery.isLoading ? (
              <p className="flex items-center gap-2 text-2sm text-muted-foreground">
                <Spinner label="正在加载趋势" /> 正在加载趋势…
              </p>
            ) : timeseriesQuery.isError ? (
              <p className="text-2sm text-muted-foreground">趋势加载失败：{describeApiError(timeseriesQuery.error)}</p>
            ) : timeseriesQuery.data?.points.length ? (
              <MiniBars data={timeseriesQuery.data} />
            ) : (
              <p className="text-2sm text-muted-foreground">近 7 天没有请求。</p>
            )}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="text-sm font-semibold text-title">最近探测</h2>
          <div className="mt-3 space-y-3 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">状态</span>
              <ConnectionStatusBadge status={channel.connection_status} />
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">探测耗时</span>
              <span className="tabular-nums text-body">
                {formatDuration(channel.connection_latency_ms)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">探测时间</span>
              <span className="text-body">{formatDateTime(channel.connection_tested_at, mode)}</span>
            </div>
            <p className="break-words rounded-control bg-surface-2 px-2.5 py-2 text-2xs leading-5 text-muted-foreground">
              {channel.connection_message || "尚无探测结果"}
            </p>
          </div>
        </Card>
      </div>

      <Card className="p-5">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-title">最近请求</h2>
          <Link to="/logs" className="text-xs text-primary hover:underline">
            查看全部调用
          </Link>
        </div>
        {logsQuery.isLoading ? (
          <p className="mt-3 flex items-center gap-2 text-2sm text-muted-foreground">
            <Spinner label="正在加载最近请求" /> 正在加载…
          </p>
        ) : logsQuery.isError ? (
          <p className="mt-3 text-2sm text-muted-foreground">加载失败：{describeApiError(logsQuery.error)}</p>
        ) : logsQuery.data?.items.length ? (
          <div className="mt-2 divide-y divide-border">
            {logsQuery.data.items.map((log) => (
              <div key={log.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-xs">
                <span
                  className={cn(
                    "inline-flex rounded-badge px-1.5 py-0.5 text-2xs font-medium",
                    log.http_status < 400
                      ? "bg-success-soft text-success-text"
                      : "bg-danger-soft text-danger-text",
                  )}
                >
                  {log.http_status}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-2xs text-body">
                  {log.requested_model || "—"}
                </span>
                <span className="text-muted-foreground">{formatMs(log.total_ms)}</span>
                <span className="text-faint">{formatDateTime(log.created_at, mode)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-2sm text-muted-foreground">该渠道还没有请求记录。</p>
        )}
      </Card>
    </div>
  );
}

export function ChannelDetailPage() {
  const { channelId } = useParams();
  const { mode } = useTimezone();
  const isNew = !channelId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const initialTab = TABS.some((item) => item.key === requestedTab)
    ? (requestedTab as TabKey)
    : "connection";
  const [tab, setTab] = useState<TabKey>(initialTab);
  const [draft, setDraft] = useState<ChannelDraft | null>(isNew ? EMPTY_DRAFT : null);
  const [rulesDraft, setRulesDraft] = useState<RewriteRules>({});
  const [pendingKeys, setPendingKeys] = useState<PendingChannelKey[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [discoverOpen, setDiscoverOpen] = useState(false);
  // 没有密钥就要先问一句：静默发出匿名请求只会换回 401，还把原因说成「密钥不对」。
  const [keylessPrompt, setKeylessPrompt] = useState<"discover" | "test" | null>(null);
  const [lastTest, setLastTest] = useState<ConnectionTestResult | null>(null);
  const createdChannelRef = useRef<Channel | null>(null);

  const channelQuery = useQuery({
    queryKey: ["channel", channelId],
    queryFn: () => getChannel(channelId ?? ""),
    enabled: Boolean(channelId),
  });

  const channel = channelQuery.data;

  // 请求会不会带上密钥：已存密钥与「待添加」的密钥都算。
  const hasUsableKey =
    (channel?.api_keys?.length ?? 0) > 0 ||
    pendingKeys.some((key) => key.api_key.trim().length > 0);

  useEffect(() => {
    const nextTab = TABS.some((item) => item.key === requestedTab)
      ? (requestedTab as TabKey)
      : null;
    if (nextTab) setTab(nextTab);
  }, [channelId, requestedTab]);

  useEffect(() => {
    if (!channel) return;
    setDraft(draftFrom(channel));
    setRulesDraft(channel.rewrite_rules ?? {});
    setPendingKeys([]);
    setErrors({});
    setLastTest(null);
  }, [channel?.id]);

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => updateChannel(channelId ?? "", { enabled }),
    onSuccess: async (saved) => {
      setDraft((current) => (current ? { ...current, enabled: saved.enabled } : current));
      await queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      notify.success(saved.enabled ? "渠道已启用" : "渠道已封禁");
    },
    onError: (error) => notify.error("更新启用状态失败", { description: describeApiError(error) }),
  });

  const testMutation = useMutation({
    mutationFn: (input: ChannelDiscoverInput) => testChannelDraft(input),
    onSuccess: (result) => {
      setLastTest(result);
      if (result.status === "ok") {
        notify.success("连接测试通过", {
          description: `${result.latency_ms} ms · ${result.message}${result.model_count ? ` · ${result.model_count} 个模型` : ""}`,
        });
      } else if (result.status === "timeout") {
        notify.warning("连接测试超时", { description: `${result.latency_ms} ms · ${result.message}` });
      } else {
        notify.error("连接测试失败", { description: result.message });
      }
    },
    onError: (error) => notify.error("连接测试失败", { description: describeApiError(error) }),
  });

  const saveMutation = useMutation({
    mutationFn: (payload: ChannelInput) =>
      isNew ? createChannel(payload) : updateChannel(channelId ?? "", payload),
    onSuccess: async (saved) => {
      setDraft(draftFrom(saved));
      setRulesDraft(saved.rewrite_rules ?? {});
      setPendingKeys([]);
      queryClient.setQueryData(["channel", saved.id], saved);
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      await queryClient.invalidateQueries({ queryKey: ["channel", saved.id] });
      notify.success(isNew ? "渠道已创建" : "渠道已保存", { description: "保存不会自动探测上游。需要验证时请点击测试连接。" });
      if (isNew) navigate(`/channels/${saved.id}`, { replace: true });
    },
    onError: (error) => notify.error(isNew ? "创建渠道失败" : "保存失败", { description: describeApiError(error) }),
  });

  const serverDraft = channel ? draftFrom(channel) : null;
  const dirty = useMemo(() => {
    if (!draft) return false;
    if (isNew) {
      return Boolean(draft.name.trim() || draft.connection_url.trim() || draft.slug.trim() || pendingKeys.length);
    }
    if (!serverDraft || !channel) return false;
    return (
      !sameDraft(draft, serverDraft) ||
      JSON.stringify(rulesDraft) !== JSON.stringify(channel.rewrite_rules ?? {})
    );
  }, [channel, draft, isNew, pendingKeys.length, rulesDraft, serverDraft]);

  const patch = (value: Partial<ChannelDraft>) =>
    setDraft((current) => (current ? { ...current, ...value } : current));

  const validate = (value: ChannelDraft): boolean => {
    const next: Record<string, string> = {};
    if (!value.name.trim()) next.name = "请输入渠道名称";
    else if (value.name.trim().length > 128) next.name = "渠道名称最长 128 位";
    if (!value.connection_url.trim()) next.connection_url = "请输入连接地址";
    else if (!/^https?:\/\/\S+$/i.test(value.connection_url.trim().replace(/#$/, ""))) {
      next.connection_url = "连接地址必须以 http:// 或 https:// 开头";
    }
    const slug = value.slug.trim();
    if (!slug && !isNew) {
      next.slug = "请输入渠道标识";
    } else if (slug && !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(slug)) {
      next.slug = "渠道标识需字母开头，仅含字母、数字、下划线或中划线";
    }
    if (!Number.isInteger(value.priority) || value.priority < 1 || value.priority > 1000) {
      next.priority = "优先级需为 1–1000 的整数";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const buildDraftInput = (): ChannelDiscoverInput | null => {
    if (!draft) return null;
    if (!draft.connection_url.trim()) {
      notify.error("请先填写连接地址");
      return null;
    }
    const apiKey = pendingKeys[0]?.api_key.trim();
    return {
      ...(channel?.id ? { channel_id: channel.id } : {}),
      connection_url: draft.connection_url.trim(),
      ...(apiKey ? { api_key: apiKey } : {}),
      ...(draft.protocol === "auto" ? {} : { protocol: draft.protocol }),
      rewrite_rules: rulesDraft,
    };
  };

  const runTestNow = () => {
    const input = buildDraftInput();
    if (input) testMutation.mutate(input);
  };

  const openDiscoverNow = () => {
    const input = buildDraftInput();
    if (!input) return;
    setDiscoverOpen(true);
  };

  /** 没有可用密钥时先弹确认框：返回 true 表示这次调用被拦下。 */
  const blockWhenKeyless = (action: "discover" | "test") => {
    if (!draft) return false;
    // 连接地址还没填时交给 buildDraftInput 提示，避免两个提示打架。
    if (!draft.connection_url.trim()) return false;
    if (hasUsableKey) return false;
    setKeylessPrompt(action);
    return true;
  };

  const runTest = () => {
    if (blockWhenKeyless("test")) return;
    runTestNow();
  };

  const openDiscover = () => {
    if (blockWhenKeyless("discover")) return;
    openDiscoverNow();
  };

  const proceedKeyless = () => {
    const action = keylessPrompt;
    setKeylessPrompt(null);
    if (action === "test") runTestNow();
    else if (action === "discover") openDiscoverNow();
  };

  const focusKeyInput = () => {
    setTab("connection");
    // 切分区之后才渲染出密钥输入框，等一帧再聚焦。
    window.setTimeout(() => {
      const input = document.getElementById("channel-key");
      input?.scrollIntoView({ block: "center" });
      input?.focus();
    }, 0);
  };

  const buildChannelPayload = (): ChannelInput | null => {
    if (!draft) return null;
    return {
      slug: draft.slug.trim() || slugFromUrl(draft.connection_url),
      name: draft.name.trim(),
      note: draft.note,
      connection_url: draft.connection_url.trim(),
      ...(draft.protocol === "auto" ? {} : { protocol: draft.protocol }),
      priority: draft.priority,
      enabled: draft.enabled,
      rewrite_rules: rulesDraft,
      ...(isNew && pendingKeys.length
        ? {
            api_keys: pendingKeys.map((key) => ({
              label: key.label,
              api_key: key.api_key,
            })),
          }
        : {}),
    };
  };

  const save = () => {
    if (!draft) return;
    if (!validate(draft)) {
      notify.error("请先修正表单中的错误");
      return;
    }
    const payload = buildChannelPayload();
    if (payload) saveMutation.mutate(payload);
  };

  const createFromDialog = async (): Promise<Channel> => {
    if (!draft) throw new Error("请先填写渠道信息");
    if (!validate(draft)) {
      notify.error("请先修正表单中的错误", { description: "创建渠道需要渠道名称与连接地址" });
      throw new Error("请先补全渠道名称与连接地址");
    }
    const payload = buildChannelPayload();
    if (!payload) throw new Error("请先填写连接地址");
    const saved = await createChannel(payload);
    createdChannelRef.current = saved;
    queryClient.setQueryData(["channel", saved.id], saved);
    await queryClient.invalidateQueries({ queryKey: ["channels"] });
    return saved;
  };

  const handleDiscoverOpenChange = (open: boolean) => {
    setDiscoverOpen(open);
    if (open) return;
    const created = createdChannelRef.current;
    if (created) {
      createdChannelRef.current = null;
      navigate(`/channels/${created.id}`, { replace: true });
    }
  };

  const draftInput = useMemo<ChannelDiscoverInput | null>(() => {
    if (!draft) return null;
    const apiKey = pendingKeys[0]?.api_key.trim();
    return {
      ...(channel?.id ? { channel_id: channel.id } : {}),
      connection_url: draft.connection_url.trim(),
      ...(apiKey ? { api_key: apiKey } : {}),
      ...(draft.protocol === "auto" ? {} : { protocol: draft.protocol }),
      rewrite_rules: rulesDraft,
    };
  }, [channel?.id, draft, pendingKeys, rulesDraft]);

  if (!isNew && channelQuery.isLoading) {
    return (
      <div className="flex min-h-70 items-center justify-center gap-2 text-2sm text-muted-foreground">
        <Spinner label="正在加载渠道" />
        正在加载渠道…
      </div>
    );
  }

  if (!isNew && (channelQuery.isError || !channel || !draft)) {
    return (
      <div className="animate-fade-up">
        <Button asChild variant="ghost" size="sm" className="-ml-2 gap-1.5">
          <Link to="/channels">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            返回渠道列表
          </Link>
        </Button>
        <Alert variant="destructive" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{describeApiError(channelQuery.error)}</span>
            <Button variant="outline" size="sm" onClick={() => channelQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        </Alert>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="flex min-h-70 items-center justify-center gap-2 text-2sm text-muted-foreground">
        <Spinner label="正在准备表单" />
        正在准备表单…
      </div>
    );
  }

  const exact = isExactEndpoint(draft.connection_url);
  const chatPreview = chatUrlFor(draft.connection_url);
  const modelsPreview = modelsUrlFor(draft.connection_url);
  const canUseAuto = isNew || channel?.protocol_source === "auto";
  const status = lastTest?.status ?? channel?.connection_status ?? "unknown";
  const latency = lastTest?.latency_ms ?? channel?.connection_latency_ms ?? null;
  const message =
    lastTest?.message ??
    channel?.connection_message ??
    (isNew ? "尚未测试。填写连接信息后可直接点击测试，无需先保存。" : "尚无探测结果");
  const modelsPreviewList = lastTest?.models_preview ?? [];

  return (
    <div className="animate-fade-up">
      <Button asChild variant="ghost" size="sm" className="-ml-2 gap-1.5">
        <Link to="/channels">
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          渠道管理
        </Link>
      </Button>

      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="truncate text-h1 font-semibold tracking-[-0.02em] text-title">
              {isNew ? "新建渠道" : channel?.name}
            </h1>
            {channel ? <ConnectionStatusBadge status={channel.connection_status} /> : null}
            {isNew ? (
              <span className="rounded-badge bg-primary-soft px-2 py-0.5 text-2xs font-medium text-primary">
                尚未保存
              </span>
            ) : null}
            {!isNew && dirty ? (
              <span className="rounded-badge bg-warning-soft px-2 py-0.5 text-2xs font-medium text-warning-text">
                有未保存修改
              </span>
            ) : null}
          </div>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {isNew
              ? "填写连接信息 → 可先测试或获取模型 → 保存"
              : `${channel?.slug} · ${PROTOCOL_LABELS[channel?.protocol ?? "openai_chat"] ?? channel?.protocol}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex h-10 items-center gap-2 rounded-control border border-border bg-surface px-3">
            <span className="text-xs text-muted-foreground">启用</span>
            <EnabledToggle
              checked={draft.enabled}
              disabled={toggleMutation.isPending || isNew}
              label={`${draft.enabled ? "封禁" : "启用"} ${draft.name || "渠道"}`}
              onChange={(next) => {
                if (isNew) patch({ enabled: next });
                else toggleMutation.mutate(next);
              }}
            />
          </div>
          <Button
            variant="outline"
            className="min-w-33 justify-center"
            onClick={openDiscover}
          >
            <CloudDownload className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            获取上游模型
          </Button>
          <Button
            variant="outline"
            className="min-w-28 justify-center"
            onClick={runTest}
            disabled={testMutation.isPending}
          >
            {testMutation.isPending ? (
              <Spinner size="sm" label="正在测试连接" />
            ) : (
              <Zap className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            测试连接
          </Button>
          <Button
            className="min-w-28 justify-center"
            onClick={save}
            disabled={saveMutation.isPending}
          >
            {saveMutation.isPending ? (
              <Spinner size="sm" label="正在保存" />
            ) : (
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            {isNew ? "创建渠道" : "保存"}
          </Button>
        </div>
      </div>

      <Tabs value={tab} onValueChange={(value) => setTab(value as TabKey)}>
        <TabsList
          aria-label="渠道编辑分区"
          className="mt-6 flex h-auto w-full justify-start gap-1 overflow-x-auto rounded-none border-b border-border bg-transparent p-0"
        >
          {TABS.map((item) => {
            const Icon = item.icon;
            return (
              <TabsTrigger
                key={item.key}
                value={item.key}
                id={`channel-tab-${item.key}`}
                aria-controls={`channel-tabpanel-${item.key}`}
                className="group relative inline-flex shrink-0 items-center gap-1.5 rounded-none px-3 py-2.5 text-2sm font-normal text-muted-foreground shadow-none transition-colors hover:text-title data-[state=active]:bg-transparent data-[state=active]:font-medium data-[state=active]:text-primary data-[state=active]:shadow-none"
              >
                <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                {item.label}
                <span
                  className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary opacity-0 group-data-[state=active]:opacity-100"
                  aria-hidden="true"
                />
              </TabsTrigger>
            );
          })}
        </TabsList>

      <div className="mt-5">
        <TabsContent
          value="connection"
          id="channel-tabpanel-connection"
          aria-labelledby="channel-tab-connection"
          className="mt-0"
        >
          <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
            <Card className="p-5">
              <div>
                <h2 className="text-sm font-semibold text-title">连接信息</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  地址、渠道密钥与协议配置。保存只写入系统；测试始终使用页面当前值。
                </p>
              </div>

              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field className="gap-1.5" data-invalid={Boolean(errors.name)}>
                  <FieldLabel htmlFor="channel-name">渠道名称</FieldLabel>
                  <Input
                    id="channel-name"
                    value={draft.name}
                    onChange={(event) => patch({ name: event.target.value })}
                    aria-invalid={Boolean(errors.name)}
                    aria-describedby={errors.name ? "channel-name-error" : undefined}
                  />
                  <FieldError id="channel-name-error" className="text-xs">
                    {errors.name}
                  </FieldError>
                </Field>
                <Field className="gap-1.5" data-invalid={Boolean(errors.slug)}>
                  <FieldLabel htmlFor="channel-slug">渠道标识</FieldLabel>
                  <Input
                    id="channel-slug"
                    className="font-mono text-xs"
                    placeholder={isNew ? "留空自动生成" : undefined}
                    value={draft.slug}
                    onChange={(event) => patch({ slug: event.target.value })}
                    aria-invalid={Boolean(errors.slug)}
                    aria-describedby={errors.slug ? "channel-slug-error" : undefined}
                  />
                  <FieldError id="channel-slug-error" className="text-xs">
                    {errors.slug}
                  </FieldError>
                </Field>
              </div>

              <Field className="mt-4 gap-1.5" data-invalid={Boolean(errors.connection_url)}>
                <div className="flex items-center justify-between gap-2">
                  <FieldLabel htmlFor="channel-url">连接地址</FieldLabel>
                  {exact ? (
                    <span className="inline-flex items-center gap-1 rounded-badge bg-primary-soft px-2 py-0.5 text-2xs font-medium text-primary">
                      <Hash className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                      精确端点
                    </span>
                  ) : null}
                </div>
                <Input
                  id="channel-url"
                  className="font-mono text-xs"
                  placeholder="https://api.example.com/v1"
                  value={draft.connection_url}
                  onChange={(event) => patch({ connection_url: event.target.value })}
                  aria-invalid={Boolean(errors.connection_url)}
                  aria-describedby={
                    errors.connection_url ? "channel-url-help channel-url-error" : "channel-url-help"
                  }
                />
                <FieldDescription id="channel-url-help" className="text-xs">
                  末尾加 <code className="rounded-sm bg-surface-2 px-1 font-mono">#</code> 表示完整端点，系统不再追加路径。
                </FieldDescription>
                <FieldError id="channel-url-error" className="text-xs">
                  {errors.connection_url}
                </FieldError>
              </Field>

              <div className="mt-3 rounded-control border border-border bg-surface-2 px-3.5 py-3">
                <p className="text-2xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  最终请求地址
                </p>
                <p className="mt-1.5 break-all font-mono text-2xs text-title">
                  {chatPreview || "填写连接地址后自动生成"}
                </p>
                <p className="mt-1 break-all font-mono text-2xs text-muted-foreground">
                  /models：{modelsPreview || "—"}
                </p>
              </div>

              <div className="mt-4">
                <ChannelKeysPanel
                  channelId={channel?.id}
                  existingKeys={channel?.api_keys ?? []}
                  pendingKeys={pendingKeys}
                  onAddPending={(key) => setPendingKeys((current) => [...current, key])}
                  onRemovePending={(localId) =>
                    setPendingKeys((current) => current.filter((item) => item.local_id !== localId))
                  }
                  testInput={draftInput}
                />
              </div>

              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field className="gap-1.5">
                  <FieldLabel htmlFor="channel-protocol">协议</FieldLabel>
                  <Select
                    value={draft.protocol}
                    onValueChange={(value) =>
                      patch({ protocol: value as ChannelDraft["protocol"] })
                    }
                  >
                    <SelectTrigger
                      id="channel-protocol"
                      className="h-10 border-border-strong bg-surface"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {canUseAuto ? <SelectItem value="auto">自动探测（推荐）</SelectItem> : null}
                      {PROTOCOLS.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FieldDescription className="text-xs">
                    {draft.protocol === "auto"
                      ? AUTO_PROTOCOL_HINT
                      : PROTOCOLS.find((item) => item.value === draft.protocol)?.hint}
                  </FieldDescription>
                </Field>
                <Field className="gap-1.5" data-invalid={Boolean(errors.priority)}>
                  <FieldLabel htmlFor="channel-priority">优先级</FieldLabel>
                  <Input
                    id="channel-priority"
                    type="number"
                    min={1}
                    max={1000}
                    value={draft.priority}
                    onChange={(event) => patch({ priority: Number(event.target.value) || 0 })}
                    aria-invalid={Boolean(errors.priority)}
                    aria-describedby={
                      errors.priority ? "channel-priority-help channel-priority-error" : "channel-priority-help"
                    }
                  />
                  <FieldError id="channel-priority-error" className="text-xs">
                    {errors.priority}
                  </FieldError>
                  <FieldDescription id="channel-priority-help" className="text-xs">
                    数值越大越先调用；自动组合内仍按层等概率随机分流。
                  </FieldDescription>
                </Field>
              </div>
            </Card>

            <div className="space-y-4">
              <Card className="p-5">
                <div>
                  <h2 className="text-sm font-semibold text-title">连接状态</h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    测试使用页面最新填写的地址、密钥、协议与改写规则，不会保存；点右上角「测试连接」发起。
                  </p>
                </div>

                <div className="mt-4 flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">状态</span>
                  <ConnectionStatusBadge status={status} />
                </div>
                <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">耗时</span>
                  <span className="tabular-nums text-body">{formatDuration(latency)}</span>
                </div>
                <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">最近测试</span>
                  <span className="text-body">
                    {lastTest ? "刚刚（未保存）" : formatDateTime(channel?.connection_tested_at, mode)}
                  </span>
                </div>
                <p className="mt-3 break-words rounded-control bg-surface-2 px-2.5 py-2 text-2xs leading-5 text-muted-foreground">
                  {message}
                </p>

                {modelsPreviewList.length ? (
                  <div className="mt-3">
                    <p className="text-2xs uppercase tracking-[0.1em] text-muted-foreground">模型预览</p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {modelsPreviewList.slice(0, 8).map((model) => (
                        <span
                          key={model}
                          className="rounded-badge border border-border bg-surface-2 px-2 py-0.5 font-mono text-2xs text-body"
                        >
                          {model}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}

                {!isNew && lastTest ? (
                  <p className="mt-3 text-2xs text-warning-text">
                    这是未保存草稿的测试结果，渠道卡片上的状态不会改变。
                  </p>
                ) : null}

                {channel ? (
                  <div className="mt-4 grid gap-2 border-t border-border pt-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-muted-foreground">鉴权方式</span>
                      <span className="text-title">
                        {AUTH_STYLE_LABELS[channel.auth_style] ?? channel.auth_style}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-muted-foreground">已识别协议</span>
                      <span className="text-title">
                        {PROTOCOL_LABELS[channel.protocol] ?? channel.protocol}
                        {channel.protocol_source === "auto" ? "（自动）" : "（手动）"}
                      </span>
                    </div>
                  </div>
                ) : null}
              </Card>

              <Card className="p-5">
                <h2 className="text-sm font-semibold text-title">模型</h2>
                <p className="mt-1 text-xs leading-6 text-muted-foreground">
                  {channel ? (
                    <>
                      已接入路由{" "}
                      <span className="font-medium tabular-nums text-title">{channel.model_count}</span>{" "}
                      个。获取上游模型后可勾选写入系统。
                    </>
                  ) : (
                    "新渠道保存后，才能在「路由与映射」中写入模型。获取上游模型本身无需保存。"
                  )}
                </p>
                <Button
                  variant="outline"
                  className="mt-3 w-full justify-center"
                  onClick={() => setTab("routing")}
                >
                  <Blocks className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  前往路由与映射
                </Button>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent
          value="routing"
          id="channel-tabpanel-routing"
          aria-labelledby="channel-tab-routing"
          className="mt-0"
        >
          {channel ? (
            <div className="space-y-4">
              <ChannelModelsPanel channelId={channel.id} />
            </div>
          ) : (
            <Card className="flex min-h-65 flex-col items-center justify-center px-6 text-center">
              <Blocks className="h-6 w-6 text-faint" strokeWidth={1.5} aria-hidden="true" />
              <h2 className="mt-3 text-sm font-semibold text-title">先保存渠道，再配置路由</h2>
              <p className="mt-1 max-w-md text-xs leading-6 text-muted-foreground">
                模型映射需要渠道 ID。可以先用右上角「测试连接」或「获取上游模型」验证配置，再保存。
              </p>
              <Button className="mt-4" onClick={save} disabled={saveMutation.isPending}>
                <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                创建渠道
              </Button>
            </Card>
          )}
        </TabsContent>

        <TabsContent
          value="rewrite"
          id="channel-tabpanel-rewrite"
          aria-labelledby="channel-tab-rewrite"
          className="mt-0"
        >
          {channel ? (
            <RewriteRulesEditor
              channelId={channel.id}
              rules={channel.rewrite_rules}
              value={rulesDraft}
              onChange={setRulesDraft}
            />
          ) : (
            <Card className="flex min-h-60 flex-col items-center justify-center px-6 text-center">
              <ScrollText className="h-6 w-6 text-faint" strokeWidth={1.5} aria-hidden="true" />
              <h2 className="mt-3 text-sm font-semibold text-title">先保存渠道，再配置改写</h2>
              <p className="mt-1 max-w-md text-xs leading-6 text-muted-foreground">
                请求头/请求体与响应改写需要渠道 ID 才能预览和保存。
              </p>
            </Card>
          )}
        </TabsContent>

        <TabsContent
          value="settings"
          id="channel-tabpanel-settings"
          aria-labelledby="channel-tab-settings"
          className="mt-0"
        >
          <div className="space-y-4">
            <Card className="p-5">
              <h2 className="text-sm font-semibold text-title">其他设置</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">备注用于列表识别，不发送给上游。</p>
              <Field className="mt-4 gap-1.5">
                <FieldLabel htmlFor="channel-note">备注</FieldLabel>
                <Textarea
                  id="channel-note"
                  className="min-h-24 resize-y text-2sm leading-6"
                  maxLength={500}
                  placeholder="例如：需要携带 X-Codegen-Client-Name 请求头"
                  value={draft.note}
                  onChange={(event) => patch({ note: event.target.value })}
                />
                <FieldDescription className="text-right text-xs text-faint">
                  {draft.note.length} / 500
                </FieldDescription>
              </Field>
              <div className="mt-4 flex items-center justify-between gap-3 rounded-control border border-border px-3.5 py-3">
                <div>
                  <p className="text-2sm font-medium text-title">启用渠道</p>
                  <p className="mt-0.5 text-2xs text-muted-foreground">封禁后该渠道不再参与模型路由。</p>
                </div>
                <EnabledToggle
                  checked={draft.enabled}
                  disabled={toggleMutation.isPending}
                  label={`${draft.enabled ? "封禁" : "启用"} ${draft.name || "渠道"}`}
                  onChange={(next) => {
                    if (isNew) patch({ enabled: next });
                    else toggleMutation.mutate(next);
                  }}
                />
              </div>
            </Card>

            {channel ? (
              <>
                <Card className="p-5">
                  <h2 className="text-sm font-semibold text-title">渠道信息</h2>
                  <dl className="mt-2">
                    <DetailRow label="渠道 ID">
                      <span className="break-all font-mono text-xs">{channel.id}</span>
                    </DetailRow>
                    <DetailRow label="连接状态">
                      <ConnectionStatusBadge status={channel.connection_status} />
                    </DetailRow>
                    <DetailRow label="最近结果">
                      <span className="break-all">{channel.connection_message || "—"}</span>
                    </DetailRow>
                    <DetailRow label="创建时间">
                      <span>{formatDateTime(channel.created_at, mode)}</span>
                    </DetailRow>
                    <DetailRow label="更新时间">
                      <span>{formatDateTime(channel.updated_at, mode)}</span>
                    </DetailRow>
                  </dl>
                </Card>

                <div>
                  <div className="mb-3 flex items-center gap-2">
                    <Gauge className="h-4 w-4 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
                    <h2 className="text-sm font-semibold text-title">运行概览</h2>
                    <span className="text-2xs text-faint">近 7 天</span>
                  </div>
                  <ChannelOverview channel={channel} />
                </div>
              </>
            ) : null}
          </div>
        </TabsContent>
      </div>
      </Tabs>

      <AlertDialog
        open={keylessPrompt !== null}
        onOpenChange={(open) => {
          if (!open) setKeylessPrompt(null);
        }}
      >
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle>这个渠道还没有密钥</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm leading-relaxed">
                <p>
                  上游大多要求鉴权，不带密钥的请求会被直接拒绝（HTTP 401）——
                  那不是密钥失效，而是这次请求根本没有带上密钥。
                </p>
                <p>先在「渠道密钥」里粘贴密钥并点「添加」，再{keylessPrompt === "test" ? "测试连接" : "获取模型"}。</p>
                <p className="text-muted-foreground">匿名可访问的自建上游可以继续。</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="outline" onClick={proceedKeyless}>
              仍然匿名尝试
            </Button>
            <Button
              onClick={() => {
                setKeylessPrompt(null);
                focusKeyInput();
              }}
            >
              去添加密钥
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <DiscoverModelsDialog
        open={discoverOpen}
        keyless={!hasUsableKey}
        onOpenChange={handleDiscoverOpenChange}
        channelId={channel?.id}
        onCreateChannel={isNew ? createFromDialog : undefined}
        input={
          draftInput ?? {
            connection_url: draft.connection_url.trim(),
            rewrite_rules: rulesDraft,
          }
        }
        onAdded={(result) => {
          setTab("routing");
          notify.success("模型已加入路由", {
            description: `新增 ${result.created} 个${result.skipped ? `，跳过 ${result.skipped} 个重复项` : ""}`,
          });
          const created = createdChannelRef.current;
          if (created) {
            createdChannelRef.current = null;
            navigate(`/channels/${created.id}`, { replace: true });
          }
        }}
      />
    </div>
  );
}
