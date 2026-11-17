import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EnabledToggle } from "@/components/channels/enabled-toggle";

// 契约：checked / disabled / label / onChange 外部不变，内部实现从手写 switch
// 迁到 ui/switch（Radix）时，本文件锁住可观察行为。
describe("EnabledToggle", () => {
  it("renders as a switch whose aria-checked follows the controlled value", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <EnabledToggle checked={false} label="启用 Alpha" onChange={onChange} />,
    );

    expect(screen.getByRole("switch", { name: "启用 Alpha" })).toHaveAttribute(
      "aria-checked",
      "false",
    );

    rerender(<EnabledToggle checked={true} label="启用 Alpha" onChange={onChange} />);
    expect(screen.getByRole("switch", { name: "启用 Alpha" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("reports clicks as the next state through onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <EnabledToggle checked={false} label="启用 Alpha" onChange={onChange} />,
    );

    await user.click(screen.getByRole("switch", { name: "启用 Alpha" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(true);

    rerender(<EnabledToggle checked={true} label="启用 Alpha" onChange={onChange} />);
    await user.click(screen.getByRole("switch", { name: "启用 Alpha" }));
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it("ignores clicks while disabled", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <EnabledToggle checked={false} disabled label="封禁 Alpha" onChange={onChange} />,
    );

    const toggle = screen.getByRole("switch", { name: "封禁 Alpha" });
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    expect(onChange).not.toHaveBeenCalled();
  });
});
