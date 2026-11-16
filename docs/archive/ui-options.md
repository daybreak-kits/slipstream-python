# 前端 UI 技术栈选型：现状自建 / antd v6 / shadcn+Radix 三方对比

> **已归档（2026-11-14）**：本文是历史文档，只作证据与背景，**不再更新、不再作为权威**。现状见 [../ui-adoption-map.md](../ui-adoption-map.md)（台账）与 [../ui-standards.md](../ui-standards.md)（规范正文）。

> 评估日期 2026-11-14。只读评估：未安装任何包、未改 `web/src`、未构建。外部事实均标 URL 与查阅日期；本地事实标 `文件:行号`。配套文档：[`ui-stack.md`](../ui-stack.md)（antd 冲突面与清理清单完整版）、[`adr/ADR-062`](../adr/ADR-062-ui-component-strategy.md)（上一轮决策）。

## 结论先行

1. **推荐 A′：保留现有 token / 暗色 / 布局 / 12 个自建基元，把「缺口控件」（Select、Dropdown/Menu、Dialog、Tooltip、Tabs、Table）改成从 shadcn 取源码（Radix 底座，copy-in），而不是继续手写，也不是整套换 shadcn。**
2. **一句话理由**：用户的痛点是「控件配方没有单一来源」，shadcn 的机制恰好是「把配方源码交给你」——它不是一个运行时依赖，也不是第二套主题引擎；它同时补上我们最缺的无障碍与浮层行为，并且它的 API 是模型训练里的高频先验（省 token 是真的，但省的是「读源码」而不是「读需求」）。
3. **代价**：约 6–9 人日（与 ADR-062 的 A 方案同量级，但产出多 5 个复杂基元与 a11y 行为）；新增 `radix-ui` 依赖，按实际用到的原语估算 gzip 40–70 KB；另有一个前置决策：Tailwind 3.4.19 → 4 是否进本轮（v3 风格组件仍在 registry 可取，但 2026 年新增组件只发 v4 风格）。
4. **不推荐 antd（v5/v6）**：全量换 25–40 人日、首屏 gzip +460 KB 量级、CSS-in-JS 与 Tailwind preflight 的层序冲突要 Tailwind 4 原生 `@layer` 才能干净解决，且清理不彻底（16 槽图表色板、`--overlay / --tooltip-* / --code-* / --login-*` 这类自有语义层 antd 没有对应物）。详见 [`ui-stack.md`](../ui-stack.md)。
5. **与 ADR-062 的关系**：主结论「不引 antd、继续 Tailwind + 自有 token」不变；但要修订其中「5 个基元全部手写」这一条——手写 `select/dropdown/tooltip` 等于自己重做一遍成熟的 WAI-ARIA 模式。建议立 ADR-063 记录这次修订（本轮只提案，不写 ADR）。
6. **一致性靠机制不靠选库**：无论选哪条路，都必须收敛到 `components/ui/*`、禁止页面内联尺寸类、加组件画廊与视觉快照。换库不会消灭 `dashboard.tsx:800` 那个问题，只有「配方单一来源 + 可执行检查」能。

## 1. 评估口径

- 三方案：**A 现状自建**（React 19 + Tailwind 3.4 + 12 个手写基元 + 页面内联样式）／**B antd v6 全量**／**C shadcn/ui + Radix 全量引入**。推荐方案 A′ = A 的骨架 + C 的缺口件，属于两者交集，见 §5。
- 「一致性」「可访问性」「维护成本」按证据评级 ✅ 可行／⚠️ 有条件／❌ 明确不足；不打分、不给加权总分（权重随团队变化，打分只会制造伪精度）。
- 外部事实来自官方文档、官方仓库、npm registry、bundlephobia，查阅日期 2026-11-14；推断性数字（如「按需引入后的体积量级」）在文中显式标注「估算」。
- 不写用例数与耗时计数（历史测试结果不作为选型证据）；本轮不装包，因此**没有任何方案的真实构建产物对比**，体积数字均为包级数据或估算——列为 §6 待验证项。

## 2. 七维度矩阵

