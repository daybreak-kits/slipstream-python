# UI 迁移映射表（shadcn copy-in + Tailwind 4）

> **已归档（2026-11-14）**：本文是历史文档，只作证据与背景，**不再更新、不再作为权威**。现状见 [../ui-adoption-map.md](../ui-adoption-map.md)（台账）与 [../ui-standards.md](../ui-standards.md)（规范正文）。

> 台账文档，不是决策文档。决策见 [ADR-064](../adr/ADR-064-full-shadcn-migration.md)（accepted，2026-11-14，supersedes ADR-062 / ADR-063）。
> 盘点依据：[ui-inventory.md](./ui-inventory.md):10 —— 68 个用户可见控件/交互模式：完整覆盖 11、部分覆盖 18、无基元 39。
> 复核日期 2026-11-14。范围 web/src 的 51 个非测试 tsx（39 个产品 + components/ui 的 12 个）。本文只写文档，未改任何源码。
>
> **行号与文件尺寸是迁移过程中的历史锚点**：文中 `file:line`、文件行数、扫描文件数都是当时实读的快照；相关文件在 9847b66 删除（`modal`/`search-select`/`toast`）后，这些锚点只作证据、不再可点——现状看 [`ui-adoption-map.md`](../ui-adoption-map.md) §8。

## 0. 结论

- 68 个模式：**换 shadcn 37 条、保留自建 25 条、删除合并 6 条**。
- 115 处 `title=` 里只有 **58 处是提示**（55 处该换 Tooltip、3 处该改成可见帮助文字）；**29 处是组件 prop**（Modal / Section / TopList / HeadersGroup / PagePlaceholder 的 title，删了会丢标题）；**28 处是可访问名**（其中 27 处 title 与 aria-label 完全重复，直接删 title 即可）。
- 顺序比选件重要：批 1 只做「零渲染变化」的属性清理与原子件；批 3 的浮层是唯一会「看起来没变、用起来变了」的部分，必须配焦点验收清单；批 4 才删旧基元。
- 前置硬约束：Tailwind 3.4.19 必须先升 4（package.json:39）。2026 年新增的 shadcn 组件只发 v4 风格，不升就只能吃旧件，等于把 ADR-063 的半步再走一遍（ADR-064:24）。

## 1. 口径与复核方式

- 表内 file:line 相对 `web/src`，行号取自 ui-inventory.md 的实测扫描；本轮对以下锚点做了 grep 复核：19 处 `<select`（11 文件）、9 处 `<table`（8 文件）、15 处 `<Modal` 与 1 处 `window.confirm`、6 处 `<textarea`、12 处 `type="checkbox"`、2 处 `type="radio"`、`role=` 锚点，以及**全部 115 处 `title=` 的宿主元素**。
- 覆盖列沿用 inventory 三档（完整 / 部分 / 无）；批列对应 §4 的 PR 批次，0 = 前置。
- 目标列写 shadcn registry 件名；写「保留自建」的必须给理由，理由类型只有三种：图表/可视化、业务组合件、布局骨架。

## 2. 主表（68 条）

### 2.1 操作类（9 条）

| # | 现状模式 | 主实现 file:line | 迁移目标 | 批 |
|---|---|---|---|---|
| 1 | 主/次/轮廓/幽灵/危险/链接按钮 | components/ui/button.tsx:6（variant :10-19、size :20-26） | `button`：变体名映射 default/secondary/outline/ghost/destructive/link，尺寸保留 h-8/h-9/h-10/h-11 四档 | 1 |
| 2 | 图标按钮 | layout/topbar.tsx:77、86、132；layout/sidebar.tsx:44；layout/app-shell.tsx:52；pages/keys.tsx:238-292；pages/channel-detail.tsx:534 | `button` size=icon（禁 h-6/h-7/h-8/h-9 混用） | 1 |
| 3 | 链接式按钮 | pages/channels.tsx:235、328；pages/channel-detail.tsx:524、567；pages/catalog.tsx:159；pages/overview.tsx:417、428、439、454 | `button` asChild + Link（官方 asChild 模式） | 1 |
| 4 | 文本链接 | pages/logs.tsx:950；pages/dashboard.tsx:939-940；pages/overview.tsx:311、348；pages/settings.tsx:228 | 保留自建：shadcn 无 Link 件，收敛到 `button` variant=link | 1 |
| 5 | 加载/禁用按钮 | pages/channel-detail.tsx:626、638；pages/keys.tsx:361；pages/login.tsx:231；components/channels/channel-batch-toolbar.tsx:41 | `button` disabled + `spinner`（替手写 Loader2） | 1 |
| 6 | 分段选择按钮组 | pages/catalog.tsx:118；pages/dashboard.tsx:784、976、994 | `toggle-group` type=single（带 aria-pressed） | 2 |
| 7 | 批量操作条 | components/channels/channel-batch-toolbar.tsx:27 | 保留自建：业务条（Card + Button 组合），无对应件 | — |
| 8 | 分页 | components/ui/pagination.tsx:97（每页行数原生 select 在 :108） | `pagination` 结构件 + `select`（:108 随批 2 换） | 4 |
| 9 | 复制按钮 | pages/catalog.tsx:217；pages/keys.tsx:238、459；pages/overview.tsx:281、295 | 保留自建：shadcn 无 copy 件，5 处收敛为一个 CopyButton | 4 |

### 2.2 表单输入（17 条）

| # | 现状模式 | 主实现 file:line | 迁移目标 | 批 |
|---|---|---|---|---|
| 10 | 单行文本输入 | components/ui/input.tsx:7；channel-form-dialog.tsx:159、179、210、248；channel-detail.tsx:701、712、734、799；keys.tsx:338、374；login.tsx:182、201 | `input`（删页面 h-9/h-8 覆盖，尺寸走 h-control-*） | 2 |
| 11 | 搜索输入 | channels.tsx:249；logs.tsx:786；discover-models-dialog.tsx:340；catalog.tsx:145；combo-editor-dialog.tsx:886 | `input-group` + 自建 SearchInput 封装（shadcn 无 SearchInput） | 2 |
| 12 | 数字输入 | channel-form-dialog.tsx:263；channel-model-form-dialog.tsx:382、394、406、421、447、459；channels.tsx:447；combo-editor-dialog.tsx:501、743 | 保留自建 NumberInput（底层 `input`；shadcn 无 stepper/单位后缀） | 2 |
| 13 | 密码输入 | login.tsx:201；settings.tsx:424、436、447 | 保留自建 PasswordInput（显隐按钮非 shadcn 能力） | 2 |
| 14 | 多行文本 | channel-keys-panel.tsx:471；rewrite-rules-editor.tsx:724、787；channel-detail.tsx:978；playground.tsx:284、343 | `textarea` | 2 |
| 15 | 复选框 | channels.tsx:341、372；channel-form-dialog.tsx:277；channel-model-form-dialog.tsx:492、517、607、668；playground.tsx:362；rewrite-rules-editor.tsx:1071、1082；discover-models-dialog.tsx:230；combo-editor-dialog.tsx:478 | `checkbox`（Radix；补 indeterminate） | 2 |
| 16 | 单选按钮 | channel-models-panel.tsx:372；settings.tsx:319 | `radio-group` | 2 |
| 17 | 开关 | channels/enabled-toggle.tsx:10（role=switch 在 :14） | `switch` → **删除** enabled-toggle.tsx | 2 |
| 18 | 原生下拉（19 处 / 11 文件） | channel-form-dialog:225；channel-model-form-dialog:358；rewrite-rules-editor:870、1000、1033；combo-editor-dialog:511、581；pagination:108；catalog:131；channel-detail:776；channels:257；dashboard:800、841、857、873；logs:827、913、923；playground:227 | `select`（Radix）；过渡期可先用 `native-select` 统一外观（移动端行为不变） | 2 |
| 19 | 自建搜索下拉 | components/ui/search-select.tsx:166-205；pages/logs.tsx:815 | **用户拍板覆盖**：new-york 风格没有独立 `combobox` 条目，等价组合就是 **popover + command**，按此实现 → **删除** search-select.tsx | 3 |
| 20 | 多选（通用） | channels.tsx:340、371；combo-editor-dialog.tsx:680 | 保留自建：业务多选（checkbox 列表 + 已选 N 项摘要），shadcn 无 MultiSelect | 2 |
| 21 | 时间范围 | components/ui/time-range-picker.tsx:99、102；logs.tsx:801 | **用户拍板覆盖**（t4 已实装）：时间过滤**改用 calendar**（react-day-picker/date-fns 已引入），原「保留自建（ADR-063 明确不换）」的结论**作废** → 自建 time-range-picker 后续删除 | — |
| 22 | 日期时间输入 | time-range-picker.tsx:166、175；dashboard.tsx:821、829 | 保留自建：原生 datetime-local，shadcn 只有日期级 date-picker | — |
| 23 | 标签 | components/ui/label.tsx:1；各表单 | `label` | 2 |
| 24 | 表单行/字段容器 | channel-form-dialog.tsx:185 起（各弹窗 div + space-y） | `field`（FieldGroup / FieldLabel / FieldDescription） | 2 |
| 25 | 校验错误（3 份同名 FieldError） | channel-form-dialog.tsx:69；channel-model-form-dialog.tsx:148；channel-detail.tsx:281 | `field` 的 FieldError → **删除**三份手写（补 aria-describedby / aria-invalid） | 2 |
| 26 | 字数计数 | channel-detail.tsx:986 | 保留自建：shadcn 无计数组件 | — |

