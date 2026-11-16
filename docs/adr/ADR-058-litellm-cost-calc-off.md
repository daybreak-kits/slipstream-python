# 默认关闭 LiteLLM 的每请求成本计算

---
status: accepted
date: 2026-11-14
---

## 背景

litellm 每次请求都会算一遍 `response_cost`：profile 里 cost 家族累计 6.352 s / 703 个 chat 请求（9.0 ms/请求，稳态 6.4 ms），内部是 cost map 的 125 万次 `re.search`，加上「模型名不在 cost map 里」时的 zero-cost 诊断（1.4 s）。

而本项目的计费并不依赖它：单价从 DB 读、由 `l05_service/billing.py:26 compute_cost()` 自算，`usage.cost` 既不落盘也不返回客户端，litellm 算出来的 `response_cost` 在本仓库里只有一个消费者——`gateway.py:860 _response_headers()` 读 `_hidden_params[headers]`。

## 决策

新增开关 `LITELLM_COST_CALC`，默认 `off`（`l01_core/config.py:31`）。网关启动时把四个成本计算入口统一替换为返回 0.0：

- `l05_service/llm/gateway.py:683 _disable_litellm_cost_calc()`（幂等），在 `startup()` 的 litellm 分支里调用（`:782`）；
- 覆盖 `Logging.response_cost_calculator`、`Logging._response_cost_calculator`、`cost_calculator.response_cost_calculator`、`litellm.response_cost_calculator` 四个入口（模块函数、类属性别名与懒加载注册表都要打，否则有一条路径绕过）。

## 理由

自己已经有计费口径，SDK 再算一遍是纯浪费；而按请求付 4–20 ms 的 CPU，在 1000 RPS 量级就是 4–20 个核。

## 备选与否决原因

- **官方开关**：否决——不存在。`disable_end_user_cost_tracking` 只管 end-user 上报，`include_cost_in_streaming_usage` 只管 proxy 是否把 cost 塞进流式 usage。
- **自定义 cost map**：否决——只省掉 zero-cost 诊断（1.4 s/703），命中 map 后仍走 `completion_cost` + `cost_per_token`。
- **直接不用 litellm**：否决——那是 ADR-034 之外的大改，且用户要求保留 SDK 默认传输。

## 后果与影响

- 实测：非流式 38.1→34.0 ms/请求（RPS 30.99→35.12，+13.3%）；流式 87.7→67.6 ms/请求（RPS 12.50→16.66，+33.3%）。流式更贵，因为流尾要 `stream_chunk_builder` 组装 usage 再算 cost。
- **风险边界**：将来若启用 litellm 的预算控制或花费回调，必须把 `LITELLM_COST_CALC` 置 `on`，否则预算恒为 0。
- 回滚 = 改环境变量重启，不需要改代码。
- stub 后 litellm 流式仍会走一次 `stream_chunk_builder`（2.8 s/703），属 SDK 实现本身，不在本条范围。

## 相关代码与文档

- 代码：`api/src/l01_core/config.py:29-31`、`api/src/l05_service/llm/gateway.py:674-711`。
- 文档：`docs/PERFORMANCE_REPORT.md` §6、`ssot.md` §5.2。