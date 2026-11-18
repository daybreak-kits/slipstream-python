# 11: 零散视觉与文案清理

**What to build:** 一组小而独立的 slop 清理，每项独立可验：加载屏换正式产品 logo + spinner（删裸字母 "L" 与无限 ping 光圈）；数据看板删除问候语（副标题直接以时间范围开头）；overview 统计卡悬停箭头过渡具名化（`transition-[transform,color]`）；两处 AI sparkle 装饰图标清理（试验空态与概览「在模型试验中验证」；组合标记的 Wand2 为功能性图标豁免）；全局搜索快捷键按平台显示（Mac ⌘K / 其他 Ctrl+K）；时间预设文案与 ssot 措辞对齐；playground 历史键与 storage.ts 注释矛盾消解（修注释保现状键）。

**Blocked by:** None (can start immediately)

**Status:** resolved

依据：audit.md B5/B6/B8/N5/C12/N6/N8。

- [x] 加载屏：正式 logo + spinner，`animate-ping` 与裸字母清除
- [x] 看板副标题 = 时间范围 + 分桶说明，无问候语
- [x] overview 箭头 hover 行为不变、过渡具名
- [x] 两处 sparkle 删除或换语义图标；Wand2 保留并带豁免注释
- [x] 快捷键提示按平台渲染（navigator.platform 判定）
- [x] 「7 天/30 天」文案与 ssot §9.3 一致（二选一后回改文档）
- [x] storage.ts 注释与 playground 历史键关系说明更正
- [x] build + vitest + lint 绿；登录加载/看板/概览/试验复拍
