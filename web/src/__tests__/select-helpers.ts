import { screen } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { expect } from "vitest";

/** 可访问名匹配：字符串按 exact 匹配，正则原样透传。 */
export type SelectName = string | RegExp;

interface ChooseSelectOptions {
  /** SelectTrigger 的可访问名（aria-label 或关联 Label 文案）。 */
  triggerName: SelectName;
  /** 选项的可访问名，即 SelectItem 的显示文案。 */
  optionName: SelectName;
}

/** 当前选中的 shadcn/Radix Select 触发器（button[role=combobox]）。 */
export function getSelectTrigger(triggerName: SelectName): HTMLElement {
  return screen.getByRole("combobox", { name: triggerName });
}

/**
 * 打开 shadcn/Radix Select 并点选一个选项。
 * Radix 的 trigger 是 button[role=combobox]，没有原生 option/value，因此不能再用 selectOptions。
 */
export async function chooseSelect(user: UserEvent, { triggerName, optionName }: ChooseSelectOptions): Promise<void> {
  await user.click(getSelectTrigger(triggerName));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

/**
 * 断言 Select 当前选中项的显示文案。
 * 原生 select 的 toHaveValue 在 Radix trigger 上不可用（button 没有 DOM value），改看可见文案。
 */
export function expectSelectText(triggerName: SelectName, expectedText: string | RegExp): void {
  expect(getSelectTrigger(triggerName)).toHaveTextContent(expectedText);
}
