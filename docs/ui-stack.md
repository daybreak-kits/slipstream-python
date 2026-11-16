# 前端 UI 技术栈评估：要不要换 antd、换了要清理什么

> **时效说明**：本文写于 ADR-062/ADR-063 决策之前，正文里的「Tailwind 3.4」「`tailwind.config.js`（80 行）」「`postcss.config.js`」「12 个基元」都是**当时快照**。现行状态以 [ADR-064](./adr/ADR-064-full-shadcn-migration.md)（控件层全量 shadcn copy-in + Tailwind 4，`tailwind.config.js`/`postcss.config.js` 已删除、配置并入 `index.css` 的 `@theme`）与 [ADR-066](./adr/ADR-066-token-semantics-adoption.md)（token 语义与圆角标尺）为准；组件来源见 `ui-standards.md` §3.0。

结论（先看这 5 行）

1. **不能"直接换"**：仓库没有 antd，现在是 React 19 + Tailwind 3.4 + 自建基元（`web/package.json`），换 antd 不是加依赖，而是把一套主题引擎叠到已有 74 个 CSS 变量 + 1941 处 className 之上。
2. **冲突不是猜测**：antd 官方 FAQ 明确列了「CSS-in-JS 与 Tailwind 优先级冲突」条目，官方给出的解法是 `@layer` + Tailwind v3 需包一层 `tailwind-base`，而本仓 Tailwind 3.4 不输出原生层——换 antd 会连带一次 Tailwind 4 / 构建链改造。
3. **全量换的清理量是文件级的**：删 `index.css`（250 行、74+61 个变量、32 个图表槽位）、`tailwind.config.js`（80 行）、`postcss.config.js`、`components/ui/` 12 个文件、`lib/utils.ts` 的 `cn()` 与 3 个依赖，改写 74 个 tsx 里的 1941 处 className、660 处 `text-[Npx]`、19 个原生 `<select>`、9 个原生 `<table>`，重拍 7 张视觉快照。**但图表 16 色板（ADR-040）和页面布局骨架仍然要自己维护**，换完仍留一套自定义 CSS。
4. **用户痛点的根因不是缺组件库，而是缺"控件配方单一来源"**：12 个基元只覆盖了一半控件，Select / Dropdown / Tooltip / Tabs / Table 全靠页面手写或原生元素补，于是出现 `dashboard.tsx:800` 那个和旁边按钮长得不一样的刷新间隔控件。antd 会新增第二个来源，不会消掉第一个。
5. **建议 A：继续 Tailwind + 自建基元**，补 5 个缺失基元 + 字号/控件尺寸 token 阶梯 + 一条可执行的 lint 规则（禁止页面内写 `<select>`/`<table>`/`text-[Npx]`/`rounded-[Npx]`）。ADR 见 [`adr/ADR-062-ui-component-strategy.md`](./adr/ADR-062-ui-component-strategy.md)。

---

## 1. 现状盘点

### 1.1 规模与构成（**2026-11-14 批 4 前的快照；计数已过期**）

当时的构成：页面 13 个文件、组件 34 个（其中 `components/ui` 12 个）、工具库 25 个、单测 27 个，`web/src` 共 104 个文件。

**这些数字不要引用**：批 2/3/4 之后组件面已重排（`components/ui` 终态 **42 个 = A 27 + B 11 + C 4**），权威计数只有两处——[ui-adoption-map.md](./ui-adoption-map.md) §0 的分项，以及 `node scripts/lint-ui.mjs --json` 当次输出的 `filesScanned`。

### 1.2 自建基元（**历史快照：当时 12 个文件**）

当时那 12 件：`alert`、`button`、`card`、`icon-badge`、`input`、`label`、`metric-pills`、`modal`、`pagination`、`search-select`、`time-range-picker`、`toast`。

**现状**：`modal` / `search-select` / `toast` 已于 9847b66 删除（toast 统一 sonner），基元面扩到 42 个，逐件归属与调用点见 [ui-adoption-map.md](./ui-adoption-map.md) §1–§3。

