# ADR-076 数据面与控制面分离：契约先冻结、换语言设门槛

- 状态：accepted（2026-11-16 用户裁定）
- 关联：ssot §8.2、ADR-073/074/075、`docs/contracts/`、外部压测评审记录（2026-11-16）

## 背景

外部评审确认三件事：① 闭环压测的 p50/p99 主要反映排队（利特尔定律），不是网关自身开销，要改用**开环**方式测网关增量延迟；② 过载时吞吐塌陷（并发 16→1024 吞吐 38→21 RPS、p50 13.5s）说明没有准入控制；③ Python 数据面每请求固定 ~29ms、长流式每帧 ~0.19ms，原生数据面（Go/Rust）估计 0.5–2ms/请求（未实测）。用户提名 agentgateway（Linux 基金会，Rust，LLM/MCP/A2A 策略网关，standalone 与 Envoy 两种形态）作为现成数据面候选，并询问评审建议是否采纳。

## 决策

1. **方向**：数据面/控制面分离。数据面只做热路径：快照鉴权、路由、改写、流式转发、usage 抽取、重试与故障转移、指标上报。控制面（Python + SQLite/PG）保留配置、规则编译、控制台、计费对账、看板。现有 `CatalogSnapshot`（不可变快照 + 本地读）与 ledger WAL 追加已是雏形。
2. **不整体重写**：换语言不由印象决定，按「契约 → 一致性测试集 → 影子流量 → 灰度」推进，每阶段有量化门槛。
3. **契约冻结第一批两份**：`docs/contracts/usage-event-schema.md`（全局唯一 `event_id`，至少一次投递 + 幂等入库）与 `docs/contracts/data-plane-management-api.md`（`/healthz`、`/metrics`、`/drain`、`/snapshot_version`）。规则 DSL 与黄金测试集、路由/健康事件 schema 随 spike 阶段补齐。
4. **agentgateway 评估路径（先 A 后 B，用户裁定）**：A = 本机轻量基准：agentgateway standalone 与本网关接同一 mock 上游，跑长 SSE 与大 body（≈百 KB）两档，测吞吐、网关增量延迟、RSS，结论标注「Windows、单机、非生产」；B = 仅当 A 证明裸转发差距值得追，才做契约化 spike（Linux、一致性测试集 100% 通过、影子流量比对无差异）。评估看两个数：**裸转发性能差距**与**补齐我们 LLM 专有逻辑（逐 chunk usage 抽取、按渠道改写 body、首字节前重试、计费语义）的成本**。
5. **准入控制本期落地**：节点级最大在飞数 + 快速失败（过载返回 429 + Retry-After，不再让吞吐塌陷）；语义与默认值见实现时的设计记录。
6. **依赖锁定**：pip-compile 生成带哈希的 `requirements.lock`，压测与生产同栈。
7. **保留项**：LiteLLM 传输路径的 canonical-case 头改写保留（特定上游兼容修复，ADR-074 语义不变）；吞吐/TTFA 口径不变（GLOSSARY §5）。

## 明确不做（现阶段）

- 不整体重写数据面；不在没有开环数据时下「要不要换语言」的结论。
- 不为 10k QPS 预建多节点状态外置、服务网格、多地域。
- 不改动 SQLite 管线（memory 管线单进程略差疑似后台写线程争 GIL——记为假设，验证推迟）。

## 进入下一阶段的门槛

| 阶段 | 门槛 |
|---|---|
| A（1k QPS） | Linux 开环测试：每请求 CPU ≤ 8ms，70% 容量下网关增量 P99 < 20ms；准入控制上线后过载快速失败而不是塌陷 |
| B（3k QPS） | agentgateway/自研数据面 spike 达标（每核 chunk 吞吐 ≥ Python httpx-native 10×，增量 P99 < 5ms，每并发流 < 50KB，一致性 100%）；达标才进影子流量，否则继续 Python 并持续监控 |
| C（10k QPS） | 影子流量 → 按租户灰度 → 全量；Python 保留控制面与低 QPS 自定义 hook |

> 修正说明（2026-11-16，决策 4-A 基准后）：B 的「chunk 吞吐 ≥10×」须以 **Linux 开环**复测判定；Windows 单机闭集合首轮实测每帧 CPU 差距 6.3–7.5×（见下节），不构成达标与否的结论。

## 决策 4-A 基准记录（2026-11-16，Windows 单机）

第一轮本机基准（决策 4 的 A 步）已跑完，报告在 `docs/scratch/bench-agentgateway/REPORT.md`（scratch 已 gitignore，关键数字以本节为准）。口径：Windows 10 单机闭集合（固定并发，客户端、mock、被测进程同机）、非生产；agentgateway standalone v1.7.0 纯透传（无 policy、无鉴权）；每请求 CPU = 被测进程 CPU 时间增量 ÷ 完成请求数，不受排队影响，与阶段 A 门槛同口径。

| 指标 | 本网关（Python） | agentgateway（Rust，纯透传） | 差距 |
|---|---:|---:|---:|
| 非流式每请求 CPU（S1/S3） | 28.3–28.5ms | 1.1–1.4ms | 20–26× |
| 流式每帧 CPU（S2/S4：400 chunk / ~90KB） | 0.86–0.91ms | 0.115–0.145ms | 6.3–7.5× |
| RSS 峰值 | 112–184MB | 28–33MB | 4–6× |

**归因（/health 对照）**：同一 ASGI 栈上 `/health` 仅 **1.96ms CPU/请求**——28ms 中约 **26.5ms 是 `/v1` 请求管线**（鉴权、路由、usage 抽取、计费、SQLite 落库），不是 HTTP/ASGI 转发成本，与背景「Python 数据面每请求固定 ~29ms」的既有结论吻合。

**结论**：

1. **值得进阶段 B，但不能只换语言**：换掉 HTTP 层最多回收 ~2ms/请求，其余 ~26.5ms 在自己的请求管线里。
2. 阶段 B 拆两段验收：**B1** 先把 usage/计费/落库事件化 + 批量化（前置：usage-event-schema 冻结、影子流量对账 0 差异）；**B2** 再做原生数据面 spike，并补测「补齐 LLM 专有逻辑（逐 chunk usage 抽取、按渠道改写、首字节前重试、计费语义）后的成本」——agentgateway 本轮纯透传，此成本未覆盖，即决策 4 要求评估的第二个数。
3. **准入控制（决策 5）优先于换语言**：闭环下 p50/p99 由排队主导，先止住过载塌陷才有稳定的验收基线。

**局限**：Windows 单机闭集合，p50/p99 主要反映排队；mock 为 Python 单进程且直连臂已打饱和，跨臂吞吐/延迟不可直接归因；单 rep；流式每帧差距 6.3–7.5× 未达阶段 B ≥10× 门槛值，需 Linux 开环 + 补齐逻辑后复测判定（见上文修正说明）。
