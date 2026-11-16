# 契约 1：用量事件 schema（数据面 → 控制面）

- 状态：frozen v1（2026-11-16，ADR-076 决策 3）
- 消费者：任何语言的数据面实现；生产者必须按本 schema 追加本地 WAL，由 shipper 批量外发。

## 事件信封

```json
{
  "event_id": "uuid（全局唯一，生成一次永不复用）",
  "schema_version": 1,
  "request_id": "req_…",
  "attempt_id": "一次上游尝试的 id（一次请求可有多条尝试事件）",
  "source": "chat | probe",
  "occurred_at": "RFC3339 UTC，网关侧完成时刻",
  "routing": {
    "channel_id": "…", "channel_slug": "…",
    "upstream_model": "…", "call_model": "…", "resolved_combo_id": "… | null"
  },
  "timing": {
    "ttft_ms": 1910, "ttfa_ms": 3245, "totlt_ms": 20300,
    "total_ms": 20300, "attempts": 2
  },
  "usage": {
    "prompt_tokens": 130781, "completion_tokens": 2570,
    "cached_tokens": 124000, "reasoning_tokens": 0,
    "usage_estimated": false,
    "estimator": "tiktoken:o200k_base | fallback_script | upstream"
  },
  "cost": { "input_cost": 0.0, "output_cost": 0.0, "currency": "USD" },
  "outcome": { "status": 200, "finish": "stop | length | aborted | error", "error_code": null }
}
```

## 语义规则

1. **至少一次投递 + 幂等入库**：事件可能重复到达，入库端按 `event_id` 幂等 upsert；`attempt_id` 区分重试尝试，重复投递不产生新行。
2. **只追加**：数据面在请求内只做本地 WAL append（已有实现：ledger WAL），不做远程写；控制台读数由控制面聚合。
3. **令牌口径**遵循 GLOSSARY §5：TTFT 是第一个可识别增量、TTFA 是首个 answer surface delta、吞吐生成窗口 < 100ms 记 null、缓存命中三级回退、中断流按 ADR-075 折算并置 `usage_estimated=true`。
4. **对账不变量**：`rows/req < 1`（入库行数 ÷ 网关计数）低于阈值告警；对账作业比较「网关 WAL 计数」与「入库计数」。
5. **演进**：只加字段不改语义；`schema_version` 递增，消费端必须容忍未知字段。