**缺口（当时）**：Select、Dropdown/Menu、Tooltip、Tabs、Table、Checkbox/Switch、Field/FormRow、Empty/Skeleton —— **均已补齐**（应用层原生 `<select>` / `<table>` 各 0，手写弹层 0；见 [ui-adoption-map.md](./ui-adoption-map.md) §8）。当时的证据（保留作「为什么要换 shadcn」的论证）：

- 原生 `<select>` **19 处 / 11 个文件**（`channel-form-dialog`、`channel-model-form-dialog`、`rewrite-rules-editor`、`combo-editor-dialog`、`pagination`、`catalog`、`channel-detail`、`channels`、`dashboard`、`logs`、`playground`）；`dashboard.tsx` 一个文件就有 4 个（:800、:841、:857、:873）。
- 原生 `<table>` **9 处 / 8 个文件**（`channel-models-panel`、`rewrite-rules-editor`、`log-detail-drawer`、`catalog`、`channels`、`combos`、`keys`、`logs`）。
- 手写弹层 **4 处**（`theme-menu`、`topbar`、`search-select`、`time-range-picker`），各自重复实现 outside-click、Escape、`aria-haspopup`、焦点管理。
- 三处 tablist 已抽出 `lib/use-roving-tabs.ts`，但仍是页面级拼装，没有 Tabs 基元。

### 1.3 设计 token 定义处

> **语义以 [ADR-066](./adr/ADR-066-token-semantics-adoption.md) 为准**（muted = 弱表面 / `text-muted-foreground` = 文字层 / 圆角两套标尺同值并存）。本节只记「定义在哪」，不复述语义。

- **唯一事实源**：`web/src/index.css`。
  - `:root`（:5）**74 条**声明：底色/表面/边框/文字 9、主色 5、highlight 2、语义色 11、badge 12、浮层/代码/登录 17、focus-ring 1、`--chart-1..16`（:79-94）；
  - `.dark`（:97）**61 条**覆盖：60 条同名变量 + 深色图表 16 色（:154-169）；
  - `@layer base`（:172）：`* { border-color }`、body 背景/字体/14px、h1-h4、`::selection`、autofill、`:focus-visible`；
  - `html.dark`（:182）只切 `color-scheme`；`@layer utilities`（:234）3 个自定义工具类。
- **Tailwind 映射（ADR-064 前快照；`tailwind.config.js` 已在批 0 删除，映射改由 `index.css` 的 `@theme` 承担，见 ADR-066）**：原 `web/tailwind.config.js`（80 行）把上述变量映射成 `bg-*/text-*/border-*` 语义色名（:6-48），`borderRadius` 三档（:54：control 8px / card 12px / badge 6px），字体栈（:50），阴影、动画。
- **16 色图表色板**：`index.css:79-94`（浅色）与 `:154-169`（深色），配套 `web/src/lib/chart-colors.ts`；口径见 ADR-040（16 槽循环复用、取消"其他"聚合）。
- **暗色模式 = class 策略**：`index.css:6` 的 `@custom-variant dark (&:where(.dark, .dark *))`（原 `tailwind.config.js:3 darkMode: ["class"]` 的类策略，已随批 0 删除 config 后写进 CSS）；`lib/theme.ts:25-30 applyThemeMode()` 对 `documentElement` 切 `.dark`；`index.html:9-28` 用内联脚本在读 `localStorage["system.storage"].theme` + `prefers-color-scheme` 后再首屏上色，防闪烁。**没有用 media 查询做主题声明**，media 只在 `system` 模式下决定初值。

### 1.4 样式使用现状（**批 4 前快照，已被销账**）

> 下表是 2026-11-14 批 4 之前的实测，**已随批 4 销账**：应用层任意字号与盒尺寸均为 0 处，终态 0 error / 19 warning（19 全为 vendored 例外）。权威口径永远是当次 `node scripts/lint-ui.mjs --json`；终态与例外清单见 [ui-adoption-map.md](./ui-adoption-map.md) §8/§9。

