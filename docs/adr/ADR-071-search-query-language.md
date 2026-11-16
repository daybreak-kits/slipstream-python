# ADR-071 日志搜索：FOFA 风格查询语法与表达式编译

---

status: accepted（实现先行，随本批提交转正；2026-11-15 由 proposed 转正）
date: 2026-11-15
supersedes: 无（ADR-044 的字面量搜索约定继续有效，本 ADR 在其之上加一层语法）
related: ADR-032（日志与正文分离）、ADR-044（字面量搜索转义）、ADR-052（日志筛选与时间预设）、ADR-069（日志三视图：事实层与展示层分离）

---

## 背景

使用日志现在只有一个搜索入口：`q` 关键词，对所有字段做 OR 包含（`api/src/l04_repo/repo.py:904` `_request_log_conditions`，顶层 11 列 + 尝试内 6 个 json 路径，见 ADR-069 决策 6）。筛选项（渠道、模型、状态、来源、流式、密钥、用户）只能按 AND 叠在一起。

于是三种查询表达不出来：

1. **同字段多值**：「看 gpt-4o 或 gpt-4o-mini」，只能搜两次、用眼睛合并。
2. **排除**：「状态 >= 400 且渠道不是 MiniMax」，没有否定。
3. **跨字段条件**：「首答耗时 > 3s，且是流式，且没走缓存」。

用户已拍板：做 **FOFA 风格查询语法**，日志搜索先上，将来操作日志复用同一层；筛选控件（渠道/模型/状态/时间等）负责把选中的条件**自动拼成语法**，即语法是筛选控件的输出格式，而不是要求用户手写。

本轮只做核心模块 + 单测 + 本 ADR，**不接线** `repo.py` / `l05_service/log_views.py`（后端正在改这两处，避免并行冲突）。接线时按「编译产物 + 列映射」一次替换。

三条硬前提：

1. **编译产物必须是 SQLAlchemy 表达式**，不是 SQL 字符串——同一查询要能在 SQLite 与 Postgres 上跑，且值必须走绑定参数。
2. **严格模式不许静默回退**：语法一旦成立，解析失败必须报错（带列位置），不能退回「整串当关键词搜」。
3. **必须兼容既有搜索语义**：`NO_AVAILABLE_CHANNEL`、`minimax-m3.1-flash`、`model-a` 这类值今天都是裸词，新语法不能把它们判成语法错误。

## 决策

1. **语法与分层。** 语法（EBNF）：

   ```text
   query     := or_expr
   or_expr   := and_expr ("||" and_expr)*
   and_expr  := unary ("&&" unary)*
   unary     := "!" unary | "(" query ")" | predicate | bareword
   predicate := field OP value          # OP ∈ == != = *= > >= < <=
   value     := word | quoted           # quoted 用双引号，可含空格
   ```

   模块放 `api/src/l03_domain/query_lang/`：`lexer.py`（带 1-based 列号的 token）→ `parser.py`（递归下降 + `has_syntax`）→ `fields.py`（字段表与取值强制）→ `compiler.py`（SQLAlchemy 表达式）→ `errors.py`（`QueryError`，列号放 `details.column`）。理由：解析与编译分开，操作日志将来只换 `compile_query` 的列映射，语法、错误文案、前端交互全部不动。

2. **严格模式由 `has_syntax` 判定，判为语法就不再回退。** v2 口径（task-126 收紧）：只有「已知字段名或别名 + 比较操作符」、或出现 `&&` / `||`（引号内一律不算）才判语法；括号、感叹号、裸 `=`、未知字段（`a=b`）都是普通字符——**未知字段名不判语法**，整串（去首尾空白、保留中间空格）当一个关键词搜索。判定表：`429 (Too Many Requests)`→关键词；`upstream_model=x`→语法（别名命中）；`a=b`→关键词；`status >= 400`→语法；`foo && bar`→语法；`(a)`→关键词。判为语法后解析失败仍抛 `INVALID_REQUEST`（400，带列号与建议），绝不静默回退。理由：旧口径把含 `= ( ) < > !` 的粘贴文本（如 `429 (Too Many Requests)`）整条判成语法再 400，用户从能搜到变成不能搜，属回归；反过来，判成语法就绝不把 `=` 当裸词搜整串，「看起来筛了、其实搜的是整串」是比报错更坏的失败。`-` `.` `_` `%` 仍不是语法符号。

