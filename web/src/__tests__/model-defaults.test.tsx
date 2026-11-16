import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChannelModelFormDialog } from "@/components/channels/channel-model-form-dialog";
import type { Combo } from "@/lib/combos";
import {
  aggregateComboParams,
  comboParamLines,
  comboPriceRangeText,
  type ComboParamSource,
} from "@/lib/combo-params";
import type { ChannelModel, ModelParameters } from "@/lib/channel-models";
import type { ImplicitGroup } from "@/lib/groups";
import {
  describeDefaultsStatus,
  describeFieldSource,
  describePrefillSource,
  describeRefreshResult,
  fetchModelDefaultsStatus,
  prefillFormPatch,
  refreshModelDefaults,
  type ModelDefaultsRefreshResult,
  type ModelDefaultsStatus,
  type ModelPrefill,
} from "@/lib/model-defaults";
import type { CatalogEntry } from "@/lib/playground";
import { ModelsPage } from "@/pages/models";

const CHANNEL_ID = "CH000000000000000000000001";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function parameters(overrides: Partial<ModelParameters> = {}): ModelParameters {
  return {
    max_input_tokens: 0,
    max_output_tokens: 0,
    reasoning_level: "",
    input_types: ["text"],
    output_types: ["text"],
    ...overrides,
  };
}

function channelModel(overrides: Partial<ChannelModel> = {}): ChannelModel {
  const now = 1762838400000;
  return {
    id: "CM000000000000000000000001",
    channel_id: CHANNEL_ID,
    upstream_model: "alpha-model",
    call_model: "alpha",
    parameters: parameters(),
    input_price_per_1m: 0.5,
    output_price_per_1m: 1.5,
    cache_read_price_per_1m: null,
    cache_write_price_per_1m: null,
    enabled: true,
    health_status: "normal",
    visible_status: "normal",
    consecutive_failures: 0,
    consecutive_trip_count: 0,
    escalation_trip_count: 0,
    opened_at: null,
    cooldown_until: null,
    fault_until: null,
    next_retry_at: null,
    schedule: { version: 2, timezone: "Asia/Shanghai", rules: [] },
    cooldown: {
      failure_threshold: 5,
      cooldown_s: 30,
      rate_limit_cooldown_s: 5,
      half_open_max: 1,
      escalate_mode: "after_n",
      escalate_after: 3,
      fault_cooldown_s: 300,
    },
    probe_status: "unknown",
    probe_default: false,
    model_type: "chat",
    model_type_source: "upstream",
    probe_latency_ms: null,
    probe_message: "",
    probed_at: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function prefillResponse(overrides: Partial<ModelPrefill> = {}): ModelPrefill {
  return {
    upstream_model: "gpt-4o",
    matched: true,
    matched_by: "exact",
    version_loose: false,
    defaults: {
      model_type: "chat",
      model_type_source: "upstream",
      parameters: {
        max_input_tokens: 128000,
        max_output_tokens: 16384,
        reasoning_level: "medium",
        input_types: ["text", "image"],
        output_types: ["text"],
        tool_call: true,
        reasoning: true,
        attachment: true,
        structured_output: false,
      },
      input_price_per_1m: 2.5,
      output_price_per_1m: 10,
      cache_read_price_per_1m: 1.25,
      cache_write_price_per_1m: null,
    },
    sources: {
      max_input_tokens: { provider: "openai", providers: ["openai", "azure"], rule: "max" },
      input_price_per_1m: { provider: "openai", providers: ["openai"], rule: "median" },
      tool_call: { provider: "openai", providers: ["openai", "azure"], rule: "any" },
      model_type: { provider: "openai", providers: ["openai", "azure"], rule: "mode" },
    },
    provenance: {
      model_id: "openai/gpt-4o",
      canonical_model_id: "openai/gpt-4o",
      provider: "openai",
      matched_by: "exact",
      rule_version: "2026-11-01",
      pulled_at: "2026-11-16T02:00:00Z",
    },
    snapshot: { available: true, pulled_at: "2026-11-16T02:00:00Z", age_days: 0, stale: false },
    candidates: [],
    review_required: false,
    ...overrides,
  };
}

function statusResponse(overrides: Partial<ModelDefaultsStatus> = {}): ModelDefaultsStatus {
  return {
    enabled: true,
    rule_version: "2026-11-01",
    today: "2026-11-16",
    last_pull: "2026-11-16T02:00:00Z",
    last_attempt: "2026-11-16T02:00:00Z",
    fuzzy_auto: 0.92,
    fuzzy_review: 0.75,
    defaults_count: 1234,
    snapshot: {
      available: true,
      pulled_at: "2026-11-16T02:00:00Z",
      age_days: 0,
      stale: false,
      providers: 2,
      entries: 1200,
      bytes: 500000,
      error_summary: "",
      error_at: null,
    },
    ...overrides,
  };
}

function refreshResponse(overrides: Partial<ModelDefaultsRefreshResult> = {}): ModelDefaultsRefreshResult {
  return {
    status: "ok",
    trigger: "manual",
    attempted: true,
    refreshed: true,
    http_status: 200,
    elapsed_ms: 812,
    not_modified: false,
    conditional: true,
    providers: 2,
    entries: 1200,
    bytes: 500000,
    error_summary: "",
    pulled_at: "2026-11-16T03:00:00Z",
    last_pull: "2026-11-16T03:00:00Z",
    last_attempt: "2026-11-16T03:00:00Z",
    defaults_count: 1234,
    ...overrides,
  };
}

function renderDialog(
  fetchImpl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
  model: ChannelModel | null = null,
) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl));
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChannelModelFormDialog open onOpenChange={() => {}} channelId={CHANNEL_ID} model={model} />
    </QueryClientProvider>,
  );
}

