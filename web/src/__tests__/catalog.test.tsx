
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CatalogEntry } from "@/lib/playground";
import { ModelsPage } from "@/pages/models";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function directEntry(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: "CM000000000000000000000001",
    kind: "direct",
    status: "enabled",
    name: "beta/live-model",
    channel_id: "CH000000000000000000000001",
    channel_slug: "beta",
    channel_name: "渠道乙",
    protocol: "openai_chat",
    upstream_model: "live-model",
    call_model: "live-model",
    parameters: {
      max_input_tokens: 128000,
      max_output_tokens: 8192,
      reasoning_level: "",
      input_types: ["text"],
      output_types: ["text"],
    },
    input_price_per_1m: 1,
    output_price_per_1m: 2,
    enabled: true,
    created_at: 1762838400000,
    health_status: "normal",
    visible_status: "normal",
    consecutive_failures: 0,
    status_message: "",
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/catalog"]}>
        <ModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("model catalog management", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps banned direct models listed and lets them be enabled again", async () => {
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const items = [
      directEntry(),
      directEntry({
        id: "CM000000000000000000000002",
        name: "beta/banned-model",
        upstream_model: "banned-model",
        call_model: "banned-model",
        enabled: false,
        status: "disabled",
        visible_status: "disabled",
      }),
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.startsWith("/api/catalog") && method === "GET") {
        return jsonResponse({ items, total: items.length });
      }
      if (url.startsWith("/api/channel-models/")) {
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
        return jsonResponse({ ...items[1], enabled: true, status: "enabled", visible_status: "normal" });
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPage();

    // 目录页必须带 include_disabled=true：否则被停用的行根本拿不到，「启用」无从点起。
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes("include_disabled=true")),
      ).toBe(true),
    );

    expect(await screen.findByRole("button", { name: "封禁 beta/live-model" })).toBeInTheDocument();
    // 已停用直连行仍留在列表与计数里，操作列给出「启用」。
    expect(screen.getByText(/2 个直连/)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "启用 beta/banned-model" }));
    expect(screen.getByRole("heading", { name: "启用模型" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({
      url: "/api/channel-models/CM000000000000000000000002",
      method: "PATCH",
      body: { enabled: true },
    });
    // 健康正常的停用模型只需写回 enabled，不再叠一次复位。
    expect(calls.some((call) => call.url.endsWith("/reset"))).toBe(false);
  });
});
