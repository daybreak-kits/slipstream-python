# 驭流（Slipstream）单一事实源（SSOT）

> **状态**：当前唯一事实源。仓库中的产品、架构、设计、运维结论只保留在本文件与 `ssot-index.md`；其余历史 Markdown 已清理。
>
> **快照**：2026-11-14，工作树（最后提交 `c0f46f5` 之上；新增日志来源 IP 与请求/响应头透出、用户密钥可复制可重置、渠道列表批量操作、看板热力图固定 7 天窗口、试验页密钥改内存态、内部标识统一改名，见 ADR-050…056）。
>
> **权威顺序**：运行代码与测试 > 本文件 > 任何历史叙述。发现冲突时，先改代码或本文件，不新增旁支文档。
>
> **用途**：产品交接、后端/前端开发、测试、部署、故障定位、面试/评审。

---

## 1. 结论与边界

### 1.1 一句话

驭流（Slipstream）是一个**本地优先、OpenAI Chat Completions 兼容的 LLM 网关 + 管理控制台**：把多个上游渠道抽象成「渠道 → 渠道模型 → 组合（手动 / 自动）」，对外提供统一鉴权、路由、故障切换、改写、流式转发、用量日志与看板。

### 1.2 为什么做

上游接口、鉴权、错误码、SSE 格式互不一致，业务方不应各自适配；同一模型常散在多个渠道，需要统一入口与自动故障切换；需要统一看到谁在调、多少 token、花了多少钱、哪家上游在抖动；部分上游路径/头/体/响应不标准，需要按渠道有限改写但不能牺牲转发速度。

### 1.3 当前结论

- **控制面与路由完全自研**；单次上游调用默认走**原生 `httpx`**（ADR-065），需要 SDK 归一化时**整实例**切 `UPSTREAM_TRANSPORT=litellm`（全局开关，无渠道级覆盖）；非 OpenAI Chat 端点始终走 httpx。双轨形态见 ADR-034。
- 入站只实现 OpenAI Chat Completions；Anthropic / Gemini 只用于渠道协议探测与模型发现，不作为入站协议（ADR-011）。
- 本地单进程、SQLite，无需 Docker/Redis/PostgreSQL 即可跑通；生产化设施（共享连接池、分层超时、全局重试预算、SIGTERM 排空、DB 连接池）已落地（ADR-035）。
- 单实例容量仍是数量级瓶颈：历史实测 mock 0.6s 流下稳定约 **100–200 个在飞流、12–13 rps**（与零延迟 mock 口径不可对比，见 `PERFORMANCE_REPORT.md`）；十万并发必须依靠边缘层 + 无状态网关集群 + PostgreSQL/Redis + 背压，不能靠单进程。
- `/readyz` **从未实现**；后端只有 `/health`，且它只是存活检查，不是就绪检查。

### 1.4 目标与非目标

| 类别 | 内容 |
|---|---|
| 必须有 | 渠道多密钥、渠道模型、直连寻址、组合（手动 / 自动）、priority 路由（越大越先）、轮次重试、可用性二分（封禁 / 冷却）与排期窗口、熔断/半开、请求/响应改写、模型探测、请求日志、看板、用户密钥、系统设置、流式 SSE |
| 当前不做 | 多租户/RBAC/OIDC、充值/发票、按密钥配额、审计到人、Anthropic/Gemini 入站、语义缓存、Prompt 管理、Guardrails、多区域/多集群、K8s 清单、Alembic |
| 明确取舍 | SQLite 只适合开发/单机；多 worker 不共享熔断状态；响应体改写仅非流式；流式首字节后不切换、不重试 |

---

## 2. 运行与仓库

### 2.1 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+、FastAPI 0.115、SQLAlchemy 2.0 async、aiosqlite、httpx 0.28、PyJWT、cryptography、LiteLLM 1.108.4 |
| 前端 | Vite 7、React 19、TypeScript 5.9、React Router 7、TanStack Query 5、react-hook-form、zod、Tailwind 3、lucide-react |
| 测试 | pytest 8 + pytest-asyncio；Vitest 3 + Testing Library；Playwright 视觉回归 |
| 存储 | SQLite（WAL、busy_timeout=15s、手写增量迁移） |

### 2.2 运行拓扑

- 后端 `http://127.0.0.1:3681`，入口 `api/app.py` / `l07_bootstrap.app_factory:create_app`；前端 `http://127.0.0.1:3680`，Vite 把 `/api` 与 `/v1` 代理到 `VITE_API_TARGET`（默认 `http://127.0.0.1:3681`）。
- 默认管理员 `admin / admin`，首次启动由 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 创建；部署必须修改。
- 健康检查 `GET /health` 返回 `{"status":"ok","app":"Slipstream","env":"local"}`。

### 2.3 目录

~~~text

