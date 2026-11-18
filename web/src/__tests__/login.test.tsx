import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/login";
import { AuthProvider } from "@/lib/auth";

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>dashboard-screen</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("login form", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows validation messages and does not call the API when submitted empty", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderLogin();
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("请输入用户名")).toBeInTheDocument();
    expect(await screen.findByText("请输入密码")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("login page craft (audit B7/C11/N2)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("keeps the blueprint grid plus the two ambient accent glows as the backdrop", () => {
    const { container } = renderLogin();

    expect(container.querySelector(".bg-blueprint")).not.toBeNull();
    expect(container.innerHTML).toContain("blur-[120px]");
    expect(container.innerHTML).toContain("blur-[100px]");
    expect(container.innerHTML).toContain("bg-[var(--login-accent)]");
    expect(container.innerHTML).toContain("bg-[var(--login-teal)]");
  });

  it("renders both headings on the text-h1 scale with canonical tracking", () => {
    const { container } = renderLogin();

    const brandH1 = screen.getByRole("heading", { level: 1, name: "登录管理台" });
    expect(brandH1.className).toContain("text-h1");
    expect(brandH1.className).toContain("tracking-[-0.01em]");
    expect(brandH1.className).not.toContain("text-2xl");

    const heroH1 = screen.getByRole("heading", { level: 1, name: /把多路上游/ });
    expect(heroH1.className).toContain("text-h1");
    expect(heroH1.className).toContain("tracking-[-0.01em]");
    expect(heroH1.className).toContain("text-balance");
    expect(heroH1.className).toContain("leading-[1.16]");
    expect(heroH1.className).not.toContain("text-4xl");
    expect(heroH1.className).not.toContain("tracking-[-0.03em]");

    expect(container.innerHTML).not.toContain("text-4xl");
    expect(container.innerHTML).not.toContain("tracking-[-0.03em]");
  });

  it("normalizes all all-caps small labels (SLIPSTREAM, Admin console, live) to the 0.08em tracking", () => {
    const { container } = renderLogin();

    const labels = screen.getAllByText("SLIPSTREAM");
    expect(labels).toHaveLength(2);
    for (const label of labels) {
      expect(label.className).toContain("tracking-[0.08em]");
    }
    expect(screen.getByText("Admin console").className).toContain("tracking-[0.08em]");

    expect(container.innerHTML).not.toContain("tracking-[0.18em]");
    expect(container.innerHTML).not.toContain("tracking-[0.16em]");
  });

  it("keeps the password placeholder terse and non-instructional", () => {
    renderLogin();

    const password = screen.getByLabelText("密码");
    expect(password).toHaveAttribute("placeholder", "••••••••");
    expect(screen.queryByPlaceholderText("请输入密码")).toBeNull();
  });

  it("focuses the username input on first paint with no entrance animation on the card", () => {
    const { container } = renderLogin();

    expect(screen.getByLabelText("用户名")).toHaveFocus();
    expect(container.querySelector(".animate-fade-up")).toBeNull();
  });
});
