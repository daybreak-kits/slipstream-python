import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { GlobalSearch, isApplePlatform } from "@/components/layout/global-search";

/** jsdom 的 navigator.platform 定义在原型上；往实例上盖一层 own property，测完删掉还原。 */
function stubNavigatorPlatform(platform: string): void {
  Object.defineProperty(window.navigator, "platform", { value: platform, configurable: true });
}

afterEach(() => {
  delete (window.navigator as unknown as { platform?: string }).platform;
});

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

describe("global search shortcut hint (C12 平台键位)", () => {
  it("shows ⌘K on Apple platforms", () => {
    stubNavigatorPlatform("MacIntel");
    render(
      <MemoryRouter>
        <GlobalSearch />
      </MemoryRouter>,
    );
    expect(screen.getByText("⌘K")).toBeInTheDocument();
    expect(screen.queryByText("Ctrl K")).not.toBeInTheDocument();
  });

  it("shows Ctrl K on Windows and Linux", () => {
    stubNavigatorPlatform("Win32");
    render(
      <MemoryRouter>
        <GlobalSearch />
      </MemoryRouter>,
    );
    expect(screen.getByText("Ctrl K")).toBeInTheDocument();
    expect(screen.queryByText("⌘K")).not.toBeInTheDocument();
  });

  it("detects Apple platforms from navigator.platform, falling back to the user agent", () => {
    expect(isApplePlatform({ platform: "MacIntel" })).toBe(true);
    expect(isApplePlatform({ platform: "iPhone" })).toBe(true);
    expect(isApplePlatform({ platform: "Win32" })).toBe(false);
    expect(isApplePlatform({ platform: "Linux x86_64" })).toBe(false);
    // platform 缺省（隐私模式等）时回看 userAgent。
    expect(
      isApplePlatform({ platform: "", userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" }),
    ).toBe(true);
    expect(isApplePlatform({ platform: "", userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" })).toBe(false);
    // 无 navigator（SSR/极端测试环境）不抛错，按非 Mac 处理。
    expect(isApplePlatform(undefined)).toBe(false);
  });
});
