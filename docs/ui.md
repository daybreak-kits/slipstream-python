# UI 规范（生产留档 · 精简版）

> 适用 `web/src` 下所有用户可见界面（不含 lib 层与 docs 示意图）。
> 事实来源三处：`index.css` 的 CSS 变量与 `@theme` 映射（Tailwind 4 已无 `tailwind.config.js`）、`components/ui/` 的基元实现。页面不是事实来源（`h-7`/`text-[10.5px]` 等是技术债，不是配方）。冲突以代码与 ADR 为准，并当场改本文。

## 1. 技术栈与关键决策

| 层 | 选型（版本） | 决策理由 |
|---|---|---|
| 框架 | React 19.3.1 / Vite 7.3.8 / TS 5.9 | SPA 形态不动；换 antd 则 React 19 只能走 v6 |
| 样式 | Tailwind 4.3.5（`@tailwindcss/vite`） | ADR-064 把 80 行 JS 配置迁进 CSS-first `@theme`；v4 原生 `@layer` 亦是 antd 解法前提 |
| 控件层 | shadcn new-york **v3** copy-in：27 vendored + 11 并入 + 4 自建 = **42** | 不引运行时组件库、源码可改；Radix 自带键盘与 aria |
| 原语 | `@radix-ui/react-*` 单包 | 禁聚合包 `radix-ui`；重拉出现 `from "cn"`/`from "radix-ui"` 即拒收 |
| 表单 | react-hook-form 7.9 + zod 3.25 | shadcn form/field 接 Controller；antd Form 自带状态只能二选一 |
| 通知 | sonner 2.0，单个 `<Toaster/>`（`App.tsx:94`） | 取代自建 toast，禁第二套 |
| 组合件 | cmdk 1.1、react-day-picker 10 | 无 combobox 条目 → 本地 Popover+Command；calendar 无时间 API → 加 wrapper |
| 工具 | clsx + tailwind-merge（`cn()`）、cva 0.7 | vendored 件必须从 `@/lib/utils` 取 `cn` |
| **不选 antd** | — | ①官方承认的 Tailwind 优先级冲突（FAQ `#cssinjs-tailwind-conflict`、issue #38794），Tailwind 3 需升 4 或 `:where`+`!important`；②token 双轨：74 个 CSS 变量 vs JS 派生 Seed，`var()` 不可解析；③暗色 class 翻转 vs `darkAlgorithm`，首屏必闪；④首屏 JS 翻倍（现状 801KB）；⑤全量 25–40 人日、回滚难，图表色板与布局 CSS 仍删不掉 |
| **结论** | 继续 Tailwind + shadcn copy-in（6–9 人日） | 根因是「控件配方缺单一来源」，不是缺组件库；引 antd 只会多出第二个来源 |

## 2. 分层规范核心条款

### 2.1 单一来源

- 唯一控件来源 `web/src/components/ui/`；页面与 `components/<domain>/` 禁原生 `<select>`/`<table>`、`aria-haspopup`、`role="menu"`、手写弹层骨架。新增控件先查 42 件清单：shadcn 有就 copy-in + 换 token 类。
- **A 类 vendored 只改 token 类与尺寸类**，不动 DOM 结构、导出名、交互行为。唯一受控例外：受控打开（无 Trigger）时由基元归还焦点，实现在 `src/lib/dialog-opener-focus.tsx`，只挂 dialog/sheet/alert-dialog 的 Content。
- B 类保留原公开入口（导出名、`size`/`variant`、slot）；vendored 里的 `dark:` 属上游写法，不改源码，lint 在 `src/components/ui/**` 内只计 warning。

### 2.2 Token

