# 驭流（Slipstream）界面索引 INDEX

> **用途**：按**界面（前端页面）**检索接口、决策、机制章节与测试。每个界面一节；找「某个页面历史上改过什么、为什么这样」从这里进，不要翻整个 ssot。
>
> **事实源分工**：路由定义 = `web/src/App.tsx`（本文件唯一权威）；后端接口 = `api/src/l06_api/routers/`；机制与口径 = [`ssot.md`](./ssot.md)；决策与理由 = [`adr/`](./adr/ADR-000.md)；词义 = [`GLOSSARY.md`](./GLOSSARY.md)；性能实测 = [`PERFORMANCE_REPORT.md`](./PERFORMANCE_REPORT.md)。
>
> **导航职责**：本文件按界面分类；`ssot-index.md` 是总入口（各文档指路）；`ssot.md` 按主题承载机制。

## 界面总览

| 界面 | 路由 | 页面组件 | 主要接口 | 相关 ADR（正文见 `docs/adr/`） |
|---|---|---|---|---|
| 登录 | `/login` | `web/src/pages/login` | `/api/auth/*` | ADR-020 |
| 全局概览 | `/overview` | `web/src/pages/overview` | `/api/settings`、`/health` | ADR-055 |
| 数据看板 | `/` | `web/src/pages/dashboard` | `/api/dashboard/*` | ADR-026、039、040、047、068 |
| 渠道列表 | `/channels` | `web/src/pages/channels` | `/api/channels` | ADR-042、044、050、070 |
| 渠道详情 / 新建 | `/channels/new`、`/channels/:channelId` | `web/src/pages/channel-detail` | `/api/channels/{id}/*`、`/api/channel-models/*` | ADR-016、029、033、041、043、070、073、074 |
| 模型管理 | `/catalog`（`?kind=` 筛选） | `web/src/pages/models` | `/api/catalog`、`/api/combos`、`/api/groups`、`/api/channel-models` | ADR-014、022、038、067、070、073 |
| 使用日志 | `/logs` | `web/src/pages/logs` | `/api/logs/*` | ADR-032、052、053、057、069、071、072 |
| 用户密钥 | `/keys` | `web/src/pages/keys` | `/api/keys` | ADR-056 |
| 模型试验 | `/playground` | `web/src/pages/playground` | `/v1/chat/completions`、`/v1/models` | ADR-017、034、036、074 |
| 系统设置 | `/settings` | `web/src/pages/settings` | `/api/settings` | ADR-054、055 |
| 旧书签 | `/combos` | `web/src/pages/models`（`CombosRedirect`） | —（并入 `/catalog`，`?kind=` 落组合筛选） | ADR-038、067 |
| 未实现导航项 | `navItems` 中未实现路径 | `web/src/pages/page-placeholder` | — | — |
| 全站控件层（跨界面） | — | `web/src/components/ui` | — | ADR-064（全量 shadcn copy-in + Tailwind 4，取代 ADR-062 / ADR-063）、ADR-031 |

> 控件层是全站共用的，不逐页重复列 ADR-064：所有页面最终都走同一套 shadcn copy-in 基元，迁移进度与回滚点记在归档的 [`archive/ui-migration-map.md`](./archive/ui-migration-map.md)，组件级采用映射、门禁阶梯与批处理纪律见 [`ui-adoption-map.md`](./ui-adoption-map.md) 与 [`ui-standards.md`](./ui-standards.md) §10。**终局（9847b66）**：0 error / 19 warning（全为 vendored 例外，应用层 0；文件数取当次 `--json`，该次为 81），门禁 `npm run lint:ui` = `node scripts/lint-ui.mjs --max-warnings 19`。

导航分组（`web/src/lib/navigation.ts`）：常规（全局概览、数据看板、使用日志）／配置（渠道管理、模型管理）／工具（模型试验）／系统（用户密钥、系统设置）。

---

## 登录（`/login`）

- **接口**：`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/password`（routers/auth.py）。
- **机制**：单管理员用户名密码 + JWT；无 RBAC/审计到人（ADR-020）。`/api/*` 除登录与 `/v1/*`、`/health` 外都要管理员 JWT（ssot §6 开头）；密钥表鉴权依据仍是 `key_hash`，明文只在 `key_enc`（ADR-056）。
- **已知坑**：`verify_password` 是同步 PBKDF2（240k 轮，实测 ~0.77 s），在 async 登录端点里阻塞事件循环，ssot §11.4 已记待办（移线程池）。
- **测试**：`api/tests/test_auth.py`。
- **ssot**：§5.9、§6。

## 全局概览（`/overview`）