| 指标 | 数量 | 命令（在 `web/` 下） |
|---|---|---|
| `className=` 出现次数 | **1941 次 / 47 个文件** | `rg -c "className=" src` |
| 任意像素字号 `text-[Npx]` | **660 处 / 42 个文件**（10/10.5/11/11.5/12/12.5/13/14 八档并存；只数 px；2026-11-14 实跑，同日 660–673 波动，权威以 `node scripts/lint-ui.mjs --json` 当次输出为准；**出现次数口径**：该规则每行恰 1 处，660 次 = 660 行（尺寸规则才是 100 次 / 94 行、28 个文件）） | `cd web && node scripts/lint-ui.mjs --json`（规则 `no-arbitrary-font-size`） |
| 内联控件尺寸 `h-8/9/10/11`、`rounded-md/lg` | **117 处** | `rg -c '\b(h-8|h-9|h-10|h-11|rounded-md|rounded-lg)\b' src` |
| 任意圆角 `rounded-[Npx]` | 7 处（`dashboard.tsx` 6、`channel-models-panel.tsx` 1） | `rg -c 'rounded-\[[0-9]+px\]' src` |
| 原始色板类（`text-gray-500` 等） | **0** | `rg -c '\b(bg|text|border|ring)-(white|gray|slate|red|blue|…)-[0-9]{2,3}\b' src` |
| tsx 内十六进制色值 | **0** | `rg '#[0-9a-fA-F]{3,8}' src -g '*.tsx' -c` |
| 从 `@/components/ui/*` 导入 | 107 处 / 32 个文件 | `rg -c 'from "@/components/ui/' src` |

**判读**：颜色纪律很好（0 处原生色板、0 处内联色值，全部走 token），**漂移集中在"尺寸与控件外形"**——字号八档、行高/高度各处手写、换行内联圆角。这正是用户举的"刷新间隔控件和旁边按钮长得不一样"的机制：`dashboard.tsx:783-796` 手写分段控件与 `:800-810` 原生 `<select>`（`h-8 rounded-[7px] text-[12px]`）在同一行并排，前者圆角 7px、后者字体 12px、无边框，和 `button.tsx:21-25` 的 32/36/40/44 阶梯与 `rounded-control` 对不上。

---

## 2. 冲突面（若引入 antd）

### 2.1 全局重置（preflight vs reset.css）

本仓 `index.css:1-3` 引入 `@tailwind base`，即 Tailwind preflight：`*{box-sizing;margin:0;padding:0;border:0 solid}`、`button{background:transparent;border:0}`、`h1-h6{font-size/weight:inherit}` 等。antd 的全局重置是**可选**的 `antd/dist/reset.css`（`unpkg.com/antd@6.7.0/dist/reset.css`，3,587 字节，2026-11-14 可取）。两者不必然打架：antd 组件样式是类选择器，权重高于 preflight 的元素选择器。**真正的冲突在反向**——见 2.2。

### 2.2 CSS 优先级：Tailwind 工具类压不住 antd（官方承认）

antd 5 起用 CSS-in-JS + `:where()` 降权，样式在运行时注入到 `<head>` 末尾。Tailwind 工具类是静态 CSS、在构建产物里先出现；同权重时**后出现的 antd 胜出**，于是 `<Button className="h-8">` 这类覆写会静默失效。

官方证据：antd FAQ 专设「CSS-in-JS css priority conflict with tailwindcss?」条目（<https://ant.design/docs/react/faq>，锚点 `#cssinjs-tailwind-conflict`，查阅 2026-11-14），指向 issue <https://github.com/ant-design/ant-design/issues/38794>（"New ant design version 5.0.0 is confilicting with tailwindcss"，2022-11-21 创建、状态 closed、71 条评论，2026-11-14 查询）。

官方解法在「CSS Compatible」文档（<https://ant.design/docs/react/compatible-style>，查阅 2026-11-14）：

- 给 antd 开 `<StyleProvider layer>`（antd ≥ 5.17.0），把 antd 样式包进 `@layer antd` 降权；
- 再按 Tailwind 版本排层序：**v4** 用 `@layer theme, base, antd, components, utilities;` + `@import 'tailwindcss';`；**v3** 用 `@layer tailwind-base, antd; @layer tailwind-base { @tailwind base; }` 再 `@tailwind components; @tailwind utilities;`；
- 若用 `reset.css` 或 v6 `zeroRuntime` 的 `antd.css`，必须显式 `@import url(...) layer(reset|antd);`。

