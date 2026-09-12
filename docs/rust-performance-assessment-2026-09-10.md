# Rust 后端与 GPUI Kit 的剩余性能空间

日期：2026-09-10。代码基线：Vocal More 0.4.17，提交 `084d1a86f45e6c7f8a94a9a2356561a71ed2c990`。本次只评估，不修改产品代码。Rust 全栈 + GPUI Kit 的既定方向不变。

## 判断

仍有提升空间，但 Sparkle 类扫描修正后，应重新排序收益：**长录音的内存增长最明确；Python 与 SDK 常驻开销其次；设置页的 GPUI 收益需要完整窗口对照；待机 CPU 和云端识别速度不宜承诺大幅提升。**

迁移的收益由两部分组成：移除 Python/PyObjC/部分 SDK 等运行时成本，以及调整数据存储、复制和任务生命周期。后者不由语言自动提供；将整段 PCM 从 Python `list[bytes]` 改写成 Rust `Vec<Vec<u8>>`，仍会随录音时长增长。

## 1. 当前正式版实测

本机为 MacBookPro18,1、Apple M1 Pro、16 GiB 内存、macOS 27.0（26A428）。运行对象是 `/Applications/Vocal More.app`，版本 0.4.17，内置 CPython 3.12.14，PID 5999。关键录音、ASR、设置、播放与胶囊源码逐文件核对，与仓库一致。

| 项目 | 本次观测 | 解释 |
| --- | --- | --- |
| 启动约 59 秒时主进程 physical footprint | 95.0 MiB，报告峰值 95.5 MiB | 已是关闭 Sparkle 全量扫描后的正式版 |
| 随后约 6 秒的 `top` 采样 | CPU 均显示 0.0%，16–17 个线程 | 短时间分辨率内几乎没有待机计算，不能解释为绝对零能耗 |
| 3 秒线程采样 | 主线程在 AppKit 事件等待，多数 Python 线程在条件变量等待 | 没有看到忙循环或 CPU 热点证据 |
| 设置 WebKit helper | 两次进程枚举均未匹配到 WebContent / Networking | 本次没有取得设置页打开态样本 |
| 应用包磁盘占用 | `du -sh` 为 75 MiB | 不是运行内存；Rust + GPUI 也会有自身二进制与资源开销 |

本次主机与系统版本不同于前一天的启动对照，95.0 MiB 与此前 93.8 MiB 不作为严格 A/B。采样时系统约有 3.2 GiB 压缩内存、空闲内存较少，短时结果不代表所有负载环境。

`vmmap` 还显示：malloc 区域 dirty 约 44.1 MiB，Untagged dirty 约 40.7 MiB，线程栈 resident 约 512 KiB。它们是内存区域分类，不能分别等同于“可全部删除的 Python 内存”。尤其不能把约 217 MiB 的虚拟栈地址空间当作实际线程内存收益。

## 2. Rust 后端：已发现的两类机会

### 2.1 长录音缓冲：证据最强

`AudioRecorder._native_pcm_callback()` 将每块 PCM 留在 `_audio_buffer`；`stop()` 对全部块执行 `b"".join(...)`，再清空列表。后续保存和 ASR 完成流程还会持有合并后的 `bytes`。ASR 发送队列本身已有限额，增长点主要是另一路完整录音存档缓冲。

固定音频契约为 16 kHz、mono、PCM16：

```text
每秒 PCM = 16,000 × 1 × 2 = 32,000 bytes
10 分钟 = 18.3 MiB
30 分钟 = 54.9 MiB
60 分钟 = 109.9 MiB
```

本次使用正式包内的 `AudioRecorder._native_pcm_callback()`，离线快速送入 45,000 个独立的 1,280-byte 块，等价于 30 分钟、每块 40 ms 的 PCM。仅配置该回调所需状态，关闭观察者并跳过首次 PCM 计时，再复现停止时的合并语句；不启动设备、网络或 UI。

| 离线阶段 | 测试进程 footprint | 相对该进程基线 |
| --- | ---: | ---: |
| 导入模块后 | 35.8 MiB | — |
| 保存全部音频块 | 106.4 MiB | +70.6 MiB |
| 合并结果与旧块同时存活 | 164.8 MiB | +129.0 MiB |

合并实测约 16 ms，45,000 次回调循环约 24 ms。这里说明的主要问题是内存持有与复制，不是已证明存在数秒计算卡顿。这是加速的单次局部实验，不是实际录音 30 分钟的整机测试，不能把 164.8 MiB 当作正式应用的录音内存。

