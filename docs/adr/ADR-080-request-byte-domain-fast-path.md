# ADR-080 请求侧字节域快路径与流式跟踪字节门（P2）

---
status: accepted
date: 2026-11-19
关联：ADR-074（头透传）、ADR-078（流式改写字节域）、ADR-079（body 有界渲染）、`docs/upstream_transport.md` §2
---

## 背景

memray 归因（§12.3）：每在飞请求持有 2–3 份 MB 级大块（客户端原始 bytes、解析后 dict、上游重建 dict + 编码 bytes），逐帧 churn（jsonfast.loads ×3.28M 次/趟 = 12.8GB）撕裂 glibc arena——RSS 平台 851MB 的主因；py-spy 归因：200KB 请求体在 httpx 内部被 stdlib json 二次编码占主循环 8.3%。

## 决策

1. **请求侧字节域快路径**：同时满足六条资格（httpx 臂；渠道无 request_body 改写规则；bytes 无 `b"null"` 与 BOM；model needle 宽松正则**恰好命中一次**且紧凑拼写命中；stream 请求 bytes 无 `stream_options` 且 `stream is True`）时，上游请求体 = 客户端原始 bytes 仅替换 model needle、末尾注入 stream_options。**任一资格不满足 → 回退 dict 路径**（与今天逐字节一致，`upstream_content=None` 已由测试钉住）。
2. **needle 命中数 ==1 加严**：命中 >1 说明嵌套对象里有同值 model（replace 会误伤），=1 但紧凑拼写不命中说明顶层是非紧凑拼写（替换点不确定）——都回退。
3. **流式跟踪字节门**：`_track_line` 解析前做 needle 扫描（usage/error/finish_reason/content/reasoning/tool_calls/**function_call**（has_answer_surface 的 TTFA 口径依赖）+条件 id），无命中的帧跳过解析；line-mode 改写消费 payload 时由 `rewrite_line` 兜底解析。
4. **快照存切片 bytes**：快路径日志快照存改写后 bytes 的头部切片（渲染预算内），渲染走 bytes 分支；上游收完整 bytes。

## 理由

- 每在飞请求的大块分配 3→2 份（dict + 客户端 bytes；快照与 httpx 请求共享），消灭 per-request 1.2MB 级 dumps——450MB arena 滞留的撕裂源。
- needle 安全论证与 ADR-078 相同（JSON 字符串内引号必转义，未转义序列只可能是结构位）+ 恰好一次命中消除嵌套误伤。

## 后果与影响

- 已知行为变化：①快路径下日志页 `upstream_body` 为「客户端原样 JSON（model 已改）头部切片」，不再是规范化 JSON；②打码从 dict 感知 `redact()` 变为文本 `redact_text()`（凭证形态照常打码，测试钉住）；③字节门对 `\uXXXX` 转义键名的非常规上游跳过解析（与 ADR-078 needle miss 同级，usage 走估算兜底）。
- 同箱内部 A/B（夜间箱态劣化窗口内测得）：流式 64 QPS CPU/ok 24.1 → **17.7ms**（快路径 vs dict 路径）；RSS 重载平台 999 → **783MB**、HWM 903 → 834。对 P1.5 下午干净数字的最终验收待安静窗口复跑（今晚箱态全体 ~2× 劣化，连 ffe8a9c 原样重跑也复现不了下午数字——环境性，代码由 stash 对照排除）。
- 回滚：revert 本单即可；`upstream_content=None` 路径与改动前逐字节一致。

## 相关代码与文档

- `gateway.py`：`fast_forward_content`、`complete/open_stream` 的 `upstream_content` 参数、`_frame_may_matter` + `_TRACK_NEEDLES_*`、`_track_line` 字节门。
- `chat.py`：invoke 内逐 candidate 快路径、快照切片；`rewrite.py`：`PreparedRequest.no_body_rules`；`v1.py`：`(parsed, raw_body)`；`body_logging.py`：render_value bytes 分支。
- 测试：`test_httpx_transport.py` +17 条（命中/回退/注入/门/兜底）。
