# 多实例形态：独立进程 + 每副本各自本地库 + 零 SQL 管线

---
status: accepted, in progress
date: 2026-11-14
---

## 背景

两个硬约束把形态锁死了：其一，`uvicorn --workers N`（N≥2）在这台 Windows 机器上起不来（`multiprocessing/connection.py:601` → `PermissionError: [WinError 5]`），与本仓库代码无关；其二，多进程共享一份 SQLite 会静默丢日志行（同 ADR-059 的 4.9%）。

## 决策

多实例 = **多个独立 uvicorn 进程 + 前置负载均衡**，并且：

- **每副本各自本地库**，不共享 SQLite；
- 日志走零 SQL 管线（`USAGE_PIPELINE=memory`，见 ADR-057），目录读走进程内快照；
- 目录轮询间隔收紧到 2 s（`catalog_poll_interval_ms`，默认 30_000 ms）：memory 模式下 admin 写入不触发本进程 reload，新模型绑定在已运行进程里最长 30–60 s 不可见，实测共享库 + memory 臂有 57.6% 请求 404，把预热拉到 45 s 复跑后 `ok=1.000`；
- 负载均衡按 api key 亲和，避免同一把密钥的限流计数被摊到不同副本。

## 理由

`--workers` 共享同一份进程内状态（熔断计数、冷却、目录快照、usage WAL），本来就不该用于多进程部署；那就用操作系统级的进程隔离 + 外部负载均衡来替代。各自库 + memory 管线让「多副本」这件事不再依赖共享数据库。

## 备选与否决原因

- **`uvicorn --workers`**：否决——本机不可用（WinError 5），且状态共享。
- **多副本共享同一 SQLite**：否决——丢日志行且不报错。
- **把熔断/冷却状态外置到 Redis**：未做——方向正确但工程量在别处，仍是 ssot §12.2 的差距项；本条接受「副本间状态各管各的」作为过渡。

## 后果与影响

- 实测最优臂（8 进程 + 各自库 + memory）：179.0 RPS、29.4 msCPU/ok、p99 1047 ms，对比 8 进程 classic 105.3 RPS、51.3 ms、p99 1645 ms。
- 扩展性次线性：8 进程只有 2.30×（105.3 vs 45.9），墙是 CPU 与超线程（8 进程合计只吃到 5.2–5.4 核），不是 SQLite。
- **未验证**：12/16 进程边界未测；跨机部署未测；进程数上界没有依据，只能从「每进程剩 0.65 核」推断。
- 副本间状态（熔断、半开、冷却）仍是各自独立的，跨副本不共享——这是已知差距，不是设计目标。

## 相关代码与文档

- 配置：`api/src/l01_core/config.py:83-84`（`catalog_poll_interval_ms` / `catalog_ttl_ms`）、`:93-101`（usage 管线相关键）。
- 文档：`docs/PERFORMANCE_REPORT.md` §7、§8；`ssot.md` §12.2。