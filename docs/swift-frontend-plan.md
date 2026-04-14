# Vocal-More Swift 前端实现计划

## Context

当前 vocal-more 是一个纯 Python macOS 菜单栏应用（基于 rumps），需要为其添加原生 Swift/SwiftUI 前端。Python 保留为后端服务处理 ASR、文本润色等核心逻辑，Swift 负责所有 UI 和快捷键拦截，两者通过 JSON-RPC over stdin/stdout 通信。

### 迁移动机

- **rumps 限制**：无法自定义通知图标样式、窗口样式受限、不支持现代 SwiftUI 特性
- **原生体验**：原生 Swift 应用可以利用 Template Image 适配暗色模式、使用 SwiftUI Settings 面板、UNUserNotificationCenter 富通知
- **性能与稳定性**：消除 PyObjC 层 CGEventTap 的不稳定性，Swift 原生调用更可靠
- **可维护性**：UI 逻辑与业务逻辑分离，Python 端只保留 ASR/LLM/音频核心

### 现有 Python 模块保留情况

| 模块 | 迁移后 | 说明 |
|------|--------|------|
| `app.py` | **废弃** | 被 Swift 前端完全替代 |
| `core/hotkey_manager.py` | **废弃** | 移植到 Swift `HotkeyManager.swift` |
| `core/audio_recorder.py` | **保留** | Python 后端继续负责音频采集 |
| `core/asr_engine.py` | **保留** | DashScope ASR 调用 |
| `core/text_polisher.py` | **保留** | DashScope LLM 润色 |
| `core/keyboard_sim.py` | **保留** | pynput + pyperclip 模拟粘贴 |
| `config.py` | **保留** | YAML 配置读写 |
| `dictionary.py` | **保留** | 词典管理 |
| `modes/*.py` | **保留** | 状态机和录音流程控制 |

## 架构概览

```
┌──────────────────────────────────────┐      stdin/stdout       ┌───────────────────────────────┐
│          Swift 前端 (.app)            │   ← JSON-RPC 2.0 →    │       Python 后端进程           │
│          macOS 14+ / Swift 5.9+      │   (NDJSON framing)     │       Python 3.10+             │
│                                      │                        │                               │
│  • NSStatusItem 菜单栏               │   ──── 请求 ────→      │  • AudioRecorder (sounddevice) │
│  • SwiftUI Settings 面板             │                        │  • BatchASREngine (DashScope)  │
│  • CGEventTap 全局快捷键             │   ←─── 响应 ────       │  • TextPolisher (Qwen LLM)     │
│  • UNUserNotificationCenter 通知     │   ←─── 通知 ────       │  • KeyboardSimulator (pynput)  │
│  • Process 进程管理                   │                        │  • Config / Dictionary I/O     │
│  • @Observable 状态管理               │   stderr ────→ 日志    │  • WalkieTalkieMode            │
│                                      │                        │  • RealtimeLongMode            │
└──────────────────────────────────────┘                        └───────────────────────────────┘
```

## 项目结构

```
vocal-more/
├── VocalMore/                              # Swift Xcode 项目
│   ├── VocalMore.xcodeproj/
│   └── VocalMore/
│       ├── VocalMoreApp.swift              # @main 入口
│       ├── AppDelegate.swift               # NSApplicationDelegate: 启动 Python、初始化各管理器
│       │
│       ├── MenuBar/
│       │   └── MenuBarController.swift     # NSStatusItem + NSMenu 动态菜单
│       │
│       ├── Hotkey/
│       │   └── HotkeyManager.swift         # CGEventTap 全局快捷键（从 Python 移植）
│       │
│       ├── Backend/
│       │   ├── PythonBackend.swift          # Process 生命周期 + 重启 + stdin/stdout 管道
│       │   ├── JSONRPCClient.swift          # JSON-RPC 2.0 编解码 + 请求/响应匹配
│       │   └── BackendMessages.swift        # Codable 请求/响应/通知消息类型
│       │
│       ├── Models/
│       │   └── AppState.swift              # @Observable @MainActor 全局状态
│       │
│       ├── Views/
│       │   ├── SettingsView.swift           # Settings 主窗口 (TabView)
│       │   ├── GeneralSettingsTab.swift     # 通用设置：模式、润色、自动粘贴
│       │   ├── AudioSettingsTab.swift       # 音频：输入设备选择
│       │   ├── HotkeySettingsTab.swift      # 快捷键：多选开关
│       │   ├── DictionarySettingsTab.swift  # 词典管理：列表 + 增删
│       │   └── AddTermSheet.swift           # 添加词条 Sheet
│       │
│       ├── Notification/
│       │   └── NotificationManager.swift   # UNUserNotificationCenter 封装
│       │
│       ├── Assets.xcassets/
│       │   ├── AppIcon.appiconset/         # 应用图标 (logo.png 多尺寸)
│       │   ├── StatusIdle.imageset/        # 菜单栏图标 - 空闲 (template)
│       │   ├── StatusRecording.imageset/   # 菜单栏图标 - 录音 (template)
│       │   ├── StatusProcessing.imageset/  # 菜单栏图标 - 处理 (template)
│       │   └── NotificationIcon.imageset/  # 通知图标 (logo.png)
│       │
│       ├── Info.plist
│       └── VocalMore.entitlements
│
├── src/vocal_more/
│   ├── serve.py                            # 新增：JSON-RPC stdin/stdout 服务入口
│   ├── rpc_handler.py                      # 新增：请求分发器
│   └── ... (现有代码不变)
│
└── pyproject.toml                          # 新增 vocal-more-serve 入口
```

