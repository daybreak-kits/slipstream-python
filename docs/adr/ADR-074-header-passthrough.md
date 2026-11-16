# ADR-074 请求 / 响应头透传语义

---
status: accepted（实现先行，随本批提交转正；2026-11-15 由 proposed 转正）
date: 2026-11-15
supersedes: 无（新增语义；ADR-024 / ADR-029 的改写规则 v2 头作用域继续有效，见 ssot §5.6）
related: ADR-024（改写规则 v2）、ADR-029（渠道级改写）、ADR-034（上游传输双轨）、ADR-065（默认 httpx 传输）
related_docs: `docs/ssot.md` §5.6、`docs/GLOSSARY.md` §8
---

## 背景

网关原先**自建一整套请求头**再发上游：客户端带来的自定义头、`user-agent`、追踪头一律丢弃，上游只看到网关造出来的最小头集（`content-type: application/json`、`accept`、`authorization`、厂商私有头按渠道硬编码）。用户裁定（m14646）改为「**以客户端请求头为基线 + 网关修改（结合密钥替换）**，原始头的自定义权交给用户」；响应侧同理——上游响应头原样转发，除非网关配置要改。

三条不能含糊的边界：

- **安全红线**：客户端凭据绝不能借透传之名到上游；上游只认渠道密钥。
- **协议与流式承诺**：`content-type`、流式的 `content-type: text/event-stream` / `cache-control` / `x-accel-buffering` 由网关说了算。
- **职责边界**：渠道改写规则（`channels.rewrite_rules_json.request_headers`）在网关自己的头之后叠加——规则能看到并改写客户端头，但改不回凭据。

本文按 task-144 的**已落地实现**写实（实现已在树上，测试 4 条），把语义固化下来，避免下一次改动把「基线」误读成「全量转发」。

## 决策

1. **出站基线 = 客户端请求头的副本**，剥三类头，其余原样保留。
   - 传输层头（hop-by-hop，`HOP_BY_HOP_HEADERS`，`api/src/l05_service/llm/gateway.py:101-112`）：`connection`、`keep-alive`、`proxy-authenticate`、`proxy-authorization`、`te`、`trailer`、`transfer-encoding`、`upgrade`——它们描述的是「这一跳」，换一跳就不成立。
   - `host`、`content-length`（`gateway.py:133`）：描述客户端→网关这一跳的报文。
   - 客户端凭据（`CLIENT_CREDENTIAL_HEADERS`，`gateway.py:115-117`）：`authorization`、`proxy-authorization`、`cookie`、`api-key`、`x-api-key`。
   - 过滤实现：`client_request_headers()`（`gateway.py:120-135`）。其余头（自定义 `x-*`、`user-agent`、追踪头、厂商私有头）原样到上游。
2. **网关随后覆盖自己拥有的头**（`build_headers()`，`gateway.py:138-162`）：`Content-Type` 强制 `application/json`（协议固定）；`Accept` 缺省补 `application/json`（客户端显式给了就保留）；凭据按渠道 `auth_style` 写入——`bearer` → `Authorization: Bearer <渠道密钥>`、`api-key` → `api-key: <渠道密钥>`、`x-api-key` → `x-api-key: <渠道密钥>` + `anthropic-version: 2023-06-01`（`gateway.py:152-159`）。
3. **`x-request-id` 保持现状语义**（本次裁定）：中间件 `request.state.request_id = request.headers.get("x-request-id") or new_request_id()`（`api/src/l07_bootstrap/app_factory.py:159`）——**客户端传了就沿用**（跨系统追踪连续性），没传才生成；网关出站把 `x-request-id` 写成这个 id（`gateway.py:160-161`，覆盖客户端头副本里的同名值）；**响应头恒为网关 request_id**（`api/src/l06_api/routers/v1.py:92` 非流式、`:64-72` 流式），上游回传的 `x-request-id` 一律被覆盖。**中间件不改**。
4. **渠道级头规则最后叠加**，复用既有 `channels.rewrite_rules_json.request_headers`（`set` / `delete` / `rename`，ssot §5.6）。出站顺序固定：**客户端基线 → 网关覆盖 → 渠道规则 → 上游**（`api/src/l05_service/rewrite.py:206-213`）。规则看到的基线里已经没有凭据，且凭据类头名受保护（`PROTECTED_HEADERS` = `authorization` / `api-key` / `x-api-key` / `cookie` / `set-cookie`，`api/src/l03_domain/rewrite.py:38-40`；`apply_header_rules()` 对受保护头的 `set` / `delete` / `rename` 一律跳过，`:399`、`:409`）——规则**改不回凭据**。**渠道规则不得触碰凭据、传输层头与网关身份 / 协议头（`content-type` / `content-length` / `host` / `x-request-id`）**：这四个头名与凭据同级由 `PROTECTED_HEADERS` 强制（2026-11-15 裁定「保护」；实现由 t5 落地并随本批提交）。
5. **入站响应头**：上游头**原样转发**，只剥传输层头、`content-length`、`content-encoding`（`UNSAFE_RESPONSE_HEADERS`，`api/src/l05_service/rewrite.py:19-32`；过滤在 `prepare_response()` 里做，`:246-302`，`:268` 与 `:295` 各过滤一次）；渠道 `response_headers` 规则在其后（`:273-277`）；**网关必需头最后覆盖，且大小写不敏感去重**（`_overlay_headers()`，`api/src/l06_api/routers/v1.py:17-22`）：上游的 `cache-control: max-age=999` 与网关的 `Cache-Control: no-cache` 只留一份，不会并存。流式固定 `content-type: text/event-stream`、`cache-control: no-cache`、`x-accel-buffering: no` 与网关 `x-request-id`（`v1.py:64-72`）；非流式的 `content-type` / `cache-control` 按上游。

