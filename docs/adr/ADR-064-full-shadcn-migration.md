# ADR-064 全量迁移到 shadcn（copy-in）+ Tailwind 4

---
status: accepted
date: 2026-11-14
supersedes: ADR-062, ADR-063
---

## 背景

三周里 UI 一致性缺陷反复出现，且每次修完又在新页面复发：19 处原生 `<select>`（11 个文件）、9 处原生 `<table>`（8 个文件）、4 套手写弹层，另有 1,941 处 `className=`、**660 处任意字号 `text-[Npx]`**（42 个文件；**只数 px 单位**）、**100 处任意尺寸 `h-[Npx]`/`w-[Npx]`**（28 个文件；2026-11-14 20:5x 实测，97/101 都是更早快照）（旧文档里的「117 处内联控件尺寸」数的是 `h-8/9/10/11`、`rounded-md/lg` 这类**具名**类，与这 100 处不是同一个计数，见 `ui-stack.md`；本清单以 lint 实测为准）。盘点结论（[`docs/archive/ui-inventory.md`](../archive/ui-inventory.md):10）：**68 个用户可见控件/交互模式，11 个完全由 `components/ui/` 覆盖、18 个部分覆盖、39 个没有对应基元**。

自建基元本身的维护成本也不低：`components/ui/` 12 个文件共 **1,246 行**，全量读一遍约 12–15k tokens，且没有官方 agent 资料；每次改控件都要先读私有源码。

前两条决策都是**增量修补**：ADR-062 选「不引 antd，补 5 个自建基元」，ADR-063 进一步把 6 个缺口件改成 shadcn registry copy-in（但仍停在 Tailwind 3.4）。ADR-063 自己已经写下边界：2026 年新增的 shadcn 组件只发 Tailwind v4 风格，「用新组件前必须先升 4」——这就是半量方案的固有天花板：缺口件换完了，半覆盖控件仍各写各的。

**这是一条第二次回到 shadcn 的路**，留档防误解：ADR-007 早期选过 shadcn + TanStack Table，ADR-031 在 2026-11-12 文档压缩时按「as-built」把前端记成 Tailwind + 手写 UI。ADR-063 已考证清楚：ADR-031 是**回填占位**，背景/理由/备选三节都写「压缩表没有记录，不补写没有出处的内容」，它不是论证「不该用 shadcn」的选型结论。因此本条不是摇摆，而是把一次**没有论证过的转向**改回来，并且目标也变了——当时是让文档追认既有代码，现在要消除「同一控件各页不同」的缺陷。以后再有人问「要不要换组件库」，先按 ADR-007 → ADR-031 → ADR-062 → ADR-063 → 本条 这条链读完再提。

用户因此拍板（原话）：「直接全换成 shadcn 就好了一次性搞定，我已经受够了动不动发现 UI 一致性的问题」「全换了然后我们搞我们的设计、美化、交互规范」。

## 决策

**控件层全量迁移到 shadcn copy-in（Radix 底座），同时把 Tailwind 3.4.19 升到 4.x；设计规范仍在本仓自建。** 具体：

1. **全量而非缺口**：所有交互控件统一走 shadcn 源码 copy-in，12 个自建基元按「行为等价 → 替换 → 删除」退场，页面不得再出现第二套控件实现。初始化与取件显式带 `-b radix`（2026-07-02 起 CLI 默认 Base UI，不能吃默认值）。
2. **Tailwind 4 同步升级**：CSS-first `@theme`、`@tailwindcss/vite`（接入包裁定见 [`archive/tailwind4-migration.md`](../archive/tailwind4-migration.md) §1.2；`postcss.config.js` 是**删除**，不是把内容换成 postcss 插件写法）、`index.css` 的 74 条浅色 + 61 条深色变量与 `layer` 结构调整——不升就只能吃 v3 风格旧件，等于把 ADR-063 的半步再走一遍。
3. **设计规范不外包**：颜色、圆角、尺寸阶梯、暗色策略、16 槽图表色板（ADR-040）仍由本仓单点定义（`index.css` + `tailwind.config.js`，迁移后进 `theme`）；shadcn 只是控件实现来源。用户要的「搞我们的设计、美化、交互规范」= [`docs/ui-standards.md`](../ui-standards.md) 继续做唯一规范，并新增「现状约束」一节把修修补补得来的规则固化。
4. **分期落地**（逐控件台账记在 [`archive/ui-migration-map.md`](../archive/ui-migration-map.md)，升级手册为 [`archive/tailwind4-migration.md`](../archive/tailwind4-migration.md)）：阶段 0 决策与文档 → 阶段 1 Tailwind 4 + 主题别名层 + 一个试点页 → 阶段 2 高频控件（table / select / dialog / dropdown / tooltip / tabs）→ 阶段 3 其余页面与死代码删除 → 阶段 4 防护（`lint:ui` 禁原生 select/table、`/dev/ui` 画廊、视觉基线重拍）。
5. **明确不自建**：图表、业务组合控件、布局骨架继续自有实现，shadcn 不提供也不该硬套。

