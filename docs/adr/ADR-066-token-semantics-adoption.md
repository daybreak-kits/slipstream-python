# ADR-066 shadcn token 语义与组件底座的对齐方式

---
status: accepted
date: 2026-11-14
supersedes: 无（在 ADR-064 的框架内补齐执行期被迫做出的选择）
related: ADR-031（补注：依赖集主动排除 @tanstack/react-table）
---

## 背景

ADR-064 定了「控件层全量 copy-in shadcn + Tailwind 4」，但它只讲到策略（全量、分期、设计规范自留），没回答落地当天就会撞上的几个问题：**token 语义怎么对齐**、**取哪一版 registry 源码、拿到的源码还算不算同一份**、**哪些件不取、哪些依赖不装**。这些问题在批 1 开工时各以「先照着改，跑通再说」的方式被解决了一次；只留在代码里，下一批会重做同样的选择，而且很可能做出相反的那个。本条把已落地的选择写死，并记录每条选择的代价。

## 决策

1. **`--color-muted` 指「弱表面」，不指文字。** `index.css:63` 是 `--color-muted: var(--surface-2)`，文字层是 `--color-muted-foreground: var(--muted-foreground)`（`index.css:99`，源头 `--text-muted`，`index.css:116`）。理由：vendored 代码里 `bg-muted` 是表面（悬停底、次级块），`text-muted-foreground` 才是文字；语义若不翻转，10 处 `bg-muted` 会渲染成深灰块。扩展名 `--color-title/--color-body/--color-faint` 保留（`index.css:61-64`）。
2. **圆角补一档 shadcn 标尺。** `--radius-sm 6 / md 8 / lg 12 / xl 16`（`index.css:20-23`）与语义档 `--radius-badge 6 / --radius-control 8 / --radius-card 12`（`index.css:14-16`）同值并存：vendored 件写 `rounded-md` 拿到的就是 control 档，不必逐件改类名；`xl` 是新增第四档，只给浮层内嵌容器。
3. **取 new-york（v3）源码，不取 new-york-v4；取到的源码视为冻结件。** v4 依赖统一 `radix-ui` 包与 `cn` 包，本仓 `cn()` 在 `src/lib/utils.ts:4`、Radix 是 19 个单包（`package.json:17-35`）。**registry 会漂移**：同一个 URL 现在可能返回 v4 风格的新 payload，所以任何再取件都必须过一道校验——payload 里出现 `from "cn"` 或 `from "radix-ui"` 一律拒收（本仓的件固定 `cn` 来自 `@/lib/utils`、`Slot` 来自 `@radix-ui/react-slot`）。代价是 v3 源码按 Tailwind 3.4 写成，`outline-none`/`shadow-sm` 在 v4 语义变了（`outline-hidden`/`shadow-xs`），只能逐件回贴——21:0x 复核：`outline-hidden` 47 处、`outline-none` 0 处（回贴已完成），`shadow-sm` 0 处，`shadow-xs` 13 处成为新的未登记档。
4. **主题状态机只有一个，留在 `src/lib/theme.ts`。** `sonner.tsx:5` 直接取 `useThemeMode()`，不引 next-themes（上游 Toaster 默认用它）；`sonner.tsx:9-13` 的注释写明理由——避免两套 system/light/dark 判断互相打架。
5. **`dark:` 在 `components/ui/` 内合法，应用层禁止。** `@custom-variant dark (&:where(.dark, .dark *))`（`index.css:6`）本身就是 class 策略，所以 vendored 件里的 `dark:` **不是机制偷换**——它和我们的深色实现走同一条 `.dark` 链。`scripts/lint-ui.mjs:110-117` 的 `no-dark-prefix` 因此分域：应用代码 error，`src/components/ui/**` 内 warn。现状 5 处全在 ui（`field.tsx:120`、`input-group.tsx:17/30/139/155`），应用层 0 处。
6. **清单：`src/components/ui/` 共 43 个 .tsx = A 类 27 个 shadcn registry 源码（new-york v3 风格）+ B 类 10 个本仓自有、正合并到 shadcn 基座（button/badge/card/input/alert/label/pagination/skeleton/spinner/empty）+ C 类 6 个本仓自有、保留或待替换（icon-badge/metric-pills/modal/search-select/time-range-picker/toast）。** 只取用得上的，不整包 vendor。（21:0x 复核：目录另有批 2 落地的 `confirm-dialog.tsx` 与批 2 的测试文件 `__picker_check.test.tsx`，实际 44 个组件 + 1 个测试文件。）
   - A 类落到页面的时机（`archive/ui-migration-map.md` 的批列）：批 2 用 `select`/`checkbox`/`radio-group`/`switch`/`textarea`/`field`/`input-group`/`toggle-group`；批 3 用 `dialog`（删 `modal.tsx`）/`alert-dialog`（删 `window.confirm`）/`sheet`/`dropdown-menu`/`command`/`popover`/`collapsible`/`tabs`/`sonner`（删 `toast.tsx`）/calendar；批 4 用 `table`/`tooltip`/`breadcrumb`/`progress`/`scroll-area`/`pagination`。
   - C 类里 `modal`/`toast`/`search-select` 是**临时**自建，批 3 删除；`icon-badge`/`metric-pills`/`time-range-picker` 长期自建。
   - **不取**（台账对应行给的是「保留自建」）：charts（Recharts 封装，新增运行时依赖且无收益，`#60`；我们的图是手写 SVG + 16 槽色板）、TanStack Table（`table` 只做样式层，9 处表格够不到排序/虚拟化，`#29`）、sidebar（自带 Provider 与折叠状态机，与 `app-shell` 受控折叠冲突，`#45`）、drawer（移动端底部抽屉由 `sheet` 覆盖，`#50/#55`）、carousel / menubar / navigation-menu / aspect-ratio / context-menu / hover-card / kbd / item / slider / resizable / input-otp / accordion / button-group（全部 0 使用点；折叠面板用 `collapsible`，分段选择用 `toggle-group`）。
