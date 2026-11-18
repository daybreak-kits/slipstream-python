# 驭流（Slipstream）性能权威报告

> **状态**：性能权威报告 v3（2026-11-19）。结构三个时代：**§2–§9** = Windows 同机口径（2026-11-14，历史基线，已被取代）；**§10–§11** = Linux 开环三臂与 agentgateway 对照（2026-11-16，方法论与 B2 决策依据）；**§12** = **当前权威结论**（五臂基准与 P0→P2 优化终验收，2026-11-19）。§13 为数据与复现索引。
>
> **证据来源**（**不在本仓**，见 §13.5「证据归属」）：`.scratch/bench2/analysis/`、`docs/scratch/bench-5arm-2026-11-18.md`、`docs/scratch/research/`；原始数据与脚本在开发仓压测工作区，生产仓不随附；引用路径仅为标明数字出处，不是可点击的链接。
>
> **两个时代的环境**：§2–§9 = Windows 10，8 逻辑核 / 4 物理核，Python 3.13.14，闭环并发阶梯；§10–§13 = WSL2 Ubuntu 26.04，16 逻辑核，Python 3.14.4，**开环**（固定到达率，延迟从应发时刻起算）。两时代数字**不可互比**；比较基准见各节口径警告。

---

## §0 结论先行

### 当前终态（2026-11-19，P2 泄漏修复 + 安静窗口终验收，全部实测）

| 指标（50K tok 非流式 / 2048 帧流式） | 优化前（P0 前） | **终态** | 对照 agentgateway |
|---|---|---|---|
| 非流式 CPU/req | 4.97ms | **3.38ms**（240 QPS 线速零 429） | ~1.0ms |
| 非流式膝点 | ~230 RPS（429 shedding） | **~300+ RPS**（304 完成 82%、320 达成 305/s） | 1024 线速 |
| 直连名流式 CPU/req（2048 帧） | **232ms**（逐帧改写慢路径） | **12.9ms**（快路径恢复后） | 3.36ms |
| 流式 64 QPS | p50 2s 级 + 429 | 线速 64，p50 13.6ms、TTFT 8.8ms | 线速，p50 5.2ms |
| 生产长流（300K tok 入/20K tok 出 ×16 并发） | 0 完成 | 全部完成，p50 91ms、**RSS HWM 仅 138MB** | 线速 21.5ms |
| 多波 RSS 爬梯 | 619→808→990（+180/波不收敛） | **286→308→302（波 2 起持平，爬梯消失）** | 38MB 全回落 |
| 功能核验 | — | 27/28（唯一失败为存量目录轮询时序偶发） | — |

### 演变历程（四轮优化，全部保持计费/日志/错误映射/重试语义）

| 轮次 | 主要改动 | 关键实测 |
|---|---|---|
| 早期（Windows 口径） | memory 管线、cost 移除、httptools、keep-alive 75s | §2–§9 |
| 传输换 httpx（ADR-065/078） | 直连名流式逐帧改写（232ms→字节域块批发） | §10.2、§12.2-R1 |
| P0/P1（ADR-078/079） | body 有界渲染、orjson、纯 ASGI、闸门前移、413、fsync 出循环、路由 SQL 单飞、字节域跟踪 | §12.2、§12.3 |
| P2（ADR-080） | 请求侧字节域快路径（六资格门+回退）+ 泄漏修复（close 断 Response._request/stream 链） | §12.2、§12.3b |

对比 agentgateway（Rust）：非流式 4.97→3.38 vs 1.0（**3.5×**）、流式 232→12.9 vs 3.4（**3.8×**）——从「全面跑不赢（68×/5×）」收敛到 **3–5× 同量级差距**；残余差距 = 每帧地板（Python ~5µs vs Rust ~0.1µs）+ 框架底盘（~1.5ms ASGI + ~1.0ms httpx/aiosqlite），对应 ADR-076 阶段 B 门槛，维持「数据决定是否换语言」（§12.7）。

### 历史结论（2026-11-14 Windows 口径，仅作演变记录）

1. 该机「加并发」无效：C 16→1024 吞吐 38.01→23.38 RPS——单核 ×32ms 是墙，不是并发度。
2. 多实例次线性：8 进程仅 2.30×，墙是核与超线程，不是 SQLite。
3. 已落地三件套：memory 管线 −21.7% CPU/req、litellm cost 移除（非流式 −11%/流式 −23%）、httptools 默认。
4. 非性能事实：Windows `uvicorn --workers N≥2` 起不来（WinError 5，Linux/WSL2 不受影响）；多实例共享 SQLite 时 classic 静默丢行 4.9%。

---

## §1 方法与口径

### 1.1 本报告各时代支持的决策

- **§2–§9（Windows 闭环时代）**：单机还值得加并发吗（否）、开 memory 关 cost（要）、到 1000 RPS 需几台（4–6 台）。
- **§10–§11（Linux 开环时代）**：开环方法论确立；agentgateway 对照与 B2 门槛框架。
- **§12（当前）**：P0→P2 优化的终验收；五臂矩阵回答「能否跑赢 agentgateway」；内存治理与泄漏归因。

### 1.2 负载模型与其局限

全部实验都是 closed-loop（N 个客户端各自「发一个 → 等完整响应 → 再发下一个」）的并发阶梯，不是开环到达率。closed-loop 在服务器排队时会跟着降速，**会低估尾延迟**（协调遗漏 / coordinated omission）；因此所有 p99 只做组内比较，不能当 SLA。

