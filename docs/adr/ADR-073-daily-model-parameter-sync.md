# ADR-073 模型默认参数每日同步（models.dev）

---
status: accepted（设计已定并已实现：步骤①–⑤于 2026-11-16 落地；D1/D3/D4 已于 2026-11-16 裁定，见「裁定表」）
date: 2026-11-15
supersedes: 无（新建能力；手动录入的语义不变，见决策 6）
related: ADR-067（模型目录的条目口径）、ADR-070（`channel_models` 行的可见性与时间语义）、ADR-072（同为「已决策、待实施」批次）
related_docs: `docs/scratch/models-dev-research.md`（t8 调研 507 行，本文全部数字的出处；`.scratch/` 不入库，冲突时以本文与代码为准）
---

## 背景

需求由用户提出：**models.dev 每天自动拉取模型默认参数**，并回答四个子问题——默认值从哪来、什么时候拉、怎么与人工值共存、组合模型怎么呈现。

今天 `channel_models` 的价格与能力参数全靠管理员手填（`input_price_per_1m` / `output_price_per_1m` / `parameters_json` 的 `max_input_tokens`、`max_output_tokens`、`reasoning_level`、`input_types`、`output_types`，见 ssot §7）。三个代价：① 新建一个渠道模型要逐条查厂商文档；② 价格手填错就直接算错钱（计价 = 列值 × token）；③ 能力位缺失时探测与路由只能靠猜。

调研（`docs/scratch/models-dev-research.md`）的结论足以支撑施工，其 §0.1「决策输入摘要」是本文的直接输入：R1–R6 规则压缩表（§0.1.1）与聚合商名单（§0.1.2）。素材事实：

- `api.json` 是「provider → models」二级字典：324 个 provider / 9878 条 / 4317 个不同 model id；**id 不全局唯一**，1266 个 id 被 ≥2 个 provider 挂载（最多一个挂 50 个）。
- 字段覆盖到位：`cost`（USD per 1M tokens，与 `input_price_per_1m` 免换算对齐）、`limit`、`modalities`、`tool_call` / `reasoning` / `attachment` / `structured_output`、`canonical_model_id`、`last_updated`、`type`。
- 跨 provider 一致率很差：`cost.input` 37.5%、`limit.context` 53.3%、`limit.output` 47.9%、`modalities.input` 62.1%；能力位很稳：`tool_call` 91.2%、`reasoning` 90.2% → 价格/长度/模态必须消歧合成，能力位可以直接用。
- `canonical_model_id` 覆盖 8480/9878（86%），但 434 个 canonical 对应多个 id、212 个 id 反向冲突 → 只能当归组线索，不能当唯一真值。
- 许可友好：仓库 MIT，README 把 `curl https://models.dev/api.json` 写成官方用法；风险不在许可，而在「数据随时间漂移 + 聚合商错价」。

用户已裁决的硬约束（本文不再讨论，只落实）：**只按 `model` 匹配、不按 provider**；**刷新是「每天首次使用触发」，不做常驻定时任务**；**人工改过的值不被自动覆盖**（细则见决策 6：以「上次自动写入值」为基线做值比对，值不同即保留）；**models.dev 的价格只当默认值，不作计费结算依据**（实际计费以渠道配置为准）。

## 决策

1. **数据源固定为 `https://models.dev/api.json`；本地快照优先，请求路径不依赖网络。**
2. **刷新是惰性的：本地时区 00:00 之后第一次需要模型参数时触发一次，当天不再抓。**
3. **匹配只认 `model`，不认 provider；「未命中」是正常结果，返回候选而不是猜。**
4. **簇内按 T1/T2/T3 三档可信度选代表，分档由「id 名单 + 信号」共同判定，不能只看 lab 数。**
5. **字段级合并，绝不整条覆盖：上限取档内最大值，模态与能力位取并集，价格取中位数——上限与能力是「厂商事实」，价格是「供应商报价」。**
6. **人工值保护用「值比对」而不是来源标记：当前值与上次自动写入值相同才随刷新更新，不同则保留；不引入 `source` 字段。**
7. **新建渠道模型按 `upstream_model` 预填全部可同步字段（含能力位），逐字段展示来源提示（只展示、不落库）；预填失败不阻塞创建。**
8. **组合模型不合成单值：能力/模态取并集、上限取成员最大值、价格按成员列出或给区间。**
9. **失败一律降级到上一份快照，并在 UI 标注数据日期与「已过期 N 天」；同步失败不影响路由、计费与日志。**
10. **明确不做**：常驻定时器 / Celery；provider 维度匹配；自动覆盖人工值；用 models.dev 价格做计费结算。

