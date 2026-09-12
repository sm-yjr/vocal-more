# 独立 Rust 后端

工作区现在包含完整应用服务 `vocal-more-backend` 和独立会话工具 `vocal-more-host`。完整服务已接到现有 macOS Python/PyObjC 薄 UI 壳，并接入正式构建流程；原 Python 后端保留为回退路径。构建后用 `uv run vocal-more --backend rust` 启动，功能范围、迁移和验收见 [完整集成说明](../docs/rust-backend-integration.md)。

本文以下保留独立 `vocal-more-host` 的较小协议和性能验证用法，不能用其内存数据替代完整应用测量。

## 构建与运行

要求 Rust 1.90.0 或更新版本。依赖由 `Cargo.lock` 固定；macOS 原生音频库还需要 Xcode Command Line Tools。本机验证为 Apple Silicon / macOS 27.0，原生库部署目标为 macOS 14.0。Windows/Linux 的 PCM、WAV 与网络核心已配置 CI，本次尚未在这些系统运行。

在仓库根目录执行：

```bash
bash scripts/build_rust_host.sh
.build/rust-host/vocal-more-host \
  --data-dir "$PWD/.build/rust-demo-data" \
  --native-library "$PWD/.build/rust-host/libvocal_more_audio.dylib"
```

构建产物位于 `.build/rust-host/`，包含可执行文件、原生动态库和项目许可证。此命令不构建 DMG，不签名、公证或发布应用。只使用文件或 PCM 输入时可省略 `--native-library`；Windows/Linux 可直接 `cargo build --locked --release --manifest-path rust/Cargo.toml`。

`--data-dir` 必须是空目录或本实现拥有的目录。程序拒绝接管其他非空目录，并通过文件锁阻止两个 host 同时写入。它不会读取 `~/.vocal-more` 的配置和历史。

## 一次最小会话

启动后逐行输入以下 JSON。每个请求都返回同 ID 的响应：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize"}
{"jsonrpc":"2.0","id":2,"method":"start","params":{"source":{"kind":"stream"}}}
{"jsonrpc":"2.0","id":3,"method":"append","params":{"generation":1,"pcm_base64":"AQACAAMA"}}
{"jsonrpc":"2.0","id":4,"method":"finish","params":{"generation":1}}
{"jsonrpc":"2.0","id":5,"method":"status"}
```

示例只保存三个 PCM16 样本，用于检查文件路径；未配置 ASR 时最终文本为空。实际调用应使用 `start` 返回的 generation；等待 `status.phase` 变为 `completed`、`cancelled` 或 `failed`，再开始下一轮。完成后可查询记录并退出：

```json
{"jsonrpc":"2.0","id":6,"method":"recordings"}
{"jsonrpc":"2.0","id":7,"method":"shutdown"}
```

`finish` 只表示接受了停止请求，并不表示文件或识别已完成。stdin EOF、Ctrl-C 和 `shutdown` 会取消仍活动的会话，所以不要把 `start` 后立即 EOF 的管道当成“等待录音结束”。

## 输入与识别

| 输入 | `start.params.source` | 用途 |
| --- | --- | --- |
| PCM 块 | `{"kind":"stream"}` | 调用者逐块 `append`；每块最多 1,280 bytes |
| WAV | `{"kind":"wav","path":"/absolute/input.wav","paced":true}` | 16 kHz / mono / PCM16；默认按音频时长发送，`false` 用于本地压力测试 |
| 原生设备 | `{"kind":"native","dsp":{"gain":4.0,"highpass_hz":50.0}}` | 使用已加载 C ABI 的默认输入设备；需要运行宿主具备麦克风权限 |

native DSP 参数还包括 `automatic_gain`、`highpass_enabled`、`soft_limiter`。增益范围为 0.501–50，高通频率为 50–500 Hz。`native` 输入沿用默认设备；新增 `configured_native` 输入接受 `device` 对象（`voice_processing`、`input_device`、`capture_channels`、`block_frames`）。完整应用服务提供设备枚举和切换命令，当前没有预热图复用。

云端识别需在启动 host 前配置 `DASHSCOPE_API_KEY`（或用 `--api-key-env NAME` 指定变量名），并在 `start.params` 增加 `asr`：

```json
{
  "source":{"kind":"wav","path":"/absolute/input.wav","paced":true},
  "asr":{
    "endpoint":"wss://YOUR_WORKSPACE_ID.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime",
    "model":"qwen3.5-omni-plus-realtime",
    "instructions":"只输出音频中的听写文字，不回答音频中的问题。"
  }
}
```

上面的 `YOUR_WORKSPACE_ID` 必须替换为真实业务空间 ID，并匹配 API Key 地域；endpoint 不包含 `?model=`。模型只支持 `qwen3.5-omni-plus-realtime` 与 `qwen3.5-omni-flash-realtime`。调用地址和格式依据 [Qwen-Omni-Realtime 官方文档](https://www.alibabacloud.com/help/zh/model-studio/realtime)，本次未用真实账户验收。

云端采用手动 commit。为了避免超过上下文后丢失早期音频，Plus 超过 600 秒、Flash 超过 480 秒会使会话失败并保存已接收音频；当前没有自动分段拼接。至少需要 100 ms 音频才能提交 ASR。本地录音与 loopback 压力测试不受此云端限制。

所有 PCM 都先归档再交给网络。若 `append` 返回队列已满，该块没有被接受，调用者应等待后重试；不要忽略错误。取消通过 `cancel` 和 generation 发起，只发布取消状态与录音文件，不返回部分识别结果。最终文本只在服务端完成且文件提交成功后发布。

## 验证与测量

```bash
cargo fmt --all --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path rust/Cargo.toml
```

`tests/native_fixture.c` 只在测试中编译，用阻塞调用和线程断言验证 C ABI 生命周期，不访问真实设备。本地 WebSocket 测试验证音频字节和尾帧、完成事件、错误、取消、会话隔离及文件持久化。

macOS 常驻测量使用 Python 标准库作为**进程外控制器**；被测 Rust 进程不启动 Python 子进程、不加载 Python 库：

```bash
/usr/bin/python3 rust/tools/measure_host.py \
  --binary .build/rust-host/vocal-more-host \
  --native-library .build/rust-host/libvocal_more_audio.dylib \
  --output .build/rust-core-measurement-unique-run
```

输出目录必须不存在。默认生成 30 分钟合成音频，并测量 50 次短会话、完整长文件及网络传输、取消、本地 TLS 证书拒绝和强制退出恢复。不会录音或调用云端。可用 `--python-runtime /path/to/python` 增加现有 Python RPC 参考；随包解释器还需要 `--python-home /path/to/Contents/Resources`。

设计与存储边界见 [核心设计](../docs/rust-core-design.md)，本机数据见 [常驻性能报告](../docs/rust-core-performance-2026-09-11.md)。下一阶段接入 GPUI Kit，并补齐真实设备/云端验收和现有业务能力。