- **颜色**：只写语义类名；出现 hex、`text-[Npx]`、`h-[Npx]`、`rounded-[Npx]` 即不合规；组件内禁 `dark:` 重复声明；`@theme inline` 必须带 `inline`（否则 `var()` 在定义处求值，深色无覆盖）。
- **命名语义**（ADR-066 决策 1）：`--color-muted` 是**弱表面**（`surface-2`），文字层用 `text-muted-foreground`；圆角两套标尺同值（badge 6 / control 8 / card 12 ≡ sm/md/lg，xl 16 只给浮层内嵌容器）。
- **字号**：11/16 `text-2xs` · 12/16 `text-xs` · 13/20 `text-2sm`（正文默认）· 14/20 `text-sm`；例外 19/26 `text-metric`（仅指标大数字）、22/28 `text-h1`（仅 PageHeader），**只在基元内部**，页面不写 19/22。字重仅 400/500/600；字距仅 normal / -0.01em（标题）/ 0.08em（全大写小标签，中文禁 `uppercase`）。
- **控件高度**：32/36/40（Button lg 44 仅登录与空态主 CTA）；dense 28 只给工具条与密集表格；同行相邻必须同高；图标按钮 32/36/40 正方形；图标 14/16/18，行内 12/14。
- **圆角阴影**：badge 6 / control 8 / card 12 / full；阴影只有 `shadow-card`、`shadow-pop`，禁自写 `box-shadow`，hover 不加阴影只改底色/边框。
- **间距**：4px 基数，允许 2/4/6/8/10/12/16/20/24/32，禁 5/7/9/11/13/14/15/18/28；页面 padding 移动 16 / 桌面 24，section 24，卡片 20 或 24，表单 gap 16、label 6、帮助 6，按钮组 8，表格 px 12/20 py 8/12、行高 ≥44；横向布局必须定窄屏退化（wrap / 降列 / `overflow-x-auto`），禁 min-w 撑屏。
- **z-index**：吸顶列 5 / sticky 10 / topbar 30 / popover 40 / modal 50 / toast 70；新增层级先改表（tooltip、搜索层待补 token）。
- **动效**：颜色·边框·背景 150ms，进入/离开 200ms ease-out，上限 300ms；只 `fade-up`/`fade-in`；新增动画必须注册进 `@theme`（`--animate-*` 与 keyframes 同在 `@theme`，放 `@layer utilities` 不生效）；必须尊重 `prefers-reduced-motion`。

### 2.3 交互与无障碍

- 一个视图一个主动作（每屏最多一个 primary，其余 outline/ghost/link 降级）；危险动作用 danger + 二次确认。
- 键盘一等公民：验收含 Tab 顺序、Enter/Space、Escape、方向键、焦点陷阱、关闭后焦点归还。
- 结构性容器必须 `role` + `aria-*` + 焦点管理：`role=dialog` 配陷阱与归还；`role=tablist` 配 roving tabindex，`aria-controls` 指向真实常驻的 `role=tabpanel`（未选中用 `hidden` 而非不渲染）；`title=` 不算 tooltip。
- 四态互斥：loading 与 empty 不同屏；空态区分「无数据」与「无匹配」（后者给清除筛选 CTA）；同一事件不同时 toast + Alert；错误只落一处。
- 危险操作只走 `ConfirmDialog`（标题问句、正文写不可逆后果、取消在左 danger 在右、文案是动作）；禁 `window.confirm`。
- Radix Select 空值哨兵：`SELECT_ALL_VALUE="__all__"` / `SELECT_NONE_VALUE="__none__"`，唯一来源 `src/lib/select-sentinels.ts`；页面禁字面量与自起名；`value` 永远非空。`body_policy`（off/errors/all）是业务取值，不套用。
- 格式化单一来源 `lib/format.ts`、`lib/time.tsx`；页面禁 `toFixed`/`toLocaleString` 包装；未配置单价显示「—」不渲染 `$0.00`（会被读成免费）；会纵向比较的数字用 `tabular-nums`；图表轴走 `formatTrendAxis`。
- 文案：按钮「动词 + 对象」，禁「确定/提交」，加载中保持动词只加 spinner；错误 = 发生了什么 + 为什么 + 怎么办（走 `describeApiError`），禁「请求失败」与堆栈；空态标题说状态、说明说原因、CTA 说下一步。

### 2.4 门禁

- `npm run lint:ui` = `node scripts/lint-ui.mjs --max-warnings 19`；基线 **0 error / 19 warning**（全是 vendored 例外：字号 10 + 盒尺寸 4 + `dark:` 5，走 relaxed 桶），应用层新增一条即失败；`filesScanned` 取 `--json` 当次输出。
- 十条清单第 1–5 条可 grep（select/table/`aria-haspopup`/任意值/hex 与 `dark:`）；第 3 条无 lint 规则，靠 `rg` 或 PowerShell + 人工。
- `/dev/ui` 画廊仅开发环境注册、不进侧栏、生产构建不含入口；4 张主快照（1440×900、390×844 × 浅/深），关动画、固定数据，阈值 `maxDiffPixelRatio 0.002`；改基元必须重拍并人眼过一遍。视觉套件用 **3690** 且 `reuseExistingServer: false`：**3680 属网关**（服务生产 `dist`），复用会测到上次构建的产物。

## 3. 组件采纳状态

