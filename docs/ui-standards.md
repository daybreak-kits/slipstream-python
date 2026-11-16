# 前端 UI 规范

> 状态：v1（2026-11-14 定稿）。适用对象：人类开发者与 coding agent。
> **本文是 UI 的唯一规范正文**：token、组件 recipe、页面模式、评审清单、现状约束与 §11 视觉语言（含十条反例）都在这里。**现状台账**（42 个基元 × 调用点、门禁阶梯、欠账表）见 [ui-adoption-map.md](./ui-adoption-map.md)；技术栈取舍见 [ui-stack.md](./ui-stack.md)。
> 冲突时以**代码与 ADR** 为准，并当场改本文——不允许两份文档各自为政。

## 0. 适用范围

本规范约束 web/src 下的所有用户可见界面：页面、布局、弹窗、表格、图表、空态、错误态、文案。它不约束 lib 层的数据转换，也不约束 docs 里的示意图。

规范的事实来源只有三处：
1. index.css 的 CSS 变量（颜色、焦点环、图表色板）。
2. `index.css` 的 `@theme` 语义映射（色名、圆角、阴影、动画；Tailwind 4 已无 `tailwind.config.js`）。
3. components/ui 下的基元实现（尺寸、状态、键盘、aria）。

页面不是事实来源。页面里出现的 h-7、text-[10.5px]、rounded-[7px] 都是待收敛的技术债，不是可复制配方。

## 1. 五条原则

1. **单一来源**：同一控件只能有一个实现。页面不得直接写 select、table、menu、dialog、tooltip 的交互骨架；只能用 components/ui 的基元，或在基元不足时先补基元。
2. **token 即约束**：颜色只写语义名，尺寸只写阶梯值，圆角只写 control/card/badge。页面里出现 hex、text-[Npx]、h-[Npx]、rounded-[Npx] 即视为不合规。
3. **一个视图一个主动作**：每屏最多一个 primary Button。其余动作按 outline、ghost、link 降级；危险动作必须用 danger，且必须二次确认。
4. **键盘是一等公民**：组件验收必须包含 Tab 顺序、Enter/Space、Escape、方向键、焦点陷阱、关闭后焦点归还。只有鼠标能完成的交互不算完成。
5. **文案是界面的一部分**：按钮写动作，错误写原因与下一步，空态写为什么空。文案不通过评审，界面不算通过。

## 2. Design tokens

### 2.1 颜色

颜色只在 `index.css` 声明浅色与深色两套；组件与页面只使用 Tailwind 4 `@theme` 转发出的语义类名。`@theme inline` 必须带 `inline`，否则 `var()` 在定义处求值、深色主题拿不到覆盖（`index.css:52-55` 的注释就是这次踩坑的记录）。禁止在组件里写 `dark:` 前缀重复声明（vendored 件里的上游写法见 §6 覆盖段）。

**色值不在本文复制**：浅色见 `index.css` 的 `:root`（`index.css:108-217`），深色见 `.dark`（`index.css:219-289`）；**命名语义**（如 `muted` = 弱表面、`muted-foreground` = 文字层）以 [ADR-066](./adr/ADR-066-token-semantics-adoption.md) 决策 1 为准。要查「某个 token 到底是什么颜色、对应哪个变量」，直接读 `index.css`——文档里再抄一份就多一处必然漂移的副本（这一次收敛已经为此返工过）。

#### 图表色板

- chart-1 到 chart-16 共 16 槽，浅色见 index.css:79-94，深色见 :154-169。
- 只通过 lib/chart-colors.ts 的 assignCategoryColors 使用；不要按指标类型重新定义颜色，不要新增第 17 色。
- 系列名为空时图例文案沿用「请求模型无可用渠道」。

### 2.2 字号与字重

正文/控件只允许四档；页面 h1、指标大数字、弹窗标题是基元内部例外，不允许页面自己写。

| 用途 | 字号 | 行高 | 现状写法 | 目标 |
|---|---:|---:|---|---|
| 角标/辅助说明 | 11px | 16px | text-[10.5px]/text-[11px] | text-2xs |
| 元信息/表格副行 | 12px | 18px | text-[12px]/text-[12.5px] | text-xs |
| 控件/正文 | 13px | 20px | text-[13px]/text-[13.5px] | text-sm |
| 区块正文 | 14px | 22px | text-[14px] | text-base |
| 页面 h1 | 22px | 28px | text-[22px] | 仅 PageHeader |
| 弹窗标题 | 15px | 22px | text-[15px] | 仅 Modal |
| 指标大数字 | 19px | 24px | text-[19px] | 仅 MetricStrip |

实施说明：目标 utility 名在 `index.css` 的 `@theme` 里用 `--text-*` 注册（Tailwind 4 已无 `tailwind.config.js`）；若不想覆盖默认 `text-sm`=14，可改用 `text-caption`/`text-meta` 等别名，但全站只能有一套，且页面只能使用这四个别名，不允许任意值。新增字号 token 前，任意值写法只能减少、不能增加。

字重只允许 400、500、600。标题 tracking 只用 normal 或 -0.02em；8px 以上的字距只用于全大写小标签（如指标名、表头），不允许正文使用。

### 2.3 控件高度

常规控件三档：32 / 36 / 40。Button lg 的 44 只用于登录、空态主 CTA，不进入常规表单阶梯。

| 档位 | 高度 | Button | Input | IconButton |
|---|---:|---|---|---|
| sm | 32 | size=sm h-8 | [待新增] size=sm | 32×32 |
| md | 36 | size=md h-9 | [待新增] size=md | 36×36 |
| lg | 40 | size=default h-10 | h-10 | 40×40 |

- 同一行内的输入、选择、按钮必须同高；不允许按钮 40、旁边 select 32。
- 图标按钮必须是 32/36/40 的正方形，不允许 h-6/h-7/h-8 混用；表格行内操作若必须紧凑，用 32 并保持行高。
- 图标尺寸：按钮内 14/16/18，行内 12/14；不要用 20 以上除非空态插画。

### 2.4 圆角、阴影

| token | 值 | 用途 |
|---|---:|---|
| rounded-badge | 6px | 徽章、极小控件 |
| rounded-control | 8px | 按钮、输入、选择、菜单项 |
| rounded-card | 12px | 卡片、弹窗、抽屉 |
| rounded-full | 9999px | 头像、圆点、开关轨道 |

阴影只有两档：shadow-card（卡片静止）、shadow-pop（弹层、菜单、toast）。禁止自写 box-shadow；hover 不加阴影，只改底色/边框。

**token 语义以 [ADR-066](./adr/ADR-066-token-semantics-adoption.md) 为准**（决策 1、2）：`--color-muted` 是**表面**语义（`index.css:63` → `--surface-2`，与 shadcn 用 `bg-muted` 当弱化表面一致），文字层级用 `text-muted-foreground`（`index.css:99` → `--muted-foreground`，源头 `--text-muted` 在 `index.css:116`）；圆角是**两套标尺同值并存**——语义档 `--radius-badge 6 / --radius-control 8 / --radius-card 12`（`index.css:14-16`）与 shadcn 数值档 `--radius-sm 6 / md 8 / lg 12 / xl 16`（`index.css:20-23`），vendored 件写 `rounded-md` 拿到的就是 control 档，`xl` 只给浮层内嵌容器。本节表的语义三档仍有效，**数值与新增档以 ADR-066 为准**，本文不另述。

### 2.5 间距与布局

- 基数 4px；允许 2、4、6、8、10、12、16、20、24、32。禁止 5、7、9、11、13、14、15、18、28 等中间值。
- 页面左右 padding：移动 16，桌面 24。页面 section 间距 24；卡片内 padding 20（紧凑）或 24（常规）。
- 表单字段纵向 gap 16；label 与控件 6；控件与帮助/错误 6。
- 按钮组 gap 8；表格单元格 px 12/20、py 8/12；表格行高不低于 44。
- 任何横向布局必须定义窄屏退化：flex-wrap、grid 降列、或 overflow-x-auto；不允许靠 min-w 把页面撑出屏幕。

### 2.6 层级 z-index

只允许以下层级；新增层级必须先改本表。

| 层 | z | 内容 |
|---|---:|---|
| 表格吸顶列 | 5 | 表格左右吸附列（低于表头，避免盖住表头） |
| sticky | 10 | 表头、吸顶工具条 |
| topbar | 30 | 顶栏 |
| popover | 40 | 下拉、tooltip、搜索建议 |
| modal | 50 | 模态、抽屉、移动导航 |
| toast | 70 | 全局通知 |

### 2.7 动效

- 颜色/边框/背景过渡 150ms；进入/离开 200ms ease-out；不允许超过 300ms。
- 只允许 fade-up、fade-in 两种内置动画；新增动画必须先注册进 `index.css` 的 `@theme`（`--animate-*` 与 keyframes 都要在 `@theme` 内，写在 `@layer utilities` 里不生效）。
- 必须尊重 prefers-reduced-motion：在该媒体查询下把 animation/transition 设为 none 或极短。
- 加载中不要用布局位移换动画；骨架屏尺寸必须与最终内容一致。

## 3. 组件规格 recipe

每个基元都按 anatomy、尺寸、状态、键盘、aria 五项验收。组件来源与单一来源见 §3.0；**规格以 `web/src/components/ui/` 的代码为准**。§3.2/§3.3 的表是**验收口径**：9847b66 后这些基元绝大多数已 vendored 落地（个别仍未接线的按 [待新增] 处理），逐件现状看 [ui-adoption-map.md](./ui-adoption-map.md) §0/§1。

### 3.0 组件来源：单一来源是 `web/src/components/ui/`

**所有基元组件只有一个来源：`web/src/components/ui/`（当前 42 个 .tsx）**，页面不自带控件实现。按「来源 + 归属」分三类（2026-11-14 lead 裁定）：

| 类别 | 数量 | 文件 |
|---|---|---|
| **A 类：shadcn registry 源码**（new-york v3 风格，Radix 底座） | **27** | alert-dialog、avatar、breadcrumb、calendar、checkbox、collapsible、command、dialog、dropdown-menu、field、form、input-group、popover、progress、radio-group、scroll-area、select、separator、sheet、sonner、switch、table、tabs、textarea、toggle、toggle-group、tooltip |
| **B 类：本仓自有、正合并到 shadcn 基座** | **11** | button、badge、card、input、alert、label、pagination、skeleton、spinner、empty、confirm-dialog |
| **C 类：本仓自有、保留** | **4** | combobox、time-range-picker、icon-badge、metric-pills |

27 + 11 + 4 = **42** ✓（与目录实际文件一一对应，无重复无遗漏）。`modal` / `search-select` / `toast` 已于 9847b66 删除，`confirm-dialog` 归 B 类。

