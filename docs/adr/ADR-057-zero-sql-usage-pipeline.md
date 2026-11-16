# 观测写走零 SQL 管线：WAL 追加 + 后台批量落库

---
status: accepted
date: 2026-11-14
---

## 背景

每请求一次 `request_logs` 同步 commit（`l04_repo/repo.py:594`）在 SQLite 单写者下会串行化：C=32 的 A/B 实测网关 CPU 从 61.8 ms/请求 降到 48.4 ms/请求（−13.4 ms，−21.7%）后，RPS 18.03→20.65、p99 3812→2797 ms。13.4 ms 里只有约 6 ms 是「少做几条 SELECT/INSERT」的直接成本，其余来自写锁排队被放大进每请求耗时。

同一条路径还有完整性问题：classic 管线在流式请求上实测丢过 1/2364 行（0.042%）——客户端读完 `[DONE]` 即断连时，ASGI 取消生成器，尾部 `await _write_log(...)` 随之中断，异常被静默 rollback 且不重试。

## 决策

新增 `USAGE_PIPELINE` 开关（`l01_core/config.py:97`，默认 `classic`，别名 `on` → `memory`）。`memory` 模式下：

- 请求路径**不碰 SQLite**：只向 WAL 追加一行（`l05_service/usage_sink.py:122 record`）；
- 后台线程批量 upsert `request_logs`（`:171 flush_once`，批量大小与间隔由 `usage_sink_batch` / `usage_sink_interval_ms` 控制）；
- 启动时回放未落库的 WAL 段（`:69 startup`），关停时排空（`:84 drain`）；
- 幂等按 `request_id` 去重，语义是 at-least-once + 幂等，不是 exactly-once。

## 理由

观测日志的写入不在用户响应路径上，却吃掉了 22% 的 CPU 并制造了丢行窗口；把写入移到后台既省 CPU 又消除结构性的 at-most-once 缺口。WAL 是现成的、能跨重启保留、又能顺序追加的东西，所以用它当缓冲，而不是引入队列中间件。

## 备选与否决原因

- **纯异步队列（进程内）**：否决——进程崩溃即丢日志段，且网关是多副本部署，没有共享队列。
- **直接换 PostgreSQL**：否决（作为本条的理由是它更重、迁移成本大）——PostgreSQL 是更根本的解，见 ADR-059；本条不依赖数据库形态，可以先落地。
- **只缓存不落库**：否决——审计与计费要读历史行。

## 后果与影响

- 收益**只出现在多进程/高并发**：单进程实测 memory 40.2 RPS / p99 15188 ms，classic 45.2 RPS / p99 10158 ms（吞吐更低、尾延迟更差；WAL append + 后台批写的时序原因未定位）。所以它是「多实例前置条件」，不是无脑开关。
- 跨进程可见性：memory 模式下请求路径只认进程内目录快照，配置变更最多 30 s 才可见（`catalog_poll_interval_ms` 默认 30_000 ms，TTL 60_000 ms），多实例必须调小，见 ADR-060。
- 日志完整性从 at-most-once 变成 at-least-once + 幂等；坏 WAL 段或死库不影响请求。
- 默认值仍是 `classic`，切换靠环境变量 + 重启，回滚成本等于一次重启。

## 相关代码与文档

- 代码：`api/src/l05_service/usage_sink.py`、`api/src/l04_repo/usage_repo.py`（`build_log_row` 填满 `request_logs` 全部列）、`api/src/l01_core/config.py:93-101`、`api/src/l07_bootstrap/app_factory.py`（lifespan 里 `startup` / `drain`）。
- 测试：`api/tests/test_usage_sink.py`、`api/tests/test_usage_pipeline_wiring.py`。
- 文档：`docs/PERFORMANCE_REPORT.md` §5、`ssot.md` §5.7。