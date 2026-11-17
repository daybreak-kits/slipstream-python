import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Hash, Link2, Save, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

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
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AUTO_PROTOCOL_HINT,
  chatUrlFor,
  createChannel,
  describeApiError,
  isExactEndpoint,
  modelsUrlFor,
  PROTOCOLS,
  slugFromUrl,
  type Channel,
  type Protocol,
} from "@/lib/channels";
import { cn } from "@/lib/utils";

const SLUG_RE = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;

const channelFormSchema = z.object({
  name: z.string().trim().min(1, "请输入渠道名称").max(128, "渠道名称最长 128 位"),
  connection_url: z
    .string()
    .trim()
    .min(1, "请输入连接地址")
    .refine(
      (value) => /^https?:\/\/\S+$/i.test(value.replace(/#$/, "")),
      "连接地址必须以 http:// 或 https:// 开头",
    ),
  slug: z
    .string()
    .trim()
    .max(64, "渠道标识最长 64 位")
    .refine(
      (value) => !value || SLUG_RE.test(value),
      "渠道标识需字母开头，仅含字母、数字、下划线或中划线",
    ),
  api_key: z.string(),
  protocol: z.enum(["auto", "openai_chat", "anthropic_messages", "gemini"]),
  priority: z
    .number({ invalid_type_error: "请输入优先级" })
    .int("优先级必须是整数")
    .min(1, "优先级至少为 1")
    .max(1000, "优先级最多为 1000"),
  enabled: z.boolean(),
});

type FormValues = z.infer<typeof channelFormSchema>;

const DEFAULTS: FormValues = {
  name: "",
  connection_url: "",
  slug: "",
  api_key: "",
  protocol: "auto",
  priority: 1,
  enabled: true,
};

interface ChannelFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (channel: Channel) => void;
}

export function ChannelFormDialog({ open, onOpenChange, onCreated }: ChannelFormDialogProps) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);

  const {
    control,
    register,
    handleSubmit,
    reset,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(channelFormSchema),
    defaultValues: DEFAULTS,
  });

  useEffect(() => {
    if (!open) return;
    reset(DEFAULTS);
    setFormError(null);
  }, [open, reset]);

  const connectionUrl = watch("connection_url") ?? "";
  const slugValue = (watch("slug") ?? "").trim();
  const autoSlug = slugFromUrl(connectionUrl);
  const exact = isExactEndpoint(connectionUrl);
  const chatPreview = chatUrlFor(connectionUrl);
  const modelsPreview = modelsUrlFor(connectionUrl);

  const submit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      const apiKey = values.api_key.trim();
      const saved = await createChannel({
        slug: values.slug.trim() || undefined,
        name: values.name.trim(),
        connection_url: values.connection_url.trim(),
        ...(values.protocol === "auto" ? {} : { protocol: values.protocol as Protocol }),
        priority: values.priority,
        enabled: values.enabled,
        api_key: apiKey,
      });
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      await queryClient.invalidateQueries({ queryKey: ["channel", saved.id] });
      onCreated?.(saved);
      onOpenChange(false);
    } catch (error) {
      setFormError(describeApiError(error));
    }
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && isSubmitting) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-h-[calc(100dvh-5rem)] sm:max-w-2xl">
        <DialogHeader className="shrink-0 gap-0 space-y-0 border-b border-border px-5 py-4 pr-12 text-left">
          <DialogTitle className="text-sm">新建渠道</DialogTitle>
          <DialogDescription className="mt-0.5 text-xs">
            先保存连接配置，再在编辑页获取上游模型并勾选导入。保存不会触发任何上游测试。
          </DialogDescription>
        </DialogHeader>
        <form className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4" onSubmit={(event) => event.preventDefault()}>
          {formError ? <Alert variant="destructive">{formError}</Alert> : null}

          <Field data-invalid={Boolean(errors.name)}>
            <FieldLabel htmlFor="channel-name">渠道名称</FieldLabel>
            <Input
              id="channel-name"
              placeholder="例如：主力 OpenAI 渠道"
              aria-invalid={Boolean(errors.name)}
              aria-describedby={errors.name ? "channel-name-error" : undefined}
              {...register("name")}
            />
            <FieldError id="channel-name-error" className="text-2xs text-danger-text">
              {errors.name?.message}
            </FieldError>
          </Field>

          <Field data-invalid={Boolean(errors.connection_url)}>
            <div className="flex items-center justify-between gap-2">
              <FieldLabel htmlFor="channel-url">连接地址</FieldLabel>
              {exact ? (
                <span className="inline-flex items-center gap-1 rounded-badge bg-primary-soft px-2 py-0.5 text-2xs font-medium text-primary">
                  <Hash className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                  精确端点（不拼接路径）
                </span>
              ) : null}
            </div>
            <Input
              id="channel-url"
              className="font-mono text-xs"
              placeholder="https://api.example.com/v1"
              aria-invalid={Boolean(errors.connection_url)}
              aria-describedby={
                errors.connection_url ? "channel-url-desc channel-url-error" : "channel-url-desc"
              }
              {...register("connection_url")}
            />
            <FieldDescription id="channel-url-desc" className="text-2xs">
              末尾加 <code className="rounded-sm bg-surface-2 px-1 font-mono">#</code> 表示完整端点，系统不再追加路径。
            </FieldDescription>
            <FieldError id="channel-url-error" className="text-2xs text-danger-text">
              {errors.connection_url?.message}
            </FieldError>
          </Field>

          <div className="rounded-control border border-border bg-surface-2 px-3.5 py-3">
            <p className="text-2xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">最终请求地址</p>
            <div className="mt-1.5 flex items-start gap-2">
              <Link2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
              <div className="min-w-0 space-y-1">
                <p className="break-all font-mono text-2xs text-title">
                  {chatPreview || "填写连接地址后自动生成"}
                </p>
                <p className="break-all font-mono text-2xs text-muted-foreground">
                  /models：{modelsPreview || "—"}
                </p>
              </div>
            </div>
          </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field data-invalid={Boolean(errors.slug)}>
            <FieldLabel htmlFor="channel-slug">渠道标识</FieldLabel>
            <Input
              id="channel-slug"
              className={cn("font-mono text-xs", !slugValue && "placeholder:text-muted-foreground")}
              placeholder={autoSlug}
              aria-invalid={Boolean(errors.slug)}
              aria-describedby={errors.slug ? "channel-slug-desc channel-slug-error" : "channel-slug-desc"}
              {...register("slug")}
            />
            <FieldDescription id="channel-slug-desc" className="text-2xs">
              {slugValue ? "标识可修改，必须全局唯一。" : `留空自动生成：${autoSlug || "channel"}`}
            </FieldDescription>
            <FieldError id="channel-slug-error" className="text-2xs text-danger-text">
              {errors.slug?.message}
            </FieldError>
          </Field>

          <Field>
            <FieldLabel htmlFor="channel-protocol">协议</FieldLabel>
            <Controller
              control={control}
              name="protocol"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="channel-protocol" aria-label="协议">
                    <SelectValue placeholder="选择协议" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">自动探测（推荐）</SelectItem>
                    {PROTOCOLS.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
            <FieldDescription className="text-2xs">
              {watch("protocol") === "auto"
                ? AUTO_PROTOCOL_HINT
                : PROTOCOLS.find((item) => item.value === watch("protocol"))?.hint}
            </FieldDescription>
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="channel-key">渠道密钥</FieldLabel>
            <Input
              id="channel-key"
              type="password"
              autoComplete="new-password"
              className="font-mono text-xs"
              placeholder="sk-..."
              aria-describedby="channel-key-desc"
              {...register("api_key")}
            />
            <FieldDescription id="channel-key-desc" className="text-2xs">
              仅写入，不会以明文返回。
            </FieldDescription>
          </Field>

          <Field data-invalid={Boolean(errors.priority)}>
            <FieldLabel htmlFor="channel-priority">优先级</FieldLabel>
            <Input
              id="channel-priority"
              type="number"
              min={1}
              max={1000}
              aria-invalid={Boolean(errors.priority)}
              aria-describedby={
                errors.priority ? "channel-priority-error channel-priority-desc" : "channel-priority-desc"
              }
              {...register("priority", { valueAsNumber: true })}
            />
            <FieldError id="channel-priority-error" className="text-2xs text-danger-text">
              {errors.priority?.message}
            </FieldError>
            <FieldDescription id="channel-priority-desc" className="text-2xs">
              数值越大越先调用；自动组合按渠道优先级分层，同层等概率随机。
            </FieldDescription>
          </Field>
        </div>

          <Field orientation="horizontal" className="rounded-control border border-border px-3.5 py-3">
            <Controller
              control={control}
              name="enabled"
              render={({ field }) => (
                <Checkbox
                  id="channel-enabled"
                  checked={field.value}
                  onCheckedChange={(checked) => field.onChange(checked === true)}
                />
              )}
            />
            <FieldLabel htmlFor="channel-enabled" className="text-body">
              启用该渠道
            </FieldLabel>
            <ShieldCheck className="ml-auto h-4 w-4 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          </Field>
        </form>
        <DialogFooter className="shrink-0 gap-2 border-t border-border bg-surface-2 px-5 py-3.5">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            取消
          </Button>
          <Button className="min-w-28" onClick={() => void submit()} disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            )}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
