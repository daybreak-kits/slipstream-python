# 驭流网关数据面全链路简报（外部咨询用）

> 目的：向外部专家完整呈现网关核心流程、处理逻辑与当前性能问题，征求优化建议。
> 基线：main @ 64e5221+（2026-11-19），Python 3.14.4 / FastAPI 0.162.2 / uvicorn 0.54.0（httptools+uvloop，单 worker）/ httpx 0.28.1 / SQLAlchemy 2.1.8 async + aiosqlite / SQLite WAL。测试机：WSL2 Ubuntu 26.04，16 核。所有数字为开环测法（固定到达率，延迟从应发时刻起算），核隔离（mock 0-2 / 网关 3-9 / 压测 10-15）。
> 读者假设：懂 HTTP 代理、asyncio、Python 运行时；不需要了解我们仓库内部。

## 0. 系统一句话

OpenAI 兼容的 LLM 网关：多渠道路由 + 熔断/冷却/准入控制 + 请求/响应改写 + 流式 SSE 中继 + 逐流 usage 计费 + 零 SQL 请求路径（内存管线）+ 管理面（同端口）。数据面为单进程单事件循环；水平扩展靠多实例 + 外部 LB。

性能基线（开环，非流式 200KB 请求体 / 2048 帧流式，均 50K tok 级）：

| 指标 | 数值 |
|---|---|
| 非流式 CPU/请求 | 2.65–4.6ms（膝点 ~310 RPS，240 QPS 线速零 429） |
| 流式 CPU/请求（2048 帧） | 10.2–11.2ms（64 QPS 线速，TTFT p50 8.8ms） |
| 生产长流（300K tok 入/20K tok 出 ×16 并发） | p50 76.7ms，零失败 |
| RSS（重载长流平台） | 783–871MB（启动 119MB） |
| 对照：Rust 的 agentgateway | 非流式 1.0ms、流式 3.4ms、重载 RSS 39MB 全回落 |

## 1. 完整流程：用户 → 网关 → 上游 → 网关 → 用户

以下为 POST /v1/chat/completions 的全链路。`file:line` 为当前代码位置。

### 1.1 入口层（l06_api/routers/v1.py + l07_bootstrap/app_factory.py）

1. **纯 ASGI middleware**（app_factory.py:74-105，R6 改造）：读/生成 `x-request-id` 写入 scope state，send 拦截 `http.response.start` 回写响应头。不再用 BaseHTTPMiddleware（省 per-request anyio task 与内存流拷贝）。
2. **CORS**（Starlette 内置，纯 ASGI）。
3. **依赖求解按声明顺序**（v1.py:124-128）：
   - `_inflight_slot`（v1.py:31-49）：**先于鉴权与 body 解析**占在飞槽（默认上限 256，DB 热改 + env 回退；429 + `Retry-After: 1`，信封 `INFLIGHT_LIMITED`）。释放收归依赖栈：非流式在响应后、流式在 SSE 体发完后、断连取消随栈回卷——恰好一次。抬闸对照实验（cap 256→1024）证明闸门在保护吞吐：去掉后吞吐反降（152-199 ok/s，p50 秒级）。
   - `get_user_key`（l06_api/deps.py）：SHA256(key+jwt_secret) 后查**进程内目录快照**（memory 管线零 SQL；未知 key 才落 SQL 兜底），节流回写最后使用时间。
   - `get_db`（deps.py:24-31）：SQLAlchemy async 会话（惰性连接）。
4. **显式读体 + 上限**（v1.py:64-117，R6 新增）：Content-Length 超限在读前 413；读后按实际长度兜底 413；`json.loads` 解析；非 dict → 422 信封（与 Body(...) 时代逐字节同构）。**解析后置空 `request._body`**（P2：省 200KB-1.2MB/流的中继期持有），并返回 `(parsed, raw_body)`——raw_body 供 P2 快路径（§1.5）。

### 1.2 服务层校验与路由（l05_service/chat.py + routing.py）

