# ADR-079 memory 管线 body 行有界渲染：先截断后序列化，capture 决策仍归策略

---
status: accepted
date: 2026-11-18
关联：ADR-057（零 SQL 管线）、ADR-075（流生命周期）、GLOSSARY §5.1（请求/正文行）
---

## 背景

memory 管线（ADR-057）的 WAL 记录无条件携带 `BodySnapshot` 原文：`chat.py` 为每个请求创建快照（含 `request_body` 与 `upstream_body`——后者是同一份 prompt 的第二份拷贝），`usage_sink.record()` 把整包 `json.dumps` 进 WAL，随后 sink 再整行写入。**序列化发生在任何截断/脱敏/策略判断之前**，且不受 `should_capture` 约束（capture 判断在下游 `write_body_log` 才做——默认 `mode=off` 下这些 dump 是纯浪费，`mode=all` 下则全额落库）。

五臂基准实测：1.2MB 请求（≈300K tok）时 `JSONEncoder.iterencode` 占网关 CPU **32.7%**（约 77MB/s 的 dumps），200KB 时占 14.8%；非流式 CPU/req 17.9ms（对照 agentgateway 2.4ms）；RSS 随并发线性膨胀（32 QPS 长流堆到 1.45GB）。生产流量单请求 50K–300K tok，此路径是每请求固定大头。

## 决策

1. **渲染前置**：memory 路径在 WAL append **之前**经 `body_logging.build_body_record` 渲染（redact + 按预算截断），WAL 与 request_bodies 里流动的是渲染后的字符串，不再是原始对象。capture 决策**仍归策略**（`should_capture`/`mode` 在 sink 写库时判断，语义不变）。
2. **有界渲染**：`render_value` 先按字符预算深截断（`_bound_deep`：结构里每个超长字符串先切到预算），redact 与序列化只在界后负载上执行；终点仍由 `truncate_text` 做字节级精截。输出与「全文处理后再截断」相比，只有超大正文尾部内容不同（同样的截断标记、同样的字节上限）。
3. **classic 路径同规则**：`write_body_log` 用同一 `build_body_record`，两条管线在 body 行上的可见口径从此一致（此前 classic 有截断、memory 无界）。

用户裁定口径（Q2）：**(b) 保留但截断**——即 body 行保留、落库内容为截断渲染版；`mode=off` 默认下可见行为不变（不落 body 行），`mode=all` 下 2xx body 从「全量原文」变「截断渲染版」。

## 理由

- 界定成本的位置：redact（4 条正则）与 dumps 都是 O(全文) 操作，先切后算把它们的工作量钉在预算附近；chars≥bytes 的字符级预切保证只多切不越界，终点精截兜底。
- 不把「skip body」做成默认的原因：管理面日志页的正文查看是有验收闸门的功能（PERFORMANCE_REPORT §5.3 闸门 d）；砍默认可见性属于产品裁定（当时被否的选项 (a)），不属于性能修复的授权范围。

## 备选与否决原因

- **按 `should_capture` 直接跳过（选项 a）**：语义上最省（成功请求零 body 开销），但改变日志页可见口径，被用户否决。
- **只加 orjson 不动结构**：本轮已做（ADR 之外，requirements 补 orjson，−20%），但 dumps 的输入仍是全量，量级不变。
- **正文不进 WAL、sink 回读 DB 拼**：把成本从请求路径挪到 sink 但引入 DB 读放大，违背 ADR-057 的「请求路径零 DB」。

## 后果与影响

- 实测：非流式 200KB CPU/req 4.97 → 4.23ms（含 orjson）；256 QPS 完成率 54% → 86%；1.2MB 档的 json 占比待 P1 复测确认（此项与 R4/R5/R6/R8 叠加）。
- 行为差异（有意）：`mode=all` 下 request_bodies 的 body 内容为截断渲染版（尾部带截断标记），不再是全量原文；超大正文尾部不可查（与 classic 路径既有口径一致）。
- 回滚：revert `chat.py` memory 分支与 `body_logging.render_value` 的有界化即可回到无界原文；无数据迁移。

## 相关代码与文档

- `api/src/l05_service/chat.py`：`_write_log` memory 分支（渲染前置）。
- `api/src/l05_service/body_logging.py`：`_bound_text` / `_bound_deep`（新增）、`render_value`（有界化）、`cached_body_settings`（私有名转正供 chat 引用）。
- `api/tests/test_body_logging.py`、`tests/test_usage_pipeline.py` 回归覆盖。
