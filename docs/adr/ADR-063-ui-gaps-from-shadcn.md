# ADR-063 UI 缺口控件改用 shadcn registry 源码（Radix 底座），不再手写

---
status: superseded by ADR-064
date: 2026-11-14
superseded_by: ADR-064
---

## 背景

本仓前端形态：React 19.3 + Tailwind CSS 3.4.19 + 自建基元（`web/package.json`）；`web/src/` 104 个文件、10 个路由页面；`components/ui/` 12 个文件、共 1,246 行；色板与暗色由 `web/src/index.css` 的 74 条浅色 + 61 条深色变量单点定义（`index.css:5` / `:97`），`tailwind.config.js` 把它们映射成工具类，`lib/theme.ts:25-30` 切 `.dark` class、`index.html:9-28` 内联防闪。

缺口是控件配方而非 token：**19 处原生 `<select>`（11 个文件）、9 处原生 `<table>`（8 个文件）、4 套手写弹层**（theme-menu、topbar、search-select、time-range-picker），另有 1,941 处 `className=`、625 处任意像素字号、117 处内联控件尺寸（**当时快照**；本条已被 ADR-064 取代，现行口径见 ADR-064「清单计数口径」，任意字号实测 672 处）。典型症状：`pages/dashboard.tsx:783-810` 的手写分段控件与原生 `<select>`（`h-8 rounded-[7px] text-[12px]`）并排，与 `components/ui/button.tsx:21-25` 的尺寸阶梯、`rounded-control` 对不上。

上一轮结论（ADR-062）是「不引 antd，补 5 个基元，全部手写」。本轮按用户要求补做三方对比（[`../archive/ui-options.md`](../archive/ui-options.md)，已归档、只作证据），过程中确认了两件事，构成本条的动机：

1. **ADR-031 不是选型结论**。它是回填占位：背景、理由、备选三节都写着「压缩表没有记录，不补写没有出处的内容」（[`ADR-031`](./ADR-031-as-built-frontend-tailwind.md)）。它的「按 as-built 收敛」动机是让文档追认既存代码，不是论证「不该用 shadcn」。
2. **ADR-062 的备选段存在范围偷换**（详见「备选与否决原因」）：它用「全量重做 12 个基元」的成本，否掉了「只取缺口件」的选项。

## 决策

**A′ 方案：现有 token / 暗色 / 布局 / 12 个自建基元不动；6 个缺口控件（Select、Dialog、Dropdown/Menu、Tooltip、Tabs、Table）改为从 shadcn registry 取源码 copy-in（显式 `-b radix`，不吃 2026 年的默认底座），不再手写；同时不引入 antd。** 具体：

1. **不动现有底座**：`index.css` 的 74/61 条变量、`tailwind.config.js` 的颜色/圆角/字体映射、`lib/theme.ts` 的 class 策略、16 槽图表色板（ADR-040）、12 个现有基元全部保留；ADR-062 的「token 阶梯 + 可执行防护（`check-ui-discipline`）」两条继续有效。
2. **缺口件取源码**：`select`（替 19 处原生 select）、`dialog`（替 `modal.tsx` 及 4 套手写弹层中的两套）、`dropdown-menu`（替 theme-menu / topbar）、`tooltip`（替散落的 `title=`）、`tabs`（吸收 `lib/use-roving-tabs.ts` 的页面级拼装）、`table`（只取样式层，列逻辑保留）。
3. **底座显式选 Radix**：初始化与取件用 `npx shadcn@latest init -b radix`（2026-07-02 起 CLI 默认 Base UI，不能吃默认值）；接入后按官方给的能力记录「本仓底座 = Radix」。
4. **取 v3 风格组件**：本仓 Tailwind 3.4.19，取 registry 的 `new-york`（v3）风格——该路径仍有组件、源码用 `forwardRef`、依赖逐个 `@radix-ui/react-*`（已核对 `select.json` / `dialog.json`，2026-11-14）。
5. **一次性别名层**：新增约 40 行 CSS，把 shadcn 语义 token（`--background/--foreground/--card/--popover/--primary/--secondary/--muted/--accent/--destructive/--border/--input/--ring`）指到现有变量（如 `--background: var(--bg)`），并在 `tailwind.config.js` 补对应颜色映射；主题仍由 `index.css` 单点控制。
6. **尺寸类替换**：组件源码里的 `h-9`/`rounded-md` 一律改成 `h-control-*`/`rounded-control`（`tailwind.config.js:54` 已有圆角三档，高度阶梯按 ssot §9.2 补）。
7. **落地顺序**：阶段 0 决策登记（本条）→ 阶段 1 别名层 + `dialog` 试点 → 阶段 2 `select` 换 19 处 + `tooltip`/`dropdown-menu` → 阶段 3 `table`/`tabs` + 组件画廊 + lint 规则（细表见 [`../archive/ui-options.md`](../archive/ui-options.md) §5.2）。

## 理由

