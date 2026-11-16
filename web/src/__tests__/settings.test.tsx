import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SystemSettings } from "@/lib/settings";
import { SettingsPage } from "@/pages/settings";

import { chooseSelect, getSelectTrigger } from "./select-helpers";

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
    route_strategy: "uniform",
    route_window_minutes: 30,
    gateway_inflight_cap: 256,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/settings"]}>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("settings page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to off and explains that redaction cannot be disabled", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(makeSettings())));

    renderPage();

    expect(await screen.findByRole("heading", { name: "日志正文记录策略" })).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: /关闭/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /仅错误/ })).not.toBeChecked();
    expect(screen.getByText(/脱敏强制生效/)).toBeInTheDocument();
    expect(screen.getByText(/尚未修改，当前使用默认策略/)).toBeInTheDocument();
  });

  it("saves the chosen policy and shows who updated it and when", async () => {
    let current = makeSettings();
    const calls: Array<{ method: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (method === "PATCH") {
          const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
          calls.push({ method, body });
          current = makeSettings({
            body_policy: body.body_policy,
            updated_at: NOW,
            updated_by: "admin",
          });
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("radio", { name: /仅错误/ }));
    const save = screen.getByRole("button", { name: "保存策略" });
    expect(save).toBeEnabled();
    await user.click(save);

    expect(await screen.findByText(/策略已保存/)).toBeInTheDocument();
    expect(calls).toEqual([{ method: "PATCH", body: { body_policy: "errors" } }]);
    expect(screen.getByRole("radio", { name: /仅错误/ })).toBeChecked();
    expect(screen.getByText(/最后更新：/)).toHaveTextContent("admin");
  });

  it("only enables save when the policy actually changes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(makeSettings({ body_policy: "all" }))));

    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("radio", { name: /全部/ });
    expect(screen.getByRole("radio", { name: /全部/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "保存策略" })).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /关闭/ }));
    expect(screen.getByRole("button", { name: "保存策略" })).toBeEnabled();
  });

  it("shows watchdog timeouts and saves changed values", async () => {
    let current = makeSettings();
    const calls: Array<{ method: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (method === "PATCH") {
          const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
          calls.push({ method, body });
          current = makeSettings({
            ...body,
            upstream_timeouts_updated_at: NOW,
            upstream_timeouts_updated_by: "admin",
            upstream_timeouts_is_default: false,
          });
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { name: "上游超时看门狗" })).toBeInTheDocument();
    await screen.findByDisplayValue("10"); // 等设置数据落地，输入框才有初值
    expect(screen.getByLabelText("连接超时（秒）")).toHaveValue("10");
    expect(screen.getByLabelText("首字节超时（秒）")).toHaveValue("30");
    expect(screen.getByLabelText("读空闲超时（秒）")).toHaveValue("120");
    expect(screen.getByText("环境默认")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存超时" })).toBeDisabled();

    await user.clear(screen.getByLabelText("读空闲超时（秒）"));
    await user.type(screen.getByLabelText("读空闲超时（秒）"), "300");
    await user.click(screen.getByRole("button", { name: "保存超时" }));

    expect(await screen.findByText(/超时已保存/)).toBeInTheDocument();
    expect(calls).toEqual([
      {
        method: "PATCH",
        body: {
          upstream_connect_timeout_s: 10,
          upstream_first_byte_timeout_s: 30,
          upstream_read_timeout_s: 300,
        },
      },
    ]);
    expect(screen.getByText("已覆盖环境默认")).toBeInTheDocument();
  });

  it("rejects non-positive watchdog values without sending a PATCH", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(makeSettings()),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "上游超时看门狗" });
    await screen.findByDisplayValue("10"); // 等数据落地，避免首屏 effect 重置输入
    await user.clear(screen.getByLabelText("连接超时（秒）"));
    await user.type(screen.getByLabelText("连接超时（秒）"), "0");
    await user.click(screen.getByRole("button", { name: "保存超时" }));

    expect(await screen.findByText(/连接超时必须大于 0/)).toBeInTheDocument();
    const patchCalls = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patchCalls).toEqual([]);
  });

  it("shows routing defaults and reveals a custom window input", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(makeSettings())));

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { name: "路由策略与准入" })).toBeInTheDocument();
    await screen.findByDisplayValue("256"); // 等设置数据落地，输入框才有初值
    expect(getSelectTrigger("同层路由策略")).toHaveTextContent("同层负载均衡");
    expect(getSelectTrigger("统计窗口（分钟）")).toHaveTextContent("30 分钟");
    expect(screen.getByLabelText("节点在飞上限")).toHaveValue("256");
    expect(screen.getByRole("button", { name: "保存路由策略" })).toBeDisabled();

    await chooseSelect(user, { triggerName: "统计窗口（分钟）", optionName: "自定义" });
    expect(screen.getByLabelText("自定义窗口（分钟）")).toHaveValue("30");
    expect(screen.getByRole("button", { name: "保存路由策略" })).toBeDisabled();

    await user.clear(screen.getByLabelText("自定义窗口（分钟）"));
    await user.type(screen.getByLabelText("自定义窗口（分钟）"), "45");
    expect(screen.getByRole("button", { name: "保存路由策略" })).toBeEnabled();
  });

  it("saves strategy, window and inflight cap together", async () => {
    let current = makeSettings();
    const calls: Array<{ method: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (method === "PATCH") {
          const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
          calls.push({ method, body });
          current = makeSettings({ ...body });
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "路由策略与准入" });
    await screen.findByDisplayValue("256");
    await chooseSelect(user, { triggerName: "同层路由策略", optionName: "成本优先" });
    await chooseSelect(user, { triggerName: "统计窗口（分钟）", optionName: "15 分钟" });
    await user.clear(screen.getByLabelText("节点在飞上限"));
    await user.type(screen.getByLabelText("节点在飞上限"), "0");
    await user.click(screen.getByRole("button", { name: "保存路由策略" }));

    expect(await screen.findByText(/路由策略已保存/)).toBeInTheDocument();
    expect(calls).toEqual([
      {
        method: "PATCH",
        body: {
          route_strategy: "cost",
          route_window_minutes: 15,
          gateway_inflight_cap: 0,
        },
      },
    ]);
    expect(getSelectTrigger("同层路由策略")).toHaveTextContent("成本优先");
    expect(screen.getByLabelText("节点在飞上限")).toHaveValue("0");
  });

  it("rejects a custom window below 15 minutes without sending a PATCH", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(makeSettings()),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "路由策略与准入" });
    await screen.findByDisplayValue("256");
    await chooseSelect(user, { triggerName: "统计窗口（分钟）", optionName: "自定义" });
    const customWindow = screen.getByLabelText("自定义窗口（分钟）");
    await user.clear(customWindow);
    await user.type(customWindow, "14");
    await user.click(screen.getByRole("button", { name: "保存路由策略" }));

    expect(await screen.findByText(/自定义窗口不能小于 15 分钟/)).toBeInTheDocument();
    const patchCalls = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patchCalls).toEqual([]);
  });

  it("offers restoring env defaults once overrides exist", async () => {
    const calls: Array<{ method: string; body: unknown }> = [];
    let current = makeSettings({
      upstream_read_timeout_s: 300,
      upstream_timeouts_updated_at: NOW,
      upstream_timeouts_updated_by: "admin",
      upstream_timeouts_is_default: false,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (method === "PATCH") {
          const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
          calls.push({ method, body });
          current = makeSettings();
          return jsonResponse(current);
        }
        return jsonResponse(current);
      }),
    );

    const user = userEvent.setup();
    renderPage();

    const reset = await screen.findByRole("button", { name: "恢复环境默认" });
    await user.click(reset);

    expect(await screen.findByText("环境默认")).toBeInTheDocument();
    expect(calls).toEqual([{ method: "PATCH", body: { upstream_timeouts_reset: true } }]);
  });
});