外部对照：开环与闭环的差别、Little 定律与 goodput 的定义见 [LLM Load Testing: Open vs Closed Loop](https://gpusmith.com/articles/en/llm-load-testing-methodology-open-vs-closed-loop)。

### 1.3 负载生成器必须自证不是瓶颈

判据是「网关数字必须 ≤ 客户端 + mock 直连上限」：mock 直连 **M_Z 2382.6 RPS**（0 ms 上游）/ **M_L 2074.7 RPS**（50 ms 上游）。本轮所有带网关的臂在 40–179 RPS，只占上限的 1.7%–8.6%，所以网关侧没有被客户端封顶。

这条判据此前被违反过一次：用 httpx 客户端时 C=8 只有 470 RPS、**C=32 反而降到 316 RPS**，网关只吃到 0.42 核而客户端吃 0.99 核——那次 15 臂矩阵的 10.31 RPS 是「客户端天花板」而非网关天花板。换成自写 raw asyncio + SSE 客户端（`runner/mw_client.py`，每 worker 一条 keep-alive、请求预序列化、TCP_NODELAY）后，同一 mock 实测 2436–3086 RPS（C=8/32/128，零错误），第一根可信基线才出现（45.9 RPS / 1.25 核 / 27.3 msCPU）。方法论同名问题见 [How to Benchmark an AI Serving Harness Without Misleading Results](https://sekin.in/how-to-benchmark-an-ai-serving-harness-without-misleading-results)。

### 1.4 预热、窗口与指标定义

窗口：单进程阶梯每格「每并发槽 1 个请求预热（marker 文件剔除）+ 测量 45 s」，C=16/64/256/512/1024；自 profile 50 s（C=16，464 非流式 + 239 流式 = 703 请求）；零 SQL A/B 每轮 warmup 15 s + 测量 45 s，共 3 轮交错（C=32、stream=1、max_tokens=64、tier=50 ms、8 chunks）；cost 微基准 30 s/格、含 5 次 warmup、每臂独立进程；多进程矩阵 C=128、warmup 15 s + 测量 25 s（404 复跑 warmup 45 s）。

指标：**网关 CPU** = 进程树 `cpu_times` 差值 ÷ 窗口内成功请求数（不是墙钟）；RSS 取窗口峰值；分位只统计成功请求；行数对账用客户端 `X-Request-ID` 集合与 `request_logs` 全量 id 集合做差集。

**cProfile 放大**：对每请求几千次函数调用的代码有 **2.5–3× 放大**（profiled 99 ms vs 真实 32 ms）。份额可用，绝对毫秒一律按 32 ms 折算——§3 的归因表按这个口径读。

### 1.5 重复、方差、版本与纪律

- **每臂 1 rep**，只有零 SQL A/B 是 3 轮中位数（唯一给出 min–max 的数据）。没有置信区间，所以本文不把「差异 < 10%」写成结论；被外部负载或已知异常干扰的臂在 §7 单列并标注「不可并列比较」。
- 顶级复现清单要求「钉住软件与数据版本、保留逐次原始输出、报告方差而不只是最好成绩」。本报告保留全部逐臂 JSON（§13.2），**方差一项未满足**（每臂 1 rep）。
- **未测**：真实上游（全部 mock）、跨机部署、12/16 进程边界、Postgres、负载均衡器自身开销、上游 h2。
- **纪律**：重负载前抢 `.scratch/bench2/.bench.lock`（`O_EXCL` 独占创建，已存在就等，finally 删除），一次只跑一个压测；测试端口 ≥3690、mock 上游 3980+，不得触碰生产 3680。

### 1.6 基准测试方法学：工况如何翻转排名

这一节不是新测量，而是一份**读数规矩**：同一对实现，只要工况参数换一换，排名就能翻过来。凡是要引用本文或别人的横评，先过一遍这一节。

#### (a) 案例：同一对「httpx-native vs litellm」在我们手里排了三次

| 工况（流长 / 并发 / 上游节奏） | litellm | httpx-native | 谁赢 | 机制 |
|---|---|---|---|---|
| 饱和、20000 帧/流、C=8（网关是唯一瓶颈，两臂均 ~0.9 核） | 4,668.1 帧/s；0.191 ms/帧 | 13,264.7 帧/s；0.074 ms/帧 | **httpx 2.84×** | 每帧固定开销主导 |
| 饱和、20000 帧/流、C=1 | 1,926.5 帧/s；0.475 ms/帧 | 1,675.0 帧/s；0.577 ms/帧 | **litellm 1.15×** | 只有 1 条流在途，httpx 的摊薄优势显现不出来；它的每帧成本随并发下降得更陡（0.577→0.074），litellm 更平（0.475→0.191） |
| 2000 帧/流、上游 1 ms/帧节流、C=8 | 1,516.8 帧/s；0.615 ms/帧 | 1,370.7 帧/s；0.720 ms/帧 | **litellm 1.11×** | 上游节奏主导，网关不是瓶颈，比的是「等帧之外还要做多少事」 |

出处：`.scratch/bench2/analysis/streaming_cpu.md:24-29`（同表亦见 `task48_report.md:196-201`，原始数据 `results/gw_self_bench.json`、`results/streams_verify.json`）。

**结论**：同一台机器、同一天、同一对实现，只因「流长 / 并发 / 上游节奏」三个参数不同，排名翻转两次，比值从 2.84× 到 0.88×。所以正确说法是「**在饱和 + 20000 帧/流 + C=8 的合成工况下**，httpx 的本机吞吐是 litellm 的 2.84×」，而不是「httpx 比 litellm 快 2.84×」。机制是**每帧固定开销**（序列化、SSE 解析、回调、对象转换）与**每请求固定开销**（连接与协议适配、cost/token 计算、logging 初始化）的占比之争：流越长、并发越高，每帧成本越接近实现真正的每帧开销；流短或被上游节流时，每请求开销与上游节奏占主导，排名可以反过来。

#### (b) 我们自己的报数规矩（每条一句 + 反例）

| # | 规矩 | 反例（我们踩过或差点踩的） |
|---|---|---|
| 1 | 报生产形态：流长、并发、上游节奏三件事一个都不能少 | 只说「2.84×」不谈 20000 帧/流与 C=8，读者会当默认工况 |
| 2 | 报 CPU / 单位工作量（每帧、每请求），不只报 RPS | 客户端封顶时 RPS 只反映压测机：C=8 470 RPS、C=32 反而 316 RPS（§1.3） |
| 3 | 报比值不报绝对值 | 跨时段/跨机器绝对值不可比：PBKDF2 同机实测 0.15 s 与 1.2–1.9 s 都出现过（`analysis/task54/REPORT.md` §2） |
| 4 | 必须报 p99，不只均值 | closed-loop 会低估尾延迟（§1.2 协调遗漏），均值掩盖排队 |
| 5 | 压测进程与被测进程不能抢同一批核 | 客户端吃满 0.99 核而网关只 0.42 核时，测的是客户端（§1.3） |
| 6 | 开跑前记录系统负载，结果里标注 | t5 并行压测窗口内的绝对数一律作废（`analysis/runtime_transport.md:102`）；E5 S3 因 173/416 个样本检出外部 pytest 整段作废（`analysis/e5_section_900s_draft.md:19`） |
| 7 | 同一结论至少两趟交错（A→B、B→A） | 单 rep 的 10% 差异不足以下结论（§1.5），交错才能区分工况漂移与实现差异 |

#### (c) 读「别人家的」Go/Rust 网关横评：先问七件事

看到任何「X 网关比 Y 快 N 倍」，按顺序问：

1. **是否同工况**：两边的流长、并发、上游节奏是否一致，还是各自「最舒服」的参数？
2. **是否报 CPU / 单位工作量**：只报 RPS 的横评，很可能一边被客户端或网络封顶。
3. **是否有 p99**：只有均值/最好成绩的数字，不能用来判断生产尾延迟。
4. **是否同机压测**：客户端与被测在同一台机器上抢核时，倍数结构性地偏向「客户端更轻」的一边。
5. **流长多少**：短流（几百帧）与长流（几千帧以上）会改变每帧 vs 每请求开销的占比，排名可翻转（见 (a)）。
6. **TLS、序列化、协议转换算没算进去**：只测「转发内核」与测完整链路（含 TLS 握手、JSON 编解码、usage 统计）是两件事。
7. **有没有披露客户端瓶颈**：给不出「负载生成器直连上限」的横评，无法判断它的天花板是网关还是压测机。

**允许的结论强度**：如果这七问都过了，可以认为「**在该工况下排名大概率稳健**」；但**任何倍数都不可移植**到另一台机器、另一种流长或另一种并发。反过来，如果这七问缺了三项以上，正确动作是把数字当「待验证线索」，不是当结论。

#### (d) 反面教材：我们自己踩过的两个坑

1. **用 20000 帧/流的合成工况推生产收益**。饱和实验回答的是「网关帧处理成本」，不能直接乘生产流长：按生产 2500 帧/流重算，每流 CPU 是 litellm 0.5–1.2 CPU-s、httpx 0.2–0.6 CPU-s；早前流传的「每流 9.6 CPU-秒」是把 `--reload` watcher 的 0.9 核除以 3 个请求得来的，不是每请求成本（`analysis/streaming_cpu.md:10`、`analysis/task48_report.md:206`）。
2. **在 `pre_cpu=98.5%` 的机器上跑的那一格只能作废**。传输 A/B 里 litellm 的非流式格起跑时 `pre_cpu=98.5%`，而同格 httpx 只有 `pre_cpu=48.2%`（`results/transport/transport_ab.json:195` 与 `:454` 的 `table_row`）——两臂起跑态差近 2 倍，按 §1.5 纪律该格不进任何结论，只能重跑。这也是本文把 `pre_cpu` 写进每行原始输出的原因：**没有环境自证的臂，事后无法补救**。

#### (e) 落到我们自己的结论上

- 本文所有倍数（2.84×、2.30× 等）都只在本节规矩下成立：**方向可以带走，倍数不能带走**。
- 任何要进入对外材料的数字，必须同时带「工况三参数 + 出处（`文件:行` 或 `results/*.json`）」；给不齐的，宁可写成「待验证」。
- 这一节的规矩同样适用于本文自己：如果将来的读数与本节的 (d) 冲突，以本节为准，先作废、再重跑。

---

## §2 单进程阶梯与天花板

零延迟 mock、流式、litellm cost 计算仍开着（task-51）：

| 并发 C | RPS | p50 | p95 | p99 | 网关 ms/请求 | 网关核 | 客户端 ms/请求 | 网关 RSS |
|---|---|---|---|---|---|---|---|---|
| 16 | **38.01** | 358 ms | 727 ms | 1.62 s | 32.1 | 1.22 | 5.0 | 122 MB |
| 64 | 30.35 | 1.33 s | 6.20 s | 10.5 s | 34.5 | 1.05 | 9.4 | 138 MB |
| 256 | 27.06 | 7.10 s | 18.7 s | 22.5 s | 37.3 | 1.01 | 17.9 | 183 MB |
| 512 | 20.79 | 11.8 s | 32.4 s | 40.1 s | 40.8 | 0.85 | 37.6 | 231 MB |
| 1024 | 23.38 | 13.5 s | 31.3 s | 41.6 s | 38.9 | 0.91 | 38.4 | 270 MB |

- **拐点在 C=16**：C=64 时 RPS 已经 −20%；此后每加并发只是加排队。
- **口径警告**：本表与 §7 的 A1_L_classic（45.9 RPS / 27.3 ms）**不可直接比**；与 `ssot.md` §11.2 的旧「12–13 rps」亦不可比（该口径已撤销，指向本报告）。**本表为历史基线，已被 §12 的开环五臂矩阵取代**（完整梯度数据见 git 历史）。

---

## §3 CPU 归因与三项已落地优化

### 3.1 32 ms 花在哪（cProfile，task-48/49）

| 归因 | 量级 | 处置 |
|---|---|---|
| CPython / 框架本身（中间件、JSON、pydantic 校验、事件循环） | ~17 ms（53%） | 结构性，需改传输/精简校验才能动 |
| litellm 内部（cost 计算被 profile 低估，见 §6） | ~3.8 ms（11.8%，仅此口径） | 已移除 cost 计算（ADR-058） |
| SQLAlchemy / SQLite（鉴权、路由、日志 INSERT） | ~2.3 ms（7.1%） | 已上零 SQL 管线（§5） |
| pydantic 模型校验/序列化 | ~2.1 ms（6.5%） | 可评估（response_model 精简），未做 |
| 其余（Fernet 解密 0.7 ms、目录缓存等） | ~1 ms | 已做进程级缓存 |

### 3.2 三项优化（均已落地）

| 优化 | 实测收益 | 证据 |
|---|---|---|
| 零 SQL 管线 `USAGE_PIPELINE=memory` | **−13.4 ms/请求（−21.7%）**，RPS +14.5%，p99 −26.6% | §5，3 轮中位数 |
| 移除 litellm 每请求 cost | 非流式 **−4.0 ms（−11%）**、流式 **−20.1 ms（−23%）** | §6，墙钟微基准 |
| 中间件/框架固定开销 | 归因完成：中间件+依赖 45 ms（非流式）/115 ms（流式）@C=8；后续由 R6 纯 ASGI 改造消化 | `task48_report.md:73/:86/:169` |

### 3.3 32 ms 之后的算术（历史口径）

`32 − 13.4（memory）− 4（cost）≈ 15 ms`（乐观上限）；按静态审计口径约 **21 ms**，对应单进程 47–67 RPS。该区间只对非流式成立。**已被 §12 的 P2 终验收超越（3.38ms）**，保留作演变记录。

---

## §4 传输层事实（task-50）

| 结论 | 数字 | 处置 |
|---|---|---|
| httptools vs h11（服务端每请求 CPU） | 比值 **0.624**（省 37.6%），rps 1.139× | 已是默认（`uvicorn[standard]` 带 httptools），无需动作 |
| HTTP/2 上游 | 冷连接首包比值 minimax 0.796 / nvidia 1.281 / openrouter 0.922，**符号不一致** | 不建议默认开 |
| keep-alive 冷连接成本 | DNS 1.3 + TCP 64.6 + TLS 154.7 + TTFB 76.5 ≈ **297 ms**；稳态复用 75.6 ms | 保持长 keep-alive；`--timeout-keep-alive` 提到 **75 s** |
| `--reload` 成本 | 常驻 ~0.9 核 | 生产不带；开发用 watchfiles |

---

## §5 零 SQL 管线 A/B（task-53，已落地 = ADR-057）

### 5.1 机制

开关 `USAGE_PIPELINE=classic|memory`。memory 模式下请求路径不再访问 SQLite：写侧只向 WAL 追加一行，后台线程批量幂等 upsert `request_logs`；读侧走不可变 `CatalogSnapshot`（鉴权、路由、正文策略、目录），admin 写 bump version、进程按 `catalog_poll_interval_ms` 轮询换代。代价：跨进程可见性 ≤ 轮询间隔（生产设 2000ms，§7.3）。

### 5.2 实测（3 轮交错、每轮 45 s、warmup 15 s、C=32、流式、tier=50 ms）

| 指标 | classic | memory | 变化 |
|---|---|---|---|
| RPS | 18.03 | 20.65 | **+14.5%** |
| 网关 CPU/请求 | 61.8 ms | 48.4 ms | **−13.4 ms（−21.7%）** |
| p50 / p95 / p99 | 1564 / 2853 / 3812 ms | 1422 / 2292 / 2797 ms | −9% / −20% / −27% |
| TTFA p50 | 1244 ms | 744 ms | −40.2% |

- 省下的 13.4 ms 里约 6 ms 是「少 5 条 SELECT / 1 条 INSERT」的直接成本，其余是去掉 SQLite 单写者串行化。
- **单进程下 memory 无收益**（吞吐 −11%），适用面是多进程/高并发。

### 5.3 四道闸门与一个真实缺陷（classic 流式 at-most-once 缺口）

| 闸门 | 结果 | 关键数字 |
|---|---|---|
| a 行数对账 / 去重 | **FAIL（5/6 轮）**，±2 容差下 PASS | 缺 1 行：客户端读完 `[DONE]` 断连取消生成器，`_write_log` 被取消 → 0.042% 丢行 |
| a 的 memory 子判据 | PASS | 2799 请求 / 2799 行，0 丢 0 重 |
| b model_type 轮询 ≤10 s | PASS | PATCH 后 0.09 s 返回 400 |
| c kill -9 WAL 回放 | PASS | kill 前 DB 0 行 / WAL pending 41；重启回放 41 行，dup=0 |
| d memory 正文日志 | PASS | body 行 10 / 父日志 10 |

memory 管线结构上不存在此缺口（WAL 追加在请求内完成）——这是除性能外开 memory 的第二个理由。

---

## §6 LiteLLM cost 计算移除（task-54，已落地 = ADR-058）

litellm 每请求算一遍 `response_cost`：稳态 6.4 ms/请求，内部 125 万次 `re.search`（cost map）+ zero-cost 诊断。官方开关不存在，唯一路径是 stub：`LITELLM_COST_CALC=off`（默认）+ `_disable_litellm_cost_calc()` 把四个入口替换为返回 0.0。我们计费走 `billing.compute_cost`（读 DB 单价自算），不依赖 litellm cost。

| 场景 | cost 开 | 关 | 差值 | RPS（关/开） |
|---|---|---|---|---|
| 非流式 C=16 | 38.1 ms | 34.0 ms | **−4.0 ms（−11%）** | +13.3% |
| 流式 C=16 | 87.7 ms | 67.6 ms | **−20.1 ms（−23%）** | +33.3% |
| 空壳直调 `acompletion`（无网关） | 14.1 / 40.6 ms | 8.6 / 18.8 ms | −5.5 / −21.9 ms | — |

**风险边界**：若启用 litellm 预算/花费回调必须设 `LITELLM_COST_CALC=on`；回滚 = 改 env 重启。

---

## §7 多实例形态（task-55，实测 15 臂）

### 7.1 形态

`uvicorn --workers N≥2` 在 Windows 起不来（WinError 5，与本仓代码无关）→ 多进程 = **多个独立 uvicorn 进程 + 前置 LB**。`--workers` 共享进程内状态（catalog 快照、限流、WAL）本来就不该用于多进程部署。

### 7.2 扩展性矩阵（C=128，warmup 15 s + 测 25 s，L=50 ms，cost 已关）

| 臂 | RPS | p50 / p99 | 网关核 | msCPU/ok | rows/req |
|---|---|---|---|---|---|
| A1_L_classic（单进程基线） | 45.9 | 1998 / 9675 ms | 1.25 | 27.3 | 1.000 |
| B2_L_classic（2 进程共享库） | 64.4 | 1546 / 5942 ms | 2.32 | 36.0 | 0.998 |
| B4_L_classic | 82.1 | 1157 / 5378 ms | 3.78 | 46.1 | 0.979 |
| B8_L_classic | 94.5 | 1060 / 4466 ms | 5.18 | 54.8 | **0.951** |
| D2_L_classic（各自库） | 70.0 | 1446 / 5950 ms | 2.49 | 35.6 | 1.000 |
| D8_L_classic | 105.3 | 1180 / 1645 ms | 5.40 | 51.3 | 1.000 |
| **D8_L_memory（最优臂）** | **179.0** | **699 / 1047 ms** | 5.27 | **29.4** | 1.000 |

- 扩展次线性：8 进程仅 2.30×，墙是核与超线程（合计只吃到 5.2–5.4 核），不是 SQLite。
- **共享 DB 的隐性代价是静默丢日志**：SQLITE_BUSY 全程 0，但共享库臂 rows/req 掉到 **0.951**（8 进程丢 4.9%），各自库恒 1.000——运维必须按 rows/req < 1 告警，不能等报错。
- 最优组合 D8 + memory：179.0 RPS（3.9×）、29.4 msCPU/ok（−43%）、p99 1047 ms。

### 7.3 共享库 + memory 的 404：配置可见性，不是绑定错误

memory 模式下 admin 写不触发本进程 reload，快照按轮询/TTL 刷新 ⇒ 新绑定最长一个轮询周期不可见（实测 57.6% 404 后把 warmup 拉过一个轮询周期即 0 404、170.1 RPS）。**生产含义：`CATALOG_POLL_INTERVAL_MS=2000`；不要指望写入方进程「马上生效」**。

### 7.4 推荐配置（单机多进程形态）

**4–8 个独立 uvicorn 进程 + 前置 LB（按 api key 亲和）**；**每副本各自本地库**；`USAGE_PIPELINE=memory`；`LITELLM_COST_CALC=off`；`CATALOG_POLL_INTERVAL_MS=2000`；`--timeout-keep-alive 75`。进程数上界未验证（8 进程时每进程剩 0.65 核）。

---

## §8 到 1000 RPS 的账（Windows 口径，已被 §12 取代）

| 方案 | 算术 | 结论 |
|---|---|---|
| 优化后单进程 | ~15 ms → 47–67 RPS | 单进程天花板 |
| 优化后 8 实例 | 179.0 RPS，29.4 msCPU | 实测最优形态 |
| httpx-native 传输 | 流式饱和吞吐 2.84× | 已落地（ADR-065/078，§12） |
| 水平扩展 | 179–270 RPS/台 × 4–6 台 | 1000 RPS 的现实路径 |

---

## §9 落地清单（2026-11-18 已全部闭环）

六项动作全部完成或被取代：①memory 管线 + 目录轮询收紧；②`LITELLM_COST_CALC=off`；③`--timeout-keep-alive 75`；④httptools；⑤httpx-native 传输（ADR-065/078）；⑥requirements 对齐（orjson 已补）。`requirements.lock` 的 pip-compile 全量重编译仍未做（换 Python 版本时须重跑）。后续状态见 §12。

---

## §10 Linux 开环三臂基准与 agentgateway 对照（2026-11-16 增补）

> **环境**：WSL2 Ubuntu 26.04（8 vCPU / 7.8 GB，Python 3.14.4），与 §2–§8 的 Windows 同机争用环境**不同机**，绝对值不可互比；本节内组间比值可比。原始数据与脚本：`docs/scratch/bench-agentgateway/`（gitignore）。
>
> **口径**：**开环**——请求按固定到达率到点就发，延迟从**应发时刻**起算（排队计入延迟，纠正 coordinated omission）；每点 20 s；CPU/请求 = 被测进程 CPU 时间增量 ÷ 完成请求数（不受排队影响，是最硬的指标）。上游 mock 是 uvicorn 单进程 ASGI（线程化 mock 在负载下崩溃，见 §10.7）。

### 10.1 非流式（1KB prompt，max_tokens=64）

| 臂 | 10 RPS | 20 RPS | 40 RPS | 80 RPS | p50@40 | p99@40 | RSS@40 | 线程/fd@40 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| direct（直连 mock） | 0.80 ms | 1.48 ms | 0.80 ms | 0.98 ms | 3.5 ms | 9.7 ms | 55.7 MB | 5 / 15 |
| 本网关（memory 管线） | **11.45 ms** | **10.12 ms** | **9.13 ms** | 21.99 ms（塌陷：34.7 RPS） | 11.7 ms | 78.6 ms | 143.2 MB | 6 / 31 |
| agentgateway v1.7.0 | 1.10 ms | 1.50 ms | 1.21 ms | **1.31 ms** | 5.2 ms | 13.7 ms | 37.8 MB | 17 / 22 |

### 10.2 流式（400 chunk × 4B，末块带 usage）

| 臂 | 10 RPS | 20 RPS | 40 RPS | CPU/请求 | 现象 |
|---|---:|---:|---:|---:|---|
| direct | 10.03 | 19.70 | 39.98 | 17–29 ms | 正常 |
| 本网关 | **2.44（崩）** | **2.11** | **2.90** | **370–472 ms** | p50 53–167 s、烧满 1 核、fd 284 |
| agentgateway | 10.03 | 20.01 | 32.62 | 20–34 ms | 正常（40 RPS 开始排队） |

单点复测（本网关 2 RPS）：p50 109.6 ms、CPU/请求 107.5 ms、busy 0.22 核 → 不是超时，是**每 chunk 成本 × 并发**把单核打满：400 chunk × ~0.27 ms ≈ 108 ms/请求，10 RPS 即需 1.08 核。

**B0 修复后复测（同日，task-159 落地，gateway.py `iter_sse` 直通 `aiter_bytes`）**：2 RPS → 2.07、CPU/请求 **30.0 ms**；10 RPS → **10.05（原 2.44）、CPU/请求 31.9 ms**；复跑稳定。吞吐 4.1×、CPU/请求 −72%，与 agentgateway 同场景只差 ~1.2×。改写语义全保留；上表塌陷数字为修复前基线，保留作对照。

### 10.3 空载资源与塌陷阈值

| 臂 | 空载 CPU | 空载 RSS | 塌陷阈值（非流式） | 说明 |
|---|---:|---:|---|---|
| direct | 0.07 % | 55.7 MB | 未测出（≥80 RPS） | 0.8–1.5 ms/请求是任何方案都要付的「上游底噪」 |
| 本网关(memory) | 0.20 % | **116.8 MB** | **34–65 RPS 之间**（80 崩） | 单进程单核模型：CPU/请求 × RPS > 1 核即塌 |
| agentgateway | 0.07 % | **31.1 MB** | 未测出（≥80 RPS） | tokio 多 worker（17 线程常驻） |

### 10.4 函数级归因（py-spy，闭集合复核）

同环境严格闭集合中位数：classic S1 36.01 ms/请求、memory 14.58、memory+split 21.02；空载对照（只打 /health）1.45–1.69 ms → **ASGI 栈固定开销 ≈1.5 ms**。

火焰图（memory 4689 样本）：SQL/ORM 20.5%（后台 sink 批量 INSERT 构造）、httpx 转发 17.5%、事件循环 13.3%、FastAPI 路由 7.2%、JSON 4.1%、WAL 0.5%、鉴权+usage 抽取 0.7%、其它 36%；classic 是 53.4% SQL。结论：memory 管线的数据库红利已吃干，剩余 ~80% 是框架开销。日志脱敏 `redact_text`+`is_sensitive_key` ~2%，是当时识别的下一个果子（后续 P1.5-D/P2 已处理渲染与 body 链）。

流式慢的机制定位（修复前基线）：`iter_sse` 用 `aiter_lines()` 逐行 + 每行一次完整 JSON 往返，单核 ~3.5k chunk/s 封顶；agentgateway 每 chunk ~0.05 ms，我们 ~0.27 ms（5×）。已于 task-159 修复（§10.2 补记）。

### 10.5 agentgateway 实测遥测事实（接入计费的关键约束）

用本地 mock 实测其 v1.7.0 行为（`probe_agw_metrics.sh`）：

1. **非流式**请求的访问日志带 `gen_ai.usage.input_tokens=16 gen_ai.usage.output_tokens=8`（与上游 usage 一致）及 model/provider/duration/status。
2. **流式**请求的日志**没有 usage 字段**——流式 token 用量拿不到。
3. `/metrics` 只有通用指标，**没有任何 token/cost/gen_ai 指标**。（修正：docs-1.7 源码已有 `gen_ai_client_token_usage`/`gen_ai_client_cost` 与流式 usage 抽取——实测二进制与源码存在版本差，见 §11.4。）

含义：客户端直连 agentgateway 时流式计费没有现成数据源——要么控制面留在链路上抽 usage（回到老问题），要么上游按 `stream_options.include_usage` 返回、由旁路消费日志/OTel（流式需最新构建复测，§11.4）。

### 10.6 迁移评估（数据驱动的初判）

1. **双跳算术否决「Python 前置 agentgateway」**：网关自身 + agw ≈ 10.5 ms+，比直连更慢——前置转发吃掉全部收益。
2. **收益边界**：非流式上限 ~1.5 ms ASGI 固定开销 + 请求管线；流式的每帧成本才是大头，且不换语言也能修（task-159 已修）。
3. **能力缺口**：组合优先级/观察期排序、冷却排期状态机、多候选跨渠道回退、分布式限流后端无对应物；改写可用 CEL 表达、每 key 预算是内置能力——逐项对照见 §11.2。
4. **路线**：B0 修流式（✅）→ B1 非流式连接复用与序列化（✅ P0/P1）→ B2 若仍需 <2 ms，把「纯转发+SSE 透传」独立成原生数据面（agentgateway 或自写），控制面留 Python。是否采纳 agentgateway 在 B2 时再拍板。

### 10.7 测量陷阱（复现必读）

1. 线程化 mock 高并发下崩溃 → 换 uvicorn ASGI mock。**流式对比结论以 uvicorn mock 为准；线程 mock 只能跑到 ~20 RPS。**
2. memory 管线鉴权读进程内目录快照，seed 在启动后建 key → 全量 401；压测必须用「保留 DB」的 spawn。
3. WSL2 会在每条 `wsl -e` 命令结束时整体重启——**压测必须一条命令跑完**。
4. `--out` 相对路径落在 cwd，不是脚本目录。
5. Windows 宿主抢占在宿主侧，taskset 消不掉；绝对值按 ±30% 读，倍数关系可信。

---

## §11 agentgateway 接入方案与逐功能对照（2026-11-16 增补）

> **证据**：`docs/scratch/bench-agentgateway/RESEARCH-agentgateway-integration.md`（本地二进制 `26f5aebe` v1.7.0 + 官方源码 docs-1.7 浅克隆）。注意实测二进制与 docs 源码存在版本差（§11.4）。

### 11.1 接入形态：没有 SDK，只有三种进程外形态

独立 Rust 静态二进制（musl），无官方 SDK，不能当 Envoy 插件（ext_proc/ext_authz/RLS 均只做 gRPC 客户端）。可用形态：

| 形态 | 配置方式 | 动态性 | 适用 |
|---|---|---|---|
| Standalone 二进制 + YAML（`-f config.yaml`） | config watch（250ms debounce）热重载；校验失败保留旧配置 | 文件级热更 | 单机/基准/简单转发 |
| Kubernetes + 内置 controller | Gateway API + 自有 CRD，xDS ADS 推送 | xDS 推送 | 云原生多租户 |
| 独立 xDS 客户端模式 | `XDS_ADDRESS`，任何 ADS 控制面都能推配置 | xDS 推送 | 自建控制面接管 |

Admin 端口可编程 REST：`GET /config_dump`、`POST /api/config`（整文件写）、细粒度 `CRUD /api/config/resources/{kind}/{id}`、`GET /api/costs/models` + `POST /api/costs/refresh-base`、`GET /api/budgets/status`。REST 适合低频编排，高频逐请求决策走 §11.3 的钩子。

### 11.2 逐功能对照（我们要的 27 项 ↔ 它有的）

| 我们的能力 | agw 状态 | 说明 / 替代路径 |
|---|---|---|
| POST /v1/chat/completions、GET /v1/models | ✅ 原生 | lib.rs:153-155,158-182 |
| 协议转换（Anthropic/Gemini/Responses ↔ OpenAI） | ✅ **它有，我们没有** | conversion/ 三套——接 agw 白得一个待建能力 |
| 多 provider 凭证注入 / hostOverride / custom provider | ✅ | 与渠道 base_url+api_key 等价 |
| 模型路由 + 负载均衡 | ✅ | ModelRoute header_matches + rendezvous-hash 权重 |
| 组合优先级 / ADR-077 策略排序 / 观察期 | ❌ 无 | 必须 Python 决策钩子（§11.3 callout）实现 |
| 渠道级请求/响应/头改写 | ⚠️ 可表达 | CEL transformation；渠道语义需映射成 per-backend CEL 规则 |
| 首字节前重试 | ✅ 语义相同 | 请求体缓冲重放；**仅同后端串行重放——无多候选竞速、无跨渠道回退** |
| 请求超时 | ✅ | request_timeout |
| API key 鉴权 | ✅ 原生或外包 | apikey.rs / jwt / oidc / basicauth |
| 每 key 预算（金额/token） | ✅ | http/budget——组合计费的近似替代 |
| 限流 | ⚠️ | 本地有；分布式需自建 Envoy-RLS |
| token/cost 遥测 | ✅（口径不同） | `gen_ai_client_cost`；与我们的成本口径不同，账本仍留 Python |
| 冷却排期 / 时间表 / 随时间变权重 | ❌ 无 | 只有被动 outlier ejection——冷却状态机需 callout 服务持有 |
| 在飞上限准入（per-worker，DB 热改） | ⚠️ | 无节点级 cap；有 requests_shed + 分布式限流（需自建 RLS） |
| 流式 SSE 逐 chunk usage | ✅（形态正是我们要的） | 字节原样透传 + 旁路逐 chunk 解码——task-159 想做的它已做对 |
| session affinity / TLS / WS(Realtime) / MCP / A2A / prompt-cache 感知 | ✅ 白得 | sessionaffinity.rs、backendtls.rs、mcp/、a2a/ |
| 语义缓存 / 主动健康探测 / 发票级账单 / 每键细粒度配置 API | ❌ 无 | 缺口清单共 10 项 |

### 11.3 推荐拓扑（若采纳）：旁路双钩子 + 低频编排

数据面 = agentgateway；Python 控制面三件：

1. **VirtualModelCallout**：每请求 HTTP 调 Python 服务选模型——组合排序/冷却状态机/观察期都在这个 Python 服务里，进程内状态天然满足冷却状态机要求。
2. **ext_authz gRPC**：每请求鉴权/配额/计费闸门；接受 agw 原生 apikey+jwt 时可省，鉴权并入 callout。
3. **配置/价目编排**（低频）：admin REST 或 xDS。

计费账本留 Python：消费 agw 日志（sqlite/postgres，带 analytics 列）或 OTel，对账。**代价**：每请求 +1~2 次内网 RPC（callout ~0.5–2 ms）→ 合计 ~2–4 ms，仍显著低于现状；Python 从**数据路径**退到**旁路**（只做决策，不碰 payload）。

### 11.4 「用户直连」拓扑与流式计费可行性（含版本差修正）

client 直连 agw，Python 仅旁路：非流式计费数据现成。流式：docs-1.7 源码 passthrough 路径逐 chunk 抽 usage、metrics 有 token/cost 指标——但实测 `26f5aebe` 二进制**流式日志无 usage**。属**版本差**（一周内遥测行为已变）。结论：**直连方案的流式计费须用最新构建复测**；若仍无 usage，退化为「agw 内置预算闸门 + Python 旁路按日志/OTel 对账（有误差）」。

### 11.5 决策建议

维持 §10.6 路线：**B0 修流式（✅）→ B1 非流式连接复用与序列化（✅ P0/P1/P2）→ B2 只有当仍需 <2 ms 数据面时才引入 agw**（形态 = §11.3 旁路双钩子）。理由：引入 agw 换来非流式延迟与白得能力（协议转换/MCP/A2A/预算/会话亲和），付出两套控制面语义映射 + 上游快速演进风险（一周内遥测已变）+ 冷却/观察期语义只能在 callout 里重建。B1 落地后用同一开环矩阵复测，用数据决定 B2。

---

## §12 五臂基准与 P0→P2 优化（2026-11-18/19，当前权威结论）

> **环境与工具**：tst WSL2 Ubuntu 26.04，16 逻辑核，Python 3.14.4，uvicorn 0.54.0（httptools+uvloop 单 worker）、SQLite WAL、`USAGE_PIPELINE=memory`、httpx 传输；agentgateway v1.7.0（构建 26f5aebe）。开环测法，核隔离 mock 0-2 / 网关 3-9 / 压测 10-15；CPU/请求 = `/proc` jiffies 增量 ÷ 窗口成功数。负载链天花板自证：直连臂两工况均 1024 QPS 线速（p50≈1ms）。工具与原始数据：`~/workspace/labs/gwbench/`；完整矩阵与 py-spy 归因见 `docs/scratch/bench-5arm-2026-11-18.md`。

### 12.1 五臂判定（优化前基线，回答「能否跑赢 agentgateway」）

| 工况（50K tok） | slipstream 直连名 | slipstream 裸名直通 | agentgateway |
|---|---|---|---|
| 非流式 CPU/req | 4.97ms | — | ~1.0ms（两次交错 0.9–1.2） |
| 非流式容量 | 膝点 ~230 RPS（429 shedding，非 CPU 崩） | — | 1024 线速 |
| 流式 CPU/req（2048 帧） | **232ms** | 14.25ms | **3.36ms** |
| 生产长流（300K tok/20480 帧） | **0 完成**（2.2 CPU-s/流） | — | 线速 21.5ms |
| 非流式 1.2MB | 17.9ms，30–60 RPS 塌 | — | 2.4ms，128 线速 |

现状答案：**全面跑不赢**。根因三件：①直连名流式逐帧改写（ADR-078 前的生产默认，~106µs/帧）；②memory 管线 WAL 无条件双份 body 全量序列化（无视截断与策略）；③「零 SQL」管线的请求路径残余 SQL（每请求 get_db 会话 + route_stats 过期突刺 + 熔断 failure 无条件写）。

### 12.2 P0→P2 落地与实测（同矩阵复测，全部保持计费/日志/错误映射/重试语义）

| 改动 | 内容 | 实测 |
|---|---|---|
| **P0-R1**（ADR-078） | 流式改写字节化：块级批发 + needle 替换 + 解析兜底；`aiter_bytes` 去 chunk_size（修 httpx ByteChunker 吞 error 前缀 bug） | 直连名流式 232 → **13.8ms**（17×）；生产长流恢复全线完成 |
| **P0-R2**（ADR-079） | body 行先截断渲染再进 WAL（`build_body_record` + `_bound_deep`），capture 决策仍归策略 | 1.2MB 档 json 占比 32.7%→待复测；200KB 非流式 −15% |
| **P0-R3** | orjson 进 requirements.txt/lock（代码早已就绪） | 非流式 −20%（单独 A/B） |
| **P1-R5** | 跟踪 bytes 直进 `jsonfast.loads`（orjson 优先） | 流式 CPU 13.8 → 11.2ms |
| **P1-R6** | `attach_request_id` 纯 ASGI 化；`max_body_bytes` 生效（413）；在飞闸门前移到鉴权/body 解析之前（释放收归依赖栈） | 入口层开销 −；429/413 拒绝不再付解析成本 |
| **P1-R8** | WAL fsync 移出事件循环（后台 fsync 线程 + seq 快照协议；100ms 持久性窗口与 kill -9 回放契约不变） | append 最大延迟 2.275 → 0.567ms（ext4 微基准） |
| **P1-R4** | route_stats 过期改 stale-while-revalidate + 单飞后台刷新；record_failure 仅状态迁移才写（record_success 原有闸门保持） | 稳态请求路径 SQL → 0（冷缓存首查除外） |
| **P2**（ADR-080，2026-11-19） | 请求侧字节域快路径（六条资格门 + needle 替换 + 注入，回退 dict 路径）+ 流式跟踪字节门 + `_body`/快照切片 + **泄漏修复**（close 置空 Response._request/stream） | **终验收（安静窗口）**：非流式 240 QPS 线速 **3.38ms**、膝点 **~300 RPS**（304 完成 82%、320 达成 305/s）；流式 64 QPS 线速 **12.9ms**、TTFT 8.8ms；重载长流 p50 91ms、**RSS HWM 仅 138MB**；**多波爬梯消失**（三波 settle 286→308→302MB 持平，修复前 619→808→990 每波 +180） |

**对 agentgateway（安静窗口终验收）**：非流式 3.38 vs 1.0ms（**3.4×**）、流式 12.9 vs 3.4ms（**3.8×**）——从 68×/5× 收敛到 3–5×；残余差距 = 每帧地板（Python ~5µs vs Rust ~0.1µs）+ 框架底盘（~1.5ms ASGI + ~1.0ms httpx/aiosqlite），对应 ADR-076 阶段 B 门槛，不在 Python 内解决。

### 12.3 P1.5：膝点与 RSS 归因闭环（py-spy @拐点 + memray 两趟）

**膝点**：加密梯度（192–320 QPS）+ 拐点 py-spy + 抬闸对照实验（`GATEWAY_INFLIGHT_CAP=1024`）。结论：P1 落地后膝点已从 ~230 上移至 **~290–300 RPS**；296 起 in-flight 顶格 256 触发 429 shedding。**抬闸对照证明闸门在保护吞吐**：cap=1024 去掉 shedding 后吞吐反降（152–199 ok/s、p50 3–10s）。拐点处主循环 92.9% busy 里：httpx 组 40.7%（连接池 O(N) 存活扫描 ~10%、请求体二次 stdlib JSON 编码 8.3%——P2 已消）、stdlib json 15.1%（P2 字节门后大降）、aiosqlite 8.2%（旁路线程）、GC 0%、WAL fsync 0%（R8 生效）。

**RSS（memray 两趟）**：重载流平台 703MB = ~250MB 真实活对象 + ~450MB 分配器滞留。P1.5 修复（close 置空 + `_body` 释放 + relay 帽收紧）后：999 → 851MB。P2 后进一步降至 settle ~783MB（泄漏修复后多波爬梯消失，见 §12.3b）。

**tiktoken 估算**（P1.5-D，`89924d7`；媒体常数见 §12.2-P2/ADR-080 轮）：≤128K 字符走真分词，超出走脚本折算；1.2MB finalize 37→1.15ms、4MB 极端 105→23.7ms。`usage_estimated` 红线不变；媒体 part 常数 1000 tok（用户裁定，消灭 base64 当文本的 350× 高估）。

**ledger/service.py fsync 对称改造**（默认 off 管线）：与 usage 管线同款 `background_sync` 模式，32 项定向测试过。

### 12.3b P2 泄漏修复终验收（2026-11-19，安静窗口，金丝雀直连臂 240 线速确认箱态）

P1.5 的 close 置空修复了 Response 链，但 ffrefs 归因（id 复活 + gc.get_referrers）发现 **P2 快路径的改写 bytes 走的是另一条链**：`gateway.py:275 raw.replace 结果 ← httpx Request ← Response._request / Response.stream(BoundAsyncStream)`——每流 1.17MiB × 320 条（两波）= 366MiB 在 settle 20s 后仍存活。修复：close() 里 aclose 后再置空 `response._request` 与 `response.stream`（提交 `90ff9d8`）。终验收：

| 指标 | 泄漏期（同箱同探针） | 修复后终验收 |
|---|---|---|
| 非流式 240 QPS | 1257/2880（44%，cpu 10.76ms） | **2880/2880 线速，cpu 3.38ms** |
| 非流式膝点 | 240 起 shed | **~300+ RPS**（304 完成 82%、320 达成 305/s） |
| 流式 64 QPS（快路径） | — | **线速 64、p50 13.6ms、TTFT 8.8ms、cpu 12.9ms** |
| 重载长流 RSS HWM | — | **138MB**（修复前同场景 237MB） |
| **三波爬梯** | 619→808→990（+180/波不收敛） | **286→308→302（波 2 起持平，爬梯消失）** |
| 功能核验 | — | 27/28（存量时序偶发）+ 30s 混合 555 请求零失败 |

多波 RSS 爬梯消失 = 泄漏修复的直接证据：波次间只余有界活对象（body 切片 + 在飞缓冲），非流式后 settle 392MB、重载 settle 727MB——残留为活对象 + 有界滞留，无爬梯。

### 12.4 内存治理：与 agentgateway 对比、回落行为与 allocator 权衡（2026-11-18）

#### 12.4.1 背靠背对比（同机同时段、同一探针七阶段，VmRSS，MB；P1.5 后基线）

| 阶段 | slipstream | agentgateway v1.7.0 | 比值 | ag 回落？ | slipstream 回落？ |
|---|---|---|---|---|---|
| 空载（启动后静置） | 119.1 | 31.6 | 3.8× | — | — |
| 非流式 128 QPS 峰值 | 400.3 | 43.3 | 9.2× | — | — |
| ↳ 停 20s | 413.5（不回落） | 38.4 | 10.8× | ✅ | ❌ |
| 流式 64 QPS 峰值 | 541.2 | 44.2 | 12.2× | — | — |
| ↳ 停 20s | 545.7（不回落） | 38.3 | 14.3× | ✅ | ❌ |
| 满载（300K tok/20480 帧 ×16 并发）峰值 | 903.6 | 64.4 | 14× | — | — |
| ↳ 停 30s | 870.7（几乎不回落） | 38.4 | 22.7× | ✅ **完全回落** | ❌ |
| HWM（全程水位） | 903.2 | 64.3 | 14× | — | — |
| 折算：满载每并发流 | ~49 MB/流 | ~2 MB/流 | 24× | — | — |

P2 泄漏修复后多波爬梯消失（§12.3b）；残余平台 = 在飞 body 两份拷贝（dict + bytes，设计使然）+ 有界滞留。空载 3.8× 是 Python 运行时地板（litellm 装而未载不占 RSS；同类 LiteLLM proxy 空载 ~500MB）。

#### 12.4.2 回落行为：爬梯式 vs 呼吸式

**slipstream 的 RSS 是爬梯式**：每个新高水位在负载归零后持续驻留。机制：free 后的 1.2MB 级块进入 glibc 空闲链表而非还给 OS；自动 trim 只归还堆顶连续空闲区，堆中部空闲块够不着。**它不是泄漏**——内存被后续分配复用，平台 ≈ 历史最高并发工作集 + 碎片开销；风险仅在于 cgroup/OOM 按全量 RSS 计费与监控观感（运维口径：以「首轮负载后的 RSS」为基线，对超出基线的增长告警，那才是真泄漏信号）。agentgateway 是呼吸式（free 即 munmap 还 OS），归零即回 38MB。**P2 泄漏修复后多波爬梯消失（§12.3b）**。

#### 12.4.3 allocator env 权衡（全部实测，不做默认推荐）

| 配置 | 重载后平台 RSS | HWM | 延迟代价 |
|---|---|---|---|
| 默认（无 env，P1.5 后） | 851MB | 888-903MB | 0（膝点 ~310 线速基准） |
| `MALLOC_ARENA_MAX=2`+`TRIM=128KB` | 750MB | 963MB | 未测出（收益也弱） |
| +`MMAP_THRESHOLD=128KB` | **400MB** | 786MB | **大 body p50 数倍劣化**（缺页风暴，非流式 256 完成率 86%→51%） |
| +`MMAP_THRESHOLD=64KB` | 744MB | 961MB | 同上且封顶更差 |
| `PYTHONMALLOC=mimalloc`（3.14 内置） | 778MB（且 settle 有回落行为） | 916MB | **膝点 ~310→~285**（304 QPS 从线速变 shedding：2307/3040、p50 530ms）；亚膝点延迟无差异 |
| jemalloc LD_PRELOAD（默认配置） | 971MB | 1105MB | 比 glibc 差；社区「-50%」案例依赖配置三件套（`PYTHONMALLOC=malloc` + `background_thread` + `dirty_decay_ms`），**待正确配置复测**（外部咨询 2026-11-19 指出默认配置下 decay 只在分配活动时推进，空闲即停——正是我们「settle 不回落」的症状） |

结论：**生产默认不启用 allocator env**；四条 glibc/mimalloc 路线已测「内存 × 延迟」两轴不可兼得。jemalloc 三件套重测待做。glibc 动态 mmap 阈值机制与同类案例（CPython #109534、uvicorn #2078）见 `docs/scratch/research/python-gateway-memory-2026-11-18.md`；内存滞留成熟方案综述见 `docs/scratch/research/memory-retention-solutions-2026-11-19.md`。

#### 12.4.4 把内存压下来的现实选项（按代价排，2026-11-19 更新）

1. **多实例分摊（零代码）**：ADR-060 形态下每进程 in-flight 减半 → RSS 平台近似减半；水平扩容本来就要多进程，内存顺势解决。
2. **调低在飞上限（零代码）**：cap 256→128，重载 RSS 平台近似对半（抬闸对照已证明 cap 不影响吞吐拐点，§12.3）。
3. ~~周期 malloc_trim(0)~~：**已实测无效**——env 门控每 5s trim 的探针与基线无差异（settle 844 vs 871MB），240 QPS 延迟无伤但也无收益；trim 只归还堆顶连续空闲区，对堆中部空闲块无能为力。
4. **P2 字节域全链路（已落地，ADR-080）**：每在飞请求的大块分配 3→2 份——实测重载平台 999 → 783MB、HWM 903 → 834（−8~22%）；进一步压到 <500MB 需消掉剩余的 dict 拷贝（仅按需解析路由键）。
5. **jemalloc 三件套重测（待办）**：`PYTHONMALLOC=malloc` + `MALLOC_CONF=background_thread:true,narenas:2,dirty_decay_ms:1000`——外部咨询指出我们的 jemalloc 实验用了默认配置（dirty_decay 空闲即停），结论「比 glibc 差」需重验。
6. **不推荐**：MMAP_THRESHOLD 固定（延迟崩）；mimalloc（膝点代价）。

### 12.5 功能核验（labs 端到端，27-28/28 PASS）

真进程 + mock 上游跑通：健康/双面鉴权（API key 401、管理 JWT）、/v1/models、目录、非流式（usage 上游真实值）、流式（[DONE]、帧内 model 改写为直连名、usage 帧、SSE content-type）、改写链路三验（请求头/请求体/响应头经 mock 回显确认到达）、未知模型 404、**413 body 上限**（R6 新行为）、渠道 CRUD、**熔断冷却目录可见 + 开启后快速失败**、日志落库（零 SQL 异步可见）、看板汇总、**WAL kill -9 回放零丢失**、30s 混合负载零失败。唯一偶发失败：目录轮询时序检查（存量，轮询窗口边界竞态）。脚本：`labs/gwbench/runner/functional_check.py`。

### 12.6 测试与回归

全量 pytest（labs，-n 8）：1105→1126 passed + 存量随机 flake 0–3 个（基线 HEAD 同样出现、失败集每轮轮换、串行复跑全绿——三轮全量+基线对照钉死为存量）。新增回归：`test_body_limit.py`（R6，8 条）、`test_background_sync_fsyncs_without_an_inline_flush`（R8）、`test_breaker_api.py`/`test_usage_pipeline_wiring.py` 扩展（R4）、`test_httpx_transport.py` bytes 消费断言 + P2 快路径 17 条（ADR-078/080）、`test_billing_estimate.py` 媒体常数 3 条（P1.5-D）。本轮修掉的实现 bug：`aiter_bytes(chunk_size)` 吞 error 安全前缀（真 bug，测试抓出）；直连名流式慢路径（ADR-078 的动因）；P2 快路径 bytes 泄漏（ffrefs 归因，`90ff9d8`）。

### 12.7 与 ADR-076 阶段门槛对照

| 阶段 A 门槛 | 现状 |
|---|---|
| 每请求 CPU ≤8ms（Linux 开环） | 非流式 2.65–4.56ms ✅（P1.5 后膝点处 2.65ms）；流式 10.2–11.2ms（2048 帧/50K tok 工况）△ |
| 70% 容量下网关增量 P99 <20ms | 膝点已上移至 ~310 RPS（§12.3）；304 QPS 非流式 p99 55.9ms 含排队、240 QPS p99 151ms 为末梢抖动——增量口径未单独测，△ |
| 过载快速失败而非塌陷 | ✅（429 shedding 全程可控；抬闸对照证明闸门在保护吞吐，未再见 §10 的吞吐塌陷） |
| B2（原生数据面）触发评估 | 非流式未达 <2ms，差距 3–5× 且每帧/框架地板明确——维持 ADR-076「B1 后用数据决定」，用户裁定正常推进、数据留档（2026-11-18） |

---

## §13 数据、复现与原始报告对应关系

> 原编号 §10（与 §10 Linux 三臂撞号），2026-11-18 顺延为 §13；文中「§10.x」引用除特别注明外均指 §10 Linux 三臂基准。

### 13.1 本文章节 → 原始报告

- §0 结论、§2 阶梯、§3 归因、§4 传输、§8 账、§9 清单 ← `PERF_CEILING_REPORT.md`（§一–§十）；
- §7 多实例矩阵、§1.3 客户端饱和、§2 的 250 RPS 理论上界 ← `multiprocess_scaling.md`、`highconc_ceiling.md`；
- §6 ← `litellm_cost_calc.md`；§5 ← `memory_pipeline.md`；
- §3 的分段与消融数字 ← `task48_report.md`、`task49_conn_cache_audit.md`；§4 的传输层细节 ← `runtime_transport.md`；
- §1.6 的工况翻转案例 ← `streaming_cpu.md`（定稿版）、`task48_report.md` §7；环境自证反例 ← `runtime_transport.md`、`e5_section_900s_draft.md`、`results/transport/transport_ab.json`、`task54/REPORT.md`。

### 13.2 原始数据与脚本（相对 `.scratch/bench2/`）

| 内容 | 路径 |
|---|---|
| 单进程阶梯 | `results/highconc/summary.json`、`results/highconc/ladder_c*.json` |
| 零 SQL A/B / 缺失行证据 | `results/pipeline/memory_ab.json`（235 KB，含每轮 raw）、`results/pipeline/gate_a_missing_row.json` |
| cost 微基准与 profile | `results/litellm_cost_bench.json`、`results/litellm_cost_check.json`、`results/gw_self_profile.pstats` |
| 多进程 15 臂与 404 复跑 | `results/multiproc/multiworker.json`、`results/multiproc/_b8mem_recheck.json` |
| 传输层 / 运行日志 / 脚本 / 数据库 | `results/tp/*.json`、`logs/mw_matrix_152525.log`、`runner/{mw_bench,mw_client,mw_mock,litellm_cost_bench,litellm_cost_calc_check,gw_self_server}.py`、`db/pipeline/`（六轮）与 `db/seeds/seed.db` |

### 13.3 关键复现命令

~~~bash
cd slipstream
# cost 关闭的前后对照（4 个真实请求，非压测）
./api/.venv/Scripts/python.exe .scratch/bench2/runner/litellm_cost_calc_check.py --out .scratch/bench2/results/litellm_cost_check.json
# 共享库 + memory 的 404 复跑（warmup 45 s 后应 ok=1.0）
./api/.venv/Scripts/python.exe -u .scratch/bench2/runner/mw_bench.py --only B8_L_memory --warmup-s 45 --duration 25 --out .scratch/bench2/results/multiproc/_b8mem_recheck.json
~~~

跑任何压测前先抢锁：`O_EXCL` 创建 `.scratch/bench2/.bench.lock`，已存在就等，跑完 finally 删除；测试端口 ≥3690、mock 上游 3980+，不得触碰生产 3680。

### 13.4 与 SSOT 的关系（治理）

- 本文是**证据型测量报告**，不替代 `ssot.md`：机制、字段、配置口径仍以 `ssot.md` 为准，冲突时以代码与测试为准并回来修本文。
- **计数纪律（2026-11-14 更新）**：事实源只写「全量套件最近一次实跑通过」，复现命令与当次结果放 `testing.md`。本文正文中的实测数字（RPS、CPU ms、p99 等）是测量结果，保留。
- **治理冲突已解决（2026-11-14）**：文档职责边界——`ssot.md` 机制与口径、`adr/` 决策理由、`INDEX.md` 文档入口、`GLOSSARY.md` 词义；旁支文档只允许「实测证据」（本文、`testing.md`）、「操作手册/专项说明」（`upstream_transport.md`、`upstream-reasoning.md`、`postgres.md`、`ui.md`、`data_plane_pipeline.md`）与「agent 工作流配置」（`AGENTS.md`、`docs/agents/`）三类。
- 早期曾有一份 `ssot-index.md` 承担总入口页，已于 2026-11-16 撤销并合并进 `INDEX.md`；凡引用它按 `INDEX.md` 理解。

### 13.5 证据归属

`.scratch/bench2/`、`docs/scratch/` 与各 `REPORT-*.md` 原始报告都位于**内部开发仓**，不随生产仓同步（`docs/scratch/` 在 `.gitignore` 内）。本文保留对它们的路径引用，仅为标明每个数字的出处；在本仓内这些路径不可解析，这是预期行为，不是待修的死链。需要复核原始数据时去开发仓。
