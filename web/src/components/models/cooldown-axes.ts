import { isBrokenHealth } from "@/components/models/model-availability";
import { scheduleWindowActive } from "@/components/models/model-status";
import type { ChannelSchedule, HealthStatus } from "@/lib/channel-models";
import type { CatalogEntry } from "@/lib/playground";

/**
 * 一行当前踩着哪几条冷却轴。三轴互相独立，后端各走各的端点：
 * - 故障轴：熔断 open / 半开 half_open / 故障 fault，只有 POST /reset 能清；
 * - 人工定时轴：banned_until 在未来，批量端点 op=unban 能清；
 * - 排期轴：排期窗口命中（或后端给出 COOLDOWN_WINDOW），批量端点 op=schedule 写空规则集能清。
 */
export interface CooldownAxes {
  /** 故障轴：熔断 / 半开 / 故障。 */
  fault: boolean;
  /** 人工定时轴：定时冷却未到期。 */
  timed: boolean;
  /** 排期轴：命中排期窗口。 */
  schedule: boolean;
}

export interface CooldownAxisInput {
  /** 后端下发的熔断状态；判故障轴时复用 model-availability 的 isBrokenHealth，不另造字面量。 */
  health?: HealthStatus | null;
  /** 人工轴到期时刻（epoch ms）。 */
  bannedUntil?: number | null;
  /** 该行的排期（v1/v2 都可能）。 */
  schedule?: ChannelSchedule | null;
  /** 后端给出的冷却原因码。 */
  cooldownReason?: CatalogEntry["cooldown_reason"];
  /** 求值时刻。 */
  nowMs: number;
}

/** 判定一行当前在哪些轴上冷却；没有轴命中时三个标记全为 false。 */
export function cooldownAxes(input: CooldownAxisInput): CooldownAxes {
  return {
    fault: isBrokenHealth(input.health),
    timed: typeof input.bannedUntil === "number" && input.bannedUntil > input.nowMs,
    schedule:
      input.cooldownReason === "COOLDOWN_WINDOW" ||
      scheduleWindowActive(input.schedule, input.nowMs),
  };
}