1. **a11y 缺口是自研最贵的部分**。Radix 官方声明其原语遵循 WAI-ARIA authoring practices，「we take care of many of the difficult implementation details related to accessibility, including aria and role attributes, focus management, and keyboard navigation」（<https://www.radix-ui.com/primitives/docs/overview/accessibility>，2026-11-14）。对照本仓：`components/ui/modal.tsx:41-43` 只有 `role=dialog`/`aria-modal`/`aria-labelledby`，没有焦点陷阱、没有初始焦点、关闭后不归还焦点；19 处原生 select 与 4 套手写弹层行为互不相同。手写这些行为与 copy-in 的成本不是一个量级。
2. **copy-in 不是「引一个依赖」**。shadcn 官方定位是「This is not a component library. It is how you build your component library.」（<https://ui.shadcn.com/docs>，2026-11-14）：源码进仓库、无运行时框架、不锁定主题，机制上与我们的 `cn()`（clsx + tailwind-merge）和 `cva` 完全同构（两者都已在 `web/package.json` 里）。因此退出成本低：删文件 + 卸依赖。这一点与 antd 的运行时主题 + 层序冲突形成明确对照（antd 的反对理由见 ADR-062 理由 1–4，本条不重复）。
3. **维护成本（用户最关心的 token 成本）**。自建路径下，agent 每次改控件都要先读私有源码：单个基元 51–243 行、全套 1,246 行（全量盘点约 12–15k tokens），且没有官方 agent 资料；shadcn 侧有 registry、MCP server、skills 与 `shadcn info --json`，组件 API 又是模型训练里的高频先验（<https://ui.shadcn.com/docs/mcp>、<https://ui.shadcn.com/docs/skills>，2026-11-14），单次查证成本降到 0.5–2k tokens 量级。
4. **Tailwind 3.4 下确实可用**，这是 ADR-031 当时没有核实的前提：v3 风格组件仍在 registry 发布（`/r/styles/new-york/select.json` 依赖 `@radix-ui/react-select`、源码含 `forwardRef`）。因此「不升 Tailwind 4 也能先落地缺口件」成立。
5. **成本同量级、产出更硬**：A′ 估算 6–9 人日（与 ADR-062 的 A 方案相同），但同样这些天里换来的是焦点管理/键盘/aria 的实际行为，而不是「自己写一遍再回头补 a11y」。

## 备选与否决原因

- **B 全量换 antd v6（或混合只上 Table/DatePicker）**：否决。层序冲突（CSS-in-JS `:where` 降权压过 Tailwind 工具类，需 `@layer` 配方，本仓 Tailwind 3.4 无原生级联层）、主题双轨（Seed/Map/Alias 派生 vs 74 条 CSS 变量）、体积（整包 1,446,397 B / 460,782 B gzip，48 deps）与清理不彻底（16 槽图表色板与 `--overlay/--tooltip-*/--code-*/--login-*` 无对应物），合计 25–40 人日。详见 ADR-062「理由」1–4 与 [`../ui-stack.md`](../ui-stack.md)。
- **「继续全部手写」（ADR-062 原第 1 步）**：否决，本条即是对这一步的修订。手写 `select/dialog/dropdown/tooltip/tabs` 等于从零实现一套 WAI-ARIA 模式（焦点陷阱、typeahead、aria-activedescendant、碰撞定位），成本高于 copy-in 且更难写对；同时它是 agent 维护成本最高的路径（每次都要读私有源码）。
- **ADR-062 的范围偷换（留档，防止同一推理再被重复一次）**：ADR-062 的备选段把「重新引入 shadcn/ui + Radix」的代价写成「重引入等于重做一遍基元并对齐 token，收益与 A 的方案重叠而成本更高」，然后据此否决。**这个成本是「全量重做 12 个基元」的成本，而本条只取 6 个我们本来就没有的缺口件**——用全量方案的代价否证增量方案，是范围偷换。同一推理模式还会以别的形式出现（例如用「全量重写前端」的代价挡回「升级 Tailwind 4」），所以在此显式记录：**评估一个方案时，成本必须按被评估方案的实际范围计，不能按它的超集计。**
- **C 全量换 shadcn（12 个基元全部替换）**：否决。已能用的基元重写一遍无收益；估算 8–14 人日，收益与 A′ 的缺口件部分重叠。
- **换 Base UI 或 React Aria 底座**：本轮不选。Radix 是当前证据最多的底座（官方 a11y 声明、2026-10 仍在推送、shadcn 三底座并存且 API 同一套），且未来换底座只需重装组件、不动调用方；作为后续选项保留，不在本条决策内。

## 后果与影响

