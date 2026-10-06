# macOS Rust 平台适配与验收

本文件记录 `rust/crates/desktop/src/platform/` 与当前 Python 薄壳的系统能力对照。胶囊绘制与设置界面由各自模块验收；平台测试通过不能证明胶囊像素、动画或所有设置交互等价。

## 能力与所有权

| 能力 | 当前 Python 依据 | Rust 实现与约束 |
|---|---|---|
| 全局 Fn/Globe、自定义普通键及左右修饰键 | `core/hotkey_manager.py`、`domain/hotkey_catalog.py` | `hotkeys.rs`：独立 event-tap 线程、HID/head-insert tap；重复按键只吞事件，不重复发送按下；多个物理键合并为一组逻辑 press/release；同旗标左右键的释放沿用原判定 |
| 旧双 Cmd 名称 | `HotkeyEvent.DOUBLE_CMD`、后端 `config.rs` | 旧 Python 只保留枚举和回调派发，没有实际双击检测；后端将 legacy `active_hotkeys=[double_cmd]` 归一化为 Fn。Rust 不在默认配置新增全局 Cmd 触发；平台可读取显式旧配置并检测两个独立 Cmd tap，使用 `double_tap_threshold`（默认 0.3 秒，限制 0.15–0.5 秒），排除 Command 快捷键与左右 Cmd 同时按住，自定义 Command 绑定优先 |
| Fn 系统动作 | `core/fn_system_action.py` | 持久化相同 `com.sm-yjr.vocal-more` recovery 键，再调用 Carbon `TISUpdateFnUsageType`；恢复原有效值及原显式/隐式 preference；禁用绑定或退出时恢复。`--no-hotkeys` 完全不读写 Fn guard，避免影响正在运行的旧版 |
| tap 恢复 | `core/hotkey_manager.py`、`rust_ui.py::_watchdog` | timeout/user-input disable 回调重启当前 tap，释放逻辑 held 状态；创建失败以两秒 watchdog 重试，不请求麦克风或自动重放录音 |
| 快捷键录制 | 设置 UI 的物理按键配置 | capture 状态停用听写派发，保留精确 key code、左右变体、modifier flag；事件进入 `hotkey_capture`。两次确认和最多八个键由设置 UI 承担 |
| 原生/兼容粘贴 | `core/macos_native_paste.py`、`core/keyboard_sim.py` | `paste.rs`：主线程 NSPasteboard + CGEvent；兼容路径保留 Command down/V down/up/Command up 与 50 ms 等待。写入前及实际注入前检查原子 epoch/generation/cancel/closing；自发事件有专用 tag，避免自定义 V/Command 键重新触发听写 |
| 恢复剪贴板 | 同上 | 600 ms 后仅在 `changeCount` 保持本次写入值时恢复先前字符串；外部应用的新写入优先。此产品行为沿用原版；验收 harness 另外备份所有剪贴板类型并在退出时恢复 |
| AX 读写观察 | `core/accessibility_text.py`、`rust_ui.py::_paste/_observe` | `accessibility.rs`：唯一线程、容量 16 的命令队列、200 ms AX messaging timeout；真实 CF AX 元素留在 owner 线程，通过 observation ID 保留；焦点移动后读取该元素，不重新定位。先判定安全 role/subrole，再决定是否读取值与选区 |
| 选区坐标 | `accessibility_text.py::_python_text_range` | AX UTF-16 单位转 Unicode scalar 索引；拒绝切开 surrogate pair 的范围；与 Rust 后端学习快照约定一致 |
| 屏幕上下文 | `core/macos_screen_capture.py` | `screen.rs`：只抓主显示器；permission preflight，初次显式动作可 request；1280×720 内等比缩放、三档尺寸/四档 JPEG 品质，验证 JPEG magic 与 190 KiB 上限；原始像素和编码帧均只留内存。host 使用独立有界截帧线程、两秒周期与 230 秒窗口，并检查 session generation |
| 历史录音播放 | `ui/recording_player.py` | `playback.rs`：AVPlayer 流式 WAV/FLAC 文件播放；每 250 ms 检查错误、时长和位置；ID 匹配停止；删除记录停止对应播放 |
| 麦克风试听 | settings microphone playback | NSData + AVAudioPlayer 从内存解码 WAV；停止或自然结束发送 `micTestPlaybackEnded`，不通过 JS 或临时敏感录音文件 |
| 升级 | `infrastructure/sparkle_updater.py` | `updater.rs`：加载包内 Sparkle.framework、持有标准 updater controller 和 delegate；显式 Stable/Nightly 对应 stable/alpha；缺省 preference 返回 nil，沿用包内 `SUFeedURL`，因此既有 beta 构建仍使用独立 beta feed；没有框架时打开正式发布页 |
| 菜单栏 | `rust_ui.py::_build_menu` | `menu.rs`：NSStatusItem + 原生 NSMenu，录音模式、模型、设备、润色、屏幕上下文、润色强度、复制、设置、环境、诊断、升级、退出均保留；中文/英文；使用原有 idle/recording 图标 |
| 通知与权限 | `rust_ui.py` | NSUserNotification 保留原行为；普通开发二进制的 notification center 可为 nil，安全跳过。权限动作打开相应系统设置或调用 AVCaptureDevice/AX/CoreGraphics 请求；异步授权回调只入 CommandSink，不操作 AppKit |

