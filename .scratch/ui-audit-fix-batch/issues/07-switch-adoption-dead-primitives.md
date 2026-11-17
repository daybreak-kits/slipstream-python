# 07: switch 接线 + 死基元移除

**What to build:** 渠道/模型的启停控件由手写实现改为组合 `ui/switch`（行为等价：受控、aria-checked、禁用态、焦点环），`ui/switch` 从零调用转为唯一实现；同时删除 5 个零调用基元（avatar/breadcrumb/form/progress/scroll-area）并登记 ui.md §3 已删除——"基元在库里、页面在外面写"的脱锚机制清除（ADR-081）。

**Blocked by:** None (can start immediately)

**Status:** resolved

依据：ADR-081 决策 2/4；audit.md A3 + 组件治理盘点。

- [x] EnabledToggle 行为等价重写（外部 props 契约不变，调用方无感）
- [x] `ui/switch` 成为启停唯一实现，手写 `role="switch"` 清零
- [x] 5 个死基元文件删除，ui.md §3 已删除类更新（4→9 件）
- [x] vitest：渠道/模型启停路径回归通过
- [x] build + vitest + lint 绿；渠道列表与详情启停复拍
