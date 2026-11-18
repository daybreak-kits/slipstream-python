/**
 * 全站展示格式化器的唯一事实源。
 * 页面只负责取数与排版，数值口径（小数位、单位、缺数据时的破折号）都在这里收拢。
 * 口径见 docs/ssot.md 9.2：K/M/B 两位小数、美元汇总两位/单笔 2–6 位、百分比一位小数、缺失值「—」。
 */

/** 大数字统一走自适应 K/M/B，保留两位小数，避免度量条忽宽忽窄。 */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return (value / 1_000_000_000).toFixed(2) + "B";
  if (abs >= 1_000_000) return (value / 1_000_000).toFixed(2) + "M";
  if (abs >= 1_000) return (value / 1_000).toFixed(2) + "K";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

/** 千分位计数：缺数据给破折号。 */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US");
}

/** 美元金额：两位小数；缺数据给破折号。 */
export function formatUsdCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return "$" + value.toFixed(2);
}

/**
 * 美元金额的统一口径（汇总、单笔、行内成本都用它）：
 * 一条规则：最少两位、最多六位小数，不按量级分档——避免同列里 $0.03 与 $0.008551 混排。
 * 缺数据给破折号。
 */
export function formatUsdFine(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return "$" + value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
}

/**
 * 「单价未配置」的展示判定。计费按渠道模型的每百万 token 单价算，单价为 0 时
 * 成本必然是 0；直接显示 $0.00 会被读成「这些调用是免费的」。这里只在展示层兜底：
 * 有词元消耗但成本为 0 ⇒ 判为未配置单价。不改接口字段，也不影响真实成本为 0 的空请求。
 */
export function isCostUnconfigured(
  cost: number | null | undefined,
  tokens: number | null | undefined,
): boolean {
  if (cost === null || cost === undefined || !Number.isFinite(cost) || cost > 0) return false;
  return (tokens ?? 0) > 0;
}

/** 成本展示：单价未配置时给明确标注，否则沿用 formatUsdFine 的口径。 */
export function formatCostOrUnconfigured(
  cost: number | null | undefined,
  tokens: number | null | undefined,
): string {
  if (isCostUnconfigured(cost, tokens)) return "未配置单价";
  return formatUsdFine(cost);
}

/**
 * 日志请求行的费用口径（列表行、聚合组、详情抽屉共用）：
 * - 失败请求从未完成一次可计费的响应，费用为 0 是「没有计费数据」而不是免费，
 *   数值位给「—」，不渲染 $0.00；计到真实费用（cost > 0）的失败请求仍显示真实值。
 * - 成功请求沿用 isCostUnconfigured 的口径（有词元消耗而费用为 0 ⇒ 未配置单价）。
 */
export function formatRequestCost(
  cost: number | null | undefined,
  tokens: number | null | undefined,
  failed: boolean,
): string {
  if (failed && (cost === null || cost === undefined || !Number.isFinite(cost) || cost <= 0)) {
    return "—";
  }
  return formatCostOrUnconfigured(cost, tokens);
}

/**
 * 每百万 token 的价格（输入价/输出价/缓存价）：两位起、最多四位，
 * 低价模型的单位价格会精确到 0.0001 美元；缺数据给破折号。
 * 单价 0 的正式语义是「未配置」（ADR-082：系统不支持显式标价 0 的免费模型），
 * 数值位给「—」而不渲染 $0.00——后者会被读成免费。
 */
export function formatPricePer1m(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return "—";
  return "$" + value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

/** 比率（0..1）转百分比；缺数据给破折号，是否隐藏 0 由调用方决定。 */
export function formatPercent(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** 缓存命中率：缺数据或为 0 都给破折号，全站统一一位小数。 */
export function formatCacheHitRate(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio) || ratio <= 0) return "—";
  return formatPercent(ratio, 1);
}

/** 行内展示沿用的短名，与缓存命中率同口径。 */
export function formatCachePercent(ratio: number | null | undefined): string {
  return formatCacheHitRate(ratio);
}

/**
 * 速率类数值（RPM/吞吐速率等）：缺数据或没有流量给破折号，避免 0.00 被读成有请求；
 * 超过千位走 K/M/B 缩写，否则固定两位小数。
 */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) return "—";
  if (Math.abs(value) >= 1_000) return formatCompact(value);
  return value.toFixed(2);
}

/** 流式吞吐：一位小数 + t/s；缺数据给破折号。 */
export function formatThroughput(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)} t/s`;
}

/**
 * 时间跨度：不足 1 小时按分钟、否则一位小数小时/天；缺数据给破折号。
 * 0 分钟表示「窗口里没有跨度」，与缺数据区分开。
 */
export function formatSpanMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return "—";
  if (minutes <= 0) return "0 分钟";
  if (minutes >= 1440) return `${(minutes / 1440).toFixed(1)} 天`;
  if (minutes >= 60) return `${(minutes / 60).toFixed(1)} 小时`;
  return `${Math.round(minutes)} 分钟`;
}