- **接口**：`GET /api/settings`（接入地址与示例文案相关配置）、`GET /health`。
- **页面**：接入地址、示例、运行概况；无独立后端聚合接口。
- **测试**：`api/tests/test_health.py`、`api/tests/test_app_import.py`（应用装配冒烟）。
- **ssot**：§2.2（运行拓扑与端口）、§6（`/health`，无 `/readyz`）。

## 数据看板（`/`）

- **接口**：`GET /api/dashboard/summary`、`GET /api/dashboard/timeseries`、`POST /api/dashboard/rebuild`（routers/dashboard.py）。
- **口径**：吞吐公式、缓存比例归一、16 色调用量配色（ADR-019/040）；P95 合计按请求数加权平均（ADR-039）；延迟四段口径 + 成对样本（ADR-028，日志展示部分被 ADR-032 修订）；统计聚合统一由 `l05_service/aggregates.py` 的 `usage_totals()` / `summary_metrics()` 计算，估算行只计请求数（ADR-047）。
- **已知坑**：`daily_aggregates` 只写不读，大数据量看板扫 `request_logs` 会慢（ssot §12.2）。
- **测试**：`api/tests/test_dashboard.py`、`api/tests/test_timeseries.py`、`api/tests/test_latency_metrics.py`、`api/tests/test_logs_summary.py`（聚合共用）。
- **ssot**：§5.8、§6（`/api/logs/summary` 与 `/api/dashboard/summary` 的字段口径）。

## 渠道列表（`/channels`）

- **接口**：`GET /api/channels`、`POST /api/channels`；列表搜索字面量匹配（`l04_repo/search.py like_contains()`，ADR-044）；`POST /api/channels/{id}/test-connection` 等动作在详情页。
- **状态口径**：五态 `visible_status`（fault/open/half_open/disabled/normal），聚合取最差；`disabled` 这一态按 `banned_until` 分「封禁」（空 = 永久、只能人工解除、不出现于 /v1/models）与「冷却」（非空 = 到期自动恢复、期间仍可见），熔断/故障是冷却的来源而非并列状态（ADR-070 修订 ADR-050）；列表页取数 `include_disabled=true`（ADR-050）。
- **已知坑**：多渠道同毫秒创建时「主密钥」判定靠 Fernet 解密比对明文，不靠排序（ADR-043）；`PATCH /api/channels/{id}` 的 `api_key` 与同渠道其它密钥重复返回 409 不静默覆盖（ssot §6）。
- **测试**：`api/tests/test_channels.py`、`test_channel_discovery.py`、`test_channel_keys.py`、`test_channel_legacy_schema.py`、`test_failover_rules.py`、`test_breaker_api.py`、`test_repo_search.py`。
- **ssot**：§5.5、§5.9、§6、§8.4。

## 渠道详情 / 新建（`/channels/new`、`/channels/:channelId`）

- **接口**：`GET/PATCH/DELETE /api/channels/{id}`、`GET/POST /api/channels/{id}/keys`、`PATCH/DELETE /api/channels/{id}/keys/{key_id}`、`GET/POST /api/channels/{id}/models`、`POST /api/channels/{id}/models/bulk`、`POST /api/channels/{id}/probe-all`、`POST /api/channels/{id}/rewrite-preview`、`POST /api/channels/{id}/test-connection`；渠道模型侧 `PATCH/DELETE /api/channel-models/{id}`（`model_type` 可传 `auto` 推断）、`POST /api/channel-models/{id}/reset`、`POST /api/channel-models/{id}/probe`。
- **机制**：渠道操作模式 = 请求头/体、响应头/体有限改写（ADR-024 → 动作模型 v2 由 ADR-029 取代）；渠道多密钥 + 统一模型参数（ADR-033）；连接测试与模型探测分离，协议探测落库（ADR-016）；只有深探成功才写回熔断（ADR-041）；模型级周计划段 + 冷却 + 双计数熔断（ADR-025），计划测试在 `test_schedule.py`。
- **已知坑**：非 slug 完整性错误统一 500 INTERNAL（不回传 SQL 明文，ssot §6）；改动规则只作用于「进入 SDK 前、离开 SDK 后」（ADR-034）；请求 / 响应头的透传基线与禁传清单见 ADR-074（客户端头为基线、凭据禁传、网关必需头最后覆盖）。
- **测试**：`api/tests/test_channel_models.py`、`test_model_type_gate.py`、`test_probe.py`、`test_rewrite_actions.py`、`test_rewrite.py`、`test_response_rewrite.py`、`test_breaker.py`、`test_schedule.py`、`test_schedule_api.py`、`test_rounds.py`、`test_direct_native_model.py`。
- **ssot**：§3.3、§5.5、§5.6、§6。

