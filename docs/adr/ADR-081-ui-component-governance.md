# ADR-081 UI 组件来源治理

- 状态：accepted（2026-11-18）
- 关联：ADR-064（shadcn copy-in 全量迁移）、`docs/ui.md` §2.1（组件来源治理条款）与 §2.5（门禁探针）

## 背景

全站 unslop 审计（playwright-mcp CRUD 实测轮）回答了"组件是不是都在系统里"：

- `components/ui/` 43 文件 = 27 vendored + 11 并入 + 4 自建 + 1 hook，台账一致；**Radix 原语全仓只在 ui/ 内 import，零绕过**——这一层是健康的。
- 但应用层仍有 4 处手写基元语义：`channels/enabled-toggle.tsx` 手写 `role="switch"`（`ui/switch` 在库零调用）、`combos/combo-editor-dialog.tsx:947` 手写 `tablist`、`logs.tsx` RoutePopover 手写 dialog 浮层、playground 与 models 各有一套 combobox 并行实现（未组合 `ui/combobox`）。
- 同时 **6 个基元零调用**：avatar、breadcrumb、form、progress、scroll-area、switch。"组件在库里、页面在外面写"同时发生——这就是规范脱落的机制：库越攒越多，页面越写越偏。

## 决策

1. **Radix 边界不变**：Radix 原语只允许在 `components/ui/` 内 import（现状合规，长期保持）。
2. **禁手写基元语义**：`role="switch"/"tablist"/"tab"/"dialog"/"alertdialog"/"listbox"` 不得出现在 ui/ 之外；现存 4 处列入修复批次清零（switch 由 `ui/switch` 接线、tablist 换 `ui/tabs`、RoutePopover 换 `ui/popover`、combobox 收敛到 `ui/combobox`）。
3. **自建件登记制**：C 类自建件（combobox/time-range-picker/icon-badge/metric-pills）必须登记在 ui.md §3；新自建件先证明 registry 与现有 42 件都覆盖不了。
4. **死基元二选一**：0 调用基元三个月内接线或移除，禁止"先 copy 备着"。本轮：`switch` 接线；`avatar/breadcrumb/form/progress/scroll-area` 五件移除并登记 ui.md §3 已删除（将来需要时一次 shadcn copy-in 即回，先例：modal/search-select/toast）。
5. **治理探针为 warning 级**（用户裁定：不设 error 拦截）：三条探针（手写基元语义 / ui 外 Radix / `transition-all`）进 `lint-ui.mjs` 只计 warning 不阻断，`--max-warnings 19` 上限不变；修复批清零现存违例后探针维持 0 命中。

## 理由

- 备选"保留死基元 + 待接线标注"被否：标注挡不住下一页继续手写；移除后 copy-in 的成本（几分钟）远低于规范漂移的成本。
- 备选"探针设 error 级拦截"被否：用户裁定拦截告警分离——现存违例清零后 warning 级已足以防回潮，且避免第三方 vendored 更新时门禁误伤。
- ADR-064 的"vendored 冻结"约束的是**不修改上游源码**，不约束删文件；已删除类有先例。

## 后果与影响

- `ui/switch` 成为启停控件的唯一实现（EnabledToggle 重写为组合 `ui/switch`）。
- 5 个死基元文件删除；`docs/ui.md` §3 已删除类 4 → 9 件。
- `lint-ui.mjs` 新增三条 warning 级规则；修复批次完成时全站探针归零。
- 未来"要不要用某基元"的判断依据从记忆变成 `ui.md` §3 清单 + 探针。

## 相关代码与文档

- `docs/ui.md` §2.1/§2.5/§3
- 违例锚点：`components/channels/enabled-toggle.tsx:14`、`components/combos/combo-editor-dialog.tsx:947`、`pages/logs.tsx:321`、`components/playground/model-combobox.tsx`、`components/models/schedule-rules.tsx`
- 探针命令见 `docs/ui.md` §2.5