## IPC 协议 (JSON-RPC 2.0 over NDJSON)

### 传输层规范

- **帧格式**: NDJSON (Newline-Delimited JSON) — 每条消息是一行紧凑 JSON，以 `\n` 结尾
- **编码**: UTF-8
- **消息内不能包含字面换行符** — JSON 字符串中的换行必须转义为 `\n`
- **选择 NDJSON 而非 LSP 的 Content-Length 帧**：
  - 实现更简单（`readline()` 即可）
  - 与 MCP (Model Context Protocol) 生态一致
  - 语音转文本场景消息体小，无需支持 pretty-print
  - 调试方便（每行即一条完整消息）

### JSON-RPC 2.0 消息格式

**请求** (有 id，期望响应):
```json
{"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}
```

**通知** (无 id，不期望响应):
```json
{"jsonrpc": "2.0", "method": "state_changed", "params": {"state": "recording"}}
```

**成功响应**:
```json
{"jsonrpc": "2.0", "result": {"version": "0.1.0"}, "id": 1}
```

**错误响应**:
```json
{"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": 1}
```

### 错误码定义

| 错误码 | 名称 | 说明 |
|--------|------|------|
| `-32700` | Parse error | 无法解析 JSON |
| `-32600` | Invalid Request | 非法 JSON-RPC 请求格式 |
| `-32601` | Method not found | 方法不存在 |
| `-32602` | Invalid params | 参数错误 |
| `-32603` | Internal error | 服务端内部错误 |
| `-32000` | Transcription failed | ASR 转写失败 |
| `-32001` | Polish failed | 文本润色失败 |
| `-32002` | Device error | 音频设备错误 |
| `-32003` | Config error | 配置读写错误 |
| `-32004` | API key missing | DashScope API Key 未设置 |

### 请求方法 (Swift → Python)

| Method | Params | Result | 说明 |
|--------|--------|--------|------|
| `initialize` | `{}` | `{version, state, config}` | 握手，返回版本、当前状态、完整配置 |
| `get_config` | `{}` | `{config}` | 获取完整配置对象 |
| `set_config` | `{key, value}` | `{ok: true}` | 设置单项配置并持久化 |
| `list_devices` | `{}` | `{devices: [{index, name, is_default}]}` | 列出音频输入设备 |
| `set_device` | `{device: string\|null}` | `{ok: true}` | 设置输入设备 (null=系统默认) |
| `set_mode` | `{mode: string}` | `{ok: true}` | 切换 `walkie_talkie` / `realtime_long` |
| `hotkey_pressed` | `{}` | `{ok: true}` | 通知后端快捷键按下 |
| `hotkey_released` | `{}` | `{ok: true}` | 通知后端快捷键释放 |
| `get_dictionary` | `{}` | `{entries: [{term, aliases}]}` | 获取全部词条 |
| `add_dict_entry` | `{term, aliases?}` | `{ok: true}` | 添加词条（term 重复则合并 aliases） |
| `remove_dict_entry` | `{term}` | `{ok: true}` | 删除词条 |
| `set_active_hotkeys` | `{hotkeys: [string]}` | `{ok: true}` | 更新活跃快捷键列表 |
| `shutdown` | `{}` | `{ok: true}` | 优雅关闭后端进程 |

### 通知方法 (Python → Swift)

| Method | Params | 说明 |
|--------|--------|------|
| `state_changed` | `{state: "idle"\|"recording"\|"processing"}` | 模式状态变更 |
| `partial_result` | `{text: string}` | 原始 ASR 中间文本 |
| `final_result` | `{text: string}` | 最终文本（润色后），已自动粘贴 |
| `error` | `{message: string}` | 错误通知 |

### 请求/响应时序图

```
Swift                                   Python
  │                                       │
  │──── initialize ────────────────────→  │  (握手)
  │  ←── result: {version, state} ─────  │
  │                                       │
  │──── hotkey_pressed ────────────────→  │  (用户按下 Fn)
  │  ←── result: {ok} ────────────────   │
  │  ←── state_changed: recording ─────  │  (通知)
  │                                       │
  │  ... 录音中 ...                        │
  │                                       │
  │──── hotkey_released ───────────────→  │  (用户松开 Fn)
  │  ←── result: {ok} ────────────────   │
  │  ←── state_changed: processing ────  │  (通知)
  │                                       │
  │  ←── partial_result: "原始文本" ────  │  (ASR 完成)
  │  ←── final_result: "润色后文本" ────  │  (润色+粘贴完成)
  │  ←── state_changed: idle ──────────  │  (回到空闲)
  │                                       │
```

## 技术方案详解

### 1. Python 后端 stdout 缓冲处理

**问题**：Python stdout 连接管道时默认块缓冲（4-8KB），JSON-RPC 响应会被缓冲而无法及时送达 Swift。

**解决方案** — 三重保险：