3. **单双引号都是字符串定界符，且值以哪个引号开头就必须以同一个引号闭合。** 引号内的 `= != && || ! ( )` 一律是字面量；未闭合报语法错，列号指向开引号，建议「在结尾补一个引号」。词内撇号（`don't`）不算定界符——只有 token 开头的引号才触发。理由：用户从别处粘值（`model='MiniMax-M3.1-Flash'`）与手写 `model="MiniMax-M3.1-Flash"` 是同一件事，只认双引号会把一整类合法输入判成语法错；而「引号风格改变语义」并不存在——两种引号的解码与转义规则完全一致，未闭合仍是错误而不是回退。

4. **裸词 = 默认搜索列的 OR，集合与今天完全一致。** `DEFAULT_SEARCH_COLUMNS` = request_id / upstream_request_id / requested_model / channel_name / upstream_model / response_model / api_key_label / api_key_id / user_id / error_code / error_message；调用方可用 `search_columns=` 覆盖。理由：上新语法不能悄悄缩小既有搜索面，先对齐再谈扩展；`users`/`attempts` 这类跨表匹配见决策 11。

5. **字段表一张表定三件事**：字段白名单、可用操作符、映射列。初版 17 个字段：

   | 字段 | 类型 | 可用操作符 | 映射列 |
   | --- | --- | --- | --- |
   | `model` | 文本 | `== != = *=` | `requested_model` |
   | `upstream` | 文本 | `== != = *=` | `upstream_model`（+ 尝试内 `$.upstream_model`） |
   | `channel` | 文本 | `== != = *=` | `channel_name` + `final_channel_model_id`（+ 尝试内 `$.channel_name`/`$.channel_slug`） |
   | `channel_model` | 文本 | `== != = *=` | `final_channel_model_id`（+ 尝试内 `$.channel_model_id`） |
   | `error` | 文本 | `== != = *=` | `error_code`（+ 尝试内 `$.error_code`） |
   | `msg` | 文本 | `== != = *=` | `error_message`（+ 尝试内 `$.message`） |
   | `key` | 文本 | `== != = *=` | `api_key_label` + `api_key_id` |
   | `user` | 文本 | `== != = *=` | `user_id` |
   | `source` | 文本 | `== != = *=` | `source` |
   | `status` | 整数 | `== != > >= < <=` | `http_status` |
   | `tokens` | 整数 | `== != > >= < <=` | `tokens_total`（调用方传 `prompt_tokens + completion_tokens`） |
   | `stream` | 布尔 | `== !=` | `is_stream` |
   | `latency` | 毫秒 | `== != > >= < <=` | `total_ms` |
   | `ttft` | 毫秒 | `== != > >= < <=` | `ttft_ms` |
   | `ttat` | 毫秒 | `== != > >= < <=` | `ttfa_ms` |
   | `cost` | 小数 | `== != > >= < <=` | `cost_usd` |
   | `time` | 时间 | `== != > >= < <=` | `created_at` |

   理由：字段的「能不能搜」「能怎么比」「搜哪一列」分散在三处，是「能解析但编译不了」这类洞的温床；一张表一个入口，新增字段只改一行。

   真实列名全部注册为别名（`fields.FIELD_ALIASES`，task-126）：`upstream_model→upstream`、`requested_model→model`、`channel_name→channel`、`error_code→error`、`error_message→msg`、`http_status→status`、`total_ms→latency`、`ttft_ms→ttft`、`ttfa_ms→ttat`、`created_at→time`、`is_stream→stream`、`api_key_label→key`、`user_id→user`（`source` 即主名，无别名）。别名与主名同列同谓词，`has_syntax` 同样认别名；语法帮助与错误建议仍按主名展示。

