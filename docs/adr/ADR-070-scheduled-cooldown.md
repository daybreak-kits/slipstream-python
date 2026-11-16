# ADR-070 调度式冷却：规则数据模型与运行时语义

---
status: accepted（实现先行，随本批提交转正；2026-11-15 由 proposed 转正）
date: 2026-11-15
related: ADR-025（模型级周计划段 + 冷却 + 双计数熔断升级）、ADR-050（封禁与启用是停用轴上的同一对操作）、ADR-067（条目二元化 + 优先级翻转）、ADR-059（SQLite 单写者 → Postgres）、ADR-069（日志三视图）
supersedes: ADR-050 中「封禁 = 置停用快捷词」的措辞（历史 ADR 原文保留，本条按新口径叠加）
related_docs: `docs/ssot.md` §3.1 术语表（:118-121）、`docs/GLOSSARY.md` :61-66、`docs/scratch/terminology-audit.md`（task-100 只读审计，:5 记录用户裁定）
---

## 背景

1. **用户已裁定术语**（task-100 审计原文，`docs/scratch/terminology-audit.md:5`）：**封禁（ban）= 永久关闭，不出现在 `/v1/models`；冷却（cooldown）= 定时自动恢复，仍出现在 `/v1/models`；熔断与上游故障都属于「冷却」，是自动触发的档位。**
2. **现状与裁定有三处不一致**（审计 `2.` 节）：
   - 定时冷却期间直连条目从 `/v1/models` 消失（`api/src/l05_service/catalog.py:141` → `api/src/l06_api/routers/v1.py:83` → `catalog.py:451`），与「冷却仍出现」相反；
   - 同一状态在两个面上相反：直连消失、自动组合留着但不可用（`api/src/l05_service/groups.py:79-82`）；
   - **成员全被封禁的组合仍被 `/v1/models` 广告**（`catalog.py:193-198` 只看 `combo.enabled` + 目标数 ≥ 1）。
3. **调度形态要扩**：现在是「定时封禁固定 1 小时 / 24 小时 / 7 天」三档（`web/src/components/catalog/availability-control.tsx:52-55`）+ 「每周计划段」单形态（`api/src/l03_domain/schedule.py:25-37`）。用户要求每个**上游模型**可挂**多条规则**（OR 关系）：① 每天 HH:MM–HH:MM；② 每周[周一…周日子集] HH:MM–HH:MM；③ 每月[第 N 天子集] HH:MM–HH:MM；④ 永久封禁；支持跨天窗口（22:00–02:00）；批量可一次给多个模型设同一套规则。
4. **范围约束**：定时启停只对**上游模型（channel_model）**有意义，**组合不做定时**；本文只写设计，不改代码。

## 决策摘要（TL;DR）

1. 三类「不可用」统一成**一条轴 + 三个来源**：**人工轴**（`enabled` + `banned_until`）、**时间轴**（排期规则，`schedule_json` v2）、**自动轴**（熔断 / 冷却升级，breaker 列）。**封禁 = 人工轴且无到期；冷却 = 有到期或会自愈**（三种来源都算）。
2. **规则存 `channel_models.schedule_json`（JSON 列升 v2），不新建表**（选型理由与代价见 §1.2）。
3. 规则是**本地墙钟 + IANA 时区**；求值是**纯函数**（rules × now），**不引入后台定时任务**；解析结果随行快照缓存（§2）。
4. **永久封禁不写进规则集**——它是状态位不是时间轴规则，写进规则集会让它覆盖一切且无法被其它规则覆盖（§1.1）。批量接口里它是 op 不是 rule kind。
5. 路由侧把「封禁 / 冷却 / 冷却窗口」拆成**不同跳过原因**（今天共用 `MODEL_DISABLED`，日志里分不出来）；冷却类直连调用返回 **503 + Retry-After**。
6. 批量走**一个 PATCH 批量端点**（幂等、逐条结果、单事务），前端从串行循环切过来。
7. 5 个待拍板点见 §7；改动点清单见 §8。

## 1 数据模型

### 1.1 三类不可用的统一词表与存储落点