### 决策 1 数据源与快照

- 只取 `api.json`。`/models.json` 是 OpenRouter 形状（`pricing` 为 per-token 字符串），两套混用会差 1e6 倍，禁混。
- 本地快照（建议 `data/models-dev/`）：`api.json` 原文 + `meta.json`（`fetched_at`、`http_date`、`etag`、`last_modified`、`bytes`、provider / entry 计数、`error_summary`）。快照不入版本库。
- 请求路径（`/v1/chat/completions`、目录读取、路由）只读本地派生数据，**不发网络请求**；网络只出现在刷新动作里。
- 抓取失败（超时、非 200、JSON 结构不符预期）→ 保留上一份快照，本次记为失败，当天不再重试。

### 决策 2 惰性刷新（不是定时任务）

- 触发条件：`today > app_settings` 里的 `last_attempt`（本地时区；单 key 同时存 `last_attempt` / `last_pull` / 模糊阈值，D4 已裁定）**且**本次操作确实「需要模型参数」。满足则先刷新、再返回数据。**触发判定用 `last_attempt`——任何一次尝试结束都写（成功、304、失败一律写），因此失败当天不再重试（与决策 1 对齐）；`last_pull` 只在成功 / 304 时更新，代表快照里数据的实际日期。**
- 「需要模型参数」的挂点：**模型目录列表读取**（D3 已裁定）——列表读取时若当天还没拉过，先刷新再返回；刷新完成后调 `catalog.mark_dirty()` 让目录缓存失效。**表单打开不触发刷新**，直接吃已拉结果（当天没拉过就显示「暂无默认值」，不阻塞保存）。**客户端推理路径永不触发**——`/v1/*` 不刷新、不阻塞、不因刷新失败而报错。
- 启动不预热：进程启动不抓取，避免冷启动依赖外网。
- 手动刷新：管理员「立即刷新」按钮走同一刷新函数，绕过日期判断，仍在进程锁内。
- 并发：进程内锁（单实例前提）；锁被占用时其他调用直接读旧快照，不排队等待。
- 条件请求：优先带 `If-None-Match` / `If-Modified-Since`。**本机沙箱未能实测响应头**（调研的环境限制：curl 走 schannel 报 `SEC_E_NO_CREDENTIALS`、node 直连 Cloudflare 边缘超时，只有 harness 的 `web_fetch` 能出网且每次截断在 100,000 字节），因此条件请求是否生效要在部署环境实测；不生效就整份下载（6.3 MiB，一天一次可接受）。304 → 只更新 `last_pull`（`meta.json` 的 `fetched_at` 同时刷新）。
- 上游当天会多次推送数据；一天一次足够，不做增量更新（整份覆盖写快照）。

### 决策 3 身份与匹配（R1、R4）

匹配对象只有 `upstream_model`（用户裁定：同一 `model` 可挂在多个渠道，provider 不构成身份）。7 级管线，命中级别记入 `matched_by`：

| 级 | 规则 | `matched_by` |
| --- | --- | --- |
| 1 | 与 `upstream_model` 精确相等 | `exact` |
| 2 | 归一化后相等（小写 + 分隔符统一）：`_` `.` `:` → `-` | `normalized` |
| 3 | 剥已知前缀（`lab/model`、`@cf/…` 等）再比 | `prefix_stripped` |
| 4 | 带日期/版本后缀先精确；失败才降级裸名 | `version_loose` |
| 5 | 别名表（人工维护的配置文件，不写死代码） | `alias` |
| 6 | 模糊：≥0.92 自动、0.75–0.92 入人工复核队列（阈值从配置读，默认 0.92 / 0.75，D4 已裁定） | `fuzzy_auto` / `fuzzy_review` |
| 7 | 未匹配：留空并返回候选列表 | `unmatched` |

