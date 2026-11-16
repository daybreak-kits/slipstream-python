# litellm Rust 网关：能否构建、能否试用（2026-11-15 实测）

> 状态：研究记录，只作证据，不作权威。结论一句话：**能编译、能启动、能服务非流式 chat，但流式被显式拒绝（501），对我们的工作负载不可用。**

## 1. 先分清两个「litellm Rust」

| 对象 | 是什么 | 能不能用 |
|---|---|---|
| PyPI/crates.io 上的 `litellm` crate 1,662 字节 | `#![no_std]` 空壳，挂牌占位 | ❌ 完全不可用 |
| BerriAI/litellm 仓库内 `litellm/rust/`（78 个 crate，含 `litellm-gateway` v0.1.0，MIT） | 真的独立网关二进制 | ✅ 本次实测编译并运行 |
| 已安装的 Python 包 `litellm` 1.108.4 的 `rust_bridge/_native.pyd`（41.6 MB） | 只为少数路径加速（OCR、bedrock transcription 等） | ❌ 不服务 chat：`litellm/rust_bridge/catalog.py` 里 `CHAT_COMPLETIONS -> PYTHON_ONLY`（embeddings/responses/token_counter/tokenizer 同） |

把 `LITELLM_RUST` 环境变量打开也只是翻转 RUST_OPT_IN/OUT 名单（OCR、bedrock transcription、1.109.1 的 anthropic messages），**chat completions 永远不会走 Rust**。所以「升级 Python 包换来更快的 chat」这条路不存在。

## 2. Windows 上怎么编译成功的

本机基线：无 cargo/rustc、无 MSVC/cl、无 gcc、无 docker。

1. `rustup-init.exe` 装 `1.98.0-x86_64-pc-windows-gnu` 工具链（仓库 `rust-toolchain.toml` 钉的版本）。
2. **坑 1**：MSYS 把 `CARGO_HOME=/c/...` 解释成 `C:/c/...` 传给 Windows 程序，会把 cargo 装到 `C:\c\Users\...`。修法：不设路径类环境变量，直接用 `$HOME/.cargo/bin/*.exe` 绝对路径。
3. **坑 2**：第一次构建失败 `error calling dlltool 'dlltool.exe': program not found`（`getrandom`/`windows-sys` 需要 import lib，而 rustup 的 gnu 工具链不带 dlltool）。
4. 修法：下载 winlibs 免安装 MinGW-w64，把其 `bin` 前置到 PATH，再 `cargo build --release --locked -p litellm-gateway`。
5. 结果：**`Finished \`release\` profile [optimized] target(s) in 43m 00s`，产物 `litellm-gateway.exe` 36,165,120 字节（34.5 MB）**。release profile 是 `lto = "fat"` + `codegen-units = 1`，4 核机器冷编译 43 分钟属正常。

## 3. 跑起来：配置与实测

配置（`LITELLM_CONFIG` 指向它，另需 `HOST`/`PORT`/`RUST_LOG`，`general_settings.master_key` 作鉴权）：

```yaml
model_list:
  - model_name: mock-gpt
    litellm_params:
      model: mock-gpt
      custom_llm_provider: openai_like      # 关键：写 openai 会 400 invalid provider
      api_base: http://127.0.0.1:3997/v1
      api_key: sk-mock
general_settings:
  master_key: sk-1234
```

**坑 3**：`custom_llm_provider: openai` → `400 {"error":{"message":"invalid provider: openai"}}`（0.06 ms 内即失败，是配置解析期报错，不是调用期）。换成 `openai_like` 才通。上游 OpenAI 兼容端点应当统一用 `openai_like`。

### 实测数字（Windows 10 / i7-10510U，本机 loopback，mock 上游）

| 路径 | 非流式 p50 | 说明 |
|---|---|---|
| 直连 mock（keep-alive） | 0.88 ms | 基线 |
| 经 Rust 网关（keep-alive） | **27–32 ms** | 每次请求多约 25–30 ms |
| 直连 mock 10 并发 wall | — | 毫秒级 |
| 经 Rust 网关 10 并发 wall | **364 ms**（p50 233 ms、max 358 ms） | 近似串行，未并行 |

上游自测（BerriAI/ai-gateway-bench，50 rps、4 核 pod、Linux）报的是 overhead p50 0.9 ms / p99 1.8 ms。**本机与上游差 30 倍**，只能说明与平台相关（Windows IOCP/运行时差异或该构建的 runtime 配置），不能直接否定上游数字；但**在我方部署环境就是 25–30 ms**。

### 致命的这一条

`stream: true` 请求返回：

```json
{"error":{"message":"declined by the rust path: streaming","type":"api_error","param":null,"code":501}}
```

即 **Rust 网关显式拒绝流式请求**（501，不是不支持而是主动声明 decline）。我们的 chat 全部是 SSE 流式，所以这条路在我们的负载上**直接不可用**，无需再比性能。

## 4. 结论与建议

1. **不要押注 litellm 的 Rust 网关替代 Python 侧 chat 路径**——与 `rust_bridge/catalog.py` 把 chat 标为 PYTHON_ONLY 是同一个事实的两种表现。
2. **可以留作客户端臂对照**：它是个能跑的标准 OpenAI 兼容服务端，用来分离「客户端 SDK 成本」与「传输成本」是合格的（这也是本次唯一采用的用途）。
3. 若将来想用，必须先有这三个前提：`openai_like` 配置口径、流式支持落地、Windows 下每请求 25–30 ms 的来源定位（怀疑是 runtime 在该平台的每请求固定开销）。
4. 复现路径与脚本在本机 `.scratch/rust-gateway/`（`gw.yaml`、`bench.py`、`bench2.py`、`bench3.py`、`gw.log`），**均不进版本库**。