```python
# serve.py 启动时

# 1. 保存真正的 stdout 用于 JSON-RPC
_jsonrpc_stdout = sys.stdout

# 2. 将 sys.stdout 重定向到 stderr（防止 print/log 污染 JSON-RPC 通道）
sys.stdout = sys.stderr

# 3. 对 JSON-RPC stdout 设置行缓冲
_jsonrpc_stdout = open(_jsonrpc_stdout.fileno(), 'w', buffering=1, closefd=False)

# 4. Swift 侧启动 Python 时传 -u 标志 + 设置 PYTHONUNBUFFERED=1
```

**发送消息时始终 flush**：
```python
def _send(msg: dict):
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    _jsonrpc_stdout.write(line)
    _jsonrpc_stdout.flush()  # 即使设了行缓冲也显式 flush
```

### 2. Swift 进程管理 (PythonBackend.swift)

**启动 Python 进程**：
```swift
let process = Process()
process.executableURL = URL(fileURLWithPath: pythonPath)  // .venv/bin/python
process.arguments = ["-u", "-m", "vocal_more.serve"]
process.environment = [
    "PYTHONUNBUFFERED": "1",
    "DASHSCOPE_API_KEY": apiKey  // 从 Keychain 或配置读取
]

let stdinPipe = Pipe()
let stdoutPipe = Pipe()
let stderrPipe = Pipe()

process.standardInput = stdinPipe
process.standardOutput = stdoutPipe
process.standardError = stderrPipe
```

**行缓冲读取 — 处理 stdout 分片**：

管道读取可能返回部分行或多行合并，必须自行处理缓冲拆分：

```swift
var buffer = Data()

stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
    let data = handle.availableData
    if data.isEmpty {
        // EOF: Python 进程已退出
        handle.readabilityHandler = nil
        self.handleProcessExit()
        return
    }
    buffer.append(data)

    // 按换行符拆分完整行
    while let newlineRange = buffer.range(of: Data("\n".utf8)) {
        let lineData = buffer.subdata(in: buffer.startIndex..<newlineRange.lowerBound)
        buffer.removeSubrange(buffer.startIndex...newlineRange.lowerBound)
        if let line = String(data: lineData, encoding: .utf8) {
            self.handleIncomingMessage(line)
        }
    }
}
```

**崩溃自动重启 — 指数退避**：

```swift
process.terminationHandler = { [weak self] proc in
    let reason = proc.terminationReason  // .exit 或 .uncaughtSignal
    let status = proc.terminationStatus

    DispatchQueue.main.async {
        if reason == .uncaughtSignal || status != 0 {
            // 非正常退出 → 带退避重启
            self?.scheduleRestart()
        }
    }
}

// 退避策略: 1s, 2s, 4s, 8s, 最大 30s
private var restartCount = 0
private func scheduleRestart() {
    let delay = min(pow(2.0, Double(restartCount)), 30.0)
    restartCount += 1
    DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
        self.startProcess()
    }
}
// 成功 initialize 后 restartCount = 0
```

**注意事项**：
- `Process` 实例不可复用，每次重启必须创建新实例
- 优雅关闭：先发 `shutdown` 请求 → 等待响应 → 关闭 stdin → 超时后 `terminate()` (SIGTERM) → 再超时 `interrupt()` (SIGINT)

### 3. JSON-RPC 客户端 (JSONRPCClient.swift)

**使用 Swift actor 保证线程安全**：

```swift
actor JSONRPCClient {
    private var nextId: Int = 0
    private var pending: [Int: CheckedContinuation<JSONRPCResponse, Error>] = [:]

    func sendRequest<P: Codable>(method: String, params: P) async throws -> JSONRPCResponse {
        let id = nextId
        nextId += 1

        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            // 编码并写入 stdin 管道
            writeToStdin(request)
        }
    }

    func handleResponse(_ msg: JSONRPCMessage) {
        if let id = msg.id, let continuation = pending.removeValue(forKey: id) {
            if let error = msg.error {
                continuation.resume(throwing: RPCError.server(error))
            } else {
                continuation.resume(returning: msg)
            }
        }
        // 无 id → 通知，dispatch 到 AppState
    }
}
```

**请求超时**：JSON-RPC 2.0 不定义超时，应用层自行实现。对于 `hotkey_pressed/released` 等低延迟请求设 5s 超时；对于 `initialize` 设 10s 超时。

### 4. CGEventTap 快捷键管理 (HotkeyManager.swift)

从 Python `hotkey_manager.py` 移植，关键数据和算法需完全保留。

**键码与标志位映射表**（从 Python KEY_REGISTRY 移植）：

```swift
struct HotkeyDef {
    let keyCode: Int64
    let isModifier: Bool    // true=通过 flagsChanged 检测, false=通过 keyDown/keyUp
    let flagMask: UInt64    // 仅 isModifier=true 时使用
}

let keyRegistry: [String: HotkeyDef] = [
    "fn":  HotkeyDef(keyCode: 63,  isModifier: true,  flagMask: 0x80_0000),  // NX_SECONDARYFNMASK
    "f13": HotkeyDef(keyCode: 105, isModifier: false, flagMask: 0),
    "f14": HotkeyDef(keyCode: 107, isModifier: false, flagMask: 0),
    "f15": HotkeyDef(keyCode: 113, isModifier: false, flagMask: 0),
    "f16": HotkeyDef(keyCode: 106, isModifier: false, flagMask: 0),
    "f17": HotkeyDef(keyCode: 64,  isModifier: false, flagMask: 0),
    "f18": HotkeyDef(keyCode: 79,  isModifier: false, flagMask: 0),
    "f19": HotkeyDef(keyCode: 80,  isModifier: false, flagMask: 0),
    "f20": HotkeyDef(keyCode: 90,  isModifier: false, flagMask: 0),
]

// Double-Cmd 检测
let cmdLeftKeyCode: Int64 = 55
let cmdRightKeyCode: Int64 = 54
let cmdMask: UInt64 = 0x10_0000  // NX_COMMANDMASK
```