### 2.3 数据展示（18 条）

| # | 现状模式 | 主实现 file:line | 迁移目标 | 批 |
|---|---|---|---|---|
| 27 | 卡片 | components/ui/card.tsx:1；各页 Card | `card`（页面禁止再用 className=p-5 绕过 CardHeader/Content） | 1 |
| 28 | 指标条 | components/ui/metric-pills.tsx:18；logs.tsx:771 | 保留自建：业务磁贴（内部改用 card 结构，加载态换 skeleton） | — |
| 29 | 表格（9 处 / 8 文件） | catalog:163；channels:336；combos:296、380；keys:189；logs:1027；log-detail-drawer:213；channel-models-panel:347；rewrite-rules-editor:1151 | `table`（只取样式层；**不引 TanStack**，排序/筛选后续单独立项） | 4 |
| 30 | 表格全选/行选择 | channels.tsx:340、371 | `checkbox`（indeterminate）+ 保留选择逻辑 | 4 |
| 31 | 可展开行 | logs.tsx:1069（tr role=button） | 保留自建：`table` + 行展开；需先解 role=button 与行内按钮的语义冲突 | 4 |
| 32 | 表格滚动/粘性表头 | logs.tsx:1026-1027 | 保留自建：`table` 容器 + sticky thead | 4 |
| 33 | 空态 | channels.tsx:304；combos.tsx:284；dashboard.tsx:935；keys.tsx；catalog.tsx | `empty`（若 registry 版本无此件，用 card 封装的同一 API） | 4 |
| 34 | 加载态 | channels.tsx:299；channel-detail.tsx:514；全局 72 处 animate-pulse/Loader2 | `spinner`（收敛 Loader2） | 1 |
| 35 | 骨架屏 | 仅 metric-pills 的 animate-pulse 文本 | `skeleton` | 1 |
| 36 | 错误态 | channels.tsx:285；channel-detail.tsx:531；各 query | `alert`（现有 alert.tsx 换源码，4 variant 映射） | 1 |
| 37 | 状态徽章（3 份） | connection-status-badge.tsx:21；model-health-badge.tsx:26；probe-status-badge.tsx:14 | `badge` → **删除**三份实现（tone 映射保留在调用方） | 1 |
| 38 | 通用计数徽章 | models.tsx:97；combos.tsx:207；channels.tsx:269 | `badge` → **删除** 3 处手写（合并为同一件） | 1 |
| 39 | 图标徽章 | components/ui/icon-badge.tsx:1 | 保留自建：8 tone × 3 size 是业务语义，shadcn 无对应件 | — |
| 40 | 定义列表 | channel-detail.tsx:1009；log-detail-drawer.tsx:93 | 保留自建：dl/dt/dd，shadcn 无 DescriptionList | — |
| 41 | 普通列表 | overview.tsx:417；sidebar.tsx:60；combo-editor-dialog.tsx:680 | 保留自建：密度/分隔线按页面定 | — |
| 42 | 代码块 | overview.tsx:295；log-detail-drawer 的 pre | 保留自建：CodeBlock（shadcn 无必需件） | — |
| 43 | 头像 | topbar.tsx:142 | `avatar` | 1 |
| 44 | 分隔线 | 全局 border-b/border-t | `separator` | 1 |

### 2.4 导航与浮层（15 条）

| # | 现状模式 | 主实现 file:line | 迁移目标 | 批 |
|---|---|---|---|---|
| 45 | 侧栏导航 | components/layout/sidebar.tsx:1；app-shell.tsx:31 | 保留自建：布局骨架；shadcn sidebar 自带 Provider/折叠状态机，与 app-shell 受控折叠冲突 | — |
| 46 | 面包屑 | topbar.tsx:100 | `breadcrumb` | 4 |
| 47 | 标签页（3 处） | models.tsx:76；channel-detail.tsx:649；combo-editor-dialog.tsx:837 | `tabs`（吸收 lib/use-roving-tabs.ts） | 3 |
| 48 | 模态框（15 个使用点） | components/ui/modal.tsx:18（role=dialog 在 :41） | `dialog` → **删除** modal.tsx | 3 |
| 49 | 确认框（15 个确认点 + 1 处 window.confirm） | channel-keys-panel.tsx:587、625；channels.tsx:555、598；keys.tsx:384、406；model-availability.tsx:126；combos.tsx:200 | `alert-dialog` → **删除** window.confirm | 3 |
| 50 | 抽屉 | logs/log-detail-drawer.tsx:532 | `sheet`（side=right） | 3 |
| 51 | 下拉菜单（2 套手写） | layout/theme-menu.tsx:67；layout/topbar.tsx:153 | `dropdown-menu` → **删除**两套手写 | 3 |
| 52 | 全局搜索 | layout/global-search.tsx:90 | `command`（dialog + command，补 ⌘K 提示与 listbox 语义） | 3 |
| 53 | 路由链浮层 | pages/logs.tsx:163 | `popover` | 3 |
| 54 | 标题提示 tooltip（115 处） | 见 §3.2 拆分（27 删 + 1 补 aria-label + 55 Tooltip + 3 改可见帮助 + 29 组件 prop） | 属性类进批 1；`tooltip` 进批 4 | 1 / 4 |
| 55 | 移动导航 | layout/app-shell.tsx:46 | `sheet` | 3 |
| 56 | 折叠面板 | log-detail-drawer.tsx:247；combo-editor-dialog.tsx:811 | `collapsible` | 3 |
| 57 | Toast | components/ui/toast.tsx:98-132 | `sonner` → **删除** toast.tsx（保留 role=alert/status 语义） | 3 |
| 58 | 告警条 | components/ui/alert.tsx:7 | `alert`（已有件换源码） | 1 |
| 59 | 进度条 | 无（role=progressbar 0 处） | `progress`（新增件） | 4 |

