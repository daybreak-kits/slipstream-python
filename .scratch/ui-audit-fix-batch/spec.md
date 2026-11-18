# Spec: UI 审计修复批次（unslop 全站整改）

Status: resolved (2026-11-18, both batches landed)

Commits: 768c4bc (tickets 01-05, semantics) / 74d98d9 (tickets 06-13, governance + craft) / 18061e9 (gates + docs).
Gates at close: vitest 40 files / 408 tests passed; build ok; lint:ui 0 error / 16 warning (probes included, zero hits).
Pixel re-verification: .scratch/ui-audit/shots-after/ (10 shots) — overview endpoints+auth row, log fee/latency dashes, catalog real prices, params panel regroup, question-form delete confirm all confirmed on screen. Remaining manual item: sankey dark-ribbon contrast (CSS-var two-tier verified in build output only).
Source: `.scratch/ui-unslop-audit/audit.md`（三轮审计证据索引）· ADR-081（组件来源治理）· ADR-082（单价 0=未配置）· `docs/ui.md` 2026-11-18 修订（规范正文）
Branch: `fix/b82e-breaker-and-cleanup`（当前分支，两批提交，每批过三门禁）

## Problem Statement

管理台经过 shadcn 全量迁移（ADR-064）后底子是好的，但三轮审计（代码 redline → 交互深扫 → 全站 CRUD 实操）发现规范正在局部脱落：同一价格事实在五个界面渲染出 `$0.00` 与「未配置单价」两套口径（$0.00 会被读成免费）；概览页宣传了不存在的 API 端点；危险确认弹窗存在两套实现、标题不是问句；字距漂移出 12 种取值而规范只允许 3 种；页面各自手抄 `<h1>` 而规范要求的 PageHeader 基元不存在；加载屏是来历不明的裸字母 "L" 加无限 ping；组件层出现"基元在库里、页面在外面手写"与六个零调用死基元并存的脱锚现象；试验参数面板把四种性质的控件塞进同一个等宽网格导致歪斜空洞。这些问题每一个都在教未来的页面"可以不守规范"。

## Solution

按已裁定的两批执行 22+2 项修复，全部"向站内已有的正确实现看齐"而非发明新设计：

- **批一（语义/行为）**：价格与耗时统一为「未配置/—」口径（ADR-082）、模型编辑表单价格留空化、概览页端点清单对齐后端实现（删两行 + 补认证行）、确认弹窗统一到唯一实现并全站问句标题（含底层 role 归一）、空容器不渲染，关键语义断言进入 vitest。
- **批二（视觉/治理）**：PageHeader 基元 + 字距收敛到规范三值、手写 switch 换 `ui/switch`、死基元移除（ADR-081）、metric-pills 字号 token 化、加载屏换正式 logo、看板问候语移除、登录页去光斑/归字阶、过渡具名化、AI 装饰图标清理、参数面板按"同质控件一行"重排、行内操作风格统一、平台键位、lint 三条 warning 级治理探针。

验收 = 三门禁（build / vitest / lint:ui）+ playwright-mcp 按修前 45 张基线逐页复拍对照（浅/深两主题），CRUD 流程抽查。

## User Stories