describe("models.dev prefill defaults", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("turns defaults into a form patch and skips null fields", () => {
    const patch = prefillFormPatch(prefillResponse().defaults);
    expect(patch).not.toBeNull();
    expect(patch?.prices).toEqual({ input: 2.5, output: 10, cacheRead: 1.25, cacheWrite: null });
    expect(patch?.maxInputTokens).toBe(128000);
    expect(patch?.maxOutputTokens).toBe(16384);
    expect(patch?.reasoningLevel).toBe("medium");
    expect(patch?.inputTypes).toEqual(["text", "image"]);
    expect(patch?.capabilities).toEqual({
      tool_call: true,
      reasoning: true,
      attachment: true,
      structured_output: false,
    });
    expect(patch?.modelType).toBe("chat");

    expect(
      prefillFormPatch({
        model_type: null,
        model_type_source: null,
        parameters: {
          max_input_tokens: null,
          max_output_tokens: null,
          reasoning_level: null,
          input_types: null,
          output_types: null,
          tool_call: null,
          reasoning: null,
          attachment: null,
          structured_output: null,
        },
        input_price_per_1m: null,
        output_price_per_1m: null,
        cache_read_price_per_1m: null,
        cache_write_price_per_1m: null,
      }),
    ).toBeNull();
  });

  it("describes the snapshot source in one line, including providers and staleness", () => {
    expect(describePrefillSource(prefillResponse())).toBe(
      "参数来自 models.dev 快照 2026-11-16，合并 2 家供应商 · 精确匹配 · 仅作默认值，计费以渠道配置为准",
    );
    expect(
      describePrefillSource(
        prefillResponse({
          snapshot: { available: true, pulled_at: "2026-11-16T02:00:00Z", age_days: 3, stale: true },
        }),
      ),
    ).toContain("已过期 3 天");
    expect(
      describePrefillSource(
        prefillResponse({
          matched: false,
          matched_by: "fuzzy_review",
          defaults: null,
          candidates: [{ provider: "openrouter", model_id: "openai/gpt-4o-2024", score: 0.83, tier: "review" }],
          review_required: true,
        }),
      ),
    ).toBe("models.dev 未确认该模型，候选：openrouter/openai/gpt-4o-2024（review）。可先手填，不影响保存。");
    expect(
      describePrefillSource(
        prefillResponse({
          matched: false,
          matched_by: "unmatched",
          defaults: null,
          snapshot: { available: false, pulled_at: null, age_days: null, stale: false },
        }),
      ),
    ).toContain("暂无 models.dev 默认值");
  });

  it("prefills prices, limits and capability bits once the upstream name settles", async () => {
    const posts: unknown[] = [];
    renderDialog(async (input, init) => {
      if (String(input) === "/api/channel-models/prefill") {
        posts.push(JSON.parse(String(init?.body)));
        return jsonResponse(prefillResponse());
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    });

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("上游模型名"), { target: { value: "gpt-4o" } });

    await waitFor(() => expect(posts).toHaveLength(1), { timeout: 3000 });
    expect(posts[0]).toEqual({ upstream_model: "gpt-4o" });

    await waitFor(() => expect(within(dialog).getByLabelText("输入单价 / 1M")).toHaveValue(2.5));
    expect(within(dialog).getByLabelText("输出单价 / 1M")).toHaveValue(10);
    expect(within(dialog).getByLabelText("缓存读取单价 / 1M")).toHaveValue(1.25);
    expect(within(dialog).getByLabelText("缓存写入单价 / 1M")).toHaveValue(null);
    expect(within(dialog).getByLabelText("最大输入")).toHaveValue(128000);
    expect(within(dialog).getByLabelText("最大输出")).toHaveValue(16384);
    expect(within(dialog).getByLabelText("推理等级")).toHaveValue("medium");
    expect(within(dialog).getByLabelText("输入类型 图片")).toBeChecked();
    expect(within(dialog).getByLabelText("能力位 工具调用")).toBeChecked();
    expect(within(dialog).getByLabelText("能力位 推理")).toBeChecked();
    expect(within(dialog).getByLabelText("能力位 附件")).toBeChecked();
    expect(within(dialog).getByLabelText("能力位 结构化输出")).not.toBeChecked();
    expect(within(dialog).getByRole("note")).toHaveTextContent(
      "参数来自 models.dev 快照 2026-11-16，合并 2 家供应商 · 精确匹配",
    );
    // 决策 7：被预填字段带逐字段来源提示（provider 们 + 合并规则），挂在 Field 容器上。
    const fieldTitle = (label: string): string =>
      within(dialog).getByText(label).closest("div")?.getAttribute("title") ?? "";
    expect(fieldTitle("输入单价 / 1M")).toBe("来源 models.dev：openai · 多家取中位数；仅作默认值");
    expect(fieldTitle("最大输入")).toContain("多家取最大");
    expect(fieldTitle("工具调用")).toContain("任一家支持");
    expect(fieldTitle("上游模型名")).toBe("");
  });

  it("keeps values the operator already typed", async () => {
    renderDialog(async (input) => {
      if (String(input) === "/api/channel-models/prefill") return jsonResponse(prefillResponse());
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    });

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("输入单价 / 1M"), { target: { value: "9" } });
    fireEvent.change(within(dialog).getByLabelText("上游模型名"), { target: { value: "gpt-4o" } });

    await waitFor(() => expect(within(dialog).getByLabelText("最大输入")).toHaveValue(128000), {
      timeout: 3000,
    });
    // 人工值优先：被改过的单价不被默认值覆盖，其余字段照常预填。
    expect(within(dialog).getByLabelText("输入单价 / 1M")).toHaveValue(9);
  });

  it("never blocks creation when the snapshot is missing", async () => {
    renderDialog(async (input) => {
      if (String(input) === "/api/channel-models/prefill") {
        return jsonResponse(
          prefillResponse({
            matched: false,
            matched_by: "unmatched",
            defaults: null,
            snapshot: { available: false, pulled_at: null, age_days: null, stale: false },
          }),
        );
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    });

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("上游模型名"), { target: { value: "brand-new" } });

    await waitFor(
      () => expect(within(dialog).getByRole("note")).toHaveTextContent("暂无 models.dev 默认值"),
      { timeout: 3000 },
    );
    expect(within(dialog).getByLabelText("输入单价 / 1M")).toHaveValue(0);
    expect(within(dialog).getByRole("button", { name: "保存" })).toBeEnabled();
  });

  it("describes per-field sources with the provider list and merge rule", () => {
    expect(describeFieldSource(prefillResponse(), "max_input_tokens")).toBe(
      "来源 models.dev：openai、azure · 多家取最大；仅作默认值",
    );
    expect(describeFieldSource(prefillResponse(), "input_price_per_1m")).toContain("多家取中位数");
    expect(describeFieldSource(prefillResponse(), "tool_call")).toContain("任一家支持");
    expect(describeFieldSource(prefillResponse(), "missing_field")).toBe("");
    expect(describeFieldSource(null, "max_input_tokens")).toBe("");
  });

  it("renders the status and refresh echoes in one line", () => {
    expect(describeDefaultsStatus(statusResponse())).toBe(
      "快照 2026-11-16 · 2 家供应商 · 1200 个条目 · 入库默认值 1234 条",
    );
    expect(
      describeDefaultsStatus(
        statusResponse({
          snapshot: {
            available: true,
            pulled_at: "2026-10-12T02:00:00Z",
            age_days: 35,
            stale: true,
            providers: 2,
            entries: 1200,
            bytes: 500000,
            error_summary: "",
            error_at: null,
          },
        }),
      ),
    ).toContain("已过期 35 天");
    expect(
      describeDefaultsStatus(
        statusResponse({
          snapshot: {
            available: false,
            pulled_at: null,
            age_days: null,
            stale: false,
            providers: 0,
            entries: 0,
            bytes: 0,
            error_summary: "snapshot fetch failed",
            error_at: 1763200000000,
          },
        }),
      ),
    ).toContain("snapshot fetch failed");
    expect(describeRefreshResult(refreshResponse())).toBe(
      "已同步 1200 个条目（2 家供应商） · 812 ms · 入库默认值 1234 条",
    );
    expect(
      describeRefreshResult(refreshResponse({ status: "not_modified", not_modified: true, refreshed: false })),
    ).toContain("304");
    expect(
      describeRefreshResult(refreshResponse({ status: "error", refreshed: false, http_status: 503, error_summary: "upstream 5xx" })),
    ).toContain("upstream 5xx");
    expect(describeRefreshResult(refreshResponse({ status: "disabled" }))).toContain("未启用");
    expect(describeRefreshResult(refreshResponse({ status: "busy" }))).toContain("稍后再试");
    expect(describeRefreshResult(refreshResponse({ status: "skipped" }))).toContain("跳过");
  });

  it("fetches status and triggers refresh through the admin endpoints", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push((init?.method ?? "GET") + " " + url);
        if (url === "/api/model-defaults/status") return jsonResponse(statusResponse());
        if (url === "/api/model-defaults/refresh") return jsonResponse(refreshResponse());
        return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
      }),
    );
    const status = await fetchModelDefaultsStatus();
    const refresh = await refreshModelDefaults();
    expect(calls).toEqual(["GET /api/model-defaults/status", "POST /api/model-defaults/refresh"]);
    expect(status.snapshot.entries).toBe(1200);
    expect(refresh.entries).toBe(1200);
  });

  it("writes false when the operator unchecks a prefilled capability and keeps the provider's false claim", async () => {
    const posts: Array<Record<string, unknown>> = [];
    renderDialog(async (input, init) => {
      const url = String(input);
      if (url === "/api/channel-models/prefill") return jsonResponse(prefillResponse());
      if (url === "/api/channels/" + CHANNEL_ID + "/models") {
        posts.push(JSON.parse(String(init?.body)));
        return jsonResponse({
          id: "CM000000000000000000000099",
          channel_id: CHANNEL_ID,
          upstream_model: "gpt-4o",
          call_model: "gpt-4o",
          parameters: {},
          input_price_per_1m: 2.5,
          output_price_per_1m: 10,
          cache_read_price_per_1m: 1.25,
          cache_write_price_per_1m: null,
          enabled: true,
          health_status: "normal",
          visible_status: "normal",
          consecutive_failures: 0,
          consecutive_trip_count: 0,
          escalation_trip_count: 0,
          opened_at: null,
          cooldown_until: null,
          fault_until: null,
          next_retry_at: null,
          model_type: "chat",
          model_type_source: "upstream",
          created_at: 1762838400000,
          updated_at: 1762838400000,
        });
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    });

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("上游模型名"), { target: { value: "gpt-4o" } });
    await waitFor(() => expect(within(dialog).getByLabelText("能力位 工具调用")).toBeChecked(), {
      timeout: 3000,
    });

    // 取消勾选 = 显式 false（task-153 拍板 A）：所见即所得。
    fireEvent.click(within(dialog).getByLabelText("能力位 工具调用"));
    await waitFor(() => expect(within(dialog).getByLabelText("能力位 工具调用")).not.toBeChecked());
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(posts).toHaveLength(1), { timeout: 3000 });
    const parameters = posts[0].parameters as Record<string, unknown>;
    expect(parameters.tool_call).toBe(false);
    // 未触碰的能力位按当前所见值全量提交（models.dev 厂商事实 false 原样保留）。
    expect(parameters.structured_output).toBe(false);
    expect(parameters.reasoning).toBe(true);
    // 预填的 model_type 只是建议，不提交，后端继续自动判定。
    expect("model_type" in posts[0]).toBe(false);
  });

  it("submits every capability bit as an explicit boolean for a new model", async () => {
    const posts: Array<Record<string, unknown>> = [];
    renderDialog(async (input, init) => {
      const url = String(input);
      if (url === "/api/channel-models/prefill") {
        return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
      }
      if (url === "/api/channels/" + CHANNEL_ID + "/models") {
        posts.push(JSON.parse(String(init?.body)));
        return jsonResponse(channelModel({ upstream_model: "fresh-model" }));
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    });

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("上游模型名"), {
      target: { value: "fresh-model" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(posts).toHaveLength(1), { timeout: 3000 });
    // 拍板 A 全量提交：没有预填时，四个能力位也按表单所见写成显式 false。
    expect(posts[0].parameters).toMatchObject({
      tool_call: false,
      reasoning: false,
      attachment: false,
      structured_output: false,
    });
  });

  it("keeps an existing model's capability bits when saving the form", async () => {
    const patches: Array<Record<string, unknown>> = [];
    const existing = channelModel({
      parameters: parameters({ tool_call: true, structured_output: false }),
    });
    renderDialog(async (input, init) => {
      if (String(input) === "/api/channel-models/" + existing.id) {
        patches.push(JSON.parse(String(init?.body)));
        return jsonResponse(existing);
      }
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    }, existing);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("能力位 工具调用")).toBeChecked();
    expect(within(dialog).getByLabelText("能力位 结构化输出")).not.toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patches).toHaveLength(1), { timeout: 3000 });
    const body = patches[0].parameters as Record<string, unknown>;
    expect(body.tool_call).toBe(true);
    expect(body.structured_output).toBe(false);
    // 旧行没声明过的键，保存时全量落成 false（拍板 A），不再保留 undefined。
    expect(body.reasoning).toBe(false);
    expect(body.attachment).toBe(false);
  });
});