### 2.5 数据可视化与特殊交互（9 条）

| # | 现状模式 | 主实现 file:line | 迁移目标 | 批 |
|---|---|---|---|---|
| 60 | 折线/柱状图 | dashboard.tsx:201（TrendChart）、:347（role=img） | 保留自建：手写 SVG；shadcn chart 是 Recharts 封装，新增运行时依赖且无收益 | — |
| 61 | 热力图 | dashboard.tsx:641 | 保留自建：grid 自绘（补键盘与 role=img 是另一件事） | — |
| 62 | 图表 tooltip | dashboard.tsx:448、462 | 保留自建：与 shadcn tooltip 的碰撞/边界策略不同，硬套会退化 | — |
| 63 | 图例开关 | dashboard.tsx:477 | 保留自建：`toggle` + aria-pressed（32 系列需折叠/搜索） | — |
| 64 | 拖拽排序 | combo-editor-dialog.tsx:717（draggable） | 保留自建：HTML5 DnD + 上下移按钮兜底，shadcn 无 dnd 件 | — |
| 65 | 自定义滚动容器 | index.css:246（.scrollbar-thin）；logs.tsx:1026 | `scroll-area` | 4 |
| 66 | 页面标题区 | 13 个页面各写 h1 + 描述 + 右侧操作 | 保留自建：PageHeader（shadcn 无） | — |
| 67 | 焦点环 | index.css 的 :focus-visible 全局规则（ui-inventory.md §2.5 记 :172） | 保留自建：全局 outline + --focus-ring；shadcn 的 ring 指向同一变量 | — |
| 68 | 响应式横向滚动表格 | channels.tsx:335；logs.tsx:1026；combos.tsx:295 | 保留自建：`table` 容器（overflow-x-auto + min-w） | 4 |

## 3. 分组统计

### 3.1 三类处置

| 处置 | 条数 | 说明 |
|---|---:|---|
| 换 shadcn | 37 | 交互行为由 Radix 承载；页面调用点必须改，旧件在全部页面替换完后删 |
| 保留自建 | 25 | 只有三类：图表与可视化（28、60–64、39、40、42）、业务组合件（4、7、9、12、13、20–22、26、31、32、41、68）、布局骨架（45、66、67） |
| 删除合并 | 6 | 25（FieldError×3）、37（状态徽章×3）、38（计数徽章×3）、17（enabled-toggle）、19（search-select）、57（toast） |
| 合计 | 68 | 与 ui-inventory.md:10 一致 |

### 3.2 115 处 title= 的拆分（判定规则在 §3.3）

| 类别 | 处置 | 处数 | 代表位置 |
|---|---|---:|---|
| 组件 prop（不是 DOM title） | 保留；迁移后改成 DialogTitle / 组件标题 prop | 29 | log-detail-drawer.tsx:344（Section）、channels.tsx:560（Modal）、dashboard.tsx:964（TopList）、App.tsx:71（PagePlaceholder） |
| 可访问名：title 与 aria-label 重复 | 删 title，保留 aria-label | 27 | channels.tsx:499、pagination.tsx:133、overview.tsx:285、model-availability.tsx:115 |
| 可访问名：缺 aria-label | 补 aria-label 后删 title | 1 | dashboard.tsx:477（图例开关，原有 aria-pressed 无名称） |
| 真提示 | `tooltip` | 55 | logs.tsx:171、catalog.tsx:174、log-detail-drawer.tsx:388、channels.tsx:414 |
| 真提示但宿主不可聚焦 / 是表单控件 | 改可见帮助文字（field description）或补 tabIndex + aria-label | 3 | channels.tsx:454（input）、channel-models-panel.tsx:376（checkbox）、dashboard.tsx:654（热力图单元格） |
| 合计 | | 115 | 产品 tsx 115 处 / 27 文件（另 1 处在 __tests__/auth-routing.test.tsx:140 的选择器里） |

### 3.3 判定规则

- **R1 组件 prop**：宿主是 React 组件（`<Modal>`、`<Section>`、`<TopList>`、`<HeadersGroup>`、`<PagePlaceholder>`）且 title 传的是 props —— 不是 DOM 属性，**不动**，迁移时改成该组件的标题 API。
- **R2 可访问名**：宿主是按钮/链接/输入且元素内**没有可读文本**（纯图标）→ title 是事实上的名字 → 改 `aria-label`；**若已有 aria-label，直接删 title**（重复命名会让读屏念两遍）。
- **R3 真提示**：宿主是可 hover / 可聚焦的数据或文本节点（span、p、td、dd、th、div、svg 元素），内容是「截断全文 / 列头解释 / 状态说明」→ `tooltip`。
- **R4 表单控件上的说明文字**：提示的是字段规则而非「这是什么」→ 改成 FieldDescription 可见文字；tooltip 在触屏与读屏下不可达，且 input 上的 title 在部分浏览器不显示。
- **R5 装饰元素**：宿主是 `aria-hidden` 装饰节点（sidebar.tsx:33 的 logo）→ title 无意义，删。

## 4. 分批（0 为前置，1–4 为本轮四个 PR）

每批的 done 定义（可直接粘贴的命令 + 预期输出特征）见 §7。

### 批 0（前置，单独 PR）：Tailwind 4 + 别名层 —— ✅ DONE（2026-11-14，commit 23a2c9a）

- 文件：`web/package.json:39`（tailwindcss ^3.4.19 → 4.x；新增 `@tailwindcss/vite`，**不采用 `@tailwindcss/postcss` 路线**；移除 autoprefixer 直连）、`web/postcss.config.js:1-6`（**整文件删除**——Vite 插件不需要 postcss 环节，是删除而非替换内容）、`web/tailwind.config.js:1-80`（darkMode :3、colors :7-49、borderRadius :54-58、keyframes :63-76 全部搬进 CSS）、`web/src/index.css:1-3`（@tailwind 三指令 → @import "tailwindcss"）、`:5-96`（:root 74 条）与 `:97-153`（.dark 61 条）、`:79-94` 与 `:154-169`（16 槽色板原样搬）。
- 新增别名层约 40 行（token 语义以 `docs/adr/ADR-066-token-semantics-adoption.md` 为权威：`--color-muted` = **弱表面** `var(--surface-2)`（`index.css:63`），文字弱化走 `text-muted-foreground`（源头 `--text-muted`）；圆角**两套标尺同值并存** —— 语义档 badge 6 / control 8 / card 12（`index.css:14-16`）、数值档 sm 6 / md 8 / lg 12 / xl 16（`index.css:20-23`），vendored 写 `rounded-md` 即拿 control 档 8px）：`--background: var(--bg)`、`--foreground: var(--text-title)`、`--card: var(--surface)`、`--popover: var(--surface)`、`--primary: var(--primary)`、`--muted: var(--surface-2)`、`--border: var(--border)`、`--input: var(--border-strong)`、`--ring: var(--focus-ring)`、`--destructive: var(--danger)`、`--radius-*` 指向 8/12/6px。
- 改动点：4 个文件 + 约 135 条变量 + 约 40 行别名。
- 验收：`npx tsc --noEmit`；`npx vitest run`（现有 19 个测试文件）；`npx vite build`；`npx playwright test`（web/e2e/visual.spec.ts，7 张快照应为 0 差异，有差异逐张写原因）。
- 回滚：单 PR revert，不触页面。

### 批 1（低风险，tsc + 现有 vitest 可覆盖）：属性清理 + 原子件 —— ✅ DONE（2026-11-14；批 0 与 vendoring 同在 commit 23a2c9a「feat(web): adopt Tailwind 4 and vendor the shadcn component set」）