| 概念 | 触发源 | 存储 | 自动恢复 | 出现在 `/v1/models` |
|---|---|---|---|---|
| **封禁 Ban** | 人工 | `enabled=false` 且 `banned_until IS NULL` | 否（只认手动「启用」） | **否** |
| **定时冷却 Timed Cooldown** | 人工设到期 | `enabled=false` 且 `banned_until = T` | 是（到点惰性判活） | **是** |
| **排期冷却 Scheduled Cooldown** | 人工设规则 | `schedule_json.rules[]` 命中窗口 | 是（出窗即恢复） | 是 |
| **熔断 / 升级冷却** | 上游失败 | `health_status` + `cooldown_until` / `fault_until` | 是 | 是 |

判据唯一且现成：**`banned_until` 有无**就是「封禁 vs 定时冷却」的分界（task-100 `3` 已下此结论），因此**不新增状态列、不改字段名**（`banned_until` 与 breaker 的 `cooldown_until` 会撞名，见审计 `3` 第 1 条）。永久封禁与定时冷却的唯一差别是**是否出现 `/v1/models`**，不是存储。

### 1.2 规则存哪：新表 vs JSON 列

**推荐：沿用 `channel_models.schedule_json`，把文档形状升到 v2；不新建表。**

理由：

1. **求值永远是「某一行此刻是否在冷却」**，没有任何「按规则反查模型」的查询需求（管理目录本来就是逐行取全量再拼装：`catalog.py:136`、`combos.py:319-323`）。
2. **热路径已经把行读进内存**（路由侧 catalog snapshot，`routing.py:276-278`）。规则跟着行进内存 → 求值零额外 SQL；新表则要么每次求值多一次查询，要么在 snapshot 之外再拼一次映射，等于把「行 → 规则」的关联再存一遍。
3. **生命周期 1:1**：规则只属于一个模型行，删模型即删规则，存列没有孤儿行、没有级联删除的额外路径（对比 `combo_targets` 有 `channel_model_id` 外键与 `ondelete` 处理）。
4. **SQLite 单写者**（ADR-059）：新表意味着批量写规则时多一张表的写事务；JSON 列是**整份覆盖写**，批量 N 个模型 = N 行 UPDATE，一次提交。
5. **零迁移**：既有 `schedule_json` 里已有线上数据（v1 形状），向前兼容解析即可（§1.3）。

否决：**新表 `channel_model_cooldown_rules`**（一行一规则、更规范、可索引、可审计）——收益（跨行查询、审计流水）与本场景的诉求不匹配，代价（新表 + 迁移 + 热路径关联 + 双写）明确更高。**若将来出现「按规则批量停用」「排期看板」这类跨行需求，再引入表，届时 JSON 列降级为读取缓存。**

**索引**：JSON 列无法建索引，但本设计**不需要索引**——冷却判定不在 SQL 里做（§2.2），没有 `WHERE cooldown` 这类过滤。

### 1.3 规则文档形状 v2

```json
{
  "version": 2,
  "timezone": "Asia/Shanghai",
  "rules": [
    {"kind": "daily",   "start": "22:00", "end": "02:00"},
    {"kind": "weekly",  "days": [1, 3, 5], "start": "09:00", "end": "18:00"},
    {"kind": "monthly", "days_of_month": [1, 15, 28], "start": "00:00", "end": "06:00"}
  ]
}
```

- `kind`：`daily` | `weekly` | `monthly`。**daily ≡ weekly(days=[1..7])**，解析时统一归一为 weekly 全周，wire 格式保留 `daily` 只为让 UI 与用户心智一致。
- `weekly.days`：ISO 1–7（周一=1），**沿用 v1 语义**（`schedule.py:50-58` 已按 1–7 过滤）。
- `monthly.days_of_month`：1–31（1 = 当月 1 日）。
- `start` / `end`：`"HH:MM"` **本地墙钟**；`end <= start` → 跨天窗口（§1.5）。
- **永久封禁不在 rules 里**（§1.1）。空规则集 = 永不冷却。
- **v1 兼容**：`{"segments": [{"days": [...], "start": "...", "end": "..."}]}` 解析为等价的 weekly 规则集合（多段 → 多规则）。旧数据**零迁移**；写回时统一输出 v2（`channel_models.py:186` 的 `_schedule_json` 负责规范化输出）。