**对本仓的硬约束**：我们用的是 `tailwindcss 3.4.19`（`web/package.json`），Tailwind 3 的 `@layer` 是构建期指令，产物里没有原生 CSS 级联层；原生层是 v4 的产出。也就是说，走官方配方要么升 Tailwind 4（80 行 JS 配置迁到 CSS-first `@theme`、`tailwind-merge`/插件生态跟着动），要么留在 v3 用 `:where`/`!important` 手工兜底。这一条在 antd 官方文档里没有"v3 免改造"的承诺，v3 那段是"先开 @layer 再排层"的前提，实际能否在 3.4 的 `@layer` 指令下成立，需要在工作树里做一次 spike（半天，未验证不下结论）。

### 2.3 主题 token 双轨：74 个 CSS 变量 vs antd Design Token

本项目主题 = `index.css` 的 CSS 变量 + `.dark` 覆盖 + Tailwind 语义色名，改一处变量全站生效，深色靠 class 翻转。antd 主题 = `ConfigProvider theme` 的 Seed/Map/Alias Token，派生色由 JS 用色板算法算出（`docs/react/customize-theme`，2026-11-14 查阅）。后果：

- 不能用 `colorPrimary: 'var(--primary)'` 直接接现有变量——Seed Token 参与 JS 颜色派生，收到 `var()` 无法解析；
- 74 个变量 × 两套主题要**在 TS 里再写一遍**（至少主色、语义色、圆角、字号、控件高度得对齐），`index.css` 与 `ConfigProvider` 成为两个真相源；
- 反之若删 `index.css`（选项 C），图表 16 色板、`--overlay/--tooltip-*/--code-*/--login-*` 这些"主题无关"的变量仍要被某个地方接管，而 antd 不提供图表组件（图表在 `@ant-design/charts` 独立包），这部分自定义 CSS 删不掉。

### 2.4 暗色模式：class 翻转 vs ConfigProvider algorithm

现在我们能"零闪烁"全靠 `index.html:9-28` 在 React 挂载前读 localStorage 上 `.dark`。antd 的暗色是 `theme.darkAlgorithm`（JS 状态），只能在 React 渲染后生效：

- 首屏 `<html class="dark">` 已就位，但 antd 组件先按浅色渲染再切 → 闪一下；
- 要避免就得把主题决策前移到服务端/内联脚本，并让 antd 在 CSS 变量模式下吃 CSS 变量（v6 默认 CSS 变量，但 seed 值仍在 JS 配置里），仍是两套开关；
- 现有 `useThemeMode()`（`lib/theme.ts:41-72`）要再喂一份给 `ConfigProvider`，两处状态必须同步，属于新增的一致性风险。

### 2.5 组件尺寸 / 圆角 / 字号 / 视觉语言

我们的规格写死在 ssot §9.2 与 `button.tsx:21-25`：控件 32/36/40/44，圆角 8/12/6，正文 14px，表格行高由 padding 驱动（表头 py-3、数据行 py-2，日志页更紧凑），动效 150–200ms。antd 默认 `controlHeight=32`、`borderRadius=6`、字号 14，可改 token 对齐大部分，但每个组件的内部 padding、图标尺寸、间距仍按 antd 的节奏；且我们有 7 张视觉快照（`e2e/__screenshots__/*.png`）作为基线，全量换意味着全部重拍并逐张确认"变好还是变坏"，这是评审成本而不是 0。

### 2.6 Bundle 与首屏（量级估算）

