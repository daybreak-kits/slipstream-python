# ADR-078 流式改写下沉到字节域：块级批发 + needle 替换 + 解析兜底

---
status: accepted
date: 2026-11-18
关联：ADR-065（httpx 默认传输）、ADR-074（头透传）、ADR-075（流生命周期）、`docs/upstream_transport.md` §4
---

## 背景

task-159（B0）给 `UpstreamStream.iter_sse` 修了直通模式的逐帧 JSON 往返，但**只修了一半**：直通条件是 `public_name == upstream_model` 且无 `sse_patch`。生产客户端写直连名 `slug/model` 调用时，`public_name` 恒带斜杠前缀、恒不等于上游名 ⇒ **所有直连名流式都在走逐行改写路径**：每帧 `json.loads → 改 model → json.dumps`（~106µs/帧）+ 每行一条 ASGI 消息（2048 帧/流 = 2048 次事件循环 hop）。

五臂基准实测（docs/scratch/bench-5arm-2026-11-18.md）：50K tok/2048 帧流式直连名 **232ms CPU/req**（裸名直通 14.25ms，agentgateway 3.36ms）；300K tok/20480 帧的生产长流**全部超时无法完成**（2.2 CPU-s/流）。上游连接池 200 并发顶格。

## 决策

1. **转发粒度统一为网络块**：改写不再是「逐行路径」的代名词。`iter_sse` 三态——直通块（字节原样）、**改写块（块内逐行跟踪+改写后重组，本条新增）**、逐行（仅测试替身无 `aiter_bytes` 时退化）。三种路径都按块产出 ASGI 消息。
2. **纯改名场景走字节 needle 替换**：`'"model":"<upstream>"' → '"model":"<public>"'`（带键名前缀）。needle 带完整 `"model":` 键，未转义序列只可能出现在真实 JSON 结构位（字符串内部引号必被转义），不会误伤内容文本。needle 不命中（带空格/转义的非常规回显）**自动回退 `rewrite_line` 的 json 解析路径**，语义不丢。
3. **`aiter_bytes` 不带 chunk_size**：httpx 的 ByteChunker 攒满才吐块，流中途出错时压在 chunker 里的未满块会连同 error 安全前缀一起丢失（本轮测试抓到的真 bug）。按网络块即时产出，批发由自己的块逻辑负责。
4. **跟踪消费 bytes**：`_track_line` 接受 str|bytes，`jsonfast.loads`（orjson 优先、stdlib 兜底）直吃字节，省每帧 decode。
5. **`iter_sse` 产出形态**：改写块路径产出 bytes（ASGI 原生）。此前逐行路径产 str、直通路径产 bytes 的双态收敛为恒 bytes；消费方（chat.py `events()` / Starlette）本就两者兼容。

## 理由

- 机制归因清楚：232ms 里大头是**每行一次的 ASGI send/事件循环 hop**（火焰图 asyncio 74%），json 往返次之。块批发 + needle 同时消掉两者，且保证 error 帧安全前缀、[DONE] 合成、usage 兜底、断流判定、relay 累计等契约逐行照跑。
- needle 替换被拒的原因：逐帧 parse+dumps 即使用 orjson 也要 ~10µs/帧（2 万帧流 200ms）；且重序列化会把上游的 JSON 间隔/键序规范化成网关自己的格式——改写后的帧与上游其余帧格式不一致本身就是一种可观察差异。字节替换保持「除 model 字段值外与上游逐字节一致」。

## 备选与否决原因

- **只首帧改 model**：OpenAI 客户端普遍只读首帧 model，但存在逐帧校验 model 的客户端；契约收窄不可接受。
- **SSE 级流式 JSON 编辑库 / orjson 定点写**：工程复杂度与收益不成比例，needle 已覆盖生产形态（上游 compact JSON），其余走解析兜底。
- **改用 aiter_lines 但批量 yield**：仍要逐行 str 化，丢掉 needle 的免解码收益。

## 后果与影响

- 实测（Linux 开环，16 核 WSL2，五臂矩阵）：直连名流式 232 → **13.8ms CPU/req**（17×），生产长流从 0 完成恢复到 8–32 QPS 全线完成；带改写渠道同步受益（240 → 14.0ms）。
- 行为差异（有意）：改写后的帧保留上游 JSON 原格式（此前被网关重排为带空格分隔符）。逐帧内容语义不变。
- 已知残留：needle miss 的帧仍走逐帧解析（非常规上游）；纯转发场景与 agentgateway 的每帧差距（3.4 vs 13.8ms）仍在，属语言地板，见 ADR-076 阶段 B 门槛再议。

## 相关代码与文档

- `api/src/l05_service/llm/gateway.py`：`UpstreamStream.__init__`（迭代器选择 + needle 预计算）、`_rewrite_blocks` / `_rewrite_raw_block`（新增）、`iter_sse`（三态分派）、`_track_line`（bytes 支持）、`_track_raw_block`（bytes 直传）。
- `api/src/l01_core/jsonfast.py`：新增 `loads`。
- 回归：`tests/test_httpx_transport.py`（含 bytes 消费断言更新）；`test_streaming_chat.py` 全绿。