## 模型管理（`/catalog`，`/combos` 重定向）

- **接口**：`GET /api/catalog`、`GET/POST /api/combos`、`GET/PATCH/DELETE /api/combos/{id}`、`GET /api/groups`（自动组合只读派生，`kind=combo` + `origin=auto`，ADR-067）；`?include_disabled=true` 取目录（封禁条目默认不出现、冷却条目始终可见）；模型默认参数 `GET /api/model-defaults/status`、`POST /api/model-defaults/refresh`、`POST /api/channel-models/prefill`（新建预填，不触发刷新）、`POST /api/channel-models/{id}/adopt-defaults`（界面名「应用默认参数」，旧称「恢复自动值」；`force` 全量重置；ADR-073）。
- **机制**：模型目录与组合同页呈现（ADR-067 起取消「组合模型」页签，旧 `/combos` 仅重定向并保留 state，取代 ADR-038 的两 tab）；组合最多 3 层、priority 以「层」呈现且数值越大越先（ADR-067）、删除 weight、max_attempts 按轮（ADR-014/037）；组合不允许嵌套（ADR-012 → ADR-014）；自动组合查询期派生（ADR-022）；`model_type` 字段 + 类型感知探测，`auto` 重新推断（ssot §3.5）；目录缓存 TTL 与引擎预热（`l05_service/config_catalog.py`，`test_ttl_cache.py` / `test_zs2_catalog_cache.py`）；模型默认参数（价格 / 上下文上限 / 能力位）每日自 models.dev 惰性同步，新建渠道模型按 `upstream_model` 预填并标注来源，刷新按值比对更新渠道模型（等于基线才采用，人工改动保留），档位名单与阈值进 `app_settings`（ADR-073，accepted：2026-11-16 实现，机制与配置见 ssot §8.7）。
- **已知坑**：渠道改名会使旧 `call_model` 调用名失效（ADR-022）；memory 管线下目录读取依赖进程内快照，管理写入最长 `catalog_poll_interval_ms`（默认 30 s）后可见（ADR-057/060）。
- **测试**：`api/tests/test_catalog.py`、`test_combos.py`、`test_model_types.py`、`test_implicit_groups.py`、`test_p0d_routing_shape.py`、`test_ttl_cache.py`、`test_zs2_catalog_cache.py`、`test_l1_db_merges.py`、`test_model_defaults.py`、`test_model_defaults_sync.py`；web 侧 `web/src/__tests__/model-defaults.test.tsx`。
- **ssot**：§3.2、§3.5、§5.1、§6。

## 使用日志（`/logs`）

- **接口**：`GET /api/logs`、`GET /api/logs/recent`、`GET /api/logs/filters`、`GET /api/logs/summary`、`GET /api/logs/{request_id}`。
- **机制**：日志只显示首字/首答/总，看板保留四段（ADR-032）；失败尝试可见（`attempt_status` 筛选 + 抽屉明细，ADR-052）；时间筛选「预览不查询、提交权归搜索」+ 今天留 1 h 余量（ADR-053）；正文日志按渠道策略记录（ssot §5.7）；保留期配置与清理（`test_retention.py`）；流式终态按真实结局记账（ADR-051）。
- **已知坑**：classic 写路径在流式请求上曾实测丢 1/2364 行（0.042%，ADR-057）；多实例共享库掉到 4.9%（ADR-059）。
- **测试**：`api/tests/test_logs.py`、`test_logs_summary.py`、`test_log_split.py`、`test_body_logging.py`、`test_retention.py`、`test_latency_metrics.py`。
- **ssot**：§5.7、§6（summary 字段口径）、`PERFORMANCE_REPORT.md` §5。

## 用户密钥（`/keys`）

- **接口**：`GET/POST /api/keys`、`PATCH/DELETE /api/keys/{id}`、`POST /api/keys/{id}/reset`、`GET /api/keys/{id}/secret`。
- **机制**：明文加密留存、可复制可重置（ADR-056，`api_keys.key_enc` Fernet 列；旧数据无 key_enc 时提示先重置）；鉴权依据仍是 `key_hash`；密钥可编辑标识（ADR-020）。
- **已知坑**：浏览器存储收敛为单键 `system.storage` JSON blob，密钥与明文不入 blob（ADR-055）。
- **测试**：`api/tests/test_key_management.py`、`api/tests/test_keys_and_chat.py`（含 `/v1` 鉴权路径）。
- **ssot**：§5.9、§6。

## 模型试验（`/playground`）

