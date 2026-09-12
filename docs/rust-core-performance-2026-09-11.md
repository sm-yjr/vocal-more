# 独立 Rust 后端：实现与常驻性能验收

2026-09-11 已完成独立 Rust 会话核心、文件化录音和 release 常驻测量。当前 Python 产品代码、配置、版本与发布路径保持原状；Rust 可执行文件不需要 Python host。

本机结果：纯 Rust 后端使用后约 **3.48 MiB physical footprint**，加载现有原生音频库后约 **4.13 MiB**。现有 Python RPC 后台在本轮为 **60.2 MiB**。这个结果支持继续迁移运行时和录音数据通路；完整 GPUI 桌面应用的总内存还需要下一阶段实测。

## 1. 测量对象与口径

- 机器：MacBookPro18,1，Apple M1 Pro，16 GiB 内存。
- 系统：macOS 27.0，build 26A428。
- Rust：1.90.0，release、thin LTO、单 codegen unit；依赖固定在 `rust/Cargo.lock`。
- Python 参考：正式包的 CPython 3.12.14 与随包依赖，加载本仓库 0.4.17 的 RPC 服务源码。仓库基线 commit 为 `084d1a86f45e6c7f8a94a9a2356561a71ed2c990`，Rust 是本次新增实现。
- 主指标：`vmmap -summary` 的 Physical footprint，按 MiB 记录；RSS 独立列出，二者不能混用。测量不包含进程外的 Python 控制器与本地 WebSocket 服务。

两种 Rust 配置使用同一可执行文件。加载原生库的配置只执行动态库加载和 ABI 校验，**没有创建真实采集图、访问麦克风或打开 GPUI 窗口**。Python 参考使用隔离目录、空 API Key，完成既有 RPC 装配和 initialize，未开始录音、ASR 预热或云端调用。

Python 参考还包含现有模式装配、历史、词典学习、重试等模块，Rust 本阶段仅包含会话、存储、音频输入适配与一条实时协议。因此此处是明确能力边界后的后台成本比较，不是完整功能等价的应用 A/B。之前约 95 MiB 的 Python 菜单应用数据也不作为净节省的分母。

## 2. 常驻与使用后状态

“未使用”取 initialize 后 30 秒；“使用后”取首轮加 50 轮短会话、30 分钟等量音频的文件/网络处理、取消和本地 TLS 证书拒绝后，再空闲 10 秒。

| 进程 | 未使用 footprint | 使用后 footprint | 未使用 RSS | 使用后 RSS |
| --- | ---: | ---: | ---: | ---: |
| Rust 核心，不加载原生库 | 1.56 MiB | 3.48 MiB | 2.94 MiB | 5.66 MiB |
| Rust 核心，加载原生音频库 | 2.39 MiB | 4.13 MiB | 13.25 MiB | 15.94 MiB |
| Python RPC 后台参考 | 60.2 MiB | 本次未做等价使用后测量 | 101.66 MiB | — |

Rust 两种配置在使用后分别出现过 3.50 / 4.30 MiB 的 footprint 峰值；空闲快照的 CPU 均为 0.0%，线程均为 5 个。0.0% 是采样显示值，不是绝对零开销，也不是能耗测量。

同日开发期间的 Python 参考启动样本约为 60.2–68.7 MiB；最终表格只使用最终验收这一轮。启动到 initialize 的单次耗时分别为 Rust 61 ms、加载原生库的 Rust 318 ms、Python 685 ms，包含新目录初始化与文件系统同步，受缓存和系统状态影响；样本不足以当作稳定的冷启动加速比。

## 3. 文件化录音与容量验证

合成音频为 16 kHz、mono、PCM16，共 1,800 秒，PCM **57,600,000 bytes（54.93 MiB）**。WAV 文件最终大小为 57,600,044 bytes。

| 测量项 | Rust 核心 | Rust 加载原生库 |
| --- | ---: | ---: |
| 仅文件回放与流式归档完成 | 0.80 s | 0.89 s |
| 本地 WebSocket 发送并完成归档 | 11.43 s | 11.59 s |
| WebSocket 处理中的 footprint 快照 | 3.45–3.47 MiB | 4.27 MiB |
| ASR 队列观测最大占用 | 160 块 | 160 块 |
| 取消到终态，含文件提交与轮询 | 26.4 ms | 15.4 ms |
| 使用后正常 shutdown 到退出 | 1.69 ms | 1.69 ms |