- **不合并日期/版本后缀**（R4）：`deepseek-v4.1-flash` 与 `deepseek-v4.1-flash-1017` 并存；同一 id 下可挂 4 个不同 canonical。降级裸名时必须打 `version_loose=true` 供复核。
- `canonical_model_id` 只做归组线索（覆盖率 86%、212 个 id 反向冲突），不参与「唯一真值」判定。
- 第 6、7 级都不自动写默认值：`fuzzy_review` 与 `unmatched` 一律交给管理员在候选列表里选。

### 决策 4 三档可信度（R2）

- **T1 第一方实验室**：`labs ≤ 1` 且 id 在官方名单（openai / anthropic / google / xai / deepseek / moonshotai / minimax / zhipuai / alibaba / mistral / cohere / meta / ai21 / qwen）。
- **T2 官方云 / 企业平台**：amazon-bedrock、azure、azure-cognitive-services、google-vertex、cloudflare-ai-gateway、snowflake-cortex、sap-ai-core、digitalocean。
- **T3 聚合商 / 转售 / 网关 / 个人代理**：其余 `labs ≥ 4`（实测 170/324）或 `npm = @ai-sdk/openai-compatible`（284/324）。
- 同级内先取 `last_updated` 最新，再取字段完整度高者。
- **判定是「id 名单 + 信号」共同决定，不能只看 lab 数**（调研 §8.2 的警示原话）：lab 多样性能把大部分聚合商挑出来，但不足以单独下结论——digitalocean（17 个 lab）、amazon-bedrock（14）、cloudflare-ai-gateway（12）的 lab 数都很高，它们属**官方云（T2）**而不是聚合商（T3）。因此判序是：T1/T2 名单先命中，剩下的才交给 `labs ≥ 4` / npm 信号判 T3。名单与阈值全部落配置，不写死在代码里。
- 聚合商名单样本（调研 §0.1.2 / §8.2，`labs ≥ 12` 的 22 个组织）：nano-gpt 42、kilo 41、openrouter 41、aimlapi 36、vercel 28、edenai 26、opencode 23、novita-ai 22、commandcode 22、requesty 19、llmgateway 19 / llmgateway-providers 18、merge-gateway 17、venice 17、aihubmix 17、deepinfra 16、cortecs 15、opencode-go 15、kenari 14、nvidia 12；其中 digitalocean 17、amazon-bedrock 14、cloudflare-ai-gateway 12 按上一条归 **T2**。

### 决策 5 字段级合并（R3）

| models.dev 字段 | 落点 | 取值规则 |
| --- | --- | --- |
| `cost.input` / `cost.output` | `input_price_per_1m` / `output_price_per_1m` | 所选档内**中位数**；该档全缺才降一档 |
| `cost.cache_read` / `cost.cache_write` | `cache_read_price_per_1m` 等 | 缺失**留 NULL**（NULL = 按 input 原价计价），不猜 0 |
| `limit.context` / `limit.output` | `max_input_tokens` / `max_output_tokens` | 档内**最大值**（厂商发布值，见下方 rationale）；`limit.input` 存在时优先于 `limit.context`（输入上限 ≠ 上下文窗口） |
| `tool_call` / `reasoning` / `attachment` / `structured_output` | `parameters_json` 可选键 | **任一为真即真**（并集语义：「有 provider 声称支持」就算支持）；全为假才是 `false` |
| `modalities.input` / `modalities.output` | `input_types[]` / `output_types[]` | 档内**并集**（能力集合：不广告 ≠ 没有）；缺省以 `text` 兜底 |
| `type` | `model_type` | embedding / image_generation / audio_tts / audio_transcription / video / rerank；缺省 `chat` |
| `reasoning_options` | `reasoning_level` | 只存档位，不发明档名 |
| `name` / `description` / `family` / `knowledge` / `release_date` | 仅展示 | 不落计费字段 |
| `status=deprecated` | 仅记录 | 不自动封禁模型 |