AppKit、pasteboard、菜单、player 和 Sparkle 都由 `MainThreadMarker` 限定在主线程。event tap 与 AX worker 只持有可跨线程的命令通道，各自拥有原生对象。关停先禁止新工作，worker 等待最多 250 ms；若系统调用延迟返回，其线程继续独占 CF 对象直到返回，不从另一线程提前销毁对象或发布结果。

`Platform::next_tick_delay()` 在空闲时最长为两秒；待恢复剪贴板或播放中会提前唤醒。host 即便隐藏所有窗口也必须采用此截止时间，否则无法满足 600 ms 恢复和 250 ms 播放检测。

## Host 事件协议

菜单动作以 `platform_copy_last`、`platform_show_settings {tab}`、`platform_export_diagnostics`、`platform_check_updates`、`platform_quit` 交给 host。业务修改仍直接进入相同 Rust application 命令，如 `set_mode`、`set_device`、`set_config`。

平台异步结果通过 `CommandSink.request("platform_event", {method, params})` 进入主线程。包括：

- `platform_focused_snapshot {request_id, snapshot}`：`request_id` 由 host 原样提供，通常包含 token/epoch/generation；snapshot 不携带进程内 AX 对象。
- `hotkey_capture {key_code, display_name, is_modifier, flag_mask, repeat}`。
- `recordingPlaybackStarted/recordingPlaybackEnded {id}`、`micTestPlaybackEnded {}`、`copiedFeedback {id}`。

`retain_observation(id, snapshot)` 按待认领 target ID 转移真实 AX 对象；`observe(id)` 从 AX owner 线程发送业务命令 `poll_observation {observation_id, focused, retained}`；`end_observation(id)` 只清理该观察，不删除刚准备的新目标。粘贴必须通过 `paste_guarded(..., epoch, generation)`，不可用“当前代次”替换最初 claim 的代次。

## 自动与原生验收

平台自动测试覆盖多键合并/repeat、共享修饰键旗标释放、捕获与物理变体、异常配置、双 Cmd 阈值/单次派发/左右 chord 与快捷键排除/自定义 Command 优先、surrogate 选区、图像缩放界限、剪贴板所有权、更新渠道和缺省 beta feed，并验证 `no_hotkeys` 的初始化、配置变更、capture、watchdog 与退出全过程均不访问 Fn preferences。它们验证逻辑，不能证明 CoreGraphics 能实际向某个应用输入。

独立原生 harness 源为 `rust/crates/desktop/src/platform/tests/native_harness.rs`。对应 bin target 为 `vocal-more-native-platform-acceptance`，在 macOS 运行：

```sh
cd rust
cargo +stable run -p vocal-more-desktop --bin vocal-more-native-platform-acceptance
```

该测试进入真实 `NSApplication.run`，建立自己的两个 NSTextView，并在注入前同时确认自建窗口为 key window、NSApplication active、NSWorkspace foreground PID 为本进程；测试 Unicode 原生/兼容粘贴、取消代次、600 ms 剪贴板恢复和外部写入保护、AX 原对象跨焦点观察及 UTF-16 选区、真实静音历史/试听自主结束、实际主屏 JPEG。使用新建临时数据目录和 `no_hotkeys`，不改现有用户配置、全局 Fn 动作或用户应用内容；结束恢复原剪贴板所有类型和原前台应用。需要本测试进程已有 Accessibility/屏幕录制权限和可交互的解锁会话；缺失时报告失败，不能将只编译通过作为替代证据。

