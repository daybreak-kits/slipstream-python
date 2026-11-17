# 10: 登录页工艺

**What to build:** 登录页在保留登记例外（固定深色左栏 + 路由追踪卡）的前提下去 slop：删除两个装饰性模糊光斑（蓝图网格与追踪卡成为唯一视觉层次）；右栏 h1 归 `text-h1` 字阶；「SLIPSTREAM」字距统一为规范值（左右栏一致）；密码占位符精简；登录卡不再做入场延迟（首屏可交互优先）。用户感知：页面更安静，示例卡独挑大梁。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

依据：audit.md B7/C11/N2（`shots/01`、`21` 实证 blur 光斑）。

- [ ] 两个 blur 光斑元素删除，页面视觉层次 = 网格 + 追踪卡
- [ ] 右栏 h1 字号归字阶（22/28），`text-4xl` 清除
- [ ] SLIPSTREAM 两个取值（0.18em/0.16em）统一为规范值
- [ ] 密码占位符精简（快捷键/操作说明不入 placeholder）
- [ ] 登录卡无入场延迟（或 ≤150ms 且不遮蔽可交互性）
- [ ] build + vitest + lint 绿；登录页浅/深复拍（基线 shots/01、21）
