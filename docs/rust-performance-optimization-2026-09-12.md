# Rust 性能优化与 Python 复测

日期：2026-09-12。范围：当前 Rust 业务后端及 Python/PyObjC 薄 UI。原始数据、逐轮结果和源码哈希见 [JSON](benchmarks/rust-performance-optimization-2026-09-12.json)。

**严格的“所有性能都优于 Python”尚未达到。** 已测启动反馈、首帧事件、部分文字、设置加载、内存和包含归档工作的 CPU 均有优势；正常无关闭超时的收尾仍约慢 10.7 ms，稳定后的音频存储空间相同，空闲 CPU 未证明领先。

## 这次实际改了什么

- 开始会话先保留 ID；录音文件和初始元数据延后到首块 PCM 创建。输入到达后立即反馈 RMS，归档计数仍在写入成功后更新，有界队列和失败恢复继续生效。
- 停止时让 WAV 封存与服务返回重叠；paced WAV 的等待可立即被 stop/cancel 唤醒。终态通过内部 `Arc<Recording>` 交接，避免扫描历史目录。
- 已有最终实时文字且无需第二次润色请求时，先准备不可见的历史文件，与核心元数据提交并行；核心成功后再原子发布历史和结果。过期的索引或 pin 快照会重建，取消和提交失败不会发布成功文字。
- 新增历史与淘汰旧记录合并提交；删除标记先持久化，清理标记可以合并到下次事务。自动归档保留最近 3 条 WAV，等待短暂空闲再开始扫描，连续输入时延期；已有归档任务仍按原有取消和队列边界运行。
- 归档校验按 64 KiB 读取，放到阻塞任务中，并比较转换前后的 PCM。对系统转换器不能正确处理的短录音保留 WAV。
- UI 事件到达即唤醒主线程，突发事件合并调度；两秒定时器只承担快捷键/权限恢复。后端每条 RPC 消息一次写出；薄 UI 使用有上限的缓冲读取，标准输入保持即时写入。

## 同口径后端复测

Apple M1 Pro、arm64、macOS 27.0（26A428），Python 3.13.2，Rust release，`MACOSX_DEPLOYMENT_TARGET=14.0`。构建、测试和性能批次顺序执行。此时机器仍有用户原来的应用运行，因此结果不是完全隔离机器的长期统计。

两侧使用同一虚拟环境、同一模型 `qwen3.5-omni-flash-realtime`、一秒 16 kHz mono PCM16、40 ms 输入块、本地服务 100 ms admission 和 20 ms 提交响应；关闭润色与系统粘贴，Python 保留生产预热。修正后的驱动给两侧使用相同的缓冲管道读取。旧 Rust 可执行文件也用同一驱动重测。

| 毫秒，p50 / p95 | 优化前 Rust，20 轮 | Python，40 轮 | 优化后 Rust，40 轮 |
| --- | ---: | ---: | ---: |
| 开始请求 → 状态反馈 | 15.94 / 17.83 | 2.05 / 4.36 | 0.86 / 1.82 |
| 开始请求 → 首个非零音量事件 | 17.06 / 19.22 | 4.96 / 8.56 | 1.84 / 3.20 |
| 开始请求 → 部分文字 | 131.36 / 135.44 | 131.46 / 239.45 | 117.72 / 119.08 |
| 停止请求 → 服务端收到提交 | 46.93 / 49.40 | 5.26 / 6.14 | 0.67 / 1.64 |
| 停止请求 → 最终结果 | 115.48 / 121.58 | 288.13 / 303.66 | 50.72 / 56.08 |

旧 Rust 与新 Rust 的收尾中位数由 115.5 ms 降至 50.7 ms，约降低 56%；反馈由 15.94 ms 降至 0.86 ms。旧版本有 20 条历史，新版 40 轮会触及 30 条保留上限；新版最后 10 轮收尾 p50 为 51.36 ms。每一列百分位独立计算，不能相加；小样本 p95 不能替代生产长期尾延迟。

**Python 的 288 ms 中位数不能当作稳定基线。** 本轮日志再次出现 250 ms 连接关闭超时；耗时低于 150 ms 的诊断子集有 19 / 40 轮，其 p50 为 40.01 ms，仍快于 Rust 的 50.72 ms。此子集用于暴露剩余差距，不替代上表全部轮次。分段计时表明核心元数据和历史索引持久化仍占主要本地收尾时间，需要继续从共享持久化事务优化。

旧报告和中间批次使用未缓冲驱动，保留在原始数据中，不能与上述新驱动结果合并。一次归档重叠时出现的启动长尾也包含逐字节读历史消息的开销；改为缓冲读取后，最终 40 轮 Rust 启动反馈最大值为 1.94 ms。

## 资源与真实设置窗口

