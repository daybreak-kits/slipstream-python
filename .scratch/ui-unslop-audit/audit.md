# Unslop 全站审计 — 驭流管理台（2026-11-18）

> 状态：**已审计，待修**。像素级核验尚未完成（本会话浏览器工具被宿主拒绝），先按代码级审计记录；
> 修复须在像素级核验之后进行，前后对照。判据 = unslop 信号清单 + `docs/ui.md`（冲突以仓内规范为准）。
> 门禁基线：`lint:ui` 0 error / 19 vendored warning；`build` ✓；`vitest` 36 文件 / 359 用例。

## 方法与限制

- 代码级：全量 grep 机械信号（emoji/感叹号/句号/大写/渐变/辉光/动效/任意值/裸格式化）+ 逐页交互完整性矩阵（按钮/确认/四态/aria/pending 禁用）+ 关键文件精读（login、keys、sidebar、topbar、dashboard、channel-detail、protected-route、enabled-toggle、page-placeholder）。
- 功能链路经 API 实测（一次性 DB 种子：2 渠道/2 模型/1 密钥/8 条日志）。
- **未做**：像素级目验（浏览器在 worktree 会话不可用，报 "Request workspace is not owned by this CLI"）。

## 页面清单（10 个界面 + 壳层）

/login · /(数据看板) · /overview · /logs · /channels · /channels/new + /channels/:id · /catalog · /playground · /keys · /settings；壳层 sidebar/topbar/global-search/theme-menu/timezone-toggle/protected-route。PagePlaceholder 为防御性死路径（8 个导航项全部已实现）。

## A 类：规范失守（复利型，优先）

- **A1 字距漂移**：全站 12 种 tracking 值，规范只允许 3 种（normal / −0.01em / 0.08em）。
  `tracking-[-0.02em]`×13（全部页面 h1）、`[0.12em]`×4、`[0.16em]`×3、`[0.18em]`×2、`[0.14em]`×1（sidebar 分组标签）、`[0.1em]`×1、`[-0.03em]`×1（登录 h1）、`tracking-tight`×6、`tracking-wide`×5；规范值 `[-0.01em]` 仅 1 处在用。
- **A2 PageHeader 基元缺失**：`docs/ui.md` 称 `text-h1` 仅限 PageHeader，但该组件不存在；8 个页面手抄 `<h1 className="text-h1 font-semibold tracking-[-0.02em] text-title">`（dashboard/keys/channel-detail/playground/settings/overview/channels/logs + page-placeholder）。这是 A1 的根源。
- **A3 手写开关**：`components/channels/enabled-toggle.tsx:12` 手写 `role="switch"`（含 focus-visible/aria，实现不差），但 vendored `components/ui/switch.tsx`（Radix）就位未用——第二套控件实现（ADR-064 病根）；旋钮 `bg-white` 违反 §2.2。
- **A4 自建件任意字号**：`components/ui/metric-pills.tsx:42,67` 两处 `text-[10.5px]`（低于字阶下限 11px），藏在 relaxed lint 桶里。

## B 类：unslop 信号（用户可见）

- **B5 加载屏占位 logo**（`protected-route.tsx:5-17`，每次硬刷新可见）：裸字母 "L" + 无限 `animate-ping` 光圈；正式 logo `/logo-v10-contrast.svg` 侧栏在用却没被用上。
- **B6 问候语标题**（`dashboard.tsx:459`）：「欢迎回来，{username}。」——用户名在右上角头像已有，标题应直接以时间范围开头。
- **B7 登录页光斑**（`login.tsx:82-89`）：~~已删除~~ **2026-11-18 用户裁定恢复**——两团流光是页面的记忆点而非噪音，随票 10 复拍后用户要求还原；已原样恢复（含两个 CSS 变量），测试断言同步翻转。
- **B8 过渡失准**（`overview.tsx:384`）：`transition-all` + 悬停 translate 箭头 → 应 `transition-[transform,color]`。

## C 类：文案与一致性