在网络处理快照跨越整段约 55 MiB PCM 的过程中，进程 footprint 没有随音频长度线性上升。输入队列和 ASR 队列各最多 160 × 1,280 bytes，原始 PCM 合计约 400 KiB，另有 64 KiB 文件缓冲、当前处理块、网络编码和原生库自身状态。每块音频先写归档缓冲，再发送；没有整段 PCM 收集或停止时 join。

所有完整长文件的 PCM SHA-256 与输入一致，WebSocket 服务接收的 PCM SHA-256 也一致；端到端测试另用 10,002 bytes 音频确认最后 402 bytes 尾帧在 commit 前发出。两种进程合计完成 104 次本地 WebSocket 会话，夹具错误为零。

这属于**压缩时间执行的容量压力测试**，不是持续真实录音 30 分钟。上述墙钟耗时包括控制器、Python 协议夹具和 `vmmap` 观测成本；不能推断云端转写速度，也没有与 Python 做等价音频处理速度比较。云端音频上下文限制另行处理，当前 Plus 超过 600 秒、Flash 超过 480 秒会失败并保留已接收音频，尚未实现自动分段。[协议与限制依据](https://www.alibabacloud.com/help/zh/model-studio/realtime)

## 4. 退出、恢复与运行时独立性

最终 release 可执行文件大小 **3,630,192 bytes**，原生库为 **80,664 bytes**。主程序的 `otool -L` 只列出 `libiconv` 和 `libSystem`；原生库沿用 Foundation、AVFoundation、Accelerate 等系统依赖。

运行后检查确认：

- Rust 进程没有 Python 子进程，也未映射 Python/PyObjC 库。
- 完成与取消后不存在残留 TCP 连接；所有被测 host 已退出。
- 本地自签名 TLS 服务被正常拒绝，HTTP Authorization 尚未发送；状态返回握手错误，stderr 没有 panic。实际 WSS 配置使用显式 Rustls ring provider 与 WebPKI 根证书。
- 项目许可证随开发构建复制，内容与根 `LICENSE` 一致。

强制终止实验中，host 已接收 256,000 bytes PCM；重启后恢复了磁盘上 **195,840 bytes** 的完整前缀，逐字节验证一致，状态为 `interrupted`，transcript 为空。未恢复的 60,160 bytes 位于尚未 flush 的缓冲区。这证明恢复机制可用，也明确说明它不是崩溃或断电零丢失承诺。

原生生命周期测试使用不访问设备的 C ABI 夹具。它验证单线程句柄所有权、启动阻塞时取消、停止排空超时、无 PCM 超时，以及清理失败后的 quarantine；最后一种情况会阻止再次采集，文件和 PCM 输入仍可继续。真实麦克风、权限弹窗、低声 DSP 效果与冷暖启动表现尚需设备验收。

## 5. 交付与复现

- [Rust 运行与 API 示例](../rust/README.md)
- [会话、存储和迁移边界设计](rust-core-design.md)
- [可保留的测量数据摘要](benchmarks/rust-core-2026-09-11.json)
- [性能测量脚本](../rust/tools/measure_host.py) 与 [Python 隔离参考入口](../rust/tools/python_reference.py)
- [独立开发构建脚本](../scripts/build_rust_host.sh)

最终实测二进制 SHA-256：

```text
a2b44edb59d9d012bd24ab56317b24120929a2c14c8d0c5d591c1a9611cf7ee3
```

原始数据位于 `.build/rust-core-acceptance-20260911/`：`results.json`、逐阶段 `*-vmmap.txt`、映射库、文件描述符与连接快照、合成 WAV 和故障恢复结果。早期开发测量保留作排查记录；最终验收以此目录和上面的二进制哈希为准。

最终检查：**17 项 Rust 测试、946 项 Python 测试通过**；Rust fmt、Clippy `-D warnings`、release 构建与本地验收脚本通过。新增 CI 配置覆盖 macOS、Windows、Linux，但尚未推送执行，本次结果只证明本机 macOS 行为。

下一阶段可以在这个独立核心上接入 GPUI Kit，再迁移现有配置、词典、润色、历史、热键与粘贴能力。先补真实麦克风和真实云端的垂直验收，再测完整 UI 的使用后 footprint、GPU 开销和端到端延迟，才能判断最终桌面产品的净收益。