- 28 处 title 语义清理（27 删 + 1 补 aria-label），位置见 §3.2；同步改 `web/src/__tests__/auth-routing.test.tsx:140` 的选择器（`a[title="全局概览"]` → `a[aria-label="全局概览"]`，否则该测试必挂）。
- 新增件：components/ui/badge.tsx、separator.tsx、skeleton.tsx、spinner.tsx、avatar.tsx；换源码：button.tsx:6、card.tsx:1、alert.tsx:7、label.tsx:1。
- 合并：connection-status-badge.tsx:21、model-health-badge.tsx:26、probe-status-badge.tsx:14、models.tsx:97、combos.tsx:207、channels.tsx:269 → badge。
- 改动点：约 44（28 处 title + 10 个基元文件 + 6 处合并点）。
- 验收：`npx tsc --noEmit`；`npx vitest run` 全量；`rg -c "title=" src --glob "!**/__tests__/**"` 从 115 降到 87；视觉快照逐张确认（button 尺寸阶梯变化属预期差异，需在 PR 里贴前后对比）。
- **组件清单（lead 定案口径，2026-11-14）**：`src/components/ui/` 现 **43 个 .tsx** = **A 27 个 vendored registry 组件**（new-york **v3** 风格）+ **B 10 个并入 shadcn 底座的自有基元**（button、badge、card、input、alert、label、pagination、skeleton、spinner、empty）+ **C 6 个自建件**（icon-badge、metric-pills、modal、search-select、time-range-picker、toast）。另有 **1 个 hook**：`src/hooks/use-mobile.ts` —— 是 hook 不是组件，不计入组件数。**目录还会再缩**：time-range-picker 由 calendar 版替换（t4），search-select / modal / toast 在批 3/4 删除。**版本冻结**：这 27 个 vendored 是 new-york **v3** 快照（`cn` 来自 `@/lib/utils`，`Slot` 来自 `@radix-ui/react-slot`）；线上 registry 对同一 URL 偶尔会返回 **v4 风格** payload，**重新拉取必须校验**——源码里出现 `from "cn"` 或 `from "radix-ui"`（v4 的统一包）一律拒收。

### 批 2（中风险）：表单控件

- select 19 处（11 文件）= channel-form-dialog:225、channel-model-form-dialog:358、rewrite-rules-editor:870/1000/1033、combo-editor-dialog:511/581、pagination:108、catalog:131、channel-detail:776、channels:257、dashboard:800/841/857/873、logs:827/913/923、playground:227。
- textarea 6 处、checkbox 12 处、radio 2 处、switch 1 处（删 enabled-toggle.tsx）、toggle-group 3 处（catalog:118、dashboard:784/976/994）、field/field-error（替三份 FieldError）。
- 改动点：约 50。
- 验收：tsc；vitest 的 channels.test.tsx、channel-keys.test.tsx、discover-models.test.tsx、settings.test.tsx、login.test.tsx、rewrite-editor.test.tsx、channel-models.test.tsx、catalog.test.tsx；键盘手测（Tab / ↑↓ / Home / End / typeahead / Space / Enter / Esc）；移动端原生 select 行为抽查（ADR-063 记的未解风险）。

### 批 3（高风险）：浮层与焦点

- dialog 15 个 Modal 使用点（channels:555/598、keys:305/354/384/406/435、channel-form-dialog:131、channel-model-form-dialog:307、channel-keys-panel:587/625、channel-models-panel:522、discover-models-dialog:254、combo-editor-dialog:422、model-availability:126）；alert-dialog 15 个确认点 + combos.tsx:200 的 window.confirm。
- sheet：log-detail-drawer.tsx:532、app-shell.tsx:46；dropdown-menu：theme-menu.tsx:67、topbar.tsx:153；command：global-search.tsx:90；popover：logs.tsx:163；collapsible：log-detail-drawer.tsx:247、combo-editor-dialog.tsx:811；tabs 3 处；sonner 替 toast.tsx。
- 改动点：约 43。
- 验收：vitest 的 global-search.test.tsx、logs.test.tsx、channel-detail-flow.test.tsx、combos.test.tsx、keys.test.tsx、channel-keys.test.tsx；焦点五条硬指标（打开后初始焦点落在首字段、Tab 循环不逃逸、Esc 关闭、关闭后焦点归还触发元素、背景内容不可达）。

### 批 4（最大 diff）：表格、骨架、死代码、防护

- table 9 处（catalog:163、channels:336、combos:296/380、keys:189、logs:1027、log-detail-drawer:213、channel-models-panel:347、rewrite-rules-editor:1151）、空态 5 处 → empty、scroll-area 3 处、pagination.tsx:97、breadcrumb topbar.tsx:100、progress 新增件、tooltip 55 处。
- 删除：12 个自建基元（ADR-064:60 记 12 文件 1,246 行）在对应控件全页面替换完后移除；其中 modal / toast / search-select / enabled-toggle 已在前批删。
- 防护：`web/scripts/lint-ui.mjs`（2026-11-14 已落地，零依赖；npm script 已加：`npm run lint:ui` = `node scripts/lint-ui.mjs`）：禁页面内原生 select/table、禁 `text-[Npx]`、禁 `h-8|h-9|h-10`；`/dev/ui` 画廊。
- 收尾：7 张视觉快照重拍并逐张人眼确认（`e2e/__screenshots__` 实测 7 个 png、`visual.spec.ts` 里 7 个 test 各一张；ADR-064 已于 2026-11-14 订正为 7 张）；文档同步 —— ADR-031 标 `partially superseded`（shadcn/Radix 部分由 ADR-064 取代，**TanStack Table 禁令仍有效**）、ssot §9/§13/§15。
- 改动点：约 90。
- 验收：vitest 全量；快照重拍；`rg -n "<select|<table" src/pages src/components --glob "!**/__tests__/**"` 结果必须为 0。

## 5. 不迁移清单

| 项 | 位置 | 理由 |
|---|---|---|
| 16 槽图表色板 | src/index.css:79-94（浅）/ :154-169（深） | ADR-040 锁死 16 槽且「不许新增」；shadcn chart 只有 5 槽语义色，套进去会丢第 6–16 槽 |
| 图表本体（折线/柱状/热力图） | dashboard.tsx:201、:641 | shadcn chart 是 Recharts 封装：新增运行时依赖、替换手写 SVG 无收益，且会和 16 槽色板对不上 |
| 图表 tooltip 与图例开关 | dashboard.tsx:448、462、477 | 与 shadcn tooltip 的碰撞/边界翻转策略不同；图例要支持 32 系列折叠 |
| 布局骨架（app-shell / sidebar / topbar / PageHeader） | app-shell.tsx:31、sidebar.tsx:1、topbar.tsx:100、13 页 h1 区 | shadcn sidebar 自带 Provider 与折叠状态机，与受控折叠冲突；PageHeader 无对应件 |
| index.css 颜色变量与 tailwind.config.js 映射 | index.css:5-96、:97-153；tailwind.config.js:7-49、:54-58 | 通过 §4 批 0 的别名层桥接；变量名不改，主题仍单点控制 |
| 业务组合件 MetricStrip / IconBadge / TimeRangePicker / CodeBlock / CopyButton | metric-pills.tsx:18、icon-badge.tsx:1、time-range-picker.tsx:99、overview.tsx:295 | 无对应件；硬套会退化成「包装一层再写一遍」 |
| 拖拽排序 | combo-editor-dialog.tsx:717 | shadcn 无 dnd 件；引入 dnd-kit 属另一件事 |
| 暗色策略与防闪 | lib/theme.ts:25-30、index.html:9-28 | 仍是 `.dark` class 单点；换 shadcn 主题机制会同时动防闪脚本 |

