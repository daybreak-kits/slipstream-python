# 前端 UI 控件与交互模式盘点

> **已归档（2026-11-14）**：本文是历史文档，只作证据与背景，**不再更新、不再作为权威**。现状见 [../ui-adoption-map.md](../ui-adoption-map.md)（台账）与 [../ui-standards.md](../ui-standards.md)（规范正文）。

> 盘点日期：2026-11-14。范围：web/src 下全部 tsx。
> 扫描口径（2026-11-14 20:5x 更新）：lint 实扫 `src` 下 **83 个**非测试 tsx（历史快照 51 → 55 → 82 → 83，随批次增长）；`components/ui/` 现有 **44 个**文件 = A 类 shadcn registry 27 + B 类本仓自有并入 shadcn 基座 11 + C 类本仓自有 6（分类见 `ui-standards.md` §3.0），按公开入口计 13 个基元（ToastProvider 与 useToast 分开计）。docs/ui-stack.md 里的 74 个 tsx 是更早快照，本文件以当次实跑为准。
> 路径约定：下表位置均相对 web/src；file:line 为控件首次出现或主要实现位置。
> 复现命令（在 web 下）：rg -n '<button|<select|<table|<Input|<textarea|<Modal|title=|aria-|role=' src --glob '!**/__tests__/**'；本机没装 `rg` 时用等价 PowerShell：`Get-ChildItem src -Recurse -Include *.tsx | Select-String '<button|<select|<table|<Input|<textarea|<Modal|title=|aria-|role='`（排除 `__tests__` 目录；`Select-String` 默认数**命中行**，同一行多次只算 1；本表字号规则恰每行 1 处，故行数=次数，尺寸规则才是 100 次 / 94 行、28 个文件）

## 0. 结论摘要

- 共盘点 **68 个用户可见控件/交互模式**：**11 个完全由 components/ui 覆盖**、**18 个部分覆盖**、**39 个没有对应基元**。
- 最大的三个漂移源不是颜色，而是**控件配方没有单一来源**：19 个原生 select、9 个原生 table、115 处 title 提示、4 套手写弹层（主题菜单、用户菜单、全局搜索、路由链浮层）各自实现。
- 键盘问题集中在自建浮层，不在原生控件：Modal、抽屉、全局搜索都没有焦点陷阱/初始焦点/关闭归还；两个菜单没有方向键；拖拽只有 HTML5 DnD。
- aria 覆盖率不低（aria-* 517 处 / 42 个产品文件），但**结构性语义**不足：图表与热力图没有文字替代，catalog 分段筛选没有 aria-pressed，全局搜索的结果没有 listbox/option。
- 组件库只能解决第 1 类问题（控件外形），解决不了第 2 类（页面级 pattern）与第 3 类（焦点/键盘）。本文件的第 3 节与 ui-standards.md 第 4 节是页面重写时真正要照的部分。

## 1. 统计基线

