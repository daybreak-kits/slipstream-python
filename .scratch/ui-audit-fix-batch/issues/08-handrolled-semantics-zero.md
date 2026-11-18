# 08: 手写基元语义清零

**What to build:** 四处手写交互语义收敛到基元层：组合编辑器内手写 tablist 换 `ui/tabs`；日志路由链浮层换 `ui/popover`；试验页模型选择的 combobox 收敛到 `ui/combobox`（保留选项内 spinner 等既有能力）；模型页排期时区选择器保留页内 Popover+Command 组合并登记豁免（`ui/combobox` 未透出 id/aria-invalid/aria-describedby 表单语义，2026-11-18）。用户感知：交互行为（键盘/焦点/Escape）全站一致。

**Blocked by:** None (can start immediately)

**Status:** resolved

依据：ADR-081 决策 2；audit.md 组件治理盘点（4 违例锚点）。

- [x] 组合编辑器候选 tab 换 `ui/tabs`，aria-selected 由基元接管
- [x] 日志路由链浮层换 `ui/popover`，Escape/外点关闭行为保持
- [x] 试验页与模型页 combobox 收敛到 `ui/combobox`，检索/空态/禁用行为等价——试验页已收敛；模型页排期时区选择器（`components/models/schedule-rules.tsx`）保留页内 Popover+Command 组合并登记豁免（`ui/combobox` 未透出 id/aria-invalid/aria-describedby 表单语义，2026-11-18）
- [x] 全站 ui/ 之外无 `role="switch|tablist|tab|dialog|alertdialog|listbox"`（violation 清零，04 票的提示类 AlertDialog 除外）
- [x] vitest：组合编辑器 tab 切换、日志路由链、两处 combobox 检索回归通过
- [x] build + vitest + lint 绿；组合编辑器/日志/试验/模型页复拍
