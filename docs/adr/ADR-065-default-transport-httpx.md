# ADR-065 默认上游传输改为 httpx（LiteLLM 降为全局开关）

---
status: accepted
date: 2026-11-14
---

## 背景

ADR-034 定下「双轨传输」，同时把默认臂放在 LiteLLM SDK：SDK 帮我们归一化未知字段、把 typed parts 数组的 delta 收敛成纯文本、抹平部分渠道差异。代价是每请求 CPU 明显更高——LiteLLM 在流式链路上要逐 chunk 重建 pydantic 对象，而 httpx 臂只搬运字节。

2026-11-14 的 t6 A/B 把这个代价量化了（两趟独立测量，同工况）：非流式 C=16 每请求 CPU **0.736**（两趟 0.734 / 0.736）；短流 C=16 吞吐 **2.71×**；长流 C=4 / C=16 的每请求 CPU 为 **0.571 / 0.478**（httpx 相对 LiteLLM）。方向在两趟里一致，量级稳定，不是噪声。

同时确认了一件事：httpx 臂并不是「功能更弱的备胎」。改写、组合路由、熔断、计费全在我们自己的统一 OpenAI 请求体上完成，httpx 只负责收发字节；SDK 提供的归一化，恰恰在需要严格透传时会篡改原始帧与头部（见 ADR-034「已知限制」）。也就是说，默认臂恰好压在「更贵、且对透传更不利」的那一侧。

## 决策

默认 `UPSTREAM_TRANSPORT=httpx`。LiteLLM 臂保留，但只能用**全局开关**显式开启（`UPSTREAM_TRANSPORT=litellm`），不再作为默认。

**注意：没有渠道级开关。** `upstream_transport` 是全局 settings 项（`config.py:27`），全仓只有两个读取点——`gateway.py:778` 的 `startup()` 与 `gateway.py:815` 的 `_use_litellm()`——`orm.py` 里也没有 transport 字段。所以「让某个渠道走 SDK」在本实现里做不到，能选的粒度只有整实例。

切流口径：可逆变更不再要求「安静窗口 ≥2 rep」。满足下面三条即可切，切完看 24 小时数据：

1. **方向稳定**：同工况两次独立测量方向一致（本次 0.734 / 0.736 同向）；
2. **回滚便宜**：回滚是一条 env（`UPSTREAM_TRANSPORT=litellm`）加重启，无 schema 变更、无数据迁移；
3. **有观测闭环**：24 小时内盯 5xx 率、P95 首字、P95 总耗时、CPU/请求四个指标，任一恶化 >20% 且持续 15 分钟即回滚。

## 理由

- 默认值应当给「绝大多数渠道都能用、且最省」的那条臂。httpx 对 OpenAI 形状的上游是全功能路径（原始 SSE 透传、精确头删除、非标准端点），LiteLLM 的价值集中在少数需要 SDK 适配的上游形态；但开关是全局的，取舍只能在实例层面做——要么整实例用 SDK 换归一化，要么整实例用 httpx 换吞吐。
- 每请求 CPU 是单实例容量的直接约束（ssot §11.2）。把默认臂换到便宜的一侧，等于不加机器抬高上限。
- 回滚成本低到一条 env，所以默认值不必当成不可逆承诺：先切，再看 24 小时数据，坏了立刻回。

## 备选与否决原因

| 备选 | 否决原因 |
|---|---|
| 保持 LiteLLM 为默认 | 每请求多约 50ms CPU、吞吐低约 29%（§11.3）；默认臂应该是最不会出错的一条，而不是最贵的一条 |
| 按流量比例双跑灰度 | 两条臂对 SSE 与头部的处理不同，混跑会让同一渠道的两次请求行为不一致，排障成本高于收益；而且没有渠道级覆盖，能在同一实例里「双跑」的粒度根本不存在——真要两侧并存只能起两台实例 |
| 只对短流默认 httpx | 两套默认难解释也难维护；长流（0.571 / 0.478）方向同样一致，没有分叉的理由 |
| 等非流式 usage 兜底补完再切（t4） | 缺 usage 只影响计量口径、不影响可用性，且已有估算兜底；可逆变更不值得为此阻塞 |
| 保持双轨但不改默认，仅文档标注「httpx 更快」 | 等于把选择责任推给每个部署者，实际不会有人改；默认值本身就是决策 |

## 后果与影响

- **上线前必须确认 `.env` 里没有遗留 `UPSTREAM_TRANSPORT=litellm`**：显式值优先于默认值，遗留一行就会让整台机器继续走旧臂——它既是唯一的回退手段，也是唯一的误切来源。
- 非流式路径在 httpx 臂可能拿不到上游 usage，走估算兜底（t4 正在补）；计量报表短期可能有估算误差。
- 冷启动预热的 LiteLLM 首次流式初始化成本（约 0.9s）不再落在默认路径上，httpx 臂不需要这段预热。
- ADR-034 的「双轨」结论仍然成立，本 ADR 只取代它的「默认臂 = LiteLLM」这一项，故 ADR-034 标记 `partially_superseded_by: ADR-065`。
- 残余风险：① A/B 是在本地 mock 上游上做的，**生产形态未经真实 RTT 验证**；② httpx 臂**零生产里程**，长尾渠道行为缺样本；③ 2.84× 的吞吐数字只成立于「系统饱和 + 20000 帧/流 + C=8」这一特定组合，不要外推到其它负载形状。

## 相关代码与文档

- 落地改动（2026-11-14 已合入）：`api/src/l01_core/config.py:27` 的 `upstream_transport` 默认值 `litellm` → `httpx`；`api/src/l05_service/llm/gateway.py:778` 与 `:815` 的 fallback 同步改为 `"httpx"`（777–778 是 `startup()` 的预热判定，813–815 是 `_use_litellm()`）；`api/.env.example:15` 改为 `UPSTREAM_TRANSPORT=httpx`。
- 分流逻辑：`gateway.py` 的 `_use_litellm()` 判定，以及 `complete` / `open_stream` 两条路径。**两处读取点都取全局 settings，没有渠道级 override。**
- 测量数据：`results/transport/transport_ab.json`、`results/transport/transport_ab_pass2.json`（t6 产出；数值口径以 JSON 为准，本文件只记结论）。
- [`ADR-034`](./ADR-034-upstream-transport-dual-track.md)（双轨形态，部分被本 ADR 取代）、[`ADR-036`](./ADR-036-typed-content-parts-bytes-fix.md)（content 数组收敛，只在 LiteLLM 臂需要）、[`ADR-058`](./ADR-058-litellm-cost-calc-off.md)。
- `docs/ssot.md` §5.2（传输机制现状）、§8.2（`UPSTREAM_TRANSPORT` 配置项）、§11.3（归因复测）。
