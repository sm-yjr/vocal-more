# Vocal More 0.6.0 听写延迟诊断与 0.6.1 修复

0.6.1 处理 0.6.0 桌面路径中可确认的同步等待，并增加可选、无内容的分阶段时延指标。用户明确跳过本轮真实桌面、麦克风和在线端到端验收；不能据此宣称现场卡顿完全解决或云端识别提速。

## 分阶段定位

- Fn 接收：Quartz 线程生成事件并放入有界队列；350 ms 是免提松键判定，不是按键启动前等待。保留手势与取消代次，增加 hotkey_to_main / main_to_backend 指标。
- 主线程与胶囊：原 state_changed 重建菜单并重新配置热键和 updater，改为仅 set_status；增加 backend_to_main、hotkey_to_capsule、状态和预览 UI 指标。活动期间持有 App Nap 令牌，空闲和清理完成后释放；其现场影响尚未测量。
- 音频与网络：核心原本已并行采集和云握手，没有改动设备、音频算法或模型。增加 source ready / first PCM / ASR ready 指标。
- 流式识别与结束：保留最终 Omni 文本与早期预览的语义，保留 FIFO finish、重叠封存与识别等待、durable commit 后发布结果；增加 finish_to_asr_done、durable_commit、finish_to_final_result 指标。
- 输出：默认兼容粘贴原来在 AppKit 主线程 sleep(50 ms)，改成定时事务。单一 FIFO 保持到真实 post/rollback，在注入前重查取消代次与剪贴板所有权/载荷；外部写入优先，取消时回滚，600 ms 读取宽限从实际 post 开始。退出仍等待清理，并保留有界恢复重试。
- 可选词典学习：AX 主机兜底原为 5 秒；现总预算为 150 ms，包含排队，单次 IPC 不超过 40 ms 或剩余预算。过期快照丢弃，超时跳过当次学习并继续输出；无法确认 subrole 时不读取 AXValue。没有更改全局学习设置。

相对 v0.5.1，core/provider/application 听写生产实现没有变化，backend 配置主要差别是更新通道。若对照 v0.5.0，b537df6 在 0.5.1 中把 Omni 早期转写子模型从 gummy-realtime-v1 改为 qwen3-asr-flash-realtime；未检查用户实际模型或执行在线测试，不能归因于这一变化。本次不回退模型。

## 局部对照与验证边界

| 对照 | 发布旧路径 / 基线 | 修复路径 | 方法与限制 |
| --- | ---: | ---: | --- |
| 菜单状态更新中位 / p95 | 1.154 / 1.216 ms | 0.273 / 0.297 ms | 160 次合成状态，真实非激活 AppKit，仅菜单部分 |
| 兼容事务开始返回中位 | 60.043 ms | 0.006 ms | 12 次 mock；旧路径包含 50 ms sleep，负载下可能超时；新路径仍保留 50 ms 稳定窗口，未测系统剪贴板 IO |
| AX 等待预算 | 5,000 ms | 150 ms | 策略及虚拟时钟回归，不是现场测量；需要主线程可调度 |
| 离线 start RPC 中位 | 1.243 ms | 1.251 ms | 8 次合成静音 PCM、loopback provider 和临时历史 |
| 离线 stop 到 durable final 中位 | 48.683 ms | 54.458 ms | 负载、文件同步和 mock 调度影响小样本，没有后端提速证据 |
| 离线 claim + prepare 中位 | 0.227 ms | 0.162 ms | 未做原生注入，不能外推目标应用 |

C ABI mock 故意延迟握手 600 ms，首块 PCM 在 43.354 ms 到达且首段完整保留，证明采集不等待握手；真实设备耗时未知。过期 AX 快照回归在准确发布基线 09977b7 失败，在修复版通过。原有取消、队列、durable 失败和音频恢复测试保留。快速连续切换的回归在旧活动标记清理条件下失败，修复后通过；只有匹配 request ID 和 epoch 的终结响应释放标记，真实开始响应仍保留到胶囊展示。

本地源码验证：Rust workspace 160 passed / 0 failed / 0 ignored；Python 分两部分合计 1086 passed / 11 skipped。核心 1.90 和桌面 1.98 Clippy、格式、release 构建、合成慢 Cocoa 下的原生退出验收通过。这些不等于用户桌面或真实听写验收。正式交付还须以最终提交的 CI、签名、公证和公开资产验证为准。

## 复现

使用独立 CARGO_TARGET_DIR，不与发布基线共用产物；macOS 设置 MACOSX_DEPLOYMENT_TARGET=14.0。测试仅生成夹具并使用 loopback，需允许本地 socket；不需要真实 API key 或麦克风。

```sh
cargo +1.98.1 fmt --all --manifest-path rust/Cargo.toml -- --check
cargo +1.90.0 clippy --offline --locked --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo +1.98.1 clippy --offline --locked --manifest-path rust/Cargo.toml -p vocal-more-desktop --features ui-test,perf-test --all-targets --no-deps -- -D warnings
cargo +1.98.1 test --offline --locked --manifest-path rust/Cargo.toml --workspace
cargo +1.98.1 test --offline --locked --manifest-path rust/Cargo.toml -p vocal-more-backend --test application_integration offline_dictation_latency_probe -- --nocapture
cargo +1.98.1 test --offline --locked --manifest-path rust/Cargo.toml -p vocal-more-desktop --lib compatibility_microbenchmark_uses_only_fixture_board -- --nocapture
benchmark_root=$(mktemp -d)
cargo +1.98.1 run --offline --locked --manifest-path rust/Cargo.toml -p vocal-more-desktop --features perf-test --bin vocal-more-latency-fixture -- "$benchmark_root/data"
```

VOCAL_MORE_TRACE_TIMINGS=1 默认关闭，启用后仅记录固定阶段名与耗时，不记录音频、听写文本、目标应用文本、路径、模型配置或凭据。hotkey_to_capsule 截止于 orderFront 返回，不代表合成器已呈现在屏幕上。