## 6. 迁移时不要做的事

1. **别把 aria-label 换成 Tooltip**。Tooltip 是视觉提示，不是可访问名；28 处 title 里有 27 处本就与 aria-label 重复，正确动作是删 title（R2），不是包一层 Tooltip。
2. **别用 shadcn 的 dark 主题机制取代现有 `.dark` class**。保持 `darkMode: class` → Tailwind 4 的 `@custom-variant dark`；`lib/theme.ts:25-30` 与 `index.html:9-28` 的防闪脚本不动，否则首屏会闪白。**口径补充（2026-11-14）**：`index.css:3-6` 已声明 `@custom-variant dark (&:where(.dark, .dark *))`，组件里的 `dark:*` 变体**仍编译到 `.dark` 类上**，不等于换成 `prefers-color-scheme`；因此被 `no-dark-prefix` 命中的 5 处属于「规则与 vendored 写法相抵」，**不构成违反本节**（冻结契约见 §7.8：vendored 目录内降级为 warn）。
3. **别引 TanStack Table**。ADR-064 只取代 ADR-062 / ADR-063，**ADR-031 关于 TanStack Table 的那条仍有效**；table 只取样式层，排序/筛选要另立项。（ADR-031 的「不引 shadcn/Radix」部分已被 ADR-064 取代，但**只标 `partially superseded`、不整条标 superseded**。标注动作由 t7 执行，本文只登记口径。）
4. **别一次性清空 title=**。29 处是组件 prop（Modal / Section / TopList / HeadersGroup / PagePlaceholder），删了要么编译错、要么弹窗丢标题。
5. **别改 shadcn 源码结构**。只允许改 token 类与尺寸类（h-9 → h-control-*）；**圆角不用改** —— 按 ADR-066，`rounded-md` 已等于 control 档 8px（数值档 sm6/md8/lg12/xl16 与语义档 badge6/control8/card12 同值并存，`index.css:14-23`），把 `rounded-md` 重命名成 `rounded-control` 只是换名字、不换像素；结构一改就失去「行为可预期」与上游手动 merge 的可能（ADR-063 约束 5）。
6. **别让页面直接 import `@radix-ui/*`**。唯一入口是 `components/ui/*`，否则下一轮盘点会多出一类「页面直连底座」的漂移。
7. **别在同一 PR 改 `index.css` 的变量名**。别名层只做指向（`--background: var(--bg)`），改名会让 74+61 条变量与所有工具类同时炸。
8. **别提前删旧基元**。旧件保留到该控件在**所有**页面替换完毕（ADR-064:49 的回滚点：批 3 之前整体可退，之后按控件回退）。
9. **别在页面里用 `text-[13px]` / `h-8` 覆盖组件内部尺寸**。尺寸阶梯从 `h-control-*` 走，否则等于把今天的漂移原样搬进新件。
10. **别让 sonner 与自建 toast 并存**。ADR-064 的核心论点就是「两套实现长期并存 = 缺陷来源」；批 3 内必须删干净 toast.tsx。

## 7. 迁移验收清单

> 规则：每条都要跑出**具体数字或具体字串**才算 done；「通过即可」不算。
> 命令从仓库根开始，全部兼容 Windows PowerShell 5.1（不用 `&&`、不用 `??`、不依赖 rg）。
> 注：视觉基线是 **7 张** —— `web/e2e/__screenshots__` 下 7 个 png，`visual.spec.ts` 里 7 个 test 各一张（§7.0 可核）。ADR-064 的快照数已于 2026-11-14 订正为 7 张，本文与之一致。

### 7.0 一次性准备

```powershell
Set-Location C:\Users\microsoft\workspace\webdev\slipstream\web

function Count-Hits {
  param([string]$Pattern, [string[]]$Roots = @("src"), [switch]$NoUi, [switch]$WithTests)
  $files = Get-ChildItem $Roots -Recurse -Include *.tsx,*.ts
  if (-not $WithTests) { $files = $files | Where-Object { $_.FullName -notmatch "__tests__" } }
  if ($NoUi) { $files = $files | Where-Object { $_.FullName -notmatch "components.ui" } }
  ($files | Select-String -Pattern $Pattern -AllMatches | ForEach-Object { $_.Matches.Count } | Measure-Object -Sum).Sum
}

(Get-ChildItem e2e\__screenshots__ -Filter *.png).Count   # 应为 7
```

预期：函数定义无输出；末尾打印 `7`。若 `Get-ChildItem` 报路径不存在，说明当前目录不是 `web`。

这组命令 2026-11-14 在 Windows PowerShell 5.1 上实跑过，原始输出：`title=115 select=19 selectNoUi=18 table=9 checkbox=12 radio=2 textarea=6 modal=15 confirm=1 menu=2 arialabel=151 cssvars=135 shots=7 uiFiles=12 testFiles=19`。你机器上的数字与下表不一致时，以实测为准并在 PR 里记差异。

基线（迁移前先跑，把这张表抄进 PR 描述；数字对不上说明代码已被改过，先查清再动手）：

| 指标 | 命令 | 迁移前 |
|---|---|---:|
| title= 处数 | `Count-Hits "title="` | 115 |
| 原生 select | `Count-Hits "<select"` | 19 |
| 原生 table | `Count-Hits "<table"` | 9 |
| checkbox / radio | `Count-Hits "type=.checkbox"` / `Count-Hits "type=.radio"` | 12 / 2 |
| textarea | `Count-Hits "<textarea"` | 6 |
| Modal 使用点 | `Count-Hits "<Modal"` | 15 |
| window.confirm | `Count-Hits "window\.confirm"` | 1 |
| role=menu（不含 menuitem） | `Count-Hits "role=.menu[^a-z]"` | 2 |
| index.css 变量声明 | `(Select-String -Path src\index.css -Pattern "^\s*--[a-z0-9-]+\s*:").Count` | 135（74 浅 + 61 深） |
| 视觉快照 | `(Get-ChildItem e2e\__screenshots__ -Filter *.png).Count` | 7 |
| components/ui 文件数 | `(Get-ChildItem src/components/ui -Filter *.tsx).Count` | 12 |
| 测试文件数 | `(Get-ChildItem src/__tests__ -Filter *.test.tsx).Count` | 19 |

### 7.1 批 0（Tailwind 4 + 别名层）done 定义 —— ✅ 已完成

```powershell
npx tsc --noEmit; if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
npx vite build; if ($LASTEXITCODE -ne 0) { throw "build failed" }
npx vitest run; if ($LASTEXITCODE -ne 0) { throw "vitest failed" }
npm run test:visual; if ($LASTEXITCODE -ne 0) { throw "visual failed" }
(Select-String -Path src\index.css -Pattern "^\s*--[a-z0-9-]+\s*:").Count
git status --porcelain e2e/__screenshots__
```

| 检查 | 预期输出特征 |
|---|---|
| tsc | 无任何输出，`$LASTEXITCODE = 0` |
| vite build | 末行含 `built in`，退出码 0；`dist/assets` 的 CSS 体积变化写进 PR |
| vitest | 汇总行 `Test Files  19 passed (19)`，退出码 0 |
| 视觉 | `7 passed`、0 差异（阈值 `maxDiffPixelRatio: 0.002`，playwright.config.ts:13） |
| index.css 声明数 | **≥ 135**：原有 135 条一条不能少，别名层会新增约 40 条，所以是 ≥ 不是 = |
| 快照变更 | `git status --porcelain e2e/__screenshots__` **空**（批 0 不许改视觉） |

### 7.2 批 1（属性清理 + 原子件）done 定义 —— ✅ 已完成