| 指标 | Python | Rust 版本 |
| --- | ---: | ---: |
| 每轮后端及已回收转码子进程 CPU，含所有录音和维护，均值 | 116.71 ms | 70.54 ms |
| 后端空闲内存 | 61.80 MiB | 2.97 MiB |
| 后端 40 轮后内存 | 63.50 MiB | 5.24 MiB |
| UI 初始化，包含 imports | 449 ms | 241 ms |
| UI + 后端空闲内存 | 63.00 MiB | 36.03 MiB |
| 设置页打开时内存 | 81.80 MiB | 51.78 MiB |
| 三次开关设置后内存 | 84.30 MiB | 53.24 MiB |
| 设置首次就绪，实际检查 7 个 tab | 486 ms | 373 ms |
| 设置再次就绪，后两次均值 | 193 ms | 164 ms |
| 空闲 CPU，单核百分比，10 秒单次采样 | 0.0106% | 0.0172% |
| 40 轮后保留音频 | 3 WAV + 27 FLAC | 3 WAV + 27 FLAC |
| 保留音频文件合计 | 626,196 字节 | 626,196 字节 |

CPU 使用 `proc_pid_rusage` 的 Mach 时间，并用 `mach_timebase_info` 换算；已与 `getrusage` 的自身及已回收子进程 CPU 增量交叉核对。后端 CPU 计时覆盖两轮之间和最后的归档清空等待，包括已回收转码子进程，避免把延期维护隐去。独立校验音频的测试驱动不计入产品 CPU。GUI 空闲采样只合计所列主进程，10 秒内两者实际都只用了约 1–2 ms CPU，不能据此宣称 Rust 空闲能耗更低。

GUI 数字不包括共享 WebKit 辅助进程，关闭了真实快捷键和麦克风；是实际 AppKit/WKWebView 窗口结果，不是完整常驻设备场景。初始化属于已有代码和文件缓存下的单次测量，不代表重启机器后的冷启动。两版本的稳定存储结果相同；短间隔连录期间 Rust 会延期归档，不能把稳定后的文件数当作任意瞬间的占用。

## RPC 读取专项验证

真实 OS pipe、同一份 75,951 字节历史消息、交替顺序各 10 次，读取后逐字节比较：

| 方式 | 读取耗时 p50 | 底层读取调用中位数 |
| --- | ---: | ---: |
| 无缓冲 `readline` | 33.99 ms | 75951 |
| 64 KiB `BufferedReader` | 0.082 ms | 2 |

该结果只说明管道读取成本，不能等同于整个设置窗口加速比例。生产读取保留 40 MiB 单条响应上限、EOF/异常处理和退出清理；所有 RPC 集成测试继续通过。

## 验证结果及剩余边界

- Rust workspace **58 项通过**；Python 全量 **1001 项通过**；`cargo clippy --workspace --all-targets -- -D warnings` 和格式检查通过。测试数包含工作区原有和并行发行相关测试，不全是本次新增。
- 覆盖提交失败不发布成功、历史提交失败后回到空闲、准备索引不可见/丢弃/冲突重建、遗留准备文件恢复、历史满载淘汰、短音频转换失败保留原音频、自动压缩、突发 UI 事件顺序与关闭后回调。
- 两版共 **80 轮**网络 PCM 与预期逐字节/哈希一致；两版最后保留的 **60 条**音频逐条解码为 WAV，再与完整预期 PCM 比较通过。没有靠丢弃首尾音频换取计时成绩。
- 仍未覆盖真实麦克风、低声声学质量、蓝牙拔插、云端识别准确率、系统粘贴与签名安装包权限；这些不能从本地合成 PCM 推导。
- 原体验审计里的待执行粘贴被下一次录音清空、流式粘贴失败确认、设备回退、实时网络重试和完成提示等差异仍需处理。自动归档已修复；本次性能结果不构成完整体验等价或正式发布验收。

## 复现与生效

```sh
MACOSX_DEPLOYMENT_TARGET=14.0 cargo build --locked --release --manifest-path rust/Cargo.toml -p vocal-more-backend
MACOSX_DEPLOYMENT_TARGET=14.0 cargo test --manifest-path rust/Cargo.toml --workspace
VOCAL_MORE_TEST_RUST_BACKEND=rust/target/release/vocal-more-backend uv run python -m pytest -q
uv run python rust/tools/compare_backends.py --rounds 40 --order rust-first --rust-binary rust/target/release/vocal-more-backend --output .build/perf-rerun
uv run python rust/tools/compare_desktop.py --rust-binary rust/target/release/vocal-more-backend --output .build/desktop-rerun
uv run python rust/tools/benchmark_rpc_reader.py --output .build/rpc-reader-rerun.json
```

已将候选后端原子替换到 `.build/rust-host/vocal-more-backend`，并验证初始化及正常退出。重启当前开发版才会加载新后端和薄 UI。正在运行的进程保持原版；本轮没有生成 DMG 或发布正式版本。优化前可执行文件备份在 `.build/perf-optimization/before/vocal-more-backend`。详细日志、阶段计时与原始 `vmmap` 保存在 `.build/perf-optimization/`。
