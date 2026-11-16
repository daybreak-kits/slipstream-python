# ADR-072 尝试明细行化：attempts_json → attempts 表

---
status: accepted（实现先行，随本批提交转正；2026-11-15 由 proposed 转正）
date: 2026-11-15
supersedes: 无（`request_logs.attempts_json` 单列存储退出；ADR-032/044/052/069/071 继续有效）
related: ADR-032（日志与正文分离）、ADR-044（字面量搜索转义）、ADR-052（日志筛选与时间预设）、ADR-069（日志三视图：事实层与展示层分离）、ADR-071（FOFA 查询语法与 AttemptResolver）
---

## 背景

一次请求的尝试链（候选上游、失败切换、跳过原因）今天以 JSON 数组放在 `request_logs.attempts_json`（`api/src/l04_repo/orm.py:227`，写入 `api/src/l05_service/chat.py:564` 与 `api/src/l05_service/probe.py:1100`）。本机开发库实测：17,171 条请求、46,805 条尝试（每请求 1–4 条，均值 2.73）、载荷 18.26 MB、390 B/尝试。

这一列同时承担五个职责，每个都在为「数组塞进一列」付代价：

1. **展示**：`api/src/l06_api/routers/logs.py:48` 把整列反序列化后塞进响应（`schemas.py:789` `attempts: list[dict[str, Any]]`）。
2. **搜索**：`api/src/l04_repo/repo.py:826-847` 用 json1 `json_each` 展开后 LIKE 6 个路径；`:874-884` 用 blob 片段匹配（见决策 7 的缺陷）。
3. **筛选**：渠道 / 尝试状态 / 上游模型 / 错误码 / HTTP 状态分别走 `repo.py:773`、`:781`、`:783`、`:895`、`:935-936`、`:887-892`。
4. **三视图**：`api/src/l05_service/log_views.py:77-143` 把整列读出来、`status_segments` 在 Python 里展开切段；扫描上限 `MAX_SCAN_ROWS = 50_000`（`log_views.py:40`）。
5. **查询语法**：ADR-071 决策 9 把尝试级字段交给 `AttemptResolver`，因为 JSON 的展开形状方言相关（SQLite `json_each` / PG `jsonb_array_elements`）。

实测（task-103，见 `.scratch/perf-attempts/REPORT.md`，脚本与原始 JSON 同目录）：

| 维度 | JSON 列 | 独立行表 | 结论 |
| --- | --- | --- | --- |
| 12 条日志查询 p50 | 0.3–4.6 s | 1.2 ms–1.0 s | 行表 6–12× |
| 索引驱动上限 | 954–3039 ms | 43–103 ms | 22–42× |
| 写（每 1000 请求） | 1253.6 ms | 2489.5 ms | +1.24 ms/请求（+98.6%） |
| 空间（每尝试） | 390 B（18.26 MB） | 260 B（12.19 MB） | −33% |
| 跨库 | SQLite 有 json1，PG 无 → `repo.py:850-871` 自述 CAPABILITY DOWNGRADE | 两侧同形 DDL/SQL | PG 从「降级」变「同形」 |
| 尝试级聚合 | json_each + Python（现网 4.3 s） | SQL GROUP BY（164 ms） | 26× |

用户把裁决权交给实测：四条标准（搜得动、写得快、跨库一致、可扩展）全部指向行化。本 ADR 把结论变成可施工的设计；实现等 `task-101`（冷却后端改 `repo.py`/routing）落地后单独派发，避免同文件互相覆盖。

## 决策

1. **新增 `attempts` 表（ORM 名 `AttemptORM`），与 `request_logs` 1:N，随父行级联删除；`attempts_json` 在全量回填与切读验收后删除。** 不做长期双存储。
2. **所有尝试级判定改成 `EXISTS (SELECT 1 FROM attempts ...)`**，删除 json1 分支与非 SQLite 降级分支；跨库从「能力不同」变成「同一段 SQL」。
3. **三阶段灰度**：双写（读 JSON）→ 切读（双写保留）→ 停写 JSON 并 drop 列（不可逆，单独发布）。每阶段独立配置开关，见决策 5。
4. **`AttemptResolver` 由仓库层实现为单一 EXISTS 构造器**，`!=` 编译成 `NOT EXISTS(相等)`，见决策 6。
5. **`MAX_SCAN_ROWS` 保留**，语义与数值都不变；行化的收益来自过滤下推与不再解析 JSON，而不是取消上限（见决策 3 末）。