├─ api/   app.py、requirements.txt、pytest.ini、.env.example
│  └─ src/  l01_core(config,errors,ids,logging,timeutil) l02_infra(crypto,db,tokens)
│           l03_domain(breaker,combos,failover,naming,rewrite,schedule,urls) l04_repo(orm,repo,search)
│           l05_service(auth,chat,channels,routing,rewrite,probe,aggregates,retention,timeseries,llm/gateway.py)
│           l06_api(deps,errors,schemas,routers/*) l07_bootstrap(app_factory)
│  ├─ tests/    pytest
│  └─ scripts/  seed.py、manual_smoke.py
├─ web/   src/{components,lib,pages,test,__tests__}/、e2e/（Playwright）、public/
├─ docs/  ssot.md（本文件）、INDEX.md、adr/、GLOSSARY.md、ssot-index.md、PERFORMANCE_REPORT.md、
│         testing.md、upstream_transport.md、postgres.md
└─ .scratch/  基准原始数据与脚本，非事实源，不随产品交付
~~~

### 2.4 启动

~~~powershell
cd api; python -m venv .venv; .\.venv\Scripts\pip.exe install -r requirements.txt; Copy-Item .env.example .env
$env:PYTHONPATH = "$PWD/src"; .\.venv\Scripts\python.exe -m uvicorn l07_bootstrap.app_factory:create_app --factory --reload --port 3681
cd web; npm ci; npm run dev
~~~

### 2.5 测试命令

~~~powershell
cd api; $env:PYTHONPATH = 'src'; .\.venv\Scripts\python.exe -m pytest -q
~~~

~~~powershell
cd web; npm test -- --run
npm run build; npm run test:visual; npm run test:visual:update
~~~

> 本机若存在全局 `PYTHONPATH`，必须显式设为当前 `api/src`，否则可能导入宿主环境中的错误包。
> 全量套件最近一次实跑**通过**（基线以最近一次实跑为准，复现命令与当次结果见 `docs/testing.md`）；**不记录用例数、通过数与耗时秒数**。实跑约束：单次 Shell 调用超过 180s 会超时，宜放后台 job；与 vitest/Playwright 并行会互相拖慢并产生假失败，全量回归务必独占跑。

---

## 3. 领域模型与寻址

### 3.1 术语

| 术语 | 定义 |
|---|---|
| 渠道 Channel | 上游接入点：URL、多密钥、协议/鉴权、优先级、备注、改写规则 |
| 渠道标识 Slug | 全局唯一短标识，如 `nim`；调用名斜杠前的部分 |
| 渠道模型 Channel Model | 渠道下的一个上游模型 + 对外 `call_model` |
| 调用模型 Call Model | 客户端调用名，默认等于上游模型名，可改，可含 `/` |
| 直连名 | `渠道标识/调用模型`，按**第一个** `/` 切分 |
| 组合 Combo | 裸名逻辑模型的上位概念，`kind = combo`；靠 `origin` 分手动/自动。旧的 `kind = group` 已取消（ADR-067） |
| 手动组合 Manual Combo | `origin = manual`；落库记录，成员可为渠道模型或子组合，最多 3 层；有独立 failover 规则与轮数，可编辑、可删除 |
| 自动组合 Auto Combo | `origin = auto`；由各渠道**启用的**、同名且不含 `/` 的 `call_model` 查询期派生，不落库、只读、不可编辑 |
| 条目 kind / origin | `kind ∈ {direct, combo}`，`origin ∈ {manual, auto}` 仅 combo 有；界面文案为「直连 / 手动组合 / 自动组合」 |
| priority | 成员排序值，**越大越先调用**（ADR-067 翻转，此前为越小越先）；不同值按降序故障转移，相同值是同一层、等概率随机（同层负载均衡）。它是路由语义，**不是展示序号**：界面上 `P{n}` 的 n 必须等于 priority 原值，不得用排名序号冒充 |
| 优先级来源 | 手动组合的成员**永远**取 `combo_targets.priority`（编辑器添加成员时必赋，不存在「没有优先级」的成员）；自动组合没有成员表，成员只能取所属渠道的 `channels.priority`。**不存在**「成员无优先级时回退渠道优先级」这条通用规则 |
| 渠道亲和 Channel Affinity | 同一会话在 TTL（默认 3600s）内**钉住**上次成功服务的渠道模型；钉住可**跨优先级层**（缓存前缀绑渠道/账号，会话连续性优先于层序），被钉成员不可用（封禁/冷却/排期）时自动回落层序。失败即清记忆（带 model_id 守卫：慢失败不抹掉并发请求刚写入的新记忆），流式**只在终止标记 200 时**写回；键取 `x-session-id`，回退 `x-bili-plugin-conversation`，再缺则不做亲和。**配置变更即失效**：组合/渠道/渠道模型的增删改与优先级调整会 bump 亲和 epoch，老记忆立即作废、下一请求按新层序选（ADR-068）。多 key 渠道按会话键稳定哈希选 key（同一会话恒用同一把），无会话键时保持按 request_id 选 |
| 轮次 attempt | 顶层组合按 priority 把所有可用成员各试一次为一轮 |
| 熔断 Breaker | 连续失败后短期跳过该模型，是**冷却的自动触发源之一**；冷却结束进入半开，只放一个探测。自有字段 `cooldown_until`（与 `banned_until` 是两回事） |
| 故障 Fault | 反复熔断后升级的较长冷却窗口，到点自动恢复，可带原因。与封禁的区别是**触发者**：故障由失败自动升级，封禁由人工 |
| 封禁 Ban | 人工设置的**永久**不可用（`enabled=false` 且 `banned_until` 为空），只能人工解除，与健康无关。封禁条目**不出现**在 `/v1/models`，直连调用 404（ADR-070） |
| 冷却 Cooldown | 到期**自动恢复**的不可用，三种来源都算冷却：① 人工设定到期时刻（`banned_until` = T）② 周期性排期窗口 ③ 熔断/故障自动触发。冷却期间条目**仍出现**在 `/v1/models`，路由跳过，直连调用 503 + `Retry-After`（ADR-070） |
| 半开 Half-Open | 冷却结束后只放一个请求试探，成功即恢复、失败重新熔断；界面上单列一态，比「可用」重、比「冷却」轻 |
| 排期窗口 Schedule | 周期性复发的冷却区间：每天 / 每周指定星期 / 每月指定日期 + 起止时刻，本地墙钟 + IANA 时区，跨天归起始日（22:00–02:00 归起始日）。**只对渠道模型**有意义，组合不做定时（ADR-070） |
| `banned_until`（历史字段名） | 存储里的**人工冷却到期时刻**（epoch ms，可空）：非空 = 定时冷却，到点读取时惰性判活、不靠定时器；为空 = 永久封禁（`enabled=false` 时）。字段名沿用旧名（与熔断的 `cooldown_until` 撞名，ADR-070 §1.1 决定不改名），语义以本行为准；旧词「定时封禁 Scheduled Ban」已废 |
| 探测 / 连接测试 | 前者验证模型可答；后者验证地址与渠道凭据可达 |
| 模型类型 model_type | 渠道模型的类别（chat / embedding / rerank / image_generation / audio_tts / audio_transcription / video），决定能否接聊天请求与用哪种探测，见 §3.5 |

### 3.2 寻址规则

1. `model` 含 `/`：一律按 `slug/call_model` 直连；只按第一个 `/` 切分，剩余整体为调用模型。例如 `nim/openai/gpt-4o` → 渠道 `nim`，调用模型 `openai/gpt-4o`。切出的 `slug` 找不到时，整串会再按原生 `call_model` 命中一次（覆盖 `nvidia/nemotron-…` 这类原生带斜杠的 id）。
2. 不含 `/`：先查手动组合（`origin = manual`）；组合自身被封禁（`enabled=false`）→ 404（组合只有启用/封禁这一个开关，无冷却与定时）。
3. 无同名手动组合则查自动组合（`origin = auto`）；它由各渠道启用的、相同且不含 `/` 的 `call_model` 查询期派生，允许只有一个成员，也允许成员来自同一渠道。
4. 裸名既无组合也无分组 → 404，并尽量给出相似直连名建议。
5. 渠道标识与组合名共用命名空间，禁止重名；手动组合名不能等于任何自动组合名。
6. 渠道标识格式：`^[A-Za-z][A-Za-z0-9_-]{0,63}$`；组合名同字符集且不能含 `/`。

### 3.3 状态与探测

| 可见状态 | 启停轴 | 健康轴 | 行为 |
|---|---|---|---|
| 正常 | 启用 | 正常 | 可接流量 |
| 半开 | 启用 | 半开 | 冷却刚结束，只放一个请求试探；成功即恢复，失败重新熔断 |
| 熔断 | 启用 | 熔断中 | 冷却期内跳过，冷却后半开 |
| 故障 | 启用 | 故障 | 多次熔断升级，定时自动恢复 |
| 停用 | 停用 | 任意 | 人工关闭或排期窗口命中；按 `banned_until` 分**封禁**（为空，永久）与**冷却**（非空，到期自愈） |

列表页「状态」列只读 `visible_status`，五态展示词为 正常 / 半开 / 冷却〔熔断〕/ 冷却〔故障〕/ 封禁或冷却〔人工〕（内部聚合顺序仍是 fault > open > half_open > disabled > normal，取最差）；`health_status` 只用于排查，前端不得据此二次派生。

**用户可见的措施只有两种：封禁 / 冷却**（ADR-070；词义表见 `GLOSSARY.md` 的 §4）。上表五态是内部聚合轴与排查口径，「停用」一档对外按 `banned_until` 拆成两种措施：

- **封禁**：`banned_until` 为空，永久，只能人工「启用」解除。条目**不出现**在 `/v1/models`，直连调用 404。
- **冷却**：`banned_until` 有值（或命中排期窗口、或由熔断 / 故障自动触发），到期自动恢复。条目**仍出现**在 `/v1/models`，路由跳过它，直连调用 503 + `Retry-After`。

熔断与故障都是冷却的**自动触发源**，不是与冷却并列的第三种措施；排期窗口是冷却的第三种触发源。判据唯一：`banned_until` 有无（存储层不新增状态列、不改字段名）。

**实现状态（ADR-070 尚未落地）**：当前直连目录在定时冷却期间会从 `/v1/models` 消失（`l05_service/catalog.py` → `l06_api/routers/v1.py`），与上句相反；同一状态下自动组合保留成员但标不可用（`l05_service/groups.py`）。本节是目标口径，改动点清单见 ADR-070 §8，术语成因见 ADR-070 §1.1 与 `docs/scratch/terminology-audit.md`。

探测状态只有 `ok / timeout / error / unknown`；`unknown` 只表示“从未探测”，不会随时间自动过期，展示时必须带 `probed_at`。

### 3.4 指标口径

**基线** = 网关收到请求、进入处理函数的那一刻；下表「− 基线」都从这一刻起算（旧文写作「− 请求开始」，同一含义）。**不设 `upstream_sent_ms`**：网关自身开销与上游耗时在指标里合并、不拆分（2026-11-15 用户裁定）。各指标的词义与易混点、统计维度的 id 归并口径见 `GLOSSARY.md` §5 / §5.2。

| 指标 | 定义 |
|---|---|
| 首字 TTFT `ttft_ms` | 上游第一个有意义 delta（非空 content / reasoning / tool_calls）**到达网关** − 基线 |
| 首答 TTFA `ttfa_ms` | 第一个「正文或工具调用」delta（非空 content，或 tool_calls / legacy function_call）− 基线；reasoning 不算；纯工具调用流 ttfa == ttft，仅推理流为空 |
| 尾字 TTLT `ttlt_ms` | 上游最后一个有意义 delta 到达网关 − 基线（中断流取最后到达的增量） |
| 总耗时 `total_ms` | 基线到最后一帧**写给客户端之后**（含回写时间，客户端慢读会拉长）；流结束 / 失败 / 中断同口径收口 |
| 吞吐 | 输出 tokens ÷（total_ms − ttft_ms）；非流式没有 TTFT，吞吐留空（null） |
| 缓存比例 | 若 `prompt_tokens ≥ cached_tokens`，分母 = prompt；否则分母 = prompt + cached；比例 = cached ÷ 分母 |
| 延迟分位 | 同一批“ttft 与 total 均非空且 total>0”的成对样本；样本 < 5 返回 null |

约束：四个流式值都存在时必须满足 `0 ≤ ttft ≤ ttfa ≤ ttlt ≤ total`；仅推理流（无正文也无工具调用）`ttfa` 为空；正文或工具调用任一出现即有值；非流式三个流式字段都为 `NULL`。
- 前端计时展示统一以秒（s）为单位并保留 2 位小数（75.765s → 75.77s）；需要 `d/h/m/s` 可读性时按阈值自适应（<60s 用秒、<1h 用 `1m15s`、<1d 用 `1h02m`、否则 `2d03h`）。

### 3.5 模型类型（model_type）

每个渠道模型带一个**类型**与**类型来源**（`channel_models.model_type` / `model_type_source`）。它只回答「这个模型是干什么的」，不参与寻址：

| 类型 | 含义 | 能否接 `/chat/completions` | 自动探测（`force=false`） |
|---|---|---|---|
| `chat`（存储默认） | 对话模型 | 能 | 能，走标准 chat 探测 |
| `embedding` | 向量化 | 不能，400 | 能，`/embeddings` 探测 |
| `rerank` | 重排 | 不能，400 | 能，`/rerank` 与 `/ranking` 两种方言 |
| `image_generation` | 文生图 | 不能 | 不能，仅 `force=true` 打一次 `images/generations` |
| `audio_tts` | 语音合成 | 不能 | 不能，仅 `force=true` 打一次 `audio/speech` |
| `audio_transcription` | 语音转写 | 不能 | 不能，没有 JSON 探测形态（需上传音频文件） |
| `video` | 视频生成 | 不能 | 不能，仅 `force=true` 打一次 `videos` |

- **类型来源** `model_type_source` 只有三个存储值：`default`（从未判定，新行默认）、`upstream`（发现模型时按名字推断得出）、`manual`（人工改过）。写入接口另接受 `auto`，它是**指令不是存储值**：含义为「按上游模型名重新判定类型」。
- **名字推断刻意保守**：只有 `rerank`（含 `rerank` / `reranker` / `re-rank`）与 `embedding`（含 `embedding` / `embed-` / `-embed` / `embed_` / `bge-` / `bge_` / `gte-` / `gte_` / `e5-` / `voyage`）能从模型名推断；其余一律落 `chat` + `default`，因为猜错会把探测发到错误端点、把类型判死。
- **chat 守卫**：`/chat/completions` 只接受 `chat` 类型候选。组合里混有非 chat 成员时，这些成员直接不进入本轮候选（不算跳过、不消耗轮次）；若整轮一个 chat 候选都没有，立即返回 `400` `NOT_IMPLEMENTED`，文案「模型 X 的类型是 embedding/rerank，不支持 /chat/completions」——绝不把对话请求转发到非 chat 端点，否则会白烧一次故障计数、把渠道冷却掉。
- **入站面目前只有聊天**：`/v1` 只暴露 `POST /chat/completions` 与 `GET /models`，没有 `/v1/embeddings`、`/v1/rerank`。所以非 chat 类型当前是「元数据 + 探测分派 + 目录展示」：能存、能探、前端能看到并筛选，但还不能经网关调用；将来接出入站端点时这道类型守卫就是路由条件。
- **升级迁移**：`channel_models` 启动时补 `model_type` / `model_type_source` 两列，并把名字命中 `rerank`（含 `/ranking` 类命名）或 `embed` 的老行就地推断成 `rerank` / `embedding` + `upstream`，其余保持 `chat` + `default`。

---

## 4. 架构

### 4.1 后端分层

| 层 | 职责 | 约束 |
|---|---|---|
| `l01_core` | 配置、错误、ID、日志、时间工具 | 不依赖上层 |
| `l02_infra` | 加密、DB 引擎/迁移、JWT | 适配器边界 |
| `l03_domain` | 纯业务规则：熔断、组合、failover、命名、改写、计划、URL | 尽量无 I/O |
| `l04_repo` | ORM 与仓储 | 其他层不直接写 SQL；查询与条件更新统一收在 `l04_repo/repo.py`（含管理员查询、熔断状态迁移）；迁移 DDL 是唯一例外，只写在 `l02_infra/db.py` |
| `l05_service` | 应用服务：chat、routing、channels、probe、rewrite、aggregates、retention、gateway | 唯一允许发上游 HTTP 的是 `l05_service/llm/gateway.py`；探测等旁路请求也走它暴露的 seam（`upstream_probe_get` / `upstream_probe_post`），服务层不得自建 HTTP 客户端——唯一例外：models.dev 快照拉取器（`l05_service/model_defaults/`）按 ADR-073 自建客户端拉公开目录快照（非路由上游、无网关语义：不透传、不计费、无凭据） |
| `l06_api` | FastAPI 依赖、schema、错误处理、路由 | 薄 |
| `l07_bootstrap` | 组装 app、lifespan、信号、路由注册 | 生命周期唯一入口 |

### 4.2 前端结构

- 页面通过 `web/src/lib/api.ts` 访问后端；TanStack Query 管理服务端状态。
- UI 控件以 shadcn copy-in 源码为准（`components/ui/*`，Radix 底座，new-york v3 风格冻结），页面不得再写第二套实现；图表、业务组合控件与布局骨架仍自有实现（ADR-064 / ADR-066）。不引 antd、不引运行时组件库，也不引 TanStack Table。
- 认证用 `AuthProvider` + `ProtectedRoute` / `PublicOnlyRoute`；JWT 存本地。
- 主题与时间显示分别由 `lib/theme.ts`、`lib/time.tsx` 统一管理。

### 4.3 一次请求的生命周期

1. HTTP middleware 生成/透传 `X-Request-ID`（ADR-074 决策 3：客户端带了就沿用，响应头恒为该 id）。
2. `/v1/*` 用用户 API Key 鉴权；管理 API 用管理员 JWT。
3. 解析 `model` 为直连或组合（手动组合优先，其次自动组合）。
4. 展开组合为候选叶子，按 `priority_path` 排序，同路径随机。
5. 每个候选经 `admit_candidate` 检查启停、计划窗口、熔断/冷却/半开；跳过项写 attempts，不消耗轮次。
6. 请求改写：请求头 3 动作、请求体 9 动作，共用 6 种条件；请求头默认以客户端头为基线（见 §5.6「头透传」、ADR-074）。
7. 传输：默认 httpx（ADR-065）；需要 SDK 归一化时该渠道设 `litellm`，非 OpenAI Chat 端点自动走 httpx。每候选尝试前扣全局重试预算。
8. 流式先 `prime()` 拿到首个有意义事件；首字节后不再切换。
9. 流结束/失败/断开后 `finalize()`：独立短 session 写日志、用量、成本、attempts、正文摘要。
10. `StreamRegistry` 释放；SIGTERM 时先停止接收新流，再排空在飞流。

---

## 5. 核心机制

### 5.1 模型解析、组合与路由

- 手动组合可嵌套，最多 **3 层**；第 3 层只能落到渠道模型；保存时做环检测、深度校验、重复目标校验。组合内不设 `weight`，统一用目标 `priority`：不同值**从大到小**顺序调用（前一层全失败才进下一层），相同值同层等概率随机打乱后逐个尝试（ADR-067）。自动组合不参与嵌套，也不拥有自己的 failover 规则与轮数。
- `max_attempts` 是**完整轮数**不是候选数：一轮 = 当前可调用顶层成员各试一次，全失败算 1 次失败。顶层组合拥有 `failover_rules` 与 `max_attempts`，子组合只贡献成员与 priority，不单独执行规则/轮次。
- 每个叶子渠道模型独立记熔断；跳过候选（封禁、冷却〔人工到期 / 排期窗口 / 熔断 / 故障〕、渠道或成员封禁、半开已占用）不调用、不消耗轮次，但写 attempts。路由侧应把封禁 / 冷却 / 排期窗口拆成**不同跳过原因**（今天共用 `MODEL_DISABLED`，日志里分不出来；ADR-070 目标口径）。
- 一轮无可调用成员：全部处于排期冷却窗口 → `503 MODEL_SCHEDULED_OFFLINE` + 下次可用时间；全部封禁 → 按「模型不存在」处理（直连 404，ADR-070 目标口径）；其余 → `503 NO_AVAILABLE_CHANNEL`。
- `failover_rules_json` 规范化为 `{rules, default_action}`，按 HTTP 状态码 + 响应体正则匹配；动作 `next` 继续下一候选，`stop` 立即结束整次请求。
- 直连 `渠道标识/调用模型` 只有一个候选，不套用组合轮次；首字节后不切换、不重试，流式中断只发 error 事件，不伪造 `[DONE]`。

### 5.2 上游传输：httpx 默认，LiteLLM 全局可切（ADR-034、ADR-065）

| 场景 | 传输 | 说明 |
|---|---|---|
| `UPSTREAM_TRANSPORT=httpx`（默认，ADR-065） | 原生 httpx | 完整原始 SSE 透传、精确头删除、非标准端点；每请求 CPU 最低 |
| 显式 `UPSTREAM_TRANSPORT=litellm` 且连接地址可还原为 OpenAI `api_base` | LiteLLM SDK | `litellm.acompletion(...)`，`model="openai/<upstream_model>"`，`max_retries=0`；比 httpx 每请求多约 50ms CPU、吞吐低约 29%（§11.3），只在整实例需要 SDK 归一化时切过去 |
| 连接地址以 `#` 结尾且不是 `/chat/completions` | 原生 httpx | LiteLLM 无法表达该精确端点 |

- `UPSTREAM_TRANSPORT` 是**全局**配置（`config.py:27`）：全仓只有两个读取点——`l05_service/llm/gateway.py:778`（`startup()` 预热判定）与 `:815`（`_use_litellm()`），来源都是全局 settings；`orm.py` 没有 transport 字段，也没有任何渠道级覆盖路径。想「只让某个渠道走 SDK」做不到，只能整实例切。
- `_litellm_api_base()` 从 `build_chat_url()` 剥掉末尾 `/chat/completions` 得到 SDK 的 `api_base`，保证两条路径实际 URL 一致；改写先在我们的统一 OpenAI 请求体上完成，再拆成 `messages / model / stream / stream_options / extra_body`。
- 渠道自定义头走 `extra_headers`，凭据头由 `_litellm_auth()` 生成；OpenAI SDK 会无条件发自己的 `Authorization`，非 Bearer 渠道用占位 key + 真实凭据走 `extra_headers`。
- 响应：非流式把 `ModelResponse` 转 dict 后做体/头改写；流式逐 chunk 转 dict 透传、体改写跳过并记 `rewrite_skipped=stream`。`_HeaderCaseTransport` 恢复常见头大小写、剥离内部 usage trace header、按请求 observer 包装响应流。
- `_ContentListNormalisingStream` 在 LiteLLM 解析前按字节改写 SSE：把 `delta.content` 为 typed parts 数组的 chunk 收敛成纯文本（`Delta.content` 只收 `str`，数组会被整块丢弃并以 EMPTY_RESPONSE 结束），其余原样透传，因此两条路径的正文行为一致（ADR-036）。
- **已知限制**：SDK 会归一化部分未知字段与原始字节，且不能删默认头；需要严格字节级兼容或删除默认头时用 httpx 回退。

### 5.3 生产化可靠性设施（ADR-035）

| 设施 | 当前实现 |
|---|---|
| 上游连接池 | 进程级共享 `httpx.AsyncClient`；`max_connections=200`、`max_keepalive_connections=50`、`keepalive_expiry=30s`；LiteLLM 的 `aclient_session` 指向共享 client |
| 分层超时 | 非流式总超时 `REQUEST_TIMEOUT_S=120`；**流式无总闸**（ADR-075：生存期归客户端，httpx `total=None`）；连接 10s；流式首字节 30s（`asyncio.wait_for` 包住 priming）；读空闲 120s（逐块看门狗，由 httpx 控制）；写 10s；池等待 10s |
| 全局重试预算 | 每请求 `max_attempts=12` 次上游调用、`budget_s=120` 秒（= `REQUEST_TIMEOUT_S`；不变量 11：预算不得大于请求超时，超出时启动即失败；该上限只约束非流式，流式无总闸）；每次尝试前 `consume()`，单次尝试超时按剩余预算封顶 |
| SIGTERM 排空 | `StreamRegistry` 的 `begin_shutdown/acquire/release/drain`；production/staging 下 lifespan 注册 SIGTERM/SIGINT；顺序：停收新流 → 排空最长 60s → 取消 retention → 排空正文写队列 → 关上游 client → dispose DB |
| DB 连接池 | `pool_size=10`、`max_overflow=20`、`pool_timeout=30`、`pool_recycle=1800`、`pool_pre_ping=True`；SQLite 另设 WAL、`busy_timeout=15000`、外键 |
| 冷启动预热 | `gateway.startup()` 调 `_warm_litellm()`：MockTransport 模拟完整 SSE，把 LiteLLM 首次流式初始化约 0.9s 的成本移到启动阶段；best-effort 失败不阻断；每次 startup `flush_cache()` 防跨事件循环复用死 client |
| 流式 DB 释放 | 路由层返回 `StreamingResponse` 前 `session.close()`；流结束由独立短 session 写日志，避免整条流占住连接池 |

### 5.4 流式契约

- 响应 `text/event-stream`，头含 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。
- `finish_reason` 或 `[DONE]` 都是终态；收尾 **error 优先**：`self.error` 非空一律按错误记账（即使已见终态标记），否则按 `terminal` 判 200，再否则 `499/STREAM_STOPPED`。
- 终态标记后客户端断开属正常 teardown，**不记 499**；终态前断开且无 error 记 `499` + `STREAM_STOPPED`、attempt 标 `stopped`，后台 finalize 仍释放 registry。流中错误只发一个 SSE error 事件并记 `STREAM_ABORTED`，不发伪造 `[DONE]`。
- 熔断按真实结局记账（ADR-051）：`error → record_failure`（429/rate_limit 标限流）、`http 200 → record_success`、`499` 不记任何一侧；流式 prime 期（`log_success=False`）不记成功，只有完整跑完才清零连续失败计数。
- usage 只在上游原始 SSE 真观察到 usage 对象时才信任，否则按转发文本字符估算并置 `usage_estimated=True`；LiteLLM 可能补伪 finish，`_is_synthetic_finish()` 丢弃它，避免把「usage 结束」误判为「模型完成」。
- 连接失败/超时映射：先查 cause chain 的 `httpx.ConnectTimeout/ConnectError` → `connection`；再按异常名含 timeout / HTTP 408 → `timeout`；最后按 429/401/403/404/4xx/5xx 分类。

### 5.5 探测与连接测试（ADR-016、ADR-041）

- 连接测试验证渠道地址与凭据可达（优先读 `/models`），写回渠道级 `connection_status / connection_latency_ms / connection_message / connection_tested_at`；未保存草稿只做瞬时预检，不落库、不写 probe 日志。
- 模型探测对单个渠道模型发一次最小真实调用 `max_tokens=64`；2xx 但无有效内容算伪成功；写回 `probe_status/probe_latency_ms/probe_message/probed_at`。探测也入账日志：`request_logs` 标 `source=probe`、令牌用量 0、`usage_estimated=true`、流式计时为空（不进延迟分位样本）。
- 只有 `mode=deep` 且拿到有效正文的探测才走 `record_success`，清冷却/故障时间并清零连续失败；`mode=shallow` 的模型拉取探测与渠道连接测试只证明地址与密钥可达，**不**改健康状态；探测失败只写 `probe_*` 与使用日志，不计入熔断计数。
- 协议探测顺序 OpenAI → Anthropic → Gemini 串行，成功后落库 `protocol` 与实际 `auth_style`，后续转发直接复用。探测固定用渠道首密钥；正式请求按 `sha256(request_id)` 在启用密钥中确定性选键。
- 按 `model_type` 分派（§3.5）：`chat` 走「`/models` 协议探测 + 一次 `max_tokens=64` 真实调用」；`embedding`/`rerank` **不走 `/models`**（只能证明地址通），直接真实调用，判据分别是「返回向量且维度 > 0」「返回非空候选列表」，`/rerank` 与 `/ranking` 两种方言都试并按 URL 记住命中的方言；`image_generation`/`audio_tts`/`video`/`audio_transcription` 默认 `400 INVALID_REQUEST` 要求显式 `force=true`，force 时只尽力打一次各家端点、2xx 即算通。
- 端点候选按「已记住的方言 → 其余方言 → 鉴权风格」展开，遇 `400/401/403/404/405/422` 换下一个，最后一个才判失败；需要 `X-Codegen-Client-Name/Version` 的渠道（如 vendorq）由渠道改写规则补齐。
- 未保存草稿可调 `POST /api/channels/test-connection` 与 `POST /api/channels/discover-models` 预检（`persist=false`，不写 `request_logs`）；已保存渠道的连接测试只回写渠道状态。

### 5.6 改写规则 v2（ADR-024、ADR-029、ADR-074）

| 作用域 | 动作 |
|---|---|
| 请求头 | `set / delete / rename`（rename = copy + delete） |
| 请求体 | `create / update / delete / copy / prepend / append / regex_replace / to_upper / to_lower` |
| 响应头 | `set / delete` |
| 响应体 | 同请求体 9 动作，**仅非流式 2xx** |

- 条件 `full / regex / gt / gte / lt / lte`，支持 `invert`、`pass_missing_key`、多条件 AND/OR；路径语法是对象键与数组下标（`messages.0.content`、`messages.-1.content`、`metadata.user.name`），内置变量 `model`、`upstream_model`、`original_model`。
- 规则按 `operations` 数组顺序执行，前序影响后序；旧动作（`set/move/trim_prefix/trim_suffix/ensure_prefix/ensure_suffix/trim_space/replace`）与旧条件（`prefix/suffix/contains`）**不兼容不迁移**：保存时拒绝、运行时跳过并记原因。
- 凭据头（Authorization/api-key 等）与传输层响应头禁止被渠道规则覆盖；**出站侧的凭据与网关身份 / 协议头（`content-type` / `content-length` / `host` / `x-request-id`）同样禁止被规则改写**（`PROTECTED_HEADERS`，ADR-074 决策 4，2026-11-15 裁定）；流式体改写在日志写 `rewrite_skipped=stream` 并在 UI 常驻提示。
- **头透传（出站）**：上游请求头以**客户端请求头为基线**——自定义头、`user-agent`、追踪头原样保留。网关先剥传输层头（`connection / keep-alive / proxy-authorization / proxy-authenticate / te / trailer / transfer-encoding / upgrade`）与 `host / content-length`，再**强制丢弃一切客户端凭据**（`authorization / proxy-authorization / cookie / api-key / x-api-key`）：凭据只能来自渠道密钥，客户端无法用请求头冒充。随后网关覆盖自己拥有的头（`content-type` 固定 `application/json`、`x-request-id`），**最后**才叠加渠道 `request_headers` 规则——规则能看到并改写客户端头，但**改不回客户端凭据，也改不动 `content-type` / `content-length` / `host` / `x-request-id`**（ADR-074 决策 1–4）。
- **头透传（入站）**：上游响应头**原样转发**，同样剥传输层头、`content-length` 与 `content-encoding`；渠道 `response_headers` 规则在其后；网关必需头**最后覆盖且大小写不敏感去重**（上游小写 `cache-control` 与网关 `Cache-Control` 只留一份）：流式固定 `content-type: text/event-stream`、`cache-control: no-cache`、`x-accel-buffering: no` 与网关自己的 `x-request-id`，非流式的 `content-type / cache-control` 按上游（ADR-074 决策 5）。
- 编辑器支持可视化规则卡片、高级 JSON、四作用域 tab、拖拽排序、试运行 diff（先「试运行」生成 diff 再「运行」应用）。

### 5.7 日志、正文与保留（ADR-032、ADR-052）

- 请求元数据写 `request_logs`，正文写独立 `request_bodies` 避免主表膨胀；正文模式 `off`（默认）/`errors`/`all`，开启后递归脱敏 `Authorization / api-key / token / password / secret / Cookie` 等。
- 截断默认 16 KiB（1 KiB–1 MiB），摘要默认 64 KiB（1 KiB–4 MiB）；正文写异步队列，退出时 `drain_body_writes()` 排空；正文保留默认 7 天（1–90 天），日志元数据固定保留 30 天，每天本地午夜清理。
- 分页默认 25 条（25/50/100/200），后端 `page_size` 与 `/recent` 的 `limit` 上限 200、超出 422；支持时间范围、渠道、模型、状态、用户、密钥标识、是否流式、请求编号/上游 ID 搜索；`attempt_status=error|success` 筛失败尝试（error=至少一次失败，success=有尝试且无失败），列表项带 `attempt_error_count`（缺省 0 兼容旧响应）；渠道筛选同时命中 attempts 内渠道名与 slug（通配符按字面量转义）。
- `GET /api/logs/summary` 与列表同参（忽略分页），返回请求数/token/成本/命中率（含样本数）/活跃分钟/跨度/RPM/TPM/峰值 RPM/估算行数；RPM、TPM 的分母是**活跃分钟**（窗口内确实有请求的分钟数），同时给全窗口跨度，避免区间拉长稀释指标。
- 列表与详情只显示**首字 / 首答 / 总耗时**；时间列两行（时间 + 状态码），吞吐列两行（t/s + 流式标签）；信封带 `source`（`chat`/`probe`）显示「聊天 / 探测」徽标；首答为空的流式日志标注「无正文与工具调用（仅推理）」，非流式三个流式字段全空（非流式不记 TTFT/TTFA/TTLT）。
- 模型列发生顺序转移时显示分叉图标与「N 次尝试」，点击展开 attempt 路径；请求编号同时匹配网关 `request_id` 与上游 `id`；`requested_model` 为空时 API 返回 `null`（不是空串），前端显示「—」。
- 三种视图 `view=requests|combo|status`（默认 `requests`，写进 URL）：`combo` 按请求模型聚合、`status` 按**状态段**聚合；聚合只改展示分组，**筛选永远作用在事实层**（filter-then-aggregate），聚合视图的 `total` 是组数、`scan_limited=true` 表示扫描触顶（`MAX_SCAN_ROWS=50000`），页面必须如实显示「已扫描 N 条，聚合可能不完整」。下钻带 `group_key` 回到 `view=requests` 并沿用全部筛选（ADR-069）。
- 状态段：把 `attempts` 摊平、排除 `skipped`、按上游模型分组，连续相同状态码合成一段；状态码变化、上游模型变化或间隔超过 `STATUS_SEGMENT_IDLE_MS`（5 分钟）即断段。段 key 形如 `status:<code>:<window>:<model>`，其中下钻窗口取**请求行的 `created_at`**（尝试时间总早于行写入时间，用尝试窗口会漏行），组上展示的 `first_at/last_at` 仍是尝试时间。
- 每条尝试记录 `at`（尝试开始 UTC 毫秒）与 `http_status`（上游真实状态码，拿不到时留空）：读取侧优先真实码，缺失才按错误类型推断（`rate_limit→429`、`not_found→404`、`bad_request→400`、`auth|upstream|bad_response|empty|stream_abort→502`）。`skipped` 只有 `at`，既没有状态码也没有耗时；失败尝试**有**耗时（`ms`），显示在「优先级 N」行尾。
- 关键词搜索覆盖尝试内部字段（渠道名、渠道模型 id、上游模型、错误码、错误消息）与用户显示名、密钥标识：SQLite 用 `json_each + json_extract` 做任意子串匹配（`%`/`_` 仍按字面量转义、`json_valid` 保护脏行），非 SQLite 是**能力降级**（只匹配字段值前缀、看不到错误正文），两者不可当等价（ADR-069）；行化改造已决策（ADR-072，见 §7）。
- **搜索语法 v1（ADR-071）**：查询串支持 FOFA 风格谓词 `field OP value`——17 个字段（`model / upstream / channel / channel_model / error / msg / key / user / source` 为文本，`status / tokens / latency / ttft / ttat / cost / time` 为数值，`stream` 为布尔）、操作符 `= == != *= > >= < <=`、布尔 `&& || !` 与括号、单双引号字符串（值以哪个引号开头就必须以同一个引号闭合，引号内的语法符号一律按字面量）；裸词 = 既有 11 列 OR（搜索面不缩小），`ttat` 映射 `ttfa_ms`。**严格模式**：出现任一语法符号即按语法解析，失败返回 400 并带 1-based 列号，**不回退**成整串关键词；上限 512 字符 / 20 谓词 / 8 层嵌套。编译产物是 **SQLAlchemy 表达式**（值走绑定参数、SQLite 与 Postgres 同一段编译），不是 SQL 字符串。筛选控件是这套语法的**输出方**：用户点选条件即生成语法，不要求手写。**当前状态**：核心模块在 `api/src/l03_domain/query_lang/`（lexer / parser / fields / compiler / errors，98 用例），已接线 `/api/logs` 的 `q`——无语法=原关键词搜索，有语法=严格解析，`details.column` / `details.suggestion` 原样透出；`GET /api/logs/filters` 的模型候选改为「目录（渠道模型 + 组合名）∪ 近 30 天观测」，空日志不再返回空数组。**v1 只作用于逐次请求视图与段钻取**：聚合视图（`combo` / `status`）与 `/api/logs/summary` 收到带语法的 `q` 返回 400（`details.view=requests`），既不静默忽略也不降级成关键词。**大小写折叠统一为 `fold_key`（只折 ASCII 的 `lower()`，与 SQLite `lower()` 逐字符等价）**：聚合分组键、分流节点 id、筛选项去重共用同一定义，展示名保留首见原写法（目录有写法则优先目录），下钻与汇总标签同折叠，跨方言差异见 ADR-071 决策 14。`model=` 三路匹配（请求名 / 上游名 / 组合成员）待 `repo.py` 解冻后合并。

### 5.8 看板（ADR-039、ADR-047）

- 默认首页；范围六档 `range=today|week|24h|7d|30d|custom`（「本周」从本地周一 00:00 起算，粒度小时），粒度小时/天。「今天」与分桶按浏览器 IANA 时区：前端传 `tz`，后端把边界换回 UTC 毫秒，缺省 `Asia/Shanghai`，最近 24h 是滚动区间。
- KPI 5 张卡 / 9 个指标格：请求数+错误率、输入 token+输出 token、缓存 token+缓存命中率、RPM+TPM、成本；RPM/TPM 以活跃分钟为分母并附峰值与跨度，命中率与成本标注被排除的估算行数；P95 首字/总耗时不在 KPI 行，单列「延迟分位」卡片。
- 图表：请求/token/成本趋势、模型/上游模型/渠道 Top N（随趋势维度切换）、错误类型、小时热力图（**固定最近 7 天窗口**统计星期×小时——跟随筛选窗口会让窗口外星期整行无数据显灰）、四段延迟分位（首字/首答/尾字/总）。趋势图与分流图合并为一张**标签卡**：默认落在趋势（进页面即渲染），切到「分流图」才挂载桑基并请求 `/api/dashboard/flows`，标签写 URL `?tab=flow`；切回趋势走 React Query 的 60s 缓存，不重复请求。
- 延迟只用成对样本（ttft 与 total 均非空且 total>0），样本 < 5 返回 null；`compute_summary`、`compute_timeseries`、`refresh_daily_aggregates` 共用同一套配对逻辑；分模型 P95（`by_model[].p95_total_ms`）同一阈值，不足 5 个返回 `null`，前端显示「—」。
- 缓存命中率按**四个口径**同时给出（ADR-068）：**A 缓存命中率** `cached ÷ (prompt ≥ cached ? prompt : prompt + cached)`，按 token 加权，是面板主指标；**B 请求级命中率** `cached > 0 的请求数 ÷ 计入请求数`，与按请求计的报表可比，天然高于 A；**C 可缓存占比** 本请求 prompt 中已在同会话更早请求出现过的 token 占比，无会话头时为 `null`、前端显示「—」而不是 0；**D 节省** `Σcached_tokens` 及按该模型价格估算的金额。四者各带样本数 `cache_hit_rate_samples`；无样本显示「—」。全站百分比统一 1 位小数，错误率例外固定 2 位（区分 0.1% 与 0.01%）。
- 中断的流按上游口径入账：**输入按发出侧已知数计、缓存记 0**；仍取不到 usage 的行按估算处理，不计入缓存命中率与成本分母，`estimated_rows` 只用于提示「N 条估算未计入」；token 与成本只累加真实行（看板与 `/api/logs/summary` 同一口径）。
- 口径的**唯一实现来源**是 `l05_service/aggregates.py` 的 `usage_totals()`（计数 + token/成本求和，估算行不计）与 `summary_metrics()`；看板汇总、日志汇总、日聚合、时序趋势四处共用。
- 看板只统计 `source='chat'` 的真实流量，探测行只出现在使用日志、不计入任何 KPI 或趋势。P95 趋势合计**按请求数加权**：`Σ(p95 × requests) ÷ Σrequests`，隐藏序列权重记 0，p95 为空或 0 的样本不参与。
- 16 个图表色槽 `--chart-1..16` 按窗口内调用量降序分配，零调用不占槽；模型维度全量展开，超过 16 个按 `palette[i % 16]` 循环复用，不合并「其他」。三视角并排时颜色**按名字全局分配**：三个视角的成员合并后按整窗请求数降序统一取 16 槽，**同名跨视角同色**；图例分三节（节标题=视角名，点标题整视角隐藏且保持 `aria-pressed`，节内成员逐个可点）。`daily_aggregates` 只由 `POST /api/dashboard/rebuild` 重建，看板查询直接扫 `request_logs`。
- 分流图（`GET /api/dashboard/flows`，`metric=requests|tokens`）：固定五段 `用户 → 密钥 → 模型 → 上游模型 → 渠道`，只统计 `source='chat'`；每段保留 Top 20 真实节点，其余合并成一个「其他（N）」，边按同一映射重指后累加，保证中间各段「入边和 = 出边和 = 节点 value」；前端是自研 SVG 桑基（五列等距 + 三轮 barycenter 减交叉），支持滚轮锚点缩放 0.5×–8×、拖拽平移、双击复位、点击或 Enter/Space 隐藏节点（至少保留一个）；画布有明确边界（浅底 + 1px 边框 + 内边距），节点最小高 8px、命中热区 ≥12px，悬停/键盘聚焦弹出详情卡（名称、数值、占比、上下游前 3），窄屏放进 900px 宽的内部滚动容器而不是压扁。
- 趋势图横轴在**每根柱子下方**标时间点（小时粒度 `HH:mm`、天粒度 `MM-DD`），按最小间距抽稀避免重叠；起止范围降级为卡片右上角小字，不再只在首尾标注。

### 5.9 鉴权、密钥与设置（ADR-020、ADR-043）

- 管理端用户名 + 密码登录，JWT 默认 7 天；单管理员，密码经 `/api/auth/password` 修改。
- 用户 API Key 鉴权只认哈希 `key_hash`；明文以 Fernet 加密存 `api_keys.key_enc`，创建后可随时复制（`GET /api/keys/{id}/secret`，旧数据无 `key_enc` 时提示先重置）；行内「重置」生成新明文并立即作废旧密钥（`POST /api/keys/{id}/reset`）；同一用户下 `label` 唯一；日志记不可变 key id 与 label。
- 渠道密钥：`channel_keys.key_enc` 加密存储；`channels.api_key_enc` 保留为首密钥镜像兼容旧数据；删除首密钥需回填下一个启用密钥。
- **主密钥 = `channels.api_key_enc` 指向的那一行**，不是「排序第一条」：Fernet 加密非确定性，同一明文两次加密密文不同，识别主密钥必须解密比对明文。列表顺序、`api_key_masked`、批量添加查重、`PATCH api_key` 查重（与其它密钥重复一律 409，不静默覆盖）、删除后回落都以它为准，绝不依赖 `created_at` / ULID 排序（同毫秒创建的多密钥排序随机）。
- 渠道密钥选择：正式请求按 `sha256(request_id)` 确定性选键、重试保持同一 key；探测固定首密钥。系统设置当前覆盖正文日志策略 + 网关对外 Base URL，都存 `app_settings`。
- 安全底线：生产必须改 `JWT_SECRET`、`ADMIN_PASSWORD`，显式设置 `ENCRYPTION_KEY`；`.env` 与 `note.txt` 不入库。

---

## 6. API 面

除 `/health`、`/api/auth/login` 与 `/v1/*` 外，管理 API 均要求管理员 JWT。错误形状两套：`/v1/*` 为 OpenAI 风格 `{error: {message, type, code, param}}`；管理 API 为 `{code, message, details?, request_id}`，参数校验失败同样用后者。

| 前缀 | 端点 |
|---|---|
| `/api/auth` | `POST /login`、`GET /me`、`POST /password` |
| `/api/catalog` | `GET ""` |
| `/api/channels` | `GET ""`、`POST ""`、`POST /test-connection`（草稿）、`POST /discover-models`（草稿）、`GET/PATCH/DELETE /{id}`、`GET/POST /{id}/keys`、`PATCH/DELETE /{id}/keys/{key_id}`、`GET/POST /{id}/models`、`POST /{id}/models/bulk`、`POST /{id}/probe-all`、`POST /{id}/rewrite-preview`、`POST /{id}/test-connection` |
| `/api/channel-models` | `PATCH/DELETE /{id}`（`PATCH` 可改 `model_type`，传 `auto` 表示按上游模型名重新推断）、`POST /{id}/reset`、`POST /{id}/probe`（非 chat/embedding/rerank 类型需 `force=true`）、`POST /prefill`（按 `upstream_model` 预填默认值，只读已拉快照、不触发刷新）、`POST /{id}/adopt-defaults`（「应用默认参数」：可同步字段重置为本次拉取值并刷新基线，`force=true` 全量重置；ADR-073） |
| `/api/model-defaults` | `GET /status`（当天是否已尝试、快照日期与陈旧度、派生行数）、`POST /refresh`（管理员「立即刷新」，绕过当天判断） |
| `/api/combos` | `GET ""`、`POST ""`、`GET/PATCH/DELETE /{id}` |
| `/api/groups` | `GET ""`（自动组合只读视图） |
| `/api/logs` | `GET ""`、`GET /recent`、`GET /filters`、`GET /summary`、`GET /{request_id}` |
| `/api/dashboard` | `GET /summary`、`GET /timeseries`、`GET /flows`（分流图）、`POST /rebuild` |
| `/api/keys` | `GET ""`、`POST ""`、`PATCH/DELETE /{id}`、`POST /{id}/reset`、`GET /{id}/secret` |
| `/api/settings` | `GET ""`、`PATCH ""` |
| `/health` | `GET`（没有 `/readyz`） |
| `/v1` | `POST /chat/completions`、`GET /models` |

- `/v1/chat/completions` 请求体遵循 OpenAI Chat，`stream=true` 返回 SSE；`/v1/models` 返回所有启用的直连名与组合名（手动组合与自动组合同名合并，一套名字对应两条寻址路径，见 §3.2）。
- `GET /api/logs/summary` 复用列表筛选参数（忽略分页），返回 `requests / prompt_tokens / completion_tokens / cached_tokens / cost_usd / cache_hit_rate / cache_hit_rate_samples / active_minutes / span_minutes / rpm / tpm / peak_rpm / estimated_rows`；`GET /api/dashboard/summary` 在原字段后追加 `active_minutes / span_minutes / rpm / tpm / peak_rpm / estimated_rows`。
- `GET /api/logs` 支持 `view=requests|combo|status` 与 `group_key`（ADR-069）：聚合视图的响应体多出 `view`、`groups[]`、`scan_limited`，`total` 是**组数**；`group_key` 仅在下钻时传，回到 `view=requests` 并按 key 里的窗口与成员过滤。
- `GET /api/catalog` 每条另带六个可选状态字段：`health_status`、`visible_status`、`cooldown_until`、`fault_until`、`status_message`、`consecutive_failures`（直连为该模型连续失败次数，组合条目只透出成员聚合值）。直连取该模型自身状态，手动组合沿成员链递归，自动组合按成员聚合；可见状态取最差（fault > open > half_open > disabled > normal），恢复时间取最早，`status_message` 给人话说明（如「5 个成员中 4 个熔断」）。术语固定为故障 / 熔断 / 半开试探 / 已停用，禁止把熔断写成「限流冷却」。模型管理页用 `?include_disabled=true` 取目录，停用直连条目不消失、可就地启用。
- 渠道密钥：`POST /api/channels/{id}/keys` 与 `POST /api/channels`（批量）遇重复密钥返回 409 `CONFLICT`；`PATCH /api/channels/{id}/keys/{key_id}` 可改 `label` 与启用状态，`label` 为空或纯空白时回落默认名；`PATCH /api/channels/{id}` 的 `api_key` 与同渠道其它密钥重复时 409，不静默覆盖。渠道保存的非 slug 完整性错误统一 500 `INTERNAL`（不回传 SQL 明文，日志记完整异常）。所有列表条数参数上限 200。
- 搜索语义：渠道与日志列表的关键词按**字面量**匹配，`%` 与 `_` 是普通字符不是 SQL 通配符（统一走 `api/src/l04_repo/search.py` 的 `like_contains()`：转义后 `ILIKE`，`ESCAPE` 为反斜杠）（ADR-044）。


---

## 7. 数据模型

所有时间字段为 UTC 毫秒整数（`BigInteger`）。数据库迁移是 `PRAGMA table_info` + `ALTER TABLE` 手写增量，无 Alembic；迁移幂等，启动时重复执行安全。`channels.timeout_s` 是遗留必填列（旧库无默认值，曾导致新建渠道被误报为 slug 冲突），迁移会补 `DEFAULT 60`，`ChannelORM` 已映射该列。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `users` | `id, username, display_name, created_at` | 调用方 |
| `api_keys` | `id, user_id, label, key_prefix, key_hash, key_enc, enabled, last_used_at, created_at` | 鉴权只认 `key_hash`；`key_enc` 是 Fernet 加密明文，供列表复制/重置（旧行为空） |
| `admin_users` | `id, username, password_hash, created_at, updated_at` | 单管理员 |
| `channels` | `id, slug, name, connection_url, api_key_enc, protocol, auth_style, protocol_source, priority, rewrite_rules_json, enabled, note, connection_*` | `api_key_enc` 是首密钥镜像 |
| `channel_keys` | `id, channel_id, label, key_enc, enabled, last_used_at, created_at, updated_at` | 多密钥 |
| `channel_models` | `id, channel_id, upstream_model, call_model, model_type, model_type_source, input_price_per_1m, output_price_per_1m, parameters_json, enabled, banned_until, schedule_json, cooldown_json, health_status, consecutive_failures, consecutive_trip_count, escalation_trip_count, opened_at, cooldown_until, half_open_since, fault_until, probe_*` | `(channel_id, call_model)` 唯一；`banned_until` 是**人工冷却到期时刻**（空 = 永久封禁），`schedule_json` 按 ADR-070 的 v2 规则文档（每天 / 每周 / 每月 + 起止时刻 + IANA 时区，整份覆盖写，空规则集 = 永不冷却） |
| `combos` | `id, name, description, enabled, failover_rules_json, max_attempts, created_at, updated_at` | 组合 |
| `combo_targets` | `id, combo_id, target_type, channel_model_id, child_combo_id, priority, enabled, created_at` | 成员 |
| `request_logs` | `id, request_id, upstream_request_id, user_id, api_key_id, api_key_label, requested_model, resolved_combo_id, final_channel_model_id, channel_name, upstream_model, response_model, is_stream, source, http_status, error_code, error_message, prompt_tokens, completion_tokens, cached_tokens, cache_ratio, cost_usd, ttft_ms, ttfa_ms, ttlt_ms, total_ms, throughput_tps, usage_estimated, attempts_json, rewrite_applied, rewrite_skipped, rewrite_diff_json, response_headers_json, params_json, created_at` | 主日志 |
| `request_bodies` | `request_id, request_body, upstream_body, response_body, response_summary, reasoning_content, created_at` | 正文独立表 |
| `app_settings` | `key, value_json, updated_at, updated_by` | 正文策略 + 网关 Base URL |
| `daily_aggregates` | `id, day_ms, channel_id, model_key, requests, prompt_tokens, completion_tokens, cached_tokens, cost_usd, errors, ttft_p95_ms, total_p95_ms` | 只由 rebuild 写，查询不读 |
| `model_defaults` | `model_id`（PK，逻辑模型）, `matched_by`, `providers_json`, `rule_version`, `fields_json`, `pulled_at` | models.dev 派生默认值（ADR-073）：每逻辑模型一行，`fields_json` 装合并结果并充当「上次自动写入值」基线；派生表不 bump `catalog_version` |

`request_logs` 的维度字段（`resolved_combo_id` / `final_channel_model_id` / `channel_name`）按实体 id 归并、展示名取当前名；时间字段（`ttft_ms` / `ttfa_ms` / `ttlt_ms` / `total_ms`）的节点口径见 §3.4 与 `GLOSSARY.md` §5 / §5.2。

**已决策、待实施：尝试行化（ADR-072）**。请求日志的尝试数组将从 `request_logs.attempts_json`（blob）迁到独立表 `attempts`：ORM 名 `AttemptORM`，与 `request_logs` 1:N、随父行 `ON DELETE CASCADE`，唯一键 `(log_id, seq)`；`attempts_json` 在「双写 → 切读 → 停写」三阶段验收后 drop（不可逆，单独发布）。为什么：① 尝试级判定今天 SQLite 走 `json_each + json_extract`、Postgres 走**能力降级**（只匹配前缀、看不到错误正文），行化后两侧是同一段 `EXISTS`（`!=` 编译成 `NOT EXISTS`）；② 读放大 9–12× 变一次过滤下推，尝试级聚合从 `json_each + Python`（现网 4.3 s）降到 SQL `GROUP BY`（164 ms）；③ 写 +1.24 ms/请求、空间 −33%（每尝试 260 B vs 390 B）；④ 顺带修掉数值字段被当 blob 子串匹配的假阳性。灰度开关：`ATTEMPTS_WRITE`（双写，读 JSON）→ `ATTEMPTS_SOURCE`（切读）→ 停写并 drop；`MAX_SCAN_ROWS` 语义与数值都不变。PG 侧在 `db/postgres_schema.sql` 的 `request_logs` 之后追加 `attempts` 段，并把 `attempts` 插进迁移脚本 `TABLE_ORDER` 的 `request_logs` 之后、`request_bodies` 之前（父先子后）。

`channel_models.model_type` / `model_type_source` 的取值、推断与守卫语义见 §3.5；`auto` 只出现在写入接口，不入库。

`channel_models.parameters_json` 固定字段：`max_input_tokens`（0=未设置）、`max_output_tokens`（0=未设置）、`reasoning_level`（空=未设置）、`input_types`（默认 `["text"]`）、`output_types`（默认 `["text"]`），外加四个可选能力位键 `tool_call` / `reasoning` / `attachment` / `structured_output`（三态布尔，`None` = 未设置且**不写 JSON**，ORM 默认值不变）。 这些字段的默认值可由 models.dev 同步预填与更新；人工值保护用**值比对**——当前值与上次自动写入值相同才更新，不同则保留（不引入 `source` 字段），见 ADR-073（status: accepted，已实现 2026-11-16）。

---

## 8. 配置

配置由 `pydantic-settings` 从环境变量与 `api/.env` 读取，变量名不区分大小写，无前缀。`api/.env.example` 是模板；生产必须覆盖安全项。

### 8.1 基础与安全

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `local` | `local / staging / production`；后两者才注册 SIGTERM handler |
| `APP_NAME` | `Slipstream` | 应用名 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app.db` | SQLAlchemy URL |
| `JWT_SECRET` | `dev-secret-change-me` | 生产必须改 |
| `JWT_EXPIRE_MINUTES` | `10080` | 默认 7 天 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin` | 首次启动创建；生产必须改 |
| `ENCRYPTION_KEY` | 空 | 32 字节 base64；空则从 JWT_SECRET 派生 |
| `CORS_ORIGINS` | `http://localhost:3680,http://127.0.0.1:3680` | 逗号分隔 |
| `DEFAULT_TIMEZONE` | `Asia/Shanghai` | 看板/计划窗口缺省 IANA 时区 |

### 8.2 上游传输、连接池、超时与重试

| 变量 | 默认值 | 说明 |
|---|---|---|
| `UPSTREAM_TRANSPORT` | `httpx` | `httpx`（默认，ADR-065）或 `litellm`；**全局**开关（`config.py:27`，读取点 `gateway.py:778/:815`），无渠道级覆盖；切 `litellm` 会抬高每请求 CPU（§11.3） |
| `LITELLM_COST_CALC` | `off` | LiteLLM 自带的 per-request 花费计算（cost map 查表 + 零花费诊断，实测约 6.4 ms CPU/请求）。`off` 时四个入口被 stub（`gateway.py:674-711`），计费由 `billing.compute_cost` 按库内价格算（ADR-058）。**改错后果**：设 `on` 只多花 CPU、不影响账单；但只要将来启用 LiteLLM 的预算或花费回调（`max_budget` / 花费用例），必须设 `on`，否则预算永不触发 |
| `REQUEST_TIMEOUT_S` | `120` | 非流式上游请求总超时 + 重试预算参照；流式无总闸（ADR-075） |
| `UPSTREAM_CONNECT_TIMEOUT_S` | `10` | TCP/TLS 建连超时 |
| `UPSTREAM_FIRST_BYTE_TIMEOUT_S` | `30` | 流式首个事件超时 |
| `UPSTREAM_READ_TIMEOUT_S` | `120` | 已建立连接后的读空闲超时（流式逐块看门狗：判「连接死了」而非「用得久」，非总闸） |
| `UPSTREAM_MAX_CONNECTIONS` | `200` | 共享 client 最大连接 |
| `UPSTREAM_MAX_KEEPALIVE_CONNECTIONS` | `50` | 热连接数 |
| `UPSTREAM_KEEPALIVE_EXPIRY_S` | `30` | keepalive 过期 |
| `GLOBAL_RETRY_MAX_ATTEMPTS` | `12` | 单请求最大上游尝试次数 |
| `GLOBAL_RETRY_BUDGET_S` | `120` | 单请求重试总时间预算（须 ≤ `REQUEST_TIMEOUT_S`，否则启动失败） |

三个上游看门狗超时（连接/首字节/读空闲）可在管理设置页按部署覆盖：env 值始终是默认值，覆盖值存 `app_settings` 的 `upstream_timeouts` 行（范围 (0, 86400]，可重置回 env；见 `upstream_settings.py`，ADR-075 修订）。请求路径读进程内缓存（15 秒 TTL，启动预热，保存/重置即时刷新本进程）。`REQUEST_TIMEOUT_S` 与重试预算不参与覆盖。

### 8.3 数据库与排空

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DB_POOL_SIZE` | `10` | 连接池常驻连接 |
| `DB_MAX_OVERFLOW` | `20` | 峰值溢出 |
| `DB_POOL_TIMEOUT_S` | `30` | 拿连接超时 |
| `DB_POOL_RECYCLE_S` | `1800` | 连接回收 |
| `SHUTDOWN_GRACE_S` | `60` | 在飞流排空上限 |
| `DB_POOL_PRE_PING` | `True` | 取连接前先探活（一次额外往返），能发现已被服务端关掉的连接（PG + pgbouncer 场景）。**改错后果**：设 `False` 时空闲断连会以「连接已关闭」的形式抛到请求上；SQLite 下无差别 |
| `DB_STATEMENT_TIMEOUT_MS` | `0` | 仅 PostgreSQL 生效：单语句超时毫秒，`0` = 不限。**改错后果**：设得比看板聚合/日志检索的实际耗时小，这些页面会直接 5xx；设 `0` 则一条慢查询可以占满连接池 |
| `DB_APPLICATION_NAME` | `slipstream` | 仅 PostgreSQL：写进 `pg_stat_activity.application_name`。不影响功能，只影响排障时能否按副本区分连接 |

### 8.4 熔断、探测与请求限制

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROBE_TIMEOUT_S` | `20` | 探测超时 |
| `PROBE_MAX_TOKENS` | `16` | 模型连接探测预算，避免推理模型被 1 token 误判（`probe.py:217` 在配置为 0/空时回落到 64） |
| `BREAKER_FAILURE_THRESHOLD` | `5` | 连续失败触发熔断 |
| `BREAKER_COOLDOWN_S` | `30` | 普通冷却 |
| `BREAKER_RATE_LIMIT_COOLDOWN_S` | `5` | 429 冷却 |
| `BREAKER_FAULT_THRESHOLD` | `3` | 连续熔断升级故障阈值 |
| `BREAKER_FAULT_COOLDOWN_S` | `300` | 故障冷却 |
| `BREAKER_ESCALATE` | `True` | 是否升级为故障 |
| `MAX_ATTEMPTS` | `3` | 组合默认轮数（该配置当前未被代码读取；组合轮数由 `combos.max_attempts` 列默认 3、取值 1–50 决定） |
| `MAX_BODY_BYTES` | `2000000` | 入站请求体上限 |

### 8.5 前端

前端只读 `VITE_APP_ENV` 与可选 `VITE_API_TARGET`；后者覆盖 Vite 代理目标。生产构建产物是纯静态文件。

### 8.6 写管线与零 SQL 读路径（默认全关）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `USAGE_PIPELINE` | `classic` | 请求日志写路径的**总开关**（ADR-057）。`classic`/`off`：每请求一条多列 INSERT，路由/鉴权/正文策略读库；`memory`/`on`：请求只往 WAL 追加一行完整日志，策略读进程内 catalog 快照，后台批量 upsert。其它值启动即失败（`use classic|memory`）。**改错后果**：单进程下 `memory` 无吞吐收益（实测 40.2 vs 45.2 RPS）且日志延后可见；`classic` 让多个进程共用一个 SQLite 时实测丢 4.9% 行 |
| `USAGE_WAL_DIR` | `data/usage` | WAL 段目录，仅 `memory` 使用；相对路径按进程工作目录解析 |
| `USAGE_WAL_FLUSH_MS` | `100` | 段文件 fsync 间隔；调大 = 更少吃 fsync，但崩溃时可能丢掉已返回给客户端的日志行 |
| `USAGE_SINK_BATCH` | `200` | 后台一次 upsert 的最大行数；调大 = 单事务更久、与读侧争锁更明显 |
| `USAGE_SINK_INTERVAL_MS` | `1000` | 后台 flush 间隔，也就是「日志/看板多久后能看到」的量级 |
| `LEDGER_SPLIT` | `off` | 计费流水拆分路径。`off`：账本与日志同一条 INSERT 写完；`on`：计费 INSERT + 可观测队列 + ledger WAL/sink，每请求三条语句、CPU 更高。**改错后果**：开 `on` 不会算错钱，只是更慢；保持 `off` 时改日志保留策略要连账本一起想（两者同表同生命周期） |
| `LEDGER_WAL_DIR` | `data/ledger` | ledger 的 WAL 段目录，仅 `LEDGER_SPLIT=on` 使用 |
| `LEDGER_WAL_FLUSH_MS` | `100` | ledger 段 fsync 间隔，语义同 `USAGE_WAL_FLUSH_MS` |
| `LEDGER_SINK_BATCH` | `200` | ledger 后台批量 upsert 行数上限 |
| `LEDGER_SINK_INTERVAL_MS` | `1000` | ledger 后台 flush 间隔 |
| `CATALOG_POLL_INTERVAL_MS` | `30000` | catalog 版本轮询间隔（ADR-060）：后台比对 `catalog_version`，变了才换快照。调大 = 改渠道/价格/改写规则后生效更慢；调小 = 更多无效轮询 |
| `CATALOG_TTL_MS` | `60000` | 快照兜底全量重载的 TTL：版本轮询持续失败时最迟这么久重载一次。**改错后果**：设得过大，版本号丢失后配置可能长期不生效 |
| `CATALOG_DIRECTORY_TTL_MS` | `2000` | `/v1/models` 序列化缓存；管理端改动最多这么久后才反映到模型列表 |
| `PROBE_RESULT_TTL_MS` | `30000` | 批量探测结果的复用窗口，`force=true` 绕过；调大 = 「一键探测全部」看到的是旧状态 |

### 8.7 models.dev 每日同步（ADR-073）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MODELS_DEV_ENABLED` | `True` | 关掉后不拉快照，目录读取与预填都退化为「无默认值」 |
| `MODELS_DEV_URL` | `https://models.dev/api.json` | 只取 `api.json`（per-1M-token 口径），不混用 `/models.json` |
| `MODELS_DEV_DIR` | `data/models-dev` | 快照目录（`api.json` + `meta.json`），不入版本库 |
| `MODELS_DEV_TIMEOUT_S` | `60` | 单次拉取超时；超时按失败处理，保留旧快照且当天不再重试 |
| `MODELS_DEV_ALIAS_FILE` | 空 | 人工别名表路径（JSON），缺省或坏文件按「无别名」处理 |

机制要点（词义见 GLOSSARY §11）：刷新**惰性触发**——当天本地 00:00 后第一次读模型目录列表（`GET /api/catalog`）时拉一次，表单打开不触发、`/v1/*` 永不触发；管理员手动 `POST /api/model-defaults/refresh` 绕过日期判断；状态查询 `GET /api/model-defaults/status`。任何一次尝试都写 `last_attempt`（失败当天不再重试）；`last_pull` 仅在成功 / 304 时更新，界面的「数据日期」读它。拉取失败保留旧快照、管理面提示陈旧度；条件请求（ETag / Last-Modified）304 时只更新 `last_pull`。值比对同步（决策 6）挂在每次物化前后：旧 `fields_json` 是基线，等于基线的字段跟新拉取值、人工改动保留；`POST /api/channel-models/{id}/adopt-defaults`（`force`；界面名「应用默认参数」，旧称「恢复自动值」）把字段重置为本次拉取值并刷新基线。T1/T2 名单与 `labs` 阈值随模糊阈值同落 `app_settings` 单 key（决策 4），status 回显两维陈旧度（快照 + 派生表，决策 9）。表单能力位两态（task-153 拍板 A）：取消勾选=显式 `false`，全量提交。

---

## 9. 前端信息架构与视觉规范

> 决策理由链：ADR-031（as-built 收敛到 Tailwind + 手写 UI）→ ADR-062（不引 antd，自建基元）→ ADR-063（缺口件取 shadcn registry 源码，Tailwind 3.4 不动）→ **ADR-064（控件层全量迁到 shadcn copy-in + Tailwind 4，取代 ADR-062 / ADR-063）**；图表色板见 ADR-040。本节只写现状事实，不重复理由；界面规则见 `docs/ui-standards.md`。

### 9.1 路由

| 路由 | 页面 |
|---|---|
| `/login` | 登录 |
| `/` | 数据看板 |
| `/overview` | 全局概览（接入地址、示例、运行概况） |
| `/channels` | 渠道列表 |
| `/channels/new` | 新建渠道 |
| `/channels/:channelId` | 渠道详情/编辑 |
| `/catalog` | 模型管理：模型目录（只读） / 组合模型（`?tab=combos`，编辑用弹窗） |
| `/combos` | 旧书签重定向到 `/catalog?tab=combos`，保留 `location.state` |
| `/logs` | 使用日志 |
| `/keys` | 用户密钥 |
| `/playground` | 模型试验 |
| `/settings` | 系统设置 |
| `*` | 重定向 `/overview` |

导航分组：常规（全局概览、数据看板、使用日志）／配置（渠道管理、模型管理）／工具（模型试验）／系统（用户密钥、系统设置）。「模型管理」是**一页**（`/catalog`，ADR-067 起不再有「模型目录 / 组合模型」两个 tab），`?kind=` 驱动筛选（全部 / 直连 / 组合），URL 可分享。

### 9.2 视觉系统

- 浅色/深色两套 CSS 变量；精确 token 以 `web/src/index.css` 为准，本文只保留规则。
- 主色靛蓝 `#4F46E5`（深色 `#818CF8`）；青绿 `#14B8A6` 只做图表高亮/装饰/空状态，文字用 `#0F766E`（深色 `#2DD4BF`）。
- 语义色一律“浅底 + 深字”徽标，不用纯色底 + 白字；状态圆点旁必须带文字或图标，满足色盲可读性。
- 组件层禁写原始色值、禁 `dark:` 变体重复定义：浮层/遮罩用 `--overlay`，图表 tooltip 用 `--tooltip-bg` / `--tooltip-border`，终端示例底色用 `--code-bg`、前景色用 `--code-fg` / `--code-fg-strong` / `--code-fg-muted` / `--code-fg-faint` / `--code-hover`，登录页固定深色面板用 `--login-*`（不随主题翻转），状态徽标用 `--badge-<tone>-bg` / `--badge-<tone>-fg`（六个 tone 各有浅/深两套定义）；精确值以 `web/src/index.css` 为准。
- 字体：`Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif`；数字用 `tabular-nums`；API key/模型 ID/请求编号/JSON 用 JetBrains Mono。正文走 `var(--font-sans)`，`svg text` 必须同族（图表文字不另起字体栈），代码族走 `var(--font-mono)`；sonner toast 运行时注入的是**不带 layer** 的字体栈，须在 `index.css` 末尾 layer 外用 `html [data-sonner-toaster]` 显式覆盖，否则落回 `ui-sans-serif`。
- 正文 14px、中文行高 1.6–1.7；控件圆角 8px、卡片 12px、徽标 6px/全圆；表格行高由 padding 驱动（表头 py-3、数据行 py-2），日志页用更紧凑的行（首列 px-4、其余 px-3，数据行 py-2），没有可切换的密度档。
- 控件高度阶梯：按钮 `sm` 32px / `md` 36px / 默认 40px / `lg` 44px；同一界面、同一位置的相邻控件必须同高，同一行里的输入框与按钮同高；表格内操作按钮 32px。
- 数值格式化统一走 `web/src/lib/format.ts`：K/M/B 两位小数、百分比 1 位（错误率例外：2 位）、缺失值统一「—」；成本类数字（单笔 / 汇总 / 看板 / 行内成本）一律走 `formatUsdFine`，统一「≥ 2 位、≤ 6 位小数」，**不再按量级分档**——行内成本必须能读到 `$0.008551`，也不能把 `$0.030042` 读成 `$0.03`（2026-11-15 修订，取代 ADR-046 的「≥ 0.01 美元固定 2 位」分档）；模型单价走 `formatPricePer1m`（每百万 token，2–4 位）——**成本与单价是两类口径**，前者是花掉的钱、后者是报价，不要互相套用；`formatUsdCompact`（固定 2 位）仅作兼容导出保留，界面不再调用；页面与组件不得再自带同名格式化函数。时间显示只走 `web/src/lib/time.tsx` 的 `formatDateTime(ms, mode)`（`useTimezone()` 取 mode，支持本地/UTC 切换），不得自建 `toLocaleString` 包装；计数走 `formatCount`（千分位、缺失「—」）；百分比走 `formatPercent`；`lib/logs.ts` 只把 `lib/format.ts` 的格式化器转出给既有页面（含仅供兼容的 `formatUsdCompact`），不再提供 `formatTokens` / `formatUsd` 别名。
- 动效 150–200ms ease-out，只用于 hover/展开/toast。
- 布局：桌面侧栏 248px；平板折叠图标；移动端抽屉 + 卡片列表/横向滚动 + 底部筛选；所有页面过 375/768/1280 三档。

### 9.3 UX v2 已落地要点

- 渠道列表：首列多选 + 批量工具栏（打开/关闭/测速/删除，逐个执行、失败逐条提示，删除二次确认；测速取各渠道首个模型做模型拉取探测）；「探测记录」列两行——状态徽标+自适应延迟 / 最近探测时间；快捷操作列启停图标钮（绿开红停）+测速+编辑+删除；紧凑行高；密钥数/掩码尾号、行内优先级保存。
- 渠道详情：基本信息、密钥、模型映射、改写规则、探测/连接测试在同一页；未保存草稿可测试和发现模型；密钥行内可改备注（PATCH）与启停，批量添加提示重复条数。渠道没有可用密钥时，「获取上游模型」与「测试连接」先弹确认框说明「本次请求没有携带密钥」（并给出【仍然匿名尝试】与【去添加密钥】两个出口），不再把「没带密钥」的 401 说成「密钥被拒」；后端 `probe.py` 的 `_auth_failure_message` 同样区分「本次请求未携带渠道密钥」与「密钥被上游拒绝」。
- 模型管理页：`/catalog` **一页**（ADR-067 起取消「组合模型」页签，不再有「模型目录 / 组合模型」两个 tab）：页头有「新建组合」，`?kind=` 驱动筛选（全部 / 直连 / 组合），旧书签 `/combos` 与 `?tab=combos` 都改写成 `?kind=combo` 并保留 `location.state`。直连行与组合行共用同一份目录派生状态（`CatalogStatus`），显示一致。剩下的两处 tablist（渠道详情候选模型、组合弹窗候选池）仍是标准 tablist：`role=tab` + roving tabindex（同时只有一个可聚焦），`ArrowLeft/ArrowRight` 循环切换、`Home/End` 跳首尾，`aria-controls` 指向真实存在的常驻 `role=tabpanel`（未选中的一侧 `hidden`）。这两处 tablist 共用 `web/src/lib/use-roving-tabs.ts` 的 `useRovingTabs(order, active, select)`（返回 `onKeyDown` 与 `tabProps`），页面不得自带重复的 roving 键盘实现。模型目录直连行的操作列为「封禁 / 启用」按钮对，三态：可用→「封禁」（置停用）；停用→「启用」（解禁并自动复位熔断器：清计数/冷却/故障计时→normal）；已启用但熔断/半开/故障→「启用」（仅复位，不改停用轴）；均带二次确认，组合与隐式分组行无操作；已停用的直连条目在管理目录里仍保留（模型管理页以 `include_disabled=true` 取目录，否则封禁后会从列表消失、无处点「启用」），调用面（`/v1`、试验台）的默认目录仍只返回可调用模型（ADR-050）。
- 组合行与直连行同表：自动组合只读（标「只读 · 实时派生」），手动组合显示嵌套深度、成员 priority、启停并有编辑/删除；创建/编辑弹窗为左右两列穿梭：左列已选成员（拖拽手柄、priority 数字输入、「同层 ×N」徽章、上移/下移/移除，**成员行不提供启用开关**——组合内成员始终参与调度，要停用就移除），右列待选（「渠道模型 / 子组合」两个 tab、按名称或上游模型或渠道双搜索、按供应商分组可折叠或平铺，点击即加入，已加入与已停用置灰；候选池只含直连模型，组合/分组条目不会混进来）。候选池同时包含**被封禁（停用）的直连模型**：它们标「已封禁」并且仍可搜索、仍可加入组合——封禁是临时状态，组合是长期配置（成员池以 `include_disabled=true` 取目录；已选成员行显示「（已封禁）」标注）；子组合停用则仍不可加入。新成员 priority 取**已有成员的最小值**（并入最低层；空组合从 1 开始），**不是最大值 +1**——方向翻转后「最大值 +1」会让新成员插队到最前；拖动或上/下移是**数值互换**，行尾浮出「将互换 A / B 优先级 x ↔ y」；弹窗顶部固定一张口径卡片「priority 是层，不是序号」，并写明方向「数值越大越先调用；同一层成员等概率随机分流」。
- 日志页：顶部 5 项度量条（用量、开销、RPM、TPM、缓存命中率，各带样本/活跃分钟/估算行数 hint）；工具栏 8 列网格（关键词、时间预设、模型、状态、搜索按钮、更多筛选），时间预设 6 档（今天 / 本周 / 24 时 / 最近 7 天 / 最近 30 天 / 自定义），点「搜索」或回车才提交筛选，有未提交改动时「搜索」按钮整体高亮（`variant=default` 实心 primary + `title` 提示「有未应用的筛选修改」），不再用圆点；时间弹窗没有「应用/取消」——点预设立即预览并关窗、自定义区间填到有效值时即时预览（无效只显示红字、不提交），底部只留一行「切换后点「搜索」应用筛选」；「今天」的区间末尾预留 1 小时余量（`TODAY_TAIL_MS`），避免刚点完搜索就漏掉新产生的日志（ADR-053）；改动过但尚未提交的筛选控件（关键词、时间范围、模型、状态、渠道、用户、密钥标识、流式、失败尝试）周围加主色描边（`filter-dirty`），点「搜索」后随已生效条件同步而消失——只提示「哪个框改了」，不替用户提交；筛选条件写进 URL（`q/range/start/end/channel/model/status/user/key/stream`），刷新与分享可复现；「隐藏敏感信息」开关遮罩用户、密钥、费用三列并记在浏览器本地；紧凑行、状态点、两行时间、两行吞吐、首字/首答/总耗时三段色条、就地展开、完整详情抽屉、本地/UTC 切换；分页默认 25 条（可选 25/50/100/200），计时统一秒 + 2 位小数，首答为空（仅推理）标注「无正文与工具调用」，来源徽标「聊天/探测」。分页与每页行数写进 URL（`page` / `page_size`），刷新与分享可回填，翻页只改 URL、不重置筛选；日志行可聚焦（`tabIndex=0` + `role=button` + `aria-expanded`），`Enter`/`Space` 展开或收起明细。状态列对整体 2xx 但含失败尝试的行加警告徽标「含 N 次失败尝试」（不伪造状态码），详情抽屉新增「失败尝试」Section（渠道/上游模型/错误码/耗时/原始消息）；更多筛选含「失败尝试筛选」（透传 `attempt_status=error` 进 URL 与请求），渠道筛选旁说明「包含该渠道失败的请求」；默认时间区间为「今天」，时间预设选择器以文本显示所选区间（ADR-052）。请求日志新增来源与头字段：`client_ip`、`forwarded_ips`/`forwarded_sources`（RFC 7239 + XFF/X-Real-IP 解析、去重）、`request_headers`/`response_headers`（脱敏+1024 截断，仅详情返回）；流式三个终态 attempt 均带请求/响应头；直连入站兼容管理员会话 JWT（无 API 密钥时可带会话调用，试验页即用此路径）。详情抽屉「用量与耗时」末尾加「来源地址 / 转发地址」两行（无值显示「—」，hover 显示转发原始头），Attempts 时间线后新增「请求头 / 响应头」Section：下游请求头默认展开、上游请求头默认折叠（取最后一个带头部的 attempt 并注明来源轮次）、上游响应头默认展开，折叠策略集中在 `HEADERS_POLICY` 常量，展开后可见脱敏值（如 `Authorization: ***`）。视图切换在筛选卡右上（逐次请求 / 按组合 / 按状态，写 URL `view`，默认逐次请求）；聚合行可就地展开为成员行（下钻带 `group_key` 并保留全部筛选），扫描触顶时行内提示「已扫描 N 条」；尝试时间线里失败尝试与跳过尝试默认折叠，摘要显示「渠道 · 模型 · 原因 · 优先级 · 耗时」，耗时统一放在「优先级 N」行尾，`skipped` 显示「未发出请求」。
- 获取上游模型弹窗：搜索过滤 + 按 `/` 前缀分组切换（分组/平铺、组头可折叠）；≥1024px 宽屏两列（平铺时每列独立滚动，分组时组头整行横跨）；「取消 / 重新获取 / 添加 N 个模型」三键同高 40px，主操作实心 primary；新建渠道时弹窗内先建渠道再批量写模型，失败保留已勾选模型可重试。
- 看板：5 张 KPI 卡 / 9 个指标格（请求数+错误率、输入+输出词元、缓存词元+命中率、RPM+TPM、成本），`xl:grid-cols-5` 自适应、1px 分隔线；趋势维度三档（综合模型=请求里的 combo、上游模型、上游渠道），Top 榜跟随维度切换；趋势图两级悬停——列模式列出该时刻所有可见模型与合计，段模式只显示该模型；右侧竖排图例每项带全时段合计、点击可隐藏，隐藏列不计入柱高与加权平均；16 色循环复用（不再折叠「其他」）；tooltip 贴边自动翻转；空状态引导。趋势图左侧留 44px 标尺槽：纵轴按当前指标的最大值自动取整量级刻度（步长取 1/2/2.5/5×10ⁿ，请求数这类不可分割指标取整步长），刻度标签与柱高、tooltip 同一口径（词元/请求走 K/M/B、成本走美元、延迟走秒），窗口无数据时退化为 0–1。Top 渠道 排行把从未命中任何渠道的请求（503 `NO_AVAILABLE_CHANNEL`，库中 `channel_name` 为空串）显示为「请求模型无可用渠道」，不再显示「（未命名渠道）」——那是「一个渠道都没服务成功」，不是渠道名缺失；请求详情里的 `channel_name` 仍是空串原值。缓存 KPI 卡的说明文字单行省略（`truncate`），hover 或键盘 focus 都展开四口径详情（A token 加权 / B 请求级 / C 可缓存占比 / D 节省），详情档 `w-80` 并带样本数与口径说明；单维趋势图与三视角趋势图底部都补「起 / 至 + 粒度」横轴说明。趋势区在 ≥900px 时**同时画出三档维度**（综合模型 / 上游模型 / 上游渠道）为一组并排柱、共用 y 轴：图例三项默认全开、可逐个关闭（`aria-pressed`，至少保留一个），隐藏后柱高、合计与加权 P95 同步重算；tooltip 固定写明「同一批请求的三种分组视角，不可跨维度相加」；<900px 降级为单维度 + 分段选择器；Top 榜仍跟随当前维度。趋势卡的第二个标签是「分流图」（桑基）：显示前 20 / 全部切换、按请求数与按 Token 切换、缩放与复位控件、点击节点隐藏并可恢复；窄屏横向滚动不压扁；只有切到这个标签才发 `/api/dashboard/flows`（懒挂载 + 60s 缓存）。
- 设置页：正文日志策略、脱敏不可关闭说明、更新人/时间。
- 用户密钥：列表行内「复制」完整密钥（服务端加密留存，按钮变「已复制」+ 绿勾、2s 复原，并弹**右下角 toast**「已复制密钥」，距边 24px、13px 且与正文同字体）与「重置」（二次确认，旧密钥立即作废、新明文可复制）；行内操作按钮各配 lucide 图标（重命名/封禁或启用/重置/删除等），相邻按钮间距 `gap-0.5`；标识列 `max-w-70 truncate`，表格列一律 `whitespace-nowrap`，窄屏改横向滚动而不是挤断行；创建弹窗提示完整密钥已加密保存、可随时复制（ADR-056）。
- 模型试验：**聊天式布局**——底部固定输入框（随内容自动增高），左侧附件 / 参数控制 / 清除记录，右侧模型选择、思考等级、发送；参数控制面板四组两列（系统提示词 / 采样 temperature·top_p·top_k·seed / 输出 max_tokens·stop·response_format / 高级），标注「只对下一次请求生效」，`max_tokens` 上限取所选模型的 `max_output_tokens`；高级组另有**思考**下拉（不发送 / `reasoning_effort` / `thinking` 开关 / `enable_thinking` 开关，开思考时默认 `max_tokens` 抬到 16384）与**额外请求字段（JSON）**自由透传框（网关除 `None` 外全透传，厂商私有字段用它兜底，删除值用 `null`）；**聊天记录只保存在浏览器**（`localStorage`，不写后端）；模型选择用**搜索框与下拉合并**的 combobox（搜调用名/上游模型/渠道）。助手消息带**思考块**：默认折叠、内部滚动，badge 由我们发出的参数决定（`reasoning_effort=<值>` / `thinking=on` / `enable_thinking` / `think` / `think 标签`），没有思考内容时显示「无思考块」而不是空白，厂商差异见 [`upstream-reasoning.md`](./upstream-reasoning.md)。保留非流式/流式、TTFT/总耗时/吞吐展示、中途停止保留部分输出；API 密钥输入框仅存内存态（不写任何浏览器存储），留空回落管理员会话 JWT——用户可换不同密钥测试。
- 模型目录列依次为 名称 / 渠道 / 协议 / 价格 / 状态 / 操作——**没有「类型」列**（类型只在筛选里出现）、**没有「最大输入」列**、**没有单独的「调用串」列**（名称列显示的就是调用名）；价格三合一为一列（输入 · 输出 · 缓存，每百万 tokens、美元，表 `caption` 说明单位与「—」的含义）；上游模型挂在名称旁图标的 tooltip 里，组合行名称旁另有组合标记（hover/focus 展开优先级分层，如 `P2 model-a, model-b` / `P1 model-c`）；状态列 = 可见状态 + 预计恢复时间 + 一行 `status_message` 摘要，缺字段时回落「可用」；可见状态五态——正常「可用」、半开「半开 · 等待下次调用」、熔断「熔断 · 预计 HH:mm 可用」、故障「故障 · 预计 HH:mm 可用」、停用「停用」，前端只读 `visible_status`；行内「加入组合」打开组合编辑器并预置该成员；状态列在 `consecutive_failures > 0` 时附「近期连续失败 N 次（达到阈值后熔断）」（字段由 `/api/catalog` 直连条目透出）。
- 列表分页统一用 `components/ui/pagination.tsx`：左侧「总计 N 条」，右侧「每页行数」（25/50/100/200）与首页/上一页/页码/下一页/末页；翻页或改每页行数后表格回到顶部；无障碍状态行「第 x / y 页 · 共 N 条」放在 sr-only `role=status` 里，避免与左侧重复。分页另带「跳到」输入：Enter 或失焦生效，非数字回退当前页，0/负数/超出末页钳到首末页，与当前页相同时不触发翻页；组件保持受控，不托管 `page`。

---

## 10. 测试与质量基线

| 层 | 基线 |
|---|---|
| 后端 | pytest 全量套件最近一次实跑**通过**；基线以最近一次实跑为准，复现命令见 `docs/testing.md`（不记录用例数/通过数/耗时，见 §16 计数纪律） |
| 前端 | Vitest 全量最近一次实跑**通过**，`tsc --noEmit` exit 0；Playwright 视觉用例最近一次实跑**通过**（基线在 `web/e2e/__screenshots__`）。不记录用例数与耗时 |
| 构建 | `npm run build` = `tsc --noEmit && vite build`；产物文件名与体积不作为基线记录；本机注意：esbuild 在系统 TEMP 下会报 `remove ...\Temp\esbuild-* : Access is denied`，须把 `TEMP`/`TMP` 指向仓库内目录（如 `web/.build-tmp`）再构建 |
| 视觉 | Playwright 视觉用例固定 1440×900 + `Asia/Shanghai`，全部 `/api/**` 用 `page.route` mock；基线在 `web/e2e/__screenshots__`（场景数与耗时不作为基线） |
| 后端重点覆盖 | app 导入/健康、鉴权、正文日志、熔断、渠道/密钥/模型、组合、看板、E2E 冒烟、failover、假上游、自动组合、延迟、日志、探测、脱敏、响应改写、保留、轮次、计划、schema、设置、流式、时间序列 |

- 后端约束：必须 `$env:PYTHONPATH='src'`（否则全局 PYTHONPATH 可能导入宿主 pydantic）；无 pytest-timeout 插件，不要用整个套件做快速回归；分批时单批 ≤4 文件，耗时以最近一次实跑为准。
- 前端约束：单文件测试远快于全量；UI 改动后必须跑 `npm run test:visual`，确认预期变化后再 `test:visual:update`。

---

## 11. 性能与容量

> 逐项测量表（三栈对比、阶梯压测、冒烟）与复现命令在 `docs/PERFORMANCE_REPORT.md`；本节只留结论与口径，不重复表格（ADR-019）。

### 11.1 传输栈对比（2026-11-12）

- LiteLLM SDK 与原生直连基本同档，不能得出显著快/慢；旧自建网关固定附加延迟为 首字 +0.77–0.92s、首答 +0.77–1.26s、总耗时 +0.75–1.25s，根因是**每请求新建 `httpx.AsyncClient`**（构造约 0.65–0.95s，SSL context 初始化同步阻塞事件循环）。
- 共享 client 后：1 并发首字 p50 734.5ms → 44.0ms；10 并发 5062.5ms → 290.5ms；10 并发吞吐 1.1 → 8.4 rps。

### 11.2 单实例容量（mock 0.6s 流，SQL 瘦身前）

- 稳定区约 **100–200 在飞流、12–13 rps**；500 并发成功率 91.6%、开始出现 500 错误，1000 并发成功率 45.1%；首字 p50 从 1 并发的 44.0ms 涨到 200 并发的 10167.5ms。
- 根因不是上游，是 DB 连接池 + SQLite 单写者 + 每请求 `touch_api_key` 写事务；单纯扩大 DB pool 更差（200 并发成功率 100% → 88.2%，仍是 `database is locked`）。
- 十万并发单进程做不到：按 200 在飞流/实例至少约 500 实例；按 30s 流、12–16 rps/实例，启动速率需 200–280 实例；100k QPS 需 6,000–8,000+ 实例。正确方向是边缘层承接连接、网关集群有界并发、PG/Redis、429/503 + 排队、按在飞流/上游配额扩容。
- 这些数字是 **mock 0.6s 流**，不能当真实 NIM 容量（真实流 3–30s，上游配额是另一道墙）。

### 11.3 归因复测（SQL 瘦身后，httpx 回退路径）

- 同机同并发：直连 mock 64.7 rps / 最小 SSE 反代 29.7 rps / 网关 httpx 回退 15.0 rps / 网关 LiteLLM 10.7 rps。直连 mock 也是 Python，说明瓶颈不在语言，而在多一跳 SSE 反代（约 2.2×）与网关控制面（再约 2×）。
- LiteLLM 比 httpx 回退慢约 29%（10.7 vs 15.0 rps）、每请求多约 50ms CPU，主要来自每 chunk 的 `ModelResponse → dict → 归一化 → JSON 再序列化` 与 `asyncio.wait_for`/回调链——LiteLLM 是放大器，不是根因。
- SQL 从每请求 13 条降到 6 条（`api_keys`/`users`/`channels`/`channel_keys`/`channel_models` 各 1 条 SELECT + `request_logs` 1 条 INSERT）：`admit_candidate` 健康候选快速返回、`touch_api_key` 60s 节流、`record_success` 无变化短路、body-logging 策略 5s TTL 缓存。100 并发吞吐 8.4 → 13–15 rps。
- 4 worker（同机、SQLite WAL）20/50/100 并发 = 15.9/25.3/17.7 rps：50 并发约 1.6× 单进程，100 并发不线性（SQLite 单写者 + 4 核争抢）。

### 11.4 仍未解决

- 单进程事件循环 + GIL：稳定区约 14–15 rps（16 片快流）；200 并发 400/400 成功但总耗时 p50 8447ms / p95 22019ms，尾延迟不可用。
- SQLite 单写者：每请求 1 条 `request_logs` INSERT，多 worker 后写锁竞争成为上限；高吞吐需要 Postgres 或独立日志队列。
- `verify_password` 是同步 PBKDF2（240k 轮，实测 0.77s），在 async 登录端点内会阻塞整个事件循环，应移入线程池；它不在 `/v1/chat/completions` 路径上，但管理登录会卡住正在流式的请求。
- 建议：纯转发吞吐优先让边缘 nginx/Envoy/Go 反代承接 SSE，Python 网关只做控制面；需要 Python 直出则多 worker + Postgres + 有界并发（429/503）；降低每 chunk 成本（整段 timeout 替代逐行 `asyncio.wait_for`、能透传 bytes 就不 parse/re-serialize、LiteLLM 只在需要渠道适配时启用）。

---

## 12. 生产差距与运维

### 12.1 已补齐

进程级共享上游 client + 连接池、连接/首字节/读/总四层超时、单请求全局重试预算、SIGTERM/SIGINT 排空在飞 SSE、DB 连接池显式配置与 SQLite `busy_timeout`、LiteLLM 冷启动预热、流式请求提前释放 DB session、正文异步写与退出排空（细节见 ADR-035 与 §5.3）。

### 12.2 仍存在的差距

| 差距 | 影响 | 建议 |
|---|---|---|
| 只有 SQLite，无 PostgreSQL | 单写者、锁等待、无法多实例共享 | 生产切 PostgreSQL；补备份/迁移 |
| 单 uvicorn worker | 熔断/冷却/半开状态不跨进程 | 多 worker 前把状态外置到 Redis/PG，或每实例独立状态 + 粘性 |
| 无内置并发上限/背压 | 过载时可能在连接池等待后 500 | 增加在飞流上限，超限快速 429/503 + Retry-After |
| 无 Prometheus/OTel | 只能看 DB 日志，缺连接池/上游建连/队列指标 | 补 metrics 与 trace |
| `/readyz` 未实现 | 只有 `/health` 存活检查 | 部署需要就绪探针时新增：DB ping + 上游 client 就绪 + 非关闭中 |
| `daily_aggregates` 只写不读 | 大数据量看板扫 `request_logs` 会慢 | 读路径切聚合表或物化视图 |
| 手写迁移 | 无版本表/回滚/审计 | 引入 Alembic 或等价迁移工具 |
| 无 RBAC/多租户/配额 | 只有单管理员 + 用户密钥 | 多人/对外售卖前补 |
| 入站只支持 OpenAI Chat | Anthropic/Gemini 只能探测 | 需要时加入站适配器 |
| LiteLLM 归一化未知字段 | 极端私有协议可能丢字段/头 | 传输臂是**全局**开关、无渠道级覆盖：实例跑 `litellm` 时，单个渠道无法单独用 httpx，只能整实例切回默认 |
| 流式响应体不改写 | 部分上游响应结构无法在线修正 | 保持现状或在客户端/入站适配层处理 |

### 12.3 部署检查清单

1. 改 `JWT_SECRET`、`ADMIN_PASSWORD`，显式设 `ENCRYPTION_KEY`。
2. 设 `APP_ENV=production`，确认 SIGTERM handler 生效；进程管理器给足 `SHUTDOWN_GRACE_S`。
3. 前端 `npm run build`，静态托管 `web/dist`；反向代理关闭 SSE 缓冲。
4. 反向代理透传 `X-Request-ID`、`Authorization`，设置合理的读超时（≥ 上游总超时）。
5. 监控 `/health`、5xx 分类、在飞流、DB pool wait、上游首字节/总耗时。
6. 单实例先按 100–200 在飞流做容量规划；超限前加副本与背压，不要直接放大 DB pool。
---

## 13. 决策记录（ADR 索引）

> ADR-001 至 ADR-056 是 2026-11-12/13 期间的早期决策，2026-11-14 已全部回填为 `docs/adr/` 下的独立文件（编号不变、front-matter 标 `backfilled: true`、决策与后果两栏逐字复制；压缩表当时只有一两句话的，文件里明确写「当时未记录备选方案 / 未记录后果」，**不补写没有出处的理由**）。**本表自此只是索引**：标题是一句话摘要，正文与状态见各自文件；ADR-034 与 ADR-057..061 本来就有独立文件。新决策一律「建文件 + 本表加一行」。格式规则见 [`docs/adr/ADR-000.md`](./adr/ADR-000.md)。

| 编号 | 决策（一句话） | 全文 |
|---|---|---|
| ADR-001 | 本地优先，先跑通再上生产组件；默认 Python + Node + SQLite | [local-first-bootstrap](./adr/ADR-001-local-first-bootstrap.md) |
| ADR-002 | 不建独立 `gateway/` 目录，网关逻辑在 `l05_service` | [no-separate-gateway-dir](./adr/ADR-002-no-separate-gateway-dir.md) |
| ADR-003 | 所有 LLM 调用只走 `l05_service/llm/gateway.py` | [single-llm-gateway](./adr/ADR-003-single-llm-gateway.md) |
| ADR-004 | MVP 用 LiteLLM SDK，不用 Proxy | [litellm-sdk-not-proxy](./adr/ADR-004-litellm-sdk-not-proxy.md) |
| ADR-005 | SQLite + 仓储层隔离，原计划 Alembic | [sqlite-with-repository-layer](./adr/ADR-005-sqlite-with-repository-layer.md) |
| ADR-006 | Vite + React，不用 Next.js | [vite-react-not-nextjs](./adr/ADR-006-vite-react-not-nextjs.md) |
| ADR-007 | 只用 shadcn/ui + TanStack Table | [shadcn-ui-tanstack-table](./adr/ADR-007-shadcn-ui-tanstack-table.md) |
| ADR-008 | TanStack Query 管服务端状态，OpenAPI codegen 后置 | [tanstack-query-server-state](./adr/ADR-008-tanstack-query-server-state.md) |
| ADR-009 | Arq 后置，先留 TaskQueue 接口 | [arq-deferred-taskqueue-seam](./adr/ADR-009-arq-deferred-taskqueue-seam.md) |
| ADR-010 | 后端 `lNN` 分层 + 公开入口 | [layered-backend-lNN](./adr/ADR-010-layered-backend-lNN.md) |
| ADR-011 | 多协议入站用适配器注册表，MVP 只开 OpenAI Chat | [multi-protocol-inbound-registry](./adr/ADR-011-multi-protocol-inbound-registry.md) |
| ADR-012 | 组合模型不允许嵌套 | [no-nested-combos](./adr/ADR-012-no-nested-combos.md) |
| ADR-013 | 模型分类与三段式导航 | [model-taxonomy-and-navigation](./adr/ADR-013-model-taxonomy-and-navigation.md) |
| ADR-014 | 组合最多 3 层；priority 顺序、同层随机；删除 weight；max_attempts 按轮 | [combo-layers-priority-random](./adr/ADR-014-combo-layers-priority-random.md) |
| ADR-015 | 双轴状态（启停/健康）+ 熔断升级 + 伪成功 | [dual-axis-status-and-breaker](./adr/ADR-015-dual-axis-status-and-breaker.md) |
| ADR-016 | 连接测试与模型探测分离；协议探测落库；unknown 只表示未探测 | [probe-vs-connection-test](./adr/ADR-016-probe-vs-connection-test.md) |
| ADR-017 | 网关只转发不改道；仅允许头/体/响应有限改写；`#` 精确地址 | [forward-only-rewrite](./adr/ADR-017-forward-only-rewrite.md) |
| ADR-018 | 服务端 UTC、前端本地优先 | [utc-server-local-client](./adr/ADR-018-utc-server-local-client.md) |
| ADR-019 | 吞吐公式、缓存比例归一、16 色调用量配色 | [throughput-formula-and-palette](./adr/ADR-019-throughput-formula-and-palette.md) |
| ADR-020 | 单管理员用户名密码 + JWT；API Key 可编辑标识 | [single-admin-jwt](./adr/ADR-020-single-admin-jwt.md) |
| ADR-021 | 自研控制面 + LiteLLM SDK 单次调用层 | [self-hosted-control-plane-litellm-call](./adr/ADR-021-self-hosted-control-plane-litellm-call.md) |
| ADR-022 | 取消“公开模型”实体；`slug/call_model` 直连；自动组合查询期派生（旧文「隐式分组」，ADR-067 起改称） | [drop-public-model-entity](./adr/ADR-022-drop-public-model-entity.md) |
| ADR-023 | API 时间统一 UTC 毫秒整数；看板按浏览器 IANA 时区分桶 | [utc-millis-api-format](./adr/ADR-023-utc-millis-api-format.md) |
| ADR-024 | 渠道操作模式：请求头/体、响应头/体有限改写 | [rewrite-headers-and-body](./adr/ADR-024-rewrite-headers-and-body.md) |
| ADR-025 | 模型级周计划段 + 冷却 + 双计数熔断升级 | [model-weekly-schedule-breaker](./adr/ADR-025-model-weekly-schedule-breaker.md) |
| ADR-026 | 数据看板 + 使用日志的信息架构 | [dashboard-and-logs-ia](./adr/ADR-026-dashboard-and-logs-ia.md) |
| ADR-027 | 视觉系统 v2：对比度、状态表达、响应式 | [visual-system-v2](./adr/ADR-027-visual-system-v2.md) |
| ADR-028 | 四段延迟口径 + 成对样本 + 固定阈值 | [four-stage-latency](./adr/ADR-028-four-stage-latency.md) |
| ADR-029 | 改写动作模型 v2：9 动作 + 6 条件，不兼容旧规则 | [rewrite-actions-v2](./adr/ADR-029-rewrite-actions-v2.md) |
| ADR-030 | 上游传输改用原生 httpx，LiteLLM 只作协议参考 | [native-httpx-transport](./adr/ADR-030-native-httpx-transport.md) |
| ADR-031 | 前端按 as-built 收敛：Tailwind + 手写 UI，不引 shadcn/Radix/TanStack Table | [as-built-frontend-tailwind](./adr/ADR-031-as-built-frontend-tailwind.md) |
| ADR-032 | 日志只显示首字/首答/总；看板保留四段 | [logs-three-timestamps](./adr/ADR-032-logs-three-timestamps.md) |
| ADR-033 | 渠道多密钥 + 统一模型参数 | [channel-multi-key-and-params](./adr/ADR-033-channel-multi-key-and-params.md) |
| ADR-034 | LiteLLM SDK 回归默认传输，httpx 为回退（**默认臂已被 ADR-065 取代**，双轨形态保留） | [upstream-transport-dual-track](./adr/ADR-034-upstream-transport-dual-track.md) |
| ADR-035 | 生产化可靠性设施：共享连接池、四层超时、全局重试预算、SIGTERM 排空、DB 池、LiteLLM 预热 | [production-reliability-facilities](./adr/ADR-035-production-reliability-facilities.md) |
| ADR-036 | 传输层字节级修复上游 typed content parts | [typed-content-parts-bytes-fix](./adr/ADR-036-typed-content-parts-bytes-fix.md) |
| ADR-037 | 界面把 priority 呈现为「层」，不是序号 | [priority-presented-as-layer](./adr/ADR-037-priority-presented-as-layer.md) |
| ADR-038 | 模型目录与组合模型合并为「模型管理」一页两 tab | [model-management-two-tabs](./adr/ADR-038-model-management-two-tabs.md) |
| ADR-039 | 看板 P95 合计改为按请求数加权平均 | [dashboard-p95-weighted](./adr/ADR-039-dashboard-p95-weighted.md) |
| ADR-040 | 图表调色板 16 槽循环复用，取消「其他」聚合段 | [palette-16-slots-no-other](./adr/ADR-040-palette-16-slots-no-other.md) |
| ADR-041 | 只有模型连接探测成功才写回熔断 | [probe-success-only-updates-breaker](./adr/ADR-041-probe-success-only-updates-breaker.md) |
| ADR-042 | 可见状态独立出「半开」，共五态 | [visible-status-five-states](./adr/ADR-042-visible-status-five-states.md) |
| ADR-043 | 主密钥以 `channels.api_key_enc` 指向的行为准，不靠排序 | [primary-key-by-plaintext-match](./adr/ADR-043-primary-key-by-plaintext-match.md) |
| ADR-044 | 列表搜索按字面量匹配（`%` / `_` 转义） | [literal-search-escape](./adr/ADR-044-literal-search-escape.md) |
| ADR-045 | 语义色令牌化：徽标 / 浮层 / tooltip / 示例底色走 CSS 变量 | [semantic-color-tokens](./adr/ADR-045-semantic-color-tokens.md) |
| ADR-046 | 金额精度：成本类「≥2 位、≤6 位小数」不分档（2026-11-15 修订，取消原「≥ 0.01 美元固定 2 位」分档）；单价每百万 token 2–4 位不变，见 §9.2 | [usd-precision-adaptive](./adr/ADR-046-usd-precision-adaptive.md) |
| ADR-047 | 统计口径与 tablist 键盘逻辑各收敛到单一来源 | [single-source-aggregates-and-tablist](./adr/ADR-047-single-source-aggregates-and-tablist.md) |
| ADR-048 | 服务层不直接写 SQL；上游 HTTP 统一收口到 `l05_service/llm/gateway.py` | [no-sql-in-service-layer](./adr/ADR-048-no-sql-in-service-layer.md) |
| ADR-049 | 前端时间、计数、百分比格式化各只有一处实现 | [single-source-formatters](./adr/ADR-049-single-source-formatters.md) |
| ADR-050 | 封禁与启用是停用轴上的同一对操作（**措辞已被 ADR-070 修订**：封禁 = 永久、冷却 = 会自愈，两者不再是同一条轴的两种叫法） | [ban-and-enable-share-disable-axis](./adr/ADR-050-ban-and-enable-share-disable-axis.md) |
| ADR-051 | 流式终态按真实结局记账 | [stream-terminal-booking-by-outcome](./adr/ADR-051-stream-terminal-booking-by-outcome.md) |
| ADR-052 | 失败尝试在使用日志可见 | [failed-attempts-visible-in-logs](./adr/ADR-052-failed-attempts-visible-in-logs.md) |
| ADR-053 | 时间筛选只要预览、提交权归「搜索」，今天留 1 小时余量 | [time-filter-preview-search-commits](./adr/ADR-053-time-filter-preview-search-commits.md) |
| ADR-054 | 产品改名：驭流 / Slipstream | [rename-to-slipstream](./adr/ADR-054-rename-to-slipstream.md) |
| ADR-055 | 内部标识统一改名（slipstream / system） | [internal-identifiers-rename](./adr/ADR-055-internal-identifiers-rename.md) |
| ADR-056 | 用户密钥明文加密留存，可复制可重置 | [user-key-plaintext-encrypted-at-rest](./adr/ADR-056-user-key-plaintext-encrypted-at-rest.md) |
| ADR-057 | 观测写走零 SQL 管线：WAL 追加 + 后台批量落库 | [zero-sql-usage-pipeline](./adr/ADR-057-zero-sql-usage-pipeline.md) |
| ADR-058 | 默认关闭 LiteLLM 的每请求成本计算 | [litellm-cost-calc-off](./adr/ADR-058-litellm-cost-calc-off.md) |
| ADR-059 | 用 PostgreSQL 取代 SQLite 单写者 | [sqlite-single-writer-to-postgres](./adr/ADR-059-sqlite-single-writer-to-postgres.md) |
| ADR-060 | 多实例形态：独立进程 + 每副本各自本地库 + 零 SQL 管线 | [multi-instance-shape](./adr/ADR-060-multi-instance-shape.md) |
| ADR-061 | 生产单源部署：网关直接托管前端构建产物 | [single-origin-web-dist](./adr/ADR-061-single-origin-web-dist.md) |
| ADR-062 | UI 组件继续 Tailwind + 自建基元，补 token 与统一基元，不引入 antd（**被 ADR-064 取代**） | [ui-component-strategy](./adr/ADR-062-ui-component-strategy.md) |
| ADR-063 | UI 缺口控件改用 shadcn registry 源码（Radix 底座），不再手写（**被 ADR-064 取代**） | [ui-gaps-from-shadcn](./adr/ADR-063-ui-gaps-from-shadcn.md) |
| ADR-064 | 控件层全量迁到 shadcn copy-in（Radix 底座）+ Tailwind 4，设计规范仍自建 | [full-shadcn-migration](./adr/ADR-064-full-shadcn-migration.md) |
| ADR-065 | 默认上游传输改为 httpx，LiteLLM 降为全局开关（无渠道级覆盖） | [default-transport-httpx](./adr/ADR-065-default-transport-httpx.md) |
| ADR-066 | shadcn token 语义与组件底座对齐（muted=弱表面、圆角两套标尺同值并存、取 new-york v3 源码、哪些件不取） | [token-semantics-adoption](./adr/ADR-066-token-semantics-adoption.md) |
| ADR-067 | 条目二元化（直连 / 组合），priority 数值越大越先（方向翻转） | [entry-kind-and-priority-flip](./adr/ADR-067-entry-kind-and-priority-flip.md) |
| ADR-068 | 缓存命中率四分口径 + 跨层会话亲和 + 配置变更 epoch 失效 | [cache-metrics-and-affinity](./adr/ADR-068-cache-metrics-and-affinity.md) |
| ADR-069 | 日志三视图（逐次请求 / 按组合 / 按状态）：筛选作用事实层、聚合只改展示 | [log-views-fact-and-presentation](./adr/ADR-069-log-views-fact-and-presentation.md) |
| ADR-070 | 可用性二分：**封禁**（永久、不广播、直连 404）/ **冷却**（到期自愈、仍广播、直连 503 + `Retry-After`），熔断与故障归入冷却；排期规则存 `channel_models.schedule_json` v2，`banned_until` 不改名（status: accepted，§7 的 5 个决策点已按推荐项落地） | [scheduled-cooldown](./adr/ADR-070-scheduled-cooldown.md) |
| ADR-071 | 日志搜索上 FOFA 风格查询语法，编译成 SQLAlchemy 表达式：严格模式不回退、17 字段 / 9 操作符 / 上限 512 字符·20 谓词·8 层；筛选控件是语法的输出方 | [search-query-language](./adr/ADR-071-search-query-language.md) |
| ADR-072 | 尝试从 `attempts_json` 行化为 `attempts` 表（唯一键 `(log_id, seq)`、三阶段灰度 `ATTEMPTS_WRITE`/`ATTEMPTS_SOURCE`）：读 9–12× 变一次 `EXISTS`、写 +1.24 ms/请求、空间 −33% | [attempts-rowification](./adr/ADR-072-attempts-rowification.md) |
| ADR-073 | 模型默认参数（价格 / 上下文上限 / 能力位）每日自 models.dev 同步：本地时区 00:00 后首次使用惰性触发一次、只按 `model` 匹配（7 级管线 + 别名表 + 模糊复核）、T1/T2/T3 分层 + 字段级合并、人工值按**值比对**保护（与上次自动写入值相同才更新，不引入 `source` 字段）、上限与能力位取 max / 并集、价格取中位数、组合取成员并集；派生默认值落新表 `model_defaults`、触发挂目录列表（触发判定按 `last_attempt`，`last_pull` 仅成功 / 304 时更新）、阈值与 `last_attempt` / `last_pull` 落 `app_settings`（2026-11-16 裁定 D1/D3/D4）（status: accepted，已实现 2026-11-16） | [daily-model-parameter-sync](./adr/ADR-073-daily-model-parameter-sync.md) |
| ADR-074 | 头透传语义：出站以客户端请求头为基线（剥传输层头 / `host` / `content-length` / 客户端凭据），网关覆盖协议头并按 `auth_style` 写入渠道密钥，渠道 `request_headers` 规则最后叠加（且不得改写凭据与网关身份/协议头：`PROTECTED_HEADERS`，2026-11-15 裁定）；`x-request-id` 客户端传了就沿用、响应头恒为网关 id；入站上游头原样转发（剥传输层 / `content-length` / `content-encoding`），网关必需头最后覆盖并大小写不敏感去重（status: accepted，实现先行随本批提交转正） | [header-passthrough](./adr/ADR-074-header-passthrough.md) |

### 13.1 为什么传输方案反转了两次

完整过程已迁到 ADR 文件：[`docs/adr/ADR-034-upstream-transport-dual-track.md`](./adr/ADR-034-upstream-transport-dual-track.md) 的「背景」「备选与否决原因」两段记录了 ADR-021 → ADR-030 → ADR-034 的两次反转。这里只留结论：**反转是因为「改写能力」与「精确透传能力」分别落在不同的传输层**，双传输是同时保住两者的折中，不是又一轮摇摆。2026-11-14 的 ADR-065 只换了**默认臂**（LiteLLM → httpx），双轨形态与这条结论都不变，不算第三次反转。

---

## 14. 交付历史

| 阶段 | 内容 |
|---|---|
| 2026-11-11 MVP | 22 张票据：骨架/鉴权/DB、渠道与模型、组合与 priority 路由、failover/熔断、计划窗口、探测、请求/响应改写、日志、看板、密钥、设置、E2E 冒烟 |
| 2026-11-12 UX v2 | 渠道列表/详情合并、多密钥、模型参数统一、日志紧凑行与详情抽屉、改写编辑器 v2、看板分位与 16 色、响应式与视觉回归 |
| 2026-11-12 性能与传输 | 三栈对比与单实例阶梯压测，定位每请求新建 client 的固定开销并改为共享 client；LiteLLM 可行性验证 → 双传输实现 → usage/伪 finish/错误映射/断流/终态/时延修复；生产化：连接池、四层超时、全局重试预算、SIGTERM 排空、DB 池、冷启动预热 |
| 2026-11-12 文档 | 全部 Markdown 压缩到 `docs/ssot.md` + `docs/ssot-index.md`，删除其余文档与空目录（2026-11-14 起重新启用 `docs/adr/`，见 §16） |
| 2026-11-13 分层与口径 | 后端分层下沉：SQL 与条件更新收进 `l04_repo/repo.py`，上游 HTTP 只走 `llm/gateway.py` 的 `upstream_probe_*` seam；统计口径统一：看板/日志/日聚合/时序共用 `aggregates.usage_totals`，估算行不计 token 与成本；前端格式化单一来源：`formatDateTime` / `formatCount` / `formatPercent`，删除兼容别名；三处 tablist 统一用 `useRovingTabs`；文档全量核对后修正 17 处偏差 |

| 2026-11-14 ADR 化 | 决策从 §13 压缩表迁到 `docs/adr/`（一条决策一文件，格式见 ADR-000，ADR-001–062）；新增 `docs/INDEX.md` 按界面导航；本文收敛为机制与口径事实源 |

> 提交策略与工作树状态见 §15 第 1 条；逐个 commit 的历史由 `git log` 提供，不在此重复。

---

## 15. 仍待决事项

1. **提交策略**：`apps/` 删除与 `api/`/`web/` 新目录是整体一次提交，还是拆成“目录迁移”“LiteLLM 传输”“生产化”“文档清理”多个提交。
2. **生产组件**：是否在下一阶段引入 PostgreSQL、Redis、Prometheus/OTel、Alembic、多 worker；这会改变熔断状态与迁移方案。
3. **背压与限流**：是否增加单实例在飞流上限、按 key/模型限流、429/503 + Retry-After。
4. **就绪探针**：当前只有 `/health`；若部署平台要求 `/readyz`，需要新增 DB ping + 上游 client 就绪 + `StreamRegistry.closing` 检查。
5. **LiteLLM 私有协议边界**：是否对特定渠道强制 httpx，以保留未知字段/响应头删除能力。
6. **`.scratch/` 去留**：已加进 `.gitignore`（不进版本库）。目录里是迁移期的一次性报告、脚本与基准原始数据，保留仅供复查，可随时清理。
7. **视觉基线**：UI 改动后已重跑 Playwright 视觉回归，基线无漂移；后续 UI 改动仍需 `npm run test:visual`。
8. **日志深翻页**：日志列表已改为 25/50/100/200 分页（后端上限 200），尚未实现游标分页；单页 200 条以外的翻页仍受此上限约束。
9. **MAX_ATTEMPTS 配置项**：环境变量 `MAX_ATTEMPTS`（默认 3）在代码里没有任何读取点，组合轮数实际由 `combos.max_attempts` 列（默认 3、取值 1–50）决定；该配置是删除还是接到组合默认值上，待定。
10. **UI 迁移（已落地，ADR-064）**：控件层全量迁到 shadcn copy-in（Radix 底座）并把 Tailwind 升到 4；ADR-062 / ADR-063 的增量路线已被取代。终态门禁：`npm run lint:ui` = 0 error / 19 warning（19 全是 vendored 例外，`--max-warnings 19` 已封顶；文件数取当次 `--json`，不要照抄快照数）、`npm run build` 通过、单元 27 文件 214 条全绿、视觉 7 张基线全绿。现状台账见 `docs/ui-adoption-map.md`（§9 门禁阶梯、§10 门禁真实运行环境、§11 欠账表），界面规则唯一正文见 `docs/ui-standards.md`；升级步骤与旧台账已归档到 `docs/archive/`（只作证据、不作权威）。
11. **可用性二分的落地（ADR-070，status=accepted）**：术语已定（封禁 / 冷却，词义见 `GLOSSARY.md` 的 §4），实现已进工作树（跳过原因拆分、可见性、排期 v2），随本批提交；ADR-070 §7 的 5 个决策点（批量端点形态、`Retry-After` 取值、跳过原因码拆到什么粒度等）已按推荐项定下并落地，不再等拍板。
12. **日志查询语法接线（ADR-071）**：`l03_domain/query_lang/` 已合并并有单测，**未接线**到 `repo.py` / `l05_service/log_views.py`（避免与后端正在改的两处并行冲突）；接线时筛选控件从「拼查询参数」改为「拼语法」。
13. **`attempts` 行化实施（ADR-072）**：设计已定，等 `task-101`（冷却后端改 `repo.py` / routing）落地后再动，避免同文件互相覆盖；DDL、索引与三阶段开关见 §7。
14. **模型默认参数同步（ADR-073，status=accepted）**：已实现（2026-11-16，task-148 步骤①–⑤；端点 `GET /api/model-defaults/status`、`POST /api/model-defaults/refresh`、`POST /api/channel-models/prefill`；`POST /api/channel-models/{id}/adopt-defaults` 已随 task-150 落地（2026-11-16））。数据源 models.dev `api.json`（MIT）；刷新是「本地时区 00:00 后第一次需要参数时触发一次」，不是定时任务；人工值按**值比对**保护（当前值 == 上次自动写入值才更新，不同则保留；不引入 `source` 字段）；能力位纳入自动预填与更新，上限与模态取 max / 并集、价格取中位数；models.dev 价格只作默认值、不作计费结算。**D1/D3/D4 已于 2026-11-16 全部裁定**：派生默认值落新表 `model_defaults`（`fields_json` 容器，真列 `model_id` unique / `matched_by` / `pulled_at`，另存 `providers_json` / `rule_version`）；惰性触发挂**模型目录列表读取**（表单打开不触发、刷新后调 `catalog.mark_dirty()`）；模糊阈值与 `last_attempt` / `last_pull` 落 `app_settings` 单 key（默认 0.92 精确 / 0.75 候选；触发判定看 `last_attempt`，任何尝试都写，失败当天不重试）。`parameters_json` 扩展键范围已于 2026-11-15 裁定为「能力位纳入」。见 ADR-073 的「裁定表」。

---

## 16. 文档治理

- **文档职责边界**：`docs/ssot.md` = 机制与口径的事实源；`docs/adr/` = 决策与理由（一条决策一文件，格式见 `docs/adr/ADR-000.md`）；`docs/INDEX.md` = 按界面导航（每个页面 → 接口 / ADR / ssot 章节 / 测试）；`docs/GLOSSARY.md` = 词义表；`docs/ssot-index.md` = 总入口（只指路，不承载事实）。允许存在的旁支文档仅两类：**实测证据**（`docs/PERFORMANCE_REPORT.md`、`docs/testing.md`）与**操作手册/专项说明**（`docs/upstream_transport.md`、`docs/postgres.md`）——它们只放数字、命令与差异，机制与配置仍以本文件为准，冲突时以代码与测试为准并回头修它们。
- 改「词义」先改 `GLOSSARY.md`，改「行为/字段/参数」改本文件；两处冲突以本文件为准并回头修词义表。
- **计数纪律**：不记录随手就变的数字——用例数、测试文件数、通过数、耗时秒数、构建产物体积。事实源只写「全量套件最近一次实跑通过」这类定性结论；具体数字与复现命令放 `docs/testing.md`（测试实测）与 `docs/PERFORMANCE_REPORT.md`（性能测量）。交付历史（§14）里的当日数字保留为历史事实，但不得当作当前基线。
- 代码变更必须同步更新本文件的相关章节；测试基线变化必须更新 §10；配置新增必须同步 `api/.env.example` 与本文件 §8。
- 历史文档（`docs/00`–`08`、`docs/agents/`、`docs/archive/`、`docs/research/`、`docs/specs/`、根 `README.md`、`AGENTS.md`、`web/README.md`）已删除；其结论已压缩进本文件。`docs/adr/` 于 2026-11-14 重新启用，但用途收窄为「一条决策一文件」，只收录值得单独解释的决策；`GLOSSARY.md` 于 2026-11-12 按用户要求重建为纯词义表（见上一条）。
- `.scratch/` 下的基准 JSON/脚本是原始证据，不是事实源；与本文件冲突时以本文件为准。
- `/readyz` 事实：仓库从未实现，前端零引用；不要记录为“已删除”，当前只有 `/health`。