- 单位已确认：`cost` 是 USD per 1M tokens，**免换算**；`0` 表示免费（不是「未公布」）；`cost` 缺失 432 条 → 留 NULL。
- 合并只影响「默认值」，不写运行语义（计费仍读 `channel_models` 的列值）。
- 来源信息（provider、`matched_by`、`canonical_model_id`）只用于展示与复核，不逐字段落库；归组线索与规则版本记在派生默认值表（见「数据形状与落点」）。

#### 为什么上限取 max、能力位与模态取并集、价格取中位数

三类字段的合并语义不同，不能用同一把尺子：

- **上限是「限制」不是「真值」，而且没有人会把它调大**：模型由厂商研发，第三方只做量化 / 调权重降本，不可能把厂商发布的 10000 变成 20000——上限只会是厂商发布值的下限约束。故取**最大值**：档内最大者最接近厂商发布值；取最小值等于用最保守的转售商替厂商定价。
- **模态与能力位是「厂商事实」，不广告 ≠ 没有**：某家聚合商没列 `image` 只说明它没转发这个能力，不代表模型不支持；任一 provider 声称支持就应算支持（并集）。
- **价格是「供应商报价」，没有真值**：转售商各自定价（打折、加价、套餐折算），不存在唯一的「模型价格」，只能取中位数抑制离群。

一句话：**上限与能力是「厂商事实」，价格是「供应商报价」，两类字段合并语义不同。**

### 决策 6 人工值保护（值比对，不引入 `source` 字段）

- **基线**：派生默认值表按逻辑模型存「上次自动写入的合并结果」与 `pulled_at`。刷新时逐字段比较：**当前值 == 基线值 ⇒ 用新拉取值更新；当前值 != 基线值 ⇒ 保留当前值**（视为人工改过）。
- **不引入 `source`（`auto` / `manual`）字段，也不引入 `params_source_json`**：来源只用于展示，不落库。
- 边界三条：
  - 用户手填的值**恰好等于**上次拉取值 ⇒ 会被覆盖（已接受：值相同即视为「与自动值等价的未改动值」）。
  - 用户把某个字段**改成空值**（0 / 空串 / 空数组）⇒ 视为人工改动，不覆盖（空值 != 基线值）。
  - **恢复自动值**：管理员点「恢复自动值」时，把该字段重置为本次拉取值并同步刷新基线，下次刷新继续自动更新。
- 现有列语义不变：`input_price_per_1m` 等仍是计费唯一真值来源；同步只写「默认值」，不改运行语义、不改路由。
- 没有对应列的字段（`tool_call` / `structured_output` 等）落 `parameters_json` 的**向后兼容可选键**：模型 代码忽略未知键；ORM 默认值（`api/src/l04_repo/orm.py` 的 `parameters_json`：`max_input_tokens=0`、`max_output_tokens=0`、`reasoning_level=""`、`input_types=["text"]`）保持不变。

### 决策 7 新建模型的预填

- 新建渠道模型时，按 `upstream_model` 跑决策 3 的管线；命中即把**全部可同步字段**填进表单草稿——含上限、模态与能力位（`tool_call` / `reasoning` / `attachment` / `structured_output`）。**预填只读已拉结果、不触发刷新**（D3：刷新挂在目录列表读取；表单打开当天没拉过就留空提示，不阻塞保存）。
- 表单里每个被预填的字段展示来源提示（provider + `matched_by`）；**提示只用于展示，不落库**（不引入 `source` 字段）。
- 未命中 / 多义 / 仅 `fuzzy_review`：静默跳过预填并给出候选列表，**不阻塞创建**。
- 与调研 §9「先做预填建议而不是自动写入」的调和：预填的是草稿默认值，管理员确认保存才落库；落库后的值成为决策 6 的基线，后续刷新按值比对决定是否更新（没有 `locked` / `manual` 标记）。
- `model_type` 预填（2026-11-16 裁定）：**仅作建议**——用户未主动修改就不随创建提交，落库保留后端自动判定（`model_type_source` 记 `upstream`，`auto` 仍可重新推断），防止预填值把来源固化成 `manual`。