- **C9**（`keys.tsx:503,541`）删除/重置密钥用手写 `AlertDialog` 而非共享 `ConfirmDialog`；标题「删除密钥」不是问句（规范 §2.3 要求标题问句；正确示范 `models.tsx`「删除手动组合 X？」）。
- **C10**（`keys.tsx:400-406`）删除按钮 `text-danger-text` + 图标 `text-danger` 两种危险色混用。
- **C11**（`login.tsx`）右栏 h1 `text-2xl`（24px 越阶；规范大字号 22/28）、「SLIPSTREAM」0.18em/0.16em 两个值、`placeholder="请输入密码"` 偏长。
- **C12**（`global-search.tsx:58`）Windows 显示 `⌘K`（Ctrl+K 实际生效）——按平台显示。
- **C13** 绕过 `lib/format` 单源：`flow-sankey.tsx:262`（`.toFixed(1)×`）、`:455`（占比 `.toFixed(1)%`）、`trend-card.tsx:1007`（`.toLocaleString()`）；`trend-card.tsx:441` 的 toFixed(3) 是定位计算，豁免。
- **C14**（`docs/ui.md`）「/dev/ui 画廊仅开发环境注册」——生产仓无此画廊（在开发仓），文档失真。

## 判定为干净（不修）

- 无 emoji、无感叹号、无辉光/彩色阴影、无斜体/超粗、无 `window.confirm`。
- logs.tsx 三处 `bg-gradient-to-r from-border/70` = 滚动边缘淡出遮罩（中性色内色调过渡，允许）；`index.css:407` blueprint 网格 = 登录页登记例外的材质。
- playground 流式光标 `animate-pulse` 是"活物"指示；skeleton 带 `motion-reduce`。
- vendored 的 `transition-all`（progress/tabs）按冻结规则不动。
- 危险操作 100% 二次确认（models/playground/channel-models-panel/channel-keys-panel 走 ConfirmDialog；keys 走 AlertDialog——语义对，仅一致性归 C9）。
- 侧栏激活态 = `primary-soft` 面 + 图标瓦片反色 + 指示条（正确的 active state 范式）。
- 登录页路由追踪卡 = 全站 moment；假数据可信（P1/P2/P3、429/200/冷却、1.24s · 812 tok）。
- 表格 `tabular-nums` 到位（logs×17、channels×5、dashboard×3）；`SELECT_ALL_VALUE` 哨兵单源使用。
- 图标按钮 aria-label 覆盖良好（logs×22、settings×9、topbar 全量）。

## 方向（五条）

1. **Product**：LLM 网关「驭流」的管理台——多路上游收束为一个 OpenAI 兼容端点的运维面板。
2. **Character**：克制、精确、仪器感。
3. **World**：机房与仪表——深色面板、等宽数字、状态点；现有 `index.css` token 体系即此世界，不动。
4. **Moments**：登录页路由追踪卡（去光斑后独挑大梁）；看板主趋势图。
5. **Signature**：1.5px 状态点（健康/环境/live）已全站一致，定为唯一签名细节，其余装饰不加。

## 修复批次（像素核验通过后一次执行）

1. 建 `PageHeader`（title/description/actions），8 页接入；字距收敛到规范 3 值（A1+A2）
2. `EnabledToggle` → `ui/switch`，旋钮 `bg-background`（A3）
3. `metric-pills` 10.5px → `text-2xs`（A4）
4. 加载屏：正式 logo + Spinner，去 ping（B5）
5. dashboard 问候语 → 范围直接开头（B6）
6. 登录页删两光斑、h1 归 `text-h1`、SLIPSTREAM 字距统一 0.08em（B7+C11）
7. overview 箭头 `transition-[transform,color]`（B8）
8. keys 两处 → `ConfirmDialog` + 问句标题 + 统一危险色（C9+C10）
9. 三处数字格式走 `lib/format`（C13）
10. ui.md 画廊句更正（C14）；⌘K 按平台显示（C12）

验收：`npm run build` + `npm test -- --run` + `npm run lint:ui`（19 vendored warning 预期不变）+ 逐页像素对照（浅/深两主题）。

---

## 像素级核验（2026-11-18，playwright-mcp 已部署后补做）

方法：playwright-mcp（ZCode 用户级 MCP，`--browser msedge --isolated --headless --viewport-size=1440,900`）
经 stdio 驱动执行 108 步计划：登录 → 10 个界面 × 浅/深两主题全页截图（21 张，存 `.scratch/ui-audit/shots/`）。
全程 **0 控制台错误**；路由守卫（未登录弹回 /login）、主题切换（menuitemradio）、SSE 数据渲染全部正常。