function comboSource(overrides: Partial<ComboParamSource> = {}): ComboParamSource {
  return {
    parameters: parameters(),
    input_price_per_1m: 1,
    output_price_per_1m: 2,
    ...overrides,
  };
}

describe("combo parameter union", () => {
  it("takes the widest limits, unions capabilities and lists the price range", () => {
    const union = aggregateComboParams([
      comboSource({
        parameters: parameters({
          max_input_tokens: 128000,
          max_output_tokens: 8192,
          input_types: ["text", "image"],
          tool_call: true,
        }),
        input_price_per_1m: 1,
        output_price_per_1m: 2,
      }),
      comboSource({
        parameters: parameters({
          max_input_tokens: 200000,
          max_output_tokens: 16384,
          reasoning: true,
        }),
        input_price_per_1m: 3,
        output_price_per_1m: 6,
      }),
    ]);

    expect(union.maxInputTokens).toBe(200000);
    expect(union.maxOutputTokens).toBe(16384);
    expect(union.inputTypes).toEqual(["text", "image"]);
    expect(union.capabilities).toEqual(["tool_call", "reasoning"]);
    expect(comboPriceRangeText(union)).toBe("入 $1.00–$3.00 / 出 $2.00–$6.00");
    expect(comboParamLines(union)).toEqual([
      "输入上限 200,000 词元",
      "输出上限 16,384 词元",
      "能力 工具调用、推理",
      "模态 文本/图片 → 文本",
      "价格 入 $1.00–$3.00 / 出 $2.00–$6.00（按实际选中成员计费）",
    ]);
  });

  it("degrades to a single price and no-member placeholders", () => {
    const single = aggregateComboParams([comboSource()]);
    expect(comboPriceRangeText(single)).toBe("入 $1.00 / 出 $2.00");

    const empty = aggregateComboParams([]);
    expect(empty.memberCount).toBe(0);
    expect(empty.price).toBeNull();
    expect(comboPriceRangeText(empty)).toBeNull();
    expect(comboParamLines(empty)).toContain("能力 未声明");
  });
});

