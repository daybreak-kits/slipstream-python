import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { GlobalSearch } from "@/components/layout/global-search";

describe("global search", () => {
  it("searches pages and actions without request ids", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <GlobalSearch />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "全局搜索" }));
    expect(screen.getByRole("dialog", { name: "全局搜索" })).toBeInTheDocument();
    const input = screen.getByLabelText("搜索页面和操作");
    await user.type(input, "日志");
    expect(screen.getByText("使用日志")).toBeInTheDocument();
    expect(screen.queryByText("模型目录")).not.toBeInTheDocument();
  });

  it("shows an empty state for a miss and closes on escape", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <GlobalSearch />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "全局搜索" }));
    await user.type(screen.getByLabelText("搜索页面和操作"), "req_123");
    expect(screen.getByText("没有匹配的页面或操作")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
