# UI 采用映射表（42 个基元 × 调用点）

> 台账文档，不承载决策。决策见 [ADR-064](adr/ADR-064-full-shadcn-migration.md)；视觉契约与视觉语言见 [ui-standards.md](ui-standards.md)（**唯一规范正文**）；68 模式级台账已归档为 [archive/ui-migration-map.md](archive/ui-migration-map.md)（历史，不再更新）。**本文是唯一现状台账**：§9 门禁阶梯、§10 门禁真实运行环境、§11 待补 token 与机制都在本文。
> **读法**：正文里出现的 `ui-migration-map §N`、「ui-migration-map 行 47」等一律指归档件 `archive/ui-migration-map.md` 的同名节（批 0–4 的原始分期台账）；现状与终态以本文为准。
> 基线：HEAD 提交 23a2c9a（feat(web): adopt Tailwind 4 and vendor the shadcn component set，2026-11-14）。导出面逐文件实读 `web/src/components/ui/*.tsx` 校验；调用点来自归档台账 `archive/ui-migration-map.md` §4 并用本次 grep 复核（行号以实测为准）。
> **路径约定**：本文 `ui/x.tsx` = `web/src/components/ui/x.tsx`，`pages/x.tsx` = `web/src/pages/x.tsx`，`components/…` 同理省略 `web/src/` 前缀。
> **只读声明**：本轮未改任何 src 文件；本文件是任务交付物。

## 0. 口径

42 个 tsx 分三类，合计 27 + 11 + 4 = 42（9847b66 终态）：

- **A. 27 个 vendored**：23a2c9a 从 shadcn new-york **v3** 源码 copy-in，本仓只改 token 类与尺寸类（ADR-063 约束 5 / ui-standards §3.0）。
- **B. 11 个并入 shadcn 的旧基元**：button / input 已在 23a2c9a 改写为 shadcn 实现（保留 public API）；badge / card / alert / label / pagination / skeleton / spinner / empty 由自建件换成 shadcn 源码或等价实现；批 2 新增的 `confirm-dialog`（本仓自有，Radix `alert-dialog` + `button` 组合，替代 `window.confirm`）也归此类。
- **C. 4 个自建件**：`combobox`（本地组合件：Radix Popover + Command，registry 不发 combobox）、`time-range-picker`（Calendar + 小时 wrapper，重写完成）、`icon-badge` / `metric-pills`（无对应件，永久保留）。`modal` / `search-select` / `toast` 已于 9847b66 删除。

列含义：**导出面**＝该文件真实 export（含类型与变体键）；**调用点**＝现存使用处 file:line；**必须的调用点改动**＝该组件落地时业务文件必须做的修改；**批**＝ui-migration-map.md §4 的批次（批 1 已完成于此 commit，本表只把 2/3/4 列为待办）；**风险**＝低（纯外观/新增未接线）、中（交互或表单语义变化）、高（焦点陷阱/键盘/移动端行为/不可逆操作）。

> **行号会漂移，先 grep 再落行号**：本表 `file:line` 只是**某次实读的快照**，不是稳定锚点。批 2/3 期间页面一天内多次移动——`pages/dashboard.tsx` 的 4 个 `<Select>` 先记在 800/841/857/873，实测又在 816/840/865/890 一带（批 3 还在改）；两个手写分段控件原记 784/976/994，现已换成 `<ToggleGroup>` 的 993/1014。**引用前先跑** `rg -n '<Select\b' src/pages/dashboard.tsx` / `rg -n '<ToggleGroup\b' src`，落行号只用当次命令输出；表格里「调用点数」比行号稳定，行号只用于抽查。

## 1. A：27 个 vendored 组件

