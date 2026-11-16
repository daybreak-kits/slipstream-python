import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ChatComposer } from "@/components/playground/chat-composer";
import { MessageList } from "@/components/playground/message-list";
import {
  clearPlaygroundHistory,
  loadPlaygroundHistory,
  savePlaygroundHistory,
} from "@/components/playground/history-store";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  buildUsage,
  buildUserContent,
  chatParamsFromInput,
  describeChatError,
  describeThinking,
  isAbortError,
  listCatalogEntries,
  listRecentLogs,
  mergeLogUsage,
  sendChat,
  sendChatStream,
  toRequestMessages,
  validateChatParams,
  type CatalogEntry,
  type ChatParamInput,
  type PlaygroundAttachment,
  type PlaygroundMessage,
} from "@/lib/playground";

const DEFAULT_PARAMS: ChatParamInput = { temperature: "0.7" };

function newMessageId(): string {
  return "pg-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

/**
 * 模型下拉与详情的路由说明：ADR-067 之后条目只有直连与组合，组合再按 origin
 * 区分手动 / 自动；组合的 channel_name 不再承载来源文案。
 */
function modelRouteLabel(model: CatalogEntry): string {
  if (model.kind === "combo") {
    const label = model.origin === "auto" ? "自动组合" : "手动组合";
    return label + " · 按优先级分层路由，同层等概率随机";
  }
  return model.channel_name ? "直连 · " + model.channel_name : "直连";
}

export function PlaygroundPage() {
  const [messages, setMessages] = useState<PlaygroundMessage[]>(() => loadPlaygroundHistory());
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PlaygroundAttachment[]>([]);
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [streamMode, setStreamMode] = useState(true);
  const [params, setParams] = useState<ChatParamInput>(DEFAULT_PARAMS);
  const [sending, setSending] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const catalogQuery = useQuery({
    queryKey: ["catalog-entries"],
    queryFn: () => listCatalogEntries(),
  });
  const models = catalogQuery.data?.items ?? [];
  const selected = models.find((item) => item.name === model) ?? null;

  useEffect(() => {
    if (!model && models.length > 0) {
      setModel(models[0].name);
    }
  }, [model, models]);

  // 聊天记录只落浏览器本地；流式过程中不写盘，避免每个 token 都写一次。
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (last?.streaming) return;
    savePlaygroundHistory(messages);
  }, [messages]);

  const validation = validateChatParams(params, {
    maxOutputTokens: selected?.parameters?.max_output_tokens ?? 0,
  });
  const canSend =
    !sending &&
    Boolean(model) &&
    validation.valid &&
    (input.trim().length > 0 || attachments.length > 0);

  const refreshLog = async (requestId: string, assistantId: string) => {
    if (!requestId) return;
    try {
      const recent = await listRecentLogs(20);
      const entry = recent.items.find((item) => item.request_id === requestId) ?? null;
      setMessages((prev) =>
        prev.map((item) =>
          item.id === assistantId
            ? { ...item, usage: mergeLogUsage(item.usage, entry) }
            : item,
        ),
      );
    } catch {
      // 管理端日志不可用时，浏览器侧已经拿到的用量照常展示。
    }
  };

  const runRequest = async (
    userContent: string,
    fileAttachments: PlaygroundAttachment[],
    base: PlaygroundMessage[],
  ) => {
    const userMessage: PlaygroundMessage = {
      id: newMessageId(),
      role: "user",
      content: userContent,
      createdAt: Date.now(),
      attachments: fileAttachments,
    };
    const assistantId = newMessageId();
    const assistant: PlaygroundMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      createdAt: Date.now(),
      model,
      streaming: true,
    };
    const history = [...base, userMessage];
    setMessages([...history, assistant]);
    setSending(true);

    const requestMessages = toRequestMessages(history, systemPrompt);
    const requestParams = chatParamsFromInput(params);

    try {
      if (streamMode) {
        const controller = new AbortController();
        controllerRef.current = controller;
        const stats = await sendChatStream(apiKey, model, requestMessages, {
          signal: controller.signal,
          onDelta: (text) => {
            setMessages((prev) =>
              prev.map((item) =>
                item.id === assistantId ? { ...item, content: text } : item,
              ),
            );
          },
          onReasoning: (text) => {
            setMessages((prev) =>
              prev.map((item) =>
                item.id === assistantId ? { ...item, reasoning: text } : item,
              ),
            );
          },
          params: requestParams,
        });
        const marker = describeThinking(requestParams, stats.reasoning, stats.carrier);
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  content: stats.content,
                  reasoning: stats.reasoning,
                  thinkingLabel: marker?.label,
                  streaming: false,
                  streamed: true,
                  model: stats.model,
                  requestId: stats.requestId,
                  error: stats.content.trim()
                    ? item.error
                    : "上游没有返回内容：确认接口按 SSE 返回后重试。",
                  usage: buildUsage({
                    promptTokens: stats.usage?.prompt_tokens,
                    completionTokens: stats.usage?.completion_tokens,
                    totalTokens: stats.usage?.total_tokens,
                    ttftMs: stats.ttftMs,
                    elapsedMs: stats.elapsedMs,
                  }),
                }
              : item,
          ),
        );
        await refreshLog(stats.requestId, assistantId);
      } else {
        const result = await sendChat(apiKey, model, requestMessages, requestParams);
        const content = result.content;
        const marker = describeThinking(requestParams, result.reasoning, result.carrier);
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  content,
                  reasoning: result.reasoning,
                  thinkingLabel: marker?.label,
                  streaming: false,
                  model: result.data.model ?? model,
                  requestId: result.requestId,
                  error: content.trim() ? item.error : "上游没有返回内容。",
                  usage: buildUsage({
                    promptTokens: result.data.usage?.prompt_tokens,
                    completionTokens: result.data.usage?.completion_tokens,
                    totalTokens: result.data.usage?.total_tokens,
                    elapsedMs: result.elapsedMs,
                  }),
                }
              : item,
          ),
        );
        await refreshLog(result.requestId, assistantId);
      }
    } catch (reason) {
      if (isAbortError(reason)) {
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId ? { ...item, streaming: false, stopped: true } : item,
          ),
        );
      } else {
        const failure = describeChatError(reason);
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId ? { ...item, streaming: false, error: failure } : item,
          ),
        );
      }
    } finally {
      setSending(false);
      controllerRef.current = null;
    }
  };

  const handleFilesPicked = async (files: FileList | null) => {
    if (!files || !files.length) return;
    const picked: PlaygroundAttachment[] = [];
    for (const file of Array.from(files)) {
      let text = "";
      try {
        text = await file.text();
      } catch {
        // 读不出文本的附件仍然保留文件名。
        text = "";
      }
      picked.push({ name: file.name, size: file.size, text });
    }
    setAttachments((prev) => [...prev, ...picked]);
  };

  const handleSubmit = () => {
    if (sending || !model || !validation.valid) return;
    const text = input.trim();
    const files = attachments;
    if (!text && !files.length) return;
    setInput("");
    setAttachments([]);
    // 采样参数只对下一次请求生效：发完复位。
    setParams(DEFAULT_PARAMS);
    void runRequest(buildUserContent(text, files), files, messages);
  };

  const handleRetry = () => {
    const lastUserIndex = messages.map((item) => item.role).lastIndexOf("user");
    if (lastUserIndex < 0) return;
    const lastUser = messages[lastUserIndex];
    const base = messages.slice(0, lastUserIndex);
    setMessages(base);
    void runRequest(lastUser.content, lastUser.attachments ?? [], base);
  };

  const handleStop = () => {
    controllerRef.current?.abort();
  };

  const handleClear = () => {
    setMessages([]);
    setInput("");
    setAttachments([]);
    clearPlaygroundHistory();
    setClearOpen(false);
  };

  const noModels =
    !catalogQuery.isLoading && !catalogQuery.isError && models.length === 0;

  return (
    <div className="flex h-[calc(100dvh-8rem)] min-h-0 flex-col gap-3">
      <header className="flex shrink-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h1 className="text-h1 font-semibold text-title">模型试验</h1>
          <p className="mt-1 text-2sm text-muted-foreground">
            用聊天的方式试模型：聊天记录只保存在这台设备的浏览器里，不写后端。
          </p>
          {selected ? (
            <p className="mt-1 text-2xs text-muted-foreground">{modelRouteLabel(selected)}</p>
          ) : null}
        </div>
        <Badge tone="outline">{models.length} 个可用模型</Badge>
      </header>

      {noModels ? (
        <Alert>
          <p>
            还没有可用模型：先到
            <Link className="underline" to="/channels">
              渠道管理
            </Link>
            添加渠道与模型。
          </p>
        </Alert>
      ) : null}

      <MessageList messages={messages} onRetry={handleRetry} />

      <div className="shrink-0">
        <ChatComposer
          models={models}
          model={model}
          onModelChange={setModel}
          modelsLoading={catalogQuery.isLoading}
          reasoningLevel={selected?.parameters?.reasoning_level ?? ""}
          maxOutputTokens={selected?.parameters?.max_output_tokens ?? 0}
          input={input}
          onInputChange={setInput}
          attachments={attachments}
          onFilesPicked={(files) => void handleFilesPicked(files)}
          onRemoveAttachment={(name) =>
            setAttachments((prev) => prev.filter((item) => item.name !== name))
          }
          sending={sending}
          streamMode={streamMode}
          onStreamModeChange={setStreamMode}
          params={params}
          onParamsChange={setParams}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          apiKey={apiKey}
          onApiKeyChange={setApiKey}
          paramsMessage={validation.valid ? undefined : validation.message}
          canSend={canSend}
          onSubmit={handleSubmit}
          onStop={handleStop}
          onClear={() => setClearOpen(true)}
        />
      </div>

      <ConfirmDialog
        open={clearOpen}
        onOpenChange={setClearOpen}
        title="清除聊天记录？"
        description="只清掉这台设备浏览器里保存的聊天记录，不影响后端日志。"
        confirmLabel="清除"
        destructive
        onConfirm={handleClear}
      />
    </div>
  );
}
