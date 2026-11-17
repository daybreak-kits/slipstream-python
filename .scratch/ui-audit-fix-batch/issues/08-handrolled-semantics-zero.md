# 08: 手写基元语义清零

**What to build:** 四处手写交互语义收敛到基元层：组合编辑器内手写 tablist 换 `ui/tabs`；日志路由链浮层换 `ui/popover`；试验页模型选择与模型页排期规则的 combobox 收敛到 `ui/combobox`（保留选项内 spinner 等既有能力）。用户感知：交互行为（键盘/焦点/Escape）全站一致。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

依据：ADR-081 决策 2；audit.md 组件治理盘点（4 违例锚点）。

- [ ] 组合编辑器候选 tab 换 `ui/tabs`，aria-selected 由基元接管
- [ ] 日志路由链浮层换 `ui/popover`，Escape/外点关闭行为保持
- [ ] 试验页与模型页 combobox 收敛到 `ui/combobox`，检索/空态/禁用行为等价
- [ ] 全站 ui/ 之外无 `role="switch|tablist|tab|dialog|alertdialog|listbox"`（violation 清零，04 票的提示类 AlertDialog 除外）
- [ ] vitest：组合编辑器 tab 切换、日志路由链、两处 combobox 检索回归通过
- [ ] build + vitest + lint 绿；组合编辑器/日志/试验/模型页复拍
