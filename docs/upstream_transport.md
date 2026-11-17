# 上游传输契约：LiteLLM 与原生 httpx

状态：第一版（2026-11-14）。范围：`api/src/l05_service/llm/gateway.py` 中 `upstream_transport` 的两条实现路径，以及 `chat.py` 如何消费它们。本文先钉行为契约，再记录实现差异与验证缺口；A/B 数字待补。

> 结论先行：httpx 实现已经在 HEAD（提交 `2512152`）。默认传输已于 2026-11-14 由 ADR-065 **全量切到 `httpx`**（全局一条 env，保留 24h 回滚窗口）；协议对等性见 §11，A/B 数字与判定见 §12。

## 1. 入口与开关

- 配置：`l01_core/config.py:27` `upstream_transport: str = "httpx"`（2026-11-14 ADR-065 起的默认值，`gateway.py:778`/`:815` 的 fallback 同为 `"httpx"`）；合法值 `litellm` / `httpx`。**全局只有一个开关，没有渠道级 override**。
- 分派：`gateway.py:1037 complete()` 与 `gateway.py:1636 open_stream()` 都先调 `_use_litellm(channel)`（`gateway.py:813`）。
- `_use_litellm` 为真仅当：`upstream_transport == "litellm"` **且** `_litellm_api_base(channel)` 非 `None`（`gateway.py:820`）。后者把 `build_chat_url(...)` 的 `/chat/completions` 后缀去掉，得到 LiteLLM 需要的 `api_base`；拿不到 OpenAI chat URL 时返回 `None`，即使配置为 litellm 也回退 httpx。
- 生命周期：`startup()`（`gateway.py:772`）总是创建进程级 `_raw_client`（`_get_client()`，`gateway.py:643`）；仅 litellm 模式才额外导入 LiteLLM、建 `_litellm_client`、把 `litellm.aclient_session` 指过去并预热。httpx 模式不导入 LiteLLM。`shutdown()`（`gateway.py:797`）关闭 probe/litellm/raw 三个客户端。
- 连接池：`_new_client()`（`gateway.py:633`）统一使用 `max_connections=200`、`max_keepalive_connections=50`、`keepalive_expiry=30`、`follow_redirects=True`。httpx 模式复用同一个 `AsyncClient`，**不按请求建连**。

## 2. 请求构造（两臂共享）

- `build_headers(channel, request_id, auth_style)`（`gateway.py:99`）：固定 `Content-Type: application/json`、`Accept: application/json`；鉴权按渠道 auth_style：
  - `bearer` → `Authorization: Bearer <key>`
  - `api-key` → `api-key: <key>`
  - `x-api-key` → `x-api-key: <key>` + `anthropic-version: 2023-06-01`
  - 有 request_id 时追加 `x-request-id`。
- `build_request_body(channel, upstream_model, body)`（`gateway.py:121`）：删除值为 `None` 的键；强制 `model=upstream_model`、`messages=body.get("messages", [])`；当 `body.stream` 为真时设 `stream=True` 并 `stream_options.include_usage=True`（setdefault，尊重调用方已有值）。
- URL：两臂都走 `build_chat_url(channel, "chat/completions")` 的 OpenAI Chat Completions 形态。路由层只允许 `openai_chat` 渠道直连聊天（`routing.py:127/154/607/634`；非 OpenAI 协议报“本版本直连仅支持 OpenAI 兼容协议”）。`anthropic_messages` / `gemini` 在仓库中是合法渠道协议，但用于探测/embedding 等非聊天端点，**不是聊天传输的协议适配器**。因此本文的“行为对等”范围 = OpenAI 兼容 Chat Completions。

## 3. 非流式契约

入口 `_httpx_complete`（`gateway.py:1096`）与 `_litellm_complete`（`gateway.py:1054`）。

成功返回 `(payload: dict, usage: Usage, latency_ms: int, headers: dict)`，并满足：

1. HTTP 2xx 且 body 是 JSON 对象。
2. `meaningful_content(payload)`（`gateway.py:330`）为真：`choices[0].message` 的文本 content 非空，或 content 是非空 list（图像等非文本），或存在 `reasoning_content` / `reasoning`，或存在 `tool_calls`。否则视为伪成功，抛 `empty`。
3. usage 由 `extract_usage(payload)`（`gateway.py:371`）归一：`prompt_tokens` 取 `usage.prompt_tokens` 或 `input_tokens`；`completion_tokens` 取 `completion_tokens` 或 `output_tokens`；`total_tokens` 取 `total_tokens` 或 prompt+completion；`cached_tokens` 取 `usage.prompt_tokens_details.cached_tokens`、`usage.cached_tokens` 或 `prompt_cache_hit_tokens`。
4. 返回的 headers 是上游响应头，供响应头改写/回传使用。

失败分类（httpx）：

| 现象 | LLMError.kind | tier / retryable | message |
|---|---|---|---|
| `httpx.ConnectTimeout` / `ConnectError` | `connection` | transient / true | 无法连接上游 |
| `httpx.TimeoutException`（读写/池等） | `timeout` | transient / true | 上游在 `{total_s}` 秒内未返回响应 |
| 其他 `httpx.HTTPError` | `connection` | transient / true | 连接上游失败 |
| HTTP >= 400 | 见第 5 节 | 按状态 | 上游错误消息 |
| 2xx 但 body 非 JSON | `bad_response` | transient / true，raw=body | 上游返回了非 JSON 响应 |
| 2xx JSON 但内容为空 | `empty` | transient / true | 上游返回 200，但内容为空（伪成功） |

## 4. 流式契约

入口 `_httpx_open_stream`（`gateway.py:1719`）→ `UpstreamStream`（`gateway.py:1140`）；LiteLLM 对应 `_litellm_open_stream`（`gateway.py:1654`）→ `LiteLLMUpstreamStream`（`gateway.py:1405`）。