## 理由

1. **只有全量能消掉「配方没有单一来源」这个根因**。ADR-063 换完 6 类缺口件后，18 个「部分覆盖」的控件仍然各页各写；一致性缺陷正是从这里长出来的。
2. **a11y 从「自己实现」变成「继承 Radix」**。现有 `components/ui/modal.tsx:41-43` 只有 `role=dialog` / `aria-modal` / `aria-labelledby`，没有焦点陷阱、没有初始焦点、关闭后不归还焦点；19 处原生 select 与 4 套弹层行为互不相同（ADR-063 理由 1 的证据继续成立）。
3. **token 成本**：copy-in 源码是模型高频先验，registry 有 MCP / skills / `shadcn info --json`，单次查证 0.5–2k tokens；自建路径每次都要读 12–15k tokens 的私有实现。控件越换越多，这个差价越大。
4. **退出成本低**：源码进仓库、无运行时框架、删文件 + 卸依赖即可回退；与 antd 的层序冲突 + 主题双轨形成明确对照（ADR-062 理由 1–4，事实不变）。
5. **Tailwind 4 顺带清三笔旧账**：原生级联层（不再靠 `:where` 降权）、`theme` 让 token 单点更硬、构建更快；并且解除「新组件受版本卡住」这个反复出现的阻塞。

## 备选与否决原因

- **继续增量修补（ADR-063 路径：只换 6 类缺口件、Tailwind 不动）**：否决——治标。三周内同一类缺陷复发就是证据；39 个无基元控件它只覆盖 6 类，剩下的依然手写。ADR-063 的「范围最小化」在当时是对的（用最小代价验证 shadcn 可用），但它不能回答「为什么页面之间仍然长得不一样」。
- **只升 Tailwind 4、不换控件**：否决。升级成本照付，配方分散一点没解决，等于把旧债换成更贵的债。
- **换 antd**：ADR-062 已否决，理由不变（层序冲突、主题双轨、整包 1.4 MB、25–40 人日）；本次决定不改变其中任何一条事实。
- **换 MUI / Radix Themes / Base UI 等运行时组件库**：否决。运行时主题与 Tailwind 工具类继续打架，且丢掉 copy-in 的退出成本优势；Radix 原语由 shadcn 承载，将来若换底座只需重装组件、不动调用方（ADR-063 已留此判断）。
- **一次性重写全部页面**：否决。无法分阶段验证、无法分级回滚，视觉回归会退化成「一次性重拍、一次性蒙对」。
- **保留自建基元、只引入 shadcn 新组件**：否决。两套实现长期并存正是当前缺陷的来源；要么统一走一套，要么永远在两头对齐。

## 后果与影响