| # | 组件（行数） | 导出面 | 调用点 | 必须的调用点改动 | 批 | 风险 |
|---|---|---|---|---|---|---|
| 1 | ui/alert-dialog.tsx（141） | AlertDialog、AlertDialogTrigger、AlertDialogPortal、AlertDialogOverlay、AlertDialogContent、AlertDialogHeader、AlertDialogFooter、AlertDialogTitle、AlertDialogDescription、AlertDialogAction、AlertDialogCancel | 确认类＝15 处 Modal 中的确认弹窗（台账只列代表：pages/channels.tsx:555,598、pages/keys.tsx:385,407、components/channels/channel-keys-panel.tsx:587,625、components/models/model-availability.tsx:126）＋ pages/combos.tsx:200 window.confirm；15 处 Modal 全量见第 8 行 | 每个确认点由 Modal+按钮改为 AlertDialogAction/Cancel；异步确认期间禁用 Action 且不自动关闭；删除类保留下一步的代码不变 | 3 | 高（不可逆操作、焦点归还、Esc/点遮罩语义变化） |
| 2 | ui/avatar.tsx（50） | Avatar、AvatarImage、AvatarFallback | components/layout/topbar.tsx:142（台账口径；本次 grep 未命中 Avatar*，说明尚未接线） | 内联头像容器改 Avatar，缺图走 Fallback 首字母 | 1 ✅ | 低 |
| 3 | ui/breadcrumb.tsx（115） | Breadcrumb、BreadcrumbList、BreadcrumbItem、BreadcrumbLink、BreadcrumbPage、BreadcrumbSeparator、BreadcrumbEllipsis | components/layout/topbar.tsx:100 | 内联层级 div 改 Breadcrumb*，末级用 BreadcrumbPage | 4 | 低 |
| 4 | ui/calendar.tsx（213） | Calendar、CalendarDayButton | 暂无调用（t4 重写 time-range-picker 时接入） | 用 react-day-picker v10 的 mode/selected/onSelect；小时粒度必须外包一层（§4.3） | 3 | 中（粒度缺口见 §4.3） |
| 5 | ui/checkbox.tsx（30） | Checkbox | 12 处原生 input[type=checkbox]：components/combos/combo-editor-dialog.tsx:478、pages/channels.tsx:341,372、components/channels/channel-form-dialog.tsx:277、components/channels/rewrite-rules-editor.tsx:1071,1082、components/channels/discover-models-dialog.tsx:230、pages/playground.tsx:362、components/channels/channel-model-form-dialog.tsx:492,517,607,668 | input 改 Checkbox（checked/onCheckedChange），标签保持 Label htmlFor；react-hook-form 走 Controller | 2 | 中（受控语义、表单值类型 boolean） |
| 6 | ui/collapsible.tsx（11） | Collapsible、CollapsibleTrigger、CollapsibleContent | components/logs/log-detail-drawer.tsx:247、components/combos/combo-editor-dialog.tsx:811 | 手写 open state + 条件渲染改 Collapsible，Trigger asChild 复用原按钮 | 3 | 低 |
| 7 | ui/command.tsx（153） | Command、CommandDialog、CommandInput、CommandList、CommandEmpty、CommandGroup、CommandItem、CommandShortcut、CommandSeparator | components/layout/global-search.tsx:90 | 自写搜索面板改 CommandDialog；过滤交给 cmdk（fuzzy 排序与高亮行为会变） | 3 | 中 |
| 8 | ui/dialog.tsx（122） | Dialog、DialogTrigger、DialogPortal、DialogOverlay、DialogClose、DialogContent、DialogHeader、DialogFooter、DialogTitle、DialogDescription | 15 处 Modal（9 个文件）：pages/channels.tsx:555,598、pages/keys.tsx:306,355,385,407,436、components/channels/channel-form-dialog.tsx:131、components/channels/channel-model-form-dialog.tsx:307、components/channels/channel-keys-panel.tsx:587,625、components/channels/channel-models-panel.tsx:522、components/channels/discover-models-dialog.tsx:254、components/combos/combo-editor-dialog.tsx:422、components/models/model-availability.tsx:126 | Modal 改 Dialog；长表单补 max-h + 内部滚动；关闭按钮用 DialogClose；移除传 className 覆盖定位的写法 | 3 | 高（焦点陷阱、滚动锁、初始焦点） |
| 9 | ui/dropdown-menu.tsx（201） | DropdownMenu、DropdownMenuTrigger、DropdownMenuContent、DropdownMenuItem、DropdownMenuCheckboxItem、DropdownMenuRadioItem、DropdownMenuRadioGroup、DropdownMenuLabel、DropdownMenuSeparator、DropdownMenuShortcut、DropdownMenuGroup、DropdownMenuPortal、DropdownMenuSub、DropdownMenuSubContent、DropdownMenuSubTrigger | components/layout/theme-menu.tsx:67、components/layout/topbar.tsx:153 | 手写弹层改 DropdownMenu；onClick 改 onSelect；键盘与 aria 交给 Radix | 3 | 中 |
| 10 | ui/field.tsx（244） | Field、FieldLabel、FieldDescription、FieldError、FieldGroup、FieldLegend、FieldSeparator、FieldSet、FieldContent、FieldTitle | 暂无调用（表单改造时新建） | label+help+error 三件套改 Field* 组合，错误文案由 FieldError 接管 | 2 | 低 |
| 11 | ui/form.tsx（178） | Form、FormField、FormItem、FormLabel、FormControl、FormDescription、FormMessage、useFormField | 暂无调用（9 个表单文件逐步接） | FormField 的 render 接管 RHF Controller；先试点一个表单再铺开 | 2 | 中（改造面大） |
| 12 | ui/input-group.tsx（170） | InputGroup、InputGroupAddon、InputGroupButton、InputGroupText、InputGroupInput、InputGroupTextarea（addon align：inline-start/inline-end/block-start/block-end） | 暂无调用（搜索框、参数输入可复用） | 输入框+图标/前缀改 InputGroup，替代 relative+absolute 图标 | 2 | 低 |
| 13 | ui/popover.tsx（33） | Popover、PopoverTrigger、PopoverContent、PopoverAnchor | pages/logs.tsx:163 | 自写浮层改 Popover；回归 Portal 与 z-index | 3 | 中 |
| 14 | ui/progress.tsx（28） | Progress | 暂无调用 | 进度条场景直接接 value | 4 | 低 |
| 15 | ui/radio-group.tsx（44） | RadioGroup、RadioGroupItem | pages/settings.tsx:319、components/channels/channel-models-panel.tsx:372 | input[type=radio] 改 RadioGroup+Item，配 Label；RHF 走 Controller | 2 | 中 |
| 16 | ui/scroll-area.tsx（48） | ScrollArea、ScrollBar | pages/logs.tsx:1026（另有 .scrollbar-thin 自建类，定义在 src/index.css:234） | 原生滚动容器改 ScrollArea；.scrollbar-thin 保留作兜底，不删 | 4 | 低（滚动容器变更要回归横向滚动与虚拟列表） |
| 17 | ui/select.tsx（159） | Select、SelectGroup、SelectValue、SelectTrigger、SelectContent、SelectLabel、SelectItem、SelectSeparator、SelectScrollUpButton、SelectScrollDownButton | 19 处原生 select（批 2/3 已消化 15 处；**2026-11-14 21:2x 复测仅剩 4 处**：components/ui/pagination.tsx:110、components/channels/rewrite-rules-editor.tsx:870,1000,1033。已迁移：pages/dashboard.tsx 4 处改 Radix Select（`rg -n '<Select\b' src/pages/dashboard.tsx` 当次 4 处，行号随批 3 漂移）、logs/catalog/channels/playground/channel-detail/combos/channel-form-dialog/channel-model-form-dialog） | select+option 改 Select+Trigger/Content/Item；onChange 改 onValueChange（string）；空值用 undefined 而不是 ""；表单里接 Controller | 2 | 高（19 处、移动端系统 picker 行为丢失、空值语义） |
| 18 | ui/separator.tsx（31） | Separator | 暂无调用 | hr / border 分隔改 Separator（orientation/decorative） | 1 ✅ | 低 |
| 19 | ui/sheet.tsx（140） | Sheet、SheetPortal、SheetOverlay、SheetTrigger、SheetClose、SheetContent、SheetHeader、SheetFooter、SheetTitle、SheetDescription（side：top/right/bottom/left） | components/logs/log-detail-drawer.tsx:532、components/layout/app-shell.tsx:46 | 抽屉与移动导航改 Sheet（side=right/left）；保持受控 open | 3 | 中（移动端导航、滚动锁） |
| 20 | ui/sonner.tsx（37） | Toaster | toast 调用 6 个文件 11 处：components/channels/channel-keys-panel.tsx:66、components/channels/channel-models-panel.tsx:103、pages/overview.tsx:92、pages/channels.tsx:91、pages/channel-detail.tsx:292、pages/catalog.tsx:43 | useToast/ToastProvider 改 sonner 的 toast()；Toaster 挂 App 根；删除 ui/toast.tsx | 3 | 中（调用点分散，视觉/时长/暂停行为变化） |
| 21 | ui/switch.tsx（29） | Switch | 暂无调用（enabled-toggle 仍是原生 input，按 ui-migration-map 批 2 处理） | input[type=checkbox] role=switch 改 Switch（checked/onCheckedChange） | 2 | 中（受控语义） |
| 22 | ui/table.tsx（120） | Table、TableHeader、TableBody、TableFooter、TableHead、TableRow、TableCell、TableCaption | 9 处原生 table：pages/catalog.tsx:163、pages/channels.tsx:336、pages/combos.tsx:296,380、pages/keys.tsx:189、pages/logs.tsx:1027、components/logs/log-detail-drawer.tsx:213、components/channels/channel-models-panel.tsx:347、components/channels/rewrite-rules-editor.tsx:1151 | 原生 table 改 Table 原子件；**不改语义**（role=button 行、aria-expanded、展开行仍需手写）；sticky 表头用 className 补 | 4 | 中 |
| 23 | ui/tabs.tsx（55） | Tabs、TabsList、TabsTrigger、TabsContent | 3 处（ui-migration-map 行 47）：pages/models.tsx:76、pages/channel-detail.tsx:649、components/combos/combo-editor-dialog.tsx:837（当前是自写分段控件，grep 无 Tabs 调用） | 分段控件改 Tabs（value/onValueChange + Content 分离），去掉自写键盘漫游 | 3 | 中（受控 value、键盘漫游） |
| 24 | ui/textarea.tsx（22） | Textarea | 6 处原生 textarea：pages/channel-detail.tsx:978、components/channels/channel-keys-panel.tsx:471、components/channels/rewrite-rules-editor.tsx:724,787、pages/playground.tsx:284,343 | textarea 改 Textarea，保留 rows/min-h | 2 | 低 |
| 25 | ui/toggle.tsx（45） | Toggle、toggleVariants（variant/size） | 暂无调用 | 单按钮开关（如密度切换）接 Toggle | 2 | 低 |
| 26 | ui/toggle-group.tsx（61） | ToggleGroup、ToggleGroupItem | 4 处分段控件**已改 ToggleGroup**（批 2/3 落地）：pages/catalog.tsx:153、pages/dashboard.tsx:993,1014、components/channels/channel-models-panel.tsx:263（行号以 `rg -n '<ToggleGroup\b' src` 当次输出为准；原手写位置 catalog.tsx:118、dashboard.tsx:784,976,994 已不存在） | 手写分段按钮组改 ToggleGroup type=single value/onValueChange | 2 | 中（受控值） |
| 27 | ui/tooltip.tsx（32） | Tooltip、TooltipTrigger、TooltipContent、TooltipProvider | title= 41 处（本次 grep；ui-migration-map 计 55），代表：pages/catalog.tsx:174,175、pages/channels.tsx:454,517,526,535,560,603、pages/keys.tsx:311,358,388,410、pages/logs.tsx:268,1048,1145,1176、pages/dashboard.tsx:1032,1033、pages/combos.tsx:121、components/combos/combo-editor-dialog.tsx:719、pages/overview.tsx:285,299 | title= 改 Tooltip+Trigger asChild+Content；Provider 挂根；触屏无 hover，保留 aria-label | 4 | 中（量大、触屏与 a11y 回归） |

