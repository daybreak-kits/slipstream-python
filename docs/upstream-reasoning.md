# 上游思考（reasoning / thinking）接入参考

状态：专项说明，2026-11-15。范围：上游厂商「怎么开思考、思考内容落在哪个字段、我们实测到了什么、模型试验页怎么标」。本文只做**协议差异与实测证据**，不重复机制正文：流式契约看 `ssot.md` §5.4，模型试验的布局与展示规则看 `ssot.md` §9.3，上游传输与 SSE 消费看 `upstream_transport.md`。与代码/测试冲突时以代码与测试为准，并回头修本文。

> 结论先行：网关**没有参数白名单**（`api/src/l05_service/llm/gateway.py:121-130` 只删除值为 `None` 的顶层键，其余原样下发），所以「开启思考的参数能不能到上游」取决于我们有没有放进请求体，而不是网关认不认识这个字段。真正的差异在厂商：参数名、优先级、默认开关、以及思考内容放在哪个字段（`reasoning_content` / thinking blocks / thought parts / 正文内联标签）。接收侧必须按固定顺序归一化，展示侧按「我们发了什么」打 badge，不能拿响应猜。

## 1. 接收侧归一化与判定顺序

流式与非流式共用的载体识别顺序（实现：`web/src/lib/playground.ts` 的 `extractThinkingFromMessage:298`、`splitContentParts:268`、`splitInlineThinking:232`；流式路径在 `sendChatStream:514`）：

1. `message.reasoning_content` / `delta.reasoning_content`；
2. `message.reasoning` / `delta.reasoning`；
3. `message.thinking` / `delta.thinking`（字符串形态）；
4. `content` 为 parts 数组时，`type == "thinking"`、`type == "reasoning"` 或 `thought === true` 的 part；
5. 正文内联标签 `＜think＞…＜/think＞`（`think` / `thinking` / `reasoning`，半角 `<>` 与全角 `＜＞` 都认）。流式跨 chunk 拼接后再切；末尾半个开标签先扣住、不显示。

只认第一个非空载体：`reasoning_content` 有内容时不再看 `content` parts 或内联标签。载体字段名不是交易协议的一部分，厂商可以直接新增；没列进上面的名字需要在接收侧补一条，否则会被当作正文显示。

## 2. 厂商 / 协议对照