### 像素确认（代码审计 → 真机证实）

- **B7 光斑**：真机上两个 blur 色斑比追踪卡更抢眼，装饰压过 moment —— 删。
- **B6 问候语**：「欢迎回来，admin。今天 · 按 Asia/Shanghai 分桶。」在真机上尤其突兀（一次性用户名入标题）。
- **A4 任意字号**：metric-pills 的 10.5px 脚注在真机上明显低于全站最小字号，肉眼可辨的不协调。

### 像素新增发现（代码审计看不出或没看的）

- **P1 · 价格 $0.00（系统性，最高优先）**：单价未配置时，模型管理页价格列渲染「入 $0.00 / 出 $0.00」，日志页**失败行**费用列渲染 `$0.00`——两页两种口径（成功行显示「未配置单价」），且 `$0.00` 正是 ui.md §2.3 明令禁止的「会被读成免费」。改法：统一显示「—」+ 说明。
- **P2 · 失败行耗时 0.00s**：日志页失败行的首字/首答渲染 `0.00s`（从未发生），应显示「—」。
- **P3 · metric-pills `text-[19px]`**：规格已有 `text-metric`（19/26）token，组件却写原始任意值——字阶 token 漂移实例，随 A4 一起修。
- **P4 · 空状态消息渲染空灰条**：`channel-detail.tsx:894` 连接状态卡的消息框在从未测试时渲染成一根空灰条（`bg-surface-2` 空段落）；空消息不应渲染容器。
- **P5 · 主题触发按钮可访问名存疑**：代码有 `aria-label`（theme-menu.tsx:38），但 ARIA 快照里按钮显示为无名 `button [expanded]`（名称落在 menu 元素上）。低优先，人工复核一次 Radix asChild 的名称投影。
- **P6 · 行内操作风格不一致**：渠道列表行操作为纯图标按钮，密钥列表为「图标+文字」按钮。两页信息密度不同，可接受，但建议在 ui.md 里定一条规则（≥4 个动作用图标+文字，≤3 个用图标+tooltip）。
- **（撤销）**：此前疑似的「看板缓存命中率红色脚注」为估算标记的琥珀色 ≈ 图标 + 截图压缩误读，语义正确，不修。

### 像素判定为优（不动的部分）

深色主题（表面台阶/发丝线/图表亮蓝，非简单反转）· 概览页快速上手区（编号步骤 + 深色调用示例卡 = onboarding moment）· 日志页吞吐列「—」口径 · 状态徽章（绿点 200 / 红点 502）· 全局搜索 ⌘K 键帽 · 侧栏导航密度与激活态。

### 待修批次（合并后共 14 项）

原 10 项不变，追加：P1（价格口径统一）、P2（失败行耗时）、P3（text-metric token）、P4（空消息框）。P5/P6 记录为低优先观察项。

---

## 交互级深扫（2026-11-18 第二轮，playwright-mcp 驱动）

> 对第一轮"只拍默认态"的补课：渠道 4 标签、模型编辑/组合编辑器/获取模型弹窗、密钥新建/删除弹窗、
> 日志详情抽屉、试验页实发流式请求，共 12 张交互态截图（`.scratch/ui-audit/shots/31-42`）。
> 同批并入 `verification.md`（独立核验报告，14 条全实锤 + 8 条新发现 N1–N8）。

### P1 升级：$0.00 的病根在编辑表单

- **表单预填 "0"**：模型编辑弹窗把「输入单价 / 1M」「输出单价 / 1M」预填成字面 `0`（缓存价反而是正确的「留空 = 按输入价 / 暂不计价」语义）——保存后才有 $0.00 可渲染。修复必须同时改表单语义（留空 = 未配置）与三处列表显示。
- **正确示范已存在**：日志详情抽屉、试验页统计行都显示「未配置单价」——修复 = 向站内已有的正确实现看齐，不是发明新口径。
- **第三处 $0.00**：渠道详情「路由与映射」标签的单价列同样渲染 $0.00 / $0.00。

### 交互态像素实锤

