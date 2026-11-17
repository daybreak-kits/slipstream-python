# 像素级 / 交互级核验报告 — 驭流管理台（2026-11-18）

> 对 `audit.md` 的 14 条发现逐条做像素级与交互级核验，并对全部 11 个页面 + 壳层做功能走查。
> 环境：Windows 主仓工作树，dev server 3690（源码态）+ 网关 3681 + 本地 mock 上游 3699；
> 种子数据：2 渠道 / 5 直连模型（含价格）+ 1 手动组合 + 4 自动组合 + 1 用户密钥 + 35 条请求日志（成功/429/503/慢流/探测混合）。
> 工具：Playwright（chromium headless 1440×900，浅/深两主题），截图 + `getComputedStyle` 探针 + 逐交互 DOM 断言。
> 截图与脚本：主仓 `.scratch/ui-verify/shots/`（60+ 张）、`.scratch/ui-verify-scripts/`。全站控制台错误 0（仅预期的 401 认证探针）。

## 一、audit.md 14 条逐条判定（全部实锤，可直接进入修复批次）

| # | 判定 | 运行时/像素证据 |
|---|---|---|
| A1 字距漂移 | ✅ 实锤 | computed 实测：8 页 h1 `letterSpacing: -0.44px`（22px × -0.02em）；侧栏分组标签 0.14em、Slipstream 0.16em、登录 eyebrow 0.18em；规范值 `[-0.01em]` 全站仅 1 处 |
| A2 PageHeader 缺失 | ✅ 实锤 | `rg PageHeader` 0 命中；8 页 DOM 手抄 `<h1>`（dashboard/logs/channels/keys/catalog?/playground/settings/overview） |
| A3 手写开关 | ✅ 实锤 | `enabled-toggle.tsx:14` `role="switch"`；vendored `ui/switch.tsx` 全仓 0 调用；唯一使用点 channel-detail |
| A4 metric-pills 10.5px | ✅ 实锤 | `metric-pills.tsx:42,67` 代码在位；**使用点仅 logs.tsx**（catalog/dashboard 不渲染）→ 修复后到日志页验证 |
| B5 加载屏占位 logo | ✅ 实锤（截图 10/05） | 像素目验：靛蓝圆角块 + 裸字母 "L" + `animate-ping` DOM 探针命中；`img[src*=logo]` false；正式 logo 仅侧栏在用。每次硬刷新可见（dev 下可见 ~1.8s） |
| B6 问候语标题 | ✅ 实锤（截图 11） | 「欢迎回来，admin。今天 · 按 Asia/Shanghai 分桶。」像素可见；用户名在右上角头像重复出现 |
| B7 登录页光斑 | ✅ 实锤（截图 01/03） | computed：`blur(120px)` 520px 靛斑 + `blur(100px)` 360px 青斑；截图可见大面积色洗 |
| B8 过渡失准 | ✅ 实锤 | `overview.tsx:384` `transition-all … group-hover:translate-x-0.5 group-hover:text-primary` 代码在位 |
| C9 keys 手写 AlertDialog | ✅ 实锤（截图 72） | 运行时：`role="alertdialog"`、标题「删除密钥」**非问句**（正确示范 models 页「删除手动组合 X？」）、取消左/danger 右、正文含不可逆后果 ✓ |
| C10 危险色混用 | ✅ 实锤 | 行内删除按钮文字色 `rgb(185,28,28)`（--danger-text）；对话框 danger 按钮前景 `rgb(255,255,255)`（--primary-on）——两种危险色并存 |
| C11 登录页字号/字距 | ✅ 实锤 | 右栏 h1 `text-2xl`（24px，越阶；规范大字号仅 22/28）；「SLIPSTREAM」左栏 0.18em / 移动端 0.16em 两值；placeholder「请输入密码」 |
| C12 ⌘K 平台失真 | ✅ 实锤 | `global-search.tsx:58` 硬编码 `⌘K`；实测 Windows `Control+K` 打开搜索 ✓（快捷键本身工作，展示失真） |
| C13 绕过格式化单源 | ✅ 实锤 | `flow-sankey.tsx:262`（scale toFixed(1)×）、`:455`（占比 toFixed(1)%）、`trend-card.tsx:1007`（toLocaleString）在位；`:441` 确认为定位计算，豁免正确 |
| C14 ui.md 画廊句失真 | ✅ 实锤 | 生产仓 `web/src` 无 `/dev/ui` 注册；ui.md §2.4 仍写「仅开发环境注册」 |

结论：**audit.md 的修复批次（10 条）全部通过核验，可以按批执行。**

## 二、功能走查（11 页 + 壳层，全部通过项）

