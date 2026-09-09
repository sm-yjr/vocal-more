# Vocal More 下一阶段 Rust 技术路线调研

日期：2026-09-09。基于 Vocal More 0.4.16、提交 `12c25ac`。状态：用户已确定 Rust 全栈 + 最新 GPUI Kit；尚未实施迁移。

## 已确定的技术决策

用户于 2026-09-09 明确决定：“就选最新的 GPUI Kit 作为下一步的 Rust 全栈开发方向”。

- Rust 承载共享业务核心，GPUI Kit 承载桌面前端，面向 macOS 和 Windows。
- 启动实现时核验官方最新发布版本并锁定版本或 commit；本次核对的 GPUI Kit `0.6.1` 是调研时的版本快照，不是永久版本上限。
- 复用现有原生音频 C ABI 与必要的平台适配。迁移继续验证内存、延迟、中文输入、辅助功能、低声输入和升级兼容。
- 不再要求 Slint / Tauri / SwiftUI 对照作为迁移前置条件。下文其他框架的分析保留为决策背景，不代表选型仍待确定。
- 本次记录决策，不启动代码迁移；当前已交付版本仍是 Python 实现。

本次用 Exa 从桌面外壳、Rust UI、Web 前端、后台与音频四个方向检索了 44 条结果，按 URL 去掉查询参数和尾部斜杠后得到 43 个不同页面；随后补读官方文档，并用 GitHub API 直接核对发布版本和当前 README。正式结论只引用项目维护方、官方 API 文档与本仓库代码。时间窗口为 2025-09-09 至 2026-09-09，基础架构文档不限制发布日期。

## 1. 开发方向

下一阶段采用 **Rust 共享业务核心 + GPUI Kit 桌面前端**，覆盖 macOS 和 Windows。选型已确定，后续原型验证实现与迁移质量；现阶段没有证据证明某个框架必然占用最少内存。

这个建议扩展了 [9 月 5 日的可行性分析](rust-ui-feasibility.md)：当时主要比较 Tauri 与 SwiftUI；当前仓库已正式提供 Windows 路径，Rust 自绘 UI 的近期进展也值得纳入。SwiftUI/AppKit 仍是 macOS 原生体验的参考与备选，但不预先规定所有前端必须采用 Swift。

分层决策如下：

| 层 | 建议 | 依据和边界 |
| --- | --- | --- |
| ASR、润色、会话状态、录音存储 | Rust 核心试点 | 两个平台共享；生命周期、取消和有界缓冲有明确价值 |
| 设置、历史、词典 UI | GPUI Kit | 用户已确定；复用桌面组件与定制视觉，验证实际行为与资源 |
| macOS 胶囊、Fn、粘贴、权限 | 保留原生语义，按需使用 `objc2` 或窄 C ABI | 自绘 UI 不能替代平台事件与权限接口 |
| macOS 音频采集与低声 DSP | 复用现有 Objective-C++ C ABI | 已有 Voice Processing、采样率转换、vDSP 和实时队列，无需同时重写 |
| Windows 音频 | 初期保留行为对照，随后评估 CPAL/WASAPI | 设备恢复、虚拟麦、蓝牙和低声效果需要 Windows 实机验证 |
| 既有 React 设置页 | 作为交互与行为对照 | 下一阶段使用 GPUI Kit 重建；Tauri 分析仅保留为历史选型背景 |

“Rust 前后端表现很好”需要拆成三件事：Rust 网络服务、Rust 编写的浏览器界面，以及 Rust 自绘桌面界面。三者使用不同运行时和渲染路径，不能由 HTTP 吞吐或 WebAssembly 演示推导出桌面待机内存。

## 2. 近期变化与证据纠偏

以下版本由本次 GitHub API 返回的发布记录确认，表示检索时可见状态，不承诺以后仍是最新版本。

