# Rust 后端与 Tauri / SwiftUI 可行性分析

分析日期：2026-09-05。基于 0.4.11 的源码边界和本次内存实测，结合 Tauri 2、Rust、Apple 官方文档。范围是选型与迁移设计；本次没有完成 Rust 实现或两套壳的等功能内存对比，下面的资源收益属于结构判断，不能当作实测 MB 数。

## 结论

两条路线都可行。按 Vocal More 目前以 macOS 原生体验、低声输入和减少常驻开销为重点的方向，建议 **Rust 业务核心 + SwiftUI 设置页 + AppKit 胶囊和系统集成**。Rust 不依赖 Tauri 才能承接后端，也不必为了“前后端分离”增加常驻服务进程。

如果下一阶段优先目标变成同时维护 macOS / Windows，或尽快复用现有 React 设置页，选择 **Rust + Tauri，macOS 胶囊继续原生实现**更合适。Tauri 的主要收益是前端复用、跨平台壳和移除 Python，不会自动消除 WebKit：macOS 上仍使用 WKWebView，并采用核心进程加 WebView 进程的模型。[Tauri 进程模型](https://v2.tauri.app/concept/process-model/)

| 项目因素 | Rust + Tauri | Rust + SwiftUI / AppKit |
| --- | --- | --- |
| 现有 React 设置页 | 大部分可复用，替换通信桥 | 七个设置区域需要重写 |
| macOS 设置页资源 | 仍有 WebKit 与网页资源开销 | 可去掉设置页 WebKit |
| 胶囊与 Fn / 双击 Cmd | 仍需 macOS 原生适配 | 与原生外壳更直接 |
| Windows 复用 | 核心和设置 UI 均可复用 | 核心可复用，UI 另做 |
| 后端业务迁移 | 两者基本相同 | 两者基本相同 |
| 主要工程成本 | 原生边界、WebView 生命周期、更新链整合 | 设置 UI 重写、Rust/Swift ABI 与异步事件桥 |

## 当前代码能直接复用什么

采集和低声 DSP 已位于 `native/audio/src/VocalMoreAudio.mm`，头文件 `native/audio/include/vocal_more_audio.h` 提供创建、读 PCM、暂停、恢复、销毁、DSP 设置和诊断接口。Rust 可以通过现有 C ABI 消费 PCM，继续使用 AVAudioEngine、Voice Processing 和 Accelerate。首次迁移不需要重写音频算法。

胶囊已使用 NSPanel 和原生图层，但实现位于 Python/PyObjC。两条路线都要移植这一小层。SwiftUI 路线建议设置页用 SwiftUI，胶囊继续 AppKit/CALayer，以保留非激活窗口、全屏空间、鼠标穿透和动态尺寸控制。[Apple NSPanel](https://developer.apple.com/documentation/appkit/nspanel)、[SwiftUI Settings](https://developer.apple.com/documentation/swiftui/settings)

设置页已有集中通信边界：`frontend/settings/src/settings/python-bridge.ts`、`src/vocal_more/ui/settings_bridge.py` 和 `settings_actions.py`。Tauri 路线可把浏览器到 Python 的消息替换为 Rust command，把返回事件接入现有 store，保留大部分表单和交互。无需逐组件重写业务请求。[Tauri commands](https://v2.tauri.app/develop/calling-rust/)

主要迁移工作在业务层：当前有多种实时 ASR 协议、短文件/离线识别、润色和会议处理，同时包含会话预热、取消、超时、重连、计费、录音恢复、词典和粘贴。不能用一个成功的 WebSocket 连接代表后端已移植完成。相关边界集中在 `bootstrap.py`、`application/`、`core/asr_engine.py` 和 `infrastructure/asr/`。

## 两条路线的具体形态

Tauri 路线由 Rust 核心持有配置、记录存储和语音任务。设置窗口按需创建，关闭时完成表单同步、停止播放器并销毁 WebView，避免使用永远隐藏的网页窗口维持应用运行。Tauri 区分 hide、close 和 destroy；销毁窗口也不意味着系统会立刻归还全部 WebKit 缓存，仍需测量关闭后的进程与内存。[Tauri WebviewWindow API](https://docs.rs/tauri/latest/tauri/webview/struct.WebviewWindow.html)

胶囊不再退回 HTML 波形窗口。它使用原生 NSPanel，通过主线程接收合并后的音量和状态；PCM 留在 Rust/原生采集之间。Tauri 有全局快捷键插件，但其组合键按下/释放示例不能证明单独 Fn、修饰键双击与本项目 CGEventTap 行为一致。优先复用现有事件语义，针对这几个真实手势验证平台适配。[Tauri Global Shortcut](https://v2.tauri.app/plugin/global-shortcut/)

SwiftUI 路线建议把 Rust 核心编译为静态库，由 Swift/AppKit 主程序链接；只暴露任务句柄、命令、少量状态和事件。配置可通过小型 JSON 消息传递，音频使用已有 PCM 缓冲/文件边界。Rust 导出的数据由 Rust 提供对应释放函数；后台事件切回 MainActor 更新 UI。Rust 官方支持向其他语言链接的 staticlib/cdylib。[Rust linkage](https://doc.rust-lang.org/reference/linkage.html)、[Rust FFI](https://doc.rust-lang.org/nomicon/ffi.html)

两条路线都采用一个明确的后台任务运行时和有界队列，取消按会话代次生效。持续录音写入文件，识别、历史和会议流程读取文件或流，避免把 Python 的大字节串简单翻译成 Rust 的大 Vec 再反复 clone。100MB 保持参考性质，优先观察是否随时长或循环次数持续增长。

## 发布与用户数据

保留现有配置、录音索引与文件格式，迁移程序直接读取已有数据。设置层的变动不应要求用户重录快捷键、重建词典或重新导入历史。

首个迁移版优先保留相同 bundle ID、Developer ID 身份和 Sparkle 更新路径，以便现有用户正常升级。Tauri 自带 updater 使用带签名的更新清单，不能直接当作当前 Sparkle appcast 的替换件；若要切换更新系统，应安排明确的衔接版本，而非在后端重写时顺手更换。[Tauri Updater](https://v2.tauri.app/plugin/updater/)

## 建议的下一步

先做一个 Rust 垂直切片：接现有原生音频 C ABI，完成一条常用实时 ASR 路径，从开始、音频传输到停止和取消。保持 Python 版本可作结果与延迟对照，但不把 Python 和新后端同时作为最终常驻架构。这个切片会直接验证后端迁移价值，也能被两种 UI 外壳共享。

随后用相同 Rust 核心做两种薄壳对照。Tauri 接现有设置页；SwiftUI 接通同等的录音、设置保存和历史播放路径。比较首次启动、设置打开/关闭、20 分钟历史回放和连续录音循环，统计主进程及明确归属的辅助进程、首字延迟与关闭后的增长。空窗口/Hello World 的内存数不作为选型依据。

验证保持精简：实际录音与取消、设置保存与重开、录音播放与释放、升级后读取旧数据。这几个集成场景配合小量协议事件夹具即可支撑迁移；不复制现有源码字符串断言，也不为每个内部方法建立 mock 测试。若两条路线都满足体验要求，则 macOS 优先选择 SwiftUI/AppKit，跨平台速度优先选择 Tauri。