### 1.4 时区

- **存本地墙钟 + IANA 时区名，不存 UTC 时刻**。理由：规则语义是「每天 22:00–02:00」这种墙钟语义，转成 UTC 会随夏令时漂移（用户写一次、每年偏移一小时）。
- **每模型一个时区**（沿用现状 `schedule_json.timezone`，缺省 `DEFAULT_TZ = "Asia/Shanghai"`，`schedule.py:8`）。**不做到每规则一个时区**：一个模型的所有窗口用同一时区才符合「这个上游模型在本地夜里不接流量」的直觉；跨时区团队用批量接口分批设置即可。
- 换算在**读取时**用 `zoneinfo`（服务端，`schedule.py:20-22`：时区名未知则回落 DEFAULT_TZ，不让请求失败）。
- 客户端展示沿用同一时区；前端已有「保存后按 X 换算执行，界面上填写的时间均为该时区当地时间」的提示（`web/src/components/channels/channel-model-form-dialog.tsx:640-641`）。
- **批量**：一次批量共享一个时区（请求体给 `timezone`），覆盖被选模型的既有时区；缺省用 `DEFAULT_TZ`。

### 1.5 跨天窗口与归属日

- `end <= start` → 结束时刻落在**次日**，窗口**归属起始日**（沿用 `schedule.py:70-87` `_segment_bounds` 的既有语义，ADR-025 原文亦记「跨午夜归起始日」）。
- 因此「周日 23:00–01:00」的窗口在**周一 00:30 仍然命中**（属于周日的窗口）；月度同理：「2 月 1 日 22:00–02:00」覆盖到 2 月 2 日 02:00。
- 求值时对「当前时刻」与「窗口起点」做**本地时间比较**，不用 UTC 差值。

### 1.6 月度规则的月末与闰日

- 当月不存在的日（`days_of_month` 含 29/30/31 而当月没有）→ **该月这次跳过**（推荐，见 D4）；闰年 2 月 29 日命中、平年跳过。
- 理由：钳到月末会悄悄改写语义（「每月 31 日 22:00 停机」在 2 月变成「28 日」），用户看不出来但行为变了。

### 1.7 多条规则的求值顺序与幂等

- **规则之间是并集（OR）**：落在任一规则窗口内 = 冷却中。
- **求值顺序不影响结果**（并集与顺序无关）→ 幂等；不引入「更严者优先」「后写覆盖」。
- 重叠窗口的**恢复时刻**取**所有覆盖窗口的最晚结束时刻**（不是第一个结束时刻）。
- 规则之间**不要求互斥**，重叠合法；「同 kind 同 start/end 但 days 不同」合法（等价于多段）。
- **写入是整份文档覆盖**，不做增量 → 同参数重复提交得到同结果（幂等）。
- 批量与单条写同一模型时以**最后一次写入为准**（覆盖语义），批量内同一模型重复出现只取一次。

## 2 求值路径

### 2.1 纯函数求值（无后台定时任务）

判定是 **rules × now → bool** 的纯函数，无副作用、无写、无定时器：

```python
# api/src/l03_domain/schedule.py
def parse_schedule(raw: Any, default_tz: str = DEFAULT_TZ) -> Schedule: ...   # v1/v2 兼容
def is_offline(now_ms: int, schedule: Schedule) -> bool: ...                  # 现状 :102
def next_available_ms(now_ms: int, schedule: Schedule) -> int | None: ...     # 现状 :109
def current_window(now_ms: int, schedule: Schedule) -> Window | None: ...     # 新增：当前窗口 (start_ms, end_ms)
```

```python
# api/src/l05_service/channel_models.py
def is_published(model: ChannelModelORM, at_ms: int) -> bool: ...            # 新增：是否出现在 /v1/models
def cooldown_reason(model: ChannelModelORM, at_ms: int) -> str | None: ...    # 新增：封禁/冷却/窗口
```