打开阶段（交给客户端之前）：
- 请求超时必须挂在 request 上：`client.build_request(..., timeout=...)` 后 `client.send(request, stream=True)`；`send()` 不接受 timeout（已有回归测试覆盖）。
- 连接/超时/HTTPError 与非流式同表；HTTP >= 400 先 `aread()` 取错误体、`aclose()`，再 `classify_http_error`。
- 构造 `UpstreamStream` 后立即 `prime()`，并受 `upstream_first_byte_timeout_s` 约束（默认 30s）；首字节超时抛 `timeout`，消息为“上游在 `{first_byte_s}` 秒内未返回首字节内容”。
- prime 成功后才把流交给客户端；**prime 之后不再 failover**（`start_direct_stream`，`chat.py:1194`）。因此流式故障转移只覆盖“首字节前”。

消费阶段（`UpstreamStream.iter_sse`，ADR-078）：
- 三条转发路径，**转发粒度统一为网络块**（`aiter_bytes()` 无参——带 chunk_size 会让 httpx ByteChunker 吞掉流中途出错时的未满块前缀）：
  - **直通块**：无 `sse_patch` 且 `public_name == upstream_model`，字节原样转发（`_passthrough_blocks`）；
  - **改写块**（ADR-078）：块内逐行跟踪 + 改写（needle 字节替换优先，json 解析兜底）后重组（`_rewrite_blocks`）——直连名调用恒走此路径；
  - **逐行**：仅测试替身（无 `aiter_bytes`）退化（`aiter_lines`）。
- `_next_line` 每次最多等 `read_timeout_s`（逐块读空闲看门狗）。流式**无总闸**（ADR-075）：观察器没有 deadline，等多久由客户端决定。
- 识别并跟踪：`[DONE]`、`choices[0].delta` 的 content/reasoning/tool_calls、`finish_reason`、SSE `usage`、`response_id`、首内容/首答案/末 delta、`content_chars` 与 preview。跟踪以帧为单位照跑（bytes 直进 `jsonfast.loads`，省逐帧 decode）；盲转会破坏伪成功/断流/记账语义。
- 输出改写：needle 字节替换（`'"model":"<upstream>"' → '"model":"<public>"'`，未命中回退 `rewrite_line` 解析路径）合并 `sse_patch`；非 data 行原样透传；改写后的帧保留上游 JSON 原格式（不再由网关重排分隔符）。
- prime 预读阶段若先遇到 `[DONE]` 或结束：无内容 → `empty`；有内容则保留，后续继续 yield。
- 首字节之后：读超时/连接断开 → `stream_abort`（retryable=false），不再换渠道；正常结束但没有 finish_reason / `[DONE]` 完成标记 → `stream_abort`；结束且无任何内容 → `empty`。
- `close()` 关闭 line iterator 与 response；客户端取消时 `events()` 标记 cancelled 并 re-raise，不写错误。

流式 usage：
- 只有 `stream.usage.total_tokens` 存在且 `stream.usage_from_upstream` 为真时，`chat.py` 才采用上游 usage；否则用 `billing.estimate_usage_from_chars(messages, content_chars + reasoning_chars)`（ADR-075：失败兜底估算把已转发的思考块计入输出）。
- `LiteLLMUpstreamStream.usage` 在最终 chunk 缺失 usage 时，回落到 `_UsageObserver` 从原始 SSE tap 到的 usage；httpx 臂必须做到等价。

## 5. HTTP 错误分类

`classify_http_error(status, text, headers)`（`gateway.py:216`）：

| 上游状态 | kind | tier | retryable | 额外 |
|---|---|---|---|---|
| 429 且响应含 quota/额度类标记 | `rate_limit` | permanent | false | — |
| 429 其他 | `rate_limit` | transient | true | 解析 `Retry-After` → `retry_after_ms` |
| 401 / 403 | `auth` | permanent | false | — |
| 402 | `rate_limit` | permanent | false | — |
| 404 | `not_found` | permanent | false | — |
| 其他 4xx | `bad_request` | client | false | — |
| 其他（含 5xx） | `upstream` | transient | true | — |

错误消息优先取 JSON `error.message` / `error.code` / `message`，再经 `_sanitize_client_message`：截断 200 字符、掩码 Bearer / api-key / `sk-`、去控制字符。

`chat.py:115 map_error` 到 OpenAI 错误码：

| LLMError | HTTP | code |
|---|---|---|
| permanent + auth | 502 | `UPSTREAM_AUTH_FAILED` |
| permanent + rate_limit | 503 | `UPSTREAM_QUOTA_EXHAUSTED` |
| permanent + not_found | 404 | `MODEL_NOT_FOUND` |
| timeout | 504 | `UPSTREAM_TIMEOUT` |
| rate_limit（transient） | 429 | `UPSTREAM_RATE_LIMITED` |
| empty | 502 | `EMPTY_RESPONSE` |
| bad_response | 502 | `BAD_UPSTREAM_RESPONSE` |
| bad_request | 400 | `INVALID_REQUEST` |
| not_found | 404 | `MODEL_NOT_FOUND` |
| stream_abort | 502 | `STREAM_ABORTED` |
| 其他（connection/upstream） | 502 | `UPSTREAM_ERROR` |

`error_details` 只输出 `retry_after_s`（由 `retry_after_ms` 四舍五入，至少 1s）。

## 6. 超时与重试预算