| 厂商 / 协议 | 开启参数（按优先级） | 关闭方式 | 思考载体（非流式 / 流式） | 需额外字段透传 | 出处 | 我们的实测结论 |
|---|---|---|---|---|---|---|
| **DeepSeek**（OpenAI Chat 兼容） | ① `thinking: {type: "enabled"}` 优先；② `reasoning_effort: low \| high \| max`（`none` = 关闭）；③ **两者都不传 = 默认开启思考** | `thinking: {type: "disabled"}`；或 `reasoning_effort: "none"` | 非流式 `message.reasoning_content`；流式 `delta.reasoning_content`；正文始终在 `content`；usage 里 `completion_tokens_details.reasoning_tokens` 可能给出推理 token | 是：`thinking` 对象与 `reasoning_effort` 都是厂商扩展，需原样放进请求体 | [api-docs.deepseek.com/api/create-chat-completion](https://api-docs.deepseek.com/api/create-chat-completion) | 协议形状来自公开文档；经 VendorQ 渠道代理的 `deepseek-flash` 四组开关均无 `reasoning_content`（该模型无思考输出，见 §3） |
| **阿里 DashScope / Qwen**（OpenAI Chat 兼容 + 非标准扩展） | ① `enable_thinking: true`（混合思考；非 OpenAI 标准，SDK 走 extra_body，HTTP 直发放顶层）；② 部分模型支持 `reasoning_effort` 调强度；③ 存在「只能思考」的模型：始终返回 `reasoning_content`，`content` 可能很短 | `enable_thinking: false`；`reasoning_effort: "none"` 视模型 | 非流式 `message.reasoning_content`；流式 `delta.reasoning_content`；正文在 `content` | 是：`enable_thinking` 非标准字段，需原样透传（额外请求字段框/自定义参数） | [alibabacloud.com/help/en/model-studio/deep-thinking](https://www.alibabacloud.com/help/en/model-studio/deep-thinking) | **已实测（正对照）**：`vendorq/Qwen-Flash` 非流式 `message.reasoning_content` 155 字、`completion_tokens_details.reasoning_tokens = 96`；流式 `delta.reasoning_content` 28–51 字；baseline（不传开关）也返回 → 该模型默认开思考 |
| **Anthropic 风格**（thinking blocks） | `thinking: {type: "enabled", budget_tokens: N}`；要求 `max_tokens > budget_tokens`；默认关闭 | `thinking: {type: "disabled"}`，或不传 | 非流式 `content[]` 里 `{type: "thinking", thinking, signature}`（另有 `redacted_thinking`）；流式 `content_block_delta` 的 `{type: "thinking_delta", thinking}` + `signature_delta`；多轮需回传签名 | 是：`thinking` 对象、`budget_tokens`；原生端点还带 `anthropic-version` 头 | [docs.anthropic.com/en/docs/build-with-claude/extended-thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) | **未在我们的渠道实测**（协议形状来自公开文档）。仓库内 `anthropic_messages` 协议只用于探测/非聊天端点，不是聊天传输适配器（`upstream_transport.md` §2） |
| **Gemini 风格**（thinkingConfig / thought parts） | `generationConfig.thinkingConfig.includeThoughts: true`（`thinkingBudget` / `thinkingLevel` 按模型而异；部分模型默认开） | `includeThoughts: false`；或 `thinkingBudget: 0`（按模型支持情况） | 非流式 `candidates[].content.parts[]` 中 `part.thought === true` 的 `part.text`；流式同样是 parts 增量；OpenAI 兼容层通常映射成 `reasoning_content` | 是：`thinkingConfig` 嵌在 `generationConfig` 内；聚合层可能已做映射 | [ai.google.dev/gemini-api/docs/thinking](https://ai.google.dev/gemini-api/docs/thinking) | **未在我们的渠道实测**（协议形状来自公开文档）；仓库 `gemini` 协议同样只用于探测/非聊天端点 |
| **MiniMax**（OpenAI Chat 兼容） | 未确认（不猜） | 未确认 | 未确认 | 未知 | 待补厂商文档 | **未实测**：上游 429 Token Plan 配额耗尽，随后熔断 503（§3） |
| **SenseNova**（OpenAI Chat 兼容） | 未确认（不猜） | 未确认 | 未确认 | 未知 | 待补厂商文档 | **未实测**：上游 429 `inference exceeds tpm/rpm limit`，随后 503（§3） |

> 「未实测」不等于「无思考输出」。两者在 UI 上都可能显示「无思考块」，但前者的原因是没拿到响应、不能据此判断协议；写结论时必须区分。

## 3. 实测记录（2026-11-15）

### 3.1 方法

- 活服务 `http://127.0.0.1:3680`（API + Web 同进程），用本地管理员账号登录后用 `POST /api/keys` 建临时客户端密钥，直接打 `POST /v1/chat/completions`；测完 `DELETE /api/keys/{id}`，不留临时密钥。
- 模型用 `GET /api/catalog` 里的**直连条目**调用名（如 `vendorq/deepseek-flash`），不用自动组合，避免组合路由把请求分到别的渠道、掩盖渠道差异。
- 每个模型 × 四组开关（baseline / `reasoning_effort: "low"` / `thinking: {type: "enabled"}` / `enable_thinking: true`）× 流式 + 非流式；记录 HTTP 状态、`choices[0].message` 或 `delta` 的键、`reasoning_content` 长度与头部片段。
- `max_tokens` 统一取小值（64–128）以控制配额消耗；请求超时 180 s。

### 3.2 结果

| 渠道模型 | 流式 / 非流式 | 四组开关的结果 | 响应里的思考键 | 结论 |
|---|---|---|---|---|
| `vendorq/deepseek-flash` | 各 4 次，全部 200 | 全部 200 | `message` / `delta` 仅 `content, role`，无 `reasoning_content` | 该模型无思考输出（不是网关剥掉：同渠道 `Qwen-Flash` 能正常返回，见下行） |
| `vendorq/Qwen-Flash`（正对照） | 各 4 次，全部 200 | baseline 也返回 | 非流式 `message.reasoning_content`（155 字，`reasoning_tokens = 96`）；流式 `delta.reasoning_content`（28–51 字） | 链路正常，流式/非流式都能接收；`enable_thinking` 的流式样本未回 `reasoning_content`（上游行为，非我方剥离） |
| `minimaxi/MiniMax-M3.1-Flash` | 各 4 次 | 429 → 熔断后 503 | 无响应体 | 未实测：`UPSTREAM_RATE_LIMITED`，上游消息「已达到 Token Plan 用量上限：请升级 Token Plan 套餐或购买积分补充用量。(2056)」；配额耗尽后重试仍 429，随后 `NO_AVAILABLE_CHANNEL` |
| `sensenova/sensenova-6.8-flash` | 各 4 次 | 429 → 熔断后 503 | 无响应体 | 未实测：`UPSTREAM_RATE_LIMITED`，上游消息 `inference exceeds tpm/rpm limit`；随后 `NO_AVAILABLE_CHANNEL` |

### 3.3 实测边界与坑

- **熔断会放大观测**：一次 429 之后，同渠道模型在冷却期内直接返回 503 `NO_AVAILABLE_CHANNEL`。所以「后续请求 503」不能当成「该模型没有思考」，必须看第一次失败的错误码；探针结束后用 `POST /api/channel-models/{id}/reset` 复位，避免把熔断状态留给后面的人。
- **部分 429 是配额不是瞬时限流**：MiniMax 的 Token Plan、SenseNova 的 tpm/rpm 都属于上游账户侧限制，短暂等待没有意义；不要为了凑数据去降级 API 版本或换渠道，这会让「实测结论」失去可比性。
- **本轮没抓到 upstream 请求体**：`request_bodies` 正文日志默认 `off`，能证明「字段确实被发出」的证据是客户端请求体快照（§3.4）+ `gateway.py:121-130` 的原样下发路径；要抓上游请求体，先把正文日志开到 `all`（`ssot.md` §5.7/§8.6）。
- 思考 token 计入 completion，但不算「首答」：`ttfa_ms` 只认第一个非空 `content` 或 `tool_calls` delta，reasoning 不计（`ssot.md` §3.4）；只有 reasoning 的流式请求表现为「首答为空」，纯工具调用流则是 TTFA == TTFT。失败兜底的本地估算同样把思考块字符计入输出词元（ADR-075），避免「只见思考、输出为 0」的假账。

### 3.4 真实请求体快照（200，Qwen-Flash）

```json
{
  "model": "vendorq/Qwen-Flash",
  "messages": [
    {"role": "system", "content": "尽量简洁"},
    {"role": "user", "content": "用一句话说明天为什么是蓝的。"}
  ],
  "stream": false,
  "temperature": 0.6,
  "top_p": 0.9,
  "top_k": 20,
  "frequency_penalty": 0.1,
  "presence_penalty": 0.0,
  "seed": 42,
  "max_tokens": 120,
  "stop": ["###", "END"],
  "n": 1,
  "response_format": {"type": "text"},
  "logprobs": false,
  "reasoning_effort": "low",
  "reasoning_split": true
}
```

响应 200（节选）：`message.content` 与 `message.reasoning_content` 同时存在，`usage.completion_tokens_details.reasoning_tokens = 96`；`top_k` / `seed` / `stop` / `reasoning_effort` / 额外字段 `reasoning_split` 都没有被网关删除——与 `gateway.py:121-130` 的「只删 `None`」一致。`chat.py:662-675` 的 `_PARAM_KEYS` 只用于日志摘要键，不参与准入。

## 4. UI 如何判定 badge 与展示

判定规则（实现：`lib/playground.ts` 的 `describeThinking:332`）：**badge 由「我们这次发了什么」决定**，只有内联标签例外（它只能从响应里识别）。

| 我们发出的 / 收到的 | badge |
|---|---|
| 发 `reasoning_effort: <值>` | `reasoning_effort=<值>`（none / low / medium / high / max） |
| 发 `thinking: {type: "enabled"}` | `thinking=on` |
| 发 `enable_thinking: true` | `enable_thinking` |
| 什么都没发但收到思考（模型默认开） | `think` |
| 从正文内联 `＜think＞` 标签拆出 | `think 标签` |

展示规则（实现：`web/src/components/playground/message-list.tsx:65` 的 `ReasoningBlock`）：

- 有思考内容：默认折叠的「思考过程」按钮 + badge；展开后内部滚动（长思考不撑满气泡）。
- 没有思考内容：显式显示 muted 小字「**无思考块**」，不是留空白——避免用户以为页面把内容吞了。
- 内联 `＜think＞…＜/think＞` 会被拆成**独立的思考块**，正文里不再出现标签本身；流式跨 chunk 时先扣住半个开标签，闭合标签被拆到下一 chunk 时会整体重算思考内容（`lib/playground.ts:232`、`:631-648`）。
- 参数面板的「思考」下拉只影响**下一次**请求；发送后页面复位。候选值：不指定 / `reasoning_effort` 五档 / `thinking.enabled` / `enable_thinking`；厂商私有字段用「额外请求字段（JSON）」透传。

## 5. 接新模型 / 新渠道的检查清单

1. 查厂商文档确认四件事：**参数名与优先级**、**默认开还是关**、**载体字段名（非流式/流式分别是什么）**、**是否需要 budget/额外头/额外字段透传**。
2. 用直连条目（不是组合）打 `/v1/chat/completions`，四组开关 × 流式/非流式各跑一次；先确认能拿到 200，429/503 只记录错误码，不算结论。
3. 若上游把思考写进正文（内联标签、parts、thought 标记），确认接收侧归一化能拆；若是新的字段名，先补 `splitContentParts` / `extractThinkingFromMessage` 的识别，再谈展示。
4. 结论补进 §2 表与 §3 实测区；badge 只按「发了什么」判定，不按响应猜。
5. 收尾：删临时客户端密钥；对被探针打开熔断的渠道模型做 `reset`；正文日志如果临时开过，恢复原配置。

## 6. 相关文档

- `ssot.md` §5.4（流式契约：首字/首答、终态、usage 口径）、§5.2（上游传输：httpx / LiteLLM、请求体构造）、§5.7（日志与正文开关）、§9.3（模型试验 UX：参数面板、思考块、记录只落浏览器）、§16（文档治理）。
- `upstream_transport.md` §2（请求构造：`build_request_body` 删 `None`、强制改 `model/messages/stream`）、§4（流式消费：识别 `reasoning` 帧）、§5（HTTP 错误分类）。
- `GLOSSARY.md`「思考块 Thinking Block」；`INDEX.md`「模型试验」。
- 代码：`api/src/l05_service/llm/gateway.py:121-130`（原样下发）、`l05_service/chat.py:662-675`（`_PARAM_KEYS` 只做日志摘要）、`web/src/lib/playground.ts`（归一化 / badge / 流式）、`web/src/components/playground/message-list.tsx:65`（思考块渲染）。
