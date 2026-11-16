# ADR-049 前端时间、计数、百分比格式化各只有一处实现

---
status: accepted
backfilled: true
date: 2026-11-14
source: ssot.md §13 压缩表（原文逐字复制）
---

## 背景

本条属于 2026-11-12/13 的早期决策批次（见 [`ADR-000.md`](./ADR-000.md)「迁移状态」）。`ssot.md` §13 压缩表对本条只留有结论，没有记录当时的背景叙述；本文件不补写没有出处的内容。

## 决策

前端时间、计数、百分比格式化各只有一处实现

## 理由

压缩表没有把「理由」与「后果」分开记，当时的评价都写在同一栏里，原文见「后果与影响」；此处不另作解释。

## 备选与否决原因

当时未记录备选方案与否决原因（压缩表只保留结论）。

## 后果与影响

2026-11-13 新增；`lib/channels.ts` 曾自带 `formatTime`（`toLocaleString("zh-CN")`），绕过 `lib/time.tsx` 的本地/UTC 开关，渠道列表、密钥、设置、渠道详情、渠道模型面板共 9 处受影响；计数在三处各写 `toLocaleString`；错误率在页内联 `toFixed(2)`；现统一 `formatDateTime(ms, mode)` / `formatCount` / `formatPercent(rate, 2)`，并删除 `formatTokens` / `formatUsd` 兼容别名与 `metric-pills` 的格式化中转导出

## 相关代码与文档

压缩表中出现的标识与路径（回填时未逐条核实是否仍存在）：
- `lib/channels.ts`
- `formatTime`
- `toLocaleString("zh-CN")`
- `lib/time.tsx`
- `toLocaleString`
- `toFixed(2)`
- `formatDateTime(ms, mode)`
- `formatCount`
- `formatPercent(rate, 2)`
- `formatTokens`
- `formatUsd`
- `metric-pills`
- 原文出处：`ssot.md` §13 压缩表（回填前位置；2026-11-14 起 §13 改为索引）
