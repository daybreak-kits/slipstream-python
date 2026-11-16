import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Controller, useForm, type FieldPath } from "react-hook-form";
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
  ScheduleRulesFieldset,
  ScheduleTimezonePicker,
} from "@/components/models/schedule-rules";
import {
  adoptModelDefaults,
  createChannelModel,
  DEFAULT_MODEL_PARAMETERS,
  INPUT_TYPE_OPTIONS,
  MODEL_CAPABILITY_OPTIONS,
  MODEL_TYPE_OPTIONS,
  OUTPUT_TYPE_OPTIONS,
  DEFAULT_SCHEDULE_TIMEZONE,
  describeSchedule,
  localScheduleTimezone,
  normalizeSchedule,
  updateChannelModel,
  validateScheduleRules,
  type ChannelModel,
  type ChannelModelInput,
  type InputType,
  type ModelCapability,
  type ModelParameters,
  type OutputType,
  type ScheduleDocument,
} from "@/lib/channel-models";
import { describeApiError } from "@/lib/channels";
import {
  describeFieldSource,
  describePrefillSource,
  fetchModelPrefill,
  prefillFormPatch,
  type ModelPrefill,
} from "@/lib/model-defaults";
import { isValidTimezone } from "@/lib/timezone";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const MODEL_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$/;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const FALLBACK_TIMEZONE = "Asia/Shanghai";

/** 能力位在表单里的字段路径（与 parameters_json 的键同名）。 */
const CAPABILITY_FIELDS = {
  tool_call: "parameters.tool_call",
  reasoning: "parameters.reasoning",
  attachment: "parameters.attachment",
  structured_output: "parameters.structured_output",
} as const;
const CAPABILITY_KEYS = Object.keys(CAPABILITY_FIELDS) as ModelCapability[];

/** 按路径取值（"parameters.max_input_tokens"）。 */
function readPath(source: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, key) => {
    if (acc === null || acc === undefined) return undefined;
    return (acc as Record<string, unknown>)[key];
  }, source);
}

/** 深比较（数组逐项比）：判断用户是否改过某个字段。 */
function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((item, index) => sameValue(item, right[index]));
  }
  return false;
}

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || FALLBACK_TIMEZONE;
  } catch {
    return FALLBACK_TIMEZONE;
  }
}

/** 表单初始排期：v2 原样，v1（segments）先折成 v2；没有排期时给空规则集（全天可用）。 */
function defaultScheduleDocument(model?: ChannelModel | null): ScheduleDocument {
  const normalized = normalizeSchedule(model?.schedule);
  if (normalized) return normalized;
  const local = localScheduleTimezone();
  return {
    version: 2,
    timezone: isValidTimezone(local) ? local : DEFAULT_SCHEDULE_TIMEZONE,
    rules: [],
  };
}

const parametersSchema = z.object({
  max_input_tokens: z
    .number({ invalid_type_error: "请输入最大输入词元" })
    .int("最大输入词元必须是整数")
    .min(0, "最大输入词元不能为负数")
    .max(100_000_000, "最大输入词元不能超过 1 亿"),
  max_output_tokens: z
    .number({ invalid_type_error: "请输入最大输出词元" })
    .int("最大输出词元必须是整数")
    .min(0, "最大输出词元不能为负数")
    .max(100_000_000, "最大输出词元不能超过 1 亿"),
  reasoning_level: z.string().trim().max(32, "推理等级最长 32 位"),
  input_types: z.array(z.enum(["text", "image", "audio", "video", "file"])).min(1, "至少选择一种输入类型"),
  output_types: z.array(z.enum(["text", "image", "audio", "embedding"])).min(1, "至少选择一种输出类型"),
  // 能力位在表单里是两态（task-153 拍板 A）：勾选=显式 true、取消=显式 false，
  // 保存时四个键全量写布尔；zod 仍允许 undefined，仅用于尚未初始化的表单值。
  tool_call: z.boolean().optional(),
  reasoning: z.boolean().optional(),
  attachment: z.boolean().optional(),
  structured_output: z.boolean().optional(),
});