| 维度 | A 现状自建 | B antd v6 | C shadcn + Radix |
|---|---|---|---|
| 1 一致性能力 | ⚠️ token 单源已很好（74/61 条 + tailwind 映射），但控件配方只有一半有基元，缺口靠页面手写 | ✅ 组件视觉默认一致、主题算法派生 token 最系统；❌ 与我们自有 token 形成双轨，两套主题引擎并存 | ✅ 源码即配方，可被 `cva` 与自有 token 约束；⚠️ 需自己写 token 别名层，否则组件默认语义名与现有变量名对不上 |
| 2 性能/体积 | ✅ 现有 dist 801,505 B JS + 44,790 B CSS，无第三方组件运行时 | ❌ 整包 1,446,397 B raw / 460,782 B gzip（48 deps），按需引入量级仍显著；CSS-in-JS 运行时开销 | ✅ copy-in 无运行时框架；⚠️ 每个原语带 Radix 依赖，select 24,282 gzip、dropdown-menu 30,293 gzip（含其依赖，去重后更低） |
| 3 美观与暗色 | ⚠️ 视觉规范写得很细（ssot §9.2）但靠人守；暗色 `.dark` class + 内联防闪脚本已生产验证 | ⚠️ 暗色靠 `theme.darkAlgorithm` 派生，能力最强；❌ 与 `.dark` class 策略、自有 74 条变量、16 槽图表色板并置，需重写整套主题 | ✅ CSS 变量 + `.dark` 覆盖同名 token，机制与我们完全同构（我们已有此机制）；⚠️ 默认变量名不同，需一次性别名映射 |
| 4 可访问性 | ❌ 自研缺口在最难的部分：`modal.tsx` 无焦点陷阱/初始焦点/焦点归还，19 处原生 `<select>`、4 套手写弹层 | ⚠️ 组件内建键盘与 `aria` 属性（rc-* 原语），但官方文档目录里没有无障碍章节，无统一承诺（见 §3.4） | ✅ Radix 官方声明遵循 WAI-ARIA authoring practices，明确交付 `aria`/`role`、焦点管理、键盘导航（原文见 §3.4） |
| 5 可测试性 | ✅ 纯 DOM + Tailwind 类，现有 Playwright 视觉快照直接可用 | ⚠️ CSS-in-JS 在 jsdom 里需要额外环境垫片，测试要包 `ConfigProvider`；视觉快照仍可行（本轮未实测，见 §6） | ✅ 纯 DOM + `data-slot` 属性，选择器稳定；源码在仓库内，单测可直接断言行为 |
| 6 维护成本 | ❌ 对 agent 最贵：模型对我们的基元没有先验，每次改动要先读 `components/ui/*.tsx`（全量 1,246 行）；无官方 agent 资料 | ✅ 训练语料最厚 + 官方 agent 产物最全（`llms.txt`/`llms-full.txt`/`design.md`/离线 CLI/MCP/skills）；⚠️ 但要同时维护「antd 主题」与「自有 token」两套心智 | ✅ 训练语料厚、官方 agent 产物全（MCP、skills、registry、`shadcn info --json`）；copy-in 代码仍在仓库内，改过的组件仍需读，但默认不用读 |
| 7 迁移成本 | ✅ 增量、无破坏（6–9 人日，ADR-062） | ❌ 25–40 人日 + 清理清单 + 不可逆度高 | ⚠️ 8–14 人日（全量）；**只取缺口件的 A′ 为 6–9 人日**，回滚是单目录 + 移除依赖 |

> 读法：三列的 ✅/❌ 不是同一件事的比较对象——B 的「一致性 ✅」是组件内部一致，代价是与我们自有体系不一致；C 的「维护 ✅」指 agent 侧，人力侧见 §3.6。

## 3. 逐维度证据

### 3.1 一致性能力：机制差异比组件数量重要

- **A（现状，历史快照）**：颜色已有单源（token 语义现以 [ADR-066](../adr/ADR-066-token-semantics-adoption.md) 为准）——`web/src/index.css:5` 的 `:root` 74 条 + `:97` 的 `.dark` 61 条，`web/tailwind.config.js` 把 `--bg/--surface/--border/--text-*/--primary-*` 映射成 `bg-bg`、`text-title` 等工具类；圆角三档在 `tailwind.config.js:54`（control 8px / card 12px / badge 6px）。问题不在 token，而在**控件配方**：`components/ui/` 只有 12 个文件、共 1,246 行，覆盖不到 Select/Dropdown/Tooltip/Tabs/Table，页面于是各写各的。
- **B（antd）**：主题是 Seed → Map → Alias 三层 token 的 JS 派生（ConfigProvider），组件默认视觉最整齐，但 token 命名空间（`colorPrimary` 等）与我们的 CSS 变量是两套真理源；引入后「主色」要在 `index.css` 与 `ConfigProvider.theme` 各写一次，改一次主题改两处——这不是「统一」，是「统一到另一个地方」。
- **C（shadcn）**：分发的是**源码**不是包——官网原话「This is not a component library. It is how you build your component library.」（https://ui.shadcn.com/docs ，2026-11-14）。组件用 `cn()` 合并类名、用 `cva` 管理 variant，正好是我们已有的依赖（`class-variance-authority ^0.7.1`、`clsx` + `tailwind-merge`，`web/package.json`）。因此天然可被我们的 token 与尺寸阶梯约束：把 `rounded-md` 换成 `rounded-control`、把 `h-9` 换成 `h-control-md` 只需改组件源码一次，之后所有调用方自动一致。
- 机制上的关键差别：antd 是「运行时主题 + 覆盖」，shadcn 是「编译前进仓库 + 直接改」。前者适合不做设计定制的产品，后者适合有自有视觉规范（我们 ssot §9.2 写了整页规则）的产品。

### 3.2 性能与体积

现状基线（2026-11-14 读 `web/dist/assets/`）：JS 801,505 B、CSS 44,790 B（未 gzip，构建产物原样大小）。

| 方案 | 包/运行时数据 | 说明 |
|---|---|---|
| B antd v6 | 1,446,397 B raw / **460,782 B gzip**，48 deps（bundlephobia，`antd@6.7.0`，2026-11-14） | 整包数字。按需引入（Table/Select/DatePicker/Modal/Form）后仍是 150–250 KB gzip 量级（**估算**，本轮未构建验证） |
| C shadcn 原语 | `radix-ui@1.8.1` 整包 258,497 B raw / 74,339 B gzip（55 deps）；单组件 select 69,650 / 24,282、dropdown-menu 92,748 / 30,293、popover 67,550 / 23,482、tooltip 54,795 / 19,255、dialog 39,476 / 13,197、tabs 26,253 / 8,923（bundlephobia，2026-11-14） | 这些是「各自带依赖」的独立数字，实际按需引入会共享 `@radix-ui/react-primitive` 等依赖，**去重后估算 40–70 KB gzip**（估算） |
| C 其它可选件 | `react-day-picker@9.11.1` 18,126 gzip、`cmdk` 16,724、`sonner` 9,390（bundlephobia，2026-11-14） | 仅当需要日期选择/命令面板/新版 toast 时才引入 |
| A′ 缺口件 | 只用 select/dialog/dropdown-menu/tooltip/tabs（+可能的 popover） | 对应上表子集；不引入 calendar/cmdk/sonner，日期仍用现有 `time-range-picker.tsx`（191 行，已实现键盘与 `role=dialog`） |