```powershell
npx tsc --noEmit; if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
npx vitest run; if ($LASTEXITCODE -ne 0) { throw "vitest failed" }
Count-Hits "title="
Count-Hits "aria-label="
Select-String -Path src\__tests__\auth-routing.test.tsx -Pattern "a\[title"
@("badge","separator","skeleton","spinner","avatar") | ForEach-Object { Test-Path "src/components/ui/$_.tsx" }
```

- `Count-Hits "title="` = **87**（115 − 28；不是 87 就按 §3.2 的 28 处清单逐条找）。
- `Count-Hits "aria-label="` = **152**（151 + dashboard.tsx:477 补的那 1 个）；若新 shadcn 源码自带 aria-label 会再增加，增加的文件必须写进 PR。
- `Select-String ... "a\[title"` 必须 **0 行**（基线 1 行，实测；原来的 `a[title='全局概览']` 已改成 aria-label 选择器）。
- 5 个 `Test-Path` 必须全为 `True`。
- 视觉：`npm run test:visual` 允许失败（button 尺寸阶梯是预期变化），但每张 diff 都要在 PR 里贴前后图 + 一句原因，不许 `--update-snapshots` 直接盖。

### 7.3 批 2（表单控件）done 定义

```powershell
npx tsc --noEmit; if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
npx vitest run src/__tests__/channels.test.tsx src/__tests__/channel-keys.test.tsx src/__tests__/discover-models.test.tsx src/__tests__/settings.test.tsx src/__tests__/login.test.tsx src/__tests__/rewrite-editor.test.tsx src/__tests__/channel-models.test.tsx src/__tests__/catalog.test.tsx
Count-Hits "<select" -NoUi
Count-Hits "type=.checkbox" -NoUi
Count-Hits "type=.radio" -NoUi
Count-Hits "<textarea" -NoUi
```

- 8 个测试文件全 `passed`（不是 skipped）：汇总行形如 `Test Files  8 passed (8)`。
- 四个计数全为 **0**（`-NoUi` 排除 components/ui，页面侧必须归零）。

人工（每条记录实际观察值，例：`document.activeElement = INPUT[placeholder="搜索"]`）：

1. 原生下拉在 390×844 视口展开后选项可点、值回填、无双重弹层；
2. `disabled` 项不可选、不参与键盘遍历；
3. 输入 2 个字符后 ↑↓ 命中的选项文本与输入前缀一致（typeahead）；
4. 提交后错误文案在字段下方，且该字段 `aria-describedby` 指向文案 id（Elements 面板可查）。

### 7.4 批 3（浮层与焦点）done 定义

```powershell
npx tsc --noEmit; if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
npx vitest run src/__tests__/global-search.test.tsx src/__tests__/logs.test.tsx src/__tests__/channel-detail-flow.test.tsx src/__tests__/combos.test.tsx src/__tests__/keys.test.tsx src/__tests__/channel-keys.test.tsx
Count-Hits "<Modal"
Count-Hits "window\.confirm"
Count-Hits "role=.menu[^a-z]" -NoUi
Test-Path src/components/ui/modal.tsx
Test-Path src/components/ui/toast.tsx
```

- 6 个测试文件 `passed`；`<Modal` = **0**、`window.confirm` = **0**、页面侧 `role=menu` = **0**。
- 两个 `Test-Path` = **False**（旧件已删）。

焦点五条（在 DevTools Console 取 `document.activeElement`，记录真实元素，不许写「正常」）：

1. 打开后 `document.activeElement` 是首字段或首个可操作项，不是 `BODY`；
2. 连按 Tab 走完弹层内元素后回到第一个，循环期间不出现背景按钮或地址栏；
3. Esc 关闭后不触发提交（Network 面板无新请求）；
4. 关闭后 `document.activeElement` 等于打开它的那个按钮；
5. 打开期间 `document.querySelector("main").inert` 为 `true`（或背景元素 Tab 不可达）。

### 7.5 批 4（表格、骨架、死代码、防护）done 定义

```powershell
npx tsc --noEmit; if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
npx vitest run; if ($LASTEXITCODE -ne 0) { throw "vitest failed" }
Count-Hits "<table" -NoUi
Count-Hits "<select" -NoUi
Count-Hits "title="
(Get-ChildItem src/components/ui -Filter *.tsx).Count
npm run lint:ui              # = node scripts/lint-ui.mjs（package.json 已加）
```

- vitest 全量 `19 passed`。
- 页面侧 `<table` = **0**、`<select` = **0**（`components/ui/table.tsx` 内部那一处由 `-NoUi` 排除）。
- `Count-Hits "title="` = **29**（只剩组件 prop，按 §3.2 第一条清单逐条对）。
- `components/ui` 文件数：20:01 基线 12，批 1 原子件落地后已到 16；迁移后 = shadcn 件数 + 保留件（icon-badge、metric-pills 若保留）。
- `node scripts/lint-ui.mjs`（`npm run lint:ui` **已可用**：package.json 已加 `"lint:ui": "node scripts/lint-ui.mjs"`；脚本 2026-11-14 已落地）：done 的定义是**退出码 0 且 stdout 末行 `result: PASS`**；error 已归零（0），不再需要 `--max-errors 1` 这类过渡放宽（预算阶梯见 §7.6）。有违规时先看 `LEVEL RULE COUNT TOP FILES` 表，再看 `error details:` 段的 `src/…:行号  片段`。
- 视觉基线重拍：`npm run test:visual:update` 后 `git status --porcelain e2e/__screenshots__` 必须列出 **7** 个 png，逐张人眼确认后随 PR 提交。

### 7.6 门槛汇总（一眼对照）

| 门槛 | 迁移前 | 批 1 后 | 批 2 后 | 批 3 后 | 批 4 后 |
|---|---:|---:|---:|---:|---:|
| `title=` | 115 | 87 | 87 | 87 | 29 |
| `<select`（页面侧） | 18 | 18 | 0 | 0 | 0 |
| `type=.checkbox` / `type=.radio` | 12 / 2 | 12 / 2 | 0 / 0 | 0 / 0 | 0 / 0 |
| `<textarea` | 6 | 6 | 0 | 0 | 0 |
| `<Modal` / `window.confirm` | 15 / 1 | 15 / 1 | 15 / 1 | 0 / 0 | 0 / 0 |
| `<table`（页面侧） | 9 | 9 | 9 | 9 | 0 |
| 视觉快照 | 7 | 允许变化 | 允许变化 | 允许变化 | 重拍 7 张 |

`<select` 的「迁移前 18」是页面侧口径：19 处里有 1 处在 components/ui/pagination.tsx:108，随批 2 一起换。

`node scripts/lint-ui.mjs` 的预算阶梯（写进 CI 的参数，跟着批次收紧；`--max-warnings` 从**当次实测**起算）：

阶梯的绝对值按**当次实测快照**写（当前：errors **0** / warnings **19** / files **81**，2026-11-14 21:5x / 9847b66 终态 —— 应用层归零，19 条全是 vendored 例外：字号 10 + 盒尺寸 4 + `dark:` 5，均走 relaxed 桶；21:0x 的 0 / 834 / 83 是历史快照）；**每批 PR 开始时先重跑 `npm run lint:ui -- --json`**，把预算锚到当次实测值，再按同一批的差值收紧（历史差值：批 2 −33、批 4 −796；总账 834 → 19），不要照抄绝对数。**地板 = 19**：`src/components/ui/**` 下的 vendored 例外（字号 10 + 盒尺寸 4 + `dark:` 5）按冻结契约**常驻为 warn**，不计入待清量；应用层任何一条新违例都会让 `--max-warnings 19` 直接失败。

