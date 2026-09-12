# Rust 与 Python 体验对照及性能测试

日期：2026-09-12。对象：当前工作区 Python 后端与 `.build/rust-host/vocal-more-backend` 完整 Rust 应用服务。

> 同日后续：已完成针对性的性能优化与复测，见 [性能优化记录](rust-performance-optimization-2026-09-12.md)。自动历史归档及若干性能弱项已有修复；本文仍保留优化前的审计快照和原始数值。其余体验差异不能按性能测试通过来关闭。

## 结论

Rust 已获得明确的内存收益，但当前不能按完整体验等价来验收。主要问题集中在粘贴完成确认、网络重试、设备失效恢复和后台维护；正常转写成功、配置对照和已有回归测试不能覆盖这些差异。

优先处理两处可能导致文字没有插入目标应用的问题：下一次录音清掉上一条待执行粘贴，以及流式粘贴失败后不补贴全文。随后恢复实时连接重试、失效设备回退、自动历史压缩和完成提示。自动更新初始化另有代码层缺口，需在签名应用中验收。

本轮只增加审计脚本和报告，没有修改产品代码、替换正在使用的应用、录制真实麦克风或调用云端服务。用户已反馈上一轮麦克风启动问题消失；这项反馈与本轮合成测试分别记录。

## 对照边界

- 当前所谓 Rust 版本仍是 **Rust 后端 + Python/PyObjC 薄 UI**，胶囊和设置页沿用现有实现。GPUI Kit 前端尚不属于本次被测版本。
- 测试机为 Apple M1 Pro、arm64、macOS 27.0（26A428）、Python 3.13.2、Cargo 1.90.0。Python 两侧使用同一个虚拟环境；Rust 使用 release 可执行文件。
- 基线 Git commit 为 `084d1a86f45e6c7f8a94a9a2356561a71ed2c990`，存在尚未提交的迁移和其他并行工作。可执行文件及关键源码 SHA-256 已记入原始结果，不能只用 commit 复原本轮状态。
- 比较当前源码版本，不把旧版 `/Applications` 安装包、不同 Python 解释器或此前部分 Rust host 的内存数字混入比较。
- 所有测试使用独立临时配置、录音和数据库。GUI 测试关闭全局快捷键及麦克风预热。网络夹具仅监听 loopback；Python 工作者拒绝非本地连接。
- 下文“已复现”是对生产方法或完整后端的隔离复现；不表示已经在每种编辑器、音频设备或签名安装包中完成实机验证。

## 已发现的体验差异

### P1：下一次录音可能使上一条结果没有粘贴

Rust 在发出 `paste_requested` 后立即进入 `idle`，此时系统操作线程可能仍在等待。下一次 `start` 会清空 `pending_pastes` 和已领取的粘贴状态。

隔离复现顺序：完成第一条转写 → 收到粘贴请求但暂不执行 → 确认后端已经 idle，队列仍有一条粘贴 → 开始第二条录音 → 领取第一条 token，返回 `{"cancelled":true}`。第一条结果保存在历史中，但不再插入目标应用。没有取消第一条录音这一前置操作。

Python 的普通完成流程同步执行粘贴，工作流返回后才进入完成/空闲状态。Rust 的进程拆分需要明确区分“文字生成完成”和“插入完成”，并补足成功、失败及取消的确认协议。

证据：[Rust start](../rust/crates/backend/src/application.rs)、同文件 `Internal::Finished` / `claim_paste`；[薄 UI `_paste`](../src/vocal_more/rust_ui.py)；[Python 完成工作流](../src/vocal_more/application/dictation_workflow.py)。复现脚本：`probe_experience.py` 的 `rust_pending_paste_lost_on_next_recording`。

### P1：流式粘贴失败后，Rust 把未插入的文字当成已经插入

Rust 在发出流式粘贴请求时即更新 `active.streamed`。薄 UI 遇到粘贴异常只取消学习观察并显示错误，没有向后端确认插入失败。

本轮让生产后端生成一个已完成分段，领取粘贴任务，然后重放薄 UI 的失败 RPC 路径。停止录音后，最终文字完整，但最终补贴请求为空。Python 生产 `_paste_streamed_segment` 在相同的模拟异常下保留空的已插入前缀、关闭流式模式，结束时可以回退到完整文本。

应在操作成功后推进已插入前缀；失败时保留尚未插入的部分。单纯增大队列或延迟隐藏胶囊不能解决确认语义。

证据：[Rust `core_status`](../rust/crates/backend/src/application.rs)、[尾部对齐工作流](../rust/crates/backend/src/workflow.rs)、[Python 流式插入](../src/vocal_more/modes/realtime_long.py)。该复现不执行真实系统粘贴，验证的是后端面对 UI 失败路径的行为。

### P2：实时连接失败后的恢复方式发生变化