- 现状基线（仓库里已有构建产物）：`web/dist/assets/index-*.js` **801,505 B** 原始、`index-*.css` **44,790 B**（2026-11-14 读取）。
- antd 包体：`antd@6.7.0` 完整包 **1,446,397 B 原始 / 460,782 B gzip**，48 个运行时依赖（bundlephobia API `https://bundlephobia.com/api/size?package=antd@6.7.0`，2026-11-14 查询）；`antd@5.29.0` 为 1,469,306 B / 452,134 B gzip。主要依赖块：`rc-picker` 168KB、`rc-select` 84KB、`@ant-design/react-slick` 76KB、`rc-field-form` 75KB、`@ant-design/cssinjs` 68KB、`rc-menu` 58KB。
- 只取 Button/Input/Select/Table/Form/Modal/DatePicker/Tooltip/Dropdown 这一子集，按依赖块折算 **约 700–950KB 原始（250–300KB gzip）**，另加 dayjs 与 locale。当前 801KB 原始约 230–250KB gzip，**首屏 JS 大致翻倍**；v6 的 `zeroRuntime` 可以省掉运行时 CSS-in-JS，但要额外引入 `antd.css`（约数百 KB 原始，需按组件静态抽取）。
- 结论：对一个单机控制台不是致命问题，但和"性能已经调过一轮"的现状（`docs/PERFORMANCE_REPORT.md`）方向相反，属于要写进决策的负收益。

### 2.7 React 19 兼容性（查证结果，不猜）

- 本仓 **react / react-dom 19.3.1**（`web/package.json`）。
- **antd v6（当前 latest 6.7.0）**：`peerDependencies` 为 `react >= 18.0.0`、`react-dom >= 18.0.0`（`https://registry.npmjs.org/antd/6.7.0`，2026-11-14 查询）；官方迁移文档明确「v6 requires React >= 18」「You no longer need the `@ant-design/v5-patch-for-react-19` package to support React 19」（`https://github.com/ant-design/ant-design/blob/master/docs/react/migration-v6.en-US.md`，2026-11-14 查阅）。→ **v6 原生支持 React 19**。
- **antd v5**（5.29.0）：`peerDependencies` 只写 `react >= 16.9.0`，React 19 需补丁包 `@ant-design/v5-patch-for-react-19@1.0.3`（peer: `antd >= 5.22.6`、`react >= 19.0.0`，`https://registry.npmjs.org/@ant-design%2Fv5-patch-for-react-19/latest`，2026-11-14 查询）。v5 已是上一个大版本，今天新引入应从 v6 起。
- 推论：用户问的"antd v5 的 CSS-in-JS"话题，在 v6 仍默认成立（CSS-in-JS 默认、可选 `zeroRuntime`），但 v6 默认输出 CSS 变量、只支持现代浏览器（同上迁移文档）。

---

## 3. 三个选项的代价

### A. 不换：Tailwind + 自建基元，补齐 token 与基元（推荐）

**要做的**

1. 新增 5 个基元，消灭现有手写/原生控件：`select.tsx`（替代 19 处原生 select，含键盘/aria）、`dropdown.tsx`（替代 4 处手写弹层）、`tooltip.tsx`（现在大量 `title=` 属性，无统一样式）、`tabs.tsx`（吸收 `use-roving-tabs`）、`table.tsx`（表头/行高/padding 规范，替代 9 处原生 table）；可选 `field.tsx`、`empty.tsx`。
2. 在 `tailwind.config.js` 增加**字号与高度阶梯 token**（如 `fontSize: { '2xs': 11, xs: 12, sm: 13, base: 14 }`、`height: { control: { sm: 32, md: 36, lg: 40 } }`），把 660 处任意字号收敛到 4 档、117 处内联尺寸收敛到 3 档。
3. 防回退机制（见第 4 节）。

**要删的（文件级）**：无整文件删除。逐处替换：19 个原生 `<select>`、9 个原生 `<table>` 的标记、4 个页面级弹层实现、7 处 `rounded-[Npx]`、660 处任意字号（21:2x 快照，计数以 `npm run lint:ui` 当次输出为准；改为 token 类，属于改写不是删除）。基元文件保持 12 → 17 个。

**保留**：`index.css` 全部 token、`tailwind.config.js`、12 个基元、图表 16 色板、`cn()`、暗色 class 策略、`e2e` 快照。

**风险**：基元化本身有回归面（19 个 select 换成受控组件，键盘/表单行为要逐个验），收益是"下次不会再长歪"，但没有大爆炸式视觉统一。