- **登录**：空提交字段错误、错误密码 Alert（「用户名或密码错误」）、密码显隐切换、成功跳转 /overview ✓
- **壳层**：侧栏分组导航/激活态范式正确（primary-soft + 图标瓦片 + 指示条）、折叠/展开、主题菜单（浅→深即时切换，无闪烁）、时区切换（aria-label 双向）、全局搜索（打开/输入过滤 3 结果/Esc 关闭）、面包屑、env 徽标、sonner toast 字体栈正确 ✓
- **数据看板**：9 KPI 卡（text-metric 19/26 + tabular-nums 实测）、缓存 KPI 悬停四口径弹层（A/B/C/D 完整，C 无会话头显「—」、D 未配单价标注）、维度三选、图例 aria-pressed 开关、桑根 tab 懒加载 + 前 20/单位/缩放/复位、时间范围按钮、503 显「请求模型无可用渠道」语义 ✓
- **使用日志**：度量条 5 项带 hint；脏筛选三联动（aria-label「搜索（有未应用的筛选修改）」+ variant 切换 + `.filter-dirty` 描边，提交后清零）；URL 同步 `q/range/status`；状态筛选（全部/成功/失败）；更多筛选 popover；脱敏开关（费用格 `$0.00`→遮罩）；分页 2 页 + 跳页；三视图切换 + 聚合行展开 ✓
- **日志行/抽屉**：行展开、详情抽屉（基本信息/用量耗时含缓存比例/参数摘要/ATTEMPTS 时间线含「优先级路径 N」/请求头响应头三段折叠 + 来源轮次标注/改写跳过原因「流式响应跳过响应体改写」）✓
- **渠道**：批量工具栏（测速/删除）、行内优先级保存（10→7 实测）、测试连接、新建页空提交 2 条校验、详情页（最终地址预览、密钥掩码尾号 0001、协议自动探测、优先级方向文案「数值越大越先调用」、模型 4 个）✓
- **模型管理**：`?kind=` 筛选、组合编辑器（「priority 是层不是序号」口径卡逐字在位、候选 tab aria-selected、嵌套深度 1/3、failover/策略/观察窗覆盖）、封禁确认框（封禁永久 vs 冷却定时双出口，语义与 GLOSSARY 一致）、可见状态徽标（半开/可用 soft 底深字）、直连行三态操作列（半开→「启用」仅复位、可用→「封禁」）✓
- **用户密钥**：复制 toast「已复制密钥」、新建对话框说明文案 ✓
- **模型试验**：模型 combobox、流式发送、停止按钮流中可见、回复统计（词元/成本 $0.00026/TTFT/吞吐/req id/重试）、无思考块显式标注「无思考块」、参数面板「只对下一次请求生效」、历史独立 localStorage ✓
- **系统设置**：网关地址/看门狗三超时/路由策略与准入（与 ADR-077 一致）、脱敏不可关闭说明 ✓
- **全局概览**：连接地址、curl 示例、复制 toast「已复制到剪贴板」、三统计卡、开始使用三步 ✓

## 三、新发现（audit 之外）

- **N1 · P1（能力失真，建议并入修复批次）**：全局概览「API 端点」列出 `POST /v1/messages`（Claude Messages 协议）与 `POST /v1beta/models/{model}:generateContent`（Gemini 原生协议）——实测均 **404**（后端入站仅 `chat/completions` + `models`，ADR-011 未实现入站）。`overview.tsx:44-67` 的 ENDPOINTS 数组宣传了不存在的能力。修复：删除两行，或明确标注「未开放」。
- **N2 · P3**：登录表单容器 `animate-fade-up` 从 opacity 0 起步，dev 模式实测 load 后 >600ms 表单才可见（截图 01 全空白、03 可见）。规范允许 fade-up 200ms，但这里是「整块表单延迟入场」，首屏感知明显；建议去掉该入口动画或只留 150ms。
- **N3 · P3**：桑基图深色主题下细流量丝带近乎纯黑、难以辨认（截图 15/20）；节点列底色偏闷。属图表深色对比度打磨，不动 token 体系可解。
- **N4 · P4**：日志页「缓存命中率」hint 截断后呈「token 加权，… 3 …」，观感破碎（悬停有完整四口径，功能无损）；可微调截断宽度或 hint 文案。
- **N5 · P4**：两处 AI sparkle 图标——playground 空态（Sparkles 图标）与 overview「在模型试验中验证 ✦」。全站仅此两处，unslop 信号清单命中，量小可顺手清。
- **N6 · info**：时间预设文案「7 天 / 30 天」与 ssot §9.3 记载「最近 7 天 / 最近 30 天」轻微漂移（以代码为准，回头修文档或改文案，二选一）。
- **N7 · info**：catalog 封禁确认标题「封禁 mock/demo-chat」非问句——但它是封禁/冷却双出口选择器 + 「确认封禁」二次确认，语义成立；修 C9 统一问句规范时可一并定夺。
- **N8 · info**：playground 历史独立 key `slipstream.playground.history.v1`，与 `storage.ts` 模块注释「everything lives inside one JSON object under system.storage」不符；要么迁入 system.storage，要么修注释。

## 四、判定为干净（与 audit 一致 + 运行时复核）

无 emoji、无感叹号、无辉光/渐变文字、无 `window.confirm`；表格 tabular-nums 到位；SELECT_ALL/NONE 哨兵单源；危险操作 100% 二次确认；侧栏激活态范式正确；登录页路由追踪卡（moment）数据可信；状态点签名细节全站一致；浅/深两主题切换无闪烁；全站控制台错误 0。

## 五、修复批次建议（在 audit.md 十条基础上 +1）

audit.md 批次 1–10 原样可执行，另加：
11. **N1**：overview 端点清单删去两个未实现协议（或标「未开放」）——功能性失真，优先级高于纯视觉项。

验收门禁不变：`npm run build` + `npm test -- --run` + `npm run lint:ui`（19 vendored warning 预期不变）+ 逐页浅/深两主题像素对照（本次核验的 60+ 张截图可作为「修前」基线，位于主仓 `.scratch/ui-verify/shots/`）。