本地服务第一次 WebSocket upgrade 返回 HTTP 503，后续连接正常。两侧均输入并保存 6,400 字节 PCM；7.5 秒时读取状态：

| 行为 | Python | Rust |
| --- | --- | --- |
| 连接尝试次数 | 2 | 1 |
| 状态 | recording | recording |
| 恢复部分文字 | 有，“性能测试” | 无 |
| 已采集 PCM | 6,400 字节 | 6,400 字节 |

已安装的 Python SDK 最多等待约 5 秒才暴露此次连接失败，应用随后按第一次 1 秒延迟重试。Python 应用具备最多 5 次重试和连接状态提示；Rust 当前一次连接失败后继续归档，结束时由工作流转向文件识别。Rust 保留音频的策略有恢复价值，但实时文字、错误提示、使用的识别路径和恢复等待已不等价。本轮在结束前取消，不调用真实文件识别服务。

证据：[Python `_connect`](../src/vocal_more/core/asr_engine.py)、[连接状态](../src/vocal_more/domain/connection_status.py)、[Rust 实时 provider](../rust/crates/backend/src/provider.rs)、[Rust 文件恢复分支](../rust/crates/backend/src/workflow.rs)。

### P2：指定麦克风失效后，缺少原有自动回退

Python 设备解析在找不到已选设备时清空选择并保存系统默认。本轮替换设备发现边界后实际执行该方法，确认返回默认设备且配置保存一次。Rust 在相同的空设备列表条件下，执行 `refresh_devices` 后仍保留失效设备名。

原生库对指定但不存在的设备返回 `Selected input device is unavailable`；Rust 当前没有 Python 的设备清理与分层采集后端回退。USB 拔插、蓝牙切换、睡眠后设备重建是需要补做的真机场景。已确认选择状态处理差异；未在本轮拔插物理设备，不声称所有硬件都必然失败。

证据：[Python `_resolve_device` / `_open_stream_with_fallback`](../src/vocal_more/core/audio_recorder.py)、[Rust `refresh_devices` / `native_source`](../rust/crates/backend/src/application.rs)、[原生设备选择](../native/audio/src/VocalMoreAudio.mm)。

### P2：历史记录不再自动压缩

同样完成 20 次一秒录音，退出后核对音频文件：

| 项目 | Python | Rust |
| --- | --- | --- |
| WAV | 3 | 20 |
| FLAC | 17 | 0 |
| 音频文件合计 | 429,876 字节 | 640,880 字节 |

Rust 有手动压缩功能，但当前只在显式 `compact_recording_history` 时调用。Python 在记录终态及存储初始化时调度自动压缩，保留最近 3 条 WAV。这里的约 49% 额外空间只适用于本次确定性测试音频，不是实际语音压缩率预测。

证据：[Python RecordingStore](../src/vocal_more/core/recording_store.py)、[Rust History](../rust/crates/backend/src/history.rs) 及 application 的压缩入口。

### P2/P3：完成反馈与菜单状态不完整

实际执行两侧最终结果处理方法：Python 更新“复制上次结果”菜单并发出完成通知；Rust 只保存 `_last_text`。关闭自动粘贴时，完成后缺少原有通知，用户需要主动去菜单或历史找文字。Rust 的复制菜单仍能读取最新文本，不能据此认定复制功能失效。

另据代码检查，Rust 把 `processing` / `cancelling` 映射到空闲菜单图标，Python 使用处理图标。胶囊本身仍显示处理状态，这属于菜单反馈差异，不是整个应用失去进度反馈。

证据：[Python `_on_result` / `_apply_state_change`](../src/vocal_more/app.py)、[Rust UI `_event`](../src/vocal_more/rust_ui.py)。

### P2：自动更新初始化存在代码层缺口

Python 在启动后的初始化回调中创建 Sparkle controller；Rust 薄 UI 只在用户点击“检查更新”时创建它。在包含 Sparkle 的正式应用中，自动更新流程因而可能直到第一次手动检查才启动。

这是源码调用路径确认的缺口；本轮源码运行没有打包 Sparkle，没有验证实际 appcast、签名、更新弹窗或安装。应作为发行验收项修复/验证，不能用开发进程测试证明正式自动更新正常。

证据：[Python 启动初始化](../src/vocal_more/app.py)、[Rust `_check_updates`](../src/vocal_more/rust_ui.py)、[Sparkle controller 创建](../src/vocal_more/infrastructure/sparkle_updater.py)。

## 性能测量

### 桌面 UI 与主进程占用

真实 AppKit 菜单和同一份 WKWebView 设置页，分别启动隔离进程。测量 `vmmap Physical footprint`；Rust 数字合计薄 UI 和后端。**不包含 WebKit 辅助进程、真实麦克风图、ASR 连接和全局快捷键，因此不是完整日常运行占用。**