- **运行时差异**：antd v6 默认走 CSS 变量（v5 是 CSS-in-JS 运行时注入），比 v5 轻，但仍是「组件库运行时 + 主题派生」；shadcn 无运行时框架，只有 Radix 的行为代码；A′ 与现状同构，唯一新增是 Radix 行为层。
- **tree-shaking**：antd 依赖构建器正确配置按需引入；Radix 逐原语 ESM，天然可摇；自建本来就是源码。
- **首屏**：现状 `web/dist` 无路由级 code splitting（单一 `index-*.js`），任何方案都不改变这一事实——若在意首屏，优先做路由拆分，收益大于选库（**推论**，基于 dist 文件名结构）。

### 3.3 美观与暗色

- **A**：`web/src/index.css` 250 行承载全部语义层（`--overlay`、`--tooltip-*`、`--code-*`、`--login-*`、`--badge-<tone>-{bg,fg}`、16 槽 `--chart-*`）；`.dark` 61 条同名覆盖；`html.dark`（:182）只切 `color-scheme`；`lib/theme.ts:25-30` 切 class、`:41-72` 是 `useThemeMode`、`index.html:9-28` 是内联防闪脚本（localStorage + `prefers-color-scheme`）。这套机制**与 shadcn 一模一样**，差别只在变量名。
- **B**：antd 暗色 = `theme.darkAlgorithm` 派生，能力强（自动差值、对比度调整），但要求令牌由 antd 派生；我们 61 条深色变量里大量是「浅底深字徽标」「固定深色登录面板」这类**非派生**语义（ssot §9.2 明确规定），塞不进 algorithm 的派生规则，只能覆盖——又回到「两套真理源」。
- **C**：官方 Theming 文档明确「用 CSS 变量做语义 token，暗色就是在 `.dark` 里覆盖同名 token」（https://ui.shadcn.com/docs/theming ，2026-11-14）。默认 token 集约 40 个（`--background/--foreground/--card/--popover/--primary/--secondary/--muted/--accent/--destructive/--border/--input/--ring` + `--chart-1..5` + `--sidebar-*` + `--radius*`），能一对一映射到现有的 `--bg/--surface/--text-title/...`，多出来的 `--sidebar-*` 可裁剪。
- **是否闪**：三者都不改变现有防闪脚本，因为切换机制都是 class；shadcn 官方 Vite 暗色指南同样是 `useEffect` 里切 `documentElement.classList`（https://ui.shadcn.com/docs/dark-mode/vite ，2026-11-14），比我们 `index.html` 内联脚本晚一帧，保留内联脚本即可。

### 3.4 可访问性：这是 shadcn 最有说服力的一列

- **Radix 的官方承诺（原文）**：「Radix Primitives follow the WAI-ARIA authoring practices guidelines and are tested in a wide selection of modern browsers and commonly used assistive technologies.」并且「We take care of many of the difficult implementation details related to accessibility, including aria and role attributes, focus management, and keyboard navigation.」（https://www.radix-ui.com/primitives/docs/overview/accessibility ，2026-11-14）。这三项（`aria`/`role`、焦点管理、键盘导航）恰好是我们自研最薄弱的三项。
- **自研现状（代码证据）**：`modal.tsx:25` 有 Escape（该文件已于 9847b66 删除，本节行号是批 4 前的历史证据）、`:41-43` 有 `role=dialog`/`aria-modal`/`aria-labelledby`，但**没有焦点陷阱、没有初始焦点、关闭后不归还焦点**——这是对话框无障碍的硬要求；19 处原生 `<select>`（11 个文件）与 4 套手写弹层（theme-menu、topbar、search-select、time-range-picker）行为各不相同；相对好的是 `search-select.tsx:131-205`（`role=combobox`/`listbox`/`option`、`aria-expanded`、方向键）与 `pagination.tsx:98-190`（`aria-label`/`aria-current`/`role=status aria-live`），三处 tablist 也已收敛到 `lib/use-roving-tabs.ts`（ssot §9.3）。结论：**不是完全没做，而是每个缺口都自己做了一遍，且没做最难的部分。**
- **antd**：组件基于 rc-* 原语，键盘交互（Select 上下键、Modal Esc）通常可用；但官方文档目录（https://api.github.com/repos/ant-design/ant-design/contents/docs/react ，2026-11-14）里**没有无障碍（accessibility）章节**，也没有「遵循 WAI-ARIA」的承诺声明；焦点管理依赖具体组件实现，需逐组件验证。它不构成反对理由，但也不构成「换过去就解决 a11y」的理由。
- **A′ 的直接收益**：把 `modal` 换成 shadcn 的 `dialog`（Radix Dialog 自带焦点陷阱与归还）、19 处 `<select>` 换成 shadcn `select`（Radix Select 自带 typeahead/键盘/aria-*）、4 套手写弹层换成 `dropdown-menu`/`popover`。这三件事是「自研补不出来或补起来很贵」的部分。

### 3.5 可测试性