- **C9/C10 像素确认**：删除密钥弹窗标题「删除密钥」非问句；danger 按钮 = 饱和红底 + 白字（ui.md §4 点名的对比度组合）；取消左/danger 右、不可逆后果文案 ✓。
- **试验页（用户点名，实发请求验证）**：参数控制条（temperature 0.7 · 流式 + 「只对下一次请求生效」）✓；回复统计含 成本「未配置单价」/TTFT/吞吐/req id/重试 ✓；「无思考块」显式标注 ✓；模型选择器、流式 badge、清空记录齐全。
- **组合编辑器**：「priority 是『层』，不是序号」口径卡逐字在位；嵌套深度 1/3；跟随全局双覆盖；占位示例 `team-chat` 真实。
- **改写规则编辑器（空态）**：作用段轨道（请求头/体/响应头/响应体计数）+「这个作用段还没有规则。」+ 添加第一条规则 CTA——空态规范（区分无数据 + 给下一步）。
- **渠道路由标签**：探测五卡、默认探测单选、人工封禁说明段落——信息密度高但有序；唯一问题 = P1 的 $0.00。
- **交互健康**：全部弹窗 Escape 可关、无失控焦点、无布局跳动；组合名单向 ASCII 为设计使然（组合名 = 可调用模型别名，须 URL 安全），UI 报错文案清晰。

### 合并 verification.md 的 N1–N8（全部采纳）

- **N1（升 P1）**：概览页「API 端点」列出 `POST /v1/messages` 与 Gemini `:generateContent`，实测 404——能力失真高于纯视觉，进修复批次第 11 项。
- **N2**：登录表单 `animate-fade-up` 整块延迟入场 >600ms（dev 实测），建议去掉或 150ms。
- **N3**：桑基图深色主题细流量丝带近乎纯黑。
- **N4**：日志页缓存命中率 hint 截断观感破碎。
- **N5**：两处 AI sparkle 图标（playground 空态 + overview「在模型试验中验证 ✦」）——第一轮 grep 只查了 emoji 字符，漏了 lucide `Sparkles`，采纳。
- **N6/N7/N8（info）**：时间预设文案与 ssot 措辞漂移；catalog 封禁确认标题非问句（随 C9 统一）；playground 历史 key 与 storage.ts 注释矛盾。

### 最终待修批次（22 项，按优先级）

1. **P1 价格口径**（表单预填 0 + 三处列表 $0.00 → 统一「未配置单价/—」）
2. **N1 概览端点能力失真**（删两行或标「未开放」）
3. A1+A2 字距收敛 + PageHeader 基元（8 页接入）
4. A3 手写开关 → ui/switch；A4/P3 metric-pills 字号与 text-metric token
5. B5 加载屏 L logo + ping；B6 问候语；B7 登录页光斑；B8 transition-all
6. P2 失败行耗时 0.00s → —；P4 空消息框不渲染
7. C9+C10 keys 弹窗换 ConfirmDialog（标题问句化，随 N7 一并统一全站确认框问句规范）
8. N2 登录 fade-up；N3 桑基深色对比；N4 hint 截断；N5 sparkle ×2
9. C12 ⌘K 平台化；C13 三处格式化走 lib/format；C14 ui.md 画廊句；N6/N8 文档口径二选一

验收：三门禁 + 浅/深两主题逐页复拍对照（基线 = `.scratch/ui-audit/shots/` 33 张 + 主仓 `.scratch/ui-verify/shots/` 60+ 张）。

---

## 全站 CRUD 实操轮（2026-11-18 第三轮，playwright-mcp 驱动）

> 应对"举一反三 + 增删改查都点一遍"：mock 加厚数据（双模型真实单价、3 密钥、14+ 混合状态日志、手动组合）
> 后，用 `browser_run_code_unsafe` 断言式流程逐实体走增删改查，每步验证状态真变化。

| 实体 | C | R | U | D | 备注 |
|---|---|---|---|---|---|
| 渠道 | ✓ 含批量密钥添加 | ✓ | ✓ 改名保存 | ✓ | 删除确认含级联提示（模型一并删除/组合引用拒绝） |
| 模型映射 | ✓ | ✓ | ✓ 封禁确认 | ✓ 确认 | 确认弹窗 role=alertdialog |
| 组合 | ✓ 编辑器 | ✓ | —（未单独测编辑） | ✓ 确认 | 名称 ASCII 为设计使然（= 可调用别名） |
| 密钥 | ✓ 含一次性揭示弹窗 | ✓ 前缀 | ✓ 重命名+封禁/启用 | ✓ 确认 | 揭示弹窗 + 「去模型试验使用」深链是好设计 |
| 改写规则 | ✓ 校验拦截无效规则 | ✓ 刷新持久化真断言 | — | ✓ | 空态/行内校验错误条均规范 |
| 设置 | — | ✓ | ✓ 连接超时 10→75 持久化 | — | 每卡具名保存按钮（保存地址/保存超时）✓ |
| 试验 | ✓ 实发流式 | ✓ 统计行 | — | 清除记录（确认框在位未点） | 成本「未配置单价」正确示范 |

