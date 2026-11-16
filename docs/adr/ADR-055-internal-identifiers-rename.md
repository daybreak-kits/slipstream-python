# ADR-055 内部标识统一改名（slipstream / system）

---
status: accepted
backfilled: true
date: 2026-11-14
source: ssot.md §13 压缩表（原文逐字复制）
---

## 背景

本条属于 2026-11-12/13 的早期决策批次（见 [`ADR-000.md`](./ADR-000.md)「迁移状态」）。`ssot.md` §13 压缩表对本条只留有结论，没有记录当时的背景叙述；本文件不补写没有出处的内容。

## 决策

内部标识统一改名（slipstream / system）

## 理由

压缩表没有把「理由」与「后果」分开记，当时的评价都写在同一栏里，原文见「后果与影响」；此处不另作解释。

## 备选与否决原因

当时未记录备选方案与否决原因（压缩表只保留结论）。

## 后果与影响

2026-11-14 新增；推翻 ADR-054「内部标识不变」——npm 包名 `slipstream-web`、Python logger 名 `system.*`、环境变量 `SLIPSTREAM_BENCH_SKIP_LOG`、浏览器存储收敛为单键 `system.storage` JSON blob（token/theme/timezone/logsHideSensitive/navCollapsed，`lib/storage.ts` 统一读写，密钥与明文不入 blob）、主题事件 `slipstream:theme-change`、测试与视觉夹具渠道 `channel-a/b/c`、示例头 `x-client: slipstream`；仓库目录名 `` 与配置项 `APP_NAME` 保留（目录路径与外部脚本引用，改动收益低）

## 相关代码与文档

压缩表中出现的标识与路径（回填时未逐条核实是否仍存在）：
- `slipstream-web`
- `system.*`
- `SLIPSTREAM_BENCH_SKIP_LOG`
- `system.storage`
- `lib/storage.ts`
- `slipstream:theme-change`
- `channel-a/b/c`
- `x-client: slipstream`
- ``
- `APP_NAME`
- 原文出处：`ssot.md` §13 压缩表（回填前位置；2026-11-14 起 §13 改为索引）