**不引入后台定时任务的可行性边界**：不引入定时器就不会有「机器休眠 / 进程重启 / 夏令时切换后要补跑」的问题，因为判定永远按当前时刻重算；代价只有两点——①「已到点恢复」在目录里的**可见延迟 = 目录缓存 TTL**（秒级，`catalog.py:232-247`）；②做不了「提前 5 分钟提醒」这类需要提前量的事件（本期不需要）。

### 2.2 行级 SQL 形状与取值

**取数 SQL 形状不变**：`repo.list_all_channel_models` 已取全列，`schedule_json` / `banned_until` / `cooldown_until` 都在行上（`api/src/l04_repo/orm.py:131-152`），**不新增 SQL、不新增 JOIN**。

**不在 SQL 里过滤冷却**。SQLite 侧要做「窗口成员判定」得把规则展开成时间区间再 JOIN，代价远高于 Python 侧逐行判定；本设计没有「只取可调用模型」这种 SQL 过滤需求——目录是全量取、Python 侧拼装（`catalog.py:250-273`）。

### 2.3 缓存：按需求值 + 快照

- **路由热路径**：候选行已在 catalog snapshot 里（`routing.py:276-278`）。**现状每次判定都 `json.loads(schedule_json)` + `parse_schedule`**（`routing.py:185-192`）——扩展到多规则后这会成为真正的成本点。**要求：快照里缓存解析后的 `Schedule` 对象**（或按 `schedule_json` 原文做 key 的小 LRU），判定时零解析。
- **`/v1/models` 侧**：目录已有短 TTL 的进程内缓存（`catalog.py:239-247`、`api/src/l02_infra/config.py:87` 注释），冷却状态随 TTL 刷新，秒级延迟可接受。

### 2.4 每请求开销上限

- 每请求判定次数 = **候选数 N × 规则数 R**（每条规则 1 次区间比较 + 1 次日期取整）。
- N 上界 = 组合成员数（现状个位数～几十）；R 建议上限 **10 条/模型**（超出在写侧拒绝）。
- → 每请求 ≤ 几百次整数/时间比较，**可忽略**；真正的成本是 JSON 解析，由 §2.3 的快照缓存消除。
- 结论：**不引入后台定时任务**的可行性成立，且不需要「把冷却状态冗余成列」。

## 3 对外行为矩阵

| 状态 | 路由候选选择 | `/v1/models` 广播 | 直连调用（HTTP + 错误码 + Retry-After） | 组合广告 |
|---|---|---|---|---|
| **永久封禁**（`enabled=false`, `banned_until IS NULL`） | 跳过，原因 `MODEL_BANNED`（新） | **不出现** | 404 `MODEL_NOT_FOUND`（维持现状 `routing.py:157-158` → `:87`） | 该成员不算可用；**全部成员永久封禁的组合不广告**（修 `catalog.py:193-198`） |
| **定时冷却**（`enabled=false`, `banned_until=T`） | 跳过，原因 `MODEL_COOLDOWN`（新）；到点惰性恢复（`channel_models.py:212-223`） | **仍出现**（改 `catalog.py:141`） | 现状 404 → **建议 503 + Retry-After**（见 D3） | 成员算「已发布」，组合照常广告 |
| **排期窗口冷却**（规则命中） | 跳过，原因 `COOLDOWN_WINDOW`（新，现状 `SCHEDULED_OFFLINE`） | 出现（现状即如此） | 503 `MODEL_SCHEDULED_OFFLINE` + `details.next_available_at`（`routing.py:99-110`）；**建议补 `Retry-After`**（§3.1） | 成员不可用但组合仍广告（现状） |
| **熔断冷却**（`health_status=open`） | 跳过，原因 `CIRCUIT_OPEN`（现状） | 出现（现状） | 503 `NO_AVAILABLE_CHANNEL`（现状） | 同上 |
| **冷却升级**（`fault`） | 跳过，原因 `CIRCUIT_FAULT`（现状） | 出现（现状） | 503 `NO_AVAILABLE_CHANNEL`（现状） | 同上 |
| **渠道封禁**（`channels.enabled=false`） | 跳过，原因 `CHANNEL_DISABLED`（现状） | 直连条目不出现（现状 `catalog.py:138`） | 404 `MODEL_NOT_FOUND`（现状） | 全部成员渠道封禁的组合不广告（同上修复） |

