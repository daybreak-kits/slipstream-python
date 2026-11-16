# 视觉巡检 D（task-79）：组合 / 模型管理 / 目录 / 试玩

- 巡检人：t4-protocol　范围：`src/pages/{combos,catalog,playground,models}.tsx`、`src/components/{combos,models,catalog}`、截图目录 `e2e/audit`
- 截图目录：`web/e2e/audit/`，命名 `<page>-<state>.png`，共 23 张覆盖本批页面
- 门禁：`npx tsc --noEmit` EXIT 0 ✅；`npm run lint:ui` 0 error / 19 warning（≤19）✅
- 未能执行：`vitest`（沙箱禁止 node 子进程，vitest worker 无法启动）；已静态核对 `src/__tests__/combos.test.tsx` 等断言，未引用本次改动的文案与英文 `Close` 按钮，断言不受影响
- 未能执行：修复后复核截图（沙箱禁止 node 子进程 → vite/esbuild 无法启动 → 既不能起 dev server 也不能重建 `dist`）。下方截图均为**修复前**的线上构建（`dist` 2026-11-14 23:56）状态，修复本身以静态门禁 + 代码复核为准

## 试玩 /playground

| # | 问题 | 严重度 | 是否已修 | 证据 |
|---|---|---|---|---|
| 1 | 组合/分组的模型说明重复且缺空格：`kind` 与 `channel_name` 同名直接拼接，渲染成「显式组合 ·显式组合」 | 中 | 已修（新增 `modelRouteLabel()`，输出「显式组合 · 按成员优先级路由」） | [playground-model-selected.png](playground-model-selected.png)、[playground-streaming.png](playground-streaming.png) |
| 2 | 流式/等待/完成三态没有 live region，屏幕阅读器完全收不到响应；发送失败也没有 `role=alert` | 中 | 已修（响应卡加 sr-only `role=status` 状态播报 + `aria-busy`；错误 Alert 加 `role=alert`） | [playground-streaming.png](playground-streaming.png)、[playground-result.png](playground-result.png) |
| 3 | 流式进行中表单仍可编辑（可换模型/改参数而不打断当前请求） | 低 | 只报告（设计取舍） | [playground-streaming.png](playground-streaming.png) |

窄屏（390）单列布局无溢出、无文字截断：[playground-narrow-390.png](playground-narrow-390.png)；焦点环可见：[playground-focus-ring.png](playground-focus-ring.png)。

## 模型目录 /catalog（模型管理 · 模型目录 tab）

| # | 问题 | 严重度 | 是否已修 | 证据 |
|---|---|---|---|---|
| 4 | 组合行「渠道」列与「类型」列重复显示「显式组合」（后端对 combo/group 的 `channel_name` 就是类型名） | 中 | 已修（非直连行渠道列显示「—」） | [models-catalog-default.png](models-catalog-default.png) |
| 5 | 组合行「最大输入」显示 `0`，误导为「上限为 0」（真实上限未知；同表输入价/输出价已是「—」口径） | 中 | 已修（非直连行显示「—」） | [models-catalog-default.png](models-catalog-default.png) |
| 6 | 搜索/筛选无结果时仍引导「去创建渠道」（错误引导：模型存在，只是没匹配上） | 低 | 已修（有筛选条件时改为「清空筛选条件」，文案同步切换） | [models-catalog-search.png](models-catalog-search.png) |
| 7 | 1440 宽下表格 1157px > 容器 1126px，「操作」列被裁 31px（容器可横向滚动，但滚动条在窄容器/无头环境下不可见，视觉上像被截断） | 低 | 只报告（改列宽属设计取舍；建议 <2xl 时把「加入组合 / 封禁」收成图标态，或给表格容器加 `scrollbar-thin`） | [models-catalog-default.png](models-catalog-default.png)、[models-catalog-default-full.png](models-catalog-default-full.png) |
| 8 | 真·空目录与筛选无结果同形（同一句「没有匹配的模型」） | 低 | 已修（文案分支：无筛选保留创建引导，有筛选给清空入口） | [models-catalog-empty.png](models-catalog-empty.png) |

协议筛选下拉打开态正常（选中态有勾、4 个协议项、焦点在触发器）：[models-catalog-protocol-open.png](models-catalog-protocol-open.png)。

