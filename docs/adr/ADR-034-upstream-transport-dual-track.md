# 上游传输双轨：默认 LiteLLM SDK，原生 httpx 回退

---
status: accepted
partially_superseded_by: ADR-065
date: 2026-11-14
---

> **2026-11-14 更新（ADR-065）**：本条选定的「双轨」形态仍然有效；但**默认臂已从 LiteLLM 改为 httpx**。标题里的「默认 LiteLLM SDK」是当时的记录，保留不改。另外，下文引用的 `config.py:25` 与「默认 `litellm`」都是当时快照：默认值现在是 `config.py:27` 的 `httpx`，`gateway.py:778`/`:815` 的 fallback 也是 `httpx`。

## 背景

网关自研控制面（组合路由、按渠道改写、熔断、计费、观测），同时要求能精确透传上游：原始 SSE 帧、精确的头删除、非标准端点。LiteLLM Router 不提供「按渠道任意改写请求/响应」的一等入口，组合路由也不能交给它；而纯原生 httpx 缺少 SDK 归一化，且用户在 2026-11-12 明确要求用 SDK。

## 决策

保留两条传输路径，默认走 LiteLLM：

- `upstream_transport=litellm`（默认，`l01_core/config.py:25`）→ `litellm.acompletion(...)`，模型名 `openai/<upstream_model>`；
- `UPSTREAM_TRANSPORT=httpx` → 原生 httpx，原始 SSE 透传、精确头删除、非标准端点；
- 自动回退条件：配置是 litellm 但渠道地址无法还原成 OpenAI `api_base` 时走 httpx（`l05_service/llm/gateway.py:813 _use_litellm`）。

## 理由

改写可以在进入 SDK 前、离开 SDK 后完成，SDK 只负责单次 OpenAI 兼容调用——于是「自研控制面」与「用 SDK」可以同时成立。为了不再丢掉精确透传能力，回退不是删除而是保留成第二条可用路径。

## 备选与否决原因

- **纯 httpx**（ADR-030）：否决——丢掉 SDK 归一化，且与用户当时的要求冲突；其「连接池/超时/重试/排空是生产重点」的判断被 ADR-035 吸收，能力本身没丢。
- **纯 LiteLLM**：否决——精确透传（原始 SSE、头删除、非标准端点）做不到。
- **LiteLLM Router**：否决——不支持按渠道改写，组合路由需自研。

## 后果与影响

- 两套实现必须保持行为一致，契约钉在 `docs/upstream_transport.md`；差异清单是后续测试与 A/B 的验收清单。
- httpx 路径当前测试覆盖很薄（只有 `tests/test_httpx_transport.py` 一个流式回归用例；非流式、usage、4xx/5xx、非 JSON、超时、取消未覆盖）——这是已知缺口，不是「已验证等价」。
- ADR-036 在 LiteLLM 传输层按字节改写 SSE，把 typed content parts 收敛成纯文本，避免数组正文被整块丢弃；两条路径正文行为一致。

## 相关代码与文档

- 代码：`api/src/l05_service/llm/gateway.py`（`_use_litellm` :813、`complete` :1037、`open_stream` :1636）、`api/src/l01_core/config.py:25`。
- 文档：`docs/upstream_transport.md`、`ssot.md` §5.2 与 §8.2（`UPSTREAM_TRANSPORT`）、`ssot.md` §13 的 ADR-021/030/034/036。