### 3.1 Retry-After 的现状缺口

- 错误响应头只在 `details.retry_after_s` 存在时才发 `Retry-After`（`api/src/l03_domain/errors.py:16-25`）。
- `scheduled_offline_error` 只给了 `details.next_available_at`（毫秒绝对时刻，`routing.py:99-110`）→ **今天排期窗口的 503 不带 Retry-After**。
- 建议：冷却类错误统一附 `retry_after_s = ceil((next_available_at - now)/1000)`，客户端才能按标准退避。
- **实现态**（task-125）：只在能算出恢复时刻时发 `Retry-After`（`api/src/l06_api/errors.py` 的 `_retry_after_headers`）；算不出（`next_available_at=None`，如窗口覆盖整个视野）就**不发**头，避免客户端按 1 秒无限重试；文案按 `HORIZON_DAYS` 改成「未来 62 天内没有可恢复的时间」，不再是「本周」。

### 3.2 顺带修掉「成员全被封禁的组合仍被广告」

- 现状：`catalog.py:193-198` 只判断 `combo.enabled` + `combo_target_counts >= 1` → 成员全被封禁的组合照样进 `/v1/models`。
- 修法：组合「可广告」= 组合启用 **且** 至少有一个**已发布**（非永久封禁）的叶子成员。判定复用现成的 `combos._has_available_leaf`（`combos.py:281-315`）但按新口径区分「已发布」与「此刻可调用」：
  - **冷却中的成员算「已发布」**（按裁定「冷却仍出现在 /v1/models」），只影响路由可用性；
  - **永久封禁的成员不算**。
- 直连侧同口径：`catalog.py:141` 的过滤条件从「`is_model_enabled` 为假就跳过」改成「**永久封禁**才跳过」。
- **隐式分组成员同口径**（task-125）：`api/src/l05_service/groups.py` 的行收集改用 `is_published`（冷却保留、永久封禁剔除），与 `repo.list_enabled_channel_models_by_call_model` 的 SQL 条件（`enabled` 或 `banned_until IS NOT NULL`）等价；两条路径不再各写一套判据。

## 4 批量与 API 形状

### 4.1 最小 API：一个批量 PATCH 端点

```
PATCH /api/channel-models/batch
{
  "model_ids": ["cm_a", "cm_b"],              // ≤ 200
  "op": "ban" | "unban" | "cooldown" | "schedule",
  "banned_until": "2026-11-20T02:00:00Z",    // op=cooldown 必填（ISO-8601）
  "schedule": {                               // op=schedule 必填
    "timezone": "Asia/Shanghai",
    "rules": [{"kind": "daily", "start": "22:00", "end": "02:00"}]
  }
}
→ 200
{
  "results": [
    {"model_id": "cm_a", "ok": true},
    {"model_id": "cm_z", "ok": false, "error": "MODEL_NOT_FOUND"}
  ],
  "succeeded": 1,
  "failed": 1
}
```

- **op=ban**：`enabled=false` + `banned_until=null`（永久）；**op=unban**：`enabled=true` 并清 `banned_until`（沿用现状 `channel_models.py:505-509`）；**op=cooldown**：`enabled=false` + `banned_until=T`（到期自愈）；**op=schedule**：整份覆盖写 `schedule_json`。
- **幂等**：同 `model_ids` + 同 op + 同参数重复提交 → 同结果、无重复副作用（覆盖式写；`banned_until` 过期时刻照写，读侧惰性判活）。
- **部分失败**：逐条独立，失败条目带原因，**不影响其它条目**；整体 **HTTP 200 + `results[]`**（不是 207：前端只要一个状态码 + 逐条结果）。
- **单事务**：N 行 UPDATE 一次提交（SQLite 单写者，ADR-059），不产生 N 次写事务。