## 2. B：11 个并入 shadcn 的旧基元

| # | 组件（行数） | 导出面 | 调用点 | 必须的调用点改动 | 批 | 风险 |
|---|---|---|---|---|---|---|
| 1 | ui/button.tsx（51） | Button、buttonVariants、ButtonProps；variant：default/secondary/outline/ghost/danger/link；size：default/md/sm/lg/icon | 全站使用（122 处 <Button，例：components/combos/combo-editor-dialog.tsx:432、pages/channels.tsx:235）；23a2c9a 已改写且 public API 不变 | 无强制改动；原生 <button> 尚有 64 处（ui-inventory.md），按 ui-migration-map 批 1 之后增量替换 | 1 ✅ | 低 |
| 2 | ui/card.tsx（52） | Card、CardHeader、CardTitle、CardDescription、CardContent、CardFooter | 全站容器（41 处 <Card，例：components/channels/channel-models-panel.tsx:239、pages/settings.tsx:224、pages/settings.tsx:280） | 无强制改动；删手写卡片里重复的 border/padding/shadow 类 | 1 ✅ | 低 |
| 3 | ui/alert.tsx（65） | Alert、AlertTitle、AlertDescription、AlertProps；variant：default/destructive/warning/success（自带图标） | 44 处 <Alert>（17 文件导入；例：components/combos/combo-editor-dialog.tsx:447、components/logs/log-detail-drawer.tsx:336、components/channels/channel-form-dialog.tsx:155、pages/channels.tsx:24） | 旧用法保留；variant 值对齐 shadcn 语义（危险色用 destructive 不是 danger） | 1 ✅ | 低（variant 取值映射） |
| 4 | ui/label.tsx（14） | Label、LabelProps | 45 处 <Label>（10 文件导入；例：components/combos/combo-editor-dialog.tsx:451、pages/channel-detail.tsx:700、pages/settings.tsx:24） | 无强制改动，保留 htmlFor / asChild | 1 ✅ | 低 |
| 5 | ui/badge.tsx（41） | Badge、badgeVariants、BadgeProps；variant：default/secondary/success/warning/danger/outline | 20 处：pages/models.tsx:65、pages/combos.tsx:261,343、pages/logs.tsx:334,335,1101,1102、pages/channels.tsx:438,477、pages/dashboard.tsx:121、pages/channel-detail.tsx:219,581,828,1014、components/channels/channel-models-panel.tsx:14 及其余业务徽章 | 业务 tone 映射到 variant（success/warning/danger）；rounded-badge 已由 badgeVariants 带，删除调用点自带的圆角 | 1 ✅ | 低（tone→variant 映射层要统一定义） |
| 6 | ui/skeleton.tsx（21） | Skeleton、SkeletonProps | 暂无调用 | loading 占位 div 改 Skeleton，删除手写 animate-pulse | 1 ✅ 待接线 | 低 |
| 7 | ui/spinner.tsx（41） | Spinner、spinnerVariants、SpinnerProps；size：sm/md/lg；label 默认「加载中」 | 暂无调用（现有内联 Loader2 / 自写 spinner） | Loader2 内联用法改 Spinner，补 label | 1 ✅ 待接线 | 低 |
| 8 | ui/empty.tsx（41；单组件，含 EmptyProps 与内部槽位） | Empty、EmptyProps | 5 处空态（ui-migration-map §4）：pages/channels.tsx:304、pages/combos.tsx:284、pages/dashboard.tsx:935、pages/keys.tsx、pages/catalog.tsx（grep 未命中 Empty 调用） | 手写空态 div 改 Empty，children 自填；样式走 token 不走页面内联 | 4 | 低 |
| 9 | ui/pagination.tsx（200） | Pagination、PaginationProps、paginationRange、PAGE_SIZE_OPTIONS、PaginationRangeItem | pages/logs.tsx:1225（唯一） | 自建 Pagination 已含页大小选择；换 shadcn 版时保留 PAGE_SIZE_OPTIONS 导出与 a11y 属性 | 1 ✅ | 低 |
| 10 | ui/input.tsx（23） | Input、InputProps | 61 处 <Input>（例：pages/channels.tsx:249、pages/login.tsx:182、pages/channel-detail.tsx:701、pages/keys.tsx:338）；另 20 处原生 input 待换（ui-inventory.md） | input 改 Input，类型与尺寸通过 className 补 | 1 ✅ | 低 |

