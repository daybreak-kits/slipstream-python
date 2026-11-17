import {
  INPUT_TYPE_OPTIONS,
  MODEL_CAPABILITY_OPTIONS,
  OUTPUT_TYPE_OPTIONS,
  type InputType,
  type ModelCapability,
  type ModelParameters,
  type OutputType,
} from "@/lib/channel-models";
import { formatCount, formatPricePer1m } from "@/lib/format";

/**
 * 组合模型的参数呈现（ADR-073 决策 8）。
 *
 * 组合是虚拟模型，参数按成员聚合，不合成单一值：
 * - 上限取成员最大值（厂商事实，没有人会把上限调大）；
 * - 能力位与模态取并集（不广告 ≠ 没有）；
 * - 价格按成员列出区间并注明「按实际选中成员计费」——不合成单一价格。
 */
export interface ComboParamSource {
  parameters: ModelParameters;
  input_price_per_1m: number;
  output_price_per_1m: number;
}

export interface ComboPriceRange {
  min: number;
  max: number;
}

export interface ComboParamUnion {
  memberCount: number;
  maxInputTokens: number;
  maxOutputTokens: number;
  inputTypes: InputType[];
  outputTypes: OutputType[];
  capabilities: ModelCapability[];
  /** 成员价格区间；没有成员时为 null。 */
  price: { input: ComboPriceRange; output: ComboPriceRange } | null;
}

const INPUT_ORDER = INPUT_TYPE_OPTIONS.map((option) => option.value);
const OUTPUT_ORDER = OUTPUT_TYPE_OPTIONS.map((option) => option.value);

function orderedUnion<T extends string>(values: readonly T[], order: readonly T[]): T[] {
  const present = new Set(values);
  const result = order.filter((item) => present.has(item));
  // 未知取值（后端以后扩了枚举）排在已知之后，不丢数据。
  for (const item of present) if (!result.includes(item)) result.push(item);
  return result;
}

export function aggregateComboParams(sources: readonly ComboParamSource[]): ComboParamUnion {
  const union: ComboParamUnion = {
    memberCount: sources.length,
    maxInputTokens: 0,
    maxOutputTokens: 0,
    inputTypes: [],
    outputTypes: [],
    capabilities: [],
    price: null,
  };
  if (sources.length === 0) {
    union.inputTypes = ["text"];
    union.outputTypes = ["text"];
    return union;
  }
  const inputTypes: InputType[] = [];
  const outputTypes: OutputType[] = [];
  const capabilities = new Set<ModelCapability>();
  let minInput = Number.POSITIVE_INFINITY;
  let maxInput = Number.NEGATIVE_INFINITY;
  let minOutput = Number.POSITIVE_INFINITY;
  let maxOutput = Number.NEGATIVE_INFINITY;
  for (const source of sources) {
    const parameters = source.parameters;
    union.maxInputTokens = Math.max(union.maxInputTokens, parameters.max_input_tokens || 0);
    union.maxOutputTokens = Math.max(union.maxOutputTokens, parameters.max_output_tokens || 0);
    inputTypes.push(...(parameters.input_types ?? []));
    outputTypes.push(...(parameters.output_types ?? []));
    for (const option of MODEL_CAPABILITY_OPTIONS) {
      if (parameters[option.value] === true) capabilities.add(option.value);
    }
    const inputPrice = Number.isFinite(source.input_price_per_1m) ? source.input_price_per_1m : 0;
    const outputPrice = Number.isFinite(source.output_price_per_1m) ? source.output_price_per_1m : 0;
    minInput = Math.min(minInput, inputPrice);
    maxInput = Math.max(maxInput, inputPrice);
    minOutput = Math.min(minOutput, outputPrice);
    maxOutput = Math.max(maxOutput, outputPrice);
  }
  union.inputTypes = orderedUnion(inputTypes, INPUT_ORDER);
  union.outputTypes = orderedUnion(outputTypes, OUTPUT_ORDER);
  if (union.inputTypes.length === 0) union.inputTypes = ["text"];
  if (union.outputTypes.length === 0) union.outputTypes = ["text"];
  union.capabilities = MODEL_CAPABILITY_OPTIONS.map((option) => option.value).filter((value) =>
    capabilities.has(value),
  );
  union.price = {
    input: { min: minInput, max: maxInput },
    output: { min: minOutput, max: maxOutput },
  };
  return union;
}

function rangeText(range: ComboPriceRange): string {
  if (range.min === range.max) return formatPricePer1m(range.min);
  // 区间下限 0 = 有成员未配置单价（ADR-082）：破折号与上界之间留空格，
  // 避免 "—–$3.00" 双破折号连排被读成乱码。
  if (range.min === 0) return formatPricePer1m(range.min) + " – " + formatPricePer1m(range.max);
  return formatPricePer1m(range.min) + "–" + formatPricePer1m(range.max);
}

/** 价格区间的单行文本：不合成单一价格，区间 + 「按成员计费」由调用方补。 */
export function comboPriceRangeText(union: ComboParamUnion): string | null {
  if (!union.price) return null;
  return "入 " + rangeText(union.price.input) + " / 出 " + rangeText(union.price.output);
}

/**
 * tooltip 里的参数并集逐行文本：
 * 上限（max）、能力位（并集）、模态（并集）、价格（区间，不合成）。
 */
export function comboParamLines(union: ComboParamUnion): string[] {
  const lines: string[] = [];
  lines.push(
    "输入上限 " +
      (union.maxInputTokens > 0 ? formatCount(union.maxInputTokens) + " 词元" : "未声明"),
  );
  lines.push(
    "输出上限 " +
      (union.maxOutputTokens > 0 ? formatCount(union.maxOutputTokens) + " 词元" : "未声明"),
  );
  const capabilityLabels = MODEL_CAPABILITY_OPTIONS.filter((option) =>
    union.capabilities.includes(option.value),
  ).map((option) => option.label);
  lines.push("能力 " + (capabilityLabels.length > 0 ? capabilityLabels.join("、") : "未声明"));
  lines.push("模态 " + inputTypeText(union.inputTypes) + " → " + outputTypeText(union.outputTypes));
  const price = comboPriceRangeText(union);
  lines.push(
    price
      ? "价格 " + price + "（按实际选中成员计费）"
      : "价格按实际选中成员计费",
  );
  return lines;
}

function inputTypeText(values: readonly InputType[]): string {
  return values
    .map((value) => INPUT_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value)
    .join("/");
}

function outputTypeText(values: readonly OutputType[]): string {
  return values
    .map((value) => OUTPUT_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value)
    .join("/");
}