| 项目 | 已核对版本与时间 | 对选型的意义 |
| --- | --- | --- |
| Tauri | [2.11.5，2026-07-01](https://github.com/tauri-apps/tauri/releases/tag/tauri-v2.11.5) | 继续作为 Rust 后端配网页 UI 的完整桌面外壳候选 |
| Slint | [1.17.1，2026-07-07](https://github.com/slint-ui/slint/releases/tag/v1.17.1) | 已有稳定 1.x API 承诺，适合作长期维护对照 |
| Iced | [0.14.0，2025-12-07](https://github.com/iced-rs/iced/releases/tag/0.14.0) | 新增输入法支持、响应式渲染、无界面测试和端到端测试，不能沿用早期“不支持 IME”的评价 |
| Dioxus | [0.7.10，2026-07-30](https://github.com/DioxusLabs/dioxus/releases/tag/v0.7.10)；[0.8.0-alpha.1，2026-07-31](https://github.com/DioxusLabs/dioxus/releases/tag/v0.8.0-alpha.1) | 正式发布与预发布分开；主框架版本也不等同于某个渲染器的成熟度 |
| GPUI Kit | [0.6.0，2026-09-03](https://github.com/longbridge/gpui-kit/releases/tag/v0.6.0)；[0.6.1，2026-09-09](https://github.com/longbridge/gpui-kit/releases/tag/v0.6.1) | 原 GPUI Component 已扩展和更名，最近一周的变化足以影响原型选型 |

搜索索引中 GPUI Component 仍显示 0.5.1；直接访问官方仓库后，确认新仓库为 `longbridge/gpui-kit`。0.6.0 增加分层组件架构、原生菜单、辅助功能信息、文本编辑与开发工具，同时包含破坏性 API 变更；0.6.1 继续完善编辑、辅助功能和无界面 UI 测试。它值得进入候选，但刚发布的版本不能直接视为已在本产品验证。[0.6.0 发布说明](https://github.com/longbridge/gpui-kit/releases/tag/v0.6.0)、[0.6.1 发布说明](https://github.com/longbridge/gpui-kit/releases/tag/v0.6.1)

Blitz 也有索引滞后：Exa 返回旧 README 的 pre-alpha 描述，GitHub API 读取的当前主分支明确改为 **beta**，允许愿意接受快速变化的早期采用者开发应用，同时仍说明存在较多缺陷和缺失功能、正在提升到生产质量。本文采用后者，并限定为当前开发分支状态，不能将其直接套到所有 Dioxus 发布版本。[Blitz 当前 README](https://github.com/DioxusLabs/blitz#status)

## 3. Rust 前端如何选择

### GPUI Kit：已选定的桌面前端

GPUI 使用 GPU 绘制界面，在 macOS 使用 Metal；GPUI Kit 提供其上的组件和交互层。维护方列出了已经发布的 Longbridge Pro 作为实际产品案例，说明可复用组件并非只存在于演示中。它的表单、侧栏、主题和列表能力与 Vocal More 的设置、历史和词典界面匹配。这个匹配判断是本次选型推断，不是性能实测。[GPUI 文档](https://github.com/zed-industries/zed/tree/main/crates/gpui)、[GPUI Kit](https://github.com/longbridge/gpui-kit)

原型只引入需要的组件。GPUI Kit 的 `gpui-shell` JavaScript 扩展运行时、独立 `gpui-wry` WebView 和编辑器能力不是听写应用的必需项。0.6.1 的 facade 默认 feature 是 `component` 与 `assets`，应以锁定版本的依赖图确认实际带入内容。[0.6.1 Cargo 配置](https://github.com/longbridge/gpui-kit/blob/v0.6.1/crates/kit/Cargo.toml)

主要成本是 API 变化和平台细节。GPUI 官方仍明确标为 pre-1.0，并提示版本之间经常出现破坏性变更。GPU 渲染还会引入纹理、字形和渲染缓存；有商业产品采用，不代表其后台能耗、中文候选窗或屏幕阅读器已满足 Vocal More。采用时固定版本或 commit，按需绘制，并在测量时关闭 FPS/HUD 等诊断组件。[GPUI README](https://github.com/zed-industries/zed/tree/main/crates/gpui)

### Slint：稳定性与轻量实现的对照

Slint 提供 `.slint` 声明式 UI、Rust 接口、Live Preview 和跨平台运行能力，并明确承诺稳定 1.x API。它适合控件、表单和列表较多的应用，值得与 GPUI Kit 使用同一组页面做对照。它的“native”包含编译和平台集成含义，不能直接理解为全部控件都是 AppKit 系统控件；视觉、输入与辅助功能仍按实际后端验证。[Slint 官方仓库](https://github.com/slint-ui/slint)

当前项目为 `GPL-3.0-only`。Slint 1.17.1 提供 GPL-3.0-only 选项，无需为了使用它而改变 Vocal More 的许可证。正式引入时按所选分支保留声明与第三方许可文件。[Slint 1.17.1 许可文件](https://github.com/slint-ui/slint/blob/v1.17.1/LICENSE.md)

### Tauri：前端复用最多的路线

Tauri 使用 Rust 核心管理状态、窗口和系统能力，UI 仍由 macOS 的 WKWebView、Windows 的 WebView2 等系统 WebView 渲染。现有 `frontend/settings/` 的 React 页面与集中通信桥使这条路线有较高复用价值。它比重写整个设置页更容易维持现有交互，但本仓库本来已经使用 WKWebView，不能套用“Electron 换 Tauri”的宣传数字估算收益。[Tauri 进程模型](https://v2.tauri.app/concept/process-model/)

如果选 Tauri，设置页按需创建和销毁，原生胶囊继续独立于 WebView。PCM 不跨网页 IPC，只传状态、文本和录音 ID。系统 WebView 生命周期与缓存实际何时回收，要在关闭设置后的进程归属和总 footprint 中观察。

### Dioxus、Iced 与 Leptos

| 候选 | 本次判断 | 适用边界 |
| --- | --- | --- |
| Dioxus Desktop | Rust 写 UI，桌面 WebView 路线与 Native 路线分开评估 | 统一 Rust 语法不等于去掉 WebView |
| Dioxus Native / Blitz | 无系统 WebView、有 CSS/布局复用潜力，列为持续关注候选 | 当前开发分支 beta；CSS、输入、辅助功能和发布版本匹配仍需验证 |
| Iced 0.14 | 已有实际 IME 与测试能力进展，作为后备 | 官方仓库仍标 experimental，不能仅由版本号判成熟或不成熟 |
| Leptos | 适合将来独立 Web 产品或管理站 | 使用 WASM 与浏览器 DOM/SSR，不直接解决桌面原生外壳与待机开销 |

资料：[Dioxus 渲染器架构](https://github.com/DioxusLabs/dioxus/blob/v0.7.9/notes/architecture/06-RENDERERS.md)、[Blitz](https://github.com/DioxusLabs/blitz)、[Iced 0.14](https://github.com/iced-rs/iced/releases/tag/0.14.0)、[Iced 状态](https://github.com/iced-rs/iced)、[Leptos 渲染方式](https://book.leptos.dev/getting_started/)。

后续只实现 GPUI Kit 路线的原型，以现有产品作为行为与资源对照，不增加多框架并行原型工作。

## 4. Rust 后端的收益与边界

Vocal More 的主要后台工作是客户端：连接 ASR WebSocket、发送音频、接收文本、调用润色 HTTP/SSE、写录音和维护状态。因此应优先使用 `Tokio`、`tokio-tungstenite`、`reqwest` 和序列化/存储库。没有必要为了“全栈 Rust”再引入一个常驻 HTTP 服务；只有已有 RPC 接口确实需要服务器时，才评估 Axum 等服务端框架。[Tokio](https://docs.rs/tokio/latest/tokio/)、[WebSocket 客户端](https://docs.rs/tokio-tungstenite/latest/tokio_tungstenite/)、[reqwest](https://docs.rs/reqwest/latest/reqwest/)

Rust 的实际价值是把句柄、音频块和任务所有权表达清楚，减少对象和数据复制，让平台共享相同协议与状态逻辑。网络等待和云端识别仍可能主导首字延迟；无法据此承诺识别更准或一定更快。队列无界、`Vec` 反复克隆和资源未退出，在 Rust 中同样会造成内存增长。

建议一个受控 Tokio runtime，按本应用少量并发连接配置工作线程，优先实测 1–2 个 worker，而非直接采用每 CPU 核一个 worker 的默认多线程配置。UI 留在平台主事件循环，长期音频消费放独立普通线程；实时采集回调继续只写预分配 SPSC 队列。[Tokio runtime](https://docs.rs/tokio/latest/tokio/runtime/)、[有界通道](https://tokio.rs/tokio/tutorial/channels)

Rust 也不能自动终止卡死的 CoreAudio/驱动调用。Tokio 官方明确说明，已经开始的 `spawn_blocking` 任务无法通过 `abort` 停止；关闭 runtime 也可能继续等待。必须保留现有的启动代次、过期结果拒收、超时和退出策略，不能把 Python 线程换成 Rust task 后删去这些保护。[spawn_blocking](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html)、[当前并发模型](concurrency-runtime-model.md)

最小推荐数据流如下，阶段一先由无 UI 的 Rust runner 验证：

```text
macOS 原生采集 C ABI / Windows 采集适配器
    → 有界 PCM 交接
    → Rust 会话核心
        → 分块写录音文件
        → 有界 ASR 发送队列 → WebSocket
        → 转写 / 润色事件 → UI 主线程
```

首次只移植一条现有常用 ASR 协议，并保持开始、音频块、结束、取消、失败与代次隔离的语义。网络跟不上时必须显式选择有界等待、终止或转文件重试策略，不能无界堆积，也不能静默丢音后显示识别成功。

## 5. 平台能力与既有资产

macOS 的低声输入能力由 `native/audio/src/VocalMoreAudio.mm` 和 `native/audio/include/vocal_more_audio.h` 承载：Voice Processing、AGC 回读、AVAudioConverter、vDSP、暂停/恢复和诊断接口已经存在。Rust 可以直接调用这套 C ABI。CPAL 提供跨平台低层音频 I/O 和 macOS CoreAudio / Windows WASAPI 后端，但这些接口的存在不能证明自动获得现有 Apple 语音处理链的行为。[现有音频架构](apple-audio-architecture.md)、[CPAL 官方仓库](https://github.com/RustAudio/cpal)

Fn 按住说话、双击 Cmd、非激活胶囊、全屏 Spaces、辅助功能读取、粘贴恢复和权限入口需要单独的系统适配器。`objc2` 已提供 AppKit、Foundation、WebKit 等 Apple 框架绑定，Rust 可以直接做这些集成；这降低了“必须使用 Swift 外壳”的约束，但不会消除主线程、生命周期和 unsafe 边界。[objc2 框架绑定](https://docs.rs/objc2/latest/objc2/topics/about_generated/index.html)

普通全局快捷键库提供的是一部分能力。macOS 需要主线程事件循环，Windows 也有事件循环要求；库支持组合键不能证明单独 Fn 和修饰键双击与当前实现一致。[global-hotkey 平台约束](https://github.com/tauri-apps/global-hotkey)

迁移初期可以用 PyO3 把 Rust 模块嵌入当前 Python 应用，以便做行为对照；但这只适合作为过渡，不能声称已消除 Python 常驻。更推荐先保持纯 Rust 核心和独立 runner，只有确需在现有 UI 中试用时增加薄桥接。最终可链接到 Rust 外壳，或经 staticlib/C ABI 接平台 UI。[PyO3](https://pyo3.rs/)、[Rust 链接类型](https://doc.rust-lang.org/reference/linkage.html)

迁移同时保留配置、词典、录音索引、文件格式与现有升级入口。Sparkle appcast 和 Tauri updater 的签名 JSON 不是同一个协议；若更换更新器，需要明确的衔接版本。Rust/Cargo 不取代 Developer ID 签名、公证、stapling 或 Windows 打包验证。[现有发布流程](release.md)、[Tauri Updater](https://v2.tauri.app/plugin/updater/)

## 6. 可执行的下一步与停止条件

### 阶段 A：建立优化后的 Python 对照

先验证本次对话发现的 Sparkle `scan_classes=False` 候选优化，再重新记录完整应用的资源基线。独立进程的加载实验已经显示显著差异，但没有测得修改后完整应用的 315 MB 能降到多少；也没有完成自动更新流程验证。Rust 的收益必须与合理优化后的 Python 版本比较。

后续进展（同日）：已修正类扫描并完成同一应用包的启动对照，60 秒主进程 footprint 从 307.5 MiB 降至 93.8 MiB；控制器可用，尚未覆盖下载与安装。方法与边界见 [启动内存测量记录](sparkle-startup-memory-results-2026-09-09.md)。该修正纳入 0.4.17 发布。

### 阶段 B：Rust 核心垂直切片

范围是现有原生音频 C ABI、一条实时 ASR 协议、文件化录音、停止/取消和退出。使用相同音频文件及协议事件夹具验证结果，再进行授权的实机采集。对失败、慢网、取消后迟到事件和睡眠唤醒建立少量集成用例，不逐函数照搬 Python 测试。

通过条件：下游 PCM 契约保持 16 kHz / mono / PCM16；离线协议结果一致；取消与迟到事件不会污染新会话；音频块常驻量有界；录音文件可恢复和回放。这里先验证可复用核心，不引入全套设置 UI。

### 阶段 C：GPUI Kit UI 与现有产品对照

将 Rust 核心接入 GPUI Kit，先做一组有代表性的页面：设备与增益设置、含中文输入的词典表单、分页或虚拟化历史列表、文件播放和胶囊状态。使用真实平台适配，与现有 Python 产品比较完整场景。

| 必测场景 | 记录什么 | 判定原则 |
| --- | --- | --- |
| 冷启动 60 秒、使用后待机 5 分钟 | 主进程与明确归属 helper 的 footprint、CPU、唤醒 | 不靠隐藏窗口维持持续重绘 |
| 设置开关 20 次 | 窗口关闭后资源与增长趋势 | 缓存可到平台期，不能持续线性增加 |
| 30 分钟录音、50 次短录音循环 | 峰值、结束后占用、丢块、任务/句柄增长 | PCM 有界，结束与取消后资源收敛 |
| 20 分钟历史音频回放 | 播放峰值、关闭后释放 | 直接文件播放，无全量 Base64 或等价复制 |
| 相同音频和相同网络条件 | 开始至首帧、首字 p50/p95、结束至可粘贴 | 区分本地与云端时间，不能由一次在线结果判胜负 |
| 中文 IME、键盘导航、VoiceOver / Narrator | 候选窗、组合输入、焦点、朗读与控件操作 | 关键操作可完成，不能以“框架有接口”代替验收 |
| Fn、全屏、多显示器、睡眠/设备切换 | 焦点、窗口行为、取消与恢复 | 保持已建立的产品语义 |
| 旧数据与升级 | 配置、词典、录音、签名和更新连续性 | 用户数据和正常升级不退化 |

macOS 使用 physical footprint，Windows 使用合适的私有内存与工作集指标，分别在同一 OS 内比较；不要把两个平台不同指标直接求比值。固定 release 构建、日志级别、硬件、输入设备和数据量。GPU UI 也记录图形资源与后台能耗，避免只看 CPU 堆。

建议先以“相对优化后的 Python，完整使用后的待机占用降低至少 30%，且无关键行为与延迟退化”作为原型进入全面迁移的资源门槛。30% 是供决策使用的拟议门槛，**不是已测收益或框架保证**。如果收益不足，仍可因跨平台维护价值保留 Rust 核心，但不应以省内存为理由继续全面重写 UI。

### 阶段 D：逐项替换并退出旧运行时

核心和前端通过后，再迁移其余 ASR 协议、润色、词典学习和配置业务。以功能域逐项替换，保持回退构建；最后移除 Python host 和不再使用的 WebView/依赖。版本、许可证和正式发布流程仍遵循项目约定。`AGENTS.md` 已记录下一阶段 Rust 全栈 + GPUI Kit 方向；当前 Python 交付版本的维护流程继续适用。

## 7. 本次完成范围

已完成官方资料检索、发布版本核对、现有代码边界检查和试点设计。未编译或运行 Rust UI 原型，未做等功能框架内存/延迟测试，未启动麦克风或请求云端 ASR，未改动产品源码、锁文件或发布配置。

环境附注：查询 GitHub 时一次调用 PATH 中的 `python3`，触发了本机包装脚本的 `uv run python` 自动同步 `.venv`。同步日志提示旧 `dashscope-1.25.9.dist-info` 缺少 `RECORD`。随后改用 `/usr/bin/python3` 完成只读查询；本次没有进一步修改环境或运行产品测试。Git 工作区在新增本报告前保持干净。
