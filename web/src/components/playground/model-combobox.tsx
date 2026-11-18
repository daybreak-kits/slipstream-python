import { useMemo } from "react";

import { Combobox } from "@/components/ui/combobox";
import type { CatalogEntry } from "@/lib/playground";

export interface ModelComboboxProps {
  items: CatalogEntry[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  placeholder?: string;
  loading?: boolean;
  className?: string;
}

function optionLabel(item: CatalogEntry): string {
  if (item.kind === "combo") {
    return item.name + " · " + (item.origin === "auto" ? "自动组合" : "手动组合");
  }
  return item.channel_name ? item.name + " · " + item.channel_name : item.name;
}

/**
 * 目录选择的薄适配层：语义与浮层全部来自 ui/combobox（Popover + cmdk Command），
 * 这里只负责把 CatalogEntry 映射成选项。检索过滤由 Command 承担——
 * 选项的可检索文本 = 显示标签 + 上游模型名（映射过的模型按上游名也能搜到）。
 */
export function ModelCombobox({
  items,
  value,
  onChange,
  ariaLabel = "选择调用模型",
  placeholder = "选择调用模型",
  loading = false,
  className,
}: ModelComboboxProps) {
  const options = useMemo(
    () =>
      items.map((item) => ({
        value: item.name,
        label: optionLabel(item),
        // 映射过的直连模型把上游模型名放进描述行：既可见又可检索。
        description:
          item.kind === "direct" && item.upstream_model && item.upstream_model !== item.name
            ? item.upstream_model
            : undefined,
      })),
    [items],
  );

  return (
    <Combobox
      options={options}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      searchPlaceholder="输入关键词搜索"
      emptyText="没有匹配的模型"
      loading={loading}
      ariaLabel={ariaLabel}
      className={className}
    />
  );
}