function catalogItem(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: "CM000000000000000000000001",
    kind: "direct",
    status: "enabled",
    name: "channel-a/A",
    channel_id: CHANNEL_ID,
    channel_slug: "channel-a",
    channel_name: "主渠道",
    protocol: "openai_chat",
    upstream_model: "gpt-4o",
    call_model: "A",
    parameters: parameters({ max_input_tokens: 128000, max_output_tokens: 8192 }),
    input_price_per_1m: 1,
    output_price_per_1m: 2,
    enabled: true,
    created_at: 1762838400000,
    ...overrides,
  };
}

function comboMember(overrides: Partial<Combo["members"][number]> = {}) {
  return {
    id: "CT000000000000000000000001",
    target_type: "channel_model" as const,
    priority: 2,
    enabled: true,
    available: true,
    channel_model_id: "CM000000000000000000000001",
    child_combo_id: null,
    channel_id: CHANNEL_ID,
    channel_slug: "channel-a",
    channel_name: "主渠道",
    call_model: "A",
    upstream_model: "gpt-4o",
    child_combo_name: "",
    child_combo_enabled: false,
    protocol: "openai_chat",
    created_at: 1762838400000,
    ...overrides,
  };
}

function combo(overrides: Partial<Combo> = {}): Combo {
  return {
    id: "CB000000000000000000000001",
    name: "team-chat",
    description: "",
    enabled: true,
    max_attempts: 3,
    failover_rules: { rules: [], default_action: "next" },
    member_count: 2,
    available_count: 2,
    depth: 1,
    max_depth: 3,
    callable: true,
    created_at: 1762838400000,
    updated_at: 1762838400000,
    members: [
      comboMember(),
      comboMember({
        id: "CT000000000000000000000002",
        priority: 1,
        channel_model_id: "CM000000000000000000000002",
        channel_id: "CH000000000000000000000002",
        channel_slug: "newapi",
        channel_name: "备用渠道",
        call_model: "B",
        upstream_model: "gpt-4o-mini",
      }),
    ],
    ...overrides,
  };
}