### 决策 1 表设计与索引

`AttemptORM`（`api/src/l04_repo/orm.py`，紧跟 `RequestLogORM` 定义）：

| 列 | 类型 | 可空/默认 | 来源（`_attempt()` 的键，`chat.py:167-211`） | 用途 |
| --- | --- | --- | --- | --- |
| `id` | String(40) | PK，`new_id()` | 新生成 | 行标识 |
| `log_id` | String(40) | NOT NULL，FK `request_logs.id` `ondelete=CASCADE` | 父行 id | 关联/级联/幂等键前缀 |
| `seq` | Integer | NOT NULL | 数组下标（0 起） | 保序；幂等键 |
| `status` | String(16) | NOT NULL | `status` | 尝试状态筛选、状态视图 |
| `skipped` | Boolean | NOT NULL，False | `skipped` | 展示原样输出 |
| `channel_model_id` | String(40) | NULL | `channel_model_id` | 渠道模型筛选 |
| `channel_slug` | String(64) | NOT NULL，空串 | `channel_slug` | 渠道筛选、搜索 |
| `channel_name` | String(128) | NOT NULL，空串 | `channel_name` | 渠道筛选、搜索 |
| `upstream_model` | String(128) | NOT NULL，空串 | `upstream_model` | 上游模型筛选/聚合/搜索 |
| `http_status` | Integer | NULL | `http_status` | 状态段下钻 |
| `error_code` | String(64) | NULL | `error_code` | 失败原因、筛选、搜索 |
| `message` | Text | NOT NULL，空串 | `message`（写入端已截断 2000，`chat.py:204`） | 搜索、展示 |
| `ms` | Integer | NOT NULL，0 | `ms` | 展示、P95 聚合 |
| `round` | Integer | NOT NULL，1 | `round` | 展示、排序 |
| `at_ms` | BigInteger | NULL | `at` | 状态视图时间（缺省回退父行 `created_at`，`log_views.py:217`） |
| `priority_path_json` | Text | NOT NULL，空数组 | `priority_path` | 展示 |
| `request_headers_json` | Text | NOT NULL，空对象 | `request_headers` | 详情诊断 |
| `response_headers_json` | Text | NOT NULL，空对象 | `response_headers` | 详情诊断 |

表约束与关系：

- `UniqueConstraint(log_id, seq, name="uq_attempts_log_seq")`：既是排序键，也是写重放/回填的幂等键（`ON CONFLICT DO NOTHING` 的冲突目标）。
- `RequestLogORM.attempts = relationship(AttemptORM, order_by=AttemptORM.seq, cascade="all, delete-orphan", passive_deletes=True)`。
- SQLite 的 `PRAGMA foreign_keys=ON` 已在 `api/src/l02_infra/db.py:97` 打开，`repo.py:1125` 的 retention 删除父行会级联删 attempts；PG 原生 FK 同理。**不另写清理逻辑**。

索引（与实测同口径；每个都在报告第 4 节量过体积）：

| 名称 | 定义 | 服务的查询 | 实测体积 |
| --- | --- | --- | --- |
| `uq_attempts_log_seq` | UNIQUE(log_id, seq) | 关联/详情加载、幂等 | 含在表内 |
| `ix_attempts_error_code` | btree(error_code) | `error_kinds` 筛选 | +0.75 MB |
| `ix_attempts_upstream_model_nocase` | btree(lower(upstream_model))，SQLite 表达式索引 / PG `CREATE INDEX ... (lower(upstream_model))` | `upstream ==` 小写等值、状态段下钻 | +1.20 MB |
| `ix_attempts_channel_name` | btree(channel_name) | 渠道筛选（与父行 OR） | +0.78 MB |
| `ix_attempts_status` | btree(status) | `attempt_status=error/success` | +0.72 MB |

不建索引及理由：`channel_slug`/`channel_model_id`（基数低，渠道筛选先按 `channel_name` 收窄，实测命中 13,830/17,171 无收益）、`at_ms`（时间过滤在父行 `created_at`；段内排序在内存；阶段 3 若把状态视图改成 SQL 窗口函数再加 `(status, at_ms)`）、`message`（`LIKE` 前置通配，btree 无效）。

