# Tailwind 3.4.19 → 4.x 升级手册（web/）

> **已归档（2026-11-14）**：本文是历史文档，只作证据与背景，**不再更新、不再作为权威**。现状见 [../ui-adoption-map.md](../ui-adoption-map.md)（台账）与 [../ui-standards.md](../ui-standards.md)（规范正文）。

> **定位**：这是 ADR-064 阶段 1 里「批 0」的操作手册，只讲**怎么升**；为什么升、换成 shadcn 的分批与回滚点见 [ADR-064](../adr/ADR-064-full-shadcn-migration.md) 与 [ui-migration-map.md](./ui-migration-map.md)，控件规范见 [ui-standards.md](../ui-standards.md)。
> **读者**：在本机（有网络、能 npm install / vite build）执行迁移的人或 agent。
> **事实基准日**：2026-11-14。版本：`tailwindcss@4.3.5`、`@tailwindcss/vite@4.3.5`、`@tailwindcss/postcss@4.3.5`（npm registry）；依据 tailwindcss.com 文档，正文取自站点源码仓库 `tailwindlabs/tailwindcss.com` 的 `src/docs/*.mdx`（本地副本：`.scratch/bench2/notes/tw4/`）。来源清单见附录 A。
> **沙箱说明**：写这份手册的会话装不了包、跑不了 `vite build`（子进程被拒）。因此下面每条命令都写成「你在真实环境照做」的形式，并给出**预期产物**与**预期报错**。唯一没实测的是迁移后的 CSS 体积（见 `3.6`）。

---

## 0. 结论先行

1. **能升，且不需要改业务代码**。`tailwindcss 4.3.5` + `@tailwindcss/vite 4.3.5` 的 peer 是 `vite ^5.2.0 || ^6 || ^7 || ^8`，本仓 `vite ^7.3.8` 在范围内；要求 Node.js 20+。
2. **80 行 `tailwind.config.js` 可以 1:1 搬进 CSS**，且**类名一个都不变**：`bg-surface-2`、`rounded-control`、`shadow-card`、`animate-fade-up`、`font-mono` 迁移后都还在（逐条翻译见 `3.4.2`）。这是本次升级成本低的关键——页面侧不用动。
3. **颜色要分两层写**：`:root` / `.dark` 里的 74 + 61 条变量**原样保留**（它们承载明暗切换），另用 **`@theme inline`** 把语义色转发成 `--color-*`。用不带 inline 的 `@theme` 会让 `var()` 在定义点解析，暗色直接失效。
4. **必须改的类名只有 6 组、共 62 行**：`outline-none → outline-hidden`（29 行）、裸 `rounded → rounded-sm`（19）、`rounded-sm → rounded-xs`（4）、`shadow-sm → shadow-xs`（7）、裸 `blur → blur-sm`（1）、裸 `backdrop-blur → backdrop-blur-sm`（2）。不做这一步，圆角/阴影/裸 outline 会集体变小一号（见 `3.5`）。
5. **暗色链路不动**：本仓 **0 处 `dark:` 工具类**（明暗全靠 CSS 变量），但仍要写 `@custom-variant dark`，否则将来第一个 `dark:` 类会走 `prefers-color-scheme` 而不是 `.dark` class。`lib/theme.ts:25-30`、`index.html:9-28` 的防闪脚本一行不改。
6. **批 0 与批 1 必须分 PR**：批 0 只动 4 个文件（package.json / vite.config.ts / index.css / 删除 2 个配置文件）+ 类名清扫，不动任何页面与基元；批 1 起才引入 shadcn 源码。原因见 `8`。
7. **最容易被绊倒的是「中间态必红」**：`npm i` 与 `index.css` 重写必须同一次提交，否则 `vite dev` / `vite build` 会在中间态直接报错（见 `3.1`）。
8. **收尾状态（9847b66）**：`filesScanned=81`、**0 error / 19 warning**（全为 vendored 例外）、`result: PASS`、`tsc --noEmit` exit 0；门禁 = `npm run lint:ui` = `node scripts/lint-ui.mjs --max-warnings 19`。本文 §3 的类名换算与 §5 的迁移步骤在收尾后均未再变。

---

## 1. 开工前必须确认的两件事

### 1.1 浏览器基线（不满足就别升）