**CGEventTap 创建**：

```swift
let eventMask: CGEventMask = (1 << CGEventType.flagsChanged.rawValue)
    | (1 << CGEventType.keyDown.rawValue)
    | (1 << CGEventType.keyUp.rawValue)

guard let tap = CGEvent.tapCreate(
    tap: .cgSessionEventTap,
    place: .headInsertEventTap,
    options: .defaultTap,           // 可以消费事件
    eventsOfInterest: eventMask,
    callback: hotkeyCallback,       // @convention(c) 函数
    userInfo: Unmanaged.passUnretained(self).toOpaque()
) else {
    // Accessibility 权限不足
    return false
}
```

**回调函数**（必须是 `@convention(c)` 全局函数/静态方法）：

```swift
private let hotkeyCallback: CGEventTapCallBack = {
    proxy, type, event, refcon -> Unmanaged<CGEvent>? in

    guard let refcon = refcon else { return Unmanaged.passUnretained(event) }
    let manager = Unmanaged<HotkeyManager>.fromOpaque(refcon).takeUnretainedValue()

    // 系统可能因回调过慢而禁用 tap，需重新启用
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let tap = manager.eventTap {
            CGEvent.tapEnable(tap: tap, enable: true)
        }
        return Unmanaged.passUnretained(event)
    }

    return manager.handleEvent(type: type, event: event)
}
```

**事件处理逻辑**（完全复刻 Python 版）：

| 按键类型 | 检测事件 | 状态追踪 | 消费事件 |
|---------|---------|---------|---------|
| Fn (modifier) | `flagsChanged` + `flags & 0x800000` | `keyStates[63]` bool | **是**（返回 nil） |
| F13-F20 (regular) | `keyDown` / `keyUp` | `heldKeys: Set<Int64>` 防重复 | **是**（返回 nil） |
| Cmd (double-tap) | `flagsChanged` keycode 55/54 | `lastCmdTime` + `cmdTapCount` | **否**（透传） |

**Double-Cmd 检测算法**（从 Python 精确移植）：
1. 监听 `flagsChanged` 事件，keycode 为 55 (左Cmd) 或 54 (右Cmd)
2. 在 Cmd **释放**时（`flags & cmdMask == 0`）触发计时
3. 如果距上次释放 < `doubleTapThreshold`(0.3s)：`tapCount += 1`
4. 如果 `tapCount >= 2`：触发 `onDoubleCmd()` 回调，重置计数
5. 否则：重置 `tapCount = 1`

**Accessibility 权限检查**：

```swift
import ApplicationServices

func checkAccessibility() -> Bool {
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    return AXIsProcessTrustedWithOptions(options)
}
```
- 传入 `prompt: true` 会弹出系统对话框引导用户到 系统设置 → 隐私与安全 → 辅助功能
- 授权后 app 需要重启才能成功创建 CGEventTap
- 可以轮询 `AXIsProcessTrusted()` 检测用户是否已授权

### 5. 状态管理 (AppState.swift)

使用 Swift 5.9+ 的 `@Observable` 宏（比 `ObservableObject` + `@Published` 更精细）：

```swift
@Observable @MainActor
class AppState {
    // 后端状态
    var backendConnected = false
    var modeState: ModeState = .idle         // idle / recording / processing

    // 配置（与 Python config.yaml 双向同步）
    var currentMode: String = "walkie_talkie" // walkie_talkie / realtime_long
    var enablePolish: Bool = true
    var autoPaste: Bool = true
    var inputDevice: String? = nil
    var activeHotkeys: [String] = ["fn", "double_cmd"]

    // 词典
    var dictionaryEntries: [DictEntry] = []

    // 设备列表
    var availableDevices: [AudioDevice] = []

    // 最近结果（用于通知显示）
    var lastResult: String? = nil
    var lastError: String? = nil
}
```

**SwiftUI 中使用**：
- 注入：`.environment(appState)`
- 消费：`@Environment(AppState.self) private var appState`
- 绑定：`@Bindable var appState: AppState` → `$appState.enablePolish`

### 6. 菜单栏 (MenuBarController.swift)

使用 `NSStatusItem` + `NSMenu`（非 SwiftUI `MenuBarExtra`），原因：
- 需要动态重建子菜单（设备列表、词典列表）
- 需要 NSMenuItem 的 state 属性（勾选标记）
- 需要根据后端状态更新图标

**菜单结构**（完全复刻 Python `app.py`）：