**工作量**：基元 3–4 人日、页面收敛 2–3 人日、lint + 画廊 + 快照 1–1.5 人日，合计 **6–9 人日**。

**回滚**：低。逐 PR 可逆，无依赖级变更。

### B. 混合：antd 只用于复杂表格 / 表单 / 日期 / 虚拟列表

**要做的**：先解决 2.2 的层序（Tailwind 4 升级或 `:where` 兜底 spike），再写 `ConfigProvider` 主题桥把 74 个变量映射成 token（2.3），再接 `Table`/`DatePicker`/`Form`。表单层要和现有 `react-hook-form + zod` 对接（antd Form 自带状态，二者只保留一个，或受控桥接）。

**要删的（文件级）**：被替换区域的局部实现——例如若用 antd Table 重写日志表，则 `logs.tsx` 里的表头/分页/空态手写段（约 59 处字号类）与 `pagination.tsx` 可能部分退役；若用 DatePicker 替代 `time-range-picker.tsx`（191 行）则整文件删除。其余 12 个基元保留。

**保留**：现有 token 体系、绝大多数页面、图表。

**风险**：**两套视觉系统长期并存**，恰好放大了用户抱怨的"不一致"：antd 表格的圆角/字号/间距与手写卡片不同源；双轨 token 任何调色都要改两处。这是"用新债还旧债"。

**工作量**：**5–8 人日**（含 Tailwind 4 迁移风险；若只做 `:where` 兜底可压到 4–6，但要接受 `!important` 技术债）。

**回滚**：中。可保留 antd 只在一两个页面，回滚需整页回退。

### C. 全量换 antd

**要删的 / 要改的（这是用户明确要的清单）**

| 对象 | 数量 | 说明 |
|---|---|---|
| `web/src/index.css` | 1 个文件 / 250 行 | `:root` 74 条 + `.dark` 61 条 + `--chart-*` 32 条 + base/utilities 层；图表色板与 `--overlay/--tooltip-*/--code-*/--login-*` 无处安放（antd 无图表、无终端面板），实际只能保留一部分 |
| `web/tailwind.config.js` | 1 个 / 80 行 | 语义色映射、圆角、阴影、动画全废 |
| `web/postcss.config.js` | 1 个 / 6 行 | tailwindcss + autoprefixer 插件配置 |
| `web/src/components/ui/*` | **12 个文件** | button、card、input、label、alert、modal、pagination、search-select、time-range-picker、toast、icon-badge、metric-pills（+ 其 27 个单测引用面） |
| `web/src/lib/utils.ts` | 1 个文件 | `cn()`（clsx + tailwind-merge）；连带 `class-variance-authority`、`clsx`、`tailwind-merge` 3 个依赖退出 |
| `web/index.html` | 内联脚本 20 行（:9-28） | 防闪烁主题引导脚本，需改为 antd 主题初始化 |
| `web/src/lib/theme.ts` | 部分重写（:25-30、:41-72） | class 翻转 → algorithm 状态 |
| 页面/组件 className | **74 个 tsx / 1941 处** | 全部重写为 antd props + `styles`/`classNames`；其中 **660 处**任意字号（快照，以命令当次输出为准）、**117 处**具名内联尺寸、**19 个原生 `<select>`**、**9 个原生 `<table>`**、**4 个手写弹层** |
| `e2e/__screenshots__/*.png` | 7 张 | 视觉基线全部重拍；`e2e/visual.spec.ts` 选择器大量失效 |
| 单测 | 27 个文件 | `screen.getByRole`/`getByText` 多数仍可用，但 `components/ui/*` 的直接 import（107 处 / 32 文件）要改 |

**保留**：React Query、react-hook-form + zod、react-router 路由结构、`lib/api`/`format`/`time`、后端接口、图表渲染逻辑（仍需自绘 SVG 或接 `@ant-design/charts`，后者是另一套依赖）——**注意：换 antd 并不能删掉图表层和布局骨架的自定义样式**。

