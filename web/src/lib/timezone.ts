/**
 * IANA 时区工具：候选项、合法性校验与搜索匹配。
 *
 * 排期（ADR-070 §4.1）里时区是「墙钟窗口」的解释器，写错一个字母规则就永远不命中，
 * 所以界面不允许自由输入：只能从候选里选，提交前再校验一次。
 */

/** Intl 不可用时也有得选：覆盖常用地区，顺序即展示优先级。 */
const COMMON_TIMEZONES: readonly string[] = [
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Taipei",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Singapore",
  "Asia/Bangkok",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Paris",
  "Europe/Moscow",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Australia/Sydney",
  "Pacific/Auckland",
  "UTC",
];

let cachedOptions: string[] | null = null;

/** 候选时区：优先 Intl.supportedValuesOf("timeZone")，与常用区取并集后排序。 */
export function timezoneOptions(): string[] {
  if (cachedOptions) return cachedOptions;
  let supported: string[] = [];
  try {
    const supportedValuesOf = (
      Intl as unknown as { supportedValuesOf?: (key: "timeZone") => string[] }
    ).supportedValuesOf;
    if (typeof supportedValuesOf === "function") supported = supportedValuesOf("timeZone");
  } catch {
    supported = [];
  }
  const merged = new Set<string>([...COMMON_TIMEZONES, ...supported]);
  cachedOptions = [...merged].sort((left, right) => left.localeCompare(right, "en"));
  return cachedOptions;
}

/**
 * 是否为可用的 IANA 时区名。
 *
 * 只「Intl 能构造」还不够：Intl 大小写不敏感（asia/shanghai 也认），而后端按
 * zoneinfo 的规范名查表，小写或别名会直接不生效。所以还要求解析结果与原值一致，
 * 或原值本来就是候选里的规范名。
 */
export function isValidTimezone(timezone: string): boolean {
  const value = timezone.trim();
  if (!value) return false;
  try {
    const resolved = new Intl.DateTimeFormat("en-US", { timeZone: value }).resolvedOptions()
      .timeZone;
    return resolved === value || timezoneOptions().includes(value);
  } catch {
    return false;
  }
}

/** 按子串过滤（大小写不敏感）；空查询返回全部候选。 */
export function matchTimezones(
  query: string,
  options: readonly string[] = timezoneOptions(),
): string[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...options];
  return options.filter((option) => option.toLowerCase().includes(needle));
}