| 11 | ui/confirm-dialog.tsx（61） | ConfirmDialog、其 props | 待接线（批 2 新增，替代 `window.confirm`；t2 已删 `combos.tsx` 的 confirm） | 危险操作统一走它：标题是问句、正文写后果、取消在左 | 2 ✅ | 低 |

## 3. C：4 个自建件（9847b66 终态）

| # | 组件（行数） | 导出面 | 调用点 | 处置 | 批 | 风险 |
|---|---|---|---|---|---|---|
| 1 | ui/icon-badge.tsx（72） | IconBadge、IconTone | pages/combos.tsx:261,343、pages/dashboard.tsx:121、pages/models.tsx:65 | **保留自建**（无对应件，ADR-064 与 ui-migration-map §5 均列保留） | — | 低 |
| 2 | ui/metric-pills.tsx（79） | MetricStrip、MetricPillItem | pages/logs.tsx:771 | **保留自建**（同上） | — | 低 |
| 3 | ui/combobox.tsx（本地组合件） | Combobox、ComboboxOption | pages/logs.tsx 4 处（组合 / 状态 / 流式 / 尝试状态筛选） | **保留**（Popover + Command；替代已删的 search-select）；空值哨兵改用 `SELECT_ALL_VALUE` | 3 ✅ | 低 |

| 4 | ui/time-range-picker.tsx（255；重写完成） | TimeRangePicker | pages/logs.tsx、pages/dashboard.tsx | 已用 Calendar + 小时选择重写（§4.3 的 wrapper 约束仍成立） | 3 ✅ | 中（日期+小时粒度、快捷区间） |

