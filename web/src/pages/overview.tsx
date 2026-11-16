import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpen,
  Check,
  Copy,
  KeyRound,
  Layers,
  Link2,
  Pencil,
  RefreshCw,
  Rocket,
  Server,
  ShieldCheck,
  Sparkles,
  Terminal,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Alert } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { notify } from "@/lib/notify";
import { describeApiError, listChannels } from "@/lib/channels";
import { copyText } from "@/lib/clipboard";
import { listKeys } from "@/lib/keys";
import { listCatalogEntries } from "@/lib/playground";
import {
  effectiveGatewayBaseUrl,
  getSettings,
  updateSettings,
  validateGatewayBaseUrl,
  type SystemSettings,
} from "@/lib/settings";
import { cn } from "@/lib/utils";

const ENDPOINTS = [
  {
    protocol: "OpenAI 兼容",
    method: "POST",
    path: "/v1/chat/completions",
    description: "对话补全，支持流式与非流式",
  },
  {
    protocol: "OpenAI 兼容",
    method: "GET",
    path: "/v1/models",
    description: "列出当前可调用的模型",
  },
  {
    protocol: "Anthropic",
    method: "POST",
    path: "/v1/messages",
    description: "Claude Messages 协议",
  },
  {
    protocol: "Gemini",
    method: "POST",
    path: "/v1beta/models/{model}:generateContent",
    description: "Gemini 原生协议",
  },
];

