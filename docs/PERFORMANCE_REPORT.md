# 驭流（Slipstream）性能权威报告

> **状态**：四份原始性能报告的合并权威版（2026-11-14）。本文件不引入新测量，只做合并、对账与索引；数字与原始报告逐项对齐，无法确证的条目显式标注「待验证」。
>
> **证据来源**（**不在本仓**，见文末「证据归属」）：`.scratch/bench2/analysis/` 下的 `PERF_CEILING_REPORT.md`、`multiprocess_scaling.md`、`litellm_cost_calc.md`、`memory_pipeline.md`；原始数据在 `.scratch/bench2/results/**`，脚本在 `runner/**`。这些路径属于**开发仓**的压测工作区，生产仓不随附；引用它们是为标明数字出处，不是可点击的链接。
>
> **环境**：Windows 10，8 逻辑核 / 4 物理核，**15.76 GiB**（`psutil.virtual_memory().total` 实测）；uvicorn 0.54.0（httptools、单 worker、生产 app）、SQLite（WAL）、Python 3.13.14，上游是同机 mock。
>
> **口径纪律**：全部数字为**同机争用**环境下的实测（同机常驻生产网关 3680 与 DSH 3080 一起抢核），绝对值偏保守，**只用组内比值比较**；跨表数字不可直接相减（口径警告见 §2、§7）。

---

## §0 结论先行

| 维度 | 现状（默认：classic 管线 + litellm cost 开） | 优化后（memory 管线 + cost 关） | 到 1000 RPS 的路径 |
|---|---|---|---|
| 单进程吞吐 | 38.01 RPS（零延迟 mock、C=16、32.1 ms CPU/请求、1.22 核） | 45.9 RPS（50 ms mock、C=128、27.3 ms）；按算术 47–67 RPS | 单进程封顶，只能叠加实例 |
| 每请求 CPU | 32 ms；归因：框架 ~17 + litellm ~3.8 + SQL ~2.3 + pydantic ~2.1 + 其余 ~1 | 32 − 13.4（memory）− 4（cost）≈ **15 ms**（乐观）/ **21 ms**（审计估算） | httpx-native 后 8–15 ms |
| 8 进程各自库 | 105.3 RPS、51.3 msCPU/ok、p99 1645 ms | **179.0 RPS、29.4 msCPU/ok、p99 1047 ms**（+70% / −43% / −36%） | 179–272 RPS/台 × 4–6 台 |
| 日志完整性 | classic 流式 at-most-once：实测 1/2364 丢行（0.042%）；共享 SQLite 的 8 进程丢 4.9% | memory：6 轮 2799 请求 0 丢 0 重 | memory 是审计口径前提 |
| 1000 RPS 台数 | classic 8 进程口径 10 台 | 实测口径 6 台；核数上限口径 4 台 | 先做 httpx-native 可降到 2–3 台 |

1. **这台机器上「加并发」无效**：C 从 16 加到 1024，吞吐反而从 38.01 掉到 20.79–23.38 RPS，p50 从 0.36 s 涨到 13.5 s；瓶颈是「1 核 × 32 ms」，不是并发度。
2. **1024 并发在协议层能连、业务层不能同时处理**：1024 连接全部建立、2059 个请求全部完成（仅 3 次 read_error、0 connect 失败、0 个 5xx），但服务端实测 in-flight 峰值只有 343。
3. **三项已落地或已确认的收益**：memory 管线 −13.4 ms/请求（−21.7%）；移除 litellm cost 非流式 −4.0 ms、流式 −20.1 ms；httptools 已是默认（相对 h11 服务端每请求 CPU −37.6%，属已吃到）。
4. **多实例是次线性的**：8 进程只有 2.30×（105.3 vs 45.9），墙是 CPU 与超线程（8 进程合计只吃到 5.2–5.4 核），**不是 SQLite**（共享库 vs 各自库只差 10%）。
5. **两条不能忽略的非性能事实**：Windows 上 `uvicorn --workers N≥2` 在本机起不来（WinError 5）；多实例共享一份 SQLite 时 classic 会**静默丢行**（4.9%，且 SQLITE_BUSY 全程为 0，监控看不到）。
6. **2026-11-16 增补（Linux 开环三臂，§10）**：换到 Linux/WSL2 并按外部评审要求改**开环**测法后，memory 管线非流式 9.1–11.5 ms CPU/请求，agentgateway v1.7.0 同口径 1.1–1.5 ms（约 7×）；空载内存 116.8 vs 31.1 MB（3.7×）；80 RPS 本网关塌陷（34.7 RPS、p50 10.9 s），agentgateway 线性（80.03 RPS、p50 6 ms）。**流式差距更大**：本网关 107 ms/请求且 10 RPS 崩到 2.4 RPS（`iter_sse` 每 chunk 一次 JSON 往返），agentgateway 20–34 ms 稳定。
7. **2026-11-18 五臂基准 + P0/P1 落地（§12）**：直连名流式逐帧改写路径（ADR-078 前生产默认）实测 232 ms CPU/请求——五臂矩阵中「能否跑赢 agentgateway」的现状答案为全面跑不赢（流式 68×、非流式 5×、重载长流 0 完成）。P0（字节化改写+批发、body 有界渲染、orjson 装依赖）落地后：直连名流式 **13.8 ms（17×）**、生产长流从 0 完成恢复全线完成、非流式 4.2 ms；P1（纯 ASGI middleware、闸门前移、body 上限 413、WAL fsync 出循环、路由 SQL 单飞刷新、字节域跟踪）叠加后非流式 **4.6 ms**、流式 **11.2 ms**、重载长流 8 QPS p50 77 ms。内存：1.45GB 级 RSS 高水位定性为 glibc 对 MB 级 churn 的滞留（非泄漏），allocator env 可封顶但有延迟代价，**生产默认不启用**（§12.4）。功能核验 28/28（§12.5）。

---

## §1 方法与口径

### 1.1 这份报告支持哪些决策

本轮测量只回答三个决策：**单机上还值得加并发吗**（不值得）、**要不要开 memory 与关 cost**（要，两个开关各自独立可回滚）、**到 1000 RPS 需要几台**（4–6 台）。复现型基准的通行要求是「先写清结果支持哪个决策、再写方法」，本文按此组织，每张表只服务一个决策，不把性能数字转写成成本建议。

### 1.2 负载模型与其局限