5. **_validate_body**（chat.py:726-735）：model 必填、messages 必须为非空 list（含 stream=true 必带）等。
6. **resolve_target**（routing.py:1124-1170）：memory 快照路径（`_resolve_from_snapshot`）——直连名 `slug/call_model` 或裸名（手动组合→自动组合）解析出 `Candidate` 列表（channel + channel_model）。
7. **候选排序**（routing.py `order_candidates`）：优先级层（数字大者先，同层随机均匀）+ ADR-077 路由策略（uniform/stable/cost/latency，基于 15s 滑窗统计 + 30 样本观察期）。统计缓存为 **stale-while-revalidate + 单飞后台刷新**（route_stats.py:117-168，R4 改造）：TTL 过期后请求拿陈旧值，后台短会话重算聚合，请求路径不再跑聚合 SQL。
8. **重试预算**（chat.py:_RoundAttempts）：全局 `max_attempts`/`budget`（默认 12 次/120s），逐轮逐候选；失败分类（permanent/transient/client）决定换候选或终止；流式首字节后不换（ADR-075）。

### 1.3 请求准备（l05_service/rewrite.py + channels.py）

9. **prepare_request**（rewrite.py:190-249）：应用渠道的请求头规则（set/delete/rename，保护头不可触碰）与请求体规则（create/update/delete/copy/…，带参数变量）；产出 `PreparedRequest`（headers/body/rewrite 标记），并随解析顺带产出 `no_body_rules` 标记（P2 快路径资格之一）。多 key 渠道按 `sha256(request_id)` 选 key（会话亲和）。
10. **日志快照**：BodySnapshot(request_body=dict)——供 params 摘要与 body 行。

### 1.4 字节域快路径（P2，ADR-080，gateway.py:192-232 fast_forward_content）

11. **资格六条**（全部满足才走，任一不满足回退 dict 路径——回退路径与旧实现逐字节一致）：httpx 臂；渠道无 request_body 规则；raw bytes 无 `b"null"` 与 BOM；model needle（宽松正则恰好命中一次 + 紧凑拼写命中）；stream 请求 bytes 无已有 `stream_options` 且 `stream is True`。满足则上游请求体 = 客户端原始 bytes **仅替换 model needle + 末尾注入 stream_options**，直接 `content=bytes` 交 httpx（省 dict 重建 + 二次编码）。快照存改写后 bytes 的头部切片（渲染预算内）。needle 替换的安全论证同 ADR-078（JSON 字符串内引号必转义，未转义序列只可能是结构位）。

### 1.5 上游调用（l05_service/llm/gateway.py）

12. **非流式**（_httpx_complete:1222-1247）：共享进程级 `httpx.AsyncClient`（池 200 连接/50 keepalive/30s；HTTP/1.1）`POST content=预编码 bytes`；分段超时（connect 10s，总 120s 内分摊）。响应 `json()` → `meaningful_content` 门槛（空响应=伪成功 502）→ `extract_usage` 归一 → 返回 (payload, usage, latency, headers)。
13. **流式**（_httpx_open_stream:2190-2230）：`build_request(content=bytes)` + `send(stream=True)`；`prime()` 在 30s 首字节超时内读到第一个有意义 delta（期间同步采 usage/finish_reason/response_id），200 + 非 SSE content-type 无帧 → bad_response；prime 后流**不再 failover**（ADR-075）。
14. **流式中继**（iter_sse，ADR-078 三路径）：
    - **直通块**（名字相同且无 patch）：`aiter_bytes()`（无 chunk_size——带 chunk_size 会让 ByteChunker 在流中途出错时吞掉缓冲前缀）按网络块原样转发；
    - **改写块**（直连名恒走此路：public_name≠upstream_model）：块内逐行**跟踪 + needle 字节替换 model**（宽松正则恰好一次 + 紧凑替换；未命中回退逐帧解析改写），重组后按块转发；
    - **逐行**：仅测试替身（无 aiter_bytes）退化。
    - **跟踪**（_track_line，每帧）：P2 字节门（8 needle 扫描，无命中帧跳过解析）→ usage 采信（仅上游真实值，标记 usage_from_upstream）、finish_reason、error 帧（抛 stream_abort，安全前缀已转发）、TTFT/TTFA/TTLT、content/reasoning 累计（预览 4K、relay 文本帽 256K chars）、断流判定（EOF 无完成标记=stream_abort）、[DONE] 缺失时合成。
    - **背压**：无显式队列，生成器悬停自然反压；每块读有 read_timeout 看门狗。