const modelFormSchema = z.object({
  upstream_model: z
    .string()
    .trim()
    .min(1, "请输入上游模型名")
    .regex(MODEL_NAME_RE, "上游模型名需字母/数字开头，可含 . _ : - / #，最长 128 位"),
  call_model: z
    .string()
    .trim()
    .max(128, "调用模型最长 128 位")
    .refine((value) => !value || MODEL_NAME_RE.test(value), "调用模型格式不合法"),
  parameters: parametersSchema,
  input_price_per_1m: z
    .number({ invalid_type_error: "请输入输入单价" })
    .min(0, "单价不能为负数"),
  output_price_per_1m: z
    .number({ invalid_type_error: "请输入输出单价" })
    .min(0, "单价不能为负数"),
  cache_read_price_per_1m: z
    .number({ invalid_type_error: "请输入缓存读取单价" })
    .min(0, "单价不能为负数")
    .nullable(),
  cache_write_price_per_1m: z
    .number({ invalid_type_error: "请输入缓存写入单价" })
    .min(0, "单价不能为负数")
    .nullable(),
  enabled: z.boolean(),
  model_type: z.enum([
    "auto",
    "chat",
    "embedding",
    "rerank",
    "image_generation",
    "audio_tts",
    "audio_transcription",
    "video",
  ]),
});

type FormValues = z.infer<typeof modelFormSchema>;

function defaultsFor(model?: ChannelModel | null): FormValues {
  return {
    upstream_model: model?.upstream_model ?? "",
    call_model: model?.call_model ?? "",
    parameters: { ...DEFAULT_MODEL_PARAMETERS, ...(model?.parameters ?? {}) },
    input_price_per_1m: model?.input_price_per_1m ?? 0,
    output_price_per_1m: model?.output_price_per_1m ?? 0,
    cache_read_price_per_1m: model?.cache_read_price_per_1m ?? null,
    cache_write_price_per_1m: model?.cache_write_price_per_1m ?? null,
    enabled: model?.enabled ?? true,
    model_type:
      model && model.model_type_source === "manual" ? model.model_type : "auto",
  };
}

interface ChannelModelFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channelId: string;
  model?: ChannelModel | null;
  onSaved?: (model: ChannelModel) => void;
}