SQLite 侧无手写 DDL：`Base.metadata.create_all`（`db.py:501`）按 ORM 建表建索引，新库直接有、旧库多一次 `CREATE TABLE`（`db.py:171` 注释同口径）。PG 侧必须在 `db/postgres_schema.sql:147` 的 `request_logs` 之后追加 `attempts` 段（`TEXT`/`INTEGER`/`BOOLEAN`/`BIGINT`、`REFERENCES request_logs(id) ON DELETE CASCADE`、表达式索引），并在 `db/migrate_sqlite_to_postgres.py:73` 的 `TABLE_ORDER` 中把 `attempts` 插到 `request_logs` 之后、`request_bodies` 之前（父先子后）；迁移脚本按 ORM∩SQLite 列自动拷贝（`:129-177`），新表无需额外代码。

### 决策 2 写路径

写入点有四处，全部收口到仓库层两个原语（新文件 `api/src/l04_repo/attempt_filters.py` 或 `repo` 内）：

- `insert_attempts(session, log_id, attempts) -> int`：`executemany` + 方言 `on_conflict_do_nothing()`（复用 `l04_repo/dialects.py` 的 `conflict_insert`），**不 commit**，调用方决定事务。
- `replace_attempts(session, log_id, attempts) -> int`：`DELETE WHERE log_id` 后插入，供延迟补丁被重放时覆盖（seq 保序不变）。

四个调用点：

1. **usage sink 打开（生产批量路径）**：`chat.py:564` 组装 observability → `usage_sink.record`（`api/src/l05_service/usage_sink.py:122`）写 WAL → `flush_usage_records`（`:342`）→ `usage_repo.insert_request_logs`（`l04_repo/usage_repo.py:130`）。改法：`build_log_row`（`usage_repo.py:52`）把 attempts 从行 dict 拆出为独立列表随 WAL 条目一起落盘；flush 时在**同一事务**先 `insert_request_logs` 再 `insert_attempts`（每批 500 行，`DEFAULT_BATCH_SIZE` 同口径）。WAL 重放安全：父行与 attempts 都是冲突忽略，重放不产生重复行。注意 `insert_request_logs` 对冲突行也返回「成功」，attempts 不能依赖它的返回值判断是否新插入 —— 只能靠唯一键幂等。
2. **LEDGER_SPLIT=on 且 sink 关（延迟补丁路径）**：父行先以 billing 列写入，observability（含 attempts）延迟补丁：`log_pipeline.py:189` `submit_observability` → `:98/:105` `_flush_batch` → `repo.update_request_log`（`repo.py:607`）；返回 0 表示被 retention 清掉、整行重建。改法：每行 patch 成功后调 `replace_attempts(log_id, attempts)`；重建分支在同一事务里插父行 + attempts。`config.py:76` 的 `ledger_split` 默认 `off`，开关与观测口径不变。
3. **单写路径（LEDGER_SPLIT=off）**：`chat.py:628/634` `add_request_log(**values)`，`repo.py:600` 内部 `session.add` + 一次 commit。改法：`values` 不再带 `attempts_json`，改带 `attempts=列表`；`add_request_log` 用 `RequestLogORM(**values_without_attempts, attempts=[...])` 走 ORM cascade —— 单事务、单 commit，写放大最小。
4. **探针**：`probe.py:1100` 同样只组装 attempts 列表，走上面同一入口，不单独实现。

批量与事务细节：单请求 1–4 条尝试，直接 `executemany` 即可；报告中 552 ms/1000 请求的尝试插入来自逐条 ORM flush，改批量后预期减半。事务边界保持现状（单写：一请求一事务；sink：一批 500 行一事务；补丁：一批 patch 一事务）。写重放/重试只会撞唯一键，不产生重复。

### 决策 3 读路径替换（逐处）

