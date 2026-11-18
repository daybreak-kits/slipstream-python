import { Layers, Wand2 } from "lucide-react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { priorityLayerText, type ComboOrigin, type PriorityLayerText } from "@/lib/catalog";

/**
 * 组合标记：直连行不渲染；组合行在名称旁标出来源。
 *
 * 只画图标（手动组合 = Layers，自动组合 = Wand2），两种类型靠形状区分而不是颜色——
 * 颜色统一 --muted-foreground，色弱用户与明暗两套主题都不会翻车。
 * 豁免说明（ui.md §2.4 AI 装饰图标禁令）：Wand2 是「自动组合」的类型区分功能图标，
 * 标记真实来源状态而非装饰，保留。
 * 类型文字全部进 tooltip，触发元素保留 aria-label 与 title 兜底。
 * tooltip 里成员逐行列出（P 前缀保留），不再一行挤「P50 a / P40 b」也不省略；
 * 成员多时列表内部滚动（max-h-40 + overflow-y-auto），tooltip 本身不走 3 行截断。
 * 名字下方再给一段「参数并集」（ADR-073 决策 8）：上限取 max、能力位与模态取并集、
 * 价格只给成员区间——组合不合成单一值。参数拿不到时不渲染这一段。
 */
export function ComboMarker({
  origin,
  layers,
  params,
}: {
  origin: ComboOrigin;
  layers: readonly PriorityLayerText[];
  /** 参数并集的逐行文本；空数组或缺省表示不展示。 */
  params?: readonly string[];
}) {
  const label = origin === "auto" ? "自动组合" : "手动组合";
  const detail = layers.length > 0 ? priorityLayerText(layers) : "优先级分层暂不可用";
  const note = origin === "auto" ? "成员与 priority 实时派生" : "成员与 priority 由组合配置决定";
  const Icon = origin === "auto" ? Wand2 : Layers;
  // 每个成员独占一行：层按 priority 从大到小，层内保持组合配置顺序。
  const memberLines = layers
    .slice()
    .sort((left, right) => right.priority - left.priority)
    .flatMap((layer) => layer.labels.map((name) => "P" + layer.priority + " " + name));

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={label + "，优先级 " + detail}
            title={label}
            className="inline-flex shrink-0 cursor-help items-center text-muted-foreground"
          >
            <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </span>
        </TooltipTrigger>
        <TooltipContent title={label} className="max-w-80">
          <span className="block text-[var(--tooltip-fg-muted)]">{note}</span>
          {memberLines.length > 0 ? (
            <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto">
              {memberLines.map((line) => (
                <li key={line} className="whitespace-nowrap">
                  {line}
                </li>
              ))}
            </ul>
          ) : (
            <span className="mt-0.5 block">{detail}</span>
          )}
          {params && params.length > 0 ? (
            <div className="mt-1.5 border-t border-current/20 pt-1.5">
              <span className="block text-[var(--tooltip-fg-muted)]">参数并集（按成员）</span>
              <ul className="mt-0.5 space-y-0.5">
                {params.map((line) => (
                  <li key={line} className="whitespace-nowrap">
                    {line}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
