import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProtectedRoute } from "@/components/protected-route";

// 只测 FullPageLoader（audit B5）：加载态由 useAuth.status 驱动，直接钉住为 loading，
// 不真的走 /api/auth/me（那条链路归 auth-routing.test.tsx）。
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ status: "loading", admin: null }),
}));

describe("protected route full page loader", () => {
  it("renders the official logo and a spinner while the session restores", () => {
    const { container } = render(
      <MemoryRouter>
        <ProtectedRoute />
      </MemoryRouter>,
    );

    // 正式产品 logo（与侧栏/登录页同源），不再是裸字母 "L"。
    expect(container.querySelector('img[src="/logo-v10-contrast.svg"]')).not.toBeNull();
    expect(screen.queryByText("L")).not.toBeInTheDocument();

    // ui/spinner 就位，且不再有无限 ping 光圈。
    expect(container.querySelector('[data-slot="spinner"]')).not.toBeNull();
    expect(container.querySelector(".animate-ping")).toBeNull();

    // 文案与 busy 语义保留。
    expect(screen.getByText("正在恢复登录状态…")).toBeInTheDocument();
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });
});