| 位置 | 现在 | 行化后 |
| --- | --- | --- |
| `repo.py:773` 渠道 | 父行 `channel_name`/`final_channel_model_id` + blob 片段 `channel_name`/`channel_slug` | 父行两列 OR `EXISTS attempts(channel_name=:v OR channel_slug=:v OR channel_model_id=:v)` |
| `repo.py:781` HTTP 状态 | `http_status` 片段（前缀缺陷） | `EXISTS attempts(http_status=:v)` |
| `repo.py:783` 错误码 | `error_code` 片段 | `EXISTS attempts(error_code=:v)` |
| `repo.py:809-823` | `json_each` + `json_valid` 兜底 | 删除 |
| `repo.py:826-847` 搜索 | `EXISTS(json_each 6 路径 LIKE)` | `EXISTS(or_(like_contains(6 列)))`，路径映射同 `ATTEMPT_SEARCH_PATHS` |
| `repo.py:850-871` | 非 SQLite blob 值前缀降级 | 删除（两侧同形） |
| `repo.py:874-884` | blob 片段等值 | 删除 |
| `repo.py:887-901` | `status: error` 片段 / 列非空 | `EXISTS(status="error")` / `EXISTS(任意行)` |
| `repo.py:935-936` 渠道+组合成员 | 片段 | `EXISTS` 同上 |
| `repo.py:702` | `scan_request_log_facts` select `attempts_json` | 删除该列；`:665-727` 改两段查询：①按现有条件 + `limit+1` 取父行（截断语义不变）②`attempts WHERE log_id IN (...) ORDER BY log_id, seq` 挂到 dict 的 `attempts` 键 |
| `repo.py:620-663` `list_logs` | 每行反序列化 `attempts_json` | `selectinload(RequestLogORM.attempts)`（每页 20 行 → 1 条 IN 查询） |
| `routers/logs.py:48` | `json_loads(log.attempts_json)` | 从 ORM attempts 构造 payload：键名与可选键保持不变（`at`/`http_status` 仅非空时出现，`skipped`/`priority_path`/headers 解析 JSON 文本）；`schemas.py:789` 契约不变 |
| `log_views.py:77-143` | `_loads_attempts`/`_row_channels`/`_row_attempts` 解析 JSON | 直接消费 scan 返回的 attempts dict 列表 |
| `log_views.py:217` `_attempt_at` | 读 dict 的 `at` | 读 `at_ms`，NULL 回退父行 `created_at`（与现在完全一致） |
| `query_lang/compiler.py:13/95`、`fields.py:55` | 注释写 `attempts_json` | 改为「attempts 表列」，路径↔列映射见决策 6 |

**`MAX_SCAN_ROWS` 结论：保留（50,000，`log_views.py:40`）。** 状态视图仍是「展开尝试 → 按模型/状态/5 分钟空闲切段」，切段逻辑留在 Python；上限语义（最新 5 万条匹配行）与截断标记 `scan_limited` 都不变。行化让这 5 万行只取需要的列、不再解析 JSON，`log_views.py:399` 的 4.3 s（fetch 1.44 s + Python 2.88 s）会明显下降，但取消上限需要先把 `status_segments` 改成 SQL 窗口函数并单独评估 SQLite/PG 差异 —— 不在本 ADR 范围，作为阶段 3 之后的候选。

### 决策 4 回填

新脚本 `api/scripts/backfill_attempts.py`（与 `scripts/seed.py` 同目录风格）：

- **游标与边界**：启动时 `SELECT max(id)` 作为 `cutoff`（ULID 单调；以 id 游标为准，不用 `created_at` 排序）；只处理 `id <= cutoff` 的行。`cutoff` 之后的行由阶段 1 的双写负责。
- **分批**：`SELECT id, attempts_json FROM request_logs WHERE id > :cursor AND id <= :cutoff ORDER BY id LIMIT 5000`，解析（`json_valid` 口径同 `repo.py:816-823`：坏 JSON 按空数组跳过并计数打日志）→ `insert_attempts` → 提交一批 → 游标落盘。
- **断点续跑**：状态存表 `attempts_backfill_state(cursor TEXT, updated_at INTEGER)`（比文件可靠，多进程/重启可恢复）；重跑幂等（唯一键冲突忽略，行数不变）。
- **与在线写入的竞态**：同一行若在阶段 1 已被双写插入，回填的 `ON CONFLICT DO NOTHING` 是 no-op；retention 删掉的旧行 SELECT 不到即跳过，不报错；回填与在线写不共享游标、不互相覆盖。
- **对账 SQL**（验收必须为 0 差异）：
  - 行数：`SUM(json_array_length(attempts_json))`（坏 JSON 用 `json_valid` 过滤）== 回填范围内 `COUNT(*) FROM attempts`；`id > cutoff` 范围同理按双写口径核对。
  - 抽样：随机 200 个 id，逐字段比较 JSON 与行（按 `seq` 保序；数值/空值/可选键都算字段），打印不一致样例；验收 0 差异。
- **成本**：实测生成 10,000 请求的 attempts 用时 1.18 s（≈8.5k 请求/s）；加索引插入预计 5–6k/s；本库 17.2k 请求约 2–3 s，100 万请求约 2–5 分钟。建议低峰跑、`batch` 可配（默认 5000），把 SQLite 单写者锁控制在单批内。