6. **文本比较大小写不敏感：`==` 走 `lower(col) = lower(value)`，`=` 走包含（`like_contains`），`*=` 支持 `*`/`?` 通配；`%`/`_` 永远是字面量。** `*=` 只把 `*`→`%`、`?`→`_`，再把用户值里的 `%`、`_` 和反斜杠转义（`model*=100%` 搜的是字面百分号）。理由：ADR-044 已定「`%`/`_` 按字面量转义」，新语法不能破功；否则搜「100%」这类值永远搜不到。

7. **数值与时间字段只接受有序比较，不接受包含。** `status=gpt` 报错并提示可用运算符，而不是「匹配 200/201/204」。`latency`/`ttft`/`ttat` 接受 `500`（毫秒）、`2s`、`1.5d`；`time` 接受 `7d`/`24h` 相对时间与 `2026-11-01`、`2026-11-01T12:00`（无时区按 Asia/Shanghai），裸数字视作 epoch 毫秒。**相对时间在编译时刻取 `now_ms()`**。理由：把「字符串包含」和「数值比较」混在同一个操作符里，是搜索框最容易骗人的地方；`time>7d` 读作「最近一周」符合直觉，操作符语义不变。

8. **`tokens` 映射到「和」，而不是某一列。** 字段表写 `tokens_total`，由调用方传 `prompt_tokens + completion_tokens` 表达式。理由：`tokens>1000` 的语义是总 token，列里没有总和列；与其在编译层猜单列（会静默按一半判断），不如把表达式交给唯一知道口径的调用方。

9. **尝试级字段留 `AttemptResolver` 协议，不写死 SQL。** `upstream`/`channel`/`channel_model`/`error`/`msg` 带 json 路径；编译时若注入了 resolver，就把 `AttemptMatch(paths, op, value, field)` 交出去，由调用方按方言生成 `EXISTS(...)`；resolver 返回 `None` 表示「这条我答不了」，谓词降级为行级列。裸词的尝试级搜索固定走 6 条默认路径（渠道名/渠道 slug/渠道模型 id/上游模型/错误码/错误正文）。理由：ADR-069 的 `json_each`（SQLite）与未来的 `jsonb_array_elements`（PG）形状完全不同，且「尝试明细从 JSON 迁到独立表」是可预期演进——解析器和字段表不该知道这些。

10. **上限：512 字符 / 20 谓词 / 8 层嵌套**，解析与编译各校验一次（编译侧防手工构造的 AST 绕过）。**查询长度超限报 400**，不截断。理由：搜索框是用户输入直接进扫描查询的唯一入口，OR 分支数和嵌套深度必须封顶，且「悄悄截断」会给出一个用户看不出错的错误结果。

11. **跨表/子查询搜索用「列对象鸭子类型」当扩展点。** 用户显示名这类需要 join 的匹配，调用方可以往 `columns` 里放一个提供 `ilike(pattern, escape=)` 的对象；编译器只调 `DialectHooks.contains()`，不关心它是列还是子查询。理由：把最简单的接缝留给最不确定的需求，比现在造一套 hook 协议便宜得多。

12. **公开接口保持小**：`has_syntax(text) -> bool`、`parse(text) -> Node`（`strict=None` 时按 `has_syntax` 决定；`strict=False` 是「强制当关键词」的逃生门）、`compile_query(node, columns, dialect_hooks=None, *, attempts=None, search_columns=None) -> ColumnElement`。节点类型 `Bareword / Comparison / And / Or / Not` 是值对象。理由：请求路径只需要「判语法」和「解析」，仓库层只需要「编译」；其余（token、字段表、limit 常量）导出给测试与文档，不导出给路由器。