1. As a 管理员，我看到模型价格列只显示「—」或真实价格，所以不会把未配置单价误读成免费。
2. As a 管理员，我在编辑模型时价格框默认留空并注明「留空 = 未配置」，所以不会无意保存出 $0.00 语义。
3. As a 管理员，我在日志列表看到失败请求的费用显示「—」而非 $0.00，所以对账时不会困惑。
4. As a 管理员，我看到失败请求的首字/首答耗时显示「—」，所以不会以为是极快的响应。
5. As a 管理员，概览页列出的每个 API 端点都真实可用，所以照着接入不会撞 404/405。
6. As a 管理员，概览页能看到认证方式说明（Bearer 用户密钥），所以接入三要素（地址/端点/密钥）一眼齐。
7. As a 管理员，删除/封禁/重置类确认弹窗的标题是问句（「删除密钥 test-key？」），所以确信自己确认的是什么。
8. As a 管理员，所有危险确认是同一个组件同一套行为（键盘焦点、Escape、按钮角色一致），所以肌肉记忆通用。
9. As a 管理员，渠道删除确认如实说明"模型会一并删除、被组合引用时会拒绝"，所以删除前知道后果与前置条件。
10. As a 管理员，连接状态卡在从未测试时不渲染空的消息条，所以不会看到一个无意义的灰块。
11. As a 键盘/读屏用户，所有确认弹窗暴露同一语义角色（alertdialog）与可访问名，所以辅助技术行为可预期。
12. As a 键盘/读屏用户，主题切换按钮有稳定的可访问名，所以不用视觉也能切换主题。
13. As a 长时间使用管理台的运营者，全站字距只有规范三值（正常/−0.01em/0.08em），所以页面之间没有"字都不一样"的廉价感。
14. As a 长时间使用管理台的运营者，每页大标题出自同一个 PageHeader 基元（标题/说明/动作区），所以页头永远对齐、间距一致。
15. As a 运营者，加载屏显示正式产品 logo 与加载指示，所以硬刷新时不会看到来历不明的字母和无限闪烁。
16. As a 运营者，数据看板标题直接是「数据看板 + 时间范围」，所以每页进入看到的是上下文而不是寒暄。
17. As a 运营者，登录页没有装饰性光斑，所以视觉焦点落在产品示例卡上。
18. As a 移动端/窄屏使用者，试验参数面板按控件性质分行且窄屏退化为 1–2 列，所以不会看到挤压错位。
19. As a 调试模型的运营者，参数面板里中文业务名为主、参数名为等宽副注，所以既看得懂又能对上 API 字段。
20. As a 调试模型的运营者，参数面板的提示走统一的 tooltip 基元，所以悬停行为全站一致。
21. As a 调用方开发者，Windows 上全局搜索显示 Ctrl+K（Mac 显示 ⌘K），所以看到的快捷键与实际一致。
22. As a 调用方开发者，概览页的 curl 示例复制行为与文案和全站一致，所以建立信任。
23. As a 前端维护者，启停控件只有 `ui/switch` 一个实现，所以改一处样式全站生效。
24. As a 前端维护者，六个零调用的死基元被移除并登记，所以不会误以为它们是被支持的公共 API。
25. As a 前端维护者，新页面接入 PageHeader 基元就能得到规范页头，所以不需要翻旧页抄 className。
26. As a 前端维护者，lint 对手写基元语义/越层 Radix/transition-all 给出 warning，所以回潮在保存时就可见。
27. As a 前端维护者，新增 localStorage 键的规范指向 `lib/storage.ts` 单源，所以存储不会碎片化。
28. As a 审计/运维，修前 45 张截图基线与修后复拍逐页对照，所以每项修复可验证、无回归。
29. As a 运营者，看板图表与试验页成本显示的口径一致（「未配置单价」或「—」），所以跨页数字不打架。
30. As a 运营者，桑基图在深色主题下流量丝带可辨认，所以夜间值班时能读图。

## Implementation Decisions