| 阶段 | `--max-errors` | `--max-warnings` | 这一步清掉什么 |
|---|---:|---:|---|
| 批 1 完成 + t2 合并（21:0x 实测，files=83；**历史快照**） | **0** | **834** | 基线冻结：`window.confirm` 已由 t2 移除，error 级归零；warn = select 19 + table 9 + 字号 660 + 尺寸 100 + title 27 + checkbox 12 + radio 2 + vendored dark 5 |
| 批 2 后（历史） | 0 | **801** | select 19 + checkbox 12 + radio 2（834 − 33） |
| 批 3 后（历史） | 0 | **801** | 浮层/焦点迁移不改 warn 面（error 已为 0） |
| 批 4 后（历史，21:0x 冻结值口径） | 0 | **5** | table 9 + 任意字号 660 + 任意尺寸 100 + title 27（801 − 796 = 5） |
| **批 4 终态（9847b66，files=81）** | **0** | **19** | 应用层归零：select 19→0、table 9→0、字号 660→0（余 vendored 10）、尺寸 100→0（余 vendored 4）、title 41→27、checkbox 12→0、radio 2→0；**19 = 10 + 4 + 5，全部是 vendored 例外** |

| 地板（契约内常驻） | 0 | **19** | 字体 10 + 盒尺寸 4 + `dark:` 5 共 19 处 vendored 例外（字号 / 尺寸 / `dark:` 三条规则在 `src/components/ui/**` 下均走 relaxed 桶） —— 冻结契约：vendored 目录内 warn、应用代码内 error；这 19 处**不列入待清量**，除非 lead 改裁定 |

### 7.7 失败先看哪里

- 第 3 条（`aria-haspopup` / `role="menu"` 为 0）在 `lint-ui.mjs` 里**没有规则**，只能人工核（ui-standards 用 rg，本机等价 PowerShell）：`Get-ChildItem src/pages -Recurse -Include *.tsx | Select-String 'aria-haspopup|role="menu"'` 应为 0 行——2026-11-14 实测 **0 行**（全仓 `role="menu"` 2 处：`components/layout/topbar.tsx:153`、`theme-menu.tsx:67`；`aria-haspopup` 4 处：`topbar.tsx:139`、`theme-menu.tsx:52`、`components/ui/search-select.tsx:131`（listbox）、`components/ui/time-range-picker.tsx:99`（dialog）—— 两组行号并不相同，拿 `aria-haspopup` 全仓搜会看到 4 行，别以为规则漏检）；覆盖表见 `ui-standards.md` §6 第 3 条。
- 视觉只差几像素：先看 `maxDiffPixelRatio`（0.002）与字体渲染差异；仍失败就在 PR 里贴 diff 图，不要直接 `--update-snapshots` 盖掉。
- 视觉命令起不来：playwright 自己会跑 `npm run dev -- --port 3690`（playwright.config.ts:35-38，`reuseExistingServer: false`）；**不要改回 3680** —— 那是网关端口，原因见 §7.9。端口冲突时先 `Get-NetTCPConnection -LocalPort 3690`。
- vitest 报文件不存在：本仓测试是 `src/__tests__/*.test.tsx` 扁平结构，路径里没有 `components/` 段。
- `Count-Hits` 打印空白而不是 `0`：说明没有命中（`Measure-Object -Sum` 对空集合不输出 0），先加 `-WithTests` 确认函数本身可用。
- `lint:ui` 报错但怀疑误报：`node scripts/lint-ui.mjs --verbose` 打出每条违规的 `file:line 片段`；白名单是 per-rule 正则（`table.tsx` / `select.tsx` / `checkbox.tsx` / `radio-group.tsx`），规则语义见 §7.8。

### 7.8 lint:ui 输出怎么读

`npm run lint:ui` 等于 `node scripts/lint-ui.mjs`（零依赖，自己递归走 `src/`，跳过 `__tests__`、`*.test.tsx`、`*.spec.tsx`、`node_modules`、`dist`、`build`，再按规则白名单豁免）。**`npm run lint:ui` 已可用**（package.json 已加 `"lint:ui": "node scripts/lint-ui.mjs"`，与 `node scripts/lint-ui.mjs` 完全等价）。

**两个级别，作用完全不同：**

| 级别 | 规则 | 决定退出码吗 | 意义 |
|---|---|---|---|
| `error` | `no-native-dialog`、`no-inline-hex`、`no-dark-prefix` | **决定**（超过 `--max-errors` 即退出 1） | 红线，长期必须是 0；现在 **0**（t2 已移除 `window.confirm`）。**`no-dark-prefix` 按目录分档**：`src/components/ui/**` 是 vendored registry 源码（合法使用 `dark:` 变体），降级为 **warn**；应用代码里仍是 error。JSON 里该规则带 `relaxed` 子对象（scope / level / count / files），vendored 命中计入 warning、不计入 error。**冻结契约（lead 2026-11-14 定案）**：`no-native-dialog` **不改 id**（脚本注释 + ADR 说明已足够）；`no-dark-prefix` = 应用代码 error / `src/components/ui/**` warn（relax 桶），vendored 命中常驻为 warn。另注：`dark:` 变体本身**不是**违规点 —— `index.css:6` 的 `@custom-variant dark (&:where(.dark, .dark *))` 让 `dark:*` 仍编译到 `.dark` 类上，禁的其实是 `prefers-color-scheme` 媒体查询式暗色 |
| `warn` | 其余 7 条 | 不决定（除非用 `--max-warnings` 收紧） | 迁移进度条 |

**规则 id 与语义有一处不一致，读数时别被 id 骗**：`no-native-dialog` 实际检测的是 `window.confirm` 与 `window.alert`（`scripts/lint-ui.mjs:77-83`），不是 `<dialog>` 元素；目前唯一命中是 `src/pages/combos.tsx:200` 的 `window.confirm(`。所以「error 清零」＝把 `window.confirm/alert` 换成自建确认弹层，跟引入 `<dialog>` 无关。**它是 error 级，那 1 处不计入 warning 总数** —— 盯 warn 总数时别指望它出现在里面，也就别把「warn 没降」误当成「combos 那处没修」。

**计数口径：脚本的 `count` 是「出现次数」，复现时必须用 `.tsx` + `\b` + `-AllMatches`。** 脚本只扫 `src/**/*.tsx`（不含 `.ts`、不含测试），正则带 `\b` 词边界。三条实测口径：① 任意字号 `\btext-\[\d+(?:\.\d+)?px\]` = **672 次 = 672 行**（每行 1 处，次数与行数恰好相同）；② 任意尺寸 `\b[hw]-\[\d+(?:\.\d+)?px\]` = `.tsx` **101 次 / 95 行**（6 行各含 2 处）；③ 字面 `text-[` = 703 次，比 672 多 31 次，差在 rem / var / 颜色等非字号写法 —— 那是**规则范围**差，不是计数方式差。

PS 两个坑：①`-SimpleMatch` 能搜字面量（不加 `-SimpleMatch` 搜 `text-[` 会报 `Unterminated [] set`），但**它不填 `$_.Matches`**（实测累加恒为 0），要数次数只能用正则模式配 `-AllMatches`；②`(... | Measure-Object).Count` 数的是**行**，`(...) | ForEach-Object { $_.Matches.Count } | Measure-Object -Sum).Sum` PS 两个坑：①`-SimpleMatch` 能搜字面量（不加 `-SimpleMatch` 搜 `text-[` 会报 `Unterminated [] set`），但**它不填 `$_.Matches`**（实测累加恒为 0），要数次数只能用正则模式配 `-AllMatches`；②`(... | Measure-Object).Count` 数的是**行**，`(...) | ForEach-Object { $_.Matches.Count } | Measure-Object -Sum).Sum` 数的才是次数。