**9847b66 删除清单**：`ui/modal.tsx`（77 行）、`ui/search-select.tsx`（243 行）、`ui/toast.tsx`（179 行）、`lib/use-body-scroll-lock.ts`（13 行）。toast 统一到 **sonner**：`App.tsx:94` 挂单个 `<Toaster/>`，`ui/toast.tsx` 的 `ToastProvider`/`useToast` 全部移除，4 个测试改为挂 `Toaster` + `toast.dismiss()` 隔离。**哨兵统一**到 `src/lib/select-sentinels.ts`（`SELECT_ALL_VALUE="__all__"`、`SELECT_NONE_VALUE="__none__"`）；settings 的 `body_policy` 枚举（off/errors/all）是业务取值，不算哨兵。

## 4. registry 覆盖缺口（4 条硬约束）

### 4.1 没有 combobox 条目 → Popover + Command（用户已批准）

new-york registry 不发 combobox；本仓也不引 Downshift 一类新依赖。落地方式：新增 `ui/combobox.tsx` 作为**本地组合件**（Popover + Command，Trigger asChild），替换 `ui/search-select.tsx` 的 4 个调用点（pages/logs.tsx:815/878/891/902）。它不进 vendored 冻结集，允许按需改结构，但仍受 token/尺寸类规则约束。验收：可搜索、可清空、上下键+Enter 选中、Esc 关闭、aria-expanded 正确。

**落地与结论（2026-11-14）**：`ui/combobox.tsx` 已由 t6 写入并接到 `pages/logs.tsx:830/901/914/925`，`ui/search-select.tsx` 现**零引用**。**结论：删除 `ui/search-select.tsx`，保留 `ui/combobox.tsx`**——两条搜索选择路径并存会把键盘/可访问行为分叉（search-select 自己手写 listbox 与键盘处理，combobox 走 Radix Popover + Command），而 combobox 已覆盖 search-select 的全部 props（options/value/onChange/placeholder/searchPlaceholder/emptyText/loading/truncated/onSearch/className/ariaLabel/highlighted）。删除动作留给批 3 收尾，本轮只记账不动代码。**已知偏差**：`components/ui/combobox.tsx:139` 直接写字面量 `"__all__"` 当空值哨兵，应改用共享常量 `SELECT_ALL_VALUE`（见 ui-standards §3.4）。

### 4.2 registry 没有 native-select

vendored 的 select 只有 Radix Select 一种。Radix 在触屏设备上弹自绘列表，**不弹系统轮盘**；19 个原生 select 替换后移动端行为会变。若业务要求保留系统 picker，只能自写 styled `<select>` wrapper（如 `ui/native-select.tsx`，本地件、不进冻结集）。这是 ADR-063 已登记的未解风险之一，本轮不引入，先在 §1-17 的替换里接受行为变化。

### 4.3 calendar 无法做小时粒度（必须 wrapper）

`ui/calendar.tsx` 是 react-day-picker v10 的封装，**只有日期维度，没有时间 API**。TimeRangePicker 需要「起始日+小时：结束日+小时」。两条可行路径：

1. `Popover` + `Calendar mode="range"` + 两个小时 `Select`（00–23）组合，输出仍是 `{from,to}` ISO 字符串（推荐，视觉与 shadcn 一致）；
2. 保留原生 `<input type="datetime-local">` 作为移动端兜底。

不要尝试给 Calendar 加 time 补丁：v10 无此扩展点，改出来也无法随 registry 升级。

### 4.4 vendored 版本冻结规则（frozen v3）

27 件是 new-york **v3** 快照（23a2c9a）。规则来自 ADR-063 约束 5 与 ui-standards §3.0：**只允许改 token 类与尺寸类，不改结构**。重拉 registry 时若生成物出现 `from "cn"` 或 `from "radix-ui"`（v4 风格），即拒收——本仓用 `@/lib/utils` 的 cn 与 `@radix-ui/react-*` 单包。校验：

```bash
rg 'from "(cn|radix-ui)"' web/src/components/ui   # 期望 0 命中
rg 'from "@radix-ui/react-' web/src/components/ui  # 期望命中的都是单包
```

另：组件内若出现 `bg-background` / `text-muted-foreground` / `border-input` / `ring-ring` 一类类名，由批 0 的别名层解析（ui-migration-map §4 批 0），不是笔误，不要改回旧的 `bg-bg` 命名。

## 5. 批次收敛与验收

| 批 | 组件数 | 范围 | 收口判据 |
|---|---|---|---|
| 1 ✅ 23a2c9a | 11 | vendoring + button/input 改写 + 别名层 | `npm run build` 绿、现有页面无视觉回归 |
| 2 ✅ 9847b66 | 10 | 表单控件：select、checkbox、radio-group、switch、toggle、toggle-group、textarea、input-group、field、form | 原生 select **19 → 0**、原生 checkbox 12 → 0、原生 radio 2 → 0；`ui/search-select.tsx` 删除（0 引用），调用点改 `combobox`；哨兵统一到 `select-sentinels.ts` |
| 3 ✅ 9847b66 | 14 | 浮层与焦点：dialog、alert-dialog、sheet、dropdown-menu、command、popover、tabs、collapsible、sonner、calendar；删 modal / search-select / toast | `no-native-dialog` **1 → 0**（`pages/combos.tsx` 改 `confirm-dialog`）；`modal.tsx`（-77）、`toast.tsx`（-179）删除，`useToast`/`ToastProvider` 归零，toast 走 sonner；`time-range-picker` 用 Calendar 重写完成 |
| 4 ✅ 9847b66 | 6 | 表格与防护：table、empty、scroll-area、progress、breadcrumb、tooltip + skeleton/spinner 接线 | 原生 table **9 → 0**；任意字号/尺寸在应用层 **归零**（vendored 例外 10 + 4 走 relaxed 桶）；`title=` 41 → 27；`ui/tabs` 保留待调用点迁移 |