function group(overrides: Partial<ImplicitGroup> = {}): ImplicitGroup {
  return {
    name: "auto-pool",
    call_model: "auto-pool",
    member_count: 2,
    available_count: 2,
    callable: true,
    created_at: 1762838400000,
    members: [
      {
        channel_model_id: "CM000000000000000000000001",
        channel_id: CHANNEL_ID,
        channel_slug: "channel-a",
        channel_name: "主渠道",
        protocol: "openai_chat",
        upstream_model: "gpt-4o",
        call_model: "A",
        priority: 3,
        enabled: true,
        channel_enabled: true,
        available: true,
      },
      {
        channel_model_id: "CM000000000000000000000002",
        channel_id: "CH000000000000000000000002",
        channel_slug: "newapi",
        channel_name: "备用渠道",
        protocol: "openai_chat",
        upstream_model: "gpt-4o-mini",
        call_model: "B",
        priority: 2,
        enabled: true,
        channel_enabled: true,
        available: true,
      },
    ],
    ...overrides,
  };
}

function setupCatalogFetch() {
  const catalog: CatalogEntry[] = [
    catalogItem({
      parameters: parameters({ max_input_tokens: 128000, max_output_tokens: 8192, tool_call: true }),
    }),
    catalogItem({
      id: "CM000000000000000000000002",
      name: "newapi/B",
      channel_id: "CH000000000000000000000002",
      channel_slug: "newapi",
      channel_name: "备用渠道",
      upstream_model: "gpt-4o-mini",
      call_model: "B",
      parameters: parameters({
        max_input_tokens: 200000,
        max_output_tokens: 16384,
        reasoning: true,
      }),
      input_price_per_1m: 3,
      output_price_per_1m: 6,
    }),
    catalogItem({
      id: "CB000000000000000000000001",
      name: "team-chat",
      kind: "combo",
      origin: "manual",
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "",
      parameters: parameters(),
      input_price_per_1m: 0,
      output_price_per_1m: 0,
    }),
    catalogItem({
      id: "group:auto-pool",
      name: "auto-pool",
      kind: "combo",
      origin: "auto",
      channel_id: "",
      channel_slug: "",
      channel_name: "",
      protocol: "",
      upstream_model: "",
      call_model: "auto-pool",
      parameters: parameters(),
      input_price_per_1m: 0,
      output_price_per_1m: 0,
    }),
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/catalog")) return jsonResponse({ items: catalog, total: catalog.length });
      if (url === "/api/combos") return jsonResponse({ items: [combo()], total: 1 });
      if (url === "/api/groups") return jsonResponse({ items: [group()], total: 1 });
      return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
    }),
  );
}