### 4.2 与前端串行循环的取舍

- 现状前端是**单条 PATCH 循环**：`web/src/lib/catalog.ts:113-127` `setModelAvailability(modelId, {enabled, bannedUntil})` 每次一个模型，调用方逐行发起。
- 串行循环在 SQLite 单写者下的问题：N 次往返 + N 次写事务，批量 50 个模型就是 50 次事务。
- **推荐**：新增批量端点，前端把「批量封禁 / 批量冷却 / 批量排期」切过去；单行操作仍走现有 PATCH（`api/src/l06_api/routers/channel_models.py:23-36`），不强制迁移。
- 前端改动不在本任务范围内（本文只出设计）。

## 5 测试点清单

**A. 规则求值（纯函数，`tests/test_schedule.py` 已有周计划用例，扩写）**
1. daily 22:00–02:00 跨天：01:30 命中、02:00 出窗、21:59 不命中。
2. weekly 子集：周三命中、周四不命中。
3. monthly 第 15 天命中；第 31 天在 4 月命中、6 月（30 天）按 D4 结论跳过。
4. 闰日：2 月 29 日在闰年命中、平年跳过。
5. DST：`America/New_York` 春季跳变日窗口起点不存在 → 当日跳过；秋季回拨的重复时段按首次出现计一次。
6. 时区：同一 UTC 时刻在 `Asia/Shanghai` 与 `UTC` 下命中不同规则（证明按墙钟求值）。
7. 并集：daily ∪ weekly ∪ monthly 命中任一即冷却；**重叠窗口的恢复时刻取最晚结束**。
8. 空规则集 = 永不冷却；v1 文档（`{"segments": [...]}`）解析结果与等价 weekly 规则一致（旧数据零迁移）。
9. 幂等：重复写同一份文档解析结果相同；批量重复提交结果一致。
10. 跨天归属起始日：周日 23:00–01:00 的窗口，周一 00:30 仍命中。
11. `next_available_ms`：窗口内 → 返回窗口结束时刻（跨天正确）；窗口外 → 返回 now。

**B. 状态与路由**
12. 永久封禁：`/v1/models` 无该直连条目；组合全部成员永久封禁 → 组合不广告。
13. 定时冷却：`/v1/models` 仍有该条目；到期后路由恢复（惰性判活）。
14. 排期窗口命中：路由跳过原因 = 冷却窗口；直连调用 503 且 **带 `Retry-After` 头**、秒数合理。
15. 冷却中的成员仍让组合被广告（成员不可用 ≠ 组合消失）。
16. 日志侧：跳过原因在 attempts 里可区分封禁 / 冷却 / 窗口 / 熔断。

**C. 批量**
17. 批量封禁 3 个模型 → 3 行 `enabled=false`；重复提交幂等。
18. 批量部分失败：混入 1 个不存在的 `model_id` → `results` 里 `ok=false`，其余成功且已提交。
19. 批量排期：多个模型写入同一份规则，逐行解析一致。
20. 并发：批量与单条 PATCH 并发 → 最终状态一致（条件更新，不出现丢失更新）。

## 6 与现状的关系与迁移

### 6.1 既有列的演进

| 现状 | 演进 |
|---|---|
| `enabled`（bool，`orm.py:131`） | 语义不变：`false` = 人工轴关闭。**不新增状态列**。 |
| `banned_until`（ms，`orm.py:136`） | 语义不变但**口径改名**：null + enabled=false = 永久封禁；非 null = 定时冷却。字段名保留（改名会与 breaker 的 `cooldown_until` 撞名，task-100 `3`）。 |
| `schedule_json`（Text，`orm.py:138`） | 形状升 v2（§1.3）；**v1 兼容解析，存量数据零迁移**；写回统一输出 v2。 |
| `cooldown_json`（Text，`orm.py:139`） | 不动（熔断参数，`breaker.py:81-129`）。 |
| `health_status` / `cooldown_until` / `fault_until`（`orm.py:140-148`） | 不动（自动轴，熔断/升级）。 |