**风险**：① @layer/优先级改造（Tailwind 4 或 `!important` 兜底）；② token 双轨到单轨的搬迁，74+61 个变量要在 TS 里重建两套主题；③ 首屏 JS 翻倍（2.6）；④ React 19 必须走 v6（2.7），v6 只支持现代浏览器；⑤ 10 个页面视觉全量重做并逐页评审。

**工作量**：**25–40 人日**（按 74 个文件、1941 处 className、7 张快照、双主题、表单/图表桥接估；不含后续审美返工）。

**回滚**：**难**。改动是"地毯式"的，回滚只能整分支 revert；且一旦业务在新组件上继续开发，反向成本随时间上升。

---

## 4. 结论与建议

**选 A。** 用户要解决的"各组件动不动不一致"，根因是**控件配方的单一来源缺失**，不是缺一套组件库。当前 12 个基元只覆盖约一半控件；缺口处页面各自为战，于是 `<select>` 直接拿原生元素配 `h-8 rounded-[7px]` 写死。引入 antd 等于把"页面手写"换成"页面手写 + 组件库默认值"两个来源，还要付出层序迁移与首屏翻倍的代价，并把 already-tokenized 的颜色体系拆成两套。

**给用户的直白回答**

- antd 会和现有架构冲突，且是官方承认的冲突（Tailwind 优先级，FAQ + issue #38794）；不是"能不能用"，是"要连带改构建链和主题体系"。
- 换成 antd 确实要清理一大片 CSS：`index.css` 250 行、`tailwind.config.js` 80 行、12 个基元文件、74 个 tsx 的 1941 处 className、7 张视觉快照；而且图表色板和布局自定义部分**清不干净**。
- 更划算的路径：补齐 5 个基元 + 冻结任意尺寸值 + 一条 lint。花 1/3 的成本，解决"下一个控件又长得不一样"。

**防不一致机制（可执行，不是口号）**

1. **唯一入口**：`components/ui/*` 是唯一控件来源。`pages/**` 与 `components/<domain>/**` 中禁止出现原生 `<select`、`<table`、`aria-haspopup`、`role="menu"`。
2. **冻结任意值**：新增代码禁止 `text-[Npx]`、`h-[Npx]`、`rounded-[Npx]`、`gap-[Npx]`；字号只用 4 档 token，控件高度只用 3 档 token。存量（660 字号 + 117 内联尺寸 + 7 圆角）已按批次清零（9847b66：应用层 0，仅余 19 处 vendored 例外），先"禁止新增"再"逐页还债"。
3. **可执行检查**（不引新依赖，今天就能进 CI）：
   - **已落地**：`web/scripts/lint-ui.mjs`（零依赖 Node，递归扫 `src`，跳过 `__tests__`/`*.test.tsx`/`*.spec.tsx`，逐条规则报 `文件:行`；已含禁原生 select/table、禁任意字号/尺寸、禁 hex/`dark:`、禁 `window.confirm` 等规则，规则与计数口径见 ADR-064；2026-11-14 21:5x（9847b66）实跑 `filesScanned=81`，**0 error / 19 warning**（全为 vendored 例外，应用层 0，`result: PASS`）、`result: PASS`、exit 0——`window.confirm` 已由 t2 换成 `components/ui/confirm-dialog.tsx`，error 级规则清零；`no-dark-prefix` 在 `src/components/ui/**` 内按目录分档只计 warning（阶梯地板 5））；
   - `npm run lint:ui` **已落地**（`web/package.json` 的 `scripts.lint:ui` = `node scripts/lint-ui.mjs --max-warnings 19`，2026-11-14 lead 加入；封顶值就是当前 vendored 例外数，清一条下调一条），挂到 `build` 前或 CI；预算用 `--max-warnings` 按 [ui-adoption-map.md](./ui-adoption-map.md) §9 阶梯逐批收紧（当前地板 19 = vendored 例外数）；
   - 或写成一个 vitest 用例 `src/__tests__/ui-discipline.test.ts`（用 `node:fs` 扫描），这样现有 `npm test` 自动守住，不依赖新工具。
