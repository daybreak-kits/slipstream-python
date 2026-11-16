# 契约 2：数据面管理接口

- 状态：frozen v1（2026-11-16，ADR-076 决策 3）
- 目标：任何语言的数据面节点必须实现这四个端点；控制面（与未来的 agentgateway/Envoy 前置层）只依赖这些接口做运维编排。

## GET /healthz

```json
{
  "status": "ok | draining | unavailable",
  "catalog_version": 42,
  "uptime_s": 86400,
  "inflight": 17, "streams_active": 12,
  "deps": { "snapshot": "ok", "upstream_dns": "ok" }
}
```

- `draining`：已开始排空、仍在处理存量流——负载均衡应摘除但不杀进程。
- 探活只看 `status`；`deps` 仅供参考，不参与 200/5xx 判定（避免上游抖动打死探活）。

## GET /metrics

Prometheus 文本格式，最小集合：

- 计数：`requests_total{source,status_class}`、`attempts_total{channel,status}`、`usage_events_total`
- 量规：`inflight`、`streams_active`、`snapshot_version`
- 直方图：`ttft_ms`、`request_duration_ms`、`stream_chunks_total`（每核 chunk 吞吐由它派生）
- 过载：`rejected_total{reason="inflight_cap"}`（准入控制快速失败的可观测性）

## POST /drain

```json
// 请求
{ "timeout_s": 120 }
// 响应
{ "drained": true, "remaining": 0, "elapsed_ms": 8123 }
```

语义与现有 `StreamRegistry.begin_shutdown + drain` 一致：先停止接新（新请求 503），等待在飞流自然结束或超时；超时返回 `drained=false` 与剩余数。滚动发布 = 摘流量 → drain → 终止宽限期 ≥ 最长流时长。

## GET /snapshot_version

```json
{ "version": 42, "refreshed_at": "RFC3339", "poll_interval_s": 2 }
```

控制面用它对账「规则/渠道配置已推送到哪些节点」；`CATALOG_POLL` 轮询是当前机制，契约不强制推送方式，只要求版本号单调递增、可查询。

## 鉴权与绑定

管理接口默认只绑回环地址；远程编排用反向通道或独立端口 + 令牌（`Authorization: Bearer`），不与数据面业务端口混用。