### 决策 8 组合模型的参数呈现（R6）

- 组合是虚拟模型，实际计费随选中成员变化 → **不合成单一价格**。
- 能力 / 模态取成员**并集**；`max_input_tokens` / `max_output_tokens` 取成员**最大值**；价格按成员分别列出，UI 展示 `min–max` 区间并注明「按实际选中成员计费」。
- 组合的 `visible_status` 与路由不因参数同步改变（仍按 ADR-067 / ADR-070 现状）。

### 决策 9 失败降级与观测

- 刷新失败：保留旧快照；UI 展示「数据日期 + 已过期 N 天」；不阻塞操作，只在管理面提示。
- 同步与路由 / 计费 / 日志写入**完全解耦**：同步失败不得影响 `/v1/*` 可用性。
- 观测：每次刷新记一条结构化日志——触发方式（auto / manual）、耗时、条目数（provider / model）、是否 304、条件请求是否生效、错误摘要。
- 陈旧度两个维度分开查：快照 `fetched_at` 与条目 `last_updated`。

### 决策 10 明确不做

- 不做常驻定时器 / Celery / APScheduler（单实例，惰性触发已够）。
- 不做 provider 维度匹配（1266/4317 个 id 跨 provider）。
- 不自动覆盖与基线不同的人工值（值比对见决策 6）；模糊命中不自动落库。
- 不用 models.dev 价格做计费结算（计费以渠道配置为准）。
- 不做增量更新（一天一次整份）。
- 不做启动预热（拖慢冷启动，且多数天数据不变）。

## 数据形状与落点

| 层 | 存放 | 说明 |
| --- | --- | --- |
| 原始快照 | `data/models-dev/api.json` + `meta.json` | 不进版本库；失败保留上一份 |
| 派生默认值（每逻辑模型一行） | 新表 `model_defaults`：真列 `model_id`（unique）、`matched_by`、`pulled_at`；合并结果放 `fields_json`（基线即上次写入值），参与合并的 provider 与规则版本另存 `providers_json` / `rule_version` | 已裁定（2026-11-16，D1）：落新表，不复用 settings blob、不直接合并进 `channel_models`。粒度对齐 `_materialize` 实现——refresh 时按当前 `channel_models` 的 `upstream_model` 逐个匹配派生，表只含**在用模型**的基线（实测 11 行）；全快照簇不落表，预填仍实时读快照索引 |
| 渠道模型侧 | `channel_models` 现有列；无列字段落 `parameters_json` 可选键（不写来源标记） | 计费仍读列值 |
| 组合侧 | 不落库，查询期按成员聚合 | 与 ADR-067 的派生口径一致 |

### 裁定表（原「待拍板」）

| # | 事项 | 裁定 |
| --- | --- | --- |
| D1 | 派生默认值的落点 | **已裁定（2026-11-16）**：新表 `model_defaults`——`fields_json` 作合并结果的容器，真列 `model_id`（unique）/ `matched_by` / `pulled_at`，另存 `providers_json` / `rule_version` |
| D2 | `parameters_json` 扩展键范围 | **已裁定（2026-11-15）**：能力位纳入自动预填与更新，按值比对保护；来源提示只展示不落库 |
| D3 | 惰性触发挂点 | **已裁定（2026-11-16）**：挂**模型目录列表读取**（当天本地 00:00 后首次）；表单打开不触发、直接吃已拉结果；刷新完成后调 `catalog.mark_dirty()` |
| D4 | 模糊匹配阈值是否配置化 | **已裁定（2026-11-16）**：配置化——`app_settings` 单个 key 同时存 `last_attempt` / `last_pull` 与阈值，默认 0.92（精确）/ 0.75（候选） |