### CRUD 轮新证据（并入修复批次）

- **CRUD-R1（并入 C9，升级）**：确认弹窗**实现级双轨**——模型/密钥删除确认是 `role=alertdialog`（ConfirmDialog→AlertDialog 原语），渠道删除确认是 `role=dialog`；自动化与键盘用户体验分裂。C9 修复从"换文案"升级为"统一到单一实现 + 问句标题"。
- **CRUD-R2（并入 N7）**：「封禁模型」「删除渠道」确认标题均非问句——全站确认框标题规范化一次性处理。
- **CRUD-R3（正面）**：改写规则行内校验错误条、无效规则拒绝保存、每卡具名保存按钮、密钥一次性揭示——这些是站内已有的正确范式，修复批次全部"向对齐看齐"。

### 组件来源治理（回答"是不是都是 shadcn 组件"）

- `components/ui/` 43 文件 = 27 vendored + 11 并入 + 4 自建 + 1 hook，与 ui.md §3 台账一致；**Radix 原语只在 ui/ 内 import（全仓合规）**。
- 但应用层仍有 4 处手写基元语义：`enabled-toggle`（switch 在库不用）、`combo-editor-dialog:947`（手写 tablist）、`logs.tsx` RoutePopover（手写 dialog 浮层）、`playground/model-combobox` + `models/schedule-rules`（combobox 语义并行实现，未组合 ui/command）。
- **6 个死基元**：avatar/breadcrumb/form/progress/scroll-area/switch 0 调用——"组件在库里、页面在外头写"正是规范脱落的机制。
- 已沉淀为 `docs/ui.md` **§2.1 组件来源治理**（Radix 边界、手写禁令、自建登记、死基元三个月接线或移除）+ **§2.5 门禁探针**（三条 rg 可执行）。

### ui.md 规范整合（本轮交付）

`docs/ui.md` 2026-11-18 修订：§2.1 组件来源治理 · §2.2 动效具名化与入场动画上限 · §2.3 确认弹窗唯一实现+问句标题+danger 前景 · **新增 §2.4 数据口径、空态与表单语义**（— 口径/表单禁预填哨兵/空容器不渲染/能力宣传=事实/AI 装饰图标禁令/行内操作风格/存储单源/平台键位/深色图表对比）· §2.5 门禁修正（视觉套件残留句清除、画廊归属更正、治理探针）· §3 死基元清单。

---

## 第四轮补充：试验参数面板（用户点名，此前只审过收起态）

**P7 · 参数控制面板布局失序**（`components/playground/chat-params-panel.tsx:53`，截图 `shots/43-params-panel.png`）：

- 病根：全面板一个统一网格 `grid-cols-1 sm:2 lg:4 xl:6`，**控件性质不分行**——大 textarea（系统提示词）占 2/6 格、checkbox 占 1 格、短 input 占 1 格、select 占 1 格，同行行高被 textarea 撑起，其余顶对齐留大洞；「输出」组 stop 的 textarea 又把行高撑乱；「额外请求字段」JSON 框只占 1 格宽，右侧大片空白。
- 附带：中英文标签混排（系统提示词 vs temperature），参数名带虚线下划线（title 提示样式）没有走 tooltip 基元；六个采样 input 一行在窄屏无退化保证。

**修法（并入批二）**：按"同质控件一行"重排——textarea 类（系统提示词/stop/额外 JSON）`col-span-full` 独行；短 input 三列或六列同行（同质等高等宽）；checkbox/ToggleField 只与同高度控件同行；组间 `gap-y-4`、组内 `gap-y-3`；标签以中文业务名为主、参数名作 mono 副注；提示统一走 `ui/tooltip`（去掉虚线 title 样式）；全部经 `ui/field` 基元出。窄屏 1→2 列退化保持。