7. **依赖只装用得上的：full install 拉进来但用不到的，随后被 prune。** 保留 19 个 `@radix-ui/react-*` 单包（只对应 A 类件）、`cmdk`、`sonner`、`react-day-picker` + `date-fns`（日历）、`cva`/`clsx`/`tailwind-merge`。**被 prune 的**：`@tanstack/react-table`（表格只取 shadcn 的样式层，排序/筛选是另立的未来项）、`recharts`（shadcn chart 层）、`embla-carousel-react`（carousel）、`vaul`（drawer）、`@radix-ui/react-accordion`/`aspect-ratio`/`context-menu`/`hover-card`/`menubar`/`navigation-menu`/`slider`、`input-otp` 与 `@radix-ui/react-one-time-password-field`、`react-resizable-panels`（resizable）——它们对应的件不在 27 件 A 类里，留着就是「看着能用、其实没人调用」的依赖。
8. **色板顺序问题登记为未决项，不在本条解决。** `chart-colors.ts:22-28` 先 `filter(requests > 0)` 再按 `index % 16` 取槽，某系列掉到 0 会让后面整体前移，与「蓝色 = A 模型」的记忆冲突；ADR-064 决策 5 已定图表继续自有实现，修法（按稳定键分配槽位：首次出现顺序或模型 id 哈希）挂在 `design-system.md` §11 #7。

## token 映射（批 4 清扫脚本的唯一依据）

批 4 要把 `no-arbitrary-font-size` 与 `no-arbitrary-size` 两条 warn 清零（21:0x 实测：`text-[…px]` 660 处、`[hw]-[…px]` 98 处，合计约 758，是剩余 801 条 warning 的绝大部分）。本节是脚本的唯一依据：脚本不判断语义，只按下表替换；表里没有的形态一律不动并在报告里列出来。

### 1. 字号档：新增 4 档，内置档不改

`@theme` 新增（值只出现在 `index.css`）:

| token | 类名 | 尺寸 / 行高 | 用途 |
| --- | --- | --- | --- |
| `--text-2xs` | `text-2xs` | 11px / 16px | 徽标、表头、计数、坐标轴、meta |
| `--text-2sm` | `text-2sm` | 13px / 20px | **正文默认档**：正文、表格正文、段落 |
| `--text-metric` | `text-metric` | 19px / 26px | 指标数字（dashboard 两处） |
| `--text-h1` | `text-h1` | 22px / 28px | 页面主标题 |