## 后果

**正面**

- 新建渠道模型从「查文档逐条填」变成「预填 + 核对」，价格手填错误的主要来源消失。
- 能力位（`tool_call` / `reasoning` / 模态）从「多数渠道空缺」变成有默认值，探测与路由判断更准。
- 人工值受保护：与基线不同的值不会被同步改掉，用户已确认的价格保持原样。

**代价**

- 一套匹配管线 + 分档配置 + 别名表要长期维护；上游数据漂移会带来零星复核工作。
- 每个可同步字段需要一份基线（派生默认值表）与一次值比对，`channel_models` 的读写与表单都要跟着改。

**风险与缓解**

| 风险 | 缓解 |
| --- | --- |
| 聚合商错价被当成默认值 | 三档可信度先压 T3；价格取中位数、缺失留 NULL；创建仍要人确认 |
| 部署环境无外网 / 出网受限 | 快照为主，刷新失败保留旧快照，UI 标陈旧度 |
| 上游数据当天多次变动导致值抖动 | 一天一次；`pulled_at` 记录，与基线不同的字段不受影响 |
| 匹配管线误配到别名条目 | `matched_by` 全量记录；`version_loose` / `fuzzy_*` 需人工复核 |
| 有人以为同步值就是计费依据 | 决策 10 与 UI 口径写明：计费以渠道配置为准 |

## 备选与否决

| 备选 | 否决原因 |
| --- | --- |
| 常驻定时任务（Celery / APScheduler） | 用户否决；单实例部署、无常驻调度设施；惰性触发成本更低，且没人用的日子不抓 |
| 进程启动时预热抓取 | 冷启动依赖外网；多数天数据不变 |
| 按 provider 维度匹配 | 1266/4317 个 id 跨 provider，provider 不构成身份（用户裁定） |
| 用 `/models.json` 当数据源 | per-token 字符串，与 `cost` 混用差 1e6 倍 |
| 整条记录覆盖写 | 会把聚合商错价 / 缩减模态写进默认值，且会覆盖人工值 |
| 用 `source`（`auto` / `manual`）标记每个字段 | 多一套标记列与迁移成本；值比对即可判「人工改过」，「手填值恰等于拉取值会被覆盖」的边界已被接受 |
| 模糊命中直接落库 | 0.75–0.92 的错配概率过高；改为人工复核队列 |
| 把 models.dev 价格用于计费结算 | 用户否决：计费以渠道配置为准 |

## 测试点

- 匹配管线：7 级各级命中并记对 `matched_by`；大小写 / 分隔符归一化；剥前缀；带日期后缀优先精确、降级打 `version_loose`。
- 不合并日期后缀：`deepseek-v4.1-flash` 与 `deepseek-v4.1-flash-1017` 各自命中。
- 同 id 多 canonical 不串味：`deepseek-v4-flash` 的 4 个 canonical 不互相覆盖。
- 分档：T1/T2 名单命中优先于 `labs ≥ 4`；**amazon-bedrock / digitalocean / cloudflare-ai-gateway 判 T2 而不是 T3**（防回归用例）。
- 合并规则：价格中位数；`limit` 取**最大值**；能力位任一为真即真（并集）；模态取**并集**、`text` 兜底；缺失留 NULL 不猜 0。
- 值比对保护：当前值 == 上次自动写入值 ⇒ 随刷新更新；!= ⇒ 保留；手填值恰等于拉取值会被覆盖（已接受）；改成空值视为手改不覆盖；「恢复自动值」后基线刷新、下次刷新继续自动更新。
- 刷新幂等与锁：同一天第二次调用不抓取；并发两次只有一次抓取；304 只更新 `last_pull`（不发整份下载）；抓取失败后当天不再重试（`last_attempt` 已写、`last_pull` 未变）。
- 失败降级：抓取失败保留旧快照、陈旧度提示出现、`/v1/*` 不受影响。
- 新建预填：命中预填全部可同步字段（含能力位）并展示来源提示；未命中 / 多义不阻塞创建并给候选；来源提示不落库。
- 组合：能力并集、上限取最大、价格按成员列出；不出现合成单一价格。
- 计费回归：同步前后同一请求的成本计算不变（列值仍是唯一来源）。