- 单次调用超时来自 `_RoundAttempts`（`chat.py:244`）：`timeout_s = request_timeout_s`（默认 120s）；每次候选调用前计算 `captured["timeout_s"] = max(1.0, min(request_timeout_s, budget.remaining_s))`（`chat.py:363`），再传给 `complete()` / `open_stream()`。
- 预算与重试次数共同受 `request_timeout_s` 约束；一次失败是否换候选/重试由 `error.tier` 与 `error.retryable` 决定；全部失败抛 `FailoverFailed`，由 `map_error` 转成客户端错误。
- httpx 的分段超时：非流式用 `_client_timeout(settings, total_s)`（connect 限制为 `min(upstream_connect_timeout_s, total_s)`，read/write/pool 用剩余预算）；流式用 `_stream_client_timeout`（ADR-075）：`total=None`（无总闸），只剩 connect / 首字节 `upstream_first_byte_timeout_s`（`wait_for` 包住 priming）/ 读空闲 `upstream_read_timeout_s` 三道看门狗。
- 流式一旦 prime 成功就不再 failover；读阶段超时/断开是 `stream_abort`，不重试。流没有总时长上限（ADR-075），只有上述逐块看门狗。

## 7. 协议覆盖

- 聊天直连：仅 `openai_chat`。`anthropic_messages` 与 `gemini` 是合法渠道协议，但路由层拒绝其聊天请求（`routing.py:154-157/634-637`），所以两条传输实现都不做 Anthropic/Gemini 的请求/SSE 形状转换。
- `anthropic-version: 2023-06-01` 头仍会在 `x-api-key` 认证风格下发送；它只是 OpenAI 兼容代理的鉴权头兼容，不构成 Anthropic Messages API 适配。
- 探测/embedding 等非聊天路径不在本文范围。

## 8. 当前实现差异与验证缺口

已确认的差异/风险（待测试与 A/B 证实或证伪）：
1. LiteLLM 会自己处理部分异常、重试与 header 大小写；httpx 臂是显式映射。需要覆盖：200 非 JSON、200 空内容、4xx/5xx、429 带/不带 `Retry-After`、连接失败、首字节超时、读超时、客户端取消。
2. LiteLLM 的 SSE 解析对多行 `data:`、非标准 keep-alive、空行更宽容；httpx 臂按行解析，需要验证：`[DONE]` 前无内容、`[DONE]` 后多余行、usage 在最终 chunk、finish_reason 缺失、`data:` 无空格、CRLF。
3. LiteLLM 在 2xx 但 body 非 JSON 时可能直接抛解析异常；httpx 已改为 `bad_response`（提交 `2512152`），需回归。
4. 流式错误路径：LiteLLM 流内 error payload 与 httpx 的 `stream_abort` 语义需对照；目前 httpx 在首字节后不重试，LiteLLM 是否相同需确认。
5. 共享连接池是否真的复用：需要断言同一 `AsyncClient`、keep-alive 生效，且不随请求增长连接数。

测试现状（2026-11-14 更新）：
- `api/tests/test_httpx_transport.py` 已扩成矩阵，全部由 `httpx.MockTransport` 驱动，不起端口、不访问网络；矩阵与方法见 §11.5。
- 回归锚点 `test_httpx_open_stream_sets_timeout_on_request`（断言 timeout 挂到 request 上）保留。
- 仍未覆盖：连接池连接数增长、keep-alive 生效的端到端证据——两者需要真实 socket，超出 MockTransport 的能力。

## 9. 开关与回滚

- 默认值（2026-11-14，ADR-065）：`upstream_transport = "httpx"`——`l01_core/config.py:27`、`gateway.py:778`/`:815` 的 fallback、`api/.env.example:15` 三处一致。
- 粒度：**全局一条 env，没有渠道级开关**。全仓只有两处读取 `upstream_transport`（`gateway.py:778` 的 `_use_litellm`、`:815` 的 `startup`）；渠道表 (`l04_repo/orm.py`) 无 transport 列，也没有 per-channel 覆盖。切一次即对所有渠道生效。
- 回滚（24h 窗口内）：`UPSTREAM_TRANSPORT=litellm`，重启；两条实现共享同一套请求构造与错误类型，回滚不需要迁移数据。回滚后 LiteLLM 才会被 import 并预热——`startup()` 只在 `transport == "litellm"` 时导入 LiteLLM、建 `_litellm_client`，httpx 默认下不加载该依赖。
- 历史：本文件第一版（2026-11-14 上午）记录的是「默认仍为 litellm、本任务不改默认值」，切换决策在当天 A/B 完成后由用户拍板，过程保留在 §12.5。

## 10. 待补

- 第 2/3 步完成后补：httpx 实现模块化位置、测试矩阵结果。
- 第 4 步完成后补：litellm vs httpx A/B 表（非流式 C=16 ms/请求、流式饱和帧/s、p50/p95/p99、CPU）、结论与已知限制。

## 11. 协议对等性判定（默认传输切到 httpx 之后）

判定时间 2026-11-14，基线 master @ c9fccfe。结论先行：**chat 路径无功能损失**——协议层面「litellm 臂支持而 httpx 臂不支持」的渠道类型为「无」；探测与 embedding 路径与开关完全正交。剩下的差异都在「上游响应处理口径」，逐条列在 11.4。

### 11.1 开关判据：全局开关 × 渠道 URL 能力（无渠道级覆盖）

- 粒度先钉死：`upstream_transport` 是**全局**设置，全仓只有 `gateway.py:778` 与 `:815` 两处读取，没有渠道级 override。下面所说的「单渠道」只指判据里对每个渠道 URL 能力的检查，不是可以给某个渠道单独开 litellm。

- `_use_litellm(channel)`（gateway.py:813-817）= `str(settings.upstream_transport).lower() == "litellm"` **且** `_litellm_api_base(channel) is not None`。
- `_litellm_api_base`（gateway.py:820-832）取 `build_chat_url(connection_url)` 并去掉尾部 `/chat/completions`；当 URL 不是 chat completions 端点时（例如尾随 `#` 的精确端点 `/v1/responses#`）返回 `None` → 即使配置为 litellm 也走 httpx 臂。两条臂因此可以长期共存，回滚只需改配置（§9）。
- 回归用例：`test_use_litellm_gate_states`（覆盖 litellm→True、httpx→False、非 chat 端点→None/False 三态）。