### 决策 5 三阶段灰度与验收

**阶段 1 双写（读仍走 JSON）** —— 发布内容：表 + 索引 + 写路径同时写行与 JSON + 回填脚本。开关 `ATTEMPTS_WRITE=json|rows|both`（默认 `both`，与 `config.py:76` 的 `ledger_split` 同风格的 settings 字段）。两个开关的非法值在**启动期**由 `Settings` 的 `_check_attempt_switches`（`l01_core/config.py`）拒绝：属性读取是惰性的，若不前置校验，写错变量名会在第一个写日志的请求里变成 500，而不是让进程起不来。

验收：① 抽样 parity 0 差异；② 两侧条数一致（含 `skipped` 行，误差 0，回填范围 + 双写范围分别核对）；③ 日志写 p95 增量 < 2 ms；④ 12 条 parity 用例全绿（读路径未变，作为回归基线）；⑤ 无新增异常日志。回滚：`ATTEMPTS_WRITE=json`（行表闲置，无代码回退）。

**阶段 2 切读（双写保留）** —— 开关 `ATTEMPTS_SOURCE=json|rows`（默认 `rows`）。

验收：① 12 条 parity 用例（同一夹具、两个 source，结果集与顺序逐条相等）；② API 响应 golden 快照（列表 + 详情 + 三视图的 pagination/total/attempts 数组）逐项相等；③ 日志列表 p95 不回退 > 10%，三视图 status/combo < 1 s；④ `EXPLAIN` 确认 `EXISTS` 走 `ix_attempts_*`；⑤ 回填完成率 100%、cutoff 对账通过。回滚：`ATTEMPTS_SOURCE=json`（JSON 仍完整，秒级生效）。

**阶段 3 停写 + drop 列（不可逆，拆两次发布）**：3a 停写 `attempts_json`（写 NULL；读已不依赖）并观察一个发布；3b drop 列、删 `repo.py:809-901` 的 JSON helpers、删 `json_valid` 兜底、删搜索的 JSON 分支。3a 验收：无任何路径回退 JSON（开关已不可达）、parity 仍绿；3b 前置：SQLite 文件与 PG 表级备份。回滚：3b 之前均可回滚；3b 之后靠备份。

观测项（阶段 1 起常驻）：`attempts_written_total`、`attempts_write_failed_total`、`attempts_backfill_remaining`、`attempts_parity_mismatch_total`、日志查询 p95（按查询形态）、SQLite WAL 体积、attempts 表 + 索引体积（task-103 `bench_space.py` 口径）。

**阶段 3 的启动判据（本期不执行，先钉死触发条件）** —— 三个发布之间相隔的是观察窗口而不是日历，判据不满足就停在当前阶段：

- **3a（停写 JSON）前置**，须同时满足：① `ATTEMPTS_SOURCE=attempts` 已成为默认并连续运行 ≥ 14 天；② `attempts_parity_mismatch_total` 在这 14 天内恒为 0，且每日抽样对账（随机 200 个 `id`，逐字段比较行与历史 JSON）连续 14 天 0 差异；③ 回填完成率 100%、`attempts_backfill_remaining` 归零、cutoff 对账 0 差异；④ 这 14 天内没有任何一次因读路径异常而回滚 `ATTEMPTS_SOURCE=json` 的事故；⑤ 写路径指标达标（日志写 p95 增量 < 2 ms、`attempts_write_failed_total` 增速为 0），行表 + 索引体积在预算内（260 B/尝试 ±20%，即 17.2k 请求约 12 MB）。
- **3a 内容**：`ATTEMPTS_WRITE` 默认改 `rows`、`attempts_json` 写 NULL；保留 `json` 开关作为一次性回滚通道（回滚需要重放回填脚本把 NULL 行补回 JSON —— 因此 3a 之前必须保留一份全量 blob 备份）。
- **3b（drop 列、删 JSON 分支）前置**，须在 3a 发布后满足：① 3a 已运行 ≥ 7 天并覆盖至少一个 retention 周期（确认 `purge_logs_before` 之后没有代码再读被置空的列）；② 代码里已不存在可回退到 JSON 的路径（`grep -rn "attempts_json" src/` 只剩 ORM 列定义与回填脚本，且回填脚本已标记 deprecated）；③ SQLite 库文件与 PG 表在同一窗口做过逻辑备份，并完成一次「备份 → 恢复到新库 → 跑 parity 用例」的演练；④ `EXPLAIN` 复核仍走 `ix_attempts_*`（避免 drop 列引发查询计划漂移）。
- **回滚点**：3a 之前任意时刻可回滚（JSON 完整）；3a 期间靠备份重放；3b 之后只能靠备份。判据未满足时的默认动作是「继续观察」，不是「按计划推进」。

