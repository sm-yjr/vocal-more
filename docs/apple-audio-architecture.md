# Apple 原生音频与算力架构决策

本文解决两个问题：如何利用 Mac 内建麦克风获得更稳定的听写输入，以及 CPU、GPU、Apple Neural Engine 应分别承担什么工作。假设目标仍是 16 kHz、mono、PCM16 的语音识别输入，主要场景是办公室轻声输入，而不是音乐或空间音频录制。

## 1. 核心模型

采集拓扑和应用传输格式必须分开：

```text
设备输入（1～N 个逻辑通道）
  → Apple Voice Processing 或兼容回退
  → 高质量采样率转换
  → 可选的软件 DSP
  → 固定 16 kHz / mono / PCM16
  → ASR
```

Core Audio 和 AVFoundation 公共接口暴露设备、流、格式与逻辑通道；它们没有承诺向第三方应用公开 Mac 内建阵列中每个物理胶囊的身份、坐标和未处理信号。因此，`max_input_channels > 1` 只能证明逻辑格式有多个通道，不能证明拿到了独立物理麦克风。这是基于公开 API 边界作出的推断，产品状态不能把逻辑通道数描述成“物理麦克风数量”。

听写主路径应优先使用 `AVAudioEngine` Voice Processing。它提供系统管理的语音上行处理；是否启用 AGC 由应用明确配置并回读验证。设备源格式由 macOS 和当前 route 协商，采样率可以变化，内建麦克风常见为 48 kHz；这不改变应用边界。硬件采集格式到固定 16 kHz 的转换使用 `AVAudioConverter.convert(to:error:withInputFrom:)`，而不是线性插值，也不能使用不支持采样率转换的简化 `convert(to:from:)`。[Apple 的 AVAudioConverter 文档](https://developer.apple.com/documentation/avfaudio/avaudioconverter)和[采样率转换技术说明 TN3136](https://developer.apple.com/documentation/technotes/tn3136-avaudioconverter-performing-sample-rate-conversions)明确区分了这两个接口。

`audio.sample_rate` 是旧 YAML/RPC 的兼容字段，加载后始终归一化为 16 kHz。设备源率、converter 输入率和应用输出率分别记录，不能因旧字段仍可读取，就把当前实现描述成 24/48 kHz 端到端可变采样率。ASR、WAV、历史记录、重试和播放都解释同一种 mono/int16 字节流。

### 1.1 首次麦克风权限入口

TCC 状态必须在设备发现和 3 秒 Core Audio 启动 deadline 之前判定。若首次明确的录音或麦克风测试观察到 `not_determined`，应用调用异步的 `AVCaptureDevice.requestAccess(for:completionHandler:)`，立即把本次模式复位并提示用户授权后再次触发；它不等待 completion handler，不进入设备启动 deadline，也不会在用户已经松开快捷键后自动重放原意图。[Apple 的 requestAccess 文档](https://developer.apple.com/documentation/avfoundation/avcapturedevice/requestaccess%28for%3Acompletionhandler%3A%29)。

这样把“等待用户做隐私决定”和“设备/驱动是否在 deadline 内启动”分成两个不同故障域。`denied` / `restricted` 直接返回权限错误；只有后续一次明确动作观察到 `authorized`，才进入设备发现和原生流启动。静态 capability probe 只读授权状态、selectors 和 dylib ABI，永远不调用 `requestAccess`，也不打开麦克风。

## 2. AGC 决策

结论：提供 `automatic` 和 `manual` 两种模式。当前新安装以“无需先标定增益”为产品理由默认 `automatic`，旧配置迁移为 `manual`；这是便利默认，不是“自动模式音质优于手动模式”的结论。设置页使用中性标签，目标 Mac 硬件矩阵完成 ABBA、盲听和 CER/WER 验收后，再决定是否把它标为推荐或按设备族调整默认值。

```yaml
audio:
  gain_mode: automatic  # automatic | manual
  gain: 8.0             # 始终保留，供 manual 和自动回退使用
  soft_limiter: true
  capture_channels: 1   # 1 为安全默认；2/3 仅用于显式多通道实验
```

运行时不变量是：

```text
Apple AGC active XOR software gain/limiter active
```

| 用户选择 | 原生结果 | 实际处理 |
| --- | --- | --- |
| `automatic` | Voice Processing 和 AGC 均验证成功 | Apple Voice Processing（文档明确的回声去除 + 不透明系统处理）/AGC；软件 gain=1，limiter 旁路 |
| `automatic` | 设备、API、验证或启动失败 | 保存的软件 gain + limiter；配置仍保持 automatic |
| `manual` | Voice Processing 可用 | Apple Voice Processing，明确关闭 AGC；软件 gain + limiter |
| `manual` | Voice Processing 不可用 | 标准采集或阵列回退 + 软件 gain + limiter |

Apple 将 `isVoiceProcessingAGCEnabled` 定义为可读写状态，并说明默认开启；因此不能只调用 setter 就宣称 AGC 生效，必须 getter 回读一致后再旁路软件增益。[Apple AGC 属性说明](https://developer.apple.com/documentation/avfaudio/avaudioinputnode/isvoiceprocessingagcenabled)；[Voice Processing 启用接口](https://developer.apple.com/documentation/avfaudio/avaudioionode/setvoiceprocessingenabled%28_%3A%29)。

“旁路软件增益”和“整条录音已验证”是两个状态。只要 engine 启动后的 getter 快照明确显示 Voice Processing 与 AGC 均开启，就必须继续旁路软件 gain/limiter；丢块或 converter fault 只会把该 session 标为未验证，不能因此重新叠加软件增益。原生 ABI 不把这两个快照描述成持续轮询的 live getter。

公开接口明确说明 Voice Processing 会从输入中去除本机当前播放的音频，并提供一个总开关和独立 AGC 状态；它没有提供可独立验证的“降噪已开启”状态。因而产品可以声明 Voice Processing 与 AGC getter 已验证，不能把不可见的系统处理细分成已验证 NS。噪声底、风扇噪声与语音可懂度属于硬件 A/B 结果，不属于 API 状态。

录音过程中改变模式只影响下一条流。中途切换会改变电平包络并可能造成单条语音前后音量突变。场景预设属于手动标定，点击“轻声办公”“普通说话”或“嘈杂环境”时必须显式切换为 `manual`。

## 3. 多通道输入策略

### 3.1 当前生产路径

1. 系统默认内建麦克风：优先 Voice Processing mono 上行。这条路径最适合低延迟听写和扬声器回声场景。
2. Voice Processing 不可用：默认仍使用系统提供的 mono 输入。只有用户在配置中明确设置 `capture_channels > 1`，才按设备公开格式最多尝试三个逻辑通道，进入实验性的相干性筛选、极性对齐和 mono 混合。未完成目标硬件 A/B 前，不把该混音路径描述为提质。
3. 外置设备：保留用户请求的采集通道数，但输出仍固定 mono。配置中的 `capture_channels` 表示采集意图，`channels` 只保留为固定 mono 的兼容字段。

当前设备名匹配只是兼容层，不是可靠的硬件身份。下一阶段 route identity 应直接读取 Core Audio HAL 的 `kAudioDevicePropertyDeviceUID` 与 `kAudioDevicePropertyTransportType`，并只在 transport 为 `kAudioDeviceTransportTypeBuiltIn` 时认定内建设备；用 `kAudioDevicePropertyStreamConfiguration` 和 preferred channel layout 描述逻辑通道拓扑。UID/transport 可以防止把同名 USB、聚合或虚拟设备误当内建麦克风，但 channel layout 仍不能证明物理胶囊坐标。[Apple Device UID](https://developer.apple.com/documentation/coreaudio/kaudiodevicepropertydeviceuid)、[transport type](https://developer.apple.com/documentation/coreaudio/kaudiodevicepropertytransporttype)、[built-in transport](https://developer.apple.com/documentation/coreaudio/kaudiodevicetransporttypebuiltin)与[stream configuration](https://developer.apple.com/documentation/coreaudio/kaudiodevicepropertystreamconfiguration)。

当前设置页不把逻辑通道实验包装成“多麦克风提质”开关；需要实验时在本地配置文件中显式设置 `capture_channels: 2` 或 `3`。默认值始终是 `1`，而 ASR/WAV 输出始终是 mono。

### 3.2 可独立验证的实验路径

从 macOS 15 起，`AVCaptureDeviceInput.multichannelAudioMode` 可以在支持的内建麦克风上请求 stereo 或 First-Order Ambisonics，并提供 `isMultichannelAudioModeSupported` 能力查询；外置麦克风会忽略该设置，默认仍是单声道。实现必须同时做系统版本、selector 和 `isMultichannelAudioModeSupported` 三层判断。[Apple multichannelAudioMode 说明](https://developer.apple.com/documentation/avfoundation/avcapturedeviceinput/multichannelaudiomode)、[能力查询](https://developer.apple.com/documentation/avfoundation/avcapturedeviceinput/ismultichannelaudiomodesupported(_:))和[多通道模式枚举](https://developer.apple.com/documentation/avfoundation/avcapturemultichannelaudiomode)。

macOS 26 才提供 `AVCaptureAudioDataOutput.spatialAudioChannelLayoutTag` 所描述的完整 FOA/stereo 输出布局约束。把“macOS 15 能请求 FOA”与“macOS 26 能按公开布局规则取得 FOA sample buffer”分开建 capability；不能仅凭枚举值存在就启用生产路径。[Apple spatialAudioChannelLayoutTag 说明](https://developer.apple.com/documentation/avfoundation/avcaptureaudiodataoutput/spatialaudiochannellayouttag)。

`isWindNoiseRemovalSupported` / `isWindNoiseRemovalEnabled` 同样从 macOS 15 起提供，并且只有非 `none` 的 multichannel mode 才可用。它适合在隔离的多通道实验中作为单独变量，不应悄悄叠加到 Voice Processing 主路径，否则 A/B 无法区分阵列模式、风噪处理和 AGC 的贡献。[Apple 风噪能力与开关](https://developer.apple.com/documentation/avfoundation/avcapturedeviceinput/iswindnoiseremovalenabled)。

macOS 14 的 `voiceProcessingOtherAudioDuckingConfiguration` 调整的是语音聊天期间“其他非语音播放”的 ducking，不是麦克风降噪开关。Vocal More 默认不改它；若以后验证扬声器回录场景，只能作为播放副作用实验，并同时测量用户正在收听的音频是否被不期望地压低。[Apple ducking 配置说明](https://developer.apple.com/documentation/avfaudio/avaudioinputnode/voiceprocessingotheraudioduckingconfiguration)。

这条接口面向多通道/空间采集，不等价于暴露物理胶囊，也没有自动获得听写所需 AEC 的保证。它应作为隔离的 capability-gated 原型与 Voice Processing 做真实硬件 A/B；在证明轻声识别、回声和延迟均更好之前，不替换生产主路径。

### 3.3 系统麦克风模式是独立变量

macOS 控制中心的 Standard、Wide Spectrum 和 Voice Isolation 会在应用链路之外改变输入。`AVCaptureDevice.preferredMicrophoneMode` 表示用户选择，`activeMicrophoneMode` 表示当前 route 真正生效的模式；Apple 明确说明二者可能不同。因此 Vocal More 只读取并记录两者，不擅自修改用户选择。[preferredMicrophoneMode](https://developer.apple.com/documentation/avfoundation/avcapturedevice/preferredmicrophonemode)；[activeMicrophoneMode](https://developer.apple.com/documentation/avfoundation/avcapturedevice/activemicrophonemode)；[MicrophoneMode](https://developer.apple.com/documentation/avfoundation/avcapturedevice/microphonemode)。

真实 AGC A/B 的同一 pair 必须具有已知且相同的 preferred/active mode。模式未知、route 覆盖变化或两次录音模式不一致时，文件仍保留在本地 sidecar 中，但不进入 Apple AGC 专属 paired delta。静态读取不打开音频；运行时值在 engine 启动后再次记录。

## 4. CPU、GPU 与 Neural Engine 分工

| 工作 | 首选硬件/API | 原因 |
| --- | --- | --- |
| Voice Processing、采样率转换 | Apple 音频框架 / CPU | 系统拥有设备模型、回声参考和实时调度信息 |
| HPF、RMS、相关性、混音、PCM 转换 | CPU + Accelerate/vDSP | 数据块小、串行依赖强；避免 GPU 提交和同步开销 |
| 神经 VAD、降噪或声学质量模型 | Core ML，`computeUnits = .all` | 让系统在 CPU/GPU/ANE 间按模型和设备调度 |
| 已有大型自定义张量图 | MPSGraph，先离线或 worker 验证 | 仅当批量和模型规模能摊薄图编译、buffer 与 command 提交成本 |
| UI 波形 | CPU 计算标量，GPU 只负责正常界面合成 | 不把音频实时线程与渲染队列耦合 |

Accelerate 的 vDSP 提供向量算术、归约、相关、卷积、FFT 和 biquad 等优化例程。当前原生 worker 已用 vDSP 完成 RMS、向量增益、削波和 PCM 转换；高通滤波保留一阶有状态实现，以避免跨块状态被隐式重置。[vDSP 概览](https://developer.apple.com/documentation/accelerate/vdsp-library)。Apple 的示例也把实时 biquad 内核放在 C++ 中，以确保实时安全。[vDSP Audio Unit 示例](https://developer.apple.com/documentation/accelerate/creating-an-audio-unit-extension-using-the-vdsp-library)。

ANE 不应被当作可直接调用的“音频加速器”。只有在产品确实引入并验证神经 VAD、降噪或声学质量模型时，才使用 Core ML 的 `MLModelConfiguration.computeUnits`；`.all` 允许系统按真实模型选择包括 Neural Engine 在内的可用单元，`cpuOnly` 适用于后台或需要避免 GPU 竞争的情形。[MLComputeUnits](https://developer.apple.com/documentation/coreml/mlcomputeunits)与[MLModelConfiguration](https://developer.apple.com/documentation/coreml/mlmodelconfiguration)。MPSGraph 可以构建并编译运行在不同计算设备上的图，但当前 40 ms 标量 DSP 不足以证明手工 GPU 图有收益。没有模型时，不为“占用 NPU/GPU”增加空壳路径。[MPSGraph 概览](https://developer.apple.com/documentation/metalperformanceshadersgraph)。

## 5. 已实现的原生运行时

生产路径现在包含一个窄的 Objective-C++ 音频岛，并通过版本化 C ABI 由 Python `ctypes` 加载：

```text
AVAudioEngine VoiceProcessingIO tap
  → 预分配 raw Float32 SPSC（满时丢块，不等待）
  → 原生 worker：AVAudioConverter + HPF + vDSP
  → 预分配 PCM16 SPSC（满时丢块，不等待）
  → Python 普通 consumer thread
  → AudioRecorder / ASR
```

初始化阶段分配 converter、PCM buffer 和两个单生产者/单消费者队列。Apple 管理的实时 tap 只读取第一个 Voice Processing 逻辑通道并复制到固定槽位；它不进入 Python、不获取 GIL、不记录日志、不做文件或网络 I/O，也不等待锁。采样率转换、滤波、电平计算和 PCM 转换都在原生 worker 中完成。Python 只消费完整的 16 kHz mono PCM16 块。

流式模式在启动 recorder 之前先为 ASR 建立 admission。ASR 对音频/session 配置做同一条会话快照，并给连接、回调和每个出站 PCM 块绑定 generation；因此 recorder 启动门一旦放行，第一块 PCM 已有可接收的有界队列。若麦克风权限、发现或启动失败，模式调用有界 `abort_startup` 使该 generation 失效。迟到的连接、旧 socket 回调以及 sender 已取出的旧块都不能发布到下一次会话，从而同时避免首帧丢失和跨会话污染。

启动时在 `AVAudioEngine.startAndReturnError` 成功后再次读取 Voice Processing 和 AGC 状态。只有 getter 与请求模式一致，状态才标记为已验证；否则销毁该流并进入可观测回退。停止顺序先禁止新 tap 输入，再移除 tap、排空 raw 队列、向 `AVAudioConverter` 发送 EOS、发布最终不足一块的尾帧，最后结束 PCM 队列。

回退顺序是：Objective-C++ 原生库 → PyObjC Voice Processing → PortAudio。Studio Display 是经过实机计时确认的低延迟例外：VoiceProcessingIO 首帧约需 1.3 秒，兼容路径约需 0.58 秒，因此直接使用 PortAudio/CoreAudio，避免漏掉听写句首。应用不会通过空闲时持续占用麦克风来隐藏启动成本。回退不修改用户保存的 `gain_mode`；`automatic` 回退时恢复保存的软件 gain 和 limiter，并在 `last_session` 中留下结构化 code/stage。PyObjC 是兼容路径，不是实时安全基准路径。

完整的 ABI、构建和故障边界见 [`native-audio-runtime.md`](native-audio-runtime.md)。只有将来引入自有高优先级实时辅助线程时才评估 Audio Workgroups；当前 tap 线程由 Apple 管理，而 converter/DSP worker 允许有界排队。[Audio Workgroups](https://developer.apple.com/videos/play/wwdc2020/10224/)。

## 6. 发布门槛和常见错误

自动 AGC 默认值不能只凭主观听感发布。至少在目标 Mac 型号上比较：

- 轻声、正常声、风扇噪声和扬声器回录四组语料；
- CER/WER、首字延迟、削波样本比例、静音噪声底和回退率；
- 回调 P95/P99 耗时、音频 overload 次数、CPU 时间与 Energy Log；
- `automatic` 对 `manual` 的盲听偏好和转写结果。

仓库提供两层验证：

1. `scripts/benchmark_audio_quality.py` 对已有 WAV/raw PCM 做可重复离线指标计算。
2. `scripts/capture_audio_quality_ab.py` 在用户明确确认后按 ABBA 顺序录制真实麦克风对照，并把运行后 getter、回退和丢块状态写入 sidecar。软件回退样本不会被冒充为 Apple AGC 样本。

具体协议见 [`audio-quality-benchmark.md`](audio-quality-benchmark.md)。终端脚本与正式 App 是不同的 TCC 身份；CLI 权限结果不能替代 `Vocal More.app` 内的麦克风测试。

本轮实现与自动验证没有实际打开麦克风，也没有执行目标硬件 ABBA、盲听或 CER/WER。仓库内的静态 probe、合成/离线测试和 A/B 工具证明的是契约与实验可重复性，不证明 automatic 已优于 manual，也不证明多逻辑通道已经提高收音质量。

常见错误包括：把 `max_input_channels` 当作物理胶囊数量；同时开启 Apple AGC 和软件 gain；用 `np.interp` 做 48 kHz→16 kHz 降采样；忽略 `AVAudioConverter` EOS 尾帧；在实时回调里记录日志、分配大数组或等待锁；为了“使用 GPU/ANE”而把小块标量 DSP 强行搬离 CPU。

当自动 AGC 在特定设备或轻声语料上持续劣于手动链路时，应调整该设备族的新安装默认策略，而不是删除手动模式或覆盖用户保存的 gain。任何多通道实验也必须保留当前 Voice Processing 路径作为可观测回退。
