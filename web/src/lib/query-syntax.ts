/**
 * 查询语法参考的纯数据层（ADR-071）。
 *
 * 字段名与操作符必须和后端 `l03_domain/query_lang/fields.py` 对齐：那边是解析与编译时的
 * 真实白名单，这里只把它翻译成能读的说明。两边漂移时以 Python 侧为准；
 * `__tests__/query-syntax-help.test.tsx` 会盯住字段集合与分组。
 *
 * 这个文件刻意不 import 任何东西（也不依赖 React），这样既能被组件引用，也能被
 * `node --experimental-strip-types` 直接加载做逻辑校验。
 */

export type QueryFieldKind = "text" | "number" | "bool";

export interface QueryFieldSpec {
  /** 语法里的字段名（后端解析大小写不敏感，这里统一按小写写）。 */
  name: string;
  kind: QueryFieldKind;
  /** 一句话说明「查的是哪个语义」，而不是复述列名。 */
  description: string;
}

export const TEXT_OPERATORS = ["=", "==", "!=", "*="] as const;
export const ORDERED_OPERATORS = ["=", "==", "!=", ">", ">=", "<", "<="] as const;
export const BOOL_OPERATORS = ["==", "!="] as const;

export const QUERY_FIELDS: readonly QueryFieldSpec[] = [
  { name: "model", kind: "text", description: "请求模型名（客户端传入的 model）" },
  { name: "upstream", kind: "text", description: "上游模型名" },
  { name: "channel", kind: "text", description: "渠道名，或渠道 ID" },
  { name: "channel_model", kind: "text", description: "渠道模型 ID" },
  { name: "error", kind: "text", description: "错误码，如 NO_AVAILABLE_CHANNEL" },
  { name: "msg", kind: "text", description: "错误正文" },
  { name: "key", kind: "text", description: "密钥标识（label 或 ID）" },
  { name: "user", kind: "text", description: "用户 ID" },
  { name: "source", kind: "text", description: "来源：chat 或 probe" },
  { name: "status", kind: "number", description: "HTTP 状态码" },
  { name: "latency", kind: "number", description: "总耗时，如 3s、800ms" },
  { name: "ttft", kind: "number", description: "首字耗时" },
  { name: "ttat", kind: "number", description: "首答耗时（上游首个有效内容）" },
  { name: "tokens", kind: "number", description: "输入 + 输出 token 总数" },
  { name: "cost", kind: "number", description: "费用（美元）" },
  { name: "time", kind: "number", description: "请求时间，如 7d、2026-11-01" },
  { name: "stream", kind: "bool", description: "是否流式请求" },
];

export interface QueryFieldGroupSpec {
  id: QueryFieldKind;
  title: string;
  /** 该组共同的取值规则，避免每个字段重复一句。 */
  hint: string;
  operators: readonly string[];
  fields: QueryFieldSpec[];
}

export const QUERY_FIELD_GROUPS: readonly QueryFieldGroupSpec[] = [
  {
    id: "text",
    title: "文本",
    hint: "比较大小写不敏感；% 和 _ 是普通字符，不会变成通配符。",
    operators: TEXT_OPERATORS,
    fields: QUERY_FIELDS.filter((field) => field.kind === "text"),
  },
  {
    id: "number",
    title: "数值与时间",
    hint: "可以直接写毫秒数，也可以带 ms / s / d 单位；时间支持 7d 这类相对值与 2026-11-01 这类绝对日期。",
    operators: ORDERED_OPERATORS,
    fields: QUERY_FIELDS.filter((field) => field.kind === "number"),
  },
  {
    id: "bool",
    title: "布尔",
    hint: "只认 true / false，例如 stream==true。",
    operators: BOOL_OPERATORS,
    fields: QUERY_FIELDS.filter((field) => field.kind === "bool"),
  },
];

export interface QueryOperatorSpec {
  op: string;
  label: string;
  example: string;
}

export const QUERY_OPERATORS: readonly QueryOperatorSpec[] = [
  { op: "=", label: "包含", example: "model=flash" },
  { op: "==", label: "完全相等", example: "upstream==MiniMax-M3.1-Flash" },
  { op: "!=", label: "不相等（空值也算不相等）", example: "error!=NO_AVAILABLE_CHANNEL" },
  { op: "*=", label: "通配：* 任意长度，? 单个字符", example: "channel*=azure-*" },
  { op: ">", label: "大于", example: "latency>3s" },
  { op: ">=", label: "大于等于", example: "status>=400" },
  { op: "<", label: "小于", example: "cost<0.01" },
  { op: "<=", label: "小于等于", example: "tokens<=1024" },
];

export interface QueryExampleSpec {
  query: string;
  note: string;
}

export const QUERY_EXAMPLES: readonly QueryExampleSpec[] = [
  { query: "upstream==MiniMax-M3.1-Flash", note: "上游模型完全等于这个值" },
  { query: "status>=400 && model*=flash", note: "只看到失败的 flash 请求" },
  { query: "latency>3s", note: "总耗时超过 3 秒" },
  { query: "msg*=timeout", note: "错误正文里含 timeout" },
];

/** 「不写语法」这条路的口径：和后端 DEFAULT_SEARCH_COLUMNS 一致，用户不用记字段名。 */
export const QUERY_KEYWORD_HINT =
  "不写语法时按关键词搜索：请求编号、请求模型、上游模型、渠道、响应模型、密钥标识、用户、错误码与错误正文。";

export const QUERY_STRICT_HINT =
  "出现字段名、&&、||、! 或括号就按语法严格解析；写错时会在输入框下方提示出错列位置，不会悄悄退回关键词搜索。";

export const QUERY_QUOTE_HINT =
  "带空格的值用引号包住，例如 \"GPT 4o mini\"；&& 且、|| 或、! 取反，可用括号分组。";