仅验收播放可在命令末尾添加 `-- --playback-only`。此模式只用生成的 200 ms 静音 WAV 验证 AVPlayer 与内存 AVAudioPlayer 自主结束事件，不建立测试窗口、不激活应用、不读取或写入剪贴板，不要求 AX/屏幕权限；用于锁屏会话下独立验证播放器。

2026-10-03 的独立 native smoke 已在本机验证 NSStatusItem 建立/idle-recording 更新/关闭、AX owner 启停、真实主屏 JPEG（15,333 bytes）、静音 WAV 的 AVPlayer 与内存 AVAudioPlayer 创建/播放/停止。该检查发现并修正了仅运行时暴露的 CMTime Objective-C 编码、NSData `const void *` 参数与未打包进程的 notification center nil。平台库的十三项自动测试已通过，见 `.build/rust-platform-unit.log`；所有 desktop targets 的 `cargo check` 通过，见 `.build/rust-platform-all-targets-check.log`。

同日补充的独立 Rust 播放 driver 加载当前 platform 源码并进入真实 `NSApplication.run`，在没有手动 stop 的情况下收到了 `recordingPlaybackEnded` 和 `micTestPlaybackEnded`，见 `.build/rust-platform-playback-isolated.log`。随后正式桌面 crate 的 `--playback-only` harness 同样通过两种自主结束和 BackendDriver 关停，见 `.build/rust-platform-playback-acceptance.log`（`cargo +1.98.1 run --locked`）。完整桌面 harness 的其余能力仍按下述阻塞记录报告。

同日完整 harness 安全拒绝了真实注入：`NSApplication.running=true`，但 `active=false/key_window=false`，前台 PID 635 是 `loginwindow`，`CGSessionCopyCurrentDictionary` 的 `CGSSessionScreenIsLocked=true`。证据见 `.build/rust-platform-acceptance-agent.log`。这说明当前会话锁屏，不能把该完整运行报告为粘贴或 AX 跨焦点验收通过。`Platform::activation_status()` 可重新读取这一状态；解锁后重跑相同 harness，保留三项所有权检查。GPUI 的 app 回调由 `applicationDidFinishLaunching` 发出，不能仅凭锁屏时无法激活而改写应用生命周期。

全局 Fn/Globe 的真实物理按键、系统动作抑制/恢复、权限撤销与 sleep/wake 恢复，以及官方包内 Sparkle 自动检查/稳定、alpha、beta feed 和通知展示，仍需对应环境的原生验证。隔离 harness 不抢占正在使用的全局快捷键，也不替代发行包的签名、权限身份和升级验收。

## 退出保存门

当前 `gpui-pre-macos 0.3.7` 只有 `applicationWillTerminate` 回调；GPUI 的 `on_app_quit` future 最长等待 200 ms，且此时已经不能取消退出。`termination.rs` 的 `TerminationGate` 独立于 `Platform` 由 host 持有，安装在 GPUI `applicationDidFinishLaunching` 后；系统退出发出 `platform_quit`，原 delegate 的 `applicationShouldTerminate:` 返回 `NSTerminateLater`。host 先关闭原生资源、交接已接受的保存请求，异步等待 `BackendDriver::finished()`，最后调用 `finish()`。菜单、自动化退出使用相同保存流程；`on_app_quit` 只做最终幂等清理。

只添加 delegate 门仍会阻塞实际退出：GPUI 从 GCD main callback 调用 `NSApplication.terminate:`，AppKit 的 `NSTerminateLater` 嵌套循环占住此 callback，GPUI foreground task 无法继续。实现因此给本进程现有 GPUI application 单实例添加无 ivar 子类，把 `terminate:` 请求投递到 Cocoa common-mode 单次 timer，再从原 superclass 执行 AppKit 退出。原 GCD callback 已先返回，等待期间 GPUI executor 可继续运行。最终 `finish()` 再通过 `NSOperationQueue.mainQueue` 延后 `replyToApplicationShouldTerminate:YES` 或 `terminate:`，避免在 GPUI App 借用期间同步重入 shutdown。