### 6.2 数据迁移策略

- **不迁数据**：v1 规则文档原地可解析；`enabled=false + banned_until=null` 的旧行按新口径**本来就是永久封禁**（语义不变）；旧的「定时封禁」行（`enabled=false + banned_until=T`）按新口径是定时冷却，**只是可见性变了**（要出现在 `/v1/models`）。
- 迁移只在 schema 层需要动时才有：**本设计不新增列、不新增表** → `l02_infra/db.py` 的 `_migrate_legacy_schema`（:184）/ `_migrate_postgres_schema`（:474）**无需改动**（若最终采纳新表方案，才需要在此加 create/ALTER）。
- **可见性改动是行为改动**（不是数据改动）：`catalog.py:141` 的过滤条件要按「永久封禁 vs 冷却」区分 → 需要回归测试钉住（见 §5 B 组第 13 条）。

### 6.3 改动点清单（file:line，只列设计涉及的位置）

| 位置 | 现状 | 需要做什么 |
|---|---|---|
| `api/src/l03_domain/schedule.py:25-37` | `Segment(days, start, end)` | 加 `Rule(kind, days, days_of_month, start, end)`；v1 → v2 归一 |
| `api/src/l03_domain/schedule.py:40-67` | `parse_schedule` 只认 segments | 支持 `rules[]`；daily → weekly 全周 |
| `api/src/l03_domain/schedule.py:70-87` | 跨天归起始日 | 沿用；月度同语义 |
| `api/src/l03_domain/schedule.py:90-99` | `_intervals` 扫 -1..+10 天 | **月度规则需要更长视野**（建议 -1..+62 天，或按规则分别算下一个起点），否则月度窗口算不出恢复时刻 |
| `api/src/l03_domain/schedule.py:102-120` | `is_offline` / `next_available_ms` | 扩到规则集；新增 `current_window` |
| `api/src/l05_service/routing.py:87` | `_not_found` → 404 | 维持（永久封禁对外就是不存在） |
| `api/src/l05_service/routing.py:99-110` | `scheduled_offline_error` 503 + `next_available_at` | 附 `retry_after_s`；错误码口径随 D3 |
| `api/src/l05_service/routing.py:185-205` | 每次 `json.loads` 解析 | 改用快照里的已解析 `Schedule`（§2.3） |
| `api/src/l05_service/routing.py:219-231` | `_static_unavailable`：`MODEL_DISABLED` / `SCHEDULED_OFFLINE` | 拆出封禁 / 冷却 / 窗口三态（D3） |
| `api/src/l05_service/routing.py:296-297, 329-330, 357-358, 388-389` | 复核时 `MODEL_DISABLED` | 同步拆态 |
| `api/src/l05_service/chat.py:223-229` | 跳过原因中文案表 | 加新原因文案；「停用」→「封禁」、「熔断」→「冷却中」（task-100 §1.2/§1.3 文案表） |
| `api/src/l05_service/chat.py:371-380` | 全员 `SCHEDULED_OFFLINE` → `scheduled_offline_error` | 条件随新原因改名 |
| `api/src/l05_service/channel_models.py:203-223` | `banned_until_ms` / `is_model_enabled` | 新增 `is_published` / `cooldown_reason`；旧函数保留 |
| `api/src/l05_service/channel_models.py:240-271` | `serialize_channel_model` | 暴露「封禁 / 冷却中 / 排期冷却」可读状态（前端展示用） |
| `api/src/l05_service/channel_models.py:186, 366-367, 513-517` | `schedule_json` 读写 | 升 v2 规范化 |
| `api/src/l05_service/catalog.py:141` | 冷却中条目从目录消失 | 改为「永久封禁才消失」（§3.2） |
| `api/src/l05_service/catalog.py:193-198` | 组合只看 enabled + 目标数 | 至少一个已发布叶子成员（§3.2） |
| `api/src/l05_service/catalog.py:451, 466` | `include_disabled` 透传 | 管理目录口径不变（管理面要看得见封禁项） |
| `api/src/l05_service/groups.py:79-82` | 自动组合：冷却中留着但不可用 | 口径已是「留着」，文案改「冷却中」 |
| `api/src/l05_service/combos.py:281-315` | `_has_available_leaf` | 区分「已发布」与「此刻可调用」 |
| `api/src/l06_api/routers/channel_models.py:23-36` | 单条 PATCH | 新增 `PATCH /api/channel-models/batch`（§4.1） |
| `api/src/l06_api/schemas.py:374-385` | `banned_until` 校验 | 新增批量请求体 schema；规则文档 schema（含 `kind` 枚举与 `HH:MM` 校验） |
| `api/src/l03_domain/errors.py:16-25, 50-58` | `_retry_after_headers` / `ErrorCode` | 冷却类错误码（D3） |
| `web/src/lib/catalog.ts:113-127` | 单条 PATCH 封装 | 加批量封装；串行循环切批量 |
| `web/src/components/catalog/availability-control.tsx:52-55` | 时长下拉 永久/1h/24h/7d | 拆「封禁（永久）」/「冷却（定时）」；新增排期入口 |
| `web/src/components/channels/channel-model-form-dialog.tsx:627-646` | 只有周计划段编辑 | 扩 daily / weekly / monthly 三种 kind |
| `web/src/pages/models.tsx:489-494` | 「已封禁」/「定时封禁至 …」 | 分「已封禁」/「冷却中 · 预计 … 恢复」 |
| `docs/GLOSSARY.md:61-66`、`docs/ssot.md:118-121` | 旧术语表 | 按 task-100 §4.2 的替换文本改（本任务只写文档，不改这两份） |