**已核出的偏差（写文档时发现，供后续对照）**：

- ui-migration-map.md 里 keys.tsx 的确认点写成 305/354/384/406/435，实测是 306/355/385/407/436（1 行偏移）；本表用实测值。
- button 的变体名是 `danger`（不是 shadcn 默认的 `destructive`），alert 用的是 `destructive`；两者不统一，映射 tone 时不要想当然。
- ui-migration-map 计 tabs 3 处，但当前没有任何 `<Tabs` 调用，3 处仍是自写分段控件；替换成本按「从零接入」估。
- Avatar / Skeleton / Spinner / Empty / Progress / Tooltip 在页面侧 0 命中：组件已就位，**调用点尚未迁移**，别按「已完成」记。
- `ui/empty.tsx` 只导出 `Empty` 一个组件（其余槽位在文件内部），与 shadcn 上游的多段导出不同，接调用点前先读源码。

## 6. 复核方法

本表所有导出面与调用点可用下面命令复现（在 `web` 下执行）：

```bash
# 导出面：逐文件 grep export
rg -n "export (interface|const|function|\{)" src/components/ui/*.tsx
# 调用点：原生控件与待接线件
rg -n "<Modal|type=\"checkbox\"|type=\"radio\"|<textarea|<select|title=\"|<table" src --glob "!src/components/ui/**"
rg -n "window.confirm|useToast\(|<SearchSelect|<TimeRangePicker|<MetricStrip|<IconBadge" src
# 冻结规则
rg -n 'from "(cn|radix-ui)"' src/components/ui
```

## 7. 事故与纪律（2026-11-14）

- **事故（2026-11-14 21:29）**：`web/scripts/sweep-arbitrary-sizes.mjs --apply` 在口令到达前被运行，写入 **31 个文件 / 587 处**；lead 裁定保留 **291 处**现态，其余 **53 处**已精确还原。根因两条：① 把「守卫测试」当成用户可见动作；② 归因报告被随后的 dry-run 覆盖（同名文件互相覆盖）。
- **纪律**：见 `ui-standards.md` §10「批处理脚本纪律」六条——写盘开关默认关、反向操作同权限、产物只追加不覆盖、写盘前自证前提、作用域保护显式、并发写域不叠加；评审按阻断项计。
- **脚本侧现状**：`sweep-arbitrary-sizes.mjs` 头部有 SAFETY 段；`--apply` 报告默认时间戳文件名；`.scratch/sweep-history.jsonl` 为 append-only 审计（首条 2026-11-14T13:34:48Z dry-run：filesScanned=109、candidates=264、applied=0）。
- **对本表的约束**：批处理脚本不得改 `components/ui/vendored` 与已冻结文件（§4.4）；写盘前的范围核对用 §6 的 rg 命令。

## 8. 终局状态（9847b66）

- **门禁**：`npm run lint:ui` = `node scripts/lint-ui.mjs --max-warnings 19`；实跑 `filesScanned=81`、**0 error / 19 warning**、`result: PASS`、exit 0；`tsc --noEmit` exit 0。
- **19 条 warning 全部是 vendored 例外**（应用层 0）：字号 10（metric-pills 4、alert 2、button 2、card 1、label 1）+ 盒尺寸 4（separator 2、command 1、textarea 1）+ `dark:` 5（input-group 4、field 1）。
- **机制**：`no-arbitrary-font-size` / `no-arbitrary-size` 在 `src/components/ui/` 下与 `no-dark-prefix` 同走 relaxed 桶，脚本报 `count`（应用层）与 `relaxed.count`（例外）两列；因此「0 error / 19 warning」= 应用层干净 + 例外单列，`--max-warnings 19` 就是例外数，新增一条应用层违例即失败。
- **文件数会变，别引用绝对数**：下面这条「81」是 9847b66 的当次快照；`filesScanned` 随每次提交变（2026-11-15 03:5x 实跑已是 **82**——批 5 正在把 `pages/catalog.tsx` 拆成 `components/catalog/`）。**可断言的口径是 error=0 与 warning=19（例外数）**，文件数只作当次记录，引用一律取 `--json` 当次输出。
- **基线变化**：83 文件 / 834 warning → **81 文件 / 19 warning**（少的两个文件是删掉的 `ui/search-select.tsx` 与 `ui/modal.tsx`）。`components/ui/` 现有 **42 个** tsx（A 27 + B 11 + C 4）。
- **组件面收口**：批 2/3/4 全部落地；`modal` / `search-select` / `toast` 及 `lib/use-body-scroll-lock.ts` 已删除；toast 统一 sonner（`App.tsx:94` 挂单个 `<Toaster/>`）；空值哨兵统一到 `src/lib/select-sentinels.ts`；全仓无 `Loader2` 直接引用，统一走 `ui/spinner`（内部 `LoaderCircle`）。

## 9. 门禁阶梯（并入自 archive/ui-migration-map.md §7.6）

`node scripts/lint-ui.mjs` 的预算阶梯（写进 CI 的参数，跟着批次收紧；`--max-warnings` 从**当次实测**起算）：