```
[StatusIdle/Recording/Processing template icon]
Status: Idle / Recording... / Processing...
────────────────────────────────────
Mode ▶
    ✓ Walkie-Talkie (Hold Fn)
      Real-time Long (Toggle Fn)
Settings ▶
    ✓ Enable Text Polish
    ✓ Auto Paste
    ────────────────────────────────
    Input Device ▶
        ✓ System Default
        ────────────────────────────
        MacBook Pro Microphone (default)
        External USB Mic
        ────────────────────────────
        Refresh Devices
    Hotkey ▶
        ✓ Fn Key (Hold / Toggle)
        ✓ Double Cmd
          F13 (PrintScreen)
          F14 ... F20
    ────────────────────────────────
    Open Config File
Dictionary ▶
    Add Term...
    ────────────────────────────────
    Claude  (可劳德, 克劳德)
    API
    ────────────────────────────────
    Open Dictionary File
────────────────────────────────────
Settings...                           ⌘,    ← 打开 SwiftUI Settings 面板
Quit                                  ⌘Q
```

**Template Image 适配暗色模式**：

```swift
if let image = NSImage(named: "StatusIdle") {
    image.isTemplate = true   // 关键！系统自动适配亮/暗模式
    statusItem.button?.image = image
}
```

图标要求：单色 + 透明背景 PNG。系统会自动在暗色模式下反色。

### 7. SwiftUI Settings 面板

使用 SwiftUI `Settings` scene，自动集成到应用菜单的 "Settings..." 项：

```swift
@main
struct VocalMoreApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @State private var appState = AppState()

    var body: some Scene {
        Settings {
            SettingsView()
                .environment(appState)
        }
    }
}
```

**四个 Tab 页**：

| Tab | 内容 | 对应 RPC 方法 |
|-----|------|--------------|
| General | 默认模式切换、润色开关、自动粘贴开关 | `set_config` |
| Audio | 输入设备下拉选择、刷新设备 | `list_devices`, `set_device` |
| Hotkeys | 快捷键多选列表 | `set_active_hotkeys` |
| Dictionary | 词条列表、添加 Sheet、滑动删除 | `get_dictionary`, `add_dict_entry`, `remove_dict_entry` |

**打开 Settings 的方式**：
```swift
// 菜单项 action
@Environment(\.openSettings) private var openSettings
// 或 AppKit 方式
NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
```

### 8. 系统通知 (NotificationManager.swift)

使用 `UNUserNotificationCenter`：

```swift
import UserNotifications

class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()

    func setup() {
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func send(title: String, subtitle: String, body: String, iconPath: String? = nil) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.subtitle = subtitle
        content.body = body
        content.sound = .default

        // 附加 logo 作为通知图标
        if let path = iconPath,
           let attachment = try? UNNotificationAttachment(
               identifier: "icon",
               url: URL(fileURLWithPath: path)
           ) {
            content.attachments = [attachment]
        }

        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil  // 立即发送
        )
        UNUserNotificationCenter.current().add(request)
    }

    // 前台也显示通知
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        return [.banner, .sound]
    }
}
```

**注意**：LSUIElement 应用的通知权限可能需要用户在 系统设置 → 通知 中手动开启。

### 9. Info.plist 配置

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
    <!-- 隐藏 Dock 图标，仅菜单栏显示 -->
    <key>LSUIElement</key>
    <true/>

    <!-- 麦克风权限描述（虽然 Python 录音，但 App 进程也需要声明） -->
    <key>NSMicrophoneUsageDescription</key>
    <string>Vocal-More needs microphone access to record your voice for transcription.</string>

    <!-- 最低系统版本 -->
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
</dict>
</plist>
```

### 10. Entitlements 配置

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
    <!-- 禁用沙盒（CGEventTap + 子进程 + ~/.vocal-more/ 访问） -->
    <key>com.apple.security.app-sandbox</key>
    <false/>
</dict>
</plist>
```

**不能启用沙盒的原因**：
1. CGEventTap 需要辅助功能权限，沙盒内无法使用
2. 需要 spawn Python 子进程
3. 需要读写 `~/.vocal-more/` 配置目录
4. 需要访问系统 Python / .venv

## 分阶段实施

### Phase 1: Python JSON-RPC 服务端

**目标**：Python 后端能脱离 rumps 独立运行，通过 stdin/stdout 接收 JSON-RPC 命令。

**新建文件**：

**`src/vocal_more/serve.py`** — 服务入口：
```python
"""JSON-RPC stdio server for Vocal-More backend."""
import sys
import json
import logging
import threading

# 关键：保存真正的 stdout，将 sys.stdout 重定向到 stderr
_jsonrpc_stdout = sys.stdout
sys.stdout = sys.stderr

# 设置行缓冲
_jsonrpc_stdout = open(_jsonrpc_stdout.fileno(), 'w', buffering=1, closefd=False)

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

from .rpc_handler import RPCHandler

def main():
    handler = RPCHandler()

    # stdin 读取循环
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})
            continue

        response = handler.dispatch(request)
        if response is not None:  # 通知不需要响应
            _send(response)

    # stdin EOF → 优雅退出
    logger.info("stdin EOF, shutting down")
    handler.shutdown()
    sys.exit(0)

def _send(msg: dict):
    """向 JSON-RPC 通道写入一条消息。"""
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    _jsonrpc_stdout.write(line)
    _jsonrpc_stdout.flush()

# 暴露给 RPCHandler 的通知发送函数
def send_notification(method: str, params: dict):
    """发送 JSON-RPC 通知（无 id）。线程安全。"""
    _send({"jsonrpc": "2.0", "method": method, "params": params})
```

