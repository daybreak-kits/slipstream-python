import { describe, expect, it } from "vitest";

import type { Combo } from "@/lib/combos";
import {
  buildCandidates,
  descendantIds,
  fromCombo,
  maxKeySequence,
  nextKey,
  parseStatuses,
  ruleKey,
  sortCandidatesByName,
  type MemberCandidate,
} from "@/lib/combos-editor";
import type { CatalogEntry } from "@/lib/playground";

function comboMember(overrides: Partial<Combo["members"][number]> = {}): Combo["members"][number] {
  return {
    id: "CT000000000000000000000001",
    target_type: "channel_model",
    priority: 1,
    enabled: true,
    available: true,
    channel_model_id: "CM000000000000000000000001",
    child_combo_id: null,
    channel_id: "CH000000000000000000000001",
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
    description: "主备组合",
    enabled: true,
    max_attempts: 3,
    failover_rules: { rules: [], default_action: "next" },
    member_count: 2,
    available_count: 2,
    depth: 2,
    max_depth: 3,
    callable: true,
    created_at: 1762838400000,
    updated_at: 1762838400000,
    members: [comboMember()],
    ...overrides,
  };
}

function catalogItem(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    id: "CM000000000000000000000001",
    kind: "direct",
    status: "ok",
    name: "channel-a/A",
    channel_id: "CH000000000000000000000001",
    channel_slug: "channel-a",
    channel_name: "主渠道",
    protocol: "openai_chat",
    upstream_model: "gpt-4o",
    call_model: "A",
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
    ...overrides,
  };
}

function candidate(overrides: Partial<MemberCandidate> = {}): MemberCandidate {
  return {
    key: "channel_model:CM1",
    targetType: "channel_model",
    targetId: "CM1",
    label: "channel-a/A",
    searchText: "channel-a/A A gpt-4o 主渠道",
    groupKey: "channel:CH1",
    groupLabel: "主渠道",
    blocked: false,
    blockedReason: "",
    note: "",
    ...overrides,
  };
}

describe("combos-editor keys", () => {
  it("builds member and rule keys from an explicit sequence and reads the max sequence", () => {
    expect(nextKey(3)).toBe("member-3");
    expect(ruleKey(2)).toBe("rule-2");
    expect(maxKeySequence([])).toBe(0);
    expect(maxKeySequence(["member-1", "member-7", "rule-3"])).toBe(7);
    expect(maxKeySequence(["member-x", "orphan"])).toBe(0);
  });
});

describe("fromCombo", () => {
  it("returns an empty list when there is no combo", () => {
    expect(fromCombo(null)).toEqual([]);
    expect(fromCombo(undefined)).toEqual([]);
  });

  it("sorts members by priority descending and renumbers keys from one", () => {
    const source = combo({
      members: [
        comboMember({ id: "CT2", priority: 3 }),
        comboMember({ id: "CT1", priority: 1 }),
        comboMember({
          id: "CT4",
          priority: 2,
          target_type: "child_combo",
          channel_model_id: null,
          child_combo_id: "CB000000000000000000000002",
        }),
      ],
    });
    const result = fromCombo(source);
    expect(result.map((member) => member.priority)).toEqual([3, 2, 1]);
    expect(result.map((member) => member.key)).toEqual(["member-1", "member-2", "member-3"]);
    expect(result[1]).toMatchObject({ targetType: "child_combo", childComboId: "CB000000000000000000000002", channelModelId: "" });
    expect(result[0]).toMatchObject({ targetType: "channel_model", channelModelId: "CM000000000000000000000001", childComboId: "" });
  });
});

describe("descendantIds", () => {
  it("collects nested descendants and tolerates cycles", () => {
    const root = combo({ id: "root", members: [comboMember({ id: "m1", child_combo_id: "child" })] });
    const child = combo({ id: "child", members: [comboMember({ id: "m2", child_combo_id: "grand" })] });
    const grand = combo({ id: "grand", members: [comboMember({ id: "m3", child_combo_id: "root" })] });
    const ids = descendantIds([root, child, grand], "root");
    expect([...ids].sort()).toEqual(["child", "grand"]);
  });

  it("ignores missing combos and unknown roots", () => {
    expect(descendantIds([combo({ id: "solo" })], "root").size).toBe(0);
    expect(descendantIds([], "root").size).toBe(0);
  });
});

describe("buildCandidates", () => {
  it("maps catalog items to channel-model candidates grouped by channel", () => {
    const [candidateItem] = buildCandidates([catalogItem()], [], null, new Set());
    expect(candidateItem).toMatchObject({
      key: "channel_model:CM000000000000000000000001",
      targetType: "channel_model",
      targetId: "CM000000000000000000000001",
      label: "channel-a/A",
      groupKey: "channel:CH000000000000000000000001",
      groupLabel: "主渠道",
      blocked: false,
    });
    expect(candidateItem.searchText).toContain("gpt-4o");
    expect(candidateItem.searchText).toContain("主渠道");
  });

  it("excludes the current combo and blocked descendants, and marks disabled child combos", () => {
    const self = combo({ id: "self", name: "self-combo" });
    const blocked = combo({ id: "blocked", name: "blocked-child" });
    const disabled = combo({ id: "disabled", name: "paused-child", enabled: false });
    const ready = combo({ id: "ready", name: "ready-child" });
    const result = buildCandidates([], [self, blocked, disabled, ready], "self", new Set(["blocked"]));
    expect(result.map((item) => item.targetId).sort()).toEqual(["disabled", "ready"]);
    const paused = result.find((item) => item.targetId === "disabled");
    expect(paused).toMatchObject({ blocked: true, blockedReason: "已封禁", groupKey: "child_combo" });
    const readyItem = result.find((item) => item.targetId === "ready");
    expect(readyItem?.blocked).toBe(false);
  });

  it("keeps banned channel models in the candidate list and labels them", () => {
    const [banned] = buildCandidates([catalogItem({ enabled: false })], [], null, new Set());
    expect(banned).toMatchObject({ blocked: false, blockedReason: "", note: "已封禁" });
    const [healthy] = buildCandidates([catalogItem()], [], null, new Set());
    expect(healthy?.note).toBe("");
  });
});

describe("sortCandidatesByName", () => {
  it("sorts by label with numeric awareness", () => {
    const sorted = sortCandidatesByName([
      candidate({ label: "模型 10" }),
      candidate({ label: "模型 2" }),
      candidate({ label: "alpha" }),
    ]);
    expect(sorted.map((item) => item.label)).toEqual(["alpha", "模型 2", "模型 10"]);
  });

  it("does not mutate the input", () => {
    const input = [candidate({ label: "b" }), candidate({ label: "a" })];
    const sorted = sortCandidatesByName(input);
    expect(input.map((item) => item.label)).toEqual(["b", "a"]);
    expect(sorted.map((item) => item.label)).toEqual(["a", "b"]);
  });
});

describe("parseStatuses", () => {
  it("turns three-digit codes into numbers and keeps other tokens", () => {
    expect(parseStatuses("429, 500-599 403， timeout")).toEqual([429, "500-599", 403, "timeout"]);
  });

  it("returns an empty array for blank input", () => {
    expect(parseStatuses("   ")).toEqual([]);
  });
});