| 指标 | 实测 | 说明 |
|---|---:|---|
| 产品 tsx | 51 | pages 13、components 34、App/main 2、lib 2 |
| 测试 tsx | 19 | __tests__ |
| tsx 总行数 | 15431 | 产品文件 |
| components/ui 文件 | 12 | 13 个公开入口 |
| 原生 button | 64 处 / 24 文件 | 不含 Button 基元调用 |
| 原生 select | 19 处 / 11 文件 | 全部应换 select 基元 |
| 原生 table | 9 处 / 8 文件 | 全部应换 table 基元 |
| 原生 input | 20 处 / 13 文件 | 其余走 Input 基元 |
| 原生 textarea | 6 处 / 4 文件 | 无 Textarea 基元 |
| Modal 使用点 | 15 处 / 9 文件 | 无 ConfirmDialog |
| title= | 115 处 / 27 文件 | 事实上的 tooltip |
| aria-* | 517 处 / 42 文件 | 但 aria-controls 仅 5 处 |
| role= | 41 处 / 20 文件 | 自建语义为主 |
| 任意字号 text-[ | 691 处 / 44 文件（含测试，历史快照） | 产品代码仍占多数；2026-11-14 `cd web && node scripts/lint-ui.mjs --json`（不含测试）实测 660 处 / 42 文件（2026-11-14 20:5x，`filesScanned=83`）；9847b66 收尾后 `filesScanned=81`、**0 error / 19 warning**（字号 10 + 盒尺寸 4 + `dark:` 5，全为 vendored 例外，应用层 0），权威口径见 ADR-064「清单计数口径」 |

## 2. 控件清单

### 2.1 操作类

| 模式 | 出现位置 | 当前实现 | 问题 | 覆盖 |
|---|---|---|---|---|
| 主/次/轮廓/幽灵/危险/链接按钮 | 所有页面与弹窗；基元 button.tsx:6 | cva 6 variant × 5 size | 页面用 className 覆盖 h-7/h-8、text-[12px]；图标按钮尺寸与 32/36/40 阶梯不一致 | 完整 |
| 图标按钮 | topbar.tsx:77、86、132；sidebar.tsx:44；app-shell.tsx:52；keys.tsx:238-292；channel-detail.tsx:534 | 多数是原生 button，少数 Button size=icon | 尺寸 h-6/h-7/h-8/h-9 并存；部分只有 title 没有 aria-label | 部分 |
| 链接式按钮 | channels.tsx:235、328；channel-detail.tsx:524、567；catalog.tsx:159；overview.tsx:417、428、439、454 | Link + buttonVariants | 外观依赖调用方传 variant；没有统一 LinkButton | 部分 |
| 文本链接 | logs.tsx:950；dashboard.tsx:939-940；overview.tsx:311、348；settings.tsx:228 | 原生 Link 或 Button variant=link | 没有统一下划线/焦点样式；部分链接无 aria-label 说明去向 | 无 |
| 加载/禁用按钮 | channel-detail.tsx:626、638；keys.tsx:361；login.tsx:231；channel-batch-toolbar.tsx:41 | Button disabled + Loader2 手写 | 无 loading 属性；各页 spinner 尺寸/位置不一致 | 部分 |
| 分段选择按钮组 | catalog.tsx:118；dashboard.tsx:784、976、994 | 原生 button + 激活态 className | catalog 没有 aria-pressed；dashboard 三组各自手写容器和高度 | 无 |
| 批量操作条 | channel-batch-toolbar.tsx:27 | 自建 Card 条 + Button sm | 固定 h-7 text-[12px]，与普通按钮阶梯不一致 | 部分 |
| 分页 | pagination.tsx:97；logs.tsx:1225 | 受控 Pagination | 每页行数仍用原生 select（pagination.tsx:108），应随 select 基元替换 | 完整 |
| 复制按钮 | catalog.tsx:217；keys.tsx:238、459；overview.tsx:281、295 | navigator.clipboard + 本地 copied 状态 | 5 处重复实现；无 CopyButton；成功反馈有的是图标、有的是 toast | 无 |

### 2.2 表单输入

| 模式 | 出现位置 | 当前实现 | 问题 | 覆盖 |
|---|---|---|---|---|
| 单行文本输入 | Input 基元；channel-form-dialog.tsx:159、179、210、248；channel-detail.tsx:701、712、734、799；keys.tsx:338、374；login.tsx:182、201 | Input h-10 | 页面大量传 h-9/h-8 覆盖；Input 无 size/error 变体 | 完整 |
| 搜索输入 | channels.tsx:249；logs.tsx:786；discover-models-dialog.tsx:340；catalog.tsx:145；combo-editor-dialog.tsx:886 | Input + 绝对定位 Search 图标 | 无 SearchInput 基元；catalog 搜索框没有 aria-label；h-9 手写 | 部分 |
| 数字输入 | channel-form-dialog.tsx:263；channel-model-form-dialog.tsx:382、394、406、421、447、459；channels.tsx:447；combo-editor-dialog.tsx:501、743 | Input type=number 或原生 input | 无 stepper/单位后缀；channels 行内 h-7；模型表单 6 个数字框各写 aria-label | 部分 |
| 密码输入 | login.tsx:201；settings.tsx:424、436、447 | Input type=password | 只有登录页有显隐按钮；设置页三个密码框无显隐、无强度提示 | 部分 |
| 多行文本 | channel-keys-panel.tsx:471；rewrite-rules-editor.tsx:724、787；channel-detail.tsx:978；playground.tsx:284、343 | 原生 textarea + 手写 className | 无 Textarea 基元；焦点环、resize、字数样式各写一遍 | 无 |
| 复选框 | channel-form-dialog.tsx:277；channel-model-form-dialog.tsx:492、517、607、668；discover-models-dialog.tsx:230；rewrite-rules-editor.tsx:1071、1082；combo-editor-dialog.tsx:478；channels.tsx:341、372；playground.tsx:362 | 原生 input type=checkbox | 无 Checkbox 基元；行内 checkbox 只有 h-3.5，表单里 h-4；无统一 label 关联 | 无 |
| 单选按钮 | channel-models-panel.tsx:372；settings.tsx:319 | 原生 input type=radio | 无 Radio 基元；settings 用 sr-only + 手写圆点，channel-models 用原生外观 | 无 |
| 开关 | enabled-toggle.tsx:10；channel-detail.tsx:602、993；channels 行内 | 自建 button role=switch | 不在 components/ui；无 loading/禁用态说明；尺寸固定 h-5 w-9 | 部分 |
| 原生下拉 | 19 处：channel-form-dialog:225；channel-model-form-dialog:358；rewrite-rules-editor:870、1000、1033；combo-editor-dialog:511、581；pagination:108；catalog:131；channel-detail:776；channels:257；dashboard:800、841、857、873；logs:827、913、923；playground:227 | 原生 select + 手写 h-8/h-9/h-10 | 各页高度、边框、focus ring 不一致；无 typeahead、无自定义选项渲染 | 无 |
| 自建搜索下拉 | search-select.tsx:131；logs.tsx:815 | button + input role=combobox + ul role=listbox | 选项是 button role=option，语义不标准；无 aria-activedescendant；弹层 absolute 定位、无 portal/焦点陷阱 | 部分 |
| 多选（通用） | channels.tsx:340、371；combo-editor-dialog.tsx:680 | 原生 checkbox 列表 + 按钮加入；无多选下拉 | 无 MultiSelect 基元；批量选择只服务渠道表；无“已选 N 项”摘要 | 无 |
| 时间范围 | time-range-picker.tsx:99；logs.tsx:801 | 自建 dialog + 预设 + datetime-local | 无日期选择器；预设按钮无 aria-pressed；无焦点陷阱/归还 | 部分 |
| 日期时间输入 | time-range-picker.tsx:164、173；dashboard.tsx:821、829 | 原生 input type=datetime-local 或手写 input | dashboard 未走 Input 基元；浏览器差异大，无统一占位/错误样式 | 无 |
| 标签 | label.tsx:1；各表单 | Label 基元 | 部分字段只用 placeholder 没有 Label；错误提示未与 aria-describedby 关联 | 完整 |
| 表单行/字段容器 | 各弹窗用 div + space-y；channel-form-dialog.tsx:185 起 | 手写 grid/space-y | 无 FormField/FormRow；标签、帮助文字、错误、必填标记的组合每页重复 | 无 |
| 校验错误 | channel-form-dialog.tsx:69；channel-model-form-dialog.tsx:148；channel-detail.tsx:281 | 三个同名 FieldError 手写红字 | 三份实现；均未设置 aria-describedby/aria-invalid；文案格式不统一 | 无 |
| 字数计数 | channel-detail.tsx:986 | 手写 p 右下角计数 | 仅一处；无统一 maxLength/计数组件 | 无 |

### 2.3 数据展示

| 模式 | 出现位置 | 当前实现 | 问题 | 覆盖 |
|---|---|---|---|---|
| 卡片 | card.tsx:1；各页 Card | Card + Header/Title/Description/Content/Footer | 页面常直接写 Card className=p-5，绕过 CardHeader/Content | 完整 |
| 指标条 | metric-pills.tsx:18；logs.tsx:771；dashboard 顶部 | MetricStrip 2/3/5 列 | 无 role 容器，只靠 aria-label；加载态用省略号而非骨架 | 完整 |
| 表格 | 9 处：channel-models-panel:347；rewrite-rules-editor:1151；log-detail-drawer:213；catalog:163；channels:336；combos:296、380；keys:189；logs:1027 | 原生 table + 手写 thead/tbody | 无排序（aria-sort 0 处）；空态/加载/错误各页重写；无 caption（catalog 有 caption） | 无 |
| 表格全选/行选择 | channels.tsx:340、371 | 原生 checkbox + 手写 allSelected | 无 indeterminate 状态；无 aria-checked=mixed；行点击不切换选择 | 无 |
| 可展开行 | logs.tsx:1067 | tr role=button tabIndex=0 + Enter/Space | 行内又有按钮，role=button 与嵌套按钮语义冲突；展开内容无 aria-controls | 无 |
| 表格滚动/粘性表头 | logs.tsx:1026-1027 | 外层 max-h + overflow，thead sticky | 其他表格没有统一滚动容器；列宽靠 min-w 手写 | 无 |
| 空态 | channels.tsx:304；combos.tsx:284；dashboard.tsx:935；keys.tsx 空态；catalog 空态 | 每页手写图标 + 标题 + 说明 + CTA | 无 EmptyState 基元；字号/间距/图标尺寸各不同 | 无 |
| 加载态 | 72 处 animate-pulse/Loader2；channels.tsx:299；channel-detail.tsx:514 | Loader2 + 文案 | 无统一 Loading；有些整页 spinner、有些按钮内 spinner、有些骨架 | 部分 |
| 骨架屏 | 无独立组件 | 仅 MetricStrip 的 animate-pulse 文本 | 无 Skeleton 基元；表格/卡片加载会跳动 | 无 |
| 错误态 | channels.tsx:285；channel-detail.tsx:531；logs 错误；各 query | Alert destructive + 重试 Button | 文案结构不统一；重试按钮位置有的右、有的下 | 部分 |
| 状态徽章 | connection-status-badge.tsx:21；model-health-badge.tsx；probe-status-badge.tsx | span + dot + text | 三份相似实现；无通用 Badge；颜色 tone 名称不统一 | 部分 |
| 通用计数徽章 | models.tsx:97；combos.tsx:207；channels.tsx:269 | span + rounded-badge | 无 Badge 基元；bg/文字色各写 | 无 |
| 图标徽章 | icon-badge.tsx:1 | IconBadge 8 tone × 3 size | 覆盖良好；页面仍偶有自写圆角图标底 | 完整 |
| 定义列表 | channel-detail.tsx:1009；log-detail-drawer.tsx:93 的 Field | dl/dt/dd 手写 | 无 DescriptionList 基元；字段名宽度/对齐不一致 | 无 |
| 普通列表 | overview.tsx:417；sidebar.tsx:60 起；combo-editor-dialog.tsx:680 | ul/li 手写 | 无 List 基元；分隔线/密度/空态各页不同 | 无 |
| 代码块 | overview.tsx:295 起；log-detail-drawer 的 pre | 手写 pre/code + 深色底 | 无 CodeBlock 基元；复制按钮独立实现；长行换行策略不一 | 无 |
| 头像 | topbar.tsx:142 | 首字母 span 圆形 | 不是 img；无 Avatar 基元；无在线状态 | 无 |
| 分隔线 | 全局用 border-b/border-t | 无组件 | 无 Divider；间距由各页面决定 | 无 |

### 2.4 导航与浮层

| 模式 | 出现位置 | 当前实现 | 问题 | 覆盖 |
|---|---|---|---|---|
| 侧栏导航 | sidebar.tsx:1；app-shell.tsx:31 | NavLink + 分组 + 折叠 | 折叠态依赖 sr-only + title；无 Tree/aria-current 统一封装 | 部分 |
| 面包屑 | topbar.tsx:100 | nav aria-label=面包屑 + Chevron | 只有当前页文本，不是可点击层级；无 Breadcrumb 基元 | 部分 |
| 标签页 | models.tsx:76；channel-detail.tsx:648；combo-editor-dialog.tsx:837 | role=tablist + useRovingTabs + button role=tab | 三处页面级拼装；无 Tabs 基元；tabpanel 内容只在 active 时渲染，hidden 冗余 | 部分 |
| 模态框 | modal.tsx:18；15 个使用点 | portal + Escape + backdrop + scroll lock | 无焦点陷阱、无初始焦点、无关闭归还、无 aria-describedby；footer 按钮在移动端全宽 | 完整 |
| 确认框 | channel-keys-panel.tsx:587、625；channels.tsx:555、598；keys.tsx:384、406；model-availability.tsx:126 | 复用 Modal + danger Button | 无 ConfirmDialog 基元；标题/说明/按钮文案各写；combos.tsx:200 例外用 window.confirm | 部分 |
| 抽屉 | log-detail-drawer.tsx:532 | createPortal + aside role=dialog | 无 Drawer 基元；无焦点陷阱/初始焦点/归还；宽度固定 | 无 |
| 下拉菜单 | theme-menu.tsx:67；topbar.tsx:153 | role=menu + role=menuitem/menuitemradio | 无方向键/roving/初始焦点；无 Dropdown 基元；outside click + Escape 各自实现 | 无 |
| 全局搜索 | global-search.tsx:90 | portal + role=dialog + input + 结果 button | 无 focus trap/归还；结果不是 listbox/option；⌘K 无提示 | 无 |
| 路由链浮层 | logs.tsx:163 | absolute div role=dialog | 无焦点管理；点击外部关闭由行级实现；不是 popover 基元 | 无 |
| 标题提示 tooltip | 115 处 title= / 27 文件；如 dashboard.tsx:385、477；channels.tsx:454 | 浏览器原生 title | 无延迟/无触屏/不可聚焦；关键说明只放在 title 里 | 无 |
| 移动导航 | app-shell.tsx:46 | portal + role=dialog + 遮罩 button | 无焦点陷阱/归还；Escape 仅 app-shell 处理 | 无 |
| 折叠面板 | log-detail-drawer.tsx:247；combo-editor-dialog.tsx:811 | button aria-expanded + aria-controls + 条件渲染 | 无 Collapsible/Accordion 基元；两处实现不同 | 无 |
| Toast | toast.tsx:98-132 | Provider + useToast + portal | 覆盖良好；错误 role=alert、其余 status；最多 4 条；无暂停计时 | 完整 |
| 告警条 | alert.tsx:7；各页 Alert | 4 variant + role=alert | 覆盖良好；部分错误用普通 p 而非 Alert | 完整 |
| 进度条 | 无 | 无 progress 元素/role=progressbar | 长任务只有 spinner，无百分比 | 无 |

### 2.5 数据可视化与特殊交互

| 模式 | 出现位置 | 当前实现 | 问题 | 覆盖 |
|---|---|---|---|---|
| 折线/柱状图 | dashboard.tsx:201 起 TrendChart | 手写 SVG + hover/focus tooltip | 无 chart 基元；无 role=img/数据表替代；窄屏图例另排 | 无 |
| 热力图 | dashboard.tsx:641 起 | 手写 grid + span title | 无键盘/无 role；颜色对比度未验证；tooltip 依赖 title | 无 |
| 图表 tooltip | dashboard.tsx:386、425 | 绝对定位 div + 鼠标/焦点 | 无 aria-live；内容变化不播报；边界翻转手写 | 无 |
| 图例开关 | dashboard.tsx:472 | button aria-pressed + 颜色块 | 32 个系列时不可折叠/搜索；键盘可 Tab 但无 roving | 无 |
| 拖拽排序 | combo-editor-dialog.tsx:717 | HTML5 draggable + onDragStart/Over/Drop | 仅鼠标；有上下移按钮兜底但不在同一焦点区；无 aria-grabbed | 无 |
| 自定义滚动容器 | index.css:246 .scrollbar-thin；logs.tsx:1026 | CSS 工具类 | 无 ScrollArea 基元；键盘滚动依赖浏览器 | 部分 |
| 页面标题区 | 各页 h1 + 描述 + 右侧操作 | 每页手写 flex + text-[22px] | 无 PageHeader；标题字号/间距/操作区位置有差异 | 无 |
| 焦点环 | index.css:172 :focus-visible | 全局 outline 2px + offset 2 | 统一；但自建菜单的 focus 不可见、部分原生 select 只写 ring 不写 outline | 完整 |
| 响应式横向滚动表格 | channels.tsx:335；logs.tsx:1026；combos.tsx:295 | overflow-x-auto + min-w | 无统一 Table 容器；移动端列被截断，无滚动提示 | 无 |

## 3. 交互规则现状

### 3.1 键盘可达性

- 原生 button/input/select/textarea 本身可 Tab；但自建浮层没有把焦点送进去：Modal、LogDetailDrawer、GlobalSearch 打开后焦点仍停留在触发按钮（GlobalSearch 例外，用 requestAnimationFrame 聚焦 input）。
- 自建菜单（theme-menu.tsx:67、topbar.tsx:153）只有 Tab，没有 ArrowUp/Down、Home/End、typeahead；role=menu 与键盘行为不匹配。
- 表格行用 role=button + tabIndex=0 支持 Enter/Space（logs.tsx:1067），但行内又嵌套按钮，Tab 会进入两个语义层级。
- 图表 rect 可聚焦（dashboard.tsx:386），热力图单元格不可聚焦。
- 拖拽仅鼠标；组合成员有上移/下移按钮作为替代路径，但没有 aria-label 说明当前顺序。
- Escape 覆盖：Modal、ThemeMenu、Topbar 用户菜单、GlobalSearch、app-shell 移动导航、TimeRangePicker、SearchSelect；LogDetailDrawer 也处理。覆盖较全，但没有统一 hook。

### 3.2 焦点管理

- 打开弹层时：只有 GlobalSearch 主动聚焦输入；Modal/Drawer/确认框都没有初始焦点。
- 关闭弹层后：没有一处把焦点还给触发元素；Keyboard 用户会掉回 body。
- 弹层内：没有焦点陷阱；Tab 可以走到背景页面。
- 菜单：打开后焦点不移动，因此 Arrow 键即使实现也无处开始。
- 建议在 ui-standards.md 第 3 节统一为 useFocusTrap + initialFocusRef + returnFocusRef。

### 3.3 aria 覆盖

- aria-label 160 处 / 33 文件，图标按钮大多有；aria-pressed 11 处 / 7 文件；aria-expanded 11 处 / 8 文件；aria-controls 5 处 / 5 文件；aria-selected 5 处 / 4 文件；aria-sort 0 处。
- 缺口：
  - catalog.tsx:118 分段筛选没有 aria-pressed。
  - dashboard 图表/热力图没有 role=img 或文字摘要。
  - global-search 结果是普通 button，没有 listbox/option；search-select 的 option 语义不标准。
  - 表格没有 caption/aria-label（catalog 有 caption，其余没有）。
  - 三个 FieldError 没有 aria-describedby / aria-invalid（login 有 aria-invalid/aria-describedby，是正面样本）。
  - 折叠面板有 aria-expanded/aria-controls（drawer:247），但 toast/alert 之外的动态区域没有 aria-live。

### 3.4 鼠标以外操作路径

- 菜单：只能 Tab + Enter，不能方向键；可以完成操作，但不符合 menu 模式。
- 拖拽：只能鼠标；有按钮兜底，但顺序调整按钮是图标按钮，需要 aria-label 和当前顺序播报。
- 图表：只能 hover/focus 单点，不能键盘遍历所有点；没有表格替代。
- 表格排序：当前没有排序，因此不存在“只能鼠标排序”的问题；新增排序时必须给 th 加 aria-sort。
- 复制：复制按钮可键盘触发；但成功反馈有的只换图标（keys.tsx:244），屏幕阅读器不一定感知。

## 4. 重复实现与漂移

| 重复项 | 份数 | 位置 | 风险 |
|---|---:|---|---|
| 自建弹层 | 4 | theme-menu、topbar、global-search、search-select、time-range-picker、route popover | outside click、Escape、定位、焦点各写一遍 |
| 确认框 | 15 个 Modal 使用点 + window.confirm | 见 2.4 | 标题/说明/危险色/按钮顺序不统一 |
| 标签页 | 3 | models、channel-detail、combo-editor | roving 逻辑靠 hook，结构与样式各写 |
| 状态徽章 | 3 | connection、model-health、probe | tone 与尺寸不统一 |
| 空态 | 5+ | channels、combos、keys、catalog、dashboard | 图标/标题/说明/CTA 组合不一致 |
| 校验错误 | 3 | channel-form、channel-model-form、channel-detail | 三份 FieldError；无 aria 关联 |
| 分段筛选 | 3 | catalog、dashboard 指标、dashboard 维度 | aria 与高度不一致 |
| 下拉 | 19 原生 + 1 自建 | 见 2.2 | 外观与键盘行为不一致 |
| 表格 | 9 | 见 2.3 | 列宽/滚动/空态/加载各写 |
| 复制 | 5 | catalog、keys、overview | 成功反馈不一致 |

## 5. 未覆盖缺口汇总

### 5.1 计划内 5 个基元（与 ADR-062 一致）

| 缺口 | 现状载体 | 数量 |
|---|---|---:|
| Select | 原生 select | 19 处 / 11 文件 |
| Dropdown/Menu | theme-menu、topbar | 2 处 |
| Tooltip | title= | 115 处 / 27 文件 |
| Tabs | models、channel-detail、combo | 3 处 |
| Table | 原生 table | 9 处 / 8 文件 |

### 5.2 计划外但同样阻断统一的缺口

| 缺口 | 现状载体 | 数量 |
|---|---|---:|
| Textarea | 原生 textarea | 6 处 / 4 文件 |
| Checkbox | 原生 input | 12 处 |
| Radio | 原生 input | 2 处 |
| Switch | EnabledToggle（不在 ui） | 1 个组件 / 多处 |
| SearchInput | Input + 图标 | 5 处 |
| NumberInput | 原生/Input number | 7 处 |
| Field/FormRow | div + space-y | 所有表单 |
| FieldError | 3 份同名函数 | 3 处 |
| ConfirmDialog | Modal 复用 | 15 处 |
| Drawer | log-detail-drawer | 1 处 |
| Badge | 3 个状态徽章 + 计数徽章 | 5+ 处 |
| EmptyState | 每页手写 | 5+ 处 |
| Skeleton | 仅 MetricStrip pulse | 0 个基元 |
| SearchSelect | 自建，需补 aria-activedescendant | 1 个组件 |
| TimeRangePicker | 自建，需补预设 aria-pressed/焦点 | 1 个组件 |
| CopyButton | 5 处手写 | 5 处 |
| CodeBlock | overview/drawer 手写 | 2+ 处 |
| PageHeader | 每页手写 | 13 页 |
| ChartContainer | dashboard 手写 SVG | 2 类图 |
| SegmentedControl | catalog/dashboard 手写 | 3 处 |
| ScrollArea | .scrollbar-thin + overflow | 3+ 处 |

## 6. 建议的收敛顺序（不改变选型）

1. 先补 Select + Table + Tabs + Dropdown + Tooltip 五个基元，替换 19 + 9 + 3 + 2 + 115 处；这一步收益最大。
2. 同批补 Field/FormRow + FieldError + Textarea + Checkbox/Radio/Switch；否则表单页仍会手写。
3. 补 ConfirmDialog + EmptyState + Skeleton + CopyButton；把 15 个 Modal 确认点收敛。
4. 最后处理图表、拖拽、焦点陷阱与 /dev/ui 画廊；这些是页面级 pattern，换库也省不掉。
