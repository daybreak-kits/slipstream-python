import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Sidebar } from "@/components/layout/sidebar";
import { LoginPage } from "@/pages/login";
import { AuthProvider } from "@/lib/auth";

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar env="local" />
    </MemoryRouter>,
  );
}

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

describe("product brand", () => {
  it("renders the Slipstream wordmark and high-contrast logo in the sidebar", () => {
    const { container } = renderSidebar();

    expect(screen.getByText("驭流")).toBeInTheDocument();
    expect(screen.getByText("Slipstream")).toBeInTheDocument();
    expect(container.querySelector('img[src="/logo-v10-contrast.svg"]')).not.toBeNull();
  });

  it("shows the Chinese and English names on the login screen", () => {
    renderLogin();

    expect(screen.getAllByText("驭流").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SLIPSTREAM").length).toBeGreaterThan(0);
    expect(screen.getByText(/驭流 v0\.1\.0/)).toBeInTheDocument();
    expect(screen.queryByText(/LLMAPI/)).not.toBeInTheDocument();
  });
});
