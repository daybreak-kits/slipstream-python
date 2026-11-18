# 03: 概览端点卡对齐后端实现

**What to build:** 全局概览的「API 端点」卡片只列后端真实提供的端点（POST /v1/chat/completions、GET /v1/models），删除 Claude Messages 与 Gemini 两个未实现条目；补一行认证说明（`Authorization: Bearer <用户密钥> · 在用户密钥页签发`）保持三行节奏，并与「开始使用」第 2 步衔接。页面宣传与后端逐项一致（ADR：能力宣传=事实，`docs/ui.md` §2.4）。

**Blocked by:** None (can start immediately)

**Status:** resolved

依据：audit.md N1（实测两端点不可用，后端仅注册两个路由）。

- [x] 端点列表仅含两个真实端点，各保留方法徽标与说明
- [x] 新增认证说明行（事实性文案，不宣传未实现能力）
- [x] 卡片与右侧「开始使用」卡视觉平衡（三行节奏）
- [x] 页面无「未开放」类占位宣传
- [x] build + vitest + lint 绿；概览页浅/深复拍（基线 shots/02、12）