## 7 需要用户拍板的决策点

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| **D1** | 排期规则存哪 | A. `channel_models.schedule_json` 升 v2（推荐）<br>B. 新表 `channel_model_cooldown_rules`<br>C. 新表 + JSON 列缓存 | **A**。求值永远是行内判定、热路径已在快照里、新表带来迁移与双写成本且无跨行查询需求（§1.2） |
| **D2** | 批量冷却的到期语义 | A. 绝对时刻（ISO-8601，批量内同一时刻）（推荐）<br>B. 相对时长（自提交起 1h/24h/7d） | **A**。绝对时刻跨时区、跨 DST、跨机器时钟都确定；相对时长在批量里会让每个模型的到期时刻取决于各自的提交时间 |
| **D3** | 跳过原因 / 错误码是否细分 | A. 新增 `MODEL_BANNED` / `MODEL_COOLDOWN` / `COOLDOWN_WINDOW`（推荐）<br>B. 沿用 `MODEL_DISABLED` / `SCHEDULED_OFFLINE` 不变 | **A**。今天 `MODEL_DISABLED` 同时覆盖永久封禁与定时冷却，日志里分不出来（task-100 Q3）；代价是前后端 + 测试要同批改 |
| **D4** | 月度规则当月不存在的日 | A. 跳过该月这次（推荐）<br>B. 钳到月末 | **A**。钳位会悄悄改写语义（31 日 → 28 日），跳过是「那天不存在」（§1.6） |
| **D5** | 批量 API 形态 | A. 新增一个批量 PATCH 端点（推荐）<br>B. 保持单条 PATCH + 前端串行循环 | **A**。批量 N 个模型在 SQLite 单写者下是 N 次写事务（§4.2）；单行操作仍走现有 PATCH，不强制迁移 |

另有两个**已在本文定下、可推翻**的小口径：规则时区**每模型一个**（不做到每规则一个，§1.4）；排期**只对上游模型**有意义，组合不做定时（背景第 4 条）。

## 8 交付边界

- 本文件**只出设计**，不改任何代码、schema、数据库与前端；上面 §6.3 的清单是给实现任务的施工单。
- ADR 索引（`docs/ssot.md` §13 的 ADR 表、`docs/INDEX.md`）需在定稿后补一行 —— 本任务写作用域只含本文件，故未改。
- 与 task-100 的术语审计（`docs/scratch/terminology-audit.md`）同批口径：本 ADR 只**叠加**新口径，不改写 ADR-050 的历史原文。