13. **`!=` 把「没有值 / 没有命中」算作不相等；表达式里带尝试级路径的字段，尝试臂从 OR 改成 AND `NOT EXISTS(尝试命中)`。** 文本列生成 `col IS NULL OR lower(col) != lower(value)`，布尔/数值列同理。理由两条：`msg!=timeout` 的用户预期是「排掉超时」，空错误的行必须留下，否则过滤器看起来漏数据；而尝试臂若沿用 OR，`upstream!=X` 会因为「赢的那一跳是别的模型」把「某一跳正好是 X」的行留下——过滤器等于没生效。有序比较（`> >= < <=`）保持 SQL 三值逻辑不变，`latency<3s` 仍不命中空值行。

14. **大小写折叠统一成 `fold_key`：只折 ASCII 的 `lower()`，与 SQLite 的 `lower()` 逐字符等价。** 聚合分组键（`l05_service/log_views.py` 的 `requested_model` 桶、段内 `upstream_model` 比较、段名）、`l05_service/flows.py` 的模型/上游/渠道节点 id、`l05_service/timeseries.py` 的三个维度，以及筛选项去重（`l04_repo/log_filter_values.py` 的 `fold_values`，现在是 `str.lower()`，要换）全部走同一个定义，实现放 `api/src/l01_core/textfold.py` 的 `fold_key(s)`；SQL 谓词继续用 `lower(col) = lower(value)`。展示名保留「首见原写法」，候选里若目录（`channel_models.upstream_model` / `combos.name`）有写法则优先目录的；段钻取与 `/summary` 的标签同折叠，不允许列表一个写法、下钻另一个。理由：SQLite 的 `lower()` 只折 ASCII——`lower('ÄBC') = 'Äbc'`、`lower('İST') = 'İst'`、希腊字母原样不动——而 Python 的 `str.lower()` 会把这些都折掉；两侧口径不一致时，「只有大小写不同的非 ASCII 名字」会在聚合里并成一个桶、在过滤里却互相匹配不到，这比完全不折叠更难排查。**反方案（SQL 侧也做 Unicode 折叠）v1 否决**：要么写自定义 SQL 函数、要么写编译钩子，SQLite 与 PG 各一套，复杂度配不上收益；代价是 PG 的 `lower()` 本身 Unicode-aware，会比标签并得更狠——方向安全（多匹配，不会错配），这段跨方言差异写进 `fold_key` 的 docstring。渠道轴不走名字折叠当主路：`request_logs` 没有 channel_id，行上带 `final_channel_model_id`（orm.py:215，有索引），join `channel_models` 得 channel_id 作权威身份（渠道改名也合并），展示名取渠道当前 name，历史名留在行内；`fold_key(channel_name)` 只作无 id 行的回退。模型两轴同样按 id（最终裁定）：上游模型轴分组键 `final_channel_model_id`、展示名取该 channel model 当前 `upstream_model`（实体被删退回行内快照）；综合模型轴分组键 `resolved_combo_id`（手动组合，task-128 起写入），直连回退 `final_channel_model_id`，自动组合没有实体 id、历史 null 行按名回退 `fold_key(requested_model)`——回退不回填，这是数据边界，不是选择。空 channel_name 的哨兵「请求模型无可用渠道」不是渠道名，单独成桶，绝不参与折叠，也绝不并入任何真实渠道。
## 备选与否决

- **自研 DSL 直接编译成 SQL 字符串**：否决。跨 SQLite/PG 的方言差异、参数绑定、转义全要自己来，这是本项目最不该自研的一段代码。
- **解析失败就回退裸词**：否决。回退会把「字段名写错」变成「搜到 0 条但不出错」，用户区分不了「没有数据」和「查询写错了」；严格模式 + 列号错误是唯一能教会用户语法的路径。
- **让 `status` 也支持 `=` 包含**：否决。`status=2` 命中 200/201/204 会误导排障。
- **只把双引号当定界符**：否决（改单，见决策 3）。理由：两种引号的解码规则一致，只认双引号会把带 `'` 的粘贴值判成语法错，收益为零、代价是丢掉一整类输入。
- **在解析阶段做类型强制**：否决（现做法：解析保留原文，编译阶段强制，`Comparison.value_column` 让类型错误仍指向值的列）。理由：解析器不该知道 `time` 是毫秒还是字符串；将来操作日志的 `time` 同名字段可能映射到不同列，强制逻辑必须能随调用方变。
- **本轮顺手接线到 `repo.py` / `log_views.py`**：否决（不是技术问题，是并行窗口冲突）。理由：后端正在改同一批文件；先落语言层与单测，接线时按「编译产物 + 列映射」一次替换，冲突面最小。