export function OverviewPage() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const channelsQuery = useQuery({ queryKey: ["channels", { search: "", status: "" }], queryFn: () => listChannels() });
  const keysQuery = useQuery({ queryKey: ["keys"], queryFn: listKeys });
  const catalogQuery = useQuery({ queryKey: ["catalog-entries"], queryFn: () => listCatalogEntries() });

  const [editing, setEditing] = useState(false);
  const [gatewayInput, setGatewayInput] = useState("");
  const [gatewayError, setGatewayError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const editTriggerRef = useRef<HTMLButtonElement | null>(null);

  /** 退出内联编辑后把焦点还给触发按钮；保存/取消/Escape 三个出口保持一致。 */
  const closeEditing = () => {
    setEditing(false);
    setGatewayError(null);
    window.requestAnimationFrame(() => editTriggerRef.current?.focus({ preventScroll: true }));
  };

  useEffect(() => {
    if (!settingsQuery.data) return;
    setGatewayInput(settingsQuery.data.gateway_base_url);
  }, [settingsQuery.data]);

  const settings = settingsQuery.data;
  const gateway = effectiveGatewayBaseUrl(settings?.gateway_base_url ?? "");
  const curlExample = useMemo(
    () =>
      [
        `curl ${gateway.url}/v1/chat/completions \\`,
        `  -H "Authorization: Bearer $SLIPSTREAM_KEY" \\`,
        `  -H "Content-Type: application/json" \\`,
        `  -d '{"model":"<模型名>","messages":[{"role":"user","content":"你好"}]}'`,
      ].join("\n"),
    [gateway.url],
  );

  const gatewayMutation = useMutation({
    mutationFn: (baseUrl: string) => updateSettings({ gateway_base_url: baseUrl }),
    onSuccess: async (next: SystemSettings) => {
      queryClient.setQueryData(["settings"], next);
      closeEditing();
      notify.success(
        next.gateway_base_url ? "网关地址已保存" : "已恢复为自动使用当前页面地址",
      );
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => setGatewayError(describeApiError(error)),
  });

  const submitGateway = () => {
    const error = validateGatewayBaseUrl(gatewayInput);
    setGatewayError(error);
    if (error) return;
    gatewayMutation.mutate(gatewayInput.trim());
  };

  const handleCopy = async (value: string, label: string) => {
    try {
      await copyText(value);
      setCopied(label);
      window.setTimeout(() => setCopied((current) => (current === label ? null : current)), 1600);
      notify.success("已复制到剪贴板");
    } catch {
      notify.error("复制失败，请手动选择文本");
    }
  };

  // 统计卡的次级信息：给出构成而不是重复主数字；查询未返回时不显示，避免「0 个」误导。
  const catalogItems = catalogQuery.data?.items ?? [];
  const catalogSub = catalogQuery.data
    ? `${catalogItems.filter((item) => item.kind === "direct").length} 个直连 · ${catalogItems.length - catalogItems.filter((item) => item.kind === "direct").length} 个组合`
    : undefined;
  const channelsSub = channelsQuery.data
    ? `${channelsQuery.data.items.filter((channel) => channel.enabled).length} 个启用中`
    : undefined;
  const keysSub = keysQuery.data
    ? `${keysQuery.data.items.filter((key) => key.enabled).length} 个启用中`
    : undefined;

  const stats = [
    {
      label: "可调用模型",
      value: catalogQuery.data?.total ?? "—",
      sub: catalogSub,
      icon: Layers,
      to: "/catalog",
      error: catalogQuery.error,
      retry: () => void catalogQuery.refetch(),
    },
    {
      label: "渠道",
      value: channelsQuery.data?.total ?? "—",
      sub: channelsSub,
      icon: Server,
      to: "/channels",
      error: channelsQuery.error,
      retry: () => void channelsQuery.refetch(),
    },
    {
      label: "用户密钥",
      value: keysQuery.data?.total ?? "—",
      sub: keysSub,
      icon: KeyRound,
      to: "/keys",
      error: keysQuery.error,
      retry: () => void keysQuery.refetch(),
    },
  ];

  return (
    <div className="animate-fade-up">
      <div>
        <h1 className="text-h1 font-semibold tracking-[-0.02em] text-title">全局概览</h1>
        <p className="mt-1.5 max-w-3xl text-2sm leading-6 text-muted-foreground">
           这里是接入驭流所需的全部连接信息。客户端把请求发到下方网关地址，使用用户密钥认证，即可调用已配置渠道中的模型。
        </p>
      </div>

      {settingsQuery.isError ? (
        <Alert variant="destructive" className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{describeApiError(settingsQuery.error)}</span>
            <Button variant="outline" size="sm" onClick={() => settingsQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              重试
            </Button>
          </div>
        </Alert>
      ) : null}

      <Card className="mt-6 overflow-hidden">
        <div className="grid lg:grid-cols-[1.15fr_1fr]">
          <div className="border-b border-border p-5 lg:border-b-0 lg:border-r">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-soft text-primary">
                <Link2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-title">连接地址</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">所有 API 请求的 Base URL</p>
              </div>
            </div>

            {editing ? (
              <div className="mt-4">
                <Label htmlFor="gateway-base-url">网关地址</Label>
                <Input
                  id="gateway-base-url"
                  className="mt-1.5 font-mono text-xs"
                  autoFocus
                  placeholder="https://gateway.example.com"
                  value={gatewayInput}
                  onChange={(event) => setGatewayInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") submitGateway();
                    if (event.key === "Escape" && !gatewayMutation.isPending) closeEditing();
                  }}
                  aria-invalid={Boolean(gatewayError)}
                />
                <p className="mt-1 text-2xs leading-5 text-muted-foreground">
                  例如 <code className="font-mono">http://192.168.1.10:3681</code>。留空则自动使用当前浏览器地址。
                </p>
                {gatewayError ? (
                  <p className="mt-1 text-2xs text-danger-text">{gatewayError}</p>
                ) : null}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    onClick={submitGateway}
                    disabled={
                      gatewayMutation.isPending ||
                      gatewayInput.trim() === (settings?.gateway_base_url ?? "")
                    }
                  >
                    {gatewayMutation.isPending ? (
                      <Spinner size="sm" />
                    ) : (
                      <Check className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    )}
                    保存
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={gatewayMutation.isPending}
                    onClick={() => {
                      closeEditing();
                      setGatewayInput(settings?.gateway_base_url ?? "");
                    }}
                  >
                    <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    取消
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="mt-4 flex items-start gap-2">
                  <div className="min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-3.5 py-3">
                    <p className="break-all font-mono text-sm font-medium text-title">
                      {gateway.url}
                    </p>
                    <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                      {gateway.source === "configured" ? (
                        <>
                          <span className="inline-flex items-center gap-1 rounded-badge bg-success-soft px-1.5 py-0.5 font-medium text-success-text">
                            <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
                            已配置
                          </span>
                          {settings?.gateway_updated_by ? (
                            <span>由 {settings.gateway_updated_by} 设置</span>
                          ) : null}
                        </>
                      ) : (
                        <span>未配置，按当前浏览器地址推断。部署到公网时请改成外部可访问的地址。</span>
                      )}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-10 w-10 shrink-0"
                    aria-label="复制连接地址"
                    onClick={() => handleCopy(gateway.url, "base")}
                  >
                    {copied === "base" ? (
                      <Check className="h-4 w-4 text-success" strokeWidth={1.75} aria-hidden="true" />
                    ) : (
                      <Copy className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    )}
                  </Button>
                  <Button
                    ref={editTriggerRef}
                    variant="outline"
                    size="icon"
                    className="h-10 w-10 shrink-0"
                    aria-label="修改连接地址"
                    onClick={() => setEditing(true)}
                  >
                    <Pencil className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  </Button>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-badge border border-border bg-surface px-2 py-1 text-2xs text-muted-foreground">
                    <ShieldCheck className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                    认证：Authorization: Bearer &lt;用户密钥&gt;
                  </span>
                  <Link
                    to="/keys"
                    className="inline-flex items-center gap-1 text-2xs font-medium text-primary hover:underline"
                  >
                    创建用户密钥
                    <ArrowRight className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                  </Link>
                </div>
              </>
            )}
          </div>

          <div className="bg-[var(--code-bg)] p-5 text-[var(--code-fg)]">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Terminal className="h-4 w-4 text-[var(--code-fg-muted)]" strokeWidth={1.75} aria-hidden="true" />
                <h2 className="text-2sm font-semibold text-[var(--code-fg-strong)]">快速调用示例</h2>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-[var(--code-fg-muted)] hover:bg-[var(--code-hover)] hover:text-[var(--code-fg-strong)]"
                onClick={() => handleCopy(curlExample, "curl")}
              >
                {copied === "curl" ? (
                  <Check className="h-3.5 w-3.5 text-success" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <Copy className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                )}
                复制
              </Button>
            </div>
            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words font-mono text-2xs leading-6 text-[var(--code-fg)]">
              {curlExample}
            </pre>
            <p className="mt-2 text-2xs leading-5 text-[var(--code-fg-faint)]">
              把 <code className="text-[var(--code-fg)]">$SLIPSTREAM_KEY</code> 换成用户密钥，模型名可从
              <Link to="/catalog" className="mx-1 text-[var(--code-fg)] underline underline-offset-2 hover:text-[var(--code-fg-strong)]">
                模型目录
              </Link>
              复制。
            </p>
          </div>
        </div>
      </Card>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {stats.map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className="group">
              <Link to={item.to} className="block">
                <Card className="flex items-center gap-3 p-4 transition-colors group-hover:border-primary/40">
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-2 text-muted-foreground transition-colors group-hover:bg-primary-soft group-hover:text-primary">
                    <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-2xs text-muted-foreground">{item.label}</p>
                    <p className="mt-0.5 text-metric font-semibold tabular-nums tracking-[-0.02em] text-title">
                      {item.value}
                    </p>
                    {item.sub ? (
                      <p className="mt-0.5 text-2xs text-muted-foreground">{item.sub}</p>
                    ) : null}
                  </div>
                  <ArrowRight
                    className="h-4 w-4 shrink-0 text-muted-foreground transition-all duration-150 group-hover:translate-x-0.5 group-hover:text-primary"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                </Card>
              </Link>
              {item.error ? (
                <div className="mt-1 flex items-start gap-2 text-2xs">
                  <span className="min-w-0 flex-1 text-danger-text">
                    加载失败：{describeApiError(item.error)}
                  </span>
                  <button
                    type="button"
                    className="shrink-0 font-medium text-muted-foreground underline underline-offset-2 transition-colors hover:text-title"
                    onClick={item.retry}
                  >
                    重试
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card className="overflow-hidden">
          <div className="border-b border-border px-5 py-4">
            <h2 className="text-sm font-semibold text-title">API 端点</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">按上游协议自动路由，模型名全局唯一。</p>
          </div>
          <div className="divide-y divide-border">
            {ENDPOINTS.map((endpoint) => (
              <div key={endpoint.path} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <span
                  className={cn(
                    "inline-flex w-13 shrink-0 justify-center rounded-badge px-1.5 py-0.5 font-mono text-2xs font-semibold",
                    endpoint.method === "GET"
                      ? "bg-success-soft text-success-text"
                      : "bg-primary-soft text-primary",
                  )}
                >
                  {endpoint.method}
                </span>
                <code className="min-w-0 flex-1 break-all font-mono text-xs text-title">
                  {endpoint.path}
                </code>
                <span className="text-2xs text-muted-foreground">{endpoint.description}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2">
            <Rocket className="h-4 w-4 text-primary" strokeWidth={1.75} aria-hidden="true" />
            <h2 className="text-sm font-semibold text-title">开始使用</h2>
          </div>
          <ol className="mt-3 space-y-3">
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-2xs font-semibold text-primary">1</span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-title">添加渠道与模型</p>
                <p className="mt-0.5 text-2xs leading-5 text-muted-foreground">填入上游地址和密钥，获取可用模型。</p>
                <Link to="/channels" className={cn(buttonVariants({ variant: "link", size: "sm" }), "mt-1 h-auto p-0 text-2xs")}>
                  前往渠道管理
                  <ArrowRight className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                </Link>
              </div>
            </li>
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-2xs font-semibold text-primary">2</span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-title">签发用户密钥</p>
                <p className="mt-0.5 text-2xs leading-5 text-muted-foreground">客户端凭密钥调用，可随时封禁。</p>
                <Link to="/keys" className={cn(buttonVariants({ variant: "link", size: "sm" }), "mt-1 h-auto p-0 text-2xs")}>
                  管理用户密钥
                  <ArrowRight className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                </Link>
              </div>
            </li>
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-2xs font-semibold text-primary">3</span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-title">复制模型名并发起请求</p>
                <p className="mt-0.5 text-2xs leading-5 text-muted-foreground">模型名与协议无关，切换上游无需改代码。</p>
                <Link to="/playground" className={cn(buttonVariants({ variant: "link", size: "sm" }), "mt-1 h-auto p-0 text-2xs")}>
                  在模型试验中验证
                  <Sparkles className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                </Link>
              </div>
            </li>
          </ol>
        </Card>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-surface px-5 py-4">
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <BookOpen className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          模型目录同时支持直连模型、手动组合与自动组合，调用时统一使用模型名。
        </p>
        <Link
          to="/catalog"
          className={cn(buttonVariants({ variant: "outline", size: "sm" }), "shrink-0")}
        >
          查看模型目录
          <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        </Link>
      </div>
    </div>
  );
}