## 改动点（施工单，粗粒度）

| 区域 | 落点 |
| --- | --- |
| 抓取与快照 | 新增 `model_params` 模块（fetcher + `meta.json` 读写 + 条件请求），建议挂 `l05_service` |
| 匹配与合并 | 同模块：管线 + 分档配置 + 合并规则（上限 / 能力位并集，价格中位数） |
| 派生默认值存储 | 新表 `model_defaults`（D1 已裁定，2026-11-16）：DDL 进 `gitlog-schema` 与 `TABLE_ORDER`（与 ADR-072 同批纪律，避开 `repo.py` / `routing.py` 热点）；`fields_json` 存合并结果，`model_id` 建唯一索引 |
| 渠道模型侧 | `ChannelModelCreate`（`api/src/l06_api/schemas.py`）与 `ModelParametersIn` 增可空字段（能力位等扩展键）+ 基线比对；表单预填与「恢复自动值」按钮；`model_type` 预填仅建议、用户未动不提交（2026-11-16 裁定，防 `model_type_source` 固化） |
| 目录展示 | 模型目录 / 渠道详情的参数区展示来源与数据日期（陈旧度） |
| 手动刷新入口 | 管理员按钮 + 刷新结果回显（条目数 / 304 / 错误摘要） |
| 配置 | T1/T2 名单、`labs` 阈值、别名表进配置（不写死）；模糊阈值与 `last_attempt` / `last_pull` 落 `app_settings` 单 key（D4 已裁定） |

## 交付边界

- 本文已于 2026-11-16 实现（task-148 步骤①–⑤）：快照与条件请求 `api/src/l05_service/model_defaults/{snapshot_store,fetch}.py`，服务与状态 `api/src/l05_service/model_defaults/service.py`，匹配 / 合并纯函数 `api/src/l03_domain/model_defaults_{rules,match,merge}.py`，派生表 `model_defaults`（ORM `l04_repo/orm.py`、PG schema `db/postgres_schema.sql`），端点 `GET /api/model-defaults/status`、`POST /api/model-defaults/refresh`、`POST /api/channel-models/prefill`，惰性触发挂 `GET /api/catalog`。实现沿用本文全部决策口径。
- 决策 4 / 6 / 9 的剩余面已由 task-150 补齐（2026-11-16）：值比对同步挂在 refresh 物化前后（旧 `fields_json` 作基线，逐字段 `adopt_value`）；`POST /api/channel-models/{id}/adopt-defaults`（body `{"force": bool}`，force 即决策 6 边界三「恢复自动值」）；`status` 回显快照与派生表两维陈旧度（决策 9）与档位覆盖；T1/T2 名单、`labs` 阈值随阈值一起进 `app_settings` 单 key（决策 4 全量落地，不再读 `os.environ`）。能力位手动语义按 task-153 拍板 A：表单取消勾选=显式 `false`，全量提交。
- 调研文档在 `docs/scratch/`，按 ssot §15 第 6 条不入版本库；本文引用其结论，冲突时以本文与代码为准。
- 不在本文范围：数据再分发的署名位置（建议关于页 / README 注明「模型参数来源 models.dev，MIT」，见调研 §1）。
- 本 ADR 的登记点：`docs/INDEX.md` 的界面总览「相关 ADR」列与模型管理节、`docs/ssot.md` §13 台账与 §15 第 14 条（本批已补）。

## 修订记录