### 11.2 chat 路径：逐协议对照

| 渠道协议 | litellm 臂 | httpx 臂 | 是否有差异 |
|---|---|---|---|
| openai_chat（OpenAI 兼容） | 支持：`model="openai/<upstream_model>"` + `api_base`（gateway.py:1016-1028） | 支持：直发 `build_chat_url`（gateway.py:1106-1115） | 无协议差异 |
| anthropic_messages | 进不了 chat 候选：路由只放行 `channel.protocol == openai_chat`（routing.py:127、:172、:222、:607、:634），直连另在 routing.py:154-157 与 :634-637 拒绝 | 同左 | 无差异（两臂都不服务） |
| gemini | 同上 | 同上 | 无差异（两臂都不服务） |
| azure | 代码里不存在该协议值（全仓 grep `azure` 0 命中），它不是一种渠道协议；若以 openai_chat + 自定义 connection_url 接入，两臂共用 `build_chat_url` | 同左 | 无差异（Azure 的 `/openai/deployments/...` 路径两臂都不支持，属既有缺口，非本次切换引入） |
| 其他 OpenAI 兼容代理 | 与 openai_chat 同 | 与 openai_chat 同 | 无差异 |

结论：**不存在「litellm 臂支持而 httpx 臂不支持」的协议/渠道类型**。根因是三处共享实现：`prepare_request`（rewrite.py:175，两臂共用同一 body/headers）、`build_request_body`（gateway.py:121-130）、`build_chat_url`（urls.py:33-42）。LiteLLM 臂固定使用 `openai/` 前缀加 `api_base`（gateway.py:1016-1028），也就是它同样只走 OpenAI 适配器，并不会替 Anthropic/Gemini/Azure 补协议适配。

### 11.3 探测与 embedding 路径：与开关正交

- 探测：probe.py 只调用 `upstream_probe_get` / `upstream_probe_post`（probe.py:303、:615、:861、:943、:1456 → gateway.py:1845、:1865），最终走 `_probe_request`（gateway.py:1815-1843）与 `_get_probe_client()`（gateway.py:651-670）自建的 AsyncClient；不读 `upstream_transport`，也不经过 `_get_client()`。
- embedding：gateway.py 内没有任何 embedding 分支（grep `embedding` 0 命中）；唯一的 embedding 请求在探测侧（probe.py:800-811，走 `upstream_probe_post`）。
- 因此把默认传输切成 httpx **不影响**渠道探测、模型发现、连通性测试与 embedding 探测。

### 11.4 非协议层面的真实差异（切默认前要知道）

1. usage 缺失：两条路径现在都遵循同一条事实——**只信上游真实 usage，否则估算并标记**。
   - 流式（原本就正确）：`chat.py:1018-1029` 的 `_usage()` 只在 `stream.usage.total_tokens` 与 `stream.usage_from_upstream` 同时为真时采信上游值，否则走 `billing.estimate_usage_from_chars(messages, content_chars)` 并标 `usage_estimated=True`；两臂都设置 `usage_from_upstream`（`gateway.py:1236` litellm 臂、`gateway.py:1332` httpx 臂），因此 LiteLLM 用 tokenizer 补出来的 usage 不会被当成真实值。
   - 非流式（此前是缺口，已修）：litellm 臂由 LiteLLM 的 tokenizer 补 usage，`extract_usage` 会把它当成上游真实值；httpx 臂在上游不返回 usage 时得到全 0，同样被当成真实值 ⇒ 词元与成本被少算、缓存命中率记 0。修法：`chat.py` 非流式 `invoke()` 内 `if usage.total_tokens == 0: usage = billing.estimate_usage(_messages(body), _completion_text(payload)); usage_estimated = True`，该标记随 `_write_log(usage_estimated=...)` 落库；上游给了 usage 时原值不动、标记 False（防过度估算）。流式路径无改动。
   - 估算口径：`billing.py:57-69`，约 4 字符/token；中文实际约 1.5–2 字符/token，故估算误差随语言变化，量级 ±30%。
   - 口径隔离：估算行在聚合与看板里已被 `usage_estimated` 过滤（`aggregates.py:79/109`、`timeseries.py:143`），不会与真实用量混算命中率。
   - 残余风险：估算值不是真实用量，只保证「不再静默记 0」；它带标记，可被审计与单独统计。
2. 响应字段：litellm 臂返回 ModelResponse `model_dump()` 的结果（`_model_payload` / `_normalise_payload`，gateway.py:885-901），SDK 会丢弃不认识的字段；httpx 臂原样返回上游 JSON → 就「未知字段透传」而言 httpx 臂更强。
3. 响应头大小写：litellm 臂从 `_hidden_params` 取上游原始大小写的头，httpx 臂 `dict(response.headers)` 的键名被归一为小写（回归用例断言的是小写 `x-upstream-request-id`）。
   - 审计结论（2026-11-14 全仓搜索）：**没有代码按精确大小写读上游响应头，不需要改实现**。上游头只进日志链路：`complete()` 返回的 headers / `stream.upstream_headers` → `chat.py:745`、`chat.py:1111/1125/1143/1244` → `_attempt(response_headers=...)` → `_log_headers`（`chat.py:671`）→ `redact_headers` → `response_headers_json`（`chat.py:515`）→ `logs.py:46/102` → 前端 `log-detail-drawer.tsx:298`（按键值渲染，不认名字）。
   - 唯一的「按名字取头」是限流解析 `parse_retry_after_ms`（`gateway.py:173-213`），它拿到的是 `httpx.Headers`（大小写不敏感），两条臂都安全。
   - 脱敏也安全：`is_sensitive_key`（`l01_core/logging.py:54-64`）先 `lower()`，小写键照样命中 authorization / api-key。
   - 约束：`rewrite.py:256-289` 的 `response_headers` 规则作用于**网关自己返回给客户端的**响应头，不是上游头，与本差异无关。若将来要新增「按上游头名匹配」的规则或指标，必须在取值处做大小写不敏感访问（或改用 `response.headers.raw` 保留原始大小写），不能依赖字面大小写。