- 现状：Vitest + jsdom + @testing-library/react（开发依赖齐全，见 `web/package.json`），Playwright 视觉快照 `e2e/visual.spec.ts` 已有 7 张（dashboard-latency、login-dark、login-light、logs-100-rows、logs-expanded-failover、logs-time-range、rewrite-body）。注意：当前沙箱里 vitest 跑不起来，只有用户机器能跑——这是环境限制，不是选型证据。
- **C/A′**：组件是普通 DOM，`data-slot` 属性给出稳定选择器（例如 `[data-slot=dialog-content]`），行为测试可直接用 `userEvent` 打键盘；视觉快照照旧。**三个方案里测试成本最低**。
- **B**：antd 组件在测试里需要 `ConfigProvider` 包裹；CSS-in-JS/CSS 变量注入在 jsdom 下的行为与真实浏览器有差异（本轮未实测，列 §6）；视觉快照可行但断言要避开哈希类名。

### 3.6 维护成本（用户最关心的「未来维护要花大量 token」）

#### 3.6.1 AI/agent 维护成本：先算清楚「token 花在哪」

三种成本来源不同：**（i）读代码找 API**、**（ii）读代码确认意图**、**（iii）改完不自知地破坏视觉**。库能解决 (i)，解决不了 (ii)，(iii) 要靠机制。

| | A 现状自建 | B antd v6 | C shadcn |
|---|---|---|---|
| 模型先验 | 低：`components/ui/*.tsx` 是本仓私有代码，模型没见过，必须读 | 极高：antd 是训练语料里的头号 React 组件库之一 | 高：shadcn 源码是 2023 年以来被大量复制的同一批文件，模型见过 |
| 官方 agent 产物 | 无 | **最全**：`for-agents` 提示词、`llms.txt`/`llms-full.txt`/`llms-semantic.md`/`design.md`、离线元数据 CLI `@ant-design/cli`（全部 prop/token/demo/changelog 本地可查）、MCP、skills（https://ant.design/docs/react/for-agents 、https://ant.design/docs/react/llms ，2026-11-14） | **齐全**：MCP server（浏览/搜索/安装 registry）、skills（`npx skills add shadcn/ui`）、`shadcn info --json` 输出框架/Tailwind 版本/已装组件（https://ui.shadcn.com/docs/mcp 、https://ui.shadcn.com/docs/skills ，2026-11-14） |
| 单次改动典型读取量 | 读 1 个基元（51–243 行）+ `tailwind.config.js` 80 行 + `index.css` token 名 ≈ **4–6k tokens**；全面盘点读全 1,246 行 ≈ **12–15k tokens** | 无需读组件源码；查一次 MCP/CLI/llms.txt ≈ **0.5–2k tokens**；但改主题要同时读两套 token | 无需读组件源码（除非我们改过它）；查 MCP/skills ≈ **0.5–2k tokens**；改过的那几个组件仍需读（目标就是少改它） |
| 典型风险 | agent 写出「又一个 `h-8 rounded-[7px]`」，因为源码里没有正解可抄 | agent 用 antd 习惯写出 `<Table/>`，与页面既有手写表格视觉不一致 | agent 直接 `npx shadcn add` 新组件而不做 token 适配，产生第三种视觉 |

换算口径：TSX 约 8–12 token/行，中文文档另计；上表线性估算，数量级可信、精确值不必当真。**结论**：用户「未来维护要花大量 token」的判断在 A 方案下成立（每次都要读私有源码），B/C 显著下降；但 C 比 B 多一层现实约束——组件源码在仓库里，谁都能改，改完就失去「库 = 可预期」的性质。所以 token 成本最低的是 B，**综合考虑（视觉主权 + a11y + 无运行时 + 可测试）最优的是 C 的缺口件用法**。

#### 3.6.2 人力维护

- **升级路径**：antd 是 `npm install antd@next`，大版本迁移有官方迁移文档（v5→v6 有 `migration-v6`，需处理 `ConfigProvider`/cssVar 变更）；shadcn 没有包版本可升——组件是你自己的，升级=重新 `add` 一份并手动 merge（2026-09-03 起还多一个 `cn` 包迁移，官方提供 `shadcn migrate cn`），这既是自由也是负担；自建没有升级，只有自己持续投入。
- **破坏性变更频率**：antd 6.0.0 发布于 2025-11-22、当前 6.7.0（2026-11-04），v5→v6 间隔约三年（https://api.github.com/repos/ant-design/ant-design/releases/tags/6.0.0 、https://api.github.com/repos/ant-design/ant-design/releases ，2026-11-14）；Radix 原语逐包小版本演进，shadcn 的 `radix-ui` 统一包在 2026-02 切换过一次导入路径（官方给了 `migrate radix`）；自建基元没有外部破坏性变更，但每次需求都可能破坏自己。
- **社区规模**（2026-11-14，GitHub API / npm downloads API）：antd stars 100,201、npm 周下载 4,914,288；shadcn/ui stars 128,158、CLI 周下载 18,266,041；Radix Primitives stars 19,499（最近推送 2026-10-14）；`radix-ui` 统一包周下载 28,445,722。**Radix 没有被弃养**：2026-07 起 shadcn 新项目默认改用 Base UI，但官方明确「Radix is not being deprecated」，所有更新与新组件仍双库发布（https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default ，2026-11-14）。当前 shadcn 支持三种底座：Radix、Base UI（默认）、React Aria（https://ui.shadcn.com/docs/changelog/2026-07-react-aria ）。
- **团队规模现实**：这个仓库没有专职前端（`web/src` 104 个文件、13 个页面，由「agent + 少量人工评审」模式产出），所以「人力维护」的真实含义是「agent 能不能稳定产出正确代码 + 人能不能低成本评审」。这一点上 B/C 都优于 A。

