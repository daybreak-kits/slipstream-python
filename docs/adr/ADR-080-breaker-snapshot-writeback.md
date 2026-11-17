# ADR-080 熔断状态的快照回写与测试环境隔离

> 编号说明：原拟 ADR-078，与另一条在途决策（stream-rewrite-byte-blocks）撞号，2026-11-18 让位改为 ADR-080；正文内容不变。

- 状态：accepted（2026-11-18）
- 关联：ADR-051（熔断按真实结局记账）、ADR-057（`USAGE_PIPELINE` 总开关）、ADR-060（catalog 版本轮询）、ssot §5.4 / §8.4、`docs/testing.md`

## 背景

两个缺陷在同一处暴露，来源不同但互相掩盖。

**其一：`memory` 管道下流式熔断静默失效。** 准入（`admit_candidate`）在 `USAGE_PIPELINE=memory` 时只读进程内 catalog 快照；快照的版本只跟管理写变，不跟熔断流量变，所以每次失败都必须把行状态**回写**进缓存（`routing._sync_cached_model`），否则下一次准入继续读到「正常」并放行。回写依赖 `catalog_for_session(session)` 找到 catalog，而它原先只读 `session.info["catalog"]`——这个指针只由 `l06_api.deps.get_db` 打在**请求 session** 上。流式请求的结局由 finalizer 记（`StreamRun._finalize_impl`），它自己 `session_factory()` 开 session，不经 `get_db`，于是指针为空、回写变成 no-op。后果：熔断器在库里正常记账（`health_status=open`、计数到位），但路由侧永远看不到，坏渠道持续吃流量——只要进程不重启就一直错下去。

**其二：测试套件读本机 `.env`。** `Settings` 是 pydantic-settings 模型，`SettingsConfigDict(env_file=".env")` 让测试里**任何** `Settings(...)` 也读开发者的 `api/.env`。该文件在本机是 `USAGE_PIPELINE=memory`，于是整个套件跑到零 SQL 路径上：26 个按 classic 写的用例失败，而只在 classic 下才成立的断言从未被验证。同一份代码，换台机器（无 `.env`）全绿——CI 与本机的结论可以相反，且没有任何代码差异。

两者的关系是：缺陷一**本来应该**被测试抓住，但缺陷二让所有测试都跑在 memory 路径上，于是它表现为「一个莫名其妙的老测试失败」，而不是「一个待修的 bug」。反过来，一旦把套件钉回 classic，缺陷一就再没有测试守着。所以两条必须一起修，且必须**各留一条记忆**。

## 决策

1. **回写路径按 engine 反查 catalog。** `config_catalog.catalog_for_session` 的取值顺序改为：`session.info["catalog"]`（请求 session 的显式指针）→ 按 `session.bind` 反查 engine 绑定（`repo.write_catalog_for`，与 `repo` 已有的写失效注册表 `_WRITE_CATALOGS` 同一份）。后台 session 因此也能拿到快照；请求路径的行为与开销不变（指针命中即返回，不触达 engine）。
2. **快照回写是 memory 管道的正确性前提，不是优化。** 任何新增的「在请求之外记熔断」的入口都必须走 `record_failure` / `record_success`，不得绕过——绕过就等于让熔断失效。此约束写进 ssot §5.4。
3. **测试套件钉死代码默认值。** `tests/conftest.py` 在 import 期设 `Settings.model_config["env_file"] = None`，一处管住所有直接构造点（测试体内的 `Settings(...)` 也覆盖）。需要 `memory` 的用例显式 opt-in（`test_usage_pipeline_wiring.memory_app`、`test_breaker_api.memory_app`）。
4. **两条路径各有一条回归用例。** classic：`test_stream_abort_feeds_the_failure_breaker`（原有用例，修复前在 classic 下本就通过）；memory：`test_stream_abort_feeds_the_breaker_in_the_memory_pipeline`（新增，断言第三次请求 503 且未打上游）。后者经反向验证——临时移除决策 1 的回退分支后该用例变红（`assert 200 == 503`）。

## 理由

1. **只修一处会留洞。** 只修 harness（决策 3）会让熔断 bug 失去覆盖；只修回写（决策 1）会让套件继续依赖本机私有文件。两者互为对方的护栏。
2. **engine 反查比传参更省事、也更难漏。** 备选是给 `StreamRun` 加一个 `catalog` 字段层层传下去；那要改构造签名、调用点，且以后新增后台入口仍会漏。engine 绑定已经在 `repo` 里维护（`bind_write_catalog`），复用它等于让「谁能写库」与「谁能回写快照」共用同一个事实源。
3. **`session.info` 优先、engine 兜底**，而不是反过来：请求 session 的指针是显式的、语义明确，且热路径零成本；engine 反查只在后台 session 上发生（每次流式请求一次），不值得为它改请求路径。
4. **conftest 一处覆盖全部构造点**，比逐处加 `_env_file=None` 可靠。逐处改的版本先做过（`test_settings` + `test_usage_pipeline_wiring` 两处），跑完仍有 5 个失败——因为另有十几处测试体内直接构造 `Settings`。

## 后果与影响

- **生产**：`memory` 管道下流式请求的熔断恢复生效。非流式路径不受影响（它本来就经 `get_db`）。`classic` 完全不受影响（每请求重读行）。
- **开销**：后台 finalize 每次多一次 `weakref` 字典查找；请求路径无新增。
- **测试**：套件结果不再依赖 `api/.env`。带 `.env` 与不带 `.env` 两次全量实测均为 `1092 passed, 8 skipped`（2026-11-18）。
- **文档**：`docs/testing.md` 新增「The suite ignores `api/.env`」一节；ssot §5.4 增一条回写约束；本条为决策留档。

## 相关代码与文档

- `api/src/l05_service/config_catalog.py` — `catalog_for_session` 的回退分支、`catalog_for_engine`
- `api/src/l04_repo/repo.py` — `write_catalog_for`（读取 `_WRITE_CATALOGS`）
- `api/src/l05_service/chat.py:1336` — finalizer 自开 session 的调用点
- `api/src/l05_service/routing.py` — `_sync_cached_model`、`record_failure`、`record_success`
- `api/tests/conftest.py` — `Settings.model_config["env_file"] = None`
- `api/tests/test_breaker_api.py` — 两条回归用例
- `docs/testing.md`、`docs/ssot.md` §5.4
