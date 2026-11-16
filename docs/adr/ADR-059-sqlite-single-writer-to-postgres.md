# 用 PostgreSQL 取代 SQLite 单写者

---
status: accepted, in progress
date: 2026-11-14
---

## 背景

SQLite 是单写者：多个进程共享同一个库文件时，写入会互相排队，更糟的是**失败是静默的**。8 进程共享库实测 `rows/req` 从 0.998 掉到 0.951（丢 4.9% 的 `request_logs`），而 `SQLITE_BUSY` 全程 0 次——日志里看不到任何错误，只能靠对账发现。没有错误、没有告警的丢数据，是不能带到生产的形态。

## 决策

把 PostgreSQL 作为生产形态，SQLite 保留为默认且行为不变：

- 只有 `DATABASE_URL` 以 `postgres` 开头时才启用 PG 分支（`api/src/l02_infra/db.py`）；
- 方言差异集中在两处：`l02_infra/dialect.py`（URL 规范化、驱动检查、方言相关的列自省、SQLite 专有的锁重试）与 `l04_repo/dialects.py`（ON CONFLICT 插入构造只在这里选）；
- DDL 由 ORM 元数据渲染成 `db/postgres_schema.sql`，`db/generate_pg_schema.py` 负责重新生成；`db/migrate_sqlite_to_postgres.py` 做分批、可续跑、可校验的数据迁移。

## 理由

单写者是 SQLite 本身的性质，不是配置能绕过的；多实例共享一份数据的诉求是产品形态（水平扩容到 1000 RPS）的硬要求。与其靠「每副本各自库」绕开（见 ADR-060），不如把数据库换成真正支持多写者的。

## 备选与否决原因

- **继续 SQLite + 多进程共享**：否决——丢 4.9% 行且不报错。
- **Redis 外置熔断/冷却状态**：否决（作为本条的理由）——它解决的是状态共享，解决不了单写者；Redis 方案本身仍是 ssot §12.2 里的未做项。
- **只用「每副本各自库」**：不否决——作为过渡形态采纳（ADR-060），但它带来副本间数据割裂与跨副本查询问题，不能是终点。

## 后果与影响

- **当前状态：代码路径已写好，但没有对真实 PostgreSQL 服务器跑过。** 本工作区没有 psql、postgres、docker 与网络，asyncpg 都装不上，因此 PG 分支只经过方言单测（`api/tests/test_postgres_dialect.py`）与「未设 `LLMAPI_TEST_PG_DSN` 时跳过」的集成测试（`api/tests/test_postgres_integration.py`）。
- 未验证项清单见 `docs/postgres.md` 末尾；上线前必须补一次真实服务器的迁移演练与回滚演练。
- `requirements.txt` 已加 asyncpg，但本环境未安装——部署镜像需要显式装。

## 相关代码与文档

- 代码：`api/src/l02_infra/dialect.py`、`api/src/l02_infra/db.py`、`api/src/l04_repo/dialects.py`、`db/postgres_schema.sql`、`db/generate_pg_schema.py`、`db/migrate_sqlite_to_postgres.py`。
- 文档：`docs/postgres.md`（部署 runbook 与未验证项清单）、`ssot.md` §12.2（差距表）。