**`src/vocal_more/rpc_handler.py`** — 请求分发器：
```python
"""JSON-RPC request dispatcher."""
import threading
from typing import Optional
from .config import get_config, reload_config
from .dictionary import get_dictionary, reload_dictionary
from .core.audio_recorder import AudioRecorder
from .core.text_polisher import TextPolisher
from .modes.base_mode import ModeState
from .modes.walkie_talkie import WalkieTalkieMode
from .modes.realtime_long import RealtimeLongMode

class RPCHandler:
    def __init__(self):
        self._lock = threading.Lock()  # 保护 _send 并发调用
        self.config = get_config()

        # 初始化组件（复用 app.py 的接线模式）
        self._text_polisher = TextPolisher() if self.config.api_key else None

        self._walkie_talkie = WalkieTalkieMode(
            on_state_change=self._on_state_change,
            on_result=self._on_result,
            on_partial_result=self._on_partial_result,
            on_error=self._on_error,
            text_polisher=self._text_polisher,
        )
        self._realtime_long = RealtimeLongMode(
            on_state_change=self._on_state_change,
            on_result=self._on_result,
            on_partial_result=self._on_partial_result,
            on_error=self._on_error,
            text_polisher=self._text_polisher,
        )
        self._current_mode = (
            self._realtime_long if self.config.default_mode == "realtime_long"
            else self._walkie_talkie
        )

    def dispatch(self, request: dict) -> Optional[dict]:
        """分发 JSON-RPC 请求，返回响应或 None（通知）。"""
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")  # None → 通知

        # 查找处理函数
        handler = getattr(self, f"_handle_{method}", None)
        if handler is None:
            if req_id is not None:
                return self._error_response(req_id, -32601, f"Method not found: {method}")
            return None

        try:
            result = handler(params)
            if req_id is not None:
                return {"jsonrpc": "2.0", "result": result, "id": req_id}
            return None
        except Exception as e:
            if req_id is not None:
                return self._error_response(req_id, -32603, str(e))
            return None

    # --- 各方法实现 ---
    def _handle_initialize(self, params):
        return {
            "version": "0.1.0",
            "state": self._current_mode.state.value,
            "config": self.config.to_dict(),
        }
    # ... (其他方法类似)

    # --- 回调 → 通知 ---
    def _on_state_change(self, state: ModeState):
        from .serve import send_notification
        send_notification("state_changed", {"state": state.value})

    def _on_result(self, text: str):
        from .serve import send_notification
        send_notification("final_result", {"text": text})

    def _on_partial_result(self, text: str):
        from .serve import send_notification
        send_notification("partial_result", {"text": text})

    def _on_error(self, error: str):
        from .serve import send_notification
        send_notification("error", {"message": error})
```

**修改 `pyproject.toml`**：
```toml
[project.scripts]
vocal-more = "vocal_more.app:main"
vocal-more-serve = "vocal_more.serve:main"   # 新增
```

**验证**：
```bash
# 发送 initialize 请求
echo '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}' | uv run vocal-more-serve

# 交互式测试
uv run vocal-more-serve
# 手动输入 JSON-RPC 请求，观察响应
```

---

### Phase 2: 最小 Swift 应用 — 进程管理 + 菜单栏图标

**目标**：Swift app 能 spawn Python 进程、发送 `initialize`、在菜单栏显示状态图标。

**实现清单**：

1. **创建 Xcode 项目**
   - macOS App 模板，SwiftUI lifecycle
   - 设置 Info.plist: `LSUIElement = YES`
   - 禁用沙盒 entitlements
   - Deployment target: macOS 14.0

2. **`PythonBackend.swift`**
   - 发现 Python 路径：`Bundle.main.bundlePath/../../../.venv/bin/python`（开发阶段）
   - spawn `Process`，连接 stdin/stdout/stderr `Pipe`
   - stdout `readabilityHandler` → 行缓冲解析 → `handleIncomingMessage()`
   - stderr `readabilityHandler` → 转发到 `os_log` / `Logger`
   - `terminationHandler` → 崩溃检测 + 指数退避重启

3. **`JSONRPCClient.swift`**
   - Codable 消息类型（Request, Response, Notification, Error, ID）
   - `sendRequest()` → `async throws`，使用 `CheckedContinuation` 等待响应
   - `handleIncomingMessage()` → 区分响应（有 id）和通知（无 id）
   - 请求超时机制

4. **`AppState.swift`**
   - `@Observable @MainActor class AppState`
   - 通知回调更新状态属性

5. **`MenuBarController.swift`**
   - `NSStatusItem` + 最小菜单（Status + Quit）
   - 导入 `resources/icons/` 到 xcassets（3 状态 × 1x/2x）
   - `isTemplate = true` 适配暗色模式
   - 状态变化时切换图标

**验证**：Xcode Run → 菜单栏出现图标 → Console 显示 `initialize` 成功响应 → Quit 正常退出 Python 进程。

---

### Phase 3: Swift 侧快捷键管理

**目标**：移植 Python CGEventTap 到 Swift，按键 → 通知 Python → 录音/处理/粘贴全流程。

**实现清单**：