| 类 | 件数 | 成员 | 语义 / 状态 |
|---|---:|---|---|
| A vendored（v3 冻结） | 27 | alert-dialog、avatar、breadcrumb、calendar、checkbox、collapsible、command、dialog、dropdown-menu、field、form、input-group、popover、progress、radio-group、scroll-area、select、separator、sheet、sonner、switch、table、tabs、textarea、toggle、toggle-group、tooltip | 只改 token/尺寸类；avatar、progress、tooltip 页面侧仍 0 调用（组件就位、调用点未迁） |
| B 并入 shadcn | 11 | button（default/secondary/outline/ghost/**danger**/link × size default/md/sm/lg/icon）、input、badge（6 tone）、card、alert（default/**destructive**/warning/success）、label、pagination（`PAGE_SIZE_OPTIONS`）、skeleton、spinner、empty、confirm-dialog | 保留原 public API；skeleton/spinner/empty 迁移前仍是「待接线」 |
| C 自建保留 | 4 | combobox（Popover + Command）、time-range-picker（Calendar + 小时 wrapper）、icon-badge、metric-pills | 无 registry 对应件，永久保留；combobox 空值改用 `SELECT_ALL_VALUE` |
| 已删除 | 4 | `ui/modal.tsx`(77)、`ui/search-select.tsx`(243)、`ui/toast.tsx`(179)、`lib/use-body-scroll-lock.ts` | toast 统一 sonner |

偏差：button 用 `danger`、alert 用 `destructive`（不统一）；`ui/tabs` 已就位但页面 3 处仍是自写分段控件；`ui/empty.tsx` 只导出 `Empty`。

registry 四条：①无 combobox → 本地 Popover+Command；②Radix Select 在触屏弹自绘列表、不弹系统轮盘，要系统 picker 只能自写本地 `ui/native-select.tsx`（ADR-063 登记风险）；③calendar 无时间 API，禁打 time 补丁；④vendored 冻结 v3，重拉校验 `rg 'from "(cn|radix-ui)"' src/components/ui` 期望 0 命中。

## 4. 设计系统要点

- **三层语义**：表面 `--bg`/`--surface`/`--surface-2`（`surface-2` 不当页面底）· 描边 `--border`/`--border-strong`（border 不画强调）· 文字 `--text-title/body/muted/faint`（faint 不承载正文）· 强调 `--primary`+`--primary-soft`+`--primary-on`（soft 不做大面积底）。
- **三选一判据**：选中/高亮/品牌 → `primary-soft`；次级容器/hover/未选中 → `surface-2`；可交互边界 → `border`（控件用 `border-strong`）。灰阶下前两者几乎同亮度，**选中态必须再加文字色或图标**。
- **状态色载体**：超过一行文字的面积用 soft 底 + text 色，一行内用纯文字色，只有主按钮允许饱和底 + on 色；危险按钮前景必须用 `--primary-on`（深色 `--danger` 白字约 2.7:1，低于 4.5:1）。
- **暗色**：class 策略（`.dark` 挂 `<html>`，`@custom-variant dark (&:where(.dark, .dark *))`）；每个颜色必须在 `:root` 与 `.dark` 各写一次，只写浅色 = 深色静默继承。不能只靠反转的五类：阴影在深色几乎失效（改靠 `border` 提亮）· soft 底改半透明 `rgba(…,.14)` · 图表 16 槽独立亮度集 · tooltip/code/登录页两主题同值 · 图片只在固定深色或品牌位。禁 `bg-white`/`text-black`；登录页不随主题切换是登记例外。
- **图表**：16 槽（ADR-040），只经 `chart-colors.ts` 的 `assignCategoryColors` 取色；永不新开第 17 色、0 请求不占槽、超 16 循环复用、隐藏系列不重排颜色。**已知缺陷 R3**：颜色按当期请求量降序分配，排名变化或掉到 0 会让颜色前移，拟改 name → slot 稳定映射。轴标签 ≥11px、只画水平网格（虚线 3 5）、柱图必须有零基线、图表要有文本摘要或数据表、`svg` 带 `role="img"`+`aria-label`。
- **动效只解释因果**：必须动 hover/focus 底色与边框、浮层进出、加载指示、toast 进入；禁数值补间、列表重排位移、首屏大入场、滚动联动、hover 抬升。

## 5. 关联文档指针

- `docs/ssot.md` §9「前端信息架构与视觉规范」：§9.1 路由与导航分组、§9.2 视觉系统、§9.3 UX v2 已落地要点。
- ADR（均在 `docs/adr/`）：**ADR-064** 全量迁 shadcn + Tailwind 4（取代 062/063）· 062 不引 antd、自建基元 · 063 缺口件取 registry 源码 · **066** token 语义与圆角标尺（命名/数值冲突以它为准）· 040 图表 16 槽色板（`index.css:178` 注释误引 026）· 049 格式化单一来源 · 053 时间筛选只预览、提交权归搜索 · 068 缓存命中率四口径 · 027 视觉系统 v2 · 026 看板/日志。
- 代码事实源：`web/src/index.css`、`web/src/components/ui/`、`web/src/lib/`、`web/scripts/lint-ui.mjs`、`web/e2e/__screenshots__`。

---

本文为 ui-standards/ui-adoption-map/ui-stack/design-system 四篇的提炼合并版（2026-11-16），原文见开发仓 docs/（scratch 外）。
