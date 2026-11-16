# 驭流（Slipstream）文档入口

> **这里只指路，不承载事实。** 下表是仓库里**允许存在的全部文档**——任何不在此列的 `.md` 都算旁支（`ssot.md` §16），发现即并入或删除。`archive/` 下的文档是**历史存档**：允许存在、可以引用，但不作为权威，也不再更新。
>
> 快照：2026-11-14，`master` @ HEAD（提交见 `git log` 的同名提交：9847b66 起；本页只在文档集合或职责变化时更新，**不写死 commit hash**——写死必然过期；2026-11-14 增补 `ui-adoption-map.md` 一行与 §10 指向；控件层 lint 基线 **0 error / 19 warning**（19 全为 vendored 例外；文件数随每次提交变，取当次 `--json` 的 `filesScanned`，9847b66 时为 81）。
>
> 权威顺序：运行代码与测试 > [`ssot.md`](./ssot.md) > [`adr/`](./adr/ADR-000.md) > 实测报告 > 本页。

## 全部文档

| 文档 | 一句话职责 | 什么时候看它 |
|---|---|---|
| [`INDEX.md`](./INDEX.md) | 按**界面**做的索引：每个页面对应的接口、决策、机制章节、测试与坑 | 从「某个页面」出发排查或改动时的第一站 |
| [`ssot.md`](./ssot.md) | 机制与口径的**唯一事实源**：领域模型、路由、流式契约、改写、日志、配置、API 面、数据模型、容量、生产差距 | 要确认「实际怎么工作、字段是什么、默认值多少」时 |
| [`adr/`](./adr/ADR-000.md) | 一条决策一个文件：背景、备选、否决原因、后果、状态；ADR-001..068 已全部有独立文件，`ssot.md` §13 只是索引表 | 想知道「为什么这样做、当初否决了什么」时；写新决策前先读 ADR-000 的格式与编号规则 |
| [`GLOSSARY.md`](./GLOSSARY.md) | 纯词义表：一个词到底指什么、哪些概念容易叫混 | 术语有歧义，或准备新增一个概念/词时 |
| [`PERFORMANCE_REPORT.md`](./PERFORMANCE_REPORT.md) | 实测数字与口径：单实例天花板、每请求 CPU 归因、memory/cost 收益、多实例矩阵、到 1000 RPS 的台数 | 做容量规划、比较性能回归、或怀疑「为什么这么慢」时 |
| [`research/`](./research/) | 研究记录（**只作证据，不作权威**）：`cache-hit-rate-2026-11-15.md`（缓存命中率归因、厂商前缀缓存机制、优化清单）、`litellm-rust-2026-11-15.md`（Rust 网关能否构建与试用的实测） | 想搞清「命中率为什么是这个数、还能怎么提」或「要不要上 Rust 网关」时 |
| [`testing.md`](./testing.md) | 测试怎么跑、怎么复现一次实跑、用哪些命令与端口 | 要跑测试、复现某次结果、或判断「这个失败是不是已知」时 |
| [`upstream_transport.md`](./upstream_transport.md) | 上游传输契约细节：LiteLLM 与 httpx 两条路的差异、限制与已知坑 | 改上游调用、排查透传/改写/SSE 行为差异时 |
| [`upstream-reasoning.md`](./upstream-reasoning.md) | 上游思考（reasoning / thinking）接入参考：各厂商开启参数与优先级、载体字段、实测结论、UI badge 判定 | 接新模型/渠道要确认「怎么开思考、思考在哪个字段、怎么展示」时 |
| [`postgres.md`](./postgres.md) | PostgreSQL 分支现状与部署步骤 | 切 PG、迁移数据、排查 PG 侧连接/超时问题时 |
| [`ui-standards.md`](./ui-standards.md) | **UI 唯一规范正文**：token、组件 recipe、页面模式、文案、评审清单、§9 现状约束、§10 批处理脚本纪律、§11 视觉语言（原则/排版/间距/色彩/形状层级/动效/可视化/反馈空态/深色/十条反例） | 写或改任何界面前先读；评审界面按第 6 节清单与 §9/§10 逐条过 |
| [`ui-adoption-map.md`](./ui-adoption-map.md) | **唯一现状台账**：42 个基元 × 调用点（导出面、调用点、必须的改法、风险）、§5 批次终态、§7 批处理事故与纪律、§9 门禁阶梯、§10 门禁真实运行环境、§11 待补 token 与机制 | 查「某控件现在谁在用、迁移到哪一步、门禁该收多少」时 |
| [`ui-stack.md`](./ui-stack.md) | 技术栈取舍评估（要不要换 antd / 组件库），正文是 2026-11-14 前的快照 | 有人再提「换组件库」时先读它，别重做评估 |
| [`design-system.md`](./design-system.md) | **重定向桩**：内容已并入 `ui-standards.md` §11 与 `ui-adoption-map.md` §11；仅因 ADR-066 与两个脚本按路径引用而保留 | 只有顺着 ADR/脚本里的路径点进来时；不要在这里补内容 |
| [`archive/`](./archive/) | 已归档的历史文档（**只读历史，不作权威**）：`ui-migration-map.md`（68 模式台账与门禁阶梯原文）、`ui-inventory.md`（控件盘点）、`ui-options.md`（三方选型对比）、`tailwind4-migration.md`（Tailwind 4 升级手册） | 只查「当初为什么这么迁、怎么迁的」；现状一律看 `ui-adoption-map.md` |

## 分工与禁区

- 机制 → `ssot.md`；决策 → `adr/`；按界面导航 → `INDEX.md`；词义 → `GLOSSARY.md`；数字 → 实测报告。**同一事实只允许一处是权威**，其余位置只能指过去，不能各写一套。
- 本页不写结论；与事实源冲突时改本页。
- 新增文档必须在上表登记，否则按 `ssot.md` §16 视为不允许的旁支。
- 旁支只允许两类：**实测证据**（`PERFORMANCE_REPORT.md`、`testing.md`、`research/` 下的研究记录）与**操作手册/专项说明**（`upstream_transport.md`、`postgres.md`、`ui-standards.md`、`ui-adoption-map.md`、`ui-stack.md`、`design-system.md` 桩，以及 `archive/` 下的历史存档）——只放数字、命令与差异，机制与配置仍以 `ssot.md` 为准。

## 维护规则

1. **计数纪律**：不写用例数、测试文件数、通过数、耗时秒数、构建产物体积——改一次代码就变；只写「全量套件最近一次实跑通过」，数字与命令放 `testing.md` / `PERFORMANCE_REPORT.md`（见 `ssot.md` §16）。
2. 改机制 → 改 `ssot.md`；改决策 → 新建 `adr/ADR-xxx` 并在 `ssot.md` §13 登记表加一行；改界面或接口 → 改 `INDEX.md`；改词义 → 改 `GLOSSARY.md`。
3. 本页只在「文档集合」或「文档职责」变化时更新。
