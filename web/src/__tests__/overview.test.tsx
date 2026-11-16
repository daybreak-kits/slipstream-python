import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { effectiveGatewayBaseUrl, type SystemSettings } from "@/lib/settings";
import { OverviewPage } from "@/pages/overview";

const NOW = 1762838400000;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeSettings(overrides: Partial<SystemSettings> = {}): SystemSettings {
  return {
    body_policy: "off",
    body_truncate_bytes: 16384,
    body_retention_days: 7,
    summary_max_bytes: 65536,
    updated_at: null,
    updated_by: "",
    log_retention_days: 30,
    redaction_forced: true,
    gateway_base_url: "",
    gateway_updated_at: null,
    gateway_updated_by: "",
    upstream_connect_timeout_s: 10,
    upstream_first_byte_timeout_s: 30,
    upstream_read_timeout_s: 120,
    upstream_timeouts_updated_at: null,
    upstream_timeouts_updated_by: "",
    upstream_timeouts_is_default: true,
    ...overrides,
  };
}

function renderPage() {
  toast.dismiss();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Toaster />
      <MemoryRouter initialEntries={["/overview"]}>
        <OverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("overview page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("falls back to the browser origin and saves a configured gateway", async () => {
    let current = makeSettings();
    const patchBodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/settings" && init?.method === "PATCH") {
          const body = JSON.parse(String(init.body));
          patchBodies.push(body);
          current = makeSettings({
            gateway_base_url: body.gateway_base_url,
            gateway_updated_at: NOW,
            gateway_updated_by: "admin",
          });
          return jsonResponse(current);
        }
        if (url.startsWith("/api/settings")) return jsonResponse(current);
        if (url.startsWith("/api/channels")) {
          return jsonResponse({ items: [], total: 3 });
        }
        if (url === "/api/keys") return jsonResponse({ items: [], total: 2 });
        if (url === "/api/catalog") return jsonResponse({ items: [], total: 5 });
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();

    renderPage();

    const browserFallback = effectiveGatewayBaseUrl("").url;
    expect(await screen.findByText(browserFallback)).toBeInTheDocument();
    expect(
      screen.getByText(/未配置，按当前浏览器地址推断/),
    ).toBeInTheDocument();
    expect(await screen.findByText("全局概览")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "修改连接地址" }));
    const input = screen.getByLabelText("网关地址");
    await user.clear(input);
    await user.type(input, "https://llm.example.com:8443/");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(patchBodies).toEqual([
        { gateway_base_url: "https://llm.example.com:8443/" },
      ]),
    );
    expect(
      await screen.findByText("https://llm.example.com:8443"),
    ).toBeInTheDocument();
    expect(screen.getByText("已配置")).toBeInTheDocument();
  });
});