另一个独立 Python 进程用 64 KiB 缓冲边写 WAV，处理相同字节后 footprint 从 31.1 MiB 到 31.2 MiB；文件长度 57,600,044 bytes，格式、帧数和逐块 SHA-256 校验通过。这验证了**应用缓冲可以保持有界**，同时说明这部分改善并非 Rust 独占。该草图未覆盖慢磁盘、崩溃恢复、fsync 耐久性、重试与系统文件缓存成本。

建议 Rust 核心使用如下结构：

```text
现有原生音频 C ABI
  → 有界音频块交接
      → ASR 发送队列
      → 顺序写入临时 WAV / 音频文件
  → 停止时完成文件头、落盘与状态提交
  → 历史、重试、归档传递文件路径和录音 ID
```

慢磁盘或断网必须有显式的背压、失败和恢复策略。批量 ASR 与重试接口也应支持文件或分块读取，否则稍后再读回整个文件，会重新引入峰值。保留已有停止代次、超时和迟到结果拒收规则。

### 2.2 Python、SDK 与桥接运行时：中等且可信的机会

直接使用正式包的 CPython 3.12.14 与随包依赖，在独立进程中各重复 3 次导入；未构建、未修改安装包。下表为 footprint 范围和导入耗时中位数：

| 独立进程只导入的对象 | 总 footprint | 导入耗时中位数 |
| --- | ---: | ---: |
| 测量脚本基线 | 约 9.3–9.4 MiB | — |
| `numpy` | 18.0–18.2 MiB | 46 ms |
| `dashscope` | 35.7–36.1 MiB | 220 ms |
| `AppKit` | 23.6–24.1 MiB | 94 ms |
| `vocal_more.app` 模块 | 27.9–32.7 MiB | 150 ms |
| `vocal_more.ui.settings_window` 模块 | 27.2–30.0 MiB | 135 ms |

这些是不同进程的导入实验，包含共同依赖，**不能相加**；模块导入不等于完整应用启动，也没有创建设置 WebView。文件缓存已预热，不能据此声称能缩短相同数值的冷启动时间。

`dashscope` 单独导入相对基线增加约 26–27 MiB，说明 Python SDK 依赖确有可优化成本。Rust 直接实现所需 WebSocket/HTTP 协议，有机会减少解释器对象、动态导入和 SDK 依赖；同时必须计算 Rust TLS、HTTP、序列化和 GPUI 自身的新成本，不能把导入差值直接当作净节省。

迁移初期若用 PyO3 保留 Python host，常驻运行时收益不会完整兑现。仅为性能也可先在 Python 中继续减少无用导入，但本项目已确定 Rust 方向，建议在独立 Rust 核心中验证依赖成本和协议一致性。

## 3. GPUI Kit：设置页有机会，胶囊与待机收益有限