- **2026-11-15（同日修订）**：按用户裁定改写三条 —— ① 去掉 `source`（`auto` / `manual`）字段与 `params_source_json`，人工值保护改「值比对」（当前值 == 上次自动写入值才更新，不等则保留）；② `limit.context` / `limit.output` 由档内最小值改**取 max**，输入 / 输出 modalities 由交集改**并集**；③ 能力位（`tool_call` / `reasoning` 等）纳入自动预填与更新（并集语义：任一 provider 声称支持即为真）。合并规则的依据见决策 5 的 rationale：**上限与能力是「厂商事实」，价格是「供应商报价」**。同时删除 `provenance` / `synthesized` / `needs_review` 标记，来源信息只用于展示、不落库。
- **2026-11-16（实现落地，status 转 accepted）**：步骤①–⑤完成——① 匹配 / 合并纯函数与别名表（`l03_domain/model_defaults_*`）；② 快照落盘与条件请求（`l05_service/model_defaults/{snapshot_store,fetch}.py`，失败只改 `meta.json`、保留 ETag / Last-Modified）；③ 派生默认值表 `model_defaults`（SQLite + PG schema + 迁移 `TABLE_ORDER`）；④ 档位 / 合并 / 值比对纯函数；⑤ 服务与端点（惰性触发挂 `GET /api/catalog`，当天一次；`POST /api/model-defaults/refresh` 手动；`POST /api/channel-models/prefill` 预填不触发刷新；阈值与 `last_attempt` / `last_pull` 落 `app_settings` 单 key `model_defaults`）。`parameters_json` 的能力位四键（`tool_call` / `reasoning` / `attachment` / `structured_output`）按三态布尔落地，`None` 不写 JSON、ORM 默认值不变。
- **2026-11-16（用户拍板「没问题，就这样」，采纳推荐项）**：三项待拍板落档——① **D1** 派生默认值落新表 `model_defaults`（`fields_json` 容器装合并结果，真列 `model_id` unique / `matched_by` / `pulled_at`，另存 `providers_json` / `rule_version`）；② **D3** 惰性触发挂**模型目录列表读取**（当天本地 00:00 后首次；表单打开不触发、直接吃已拉结果；刷新后调 `catalog.mark_dirty()`）；③ **D4** 阈值配置化（`app_settings` 单 key 存 `last_pull` 与阈值，默认 0.92 精确 / 0.75 候选）。决策 2（触发条件）、决策 3（阈值来源）、决策 7（预填不触发刷新）、数据形状表、改动点与文首 status 同步。同日追加：触发判定与 `last_pull` 的语义分开——**任何尝试都写 `last_attempt`（失败当天不再重试），`last_pull` 仅在成功 / 304 时更新**，与决策 1 对齐；再补：`model_defaults` 的派生粒度对齐 `_materialize` 实现——refresh 按当前 `channel_models` 的 `upstream_model` 逐个匹配，表只含在用模型基线（实测 11 行），全快照簇不落表、预填实时读快照索引。
- **2026-11-16（task-152 docs 如实化）**：`model_type` 预填改「仅建议、用户未动不提交」，落库保留后端自动判定，防 `model_type_source` 固化为 `manual`（决策 7 与改动点同步）。值比对相关表述（决策 6 状态、交付边界、adopt-defaults 端点补记）见同日后条（task-150 / task-153 终稿）。
- **2026-11-16（task-150 值比对落地 + task-153 拍板，终稿）**：① 决策 6 全量实现——refresh 在物化前后捕获旧 `fields_json` 作基线，对 enabled 渠道模型逐字段 `adopt_value`（当前==基线才采用新值；人工改动保留；派生键首次出现无基线不动）；`POST /api/channel-models/{id}/adopt-defaults` 提供 force 恢复自动值。② 决策 4 收尾——T1/T2 名单与 `labs_min_providers` 阈值进 `app_settings`（`MODELS_DEV_ALIAS_FILE` 同步进 Settings，别名表改配置路径，不再读环境变量）。③ 决策 9 补齐——`status` 增派生表维（条目 `last_updated` 最大值）与快照维并列。④ task-153 拍板 A——能力位两态：表单取消勾选=显式 `false`，全量提交，所见即所得；`MODELS_DEV_ENABLED/URL/DIR/TIMEOUT_S/ALIAS_FILE` 五键补进 `api/.env.example`。测试 `api/tests/test_model_defaults_sync.py`（8 例）。
