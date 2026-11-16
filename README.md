# slipstream-python

驭流（Slipstream）网关的生产部署仓库：基于 Python/FastAPI 的 OpenAI 兼容 LLM 网关——多渠道路由、熔断与准入控制、零 SQL 计费管线，单端口同时服务 API 与管理 UI。

## 功能特性

- **OpenAI 兼容数据面**：POST /v1/chat/completions（流式 / 非流式）、GET /v1/models
- **路由与韧性**：渠道目录 + 故障熔断（半开探测租约）、429 冷却、在飞上限与 429/Retry-After 准入
- **计费管线**：`USAGE_PIPELINE=memory` 请求内零 SQL——WAL 落盘 + 后台批量幂等入库
- **单端口生产形态**：/v1 API、管理 UI（web/dist）、GET /health 探活同源托管
- **存储**：SQLite（WAL）默认开箱即用；可选 PostgreSQL（asyncpg）

## 界面预览

| 数据看板 | 渠道管理 |
|---|---|
| ![数据看板](docs/screenshots/dashboard.png) | ![渠道管理](docs/screenshots/channels.png) |

| 使用日志 | 模型管理 |
|---|---|
| ![使用日志](docs/screenshots/logs.png) | ![模型管理](docs/screenshots/models.png) |

## Quick Start

前提：Linux 或 macOS + bash；Python 3.14（在 3.14.4 验证）；构建管理 UI 需 Node 20+（可选）。

```bash
git clone git@github.com:daybreak-kits/slipstream-python.git
cd slipstream-python
./start-prd.sh
```

首次运行会自动完成：创建 `.venv` → 安装 `api/requirements.txt` → 从 `api/.env.prd.example` 生成 `api/.env.prd` →（若 `web/dist` 缺失且装有 Node）构建管理 UI → 前台启动网关。

成功判据（另开一个终端）：

```bash
curl http://127.0.0.1:3680/health
# {"status":"ok","app":"Slipstream","env":"local"}
```

浏览器打开 http://127.0.0.1:3680/ 即管理 UI。停止服务：Ctrl+C。

## 配置

环境文件 `api/.env.prd`（本机私有，**永不提交**；模板见 `api/.env.prd.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `USAGE_PIPELINE` | `memory` | 计费管线：`memory`=请求内零 SQL（WAL+后台入库）；`classic`=每请求同步落库 |
| `CATALOG_POLL_INTERVAL_MS` | `2000` | 渠道目录/路由设置下发轮询间隔（控制面 → 数据面延迟下限） |
| `DATABASE_URL` | 未设 | 未设 = `api/data/app.db`（SQLite WAL）；PostgreSQL：`postgresql+asyncpg://user:password@host:5432/db`（需 `pip install asyncpg`） |

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
api/tests/   pytest 测试（pytest-xdist 并行）
api/scripts/ 运维脚本（seed、账本对账、补数、冒烟、基线）
web/         管理前端源码（React 19 + Vite + Tailwind 4）
docs/        规范、ADR、性能报告（入口 docs/INDEX.md）
```

## 测试

```bash
cd api && ../.venv/bin/python -m pytest -n 8
```

## 文档

- `docs/INDEX.md` — 文档索引（ssot、ADR 全量、契约、术语）
- `docs/PERFORMANCE_REPORT.md` — 性能权威报告（含 Linux 开环三臂基准与 agentgateway 对照）

## 与开发仓的同步

本仓是生产部署仓：功能开发在内部主仓库进行并验证后，按相对路径同步进来——`api/src`、`api/tests`、`api/scripts`、`web/`（不含 node_modules/dist）、`docs/`（不含 scratch）；`api/data/` 与 `api/.env.prd` 永不提交。同步前端源码后需在生产机重新 `npm run build`。

## 贡献

内部项目，暂不接受外部 PR；问题请开 issue。

## License

[Apache-2.0](LICENSE)