1. **`HotkeyManager.swift`**
   - 完整移植 `KEY_REGISTRY` 映射表（见上文技术方案 §4）
   - `@convention(c)` 回调 + `Unmanaged<HotkeyManager>` refcon
   - Modifier 键 (Fn): `flagsChanged` + `flags & flagMask` 检测按下/释放
   - Regular 键 (F13-F20): `keyDown`/`keyUp` 检测，`heldKeys` 防重复
   - Double-Cmd: 释放时计时，0.3s 内两次 → 触发回调
   - 事件消费：Fn/F13-F20 消费（返回 nil），Cmd 透传

2. **Accessibility 权限处理**
   - 启动时 `AXIsProcessTrustedWithOptions` 检查
   - 未授权 → 弹出系统引导对话框 + 发送通知提醒
   - 可选：轮询 `AXIsProcessTrusted()` 检测授权状态变化

3. **回调接线**
   - `onFnPressed` → `backend.sendRequest("hotkey_pressed")`
   - `onFnReleased` → `backend.sendRequest("hotkey_released")`
   - `onDoubleCmd` → `backend.sendRequest("hotkey_pressed")` （与 Python 版一致）
   - 运行时更新：`setActiveHotkeys()` 重建查找表无需重启 tap

**验证**：按 Fn 键 → 菜单栏图标变为录音状态 → 松开 → 变为处理状态 → 文本粘贴到光标位置 → 回到空闲状态。

---

### Phase 4: 完整菜单栏

**目标**：1:1 复刻 `app.py` 的完整菜单结构。

**实现清单**：

1. **菜单结构** — 见上文技术方案 §6 的完整菜单树
2. **动态子菜单**
   - Mode: 单选切换（NSMenuItem.state = .on / .off）
   - Settings > Input Device: 通过 `list_devices` RPC 动态构建
   - Settings > Hotkey: 多选开关
   - Dictionary: 通过 `get_dictionary` RPC 动态构建
3. **NSMenu delegate** — 子菜单打开时刷新（设备列表、词典列表）
4. **Open Config File** — `NSWorkspace.shared.open(configURL)`
5. **Open Dictionary File** — `NSWorkspace.shared.open(dictURL)` + 关闭后 `reload_dictionary` RPC

**验证**：逐项对照 Python 版菜单功能，确保所有交互等效。

---

### Phase 5: SwiftUI Settings 面板 + 词典管理

**目标**：提供比菜单更友好的设置体验。

**实现清单**：

1. **SettingsView + 四个 Tab**（见上文技术方案 §7）
   - General: Picker (模式) + Toggle (润色/粘贴)
   - Audio: Picker (设备) + Refresh 按钮
   - Hotkeys: `ForEach` 多选列表，至少保留一个
   - Dictionary: `List` + `onDelete` + AddTermSheet

2. **AddTermSheet**
   - TextField: 术语
   - TextField: 别名（逗号分隔）
   - 验证：术语非空

3. **双向同步**
   - 设置变更 → `set_config` / `set_device` / `set_active_hotkeys` RPC → 更新 AppState
   - 打开 Settings 时 → `get_config` / `list_devices` / `get_dictionary` RPC 刷新

4. **Settings... 菜单项**
   - 菜单栏添加 "Settings... ⌘," 项
   - 点击 → `NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)`
   - 或 `openSettings()` environment action

**验证**：菜单和 Settings 面板的所有设置双向同步。

---

### Phase 6: 完善与容错

**目标**：生产级稳定性和用户体验。

**实现清单**：

1. **进程崩溃恢复**
   - 指数退避重启（1s, 2s, 4s, ... 30s）
   - 重启时拒绝所有 pending 请求
   - 成功 initialize 后重置退避计数
   - 连续崩溃 N 次后显示错误通知

2. **权限引导**
   - Accessibility: 启动时检查，未授权 → 通知 + 引导到系统设置
   - Microphone: Python 端 sounddevice 首次使用会触发系统授权弹窗
   - API Key: `initialize` 响应中检查，缺失 → 打开配置文件引导填写

3. **UNUserNotificationCenter 通知**
   - Transcription Complete: 显示截断文本（前50字符）
   - Error: 显示错误信息
   - Permissions Required: 辅助功能权限提醒
   - 通知附带 logo.png 作为图标

4. **菜单栏图标 Template Image**
   - 现有 PNG 转为单色 + 透明背景
   - xcassets 中勾选 "Render As: Template Image"
   - `image.isTemplate = true`

5. **日志系统**
   - Swift 侧：`os.Logger` 分类记录
   - Python stderr → Swift 捕获并转发到 `Logger`
   - 可选：写入 `~/Library/Logs/VocalMore/` 文件

6. **Python 路径发现策略**
   - 开发阶段：项目根目录 `.venv/bin/python`
   - 发布阶段：Bundle 内嵌 Python 或用户指定路径
   - 通过 `UserDefaults` 记住上次成功的路径

## Python 现有数据结构参考

### Config YAML Schema (`~/.vocal-more/config.yaml`)

```yaml
api_key: "sk-..."
audio:
  sample_rate: 16000
  channels: 1
  blocksize: 1600
  input_device: null           # null 或设备名称字符串
asr:
  model: "qwen3-asr-flash-realtime-2026-02-10"
llm:
  model: "qwen3.5-plus"
  temperature: 0.3
hotkey:
  primary_key: "fn"
  fallback_key: "double_cmd"
  double_tap_threshold: 0.3    # 秒
  active_hotkeys: ["fn", "double_cmd"]
enable_polish: true
auto_paste: true
default_mode: "walkie_talkie"  # 或 "realtime_long"
```