全部实验都是 **closed-loop**（N 个客户端各自「发一个 → 等完整响应 → 再发下一个」）的并发阶梯，不是开环到达率。closed-loop 在服务器排队时会跟着降速，**会低估尾延迟**（协调遗漏 / coordinated omission）；因此所有 p99 只做组内比较，不能当 SLA。

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
- 顶级复现清单要求「钉住软件与数据版本、保留逐次原始输出、报告方差而不只是最好成绩」（[How to Run a Reliable AI Benchmark](https://sekin.in/how-to-run-a-reliable-ai-benchmark-and-reproduce-its-results)、[AI Evaluation Checklist](https://macmyths.com/ai-evaluation-checklist-how-to-report-reproducible-benchmark-results)）。本报告保留全部逐臂 JSON（§10.2），**方差一项未满足**（每臂 1 rep），**版本钉住一项未满足**（`requirements.txt` 13 个直接 pin 里 11 个与实测 venv 不一致，§9.2）。这是本文最大的复现缺口，且它决定「结论对生产是否成立」。
- **未测**：真实上游（全部 mock）、跨机部署、12/16 进程边界、Postgres、`--workers` 形态（本机不可用）、负载均衡器自身开销、上游 h2。
- **纪律**：重负载前抢 `.scratch/bench2/.bench.lock`（`O_EXCL` 独占创建，已存在就等，finally 删除），一次只跑一个压测；本轮锁持有区间 15:25:26 → 15:40:06。测试端口 ≥3690、mock 上游 3980+，不得触碰生产 3680。

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
- **C≥64 的「降」不是网关变慢，是压不进去**：同机客户端每请求 CPU 从 5 ms 涨到 38 ms，自身单线程事件循环满负荷；网关本身始终只拿到 0.85–1.22 核。
- **天花板是算术**：8 逻辑核 ÷ 32 ms ≈ 250 RPS（整机满载理论上界，见 `highconc_ceiling.md`）；1000 RPS 需要 32 个核，而这台机器只有 8 逻辑 / 4 物理核。内存不是墙：RSS 随并发线性涨（122 → 270 MB），1024 并发不 OOM。
- **口径警告**：本表与 §7 的 A1_L_classic（45.9 RPS / 27.3 ms）**不可直接比**——本表零延迟上游、C=16、cost 开；§7 是 50 ms 上游、C=128、cost 关。两张表各自内部可比。
- 与 `ssot.md` §11.2 的「12–13 rps」不冲突：那是 mock 0.6 s 流、SQL 瘦身（13 → 6 条）之前的口径；本表是零延迟 mock + SQL 已瘦身。两者不可对比；`ssot.md` §11.2 已改为指向本报告，不再自己维护性能数字。

---

## §3 CPU 归因与三项已落地优化

### 3.1 32 ms 花在哪（cProfile，task-48/49）

| 归因 | 量级 | 处置 |
|---|---|---|
| CPython / 框架本身（中间件、JSON、pydantic 校验、事件循环） | ~17 ms（53%） | 结构性，需改传输/精简校验才能动 |
| litellm 内部（cost 计算被 profile 低估，见 §6） | ~3.8 ms（11.8%，仅此口径） | 已移除 cost 计算（实测非流式 −4 ms / 流式 −20 ms） |
| SQLAlchemy / SQLite（鉴权、路由、日志 INSERT） | ~2.3 ms（7.1%） | 已上零 SQL 管线（§5） |
| pydantic 模型校验/序列化 | ~2.1 ms（6.5%） | 可评估（response_model 精简），未做 |
| 其余（Fernet 解密 0.7 ms、目录缓存等） | ~1 ms | 已做进程级缓存 |

分段（C=8、tier=50 ms、单请求 229 ms 网关内耗时；**各段不可相加，含重叠**）：中间件+依赖 45 + 鉴权 38 + 路由 66 + 上游 75 + ledger 24 + 未归因 20 ms。流式（731 ms）：中间件 115 + 鉴权 89 + 路由 107 + 上游 91 + ledger 122 + relay 220，另 finalize 133 ms。其中**路由 66 ms 与鉴权 38 ms 的大头都是每请求 SQL**，正是 §5 要消掉的部分。

### 3.2 三项优化：两项已实测落地，一项只定位未定标

| 优化 | 实测收益 | 证据 |
|---|---|---|
| 零 SQL 管线 `USAGE_PIPELINE=memory` | **−13.4 ms/请求（−21.7%）**，RPS +14.5%，p99 −26.6% | §5，3 轮中位数 |
| 移除 litellm 每请求 cost | 非流式 **−4.0 ms（−11%）**、流式 **−20.1 ms（−23%）**；吞吐 +13.3% / +33.3% | §6，墙钟微基准 |
| 中间件/框架固定开销 | 只完成归因：中间件+依赖解析 45 ms（非流式）/115 ms（流式）@C=8，占网关内 16–20%；pydantic 6.5% + fastapi/starlette 4.6% | `task48_report.md:73/:86/:169` |

> **待验证**：Lead 交办口径里的「中间件 6.6 ms/请求」在四份原始报告中**未见独立 A/B 证据**（`analysis/` 全量检索未命中该数值）。可参照的只有①消融「跳过 `request_logs` 写入」−5.24 ms/请求（−9.4%）、②WAL fsync 约 0.49 ms/请求（`task49_conn_cache_audit.md:125`）。在拿到复测前，本文不把 6.6 ms 计入收益，也不据此改 §0 与 §8 的算术。

### 3.3 32 ms 之后的算术

`32 − 13.4（memory）− 4（非流式 cost）≈ 15 ms`（乐观上限）；按静态审计口径（只算确定能摘掉的 SQL 与 cost）约 **21 ms**，对应单进程 47–67 RPS。该区间只对非流式成立：流式要代入 §6 的 −20.1 ms，且 memory 的收益在单进程下并不成立（§5.2）。

---

## §4 传输层事实（task-50）

| 结论 | 数字 | 处置 |
|---|---|---|
| httptools vs h11（服务端每请求 CPU） | 比值 **0.624**（省 37.6%），rps 1.139× | 已是默认（`uvicorn[standard]` 带 httptools），**无需动作**；仅精简镜像漏装时才补 |
| HTTP/2 上游 | 冷连接首包比值 minimax 0.796 / nvidia 1.281 / openrouter 0.922，**符号不一致** | 不建议默认开 |
| keep-alive 冷连接成本 | DNS 1.3 + TCP 64.6 + TLS 154.7 + TTFB 76.5 ≈ **297 ms**；稳态复用 75.6 ms | 保持长 keep-alive；本轮把 `--timeout-keep-alive` 从默认 5 s 提到 **75 s** |
| `--reload` 成本 | 常驻 ~0.9 核（单趟 rglob 全树 13.9–14.7 s） | 生产 bat 早已不带；开发用 watchfiles |

httptools 是「已吃到」的红利而不是待装项；默认 5 s keep-alive 会在 C=512/1024 掐掉空闲复用连接（观察到 2 次/1 次 read_error），所以 75 s 是消除偶发错误的配置事实，不是性能调优。h2 的三个比值方向不一致，说明上游实现差异大于协议收益，不开。

数据：`results/tp/*.json`；报告 `analysis/runtime_transport.md`。

---

## §5 零 SQL 管线 A/B（task-53，已落地）

### 5.1 机制

开关 `USAGE_PIPELINE=classic|memory`（默认 classic）。memory 模式下请求路径不再访问 SQLite：

- **写**：`l05_service/usage_sink.py` 只向 WAL 追加一行，后台线程批量 upsert `request_logs`（at-least-once + 按 `request_id` 幂等）；坏 WAL / 死库不影响请求。
- **读**：`l05_service/config_catalog.py` 的不可变 `CatalogSnapshot` 承担鉴权、路由、正文策略、目录；管理写先落 SQLite 再 `bump_catalog_version()`，进程按 `catalog_poll_interval_ms`（默认 30 s）轮询换代，轮询失败回落到 `catalog_ttl_ms`（60 s）全量 reload。
- **代价**：跨进程可见性最多 30 s（多实例部署必须调小，§7.3）。

### 5.2 实测（3 轮交错、每轮 45 s、warmup 15 s、C=32、流式、tier=50 ms）

| 指标 | classic | memory | 变化 |
|---|---|---|---|
| RPS | 18.03 | 20.65 | **+14.5%** |
| 网关 CPU/请求 | 61.8 ms | 48.4 ms | **−13.4 ms（−21.7%）** |
| p50 | 1564 ms | 1422 ms | −9.1% |
| p95 | 2853 ms | 2292 ms | −19.7% |
| p99 | 3812 ms | 2797 ms | −26.6% |
| TTFA p50 | 1244 ms | 744 ms | −40.2% |
| 网关 RSS 峰值 | 317 MB | 310 MB | −2.2% |

- 省下的 13.4 ms 里**只有约 6 ms 是「不做那 5 条 SELECT / 1 条 INSERT」的直接成本**，其余是去掉了 SQLite 单写者串行化：classic 每请求一次 `request_logs` 同步 commit（`l04_repo/repo.py:594`），C=32 时写锁排队被放大进每请求 CPU。这解释了为什么并发越高收益越大。
- 延迟降幅（p99 −27%、TTFA −40%）主要是**队列变短**，不是单请求变快：C=32 下排队远大于服务时间，CPU/请求少 22% ⇒ 队列长度与等待近似同比降。
- **单进程下 memory 没有收益**：A1_Z 臂 memory 40.2 RPS / p99 15188 ms，classic 45.2 RPS / p99 10158 ms（CPU 省 8%，吞吐低 11%，尾延迟更差；原因未定位，WAL append + 后台批写时序待查）。这条优化的适用面是**多进程/高并发**。

### 5.3 四道闸门与一个真实缺陷

| 闸门 | 结果 | 关键数字 |
|---|---|---|
| a 行数对账 / 去重 | **FAIL（5/6 轮）**，±2 容差下 PASS | 5 轮 exact；round 3 classic 833/834，缺 1，dup=0；memory 六轮 exact |
| a 的 memory 子判据 | PASS | 2799 请求 / 2799 行，0 丢 0 重 |
| b model_type 轮询 ≤10 s | PASS | PATCH 后 **0.09 s** 返回 400（类型冲突文案） |
| c kill -9 WAL 回放 | PASS | kill 前 DB 0 行 / WAL pending 41；重启回放 41 行，dup=0 |
| d memory 正文日志 | PASS | body 行 10 / 父日志 10 |

- **闸门 a 抓到的是 classic 的真实缺陷，不是噪声**：缺失 `req_01m7ccnvdhpa8htjce4qqv6d99` 在客户端成功集合里（读完了 `[DONE]`），但立即查与 3 s 后再查都是 0 行；它的 ULID 时间在窗口内 12.6 s 处、该轮最大延迟 5.17 s，排除「检查太早」。根因：`chat.py:1145` 在流式生成器尾部 `await _write_log(...)` → `repo.py:594-598` 同步 commit，客户端读完 `[DONE]` 立即断连时 ASGI 取消生成器；且 `chat.py:555-572` 对 commit 异常静默 rollback 不重试。量级 1/2364 = **0.042%**。
- **memory 的 WAL 追加在请求内完成，结构上不存在这个缺口**——这是除性能之外开 memory 的第二个理由：生产用 classic 时，审计/计费日志会漏极少量流式请求。

数据：`results/pipeline/memory_ab.json`（235 KB，含每轮 raw）、`results/pipeline/gate_a_missing_row.json`。

---

## §6 LiteLLM cost 计算移除（task-54，已落地）

### 6.1 问题与调用链

litellm 每次请求都算一遍 `response_cost`：profile 里 cost 家族 cum **6.352 s / 703 个 chat 请求**（9.0 ms/请求，含一次性 cost map 加载 1.867 s；稳态 4.485 s/703 = 6.4 ms），内部是 cost-map 的 **125 万次 `re.search`** 加上「模型名不在 cost map 里」时的 zero-cost 诊断（1.4 s）。

- **非流式**：`litellm_logging.py:2536` → `:2567` → `:1894` → `cost_calculator.py:2170` → `:1473 completion_cost` → `:399 cost_per_token` → cost map 查找 + `fallback_generalizations` 的 `re.search`。
- **流式**：`streaming_handler.py:2190 _finalize_completed_stream` → `:2194 asyncify(stream_chunk_builder)` → `streaming_chunk_builder_utils.py:1503-1504` → `:1329 _stamp_streaming_usage_cost`。
- **别名/导出**（只 patch 模块函数不够）：`Logging._response_cost_calculator`（`:2059`）、`_response_cost_calculator_async`（`:2202`）、`_lazy_imports_registry.py:11-15` 的懒加载映射。
- **我们的计费完全不依赖它**：`l05_service/billing.py:26 compute_cost()` 读 DB 单价自算；`gateway.py:860 _response_headers()` 只读 `_hidden_params[headers]`；`src` 全仓 `response_cost` 引用只有这一处，`usage.cost` 不落盘、不返回客户端。

### 6.2 关闭方式与补丁

官方开关**不存在**（全仓检索 disable/skip cost、COST_TRACK、track_cost 只命中一句 docstring；`disable_end_user_cost_tracking` 只管 end-user 上报，`include_cost_in_streaming_usage` 只管 proxy 是否把 cost 塞进流式 usage）；自定义 cost map 只省 zero-cost 诊断（1.4 s/703），命中 map 后仍走 `completion_cost` + `cost_per_token`。唯一可行路径是 **stub**：

`l01_core/config.py:30 litellm_cost_calc: str = "off"`（env `LITELLM_COST_CALC`）+ `l05_service/llm/gateway.py:674-712 _disable_litellm_cost_calc()` 把 `Logging.response_cost_calculator`、`Logging._response_cost_calculator`、`cost_calculator.response_cost_calculator`、`litellm.response_cost_calculator` 四个入口统一替换为返回 0.0（幂等），在 `startup()` 的 litellm 分支、`_warm_litellm()` 之前调用，try/except 兜底不影响启动。真实请求验证：非流式 cost 家族调用 **15 → 0**，流式 **62 → 6**（剩余 6 次只是 `stream_chunk_builder` 组装 usage）。

### 6.3 实测（tier=50 ms mock、30 s/格、每臂独立进程、网关进程树 CPU/成功请求）

| 场景 | cost 开 | 关 | 差值 | RPS（关/开） | p50（关/开） |
|---|---|---|---|---|---|
| 非流式 C=16 | 38.1 ms | 34.0 ms | **−4.0 ms（−11%）** | 35.12 / 30.99（**+13.3%**） | 424 / 512 ms |
| 流式 C=16 | 87.7 ms | 67.6 ms | **−20.1 ms（−23%）** | 16.66 / 12.50（**+33.3%**） | 906 / 1187 ms |
| 流式 C=1 | 73.0 ms | 65.0 ms | −8.0 ms | — | — |
| 空壳直调 `acompletion`（无网关） | 14.1 / 40.6 ms | 8.6 / 18.8 ms | −5.5 / **−21.9 ms** | — | — |

- 流式代价远大于非流式：每个流结束要 `stream_chunk_builder` 组装 usage 再算 cost，且与帧数弱相关、与「流尾一次性收尾」强相关。profile 的 9.0 ms/请求是混合口径且被放大，**不能外推**，以本表为准。C=1 非流式 −3.3 ms 是噪声（n≈350/臂），按 0 处理。
- stub 后流式仍走一次 `stream_chunk_builder`（2.8 s/703），属 litellm 流式实现，不在本补丁范围。

### 6.4 默认 off 的三条判据与风险边界

① **无 callback**：`src` 全仓 `litellm.callbacks` / `success_callback` / `failure_callback` / `max_budget` 均 0 命中；② **无消费者**：`response_cost` 仅 `gateway.py:860` 读 headers；③ **计费另算**：走 `billing.compute_cost`。**风险边界**：将来若启用 litellm 预算或花费回调，必须设 `LITELLM_COST_CALC=on`，否则预算恒为 0；回滚 = 改 env 重启，无需改代码。

文档 `analysis/litellm_cost_calc.md`；数据 `results/litellm_cost_bench.json`、`results/litellm_cost_check.json`。

---

## §7 多实例形态（task-55，实测 15 臂）

### 7.1 先说形态：本机不能用 `--workers`

`uvicorn --workers N（N≥2）` 在这台 Windows 机器上起不来：`uvicorn/supervisors/multiprocess.py:35` → `multiprocessing/context.py:63 Pipe()` → `multiprocessing/connection.py:601 _winapi.CreateFile` → `PermissionError: [WinError 5] 拒绝访问`。用 20 行 FastAPI hello app 单独复现同样错误 ⇒ 与本仓库代码无关，是 Windows + Python 3.13 + 该 uvicorn 版本的命名管道创建被拒。

结论：多进程只能是**多个独立 uvicorn 进程 + 前置负载均衡**（生产本来也应如此）。附带一条：`--workers` 会让所有 worker 共享同一份进程内状态（catalog 快照、限流计数、usage WAL），这条路本来就不该用于多进程部署。

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
| M_Z / M_L（mock 直连上限） | 2382.6 / 2074.7 | 52/74 · 61/77 ms | — | — | — |

- **扩展性次线性**：1→2→4→8 进程 45.9→64.4→82.1→94.5（共享库）/ 45.9→70.0→105.3（各自库），8 进程只有 **2.30×**。原因不是 SQLite：每请求 CPU 从 27.3 ms 涨到 51–55 ms，8 进程合计只吃到 5.2–5.4 核（8 逻辑 / 4 物理，还要和技术栈共用），**是核不够 + 超线程下真实并行度约 5 核**。p99 从 9675 ms 单调降到 1645 ms，说明并行确实生效。
- **共享 DB 的隐性代价不是报错而是静默丢日志**：SQLITE_BUSY 全程 0 次，但 classic 共享库臂 rows/req 从 0.998 → 0.979 → **0.951**（8 进程丢 **4.9%** 的 `request_logs`），各自库恒为 1.000。机制与 §5.3 的 at-most-once 缺口同源，量级高出两个数量级；**边界**：这是「观测 + 机制推断」，本轮没抓到 per-request 的 SQL 错误（日志零命中），要坐实需在 `chat.py:555-572` 加按原因的计数埋点再复跑。**运维含义**：多实例共享 SQLite 时必须按 rows/req < 1 告警，不能等报错。
- **最优组合是 D8 + memory**：179.0 RPS（vs 单进程 45.9 = 3.9×）、29.4 msCPU/ok（vs classic 8 进程 51.3，−43%）、p99 1047 ms；memory 还把共享库的惩罚从 −10% 压到 −5%（复跑 170.1 RPS）。而 memory 的价值**只在多进程体现**（§5.2）。

### 7.3 共享库 + memory 的 404：配置可见性，不是绑定错误

B8_L_memory 有 3569/6200 = **57.6%** 请求返回 404（B8_Z_memory 3375/5962 = 56.6%），其余臂 404 为 0；404 来自 `routing.py:82 _not_found()` → `MODEL_NOT_FOUND`，是网关自己的路由表里没有该模型。

- **已确证**：memory 模式下请求路径只认进程内 catalog 快照（`deps.py:23-31` 只在 zero_sql 时挂 snapshot；classic 走 SQL 每请求读），快照只在 `config_catalog.py:240-253` 轮询（30 s）/ TTL（60 s）时刷新，**admin 写入不触发本进程 reload** ⇒ 新绑定在已运行进程里最长 30–60 s 不可见。
- **对照实验**：该臂 warmup 从 15 s 拉到 45 s 复跑 → ok 率 1.000、0 个 404、**170.1 RPS**、28.7 msCPU/ok（`results/multiproc/_b8mem_recheck.json`）。把窗口推过一个轮询周期问题就消失。
- **未定论**：为什么只打在共享库的两个 memory 臂上、失败率 ~57% 而不是 0 或 100%。最合理的机制是共享库上版本轮询的读被写锁挤掉 → 只能等 60 s TTL 兜底；**没有拿到失败时刻的网关日志**（复跑覆盖了同名日志文件），只作机制假设记录。
- **生产含义（不依赖上面未定论部分也成立）**：放量前把 `CATALOG_POLL_INTERVAL_MS` 设为 **2000**（收敛时间 ≤30 s → ≤2 s，每 worker 每 2 s 一次版本 SELECT，可忽略）；或把 bootstrap 放到所有进程起来之前；不要指望写入方进程自己「马上生效」。

### 7.4 推荐配置（单机多进程形态）

**4–8 个独立 uvicorn 进程 + 前置负载均衡（按 api key 亲和，避免键的限流计数被摊到不同进程）**；**每副本各自本地库**（共享库会丢日志行）；`USAGE_PIPELINE=memory`（每请求 CPU −43%、8 进程吞吐 +70%、p99 −36%）；`LITELLM_COST_CALC=off`（否则每请求多 4–20 ms，按 1000 RPS 算就是 4–20 个核）；`CATALOG_POLL_INTERVAL_MS=2000`（消掉 ≤30 s 的 404 窗口）；`--timeout-keep-alive 75`（默认 5 s 掐断空闲复用连接）。进程数上界未验证：8 进程时每进程只剩 0.65 核，12/16 进程本轮未测。

---

## §8 到 1000 RPS 的账

| 方案 | 算术 | 结论 |
|---|---|---|
| 现状单进程 | 45.9 RPS（C=128，L 场景），27.3 msCPU/请求 | 单进程封顶 ~1.2 核 |
| 现状 8 实例（classic，各自库） | 105.3 RPS，51.3 msCPU/请求 | 只有 2.3×，核与超线程是硬约束 |
| **优化后 8 实例（memory，各自库）** | **179.0 RPS**，29.4 msCPU/请求，p99 1047 ms | 本机实测最优形态；8 核满打满算 ≈270–272 RPS/台 |
| 优化后单进程（非流式，memory + cost） | 32 − 13.4 − 4 ≈ 15 ms（乐观）/ 21 ms（审计）→ 47–67 RPS | 单进程仍有天花板 |
| 优化后（流式，memory + cost） | 87.7 − 20（cost）− memory → 8 实例预计 ≈1.5–2× 单实例流式吞吐 | 流式是最贵形态 |
| httpx-native 传输 | 非流式 −8~−12 ms/请求；流式饱和吞吐 2.84×（13,265 vs 4,668 帧/s） | 单进程可再上一截，工程量最大 |
| 水平扩展 | 179–270 RPS/台 × 4–6 台 | **1000 RPS 的现实路径** |

台数有两种口径，都假设网关独占机器、不与生产混跑、每请求 CPU 不随进程数继续劣化：**实测口径** D8 + memory 179.0 RPS/台 → **6 台**（1000 RPS × 29.4 ms ≈ **29 个网关核**）；**核数上限口径** 8 逻辑核全给网关、每请求保持 29.4 ms → 270–272 RPS/台 → **4 台**；**classic 对照** 105.3 RPS/台 → **10 台**（需 ~51 个网关核）。

**一句话**：这台机器上「加并发」没有意义（C 从 16 加到 1024，吞吐从 38 掉到 23）；要吞吐只有三条路，按性价比排序是 ①开 `USAGE_PIPELINE=memory`（零代码，−21.7% CPU/请求）→ ②移除 litellm cost（一行默认值 + 小补丁，−4 ms 非流式 / −20 ms 流式）→ ③横向多机（成本线性）。httpx-native 重写能再拿一截，把台数压到 2–3 台，属于下一阶段工程。

> 注：两份原始报告对「8 核满打满算」分别写 **270**（`PERF_CEILING_REPORT.md`）与 **272**（`multiprocess_scaling.md`）；同源算术（8 ÷ 0.0294 s），差 2 RPS 属四舍五入，不影响 4 台/6 台的结论。

---

## §9 落地清单与待决（2026-11-18 已全部闭环，保留作证据索引）

本轮清单的六项动作全部完成或被取代：①memory 管线 + 目录轮询收紧（已进生产）；②`LITELLM_COST_CALC=off`（默认）；③`--timeout-keep-alive 75`（已进启动脚本）；④httptools（`uvicorn[standard]` 默认）；⑤httpx-native 传输（ADR-065/078，已完成）；⑥requirements 对齐（pin 与实测 venv 一致，orjson 已补，见 §12.2-R3）。当时的依赖漂移清单（fastapi 0.115.6→0.162.2 等 11 项）已随 pin 对齐消失；`requirements.lock` 的 pip-compile 全量重编译仍未做（当前只含手工 orjson 条目），换 Python 版本时须重跑。原始数字与当时的提交记录在 git 历史与本节被取代前的版本里；后续状态与最新数字见 §12。

---

## §10 Linux 开环三臂基准与 agentgateway 对照（2026-11-16 增补）

> **环境**：WSL2 Ubuntu 26.04（8 vCPU / 7.8 GB，Python 3.14.4，清华源装的 requirements+psutil+py-spy），与 §2–§8 的 Windows 同机争用环境**不同机**，绝对值不可与 §2–§8 直接比；本节内组间比值可比。原始数据与脚本：`docs/scratch/bench-agentgateway/`（`REPORT-openloop.md`、`REPORT-linux-profile.md`、`openloop.py`、`openloop_matrix.py`、`mock_asgi.py`，均 gitignore）。
>
> **口径**：**开环**（外部评审第①条）——请求按固定到达率到点就发，延迟从**应发时刻**起算（排队计入延迟，纠正 coordinated omission）；每点 20 s；CPU/请求 = 被测进程 CPU 时间增量 ÷ 完成请求数（不受排队影响，是最硬的指标）。上游 mock 是 uvicorn 单进程 ASGI（线程化 mock 在负载下崩溃，见 §10.5）。

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

**B0 修复后复测（同日，task-159 落地，gateway.py `iter_sse` 直通 `aiter_bytes`，2 files +261/−55；全量回归 1091 passed / 8 skipped 与改前同数）**：2 RPS → 2.07 RPS、CPU/请求 **30.0 ms**、p50 29.9 ms、0 错误；10 RPS → **10.05 RPS（原 2.44）、CPU/请求 31.9 ms、p50 32.7 ms、0 错误**；复跑稳定（10.04 / 39.9 ms / 37.4 ms）。吞吐 4.1×、CPU/请求 −72%，与 agentgateway 同场景 20–34 ms 只差 ~1.2×。改写语义全保留：error 帧不发客户端、`[DONE]`/usage/finish_reason/空流兜底/首字节后不可重试不变；`sse_patch` 注入或模型改名时自动退回逐行改写路径。上表塌陷数字为修复前基线，保留作对照。

### 10.3 空载资源与塌陷阈值

| 臂 | 空载 CPU | 空载 RSS | 塌陷阈值（非流式） | 说明 |
|---|---:|---:|---|---|
| direct | 0.07 % | 55.7 MB | 未测出（≥80 RPS） | 0.8–1.5 ms/请求是任何方案都要付的「上游底噪」 |
| 本网关(memory) | 0.20 % | **116.8 MB** | **34–65 RPS 之间**（80 崩） | 单进程单核模型：CPU/请求 × RPS > 1 核即塌 |
| agentgateway | 0.07 % | **31.1 MB** | 未测出（≥80 RPS） | tokio 多 worker（17 线程常驻） |

### 10.4 函数级归因（py-spy，闭集合复核）

同环境严格闭集合中位数（taskset 分核、每轮新 DB+WAL、≥5 轮取中位）：classic S1 36.01 ms/请求、memory 14.58、memory+split 21.02；空载对照（只打 /health）1.45–1.69 ms → **ASGI 栈固定开销 ≈1.5 ms**。

火焰图（memory 4689 样本）：SQL/ORM 20.5%（全部是后台 sink 的批量 INSERT 语句构造，请求内零 SQL）、httpx 转发 17.5%、事件循环 13.3%、FastAPI 路由 7.2%、JSON 4.1%、WAL 0.5%、鉴权+usage 抽取 0.7%、其它 36%；**classic 是 53.4% SQL**（每请求 INSERT+commit）。结论：memory 管线的数据库红利已吃干，剩下的 ~80% 是框架开销（转发/循环/序列化/路由/日志脱敏），其中日志脱敏 `redact_text`+`is_sensitive_key` 合计约 2%，是下一个可摘的果子。

流式慢的机制定位：`l05_service/llm/gateway.py` `iter_sse`（:1432-1476）用 `response.aiter_lines()` 逐行取上游 SSE，再对**每一行**做 `rewrite_line`（json.loads → 改写 → json.dumps → 拼 `\n`），每 chunk 一次完整 JSON 往返，单核约 3.5k chunk/s 封顶；agentgateway 同场景每 chunk 约 0.05 ms，我们约 0.27 ms（5×）。已于 task-159 修复（同日落地，见 §10.2 补记）：`iter_sse` 直通 `aiter_bytes` 按网络块转发、每帧仅 1 次 json.loads 跟踪，实测达标（≤40 ms、10 RPS 不塌陷）。

### 10.5 agentgateway 实测遥测事实（接入计费的关键约束）

用本地 mock 实测其 v1.7.0 行为（`probe_agw_metrics.sh`）：

1. **非流式**请求的访问日志带 `gen_ai.usage.input_tokens=16 gen_ai.usage.output_tokens=8`（与上游返回的 usage 一致），还有 `gen_ai.request.model`、`gen_ai.response.model`、`gen_ai.provider.name`、duration、http.status。
2. **流式**请求的日志**没有 usage 字段**——它不解析 SSE 流，token 用量在流式场景下拿不到。
3. `/metrics`（statsAddr :15020）只有通用指标：`agentgateway_requests`、`agentgateway_request_duration_seconds`、`agentgateway_response_bytes`、`agentgateway_requests_shed`、`agentgateway_upstream_call_duration_seconds`、tokio/进程内存等，**没有任何 token/cost/gen_ai 指标**。（修正：docs-1.7 源码已有 `gen_ai_client_token_usage`/`gen_ai_client_cost` 与流式 usage 抽取——实测二进制与源码存在版本差，见 §11.4。）

含义：若走「客户端直连 agentgateway」，流式计费没有现成数据源——要么我们的控制面仍留在链路上抽 usage（回到老问题），要么上游按 `stream_options.include_usage` 返回、由旁路消费日志/OTel（非流式可行，流式不可行），要么按非流式日志 + 流式 tokenizer 估算（有计费误差）。

### 10.6 迁移评估（数据驱动的初判）

1. **双跳算术否决「Python 前置 agentgateway」**：本网关自身 9–11 ms + agentgateway 1.3 ms ≈ 10.5 ms+，比现在更慢——前端转发会把 agentgateway 的收益全部吃掉（用户担忧成立，量化如上）。
2. **收益边界**：非流式换数据面的上限是 ~9 ms 里的大部分（其中 ~1.5 ms 是任何 ASGI 栈的固定开销）；流式的 5×/chunk 才是大头，而它**不换语言也能修**（task-159 已修复，见 §10.2 补记）。
3. **能力缺口**：组合优先级/观察期排序、冷却排期状态机、多候选跨渠道回退、分布式限流后端无对应物；改写可用 CEL 表达、每 key 预算是内置能力——逐项对照见 §11.2。
4. **路线**：B0 修流式（✅ 已完成：10 RPS 满速 10.05、CPU/请求 31.9 ms，见 §10.2 补记）→ B1 非流式连接复用与 JSON 编码（≤6 ms，**下一步**）→ B2 若仍需 <2 ms，把「纯转发+SSE 透传」独立成原生数据面（agentgateway 或自写），控制面留 Python。是否采纳 agentgateway 在 B2 时再拍板。

### 10.7 测量陷阱（复现必读）

1. 线程化 mock（`mock_upstream.py`）高并发下崩溃 → 已换 `mock_asgi.py`（uvicorn）。**流式对比结论以 uvicorn mock 为准；线程 mock 只能跑到 ~20 RPS。**
2. memory 管线鉴权读**进程内目录快照**，seed 在启动后建 key → 全量 401；且 `bench._spawn_python_gateway` 每次启动先删 SQLite → seed 后重启必须用「保留 DB」的 spawn（`openloop_matrix.py:_spawn_gateway_keep_db`）。
3. WSL2 会在每条 `wsl -e` 命令结束时整体重启（dmesg `init process 2 exited`），后台压测进程会无声消失——**压测必须一条命令跑完**。
4. `--out` 相对路径落在 cwd（`~/api/`），不是脚本目录。
5. Windows 宿主 8 核长期满载而 WSL loadavg 仅 1.58：抢占在宿主侧，taskset 消不掉；绝对值按 ±30% 读，倍数关系可信。

---

## §11 agentgateway 接入方案与逐功能对照（2026-11-16 增补）

> **证据**：`docs/scratch/bench-agentgateway/RESEARCH-agentgateway-integration.md`（一手证据：本地二进制 `--help/--version`、本地 `agentgateway.yaml`、官方源码浅克隆 `18f83707`（docs-1.7，2026-11-13）file:line、官网文档）。下文 file:line 相对该源码仓库。注意 §10.5 实测用的二进制是 `26f5aebe`（v1.7.0），与 docs-1.7 源码存在**版本差**（见 §11.4）。

### 11.1 接入形态：没有 SDK，只有三种进程外形态

回答「SDK 还是 CLI 还是啥」：**两者都不是**——它是一个独立 Rust 静态二进制（musl），没有官方 SDK（workspace 全部 crate `publish=false`，无 Go/Python SDK），也不能当 Envoy 插件（ext_proc/ext_authz/RLS 三个 Envoy 协议它都只做 **gRPC 客户端**，挂不进 Envoy）。可用的形态：

| 形态 | 配置方式 | 动态性 | 适用 |
|---|---|---|---|
| Standalone 二进制 + YAML（`-f config.yaml`） | config 文件被 watch（250ms debounce），改动热重载；校验失败保留旧配置（state_manager.rs:157、util.rs:80、lib.rs:791-826） | 文件级热更 | 单机/基准/简单转发 |
| Kubernetes + 内置 controller | Gateway API + 自有 CRD（AgentgatewayPolicy/Backend/Model/Parameters），controller 经 xDS ADS 推送（gateway 是 xDS client） | xDS 推送 | 云原生多租户 |
| 独立 xDS 客户端模式 | `XDS_ADDRESS`（config.rs:137，默认 `http://127.0.0.1:15010`），**任何 ADS 控制面都能推配置** | xDS 推送 | 自建控制面接管 |

Admin 端口（adminAddr，示例 :15000）可编程 REST：`GET /config_dump`、`POST /logging`、pprof/tasks/trace、`POST /api/config`（**整文件写**，ui.rs:100,542）、细粒度 `CRUD /api/config/resources/{kind}/{id}`（ui.rs:106-115）、`GET /api/costs/models` + `POST /api/costs/refresh-base`（价目刷新）、`GET /api/budgets/status`。结论：REST 适合低频编排，高频逐请求决策走 §11.3 的两个钩子。

### 11.2 逐功能对照（我们要的 27 项 ↔ 它有的）

| 我们的能力 | agw 状态 | 说明 / 替代路径 |
|---|---|---|
| POST /v1/chat/completions、GET /v1/models | ✅ 原生 | lib.rs:153-155,158-182 |
| 协议转换（Anthropic/Gemini/Responses ↔ OpenAI） | ✅ **它有，我们没有** | conversion/{messages,vertex_gemini,responses}.rs——接 agw 反而白得一个待建能力 |
| 多 provider 凭证注入 / hostOverride / custom provider | ✅ | 与渠道 base_url+api_key 等价（openai/anthropic/azure/bedrock/gemini/vertex/copilot/custom.rs） |
| 模型路由 + 负载均衡 | ✅ | ModelRoute header_matches/backend/policies（model_router.rs:23-38）；rendezvous-hash+权重（loadbalancer.rs:24-40） |
| 组合优先级 / ADR-077 同层策略排序 / 观察期 | ❌ 无 | 必须 Python 决策钩子（§11.3 callout）实现 |
| 渠道级请求/响应/头改写 | ⚠️ 可表达 | CEL transformation（transformation_cel.rs，admin `/api/cel` 试算）；渠道语义需映射成 per-backend CEL 规则 |
| 首字节前重试 | ✅ 语义相同 | 请求体缓冲重放、收齐上游响应头后判定（httpproxy.rs:1189-1316）；**但仅对同后端串行重放——无多候选竞速、无跨渠道回退**（回退链留 Python 或放弃） |
| 请求超时 | ✅ | request_timeout（httpproxy.rs:1184-1187） |
| API key 鉴权 | ✅ 原生 或 外包 | apikey.rs / jwt / oidc / basicauth；或 ext_authz 外包给 Python 保留现有口径 |
| 每 key 预算（金额/token） | ✅ | http/budget（按 api_key），`GET /api/budgets/status`——我们组合计费的近似替代 |
| 限流 | ⚠️ | 本地有；分布式需自建 Envoy-RLS 服务（它是 gRPC 客户端，remoteratelimit.rs:7） |
| token/cost 遥测 | ✅（口径不同） | 价目表×token → `gen_ai_client_cost`；与我们的成本口径（每百万 token 有效均价）不同，账本仍留 Python |
| 冷却排期 / 时间表 / 随时间变权重 | ❌ 无 | 只有被动 outlier ejection（health.rs:1-6、outlierdetection.rs），无主动探测、无时间表。§10 清点已证明冷却状态机不能纯配置下发（半开原子租约 + 下发延迟同数量级）→ **callout 服务持有状态机** |
| 在飞上限准入（per-worker，DB 热改） | ⚠️ | 无节点级 cap；有 requests_shed 指标 + 分布式限流（需自建 RLS） |
| 流式 SSE 逐 chunk usage | ✅（形态正是我们要的） | 字节原样透传 + 旁路复制流逐 chunk 解码抽 usage/TTFT/inter-chunk（passthrough.rs:21-37 及其测试）——task-159 想做的事它已经做对 |
| session affinity / TLS / WS(Realtime) / MCP / A2A / prompt-cache 感知 | ✅ 白得 | sessionaffinity.rs、backendtls.rs、mcp/、a2a/、policy/mod.rs:206 |
| 语义缓存 / 主动健康探测 / 发票级账单 / 每键细粒度配置 API | ❌ 无 | 调研 §③ 清单共 10 项缺口 |

### 11.3 推荐拓扑（若采纳）：旁路双钩子 + 低频编排

数据面 = agentgateway；Python 控制面三件：

1. **VirtualModelCallout**（llm/router_callout.rs:24-48）：每请求 HTTP 调 Python 服务选模型——CEL 取 header/body，响应回 model 名，failClosed/failOpen + fallback（model_router.rs:696,1656-1660）。**组合排序/冷却状态机/观察期都在这个 Python 服务里**，进程内状态天然满足 §10 的「冷却必须留数据面侧有状态服务」要求。
2. **ext_authz gRPC**（envoy.service.auth.v3 客户端，我们是 server）：每请求鉴权/配额/计费闸门（可注 header）。若接受 agw 原生 apikey+jwt，这层可省，把鉴权留在 callout 里合并成一次 RPC。
3. **配置/价目编排**（低频）：admin REST（`/api/config/resources`、`/api/costs/refresh-base`）或 xDS——渠道清单、改写 DSL、权重。

计费账本留 Python：消费 agw 请求日志（log_store 落 sqlite/postgres，带 analytics 列）或 OTel，与 `gen_ai_client_cost` 对账。**代价**：每请求 +1~2 次内网 RPC（callout ~0.5–2 ms 量级）→ 合计 ~2–4 ms，仍显著低于现状非流式 9–11 ms；与 §10.6「双跳否决」不矛盾——Python 从**数据路径**（转发字节流）退到**旁路**（只做决策，不碰 payload），「前端转发吃掉 agw 性能」的担忧在这个拓扑下不成立。

### 11.4 「用户直连」拓扑与流式计费可行性（含版本差修正）

client 直连 agw（:9103），Python 仅旁路：非流式计费数据现成（日志 usage/cost 字段 + 预算闸门在 agw 内）。流式：**docs-1.7 源码**里 passthrough 路径逐 chunk 抽 usage、metrics 有 `gen_ai_client_token_usage`/`gen_ai_client_cost`（metrics.rs:424-465、log.rs:948-1007，日志字段 input/output/cached_input_tokens + cost 在 log.rs:948-1007）——但 §10.5 实测的 `26f5aebe` 二进制**流式日志无 usage、:15020 无任何 token 指标**。两相对照属**版本差**：一周内（26f5aebe → 18f83707）遥测行为已变。结论：**直连方案的流式计费不能凭旧二进制下「不可行」的结论，须用最新构建复测**；若最新版流式日志仍无 usage，则退化为「agw 内置预算闸门（按 api_key 的 token/金额上限）+ Python 旁路按日志/OTel 对账（有误差）」，或回到我们网关内抽取。

### 11.5 决策建议

维持 §10.6 路线不变：**B0 修流式（✅ 已完成：task-159，10 RPS 达 10.05、CPU/请求 31.9 ms，见 §10.2 补记）**→ B1 非流式连接复用与序列化（目标 ≤6 ms，**下一步**）→ **B2 只有当仍需 <2 ms 数据面时才引入 agw**（形态=§11.3 的旁路双钩子）。理由：当前最痛点是流式 5×/chunk，不换语言就能修；引入 agw 换来的是非流式 ~8 ms 与一批白得能力（协议转换/MCP/A2A/预算/会话亲和），付出的是两套控制面语义映射（组合→ModelRoute、渠道→provider+CEL）+ 上游快速演进风险（一周内遥测行为已变）+ 冷却/观察期语义只能在 callout 里重建。B1 落地后用同一开环矩阵复测三臂，用数据决定 B2。

---

## §12 五臂基准与 P0/P1 优化（2026-11-18）

> **环境与工具**：tst WSL2 Ubuntu 26.04，16 逻辑核，Python 3.14.4，uvicorn 0.54.0（httptools+uvloop 单 worker）、SQLite WAL、`USAGE_PIPELINE=memory`、httpx 传输；agentgateway v1.7.0（构建 26f5aebe，与 §10/§11 同一二进制）。开环测法，核隔离 mock 0-2 / 网关 3-9 / 压测 10-15；CPU/请求 = `/proc` jiffies 增量 ÷ 窗口成功数。负载链天花板自证：直连臂两工况均 1024 QPS 线速（p50≈1ms）。工具与原始数据：`~/workspace/labs/gwbench/`（labs 自建，未动仓库）；完整矩阵与 py-spy 归因见 `docs/scratch/bench-5arm-2026-11-18.md`。

### 12.1 五臂判定（优化前基线，回答「能否跑赢 agentgateway」）

| 工况（50K tok） | slipstream 直连名 | slipstream 裸名直通 | agentgateway |
|---|---|---|---|
| 非流式 CPU/req | 4.97ms | — | ~1.0ms（两次交错 0.9–1.2） |
| 非流式容量 | 膝点 ~230 RPS（429 shedding，非 CPU 崩） | — | 1024 线速 |
| 流式 CPU/req（2048 帧） | **232ms** | 14.25ms | **3.36ms** |
| 生产长流（300K tok/20480 帧） | **0 完成**（2.2 CPU-s/流） | — | 线速 21.5ms |
| 非流式 1.2MB | 17.9ms，30–60 RPS 塌 | — | 2.4ms，128 线速 |

现状答案：**全面跑不赢**。根因三件：①直连名流式逐帧改写（ADR-078 前的生产默认，~106µs/帧）；②memory 管线 WAL 无条件双份 body 全量序列化（无视截断与策略）；③「零 SQL」管线的请求路径残余 SQL（每请求 get_db 会话 + route_stats 过期突刺 + 熔断 failure 无条件写）。

### 12.2 P0/P1 落地与实测（同矩阵复测，全部保持计费/日志/错误映射/重试语义）

| 改动 | 内容 | 实测 |
|---|---|---|
| **P0-R1**（ADR-078） | 流式改写字节化：块级批发 + needle 替换 + 解析兜底；`aiter_bytes` 去 chunk_size（修 httpx ByteChunker 吞 error 前缀 bug） | 直连名流式 232 → **13.8ms**（17×）；生产长流恢复全线完成 |
| **P0-R2**（ADR-079） | body 行先截断渲染再进 WAL（`build_body_record` + `_bound_deep`），capture 决策仍归策略 | 1.2MB 档 json 占比 32.7%→待复测；200KB 非流式 −15% |
| **P0-R3** | orjson 进 requirements.txt/lock（代码早已就绪） | 非流式 −20%（单独 A/B） |
| **P1-R5** | 跟踪 bytes 直进 `jsonfast.loads`（orjson 优先） | 流式 CPU 13.8 → 11.2ms |
| **P1-R6** | `attach_request_id` 纯 ASGI 化；`max_body_bytes` 生效（413）；在飞闸门前移到鉴权/body 解析之前（释放收归依赖栈） | 入口层开销 −；429/413 拒绝不再付解析成本 |
| **P1-R8** | WAL fsync 移出事件循环（后台 fsync 线程 + seq 快照协议；100ms 持久性窗口与 kill -9 回放契约不变） | append 最大延迟 2.275 → 0.567ms（ext4 微基准） |
| **P1-R4** | route_stats 过期改 stale-while-revalidate + 单飞后台刷新；record_failure 仅状态迁移才写（record_success 原有闸门保持） | 稳态请求路径 SQL → 0（冷缓存首查除外） |

**P1 复测（生产推荐 env 前提下见 §12.4）**：非流式 200KB CPU/req **4.56ms**（128 QPS 线速）；直连名流式 **11.22ms**、rw 带改写 **10.67ms**（64 QPS 线速）；重载长流 8 QPS p50 **77ms**、CPU 69.5ms。对比 agentgateway：非流式 4.6 vs 1.0ms（4.6×），流式 11.2 vs 3.4ms（3.3×）——**本轮收官后仍未跑赢，但差距从 68×/5× 收敛到 3–5×**；每帧地板（Python ~5µs vs Rust ~0.1µs）与每请求框架底盘（~1.5ms ASGI + ~1.0ms httpx/aiosqlite）是残余差距的主体，对应 ADR-076 阶段 B 的门槛判断，不在 Python 内解决。

### 12.3 P1.5：膝点与 RSS 归因闭环（2026-11-18，py-spy @拐点 + memray 两趟）

**膝点**：加密梯度（192–320 QPS）+ 拐点 py-spy + 抬闸对照实验（`GATEWAY_INFLIGHT_CAP=1024`）。结论：P1 落地后膝点已从 ~230 上移至 **~290–300 RPS**（288 QPS 零 429）；296 起 in-flight 顶格 256 触发 429 shedding（Retry-After:1，快速失败成立）。**抬闸对照证明闸门在保护吞吐**：cap=1024 去掉 shedding 后吞吐反降（152–199 ok/s、p50 3–10s——排队使每请求循环成本 3.4→6.35ms）。拐点处主循环 92.9% busy 里：httpx 组 40.7%（连接池 O(N) 存活扫描 ~10%、200KB 请求体被 httpx 二次 stdlib JSON 编码 8.3%）、stdlib json 15.1%、aiosqlite 8.2%（旁路线程）、GC 0%、WAL fsync 0%（R8 生效）。P1.5 修复（httpx 预编码 bytes、close 置空引用、`_body` 解析后释放、body 渲染 orjson、relay 帽 4M→256K）后复测：**304 QPS 线速（p50 3.8ms、CPU 2.65ms/req）、240 QPS 干净、膝点 ~310**。

**RSS（memray 两趟：峰值持有 vs settle 平台持有）**：重载流平台 703MB = ~250MB 真实活对象 + ~450MB 分配器滞留（逐帧 churn 撕裂 glibc arena，1.2MB 级大块 free 后不还 OS）。活对象里 **183MB 是泄漏类**：160/160 条流完成后 `UpstreamStream.response→Request→编码体`（1.2MB/流）仍被引用——close 不置空引用链存活到进程结束。峰值另有 body 三份拷贝（httpx 编码体 131.6MB + Starlette `_body` 105.3MB + 解析 dict 105.3MB）。P1.5 修复（close 置空 + `_body` 释放 + relay 帽收紧）后复测：平台 999 → **851MB**、HWM 1046 → **888MB**。残余平台 = 活对象（在飞 body 拷贝 ×3 为设计使然）+ arena 滞留，进一步压降需字节域全链路（原始 bytes 直传、按需解析）与逐帧 churn 减半，属结构项。

**tiktoken 估算**（P1.5-D，提交 `89924d7`）：≤128K 字符走真分词（典型输出精度不变），超出走脚本折算（C 级计数，与旧实现 3016 用例逐位一致）；1.2MB finalize 37→1.15ms、4MB 极端 105→23.7ms。`usage_estimated`/缓存口径/思考块计入红线不变。已知行为变化：超长文本 prompt 不再计数非文本 part（旧行为把 base64 高估成 ~250K tok，误差方向改善）；图片常数口径待产品裁定。

**ledger/service.py fsync 对称改造**（默认 off 管线）：与 usage 管线同款 `background_sync` 模式，32 项定向测试过。

### 12.4 allocator env 权衡（本轮实测，不做默认推荐）

| 配置 | 重载后平台 RSS | HWM | 延迟代价 |
|---|---|---|---|
| 默认（无 env） | 999MB（P1.5 后 851MB） | 1046MB（P1.5 后 888MB） | 0 |
| `MALLOC_ARENA_MAX=2`+`TRIM=128KB` | 750MB | 963MB | 未测出（收益也弱） |
| +`MMAP_THRESHOLD=128KB` | **400MB** | 786MB | **大 body p50 数倍劣化**（缺页风暴，非流式 256 完成率 86%→51%） |
| +`MMAP_THRESHOLD=64KB` | 744MB | 961MB | 同上且封顶更差 |

结论：**生产默认不启用 allocator env**；内存受限部署可显式启用全三件并在延迟与 RSS 间拍板。glibc 动态 mmap 阈值机制与同类案例（CPython #109534、uvicorn #2078）见 `docs/scratch/research/python-gateway-memory-2026-11-18.md`。

### 12.5 功能核验（labs 端到端，28/28 PASS）

真进程 + mock 上游跑通：健康/双面鉴权（API key 401、管理 JWT）、/v1/models、目录、非流式（usage 上游真实值）、流式（[DONE]、帧内 model 改写为直连名、usage 帧、SSE content-type）、改写链路三验（请求头/请求体/响应头经 mock 回显确认到达）、未知模型 404、**413 body 上限**（R6 新行为）、渠道 CRUD、**熔断冷却目录可见 + 开启后快速失败**、日志落库（零 SQL 异步可见）、看板汇总、**WAL kill -9 回放零丢失**、30s 混合负载 583 请求零失败。脚本：`labs/gwbench/runner/functional_check.py`。

### 12.6 测试与回归

全量 pytest（labs，-n 8）：1089–1091 passed + 存量随机 flake 0–2 个（基线 HEAD 同样出现、失败集每轮轮换、串行复跑全绿——与本轮改动无关，由三轮全量+基线对照钉死）。新增回归：`test_body_limit.py`（R6，8 条）、`test_background_sync_fsyncs_without_an_inline_flush`（R8）、`test_breaker_api.py`/`test_usage_pipeline_wiring.py` 扩展（R4）、`test_httpx_transport.py` bytes 消费断言更新（ADR-078）。本轮修掉的实现 bug：`aiter_bytes(chunk_size)` 吞 error 安全前缀（真 bug，测试抓出）；直连名流式慢路径（ADR-078 的动因）。

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
- **计数纪律（2026-11-14 更新）**：过去正文与 `ssot.md` 里并列着三套互相冲突的用例计数（583 = 582 passed + 1 flake、511 passed + 2 xfailed、48 个测试文件），现已全部删除：事实源只写「全量套件最近一次实跑通过」，复现命令与当次结果放 `testing.md`。本文正文中的实测数字（RPS、CPU ms、p99 等）是测量结果，保留。
- **治理冲突已解决（2026-11-14）**：`ssot.md` §16 已改写为文档职责边界——`ssot.md` 负责机制与口径、`adr/` 负责决策理由、`INDEX.md` 负责文档入口与按界面导航、`GLOSSARY.md` 负责词义；旁支文档只允许「实测证据」（本文、`testing.md`）、「操作手册/专项说明」（`upstream_transport.md`、`upstream-reasoning.md`、`postgres.md`、`ui.md`）与「agent 工作流配置」（`AGENTS.md`、`docs/agents/`）三类。
- 早期曾有一份 `ssot-index.md` 承担总入口页，已于 2026-11-16 撤销并合并进 `INDEX.md`；生产仓不再有该文件，凡引用它按 `INDEX.md` 理解。

### 10.5 证据归属

`.scratch/bench2/`、`docs/scratch/` 与各 `REPORT-*.md` 原始报告都位于**内部开发仓**，不随生产仓同步（`docs/scratch/` 在 `.gitignore` 内）。本文保留对它们的路径引用，仅为标明每个数字的出处；在本仓内这些路径不可解析，这是预期行为，不是待修的死链。需要复核原始数据时去开发仓。
