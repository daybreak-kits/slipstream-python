import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Field, FieldLabel, FieldTitle } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  maxOutputTokensFor,
  paramHintText,
  type ChatParamInput,
  type ResponseFormatType,
  type ThinkingMode,
} from "@/lib/playground";
import { cn } from "@/lib/utils";

export interface ChatParamsPanelProps {
  systemPrompt: string;
  onSystemPromptChange: (value: string) => void;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  params: ChatParamInput;
  onParamsChange: (next: ChatParamInput) => void;
  streamMode: boolean;
  onStreamModeChange: (value: boolean) => void;
  /** 所选渠道模型登记的 max_output_tokens；0/缺省表示未登记，校验回退 32768。 */
  maxOutputTokens?: number;
  /** 渠道模型登记的思考等级，只作为 reasoning_effort 的默认提示。 */
  reasoningLevel?: string;
}

/**
 * 组内行网格：同质控件一行（lg 3 列 / xl 6 列，窄屏 1→2 列退化），行内等高等宽；
 * items-end 让开关这类没有标签行的控件与同行输入框底部对齐，
 * 个别 label 换行时输入框也不会上下错位。
 */
const GRID =
  "grid grid-cols-1 items-end gap-x-3 gap-y-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6";
/** textarea 类控件（系统提示词 / stop / 额外请求字段 JSON）整行独占，不与短控件同排。 */
const FULL = "col-span-full";
/** 字段内部 label 与控件的间距（field 默认 gap-3 太松，会白占行高）。 */
const FIELD = "min-w-0 gap-1";
/** 主标签：中文业务名 + 参数名等宽副注；取值与用途一律走 Tooltip，不再用虚线下划线冒充 title。 */
const LABEL =
  "w-fit cursor-help flex-wrap items-baseline gap-x-1.5 text-xs font-medium leading-4 text-body focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
/** 参数名副注：请求字段的等宽标识，弱于中文主标。 */
const SUBNAME = "font-mono text-2xs font-normal text-muted-foreground";

/** 思考下拉的 value → 完整展示文案（控件被截断时悬停 Tooltip 给出全文）。 */
const THINKING_OPTIONS: Array<{ value: ThinkingMode; label: string }> = [
  { value: "off", label: "不指定（沿用渠道默认）" },
  { value: "none", label: "reasoning_effort=none（关闭）" },
  { value: "low", label: "reasoning_effort=low" },
  { value: "medium", label: "reasoning_effort=medium" },
  { value: "high", label: "reasoning_effort=high" },
  { value: "max", label: "reasoning_effort=max" },
  { value: "thinking", label: "thinking.enabled（DeepSeek）" },
  { value: "enable_thinking", label: "enable_thinking（Qwen）" },
];

const RESPONSE_FORMAT_LABELS: Record<ResponseFormatType, string> = {
  text: "text（默认）",
  json_object: "json_object",
};

interface ParamLabelProps {
  id: string;
  /** 中文业务名：视觉与读屏的主标签。 */
  label: string;
  /** 请求参数名：等宽副注；没有对应参数的字段（会话级设置、JSON 兜底）省略。 */
  name?: string;
  hint: string;
  /** 只读值没有可绑定的输入控件，用 FieldTitle 呈现。 */
  title?: boolean;
}

/**
 * 参数标签 + 悬停提示：提示内容就是「取值 range · 用途 usage」。
 * aria-describedby 指向一个 sr-only 副本，读屏在未展开 tooltip 时也能取到说明；
 * 键盘 Tab 到标签时 Radix 会按 focus 打开提示，鼠标悬停同样触发。
 */
function ParamLabel({ id, label, name, hint, title = false }: ParamLabelProps) {
  const descriptionId = `${id}-hint`;
  const text = (
    <>
      <span>{label}</span>
      {name ? (
        <>
          {" "}
          <span className={SUBNAME}>{name}</span>
        </>
      ) : null}
    </>
  );
  const trigger = title ? (
    <FieldTitle tabIndex={0} aria-describedby={descriptionId} className={LABEL}>
      {text}
    </FieldTitle>
  ) : (
    <FieldLabel
      htmlFor={id}
      tabIndex={0}
      aria-describedby={descriptionId}
      className={LABEL}
    >
      {text}
    </FieldLabel>
  );

  // 说明副本必须包在一层普通容器里：Field 的 [&>*] 规则会把直接子元素的宽度拉满，
  // 直接放在 Field 下的 sr-only 会被撑成整段文字宽、把文档撑出横向滚动。
  return (
    <div className="relative min-w-0">
      <Tooltip>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent>{hint}</TooltipContent>
      </Tooltip>
      <span id={descriptionId} className="sr-only">
        {hint}
      </span>
    </div>
  );
}