#### 3.6.3 锁定与退出成本

- **B antd**：锁定最深——主题、locale、样式层序、测试环境都与它耦合；退出=整分支重写，这也是「全量换 25–40 人日」的另一面。
- **C/A′**：锁定最浅——源码在仓库，Radix 只是原语包（可替换为 Base UI/React Aria，shadcn 已把三者抽象成同一套 API）；退出=删 `components/ui` 里那几个文件 + 卸依赖。唯一残留是 token 别名层（几十行 CSS），可以留着。
- **A 现状**：技术上无锁定，但**人力锁定**——那 1,246 行私有代码只有读过它的人（或 agent 的长上下文）能改。

### 3.7 迁移成本（文件级）

| 方案 | 要删/改什么 | 保留 | 人日 | 回滚 |
|---|---|---|---|---|
| A 自建补 5 基元 | 新增 `select/dropdown/tooltip/tabs/table` 5 个文件（≈12→17 个）；逐处替换 19 个原生 `<select>`、9 个原生 `<table>`、4 套手写弹层、7 处 `rounded-[Npx]`、660 处任意字号；无整文件删除 | 全部现有 token、12 个基元、图表 16 色板、`cn()`、暗色策略、视觉快照 | 6–9（ADR-062 口径） | 低（增量） |
| B antd v6 | 删 `index.css`（250 行、74+61 变量、32 个图表槽位）、`tailwind.config.js`（80 行）、`postcss.config.js`、12 个基元文件、`cn()` 与 3 个依赖；改写 74 个 tsx 的 1,941 处 `className=`（含 660 处 `text-[Npx]`、100 处任意尺寸 `h-[Npx]`/`w-[Npx]`）；重拍 7 张视觉快照；图表色板/登录面板/代码块语义层需重建 | 页面结构、路由、数据层、`lib/*` | 25–40 | 高（整分支） |
| C shadcn 全量 | 12 个基元全部替换为 shadcn 版本（约 1,246 行手写代码退役）；新增 `components.json`、token 别名层（`.dark` 同步），19 个 select / 9 个 table / 4 个弹层全部换实现；重拍视觉快照 | `index.css` 变量、16 槽色板、布局与页面结构、`lib/*`、`cn()`（已与 shadcn 同构） | 8–14（**估算**） | 中（copy-in 可逐文件回退，但 token 别名与调用点已扩散） |
| **A′ 推荐** | 取 C 的 6 个缺口件源码（select/dialog/dropdown-menu/tooltip/tabs/table 样式层）+ 一个 token 别名块（约 40 行 CSS + tailwind 映射）；替换 19 select / 4 弹层 / 9 table 的标记；现有 12 基元**不动**（button/input/label 等继续用，shadcn 的 cva 变体只用来对齐尺寸阶梯） | 同 A | 6–9（**估算**，与 A 同量级，多 5 个复杂基元但少一轮手写 a11y 调试） | 低（新增目录 + 依赖；逐文件可回退） |

## 4. 复核 ADR-031／ADR-062：当时到底决定了什么

ADR-031 是一份**回填占位**：它的「背景」「理由」「备选与否决原因」三节都明确写着「压缩表没有记录，不补写没有出处的内容」（`docs/adr/ADR-031-as-built-frontend-tailwind.md`）。所以严格说，**当时没有可逐条复核的理由**；能复核的只有结论句和它的后果记录。逐条如下：

| 当时的说法（出处） | 今天是否成立 | 判断依据 |
|---|---|---|
| 「按 as-built 收敛：Tailwind + 手写 UI，不引 shadcn/Radix/TanStack Table」（ADR-031 决策） | ⚠️ **部分不成立**。「as-built 收敛」的动机是让文档追认既存代码，不是做选型——它回答「现在是什么」，不是「该不该换」。作为选型结论，它缺理由、缺备选比较。 | ADR-031 全文三节标注「无出处」；同一批次 ADR-007 曾定「只用 shadcn/ui + TanStack Table」后被它取代，同样无理由（`ADR-007` 的「备选与否决原因」只写「后续转变见 ADR-031」） |
| 「不引 shadcn/Radix」 | ❌ **不成立（当时把 shadcn 当成「又一个组件库/依赖」）**。shadcn 官方定位是「不是组件库，是把组件源码分发给你」（https://ui.shadcn.com/docs ）；它不提供运行时、不锁定主题、源码进仓库，与「引一个依赖」是相反的机制。这是 ADR-031 结论的最大盲点。 | 官方 Introduction 与 Open Code/AI-Ready 原则；本次核对：v3 风格组件仍在 registry（https://ui.shadcn.com/r/styles/new-york/select.json 存在，依赖 `@radix-ui/react-select`，源码用 `forwardRef`），「Tailwind 3.4 项目用不了 shadcn」也不成立 |
| 「不引 TanStack Table」 | ✅ **仍成立**。shadcn 的 `table` 只是样式化 `<table>`，不含排序/分页/虚拟化（registry 里 `table` 的依赖只有 `cn`）；现在 9 处原生表格需求不到需要 TanStack Table 的规模。ADR-007 当年把 shadcn 与 TanStack Table 捆绑，是误读。 | https://ui.shadcn.com/r/styles/new-york-v4/table.json 的 `dependencies: ["cn"]`，2026-11-14 |
| 「组件需自己补无障碍/测试」（ADR-031 后果） | ✅ **成立且已应验**：`modal.tsx:41-43` 只做了 `aria` 标注、没有焦点管理；19 处原生 `<select>` 与 4 套手写弹层各写各的。这句话当时只是记录，今天应升级为**待还的债**。 | §3.4 引用的代码证据 |
| 「当时 Tailwind/React 版本与今天不同，所以结论过时」 | ❌ **不成立**。git 只有两个 commit 触及 `web/package.json`（`a70cd50` 快照、`9a0b6d2` 2026-11-13 baseline），HEAD 的 `tailwindcss ^3.4.19` 与 `react ^19.3.1` 与今天一致；仓库没有更早的前端历史。所以「版本变了所以当年不适用」这条借口不存在——当年就是在这个版本上决定不用的。 | `git log -- web/package.json`（2026-11-14 核查） |
| ADR-062「重新引入 shadcn/ui + Radix，等于重做一遍基元，收益与 A 重叠而成本更高」 | ⚠️ **对「全量重做」成立，对「只取缺口件」不成立**。把 12 个已可用基元重写一遍确属浪费；但 `select/dialog/dropdown` 这类我们**本来就没有**的件，手写等于从零实现 WAI-ARIA 模式，成本不低于 copy-in。ADR-062 用「全量」的成本否证了「增量」的选项，范围偷换。 | §3.4 与 §3.7 的对比 |

