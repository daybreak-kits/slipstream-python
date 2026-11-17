# 12: 试验参数面板重排

**What to build:** 参数控制面板按"同质控件一行"重排，消除歪斜空洞：textarea 类（系统提示词/stop/额外请求字段 JSON）整行独占；短输入（temperature/top_p/top_k/seed/frequency_penalty/presence_penalty 等）同行等高等宽；checkbox（流式/logprobs）只与同高控件为伴并垂直对齐；组间 16px、组内 12px；中文业务名为主标签、参数名作等宽副注；字段提示走 `ui/tooltip`（去虚线 title 样式）；全部经 `ui/field` 基元；窄屏 1→2 列退化保持。用户感知：面板整齐、分组呼吸感清晰、窄屏不挤。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

依据：audit.md P7（`shots/43` 实证 + 病根：全面板统一 6 列网格 `chat-params-panel` 网格常量）。

- [ ] 网格按行重组：无跨性质控件同格；textarea 全部整行
- [ ] 六个采样输入同行等高；checkbox 行对齐
- [ ] JSON 框整行，不再留右侧空洞
- [ ] 标签体系统一（中文主标 + mono 参数副注 + tooltip 提示）
- [ ] 窄屏（sm 以下）单列退化，无横向滚动
- [ ] vitest：面板渲染契约（分组标题/字段存在性）回归通过
- [ ] build + vitest + lint 绿；面板展开态浅/深复拍（基线 shots/43）
