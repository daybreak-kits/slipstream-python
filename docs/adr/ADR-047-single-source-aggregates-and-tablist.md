# ADR-047 统计口径与 tablist 键盘逻辑各收敛到单一来源

---
status: accepted
backfilled: true
date: 2026-11-14
source: ssot.md §13 压缩表（原文逐字复制）
---

## 背景

本条属于 2026-11-12/13 的早期决策批次（见 [`ADR-000.md`](./ADR-000.md)「迁移状态」）。`ssot.md` §13 压缩表对本条只留有结论，没有记录当时的背景叙述；本文件不补写没有出处的内容。

## 决策

统计口径与 tablist 键盘逻辑各收敛到单一来源

## 理由

压缩表没有把「理由」与「后果」分开记，当时的评价都写在同一栏里，原文见「后果与影响」；此处不另作解释。

## 备选与否决原因

当时未记录备选方案与否决原因（压缩表只保留结论）。

## 后果与影响

2026-11-13 新增；看板汇总、日志汇总、日聚合、时序趋势此前各自求和，估算行在时序里仍被计入 token/成本，与其它三处矛盾；现统一由 `l05_service/aggregates.py` 的 `usage_totals()` / `summary_metrics()` 计算（估算行只计请求数）；三处 tablist 的 roving 键盘逻辑统一到 `web/src/lib/use-roving-tabs.ts`，不再各写一份

## 相关代码与文档

压缩表中出现的标识与路径（回填时未逐条核实是否仍存在）：
- `l05_service/aggregates.py`
- `usage_totals()`
- `summary_metrics()`
- `web/src/lib/use-roving-tabs.ts`
- 原文出处：`ssot.md` §13 压缩表（回填前位置；2026-11-14 起 §13 改为索引）
