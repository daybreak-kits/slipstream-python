# Domain Docs

工程技能在探索代码库时应如何使用本仓的领域文档。

## 探索之前先读这些

- **`docs/GLOSSARY.md`** — 词义表。这是本仓词义的唯一正文。
- **`docs/ssot.md`** — 机制与口径的事实源（数据面契约、路由、计费、UI 采纳）。
- **`docs/adr/`** — 要动的区域相关的决策记录，一条决策一文件。被取代的决策不删除，由新 ADR 标注取代关系。
- **`docs/contracts/`** — 对外契约（管理面 API、计费事件 schema）。

若上述文件缺失，**静默继续**。不要主动提示缺失，也不要在开工前建议创建它们；`/domain-modeling` 技能会在术语或决策真正落定时惰性创建。

## 文件结构

单上下文仓库（本仓）：

```
/
├── AGENTS.md
├── docs/
│   ├── INDEX.md          ← 按界面导航的入口
│   ├── ssot.md           ← 机制与口径正文
│   ├── GLOSSARY.md       ← 词义表
│   ├── adr/              ← 决策记录，一条一文件
│   ├── contracts/        ← 对外契约
│   └── agents/           ← 本文与 tracker 配置
├── api/src/              ← 七层后端 l01_core … l07_bootstrap
└── web/src/              ← 管理前端（React 19 + Vite + Tailwind 4）
```

## 按词表用词

当输出中要命名领域概念（票据标题、重构提案、假设、测试名）时，使用 `docs/GLOSSARY.md` 中定义的词，不要漂移到词表明确避开的同义词。

若需要的概念在词表里还没有，这是一个信号：要么你在发明项目不使用的语言（重新考虑），要么这是一个真实缺口（记下来交给 `/domain-modeling`）。

## 冲突时以谁为准

- 「词义」冲突 → 改 `GLOSSARY.md`，但以 `ssot.md` 为准回头修词义表（`ssot.md` §16）。
- 「行为/字段/参数」冲突 → 改 `ssot.md`。
- `ssot.md` 与代码/测试冲突 → 以代码与测试为准，并回头修文档。

## 显式标注 ADR 冲突

若输出与既有 ADR 矛盾，要显式指出，而不是静默覆盖：

> _与 ADR-0007（event-sourced orders）矛盾，但值得重开，因为……_

## 本仓特有的额外约束

- **ADR 只增不删**：被取代时新写一条 ADR，在头部 `supersedes:` 字段标注关系，不改写旧 ADR 的结论。
- **记录数字的纪律**：不要在任何文档里写随手会变的数字（用例数、文件数、耗时、产物大小）。定性结论进 `ssot.md`，实测数字进 `docs/testing.md` 或 `docs/PERFORMANCE_REPORT.md`（`ssot.md` §16 计数纪律）。
- **代码变更必须同步 `ssot.md`** 的相关章节；新增配置必须同步 `api/.env.example` 与本文件 §8。