**交叉验证（2026-11-14 20:3x）：** 用上面这套 PowerShell 逐文件重算尺寸规则，得到 **101 次 / 29 个文件**，与脚本 `--json` 的 per-file counts **逐文件零差异** —— PS 复现口径与脚本完全一致。

**关键读法：`warn` 总数下降才是迁移进度的真实指标。** error 长期是 0，从 error 看不出一批 PR 到底动了多少；`warn` 从**当次实测值**（终态基线 19）掉到多少，才是这批真正清掉的存量。每批 PR 应写「本批 `--max-warnings` 用值 + 与上一批的差值」。

机器可读输出（PowerShell 一行拿全）：

```powershell
$r = node scripts/lint-ui.mjs --json | ConvertFrom-Json
$LASTEXITCODE                     # 0/1/2，门禁判断用这个，不是 pipeline 的成败
$r.ok; $r.filesScanned; $r.totals
$r.rules | Select-Object level, id, count | Format-Table -AutoSize
($r.rules | Where-Object id -eq "no-native-select").files | Select-Object count, path | Format-Table
```

结构（字段名即 JSON 键；`--json` 时 stdout 只有 JSON，人读摘要不打印）：

```text
{
  "ok": false,                     # errors <= maxErrors 且 warnings <= maxWarnings 时为 true
  "root": "src",                   # 扫描根（相对 web）
    "filesScanned": 83,              # 非测试 tsx 数（2026-11-14 21:0x 实测；components/ui 从 12 涨到 43，还会继续漂）
    "totals": { "errors": 0, "warnings": 19, "maxErrors": 0, "maxWarnings": null },
  "rules": [
    { "id": "no-native-select", "level": "warn", "why": "…", "count": 19,
      "files": [ { "path": "src/pages/dashboard.tsx", "count": 4,
                   "samples": [ { "line": 800, "snippet": "<select" } ] } ],
      "violations": [] },          # error 级总会填；warn 级只有 --verbose 才填
    ... 共 10 条，顺序固定 ...
  ]
}
```

批次对比（存两份 JSON 再比，别用眼睛比截图）：

```powershell
node scripts/lint-ui.mjs --json | Set-Content (Join-Path $env:TEMP "lint-before.json")
node scripts/lint-ui.mjs --json | Set-Content (Join-Path $env:TEMP "lint-after.json")
$a = (Get-Content (Join-Path $env:TEMP "lint-before.json") | ConvertFrom-Json).rules
$b = (Get-Content (Join-Path $env:TEMP "lint-after.json") | ConvertFrom-Json).rules
Compare-Object $a $b -Property id, count
```

预期：`count` 只减不增；某条规则计数一点没动，说明这一批没碰到对应控件，回去核对该批的文件清单。

当前快照（**2026-11-14 21:5x，9847b66 终态，files=81**）：error **0** / warn **19** / **exit 0（PASS）**，门禁 = `npm run lint:ui` = `node scripts/lint-ui.mjs --max-warnings 19`。明细：select **0**、table **0**、字号应用层 **0**（vendored 10）、尺寸应用层 **0**（vendored 4）、title **27**、checkbox **0**、radio **0**、hex 0、dialog 0、vendored `dark:` 5 —— **19 = 10 + 4 + 5，全部走 relaxed 桶**。历史快照（21:0x，files=83）：0 error / 834 warning。834 = 19+9+660+100+27+12+2+5。`window.confirm` 已由 t2 移除，error 级归零。（前序快照：20:4x 是 82 文件 / 1 / 848；20:3x 是 82 / 6 / 843；20:01 是 51 / 839。）

**这个数字会随批次推进漂移，别把它当常数**（终态 9847b66：files=81 / 0 error / 19 warning）：批 1 的原子件落地后（components/ui 从 12 个文件增到 16 个、扫描到的 src tsx 从 51 增到 55），同日 21:0x 一度是 **error 1 / warn 840、font-size 673**，20:2x 复跑又回到 **files=55 / error 1 / warn 839**；随后 t1 清理与 t2 合并继续落地，**21:0x 终态为 files=83 / error 0 / warn 834（font-size 660、size 100、title 27）—— 这才是当前冻结基线**。

---

本轮（2026-11-14）只读核对，未修改任何源文件；上面所有数字都由这组命令在同一台机器测得。

### 7.9 三条门禁的真实运行环境（2026-11-14 22:3x 实测）

沙箱放开后 build / vitest / playwright 三条都能真跑，结论与坑一起记在这里，别再从零试错。

**运行时与产物（`npm run build` = `tsc --noEmit && vite build`）**：`✓ built in 24.71s`。dist 主要 chunk：index CSS 94.50 kB（gzip 16.49）、vendor-misc 299.14、vendor-react 222.28、app-components 182.75、app-pages 170.61、vendor-forms 94.01、vendor-router 39.14、vendor-query 35.61、vendor-icons 27.63、app-core 34.73、入口 js 2.63。全部低于 esbuild 的 1 MiB 临时文件阈值（`vite.config.ts:15` 的 chunkSizeGuard 专门盯这条）。

**vitest：27 test files / 214 tests passed。** 首跑曾有 4 个文件 12 条失败，根因是 jsdom 缺 Radix 依赖的浏览器 API，与迁移逻辑无关。`src/test/setup.ts`（`setupFiles`）现在补了 4 类：`ResizeObserver` stub、`Element.prototype.hasPointerCapture/setPointerCapture/releasePointerCapture`、`Element.prototype.scrollIntoView`、永不匹配的 `window.matchMedia`（主题检测 `src/lib/theme.ts` 与 sonner 都会调它）。缺任何一个，任何含 Select / Popover / Tooltip 的用例都会在首屏直接抛。

**迁移带来的契约变化（改测试不是放水）：**

| 旧断言 | 现在的写法 | 原因 |
|---|---|---|
| `getByRole("button", { name: "渠道筛选" })` 等 4 个筛选触发器 | `getByRole("combobox", { name: … })` | 触发器换成 Combobox（`ui/combobox.tsx:89-93`），role 是 combobox，名字仍来自 `ariaLabel` |
| `within(row)` 上的 `aria-expanded` | `within(row).getByRole("button", { name: /展开行详情|收起行详情/ })` 再断言 `aria-expanded` | `tr` 不允许 `aria-expanded`，属性在 `pages/logs.tsx:1128-1129` 挂到第一列按钮上，按钮名随状态切换 |
| `getByText("共有 1 个密钥检测失败，2 个通过。")` | 正则 `/共有 1 个密钥检测失败，2 个通过。/` | `AlertDialogDescription` 把整句渲成一段，精确字符串匹配依赖分词边界 |

**视觉：7 passed（连续两次）。** 先 `npm run test:visual:update` 重拍 7 张 PNG（`e2e/__screenshots__`），再 `npm run test:visual` 复跑确认 0 差异；阈值 `maxDiffPixelRatio: 0.002`（`playwright.config.ts:13`）。

**端口纪律（踩过的坑，务必保留）：** 视觉套件原指向 3680，而 3680 上是**网关**（服务生产 `dist`）。Playwright 的 `reuseExistingServer` 于是直接复用网关，测试渲染的是上一次构建的产物 —— 工作树里刚加的 `aria-label` 永远进不了浏览器，症状是「源码改了、测试照旧失败」，极难定位。现在 `playwright.config.ts:20-22,34-39` 用 **3690** 且 `reuseExistingServer: false`，每次跑都自己起 vite，测的才是当前源码。**3680 属于网关，视觉套件不得占用。**

