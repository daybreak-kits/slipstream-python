import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { ThemeMenu } from "@/components/layout/theme-menu";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const loginSchema = z.object({
  username: z.string().trim().min(1, "请输入用户名"),
  password: z.string().min(1, "请输入密码"),
});

type LoginValues = z.infer<typeof loginSchema>;

const traceRows = [
  {
    model: "model-b",
    upstream: "channel-a/model-a",
    priority: "P1",
    status: "429",
    tone: "text-[var(--login-indigo)]",
  },
  {
    model: "model-b",
    upstream: "channel-b/model-a",
    priority: "P2",
    status: "200",
    tone: "text-white/35",
  },
  {
    model: "model-b",
    upstream: "channel-c/model-b",
    priority: "P3",
    status: "冷却",
    tone: "text-white/25",
  },
];

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: "", password: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      await login(values.username, values.password);
      navigate("/overview", { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        setFormError(error.message);
      } else {
        setFormError("无法连接服务器，请确认 API 服务已启动后重试。");
      }
    }
  });

  return (
    <div className="grid min-h-screen bg-bg lg:grid-cols-[1.02fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-[var(--login-ink)] text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute inset-0 bg-blueprint opacity-70" aria-hidden="true" />
        <div
          className="absolute -left-32 top-1/3 h-130 w-130 rounded-full bg-[var(--login-accent)] blur-[120px]"
          aria-hidden="true"
        />
        <div
          className="absolute -right-24 bottom-0 h-90 w-90 rounded-full bg-[var(--login-teal)] blur-[100px]"
          aria-hidden="true"
        />

        <div className="relative z-10 flex items-center gap-3 px-12 pt-11">
          <img src="/logo-v10-contrast.svg" alt="" aria-hidden="true" className="h-14 w-14 shrink-0" />
          <div className="leading-tight">
            <p className="text-base font-semibold">驭流</p>
            <p className="text-2xs tracking-[0.08em] text-white/45">SLIPSTREAM</p>
          </div>
        </div>

        <div className="relative z-10 max-w-130 px-12">
          <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-2xs text-white/60">
            <ShieldCheck className="h-3.5 w-3.5 text-[var(--login-teal-bright)]" strokeWidth={1.75} aria-hidden="true" />
            管理控制台 · 仅限管理员
          </p>
          <h1 className="text-balance text-h1 font-semibold leading-[1.16] tracking-[-0.01em] text-white">
            把多路上游，
            <br />
            收束成一个端点。
          </h1>
          <p className="mt-5 max-w-105 text-sm leading-7 text-white/55">
            统一 OpenAI 兼容入口，按优先级与冷却状态自动切换渠道。密钥、模型映射、调用成本，都收敛到同一处。
          </p>

          <div className="mt-10 overflow-hidden rounded-xl border border-white/10 bg-[var(--login-ink-2)] shadow-2xl shadow-black/40 backdrop-blur-sm">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5">
              <span className="font-mono text-2xs text-white/45">POST /v1/chat/completions</span>
              <span className="flex items-center gap-1.5 text-2xs uppercase tracking-[0.08em] text-[var(--login-green)]">
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--login-green)]" />
                live
              </span>
            </div>
            <div className="space-y-1 px-2 py-2">
              {traceRows.map((row, index) => (
                <div
                  key={row.upstream}
                  className="flex items-center gap-3 rounded-lg px-2 py-2 font-mono text-2xs transition-colors hover:bg-white/[0.04]"
                >
                  <span className="w-3 text-white/25">{index + 1}</span>
                  <span className={cn("w-20 shrink-0", row.tone)}>{row.model}</span>
                  <ArrowRight className="h-3 w-3 shrink-0 text-white/25" strokeWidth={1.75} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate text-white/60">{row.upstream}</span>
                  <span className="rounded-sm border border-white/10 px-1.5 py-0.5 text-2xs text-white/35">
                    {row.priority}
                  </span>
                  <span
                    className={cn(
                      "rounded-sm border px-1.5 py-0.5 text-2xs",
                      row.status === "200"
                        ? "border-[color-mix(in_srgb,var(--login-green)_30%,transparent)] text-[var(--login-green)]"
                        : row.status === "429"
                          ? "border-[color-mix(in_srgb,var(--login-amber)_30%,transparent)] text-[var(--login-amber)]"
                          : "border-white/10 text-white/30",
                    )}
                  >
                    {row.status}
                  </span>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between border-t border-white/10 px-4 py-2 font-mono text-2xs text-white/30">
              <span>3 次尝试 · 自动转移</span>
              <span>1.24s · 812 tok</span>
            </div>
          </div>
        </div>

        <div className="relative z-10 px-12 pb-10 text-2xs text-white/30">
          驭流 v0.1.0 · 本地优先 · 正文默认不记录
        </div>
      </aside>

      <main className="relative flex min-h-screen items-center justify-center px-5 py-12 sm:px-8">
        <div className="absolute right-5 top-5 z-20">
          <ThemeMenu compact />
        </div>
        <div className="w-full max-w-98">
          <div className="mb-9 flex items-center gap-3 lg:hidden">
            <img src="/logo-v10-contrast.svg" alt="" aria-hidden="true" className="h-12 w-12 shrink-0" />
            <div className="leading-tight">
              <p className="text-sm font-semibold text-title">驭流</p>
              <p className="text-2xs tracking-[0.08em] text-muted-foreground">SLIPSTREAM</p>
            </div>
          </div>

          <p className="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-primary">Admin console</p>
          <h1 className="text-h1 font-semibold tracking-[-0.01em] text-title">登录管理台</h1>
          <p className="mt-2 text-2sm text-muted-foreground">使用管理员账号登录，管理渠道、组合与系统设置。</p>

          <form className="mt-8 space-y-5" onSubmit={onSubmit} noValidate>
            <Field>
              <FieldLabel htmlFor="username">用户名</FieldLabel>
              <Input
                id="username"
                autoComplete="username"
                autoFocus
                aria-invalid={errors.username ? "true" : undefined}
                aria-describedby={errors.username ? "login-errors" : undefined}
                {...register("username")}
              />
            </Field>

            <Field>
              <FieldLabel htmlFor="password">密码</FieldLabel>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  className="pr-11"
                  aria-invalid={errors.password ? "true" : undefined}
                  aria-describedby={errors.password ? "login-errors" : undefined}
                  {...register("password")}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setShowPassword((visible) => !visible)}
                  className="absolute right-1.5 top-1/2 h-8 w-8 -translate-y-1/2"
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  ) : (
                    <Eye className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  )}
                </Button>
              </div>
            </Field>

            {/* 唯一错误槽（2026-11-18 用户裁定）：只在密码框与登录按钮之间占位，其他位置不占。
                高度按最坏一行预留，报错出现/消失全程按钮纹丝不动。 */}
            <div className="min-h-5" aria-live="polite">
              {formError ? (
                <FieldError errors={[{ message: formError }]} />
              ) : errors.username || errors.password ? (
                <FieldError
                  id="login-errors"
                  errors={[
                    {
                      message: [errors.username?.message, errors.password?.message]
                        .filter(Boolean)
                        .join("、"),
                    },
                  ]}
                />
              ) : null}
            </div>

            <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Spinner size="md" label="" aria-hidden="true" />
                  正在登录…
                </>
              ) : (
                <>
                  登录
                  <ArrowRight className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                </>
              )}
            </Button>
          </form>

          <p className="mt-8 text-xs leading-6 text-muted-foreground">
            首次启动会按 <code className="font-mono text-2xs text-title">ADMIN_USERNAME</code> /{" "}
            <code className="font-mono text-2xs text-title">ADMIN_PASSWORD</code> 创建默认管理员。登录后请尽快修改密码。
          </p>
        </div>
      </main>
    </div>
  );
}
