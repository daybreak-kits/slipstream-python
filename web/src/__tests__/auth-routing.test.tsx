import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { ProtectedRoute, PublicOnlyRoute } from "@/components/protected-route";
import { AuthProvider } from "@/lib/auth";

const admin = {
  id: "ADM00000000000000000000000",
  username: "admin",
  created_at: 1762838400000,
  updated_at: 1762838400000,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubMeRequest() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/auth/me")) return jsonResponse(admin);
    return jsonResponse({ code: "NOT_FOUND", message: "not found", request_id: "req_test" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("auth routing", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("redirects an anonymous visitor from the app shell to /login", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                <Route index element={<div>protected-dashboard</div>} />
              </Route>
            </Route>
            <Route path="/login" element={<div>login-screen</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("login-screen")).toBeInTheDocument();
    expect(screen.queryByText("protected-dashboard")).not.toBeInTheDocument();
  });

  it("restores a stored session and sends an authenticated visitor away from /login", async () => {
    localStorage.setItem("system.storage", JSON.stringify({ token: "stored-token" }));
    stubMeRequest();

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <Routes>
            <Route element={<PublicOnlyRoute />}>
              <Route path="/login" element={<div>login-screen</div>} />
            </Route>
            <Route path="/overview" element={<div>overview-screen</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("overview-screen")).toBeInTheDocument();
  });

  it("logs out from the admin menu and clears the stored token", async () => {
    localStorage.setItem("system.storage", JSON.stringify({ token: "stored-token" }));
    stubMeRequest();
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                <Route index element={<div>protected-dashboard</div>} />
              </Route>
            </Route>
            <Route path="/login" element={<div>login-screen</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("protected-dashboard")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /admin/i }));
    await user.click(screen.getByRole("menuitem", { name: "退出登录" }));

    expect(await screen.findByText("login-screen")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("system.storage") ?? "{}").token ?? null).toBeNull();
  });

  it("collapses the desktop navigation and restores the choice from storage", async () => {
    localStorage.setItem("system.storage", JSON.stringify({ token: "stored-token" }));
    stubMeRequest();
    const user = userEvent.setup();

    function renderShell() {
      return render(
        <MemoryRouter initialEntries={["/"]}>
          <AuthProvider>
            <Routes>
              <Route element={<ProtectedRoute />}>
                <Route element={<AppShell />}>
                  <Route index element={<div>protected-dashboard</div>} />
                </Route>
              </Route>
              <Route path="/login" element={<div>login-screen</div>} />
            </Routes>
          </AuthProvider>
        </MemoryRouter>,
      );
    }

    const first = renderShell();
    expect(await screen.findByText("protected-dashboard")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "收起导航栏" }));

    expect(screen.getByRole("button", { name: "展开导航栏" })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("system.storage") ?? "{}").navCollapsed).toBe(true);
    const nav = screen.getByRole("navigation", { name: "主导航" });
    expect(nav.querySelector("a[title='全局概览']")).not.toBeNull();

    first.unmount();
    renderShell();
    expect(
      await screen.findByRole("button", { name: "展开导航栏" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开导航栏" }));
    expect(JSON.parse(localStorage.getItem("system.storage") ?? "{}").navCollapsed).toBe(false);
  });
});
