import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KeysPage } from "@/pages/keys";
import { Toaster } from "@/components/ui/sonner";
import { copyText } from "@/lib/clipboard";
import type { ApiKey } from "@/lib/keys";

// 行级复制会先 reveal 再写剪贴板：写剪贴板在 jsdom 里没有实现，模块级 stub 掉，
// 只验证「复制成功后的内联反馈 + toast」这条链路。
vi.mock("@/lib/clipboard", () => ({ copyText: vi.fn(async () => {}) }));

const NOW = 1762838400000;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeKey(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: "KEY00000000000000000000001",
    label: "laptop",
    prefix: "sk-demo-ke",
    enabled: true,
    last_used_at: null,
    created_at: NOW,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/keys"]}>
        <KeysPage />
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("keys page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.mocked(copyText).mockReset();
  });

  it("shows an empty state with a create action", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ items: [], total: 0 })));

    renderPage();

    expect(await screen.findByText("还没有用户密钥")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建第一个密钥" })).toBeInTheDocument();
  });

  it("creates a key, shows plaintext once, then only the prefix", async () => {
    const state: { keys: ApiKey[] } = { keys: [] };
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const plaintext = "sk-demo-plaintext-value";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ url, method, body });

      if (method === "GET" && url === "/api/keys") {
        return jsonResponse({ items: state.keys, total: state.keys.length });
      }
      if (method === "POST" && url === "/api/keys") {
        const created = {
          ...makeKey({ label: body.label, prefix: plaintext.slice(0, 10) }),
          key: plaintext,
        };
        state.keys = [...state.keys, created];
        return jsonResponse(created, 201);
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "创建第一个密钥" }));
    const labelInput = screen.getByLabelText("密钥标识");
    await user.clear(labelInput);
    await user.type(labelInput, "laptop");
    await user.click(screen.getByRole("button", { name: "创建密钥" }));

    expect(await screen.findByText(plaintext)).toBeInTheDocument();
    expect(screen.getByText("密钥已创建")).toBeInTheDocument();
    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          (call.body as { label?: string } | null)?.label === "laptop",
      ),
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "我已保存" }));

    await waitFor(() => {
      expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
    });
    expect(await screen.findByText("sk-demo-pl…")).toBeInTheDocument();
    expect(screen.getByText("laptop")).toBeInTheDocument();
    expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
  });

  it("copies a row secret with inline and toast feedback", async () => {
    const key = makeKey();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/keys") {
          return jsonResponse({ items: [key], total: 1 });
        }
        if (url === `/api/keys/${key.id}/secret`) {
          return jsonResponse({ key: "sk-demo-plaintext" });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "复制" }));

    expect(vi.mocked(copyText)).toHaveBeenCalledWith("sk-demo-plaintext");
    expect(await screen.findByRole("button", { name: "已复制" })).toBeInTheDocument();
    expect(await screen.findByText("已复制密钥")).toBeInTheDocument();
  });

  // ui.md §2.3：危险确认全站唯一实现（ui/confirm-dialog → alertdialog 语义），
  // 可访问名（标题）必须是问句。
  it("delete and reset confirms are alertdialogs with question-form titles", async () => {
    const key = makeKey();
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        calls.push({ url, method });
        if (method === "GET" && url === "/api/keys") {
          return jsonResponse({ items: [key], total: 1 });
        }
        if (method === "DELETE" && url === `/api/keys/${key.id}`) {
          return new Response(null, { status: 204 });
        }
        if (method === "POST" && url === `/api/keys/${key.id}/reset`) {
          return jsonResponse({ ...key, prefix: "sk-new-pre" });
        }
        return jsonResponse({ code: "NOT_FOUND", message: "not mocked" }, 404);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await screen.findByText("laptop");

    await user.click(screen.getByRole("button", { name: "删除" }));
    const deleteDialog = screen.getByRole("alertdialog");
    expect(deleteDialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByRole("alertdialog", { name: "删除密钥 laptop？" }),
    ).toBeInTheDocument();
    expect(deleteDialog).toHaveTextContent("此操作不可撤销");
    // 取消不产生 DELETE
    await user.click(within(deleteDialog).getByRole("button", { name: "取消" }));
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "删除" }));
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "删除" }),
    );
    await waitFor(() => {
      expect(calls.some((call) => call.method === "DELETE")).toBe(true);
    });

    await user.click(screen.getByRole("button", { name: "重置" }));
    expect(
      screen.getByRole("alertdialog", { name: "重置密钥 laptop？" }),
    ).toBeInTheDocument();
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "重置密钥" }),
    );
    await waitFor(() => {
      expect(
        calls.some((call) => call.method === "POST" && call.url.endsWith("/reset")),
      ).toBe(true);
    });
  });

  it("surfaces duplicate label errors without leaking the key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if ((init?.method ?? "GET") === "GET") {
          return jsonResponse({ items: [makeKey()], total: 1 });
        }
        return jsonResponse(
          { code: "CONFLICT", message: "密钥标识 laptop 已存在，请换一个标识" },
          409,
        );
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "新建密钥" }));
    // 标识默认置空（2026-11-15 巡检修复：避免误建 default 密钥），所以先填一个再提交。
    await user.type(screen.getByLabelText("密钥标识"), "laptop");
    await user.click(screen.getByRole("button", { name: "创建密钥" }));

    expect(
      await screen.findByText("密钥标识 laptop 已存在，请换一个标识"),
    ).toBeInTheDocument();
  });
});