4. 200 非 JSON 的识别：litellm 臂依赖异常文本标记 `_NON_JSON_UPSTREAM_MARKER`（gateway.py:947-954），LiteLLM 改文案即退化为 502 UPSTREAM_ERROR；httpx 臂直接 `response.json()` 失败 → 稳定 `bad_response` → 502 BAD_UPSTREAM_RESPONSE。
5. 流式截断判定：LiteLLM 会对截断连接合成 `finish_reason="stop"`（gateway.py:408-411 注释），httpx 臂不合成，缺 `[DONE]` 且无 finish_reason 即判 `stream_abort`（gateway.py:1349-1350）→ httpx 臂更严格，但前提是上游确实发完成标记。

计费本身无差异：LiteLLM 的 cost calc 默认被 stub（gateway.py:677-712），价格一律来自 DB 行。

### 11.5 回归矩阵与跑测结果

- 文件：`api/tests/test_httpx_transport.py`（MockTransport 驱动，无端口、无网络）。
- 命令：`cd api && PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_httpx_transport.py`
- 结果（2026-11-14，分类修复后复跑）：全绿。按文档纪律，本文件不复述用例计数与通过数；回归以本节命令退出码为 0 为准。
- 覆盖点：非流式 —— 200 原样返回、usage 归一（含 cached tokens 与 input/output 回退）、响应头透传、请求体改写、401/404/429/429+配额字样/500、200 非 JSON、200 空内容、connect 超时、read 超时、连接失败、请求取消；流式 —— 多帧 + usage + `[DONE]`、4xx、5xx、200 非 SSE body → bad_response、SSE 无帧空流 → empty、非 SSE content-type 但确有 data 帧仍正常流式、首字节前读超时、仅 `[DONE]`、流内 error → stream_abort、首字节后读超时 → stream_abort、缺完成标记 → stream_abort、流中取消；另加分派判据与连接复用两条。
- 已修复（2026-11-14，仅改 `gateway.py` 的 `UpstreamStream.prime` 终态分支）：流式请求拿到 200 但上游不守 SSE 契约时不再报 504。
  - 200 + content-type 非 `text/event-stream` + 一个 data 帧都没有 → `bad_response` → 502 BAD_UPSTREAM_RESPONSE；
  - 200 + SSE，但 EOF 时没有任何内容 → `empty` → 502 EMPTY_RESPONSE（只有 `[DONE]` 的情形沿用原有 empty 分支）；
  - 真正的「连接建立但首字节迟迟不来」仍由读超时异常路径给出 `timeout` → 504；判定只用 `content-type`、是否收到过 data 行、是否 EOF 三样已有信息，没有新增等待逻辑。
  - 响应头键名小写化（11.4-3）经审计确认无需修改。

## 12. A/B 实测：httpx 是否值得切默认

数据来源：`.scratch/bench2/results/transport/transport_ab_pass2.json`（采信这一份；pass1 长流格样本不足，仅作趋势对照）与 `transport_ab_summary.md`；一键复跑 `runner/transport_ab.py --preflight --duration-s 30 --warmup-s 15 --port-base 3732 --mock-port 3997 --tag pass3`。只测 openai_chat 协议、单上游 mock、单机同进程。

### 12.1 工况三参数（结论只在这组参数下成立）

| 参数 | 短流格 | 长流格 |
|---|---|---|
| 并发 C | 16 | 4 / 16 |
| 流长 | 8 帧/流（delay 0） | 2000 帧/流（delay 1 ms、frame_pad 200、每 10 帧一个 reasoning 帧） |
| 上游节奏 | tier 50 ms | tier 50 ms |
| 窗口 | warmup 15 s + 测量 30 s、1 rep、两臂交错（cells 1-2 litellm→httpx，cells 3-4 反向） | 同左 |

长流格 2000 帧/流在 Windows 上约 32 s 跑完，30 s 窗口内只有少数流结束，吞吐一律以 frames/s 与 frames/core 计，不要求流结束（这是有意的）。

### 12.2 臂隔离自证（不是同一份代码跑两次）

网关内计数器（结束时）：litellm 网关 litellm_complete=1124、litellm_open_stream=518、httpx_*=0；httpx 网关 httpx_complete=1537、httpx_open_stream=1352、litellm_*=0。两臂环境变量只差 `UPSTREAM_TRANSPORT`（litellm / httpx），其余（mock、tier、模型、并发、时长）完全相同。

### 12.3 四组数字（比值 = httpx/litellm，<1 表示 httpx 更省）

| 格 | 指标 | litellm | httpx | 比值 |
|---|---|---|---|---|
| 非流式 C=16 | msCPU/req | 50.01 | 36.80 | 0.736（省 26%） |
| 非流式 C=16 | rps | 22.42 | 30.32 | 1.35× |
| 短流 C=16 | msCPU/帧 | 8.654 | 3.844 | 0.444（省 56%） |
| 短流 C=16 | 帧/s | 114.8 | 311.6 | 2.71× |
| 短流 C=16 | p99 ms | 2484.7 | 899.2 | −1585 |
| 长流 C=4 | msCPU/帧 | 1.713 | 0.978 | 0.571（省 43%） |
| 长流 C=4 | 帧/s | 559.3 | 967.7 | 1.73× |
| 长流 C=16 | msCPU/帧 | 1.037 | 0.496 | 0.478（省 52%） |
| 长流 C=16 | 帧/s | 939.7 | 1829.8 | 1.95× |

