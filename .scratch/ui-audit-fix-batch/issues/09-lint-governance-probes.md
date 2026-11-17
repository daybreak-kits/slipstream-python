# 09: lint 三条治理探针（warning 级）

**What to build:** `lint-ui.mjs` 新增三条 warning 级规则——①ui/ 之外手写基元语义（`role="switch|tablist|tab|alertdialog|listbox"`）、②ui/ 之外 import Radix、③应用层 `transition-all`——只计 warning 不拦截，`--max-warnings 19` 上限不变。落地时全站 0 命中（07/08 已清零违例），规范从文档变成门禁可执行形态。

**Blocked by:** 07 (switch 接线 + 死基元移除), 08 (手写基元语义清零)

**Status:** ready-for-agent

依据：ADR-081 决策 5（用户裁定 warning 不拦截）；`docs/ui.md` §2.5 探针。

- [ ] 三条规则实现，走既有 warning 计数机制（relaxed 桶样式）
- [ ] 落地时全站命中 0（07/08 完成为前提）
- [ ] 人为注入一处违例可复现 warning、撤销后归零（规则有效性自证）
- [ ] lint:ui 全绿（0 error / ≤19 warning）
- [ ] ui.md §2.5 探针段落改为"已进 lint"表述
