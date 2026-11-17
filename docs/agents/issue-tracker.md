# Issue tracker: Local Markdown

本仓的 issue 与规格以 markdown 文件形式存放在 `.scratch/` 下。生产仓不回写内部开发仓的 tracker，因此本地文件是这里的唯一来源。

## 目录约定

- 一个功能一个目录：`.scratch/<feature-slug>/`
- 规格：`.scratch/<feature-slug>/spec.md`
- 实现票据：一票一文件 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 编号，**不要**合并成一个 tickets 文件

## 文件内部约定

- 票据顶部用 `Status:` 行记录 triage 状态（取值见 `triage-labels.md`）
- 票据顶部用 `Blocked by: NN, NN` 行记录阻塞边；所列票据全部 `Status: resolved` 时该票才可开工
- 对话与评论追加到文件底部的 `## Comments` 标题之下

## 入库策略

`.scratch/` 整体在 `.gitignore` 中被忽略，但对 `.md` 开了白名单（见仓库根 `.gitignore`）：

```gitignore
.scratch/**
!.scratch/**/
!.scratch/**/*.md
```

后果：规格与票据进版本库（跨 worktree 可见、有历史）；`.scratch/` 下的基准 JSON、压测脚本等原始证据仍不入库。`ssot.md` §16 已注明 `.scratch/` 下的 JSON/脚本是原始证据而非事实源。

## 当技能要求「发布到 issue tracker」

在 `.scratch/<feature-slug>/` 下新建文件（目录不存在则创建）。

## 当技能要求「取出相关票据」

读取被引用的文件路径。用户通常会直接给出路径或票据编号。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图**是一个文件，每张子票是一个文件。

- **地图**：`.scratch/<effort>/map.md`，正文含 Notes / Decisions-so-far / Fog 三段
- **子票**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 编号，正文写问题。`Type:` 行记录票据类型（`research` / `prototype` / `grilling` / `task`）；`Status:` 行记录 `claimed` / `resolved`
- **阻塞**：文件顶部的 `Blocked by: NN, NN` 行。所列文件全部 `resolved` 时解除阻塞
- **前沿**：扫描 `.scratch/<effort>/issues/`，取未关闭、未阻塞、未认领的文件，编号最小者优先
- **认领**：把 `Status: claimed` 写入文件并保存，这是本轮会话的第一次写操作
- **解决**：在 `## Answer` 标题下追加答案，把 `Status: resolved` 写入文件，然后把「要点 + 链接」追加到 `map.md` 的 Decisions-so-far
