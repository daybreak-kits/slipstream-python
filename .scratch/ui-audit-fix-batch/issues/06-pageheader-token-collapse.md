# 06: PageHeader 基元 + 全站 token 收敛

**What to build:** 新建 PageHeader 基元（B 类并入：标题/说明/动作区/徽标插槽，`text-h1` + −0.01em 字距只在此出现），8 个页面 + 占位页接入；同时全站字距收敛到规范三值（正常 / −0.01em / 0.08em），`metric-pills` 的 `text-[10.5px]`→`text-2xs`、`text-[19px]`→`text-metric`。用户感知：每页页头对齐一致，字号字距全站统一。

**Blocked by:** None (can start immediately)

**Status:** resolved

依据：audit.md A1/A2/P3；`docs/ui.md` §2.2、§3。

- [x] PageHeader 基元落位并登记 ui.md §3 B 类
- [x] 8 个页面 + 占位页接入（数据看板/概览/日志/渠道/渠道详情/模型/密钥/设置/试验）
- [x] 全站 tracking 仅剩三值；13 处 `[-0.02em]`、9 处越界值清零
- [x] metric-pills 两处任意字号归 token；relaxed 桶计数相应下降且 ≤19
- [x] vitest：PageHeader 渲染契约测试
- [x] build + vitest + lint 绿；8 页浅/深复拍对照