### 1.6 响应侧与收尾

15. **响应头/体改写**（rewrite.py prepare_response）：渠道的 response_headers 规则（网关必需头最后覆盖）+ response_body 规则（仅非流式；流式响应体不做逐帧体改写）。
16. **非流式响应**：`dumps_compact`（orjson）编码 payload → Response（头部叠加 x-request-id）。
17. **流式响应**：StreamingResponse(events())，SSE 中继到客户端断连；断连 → CancelledError → cancelled 记账，不喂熔断（ADR-075 流生命周期客户端所有）。

### 1.7 计费与日志（零 SQL 请求路径，ADR-057/079）

18. **usage 估算兜底**（billing.py）：上游无 usage 时——prompt/补全按字符量分派：≤128K chars 走 tiktoken o200k 精确分词（media part 换占位符 + 每媒体单位 1000 tok 常数），超出走 C 级脚本折算（ASCII÷4、非 ASCII×1.5）；打 `usage_estimated` 标记（聚合/看板隔离）。上游给了 usage 则原值原样（cached_tokens 三级回退读取）。
19. **_write_log**（chat.py:537-720）：构造 billing/observability 值 + attempts_json/response_headers_json/params_json（有界）；**memory 管线**：`build_body_record`（redact + 按预算截断渲染）——**mode=off（默认）跳过渲染，body=None**（本项为膝点修复关键：此前每请求为注定被丢弃的 body 付 ~3ms redact+编码）；`usage_sink.record(row, body)` → **WAL append（内存操作 + 缓冲写，零 fsync 在循环内）**；**classic 管线**：INSERT+COMMIT 在请求内（兼容保留）。
20. **后台 usage sink**（usage_sink.py，1s 节拍）：批量 INSERT request_logs（幂等）+ body 行（受策略门控）；WAL fsync 由**专用后台线程**（R8）按 flush_ms 节奏执行；LEDGER_SPLIT（默认 off）账本管线对称。

### 1.8 熔断/冷却与finalize

21. **breaker**（l03_domain/breaker.py 纯函数 + routing.py 条件写）：失败→连续计数→open（冷却）→fault 升级；半开单请求探测租约；成功仅在状态迁移时写库；迁移后同步进程内缓存行（f 流 finalize 亦然）。
22. **流式 finalize**（后台 task）：stream.close()（**置空 response/迭代器引用**——P1.5 泄漏修复）→ usage 决策 → breaker outcome → _write_log（memory：WAL append；classic：INSERT+commit）。

### 1.9 进程内其他常驻

- **目录快照轮询**（config_catalog.py，2s/30s TTL）：admin 写 → bump version → 各进程轮询换代（多实例可见性窗口 ≤2s）。
- **route_stats 单飞刷新**（R4）、**body_settings TTL**（5s）、**retention 循环**、**model_defaults 日更**。

## 2. 关键设计决策（ADR 索引）

| ADR | 决策 |
|---|---|
| 057 | 零 SQL 计费管线（WAL + 后台幂等入库） |
| 065 | 传输默认 httpx（弃 LiteLLM SDK） |
| 074 | 头透传语义（hop-by-hop/凭据/网关必需头） |
| 075 | 流生命周期客户端所有；首字节后不 failover；估算兜底计思考块 |
| 076 | 数据面/控制面分离路线；阶段 A/B 门槛（换语言由数据决定） |
| 077 | 路由策略与观察期 |
| 078 | 流式改写字节域：块批发 + needle 替换 + 解析兜底 |
| 079 | body 有界渲染（redact+截断前置） |
| 080 | 请求侧字节域快路径（六资格门 + 回退） |

## 3. 当前问题清单（按影响排）

### 3.1 膝点 ~310 RPS（非流式 200KB）

240 QPS 线速（零 429），304 QPS 起 in-flight 顶格 256 → 429 shedding（快速失败，可控）。抬闸对照证明 cap 是保护机制而非瓶颈：cap 1024 时吞吐反降。**膝点由事件循环单线程容量决定**：拐点 py-spy 显示 httpx 组 40.7%（连接池 O(N) 存活扫描 ~10%、请求体编码 8.3%——后者 P2 已消）、stdlib json 15.1%（P2 字节门后大降）、aiosqlite worker（旁路线程）8-16%。剩余大头：httpx/httpcore 连接池管理、收发系统调用、JSON 解析。单循环 CPU ~1 核即膝点。

