# ADR-069 日志三视图：事实层与展示层分离

---
status: accepted
date: 2026-11-15
supersedes: 无（扩写 ssot.md §5.7 的日志条文；GLOSSARY.md §5.1 术语已同步）
related: ADR-032（日志与正文分离）、ADR-044（字面量搜索）、ADR-052（日志筛选与时间预设）
---

## 背景

使用日志原来只有一种形态：一行 = 一条 `request_logs`，无 `GROUP BY`（`api/src/l06_api/routers/logs.py:108-152` → `repo.list_logs`）。用户提出两件事：

1. **看不出总体**：想知道「这个模型今天被调了多少次」，只能翻页数；想按上游模型的连续状态看故障节奏，也没有入口。
2. **搜索与展示混淆**：搜索 `minimax` 时，一条「组合行 + MiniMax 成员」的请求搜不出来——因为 `q` 只 LIKE 6 个顶层列（`repo.py:744-752`：`request_id / upstream_request_id / requested_model / channel_name / upstream_model / response_model`），成员名只存在于 `attempts_json` 里。用户的判断标准很直接：**聚合不能吃掉可搜索性**，信息被折叠了也要能被搜到，只是展示按折叠后的样子展示。

由此引出用户确认的聚合轴：**不要「粒度 × 分组」两轴**，最基础粒度是逐次请求（成功/失败各算 1 次），聚合只有两种——**按组合**与**按状态**。

## 决策

1. **三种视图，默认不变。** `GET /api/logs` 增 `view=requests|combo|status`，缺省 `requests`（逐次请求，行为与旧版逐字节一致），前端把 `view` 写进 URL。理由：既有报表、书签、截图基线都依赖默认形态，新形态必须是显式选择。
2. **filter-then-aggregate。** 关键词、时间、渠道、状态等筛选永远作用在**事实层**（`request_logs` 行 + 其 attempts），聚合只决定「筛选之后怎么分组」。理由：否则「搜 minimax 出组合行」永远做不到——聚合发生在筛选之前，成员名就已被折掉。
3. **`combo` 视图按 `requested_model` 聚合**，不是按 `resolved_combo_id`。理由：同一请求模型既可被直连满足也可被组合满足，用户视角的「组合」是**调用名**；`resolved_combo_id` 为空的行归入「未路由」。聚合行的列沿用列表既有的列（请求数 / 尝试数 / 错误数 / token / 缓存 / 首字 / 总耗时 / 渠道集合 / 错误码集合），不发明新指标。
4. **`status` 视图是尝试级的连续段**（Status Segment）：把 `attempts` 摊平、排除 `skipped`、按**上游模型**分组，连续出现相同状态码就合成一段；状态码变化、上游模型变化，或两条尝试间隔超过 `STATUS_SEGMENT_IDLE_MS`（5 分钟）即断段。理由：「10:01 起连续 200，10:05 变 429」这种节奏是排障要看的形状；不设间隔上限会把隔夜的同码尝试连成一段，所以用 IDLE 阈值断段。
5. **attempts 补两个字段：`at` 与 `http_status`。** `at` 是尝试开始时刻（UTC 毫秒），`http_status` 是上游真实状态码（拿不到时留空）。读取侧**优先用真实码**，缺失才按错误类型推断（`rate_limit→429`、`not_found→404`、`bad_request→400`、`auth|upstream|bad_response|empty|stream_abort→502`）。理由：此前只能按 `error.kind` 反推，`auth` 一律算 502，于是「上游 401」在状态视图里被记成 502；写入口本来就知道真实码（`error.status_code` / 流对象的 `response.status_code`），没理由丢掉。新字段是**纯增量**：历史行没有它们时全部走推断，不需要迁移。
6. **搜索下沉到 attempts 内部字段**（渠道名、渠道模型 id、上游模型、错误码、错误消息），并覆盖用户显示名与密钥标识。SQLite 走 `EXISTS(SELECT 1 FROM json_each(attempts_json) je WHERE json_extract(je.value,'$.x') LIKE ...)`，逐字段 `like_contains()`（任意子串、`%`/`_` 按字面量转义、`json_valid` 保护脏行）。**非 SQLite 是能力降级**（只匹配字段值前缀、看不到错误正文），代码注释里写明 `CAPABILITY DOWNGRADE, not parity`，真解是 `attempts_json::jsonb + jsonb_array_elements/->>`；排障时不得把两个分支当等价。
7. **段 key 携带「请求行 created_at 窗口」而不是尝试时间窗口。** 段 key 形如 `status:<code>:<window>:<model>`，下钻时用 `group_key` 回到 `view=requests`；因为下钻过滤的是**行**，而尝试时间总早于本行写入时间，用尝试窗口当 key 会把行整段漏掉（有专门用例钉住）。组上展示的 `first_at/last_at` 仍是尝试时间。
8. **扫描有上限并如实告知。** `MAX_SCAN_ROWS=50000`，触顶时响应带 `scan_limited=true`，页面显示「已扫描 N 条，聚合可能不完整」。理由：聚合必须扫事实层，不能对任意时间窗做无界扫描；宁可显示上限，也不静默给一个「看起来完整」的错误数字。聚合视图的 `total` 是**组数**不是行数，分页语义随之改变。

## 备选与否决

- **后端 `GROUP BY` 直接算聚合**：否决。筛选要作用在事实层、聚合键是 JSON 里的成员字段、不同视图的列口径还不同；一条 SQL 里同时做「成员级筛选 + 动态分组 + 多列聚合」的可维护性远低于「受控扫描 + Python 分组」，而且 `scan_limited` 这样的诚实边界在 SQL 里更难表达。
- **前端拉全量自己分组**：否决。分页、`total` 与截断提示会全部失真，且大窗口下前端内存不可控。
- **状态段按请求聚合（每请求取最差码）**：否决。用户要看的是「上游在这段时间给了什么码」的连续形状，按请求取最差会把 200 与 429 交替的真实节奏抹平。
- **把 probe 行从三个视图里排除**：本期否决。列表本来就包含探测行，三视图与列表口径必须一致；要排除就整体排除，不能只在新视图里悄悄改口径。

## 后果

- 聚合视图的 `total` 是组数，`page_size` 仍是行页大小；前端分页文案改为「N 组」。
- 状态段依赖**上游模型名**做分组键：同渠道改上游模型名会断段，这是可接受的（改名的语义就是换目标）。
- `STATUS_SEGMENT_IDLE_MS = 5min` 是经验值，不做成配置项；若将来要按渠道调，先有数据再谈。
- 非 SQLite 部署的搜索严格弱于 SQLite，已在代码与本文写明；迁移到 Postgres 时必须换成 jsonb 算子，否则「搜得到 minimax」这条验收在新库上不成立。
- 新增用例 `api/tests/test_log_views.py`（18 例）钉住：真实 http_status 优先且可下钻、历史行回退推断、`attempt.at` 定时间且 key 仍能下钻、中间子串（`imax`）、错误正文（`connection reset`）、`attempts_json` 损坏行不炸、写入器不落空字段、段 key 用行窗口。