- **与 git 提交口径的换算**：`git show --name-status 23a2c9a -- web/src/components/ui` 显示「新增 31 + 修改 4」；其中**新增 31 = A 类 27 + 批 1 的 4 个原子件（badge、skeleton、spinner、empty，属 B 类）**，修改 4 = button、input、search-select、time-range-picker。git 按「这次提交动了哪些文件」分，A/B/C 按「这个组件是谁的、下一步去哪」分，**两个口径不冲突**，引用时别混。
- A 类**只允许改 token 类与尺寸类，不改结构**（本仓对 vendored 源码的固定约束：换 token 类、换尺寸类可以；动 DOM 结构、公开导出名或交互行为不行。这条规则自足，不依赖任何已被取代的 ADR 编号）；**例外（2026-11-15 裁定）**：允许 A 类补一条受控行为 —— 受控打开（无 Trigger）时由基元归还焦点，实现在 `src/lib/dialog-opener-focus.tsx`，只挂在 dialog / sheet / alert-dialog 的 Content 上，不改 DOM 结构与公开导出；页面不再各自补焦点逻辑。B 类迁完保留原公开入口（导出名、`size`/`variant` 取值、slot 名），页面侧不批量改；C 类 4 件：`combobox`（本地组合件）与 `time-range-picker`（Calendar + 小时 wrapper 重写完成）保留，`icon-badge` / `metric-pills` 无对应件、永久保留。批 2 新增的 `confirm-dialog`（本仓自有，shadcn `alert-dialog` + `button` 组合，替代 `window.confirm`）归 B 类。
- 新增控件先查上表：shadcn 有就 copy-in + 换 token 类，别手写第二套。
- vendored 件里的 `dark:` 前缀属上游写法，处理方式见 §6 覆盖段（vendored 目录内只计 warning），**不改上游源码**。

### 3.1 基元 recipe（B 类与 C 类；vendored 件见 §3.0）

| 基元 | anatomy 与尺寸 | 状态 | 键盘 | aria |
|---|---|---|---|---|
| Button | 根 button；可选前置/后置图标；6 variant × 5 size；默认 h-10，md 36，sm 32，lg 44，icon 40×40 | default/hover/active/focus-visible/disabled/loading | 原生 Enter/Space；loading 时 disabled 且不重复提交 | 图标按钮必须有 aria-label；loading 加 aria-busy=true |
| Alert | 根 div；图标 + 标题 + 描述；4 variant；text 13 | default/destructive/warning/success | 无 | role=alert；图标 aria-hidden；标题用 AlertTitle |
| Card | 根 div；Header/Title/Description/Content/Footer；p-6 | 静止/hover 仅可点击卡 | 无 | 可点击卡必须是 a/button，不能给 div 加 onClick |
| IconBadge | 根 span；图标 + tone；3 size；圆角 control | 8 tone × 3 size | 无 | 图标 aria-hidden；装饰性容器不加 role |
| Input | 根 input；h-10；px-3；focus ring 2 + offset 2 | default/hover/focus/disabled/error [待新增] | 原生输入 | 必须有 Label 或 aria-label；错误用 aria-invalid + aria-describedby |
| Label | 根 label；text 13 medium | 无 | 点击聚焦关联控件 | htmlFor 必须指向控件 id；不要用 placeholder 代替 label |
| MetricStrip | 根 div；2/3/5 列；每项 label/value/hint/estimated | loading/error/正常 | 无 | 容器 aria-label；数值变化不强制 aria-live，但错误要可见 |
| Dialog / AlertDialog / Sheet | overlay + content + header/title/description/body/footer；圆角 card | open/closed/scrollable/footer 响应式 | Escape 关闭；Tab 循环；Enter 不默认提交 | role=dialog aria-modal=true aria-labelledby；必须有可读 title；打开后焦点入内，关闭归还（受控打开时由 `src/lib/dialog-opener-focus.tsx` 归还） |
| ConfirmDialog | AlertDialog + 取消/危险两个动作；标题是问句 | 只需 idle/pending（异步确认期间禁用 Action、不自动关闭） | Escape 取消；默认焦点在取消 | AlertDialogDescription 必须有可读文案；危险按钮用 danger 变体 |
| Combobox | Radix Popover + Command；触发器 + 输入 + 列表 | closed/open/loading/empty/selected | ArrowUp/Down 移动，Enter 选中，Escape 关闭，输入过滤 | 触发器 role=combobox aria-expanded；空值哨兵见 §3.4 |
| Sonner Toaster | `ui/sonner.tsx` 导出 `Toaster`，App 挂一次 | success/error/warning/info；自动/手动关闭 | 关闭按钮可 Tab；不抢焦点 | 容器 aria-live；error 用 role=alert；不允许再引入第二套 toast |
| Pagination | nav + 总数 + 每页 select + 4 个翻页按钮 + 页码 | 首页/末页/禁用/页大小 | 原生 Tab/Enter | nav aria-label=分页；当前页 aria-current=page；页大小 select 有 label |

| TimeRangePicker | trigger + dialog + 预设 + 自定义起止 + 应用 | closed/open/invalid/selected | Escape 关闭；Tab 到每个预设；Enter 选择 | trigger aria-haspopup=dialog；预设按钮 aria-pressed；错误文案与输入关联 |



### 3.2 计划内 5 个基元

| 基元 | anatomy 与尺寸 | 状态 | 键盘 | aria |
|---|---|---|---|---|
| Select [待新增] | trigger button + 弹层 listbox + option + 可选搜索；高度 32/36/40 | closed/open/disabled/error/loading/empty | Enter/Space 打开；Arrow 移动；Home/End；typeahead；Escape 关闭并归还焦点 | trigger aria-haspopup=listbox aria-expanded；listbox role=listbox；option role=option aria-selected；用 aria-activedescendant |
| Dropdown [待新增] | trigger + menu + menuitem/menuitemradio + 分隔线 | closed/open/disabled | Enter/Space/ArrowDown 打开；Arrow 移动；Escape 关闭；Tab 离开即关闭 | trigger aria-haspopup=menu aria-expanded；menu role=menu；项 role=menuitem；checked 用 menuitemradio+aria-checked |
| Tooltip [待新增] | 触发元素 + 浮层；最大 280px；深色底 | hidden/visible | 触发元素 focus 时显示；Escape 关闭；不可包含交互元素 | 触发元素 aria-describedby；浮层 role=tooltip；不要只靠 title |
| Tabs [待新增] | tablist + tab + tabpanel；下划线/分段两种外观 | active/inactive/disabled | ArrowLeft/Right 移动；Home/End；Enter/Space 激活；roving tabindex | tablist aria-label；tab aria-selected aria-controls；panel role=tabpanel aria-labelledby；panel tabIndex=0 |
| Table [待新增] | 容器 + table + caption + thead/tbody + 空态/加载/分页插槽 | loading/empty/error/selected/sorted | 行内按钮原生 Tab；可排序表头 Enter/Space；行选择用 checkbox | caption 或 aria-label；可排序 th aria-sort；全选 checkbox aria-label 与 indeterminate；空态文案在表格内 |

### 3.3 其它缺口（按优先级）

| 基元 | 关键要求 |
|---|---|
| Textarea [待新增] | 高度 min 96；resize-y；字数计数；错误态；与 Input 同边框/focus |
| Checkbox [待新增] | 16×16；label 整行可点；indeterminate；错误态 |
| Radio [待新增] | 16×16；同组 name；方向键切换；label 整行可点 |
| Switch [待新增] | 20×36 轨道；role=switch aria-checked；loading/disabled；点击整行可切换 |
| Field/FormRow [待新增] | label + 必填标记 + 控件 + 帮助 + 错误；统一 gap；错误 aria-describedby |
| ConfirmDialog [待新增] | 标题是问句；正文写后果；取消在左、危险在右；禁止 window.confirm |
| Drawer [待新增] | 右/下两种；焦点陷阱；Escape；遮罩；宽度 360/480 |
| Badge [待新增] | 6 tone；badge 圆角；只读；计数与状态共用 |
| EmptyState [待新增] | 图标 + 标题 + 一句说明 + 一个 CTA；区分无数据/无匹配 |
| Skeleton [待新增] | 与最终尺寸一致；2-3 个脉冲块；不要整页 spinner |
| SearchInput [待新增] | Input + 搜索图标 + 清除按钮；aria-label；Enter 提交 |
| CopyButton [待新增] | 复制 + 成功态 2s + toast/aria-live；失败可重试 |
| CodeBlock [待新增] | 深色底；等宽；横向滚动；复制按钮；行号可选 |
| PageHeader [待新增] | h1 22 + 描述 + 右侧动作；移动端动作换行 |
| SegmentedControl [待新增] | 容器 + 2-5 段；aria-pressed；同高；键盘方向键可选 |
| ChartContainer [待新增] | 标题 + 图例 + SVG + 文本摘要/数据表；响应式 |
| ScrollArea [待新增] | 统一滚动条；键盘可滚动；横向滚动提示 |

### 3.4 Select 用法（Radix 空值哨兵）

Radix Select 不允许 `<SelectItem value="">`（空串留给「未选中」），所以「全部 / 不限」这类筛选项必须用一个**哨兵值**占位，再在 `onValueChange` 里映射回业务空值 `""`。

- **单一来源**：哨兵值放共享模块 `src/lib/select-sentinels.ts`，只导出两个常量：`export const SELECT_ALL_VALUE = "__all__";`、`export const SELECT_NONE_VALUE = "__none__";`（与 `lib/utils` 同层，不进 `components/ui/`）。
- **命名固定**：`SELECT_ALL_VALUE` / `SELECT_NONE_VALUE`；值固定 `"__all__"` / `"__none__"`（双下划线包裹，保证不与业务 id 撞车，且一眼能看出不是真实取值）。
- **禁止**：页面里写字面量 `"all"`、`"__all__"`、`"__none__"`；也禁止各页自起名（`ALL_FILTER_VALUE` 这类页面局部常量应改为 import 共享常量）。
- **写法**：`value={state || SELECT_ALL_VALUE}`、`onValueChange={(v) => setState(v === SELECT_ALL_VALUE ? "" : v)}`、`<SelectItem value={SELECT_ALL_VALUE}>全部…</SelectItem>` 放首项；`Select` 的 `value` 永远是非空串，`""` 只存在于业务 state。
- **为什么不用 disabled 的空 Item**：Radix 会把它当「未选中」显示 placeholder，视觉上分不清「全部」和「没选」，且键盘可聚焦到不可用项。
- **现状（9847b66 终态）**：哨兵已统一到 `src/lib/select-sentinels.ts`（`SELECT_ALL_VALUE` / `SELECT_NONE_VALUE`，各带一句用途注释）；`pages/catalog.tsx`、`pages/channels.tsx`、`pages/dashboard.tsx`、`pages/logs.tsx`、`pages/playground.tsx`、`components/ui/combobox.tsx` 全部从该模块 import，页面内已无字面量 `"all"` / `"__all__"` / `"__none__"`。**例外**：settings 的 `body_policy` 枚举（off/errors/all）是业务取值，不是空值哨兵，不套用本条。

## 4. 页面模式

### 4.1 表单页 / 弹窗表单

正确：
- 顶部标题与说明；字段按业务分组，每组一个 section 标题。
- 每个字段：Label（htmlFor）+ 控件 + 可选帮助文字 + 错误文字；错误同时设 aria-invalid 与 aria-describedby。
- 主动作在右下；取消在左；提交中按钮 loading 且禁用；成功后 toast + 关闭/跳转。
- 有未保存修改时，关闭弹窗/离开页面要确认。

