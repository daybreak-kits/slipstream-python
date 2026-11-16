import { Brain, ChevronDown, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Empty } from "@/components/ui/empty";
import { formatDuration } from "@/lib/duration";
import { formatCostOrUnconfigured, formatThroughput } from "@/lib/format";
import type { PlaygroundMessage, PlaygroundUsage } from "@/lib/playground";
import { cn } from "@/lib/utils";

export interface MessageListProps {
  messages: PlaygroundMessage[];
  onRetry: () => void;
}

function UsageRow({ usage }: { usage: PlaygroundUsage }) {
  return (
    <dl className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
      <div className="flex items-center gap-1">
        <dt>输入词元</dt>
        <dd className="tabular-nums text-title">{usage.promptTokens}</dd>
      </div>
      <div className="flex items-center gap-1">
        <dt>输出词元</dt>
        <dd className="tabular-nums text-title">{usage.completionTokens}</dd>
      </div>
      <div className="flex items-center gap-1">
        <dt>总计</dt>
        <dd className="tabular-nums text-title">{usage.totalTokens}</dd>
      </div>
      <div className="flex items-center gap-1">
        <dt>成本</dt>
        <dd className="tabular-nums text-title">
          {formatCostOrUnconfigured(usage.costUsd, usage.promptTokens + usage.completionTokens)}
        </dd>
      </div>
      <div className="flex items-center gap-1">
        <dt>TTFT</dt>
        <dd className="tabular-nums text-title">{formatDuration(usage.ttftMs)}</dd>
      </div>
      <div className="flex items-center gap-1">
        <dt>总耗时</dt>
        <dd className="tabular-nums text-title">{formatDuration(usage.elapsedMs)}</dd>
      </div>
      <div className="flex items-center gap-1">
        <dt>吞吐</dt>
        <dd className="tabular-nums text-title">{formatThroughput(usage.throughput)}</dd>
      </div>
    </dl>
  );
}

/**
 * 思考块：默认折叠，展开后内部滚动（长思考不会把气泡撑满页面）。
 * 上游没返回 reasoning_content 时也保留一个「无思考块」标记，
 * 免得用户以为页面把思考内容吞了。
 */
function ReasoningBlock({ reasoning, label }: { reasoning?: string; label?: string }) {
  const text = (reasoning ?? "").trim();
  // 上游真的没吐思考内容：显式给一个小字标记，避免用户以为 UI 吞了内容。
  if (!text) {
    return (
      <span className="inline-flex items-center gap-1 text-2xs text-muted-foreground">
        <Brain className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        无思考块
      </span>
    );
  }
  return (
    <Collapsible className="w-full">
      <CollapsibleTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 px-1.5 text-2xs text-muted-foreground"
          aria-label={label ? "思考过程 · " + label : "思考过程"}
        >
          <Brain className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          思考过程
          {label ? <Badge tone="secondary">{label}</Badge> : null}
          <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1.5 max-h-64 overflow-y-auto rounded-card border border-border bg-surface-2 p-2.5 text-2xs leading-5 whitespace-pre-wrap break-words text-muted-foreground">
          {text}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function MessageBubble({
  message,
  isLast,
  onRetry,
}: {
  message: PlaygroundMessage;
  isLast: boolean;
  onRetry: () => void;
}) {
  const isUser = message.role === "user";
  const hasMeta =
    Boolean(message.usage) ||
    Boolean(message.requestId) ||
    Boolean(message.streamed) ||
    Boolean(message.stopped) ||
    isLast;

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%] space-y-1.5", isUser ? "items-end" : "items-start")}>
        <div
          className={cn(
            "rounded-card px-3.5 py-2.5 text-2sm leading-6 whitespace-pre-wrap break-words",
            isUser ? "bg-primary-soft text-title" : "border border-border bg-surface text-body",
          )}
        >
          {message.content}
          {message.streaming ? (
            <span
              className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-primary align-middle"
              aria-hidden="true"
            />
          ) : null}
        </div>

        {!isUser ? (
          <ReasoningBlock reasoning={message.reasoning} label={message.thinkingLabel} />
        ) : null}

        {message.error ? (
          <Alert variant="destructive" className="text-2xs">
            {message.error}
          </Alert>
        ) : null}

        {!isUser && message.usage ? <UsageRow usage={message.usage} /> : null}

        {!isUser && hasMeta ? (
          <div className="flex flex-wrap items-center gap-2 text-2xs text-muted-foreground">
            {message.streamed ? <Badge tone="secondary">流式</Badge> : null}
            {message.stopped ? <Badge tone="warning">已停止</Badge> : null}
            {message.requestId ? <span className="font-mono">{message.requestId}</span> : null}
            {isLast ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 gap-1.5 text-2xs"
                aria-label="重试"
                onClick={onRetry}
              >
                <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                重试
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function MessageList({ messages, onRetry }: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const last = messages[messages.length - 1];
  const lastContent = last?.content ?? "";

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages.length, lastContent]);

  if (messages.length === 0) {
    return (
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex h-full w-full max-w-3xl items-center justify-center py-10">
          <Empty
            icon={Sparkles}
            title="还没有对话"
            description="选一个模型，在下面的输入框里发第一条消息。聊天记录只保存在这台设备的浏览器里，不写后端。"
          />
        </div>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 py-4">
        {messages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            isLast={index === messages.length - 1}
            onRetry={onRetry}
          />
        ))}
      </div>
    </div>
  );
}