两个实例保持原对象、所有 ivar 与原 GPUI/KVO 方法 IMP：delegate 继承原 `GPUIApplicationDelegate`，application 继承安装时的真实 class（本机为 `NSKVONotifying_GPUIApplication`），保留 KVO 行为，校验其 `GPUIApplication` 祖先。不替换 delegate，不改原 class 的方法，也不进行全局 swizzle。`define_class!` 需要静态 Rust `ClassType`，无法直接继承 GPUI 在运行时注册的私有 class；此处用 `objc2::runtime::ClassBuilder` 注册严格同布局、无新增 ivar 的子类。未来 GPUI 提供自己的 ShouldTerminate hook 时安装会明确失败，要求重新审阅接线。

`finish()` 幂等；延后回调检查安装代次，旧 owner 的 timer/operation 不会在新 gate 安装后执行。`Drop` 恢复两个实例原 isa，并对尚未完成的 native quit 回复 NO。原生请求在 timer 执行前即设置 CommandSink 的独立 quit 原子标记；UI 队列满也不能丢失退出或继续回填。

专用验收源为 `rust/crates/desktop/src/platform/tests/termination_harness.rs`，使用真实 GPUI/AppKit、独立临时 backend 目录和 650 ms 的模拟持久化延迟；同时保存真实 backend 配置并读回。它检查等待超过 500 ms 时 app 仍运行、Cocoa timer 与 GPUI foreground task 均持续回调、driver 实际完成后才退出，以及从 GPUI `AsyncApp.update` 内重复调用 `finish()` 不造成重入。另检查原 delegate/application 所有原 selector 的 IMP、平台 ivar 和 inactive `Drop` 的 isa 恢复。无窗口、全局 hotkey、应用激活、剪贴板、麦克风或用户目录访问。

```sh
cd rust
cargo +1.98.1 run --locked -p vocal-more-desktop --bin vocal-more-native-termination-acceptance
cargo +1.98.1 run --locked -p vocal-more-desktop --bin vocal-more-native-termination-acceptance -- --host-quit
```

2026-10-04 两条路径在本机均通过：native `NSTerminateLater` 和 host `finish` 都保持约 700 ms 才退出，等待期间各有至少 27 次 GPUI 与 Cocoa 回调，原 GPUI `applicationWillTerminate` 最终收到通知。日志分别为 `.build/rust-native-termination-acceptance.log` 与 `.build/rust-native-termination-host-acceptance.log`。该验收验证退出门及真实 driver 收尾；650 ms 来自独立测试保存 worker，不代表业务存储通常耗时，也不替代实际发布 `.app` 的完整 host 退出验收。

随后主代理独立重跑两条原生路径，并验证完整本地 `.app` 连续三次正常启动、退出（含两次设置窗口），均为 exit 0；启动一秒时实际加载原生音频和 Sparkle，不含 Python/WebKit 或子进程，见 `.build/rust-app-runtime/final-report.json`。此包为 ad-hoc 签名的本地验证包，尚未进行正式 Developer ID、公证或升级验收。

退出保存还保留固定大小的 `ShutdownFailures` 摘要，只有失败请求数及 cleanup flag；后台先发布摘要再设置 `finished`，主线程无锁读取，不保留 key、prompt、请求参数或错误原文。关闭前已成功投递但尚未显示的 durable `rpc_error` 由 Host 汇总，包括 closing 状态下的事件以及 finished 后的最后一次 drain；投递失败的错误由 driver 汇总，两者不重复。失败后继续处理其余已接受保存，最终沿用现有四秒胶囊提示再退出，不增加 modal 对话框。

真实配置目录故障测试覆盖正在执行的请求、后台队列及 UI 交接；四个写失败被精确汇总，后续三个词典保存仍落盘。完整 `.app` 的配置读取故障路径亦通过，约 4.8 秒后 exit 0，走完四秒失败提示，无重入 panic，见 `.build/rust-app-runtime/final-fatal-report.json`。锁屏下的此检查证明生命周期与提示调用路径，不能替代解锁后用户实际看到通知的交互验收。