内置档不改（尺寸与行高都取 Tailwind v4 默认）：`text-xs` 12/16、`text-sm` 14/20、`text-base` 16/24、`text-lg` 18/28、`text-xl` 20/28、`text-2xl` 28/36、`text-4xl` 36/40。**据此修正两处预期**：强调档是 `text-sm` = 14px/**20px**（不是 14/22）；12px 档是 12px/**16px**（不是 12/18）。`design-system.md` §2.1 与 §11 #1 原写的 18/22 已按本表改正——理由是下面「只加档、不覆盖内置」。

映射表（一对一，脚本照抄）：9.5 / 10 / 10.5 / 11 / 11.5 → `text-2xs`；12 / 12.5 → `text-xs`；13 / 13.5 → `text-2sm`；14 / 15 → `text-sm`；16 → `text-base`；18 → `text-lg`；19 → `text-metric`；20 → `text-xl`；22 → `text-h1`；28 → `text-2xl`；40 → `text-4xl`。

判定句：**取「不大于原值」的最大档；原值低于最小档（11）时归最小档。** 半像素档（10.5 / 11.5 / 12.5 / 13.5，共 254 处）按同一句消掉，不留 `text-[Npx]` 残项；`15px` 的弹窗标题档并入强调档（-1px）；`40px` 的登录页标题并入 `text-4xl`（36px，-4px，登录页本就是域外视觉）。

已知视觉 delta（**有意，不是回归**）：今天这些文字继承 `body` 的 `line-height: 1.65`（`index.css:309-318`，14px/1.65，unitless 所以按各自字号换算），替换后每档拿到固定行高——11px 18.15→16、12px 19.8→16、13px 21.45→20、14px 23.1→20。批 4 是一次**密度收敛**：全局收紧 1–4px/行，换来「行高只由档位决定」。某个多行段落确实需要更松的行距时，在该处显式写 `leading-*`，不要改档位。

为什么只加档、不覆盖内置的 `--text-xs--line-height` / `--text-sm--line-height`：一是 `components/ui/` 有 50 处 `text-xs`/`text-sm`，跑的是 Tailwind 默认度量，覆盖后按钮、菜单项、输入框的换行与内高会一起动，而**这些件是冻结件、我们不能改**（决策 3）；二是尺子只能有一个主人——内置档的尺寸与行高由上游维护，本仓只加自己需要的两档，将来再取件不会出现「上游的 sm 与我们的 sm 不同高」。

正文默认档 = `text-2sm`（13/20），强调档 = `text-sm`（14/20）。`body` 的 `font-size: 14px; line-height: 1.65` 本期**不动**：改根会波及所有未显式声明字号的文本，属单独一条；触发条件是批 4 落地、页面字号全部显式化之后，届时再单独立条把根改成 13px/20px。

### 2. 任意宽高：全部换成 spacing 类，只留一个布局 token

**S1（覆盖 98 处里的 89 处）**：像素值是 4 的倍数 ⇒ 换成 Tailwind v4 动态 spacing 类，数值 = px ÷ 4。例：`h-[240px]`→`h-60`、`min-h-[180px]`→`min-h-45`、`w-[96px]`→`w-24`、`max-h-[420px]`→`max-h-105`、`min-w-[720px]`→`min-w-180`、`min-h-[70px]`→`min-h-17.5`。**六条已实测**（`web/scripts/tw-probe.mjs`，纯 `tailwindcss` 的 `compile()` API，不起 vite、不读网络）：h-60=240、w-24=96、min-w-180=720、max-h-105=420、min-h-45=180、min-h-17.5=70，全部生成 CSS。这是**零视觉变化**的机械替换，前提两条且都是现状：`--spacing` 未被改写（`index.css` 无 `--spacing` 定义，v4 默认 0.25rem）；根字号 16px（`index.css:300-303` 只设 `-webkit-text-size-adjust`/`color-scheme`，没改 `html` 的 `font-size`）。批 4 开工前脚本先断言这两条，不成立就中止。

**S2（非 4 倍数，实测 7 处，逐条给目标，不留任意值）**：`h-[15px]`/`w-[15px]`（`sidebar.tsx:97`）→ `size-4`（16，+1）；`w-[3px]`（`sidebar.tsx:103`）→ `w-0.5`（2，-1）；`w-[2px]`（`playground.tsx:486`）→ `w-0.5`（2，0）；`h-[1px]`/`w-[1px]`（`separator.tsx:22`）→ `h-px`/`w-px`（1，0）；`max-w-[130px]`（`rewrite-rules-editor.tsx:1169/1172`）→ `max-w-32`（128，-2）；`min-w-[190px]`（`catalog.tsx:259`）→ `min-w-48`（192，+2）；`h-[18px]`/`w-[18px]` → `size-4.5`（18，0；tw-probe 实测 `.size-4\.5` 生成 `1.125rem` = 18px，动态小数刻度成立，**不用回退 16**）。

**S3（唯一的具名 token）**：`--width-sidebar: 248px`（`@theme` 内，`index.css:39-41`）→ 类名 `w-sidebar`，用在 `app-shell.tsx:56` 与 `sidebar.tsx:18`（折叠态 72 走 S1 → `w-18`）。它被两处共享、与折叠态成对，是布局契约而不是刻度值，所以具名。

**命名空间是实测结论，不要写成 `--w-sidebar`**：Tailwind 4 没有 `--w-*` 主题命名空间——`--w-sidebar: 248px` 无论写在 `@theme` 还是 `:root` 都不生成任何规则（tw-probe Q2 的三臂 A/B/C 全部 0 规则）；能生成 `.w-sidebar{width:248px}` 的只有 `--width-sidebar`，且必须放在 `@theme` 内（放 `:root` 只得到一个普通自定义属性，不生成类）。

**S4（不建 token）**：表格的 `min-w-[…]`（720/780/820/640/980/1080/1160/1920 与 52/112/132/180/220）全部走 S1（`min-w-180` 等）。每张表的最小宽由列数决定，一旦具名就会被下一张表当共享刻度抄走。**否决「为列宽造 token」（如 `--w-column-md`）**：现有 98 处里没有一处是同一列宽被两张以上的表复用，造 token 等于凭空发明刻度。

**S5（禁止误用）**：S1 的通用 spacing 类**不得**用于控件高度。控件高度只有一条阶梯：`h-7`(28) / `h-8`(32) / `h-9`(36) / `h-10`(40) / `h-11`(44) / `h-12`(48)（`design-system.md` §1.6；`--control-*` 待补见 §11 #2），不许出现 `h-8.5` 这类「顺手取整」。

其中 **`h-7` 是 dense 档（2026-11-14 补）**：只允许用在工具条与密集表格内的控件，现状 10 处——`channel-batch-toolbar.tsx:36/51/66/81/92` 的 Button、`channels.tsx:476` 的 input 与 `:533/544/555` 的 Button、`ui/input.tsx:17` 的 input 默认高度。**清扫脚本不得把它们抬到 `h-8`**：那是视觉变更，属另立一条，不在批 4。

### 3. 圆角：两套都留，清扫不碰任何 radius 类

- 语义档（回答「这是什么」）：`rounded-badge` 6 / `rounded-control` 8 / `rounded-card` 12（`index.css:14-16`）。
- 标尺档（回答「多大」）：`rounded-sm` 6 / `rounded-md` 8 / `rounded-lg` 12 / `rounded-xl` 16（`index.css:20-23`）。同值是设计（决策 2）：vendored 件写 `rounded-md` 拿到的就是 control 档。
- 清扫约束：① `rounded-md` 与 `rounded-control` **互不替换**（同值，改了只有 diff 噪音，且 vendored 件是冻结件）；② `rounded-[Npx]` **不是批 4 的范围**——`lint-ui.mjs` 没有圆角规则，不产生 warning；现有 6 处（`dashboard.tsx:656/668` 的 2px、`:807` 的 7px、`:953/971` 的 6px、`channel-models-panel.tsx:267` 的 6px）登记在 `design-system.md` §11 #14，与热力图/图表骨架一起定；③ 新增圆角只能用上面两套之一，不写任意值。

### 4. 脚本执行顺序与验收

① 先改 `index.css`：加 4 个字号档 + `--width-sidebar: 248px`（已由 lead 落地，见 S3；一次提交，之后不再动 `@theme`）；② 字号替换，`rg -o 'text-\[[0-9.]+px\]' src | wc -l` 归零；③ 尺寸替换，同上归零；④ 门禁：`tsc --noEmit` 0 error、`node scripts/lint-ui.mjs` 的 `no-arbitrary-font-size` 与 `no-arbitrary-size` 均 0 命中，`no-inline-hex`/`no-native-dialog`/`no-dark-prefix`（应用层）仍 0 error。脚本必须**逐条报告 delta**（S2 的 ±px 与字号档的行高变化），不许静默取整。

## 批 4 实测结果

**执行**：`node web/scripts/sweep-arbitrary-sizes.mjs --apply --include-protected`（**未**开 `--include-vendored`）。applied=171、skippedVendored=31、skippedProtected=0，逐条报告在 `.scratch/apply-20261114T133910.json`。

**字号收敛**：受影响 **174 行**，行高合计 delta **−458.62px**（相对原先继承 `body` 的 1.65 行距）：

| 目标档 | 处数 | 行高 delta 合计 |
|---|---:|---:|
| `text-2xs`（11/16） | 88 | −150.56px |
| `text-xs`（12/16） | 47 | −193.54px |
| `text-2sm`（13/20） | 24 | −38.12px |
| `text-sm`（14/20） | 9 | −32.85px |
| `text-xl`（20/28） | 1 | −5px |
| `text-h1`（22/28） | 4 | −33.2px |
| `text-metric`（19/26） | 1 | −5.35px |
| **合计** | **174** | **−458.62px** |

**尺寸**：89 处按 px÷4 换成动态 spacing；8 处非 4 倍数按 §2 S2 的既定目标落地（`h/w-[15px]`→`size-4`、`h/w-[18px]`→`size-4.5`、`min-h-[70px]`→`min-h-17.5`、`h/w-[1px]`→`px`、`w-[248px]`→`w-sidebar` 等）。两条 UNSURE 全部结案、均无回退：`size-4.5`=18px 与 `min-h-17.5`=70px 都经 `scripts/tw-probe.mjs` 真编译确认会生成。

**门禁**：`tsc --noEmit` EXIT 0；`node web/scripts/lint-ui.mjs` 从 **0 error / 225 warning → 0 error / 53 warning**（降 172）。apply 之后再跑一次 dry-run，`ok=31` 且 31 条全部是 `skippedVendored`——即清单之外没有漏项。

**残留清单（31 处、12 个 vendored 件，本批不动）**：`search-select` 8、`metric-pills` 4、`pagination` 4、`toast` 3、`alert` 2、`button` 2、`modal` 2、`separator` 2、`card` 1、`command` 1、`label` 1、`textarea` 1。逐件登记在 `design-system.md` §11 #16；`search-select` 与 `toast` 已排期删除，删件即销账。应用层（`pages/` 与非 `ui/` 组件）的 `text-[…px]` 已归零；剩余 25 处字号全部落在其中 9 个件里（`search-select` 7、`metric-pills` 4、`pagination` 4、`alert` 2、`button` 2、`modal` 2、`toast` 2、`card` 1、`label` 1）。

**UNPARSED 62 处的处置口径：保留原样，不猜值。** 分类——颜色 `var()` 27、`calc()` 12、`[--x]` 变量 10、百分比与 rem 6、rem 字号 4、Radix 变量 3。判定句：**只有当值能被证明等价于某个档位时才替换**；颜色/比例/rem 与 px 不同量纲，`calc()` 与 var 的取值要到运行时才知道，强行取整等于用「看起来差不多」改语义。它们不算技术债：lint 不报，也不存在第二个真源与它们冲突。

## 理由

1. **语义对齐只有一次机会。** token 名是公共接口：`--color-muted` 一经按 shadcn 语义固定，应用层 434 处 `text-muted` 就必须同时扫成 `text-muted-foreground`（`design-system.md` §11 #13 记该清扫）。晚一步就是让「浅灰字」和「浅灰底」共用一套类名，而这种错误不报错——只静默变浅或变深。
2. **取 v3 是「少两个依赖 vs 多一次机械回贴」的权衡**，且回贴可 grep（`outline-none`→`outline-hidden`、`shadow-sm`→`shadow-xs`）；冻结件 + 拒收校验把「上游漂移」变成一次显式的人工检查，而不是一次静默的取件。
3. **不整包 vendor、不整包装依赖，让「没有这个件」本身成为信息。** 取 43 个而不是 registry 全量，`components/ui` 的目录列表就是「我们真的用这些」；不取的件在台账里有逐条理由，避免下一次「顺手补一个」把图表也换掉。

## 备选与否决原因

- **取 new-york-v4 源码**：否决。要新增 `radix-ui`+`cn` 两个依赖并改 27 个文件的 import，收益只是上游省事，与本仓「token 单源 + 退出成本低」相反。
- **保留 `--color-muted` 给文字、另起 `--color-surface-muted` 给表面**：否决。shadcn 源码与模型先验写的都是 `bg-muted`；改名后每取一个新件都要改一处，语义对齐全丢。
- **引 next-themes 走 sonner 默认路径**：否决。两套主题状态机，`.dark` 会有两个设置方。
- **应用层也放开 `dark:`（不设 lint）**：否决。放开后「深色靠 token、不靠 dark: 分支」立刻失效，而这正是 74+61 条变量能保持单源的前提。
- **把 modal/toast/search-select 提前到本批删掉**：否决。15 个 `<Modal>` 使用点与 toast 的 role=alert 语义要一起改，属批 3 验收面；提前删会把两件事的回归混在一起。
- **保留 prune 掉的依赖（「装了说不定以后用」）：否决。** 未被调用的高级控件依赖会变成升级阻塞与审计噪音；真要用时按决策 6 单独取件。

## 后果与影响

- **一次性清扫成本**：`text-muted` 434 → 57 处（21:0x 实测，`text-muted-foreground` 已 377 处，余量集中在 `components/ui/`）；`components/ui` 内 `outline-none` 19 处 → `outline-hidden`、`shadow-sm` → `shadow-xs` 逐件回贴。验收：`tsc --noEmit` 为 0，`\btext-muted\b` 残留为 0。
- **语义翻转是 breaking 但静默**：`text-muted` 从文字色变成表面色，漏改的调用点渲染成一块浅灰底而不报错，只能靠 lint + 逐页视觉比对兜住。
- **依赖体积**：19 个 `@radix-ui/react-*` 单包 + `cmdk` + `sonner` + `react-day-picker` 10 + `date-fns` 4（`package.json:17-49`）。无运行时 UI 框架，退出仍是「删文件 + 卸依赖」。
- **取件纪律**：以后任何 shadcn 取件都要走决策 3 的校验（拒收 `from "cn"` / `from "radix-ui"`），并把新件登记进决策 6 的 A/B/C 分类，否则目录清单会再次与现实漂移。
- **历史条款订正**：`ADR-031` 的「不引 TanStack Table」今天读起来像「还没做」，实际是**主动排除**——本条已在 `ADR-031` 补一行说明；`ADR-007`（只用 shadcn/ui + TanStack Table）早已被 `ADR-031` 取代，两条合看才不误读。
- **文档连锁**：`design-system.md` §4.1/§5.1/§9.1 与 §11 已按本条改口径，并改为引用本条而不复制数值；`ui-standards.md` 仍记「12 文件 1246 行」，属该文档维护范围。

## 相关代码与文档

- token：`web/src/index.css:6`（dark variant）、`:14-16`（语义圆角）、`:20-23`（shadcn 标尺）、`:55`（`@theme inline`）、`:63`、`:99`、`:108-217`（`:root`）、`:196-216`（别名层）、`:219-289`（`.dark`）
- 件与工具：`web/src/components/ui/`（43 个 .tsx）、`web/src/lib/utils.ts:4`（`cn`）、`web/src/lib/theme.ts`、`web/src/components/ui/sonner.tsx:5,9-13`、`web/scripts/lint-ui.mjs:110-117`
- 台账与上位决策：`docs/ui-adoption-map.md`（现状台账与欠账表）、`docs/archive/ui-migration-map.md`（68 条逐项、批 1–4、§3.1 三类处置，已归档只作证据）、ADR-064（全量迁移，本条不改其结论）、ADR-040（16 槽色板）、ADR-027（视觉系统 v2）、ADR-031（补注）、ADR-007/062/063（历史链）
- 规范正文：`docs/design-system.md`（视觉语言）、`docs/ui-standards.md`（评审清单）
