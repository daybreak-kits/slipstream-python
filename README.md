# slipstream-python

驭流（Slipstream）网关的生产部署仓库：基于 Python/FastAPI 的 OpenAI 兼容 LLM 网关——多渠道路由、熔断与准入控制、零 SQL 计费管线，单端口同时服务 API 与管理 UI。

## 功能特性

- **OpenAI 兼容数据面**：POST /v1/chat/completions（流式 / 非流式）、GET /v1/models
- **路由与韧性**：渠道目录 + 故障熔断（半开探测租约）、429 冷却、在飞上限与 429/Retry-After 准入
- **计费管线**：`USAGE_PIPELINE=memory` 请求内零 SQL——WAL 落盘 + 后台批量幂等入库
- **单端口生产形态**：/v1 API、管理 UI（web/dist）、GET /health 探活同源托管
- **存储**：SQLite（WAL）默认开箱即用；可选 PostgreSQL（asyncpg）

## 界面预览

| 数据看板 | 模型管理 |
|---|---|
| ![数据看板](docs/screenshots/dashboard.png) | ![模型管理](docs/screenshots/models.png) |

## Quick Start

前提：Linux 或 macOS + bash；Python 3.14（在 3.14.4 验证）；构建管理 UI 需 Node 20+（可选）。

```bash
git clone git@github.com:daybreak-kits/slipstream-python.git
cd slipstream-python
cp api/.env.example api/.env    # 可选：不建则用内置开发默认值，启动时会告警
./start-prd.sh
```

首次运行会自动完成：创建 `.venv` → 安装 `api/requirements.txt` →（若 `web/dist` 缺失且装有 Node）构建管理 UI → 前台启动网关。

`start-prd.sh` **不会**替你生成 `.env`：生产配置里有密钥，模板不该被悄悄复制成一份看起来可用的生产配置。缺 `api/.env` 时脚本打印告警并按内置开发默认值启动（`JWT_SECRET=dev-secret-change-me`、`admin/admin`、SQLite）——**这份默认配置不得暴露到网络**。

成功判据（另开一个终端）：

```bash
curl http://127.0.0.1:3680/health
# {"status":"ok","app":"Slipstream","env":"local"}
```

浏览器打开 http://127.0.0.1:3680/ 即管理 UI。停止服务：Ctrl+C。

## 配置

环境文件 `api/.env`（本机私有，**永不提交**；模板见 `api/.env.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `local` | 运行环境。非 `production`/`staging` 时不注册 SIGTERM 排空 |
| `USAGE_PIPELINE` | `memory`（模板值） | 计费管线：`memory`=请求内零 SQL（WAL+后台入库）；`classic`=每请求同步落库。代码默认值仍是 `classic`，模板里给的是实测更快的 `memory` |
| `CATALOG_POLL_INTERVAL_MS` | `2000`（模板值） | 渠道目录/路由设置下发轮询间隔（控制面 → 数据面延迟下限）。代码默认 `30000`，模板收紧到 2 s |
| `DATABASE_URL` | 未设 | 未设 = `api/data/app.db`（SQLite WAL）；PostgreSQL：`postgresql+asyncpg://user:password@host:5432/db`（需 `pip install asyncpg`） |

完整变量清单（超时、连接池、熔断、探测、models.dev 同步等）见 `api/.env.example`，机制口径以 `docs/ssot.md` §8 为准。

数据库与运行时数据（价目缓存、WAL）都在 `api/data/` 下，不入库。

## 构建管理 UI（可选）

仓库不携带构建产物，只背前端源码（`web/`）：

```bash
cd web && npm install && npm run build
```

`start-prd.sh` 会在 `web/dist` 缺失且装有 Node 时自动执行上述构建。没有 Node 时网关照常服务 API（UI 路径返回 404）。

## 项目结构

```
api/src/     七层架构（l01_core 配置/工具 … l07_bootstrap 应用装配）
api/tests/   pytest 测试
api/scripts/ 运维脚本（seed、账本对账、补数、冒烟、并行跑测试、基线）
web/         管理前端源码（React 19 + Vite + Tailwind 4）
docs/        规范、ADR、性能报告（入口 docs/INDEX.md）
```

## 测试

```bash
# 后端（xdist 并行；Windows 用 .venv/Scripts/python.exe）
cd api && PYTHONPATH=src ../.venv/bin/python -m pytest -n 8 -q -p no:cacheprovider

# 前端（裸 npm test 是 watch 模式，会挂住）
cd web && npm test -- --run
cd web && npm run build
```

更多门禁与实测口径见 `docs/testing.md`。

## 文档

- `docs/INDEX.md` — 文档索引（ssot、ADR 全量、契约、术语、专项）
- `docs/ssot.md` — 单一事实源：数据面契约、路由、计费、UI 采纳
- `docs/PERFORMANCE_REPORT.md` — 性能权威报告（含 Linux 开环三臂基准与 agentgateway 对照）
- `AGENTS.md` — agent 工作流配置入口（issue tracker、标签、领域文档消费规则）

## 与开发仓的同步

本仓是生产部署仓：功能开发在内部主仓库进行并验证后，按相对路径同步进来——`api/src`、`api/tests`、`api/scripts`、`web/`（含 `src/lib/`；不含 `node_modules`/`dist`）、`docs/`（不含 scratch 与 archive）；`api/data/` 与 `api/.env` 永不提交。同步前端源码后需在生产机重新 `npm run build`。

> 同步注意：`web/src/lib/` 是前端共享模块目录，历史上曾被 `.gitignore` 里 Python 打包模板的 `lib/` 规则整体误忽略（33 个模块未能入库）。该规则已锚定到仓库根，同步后请用 `git status` 确认 `web/src/lib/` 出现在变更列表中。

## 贡献

内部项目，暂不接受外部 PR；问题请开 issue。

## License

[Apache-2.0](LICENSE)