## 后果

- 新增 `l03_domain/query_lang/`（`__init__.py`、`ast.py`、`lexer.py`、`parser.py`、`fields.py`、`compiler.py`、`errors.py`）与 `tests/test_query_lang.py`（98 个用例，含单双引号与 `!=` 空值两批改单，`pytest api/tests/test_query_lang.py` 全绿）；已接线 `q` 分流（见下两条）。
- 接线时调用方必须提供：`columns` 映射（字段表用到的列 + `DEFAULT_SEARCH_COLUMNS` 的 11 列 + `tokens_total` 表达式）；建议同时实现 SQLite 版 `AttemptResolver`，非 SQLite 显式降级并打点（与 ADR-069 的 `CAPABILITY DOWNGRADE` 同口径，不得当成等价）。缺列会抛 `ValueError`（调用方 bug），不是用户可见的 400。
- 接线用的列映射、SQLite 版 `AttemptResolver` 与 `model` 三路匹配已预置为独立模块 `api/src/l04_repo/log_query.py`（`LOG_COLUMNS` / `SqliteAttemptResolver` / `load_combo_graph` / `model_match` / `compile_log_filter`），配 `tests/test_log_query.py` 21 个用例；接线时直接注入，不在 `repo.py` 里另写一份列映射。
- 已落地（`api/src/l06_api/routers/logs.py`）：`q` 经 `has_syntax` 分流——无语法走今天的裸词搜索，有语法严格解析并把 `details.column` / `details.suggestion` 原样透出；关键字路径与旧 `repo.list_logs` 的 ids/total 逐一对齐（`tests/test_logs_query_wiring.py`）。`GET /api/logs/filters` 的模型列表改为「目录（`channel_models.upstream_model` + `combos.name`）∪ 近 30 天观测」，空日志时不再返回空数组。为在冻结期传谓词，新增临时桥 `l04_repo/log_search.py`，`repo.list_logs` 长出 `predicate=` 后删除。
- 前端可以直接把筛选控件拼成语法，但**语法是否合法必须以后端为准**：错误体 `details.column` + `details.suggestion` 足以把光标定位到出错字符，不必在前端复刻一份语法。
- 已知缺口与取舍：
  - `user` 只匹配 `user_id`；用户显示名需要子查询，走决策 11 的列对象扩展，未在初版实现。
  - `!=` 的空值与尝试臂规则见决策 13；有序比较仍按 SQL 三值逻辑，空值行不命中。
  - **语法搜索 v1 只覆盖「逐次请求」视图与段钻取**：聚合视图（`combo` / `status`）与 `GET /api/logs/summary` 收到带语法的 `q` 直接返回 400（`details.view = requests`），既不静默忽略也不降级成关键词——它们的过滤走 `l05_service/log_views.py` 与 `aggregates.py` 的独立 seam，等这些 seam 长出 `predicate=` 形参后再放开。
  - 模型筛选的「三路匹配」（请求名 / 上游名 / 组合成员，大小写不敏感）只在语法层与 `log_query.py` 里成立；旧的 `model=` 查询参数仍走 `repo._request_log_conditions` 的精确匹配，待 `repo.py` 解冻后合并。
  - 文本 `==` 用 `lower()` 做大小写折叠，SQLite 的 `lower()` 只折 ASCII——中文/全角不受影响（无大小写），但非 ASCII 拉丁字母按字节比较；与既有 `ilike` 的降级口径一致。
  - 中文错误文案（`QueryError.message`）与全站 `AppError` 一致；若将来要 i18n，列号与 `suggestion` 已结构化，文案可下沉。