### 12.4 两趟一致性与污染标注

- 非流式 C=16 跨趟极稳：msCPU/req 比 0.736（pass2）/ 0.734（pass1），rps 比 1.352 / 1.487。
- 污染标注：非流式 litellm 格 pre_cpu 97.8%（pass1 98.5%，>90% 标 CONTAM）——负载最重的一臂恰好是更慢的一臂，方向不会因污染被「利好」；短流格同样是 litellm 更重（58.1% vs 33.8%）且更慢。
- 抗污染证据（长流两格负载劣势方向相反）：C=4 时 httpx 格 pre_cpu 78.3% vs litellm 33.5%，httpx 仍快 1.73×；C=16 反过来（litellm 15.2% vs httpx 52.5%），httpx 仍快 1.95×。最不利的负载分配下结论不翻转。
- 测量期间系统负载 15%–98%（其他 agent 的 tsc/uvicorn/DSH 进程），绝对值不可跨时刻比较，只采信同一对格子内的比值。

### 12.5 判定：httpx 生产形态明确占优（当时的建议：未复跑前不切默认）

- httpx 在生产形态（长流 2000 帧 + tier 50 ms + C=4/16）下省 26%–55% CPU、吞吐 1.35×–2.71×，两趟方向一致 → **明确占优**。
- 但这是 1 rep、无置信区间、系统负载 15%–98% 的窗口里测的；按 PERFORMANCE_REPORT.md §1.6 的报数规矩，「同一结论至少两趟交错」只完成一半（交错已做、复跑未做）。**要切默认，必须先在一个安静窗口把长流 C=4 / C=16 复跑 ≥2 rep**，确认方向一致且比值仍显著。
- 当时的处理（2026-11-14 上午）：维持默认 `litellm`、`UPSTREAM_TRANSPORT=httpx` 仅作 escape hatch，切默认要等复跑。**当天晚些时候该决策被改判**：用户拍板全量切 httpx（ADR-065，全局一条 env + 24h 回滚窗口），复跑由「切前门槛」转为「切后监控项」；§12.3 的数字仍是切换依据，不是切后验收。

### 12.6 2.84× 不能等同生产收益

`.scratch/bench2/analysis/streaming_cpu.md:22-29` 的 2.84× 只成立于「饱和 mock（delay=0）+ 20000 帧/流 + C=8」——网关是唯一瓶颈的人造态。同一张表里排名会翻转：C=1 时 litellm 反而更快（1926.5 vs 1675.0 帧/s；0.475 vs 0.577 ms/帧），1 ms/帧节流 + C=8 时 httpx 更差（1370.7 vs 1516.8 帧/s；0.720 vs 0.615 ms/帧）。机制是每帧固定开销与每请求固定开销的占比之争，详见 `docs/PERFORMANCE_REPORT.md` §1.6（工况翻转案例 + 报数规矩）：**方向可以带走，倍数不能带走**。生产可引用的是 §12.3 的 0.44–0.74 CPU 比与 1.35×–2.71× 吞吐，且同样只在本机工况下成立。

### 12.7 失效条件

单机同进程、单上游 mock、无 h2 上游、无真实网络 RTT、无多副本；只测了 openai_chat 协议。跨机器/跨时段不可比，只可复用「同轮配对比值」。

## §13 切换与回滚 runbook（litellm -> httpx）

> 本节由 task-57 写入。§12 是 A/B 数字与判定；本节是「切流怎么做、切完怎么看、坏了怎么退」。切流动作由用户执行（改 env + 重启 3680 网关），本文与采样器都不碰生产进程。

### 13.0 切换时的实际状态（2026-11-14 20:20 核对）

- **代码默认值已经改成 httpx**：`l01_core/config.py:27` `upstream_transport: str = "httpx"`（ADR-065），`gateway.py:778`/`:815` 的 fallback 同为 httpx，`.env.example:15` 亦为 httpx。该文件修改时间 **19:55:47**。
- **运行中的 3680 实例仍然是 litellm 臂**：网关进程启动于 **19:34:48**（pid 22948 父、10464 子），早于上述代码改动；且该进程环境里 `UPSTREAM_TRANSPORT` **未设置**（`psutil.Process(22948).environ()` 实测），因此它加载的是启动时的旧默认值 `litellm`。
- 也就是说：**切流实际只差一次重启**。本文 §13.3 采到的 30 分钟基线是货真价实的「切前（litellm）基线」。
- 回滚 = 一条 env：`UPSTREAM_TRANSPORT=litellm` + 重启；或把 config 默认值改回 `litellm` + 重启。**二选一，不要同时改**（ADR-065 §后果 第 1 条：显式 env 优先于默认值，遗留一行会让整台机器继续走旧臂）。

### 13.1 四个判定指标与取数位置

| # | 指标 | 采样器字段 | 底层来源 | 可比性 |
|---|---|---|---|---|
| 1 | 5xx 率 | `error_5xx_rate` | request_logs.http_status >= 500 / 窗口内新增行数 | 最强（结构性错误） |
| 2 | P95 首字 | `p95_ttft_ms` | request_logs.ttft_ms（流式有值，非流式 NULL） | 弱（上游抖动大） |
| 3 | P95 总耗时 | `p95_total_ms` | request_logs.total_ms | 弱（同上） |
| 4 | CPU/请求 | `cpu_ms_per_request` | 网关进程树 CPU 时间增量（psutil，user+system，进程树去重求和）/ 窗口内新增行数 | 仅同进程同配置相对可比（见 §13.6） |