interface NumberFieldProps {
  id: string;
  label: string;
  /** 请求参数名（等宽副注）；数值字段都有对应参数。 */
  name?: string;
  value: string | undefined;
  onChange: (value: string) => void;
  placeholder?: string;
  inputMode?: "decimal" | "numeric";
  hint: string;
}

/** 数值字段：中文业务名 + 等宽参数名副注 + h-9 输入框；说明悬停标签时出现。 */
function NumberField({
  id,
  label,
  name,
  value,
  onChange,
  placeholder,
  inputMode = "decimal",
  hint,
}: NumberFieldProps) {
  return (
    <Field className={FIELD}>
      <ParamLabel id={id} label={label} name={name} hint={hint} />
      <Input
        id={id}
        inputMode={inputMode}
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full text-2sm"
      />
    </Field>
  );
}

interface ToggleFieldProps {
  id: string;
  label: string;
  name?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  hint: string;
  /** 所在行的跨列安排（如系统组在 xl 让每份占 2 列凑满整行）。 */
  className?: string;
}

/** 开关字段：checkbox 与标签同一行（h-9），在 items-end 网格里与同行输入框底部对齐。 */
function ToggleField({
  id,
  label,
  name,
  checked,
  onChange,
  hint,
  className,
}: ToggleFieldProps) {
  return (
    <Field className={cn(FIELD, className)}>
      <div className="flex h-9 min-w-0 items-center gap-2">
        <Checkbox
          id={id}
          checked={checked}
          onCheckedChange={(value) => onChange(value === true)}
        />
        <ParamLabel id={id} label={label} name={name} hint={hint} />
      </div>
    </Field>
  );
}

interface ReadOnlyValueProps {
  id: string;
  label: string;
  value: string;
  hint: string;
  className?: string;
}

/**
 * 只读值：渠道登记的思考等级可能很长，单行截断 + 悬停 Tooltip 看全文，
 * 不让它把所在列撑宽或把页面撑出横向滚动；标签说明同样走 ParamLabel。
 */
function ReadOnlyValue({ id, label, value, hint, className }: ReadOnlyValueProps) {
  const text = value.trim() || "未配置";
  return (
    <Field className={cn(FIELD, className)}>
      <ParamLabel id={id} label={label} hint={hint} title />
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            id={id}
            tabIndex={0}
            aria-label={label}
            className="flex h-9 w-full min-w-0 items-center rounded-control border border-border-strong bg-surface-2 px-3 text-sm text-title"
          >
            <span className="min-w-0 truncate">{text}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent maxLines={0}>{text}</TooltipContent>
      </Tooltip>
    </Field>
  );
}

interface ParamGroupProps {
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}

/** 四个分组共用同一个折叠头：标题 + 同基线 caret，默认全展开。 */
function ParamGroup({ title, open, onOpenChange, children }: ParamGroupProps) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="space-y-1">
      <CollapsibleTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 gap-1 px-1 text-2xs text-muted-foreground"
          aria-label={title}
        >
          {title}
          <ChevronDown
            className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-180")}
            strokeWidth={1.75}
            aria-hidden="true"
          />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  );
}

/**
 * 参数面板：系统提示词与 API 密钥是会话级设置；
 * 采样 / 输出 / 高级三组只对下一次请求生效（发送后由页面复位）。
 * 布局按「同质控件一行」组织：textarea 类整行独占，短输入与 select 同行等高等宽，
 * 开关与同高控件为伴、底部对齐；说明文案悬停标签看（Tooltip），面板一屏放下全部参数。
 */