| 指标 | Python | Rust UI + 后端 |
| --- | ---: | ---: |
| 初始化，含本进程 imports，单次 | 639 ms | 326 ms |
| 空闲主进程内存 | 64.0 MiB | 36.2 MiB |
| 设置打开后主进程内存 | 81.1 MiB | 52.3 MiB |
| 三次打开/关闭后主进程内存 | 84.4 MiB | 53.8 MiB |
| 设置首次 7 个页签就绪 | 537 ms | 435 ms |
| 设置后续两次就绪 | 197 / 185 ms | 167 / 197 ms |
| 10 秒空闲 CPU，单核百分比 | 1.00% | 1.10% |

本场景空闲主进程内存下降约 43%。启动与设置时间样本少，只作诊断快照。CPU 包含测试驱动的 RunLoop pumping，0.1 个百分点差异不足以认定耗电退化或收益。三次开关窗口也不足以证明无泄漏。

### 两侧生产后端、同音频、同本地识别服务

主测试各 20 轮，一秒 16 kHz、mono、PCM16 输入，每 40 ms 一块，WebSocket admission 固定延迟 100 ms、提交后服务响应固定延迟 20 ms；服务器启用 TCP_NODELAY。模型为 `qwen3.5-omni-flash-realtime`，关闭润色和自动粘贴，Python 保留生产 ASR 预热流程。

Python 使用生产 RPC、mode、ASR SDK 和 RecordingStore，只替换设备输入边界，将相同 PCM 送入生产 native PCM callback。Rust 使用完整 application binary 的 paced WAV source。逐轮验证服务端收到的 32,000 字节 PCM 的 SHA-256 相同；正常主测试没有丢首尾块或结果错误。这不测 DSP 声学质量或真实中文识别准确率。

| 毫秒，p50 / p95 | Python | Rust |
| --- | ---: | ---: |
| 开始请求 → 状态反馈 | 1.6 / 1.8 | 16.2 / 19.2 |
| 开始请求 → 首个非零音量事件 | 3.6 / 4.5 | 17.0 / 19.7 |
| 开始请求 → 首次部分文字 | 115.7 / 221.9 | 121.5 / 124.5 |
| 停止请求 → 最终结果事件 | 30.4 / 31.5 | 102.0 / 111.4 |
| 停止请求 → 服务端收到 commit | 1.8 / 2.8 | 40.6 / 43.6 |
| 服务端收到 commit → 最终结果 | 28.2 / 29.4 | 60.5 / 72.3 |

各子区间的百分位独立计算，不能简单相加得到总区间百分位。20 个样本的 p95 用 nearest-rank；不是生产长期尾延迟的置信估计。

Rust 的 WAV 回放停止需要等当前约 40 ms 音频块结束，Python 测试输入可即时结束。因此不能把总差值约 72 ms 全部认定为物理麦克风停止回归。提交之后仍约多 32 ms，值得继续分析归档、历史持久化和结果发布的时序；本轮没有通过修改代码验证各项耗时比例，故不把推测写成已定位根因。

正常轮次的音频完整性可以确认；Rust 的首次部分文字中位数略慢、p95 更稳定。后端隔离进程启动单次为 Python 615 ms / Rust 52 ms，空闲 62.1 / 2.83 MiB，20 轮后 63.0 / 4.67 MiB。不能只引用后端的 2.83 MiB 来代表整个 Rust 应用。

另有各 10 轮、两秒音频、500 ms admission 的压力快照：部分文字 p50 为 Python 615 ms / Rust 533 ms，全部 PCM 校验一致。该较早夹具尚未启用服务器 TCP_NODELAY，原始结果单独保留，不与主测试合并算百分位。

## 功能覆盖矩阵

