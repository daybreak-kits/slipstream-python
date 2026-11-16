# ADR-062 UI 组件策略：继续 Tailwind + 自建基元，补 token 与统一基元，不引入 antd

---
status: superseded by ADR-064
date: 2026-11-14
superseded_by: ADR-064
---

## 背景

前端是 React 19.3 + Tailwind CSS 3.4 + 自建基元（`web/package.json`；ADR-031 已定"按 as-built 收敛：Tailwind + 手写 UI，不引 shadcn/Radix/TanStack Table"）。实际形态是：`web/src/` 104 个文件、10 个路由页面、12 个 `components/ui/` 基元、`index.css` 74 条浅色 + 61 条深色 CSS 变量、16 槽图表色板、class 策略暗色（`tailwind.config.js:3`、`lib/theme.ts:25-30`、`index.html:9-28` 防闪烁）。

问题是**控件配方没有单一来源**：12 个基元只覆盖一半控件，缺口处页面各写各的——19 个原生 `<select>`（11 个文件）、9 个原生 `<table>`（8 个文件）、4 套手写弹层，另有 1941 处 `className=`、625 处任意像素字号、117 处内联控件尺寸、7 处任意圆角（**这些是 2026-11-14 当时的快照**；本条已被 ADR-064 取代，现行口径见 ADR-064「清单计数口径」，其中任意字号实测订正为 672 处）。典型症状：`pages/dashboard.tsx:783-796` 的手写分段控件与 `:800-810` 的原生 `<select>`（`h-8 rounded-[7px] text-[12px]`）并排，外形与 `components/ui/button.tsx:21-25` 的 36/32px 阶梯、`rounded-control` 对不上。用户因此提出：要不要换 antd，换的话要清理哪些 CSS。

评估正文见 [`../ui-stack.md`](../ui-stack.md)（现状盘点、冲突面、A/B/C 三方案的成本与删除清单、可执行防护）。

## 决策

**不引入 antd（v5 与 v6 均不引入），继续 Tailwind + 自建基元**，但把"随手写样式"这条路径堵死，分三步落地：

1. **补基元**：新增 `components/ui/select.tsx`、`dropdown.tsx`、`tooltip.tsx`、`tabs.tsx`、`table.tsx`（12 → 17 个），分别取代 19 处原生 `<select>`、4 处手写弹层、散落的 `title=`、三处页面级 tablist 拼装、9 处原生 `<table>`。
2. **补 token 阶梯**：`tailwind.config.js` 增加字号 4 档（11/12/13/14）与控件高度 3 档（32/36/40），把 625 处 `text-[..px]`（后实测 672 处，见 ADR-064）、117 处内联尺寸、7 处 `rounded-[Npx]` 逐页收敛；新增代码禁止任意值。
3. **加可执行防护**：`components/ui/*` 之外禁止 `<select`/`<table`/`aria-haspopup`/`role="menu"`/`text-[Npx]`/`rounded-[Npx]`/`h-[Npx]`；落地检查用 `web/scripts/check-ui-discipline.mjs`（`npm run lint:ui`）或等价的 vitest 用例，并新增仅开发环境的 `/dev/ui` 组件画廊进 Playwright 视觉快照。

## 理由

1. **冲突是官方承认的，不是风格偏好**。antd CSS-in-JS 运行时注入 + `:where` 降权，Tailwind 工具类在同权重下会被 antd 覆盖（`<Button className="h-8">` 静默失效）；官方 FAQ 专设 `#cssinjs-tailwind-conflict` 条目并指向 issue #38794。官方解法要求 antd 开 `<StyleProvider layer>` 并按 Tailwind 版本排 `@layer`：v4 用 `@layer theme, base, antd, components, utilities;`，v3 需 `@layer tailwind-base, antd;` 包住 `@tailwind base`。本仓是 Tailwind 3.4.19，v3 的 `@layer` 是构建期指令、产物无原生级联层，走官方配方实际要连带动 Tailwind 4 或接受 `:where`/`!important` 兜底——这笔改造与"统一视觉"的目标无关，纯属引入成本。
2. **主题会变双轨**。现有主题是"CSS 变量 → Tailwind 语义色名 → `.dark` 翻转"一条链；antd 的 Seed/Map/Alias Token 由 JS 色板算法派生，不能直接吃 `var(--primary)`，74 个变量 × 深浅两套要在 `ConfigProvider` 里重建，调色从此改两处。这与用户要消除的"不一致"相反。
3. **首屏与包体是负收益**。当前构建产物 JS 801KB 原始 / CSS 45KB；antd v6 完整包 1,446KB 原始 / 461KB gzip、48 个运行时依赖，取常用子集估算 +250–300KB gzip，首屏 JS 大致翻倍。v6 的 `zeroRuntime` 能把 CSS-in-JS 换成静态 CSS，但仍是另一套构建产物。
4. **清理不彻底**。全量换要删 `index.css`（250 行）、`tailwind.config.js`（80 行）、`postcss.config.js`、12 个基元文件、`cn()` 与 3 个依赖，改写 74 个 tsx 的 1941 处 className、重拍 8 张视觉快照——但 antd 不含图表组件，16 槽图表色板（ADR-040）、`--overlay/--tooltip-*/--code-*/--login-*` 与布局骨架仍要自维护，等于付全量成本、留半套自定义 CSS。
5. **痛点与组件库无关**。不一致来自"没有统一配方"，不是"配方不够多"。补齐 5 个基元 + 冻结任意值 + 一条 lint，成本约为全量迁移的 1/3，直接命中"下一个控件又长得不一样"。