截至本次 GitHub API 核对，GPUI Kit 最新正式版仍为 [v0.6.1](https://github.com/longbridge/gpui-kit/releases/tag/v0.6.1)。其默认 feature 为 `component` 与 `assets`；JavaScript 扩展宿主另行引入。实现时按实际需要选择组件，不把编辑器、脚本扩展或 WebView 一并带入。[固定版本 Cargo 配置](https://github.com/longbridge/gpui-kit/blob/v0.6.1/crates/kit/Cargo.toml)、[官方架构说明](https://github.com/longbridge/gpui-kit)

| 当前表面 | 代码事实 | 对 GPUI 迁移的含义 |
| --- | --- | --- |
| 浮动胶囊 | AppKit `NSPanel` + 原生渲染器；波形刷新只在需要时运行 | 已没有 WebView 可删；收益主要来自去掉 Python 桥接，GPU 路径也可能增加资源成本 |
| 设置窗口 | 首次打开才创建 WKWebView；关闭时拆除内容、消息处理器、窗口并清空引用 | 可减少打开态 WebKit/JS 成本与每次页面初始化，但不是消除一个当前永远常驻的浏览器 |
| 历史播放 | `AVPlayer` 从文件播放，JS 只传 ID；历史默认最多 30 条 | 没有“整段历史音频必须 Base64 到 JS”的现存主路径问题；超大列表虚拟化不是眼前主要收益 |

GPUI 使用 GPU 渲染，带来字形、纹理和渲染缓存；框架宣称高帧率并不直接证明本产品更省内存或更省电。窗口隐藏时应停止主动刷新；跨线程推送状态需要合并，避免每块音频都触发整个设置页重绘。[GPUI 官方说明](https://github.com/zed-industries/zed/tree/main/crates/gpui)

本次尝试读取正式应用的 UI 状态，但应用控制返回超时，未完成设置页打开/关闭采样。因此不提供 WebKit 可节省多少 MiB、GPUI 设置页快多少的数值预测。需在有完整窗口的原型中比较打开、关闭和重复操作后的进程及 GPU 总成本。

## 4. CPU 与延迟：不要把语言收益套到整条链路

- **待机 CPU：空间小。** 本次已接近测量显示的零值；减少线程数量不等于减少同等比例能耗，当前多数线程在等待。
- **macOS 主音频路径：空间有限。** 实时 tap、重采样和 DSP 已在 Objective-C++/AVAudioConverter/vDSP 中，tap 不进入 Python/GIL。继续复用现有 C ABI。Rust 可简化后续块包装与所有权，但不能把现有原生运算当作待移植的 Python 热点。
- **兼容采集路径：有条件的空间。** PortAudio 回调仍进入 Python/NumPy；Rust 原生消费可能改善分配与调度，但本次没有采集录音，也未测录音态 CPU、丢块或 p95 延迟，不能量化。
- **原生轮询：语言无关。** 原生 worker 在原始队列为空时有 1 ms backoff；是否调整需测录音态唤醒与实时安全性，换 Rust 不会自动解决。
- **首字与最终文本延迟：需要按阶段测量。** 设备启动、网络握手、云端识别和润色仍在关键路径上。连接复用、预热和并行落盘已部分存在，应保持这些行为后再比较。

例如，假设某次完成需要 1,000 ms，其中可优化的本地工作为 100 ms，其余 900 ms 不受迁移影响；本地工作即使加速 4 倍，总时间仍是 925 ms，只改善 7.5%。这是解释收益上限的假设例子，不是本应用实测拆分。

Rust 后台可用一个受控 Tokio runtime，先验证 1–2 个 worker 是否足够，再根据负载调整。不要为每个业务模块创建 runtime，也不要把长期阻塞的音频消费塞进异步 worker。Tokio 的多线程默认配置及 `spawn_blocking` 已开始后无法通过 abort 强制终止的边界，均需保留在设计中。[Tokio Builder](https://docs.rs/tokio/latest/tokio/runtime/struct.Builder.html#method.worker_threads)、[spawn_blocking](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html)

## 5. 建议的实施与验收顺序

1. **Rust 会话核心 + 文件化录音。** 复用音频 C ABI，只选一条 ASR 协议先做垂直切片，验证取消、迟到结果、断网、慢磁盘和录音恢复。重点验收 30–60 分钟录音的应用 PCM 缓冲保持有界，而不是随时长线性增长。
2. **去掉 Python host 后测真实常驻。** 固定 release 构建、同一台机器、相同配置、相同窗口状态。可将相对当前约 95 MiB 减少 30%（约 66 MiB）设为资源目标；这是拟议验收线，不是预测 GPUI 版一定能达到。使用后的稳态也必须纳入。
3. **接入 GPUI Kit 设置页与胶囊。** 比较首次可交互时间、反复打开关闭后的回收、录音态动画和 GPU 资源；保留中文 IME、辅助功能、Fn、非激活窗口及升级兼容验收。
4. **最后优化感知延迟。** 相同音频与网络条件下，分开记录按键到首帧、首字、停止到文本、文本到粘贴的 p50/p95。先定位阶段，再决定是否调整协议、队列或 UI。

这条路线适合希望降低长时内存、统一 macOS/Windows 维护和控制运行时依赖的目标。若唯一目标是让目前的待机 CPU 更低，或让同一个云端模型识别明显更快，全面 UI/后端迁移的性能依据较弱。

## 证据与边界

原始文件及实验脚本保存在忽略目录 `.build/rust-performance-assessment-20260910/`：`idle-vmmap.txt`、`idle-top.txt`、`idle-sample.txt`、`idle-heap.txt`、`source-parity.json`、`import-probes.json`、`buffer-probe.json`、`spool-probe.json` 及对应脚本。`synthetic-30min.wav` 仅含生成的测试字节，不含用户音频。

本次没有编译 GPUI/Rust 产品原型，没有做跨语言等功能 A/B，没有录音或调用云端 ASR，也没有修改产品源码、配置、版本号或锁文件。所有 Rust 净节省、界面速度和端到端延迟仍需原型验证；上面的实测仅证明现有成本与可改变的数据持有模式。
