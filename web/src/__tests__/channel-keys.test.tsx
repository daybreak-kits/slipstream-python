import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChannelKeysPanel } from "@/components/channels/channel-keys-panel";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import type { ChannelKey } from "@/lib/channels";

const NOW = 1762838400000;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EXISTING_KEY: ChannelKey = {
  id: "CK000000000000000000000001",
  label: "主密钥",
  masked: "••••••1111",
  enabled: true,
  last_used_at: null,
  created_at: NOW,
};

function renderPanel(
  overrides: Partial<React.ComponentProps<typeof ChannelKeysPanel>> = {},
) {
  toast.dismiss();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Toaster />
      <ChannelKeysPanel
        channelId="CH1"
        existingKeys={[EXISTING_KEY]}
        pendingKeys={[]}
        onAddPending={vi.fn()}
        onRemovePending={vi.fn()}
        {...overrides}
      />
    </QueryClientProvider>,
  );
}

describe("channel keys panel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports an existing key without inserting it again", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/CH1/keys") && init?.method === "POST") {
          return jsonResponse(
            { code: "CONFLICT", message: "该密钥已存在于当前渠道" },
            409,
          );
        }
        throw new Error(`unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPanel();
    await user.type(screen.getByLabelText("新密钥"), "sk-dup-9999");
    await user.click(screen.getByRole("button", { name: "添加" }));

    expect(
      await screen.findByText("该密钥已存在，未重复添加。"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("••••••1111")).toHaveLength(1);
  });

  it("deduplicates pasted keys and counts conflicts from the API", async () => {
    const savedKeys: string[] = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/channels/test-connection")) {
          return jsonResponse({
            status: "ok",
            latency_ms: 20,
            http_status: 200,
            message: "连接成功",
            model_count: 1,
            models_preview: ["fake-model"],
            protocol: "openai_chat",
            auth_style: "bearer",
            tested_at: NOW,
          });
        }
        if (url.endsWith("/api/channels/CH1/keys") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          savedKeys.push(body.api_key);
          if (body.api_key === "sk-b-2222") {
            return jsonResponse(
              { code: "CONFLICT", message: "该密钥已存在于当前渠道" },
              409,
            );
          }
          return jsonResponse(
            {
              id: "CK000000000000000000000002",
              label: body.label,
              masked: "••••••1111",
              enabled: true,
              last_used_at: null,
              created_at: NOW,
            },
            201,
          );
        }
        throw new Error(`unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPanel();
    await user.click(screen.getByRole("button", { name: "批量模式" }));
    await user.type(
      screen.getByLabelText("批量密钥"),
      "sk-a-1111\nsk-a-1111\nsk-b-2222",
    );

    expect(screen.getByText("已忽略 1 个重复密钥")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "检测并保存" }));

    expect(
      await screen.findByText("成功保存 1 个密钥，已忽略 1 个已存在的密钥"),
    ).toBeInTheDocument();
    expect(savedKeys).toEqual(["sk-a-1111", "sk-b-2222"]);
  });

  it("edits a key label through the PATCH endpoint", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (
          url.endsWith("/api/channels/CH1/keys/CK000000000000000000000001") &&
          init?.method === "PATCH"
        ) {
          const body = JSON.parse(String(init.body));
          return jsonResponse({ ...EXISTING_KEY, label: body.label });
        }
        throw new Error(`unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPanel();
    await user.click(
      screen.getByRole("button", { name: "修改密钥备注 主密钥" }),
    );

    const input = await screen.findByLabelText("编辑密钥备注");
    await user.clear(input);
    await user.type(input, "主力");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("备注已更新")).toBeInTheDocument();

    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/channels/CH1/keys/CK000000000000000000000001") &&
        init?.method === "PATCH",
    );
    expect(patchCall).toBeTruthy();
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ label: "主力" });
    await waitFor(() =>
      expect(
        screen.queryByLabelText("编辑密钥备注"),
      ).not.toBeInTheDocument(),
    );
  });
});