- **工作量**：Tailwind 4 升级单独估 1.5–2.5 人日（原记在 ssot §15）；控件全量迁移按阶段累计，每阶段独立可验，合计显著高于 ADR-063 的 6–9 人日——这是「一次性搞定」的代价，换来的是不再有第二轮。
- **回滚点**：阶段 1 结束前可整体回退（只新增依赖与别名层，未动页面）；阶段 2 起按控件回滚——旧基元文件保留到该控件在**所有**页面替换完毕才删除，中途回退只需还原页面引用。
- **Tailwind 3→4 连带影响**：`@tailwind` 指令改为 `import "tailwindcss"`；`tailwind.config.js` 的 theme 迁到 CSS `theme`；插件与 safelist 语法变化；任意值写法仍可用但要继续收敛；`index.css` 的 74+61 条变量改挂 `theme`；暗色仍走 class 策略（`tailwind.config.js:3`、`lib/theme.ts:25-30`、`index.html:9-28` 内联防闪不变）。
- **清单计数口径（2026-11-14 20:5x 实跑）**：`cd web && node scripts/lint-ui.mjs --json` —— 零依赖脚本，扫 `src` 下 **83 个**非测试 tsx（跳过 `__tests__`、`*.test.tsx`、`*.spec.tsx`、`node_modules`/`dist`/`build`）；`filesScanned=83`、`totals: {errors: 0, warnings: 834}`、`ok: true`、`result: PASS`、exit 0。
  - 规则明细：`no-arbitrary-font-size`=**660**（42 文件）、`no-arbitrary-size`=**100**（28 文件，`h-[Npx]`/`w-[Npx]`）、`no-title-only-tooltip`=**27**（14 文件，可交互元素上的 `title=`，是 115 处 `title=` 的子集口径（与 `archive/ui-migration-map.md` §3.2 的可访问名统计口径不同，别混用））、`no-native-select`=**19**（11 文件）、`no-native-table`=**9**（8 文件）、`no-native-checkbox`=**12**（7 文件）、`no-native-radio`=**2**（2 文件）、`no-native-dialog`=**0**（原 `window.confirm` 已由 t2 换成 `components/ui/confirm-dialog.tsx`）、`no-inline-hex`=**0**、**`no-dark-prefix`=0 error**（5 处 vendored `dark:` 按目录分档只计 warning）。834 = 19 + 9 + 660 + 100 + 27 + 12 + 2 + 5。
  - **count 是出现次数**（脚本 `--json` 的 `count` 即 Matches 累加）：字号规则 `\btext-\[\d+(?:\.\d+)?px\]` = **660 次 = 660 行**（每行恰 1 处）；尺寸规则 `\b[hw]-\[\d+(?:\.\d+)?px\]` = **100 次 / 94 行**（28 文件）；字面 `text-[` = **691 次**（多 31 次 = rem / var / 颜色等非字号写法）。按行数核字号规则能对上，核尺寸规则差 6，核 `text-[` 字面量一定对不上。
  - **`no-dark-prefix` 的分档是定案**（lead 裁定，已落地）：`relax: { dir: "src/components/ui/", level: "warn" }` —— 应用代码里仍是 error，vendored 目录内降为 warning、计入 warning 总数、不计入 error、不影响 `--max-errors`；理由是 `web/src/index.css:6` 的 `@custom-variant dark (&:where(.dark, .dark *))` 本来就是类策略，vendored 里的 `dark:` 不会把暗色机制换成 `prefers-color-scheme`，相抵的只是那条规则当初写得太宽，上游更新 vendored 源码时也不必手改。（2026-11-14 20:5x 起 error 级 0 命中，那 5 处 relaxed warn 计入 warning 总数，是 §7.6 阶梯的**地板 5**。）
  - **数字是移动靶**：并行批次一直在改 `src`，同日复跑已多次漂移（20:2x = 672 处 / 55 个 tsx / 1 error / 839 warning；20:3x = 672 处 / 82 个 tsx / 6 error / 843 warning；20:4x = 672 处 / 82 个 tsx / 1 error / 848 warning——`no-dark-prefix` 分档后那 5 处 `dark:` 从 error 转 warning；20:5x = **660 处 / 83 个 tsx / 0 error / 834 warning、`result: PASS`**；21:2x = 批 3 落地中（原生 select 19→4、Modal 15→1、SearchSelect 归零，字号/尺寸未清完）——t2 移除 `window.confirm` 后 error 清零）。
  - **权威来源是命令本身**：本 bullet 的记录值只对应一次具体实跑，每批开工前重测再锚定，不要拿这里的数字当验收基线；旧文档里的 625 是历史数据。
- **视觉基线**：`web/e2e/__screenshots__` 的 **7 张**快照（目录下 7 个 png，对应 `e2e/visual.spec.ts` 的 7 个 test）。验收分两段：**批 0 必须 0 差异**（证明升级本身没动视觉，有差异先查类名清扫 `3.5` 而不是直接重拍）；**批 4 重拍后逐张人眼过**——`npm run test:visual` 的语义从「确认没变」变成「确认变对了」，这是迁移期最容易被误判为回归的地方。
- **依赖**：新增若干 `@radix-ui/react-*`（按需，去重后 gzip 量级低于一次性引整包）；`clsx` / `tailwind-merge` / `cva` 保持不变；不引入运行时 UI 框架。
- **风险与护栏**：迁移期 UI 会短暂不一致（分阶段必然）；`web/scripts/lint-ui.mjs` 已落地并含「禁原生 select/table」「禁任意值」两条规则，但当前是 warn 级（实测 **0 error / 834 warning**、`result: PASS`、exit 0，`filesScanned=83`）：error 级规则已全部清零（`window.confirm` 由 t2 换成 `components/ui/confirm-dialog.tsx`）；5 处 `dark:` 前缀（`components/ui/input-group.tsx:17/30/139/155`、`components/ui/field.tsx:120`）按目录分档已降为 warning。**`dark:` 定案**：`no-dark-prefix` 按目录分档（`relax: { dir: "src/components/ui/", level: "warn" }`：应用代码 error、vendored 目录 warn），**不改上游 vendored 源码**——`web/src/index.css:6` 的 `@custom-variant dark (&:where(.dark, .dark *))` 本来就是类策略，vendored 里的 `dark:` 不会把暗色机制换成 `prefers-color-scheme`，相抵的只是那条规则当初写得太宽。必须按 `ui-migration-map.md` §7.6 预算阶梯把 `--max-warnings` 收紧到**地板 19**（= vendored 字号 10 + 盒尺寸 4 + `dark:` 5，全在 relaxed 桶，不列待清量；批 4 曾按「地板 5」写过一版预算，被 vendored 字号/尺寸的 14 处实测推翻），再挂 CI 阻断，否则迁完还会退化回今天的形态。
- **文档连锁**：ADR-062 / ADR-063 标 superseded；ssot §9 决策链、§13 索引、§15 待办同步；新增 `tailwind4-migration.md`（升级手册）与 `ui-migration-map.md`（逐控件台账）；`ui-standards.md` 增补「现状约束」章节。