**一句话**：ADR-031 不是「想清楚后决定不用 shadcn」，而是「当时没有做选型，只做了现状登记」；ADR-062 的主结论（不引 antd）依然正确，但它顺带把「引 shadcn 原语」也否掉了，理由是成本按「全量重做」算的。推翻自己之前的结论不算丢人，掩盖它才是——所以这里把范围重新划清。

## 5. 推荐方案 A′ 与落地路线

### 5.1 方案定义

- **底座不动**：`index.css` 的 74/61 条变量、`tailwind.config.js` 的颜色/圆角/字体映射、`lib/theme.ts` 的 class 策略、16 槽图表色板（ADR-040）、现有 12 个基元中的简单件（button/input/label/card/alert/icon-badge/metric-pills/pagination/toast）。
- **缺口件取 shadcn 源码（Radix 底座）**：`select`（替 19 处原生 select）、`dialog`（替 `modal.tsx` 与 4 套手写弹层中的两套）、`dropdown-menu`（theme-menu/topbar）、`tooltip`（替散落的 `title=`）、`tabs`（吸收 `lib/use-roving-tabs.ts` 的页面级拼装）、`table`（表格样式层，保留现有列逻辑）。
- **一次性别名层**：新增约 40 行 CSS，把 shadcn 需要的 `--background/--foreground/--card/--popover/--primary/--secondary/--muted/--accent/--destructive/--border/--input/--ring` 指向我们现有变量（例如 `--background: var(--bg)`），并在 `tailwind.config.js` 补对应颜色映射。这样 shadcn 组件源码可保持原样，主题仍由 `index.css` 单点控制。
- **尺寸阶梯统一**：把 shadcn 源码里的 `h-9`/`rounded-md` 换成我们的 `h-control-md`/`rounded-control`（`tailwind.config.js:54` 已有圆角；高度阶梯按 ssot §9.2 的 32/36/40/44 补 3–4 档），新件与旧件在同一行里同高。

### 5.2 落地顺序（每步都可独立交付）

1. **阶段 0（0.5 人日）**：决策与登记——ADR-063 记录「缺口件采用 shadcn copy-in」；同时决定 Tailwind 3.4 → 4 是否本轮做（若不升级，用 v3 风格组件，功能不受影响但只能取 2025-02 之前的组件版本）。
2. **阶段 1（1.5–2 人日）**：别名层 + `components.json` + 一个试点件（建议 `dialog`，因为 `modal.tsx` 的 a11y 债最明确），跑通「copy-in → 改 token 类 → 单测/快照」流水线。
3. **阶段 2（2–3 人日）**：`select` 替换 19 处原生控件（含 11 个文件），这是用户痛点最直接的修复面；同步补 `tooltip`/`dropdown-menu`。
4. **阶段 3（2–3 人日）**：`table` 样式层统一 9 处表格、`tabs` 吸收 3 处 tablist 拼装；补组件画廊 `/dev/ui`（仅开发环境）与视觉快照；加 lint 规则（禁止页面内 `<select>/<table>/text-[Npx]/rounded-[Npx]`）。
5. **收尾（0.5 人日）**：更新 `ssot.md` §9 与 ADR 索引；把「新组件必须走 `components/ui/*` + token 类」写进评审清单。

### 5.3 防不一致机制（比选库更重要）