反例：
- 只有 placeholder 没有 label；错误用 alert 弹窗；提交中可重复点击；点遮罩直接丢改动。
- 数字/日期控件与旁边输入不同高；错误文案只在 toast 里出现。

### 4.2 表格页

正确：
- PageHeader + 筛选条（搜索、状态、时间范围、更多筛选）+ 表格 + 分页。
- 筛选项写入 URL；刷新可恢复；有「清除筛选」。
- 加载：首次骨架或整块 loading；切换筛选保留旧数据并弱化；空态区分无数据/无匹配。
- 错误：Alert + 重试，不清空筛选。
- 行操作靠右；危险操作二次确认；批量操作在选中后出现。

反例：
- 筛选条件只在内存，刷新丢失；空态只写「暂无数据」；表格横向溢出但无滚动容器；排序图标是假的。

### 4.3 详情页

正确：
- 返回链接 + 标题 + 状态徽章 + 未保存徽章 + 右侧动作组。
- 分区用 Tabs 或 Card；每个分区可独立保存时，明确保存范围。
- 新建未保存时，依赖 ID 的分区显示「先保存」空态，而不是禁用整个页面。

反例：
- 顶部保存按钮保存全部但没有任何提示；切换 tab 丢改动；状态徽章与列表不一致。

### 4.4 设置页

正确：
- 按主题分组 Card；每组一个保存按钮，保存后显示更新时间；危险设置单独分组并二次确认。
- 选项卡片（radio card）整块可点；选中态不只靠颜色，还有边框/圆点。
- 密码修改：当前密码、新密码、确认密码；错误逐字段；成功后清空。

反例：
- 全页一个保存按钮但部分设置即时生效；选项只有颜色差异；密码错误只显示在顶部 Alert。

### 4.5 危险操作确认

正确：
- ConfirmDialog；标题是问句（例如「删除渠道？」）；正文写不可逆后果与影响范围；取消在左，danger 在右；按钮文案是动作（删除渠道），不是「确定」。
- 批量删除要列出数量/名称；删除最后一个密钥等特殊情况要额外说明。

反例：
- window.confirm；标题「提示」；正文「确定吗？」；两个按钮都叫「确定/取消」但不知道确定什么。

### 4.6 加载态

正确：
- 首次进入用骨架屏或整块 Loading；按钮动作用按钮内 spinner；页面级懒加载用顶部进度条 [待新增]。
- 加载态尺寸与最终内容一致，避免跳动；超过 300ms 才显示，避免闪烁。

反例：
- 每个按钮都转圈；整页 spinner 覆盖已有内容；加载时布局塌陷。

### 4.7 错误态

正确：
- 页面级：Alert destructive，说明发生了什么 + 重试按钮；保留用户输入。
- 字段级：字段下方红字，焦点移到第一个错误；aria-invalid/aria-describedby。
- 网络错误：describeApiError 统一文案；不要显示堆栈。

反例：
- 所有错误都 toast；错误后清空表单；显示「请求失败」没有下一步。

### 4.8 空态

正确：
- 图标/插画 + 标题 + 一句原因 + 一个 CTA；无匹配时给「清除筛选」，无数据时给「新建」。
- 空态在表格/卡片内部，不替换整个页面。

反例：
- 只写「暂无数据」；空态 CTA 指向不相关页面；空态与加载态同时出现。

### 4.9 通知

正确：
- 异步成功/失败用 toast；需要用户处理的问题用 Alert；同一事件不要同时 toast + Alert。
- toast 最多 4 条；错误停留更久；可手动关闭；不要把关键信息只放 toast。
- 表单校验错误不用 toast。

反例：
- 每个请求都 toast；toast 遮住主按钮；错误 toast 自动消失导致用户看不到。

### 4.10 导航

正确：
- 侧栏当前项有底色 + 文字色 + aria-current；分组标题不可点。
- 面包屑可点击到上级；页面标题与导航名称一致。
- 移动端用抽屉导航，打开后焦点入内，关闭归还。

反例：
- 当前项只换颜色；面包屑只是文本；移动端导航不收口。

## 5. 文案规范

### 5.1 按钮

- 动词 + 对象：保存、创建渠道、测试连接、获取上游模型、删除密钥、清除筛选。
- 禁止单独使用「确定」「提交」「是」「否」；危险按钮写清动作与对象。
- 同一屏主动作只有一个；次要动作按 outline/ghost/link 降级。
- 加载中保持动词不变，只加 spinner；不要改成「加载中」丢掉动作语义。

### 5.2 错误信息

结构：发生了什么 + 为什么 + 怎么办。

好例子：
- 「连接地址必须以 http:// 或 https:// 开头。请检查后重试。」
- 「该渠道仍被 2 个组合引用，无法删除。请先在组合中移除引用。」
- 「上游返回 401。密钥可能已失效，请更新渠道密钥后重试。」

坏例子：
- 「请求失败」
- 「系统错误，请稍后重试」
- 「Error: Network Error」

要求：API 错误统一走 describeApiError；字段错误放字段下；页面错误放 Alert；不要同时出现三处相同错误。

### 5.3 空态

- 标题说清状态，说明说清原因，CTA 说清下一步。
- 区分「还没有数据」与「没有匹配结果」；后者必须提供清除筛选。
- 不要用感叹号；不要写「快来创建吧」这类营销语。

### 5.4 时间、数字、百分比

唯一来源：lib/format.ts 与 lib/time.tsx。
- 日期时间：formatDateTime / formatDateTimeParts；尊重 UTC/本地时区切换。
- 计数：formatCount；大数用万/亿或 k/M，按现有实现。
- 百分比：formatPercent；不要手写 toFixed(1)+%。
- 耗时：formatDuration；单位 ms/s 自动。
- 价格：formatPricePer1m；保留币种与 /M 口径。
- 图表轴：formatTrendAxis；不要另写一套。
- 相对时间：用现有 formatRecoveryTime 等；不要引入新库。

## 6. 评审清单（10 条硬检查）

在合并前逐条勾；每条都可用 grep 或人工验证。

命令以 `rg` 书写；**本机没装 `rg` 时用等价的 PowerShell**：`Get-ChildItem src/pages,src/components -Recurse -Include *.tsx | Select-String '<pattern>' | Where-Object { $_.Path -notmatch 'components/ui' }`，结果应为 0 行（第 3 条只扫 `src/pages`，去掉 `src/components`）。字面片段（含正则元字符，如 `text-[`、`h-8`、`title=`、`<div`）加 `-SimpleMatch` 才不会被 PS 5.1 当正则解析报错；交替/字符类（如 `h-8|h-9`、`role=.menu[^a-z]`）保持正则、不加。**行数 ≠ 次数**：`Select-String` 默认数**命中行**（`(... | Measure-Object).Count`）；数**出现次数**必须走正则模式并 `-AllMatches` 累加 `Matches.Count`，字面量用 `[regex]::Escape()` 包一层（如 `Select-String -Pattern ([regex]::Escape('text-[')) -AllMatches`）——**`-SimpleMatch` 不填 `$_.Matches`，用它算次数恒为 0**。`rg -c` 数命中行、`rg --count-matches` 才数次数。要复现 lint 的 count 必须 **`-Include *.tsx`**（脚本只扫 `src/**/*.tsx`）、模式带 `\b` 词边界并保持正则配 `-AllMatches`：字号规则 `\btext-\[\d+(?:\.\d+)?px\]` = **660 次 = 660 行**（每行恰 1 处），尺寸规则 `\b[hw]-\[\d+(?:\.\d+)?px\]` = **100 次 / 94 行**（28 个文件，2026-11-14 20:3x；97 是 55 文件时的旧值），字面 `text-[` = **691 次**（多 31 次 = rem / var / 颜色等非字号写法）。只数行用 `(... | Measure-Object).Count` 即可：字号这条两种口径同值，尺寸那条差 6。