### 3.2 RSS 高水位不回落（爬梯式，非泄漏）

- 空载 119MB；重载长流平台 **783-871MB**；连续多波爬升：619→808→990→**1144MB 后饱和**（波次复现，饱和后稳定，服务零失败）。
- memray 归因：~250MB 活对象（在飞 body 拷贝 ×2：dict + 客户端 bytes/快照切片——P2 后已从 3 份降 2 份）+ ~450-600MB **glibc arena 滞留**（MB 级 churn 撕裂，free 后不还 OS）。
- **已实测否决**：MALLOC_ARENA_MAX/TRIM env（-11% 且弱）、MMAP_THRESHOLD 固定（RSS 400MB 但延迟崩）、PYTHONMALLOC=mimalloc（778MB 但膝点 -8%）、显式周期 malloc_trim(0)（无效，只还堆顶）、jemalloc LD_PRELOAD（平台 971MB/HWM 1105，比 glibc 还差——社区「-50%」案例不迁移到 MB 级 JSON churn 模式）。
- 对照：agentgateway（Rust）39MB 全回落。空载 3.8× 是运行时地板。

### 3.3 其他

- **流式逐帧 parse 是设计约束**：usage 兜底/断流判定/TTFT/relay 累计要求每帧都读；P2 字节门只省了 keep-alive 类帧。业界「usage 只在末帧」契约（stream_options.include_usage 已强制注入）允许跳过中间帧解析——但断流判定/relay 累计仍需逐帧过目，完全跳过的取舍未定。
- **finalize tiktoken**：上游缺 usage 时 ≤128K chars 走真分词（典型输出 <2ms），>128K 走脚本折算——已有界（P1.5-D），极端 4MB CJK 23.7ms。
- **多实例可见性**：目录快照 2s 轮询（管理写→数据面生效窗口）。
- **测试噪音**：xdist 下 0-3 个轮换 flake（时序敏感类），串行全绿——存量。

## 4. 已试过并排除的方案（避免重复建议）

| 方案 | 结果 |
|---|---|
| allocator env 四件套（TRIM/ARENA/MMAP/64KB 变体） | 全测：仅 MMAP 封 RSS 但延迟崩；其余弱 |
| PYTHONMALLOC=mimalloc（3.14 内置） | 平台 778MB（-8%）但膝点 -8%——两轴不可兼得 |
| jemalloc LD_PRELOAD（解包方式，零安装） | 平台 971MB/HWM 1105——比 glibc 差 |
| 显式周期 malloc_trim(0) | 无效（只还堆顶） |
| BaseHTTPMiddleware / 逐帧重写 / fsync 在循环内 / body 无界渲染 / 请求路径 SQL | 已全部改造（R6/R8/R4/ADR-078/079） |
| 抬高在飞上限 | 实测吞吐反降（闸门是保护） |

## 5. 想请教的问题

1. **单循环网关的膝点**：httpcore 连接池的 O(N) 存活扫描与连接管理在拐点占 ~10%——有无成熟的池管理替代（分片池、外部池、或直接 raw socket 池）？
2. **流式 relay 的逐帧 parse**：在「usage 兜底 + 断流判定 + relay 累计」语义下，业界（OpenAI SDK、envoy、 Kong 等的字节门实现）有没有跳过中间帧解析又能保住断流判定的成熟做法？
3. **glibc arena 滞留 450-600MB**：除换分配器外，Python 生态有无让 malloc 归还中部空闲页的实践（`MADV_DONTNEED` 手动 arena 遍历？`tkinter`…）？还是「接受平台 + cgroup 隔离 + 水位回收」就是终态？
4. **多实例 + 内存**：每实例 cap×实例数的内存规划，业界对「实例数 × 单实例内存平台」的容量模型怎么定？
5. 若目标是把非流式 CPU/请求压到 <2ms：Python 内（或数据面换语言前）还有什么被我们忽略的大项？