function renderCatalog() {
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

describe("combo rows show the parameter union", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the member price range instead of a single price", async () => {
    setupCatalogFetch();
    renderCatalog();
    await screen.findByRole("table");

    const ranges = await screen.findAllByText("入 $1.00–$3.00 / 出 $2.00–$6.00");
    expect(ranges).toHaveLength(2);
    // 直连行仍是单一价格，组合行不合成。
    expect(screen.getByText(/入 \$1\.00 \/ 出 \$2\.00 \/ 缓存 —/)).toBeInTheDocument();
  });

  it("syncs models.dev from the toolbar and echoes status and refresh results", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/catalog")) return jsonResponse({ items: [catalogItem()], total: 1 });
        if (url === "/api/combos") return jsonResponse({ items: [], total: 0 });
        if (url === "/api/groups") return jsonResponse({ items: [], total: 0 });
        if (url === "/api/model-defaults/status") return jsonResponse(statusResponse());
        if (url === "/api/model-defaults/refresh") return jsonResponse(refreshResponse());
        return jsonResponse({ code: "NOT_FOUND", message: "not found" }, 404);
      }),
    );
    renderCatalog();

    // 快照状态常驻一行回显。
    await screen.findByText(/快照 2026-11-16 · 2 家供应商 · 1200 个条目/);

    fireEvent.click(await screen.findByRole("button", { name: "同步 models.dev" }));
    await screen.findByText(/已同步 1200 个条目（2 家供应商）/);
  });

  it("adds limits, capabilities and the price note to the combo tooltip", async () => {
    setupCatalogFetch();
    renderCatalog();
    await screen.findByRole("table");

    const manual = screen.getByLabelText("手动组合，优先级 P2 channel-a/A / P1 newapi/B");
    let tooltip: HTMLElement | null = null;
    await act(async () => {
      manual.focus();
      await new Promise((resolve) => setTimeout(resolve, 300));
      tooltip = document.querySelector('[role="tooltip"]');
    });

    expect(tooltip).not.toBeNull();
    expect(tooltip).toHaveTextContent("参数并集（按成员）");
    expect(tooltip).toHaveTextContent("输入上限 200,000 词元");
    expect(tooltip).toHaveTextContent("能力 工具调用、推理");
    expect(tooltip).toHaveTextContent("价格 入 $1.00–$3.00 / 出 $2.00–$6.00（按实际选中成员计费）");
  });
});
