import {
  Brain,
  ChevronDown,
  CircleStop,
  Paperclip,
  Send,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";

import { ChatParamsPanel } from "@/components/playground/chat-params-panel";
import { ModelCombobox } from "@/components/playground/model-combobox";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { summarizeChatParams } from "@/lib/playground";
import type { CatalogEntry, ChatParamInput, PlaygroundAttachment } from "@/lib/playground";
import { cn } from "@/lib/utils";

export interface ChatComposerProps {
  models: CatalogEntry[];
  model: string;
  onModelChange: (value: string) => void;
  modelsLoading?: boolean;
  /** 渠道模型上配置的思考等级，只读展示。 */
  reasoningLevel: string;
  /** 所选模型登记的 max_output_tokens（0=未登记），用于输出上限校验与提示。 */
  maxOutputTokens?: number;
  input: string;
  onInputChange: (value: string) => void;
  attachments: PlaygroundAttachment[];
  onFilesPicked: (files: FileList | null) => void;
  onRemoveAttachment: (name: string) => void;
  sending: boolean;
  streamMode: boolean;
  onStreamModeChange: (value: boolean) => void;
  params: ChatParamInput;
  onParamsChange: (next: ChatParamInput) => void;
  systemPrompt: string;
  onSystemPromptChange: (value: string) => void;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  paramsMessage?: string;
  canSend: boolean;
  onSubmit: () => void;
  onStop: () => void;
  onClear: () => void;
}

/**
 * 底部输入区：左侧是附件 / 参数控制 / 清除记录，
 * 右侧是模型 combobox、只读的思考等级与发送（流式时换成停止）。
 */
export function ChatComposer({
  models,
  model,
  onModelChange,
  modelsLoading = false,
  reasoningLevel,
  maxOutputTokens = 0,
  input,
  onInputChange,
  attachments,
  onFilesPicked,
  onRemoveAttachment,
  sending,
  streamMode,
  onStreamModeChange,
  params,
  onParamsChange,
  systemPrompt,
  onSystemPromptChange,
  apiKey,
  onApiKeyChange,
  paramsMessage,
  canSend,
  onSubmit,
  onStop,
  onClear,
}: ChatComposerProps) {
  const [paramsOpen, setParamsOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 输入框至少三行、随内容长高；超过上限后在框内滚动，不顶掉消息区。
  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = Math.min(node.scrollHeight, 224) + "px";
  }, [input]);

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    onFilesPicked(files);
    // 允许连续选同一个文件。
    event.target.value = "";
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    onSubmit();
  };

  return (
    <div className="space-y-2">
      {paramsOpen ? null : (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-card border border-border bg-surface-2 px-3 py-1.5 text-2xs text-muted-foreground">
          <span className="min-w-0 truncate">
            {summarizeChatParams(params)} · {streamMode ? "流式" : "非流式"}
          </span>
          <span className="shrink-0">只对下一次请求生效</span>
        </div>
      )}

      <Collapsible open={paramsOpen} onOpenChange={setParamsOpen} className="space-y-2">
        <CollapsibleContent>
          <div className="rounded-card border border-border bg-surface p-4 shadow-card">
            <ChatParamsPanel
              systemPrompt={systemPrompt}
              onSystemPromptChange={onSystemPromptChange}
              apiKey={apiKey}
              onApiKeyChange={onApiKeyChange}
              params={params}
              onParamsChange={onParamsChange}
              streamMode={streamMode}
              onStreamModeChange={onStreamModeChange}
              maxOutputTokens={maxOutputTokens}
              reasoningLevel={reasoningLevel}
            />
          </div>
        </CollapsibleContent>

        <div className="rounded-card border border-border bg-surface p-2 shadow-card">
          {attachments.length > 0 ? (
            <ul className="mb-1.5 flex flex-wrap gap-1.5 px-1">
              {attachments.map((item) => (
                <li
                  key={item.name}
                  className="inline-flex items-center gap-1.5 rounded-badge bg-surface-2 py-1 pl-2 pr-1 text-2xs text-body"
                >
                  <Paperclip
                    className="h-3 w-3 shrink-0 text-muted-foreground"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                  <span className="max-w-40 truncate">{item.name}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    aria-label={"移除附件 " + item.name}
                    onClick={() => onRemoveAttachment(item.name)}
                  >
                    <X className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}

          <Textarea
            ref={textareaRef}
            id="playground-message"
            aria-label="消息内容"
            rows={3}
            className="max-h-56 min-h-21 w-full resize-none overflow-y-auto border-0 bg-transparent px-2 py-1.5 text-2sm leading-6 text-title shadow-none focus-visible:border-0 focus-visible:ring-0"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={handleKeyDown}
          />

          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="sr-only"
                aria-label="选择附件"
                onChange={handleFiles}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-9 w-9"
                aria-label="添加附件"
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              </Button>

              <CollapsibleTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-9 gap-1.5 text-xs"
                  aria-label="参数控制"
                >
                  <SlidersHorizontal className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  参数控制
                  <ChevronDown
                    className={cn(
                      "h-3.5 w-3.5 transition-transform",
                      paramsOpen && "rotate-180",
                    )}
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                </Button>
              </CollapsibleTrigger>

              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-9 gap-1.5 text-xs"
                aria-label="清除记录"
                onClick={onClear}
              >
                <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                清除记录
              </Button>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              <ModelCombobox
                items={models}
                value={model}
                onChange={onModelChange}
                loading={modelsLoading}
                className="w-44 sm:w-56"
              />
              <span className="inline-flex h-9 items-center gap-1 rounded-badge border border-border bg-surface-2 px-2 text-xs text-muted-foreground">
                <Brain className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                思考等级 {reasoningLevel || "未配置"}
              </span>
              {sending && streamMode ? (
                <Button
                  type="button"
                  variant="outline"
                  size="md"
                  className="w-24 justify-center gap-1.5"
                  onClick={onStop}
                >
                  <CircleStop className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  停止
                </Button>
              ) : (
                <Button
                  type="button"
                  size="md"
                  className="w-24 justify-center gap-1.5"
                  onClick={onSubmit}
                  disabled={!canSend}
                >
                  {sending ? (
                    <Spinner size="sm" aria-hidden="true" />
                  ) : (
                    <Send className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  )}
                  {sending ? "发送中" : "发送"}
                </Button>
              )}
            </div>
          </div>

          {paramsMessage ? (
            <p className="mt-1 px-1 text-2xs text-danger-text">{paramsMessage}</p>
          ) : null}
        </div>
      </Collapsible>
    </div>
  );
}