export function ChannelModelFormDialog({
  open,
  onOpenChange,
  channelId,
  model,
  onSaved,
}: ChannelModelFormDialogProps) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<ScheduleDocument>(() => defaultScheduleDocument(model));
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const localTimezone = browserTimezone();
  // 已经应用过哪一份预填（模型名 + 快照时间），避免每敲一个字就重跑。
  const appliedPrefillRef = useRef<string | null>(null);
  // 我们写进表单的预填值（按字段路径）：用来判断「用户后来有没有改过」。
  // react-hook-form 的 dirtyFields 会把「我们自己写进去的默认值」也算成 dirty，
  // 所以自己记基线：基线 = 我们写的值；没写过则用表单初始默认值。
  const prefillBaselineRef = useRef<Record<string, unknown>>({});
  const initialValuesRef = useRef<Record<string, unknown>>({});

  const {
    control,
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    getValues,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(modelFormSchema),
    defaultValues: defaultsFor(model),
  });

  useEffect(() => {
    if (!open) return;
    reset(defaultsFor(model));
    setSchedule(defaultScheduleDocument(model));
    setFormError(null);
    setScheduleError(null);
    setAdoptNotice(null);
    appliedPrefillRef.current = null;
    prefillBaselineRef.current = {};
    initialValuesRef.current = defaultsFor(model) as unknown as Record<string, unknown>;
  }, [open, model, reset]);

  // 新建模型：按 upstream_model 拉 models.dev 默认值（ADR-073 决策 7）。
  // 只读已拉取的快照、不触发刷新；端点不可用时静默跳过，绝不阻塞创建。
  const upstreamInput = watch("upstream_model");
  const debouncedUpstream = useDebouncedValue(upstreamInput.trim(), 400);
  const prefillQuery = useQuery({
    queryKey: ["channel-models", "prefill", debouncedUpstream],
    queryFn: () => fetchModelPrefill(debouncedUpstream),
    enabled: open && !model && debouncedUpstream.length > 0,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
  const prefill: ModelPrefill | null = prefillQuery.data ?? null;

  // 只填用户没动过的字段：自己记预填基线，人工改过的值天然优先。
  useEffect(() => {
    if (!open || model || !prefill?.matched || !prefill.defaults) return;
    const patch = prefillFormPatch(prefill.defaults);
    if (!patch) return;
    const appliedKey = prefill.upstream_model + "|" + (prefill.snapshot.pulled_at ?? "");
    if (appliedPrefillRef.current === appliedKey) return;
    appliedPrefillRef.current = appliedKey;

    // 基线 = 我们写进去的值；没写过的字段用表单初始默认值。
    // 当前值和基线不同 = 用户改过 → 不覆盖（人工值优先）。
    const baselineFor = (path: string): unknown =>
      path in prefillBaselineRef.current
        ? prefillBaselineRef.current[path]
        : readPath(initialValuesRef.current, path);
    const userEdited = (path: FieldPath<FormValues>): boolean => !sameValue(getValues(path), baselineFor(path));
    const apply = (path: FieldPath<FormValues>, value: unknown) => {
      setValue(path, value as never, { shouldDirty: false, shouldValidate: true });
      prefillBaselineRef.current[path] = value;
    };

    if (patch.prices.input !== null && !userEdited("input_price_per_1m")) {
      apply("input_price_per_1m", patch.prices.input);
    }
    if (patch.prices.output !== null && !userEdited("output_price_per_1m")) {
      apply("output_price_per_1m", patch.prices.output);
    }
    if (patch.prices.cacheRead !== null && !userEdited("cache_read_price_per_1m")) {
      apply("cache_read_price_per_1m", patch.prices.cacheRead);
    }
    if (patch.prices.cacheWrite !== null && !userEdited("cache_write_price_per_1m")) {
      apply("cache_write_price_per_1m", patch.prices.cacheWrite);
    }
    if (patch.maxInputTokens !== null && !userEdited("parameters.max_input_tokens")) {
      apply("parameters.max_input_tokens", patch.maxInputTokens);
    }
    if (patch.maxOutputTokens !== null && !userEdited("parameters.max_output_tokens")) {
      apply("parameters.max_output_tokens", patch.maxOutputTokens);
    }
    if (patch.reasoningLevel !== null && !userEdited("parameters.reasoning_level")) {
      apply("parameters.reasoning_level", patch.reasoningLevel);
    }
    if (patch.inputTypes && !userEdited("parameters.input_types")) {
      apply("parameters.input_types", patch.inputTypes);
    }
    if (patch.outputTypes && !userEdited("parameters.output_types")) {
      apply("parameters.output_types", patch.outputTypes);
    }
    for (const key of CAPABILITY_KEYS) {
      const value = patch.capabilities[key];
      if (typeof value !== "boolean" || userEdited(CAPABILITY_FIELDS[key])) continue;
      apply(CAPABILITY_FIELDS[key], value);
    }
    if (patch.modelType && !userEdited("model_type")) {
      // model_type 只是建议：用户没动过就不提交，保留后端的自动判定（决策 6）。
      setValue("model_type", patch.modelType, { shouldDirty: false });
      prefillBaselineRef.current["model_type"] = patch.modelType;
    }
  }, [open, model, prefill, setValue]);

  const saveMutation = useMutation({
    mutationFn: (input: ChannelModelInput) =>
      model ? updateChannelModel(model.id, input) : createChannelModel(channelId, input),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["channel-models", channelId] });
      await queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
      onSaved?.(saved);
    },
  });

  // 应用默认参数（ADR-073 决策 6 边界三）：force 重置为 models.dev 本次拉取值。
  // 列表失效后父组件用新 model 对象重开表单，init effect 自动回填最新值。
  const [adoptNotice, setAdoptNotice] = useState<string | null>(null);
  const adoptMutation = useMutation({
    mutationFn: () => adoptModelDefaults((model as ChannelModel).id, { force: true }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["channel-models", channelId] });
      setAdoptNotice(
        result.matched
          ? "已恢复 " + result.adopted.length + " 项为 models.dev 自动值（记得保存）"
          : "上游模型未命中 models.dev，无可恢复项",
      );
    },
    onError: (error) => setAdoptNotice(describeApiError(error)),
  });

  const submit = handleSubmit(async (values) => {
    setFormError(null);
    const timezone = schedule.timezone.trim();
    if (!timezone || !isValidTimezone(timezone)) {
      setScheduleError("请输入有效的 IANA 时区，例如 Asia/Shanghai");
      return;
    }
    const rulesError = validateScheduleRules(schedule.rules, { allowEmpty: true });
    if (rulesError) {
      setScheduleError(rulesError);
      return;
    }
    if (schedule.rules.some((rule) => !TIME_RE.test(rule.start) || !TIME_RE.test(rule.end))) {
      setScheduleError("计划时段需填写 HH:MM 格式的时间");
      return;
    }
    setScheduleError(null);
    const parameters: ModelParameters = {
      max_input_tokens: values.parameters.max_input_tokens,
      max_output_tokens: values.parameters.max_output_tokens,
      reasoning_level: values.parameters.reasoning_level.trim(),
      input_types: values.parameters.input_types,
      output_types: values.parameters.output_types,
    };
    // 能力位两态（task-153 拍板 A）：勾选=显式 true，取消=显式 false，
    // 全量提交、所见即所得；表单由预填/当前值初始化，未触碰的键无实际变化。
    for (const key of CAPABILITY_KEYS) {
      parameters[key] = values.parameters[key] === true;
    }
    const prefilledModelType = prefillBaselineRef.current["model_type"];
    const payload: ChannelModelInput = {
      upstream_model: values.upstream_model.trim(),
      call_model: values.call_model.trim() || values.upstream_model.trim(),
      parameters,
      input_price_per_1m: values.input_price_per_1m,
      output_price_per_1m: values.output_price_per_1m,
      cache_read_price_per_1m: values.cache_read_price_per_1m,
      cache_write_price_per_1m: values.cache_write_price_per_1m,
      enabled: values.enabled,
      // 预填的类型只是展示建议：用户没动过就不提交，保留后端的自动判定（决策 6）。
      model_type:
        typeof prefilledModelType === "string" && values.model_type === prefilledModelType
          ? undefined
          : values.model_type === "auto" && !model
            ? undefined
            : values.model_type,
      schedule: {
        version: 2,
        timezone,
        rules: schedule.rules.map((rule) => ({
          ...rule,
          ...(rule.days ? { days: [...rule.days].sort((left, right) => left - right) } : {}),
          ...(rule.days_of_month
            ? { days_of_month: [...rule.days_of_month].sort((left, right) => left - right) }
            : {}),
        })),
      },
    };
    try {
      await saveMutation.mutateAsync(payload);
      onOpenChange(false);
    } catch (error) {
      setFormError(describeApiError(error));
    }
  });

  const inputTypes = watch("parameters.input_types") ?? [];
  const outputTypes = watch("parameters.output_types") ?? [];
  const watchedParameters = watch("parameters");
  // 来源提示是一行只读文案（D2：只展示、不落库）；编辑已有模型时不显示。
  const prefillNotice =
    !model && debouncedUpstream.length > 0
      ? prefillQuery.isFetching
        ? "正在读取 models.dev 默认值…"
        : describePrefillSource(prefill)
      : "";
  // 逐字段来源提示（决策 7）：title/tooltip = provider(们) + 合并规则；编辑已有模型时不显示。
  const fieldSourceTitle = (path: string): string => {
    if (model) return "";
    const field = path.startsWith("parameters.") ? path.slice("parameters.".length) : path;
    return describeFieldSource(prefill, field);
  };
  const toggleInputType = (value: InputType) => {
    setValue(
      "parameters.input_types",
      inputTypes.includes(value)
        ? inputTypes.filter((item) => item !== value)
        : [...inputTypes, value],
      { shouldValidate: true },
    );
  };
  const toggleOutputType = (value: OutputType) => {
    setValue(
      "parameters.output_types",
      outputTypes.includes(value)
        ? outputTypes.filter((item) => item !== value)
        : [...outputTypes, value],
      { shouldValidate: true },
    );
  };

  const busy = isSubmitting || saveMutation.isPending || adoptMutation.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && busy) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-h-[calc(100dvh-5rem)] sm:max-w-3xl">
        <DialogHeader className="shrink-0 gap-0 space-y-0 border-b border-border px-5 py-4 pr-12 text-left">
          <DialogTitle className="text-sm">{model ? "编辑模型" : "添加模型"}</DialogTitle>
          <DialogDescription className="mt-0.5 text-xs">
            上游模型名是渠道真实模型；调用模型是客户端请求时使用的名字。
          </DialogDescription>
        </DialogHeader>
        <form
          id="channel-model-form"
          onSubmit={submit}
          className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4"
        >
          {formError ? <Alert variant="destructive">{formError}</Alert> : null}
          {adoptNotice ? <Alert>{adoptNotice}</Alert> : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field data-invalid={Boolean(errors.upstream_model)}>
              <FieldLabel htmlFor="model-upstream">上游模型名</FieldLabel>
              <Input
                id="model-upstream"
                className="font-mono"
                placeholder="gpt-4o-mini"
                autoComplete="off"
                aria-invalid={Boolean(errors.upstream_model)}
                aria-describedby={errors.upstream_model ? "model-upstream-error" : undefined}
                {...register("upstream_model")}
              />
              <FieldError id="model-upstream-error" className="text-2xs text-danger-text">
                {errors.upstream_model?.message}
              </FieldError>
            </Field>
            <Field data-invalid={Boolean(errors.call_model)}>
              <FieldLabel htmlFor="model-call">调用模型</FieldLabel>
              <Input
                id="model-call"
                className="font-mono"
                placeholder="留空则与上游模型相同"
                autoComplete="off"
                aria-invalid={Boolean(errors.call_model)}
                aria-describedby={errors.call_model ? "model-call-error" : undefined}
                {...register("call_model")}
              />
              <FieldError id="model-call-error" className="text-2xs text-danger-text">
                {errors.call_model?.message}
              </FieldError>
            </Field>
            <Field>
              <FieldLabel htmlFor="model-type">模型类型</FieldLabel>
              <Controller
                control={control}
                name="model_type"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="model-type" aria-label="模型类型" title={fieldSourceTitle("model_type")}>
                      <SelectValue placeholder="选择模型类型" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">自动（跟随上游）</SelectItem>
                      {MODEL_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <FieldDescription className="text-2xs">
                {model
                  ? `当前：${MODEL_TYPE_OPTIONS.find((option) => option.value === model.model_type)?.label ?? model.model_type}`
                  : "自动按上游模型名识别；识别不到时按聊天处理"}
                。只有聊天模型可调用 /chat/completions。
              </FieldDescription>
              <FieldError className="text-2xs text-danger-text">
                {errors.model_type?.message}
              </FieldError>
            </Field>
            <Field
              data-invalid={Boolean(errors.input_price_per_1m)}
              title={fieldSourceTitle("input_price_per_1m")}
            >
              <FieldLabel htmlFor="model-input-price">输入单价 / 1M</FieldLabel>
              <Input
                id="model-input-price"
                type="number"
                min={0}
                step="0.0001"
                className="tabular-nums"
                aria-invalid={Boolean(errors.input_price_per_1m)}
                aria-describedby={errors.input_price_per_1m ? "model-input-price-error" : undefined}
                {...register("input_price_per_1m", { valueAsNumber: true })}
              />
              <FieldError id="model-input-price-error" className="text-2xs text-danger-text">
                {errors.input_price_per_1m?.message}
              </FieldError>
            </Field>
            <Field
              data-invalid={Boolean(errors.output_price_per_1m)}
              title={fieldSourceTitle("output_price_per_1m")}
            >
              <FieldLabel htmlFor="model-output-price">输出单价 / 1M</FieldLabel>
              <Input
                id="model-output-price"
                type="number"
                min={0}
                step="0.0001"
                className="tabular-nums"
                aria-invalid={Boolean(errors.output_price_per_1m)}
                aria-describedby={errors.output_price_per_1m ? "model-output-price-error" : undefined}
                {...register("output_price_per_1m", { valueAsNumber: true })}
              />
              <FieldError id="model-output-price-error" className="text-2xs text-danger-text">
                {errors.output_price_per_1m?.message}
              </FieldError>
            </Field>
            <Field
              data-invalid={Boolean(errors.cache_read_price_per_1m)}
              title={fieldSourceTitle("cache_read_price_per_1m")}
            >
              <FieldLabel htmlFor="model-cache-read-price">缓存读取单价 / 1M</FieldLabel>
              <Input
                id="model-cache-read-price"
                type="number"
                min={0}
                step="0.0001"
                placeholder="留空 = 按输入价"
                className="tabular-nums"
                aria-invalid={Boolean(errors.cache_read_price_per_1m)}
                aria-describedby={errors.cache_read_price_per_1m ? "model-cache-read-price-error" : undefined}
                {...register("cache_read_price_per_1m", {
                  setValueAs: (value) => (value === "" || value === null ? null : Number(value)),
                })}
              />
              <FieldError id="model-cache-read-price-error" className="text-2xs text-danger-text">
                {errors.cache_read_price_per_1m?.message}
              </FieldError>
            </Field>
            <Field
              data-invalid={Boolean(errors.cache_write_price_per_1m)}
              title={fieldSourceTitle("cache_write_price_per_1m")}
            >
              <FieldLabel htmlFor="model-cache-write-price">缓存写入单价 / 1M</FieldLabel>
              <Input
                id="model-cache-write-price"
                type="number"
                min={0}
                step="0.0001"
                placeholder="留空 = 暂不计价"
                className="tabular-nums"
                aria-invalid={Boolean(errors.cache_write_price_per_1m)}
                aria-describedby={errors.cache_write_price_per_1m ? "model-cache-write-price-error" : undefined}
                {...register("cache_write_price_per_1m", {
                  setValueAs: (value) => (value === "" || value === null ? null : Number(value)),
                })}
              />
              <FieldError id="model-cache-write-price-error" className="text-2xs text-danger-text">
                {errors.cache_write_price_per_1m?.message}
              </FieldError>
            </Field>
          </div>

          <section
            aria-label="模型参数"
            className="rounded-control border border-border bg-surface-2 p-3"
          >
            <h3 className="text-2sm font-medium text-title">模型参数</h3>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              用于目录展示与兼容性判断；最大输入、最大输出以词元计，填 0 表示未声明。
            </p>
            {prefillNotice ? (
              <p role="note" className="mt-1 text-2xs text-muted-foreground">
                {prefillNotice}
              </p>
            ) : null}
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <Field
              data-invalid={Boolean(errors.parameters?.max_input_tokens)}
              title={fieldSourceTitle("parameters.max_input_tokens")}
            >
                <FieldLabel htmlFor="model-max-input">最大输入</FieldLabel>
                <Input
                  id="model-max-input"
                  type="number"
                  min={0}
                  max={100_000_000}
                  className="tabular-nums"
                  aria-invalid={Boolean(errors.parameters?.max_input_tokens)}
                  aria-describedby={errors.parameters?.max_input_tokens ? "model-max-input-error" : undefined}
                  {...register("parameters.max_input_tokens", { valueAsNumber: true })}
                />
                <FieldError id="model-max-input-error" className="text-2xs text-danger-text">
                  {errors.parameters?.max_input_tokens?.message}
                </FieldError>
              </Field>
              <Field
              data-invalid={Boolean(errors.parameters?.max_output_tokens)}
              title={fieldSourceTitle("parameters.max_output_tokens")}
            >
                <FieldLabel htmlFor="model-max-output">最大输出</FieldLabel>
                <Input
                  id="model-max-output"
                  type="number"
                  min={0}
                  max={100_000_000}
                  className="tabular-nums"
                  aria-invalid={Boolean(errors.parameters?.max_output_tokens)}
                  aria-describedby={errors.parameters?.max_output_tokens ? "model-max-output-error" : undefined}
                  {...register("parameters.max_output_tokens", { valueAsNumber: true })}
                />
                <FieldError id="model-max-output-error" className="text-2xs text-danger-text">
                  {errors.parameters?.max_output_tokens?.message}
                </FieldError>
              </Field>
              <Field
              data-invalid={Boolean(errors.parameters?.reasoning_level)}
              title={fieldSourceTitle("parameters.reasoning_level")}
            >
                <FieldLabel htmlFor="model-reasoning">推理等级</FieldLabel>
                <Input
                  id="model-reasoning"
                  placeholder="例如 high / medium"
                  aria-invalid={Boolean(errors.parameters?.reasoning_level)}
                  aria-describedby={errors.parameters?.reasoning_level ? "model-reasoning-error" : undefined}
                  {...register("parameters.reasoning_level")}
                />
                <FieldError id="model-reasoning-error" className="text-2xs text-danger-text">
                  {errors.parameters?.reasoning_level?.message}
                </FieldError>
              </Field>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Field>
                <p id="model-input-types-label" className="text-xs font-medium text-title">
                  输入类型
                </p>
                <div
                  role="group"
                  aria-labelledby="model-input-types-label"
                  className="flex flex-wrap gap-1.5"
                  title={fieldSourceTitle("parameters.input_types")}
                >
                  {INPUT_TYPE_OPTIONS.map((option) => (
                    <label
                      key={option.value}
                      className={`flex cursor-pointer items-center gap-1 rounded-badge border px-2 py-1 text-2xs transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus-ring ${
                        inputTypes.includes(option.value)
                          ? "border-primary bg-primary-soft text-primary"
                          : "border-border bg-surface text-body"
                      }`}
                    >
                      <Checkbox
                        className="sr-only"
                        aria-label={`输入类型 ${option.label}`}
                        checked={inputTypes.includes(option.value)}
                        onCheckedChange={() => toggleInputType(option.value)}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
                <FieldError className="text-2xs text-danger-text">
                  {errors.parameters?.input_types?.message}
                </FieldError>
              </Field>
              <Field>
                <p id="model-output-types-label" className="text-xs font-medium text-title">
                  输出类型
                </p>
                <div
                  role="group"
                  aria-labelledby="model-output-types-label"
                  className="flex flex-wrap gap-1.5"
                  title={fieldSourceTitle("parameters.output_types")}
                >
                  {OUTPUT_TYPE_OPTIONS.map((option) => (
                    <label
                      key={option.value}
                      className={`flex cursor-pointer items-center gap-1 rounded-badge border px-2 py-1 text-2xs transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus-ring ${
                        outputTypes.includes(option.value)
                          ? "border-primary bg-primary-soft text-primary"
                          : "border-border bg-surface text-body"
                      }`}
                    >
                      <Checkbox
                        className="sr-only"
                        aria-label={`输出类型 ${option.label}`}
                        checked={outputTypes.includes(option.value)}
                        onCheckedChange={() => toggleOutputType(option.value)}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
                <FieldError className="text-2xs text-danger-text">
                  {errors.parameters?.output_types?.message}
                </FieldError>
              </Field>
            </div>
            <fieldset className="mt-3">
              <legend className="text-xs font-medium text-title">能力位</legend>
              <p className="mt-0.5 text-2xs text-muted-foreground">
                models.dev 的默认值会先勾上；勾选=显式支持，取消=显式不支持，保存即生效。
              </p>
              <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
                {MODEL_CAPABILITY_OPTIONS.map((option) => (
                  <div key={option.value} title={fieldSourceTitle(CAPABILITY_FIELDS[option.value])}>
                    <label className="flex items-center gap-2 text-xs text-body">
                      <Checkbox
                        aria-label={"能力位 " + option.label}
                        checked={watchedParameters?.[option.value] === true}
                        onCheckedChange={(checked) =>
                          // 取消勾选 = 显式 false（task-153 拍板 A），所见即所得。
                          setValue(CAPABILITY_FIELDS[option.value], checked === true, {
                            shouldDirty: true,
                          })
                        }
                      />
                      <span>{option.label}</span>
                      <span className="text-2xs text-muted-foreground">{option.hint}</span>
                    </label>
                  </div>
                ))}
              </div>
            </fieldset>
          </section>

          <section
            aria-label="计划窗口"
            className="rounded-control border border-border bg-surface-2 p-3"
          >
            <div>
              <h3 className="text-2sm font-medium text-title">计划窗口</h3>
              <p className="mt-0.5 text-2xs text-muted-foreground">
                未设置规则时全天可用；多条规则是并集，命中任意一条就按冷却处理。
              </p>
            </div>

            <Field className="mt-3">
              <FieldLabel htmlFor="model-schedule-timezone">IANA 时区</FieldLabel>
              <ScheduleTimezonePicker
                id="model-schedule-timezone"
                value={schedule.timezone}
                invalid={Boolean(schedule.timezone.trim()) && !isValidTimezone(schedule.timezone.trim())}
                onChange={(next) => setSchedule((current) => ({ ...current, timezone: next }))}
              />
              <FieldDescription className="text-2xs">
                浏览器时区：{localTimezone}
                {schedule.timezone.trim() && schedule.timezone.trim() !== localTimezone
                  ? `；保存后按 ${schedule.timezone.trim()} 换算执行，界面上填写的时间均为该时区当地时间。`
                  : "；填写的时间按该时区当地时间执行。"}
              </FieldDescription>
            </Field>

            {schedule.rules.length === 0 ? (
              <p className="mt-3 rounded-badge border border-dashed border-border px-2.5 py-2 text-2xs text-muted-foreground">
                当前未设置规则，该模型全天可用。
              </p>
            ) : null}

            <div className="mt-3">
              <ScheduleRulesFieldset
                rules={schedule.rules}
                onChange={(rules) => setSchedule((current) => ({ ...current, rules }))}
                idPrefix="model-schedule-rule"
                keepAtLeastOne={false}
                addLabel="添加规则"
              />
            </div>
            <p className="mt-2 text-2xs text-body">
              当前规则：{schedule.rules.length ? describeSchedule(schedule.rules) : "全天可用"}
            </p>
            {scheduleError ? (
              <FieldError className="text-2xs text-danger-text">{scheduleError}</FieldError>
            ) : null}
          </section>

          <Field orientation="horizontal" className="w-fit">
            <Controller
              control={control}
              name="enabled"
              render={({ field }) => (
                <Checkbox
                  id="model-enabled"
                  checked={field.value}
                  onCheckedChange={(checked) => field.onChange(checked === true)}
                />
              )}
            />
            <FieldLabel htmlFor="model-enabled" className="text-body">
              启用该模型
            </FieldLabel>
          </Field>
        </form>
        <DialogFooter className="shrink-0 gap-2 border-t border-border bg-surface-2 px-5 py-3.5">
          {model ? (
            <Button
              variant="ghost"
              className="mr-auto"
              onClick={() => adoptMutation.mutate()}
              disabled={busy}
            >
              {adoptMutation.isPending ? (
                <Spinner aria-hidden="true" />
              ) : (
                <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              )}
              应用默认参数
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            取消
          </Button>
          <Button type="submit" form="channel-model-form" disabled={busy}>
            {busy ? (
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