4. **组件画廊 + 视觉快照**：新增仅开发环境的 `/dev/ui` 页，渲染全部基元 × variant × size × 浅/深主题；在现有 Playwright `e2e/visual.spec.ts` 加一组快照。改基元必须重拍并人眼过一遍——这比"评审时说一句注意一致性"有效。
5. **评审清单**：PR 触及 `pages/**` 时检查是否新增手写控件；`h-9`、`rounded-control` 等尺寸类只允许出现在 `components/ui`。
6. **可量化验收**：把 `rg -c 'text-\[' src` 的计数作为技术债指标，每个迭代只降不升。

---

## 附录：可复现命令（在 `web` 下）

现状计数核对命令（在 `web` 下）：（本机没装 `rg` 时用等价的 PowerShell：`Get-ChildItem <dir> -Recurse -Include *.tsx,*.ts,*.css | Select-String '<pattern>'`，`-c` 计数用 `(... | Measure-Object).Count`；带 `--glob` 排除的目录直接从入参里去掉；`Select-String -Path "src/**/*.tsx"` 不递归展开，必须先 `Get-ChildItem -Recurse` 且 `-Include` 配 `-Recurse` 才生效）字面片段（含正则元字符，如 `text-[`、`h-8`、`title=`、`<div`）加 `-SimpleMatch` 才不会被 PS 5.1 当正则解析报错；交替/字符类（如 `h-8|h-9`、`role=.menu[^a-z]`）保持正则、不加。**行数 ≠ 次数**：`Select-String` 默认数**命中行**（`(... | Measure-Object).Count`）；数**出现次数**必须走正则模式并 `-AllMatches` 累加 `Matches.Count`，字面量用 `[regex]::Escape()` 包一层（如 `Select-String -Pattern ([regex]::Escape('text-[')) -AllMatches`）——**`-SimpleMatch` 不填 `$_.Matches`，用它算次数恒为 0**。`rg -c` 数命中行、`rg --count-matches` 才数次数。要复现 lint 的 count 必须 **`-Include *.tsx`**（脚本只扫 `src/**/*.tsx`）、模式带 `\b` 词边界并保持正则配 `-AllMatches`：字号规则 `\btext-\[\d+(?:\.\d+)?px\]` = **660 次 = 660 行**（每行恰 1 处），尺寸规则 `\b[hw]-\[\d+(?:\.\d+)?px\]` = **100 次 / 94 行**（28 个文件，2026-11-14 20:5x），字面 `text-[` = **691 次**（多 31 次 = rem / var / 颜色等非字号写法）。只数行用 `(... | Measure-Object).Count` 即可：字号这条两种口径同值，尺寸那条差 6。

```bash
rg -c "className=" src                                   # 1941 / 47 文件
node scripts/lint-ui.mjs --json                          # no-arbitrary-font-size 660 / 43 文件
rg -c '\b(h-8|h-9|h-10|h-11|rounded-md|rounded-lg)\b' src    # 117
rg -c 'rounded-\[[0-9]+px\]' src                         # 7
rg -c '<select' src                                      # 19 / 11 文件
rg -c '<table' src                                       # 9 / 8 文件
rg -c 'from "@/components/ui/' src                       # 107 / 32 文件
rg -n "@custom-variant dark|--radius-(sm|md|lg|xl|badge|control|card)" src/index.css   # 暗色变体与圆角 token 现已并入 CSS
rg -n "^:root|^\.dark|@layer|html\.dark|--chart-1:" src/index.css
```

参考链接（查阅日期均为 2026-11-14）：antd FAQ <https://ant.design/docs/react/faq#cssinjs-tailwind-conflict>；antd CSS Compatible <https://ant.design/docs/react/compatible-style>；issue #38794 <https://github.com/ant-design/ant-design/issues/38794>；v6 迁移文档 <https://github.com/ant-design/ant-design/blob/master/docs/react/migration-v6.en-US.md>；token 定制 <https://github.com/ant-design/ant-design/blob/master/docs/react/customize-theme.en-US.md>；npm registry <https://registry.npmjs.org/antd/6.7.0>；bundlephobia <https://bundlephobia.com/api/size?package=antd@6.7.0>。