| 领域 | 本轮观察及验证 | 判断 |
| --- | --- | --- |
| Fn 长按/短按、双 Cmd、两种录音模式 | Rust 应用及 Python→Rust RPC 手势测试通过 | 合成时序有覆盖；未抢占用户真实快捷键 |
| 首尾音频、快速取消、迟到结果隔离 | 本地 WS / C ABI 夹具及会话测试通过；主性能输入逐字节一致 | 已测路径正常；真实声学另验 |
| 普通及流式粘贴 | 新增异常复现见上 | 有 P1 退化 |
| ASR 模型与协议 | 10 个模型的应用测试、实时协议/SDK 参数对照通过 | 协议覆盖不等于所有云模型实测 |
| 连接失败、重试、提示 | 第一次连接拒绝 A/B 复现 | 行为退化/策略变化 |
| 文件识别、长音频分块 | 现有 HTTP、分块、截断/取消测试通过 | 本地协议范围内有覆盖 |
| 低声增益、高通、限幅、相干混音 | 复用原生 C ABI；DSP/设备夹具测试通过 | 核心算法有复用；低声实录准确率未测 |
| 设备指定、热插拔、睡眠唤醒 | 失效设备状态处理不同 | 有回退缺口；热插拔待真机 |
| 麦克风测试、5 秒回放 | 既有接口和集成路径仍在 | 本轮不录真实环境音 |
| 设置、迁移与字段约束 | 787 条配置更新、8 条旧配置迁移对照通过 | 已有合同覆盖；非所有输入穷举 |
| 原设置页、外观、打开/关闭 | 两侧真实 7 页签三次打开关闭；共享前端 72 测试通过 | 页面主体保留 |
| API key 显示/遮罩、表单保存 | 现有前端/应用测试通过 | 未发现已测路径退化 |
| 词典 CRUD、别名、文本排版 | 15 组文本对照及重启测试通过 | 已测路径正常 |
| 润色、Prompt 模式与自定义内容 | 36 组提示词对照、本地 SSE 测试通过 | 不评价大模型生成质量 |
| 历史、播放、删除、独立重试 | 文件生命周期和 FLAC 编解码测试通过 | 自动压缩调度缺失 |
| 费用统计 | 126 组计算合同通过 | 是算法对照，不验证实时账单/价格 |
| 词典学习、隐私筛选、审批/撤销 | 169 组候选、17 组校验及数据库测试通过 | AX 在真实编辑器中的采集仍需验收 |
| 正常关闭、后台任务清理 | Rust 与 Python 回归及测试进程正常退出 | 已测范围通过；不覆盖所有阻塞设备 |
| 异常退出后的录音恢复 | Rust 文件化录音及现有恢复测试 | Rust 的优势；只保证已刷盘前缀 |
| 菜单、完成通知、连接提示 | 生产回调复现及代码对照 | 有反馈缺失 |
| 自动更新与诊断导出 | Rust 手动更新入口在，启动更新初始化缺失；诊断实现已换 | 正式包更新/诊断内容需单独验收 |
| 回退 Python | 入口保留；Rust 默认使用独立 `rust-backend` 目录 | 一次导入后不双向同步；切回会看到原 Python 数据 |
| Windows / GPUI Kit | 当前实测为 macOS 薄 UI | 不在本轮验收范围 |

## 自动化结果与复跑

Rust workspace：52 项通过。Python：989 项通过。设置前端：6 个文件、72 项通过。测试数量增加包含工作区并行新增的发行测试，不能全算作本轮审计新增覆盖。

首次直接 `uv run python -m pytest -q` 在并行新增的发行测试收集阶段遇到 `ModuleNotFoundError: release`。添加现有 `packaging` 目录到测试路径后全量通过；没有为此修改发行代码。这个测试环境问题不计入 Rust 体验回归。

从仓库根目录执行，输出目录每次用新名称：

```bash
uv run python rust/tools/compare_backends.py \
  --output .build/experience-audit-new/backend --rounds 20
uv run python rust/tools/compare_desktop.py \
  --output .build/experience-audit-new/desktop
uv run python rust/tools/probe_experience.py \
  --output .build/experience-audit-new/probes

cargo test --manifest-path rust/Cargo.toml --workspace
PYTHONPATH=packaging:src \
  VOCAL_MORE_TEST_RUST_BACKEND=.build/rust-host/vocal-more-backend \
  uv run python -m pytest -q
npm --prefix frontend/settings test -- --reporter=dot
```

三个工具应顺序运行，性能采样时避免构建和大规模测试争用 CPU/磁盘。`compare_desktop.py` 会短暂打开真实设置窗口，需 macOS 桌面会话。`probe_experience.py` 中部分断言有意确认当前缺陷；产品修复后应将相应断言改为期望的正确行为，并纳入正式回归测试。

原始合并结果：[JSON](benchmarks/rust-python-experience-2026-09-12.json)。本机详细进程日志与 `vmmap` 位于 `.build/experience-audit/`。夹具升级前的 pilot 输出仅用于排错，不作为本报告主测量结果。

## 下一轮验收顺序

1. 修复普通/流式粘贴确认协议，加入“系统操作线程延迟”“插入失败”“下一段紧接着开始”的应用级回归。
2. 对齐实时重试与连接提示，补失效设备选择清理；再做蓝牙/USB 拔插与睡眠恢复。
3. 恢复自动历史压缩、结果通知、菜单处理图标和 Sparkle 启动初始化。
4. 使用同一设备、同一录音内容测按键到首个实际音频样本、停止到实际插入；覆盖低声、普通话/英文混说和连续短句。实际插入至少验证常用编辑器、浏览器输入框及安全输入场景。
5. 单独验收签名包的权限、自动更新和 Windows 行为。以上完成前，不把当前 Rust 版本标记为“全面无体验退化”。
