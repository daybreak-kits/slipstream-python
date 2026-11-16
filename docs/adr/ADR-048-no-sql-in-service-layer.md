# ADR-048 服务层不直接写 SQL；上游 HTTP 统一收口到 `l05_service/llm/gateway.py`

---
status: accepted
backfilled: true
date: 2026-11-14
source: ssot.md §13 压缩表（原文逐字复制）
---

## 背景

本条属于 2026-11-12/13 的早期决策批次（见 [`ADR-000.md`](./ADR-000.md)「迁移状态」）。`ssot.md` §13 压缩表对本条只留有结论，没有记录当时的背景叙述；本文件不补写没有出处的内容。

## 决策

服务层不直接写 SQL；上游 HTTP 统一收口到 `l05_service/llm/gateway.py`

## 理由

压缩表没有把「理由」与「后果」分开记，当时的评价都写在同一栏里，原文见「后果与影响」；此处不另作解释。

## 备选与否决原因

当时未记录备选方案与否决原因（压缩表只保留结论）。

## 后果与影响

2026-11-13 新增；评审发现 `routing.py` 仍写条件 UPDATE、`auth.py` 直接 select 管理员、`probe.py` 自建 `httpx.AsyncClient` 发上游请求，违反 §4.1 分层约束；现把管理员查询与惰性熔断状态迁移下沉到 `l04_repo/repo.py`（条件 UPDATE 的 WHERE 仍是竞态仲裁者，提交后返回 rowcount），探测的 `/models` 与 chat 调用改走 gateway 新增的 `upstream_probe_get` / `upstream_probe_post` seam；代价是探测路径多一层适配，必须保持既有超时与错误映射语义

## 相关代码与文档

压缩表中出现的标识与路径（回填时未逐条核实是否仍存在）：
- `l05_service/llm/gateway.py`
- `routing.py`
- `auth.py`
- `probe.py`
- `httpx.AsyncClient`
- `l04_repo/repo.py`
- `/models`
- `upstream_probe_get`
- `upstream_probe_post`
- 原文出处：`ssot.md` §13 压缩表（回填前位置；2026-11-14 起 §13 改为索引）