### 顺序总表

| 方向 | 顺序 | 作用者 | 能改什么 |
|---|---|---|---|
| 出站 | 1 | 客户端头副本 | 一切非禁传头 |
| 出站 | 2 | 网关覆盖 | `content-type`、`accept`、`authorization` / `api-key` / `x-api-key`（+ `anthropic-version`）、`x-request-id` |
| 出站 | 3 | 渠道 `request_headers` 规则 | 除受保护头名外的任意头（凭据 / `content-type` / `content-length` / `host` / `x-request-id`；`set` / `delete` / `rename`） |
| 入站 | 1 | 上游响应头（剥传输层 / `content-length` / `content-encoding`） | — |
| 入站 | 2 | 渠道 `response_headers` 规则 | 同上；流式只改头不改体（`rewrite_skipped=stream`） |
| 入站 | 3 | 网关必需头 | 流式 `content-type` / `cache-control` / `x-accel-buffering` + 恒定的 `x-request-id` |

### 为什么不再沿用「网关自建头」

- **自定义权归用户**：网关自建头等于网关单方面决定上游能看到什么。用户想带 `x-my-tenant`、自研 SDK 的 `user-agent`、链路追踪 `traceparent` 时，只有「客户端头为基线」这一种写法不必每次改网关代码。
- **上游能识别客户端**：透传 `user-agent` 与追踪头后，上游 / CDN 的日志、风控、限流可以按客户端维度归因；自建头把这些信息抹平了（测试里 `user-agent: client-ua/1.0` 与 `x-custom-client: keep-me` 原样到达上游）。
- **避免「网关硬编码导致透传缺失」**：自建头是白名单式枚举，任何没被列进去的头都**静默丢失**，且丢失时没有任何报错——用户只能看到「上游没收到我要的头」。基线式透传的失败模式相反：默认保留，只有明确禁止的几类被剥掉（凭据 / 传输层 / `host` / `content-length`），语义可穷举、可测试。
- **代价**：禁传清单必须钉死（否则凭据泄露），响应侧必须大小写不敏感去重（否则 `max-age=999` 与 `no-cache` 并存，客户端与 CDN 各取一个）。两点都由测试锁住。

### 已知边界

1. **出站渠道规则不得改写网关身份 / 协议头（已裁定：保护）**：Lead 2026-11-15 裁定把这四个头名与凭据同级保护——`content-type` / `content-length` / `host` / `x-request-id` 进 `PROTECTED_HEADERS`（`api/src/l03_domain/rewrite.py:38-40`），渠道规则不得 `set` / `delete` / `rename` 它们；传输层头名同样不得触碰（它们本就不在出站基线里，这一条顺带关掉「规则新建 hop-by-hop 头」这条路）。此前这四个头对规则是开放的——规则在网关覆盖**之后**执行（`l05_service/rewrite.py:206-213`），现已关闭；代码侧由 t5 落地（扩展 `PROTECTED_HEADERS` + 加测试）并随本批提交。
2. **探针不参与透传**：`gateway.py:1061` / `:1159` / `:1842` 三处调用 `build_headers(channel)` 时不带客户端头，基线为空，探测仍是「网关自建」那套最小头集——探测不代表客户端，这是有意的。
3. **头名大小写**：httpx 会规范化头名大小写；比较、去重、保护名单一律按小写名做。

## 后果

- 客户端凭据永不出网关（安全红线），渠道密钥按 `auth_style` 写入；`auth_style` 由连接探测落库（ssot §5.5）。
- 上游看到的 `user-agent` 是客户端的、不是网关的——归因与限流口径随之改变（这是想要的，但要知道）。
- `content-length` 永不出站、也永不回客户端（流式是分块，响应体长度由网关自己算）。
- 日志仍写脱敏后的请求 / 响应头（ssot §5.7），透传不改变脱敏口径。
- 渠道改写规则的 `request_headers` 现在可能改到透传进来的客户端头（以前这些头根本不在基线里），规则作者的预期需要对齐；但凭据与网关身份 / 协议头对规则关闭（决策 4）。

## 备选与否决