### Dictionary YAML Schema (`~/.vocal-more/dictionary.yaml`)

```yaml
entries:
  - term: "Claude"
    aliases: ["可劳德", "克劳德"]
  - term: "API"
    # 无 aliases 时该字段省略
```

### 状态机转换

```
IDLE ──[hotkey_pressed]──→ RECORDING ──[hotkey_released / 二次press]──→ PROCESSING ──[完成/错误]──→ IDLE
                               ↑                                              │
                               └──────── [录音 < 100ms (3200 bytes)] ────────→ IDLE (丢弃)
```

- **Walkie-Talkie**: press → 开始录音, release → 停止录音并处理
- **Real-time Long**: press → 开始录音, 再按 → 停止录音并处理, release → 无操作

### 音频格式

- PCM int16, 单声道, 16kHz, little-endian
- `sounddevice.InputStream` 采集 float32 → 乘 32767 → 转 int16 bytes
- WAV 容器：1ch, 16-bit, 16000Hz

## 关键注意事项

1. **stdout 缓冲**：Python 必须三重保险（`-u` 标志 + `PYTHONUNBUFFERED=1` + 显式 `flush()`）
2. **stdout 通道隔离**：Python 端 `sys.stdout` 重定向到 stderr，只有 JSON-RPC 消息走真正的 stdout fd
3. **线程安全**：Python 回调从 daemon 线程触发，`_send()` 需加锁；Swift 通知需 `@MainActor` dispatch
4. **沙盒禁用**：CGEventTap + 子进程 + `~/.vocal-more/` 文件访问均不兼容沙盒
5. **Python 路径发现**：开发阶段 `.venv/bin/python -m vocal_more.serve`
6. **孤儿进程防护**：`serve.py` 的 stdin `for line in sys.stdin` 在 EOF 后自然退出循环
7. **Process 不可复用**：Swift `Process` 终止后不能 rerun，必须创建新实例
8. **CGEventTap 自动禁用**：回调过慢时系统会禁用 tap，需在收到 `tapDisabledByTimeout` 时重新启用
9. **Accessibility 权限**：授权后需重启 app 才能成功创建 CGEventTap
10. **NDJSON 格式**：消息内禁止字面换行符，JSON 字符串中的换行必须转义

## 验证方式

### Phase 1 验证 — Python 后端独立测试

```bash
# 单次请求测试
echo '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}' | uv run vocal-more-serve

# 交互式测试
uv run vocal-more-serve
# 输入: {"jsonrpc":"2.0","method":"get_config","params":{},"id":2}
# 输入: {"jsonrpc":"2.0","method":"list_devices","params":{},"id":3}
# Ctrl+D 退出（stdin EOF）

# 自动化回归测试
python -m pytest tests/test_rpc_handler.py
```

### Phase 2-3 验证 — 核心功能集成测试

- [ ] Xcode Run → 菜单栏出现图标
- [ ] Console 显示 `initialize` 成功
- [ ] 按 Fn → 图标变为录音状态 → Console 显示 `state_changed: recording`
- [ ] 松开 Fn → 图标变为处理状态 → Console 显示 `state_changed: processing`
- [ ] 转写完成 → 文本粘贴到光标 → 通知显示 → 图标恢复空闲
- [ ] Double-Cmd → 触发 Real-time Long 模式（如已切换）
- [ ] Quit → Python 进程正常退出
- [ ] 强杀 Python 进程 → Swift 检测并自动重启

### Phase 4-6 验证 — 全功能测试矩阵

| 功能 | 测试点 |
|------|--------|
| 模式切换 | 菜单选择 → 状态保存 → 重启后恢复 |
| 润色开关 | 开启/关闭 → 转写结果对比 |
| 自动粘贴 | 开启/关闭 → 验证剪贴板行为 |
| 设备切换 | 选择设备 → 录音正常 → 设备拔出处理 |
| 快捷键管理 | 启用/禁用各快捷键 → 至少保留一个 |
| 词典管理 | 添加/删除词条 → 润色结果包含修正 |
| Settings 面板 | 每个 Tab 的设置双向同步 |
| 暗色模式 | 图标自适应 → 菜单样式正确 |
| 进程崩溃 | 强杀 → 自动重启 → 功能恢复 |
| 权限缺失 | 无辅助功能 → 提示 → 授权后恢复 |
| API Key 缺失 | 无 Key → 提示 → 配置后恢复 |

## 参考项目与资源

| 项目 | 相关度 | 参考点 |
|------|--------|--------|
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 高 | `mcp.server.stdio` — NDJSON stdio 服务端实现 |
| [MCP Swift SDK](https://github.com/modelcontextprotocol/swift-sdk) | 高 | `StdioTransport` — Swift 侧 stdio 客户端实现 |
| [SourceKit-LSP](https://github.com/apple/sourcekit-lsp) | 中 | `JSONRPCConnection` — Swift JSON-RPC over stdio |
| Apple DTS Thread 690310 | 中 | Dispatch I/O vs FileHandle 管道读取的最佳实践 |
