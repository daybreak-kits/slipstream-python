import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clock3,
  Database,
  EyeOff,
  FileText,
  KeyRound,
  Link2,
  Monitor,
  Moon,
  RefreshCw,
  Route,
  Save,
  ShieldCheck,
  Sun,
  Timer,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { describeApiError } from "@/lib/channels";
import {
  changeAdminPassword,
  DEFAULT_INFLIGHT_CAP,
  DEFAULT_ROUTE_STRATEGY,
  DEFAULT_ROUTE_WINDOW_MINUTES,
  effectiveGatewayBaseUrl,
  getSettings,
  parseInflightCap,
  ROUTE_STRATEGY_OPTIONS,
  parseRouteWindow,
  parseUpstreamTimeout,
  updateSettings,
  validateGatewayBaseUrl,
  type BodyPolicy,
  type RouteStrategy,
  type SystemSettings,
} from "@/lib/settings";
import { useThemeMode, type ThemeMode } from "@/lib/theme";
import { formatDateTime, TIMEZONE_LABEL, useTimezone, type TimezoneMode } from "@/lib/time";
import { cn } from "@/lib/utils";

const POLICY_OPTIONS: Array<{
  value: BodyPolicy;
  title: string;
  description: string;
}> = [
  {
    value: "off",
    title: "关闭",
    description: "不记录任何请求/响应正文，只保留日志元数据。",
  },
  {
    value: "errors",
    title: "仅错误",
    description: "记录 4xx/5xx、伪成功、流断、超时与连接失败。",
  },
  {
    value: "all",
    title: "全部",
    description: "每次请求都记录脱敏、截断后的正文。",
  },
];

const ROUTE_WINDOW_PRESETS = [15, 30, 60] as const;

function isRouteWindowPreset(value: number): value is (typeof ROUTE_WINDOW_PRESETS)[number] {
  return (ROUTE_WINDOW_PRESETS as readonly number[]).includes(value);
}

const LIMITS = [
  {
    icon: FileText,
    title: "正文 16KB 截断",
    description: "request_body / upstream_body / response_body 各自最多 16KB。",
  },
  {
    icon: EyeOff,
    title: "reasoning_content 单独 16KB",
    description: "推理内容单独存储，不占用正文额度。",
  },
  {
    icon: Database,
    title: "流式摘要 64KB",
    description: "流式请求只保存 delta_count、首尾预览、完成标记与错误事件。",
  },
  {
    icon: Clock3,
    title: "正文保留 7 天，日志保留 30 天",
    description: "每天清空 7 天前的正文；30 天后日志与正文一起删除。",
  },
];

function updatedLabel(settings: SystemSettings, mode: TimezoneMode): string {
  if (!settings.updated_at) return "尚未修改，当前使用默认策略";
  const by = settings.updated_by || "未知管理员";
  return `最后更新：${formatDateTime(settings.updated_at, mode)} · ${by}`;
}

