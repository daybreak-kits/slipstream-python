# Triage Labels

各项技能用五个规范的 triage 角色说话。本文件把角色映射到本仓 tracker 实际使用的标签字符串。

本仓使用**本地 Markdown tracker**（见 `issue-tracker.md`），因此「标签」不是平台对象，而是票据文件顶部的 `Status:` 行取值。

| 技能中的角色 | 本仓取值 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 待维护者评估 |
| `needs-info` | `needs-info` | 等报告人补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格完备，可交给 agent 独立完成 |
| `ready-for-human` | `ready-for-human` | 需要人工实现 |
| `wontfix` | `wontfix` | 不予处理 |

## 写入方式

在票据文件（`.scratch/<feature>/issues/NN-<slug>.md`）顶部写一行：

```markdown
Status: ready-for-agent
```

`/wayfinder` 的地图票使用另一组取值（`claimed` / `resolved`），见 `issue-tracker.md` 的 Wayfinding 一节。

当技能提到某个角色（例如「打上 AFK-ready 标签」）时，取本表右列对应字符串。