## 组合 /models?tab=combos

| # | 问题 | 严重度 | 是否已修 | 证据 |
|---|---|---|---|---|
| 9 | 组合编辑器是受控打开（无 `DialogTrigger`），Radix 关闭时把焦点归还给 null trigger → 焦点掉到 `body`，键盘用户丢失位置（Esc / X / 点遮罩关闭都复现） | 中 | 已修（`pages/combos.tsx` 打开前记录触发元素 → 编辑器 `onCloseAutoFocus` 手动 `focus()` 归还；Element 已卸载时自动跳过） | [combos-editor-edit.png](combos-editor-edit.png)、[combos-editor-validation-error.png](combos-editor-validation-error.png) |
| 10 | 编辑器关闭按钮可访问名是英文「Close」（vendored `ui/dialog.tsx` 的 sr-only 文案） | 低 | 已修（本页改 `showCloseButton={false}` + 自绘 `DialogClose`，`aria-label="关闭组合编辑器"`，与 `pages/keys.tsx` 口径一致）；**vendored 默认仍是英文，建议 lead 在 `ui/dialog.tsx`/`ui/sheet.tsx` 加 `closeLabel` 或统一中文 sr-only** | [combos-editor-new.png](combos-editor-new.png) |
| 11 | 校验错误（如「组合名称不能包含斜杠」）渲染成普通 Alert，无 `role=alert`，屏幕阅读器不播报 | 中 | 已修（`role="alert"`） | [combos-editor-validation-error.png](combos-editor-validation-error.png) |
| 12 | 390 窄屏下隐式分组表依赖横向滚动，滚动条不可见时成员链看起来被裁 | 低 | 只报告（表格整体可滚动，属既有响应式取舍） | [combos-narrow-390.png](combos-narrow-390.png) |

其余状态：新建态 [combos-editor-new.png](combos-editor-new.png)、编辑态（名称自动全选、成员 priority/同层标记/上移下移禁用态正确）[combos-editor-edit.png](combos-editor-edit.png)、候选池/已选成员 [combos-editor-members.png](combos-editor-members.png)、候选面板（渠道模型 / 子组合 tab）[combos-editor-candidates.png](combos-editor-candidates.png)、分组与组合列表 [models-combos-default.png](models-combos-default.png)。

## 模型管理 /models（tab 容器）

| # | 问题 | 严重度 | 是否已修 | 证据 |
|---|---|---|---|---|
| 13 | tab 计数与列表口径一致（17 个模型 / 6 显式 · 9 隐式），roving tab 键盘可达，无问题 | — | 无需修 | [models-catalog-default.png](models-catalog-default.png)、[models-combos-default.png](models-combos-default.png) |

## 全局（跨页）

| # | 问题 | 严重度 | 是否已修 | 证据 |
|---|---|---|---|---|
| 14 | Tailwind 3 变量简写复查：全仓扫描 `[--` 仅命中 `src/components/ui/calendar.tsx:32` 的合法自定义属性声明（`[--cell-size:2.5rem]`），vendored 已统一为 v4 写法 `origin-[var(--radix-…)]` / `max-h-[var(--radix-…)]`，无尺寸塌陷风险 | — | 无需修（本轮复查已干净） | 静态扫描 |
| 15 | 本批 4 个页面无任意尺寸（`h-[Npx]`/`w-[Npx]`）残留（匹配数 0），控件尺寸一致 | — | 无需修 | 静态扫描 |

## 总评

- 本轮共 15 项：已修 9、只报告 3（均为设计取舍）、复查无问题 3；无阻塞级问题。
- 修复集中在本任务 write scope 内，未触碰 vendored 原子件与全局样式。
- 两条系统性建议转 lead：① `ui/dialog.tsx` / `ui/sheet.tsx` 的关闭按钮 sr-only 文案应中文化并支持 `closeLabel`；② `ui/alert.tsx` 建议默认 `role="alert"` 或提供 `role` 透传约定，避免各页自行补。
- 环境限制：沙箱禁止 node 子进程 → vitest / vite build / dev server 全部不可运行，修复后的截图复核无法完成（已静态复核 + 门禁通过）。