### 决策 6 与查询语法（ADR-071 `AttemptResolver`）对接

ADR-071 决策 9 把尝试级字段交给 `AttemptResolver.matches(AttemptMatch(paths, op, value, field))`（`l03_domain/query_lang/compiler.py:264-278`）。行化后它退化成单一 `EXISTS` 构造器（建议放 `l04_repo/attempt_filters.py`，与 repo 的其余条件构造同一层）：

- **路径映射**：`$.channel_name` → `channel_name`、`$.channel_slug` → `channel_slug`、`$.channel_model_id` → `channel_model_id`、`$.upstream_model` → `upstream_model`、`$.error_code` → `error_code`、`$.message` → `message`；未知路径返回 `None`（调用方退回行级列，保留 ADR-071 的降级语义；已知 6 条永不返回 `None`）。
- **操作符**：文本 `=`/`*=`/`==` → `EXISTS` 内按 `DialectHooks` 生成 contains/wildcard/小写等值；**`!=` → `NOT EXISTS(相等)`**，不是 `EXISTS(不等)`。理由：「这条请求没经过 x」的自然语义，`EXISTS(≠)` 会在多尝试请求上因为「另一条尝试不是 x」误放行；ADR-071 决策 9 没钉死这一条，本 ADR 钉死。
- **裸词**：`EXISTS(or_(like_contains(6 列)))`，与 `repo.py:826-847` 现有 6 路径一致。
- **与行级列的关系**：`compiler._comparison` 已把 resolver 结果与字段映射列 OR 在一起（`compiler.py:236-243`），行化后一个字段命中 = 「父行列 OR EXISTS」，与 `repo.py:773/935-936` 现状同义。
- **非 SQLite 降级分支删除**（`repo.py:850-871`）；`tests/test_postgres_dialect.py` 增加编译产物断言：同一 query 在 PG dialect 下仍是 `EXISTS` + 绑定参数，不含 `json_each`/`jsonb_array_elements`。
- **落地顺序**：若 ADR-071 先接线，先用 JSON 版 resolver（现 `json_each` 实现）占位，阶段 2 切读时换成同一个接口的 `RowAttemptResolver` —— 只换类，语法、解析器、错误文案都不动。

### 决策 7 缺陷的临时修法（阶段 1/2 期间）

缺陷：`repo.py:874-884 _attempt_field_fragments` 用 blob 片段 `"<field>": <json value>` 做等值，对**数值**字段是前缀匹配。实测 `ms=1` 命中 4,632 行、真实等值 0 行（例：`avg_latency_ms=1169` 命中 `"ms": 1`）。当前被 `http_status`（`:781`）与 `error_code`（`:783`）使用：`error_code` 是带闭合引号的字符串，暂未踩到；`http_status` 取值 200/404/429/500 之间无前缀关系，暂未产生实际错命中，但状态段按 `http_status` 下钻就是这条路径。行化后该函数删除、缺陷自然消失。

阶段 1/2 期间的修法建议（与阶段 1 同一次发布，约 10 行）：

- **A（推荐）**：`_attempt_field_fragments` 只保留字符串字段；新增 `_attempt_numeric_exact(field, value)` = `EXISTS(json_each(...) WHERE json_extract(...) = :value)`，`:781` 改用它。代价实测：错误码查询用 `json_each` 等值 1816 ms vs 片段 954 ms（约 2×），但只影响尝试级下钻（列表关键词搜索不变），阶段 2 切读后整段删除。
- **B**：片段快路径 + `json_each` 二次校验 —— 两次扫描，比 A 更慢，不采用。
- **C**：什么都不做、接受潜在前缀错命中 —— 否决：这正是本次要清除的缺陷类。

单测补两条：`ms` 数值等值用例（构造 `ms=1169` 与 `ms=1` 混合夹具，断言只命中真实等值）、`http_status` 下钻用例。

### 决策 8 测试计划（parity 是硬门槛）