## 备选与否决原因

- **C 全量换 antd（v6）**：否决。需先做 Tailwind 4 / `@layer` 迁移，再重建两套主题 token，再重写 1941 处 className 与 74 个文件、重拍全部视觉快照；25–40 人日、首屏 JS 翻倍、回滚只能整分支回退；且图表/布局自定义层删不干净。React 19 兼容本身不是障碍（v6 peer `react >= 18`，已原生支持；v5 需 `@ant-design/v5-patch-for-react-19`），但这只说明"能跑"，不改变成本结论。
- **B 混合：antd 只用于复杂表格/表单/日期/虚拟列表**：否决。改动最小的版本（如只上 DatePicker/Table）仍需解决层序与 token 双轨，且多个页面会长期并存两套视觉语言与两套主题源——恰好放大用户抱怨的问题。属"用新债还旧债"。
- **重新引入 shadcn/ui + Radix + TanStack Table**：否决。ADR-031 已按 as-built 否过一次；现状 12 个基元 + 25 个 lib 已覆盖交互骨架，重引入等于重做一遍基元并对齐 token，收益与 A 的方案重叠而成本更高。
- **只做"冻结任意值"、不补基元**：否决。不补 Select/Dropdown/Table，页面仍会退回原生元素并用内联类对齐外观，漂移源还在。

## 后果与影响

- 正面：无新增运行时依赖、无构建链改造、无首屏回退；token 单源不变；控件缺口被补上后，页面不再需要自写弹层/表格/下拉。
- 代价：6–9 人日的基元与收敛工作；19 个原生 select、9 个原生 table 换基元时有键盘/表单行为回归面，需要逐页验证；视觉快照会因统一尺寸而变更，需要人眼过一遍。
- 约束：`components/ui/*` 成为唯一控件来源；页面内联尺寸/字号类视为技术债，禁止新增；`index.css` 仍是 token 唯一事实源（ssot §9.2 的口径不变）。
- 部分被 ADR-063 修订：「不引 antd」「继续 Tailwind + 自有 token」不变；被改的是「补基元」的**方式**——5 个缺口控件改为从 shadcn registry 取源码 copy-in（Radix 底座），不再手写。
- 登记：本条与 ADR-063 已在 `docs/ssot.md` §13 索引登记（2026-11-14）。
- 若将来出现"必须用第三方组件库"的硬需求（如超大数据虚拟表格），按 B 的路径单点评估，并先验证 `@layer` 方案在本仓 Tailwind 版本下是否成立，再决定是否升级 Tailwind 4。

## 相关代码与文档

- 评估正文与可复现计数：[`../ui-stack.md`](../ui-stack.md)。
- token 与暗色：`web/src/index.css:5`（:root 74 条）、`:97`（.dark 61 条）、`:79-94` / `:154-169`（16 槽色板）、`:172`（base 层）、`:182`（html.dark）、`web/tailwind.config.js:3`（darkMode class）、`:54`（圆角三档）、`web/src/lib/theme.ts:25-30`、`web/index.html:9-28`。
- 基元：`web/src/components/ui/`（12 个；`button.tsx:21-25` 为尺寸阶梯）。
- 漂移样本：`web/src/pages/dashboard.tsx:783-810`；原生 `<select>`/`<table>` 分布见 `../ui-stack.md` §1.2。
- 相关 ADR：ADR-007（被 ADR-031 取代）、ADR-031（Tailwind + 手写 UI）、ADR-027（视觉系统 v2）、ADR-040（16 色板）、ADR-061（单源部署）、ADR-063（缺口控件取 shadcn 源码，部分修订本条「补基元」的方式）。
- 外部依据（查阅日期 2026-11-14）：antd FAQ `#cssinjs-tailwind-conflict` <https://ant.design/docs/react/faq#cssinjs-tailwind-conflict>；antd CSS Compatible（`@layer` 与 Tailwind v3/v4 层序）<https://ant.design/docs/react/compatible-style>；issue #38794 <https://github.com/ant-design/ant-design/issues/38794>；antd v6 迁移文档 <https://github.com/ant-design/ant-design/blob/master/docs/react/migration-v6.en-US.md>；<https://registry.npmjs.org/antd/6.7.0>；<https://bundlephobia.com/api/size?package=antd@6.7.0>。