- **新增依赖**：按需引入 Radix 原语；估算 gzip 40–70 KB（bundlephobia 单组件数据：select 24,282、dropdown-menu 30,293、dialog 13,197、tooltip 19,255、tabs 8,923、popover 23,482，含各自依赖，去重后更低）。**该数字是估算**，验证清单位于 [`../archive/ui-options.md`](../archive/ui-options.md) §6.1。
- **工作量**：估算 6–9 人日（阶段 0–3）；19 处原生 select 换受控组件有键盘/表单行为回归面，需逐页验证；视觉快照会因尺寸统一而变化，需人眼过一遍。
- **回滚**：删 `components/ui` 下的新增件 + 卸依赖 + revert 别名块；页面调用点逐文件回退。因为不触现有 token 与 12 个基元，回滚不需要整分支。
- **Tailwind 3.4 取 v3 风格组件**：本轮明确**不**升级 Tailwind 4（理由：用户当前要治不一致，不是换构建链；CSS-first 配置会动 `index.css` 的 74+61 条变量与 `@layer` 结构）。**已登记的隐藏前置**：2026 年新增的 shadcn 组件只发 Tailwind v4 风格（如 Trace/Code Block/AI Elements、`base-nova` 风格），将来要用这些新组件，必须先做 Tailwind 4 升级（估算 1.5–2.5 人日，含 `@theme`、`@tailwindcss/postcss`、OKLCH 换算与视觉回归）。该待办已登记在 `ssot.md` §15。
- **与 ADR-062 的关系**：主结论（不引 antd、继续 Tailwind + 自有 token、token 阶梯、可执行防护）**仍然有效**；被修订的是「补基元」的**方式**。ADR-062 的 front-matter 标 `partially_superseded_by: ADR-063`，status 仍为 `accepted`。**2026-11-14 晚更新**：本条与 ADR-062 都已被 ADR-064（全量迁移 shadcn + Tailwind 4）取代，两者的 status 现为 `superseded by ADR-064`、并带 `superseded_by: ADR-064`；上面这段判断保留为当时的推理记录。
- **约束**：copy-in 之后组件结构不得随意改（只允许改 token 类与尺寸类），否则失去「行为可预期」这一好处；shadcn 上游更新需手动 merge，不自动跟随。
- **例外（2026-11-15 裁定）**：允许 A 类**补一条受控行为** —— 受控打开（没有 Trigger）时由基元自己把焦点还给打开它的控件。实现在 `web/src/lib/dialog-opener-focus.tsx`，只挂在 dialog / sheet / alert-dialog 的 Content 上，**不改 DOM 结构与公开导出**。背景：Radix 的 `onCloseAutoFocus` 只认 `Trigger`，本仓弹窗全是状态驱动，关闭后焦点落到 body；此前靠页面各自补偿（三份重复实现），现下沉到基元。上游更新时保留这一处调用即可。
- **预期 a11y 收益**：dialog 的焦点陷阱/初始焦点/关闭归还；select 的键盘、typeahead 与 `aria-*`；dropdown/tooltip 的 aria 关系与碰撞定位。
- **未解风险**：移动端原生 select 行为差异；`time-range-picker.tsx`（191 行，已有键盘与 `role=dialog`）不换，避免引入 `react-day-picker + date-fns`（gzip 18 KB+）。

## 相关代码与文档

- 评估正文：[`../archive/ui-options.md`](../archive/ui-options.md)（三方矩阵、七维证据、ADR-031 逐条复核、落地路线、spike 清单）；[`../ui-stack.md`](../ui-stack.md)（antd 冲突面与清理清单）。
- 现状证据：`web/src/index.css:5`（:root 74 条）、`:97`（.dark 61 条）、`:79-94`/`:154-169`（16 槽色板）、`web/tailwind.config.js:3`（darkMode class）、`:54`（圆角三档）、`web/src/lib/theme.ts:25-30`、`web/index.html:9-28`（防闪）、`web/src/components/ui/modal.tsx:41-43`（aria 标注但无焦点管理）、`web/src/components/ui/button.tsx:21-25`（尺寸阶梯）、`web/src/pages/dashboard.tsx:783-810`（漂移样本）、`web/src/lib/use-roving-tabs.ts`（三处 tablist 的现有实现）。
- 相关 ADR：ADR-000（格式与状态词）、ADR-007（被 ADR-031 取代的「shadcn + TanStack Table」）、ADR-031（as-built 收敛，回填占位）、ADR-040（16 色板）、ADR-045（语义色令牌化）、ADR-062（不引 antd、补 token 与防护，本条部分修订）。
- 外部依据（查阅日期 2026-11-14）：shadcn 定位与 AI-Ready 原则 <https://ui.shadcn.com/docs>；三底座与默认切换 <https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default>、<https://ui.shadcn.com/docs/changelog/2026-07-react-aria>；CLI `init -b` 与 registry <https://ui.shadcn.com/docs/cli>；v3 风格组件 <https://ui.shadcn.com/r/styles/new-york/select.json>；主题与 CSS 变量 <https://ui.shadcn.com/docs/theming>；Radix 无障碍声明 <https://www.radix-ui.com/primitives/docs/overview/accessibility>；体积 <https://bundlephobia.com/api/size?package=radix-ui>。