| 备选 | 否决原因 |
|---|---|
| 继续用网关自建头（白名单枚举） | 用户要的 `x-*` / `user-agent` / 追踪头要靠改网关代码才能带上；漏掉的头静默丢失，排查成本高 |
| 出站「全量转发，只剥传输层头」 | 客户端凭据会到上游——越权；`cookie` 一起走更是数据外泄 |
| 出站「白名单式透传」（只放行已知前缀） | 退回白名单枚举的老问题：厂商私有头进不来，规则作者无从下手 |
| 入站把网关必需头与上游头**合并**而非覆盖 | `max-age=999` 与 `no-cache` 并存，客户端与 CDN 各取一个，行为不可预期 |
| 每跳都换一个新的 `x-request-id` | 跨系统追踪断链；外部系统日志与客户端带来的 id 对不上（裁定保留现状语义） |

## 测试点

`api/tests/test_header_passthrough.py`（4 条用例，随 task-144 实现落地）：

- `:92` 纯函数过滤：传输层头 + `host` / `content-length` + 全部凭据头被剥，自定义头与 `user-agent` 留下。
- `:115` 出站基线：客户端自定义头与 UA 原样到上游；`cookie` / `x-api-key` / `api-key` 不在上游；上游看到的 `authorization` 是渠道密钥；hop-by-hop 不转发；出站 `x-request-id` 与响应头一致。
- `:155` `auth_style` 三分支：`bearer` / `api-key` / `x-api-key`（附 `anthropic-version: 2023-06-01`）各自写对头。
- `:188` 入站：上游头原样转发（`x-upstream-trace`、`cache-control: max-age=999`）；网关 `x-request-id` 覆盖上游 `upstream-rid`；流式下 `cache-control` 变成 `no-cache`、`x-accel-buffering: no`、不转发 `content-length`，且大小写不同的重复头不并存。
- 渠道规则改 `content-type` / `content-length` / `host` / `x-request-id` / 凭据类头名一律不生效（`PROTECTED_HEADERS`，2026-11-15 裁定；测试随 t5 实现落地）。

## 实现锚点

| 位置 | 作用 |
|---|---|
| `api/src/l05_service/llm/gateway.py:101-112` | `HOP_BY_HOP_HEADERS` |
| `api/src/l05_service/llm/gateway.py:115-117` | `CLIENT_CREDENTIAL_HEADERS` |
| `api/src/l05_service/llm/gateway.py:120-135` | `client_request_headers()`：出站基线过滤 |
| `api/src/l05_service/llm/gateway.py:138-162` | `build_headers()`：网关覆盖 + 凭据替换 + `x-request-id` |
| `api/src/l05_service/llm/gateway.py:1213` | 上游响应头采集 `dict(response.headers)` |
| `api/src/l03_domain/rewrite.py:38-40` | `PROTECTED_HEADERS`（凭据类头名；另含 `content-type` / `content-length` / `host` / `x-request-id`，2026-11-15 裁定，t5 实现） |
| `api/src/l03_domain/rewrite.py:381-413` | `apply_header_rules()`：大小写不敏感 + 保护受保护头名（凭据 / 协议头 / 身份头） |
| `api/src/l05_service/rewrite.py:19-32` | `UNSAFE_RESPONSE_HEADERS`（入站禁转） |
| `api/src/l05_service/rewrite.py:186-243` | `prepare_request()`：基线 → 网关覆盖 → 渠道规则 |
| `api/src/l05_service/rewrite.py:246-302` | `prepare_response()`：入站过滤 + 响应规则 |
| `api/src/l05_service/chat.py:830`、`:1390` | 把客户端请求头带进 `prepare_request()` |
| `api/src/l05_service/chat.py:869-872`、`:1409-1412` | `prepare_response()` 调用点 |
| `api/src/l06_api/routers/v1.py:17-22` | `_overlay_headers()`：大小写不敏感覆盖 |
| `api/src/l06_api/routers/v1.py:64-72`、`:90-93` | 网关必需头（流式 / 非流式） |
| `api/src/l07_bootstrap/app_factory.py:159-161` | 中间件：`x-request-id` 生成或沿用 + 响应头 |
| `api/tests/test_header_passthrough.py` | 4 条用例 |

## 交付边界

- 本文只固化语义与引用，**不含代码改动**；实现已在 task-144 落地并随本批提交。
- 原「待拍板 1」（出站渠道规则能否改 `content-type` / `x-request-id`）已由 Lead 裁定（2026-11-15：**保护**）并并入决策 4；代码侧由 t5 落地（`PROTECTED_HEADERS` 扩展 + 测试），随本批提交。
- 配套登记：`docs/ssot.md` §5.6 与 §13 台账、`docs/GLOSSARY.md` §8 四条、`docs/INDEX.md` 模型试验行。

## 修订记录

- 2026-11-15（同日修订，Lead 裁定「保护」）：原「待拍板 1」落定——出站渠道规则不得改写 `content-type` / `content-length` / `host` / `x-request-id`，与凭据同级由 `PROTECTED_HEADERS` 强制（实现由 t5 落地）；决策 4、顺序总表、已知边界、后果、测试点、实现锚点、交付边界与 ssot §5.6 / §13 台账、GLOSSARY §8 同步。
