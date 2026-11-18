# 05: 确认弹窗统一 + 问句标题

**What to build:** 全站危险确认（删除渠道/模型/组合/密钥、封禁、重置密钥）是同一个组件同一套行为：底层语义角色统一为 alertdialog，标题是问句（「删除密钥 sk-xxx…？」），正文写不可逆后果，取消在左、danger 在右。`AlertDialog` 原语只保留给非破坏性提示。消除现存双轨（模型/密钥删除确认是 alertdialog、渠道删除确认是 dialog——自动化与键盘用户体验分裂的实证）。

**Blocked by:** None (can start immediately)

**Status:** resolved

依据：audit.md C9/C10/R1/R2/N7；`docs/ui.md` §2.3。

- [x] keys 页删除/重置两个手写 AlertDialog 换统一实现，标题问句化
- [x] catalog 封禁确认、渠道封禁/删除、组合删除标题逐一问句化（保持现有双出口语义不变）
- [x] 危险按钮前景 `--primary-on`，同一控件不混用两种危险色
- [x] vitest 断言：危险确认弹窗角色唯一（alertdialog）且可访问名以问号收尾的标题规范
- [x] 全站清点无第二套危险确认实现
- [x] build + vitest + lint 绿；删除/封禁/重置三条路径复拍
