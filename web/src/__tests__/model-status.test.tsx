import { describe, expect, it } from "vitest";

import { statusView } from "@/components/models/model-status";

describe("model status view", () => {
  it("hints recent consecutive failures while the model is still available", () => {
    const view = statusView(
      {
        enabled: true,
        visible_status: "normal",
        consecutive_failures: 3,
        status_message: "上游波动",
      },
      "local",
    );
    expect(view.label).toBe("可用");
    expect(view.tone).toBe("ok");
    expect(view.message).toBe("近期连续失败 3 次（达到阈值后进入冷却） · 上游波动");
  });

  it("stays quiet when there are no consecutive failures", () => {
    const zero = statusView(
      { enabled: true, visible_status: "normal", consecutive_failures: 0, status_message: "" },
      "local",
    );
    expect(zero.message).toBe("");

    const missing = statusView({ enabled: true, visible_status: "normal" }, "utc");
    expect(missing.message).toBe("");
  });

  it("labels a manual cooldown as 冷却（定时）and keeps the source in the detail", () => {
    const view = statusView({ enabled: false, banned_until: Date.now() + 3_600_000 }, "local");
    expect(view.label).toBe("冷却（定时）");
    // 人工按持续时间设置的来源要留在小字里
    expect(view.message).toMatch(/^人工设置：到 \d{2}-\d{2} \d{2}:\d{2} 自动恢复$/);
  });

  it("labels an open breaker as 冷却（故障）and tells it apart from a fault", () => {
    const open = statusView(
      {
        enabled: true,
        visible_status: "open",
        cooldown_until: Date.now() + 3_600_000,
        consecutive_failures: 2,
      },
      "local",
    );
    expect(open.label).toContain("冷却（故障）");
    expect(open.message).toContain("连续失败 2 次");

    const fault = statusView(
      { enabled: true, visible_status: "fault", fault_until: Date.now() + 3_600_000 },
      "local",
    );
    expect(fault.label).toContain("冷却（故障）");
    // 故障档与熔断档同标签，但措辞能区分来源
    expect(fault.message).toBe("多次熔断已升级为故障冷却，到期自动恢复");
    expect(fault.message).not.toBe(open.message);
  });
});