窗口对齐方式固定为 `request_logs_rowid_delta`：开窗取 `SELECT COALESCE(MAX(rowid),0) FROM request_logs` 作水位，收窗只统计 `rowid > 水位` 的行。分子（行数）与分母（CPU 秒）落在同一时间跨度上，**不用 wall clock 反推请求数**。每一行输出都带 `window_alignment` 字段自证。

### 13.2 采样器（只读，运行期不碰生产）

`api/scripts/upstream_baseline.py`：

```bash
cd api
./.venv/Scripts/python.exe scripts/upstream_baseline.py \
    --port 3680 --duration-s 1800 --interval-s 300
```

- 参数/环境：`--pid`（可重复）/`GW_PIDS`；`--port`（默认 3680，按命令行特征 `l07_bootstrap.app_factory` 自动发现网关）；`--db`/`BASELINE_DB`（默认 `api/data/app.db`，与 cwd 无关）；`--duration-s`、`--interval-s`（子窗口长度）、`--quiet`。
- 只读保证：sqlite 以 `file:...?mode=ro` 打开并设 `PRAGMA query_only=1`；实测对 app.db 执行 UPDATE 会被 SQLite 拒绝（`attempt to write a readonly database`）。
- 输出：每个窗口一行 JSON（末行 `aggregate=true` 是整段汇总，判读时只用非 aggregate 行）。
- 退出码：0 正常；2 数据库不存在；3 找不到网关 pid（用 `--pid` 或 `GW_PIDS` 指定）。

### 13.3 切前基线（已采，litellm 臂）

采样窗口 2026-11-14 19:56:34–20:26:40，pid 10464/22948，5 分钟一窗共 6 窗；原始文件 `.scratch/bench2/results/transport/baseline_litellm.jsonl`：

| 窗 | 请求数 | CPU(s) | CPU/请求(ms) | P95 首字(ms) | P95 总耗时(ms) | 5xx | 窗前系统 CPU |
|---|---|---|---|---|---|---|---|
| 1 | 62 | 85.344 | 1376.5 | 7242.9 | 17648.2 | 0 | 99.6% |
| 2 | 58 | 73.031 | 1259.2 | 6378.9 | 17968.5 | 0 | 92.9% |
| 3 | 56 | 139.047 | 2483.0 | 8581.5 | 37428.8 | 0 | 95.5% |
| 4 | 44 | 60.328 | 1371.1 | 6318.0 | 22939.4 | 0 | 96.2% |
| 5 | 31 | 34.844 | 1124.0 | 4043.5 | 47261.0 | 0 | 84.1% |
| 6 | 30 | 54.359 | 1812.0 | 6329.3 | 39256.9 | 0 | 74.8% |
| **汇总** | **281** | **446.953** | **1590.6** | **7168.0** | **27263.0** | **0** | - |

窗口中位数（判读时用中位数，不用汇总值）：CPU/请求 **1373.8 ms**、P95 首字 **6354.1 ms**、P95 总耗时 **30184.1 ms**、5xx 率 **0**。

### 13.4 切流步骤（由用户执行）

1. 确认 `.env*` 里没有遗留 `UPSTREAM_TRANSPORT=litellm`（显式值优先于默认值）。
2. 重启 3680 网关（重启后进程 pid 会变；采样器按命令行自动跟随，无需改参数）。
3. 重启后立刻采 30 分钟同口径：`./.venv/Scripts/python.exe scripts/upstream_baseline.py --port 3680 --duration-s 1800 --interval-s 300`，存成 `post_httpx.jsonl`。
4. 24 小时内持续/定期复采（每小时一段 15 分钟即可），用于「切换后自比」。

### 13.5 判读命令

```bash
cd api
./.venv/Scripts/python.exe scripts/upstream_baseline_compare.py \
    ../.scratch/bench2/results/transport/baseline_litellm.jsonl \
    ../.scratch/bench2/results/transport/post_httpx.jsonl \
    --threshold-pct 20 --sustain 3 --min-requests 10 --min-requests-cpu 100
```

输出：每指标一行（基线中位数 / 切换后中位数 / 变化% / 是否持续恶化 / 参与比较的窗口数 / 门槛），外加 `rollback: true|false`。

- `--min-requests 10`：延迟与错误指标的窗口门槛（p95 在小样本上没有意义）。
- `--min-requests-cpu 100`：**CPU/请求专用门槛**（默认 100），窗口请求数不足的窗口在 CPU 比较里被剔除。**注意当前流量**：基线 30 分钟共 281 个请求 ≈ 9.4 请求/分钟 ⇒ 5 分钟窗口只有 30–62 个请求，达不到 100 的门槛，此时 compare 会把 CPU/请求 标成 `comparable: false`（中位数为空）。要比较 CPU/请求，请用 **15 分钟窗口**（≈140 请求）或显式 `--min-requests-cpu 50` 并在记录里写明样本量。
- `--sustain 3`：连续 3 个窗口（5 分钟一窗 ⇒ 15 分钟）都恶化才算数。

单点核查（不依赖采样器，只读查库）：

```bash
./.venv/Scripts/python.exe -c "import sqlite3;c=sqlite3.connect('file:data/app.db?mode=ro',uri=True);print(c.execute('select http_status,count(*) from request_logs group by 1').fetchall())"
```

### 13.6 回滚判据与动作

**任一指标相对基线恶化 > 20%，且连续 3 个 5 分钟窗口（=15 分钟）都成立 ⇒ 回滚**（ADR-065 §决策 第 3 条）。`upstream_baseline_compare.py` 的 `rollback: true` 即触发条件。

回滚动作（用户执行）：

