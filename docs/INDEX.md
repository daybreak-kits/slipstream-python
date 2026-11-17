# 文档索引（生产仓留档）

生产仓只留：规范正文、决策记录（ADR 全量）、性能与运维、以及提炼后的精华专项。过程稿/历史稿/压测原始数据不在此仓（开发仓 docs/scratch/ 与 archive/ 均不入库）。

## 规范与决策
- **ssot.md** — 单一事实来源：数据面契约、路由、计费、UI 采纳的权威正文
- **adr/** — 架构决策记录全量（决策记录只增不删；被取代的用新 ADR 标注取代关系）
- **contracts/** — 对外契约：data-plane-management-api.md（管理面 API）、usage-event-schema.md（计费事件 schema）

## 性能与运维
- **PERFORMANCE_REPORT.md** — 性能权威报告：Windows 同机口径（§0–§9）+ Linux 开环三臂基准与 agentgateway 对照（§10–§11）+ 五臂基准与 P0/P1 优化落地、内存治理（§12）、数据与复现（§13）
- **testing.md** — 测试策略与门禁
- **postgres.md** — SQLite→Postgres 迁移评估
- **GLOSSARY.md** — 术语表

## 专项
- **upstream_transport.md** — 上游传输与 SSE 消费机制正文
- **upstream-reasoning.md** — 上游 reasoning/thinking 接入实测（协议差异与证据，配套 ssot §5.4/§9.3）
- **ui.md** — 前端技术栈/规范/采纳状态提炼合并版（原 ui-standards、ui-adoption-map、ui-stack、design-system 四篇的精华，原文在开发仓）
- **research/** — 研究记录（证据与决策依据，非规范）：cache-hit-rate-2026-11-15.md（缓存命中率归因，ADR-068）、litellm-rust-2026-11-15.md

## Agent 工作流
- **agents/** — agent 工作流配置，不承载项目事实：`issue-tracker.md`（本地 Markdown 票据约定与入库策略）、`triage-labels.md`（五角色 → `Status:` 取值）、`domain.md`（领域文档消费规则与冲突优先级）。入口在仓库根 `AGENTS.md`。
