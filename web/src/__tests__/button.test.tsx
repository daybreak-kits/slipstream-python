import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button, buttonVariants } from "@/components/ui/button";

describe("button hover ramp", () => {
  it("gives ghost a step darker than table-row hover so row actions stay perceivable", () => {
    // 行 hover 用 surface-2；ghost 悬停必须落在更深的 --border，否则在已 hover 的
    // 表格行里完全隐形（2026-11-18 用户实测反馈）。
    expect(buttonVariants({ variant: "ghost" })).toContain("hover:bg-[var(--border)]");
    expect(buttonVariants({ variant: "ghost" })).toContain("active:bg-[var(--border-strong)]");
    expect(buttonVariants({ variant: "ghost" })).not.toContain("hover:bg-surface-2");
  });

  it("keeps the 150ms color transition on every button", () => {
    render(<Button>保存</Button>);
    const button = screen.getByRole("button", { name: "保存" });
    expect(button.className).toContain("transition-colors");
    expect(button.className).toContain("duration-150");
  });
});