- **分批**：批一=语义类（上列用户故事 1–12、28 相关行为）；批二=视觉/治理类（13–27、30）。批内自包含可回滚；两批都落在当前分支。
- **价格口径（ADR-082）**：0 = 未配置是正式语义；展示层统一走 `lib/format.ts` 既有判断（有词元消耗而费用为 0 ⇒ 未配置）；不改接口字段与 schema；不支持显式标价 0，将来需要走 nullable price 独立变更。
- **表单语义**：模型编辑的价格输入留空 + 留空说明（同缓存价「留空 = 按输入价」的既有模式）；留空提交按 0 存储，与现状兼容。
- **确认弹窗（ADR-081 关联）**：危险确认统一 `ui/confirm-dialog` 单实现；确认框底层角色统一为 alertdialog 语义；全站确认标题问句化（keys 删除/重置、catalog 封禁、渠道封禁、组合删除等逐条过）；`AlertDialog` 原语只保留给非破坏性提示。
- **PageHeader 基元（新建，B 类并入）**：props 为 标题/说明/动作区/徽标插槽；8 个页面 + 预留位接入；`text-h1` 与 −0.01em 字距只在该基元出现；登录页维持登记例外但字号归 `text-h1`。
- **字距收敛**：全站只允许 正常 / −0.01em（大标题）/ 0.08em（全大写小标签）三值；`uppercase` 仅用于既有的小标签模式且中文禁 uppercase。
- **组件来源治理（ADR-081）**：EnabledToggle 重写为组合 `ui/switch`；组合编辑器内手写 tablist 换 `ui/tabs`；日志 RoutePopover 换 `ui/popover`；试验与模型两处 combobox 收敛到 `ui/combobox`；删除 5 个零调用基元（avatar/breadcrumb/form/progress/scroll-area）并登记 §3 已删除。
- **lint 探针（warning 级，不拦截）**：`lint-ui.mjs` 新增三条——ui 外手写基元语义、ui 外 Radix import、应用层 `transition-all`；现存违例在本批清零，`--max-warnings 19` 不变。
- **加载屏**：复用侧栏同款正式 logo + `ui/spinner`，删除裸字母与 `animate-ping`。
- **概览端点卡**：删除两个未实现端点行，补一行认证说明（Bearer 用户密钥 + 指向用户密钥页），保持三行节奏。
- **参数面板重排（P7）**：放弃全面板统一网格，按"同质控件一行"组织——textarea 类整行、短输入同行等高、checkbox 与同高控件为伴、组间 16px/组内 12px、中文主标签 + mono 参数名副注、提示走 `ui/tooltip`、全部经 `ui/field`。
- **动效**：过渡具名化（禁应用层 `transition-all`）、入场动画全站仅 `animate-fade-up` ≤200ms、登录卡去掉入场延迟。
- **杂项口径**：失败行耗时「—」；连接状态卡空消息不渲染容器；sparkle 类装饰图标删除（功能状态图标豁免需注释）；看板问候语删除，副标题直接以时间范围开头；登录 h1 归 `text-h1`、SLIPSTREAM 字距统一；playground 历史键迁移或 storage.ts 注释更正（二选一，倾向修注释保现状键）；ui.md 画廊句已在规范修订中更正。
- **每批完成跑一次 playwright-mcp 复拍**（修前基线 45 张：`.scratch/ui-audit/shots/`），逐项对照 audit.md 清单后提交。

## Testing Decisions

- **测试缝（沿用 grilling Q7 裁定，不新增缝）**：
  1. **vitest 组件测试**（唯一自动化缝，现有 36 文件/359 用例的既定模式）：新增语义断言——价格/耗时渲染禁 `$0.00`/`0.00s`（走 `lib/format` 判断）、确认弹窗唯一实现与 alertdialog 角色、PageHeader 渲染契约、EnabledToggle 基于 `ui/switch` 的行为等价。既有组件测试（channel-models、channel-detail-flow 等）为 prior art。
  2. **lint:ui 门禁**：三条 warning 级治理探针即规范的可执行形态；19 上限不变。
  3. **playwright-mcp 人工复拍**（验收证据，非自动化回归）：按修前基线逐页对照浅/深两主题 + CRUD 流程抽查；不重新引入 Playwright npm 依赖。
- 只测外部可见行为（渲染文本/角色/状态迁移），不测内部实现；现有测试对文案的硬断言随本批语义更新，属规格变化而非放水。

## Out of Scope

- 价格列 nullable 化与"显式标价免费"能力（将来独立 ADR）。
- 重新引入 Playwright 自动化回归 / 截图基线（仓库 2026-11-18 已裁决移除）。
- 桑基图结构性重构（本轮只做深色对比度微调；颜色按当期请求量重排的 R3 缺陷另有登记）。
- 后端行为变更：本批全部为前端展示/交互层；后端 0 价计费语义不动（ADR-082 维持现状）。
- 移动端整体重设计（仅在既有断点内保证不破）。
- `/dev/ui` 画廊（在内部开发仓，生产仓不建）。
- 性能与信息架构调整（另有 PERFORMANCE_REPORT 与 ADR-077 体系）。

## Further Notes

- 修复前证据：`.scratch/ui-unslop-audit/audit.md`（四轮合并：默认态 21 张、交互态 12 张、CRUD 实操、参数面板补充）+ `verification.md`（独立核验，14 条实锤 + N1–N8）。
- 规范文本已先于代码落地：`docs/ui.md`（§2.1 治理 / §2.2 动效 / §2.3 确认弹窗 / §2.4 数据口径 / §2.5 门禁探针）、ADR-081、ADR-082、GLOSSARY「未配置单价」。代码修复即"向规范对齐"，不再有口径争议空间。
- 正确示范锚点（修复向其对齐，不发明）：日志详情抽屉与试验页的「未配置单价」、models 页确认框问句标题、改写规则行内校验、设置卡具名保存按钮、密钥一次性揭示弹窗。
- 两批各以独立提交落库；第二批完成时 lint 探针 0 命中、全站字距三值、死基元清零。