1. 设 `UPSTREAM_TRANSPORT=litellm`（或把 config 默认值改回 litellm）→ 重启网关。
2. 重启后立刻采 15 分钟（`--duration-s 900 --interval-s 300`），确认 5xx 率与两个 p95 回到基线量级；确认前不算故障结束。
3. 记录：切换时刻、回滚时刻、触发指标、对应窗口的 `pre_system_cpu_pct`、窗口请求数（样本小时必须写明）。

不用「单窗口超标」触发回滚：同一配置的 P95 首字在本次基线里就从 4043 ms 跳到 8581 ms（同臂、同机器、5 分钟窗口，幅度 112%），单点判定会把上游抖动误判成传输退化。

### 13.7 口径陷阱（CPU/请求 的自证实验在这里）

**背景 CPU 不是主因。** 另做了一次 5 分钟、10 秒一窗的探针（30 窗、28 个请求）：12 个「窗口内 0 新增 request_logs 行」的窗口，平均 CPU **0.680 CPU-s/10 s ≈ 0.068 核**；线性回归 `cpu_s ≈ 0.685 + 0.349 × 请求数`（R²=0.279）。即：

- 常驻/背景 CPU 上界约 **0.07 核**，在 5 分钟窗口里约占总 CPU 的 5%（20.4 / 446.9 CPU-s），**不是** 1377 ms/请求 的来源。
- 0 行窗口的 CPU 仍不可视为纯空闲：request_logs 是**请求完成时**才落库，跨窗的长请求（生产 P95 总耗时 17–47 s）CPU 落在「还没落行」的窗口里，所以真实空闲比 0.068 核更低。

**那 1377 ms/请求 比 task-56 的 29–50 ms/请求 高约 30 倍，差在请求形态，不在传输层。** 证据：

1. 生产请求体量远大于压测：request_logs 里单条请求的 `prompt_tokens` 达 **12 万级**（例：124,694，cached 35,072），完成 1,683 token；而 task-56 用的是 mock 上游、几百字节的 frame。
2. 生产每个请求还要写大块 JSON（`request_headers_json`/`forwarded_json`/`params_json`/`rewrite_diff_json`/`attempts_json`）进 request_logs，压测里没有这条路径。
3. 生产是长流式（1,600+ token、P95 总耗时 17–47 s），压测的短流只有 8 帧。
4. 机器同期负载 74%–99.6%（其他 agent 的 tsc/uvicorn/DSH），争抢会放大重试与自旋。

**由此得到三条硬规矩：**

1. **CPU/请求 只做同进程、同配置的相对对比，绝对值不可跨场景/跨机器引用。** 它的分母是「请求」，而请求大小、流式与否、上游 RTT 的权重远大于传输实现的差异；切前切后只有在请求混合相近时才可比。
2. **窗口请求数 ≥ 100**（compare 脚本 `--min-requests-cpu 100` 已内置）：低于这个数，CPU/请求 的采样误差与请求混合波动都会盖过 20% 的判据。当前生产流量约 9.4 请求/分钟，所以 5 分钟窗口（约 47 请求）**不满足**该门槛 —— CPU/请求 的比较要么改用 15 分钟窗口（约 140 请求），要么把门槛降到 50 并在报告里标注样本量偏小。
3. **窗口长度 ≥ 5 分钟**：request_logs 落库在请求完成时，窗口越短，「本窗完成的行」与「本窗烧的 CPU」越错配（10 秒窗的回归斜率只有 349 ms/请求，就是这个错配造成的低估）。

另外两条通用陷阱：采样期间不要跑压测（会同时污染 CPU 与 p95）；`pre_system_cpu_pct` 只是事后解释用的旁证，不是准入条件。

### 13.8 两种口径：切换后自比 vs 跨切换对比

| 口径 | 定义 | 现在能不能用 | 可靠的部分 |
|---|---|---|---|
| 跨切换对比 | 基线（litellm，§13.3）vs 切换后（httpx） | 能用，但只有「前后各 30 分钟」一段，无并行对照 | 5xx 率最可靠；CPU/请求次之（前提请求混合相近且窗口 ≥100 请求）；两个 p95 最弱 |
| 切换后自比 | 只用切换后的时间序列跟自己的滚动中位数比 | 能用，且在切换后 24 小时内更有意义 | 5xx 跳变、CPU/请求跳变、p95 突变点；它能发现「切换后自己变坏」，不能证明「比 litellm 好」 |

关键差别：**自比只能证明「没退化」，不能证明「更快」。** 跨臂的「更快」证据来自 §12 的同轮两臂交错实测（同一台机器、同一负载、两臂交替跑、8 格两趟方向一致），不是来自这次生产切换的前后对比。生产切换的目的是「真实流量下不退化」，所以判据是「相对基线恶化」，不是「相对基线改善」。

现阶段的可靠边界：

1. 只有一次切换、没有并行 A/B ⇒ 跨切换对比**无法排除上游日内波动**（基线里 P95 总耗时已经从 17.6 s 漂到 47.3 s）。
2. 所以跨切换对比只用于**守门**（>20% 恶化持续 15 分钟即回滚），不用于宣称收益。
3. 切换后自比在 24 小时内可靠的是 5xx 率与 CPU/请求的跳变；p95 绝对值受上游与请求混合影响，不可靠。
4. 想要可宣称的收益数字，只能在独立窗口做同轮两臂交错（`.scratch/bench2/runner/transport_ab.py`）。

### 13.9 当前状态

- 2026-11-14 20:26：代码默认值已是 httpx，运行实例仍是 litellm（待用户重启）。切前基线已采（§13.3，6 窗 + 汇总）。
- 待办（用户执行）：重启 3680 → 采 30 分钟 `post_httpx.jsonl` → 用 §13.5 判读 → 24 小时内按 §13.6 判定是否回滚。
- 采样器与对比工具：`api/scripts/upstream_baseline.py`、`api/scripts/upstream_baseline_compare.py`（两者都不启动/停止/重启任何进程，只读 app.db）。

