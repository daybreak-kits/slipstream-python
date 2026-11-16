# ADR-031 前端按 as-built 收敛：Tailwind + 手写 UI，不引 shadcn/Radix/TanStack Table

---
status: accepted
partially_superseded_by: ADR-064
backfilled: true
date: 2026-11-14
source: ssot.md §13 压缩表（原文逐字复制）
---

## 背景

> **2026-11-14 补注（见 ADR-066）**：本条对 shadcn/Radix 的否定已被 ADR-064 取代；但「不引 TanStack Table」今天仍成立，且是**主动排除**——依赖集里没有 `@tanstack/react-table`，表格迁移只取 shadcn 的 `table` 样式层，排序/筛选另立为未来项（`archive/ui-migration-map.md` #29）。

本条属于 2026-11-12/13 的早期决策批次（见 [`ADR-000.md`](./ADR-000.md)「迁移状态」）。`ssot.md` §13 压缩表对本条只留有结论，没有记录当时的背景叙述；本文件不补写没有出处的内容。

## 决策

前端按 as-built 收敛：Tailwind + 手写 UI，不引 shadcn/Radix/TanStack Table

## 理由

压缩表没有把「理由」与「后果」分开记，当时的评价都写在同一栏里，原文见「后果与影响」；此处不另作解释。

## 备选与否决原因

当时未记录备选方案与否决原因（压缩表只保留结论）。

## 后果与影响

有效；组件需自己补无障碍/测试

## 相关代码与文档

压缩表未记录相关代码位置或文件名。
- 原文出处：`ssot.md` §13 压缩表（回填前位置；2026-11-14 起 §13 改为索引）