export function ChatParamsPanel({
  systemPrompt,
  onSystemPromptChange,
  apiKey,
  onApiKeyChange,
  params,
  onParamsChange,
  streamMode,
  onStreamModeChange,
  maxOutputTokens = 0,
  reasoningLevel = "",
}: ChatParamsPanelProps) {
  const [openGroups, setOpenGroups] = useState({
    system: true,
    sampling: true,
    output: true,
    advanced: true,
  });
  const limit = maxOutputTokensFor(maxOutputTokens);
  const registeredLimit = maxOutputTokens > 0;
  const thinkingValue = params.thinkingMode ?? "off";
  const thinkingLabel =
    THINKING_OPTIONS.find((option) => option.value === thinkingValue)?.label ??
    THINKING_OPTIONS[0].label;
  const responseFormat = params.responseFormat ?? "text";
  const responseFormatLabel = RESPONSE_FORMAT_LABELS[responseFormat];

  const setGroupOpen = (key: keyof typeof openGroups) => (open: boolean) =>
    setOpenGroups((previous) => ({ ...previous, [key]: open }));

  return (
    <TooltipProvider delayDuration={0}>
      {/* 组间 16px（space-y-4），组内网格 gap-y-3（12px），全部落在 4px 网格上。 */}
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-title">参数控制</h2>
          <Badge tone="secondary">只对下一次请求生效</Badge>
        </div>

        <ParamGroup
          title="系统"
          open={openGroups.system}
          onOpenChange={setGroupOpen("system")}
        >
          <div className={GRID}>
            <Field className={cn(FIELD, FULL)}>
              <ParamLabel
                id="playground-system-prompt"
                label="系统提示词"
                hint={paramHintText("systemPrompt")}
              />
              <Textarea
                id="playground-system-prompt"
                className="min-h-14 resize-y text-2sm"
                placeholder="可选：作为 system 消息放在上下文最前"
                value={systemPrompt}
                onChange={(event) => onSystemPromptChange(event.target.value)}
              />
            </Field>

            {/* 会话级行没有列入短输入清单：xl 每份占 2 列，行内三份凑满整行不留空洞。 */}
            <Field className={cn(FIELD, "xl:col-span-2")}>
              <ParamLabel
                id="playground-api-key"
                label="API 密钥"
                hint={paramHintText("apiKey")}
              />
              <Input
                id="playground-api-key"
                type="password"
                autoComplete="off"
                placeholder="留空使用当前登录会话"
                value={apiKey}
                onChange={(event) => onApiKeyChange(event.target.value)}
                className="h-9 w-full"
              />
            </Field>

            <ToggleField
              id="playground-stream"
              label="流式（SSE）"
              checked={streamMode}
              onChange={onStreamModeChange}
              hint={paramHintText("streamMode")}
              className="xl:col-span-2"
            />

            <ReadOnlyValue
              id="playground-model-reasoning"
              label="渠道思考等级（只读）"
              value={reasoningLevel}
              hint={paramHintText("reasoningLevel")}
              className="sm:col-span-2 lg:col-span-1 xl:col-span-2"
            />
          </div>
        </ParamGroup>

        <ParamGroup
          title="采样"
          open={openGroups.sampling}
          onOpenChange={setGroupOpen("sampling")}
        >
          <div className={GRID}>
            <NumberField
              id="playground-temperature"
              label="温度"
              name="temperature"
              placeholder="0.7"
              value={params.temperature}
              onChange={(value) => onParamsChange({ ...params, temperature: value })}
              hint={paramHintText("temperature")}
            />
            <NumberField
              id="playground-top-p"
              label="核采样"
              name="top_p"
              placeholder="默认"
              value={params.topP}
              onChange={(value) => onParamsChange({ ...params, topP: value })}
              hint={paramHintText("topP")}
            />
            <NumberField
              id="playground-top-k"
              label="候选词数"
              name="top_k"
              placeholder="默认"
              inputMode="numeric"
              value={params.topK}
              onChange={(value) => onParamsChange({ ...params, topK: value })}
              hint={paramHintText("topK")}
            />
            <NumberField
              id="playground-seed"
              label="随机种子"
              name="seed"
              placeholder="默认"
              inputMode="numeric"
              value={params.seed}
              onChange={(value) => onParamsChange({ ...params, seed: value })}
              hint={paramHintText("seed")}
            />
            <NumberField
              id="playground-frequency-penalty"
              label="频率惩罚"
              name="frequency_penalty"
              placeholder="默认"
              value={params.frequencyPenalty}
              onChange={(value) => onParamsChange({ ...params, frequencyPenalty: value })}
              hint={paramHintText("frequencyPenalty")}
            />
            <NumberField
              id="playground-presence-penalty"
              label="存在惩罚"
              name="presence_penalty"
              placeholder="默认"
              value={params.presencePenalty}
              onChange={(value) => onParamsChange({ ...params, presencePenalty: value })}
              hint={paramHintText("presencePenalty")}
            />
          </div>
        </ParamGroup>

        <ParamGroup
          title="输出"
          open={openGroups.output}
          onOpenChange={setGroupOpen("output")}
        >
          <div className={GRID}>
            <NumberField
              id="playground-max-tokens"
              label="最大输出"
              name="max_tokens"
              placeholder="不限"
              inputMode="numeric"
              value={params.maxTokens}
              onChange={(value) => onParamsChange({ ...params, maxTokens: value })}
              hint={paramHintText(
                "maxTokens",
                `1–${limit}${registeredLimit ? "" : "（未登记，回退 32768）"}`,
              )}
            />
            <NumberField
              id="playground-max-completion-tokens"
              label="补全上限"
              name="max_completion_tokens"
              placeholder="默认"
              inputMode="numeric"
              value={params.maxCompletionTokens}
              onChange={(value) => onParamsChange({ ...params, maxCompletionTokens: value })}
              hint={paramHintText("maxCompletionTokens", `1–${limit}`)}
            />
            <NumberField
              id="playground-n"
              label="回复份数"
              name="n"
              placeholder="默认 1"
              inputMode="numeric"
              value={params.n}
              onChange={(value) => onParamsChange({ ...params, n: value })}
              hint={paramHintText("n")}
            />
            <Field className={FIELD}>
              <ParamLabel
                id="playground-response-format"
                label="回复格式"
                name="response_format"
                hint={paramHintText("responseFormat")}
              />
              <Select
                value={responseFormat}
                onValueChange={(value) =>
                  onParamsChange({
                    ...params,
                    responseFormat: value as ResponseFormatType,
                  })
                }
              >
                <Tooltip>
                  <TooltipTrigger asChild>
                    <SelectTrigger
                      id="playground-response-format"
                      aria-label="回复格式 response_format"
                      className="h-9 w-full border-border-strong bg-surface"
                    >
                      <SelectValue />
                    </SelectTrigger>
                  </TooltipTrigger>
                  {/* 选中项被列宽截断时悬停看全文；title= 不算 tooltip（ui.md §2.3）。 */}
                  <TooltipContent>{responseFormatLabel}</TooltipContent>
                </Tooltip>
                <SelectContent>
                  <SelectItem value="text">text（默认）</SelectItem>
                  <SelectItem value="json_object">json_object</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field className={cn(FIELD, FULL)}>
              <ParamLabel
                id="playground-stop"
                label="停止序列"
                name="stop"
                hint={paramHintText("stop")}
              />
              <Textarea
                id="playground-stop"
                className="min-h-14 resize-y text-2sm"
                placeholder="每行一条，最多 4 条"
                value={params.stop ?? ""}
                onChange={(event) => onParamsChange({ ...params, stop: event.target.value })}
              />
            </Field>
          </div>
        </ParamGroup>

        <ParamGroup
          title="高级参数"
          open={openGroups.advanced}
          onOpenChange={setGroupOpen("advanced")}
        >
          <div className={GRID}>
            <ToggleField
              id="playground-logprobs"
              label="词概率"
              name="logprobs"
              checked={params.logprobs ?? false}
              onChange={(checked) =>
                onParamsChange({
                  ...params,
                  logprobs: checked,
                  topLogprobs: checked ? params.topLogprobs : "",
                })
              }
              hint={paramHintText("logprobs")}
            />
            <NumberField
              id="playground-top-logprobs"
              label="词概率条数"
              name="top_logprobs"
              placeholder="默认"
              inputMode="numeric"
              value={params.logprobs ? params.topLogprobs : ""}
              onChange={(value) => onParamsChange({ ...params, topLogprobs: value })}
              hint={paramHintText("topLogprobs")}
            />
            <Field className={FIELD}>
              <ParamLabel
                id="playground-user"
                label="用户标识"
                name="user"
                hint={paramHintText("user")}
              />
              <Input
                id="playground-user"
                placeholder="可选"
                value={params.user ?? ""}
                onChange={(event) => onParamsChange({ ...params, user: event.target.value })}
                className="h-9 w-full"
              />
            </Field>
            <Field className={FIELD}>
              <ParamLabel
                id="playground-thinking-mode"
                label="思考"
                hint={paramHintText("thinkingMode")}
              />
              <Select
                value={thinkingValue}
                onValueChange={(value) =>
                  onParamsChange({
                    ...params,
                    thinkingMode: value as ThinkingMode,
                  })
                }
              >
                <Tooltip>
                  <TooltipTrigger asChild>
                    <SelectTrigger
                      id="playground-thinking-mode"
                      aria-label="思考"
                      className="h-9 w-full border-border-strong bg-surface"
                    >
                      <SelectValue />
                    </SelectTrigger>
                  </TooltipTrigger>
                  <TooltipContent>{thinkingLabel}</TooltipContent>
                </Tooltip>
                <SelectContent>
                  {THINKING_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field className={cn(FIELD, FULL)}>
              <ParamLabel
                id="playground-extra-fields"
                label="额外请求字段（JSON）"
                hint={paramHintText("extraFields")}
              />
              <Textarea
                id="playground-extra-fields"
                className="min-h-14 resize-y font-mono text-2xs"
                placeholder='{"thinking": {"type": "enabled"}, "reasoning_split": true}'
                value={params.extraFields ?? ""}
                onChange={(event) =>
                  onParamsChange({ ...params, extraFields: event.target.value })
                }
              />
            </Field>
          </div>
        </ParamGroup>
      </div>
    </TooltipProvider>
  );
}