## 相关代码与文档

- 入口配置：`web/package.json`（tailwindcss 3.4.19 → 4.x，新增 `@tailwindcss/vite`；`postcss.config.js` 删除，不保留 postcss 路线）、`web/tailwind.config.js`（`:3` darkMode class、`:54` 圆角三档）、`web/src/index.css`（74 浅色 + 61 深色变量、`layer` 结构）
- 主题与暗色：`web/src/lib/theme.ts:25-30`、`web/index.html:9-28`
- 待退场基元：`web/src/components/ui/`（12 个文件、1,246 行）
- 典型缺陷锚点：`web/src/pages/dashboard.tsx:783-810`（手写分段控件 + 原生 select 与 `components/ui/button.tsx:21-25` 尺寸阶梯不一致）
- 证据与评估：[`docs/archive/ui-inventory.md`](../archive/ui-inventory.md)（68 / 11 / 18 / 39）、[`docs/archive/ui-options.md`](../archive/ui-options.md)（三方对比与分期）、[`docs/ui-stack.md`](../ui-stack.md)（antd 成本）、[`docs/ui-standards.md`](../ui-standards.md)（规范正文）
- 被取代：ADR-062（不换 antd、自建基元）、ADR-063（只换 6 类缺口件）

## 落地结果（2026-11-14 收口）

五个批次全部落地，`lint:ui` 的例外数就是这条 ADR 的验收线：

| 门禁 | 命令 | 实测 |
|---|---|---|
| 类型 + 构建 | `npm run build`（= `tsc --noEmit && vite build`） | `✓ built in 24.71s`，exit 0；dist 全部 chunk 低于 esbuild 1 MiB 阈值 |
| lint | `npm run lint:ui`（= `node scripts/lint-ui.mjs --max-warnings 19`） | **81 文件 / 0 error / 19 warning / PASS**；19 = vendored 字号 10 + 盒尺寸 4 + `dark:` 5 |
| 单元 | `npx vitest run` | **27 test files / 214 tests passed** |
| 视觉 | `npm run test:visual` | **7 passed**；重拍基线后复跑 0 差异（`maxDiffPixelRatio: 0.002`） |

落地顺序：批 0 token 与工具（`index.css` 四档字阶 `text-2xs/2sm/metric/h1` + `--width-sidebar`，`dialog`/`sheet` 增 `showCloseButton`）→ 批 1 十个基元并入 shadcn 底座 → 批 2 表单控件 → 批 3 页面与面板（Select / Table / Checkbox / RadioGroup / Dialog / Sheet / Tabs / Field，新增 `ui/combobox.tsx`）→ 批 4 任意值清扫（applied 171 / skippedVendored 31，行高 −458.62px）→ 批 5 收尾（sonner 接管 toast、SELECT_ALL/NONE 哨兵单点、删 `ui/toast.tsx`、`ui/search-select.tsx`、`ui/modal.tsx`、`lib/use-body-scroll-lock.ts`）。

**三条实测教训（都写进 `ui-adoption-map.md` §10 了）**：

1. **视觉套件曾经在测生产构建**：`playwright.config.ts` 原指 3680，而 3680 是服务 `dist` 的网关，`reuseExistingServer` 让 Playwright 直接复用它，于是工作树里的改动（例如给 popover 新加的 `aria-label`）永远进不了浏览器。现已改为自带 vite on **3690** 且 `reuseExistingServer: false`。
2. **jsdom 缺 Radix 依赖的四个 API**（`ResizeObserver`、pointer capture、`scrollIntoView`、`matchMedia`），缺任何一个都会让含 Select/Popover 的用例首屏即抛；`src/test/setup.ts` 统一补齐。
3. **测试契约随控件换代**：筛选触发器 role 是 combobox、行展开的 `aria-expanded` 挂在按钮而不是 `tr`、`AlertDialogDescription` 是一整段所以断言用正则。这三处是语义变化，不是放水。

**边界（不由 ADR 承担）**：真实 `.env` 的核对、网关重启（含上游传输切 httpx）与视觉的主观确认仍需人工执行；沙箱解除前的「跑不了 build」不再是限制。