阶梯的绝对值按**当次实测快照**写（当前：errors **0** / warnings **19** / files **81**，2026-11-14 21:5x / 9847b66 终态 —— 应用层归零，19 条全是 vendored 例外：字号 10 + 盒尺寸 4 + `dark:` 5，均走 relaxed 桶；21:0x 的 0 / 834 / 83 是历史快照）；**每批 PR 开始时先重跑 `npm run lint:ui -- --json`**，把预算锚到当次实测值，再按同一批的差值收紧（历史差值：批 2 −33、批 4 −796；总账 834 → 19），不要照抄绝对数。**地板 = 19**：`src/components/ui/**` 下的 vendored 例外（字号 10 + 盒尺寸 4 + `dark:` 5）按冻结契约**常驻为 warn**，不计入待清量；应用层任何一条新违例都会让 `--max-warnings 19` 直接失败。

| 阶段 | `--max-errors` | `--max-warnings` | 这一步清掉什么 |
|---|---:|---:|---|
| 批 1 完成 + t2 合并（21:0x 实测，files=83；**历史快照**） | **0** | **834** | 基线冻结：`window.confirm` 已由 t2 移除，error 级归零；warn = select 19 + table 9 + 字号 660 + 尺寸 100 + title 27 + checkbox 12 + radio 2 + vendored dark 5 |
| 批 2 后（历史） | 0 | **801** | select 19 + checkbox 12 + radio 2（834 − 33） |
| 批 3 后（历史） | 0 | **801** | 浮层/焦点迁移不改 warn 面（error 已为 0） |
| 批 4 后（历史，21:0x 冻结值口径） | 0 | **5** | table 9 + 任意字号 660 + 任意尺寸 100 + title 27（801 − 796 = 5） |
| **批 4 终态（9847b66，files=81）** | **0** | **19** | 应用层归零：select 19→0、table 9→0、字号 660→0（余 vendored 10）、尺寸 100→0（余 vendored 4）、title 41→27、checkbox 12→0、radio 2→0；**19 = 10 + 4 + 5，全部是 vendored 例外** |

| 地板（契约内常驻） | 0 | **19** | 字体 10 + 盒尺寸 4 + `dark:` 5 共 19 处 vendored 例外（字号 / 尺寸 / `dark:` 三条规则在 `src/components/ui/**` 下均走 relaxed 桶）—— 冻结契约：vendored 目录内 warn、应用代码内 error；这 19 处**不列入待清量**，除非 lead 改裁定 |

## 10. 门禁真实运行环境（并入自 archive/ui-migration-map.md §7.9，2026-11-14 22:3x 实测）

三条门禁（build / vitest / playwright）都能真跑，结论与坑一起记在这里，别再从零试错。

**构建与产物体积**：`npm run build`（= `tsc --noEmit && vite build`）`✓ built in 24.71s`；主要 chunk：index CSS 94.50 kB（gzip 16.49）、vendor-misc 299.14、vendor-react 222.28、app-components 182.75、app-pages 170.61、vendor-forms 94.01、vendor-router 39.14、vendor-query 35.61、vendor-icons 27.63、app-core 34.73、入口 js 2.63 —— 全部低于 esbuild 的 1 MiB 临时文件阈值（`vite.config.ts:15` 的 `chunkSizeGuard` 专门盯这条）。

**三条实测教训（改测试不是放水，是契约真的变了）：**

1. **jsdom 缺 Radix 依赖的浏览器 API**：首跑 4 个文件 12 条失败，与迁移逻辑无关；`src/test/setup.ts`（`setupFiles`）补了 4 类 —— `ResizeObserver` stub、`Element.prototype.hasPointerCapture/setPointerCapture/releasePointerCapture`、`Element.prototype.scrollIntoView`、永不匹配的 `window.matchMedia`（主题检测 `src/lib/theme.ts` 与 sonner 都会调它）。**缺任何一个，任何含 Select / Popover / Tooltip 的用例都会在首屏直接抛。**
2. **三处断言契约变化**：
   - 4 个筛选触发器 `getByRole("button", { name: "渠道筛选" })` → `getByRole("combobox", { name: … })`：触发器换成 Combobox（`ui/combobox.tsx:89-93`），role 是 combobox，名字仍来自 `ariaLabel`；
   - 行上 `aria-expanded` → `within(row).getByRole("button", { name: /展开行详情|收起行详情/ })` 再断言：`tr` 不允许 `aria-expanded`，属性挂在第一列按钮上（`pages/logs.tsx:1128-1129`），按钮名随状态切换；
   - 精确字符串 `getByText("共有 1 个密钥检测失败，2 个通过。")` → 正则 `/共有 1 个密钥检测失败，2 个通过。/`：`AlertDialogDescription` 把整句渲成一段，精确匹配依赖分词边界。
3. **端口纪律（踩过的坑，务必保留）**：视觉套件原指向 3680，而 **3680 是网关**（服务生产 `dist`）；Playwright 的 `reuseExistingServer` 于是直接复用网关，测的是上一次构建的产物 —— 症状是「源码改了、测试照旧失败」，极难定位。现在 `playwright.config.ts:20-22,34-39` 用 **3690** 且 `reuseExistingServer: false`，每次自己起 vite，测的才是当前源码。**3680 属于网关，视觉套件不得占用。**

**测试规模与视觉基线**：vitest `27 test files / 214 tests passed`；视觉 `7 passed`（连续两次）——先 `npm run test:visual:update` 重拍 7 张 PNG（`e2e/__screenshots__`），再 `npm run test:visual` 复跑确认 0 差异，阈值 `maxDiffPixelRatio: 0.002`（`playwright.config.ts:13`）。

## 11. 待补 token 与待补机制（并入自 design-system.md §11）