1. 页面没有原生 select：rg -n '<select' src/pages src/components --glob '!components/ui/**' 应为 0。（lint:ui `no-native-select` 已覆盖 · warn 级）
2. 页面没有原生 table：rg -n '<table' src/pages src/components --glob '!components/ui/**' 应为 0（临时迁移期只允许白名单）。（lint:ui `no-native-table` 已覆盖 · warn 级）
3. 页面没有 aria-haspopup 或 role=menu：rg -n 'aria-haspopup|role="menu"' src/pages 应为 0。（lint:ui 尚无此规则 · 仍靠 rg/PowerShell + 人工）（2026-11-14 实测：`src/pages` 为 0 行；全仓 `role="menu"` 2 处——`components/layout/topbar.tsx:153`、`components/layout/theme-menu.tsx:67`；`aria-haspopup` 4 处——`components/layout/topbar.tsx:139`、`components/layout/theme-menu.tsx:52`、`components/ui/search-select.tsx:131`、`components/ui/time-range-picker.tsx:99`；两组行号不同，均在 `components/` 不在 `pages`）
4. 没有新增任意字号/高度/圆角：git diff 中不出现 text-[、h-[、w-[、rounded-[；新增代码只用四档字号与三档高度。（lint:ui `no-arbitrary-font-size` / `no-arbitrary-size` 已覆盖 · warn 级；`rounded-[` 暂未纳入规则）
5. 没有新增 hex 或 dark:：rg -n '#[0-9a-fA-F]{3,6}|dark:' src/pages src/components --glob '!components/ui/**' 应为 0。（lint:ui `no-inline-hex` 是 error 级；`no-dark-prefix` **按目录分档**：应用代码 error、`src/components/ui/**` 内 warn——`index.css:6` 的 `@custom-variant dark (&:where(.dark, .dark *))` 已是类策略，vendored 的 `dark:` 不会把暗色机制换成 `prefers-color-scheme`，上游更新也不必手改，见 §6 覆盖段；当前 error 级 0 命中）
6. 每个表单控件有 Label 或 aria-label；每个错误有 aria-invalid 与 aria-describedby；用键盘可从第一个字段走到提交。
7. 每个 Modal/Drawer：打开后焦点在内部、Tab 循环、Escape 关闭、关闭后焦点回到触发元素、aria-modal 与 title 存在。
8. 每个图标按钮有 aria-label；不能只靠 title；每个图表有文本摘要或数据表；热力图可键盘遍历。
9. 每个危险操作走 ConfirmDialog；禁止 window.confirm；按钮文案是动作而不是「确定」。
10. 文案通过第 5 节检查；时间/数字/百分比走 lib/format 与 lib/time；空态有 CTA。

工具状态（2026-11-14 实测）：`web/scripts/lint-ui.mjs` **已落地**（零依赖，扫 `src`，跳过 `__tests__`/`*.test.tsx`/`*.spec.tsx`/`node_modules`/`dist`/`build`），用法 `node scripts/lint-ui.mjs [--json|--verbose|--max-warnings N|--max-errors N]`，退出码 0/1/2（error 级规则决定退出码）。`npm run lint:ui` **已落地**：`web/package.json` 的 `scripts.lint:ui` = `node scripts/lint-ui.mjs`（2026-11-14 lead 加入），两种跑法等价；CI 里用 `npm run lint:ui`。

第 1-5 条的覆盖情况：1 → `no-native-select`（warn，**0**）、2 → `no-native-table`（warn，**0**）、4 → `no-arbitrary-font-size`/`no-arbitrary-size`（**应用层 0**；vendored 例外 10 + 4 走 relaxed 桶，单列在 19 里）、5 → `no-inline-hex`（error 级，0 命中）与 `no-dark-prefix`（**按目录分档**：应用代码 error、`src/components/ui/**` 内 warn，当前 error 级 0 命中）；**第 3 条（`aria-haspopup`/`role=menu`）还没有对应规则**，仍靠 rg/PowerShell + 人工。当前实跑（2026-11-14 9847b66，`filesScanned=81`）为 **0 error / 19 warning**、`result: PASS`、exit 0——`window.confirm` 已由 t2 换成 `components/ui/confirm-dialog.tsx`，error 级规则全部清零（19 = vendored 例外：字号 10 + 尺寸 4 + `dark:` 5；应用层 0）。**`dark:` 口径已定案**：`no-dark-prefix` 按目录放宽（`relax: { dir: "src/components/ui/", level: "warn" }`），vendored 目录里的 5 处 `dark:`（`components/ui/input-group.tsx:17/30/139/155`、`components/ui/field.tsx:120`）只计 warning、不计入 error、不影响 `--max-errors`；理由是 `index.css:6` 的 `@custom-variant dark (&:where(.dark, .dark *))` 本来就是类策略，vendored 的 `dark:` 不改变暗色机制，只是那条规则当初写得太宽，上游更新 vendored 源码不必手改。**CI 阻断的前置只剩 warning 收紧**：`--max-warnings` 已按 [ui-adoption-map.md](./ui-adoption-map.md) §9 的预算阶梯收到**地板 19**（= 字号 10 + 盒尺寸 4 + vendored `dark:` 5 的常驻例外，不列待清量；应用层 0，新增一条即失败）。引用的文件数以当次 `--json` 的 `filesScanned` 为准（9847b66 快照是 81，工作树随后续提交变动）。

## 7. /dev/ui 组件画廊规格

目的：让基元的全部状态可被人眼和视觉快照同时检查；页面不再承担基元验收。

### 7.1 路由与开关

- 仅开发环境注册 /dev/ui；生产构建不包含入口。
- 不在侧栏导航出现；只能直接访问 URL。
- 页面顶部显示当前主题（浅/深）与视口宽度，便于截图对照。

### 7.2 必须展示的组件与状态矩阵

| 区块 | 必须展示 |
|---|---|
| Button | 6 variant × 5 size；default/hover/active/focus-visible/disabled/loading；图标左/右/仅图标 |
| Input/Textarea | 三档高度；default/focus/disabled/error；带 label/帮助/错误；密码显隐 |
| Select/SearchSelect | closed/open/loading/empty/truncated/disabled/error；键盘高亮项 |
| Checkbox/Radio/Switch | default/checked/indeterminate/disabled/error；整行点击 |
| Tabs | 2/3/5 个 tab；active/disabled；键盘焦点；长标签溢出 |
| Modal/ConfirmDialog/Drawer | 默认/长内容滚动/移动端全宽；焦点陷阱可见；危险确认 |
| Table | loading/empty/error/selected/sorted/横向滚动；全选 indeterminate |
| Badge/IconBadge | 全部 tone × size；长文本截断 |
| Toast/Alert | 4 tone；长描述；堆叠 4 条；手动关闭 |
| EmptyState/Skeleton | 无数据/无匹配；卡片/表格两种骨架 |
| Tooltip/Dropdown | hover/focus 触发；长文本；键盘导航 |
| ChartContainer | 折线/柱状/热力图；16 色图例；空数据；深色 |

### 7.3 视觉快照

- 视口：1440×900 与 390×844；主题：浅色 + 深色；共 4 张主快照。
- 关闭动画（prefers-reduced-motion 或注入样式），固定数据与时间，避免 flaky。
- 先人工过一遍，再接入 Playwright；快照变更必须在 PR 说明里写原因。
- 新增基元或改尺寸/颜色必须更新快照；只改页面不加基元时，页面截图另存，不混入基元快照。

## 8. 迁移顺序

1. **冻结**：新增代码不得引入新的原生 select/table/menu/任意值；老代码登记为债务。
2. **token**：补四档字号与三档高度到 `index.css` 的 `@theme`（Tailwind 4）；先改基元，再改页面。
3. **5 个基元**：Select、Table、Tabs、Dropdown、Tooltip；逐页替换 19 + 9 + 3 + 2 + 115 处。
4. **表单基元**：Field/FormRow、Textarea、Checkbox、Radio、Switch、FieldError；替换三份 FieldError。
5. **反馈基元**：ConfirmDialog、EmptyState、Skeleton、CopyButton、Badge；收敛 15 个 Modal 确认点。
6. **页面 pattern**：按第 4 节逐页重写；每页补键盘与空/错/载三态。
7. **防护**：`npm run lint:ui`（= `node scripts/lint-ui.mjs`，npm script 已落地）+ /dev/ui + 视觉快照进 CI；清单第 1-5 条变为阻断——前置条件是 error 级清零（第 3 条先补规则），warning 按 [ui-adoption-map.md](./ui-adoption-map.md) §9 预算阶梯逐批收紧（当前地板 19 = 字号 10 + 盒尺寸 4 + vendored `dark:` 5）。

验收标准：任意页面在 1440 与 390 宽度下，键盘可完成主流程；无原生 select/table；无新增任意值；弹层焦点可归还；空/错/载三态齐全。

## 9. 现状约束（2026-11-14 增补）

> 本节是这几周「修一处、别处又犯」的返工汇总。每条都对应一次真实缺陷，不是个人偏好。迁移到 shadcn + Tailwind 4（ADR-064）时这些约束**不变**，只换实现载体；评审时与第 6 节清单一样计为阻断项。

1. **未配置单价不显示 `$0.00`**：模型/渠道没配价格时，格式化函数必须返回「—」，不能把 `null` 当 0 渲染。`$0.00` 会被读成「免费」，而事实是「不知道」，两者在成本排查里差一个数量级。
2. **按渠道、按模型两个维度都要能看**：任何统计视图（用量、成本、延迟、错误率）必须同时回答「这个渠道怎么样」和「这个模型怎么样」。只做一个维度，等于把另一半排查路径堵死——工具只能回答做它的人当时在想的问题。
3. **热力图必须给日期 + 星期 + 时段**：颜色深浅不能是唯一信息载体。每个色块要能读出「哪天、星期几、哪个时段」，hover 与键盘焦点都要给出这三项；只给颜色的图对色盲不可读，对复盘也不可引用。
4. **原生 `<select>` / `<table>` 一律禁用**：页面不得出现原生 `<select>`、`<table>` 或手写的等价骨架；一律走 `components/ui/`（迁移后为 shadcn copy-in 基元）。新代码引入即阻断；存量已清零（9847b66 终态：原生 `<select>`/`<table>` 应用层各 0，见 [ui-adoption-map.md](./ui-adoption-map.md) §8）。
5. **控件尺寸三档**：`sm 32` / `md 36` / `lg 44`（默认 40 仅用于表单行），同一行内相邻控件必须同高，表格内操作按钮固定 32。任何 `h-[Npx]`、`text-[N.Px]`、`rounded-[Npx]` 都是技术债，不是可复制配方。
6. **aria 结构性语义**：结构性容器（tablist / dialog / menu / tooltip）必须给 `role` + `aria-*` + 焦点管理三件套——`role=dialog` 必须配焦点陷阱与关闭后焦点归还；`role=tablist` 必须配 roving tabindex，且 `aria-controls` 指向真实存在的 `role=tabpanel`（未选中的一侧用 `hidden` 而不是不渲染）；`title=` 不算 tooltip。

> 相关决策：[ADR-064](./adr/ADR-064-full-shadcn-migration.md)（全量迁移 shadcn + Tailwind 4）、[ADR-040](./adr/ADR-040-palette-16-slots-no-other.md)（图表 16 槽色板）、[ADR-049](./adr/ADR-049-single-source-formatters.md)（格式化单一来源）、[ADR-053](./adr/ADR-053-time-filter-preview-search-commits.md)（时间筛选只预览、提交权归搜索）。
## 10. 批处理脚本纪律（2026-11-14 增补）

> 这一节来自一次真实的批处理事故：一个带 `--apply` 的清扫脚本在口令到达前被当成「守卫测试」运行，写入了 31 个文件；随后的 dry-run 又覆盖了 apply 的归因报告。规则不针对个人，针对「可写盘的自动化」这一类动作。

1. **写盘开关默认关**：带写盘副作用的脚本开关（`--apply` / `--fix` / `--write` / `--force` / `--include-*`）默认关闭，只有在任务描述或 lead 明文授权下才能打开。验证「守卫是否放行」用 dry-run——dry-run 走同一套守卫逻辑，不需要真的写。
2. **反向操作同属写盘**：回滚、还原、重置（revert / restore / reset）与正向写入同权限，同样需要口令。`git checkout` 会连带丢弃他人未提交的改动，比正向写入更危险。
3. **自动化产物只追加、不覆盖**：报告一律带时间戳文件名（如 `apply-20261114T212923.json`），或写 append-only 的 JSONL（如 `.scratch/sweep-history.jsonl`）。禁止用后续 dry-run 覆盖上一次 apply 的报告——归因链路一旦断掉，事故就只能靠猜。
4. **写盘前先自证前提**：脚本必须在写之前断言配置前提（token / schema / 环境），不满足就拒绝退出（非零码），并在报告里写清拒绝原因；给出 dry-run 与 `--apply` 的逐条差异。
5. **作用域保护要显式**：写盘脚本必须知道哪些文件不属于自己（冻结目录、他人写域），默认跳过并在报告里计数，只在显式 `--include-*` 时才碰。
6. **并发写域不叠加**：共享文件被多个 lane 同时编辑时，写盘脚本要等 owner 收工（或由 lead 统一排序）；写盘记录 changed files + mtime，便于事后对账。
7. **门禁封顶 = 例外数**：`--max-warnings` 设成「可接受的 vendored 例外数」（当前 19 = 字号 10 + 尺寸 4 + `dark:` 5），并在脚本里用 relaxed 桶把 vendored 例外与应用层违例分开计数；应用层新增一条即失败，别留缓冲。

## 11. 视觉语言：规则 → 依据 → 反例（并入自 design-system.md）

本节是**视觉判断的正文**：每条规则都写成「规则 → 依据（代码或 ADR）→ 反例（真实 file:line）」。反例不是假想的坏做法，是**曾经在仓库里真实存在**的写法（多数已在批 4 销账，保留是为了判定口径可复核）；销一条就划一条。

**读法**：文中形如 `modal.tsx:NN`、`toast.tsx:NN`、`search-select.tsx:NN` 的行号指向**批 4 已删除的文件**，保留的是「为什么当初要这么定」的历史证据，不再是可点的现状锚点；现状看 §3.0 与 [ui-adoption-map.md](./ui-adoption-map.md)。「N 处」与行号都是 2026-11-14 的实测快照，复核按类名 grep，别照行号跳。

### 11.1 设计原则（六条）

原则必须能判优劣：每条给一个**可以当场判定**的判据。

#### 11.1.1 token 是唯一出口

- **规则**：颜色、圆角、阴影、动效只能来自 `index.css` 的变量；写不出对应变量名的值就是漂移。
- **判据**：指着这个值问「它对应哪个 `--var`」；答不上来 = 违反。
- **依据**：所有色值集中在 `index.css:108-217`（`:root`）与 `:219-289`（`.dark`）；Tailwind 4 通过 `@theme inline` 转发（`index.css:55`），也必须 inline——否则 `var()` 在定义处求值，深色主题永远拿不到覆盖（`index.css:52-54` 的注释就是这次踩坑的记录）；shadcn 别名同样只指向既有 token（`index.css:196-216`，注释写明 *new names only, every value points at an existing token*）。token 的**命名语义**（如 `muted` 指表面而不是文字）由 [ADR-066](./adr/ADR-066-token-semantics-adoption.md) 决策 1 定义，本文不另立一套。
- **反例**：`button.tsx:17` 的危险按钮用 `text-white`；`channels/enabled-toggle.tsx:28` 的开关滑块 `bg-white`；`pages/settings.tsx:336` 的状态点 `bg-white`。三处都绕过 token，深色主题下只能靠运气。

#### 11.1.2 密度优先，但密度不等于拥挤

- **规则**：一屏能比较的数量优先；代价由字号、行高、间距三处**按阶梯**付出，不允许某一处无下限压缩。
- **判据**：1440 宽下，屏幕上同屏可比较的行/块数量是否变多，同时最小字号仍 ≥ 11px、最小行高仍 ≥ 28px。
- **依据**：看板 11 张 KPI 卡用 `grid-cols-2/3/6` 自适应（`ssot.md:504`），卡片内边距收到 12 / 14（`metric-pills.tsx:37` 的 `px-3.5 py-3`），标签用 10.5px + 大写字距（`metric-pills.tsx:42`）——密度是靠**布局与间距**换来的，不是靠把字挤小。
- **反例**：`dashboard.tsx:364` 的趋势图轴标签 `fontSize={9}`，跌破 11px 下限：密度做过头，1440 宽下已不可读（同一张图 §11.7 还有零基线问题）。

#### 11.1.3 状态必须有第二个载体

- **规则**：颜色只能**强化**状态，不能**唯一**承载状态；去掉颜色（灰度截图）后状态仍可辨。
- **判据**：把截图转灰度，看是否还能说出「这是成功/警告/危险/选中」。
- **依据**：ADR-027 的决策项就是「对比度、状态表达、响应式」；Badge 的每种状态都是 soft 底 + 深字 + **文字本身**（`badge.tsx:11-16`），Empty 的图标底用 `bg-primary-soft` 且图标是语义图形（`empty.tsx:26-29`）。
- **反例**：`dashboard.tsx:656` 与 `dashboard.tsx:668` 的热力格只用颜色深浅表达量级，没有数字或文字替代——§6 评审清单第 8 条（图表要有文本摘要）在这一处未满足。

#### 11.1.4 一处定义，处处引用

- **规则**：数值格式化、时间、错误文案、间距档位各自只有一处实现，页面只负责取数与排版。
- **判据**：在 `src/pages` 与 `src/components` 里 `rg "toFixed|toLocaleString"` 应为 0 命中（口径只允许落在 `src/lib`）。
- **依据**：`format.ts:1-5` 开篇即「全站展示格式化器的唯一事实源」；`ssot.md:492` 明令页面不得自带同名格式化函数；时区切换走 `lib/time.tsx`。
- **反例**：暂无。实测 `pages/ components/` 内唯一 `toFixed` 是 `dashboard.tsx:338` 的 CSS 百分比，不是数据格式化。**这条现在是守住的**，写进原则是为了别弄丢——它最容易在一次「快速修一下」里被破坏。

#### 11.1.5 动效解释因果，不表演

- **规则**：每个动画都要能回答「它解释了哪两个状态之间的关系」；答不出就不加。
- **判据**：把动画删掉，信息是否丢失；丢失则保留，不丢失则删。
- **依据**：全站只注册两种进入动画（`index.css:28-29`：`fade-up` 位移 8px、`fade-in` 纯透明度），颜色过渡统一 150ms（`button.tsx:7`）。
- **反例**：`motion-reduce:animate-none` 只有 4 处（`skeleton.tsx:13`、`spinner.tsx:7`、`combobox.tsx:124`、`time-range-picker.tsx:175`）；`dialog.tsx` 的 `data-[state=open]:animate-in`（`dialog.tsx:24`、`:44`）与页面级 `animate-fade-up`（`index.css:46-47`）都不响应系统「减少动态」设置。（原证据 `modal.tsx:45`、`search-select.tsx:158` 两个文件已随 9847b66 删除。）

#### 11.1.6 相邻必等高，同层必同圆角

- **规则**：同一行/同一组内的控件高度一致、圆角一致；不同档位不得并排。
- **判据**：把两个相邻控件框出来，看上下边框是否落在同一条像素线上、圆角半径是否相同。
- **依据**：控件高度只有一条阶梯——**28（dense）/ 32 / 36 / 40 / 44**（`h-7`…`h-11`；44 只用于登录与空态主 CTA）。28 是 2026-11-14 补的 dense 档，**只允许工具条与密集表格内的控件**使用，现状 10 处（`channel-batch-toolbar.tsx:36/51/66/81/92`、`channels.tsx:476` 与 `:533/544/555`、`ui/input.tsx:17`），清单与禁令见 ADR-066 §2 S5；（其余档见 §2.3，`button.tsx:21-25`）；圆角由 ADR-066 决策 2 固定为语义三档 + shadcn 四档标尺（`index.css:14-16`、`:20-23`，两套同值）。
- **反例**：`combobox.tsx` 的触发器 `h-9`（36）与 `button.tsx:21` 的默认 `h-10`（40）在筛选工具栏同排——日志页与看板的工具栏就吃这个亏（原证据是已删除的 `search-select.tsx:124`，替代件沿用了同一高度）。关闭按钮那条**已不再是反例**：`rounded-md` 现等于 control 档 8px（ADR-066 决策 2），原 `modal.tsx:59` 随文件删除。

### 11.2 排版

#### 11.2.1 四档阶梯与两个例外（+ 一处域外）

档位定义与精确映射表以 [ADR-066](./adr/ADR-066-token-semantics-adoption.md)「token 映射」第 1 节为准；本节只写用法。

| 档 | 字号 / 行高 | 类名 | 用在哪 | 现状写法（实测） |
|---|---:|---|---|---|
| 辅助 / 角标 | 11 / 16 | `text-2xs`（新增） | 图表轴、徽章、chip、副行提示 | `text-[11px]` 105 处、`text-[11.5px]` 97 处、`text-[10.5px]` 79 处、`text-[10px]` 28 处 |
| 元信息 | 12 / 16 | `text-xs`（内置） | 表格副行、卡内说明、分页信息 | `text-[12px]` 132 处、`text-[12.5px]` 60 处 |
| **正文（默认档）** | 13 / 20 | `text-2sm`（新增） | 按钮、输入、菜单项、表格正文、卡片描述、Label | `text-[13px]` 77 处、`text-[13.5px]` 18 处 |
| 强调 | 14 / 20 | `text-sm`（内置） | 卡片标题、段落、页面副标题、弹窗标题 | `text-[14px]` 33 处、`text-[15px]` 9 处、`text-base` |
| 例外 1 | 19 / 26 | `text-metric`（新增） | **仅**指标大数字 | `metric-pills.tsx:61`、`text-[19px]` 2 处 |
| 例外 2 | 22 / 28 | `text-h1`（新增） | **仅**页面 h1 | `dashboard.tsx:782`、`text-[22px]` 10 处 |
| 域外 | 36 / 40 | `text-4xl`（内置） | 仅登录页品牌大标题 | `login.tsx:103`（原 40px，批 4 收进内置最大档） |

**实测分布**（`web/src` 下 `text-[…px]` 共 657 处，2026-11-14 21:0x 复测，**批 4 前历史快照**）：
四档原样覆盖 347 处（11px 105 + 12px 132 + 13px 77 + 14px 33）；
半像素 254 处（10.5px 79 + 11.5px 97 + 12.5px 60 + 13.5px 18）；
其它 56 处（10px 28、22px 10、15px 9、19px 2、20px 2、18px 2、40px 1、28px 1、9.5px 1）。
**结论：四档原样只覆盖一半用法**——半像素与 10px 合计 282 处，说明问题不是「档太少」而是「没档可落」。批 4 的解法是四档 + 映射句（取不大于原值的最大档）把 657 处全部收进档位，而不是继续写 `text-[…]`。

**落地状态（2026-11-14 21:29，`sweep-arbitrary-sizes.mjs --apply`）**：字号收敛受影响 174 行、行高合计 −458.62px；应用层 `text-[…px]` 已归零，终态残留 10 处字号全在 `components/ui/` 的 vendored 件（清单见 [ui-adoption-map.md](./ui-adoption-map.md) §11 #16），`lint-ui` 0 error / 19 warning。逐档数字见 ADR-066「批 4 实测结果」。

- **规则**：新代码只用四档 + 两个例外档；例外只能出现在基元内部，页面不得自己写 19 / 22。15px 已并入强调档（-1px），10 / 10.5 / 11.5 并入 2xs 档——收档规则见 ADR-066「token 映射」第 1 节。
- **判据**：diff 里出现 `text-[` 即为违反（§6 清单第 4 条已把它列为阻断项）。
- **生成证据（2026-11-14，`web/scripts/tw-probe.mjs`）**：4 个新档用 tailwindcss 的 `compile()` API 真编译验证——`text-2xs` / `text-2sm` / `text-metric` / `text-h1` 均生成带 `font-size` + `line-height` 的规则；同一探针确认内置 `text-xs` / `text-sm` / `text-base` 的尺寸与行高**未被覆盖**（仍是 12/16、14/20、16/24）。它同时证了 px÷4 动态 spacing（h-60=240、w-24=96、min-w-180=720、max-h-105=420、min-h-45=180、min-h-17.5=70）与 `size-4.5`=18px。
- **依据**：§2.2 的四档表；`CardTitle` 用 `text-base`（`card.tsx:28`）、`CardDescription` 用 `text-[13px]`（`card.tsx:35`）、`Label` 用 `text-[13px]`（`label.tsx:10`）——基元已经是这套阶梯的样板。
- **反例**：`dashboard.tsx:1070`（12.5px）、`dashboard.tsx:783`（13.5px）、`dashboard.tsx:1057`（11.5px）——同一屏出现三档半像素字号，肉眼看着「刚好」，实际让阶梯变成八档。

#### 11.2.2 字重与字距

- **规则**：字重只有 400 / 500 / 600；字距只有三档——正常、标题 `-0.01em`、全大写小标签 `0.08em`。
- **判据**：`font-bold / 700`、`tracking-[…]` 自定义值是否出现。
- **依据**：`index.css` 的 `@layer base` 把 `h1-h4` 定为 600 + `letter-spacing: -0.01em`；`metric-pills.tsx:61` 大数字 `tracking-[-0.01em]`，`metric-pills.tsx:42` 小标签 `tracking-[0.08em]`；Badge 用 `font-medium`（`badge.tsx:7`）。
- **反例**：`metric-pills.tsx:42` 同时用 `uppercase` + `tracking-[0.08em]`，而它的实际内容是中文（`ssot.md:504` 列出的指标名是「请求、输入、输出…」）——中文没有大小写，`uppercase` 是空操作，只剩被拉宽的字距在破坏对齐。

#### 11.2.3 数字与等宽

| 场景 | 用什么 | 依据 |
|---|---|---|
| 表格里的数值列 | `tabular-nums` + 右对齐 | 全站 `tabular-nums` 67 处 |
| 延迟 / 词元 / 成本 / 速率 | `tabular-nums`，不强制等宽字体 | `metric-pills.tsx:61`、`dashboard.tsx:438` |
| 请求路径、代码、命令行 | `font-mono`（JetBrains Mono，`index.css:11`） | 全站 `font-mono` 102 处；`login.tsx:114` 的 `POST /v1/chat/completions` |
| 会随刷新变化的数字块 | 必须 `tabular-nums` | 否则宽度抖动，整行跳 |

- **规则**：任何会变化、且会被纵向比较的数字都用 `tabular-nums`；等宽字体只给「代码类」内容，不给普通数字。
- **反例**：无现存违反（未找到漏 `tabular-nums` 的数值块）。最近的风险在 `dashboard.tsx:424` 的 tooltip：它已用 `w-56` 固定宽度，一旦有人把 `tabular-nums` 去掉（`dashboard.tsx:438`），长数字会把 tooltip 撑开。

### 11.3 间距与密度

#### 11.3.1 两档密度

| 维度 | 紧凑 | 常规 | 何时选 |
|---|---|---|---|
| 卡片内边距 | 12 / 14（`px-3.5 py-3`，`metric-pills.tsx:37`） | 24（Card 默认 `p-6`，`card.tsx:21`） | 一屏超过 8 个卡片 → 紧凑 |
| 内嵌子卡 | 12（`p-3`） | — | 只在磁贴/子块 |
| 控件高度 | 32 / 36 | 40 | 工具栏、弹窗 footer → 紧凑；表单 → 常规 |
| 表格行高 | 40 | 44 | 日志页（千行级）→ 紧凑；配置页 → 常规 |
| 区块间距 | 16（`gap-4`） | 24 | 看板用 16，设置页用 24 |

- **规则**：4px 基线，允许 4 / 8 / 12 / 16 / 20 / 24 / 32；2px 只用于细缝（热力格 `gap-[2px]`，`dashboard.tsx:647`）。同一页只允许一档密度。
- **判据**：同一屏出现 24 与 20 两种卡片内边距即为混档。
- **依据**：§2.5 的允许值表；`CardHeader/CardContent` 默认 `p-6`（`card.tsx:21`、`card.tsx:42`）；看板磁贴 `px-3.5 py-3`（`metric-pills.tsx:37`）。
- **反例**：
  - `dashboard.tsx:118` 磁贴 `p-3`（12px）与同页卡片 `p-5`（20px）同屏，第一眼就出现两种内边距；
  - `channel-detail.tsx:194 / 214 / 238 / 690 / 818` 五处 `<Card className="p-5">` 覆盖 Card 的 24 —— 覆盖本身合法，但**同一页必须统一**；
  - `catalog.tsx`、`channel-form-dialog.tsx` 等处的 `py-3.5`（14px）不在允许值内（应 12 或 16）；同类写法原见 `modal.tsx:69`，该文件已随 9847b66 删除。

#### 11.3.2 表格与表单的固定节奏

- 表格：表头 `py-3`、数据行 `py-2`、单元格 `px-3` 或 `px-4`（`ssot.md:490`）；行高不低于 40。
- 表单：字段纵向 `gap-4`、label 与控件 6、控件与帮助/错误 6（§2.5）。
- **反例**：实测 `py-3.5` / `px-3.5` 合计 33 处（2026-11-14 22:0x 复测），落在 12 与 16 之间——单看「略松一点更透气」，合起来就是第 9 种间距值。主要来源：`catalog.tsx` 9、`keys.tsx` 7、`discover-models-dialog.tsx` 4、`channel-form-dialog.tsx` 3、`alert.tsx:8`、`metric-pills.tsx:37`；原先举例的 `modal.tsx:69` 已随 9847b66 删除。

### 11.4 色彩

#### 11.4.1 三层语义：表面、描边、强调

| 层 | 浅色 | 深色 | 用途 | 不要用在哪 |
|---|---|---|---|---|
| 画布 | `--bg` / `--surface` / `--surface-2` | 同名 token，由 `.dark` 覆盖 | 页面底、卡片、次级容器 | 不要用 `surface-2` 当页面底 |
| 描边 | `--border` / `--border-strong` | 同名 token | 分隔、控件边界 | 不要用 `border` 画「强调」 |
| 文字 | `--text-title` / `--text-body` / `--text-muted` / `--text-faint` | 同名 token | 标题 / 正文 / 辅助 / 极弱 | `faint` 不承载正文 |
| 强调 | `--primary` + `--primary-soft` + `--primary-on` | 同名 token | 主按钮、选中、链接 | `soft` 不做大面积底 |

**数值与命名只在两处**：token 的值见 `index.css`（浅色 `:root` `index.css:108-217`、深色 `.dark` `index.css:219-289`），命名语义见 [ADR-066](./adr/ADR-066-token-semantics-adoption.md) 决策 1——本文只写「哪一层、用在哪」，不复制色值。**`muted` 已改义**：shadcn 语义下 `--color-muted` 是弱表面（`bg-muted`），灰文字层是 `--color-muted-foreground`（= `--text-muted`）；`--color-title/--color-body/--color-faint` 是本仓扩展名。

**`primary-soft` / `surface-2` / `border` 的三分法**（这是全站最常混的三选一）：

1. 「选中 / 高亮 / 品牌强调」→ `primary-soft`（Badge default：`bg-primary-soft text-primary`，`badge.tsx:11`；Empty 图标底，`empty.tsx:26`）。
2. 「次级容器 / 悬停 / 未选中」→ `surface-2`（`button.tsx:13` secondary、`button.tsx:16` ghost hover、菜单项 hover `theme-menu.tsx:85`）。
3. 「可交互边界 / 分隔」→ `border`；控件边界用 `border-strong`（`input.tsx:13`、`combobox.tsx` 的触发器），内容分隔用 `border`（`card.tsx:10`）。

- **判据**：灰阶截图里，`primary-soft` 与 `surface-2` 几乎同亮度——所以选中态**必须**再加文字色或图标区分（`badge.tsx:11` 同时给了 `text-primary`），只换底色不算做了选中态。
- **反例**：`button.tsx:17` 的危险按钮 `bg-danger + text-white`——颜色本身没错，但前景绕过了 token（见 §11.9：深色主题 `--danger` 是 `#f87171`，白字对比度约 2.7:1，低于 4.5:1）。

#### 11.4.2 状态色的载体规则

| 状态 | 浅色 | 深色 | 载体 |
|---|---|---|---|
| 成功 | `--success #16a34a` / `--success-text #15803d` / `--success-soft #f0fdf4` | `#4ade80` / `rgba(74,222,128,.14)` | Badge、toast、图表达标线 |
| 警告 | `#d97706 / #b45309 / #fffbeb` | `#fbbf24` / 半透明 | Badge、Alert warning |
| 危险 | `#dc2626 / #b91c1c / #fef2f2` | `#f87171` / 半透明 | 危险按钮、Alert、错误 toast |
| 中性 | `--info #475569` / `surface-2` | `#94a3b8` | 默认 Badge、辅助信息 |

- **规则**：面积大于一行文字的状态块用 soft 底 + text 色；一行以内用纯文字色；只有**主按钮**允许饱和底 + on 色。禁止用饱和状态色大面积铺底。
- **依据**：`index.css:134-146` 为 6 类图标徽章定义了成对的 `-bg/-fg`（浅色实色、深色半透明，`index.css:249-260`）；`badge.tsx:13-15` 三种状态全部走 soft+text。
- **反例**：`alert.tsx` 的 destructive 变体用 `border-danger + bg-danger-soft`——这是**正确**用法（边框承载状态、底色只做柔化）；对照 `button.tsx:17`：同一套颜色，一个当底、一个当前景，危险按钮的前景少了 token 这一步。

#### 11.4.3 图表 16 槽色板：六条分配规则

| 规则 | 内容 | 依据 |
|---|---|---|
| R1 | 固定 16 槽，顺位取色，永不新开第 17 色 | `index.css:178-194`；ADR-040「16 槽循环复用，取消『其他』聚合段」 |
| R2 | 主色靛蓝不入板 | `index.css:178` 注释 *primary indigo excluded* |
| R3 | 按**当期请求量降序**分配；0 请求的系列不占槽 | `chart-colors.ts:22-28` |
| R4 | 超过 16 个系列时循环复用，不折叠 | `chart-colors.ts:27`；ADR-040 明确代价是颜色会重复 |
| R5 | 隐藏系列不重排颜色：先在完整列表上分配，绘制时再跳过 | `dashboard.tsx:398-399`（`if (hidden.has(item.name)) return null`） |
| R6 | 深色有独立亮度集，不复用浅色值 | `index.css:272-288`，注释 *never reuse the light values* |

**R3 是一个已知缺陷（已登记，未修）**：颜色跟着「当期排序」走，于是
（a）某模型今天排第 3、明天上升到第 2，颜色就换；
（b）某模型掉到 0 请求被 `filter` 掉，它后面所有系列的颜色整体前移。
用户记住的是「蓝色 = A 模型」，而代码记住的是位置（`chart-colors.ts:22-28`；`dashboard.tsx:185` 的图例按分配结果取色，不看模型名）。

**拟修法**：槽位改按**稳定键**分配，而不是按当期排序位置——要么「首次出现顺序」（会话内维护 name → slot 表，排序只影响展示顺序），要么把槽号绑到模型 id 的哈希（跨会话也稳定，代价是可能撞色、需按 16 取模）。两种都不新增 token，只改 `assignCategoryColors` 的取色时机（`chart-colors.ts:17-29`）。登记在 [ui-adoption-map.md](./ui-adoption-map.md) §11 #7。

#### 11.4.4 暗色映射原则（细节见 §11.9）

- **soft 变半透明**：浅色 `#f0fdf4` → 深色 `rgba(74,222,128,.14)`（`index.css:238`），因为实色浅底在深色画布上会发白发脏。
- **阴影几乎失效**：两级阴影都是 `rgba(17,24,39,…)`（`index.css:25-26`），在 `#0b0f17` 上不可见——深色层级靠 `border` 提亮（`#273244 → #374357`）。
- **固定面板不翻转**：tooltip / code / login 两主题同值（`index.css:150-174`、`index.css:263-268`）。
- **反例**：`dashboard.tsx:424` 的 tooltip 用 `--tooltip-bg`（`rgba(11,15,23,.95)`），而深色主题的 `--bg` 就是 `#0b0f17`（`index.css:220`）——浮层与页面同色，只剩 1px 的 `--tooltip-border` 在区分。

### 11.5 形状与层级

#### 11.5.1 圆角分档（语义档 + shadcn 标尺 + full）

| 语义档 | shadcn 标尺 | 用在哪 | 不能用在 |
|---|---|---|---|
| `--radius-badge` | `--radius-sm` | 徽章、分页页码选择、极小控件 | 卡片、按钮 |
| `--radius-control` | `--radius-md` | 按钮、输入、选择、菜单项、图标徽章 | 卡片 |
| `--radius-card` | `--radius-lg` | 卡片、弹窗、浮层容器 | 控件内部 |
| —（新增） | `--radius-xl` | 浮层内嵌容器（弹窗里的区块） | 控件、卡片本体 |
| `rounded-full` | — | 头像、圆点、开关轨道、Empty 图标底 | 容器 |

> 两列的数值与映射（sm 6 / md 8 / lg 12 / xl 16，badge=sm、control=md、card=lg）由 [ADR-066](./adr/ADR-066-token-semantics-adoption.md) 决策 2 固定，代码事实源是 `index.css:14-16`（语义档）与 `:20-23`（标尺）。**本文只说「用哪一档」，不再复制数值**——上一版在两处各写一份 6/8/12，已经漂过一次。

- **规则**：只允许上表各档；同层同圆角。vendored 件写 `rounded-sm/md/lg/xl` 时命中的就是对应语义档，不需要逐件改类名。
- **判据**：`rg -o "rounded-[a-z\[\]0-9.]+"` 的结果只应落到上表的 token（`sm/md/lg/xl` 与 `badge/control/card` 同值，两者都合法）。
- **依据**：`index.css:14-16`、`:20-23`；`card.tsx:10`（`rounded-card`）、`button.tsx:7`（`rounded-control`）、`badge.tsx:7`（`rounded-badge`）。
- **反例**：
  - ~~`rounded-md`（6px，等于 badge 档）被用到控件上 32 处~~——**已销账**：ADR-066 决策 2 把 `rounded-md` 定为 control 档（8px），原 `modal.tsx:59`、`search-select.tsx:149`、`search-select.tsx:186` 三处按新标尺正好归位，不再是反例（三处所在文件均已随 9847b66 删除）；
  - `rounded-[7px]` 1 处：`dashboard.tsx:807`；`rounded-[6px]` 3 处：`dashboard.tsx:953`、`dashboard.tsx:971`、`channel-models-panel.tsx:267`；
  - `rounded-[2px]` 2 处：`dashboard.tsx:656`、`dashboard.tsx:668`（热力格）；
  - `rounded-sm` 27 处（现为 6px = badge 档），多在 `login.tsx` 的固定深色域——属 §11.9.3 的登录页豁免区，保留须在豁免清单登记。

#### 11.5.2 阴影两级（外加一个未登记档）

- **依据**：`index.css:25-26` 只有两个阴影变量；`card.tsx:10` 用 `shadow-card`，`shadow-pop` 现由页面级浮层使用（`log-detail-drawer.tsx`、`logs.tsx`、`keys.tsx`、`dashboard.tsx`）。原 `search-select.tsx:158` 与 `toast` 两处随 9847b66 删除：替代的 `sonner.tsx` 的 toast 走 `shadow-lg`，`combobox.tsx` 的浮层用 `popover.tsx` 的默认阴影。
- **反例**：`shadow-[…]` 实测 0 处（没人自写，good），但 `shadow-xs` 13 处（`button.tsx:12`、`button.tsx:17`、`input-group.tsx:17` 等）是 Tailwind 自带的第三档阴影，未在 token 表登记 → 要么登记为 `--shadow-control`，要么收敛到 `shadow-card`。它在 vendored 件里会长期存在（v3 的 `shadow-sm` 在 v4 改名成 `shadow-xs`，见 ADR-066 决策 3），所以登记比清剿更现实。

#### 11.5.3 border 还是 bg：三问定案

1. 这条线分隔的是**可交互物**还是**内容块**？可交互 → `border`（控件用 `border-strong`）；内容块 → 可以只用底色分组，不必画线。
2. 这个块是否**浮在其他内容之上**？浮 → `border` + `shadow-pop`（例：`log-detail-drawer.tsx`、`logs.tsx` 的浮层；原 `search-select.tsx:158` 随 9847b66 删除）。
3. 去掉这条线会怎样？如果靠间距或底色仍能分组，就别加线。

- **依据**：`card.tsx:10`（卡片 = border + shadow-card）、`alert.tsx:8`（状态块 = border + soft 底，边框同时承载状态）、`dialog.tsx` 的调用点（`channel-model-form-dialog.tsx`、`combo-editor-dialog.tsx`：头部 `border-b`、footer `bg-surface-2 + border-t` 两者并用；原 `modal.tsx` 已随 9847b66 删除）。
- **反例**：`dashboard.tsx:541` 的表头用 `border-b` 而非底色分组——单看没错，但同一页的卡片又全用 `border` 包边，于是 1096 行里每个块都有边框，视觉噪声叠加；这正是「能靠间距分组就别画线」要防的。

#### 11.5.4 z-index 五档

| 层 | z | 内容 | 实测 |
|---|---:|---|---|
| 表格吸顶列 | 5 | 表格左右吸附列（低于表头，避免盖住表头） | `logs.tsx` 详情列 `z-[5]` |
| sticky | 10 | 表头、吸顶工具条 | `z-10` 6 处 |
| tooltip（临时） | 20 | 图表 tooltip | `z-20` 2 处（`dashboard.tsx:425`） |
| topbar | 30 | 顶栏 | `z-30` 2 处 |
| popover | 40 | 下拉、搜索建议、时间选择 | `z-40` 2 处 |
| dialog / 模态 | 50 | 弹窗、抽屉 | `z-50` 14 处（`dialog.tsx:24`、`:44` 的 overlay 与 content，含 vendored sheet） |
| search | 60 | 全屏搜索遮罩 | `z-[60]` 1 处（`global-search.tsx:89`） |
| toast | 70 | 全局通知 | `sonner.tsx` 的 `Toaster`（原 `toast.tsx:132` 的 `z-[70]` 随 9847b66 删除；`Toaster` 未显式设 z，层级由 sonner 自带样式决定） |

- **规则**：新增层级必须先改表；两个任意值 `z-[60] / z-[70]` 应登记为 token（[ui-adoption-map.md](./ui-adoption-map.md) §11 #4）。
- **反例**：`dashboard.tsx:424` 的 tooltip 用 `z-20`，落在 sticky 与 popover 之间——这个层级只在「图表里悬浮」场景成立，一旦 tooltip 出现在弹窗内就会被遮住（当前无此组合，属隐患）。

### 11.6 动效

#### 11.6.1 时长与缓动阶梯

| 场景 | 时长 / 缓动 | 现状实现 |
|---|---|---|
| 颜色 / 边框 / 底色过渡 | 150ms | `button.tsx:7`（`transition-colors duration-150`） |
| 浮层进入 | 200ms ease-out | `index.css:28-29`（`--animate-fade-in` / `--animate-fade-up`） |
| 弹窗进入 | 200ms ease-out，位移 8px | `dialog.tsx` 的 `data-[state=open]:animate-in`（`dialog.tsx:24`、`:44`）+ 页面级 `animate-fade-up`（keyframes 在 `index.css:46-47`）；原 `modal.tsx:45` 的写法随 9847b66 删除 |
| 加载指示 | 循环 | `spinner.tsx:7`（`animate-spin`） |
| 骨架 | 循环 | `skeleton.tsx:14`（`animate-pulse`） |

上限 300ms（§2.7）。

- **规则**：新动画必须先注册进 `@theme`——`index.css:31` 的注释写明 keyframes 必须写在 `@theme` 内才会随 `--animate-*` 一起输出（在 `@layer utilities` 里写的规则不会生效）。
- **反例**：全站没有 `prefers-reduced-motion` 的全局兜底，只有 4 处逐件写了 `motion-reduce:animate-none`（`skeleton.tsx:13`、`spinner.tsx:7`、`combobox.tsx:124`、`time-range-picker.tsx:175`）；`dialog.tsx:24`/`:44` 与页面级 `animate-fade-up` 仍不响应系统设置。（原证据 `modal.tsx:45`、`search-select.tsx:158` 随 9847b66 删除。）

#### 11.6.2 必须动 / 禁止动

| 必须动 | 为什么 |
|---|---|
| hover / focus 的底色与边框变化 | 让用户知道指针落在哪 |
| 浮层出现与消失 | 解释「它从哪来」 |
| 加载指示（spinner / skeleton） | 解释「正在发生」 |
| toast 进入 | 提示「操作有反馈」 |

| 禁止动 | 为什么 |
|---|---|
| 数值补间 | 补间让「正在变化」看起来像「真实数据」；数字应直接跳变并配 `tabular-nums` |
| 列表重排 / 插入位移动画 | 行位置一变，用户的目光就找不到刚才那一行 |
| 首屏大入场动画 | 首屏目标是尽快读到数字 |
| 滚动联动、视差、鼠标跟随 | 与数据无关，只增加阅读成本 |
| hover 抬升（位移 + 阴影） | §2.4：hover 不加阴影，只改底色或边框 |

#### 11.6.3 减少动态（reduced motion）

- **规则**：系统开启「减少动态」时，动画与过渡必须归零或缩到不可感知。
- **现状**：Tailwind 的 `motion-reduce` 变体可用，已在 `skeleton.tsx:14`、`spinner.tsx:7` 使用；其余动画未覆盖。
- **建议**：在 `index.css` 的 `@layer base` 增加一条全局兜底媒体查询（把 `transition-duration` / `animation-duration` 归零），组件级 `motion-reduce:` 语义保留（[ui-adoption-map.md](./ui-adoption-map.md) §11 #10）。

### 11.7 数据可视化

#### 11.7.1 坐标轴与网格

- **规则**：轴标签字号下限 11px；刻度文案统一走 `formatTrendAxis`（`dashboard.tsx:364`），页面不另写一套。
- **规则**：只画正刻度的水平网格线，虚线 `3 5`、颜色 `stroke-border`；不画垂直网格线。
- **规则**：柱图必须有零基线，否则柱高读不出绝对值。
- **反例**：
  - `dashboard.tsx:363` 轴标签 `fontSize={9}`，低于 11px 下限；
  - `dashboard.tsx:351-352` 的 `filter((tick) => tick > 0)` 让图里没有零基线，柱底「悬空」；
  - `dashboard.tsx:360-361` 的虚线网格 + 9px 标签叠加后，网格比数据更抢眼。

#### 11.7.2 图例与 tooltip

- **图例**：固定右侧 180px 列（`dashboard.tsx:342` 的 `lg:grid-cols-[1fr_180px]`），可点隐藏；隐藏后颜色不重排（§11.4.3 R5）。
- **tooltip**：固定深色（两主题同值，`index.css:262-269`），宽度固定 `w-56`（224px），正文 11px，数值 `tabular-nums`（`dashboard.tsx:424-438`）；空桶写「该时段无数据」（`dashboard.tsx:442`），不留空白。

#### 11.7.3 空态、加载与无障碍

- 图表空 → 一行明确文案（`dashboard.tsx:442`），不是空白画布。
- 图表加载 → 目前没有图表专用骨架（[ui-adoption-map.md](./ui-adoption-map.md) §11 #9），靠整卡 loading 处理。
- 无障碍：`svg` 带 `role="img"` + `aria-label`（`dashboard.tsx:347-348`）；每个时间桶一个 `tabIndex={0}` 的命中矩形并带 `aria-label`（`dashboard.tsx:385-387`）——但焦点不可见（§11.10 #7）。

#### 11.7.4 数值格式（唯一口径）

| 场景 | 函数 | 口径 | 出处 |
|---|---|---|---|
| 大数 / KPI | `formatCompact` | K / M / B 两位小数 | `format.ts:8-15` |
| 计数 | `formatCount` | 千分位，缺失「—」 | `format.ts:18-21` |
| 金额 | `formatUsdFine` | ≥ `0.01` 美元固定两位；更小额最多 6 位 | `format.ts:34-41` |
| 每百万 token 单价 | `formatPricePer1m` | 美元 + 2–4 位 | `format.ts:69-72` |
| 百分比 | `formatPercent` | 1 位（错误率 2 位） | `format.ts:75-78`、`ssot.md:492` |
| 缓存命中率 | `formatCacheHitRate` | 0 或缺失 → 「—」；口径 A/B/C/D 四分见 ADR-068，卡面主指标是 A（token 加权） | `format.ts:81-84` |
| 速率 | `formatRate` | ≥ 1000 走 K/M/B | `format.ts:95-99` |
| 图表轴 | `formatTrendAxis` | 与所选指标同口径 | `dashboard.tsx:364` |
| 缺失值 | — | 一律「—」 | `ssot.md:492` |

- **规则**：页面不自带同名格式化函数，不手写 `toFixed(1) + "%"`。
- **反例**：暂无。实测 `pages/ components/` 内唯一 `toFixed` 是 `dashboard.tsx:338` 的 CSS 百分比，不是数据格式化（与 §11.1.4 同一事实）。

#### 11.7.5 何时条形、何时折线

判据只有一条：**横轴是不是时间**。

| 数据形态 | 图形 | 例子 |
|---|---|---|
| 连续时间序列（分钟 / 小时 / 天桶） | 折线或堆叠柱 | `dashboard.tsx:394-414` 的堆叠柱 + 分类色 |
| 离散类目对比（渠道、模型、状态码） | 条形（横向更利于长标签） | 错误类型分布 `dashboard.tsx:1064-1080` |
| 单个时间桶内再拆分类 | 堆叠柱 | 同上趋势图 |
| 二维密度（时段 × 天） | 热力格 | `dashboard.tsx:541-560` |

- **规则**：不要把「随时间变化的绝对值」画成面积图——面积暗示可加，堆叠柱已经可加，再叠面积会让人二次相加。

### 11.8 反馈与空态

#### 11.8.1 四态的固定形态

| 态 | 固定形态 | 基元 | 文案 |
|---|---|---|---|
| loading（整块） | 骨架块，尺寸 = 最终内容 | `skeleton.tsx:14` | 无文案 |
| loading（动作中） | 旋转指示器；按钮文案保持动词不变 | `spinner.tsx:27`（label 默认「加载中」） | 动词不变，只加指示器 |
| empty | 48px 圆底图标 + 标题（14 semibold）+ 说明（13）+ CTA | `empty.tsx:19-36` | 标题说状态、说明说原因、CTA 说下一步 |
| error（页面级） | Alert（danger）+ 原因 + 重试按钮 | `alert.tsx` | 发生了什么 + 为什么 + 怎么办 |
| error（动作级） | toast（error） | `sonner.tsx` 的 `Toaster`（原 `toast.tsx:118` 随 9847b66 删除） | 同上，停留更久 |
| success（动作级） | toast（success） | 同上（原 `toast.tsx:117`） | 动词 + 对象 |

- **规则**：四态互斥——同一区域不同时出现 loading 与 empty。
- **规则**：空态必须区分「还没有数据」与「没有匹配结果」，后者必须给清除筛选 CTA（§5.3）。
- **规则**：同一事件不同时用 toast + Alert（§4.9）。
- **规则**：toast 最多 4 条；默认 5200ms，warning 6500ms——这是原 `toast.tsx:99-101`、`:119` 定的口径，随 9847b66 删除后**暂无实现**：`sonner.tsx` 的 `Toaster` 没配 `duration` / `toastLimit`，走 sonner 默认。**本条待重新定义并落进 `toastOptions`。**
- **反例**：
  - `dashboard.tsx:195` 空态是裸文本「暂无数据」（`text-[12px] text-muted`；清扫后为 `text-[12px] text-muted-foreground`，见 [ui-adoption-map.md](./ui-adoption-map.md) §11 #13）：无图标、无 CTA，也没区分「无数据」与「无匹配」；
  - `dashboard.tsx:1088`「所选范围内没有错误。」用 12.5px 且无 CTA——这是「没有匹配」，却按「没有数据」处理；
  - 原 `toast.tsx:117` 的 error 没有单独设 duration（默认 5200ms），与 info 相同，而 §4.9 要求「错误停留更久」——当时 warning 最长（6500），error 反而最短，文案与实现不一致。该实现已随 9847b66 删除；新 `Toaster` 未配 `duration`，这条不一致要等收口时一并定，别当成已修。

### 11.9 深色模式

#### 11.9.1 机制

- **class 策略**：`.dark` 挂在 `<html>` 上（`index.css:3-6` 的注释：由 `lib/theme.ts` 与 `index.html` 的防闪脚本设置），并用 `@custom-variant dark (&:where(.dark, .dark *))` 固定成 class，而不是 `prefers-color-scheme`。
- **两处都给**：每个颜色都要在 `:root`（`index.css:108-217`）与 `.dark`（`index.css:219-289`）各写一次；只写浅色 = 深色下静默继承浅色值。shadcn 别名层（`index.css:196-216`）只做转发，不新开值。

#### 11.9.2 五类「不能只靠反转」

1. **阴影**：两级阴影都是 `rgba(17,24,39,…)`（`index.css:25-26`），在 `#0b0f17` 上不可见 → 深色层级改用 `border` 提亮（`#273244` → `#374357`）。
2. **soft 状态底**：浅色是实色（`#f0fdf4` 等，`index.css:127-133`），深色改成半透明（`index.css:233`、`238`、`241`、`244` 的 `rgba(…,.14)`）——实色浅底在深色画布上会发白发脏。
3. **图表 16 槽**：深色有独立亮度集（`index.css:272-288`），注释明写 *never reuse the light values*。
4. **固定深色面板**：tooltip（`index.css:264-267`）、code（`268`）、login（`167-174`）两主题同值——它们是品牌与代码语境，翻转会破坏识别（`index.css:261-262` 注释：*identical values keep the backdrop/tooltip contrast unchanged*）。
5. **图片 / 插画**：全站只有 3 处 `img`（`layout/sidebar.tsx:28`、`pages/login.tsx:91`、`pages/login.tsx:166`），都在固定深色或品牌位；内容区不放需要反色的位图。

#### 11.9.3 深色下的具体禁忌

- 不用 `bg-white` / `text-black`（见 §11.10 #8）。
- 不靠阴影表达层级（见 §11.9.2 第 1 条）。
- **登录页整体不随主题切换**（`index.css:166` 注释：*intentionally theme-independent*）——这是登记在案的例外，不要「顺手修它」。
- **反例**：`button.tsx:17` 的 `text-white`——深色 `--danger` 是 `#f87171`（`index.css:243`），白字对比度约 2.7:1（低于 4.5:1），而 `--primary-on` 在深色是 `#0b0f17`（`index.css:234`），对比约 7:1；另见 `channels/enabled-toggle.tsx:28` 的 `bg-white` 滑块与 `pages/settings.tsx:336` 的 `bg-white` 状态点。

### 11.10 十条「看起来对，但其实是错的」

| # | 审美判断 | 为什么它看着对 | 实际错在哪 | 反例 | 正确做法 |
|---:|---|---|---|---|---|
| 1 | 轴标签 9px「更精致」 | 小字显专业、图面干净 | 跌破 11px 下限，1440 宽已不可读 | `dashboard.tsx:364` | `text-2xs` 11px（已由字号映射销账，见 [ui-adoption-map.md](./ui-adoption-map.md) §11 #8） |
| 2 | 半像素字号「刚刚好」 | 12.5 / 13.5 比整数更贴切 | 阶梯从四档变八档，实测 254 处 | `dashboard.tsx:1070`、`783`、`1057` | 收敛到 11 / 12 / 13 / 14 |
| 3 | `rounded-[7px]`「约等于 8px」 | 肉眼与 8px 无差别 | 圆角四档体系被架空，下次就会有人写 9px | `dashboard.tsx:807` | `rounded-control` |
| 4 | 热力格 2px 圆角「更细腻」 | 小圆角显得精致 | 破坏三档圆角；2px 在深色几乎不可见 | `dashboard.tsx:656`、`668` | 统一 2px token 或改 4px |
| 5 | 标签 `uppercase + tracking`「有设计感」 | 仪表盘常见的英文式标签 | CJK 没有大小写，只剩被拉宽的字距噪声 | `metric-pills.tsx:42`（标签是中文，见 `ssot.md:504`） | 中文标签去掉 `uppercase` |
| 6 | 图例颜色「很稳」 | 同一模型看起来一直是同色 | 颜色按**当期请求量排序**分配，掉到 0 会让后面整体前移 | `chart-colors.ts:22-28`、`dashboard.tsx:185` | name → slot 稳定映射（[ui-adoption-map.md](./ui-adoption-map.md) §11 #7） |
| 7 | `tabIndex=0`「让图可键盘访问」 | 源码里有无障碍的样子 | `outline-hidden` 让焦点不可见，键盘用户不知道自己在哪 | `dashboard.tsx:385-388` | 焦点环用 `--focus-ring` |
| 8 | 危险按钮红底白字「最醒目」 | 符合直觉 | 深色 `--danger` 为 `#f87171`，白字约 2.7:1 | `button.tsx:17` | 前景用 `--primary-on` |
| 9 | 深色 tooltip「更有层次」 | 悬浮感强、专业 | 深色下 `--tooltip-bg` 与 `--bg` 同为 `#0b0f17`，浮层与页面同色 | `dashboard.tsx:424`、`index.css:220` 与 `264` | 深色下用 `surface-2` + `border` |
| 10 | 「暂无数据」一句话「最干净」 | 极简、不啰嗦 | 无图标、无 CTA、不区分「无数据」与「无匹配」 | `dashboard.tsx:195` | 用 `Empty` 基元（`empty.tsx`） |

---