const THEME_OPTIONS: Array<{ value: ThemeMode; label: string; description: string; icon: typeof Sun }> = [
  { value: "system", label: "跟随系统", description: "随操作系统切换", icon: Monitor },
  { value: "light", label: "浅色", description: "始终使用浅色", icon: Sun },
  { value: "dark", label: "深色", description: "始终使用深色", icon: Moon },
];

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<BodyPolicy>("off");
  const [saved, setSaved] = useState(false);
  const [gatewayInput, setGatewayInput] = useState("");
  const [gatewayError, setGatewayError] = useState<string | null>(null);
  const [gatewaySaved, setGatewaySaved] = useState(false);
  const [upstreamTimeout, setUpstreamTimeout] = useState({ connect: "", firstByte: "", read: "" });
  const [upstreamError, setUpstreamError] = useState<string | null>(null);
  const [upstreamSaved, setUpstreamSaved] = useState(false);
  const [routeStrategy, setRouteStrategy] = useState<RouteStrategy>(DEFAULT_ROUTE_STRATEGY);
  const [routeWindowChoice, setRouteWindowChoice] = useState<string>("30");
  const [routeWindowCustom, setRouteWindowCustom] = useState("30");
  const [inflightCap, setInflightCap] = useState(String(DEFAULT_INFLIGHT_CAP));
  const [routeError, setRouteError] = useState<string | null>(null);
  const [routeSaved, setRouteSaved] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const { mode: themeMode, setMode: setThemeMode } = useThemeMode();
  const { mode: timezoneMode, setMode: setTimezoneMode } = useTimezone();
  const location = useLocation();
  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: getSettings });

  useEffect(() => {
    if (location.hash !== "#admin-account") return;
    requestAnimationFrame(() => {
      document.getElementById("admin-account")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [location.hash]);

  useEffect(() => {
    if (settingsQuery.data) {
      setDraft(settingsQuery.data.body_policy);
      setGatewayInput(settingsQuery.data.gateway_base_url);
      setUpstreamTimeout({
        connect: String(settingsQuery.data.upstream_connect_timeout_s),
        firstByte: String(settingsQuery.data.upstream_first_byte_timeout_s),
        read: String(settingsQuery.data.upstream_read_timeout_s),
      });
      const savedWindow = settingsQuery.data.route_window_minutes ?? DEFAULT_ROUTE_WINDOW_MINUTES;
      setRouteStrategy(settingsQuery.data.route_strategy ?? DEFAULT_ROUTE_STRATEGY);
      setRouteWindowChoice(isRouteWindowPreset(savedWindow) ? String(savedWindow) : "custom");
      setRouteWindowCustom(String(savedWindow));
      setInflightCap(String(settingsQuery.data.gateway_inflight_cap ?? DEFAULT_INFLIGHT_CAP));
    }
  }, [settingsQuery.data]);

  const mutation = useMutation({
    mutationFn: (policy: BodyPolicy) => updateSettings(policy),
    onSuccess: async (settings) => {
      setSaved(true);
      setDraft(settings.body_policy);
      queryClient.setQueryData(["settings"], settings);
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  const gatewayMutation = useMutation({
    mutationFn: (baseUrl: string) => updateSettings({ gateway_base_url: baseUrl }),
    onSuccess: async (next) => {
      setGatewaySaved(true);
      setGatewayError(null);
      setGatewayInput(next.gateway_base_url);
      queryClient.setQueryData(["settings"], next);
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => {
      setGatewaySaved(false);
      setGatewayError(describeApiError(error));
    },
  });

  const submitGateway = () => {
    const trimmed = gatewayInput.trim();
    setGatewaySaved(false);
    const error = validateGatewayBaseUrl(trimmed);
    setGatewayError(error);
    if (error) return;
    gatewayMutation.mutate(trimmed);
  };

  const upstreamMutation = useMutation({
    mutationFn: (
      input:
        | {
            upstream_connect_timeout_s: number;
            upstream_first_byte_timeout_s: number;
            upstream_read_timeout_s: number;
          }
        | { upstream_timeouts_reset: boolean },
    ) => updateSettings(input),
    onSuccess: async (next) => {
      setUpstreamSaved(true);
      setUpstreamError(null);
      setUpstreamTimeout({
        connect: String(next.upstream_connect_timeout_s),
        firstByte: String(next.upstream_first_byte_timeout_s),
        read: String(next.upstream_read_timeout_s),
      });
      queryClient.setQueryData(["settings"], next);
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => {
      setUpstreamSaved(false);
      setUpstreamError(describeApiError(error));
    },
  });

  const routeMutation = useMutation({
    mutationFn: (input: {
      route_strategy: RouteStrategy;
      route_window_minutes: number;
      gateway_inflight_cap: number;
    }) => updateSettings(input),
    onSuccess: async (next) => {
      setRouteSaved(true);
      setRouteError(null);
      const savedWindow = next.route_window_minutes ?? DEFAULT_ROUTE_WINDOW_MINUTES;
      setRouteStrategy(next.route_strategy ?? DEFAULT_ROUTE_STRATEGY);
      setRouteWindowChoice(isRouteWindowPreset(savedWindow) ? String(savedWindow) : "custom");
      setRouteWindowCustom(String(savedWindow));
      setInflightCap(String(next.gateway_inflight_cap ?? DEFAULT_INFLIGHT_CAP));
      queryClient.setQueryData(["settings"], next);
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => {
      setRouteSaved(false);
      setRouteError(describeApiError(error));
    },
  });

  const submitUpstreamTimeouts = () => {
    const connect = parseUpstreamTimeout(upstreamTimeout.connect);
    const firstByte = parseUpstreamTimeout(upstreamTimeout.firstByte);
    const read = parseUpstreamTimeout(upstreamTimeout.read);
    if (typeof connect === "string") {
      setUpstreamError(`连接超时${connect}`);
      return;
    }
    if (typeof firstByte === "string") {
      setUpstreamError(`首字节超时${firstByte}`);
      return;
    }
    if (typeof read === "string") {
      setUpstreamError(`读空闲超时${read}`);
      return;
    }
    setUpstreamSaved(false);
    setUpstreamError(null);
    upstreamMutation.mutate({
      upstream_connect_timeout_s: connect,
      upstream_first_byte_timeout_s: firstByte,
      upstream_read_timeout_s: read,
    });
  };

  const resetUpstreamTimeouts = () => {
    setUpstreamSaved(false);
    setUpstreamError(null);
    upstreamMutation.mutate({ upstream_timeouts_reset: true });
  };

  const submitRoutePolicy = () => {
    setRouteSaved(false);
    let windowMinutes: number;
    if (routeWindowChoice === "custom") {
      const parsedWindow = parseRouteWindow(routeWindowCustom);
      if (typeof parsedWindow === "string") {
        setRouteError(`自定义窗口${parsedWindow}`);
        return;
      }
      windowMinutes = parsedWindow;
    } else {
      windowMinutes = Number(routeWindowChoice);
    }
    const parsedCap = parseInflightCap(inflightCap);
    if (typeof parsedCap === "string") {
      setRouteError(`在飞上限${parsedCap}`);
      return;
    }
    setRouteError(null);
    routeMutation.mutate({
      route_strategy: routeStrategy,
      route_window_minutes: windowMinutes,
      gateway_inflight_cap: parsedCap,
    });
  };

  const passwordMutation = useMutation({
    mutationFn: () => changeAdminPassword(currentPassword, newPassword),
    onSuccess: () => {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordError(null);
      setPasswordSaved(true);
    },
    onError: (error) => {
      setPasswordSaved(false);
      setPasswordError(describeApiError(error));
    },
  });

  // 成功提示是一次性的：6 秒后自动消失，重新输入密码时立即清除。
  useEffect(() => {
    if (!passwordSaved) return;
    const timer = window.setTimeout(() => setPasswordSaved(false), 6000);
    return () => window.clearTimeout(timer);
  }, [passwordSaved]);

  const submitPassword = () => {
    setPasswordError(null);
    setPasswordSaved(false);
    if (!currentPassword || !newPassword) {
      setPasswordError("请填写当前密码与新密码");
      return;
    }
    if (newPassword.length < 6) {
      setPasswordError("新密码至少 6 位");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("两次输入的新密码不一致");
      return;
    }
    passwordMutation.mutate();
  };

  const settings = settingsQuery.data;
  const dirty = Boolean(settings && settings.body_policy !== draft);
  const upstreamDirty = Boolean(
    settings &&
      (Number(upstreamTimeout.connect) !== settings.upstream_connect_timeout_s ||
        Number(upstreamTimeout.firstByte) !== settings.upstream_first_byte_timeout_s ||
        Number(upstreamTimeout.read) !== settings.upstream_read_timeout_s),
  );
  const savedRouteStrategy = settings?.route_strategy ?? DEFAULT_ROUTE_STRATEGY;
  const savedRouteWindow = settings?.route_window_minutes ?? DEFAULT_ROUTE_WINDOW_MINUTES;
  const savedInflightCap = settings?.gateway_inflight_cap ?? DEFAULT_INFLIGHT_CAP;
  const routeWindowValue =
    routeWindowChoice === "custom" ? Number(routeWindowCustom) : Number(routeWindowChoice);
  const routeDirty = Boolean(
    settings &&
      (routeStrategy !== savedRouteStrategy ||
        routeWindowValue !== savedRouteWindow ||
        Number(inflightCap) !== savedInflightCap),
  );

  const choose = (policy: BodyPolicy) => {
    setSaved(false);
    mutation.reset();
    setDraft(policy);
  };

  return (
    <div className="animate-fade-up">
      <div>
        <h1 className="text-h1 font-semibold tracking-[-0.02em] text-title">系统设置</h1>
        <p className="mt-1.5 max-w-2xl text-2sm text-muted-foreground">
          管理管理员账号、时间显示与主题，并控制路由策略、请求准入与请求日志是否保存正文。脱敏始终强制执行，无法关闭。
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

      <Card className="mt-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
              <Link2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-title">网关连接地址</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                展示在全局概览中，供客户端填写 Base URL。留空时自动使用当前浏览器地址。
              </p>
            </div>
          </div>
          <span className="rounded-badge bg-surface-2 px-2 py-1 font-mono text-2xs text-muted-foreground">
            当前：{effectiveGatewayBaseUrl(gatewayInput).url}
          </span>
        </div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="网关连接地址"
            className="font-mono text-xs"
            placeholder="http://192.168.1.10:3681"
            value={gatewayInput}
            onChange={(event) => setGatewayInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submitGateway();
            }}
            aria-invalid={Boolean(gatewayError)}
          />
          <Button
            className="shrink-0"
            onClick={submitGateway}
            disabled={
              gatewayMutation.isPending ||
              gatewayInput.trim() === (settings?.gateway_base_url ?? "")
            }
          >
            {gatewayMutation.isPending ? (
              <Spinner size="md" label="" aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            保存地址
          </Button>
        </div>
        {gatewayError ? (
          <FieldError className="mt-2" errors={[{ message: gatewayError }]} />
        ) : null}
        {gatewaySaved ? (
          <p className="mt-2 text-2xs text-success-text">
            地址已保存，可在全局概览中查看完整连接信息。
          </p>
        ) : null}
      </Card>

      <Card className="mt-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
              <Timer className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-title">上游超时看门狗</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                三道看门狗同时约束流式与非流式请求；流式没有总时长上限，慢流由客户端决定何时放弃。保存后立即生效，无需重启。
              </p>
            </div>
          </div>
          <span className="rounded-badge bg-surface-2 px-2 py-1 font-mono text-2xs text-muted-foreground">
            {settings?.upstream_timeouts_is_default === false ? "已覆盖环境默认" : "环境默认"}
          </span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Field>
            <FieldLabel>连接超时（秒）</FieldLabel>
            <Input
              aria-label="连接超时（秒）"
              inputMode="decimal"
              className="font-mono text-xs"
              value={upstreamTimeout.connect}
              onChange={(event) =>
                setUpstreamTimeout((prev) => ({ ...prev, connect: event.target.value }))
              }
              aria-invalid={Boolean(upstreamError)}
            />
            <p className="mt-1 text-2xs text-muted-foreground">建立 TCP/TLS 连接的最长时间。</p>
          </Field>
          <Field>
            <FieldLabel>首字节超时（秒）</FieldLabel>
            <Input
              aria-label="首字节超时（秒）"
              inputMode="decimal"
              className="font-mono text-xs"
              value={upstreamTimeout.firstByte}
              onChange={(event) =>
                setUpstreamTimeout((prev) => ({ ...prev, firstByte: event.target.value }))
              }
              aria-invalid={Boolean(upstreamError)}
            />
            <p className="mt-1 text-2xs text-muted-foreground">请求发出到上游开始返回的最长时间。</p>
          </Field>
          <Field>
            <FieldLabel>读空闲超时（秒）</FieldLabel>
            <Input
              aria-label="读空闲超时（秒）"
              inputMode="decimal"
              className="font-mono text-xs"
              value={upstreamTimeout.read}
              onChange={(event) =>
                setUpstreamTimeout((prev) => ({ ...prev, read: event.target.value }))
              }
              aria-invalid={Boolean(upstreamError)}
            />
            <p className="mt-1 text-2xs text-muted-foreground">
              流式相邻两块之间的最大静默，不是总时长上限。
            </p>
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            onClick={submitUpstreamTimeouts}
            disabled={upstreamMutation.isPending || !upstreamDirty}
          >
            {upstreamMutation.isPending ? (
              <Spinner size="md" label="" aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            保存超时
          </Button>
          {settings?.upstream_timeouts_is_default === false ? (
            <Button
              variant="outline"
              onClick={resetUpstreamTimeouts}
              disabled={upstreamMutation.isPending}
            >
              恢复环境默认
            </Button>
          ) : null}
        </div>
        {upstreamError ? (
          <FieldError className="mt-2" errors={[{ message: upstreamError }]} />
        ) : null}
        {upstreamSaved ? (
          <p className="mt-2 text-2xs text-success-text">
            超时已保存，对后续上游请求立即生效，无需重启。
          </p>
        ) : null}
      </Card>

      <Card className="mt-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
              <Route className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-title">路由策略与准入</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                决定同层成员怎么排序，以及节点同时接单的上限；保存后立即生效，无需重启。
              </p>
            </div>
          </div>
          <span className="rounded-badge bg-surface-2 px-2 py-1 font-mono text-2xs text-muted-foreground">
            当前：
            {ROUTE_STRATEGY_OPTIONS.find((option) => option.value === savedRouteStrategy)?.label ??
              "同层负载均衡"}
          </span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="route-strategy">同层路由策略</FieldLabel>
            <Select
              value={routeStrategy}
              onValueChange={(value) => setRouteStrategy(value as RouteStrategy)}
            >
              <SelectTrigger id="route-strategy" aria-label="同层路由策略">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROUTE_STRATEGY_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-1 text-2xs text-muted-foreground">
              只影响同层成员顺序；跨层仍由 priority 绝对控制。
            </p>
          </Field>
          <Field>
            <FieldLabel>统计窗口（分钟）</FieldLabel>
            <div className="flex gap-2">
              <Select value={routeWindowChoice} onValueChange={setRouteWindowChoice}>
                <SelectTrigger className="flex-1" aria-label="统计窗口（分钟）">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROUTE_WINDOW_PRESETS.map((minutes) => (
                    <SelectItem key={minutes} value={String(minutes)}>
                      {minutes} 分钟
                    </SelectItem>
                  ))}
                  <SelectItem value="custom">自定义</SelectItem>
                </SelectContent>
              </Select>
              {routeWindowChoice === "custom" ? (
                <Input
                  aria-label="自定义窗口（分钟）"
                  inputMode="numeric"
                  className="w-24 font-mono text-xs"
                  value={routeWindowCustom}
                  onChange={(event) => setRouteWindowCustom(event.target.value)}
                  aria-invalid={Boolean(routeError)}
                />
              ) : null}
            </div>
            <p className="mt-1 text-2xs text-muted-foreground">
              滑动回看；15–1440 分钟，默认 30。成功样本不足 30 次的成员按均等随机参与。
            </p>
          </Field>
          <Field>
            <FieldLabel htmlFor="gateway-inflight-cap">节点在飞上限</FieldLabel>
            <Input
              id="gateway-inflight-cap"
              aria-label="节点在飞上限"
              inputMode="numeric"
              className="font-mono text-xs"
              value={inflightCap}
              onChange={(event) => setInflightCap(event.target.value)}
              aria-invalid={Boolean(routeError)}
            />
            <p className="mt-1 text-2xs text-muted-foreground">
              流式与非流式 /v1 请求共享；0 = 关闭，默认 256，超限返回 429。
            </p>
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button onClick={submitRoutePolicy} disabled={routeMutation.isPending || !routeDirty}>
            {routeMutation.isPending ? (
              <Spinner size="md" label="" aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            保存路由策略
          </Button>
        </div>
        {routeError ? (
          <FieldError className="mt-2" errors={[{ message: routeError }]} />
        ) : null}
        {routeSaved ? (
          <p className="mt-2 text-2xs text-success-text">
            路由策略已保存，对后续请求立即生效，无需重启。
          </p>
        ) : null}
      </Card>

      <div className="mt-6 grid gap-4 lg:grid-cols-[1.35fr_1fr]">
        <Card className="overflow-hidden">
          <div className="border-b border-border px-5 py-4">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-soft text-primary">
                <FileText className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-title">日志正文记录策略</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  默认关闭；开启后仍会强制脱敏与截断。
                </p>
              </div>
            </div>
          </div>

          {settingsQuery.isLoading ? (
            <div className="flex min-h-60 items-center justify-center gap-2 text-2sm text-muted-foreground">
              <Spinner size="md" label="" aria-hidden="true" />
              正在加载设置…
            </div>
          ) : (
            <>
              {!settings ? (
                <div className="flex min-h-60 items-center justify-center px-6 text-center text-2sm text-muted-foreground">
                  暂时无法读取设置，请重试。
                </div>
              ) : (
              <RadioGroup
                value={draft}
                onValueChange={(value) => choose(value as BodyPolicy)}
                className="space-y-2.5 px-5 py-5"
              >
                {POLICY_OPTIONS.map((option) => (
                  <FieldLabel
                    key={option.value}
                    htmlFor={`body-policy-${option.value}`}
                    className={cn(
                      "flex w-full cursor-pointer items-start gap-3 rounded-control border px-3.5 py-3 transition-colors",
                      draft === option.value
                        ? "border-primary bg-primary-soft"
                        : "border-border bg-surface hover:bg-surface-2",
                    )}
                  >
                    <RadioGroupItem
                      id={`body-policy-${option.value}`}
                      value={option.value}
                      aria-label={option.title}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="block text-2sm font-medium text-title">
                        {option.title}
                      </span>
                      <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                        {option.description}
                      </span>
                    </span>
                  </FieldLabel>
                ))}
              </RadioGroup>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-4">
                <p className="text-xs text-muted-foreground">
                  {settings ? updatedLabel(settings, timezoneMode) : "设置暂不可用"}
                </p>
                <Button
                  onClick={() => mutation.mutate(draft)}
                  disabled={!dirty || mutation.isPending}
                >
                  {mutation.isPending ? (
                    <Spinner size="md" label="" aria-hidden="true" />
                  ) : (
                    <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  )}
                  保存策略
                </Button>
              </div>
            </>
          )}
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-success" strokeWidth={1.75} aria-hidden="true" />
            <h2 className="text-sm font-semibold text-title">不可关闭的安全约束</h2>
          </div>
          <p className="mt-3 rounded-control bg-success-soft px-3 py-2 text-xs leading-5 text-success-text">
            脱敏强制生效：Authorization、api-key、x-api-key、cookie、set-cookie 与 URL
            查询中的 key / api_key / token 参数一律写入 <code className="font-mono">***</code>。
          </p>
          <ul className="mt-4 space-y-3">
            {LIMITS.map((limit) => {
              const Icon = limit.icon;
              return (
                <li key={limit.title} className="flex items-start gap-2.5">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-muted-foreground">
                    <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-title">{limit.title}</p>
                    <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                      {limit.description}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card id="admin-account" className="scroll-mt-20 overflow-hidden">
          <div className="border-b border-border px-5 py-4">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-soft text-primary">
                <KeyRound className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-title">管理员账号</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">修改登录密码；至少 6 位。</p>
              </div>
            </div>
          </div>
          <form
            className="space-y-3 px-5 py-5"
            onSubmit={(event) => {
              event.preventDefault();
              submitPassword();
            }}
          >
            <Field>
              <FieldLabel htmlFor="current-password">当前密码</FieldLabel>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => {
                  setCurrentPassword(event.target.value);
                  setPasswordSaved(false);
                }}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="new-password">新密码</FieldLabel>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(event) => {
                    setNewPassword(event.target.value);
                    setPasswordSaved(false);
                  }}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="confirm-password">确认新密码</FieldLabel>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(event) => {
                    setConfirmPassword(event.target.value);
                    setPasswordSaved(false);
                  }}
                />
              </Field>
            </div>
            {passwordError ? (
              <Alert variant="destructive">{passwordError}</Alert>
            ) : null}
            {passwordSaved ? (
              <Alert variant="success">密码已更新，下次登录请使用新密码。</Alert>
            ) : null}
            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={
                  passwordMutation.isPending ||
                  !currentPassword ||
                  !newPassword ||
                  !confirmPassword
                }
              >
                {passwordMutation.isPending ? (
                  <Spinner size="md" label="" aria-hidden="true" />
                ) : (
                  <KeyRound className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                )}
                修改密码
              </Button>
            </div>
          </form>
        </Card>

        <Card className="p-5">
          <h2 className="text-sm font-semibold text-title">时间显示与主题</h2>
          <p className="mt-1 text-xs text-muted-foreground">与顶栏切换保持同步，并记住到本浏览器。</p>

          <div className="mt-4">
            <p className="text-xs font-medium text-title">时间显示默认值</p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(Object.keys(TIMEZONE_LABEL) as TimezoneMode[]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={timezoneMode === mode}
                  onClick={() => setTimezoneMode(mode)}
                  className={cn(
                    "flex h-10 items-center rounded-control border px-3 text-left text-xs transition-colors",
                    "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2",
                    timezoneMode === mode
                      ? "border-primary bg-primary-soft text-primary"
                      : "border-border text-body hover:bg-surface-2",
                  )}
                >
                  {TIMEZONE_LABEL[mode]}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-5">
            <p className="text-xs font-medium text-title">主题</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {THEME_OPTIONS.map((option) => {
                const Icon = option.icon;
                const active = themeMode === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setThemeMode(option.value)}
                    className={cn(
                      "rounded-control border px-3 py-2.5 text-left transition-colors",
                      "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2",
                      active
                        ? "border-primary bg-primary-soft text-primary"
                        : "border-border text-body hover:bg-surface-2",
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-xs font-medium">
                      <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                      {option.label}
                    </span>
                    <span className="mt-0.5 block text-2xs text-muted-foreground">
                      {option.description}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </Card>
      </div>

      {mutation.isError ? (
        <Alert variant="destructive" className="mt-4">
          {describeApiError(mutation.error)}
        </Alert>
      ) : null}
      {saved && !dirty ? (
        <Alert variant="success" className="mt-4">
          策略已保存，之后的请求将按新策略记录正文。
        </Alert>
      ) : null}
    </div>
  );
}
