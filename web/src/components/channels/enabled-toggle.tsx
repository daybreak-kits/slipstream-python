import { Switch } from "@/components/ui/switch";

interface EnabledToggleProps {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (next: boolean) => void;
}

/**
 * 渠道/模型启停开关。ADR-081 决策 2/4：ui/ 之外禁手写基元语义，
 * 本组件自此为 ui/switch（Radix）的组合，不自带第二套 switch 语义实现。
 *
 * 外部契约保持不变（checked/disabled/label/onChange）；token 对照：
 * 轨道 checked=bg-primary、unchecked=bg-input（= --border-strong，与旧 bg-border-strong 同值）、
 * 焦点环 ring-ring（= --focus-ring）、禁用 disabled:opacity-50——均与手写版等值；
 * 旋钮 bg-background 取代旧 bg-white（audit A3：bg-white 违反 §2.2 主题禁令）。
 */
export function EnabledToggle({ checked, disabled, label, onChange }: EnabledToggleProps) {
  return (
    <Switch
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onCheckedChange={onChange}
    />
  );
}
