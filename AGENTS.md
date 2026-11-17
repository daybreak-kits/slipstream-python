# AGENTS.md

本文件只登记 **agent 工作流配置**（issue 落在哪、标签怎么叫、领域文档在哪读），不承载项目事实。
机制与口径的唯一事实源是 `docs/ssot.md`；与本文冲突时以 `ssot.md` 与代码/测试为准（`ssot.md` §16）。

## 先读什么

动代码之前按此顺序读，读完再动手：

1. `docs/INDEX.md` — 按界面导航的入口（每个页面 → 接口 / ADR / ssot 章节 / 测试）
2. `docs/ssot.md` — 机制与口径正文（数据面契约、路由、计费、UI 采纳）
3. `docs/GLOSSARY.md` — 词义表（命名输出时用它的词）
4. `docs/adr/` — 要动的区域相关的决策（一条决策一文件，只增不删）

## 命令

以 `docs/testing.md` 为准（那里是实测口径与门禁说明）：

```bash
# 后端（Linux/macOS；Windows 用 .venv/Scripts/python.exe）
cd api && PYTHONPATH=src ../.venv/bin/python -m pytest -q -p no:cacheprovider

# 后端全量并行（xdist 已在 requirements 内）
cd api && PYTHONPATH=src ../.venv/bin/python -m pytest -n 8 -q -p no:cacheprovider

# 前端
cd web && npm run build          # tsc --noEmit + vite build（含 chunk 体积护栏）
cd web && npm test -- --run      # 裸 npm test 是 watch 模式，会挂住
cd web && npm run lint:ui        # UI 规范门禁，--max-warnings 19 已封顶（vendored 例外）
```

视觉验收是**人工**的（无 Playwright 截图基线）：`./start-prd.sh` 起网关后打开 UI 逐页看。

## Agent skills

### Issue tracker

本地 Markdown：一个功能一个目录 `.scratch/<feature-slug>/`，`spec.md` 存规格、`issues/NN-<slug>.md` 存票据（从 `01` 编号）。`.scratch/` 下的 `.md` 已放行进 git，压测 JSON/脚本照旧忽略。见 `docs/agents/issue-tracker.md`。

### Triage labels

默认五件套，记在 issue 文件的 `Status:` 行：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文：词义表 `docs/GLOSSARY.md`、决策记录 `docs/adr/`、机制正文 `docs/ssot.md`。见 `docs/agents/domain.md`。