1. **唯一入口**：所有 UI 原语只允许在 `web/src/components/ui/*` 定义；页面禁止直接写 `<select>`/`<table>`/裸 `<dialog>`。
2. **尺寸/字号 token 化**：`tailwind.config.js` 增 `fontSize` 4 档（11/12/13/14）与控件高度阶梯；660 处 `text-[Npx]`（批 4 前历史快照）已收敛到 4 档：9847b66 后应用层 0 处，仅余 vendored 例外 10 处（精确值以 `npm run lint:ui` 当次输出为准）。
3. **lint 规则**：禁止 `text-[..px]`/`rounded-[..px]`/`h-8|h-9|h-10|h-11` 出现在 `pages/**`（现有 117 处内联尺寸就是漂移来源）。仓库当前没有 eslint（`web/package.json`），要么加 eslint + 单条 `no-restricted-syntax`，要么用 `rg` 做 CI 检查脚本——后者零依赖，一天能上。
4. **组件画廊 + 快照**：开发环境 `/dev/ui` 渲染全部基元 × variant × size × 浅/深；改基元必须重拍快照并人眼过。现有 `e2e/visual.spec.ts` 已有 7 张，可复用这套机制。
5. **同一行同高**：把「同一行相邻控件必须同高」写进评审清单（ssot §9.2 已有规则，但没有执行手段——这正是 `dashboard.tsx:783-810` 出问题的原因）。
6. **agent 约束**：在 `AGENTS.md` 写明「改 UI 先读 `components/ui` 与 `ui-stack.md`；不得引入 antd；新增控件先查 `components/ui` 是否已有」——把结论变成 agent 的默认路径。

## 6. 风险与未验证项

- **未做真实构建对比**：本轮遵守只读纪律（沙箱里也装不了包：esbuild spawn EPERM），因此 B 的按需体积、C 的运行时去重体积、antd CSS-in-JS 在 jsdom 下的行为都是「包级数据 + 估算」。要落定它们需在**真实环境**跑 §6.1 的 spike（约 1 人日），命令、预期产物与判据都已写死。
- **Tailwind 版本是隐藏前置**：当前 `tailwindcss ^3.4.19`。shadcn 官方明确 v3 项目可继续用（「this is non-breaking… new components still in v3」），但 2026 年新增组件（Trace/Code Block/AI Elements、base-nova 风格）只发 v4 风格；长期看 A′ 应与 Tailwind 4 升级（估算 1.5–2.5 人日，含 `@theme`、`@tailwindcss/postcss`、OKLCH 换算、视觉回归）绑定规划。
- **Radix 的长期归属**：2026-07 起 shadcn 默认底座改为 Base UI（同一批 MUI 团队作者，`@base-ui/react@1.9.0`、周下载 30,276,609），Radix 不再默认但仍维护。**这不影响 A′**：shadcn 把三种底座抽象成同一套 API，未来换底座只需重装组件，不动调用方；但选型时应显式记录「用 Radix 底座」（`npx shadcn init -b radix`），不要吃默认值。
- **select 替换的回归面**：19 处原生 `<select>` 里有些依赖原生行为（移动端原生选择器、表单 `value` 直读）；换成受控组件需逐页验证（尤其 `channel-form-dialog`、`rewrite-rules-editor`、`dashboard` 的刷新间隔控件）。
- **暂不建议动日期控件**：现有 `time-range-picker.tsx`（191 行）已实现键盘与 `role=dialog`，换 shadcn `calendar` 会引入 `react-day-picker + date-fns`（gzip 18 KB+），收益有限，留到有明确需求再评估。

### 6.1 待验证 spike 清单（在真实环境执行，约 1 人日）

> 这份清单**不能在 agent 沙箱里跑**（esbuild spawn EPERM、npm 装包被拒），必须由人在真实机器上执行。每项给了命令、预期产物、判据；跑完把结论回写到 §3.2 / §3.7 与 ADR-063「后果与影响」。建议独立分支，别弄脏工作树。

#### S1 antd 按需体积（验证 §3.2 的 150–250 KB 估算）

```bash
cd web
git switch -c spike/antd-size
npm i antd@6.7.0
# 新建 src/spike-antd.tsx：只 import 计划真正会用到的组件
#   Table, Select, DatePicker, Modal, Form, Input, Button, ConfigProvider
npx vite build --outDir dist-spike-antd
gzip -c dist-spike-antd/assets/index-*.js | wc -c
gzip -c dist-spike-antd/assets/index-*.css | wc -c
```

- 预期产物：`web/dist-spike-antd/assets/index-*.js` 与 `index-*.css`；对照组是现有 `web/dist/assets/`（JS 801,505 B、CSS 44,790 B，未压缩）。
- 判据：gzip 增量 **≥ 250 KB** → §3.2 的「首屏翻倍、代价过高」成立，B 维持否决；**150–250 KB** → 估算成立，仍不推荐；**< 150 KB** → **必须重开 B 的评估**，把实测值写回 §3.2 并在 ADR-063 补一段「为何仍不选」。另记录 CSS 增量与构建是否出现 `@layer` 相关警告。

#### S2 Radix 去重体积（验证 A′ 的 40–70 KB 估算）

```bash
npm i radix-ui
# 新建 src/spike-radix.tsx：只 import Select / Dialog / DropdownMenu / Tooltip / Tabs 五个 primitive
npx vite build --outDir dist-spike-radix
gzip -c dist-spike-radix/assets/index-*.js | wc -c
```

- 判据：gzip 增量落在 **40–70 KB** → 估算成立，可写进 ADR-063；**> 100 KB** → 回写 §3.2，并评估「只上 select + dialog」的裁剪版（把 tooltip/tabs 推迟到阶段 3）。

#### S3 shadcn v3 组件在 Tailwind 3.4.19 下能否落地（结构性验证）

```bash
cd web
npx shadcn@latest init -b radix      # 显式 Radix 底座；选 new-york 风格；components.json 的 tailwind.config 填 tailwind.config.js
npx shadcn@latest add button select dialog dropdown-menu tooltip tabs table
git diff --stat
npm run build
```