Tailwind v4 依赖 `@property` 与 `color-mix()`，官方基线是 **Safari 16.4+ / Chrome 111+ / Firefox 128+**；旧浏览器只能留在 v3.4（[upgrade-guide](https://tailwindcss.com/docs/upgrade-guide)，2026-11-14）。

这是**决策闸门**而不是细节：驭流是内部自托管控制台，若使用方浏览器矩阵里有旧 Edge / 旧 Chrome（部分企业镜像会锁版本），升 v4 会让 hover、透明度修饰、渐变色在那些机器上退化。先要一份实际 UA 分布或反代日志。**确认不了也照第 3 节做，但把浏览器基线写进 PR 描述让评审一起看。**

### 1.2 接入路线：Vite 插件 vs PostCSS 插件

v4 的 PostCSS 插件已从 `tailwindcss` 包挪到 `@tailwindcss/postcss`，Vite 项目官方另给了一个专用插件。两条路线**功能等价，二选一，不要混用**：

| | **@tailwindcss/vite（本手册采用）** | @tailwindcss/postcss |
|---|---|---|
| 新增 devDeps | `tailwindcss@^4.3.5`、`@tailwindcss/vite@^4.3.5` | `tailwindcss@^4.3.5`、`@tailwindcss/postcss@^4.3.5` |
| `web/vite.config.ts` | `plugins` 里加 `tailwindcss()` | 不动 |
| `web/postcss.config.js` | **删除** | 改内容为 `{ plugins: { "@tailwindcss/postcss": {} } }` |
| autoprefixer | 删（v4 自带前缀处理） | 删 |
| 适用 | Vite 项目（官方安装文档的首选） | 非 Vite 构建链 |
| 失败模式 | 忘了删 `postcss.config.js` → 报 `It looks like you're trying to use 'tailwindcss' directly as a PostCSS plugin...` | 插件名没改 → 同样的报错 |

> **与台账的措辞差异（需 lead 裁定，见附录 C）**：`ui-migration-map.md:149` 与 `ADR-064:47` 写的是「新增 @tailwindcss/postcss」。本手册推荐 Vite 插件，理由是少一层 PostCSS 配置、官方对 Vite 的首选路径、对本仓已有 `defineConfig` 是纯增量。**若坚持台账口径，把 `3.2`/`3.3` 的包名与 `3.4` 换掉即可，其余步骤完全一致。**

---

## 2. 迁移前的输入清单

| 文件 | 行数 | 内容 | 迁移后 |
|---|---:|---|---|
| `web/tailwind.config.js` | 80 | `darkMode: ["class"]`(:3)、`content`(:4)、`colors`(:7-49，27 个色名全部映射到 `var()`)、`fontFamily`(:50-53)、`borderRadius`(:54-58，control 8px / card 12px / badge 6px)、`boxShadow`(:59-62，card / pop)、`keyframes`(:63-72) + `animation`(:73-76)、`plugins: []`(:79) | 全部进 `index.css`；文件删除 |
| `web/postcss.config.js` | 6 | `tailwindcss: {}` + `autoprefixer: {}` | 文件删除（Vite 路线） |
| `web/src/index.css` | 250 | `@tailwind base/components/utilities`(:1-3)、`:root` 74 条(:5-95)、`.dark` 61 条(:97-170)、`@layer base`(:172-232)、`@layer utilities` 三个自定义类(:234-250) | 头部换 `@import "tailwindcss"` + `@theme`；变量与 base 段保留；utilities 段改 `@utility` |
| `web/vite.config.ts` | — | `plugins: [react(), chunkSizeGuard()]`(:65)、`manualChunks`(:88)、内嵌 vitest 配置(:92) | 只改 `plugins` 一行 |

**用法计数（rg，2026-11-14，`web/src` 下按「行」计、非出现次数）**：

| 用法 | 行数 | v4 处置 |
|---|---:|---|
| `outline-none` | 29 | 改 `outline-hidden`（必改） |
| 裸 `rounded`（不带后缀） | 19 | 改 `rounded-sm`（必改） |
| `rounded-sm` | 4 | 改 `rounded-xs`（必改） |
| `shadow-sm` | 7 | 改 `shadow-xs`（必改） |
| 裸 `blur` | 1 | 改 `blur-sm`（必改） |
| 裸 `backdrop-blur` | 2 | 改 `backdrop-blur-sm`（必改） |
| 裸 `ring` / 裸 `shadow` | 0 / 0 | 无需处理（裸 `ring` 若出现要改 `ring-3`） |
| `rounded-md/lg/xl/2xl/3xl` | 47 | 值不变，不动 |
| `rounded-control/card/badge` | 178 | 自定义 token，名不变，不动 |
| `shadow-card/pop` | 13 | 自定义 token，名不变，不动 |
| `ring-<数字>` | 27 | 显式宽度，行为不变 |
| `ring-inset / ring-offset-*` | 14 | 仍支持，不动 |
| `space-x/y-*` | 57 | 编译通过；选择器实现变了，视觉回归关注（`4` 第 5 条） |
| `[var(--...)]` 任意值（`bg-[var(` 22 行、`text-[var(` 25 行） | 47 | `var()` 写法继续有效，不动 |
| `bg-opacity-* / text-opacity-*` 等 | 0 | 已废弃但本仓未用 |
| `container` | 0 | v4 移除其配置，本仓无影响 |
| `dark:` 工具类 | 0 | 明暗靠变量；仍声明 `@custom-variant dark` |
| `hover:` | 130 | 桌面后台风险低；触屏行为变化见 `4` 第 11 条 |
| `@apply` | 0 | 本仓无 `@apply`，批 0 不受影响（规则见 `4` 第 8 条） |
| `animate-*` | 72 | 自定义两档 + 默认档，均继续可用 |
| `text-[Npx]` 任意字号 | 660（ADR-064 清单计数口径；只数 px 单位；数字是快照，以 `npm run lint:ui` 当次输出为准） | v4 继续支持任意值，批 0 不动；收敛留给批 4 |

体积基线（迁移前，2026-11-14 读 `web/dist/assets/`）：JS `index-*.js` **801,505 B**、CSS `index-*.css` **44,790 B**。迁移后记录新值，不要凭感觉判断涨跌。

---

## 3. 操作步骤（批 0，一个 PR）

### 3.1 原子性：为什么 A1–A5 必须一次提交

四个动作互相依赖：`package.json` 升了 4.x 但 `index.css` 还是 `@tailwind base` → 构建报「指令已移除」；`index.css` 换成 `@import "tailwindcss"` 但 `postcss.config.js` 还在用 `tailwindcss: {}` → 报「插件已搬走」；`tailwind.config.js` 删了但 `index.css` 还没写 `@theme` → 全站颜色类消失（页面不报错，只是白屏级视觉丢失）。**中间态必然红，且红得没有信息量**，所以：先按 A1→A5 把文件全部改完（不中间验证），再一次性 `npm install` + `npx vite build`。

### 3.2 A1：改 `package.json`

在 `web` 下：

```bash
# 1) 装 v4 与 Vite 插件
npm i -D tailwindcss@^4.3.5 @tailwindcss/vite@^4.3.5

# 2) 卸掉不再需要的东西
npm rm -D autoprefixer postcss
```

**预期产物**：`web/package.json` 的 `devDependencies` 里 `tailwindcss` 变成 `^4.3.5`、出现 `@tailwindcss/vite`、`postcss` 与 `autoprefixer` 消失；`package-lock.json` 有大 diff；`node_modules/tailwindcss/package.json` 的 `version` 为 `4.3.5`。
**注意**：`postcss` 可能仍作为 vite 的传递依赖存在，这是正常的；卸的只是**直接 devDependency**。`scripts` 不用改（`build` 仍是 `tsc --noEmit && vite build`）。

### 3.3 A2：改 `vite.config.ts`

`web/vite.config.ts` 只加两处（`manualChunks`、`chunkSizeGuard`、内嵌 vitest 配置全部不动）：

```ts
import tailwindcss from "@tailwindcss/vite";
// ...
plugins: [react(), tailwindcss(), chunkSizeGuard()],
```

**预期产物**：`npm run dev` 起服务后样式由 Tailwind 的 Vite 插件直接产出（不再经过 PostCSS 配置）。
**报错示例**（若 `postcss.config.js` 未删）：启动即失败，提示 `tailwindcss` 不能再当 PostCSS 插件用。

### 3.4 A3 + A4：删两个配置文件，重写 `index.css`

```bash
cd web
rm postcss.config.js tailwind.config.js
```

然后按下面小节重写 `src/index.css`。**保留**：74 条 `:root` 变量、61 条 `.dark` 覆盖、`@layer base` 整段（含 `* { border-color: var(--border) }`、`body`、`h1-h4`、`::selection`、autofill、`:focus-visible`）、三个自定义工具类（改写法）。

#### 3.4.1 头部：三条指令 → 一条 + 暗色变体

```css
@import "tailwindcss";

/* 暗色仍走 class 策略：.dark 挂在 <html> 上（lib/theme.ts:25-30、index.html:9-28） */
@custom-variant dark (&:where(.dark, .dark *));
```

#### 3.4.2 `@theme`：`tailwind.config.js` 逐条翻译

| 原配置（行号） | 原值 | v4 写法 | 迁移后可用类 |
|---|---|---|---|
| `darkMode: ["class"]`（:3） | class 策略 | `@custom-variant dark (&:where(.dark, .dark *))` | `dark:*` |
| `content`（:4） | `index.html` + `src/**/*.{ts,tsx}` | **删除**，v4 自动探测（以 cwd=`web` 为基准，排除 `.gitignore` / `node_modules` / CSS / 锁文件 / 二进制） | — |
| `colors`（:7-49） | 27 个色名 → `var(--x)` | `@theme inline` 里的 `--color-*`（见 3.4.3，**必须是 inline**） | `bg-*` / `text-*` / `border-*` 与今天完全相同 |
| `fontFamily.sans`（:51） | Inter, Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif | `--font-sans: Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;` | `font-sans` |
| `fontFamily.mono`（:52） | JetBrains Mono, ui-monospace, … | `--font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace;` | `font-mono` |
| `borderRadius.control`（:55） | 8px | `--radius-control: 8px;` | `rounded-control` |
| `borderRadius.card`（:56） | 12px | `--radius-card: 12px;` | `rounded-card` |
| `borderRadius.badge`（:57） | 6px | `--radius-badge: 6px;` | `rounded-badge` |
| `boxShadow.card`（:60） | 双层 rgba | `--shadow-card: 0 1px 2px rgba(17,24,39,.04), 0 8px 24px -18px rgba(17,24,39,.28);` | `shadow-card` |
| `boxShadow.pop`（:61） | 双层 rgba | `--shadow-pop: 0 18px 40px -20px rgba(17,24,39,.35), 0 2px 8px rgba(17,24,39,.06);` | `shadow-pop` |
| `keyframes`（:63-72） | fade-up / fade-in | `@keyframes` **写在 `@theme` 内部** | — |
| `animation`（:73-76） | `fade-up 200ms ease-out both` 等 | `--animate-fade-up: fade-up 200ms ease-out both;` 等 | `animate-fade-up` / `animate-fade-in` |
| `plugins`（:79） | 空 | 删除 | — |

```css
@theme {
  --font-sans: Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace;

  --radius-control: 8px;
  --radius-card: 12px;
  --radius-badge: 6px;

  --shadow-card: 0 1px 2px rgba(17, 24, 39, 0.04), 0 8px 24px -18px rgba(17, 24, 39, 0.28);
  --shadow-pop: 0 18px 40px -20px rgba(17, 24, 39, 0.35), 0 2px 8px rgba(17, 24, 39, 0.06);

  --animate-fade-up: fade-up 200ms ease-out both;
  --animate-fade-in: fade-in 200ms ease-out both;

  @keyframes fade-up {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }
}
```

> **为什么 `@keyframes` 要写在 `@theme` 里**：只有写在 `@theme` 内的 keyframes 才会随 `--animate-*` 变量一起输出；写在外面则不与动画变量绑定（[theme.mdx](https://tailwindcss.com/docs/theme#defining-animation-keyframes)）。

#### 3.4.3 语义色：`@theme inline`（关键）

```css
/* 值全部来自 :root/.dark 的既有变量，所以必须 inline：
   非 inline 时 var() 会在定义点解析，暗色切换拿不到 .dark 的新值 */
@theme inline {
  --color-bg: var(--bg);
  --color-surface: var(--surface);
  --color-surface-2: var(--surface-2);
  --color-border: var(--border);
  --color-border-strong: var(--border-strong);
  --color-title: var(--text-title);
  --color-body: var(--text-body);
  --color-muted: var(--text-muted);
  --color-faint: var(--text-faint);

  --color-primary: var(--primary);
  --color-primary-hover: var(--primary-hover);
  --color-primary-active: var(--primary-active);
  --color-primary-soft: var(--primary-soft);
  --color-primary-on: var(--primary-on);

  --color-highlight: var(--highlight);
  --color-highlight-text: var(--highlight-text);

  --color-success: var(--success);
  --color-success-text: var(--success-text);
  --color-success-soft: var(--success-soft);
  --color-warning: var(--warning);
  --color-warning-text: var(--warning-text);
  --color-warning-soft: var(--warning-soft);
  --color-danger: var(--danger);
  --color-danger-text: var(--danger-text);
  --color-danger-soft: var(--danger-soft);

  --color-info: var(--info);
  --color-focus-ring: var(--focus-ring);
}
```

`--color-surface-2: var(--surface-2)` 生成 `bg-surface-2`；`--color-focus-ring` 生成 `ring-focus-ring` / `outline-focus-ring`；`--color-title` 生成 `text-title`。**色名与 v3 一一对应，所以 1,941 处 `className` 一个都不用改。**

#### 3.4.4 别名层（shadcn 语义 token，批 1 起才被用到，批 0 先落位）

shadcn 组件按 `background / foreground / card / popover / primary / secondary / muted / accent / destructive / border / input / ring` 取名。下面这层**只新增名字、不新增颜色**，全部指回既有 token：

```css
:root {
  /* ... 原有 74 条不动，这里追加 ... */
  --background: var(--bg);
  --foreground: var(--text-title);
  --card: var(--surface);
  --card-foreground: var(--text-title);
  --popover: var(--surface);
  --popover-foreground: var(--text-title);
  --primary-foreground: var(--primary-on);
  --secondary: var(--surface-2);
  --secondary-foreground: var(--text-body);
  --muted: var(--surface-2);
  --muted-foreground: var(--text-muted);
  --accent: var(--primary-soft);
  --accent-foreground: var(--primary-active);
  --destructive: var(--danger);
  --destructive-foreground: var(--primary-on);
  --input: var(--border-strong);
  --ring: var(--focus-ring);
  --radius: 8px;   /* shadcn 用 calc(var(--radius) ± n) 派生；8px 恰是 v4 默认 rounded-lg */
   /* 圆角阶梯（sm6/md8/lg12/xl16）以 [ADR-066](../adr/ADR-066-token-semantics-adoption.md) 为准，此处不另述 */
}
```

再在 `@theme inline` 里补这批别名与「图表色可选」的转发（**同一个 `@theme inline` 块**，不要另起一个非 inline 的 `@theme`）：

```css
@theme inline {
  /* ... 3.4.3 的 27 条之后继续 ... */
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-input: var(--input);
  --color-ring: var(--ring);
}
```

三个**必须知道的约束**：

1. **`--primary` 与 `--border` 不要重复声明**。本仓已有 `--primary`（`index.css:16`）与 `--border`（`index.css:9`），值正是 shadcn 想要的；写成 `--primary: var(--primary)` 是自引用，CSS 无效。别名层只补**本仓没有的**名字。
2. **`.dark` 里不需要重复别名**。别名 `--background: var(--bg)` 在 `<html>`（即 `:root` 元素）上求值，而 `.dark` 也挂在 `<html>` 上（`lib/theme.ts:25-30`）→ 同一个元素，暗色时 `--bg` 一变别名跟着变。**若哪天把 `.dark` 移到 `<body>`，这套别名会停在浅色值**——所以 `theme.ts` 不要动，这条也写进批 1 的取件注意事项。
3. 图表 16 槽色板（`--chart-1..16`，ADR-040）**继续留在 `:root`/`.dark`**，不进 `@theme`：它们被 `var()` 直接引用，不需要工具类。若想让 `bg-chart-1` 这类类名可用，再补 16 行 `--color-chart-N: var(--chart-N)`（可选，批 0 不做）。

#### 3.4.5 `@layer base`：整段保留 + 1 条推荐补丁

原有 `@layer base`（`index.css:172-232`）**一行不改**，其中 `* { border-color: var(--border) }`（:173-175）恰好就是 v4 默认边框色改成 `currentColor` 之后官方给的兼容写法（[upgrade-guide #default-border-color](https://tailwindcss.com/docs/upgrade-guide#default-border-color)），这条旧规则反而省了一次迁移。

v4 的 Preflight 有三处行为变化可能被本仓感觉到，按需在 `@layer base` 末尾追加：

```css
@layer base {
  /* 保留原有全部规则 ... */

  /* (1) 推荐：v4 的 placeholder 默认色 = 当前文字色 50%，v3 是 gray-400。
         本仓 0 处 placeholder-* 类、12 个自建基元靠 Preflight 默认值；
         不加这段，输入框占位文字会变淡。 */
  input::placeholder,
  textarea::placeholder {
    color: var(--text-faint);
  }

  /* (2) 按需：v4 里 button 的 cursor 变成 default，不再是 pointer。
         本仓若有依赖浏览器默认手型的原生 <button>，这里兜住；批 1 换 shadcn 后可删。 */
  button:not(:disabled),
  [role="button"]:not(:disabled) {
    cursor: pointer;
  }

  /* (3) 不需要：v4 重置了 <dialog> 的 margin。本仓 Modal 是 div 实现（components/ui/modal.tsx），未用原生 <dialog>。 */
}
```

**自测方法**：迁移前后各截一张登录页 + 一个带输入框的表单，比 placeholder 灰度。

#### 3.4.6 `@layer utilities` → `@utility`

v4 不再把 `@layer utilities` 里手写的类当工具类（也不再有 `@layer` 劫持），三个类必须改写法：

```css
@utility bg-blueprint {
  background-image:
    linear-gradient(to right, rgba(255, 255, 255, 0.05) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
  background-size: 40px 40px;
}

@utility text-balance {
  text-wrap: balance;
}

@utility scrollbar-thin {
  scrollbar-width: thin;
  scrollbar-color: var(--border-strong) transparent;
}
```

**不改的后果**：这三个类不报错、只是**不生成 CSS**（静默失效），登录页蓝图背景会消失——视觉回归能抓到。

#### 3.4.7 迁移后 `index.css` 的骨架（顺序不能变）

```css
@import "tailwindcss";                        /* 1 必须第一行 */
@custom-variant dark (&:where(.dark, .dark *));/* 2 */
@theme { /* 字体/圆角/阴影/动画 + @keyframes */ }         /* 3 */
@theme inline { /* --color-* 语义色 + 别名转发 */ }        /* 4 */
:root { /* 原 74 条 + 别名层 */ }                        /* 5 */
.dark { /* 原 61 条，chart 16 槽 */ }                    /* 6 */
@layer base { /* 原 172-232 + 补丁 */ }                  /* 7 */
@utility bg-blueprint { } @utility text-balance { } @utility scrollbar-thin { }  /* 8 */
```

### 3.5 A5：类名机械替换（62 行）

**顺序很重要**：先把 `sm → xs` 做掉，再把「裸类」升成 `sm`；反序会二次转换。

```bash
cd web/src
BT='(?<![\w-])'   # 左边界：避免命中 rounded-control / shadow-card / rounded-md

# 1) 尺度重命名：sm → xs（rounded-sm 4 行、shadow-sm 7 行）
rg -l -P "${BT}rounded-sm([^\w-]|$)" . | xargs perl -pi -e 's/(?<![\w-])rounded-sm(?![\w-])/rounded-xs/g'
rg -l -P "${BT}shadow-sm([^\w-]|$)"  . | xargs perl -pi -e 's/(?<![\w-])shadow-sm(?![\w-])/shadow-xs/g'

# 2) 裸类 → sm（rounded 19 行、blur 1 行、backdrop-blur 2 行）
rg -l -P "${BT}rounded([^\w-]|$)"       . | xargs perl -pi -e 's/(?<![\w-])rounded(?![\w-])/rounded-sm/g'
rg -l -P "${BT}blur([^\w-]|$)"          . | xargs perl -pi -e 's/(?<![\w-])blur(?![\w-])/blur-sm/g'
rg -l -P "${BT}backdrop-blur([^\w-]|$)" . | xargs perl -pi -e 's/(?<![\w-])backdrop-blur(?![\w-])/backdrop-blur-sm/g'

# 3) 裸 ring → ring-3（本仓 0 行，留作以后复跑）
rg -l -P "${BT}ring([^\w-]|$)"          . | xargs perl -pi -e 's/(?<![\w-])ring(?![\w-])/ring-3/g'

# 4) outline：语义改名（29 行）
rg -l -P "${BT}outline-none([^\w-]|$)"  . | xargs perl -pi -e 's/(?<![\w-])outline-none(?![\w-])/outline-hidden/g'
```

**为什么 `outline-none` 必须改名而不是留着**：v3 的 `outline-none` 实际输出「2px 透明 outline」，在 Windows 高对比 / 强制色彩模式下仍保留焦点轮廓；v4 把旧行为改名 `outline-hidden`，新的 `outline-none` 是真正的 `outline-style: none`——留着就是无障碍回退（[upgrade-guide #renamed-utilities](https://tailwindcss.com/docs/upgrade-guide#renamed-utilities)）。

**预期产物**：`git diff --stat` 约 62 行变更、分布在约 30 个文件；下面 4 条复查必须全部返回 0：

```bash
rg -c -P '(?<![\w-])rounded(?![\w-])'      web/src   # 0
rg -c -P '(?<![\w-])rounded-sm(?![\w-])'   web/src   # 0（已被第 1 步换成 xs）
rg -c -P '(?<![\w-])outline-none(?![\w-])' web/src   # 0
rg -c -P '(?<![\w-])shadow-sm(?![\w-])'    web/src   # 0
```

> 边界情况：`cn("rounded", isCompact && "rounded-md")` 这类拼接会被第 2 步改成 `cn("rounded-sm", …)`，正确。若某处确实想要 v4 的 2px 圆角（原 `rounded` 语义），手工改 `rounded-xs` 并在 PR 注明——按当前设计稿，19 处都是 4px 档，不需要保留 2px。

### 3.6 A6：验证（每条都要看输出，不要只看 exit code）

```bash
cd web

npx tsc --noEmit          # 预期 0 error（v4 只影响 CSS，类型不变）
npx vitest run            # 预期全绿：19 个测试文件（本仓沙箱跑不了，用户机器可跑）
npx vite build            # 预期成功，产物 dist/assets/index-*.css
ls -l dist/assets         # 记录新 CSS 体积，与 44,790 B 对比；JS 应基本不变（801,505 B 基线）
npx playwright test       # 7 张快照；预期 0 差异（见下）
```

**视觉快照的判据**（`web/e2e/__screenshots__` 7 张：dashboard-latency、login-dark、login-light、logs-100-rows、logs-expanded-failover、logs-time-range、rewrite-body 等）：
- **目标 0 差异**。做完 `3.5` 清扫 + `3.4.5` 补丁后理论上像素不变；出现差异先当 bug 查，不要直接 `--update-snapshots`。
- 允许的**预期差异**只有一处：若某个按钮元素不是 `<button>`（如 `<a role="button">`），v4 的 `cursor: default` 可能漏网——补类后重跑。
- 逐张写原因：ADR-064 要求「快照语义从"确认没变"变成"确认变对了"」，PR 里贴 diff 说明。

手工清单（5 分钟，能抓到自动测试抓不到的）：
1. 任意页 → `bg` 与文字色正确；切暗色 → 全部变量跟随（重点看首屏不闪）。
2. 输入框占位文字灰度正常；autofill 仍是主题色。
3. 表格与卡片边框不是黑色（`* { border-color }` 生效）。
4. 聚焦按钮：焦点环仍是 `--focus-ring` 的 2px（`:focus-visible` + `outline-hidden` 组合）。
5. 登录页：蓝图背景（`bg-blueprint`）在。
6. 触发一次弹层动画（`animate-fade-up`）：200ms 内位移 8px，不跳。

### 3.7 A7：提交

单个 commit。信息模板：

```text
chore(web): upgrade Tailwind 3.4.19 -> 4.3.5 (ADR-064 batch 0)

- tailwindcss 4.3.5 + @tailwindcss/vite; drop autoprefixer/postcss direct deps
- delete tailwind.config.js / postcss.config.js; theme moves into src/index.css (@theme + @theme inline)
- keep :root/.dark variable layer; dark still class-based via @custom-variant dark
- add shadcn semantic alias layer (background/foreground/card/...), no new colors
- rename scale-changed utilities: outline-none->outline-hidden, rounded->rounded-sm,
  rounded-sm->rounded-xs, shadow-sm->shadow-xs, blur->blur-sm, backdrop-blur->backdrop-blur-sm
- visual baselines: <0 差异 / 或逐张说明>
```

---

## 4. 破坏性变更对照表（逐条核过本仓）

| # | v4 变更 | 本仓命中 | 处置 |
|---|---|---|---|
| 1 | **默认 border 色** `gray-200 → currentColor` | `* { border-color: var(--border) }` 已在 `index.css:173-175` | 无需动作；这条 base 规则就是官方兼容写法，别删 |
| 2 | **默认 ring** 宽 3px→1px、色 blue-500→currentColor | 裸 `ring` **0 行**；`ring-2` 等 27 行是显式宽度 | 无影响。将来若写裸 `ring` 记得它现在是 1px |
| 3 | **尺度重命名**（shadow/blur/rounded 的 sm 档整体下移一档） | 33 行（`rounded` 19 + `rounded-sm` 4 + `shadow-sm` 7 + `blur` 1 + `backdrop-blur` 2） | 按 `3.5` 机械替换 |
| 4 | **outline**：`outline-none` 改名 `outline-hidden`；`outline-<n>` 自带 solid | 29 行 `outline-none` | `3.5` 改名；这是无障碍相关，不可省 |
| 5 | **space-x/y 选择器**改成 `> :not(:last-child)` + margin-bottom/inline-end | 57 行 | 编译通过；视觉应等价（flex/grid 无外边距折叠）。边界：容器最后一个子元素被 `hidden` 时，v3 与 v4 的间距归属不同 → 视觉回归盯一眼；长期改 `gap` |
| 6 | **container** 的 center/padding 配置取消 | 0 行 | 无影响 |
| 7 | **透明度语法**：`*-opacity-*` 删除，改用 `/50` 修饰符 | `*-opacity-*` 0 行；`/NN` 修饰符 33 行 | 无影响（`/NN` 继续支持） |
| 8 | **`@apply` 的限制**：① `@layer components/utilities` 里手写的类不再是工具类，不能被 `@apply` 引用（要 `@utility`）；② 被单独打包的样式表（CSS modules、Vue/Svelte/Astro 的 `<style>`）拿不到主题变量与自定义工具类，必须先 `@reference "../../app.css"` | 本仓 `@apply` **0 处**；单文件 `index.css` 就是主样式表 | 批 0 无影响。批 1+ 若引入带 `@apply` 的变体或将来抽 CSS modules，先读 [functions-and-directives#apply-directive](https://tailwindcss.com/docs/functions-and-directives#apply-directive) |
| 9 | **Preflight 变化**：placeholder 默认色改当前文字色 50%；button `cursor: default`；`<dialog>` margin 重置；`hidden` 属性优先于 display 类 | 本仓无 `placeholder-*` 类（靠默认）；button 基元用 `<button>`；Modal 是 div | 按 `3.4.5` 加补丁 (1)，(2) 按需，(3) 不需要 |
| 10 | **任意值语法**：CSS 变量简写 `bg-[--x] → bg-(--x)`；`var()` 写法仍可用；grid/object 任意值里逗号要用下划线 | `[var(--…)]` 47 行；`[--x]` 简写 0；grid 任意值未统计（低风险） | 无影响，不动 |
| 11 | **hover 变体**默认包进 `@media (hover: hover)`（触屏点按不再触发 hover） | 130 行 `hover:` | 桌面后台风险低；这是改进（不再有触屏粘滞 hover）。要旧行为：`@custom-variant hover (&:hover)` |
| 12 | 附：`transition/transition-colors` 现在包含 `outline-color`；`rotate/scale/translate` 改用独立 CSS 属性；`corePlugins`、`resolveConfig` 移除；JS 配置需显式 `@config` 才生效 | 本仓不用 `corePlugins`/`resolveConfig`；不保留 `tailwind.config.js`；动画用 keyframes 而非 `rotate-*` 复合变量 | 无需动作 |

---

## 5. 故障排查（症状 → 原因 → 处理）

| 症状 | 原因 | 处理 |
|---|---|---|
| 启动即报 `trying to use 'tailwindcss' directly as a PostCSS plugin` | `postcss.config.js` 还在用旧插件名 | Vite 路线：删文件；PostCSS 路线：改成 `@tailwindcss/postcss` |
| 全站没有样式（类没被生成） | `index.css` 顶部没有 `@import "tailwindcss"`，或还在用 `@tailwind` | `3.4.1` |
| 颜色类全部失效（`bg-surface` 等） | 忘了 `@theme inline` 里的 `--color-*`，或写成了非 inline | `3.4.3`；非 inline 的额外症状是暗色下颜色不跟随 |
| 暗色切换无效 | `@custom-variant dark` 缺失，或 `.dark` 挂到了 `<body>` | `3.4.1` + 检查 `lib/theme.ts:25-30` 仍挂 `documentElement` |
| `.bg-blueprint / .text-balance / .scrollbar-thin` 无输出 | 还写在 `@layer utilities` 里 | `3.4.6` 改 `@utility` |
| 边框变黑 | 删掉了 `* { border-color: var(--border) }` | 恢复 `index.css:173-175` |
| 输入框占位文字变淡/看不清 | v4 placeholder 默认色变化 | `3.4.5` 补丁 (1) |
| 按钮没有手型光标 | v4 button `cursor: default` | `3.4.5` 补丁 (2) |
| 构建报 `Cannot find module 'tailwindcss/plugin'` 之类 | 还引用 v3 时代的包路径 | 全仓搜 `tailwindcss/`，迁移后不应有引用 |
| 自定义 token 的冲突不合并（`cn("rounded-card","rounded-lg")` 两个都留） | tailwind-merge 只认标准尺度名 | 见 `6.2` |

---

## 6. 本仓特有的三件事

### 6.1 自动探测会多扫目录（可选收紧）

v3 的 `content` 只扫 `index.html` + `src/**/*.{ts,tsx}`；v4 以 cwd（`web`）为基准扫**所有非忽略文件**，于是 `e2e/**`、`vite.config.ts`、`playwright.config.ts` 也会被扫描。它们里面多数字符串不是类名、成本很小；若构建后发现 CSS 无端变大，收紧一行：

```css
@import "tailwindcss";
@source not "../e2e";
```

**先量再改**：对比 `dist/assets/index-*.css` 体积，差 <1 KB 就不折腾。

### 6.2 tailwind-merge 认不出自定义 token（实测，2026-11-14）

本仓 `web/src/lib/utils.ts:2` 用 `tailwind-merge@3.7.0`。该版本官方声明 **Supports Tailwind v4.0 up to v4.3**（[README](https://unpkg.com/tailwind-merge@3.7.0/README.md)），所以**升级不需要动它**。但它在 `cn()` 里只认标准尺度名，实测（直接 import 本地 `node_modules` 的包）：

```text
twMerge("rounded-card", "rounded-lg")   -> "rounded-card rounded-lg"   # 两个都留下，冲突未消解
twMerge("shadow-card", "shadow-md")     -> "shadow-card shadow-md"     # 同上
twMerge("bg-surface-2", "bg-surface")   -> "bg-surface"                # 颜色类能消解（任意 bg-color 同组）
twMerge("text-[13px]", "text-[12px]")   -> "text-[12px]"               # 任意字号能消解
```

这不是 v4 引入的问题（v3 时代同样如此），但批 1 起 shadcn 组件会用 `rounded-md/lg` 覆盖基元的 `rounded-control`，届时「谁赢」取决于 CSS 生成顺序而不是类顺序。**建议顺手修**（`web/src/lib/utils.ts`）：

```ts
import { extendTailwindMerge } from "tailwind-merge";

const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      radius: ["control", "card", "badge"],
      shadow: ["card", "pop"],
    },
  },
});
```

实测加配置后：`custom("rounded-card","rounded-lg") -> "rounded-lg"`（按类顺序消解，符合预期）。有效 theme key 全集（`getDefaultConfig().theme`）：`animate, aspect, blur, breakpoint, color, container, drop-shadow, ease, font, font-weight, inset-shadow, leading, perspective, radius, shadow, spacing, text, text-shadow, tracking`。

### 6.3 编辑器 / 工具链

- 删除 `tailwind.config.js` 后，VSCode 官方 Tailwind IntelliSense 需要把 `tailwindCSS.experimental.configFile` 指到 `src/index.css`（v4 起 CSS 即配置）。不配的话补全失效，但不影响构建。
- 本仓无 ESLint（`web/package.json` 无 `eslint`），不要顺手加；批 4 的 `lint:ui` 已由 `web/scripts/lint-ui.mjs` 落地（零依赖，`node scripts/lint-ui.mjs` 直接可跑；npm script `lint:ui` 已落地（`scripts.lint:ui` = `node scripts/lint-ui.mjs`）），预算按 `ui-migration-map.md` §7.6 阶梯收紧后再进 CI。
- `@custom-variant` / `@theme` / `@utility` 都是 v4 指令，TypeScript 不感知，`tsc --noEmit` 不受影响。

---

## 7. 回滚

批 0 是**单 commit 可整体回滚**（与台账回滚点一致）：

```bash
git revert <批 0 的 commit>
cd web && npm install    # package-lock 回到 v3 时代，重装 tailwindcss 3.4.19 + postcss + autoprefixer
```

因为没动 `pages` / `components`，回滚不会留下半迁移状态：`tailwind.config.js` 与 `postcss.config.js` 由 revert 自动恢复。**注意**：若同一 PR 里做了 `6.2` 的 `extendTailwindMerge` 改动，回滚会一起撤掉（v3 下它同样可用，撤掉也没坏处，但要知道这点）。

---

## 8. 与批 1+ 的衔接：为什么 shadcn 必须在这之后取

shadcn 2026 年新增/更新的组件默认发 **Tailwind v4 风格**：用 `size-*`、`shadow-xs`、`data-slot`，颜色直接吃 `bg-background / border-input / ring-ring` 这类语义 token。在 Tailwind 3 上取这些源码会**静默降级**（未知类不报错、只是不生成 CSS），组件看起来「能用但缺样式」。所以顺序是：

1. **批 0（本手册）**：升 v4 + 落别名层（`3.4.4`）。此后 `bg-background`、`text-muted-foreground`、`border-input`、`ring-ring` 都可编译。
2. **批 1+**：`npx shadcn@latest init -b radix` 后逐件 `add`（见 `ui-migration-map.md` 的批次与文件清单）。取件时**不要**让 CLI 覆盖 `index.css` 里的 `@theme` / `:root` 变量——shadcn init 会想写入自己的一套 token，落盘前逐个 merge。
3. 取件后**不要**引入它的暗色机制（`.dark` 我们自己管）；组件只消费 `--background` 等变量，正好由 `3.4.4` 的别名层供给。

反向证据也在：ADR-063 当时写下边界「2026 年新增的 shadcn 组件只发 Tailwind v4 风格，用新组件前必须先升 4」——这就是半量方案的固有天花板。

---

## 附录 A：事实来源（查阅日期均为 2026-11-14）

| 事实 | 来源 |
|---|---|
| 浏览器基线（Safari 16.4+ / Chrome 111+ / Firefox 128+）、`@tailwind` 指令移除、尺度重命名、默认 border 色、默认 ring、space/divide 选择器、container、Preflight 四条变化、JS 配置需 `@config`、`theme()` 用法、`@apply` 与 `@reference` | <https://tailwindcss.com/docs/upgrade-guide> |
| 主题变量命名空间（`--color-*` … `--animate-*`）、`@theme inline` 的必要性、`@theme static`、keyframes 写在 `@theme` 内、`@import "tailwindcss"` 展开为 `@layer theme, base, components, utilities` | <https://tailwindcss.com/docs/theme> |
| `@custom-variant dark (&:where(.dark, .dark *))`、data 属性写法、默认 `prefers-color-scheme` | <https://tailwindcss.com/docs/dark-mode> |
| `@theme` / `@source` / `@source inline` / `@utility` / `@custom-variant` / `@apply` / `@reference` / `@config` | <https://tailwindcss.com/docs/functions-and-directives> |
| 自动探测范围（以 cwd 为基准，排除 .gitignore / node_modules / CSS / 锁文件 / 二进制）、`@source not`、`source(none)` | <https://tailwindcss.com/docs/detecting-classes-in-source-files> |
| Preflight `border: 0 solid`、`@layer base` 仍可追加自定义样式、`@utility` 用法 | <https://tailwindcss.com/docs/preflight>、<https://tailwindcss.com/docs/adding-custom-styles> |
| 版本 `4.3.5`、`@tailwindcss/vite` peer `vite ^5.2.0 || ^6 || ^7 || ^8` | <https://registry.npmjs.org/tailwindcss/latest>、<https://registry.npmjs.org/@tailwindcss/vite/latest> |
| v4 默认 `--radius-xs/sm/md/lg/xl = 2/4/6/8/12px`、`--shadow-2xs…2xl` 默认值 | <https://unpkg.com/tailwindcss@4.3.5/theme.css> |
| `tailwind-merge@3.7.0` 支持 Tailwind v4.0–v4.3 | <https://unpkg.com/tailwind-merge@3.7.0/README.md> |
| 官方文档正文（离线副本，用于逐条比对） | `.scratch/bench2/notes/tw4/{upgrade-guide,theme,dark-mode,functions-and-directives,preflight,detecting-classes,adding-custom-styles}.mdx` |

## 附录 B：迁移前后复跑这些命令

类名基线核对命令（`rg -P` 用的 PCRE 断言在 .NET 正则里同样支持）：（本机没装 `rg` 时用等价的 PowerShell：`Get-ChildItem <dir> -Recurse -Include *.tsx,*.ts,*.css | Select-String '<pattern>'`，`-c` 计数用 `(... | Measure-Object).Count`；带 `--glob` 排除的目录直接从入参里去掉）字面片段（含正则元字符，如 `text-[`、`h-8`、`title=`、`<div`）加 `-SimpleMatch` 才不会被 PS 5.1 当正则解析报错；交替/字符类（如 `h-8|h-9`、`role=.menu[^a-z]`）保持正则、不加。**行数 ≠ 次数**：`Select-String` 默认数**命中行**（`(... | Measure-Object).Count`）；数**出现次数**必须走正则模式并 `-AllMatches` 累加 `Matches.Count`，字面量用 `[regex]::Escape()` 包一层（如 `Select-String -Pattern ([regex]::Escape('text-[')) -AllMatches`）——**`-SimpleMatch` 不填 `$_.Matches`，用它算次数恒为 0**。`rg -c` 数命中行、`rg --count-matches` 才数次数。要复现 lint 的 count 必须 **`-Include *.tsx`**（脚本只扫 `src/**/*.tsx`）、模式带 `\b` 词边界并保持正则配 `-AllMatches`：字号规则 `\btext-\[\d+(?:\.\d+)?px\]` = **660 次 = 660 行**（每行恰 1 处），尺寸规则 `\b[hw]-\[\d+(?:\.\d+)?px\]` = **100 次 / 94 行**（28 个文件，2026-11-14 20:5x），字面 `text-[` = **691 次**（多 31 次 = rem / var / 颜色等非字号写法）。只数行用 `(... | Measure-Object).Count` 即可：字号这条两种口径同值，尺寸那条差 6。

```bash
# 「必须改」的类名基线（迁移前应有输出，迁移后应为 0）
rg -c -P '(?<![\w-])outline-none(?![\w-])' web/src
rg -c -P '(?<![\w-])rounded(?![\w-])'      web/src
rg -c -P '(?<![\w-])rounded-sm(?![\w-])'   web/src
rg -c -P '(?<![\w-])shadow-sm(?![\w-])'    web/src

# 迁移后确认没有残留 v3 写法
rg -n '@tailwind\s+(base|components|utilities)' web/src web/index.html   # 0
rg -n 'tailwindcss/plugin|resolveConfig|corePlugins' web/src                    # 0
rg -n 'tailwind\.config' web --glob '!package-lock.json'                        # 0
```

## 附录 C：与 ADR-064 / 台账的措辞差异（2026-11-14 已裁定，口径已统一）

1. **接入包**：`ADR-064:47` 与 `ui-migration-map.md:149` 写「新增 `@tailwindcss/postcss`」；本手册推荐 `@tailwindcss/vite`（理由见 `1.2`）。两者功能等价、可互换，但**文档口径应统一**，否则批 0 的实施者二选一后与台账对不上。**已采纳**（2026-11-14）：`ADR-064` 与 `ui-migration-map.md` 两处均已改为 `@tailwindcss/vite`，并写明 `postcss.config.js` 是**删除**而非改内容。
2. **快照验收**：`ui-migration-map.md:152` 写「8 张快照应为 0 差异」，但**不执行 `3.5` 类名清扫就不可能 0 差异**（裸 `rounded` 会从 4px 掉到 2px、`shadow-sm` 会退成更浅一档、placeholder 变淡）。**已订正**（2026-11-14）：`ADR-064` 与台账统一为 **7 张**快照，并写明「批 0 必须 0 差异、批 4 重拍后逐张人眼过」；本手册的 `3.5` 类名清扫仍是批 0 的必做步骤。
3. **已登记**（2026-11-14）：`ssot-index.md` 第 25–26 行已去掉「待建」并补上相对链接；`INDEX.md` 与 `ADR-064` 里同样的「待建」标记也一并清除。