| # | 缺什么 | 用在哪（实测） | 建议值 / 写法 | 解锁 |
|---:|---|---|---|---|
| 1 | 字号档 token（批 4 已完成） | `text-[…px]` 657 处是**批 4 前历史快照**；批 4 完成后应用层 0 处，余 10 处 vendored 例外见 #16 | `@theme` 只加 4 档：`--text-2xs` 11/16、`--text-2sm` 13/20、`--text-metric` 19/26、`--text-h1` 22/28；**内置 `text-xs`/`text-sm` 不改**（12/16、14/20）。映射与判定句见 ADR-066「token 映射」第 1 节 | §11.2.1、§2.2 |
| 2 | 控件高度 token | `button.tsx:21-25`、`input.tsx:13`、`time-range-picker.tsx:96` | `--control-sm/md/lg/xl` = 32/36/40/44 | §2.3、§11.1.6 |
| 3 | 危险态前景色 | `button.tsx:17` | 新增 `--danger-on`，值复用 `--primary-on`（浅 `#ffffff` / 深 `#0b0f17`） | §11.4.2、§11.9.3 |
| 4 | 层级 token | `global-search.tsx:89`（`z-[60]`）、sonner 的 `Toaster`（原 `toast.tsx:132` 的 `z-[70]` 已随文件删除，层级改由 `Toaster` 承载） | `--z-search` 60、`--z-toast` 70 | §2.6、§11.5.4 |
| 5 | 第三档阴影 | `shadow-xs` 13 处（`button.tsx:12`、`17`、`input-group.tsx:17` 等；含 vendored 件的 v3→v4 迁移产物） | 登记 `--shadow-control`，或收敛为 `shadow-card` | §2.4、§11.5.2 |
| 6 | 枚举圆角 | `rounded-[2px]` 2 处（`dashboard.tsx:656`、`668`） | 登记 `--radius-cell` = 2px，或统一改 4px | §2.4、§11.5.1 |
| 7 | 名称 → 色槽稳定映射（机制，非 token） | `chart-colors.ts:27`、`dashboard.tsx:185` | 建 name → slot 表并在会话内保持，排序只影响展示顺序 | §11.4.3 |
| 8 | 图表轴字号（已由字号映射销账，不必单立 token） | `dashboard.tsx:364` 的 `text-[9.5px]` | 批 4 按映射句落到 `text-2xs`（11/16，+1.5px） | §11.7.1、§11.2.1 |
| 9 | 图表骨架（组件，非 token） | 图表加载态 | 用 `Skeleton` 拼装，并给图例留占位 | §11.7.3 |
| 10 | reduced-motion 全局兜底（规则，非 token） | 全站动画 | 在 `@layer base` 加 `@media (prefers-reduced-motion: reduce)` 兜底，保留组件级 `motion-reduce:` | §11.6.3 |
| 11 | 稀疏间距收敛（用法，非 token） | `py-3.5` / `px-3.5` 合计 33 处（22:0x 复测；`catalog.tsx` 9、`keys.tsx` 7、`discover-models-dialog.tsx` 4、`channel-form-dialog.tsx` 3、`alert.tsx:8`、`metric-pills.tsx:37`） | 改 12 或 16 | §11.3.2、§2.5 |
| 12 | 修正 token 注释里的错误引用 | `index.css:178` 写 `(ADR-026)`，但 16 槽色板出自 `ADR-040`（`ADR-026` 是看板与日志的信息架构） | 注释改为 ADR-040 | §11.4.3 |
| 13 | `text-muted` → `text-muted-foreground` 清扫（ADR-066 决策 1 的落地面） | 21:0x 实测：`text-muted` 已从 434 降到 57 处，`text-muted-foreground` 377 处；余量集中在 `components/ui/` | 残留（不含 `-foreground`）为 0，`tsc --noEmit` 为 0 | §11.4.1、§2.4 |
| 14 | 任意圆角（6 处，**不在批 4 范围**） | `rounded-[2px]` 2 处（`dashboard.tsx:656`、`668` 热力格）、`rounded-[7px]` 1 处（`dashboard.tsx:807`）、`rounded-[6px]` 3 处（`dashboard.tsx:953`、`971`、`channel-models-panel.tsx:267`） | 与热力图/图表骨架一起定；`lint-ui.mjs` 无圆角规则、不产生 warning，清扫脚本不得动 radius 类 | §2.4、§11.5.1 |
| 15 | 侧栏宽度 token（**已落地**） | `app-shell.tsx:56`、`sidebar.tsx:18`（折叠态 72 走 `w-18`） | `@theme` 内 `--width-sidebar: 248px`（`index.css:39-41`）→ 类名 `w-sidebar`。**命名空间**：Tailwind 4 没有 `--w-*`；能生成 `w-*` utility 的只有 `--width-*`（宽度）与 `--spacing-*`，且必须写在 `@theme` 内——写进 `:root` 只得到普通自定义属性，不生成类 | §11.5.3、ADR-066 §2 S3 |
| 16 | 终态 vendored 例外 **19 处 / 6 个件**（**不在清扫范围**，9847b66 实跑） | 字号 10：`metric-pills` 4、`alert` 2、`button` 2、`card` 1、`label` 1；盒尺寸 4：`separator` 2、`command` 1、`textarea` 1；`dark:` 5：`input-group` 4、`field` 1。应用层 0 处 | 冻结件（ADR-066 决策 3），本批不改；清扫脚本只能用 `--include-vendored` 才会碰它们，默认禁止 | §8、ADR-066「批 4 实测结果」 |


文档修正记录：2026-11-14 首版，基线 23a2c9a；与 ui-migration-map.md 的差异已在 §5 偏差清单列出，未回改那份台账（它属于另一条交付线）。