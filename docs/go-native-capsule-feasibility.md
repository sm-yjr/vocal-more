# Go 核心与原生胶囊可行性评估

## 结论

浮动胶囊已从 `WKWebView` 改为现有 `NSPanel` 内的原生 AppKit 视图，Python
业务层和原有公开状态接口保持不变。已安装的 0.4.6 在
当前机器上由胶囊触发的 WebKit 辅助进程约占 70 MiB；移除这组常驻进程
能够直接降低空闲物理占用，同时不改变语音输入、润色、命令模式和粘贴
等核心流程。设置窗口可以继续按需创建 WebView，后续再单独评估原生化。

Go 可以承接网络协议、配置、状态机、存储和纯业务逻辑，也可以通过 cgo
复用项目现有的 `libvocal_more_audio.dylib` C ABI。若产品仍考虑 Windows，
建议长期形态采用 Go 核心加平台原生外壳：macOS 使用 Swift/AppKit，Windows
使用对应的原生 UI。直接让 Go 通过大量 Objective-C 桥接承担 macOS UI，
会把主线程、run loop、对象所有权和 autorelease 管理集中到手写绑定层，
维护风险明显偏高。

Go 迁移本身无法承诺进程低于 100 MiB。达标需要以物理占用、线程和 socket
基线持续验证，并把 Python 运行时真正移出最终进程。短期同时运行 Python、
Go 和原生外壳还会增加组件数量，因此应按边界逐步替换。

## 原生胶囊边界

当前 `FloatingCapsule` 已经拥有 AppKit `NSPanel`，公开状态也比较集中：显示
和隐藏、录音状态、音量、处理阶段、流式文本、模式提示、取消和完成。推荐
保留这层 Python 接口，只替换内部渲染器。

黑色胶囊、边框和阴影使用 layer-backed `NSView`/`CALayer`；波形与进度使用
`CAShapeLayer`；文字优先使用 `NSTextField`，以获得系统文字渲染与无障碍
支持；取消和完成使用 `NSButton` 或明确的命中测试。所有 AppKit 状态仍在
主线程更新，`FloatingCapsule` 作为唯一状态所有者。这个设计能完整保留当前
交互，也避免引入 SwiftUI bridge。

Apple 对 [`NSPanel`](https://developer.apple.com/documentation/appkit/nspanel)、
[`NSView`](https://developer.apple.com/documentation/appkit/nsview) 和
[`CALayer`](https://developer.apple.com/documentation/quartzcore/calayer) 提供了
稳定的原生边界；`NSView.layer` 与 `updateLayer()` 可用于 layer-backed 绘制。
`CATextLayer` 的子像素抗锯齿有限，因此正文使用 `NSTextField` 更稳妥。

当前实现已经覆盖状态映射、波形、渐近进度、reduced-motion、中英文标签、
取消、完成、Prompt 提示和流式文本。原生 target/action 已在真实 AppKit 环境
中触发验证。冷启动且胶囊完成预热后，30 秒基线为 16 MiB physical footprint、
54.1 MiB RSS、0 socket、4 线程，采样期增长为零。完整的录音循环与设置窗口
场景仍需按后台基准矩阵继续测量。

## Go 核心边界

阿里云实时语音接口公开了原生 WebSocket 地址、Bearer 鉴权及
`session.update`、`input_audio_buffer.append`、commit 等事件协议，因此 Go
可以直接实现协议客户端，无需依赖 Python DashScope SDK。官方通用 Go SDK
主要面向 OpenAPI；实时语音仍应建立项目内的小型协议客户端，并通过录制的
JSON 事件夹具与现有 Python 行为做契约测试。协议参考包括
[Qwen Omni Realtime](https://help.aliyun.com/zh/model-studio/realtime)、
[Qwen Audio Realtime 客户端事件](https://help.aliyun.com/zh/model-studio/fun-audiochat-client-events)
和 [Qwen ASR Realtime 客户端事件](https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-client-events)。

项目现有音频运行时已经通过 C 头文件暴露能力。Go 官方
[`cgo`](https://go.dev/cmd/cgo/) 支持调用 C，并可在包中编译 Objective-C
源文件，所以录音增益、高通、限幅和噪声门应先保持原实现，避免在迁移中
改变低声输入的核心效果。

第一阶段只实现 Go realtime-client spike，接在现有 ASR 抽象之后，覆盖
连接、session 更新、音频追加、commit、结果事件、取消、超时、重连、warm
session 和计费字段。第二阶段迁移配置、领域状态机、记录索引和网络适配器。
第三阶段在完整功能矩阵通过后，用 Swift/AppKit 主程序替换 Python host，
此时才移除 Python 运行时。

## 风险与粗略投入

原生胶囊的首个实现和空闲资源验收已经完成。Go 实时协议 spike 属于中风险，
预计 1 至 2 周。包含菜单栏、全局热键、权限、
粘贴板、设置、自动更新、全部 ASR/LLM 路径和发布链的完整替换属于高风险，
预计 6 至 10 周以上。这些区间是基于当前代码边界的工程估算，需要在 spike
后用实际差异重新校准。

最主要的协议风险是 SDK 隐含行为，包括事件顺序、异常映射、断线恢复和用量
字段；最主要的平台风险是 macOS 权限、主线程和发布签名链；最主要的产品
风险是迁移期间低声输入质量或首字延迟回退。现有 Python 实现应作为对照组，
每个切片通过功能、延迟和资源三类门槛后再切换默认实现。

## 决策建议

0.4.7 当前已有远端标签且发布流水线失败。生命周期修复位于该标签之后，
更安全的版本策略是把这些修复和原生胶囊作为 0.4.8 候选；如果必须沿用
0.4.7，需要明确授权重打远端标签，并重新执行完整签名、 notarization、
GitHub Release 和 Sparkle appcast 流程。

下一步应完成 10/50 次录音循环与设置窗口关闭后的资源矩阵。Go 工作先限制在
realtime-client spike；在它证明协议一致性和资源收益前，不启动整仓迁移。
