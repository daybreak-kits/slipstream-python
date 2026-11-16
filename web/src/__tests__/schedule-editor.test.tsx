import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ScheduleEditorDialog } from "@/components/models/schedule-editor-dialog";
import type { ScheduleDocument } from "@/lib/channel-models";
import { isValidTimezone, matchTimezones, timezoneOptions } from "@/lib/timezone";

describe("timezone helpers", () => {
  it("accepts real IANA names and rejects typos", () => {
    expect(isValidTimezone("Asia/Shanghai")).toBe(true);
    expect(isValidTimezone("UTC")).toBe(true);
    // 常见手误：少一个字母、大小写不对，Intl 构造会抛 RangeError
    expect(isValidTimezone("Asia/Shanghia")).toBe(false);
    expect(isValidTimezone("asia/shanghai")).toBe(false);
    expect(isValidTimezone("")).toBe(false);
    expect(isValidTimezone("   ")).toBe(false);
  });

  it("offers a searchable option list that always includes common zones", () => {
    const options = timezoneOptions();
    expect(options).toContain("Asia/Shanghai");
    expect(options).toContain("Europe/London");
    // 候选里没有空串或重复项
    expect(options.every((option) => option.trim().length > 0)).toBe(true);
    expect(new Set(options).size).toBe(options.length);

    const tokyo = matchTimezones("tokyo");
    expect(tokyo.length).toBeGreaterThan(0);
    expect(tokyo.every((option) => option.toLowerCase().includes("tokyo"))).toBe(true);
    expect(matchTimezones("EUROPE").every((option) => option.startsWith("Europe/"))).toBe(true);
    expect(matchTimezones("")).toEqual(options);
  });
});

function renderDialog(props: {
  initialSchedule?: ScheduleDocument | null;
  busy?: boolean;
} = {}) {
  const onConfirm = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <ScheduleEditorDialog
      open
      onOpenChange={onOpenChange}
      subject="alpha/gpt-mini"
      initialSchedule={props.initialSchedule ?? null}
      busy={props.busy ?? false}
      onConfirm={onConfirm}
    />,
  );
  return { onConfirm, onOpenChange };
}

async function pickTimezone(
  user: ReturnType<typeof userEvent.setup>,
  query: string,
  optionName: RegExp,
) {
  await user.click(screen.getByRole("combobox", { name: "IANA 时区" }));
  await user.type(screen.getByPlaceholderText("搜索时区，如 Tokyo / Europe"), query);
  await user.click(await screen.findByRole("option", { name: optionName }));
}

describe("schedule editor timezone picker", () => {
  it("searches the option list and shows the picked zone on the trigger", async () => {
    const user = userEvent.setup();
    renderDialog();

    const trigger = screen.getByRole("combobox", { name: "IANA 时区" });
    expect(trigger).toHaveTextContent(Intl.DateTimeFormat().resolvedOptions().timeZone);

    await pickTimezone(user, "tokyo", /Asia\/Tokyo/);
    expect(trigger).toHaveTextContent("Asia/Tokyo");
    // 选择器里搜不到的值不会被写进去
    expect(screen.queryByRole("option", { name: /Asia\/Shanghai/ })).toBeNull();
  });

  it("submits a version 2 document with the chosen timezone", async () => {
    const user = userEvent.setup();
    const { onConfirm } = renderDialog();

    await pickTimezone(user, "tokyo", /Asia\/Tokyo/);
    await user.click(screen.getByRole("button", { name: "保存排期" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toEqual({
      version: 2,
      timezone: "Asia/Tokyo",
      rules: [{ kind: "daily", start: "22:00", end: "02:00" }],
    });
  });

  it("surfaces an invalid historical timezone and blocks submit until it is fixed", async () => {
    const user = userEvent.setup();
    const { onConfirm } = renderDialog({
      initialSchedule: {
        version: 2,
        timezone: "Asia/Shanghia",
        rules: [{ kind: "daily", start: "09:00", end: "18:00" }],
      },
    });

    // 脏数据原样展示 + 显式提醒，不静默换成别的时区
    expect(screen.getByText(/历史值「Asia\/Shanghia」不是合法 IANA 时区/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "IANA 时区" })).toHaveTextContent("Asia/Shanghia");

    const save = screen.getByRole("button", { name: "保存排期" });
    expect(save).toBeDisabled();

    await pickTimezone(user, "Shanghai", /Asia\/Shanghai/);
    expect(screen.queryByText(/历史值/)).toBeNull();
    expect(save).toBeEnabled();

    await user.click(save);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    // 改好的时区 + 原有规则与其它字段保持
    expect(onConfirm.mock.calls[0][0]).toEqual({
      version: 2,
      timezone: "Asia/Shanghai",
      rules: [{ kind: "daily", start: "09:00", end: "18:00" }],
    });
  });

  it("keeps submit disabled while a rule is incomplete", async () => {
    const user = userEvent.setup();
    renderDialog({
      initialSchedule: {
        version: 2,
        timezone: "Asia/Tokyo",
        // 缺星期：weekly 规则必须至少勾一天
        rules: [{ kind: "weekly", start: "09:00", end: "18:00" }],
      },
    });

    expect(screen.getByText("第 1 条规则至少选一个星期")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存排期" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "周一" }));
    expect(screen.getByRole("button", { name: "保存排期" })).toBeEnabled();
  });
});