- **接口**：`POST /v1/chat/completions`（stream SSE）、`GET /v1/models`（enabled 的直连/组合/分组）；错误形状 OpenAI 风格（ssot §6）。
- **机制**：直连仅限 `openai_chat` 协议（routing.py:127/154/607/634）；传输双轨（ADR-034）与 LiteLLM 传输层字节级修复（ADR-036）；错误正文脱敏——`error.message` 不回显上游原文（ssot §5.3 与 `test_error_redaction.py`）；cost 默认关闭（ADR-058）；请求 / 响应头透传语义见 ADR-074（出站以客户端头为基线，入站上游头原样转发，ssot §5.6）。；流式无总闸、思考块计入兜底估算（ADR-075）
- **测试**：`api/tests/test_streaming_chat.py`、`test_httpx_transport.py`、`test_fake_upstream.py`、`test_error_redaction.py`、`test_error_taxonomy.py`、`test_e2e_smoke.py`。
- **ssot**：§5.2、§5.3、§5.4、§6。

## 系统设置（`/settings`）

- **接口**：`GET/PATCH /api/settings`。
- **机制**：产品改名与内部标识统一（ADR-054/055）；设置项与 `api/.env.example` 对齐（ssot 维护规则 2）；上游超时看门狗（连接/首字节/读空闲）可按部署覆盖，env 为默认值、可重置（ADR-075 修订）。
- **测试**：`api/tests/test_settings.py`。
- **ssot**：§5.9、§8、§9。

## 未实现导航项（占位页）

`web/src/App.tsx` 的 `IMPLEMENTED_PATHS` 之外的 `navItems` 渲染为 `PagePlaceholder`。当前插槽见 `web/src/lib/navigation.ts`；不做为本文件的空壳条目——占位页没有接口、没有测试，实现后再补本节。

## 跨界面基础设施（不属于任何单页面）

| 设施 | 相关接口/代码 | 测试 |
|---|---|---|
| 健康检查 | `GET /health`（无 `/readyz`） | `test_health.py` |
| 应用装配与 lifespan（usage sink 启动/排空、web dist 挂载、litellm 预热） | `l07_bootstrap/app_factory.py` | `test_app_import.py`、`test_web_dist_mount.py`、`test_usage_pipeline_wiring.py` |
| 观测写管线（WAL + 批量落库） | `l05_service/usage_sink.py`、`l04_repo/usage_repo.py` | `test_usage_sink.py`、`test_usage_pipeline_wiring.py` |
| 账本 / 拆表路径 | `l04_repo/ledger_repo.py`；`LEDGER_SPLIT` 默认 `off` | `test_ledger_wal.py`、`test_ledger_read.py`、`test_ledger_split_default.py` |
| 数据库方言（SQLite/PG 分支） | `l02_infra/dialect.py`、`db.py` | `test_postgres_dialect.py`、`test_postgres_integration.py`（设 `LLMAPI_TEST_PG_DSN` 才跑）、`test_schema.py` |
| 稳定性不变量 | 16 条不变量 + 违例清单 | `test_stability_invariants.py` |
| `/v1` 与密钥鉴权的端到端 | `l06_api/routers/v1.py`、`keys.py` | `test_keys_and_chat.py`、`test_e2e_smoke.py` |
| 时间序列聚合（供看板与日志共用） | `l05_service/timeseries.py` | `test_timeseries.py`、`test_dashboard.py` |
| 控件层与界面规范 | `web/src/components/ui/`（迁移目标：shadcn copy-in）；规则 [`ui-standards.md`](./ui-standards.md)（唯一规范正文）；台账 [`ui-adoption-map.md`](./ui-adoption-map.md)；历史 68 模式台账见 [`archive/ui-migration-map.md`](./archive/ui-migration-map.md) | `npm run test:visual`；`lint:ui` 的「禁原生 select/table」规则在 ADR-064 阶段 4 落地 |

---

## 维护规则（本文件专属）

1. 路由变化以 `web/src/App.tsx` 为准：新增页面 → 加一行总览 + 一节正文（缺接口/测试的写占位状态，不许编空壳）；删除页面 → 删节。
2. 接口变化改 `ssot.md` §6（机制共章）与本文件对应节的接口列表，两处同步。
3. 新 ADR 建文件后，在「相关 ADR」列补编号并保证 `docs/adr/` 里能找到正文。
4. 不要在本文件写任何易变计数（用例数、测试文件数、耗时秒数）——见 `ssot.md` §16 的计数纪律。
5. 界面规则或控件实现变化 → 改 [`ui-standards.md`](./ui-standards.md)（第 6 节评审清单与 §9 现状约束同属阻断项）；迁移进度记在 [`ui-adoption-map.md`](./ui-adoption-map.md)（历史台账见 `archive/ui-migration-map.md`），不写进本文件。