- 预期产物：`web/components.json`、`web/src/components/ui/{select,dialog,dropdown-menu,tooltip,tabs,table}.tsx`、可能新增的 `web/src/lib/utils.ts`（若与现有 `cn()` 重复则合并，不要留两份）。
- 判据（四条全过才算阶段 1 可开工）：① `npm run build` 通过；② 生成文件是 **v3 风格**（含 `forwardRef`、依赖 `@radix-ui/react-*`，而不是 2026 的 `cn` 包 + `data-slot` 风格）；③ `git diff` 显示 `tailwind.config.js` / `index.css` **只增不改**（若 CLI 改写了既有变量，逐条记录，作为别名层必须规避的项）；④ `npm run test:visual` 后基线差异只与尺寸统一相关。任一条不满足 → 回写本节并停止阶段 1。

#### S4 a11y 行为验收（A′ 的实际收益，不靠猜）

新增 `web/e2e/spike-a11y.spec.ts`（Playwright，复用现有 `e2e/` 基建）：

- **dialog**：打开 → `Tab` 在弹窗内循环（焦点不出容器）→ `Escape` 关闭 → 焦点回到触发按钮。
- **select**：`↑/↓` 改高亮、输入首字母跳转、`Esc` 关闭且不提交、`aria-expanded` 随开合变化。

判据：四条全绿 = A′ 的 a11y 收益成立；出现焦点逃逸或焦点不归还 = 记入 §6 风险，并检查是否漏了 `DialogPortal` / 焦点陷阱相关 props（而不是宣布「Radix 也不行」）。

#### S5 结论回写（不做这一步等于没跑）

- S1/S2 的 gzip 实测值 → 替换 §3.2 表里的「估算」字样，并同步 ADR-063「后果与影响」第一段。
- S3 结果 → §3.7 的 A′ 行（人日估计是否仍成立）与 §6 的 Tailwind 前置条目。
- S4 结果 → §3.4 的「A′ 的直接收益」段（改成实测口径）。
- 任一项推翻结论 → 在 §4 增补一条复核，不要只改结论不改论证。

## 附录：外部事实清单（均为 2026-11-14 查阅）

| 事实 | 来源 |
|---|---|
| shadcn 定位「不是组件库，是你构建组件库的方式」；Open Code/Composition/Distribution/Beautiful Defaults/AI-Ready | https://ui.shadcn.com/docs |
| 三底座（Radix / Base UI 默认 / React Aria）、多风格；2026-07-02 起新项目默认 Base UI，Radix 不弃用 | https://ui.shadcn.com/docs/changelog/2026-07-base-ui-default ；https://ui.shadcn.com/docs/changelog/2026-07-react-aria |
| 2026-02-02 起 `new-york` 用统一 `radix-ui` 包；2026-09-03 起组件 `import { cn } from "cn"`；CLI v4 含 `--preset`、skills、MCP | https://ui.shadcn.com/docs/changelog/2026-02-radix-ui ；https://ui.shadcn.com/docs/changelog/2026-09-cn ；https://ui.shadcn.com/docs/changelog/2026-03-cli-v4 |
| Theming：CSS 变量 + `.dark` 覆盖同名 token；components.json 的 `tailwind.cssVariables` 默认 true | https://ui.shadcn.com/docs/theming |
| Tailwind v4 说明「non-breaking，v3 + React 18 项目继续可用」 | https://ui.shadcn.com/docs/tailwind-v4 |
| Vite 暗色指南（class 切换 + `useEffect`） | https://ui.shadcn.com/docs/dark-mode/vite |
| v3 风格组件仍在 registry（`new-york/select.json`、`new-york/dialog.json` 用 `forwardRef` 与 `@radix-ui/react-*`） | https://ui.shadcn.com/r/styles/new-york/select.json ；https://ui.shadcn.com/r/styles/new-york/dialog.json |
| Radix 无障碍承诺（WAI-ARIA、aria/role、焦点管理、键盘导航） | https://www.radix-ui.com/primitives/docs/overview/accessibility |
| antd v6 迁移（React 19 不再需要 v5 patch）、compatible-style 的 `@layer` 排层要求 | https://github.com/ant-design/ant-design/blob/master/docs/react/migration-v6.en-US.md ；https://ant.design/docs/react/compatible-style |
| antd 6.0.0 发布于 2025-11-22、6.7.0 于 2026-11-04；peer react >=18 | https://api.github.com/repos/ant-design/ant-design/releases/tags/6.0.0 ；https://registry.npmjs.org/antd/6.7.0 |
| antd 面向 agent 的产物（for-agents 提示词、`llms.txt`/`llms-full.txt`/`design.md`、离线 CLI、MCP） | https://ant.design/docs/react/for-agents ；https://ant.design/docs/react/llms |
| antd 文档目录无无障碍章节 | https://api.github.com/repos/ant-design/ant-design/contents/docs/react |
| 体积：antd 6.7.0 460,782 gzip / 48 deps；`radix-ui@1.8.1` 74,339 gzip；单原语 select 24,282 / dropdown-menu 30,293 / dialog 13,197 gzip | https://bundlephobia.com/api/size?package=antd@6.7.0 等（bundlephobia 公开 API） |
| 下载量：antd 周 4,914,288；`radix-ui` 周 28,445,722；shadcn CLI 周 18,266,041；`@base-ui/react` 周 30,276,609 | https://api.npmjs.org/downloads/point/last-week/<pkg> |
| stars：antd 100,201；shadcn/ui 128,158；radix-ui/primitives 19,499 | https://api.github.com/repos/<owner>/<repo> |