- **12 条 parity 用例**（task-103 实测口径；新增 `tests/test_attempts_parity.py` 或并入 `test_logs.py`）：同一夹具分别以 JSON 分支 / 行分支执行，断言命中的 `request_logs.id` 集合与顺序相等 —— 任意子串、值前缀、错误码精确、错误正文、渠道（含成员）、组合（status + attempt_status + q）、按上游模型 rollup（总数与分组）、筛选后聚合、下钻首页、失败原因分布、每渠道 P95、`json_each` 等值版。
- **API golden**：列表/详情/三视图响应快照（`attempts` 数组、`attempt_error_count`、pagination/total）逐项相等。
- **写路径**：sink 重放幂等（同一行 flush 两次行数与 seq 不变）、`LEDGER_SPLIT=off` 单事务、延迟补丁覆盖（`replace_attempts`）、retention 级联（删父行后 attempts 归零；SQLite 依赖 `db.py:97` 的 pragma，测试里显式断言）。
- **回填**：幂等重跑、坏 JSON 跳过、cutoff 边界、断点续跑。
- **Resolver**：6 路径 × 文本操作符真值表、`!=` 的 `NOT EXISTS` 语义（多尝试夹具里必须不命中「有成功尝试的失败切换请求」）、未知路径返回 `None`、PG dialect 编译产物。
- **迁移**：`migrate_sqlite_to_postgres.py` 包含 attempts 表、父子顺序正确（扩展现有 `tests/test_postgres_dialect.py` 用例）。
- **性能回归**：不进 CI；发布前按 task-103 三个脚本手工跑一次（12 条读、1000 请求写、space），与本 ADR 表格对数。

## 备选与否决

- **保留 JSON、只把片段换成 `json_each` 等值**：否决。正确性变好但读仍 1.8–4.6 s（17k 行），且 PG 仍要单独实现；成本不降反升。
- **JSON + 表达式索引 / jsonb GIN**：否决。6 个字段 × 6 种操作；PG 需改列类型为 jsonb + GIN（写更慢），SQLite 无等价物；维护成本高于建表。
- **尝试展开成固定列（attempt_1_channel … attempt_4_error）**：否决。尝试数无上界（组合成员数与重试数都不封顶），稀疏；搜索要 `OR` N 列且 N 会漂移。
- **明细写到独立日志/对象存储**：否决。搜索、聚合、retention、一致性都要跨系统，收益只是主库体积。
- **长期双写双读**：否决。写 2.5×、空间 +12 MB、两套真相必然漂移；双写只作为阶段 1 的过渡。
- **阶段 3 一次做完（停写 + drop 同发布）**：否决。不可逆且没有观察窗口；拆 3a/3b 成本几乎为零。

## 后果

- **新增**：`l04_repo/orm.py`（`AttemptORM` + 关系）、`l04_repo/attempt_filters.py`（EXISTS 条件 + `RowAttemptResolver`）、`scripts/backfill_attempts.py`、`db/postgres_schema.sql`（attempts 段）、`db/migrate_sqlite_to_postgres.py`（`TABLE_ORDER`）、`tests/test_attempts_parity.py`。
- **修改**：`repo.py:600/607/620/665/702/773-901/935-936/1125`、`usage_repo.py:52/130`、`usage_sink.py:122/342`、`log_pipeline.py:105/189`、`chat.py:564/628/634`、`probe.py:1100`、`routers/logs.py:48`、`log_views.py:77-143/217`、`l02_infra/config.py`（新开关）。
- **体积/性能**：+12.19 MB 行表与索引 / 17.2k 请求，删 JSON 列后净约 −6 MB；写 +1.24 ms/请求（`executemany` 后约 +0.6 ms）；读 6–12×，索引命中后 22–42×；三视图从 4.3 s 量级降到百毫秒级。
- **风险与缓解**：写入点分散在 4 处 → 收口到两个原语；sink WAL 条目结构变化 → 旧 WAL 条目缺 attempts 时按空列表处理（回填/双写补齐）；retention 级联依赖 FK pragma（SQLite 已开，测试断言）；drop 列不可逆 → 拆 3a/3b + 备份。
- **未决**：① `at` 缺失比例（缺省回退父行 `created_at`，与现状一致）；② attempts 是否需要按天分区（等数据量到亿级再评估，PG 侧当前无分区）；③ 操作日志是否复用同一张表（ADR-071 的可复用性目标；倾向新表 + 同一 `AttemptResolver` 接口）。
