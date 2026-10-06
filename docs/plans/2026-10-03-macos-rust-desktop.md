# macOS Rust 界面迁移

目标：完成 macOS 桌面主壳的 Rust 迁移。胶囊视觉、动画、窗口行为和交互与 `b537df6` 完全等价；设置页使用 GPUI Kit，保留全部配置项及其操作能力。

## 实现边界

- GPUI Kit 锁定 `0.7.0`（官方 tag commit `0c830f4d257e69fdd17200650533ab4ca9a40cc0`）。
- Rust 原生 AppKit `NSPanel` / Core Animation 胶囊，逐项移植现有 renderer，不改变视觉基线。
- 复用 `vocal-more-backend::Application` 的业务与有界命令/事件边界；UI 主线程不等待网络、磁盘或原生音频调用。
- 保留已验证的 Objective-C++ 音频 C ABI 与低声 DSP。
- macOS 正式包由 Rust 可执行文件启动，消除 Python/PyObjC 与 WebView 的产品运行依赖。Windows 路径在本次目标之外。
- 沿用 bundle ID、用户数据路径、密钥脱敏、取消代次、粘贴令牌、Sparkle 通道与现有签名发布流程。

## 已取得的当前证据

- [x] 原生胶囊：60 张 PNG 与 0.5.1 基准逐像素一致，24 个布局组合、按钮派发、窗口不抢焦点和减少动态效果检查通过。[胶囊证据](../capsule-rust-parity.md)
- [x] 设置：7 页及首次设置、旧 FormState 全部字段/组合操作保留；主代理独立重跑后，112 张真实 Metal 截图与 500 个 UI/后端请求通过，包括深浅主题、中英、640×480 窄窗口、Key、滑块、提示词、词典、历史撤销、合成 PCM 校准和保存失败回读。35 个独立字段和 7 个组合入口均通过实际控件操作及磁盘回读；20 个字段通过滚动可达，另验证 14 个依赖条件。设备仅验证系统默认确认，尚未验证物理设备切换。[设置证据](../macos-rust-settings-parity.md)
- [x] 平台局部实测：菜单/AX worker 生命周期、主屏 JPEG、两种原生播放器自然结束通过；13 项平台逻辑测试通过。[平台证据](../macos-rust-platform-parity.md)
- [x] 保存和取消桥接：真实 PCM/本地 HTTP 验证原会话 epoch，1088 项满队列保存最终落盘；主线程交接约 0.4 ms，后台保存约 14 秒，未将此耗时当作主线程工作。
- [x] 构建入口：Rust `.app` 已生成，版本、arm64、macOS 14、原生 ABI、GPL/第三方声明、无 Python/WebView 载荷和签名读回通过；Codex Run 指向 `script/build_and_run.sh`。
- [x] 自动检查：Python 全量 1083 项、Rust workspace 131 项、打包测试 39 项通过；桌面 Rust 1.98.1 与核心 Rust 1.90.0 的 Clippy、格式检查通过。CI 文件已更新并通过 YAML 解析，未触发外部 workflow。

- [x] 完整 `.app`：三次真实启动/退出全部 exit 0，含两次设置窗口；启动一秒时的加载文件与进程读回包含原生音频和 Sparkle，不含 Python/WebKit 或子进程。实际主程序另导出 60 张胶囊 PNG，与基准逐像素一致。
- [x] 退出失败反馈：后台队列、UI 交接和正在执行的请求遭遇真实配置写盘失败时保留纯数量摘要，后续词典保存仍落盘；完整 `.app` 的配置读取故障验证四秒失败提示与正常退出（约 4.8 秒）。正常退出不显示失败提示。

以上证据分别证明其覆盖范围。旧签名/退出崩溃报告保留为诊断记录，不计为通过；当前结果见 `.build/rust-app-runtime/final-report.json`、`final-fatal-report.json` 和 `.build/capsule-release-final-comparison.json`。本地签名为 ad-hoc，尚未运行正式候选的 Developer ID、公证和升级验收。

## 已落地的实现

- [x] 原生胶囊、7 页设置与首次引导、全部配置项及模型/设备/密钥/词典/历史操作。
- [x] macOS 菜单、热键、粘贴与剪贴板、AX、屏幕、权限、播放、Sparkle 和通知适配。
- [x] 有界命令与事件、配置回读、代次隔离、独立屏幕 worker、取消与退出前保存。
- [x] Cargo 构建、Codex Run、本地 Rust `.app` 与版本/通道/许可证兼容。

## 仍需当前环境的交互验收

- [ ] 物理 Fn/Globe、自定义键、系统动作抑制与恢复、权限撤销及睡眠唤醒。
- [ ] 完整原生粘贴 harness：实际注入、AX 跨焦点观察、剪贴板恢复及取消后不回填。
- [ ] 胶囊在 Spaces、全屏和多显示器切换时的位置、层级与不抢焦点行为。
- [ ] 真实中文 IME、键盘导航及 VoiceOver。
- [ ] 真实麦克风、低声采集、设备切换和屏幕上下文权限路径。

本机 `CGSSessionScreenIsLocked=true`、`kCGSessionOnConsoleKey=false`；连续三轮目标执行都确认会话锁定，当前锁屏阻止焦点、输入与权限交互。离线检查已完成，继续上述交互验收需要解锁并保持登录桌面。各项原生 harness 保留所有权检查，解锁后继续相同验收。正式 Developer ID、公证及升级流程在后续发布候选中验收，本次未触发发布或构建本地 DMG。

只有所有必要项取得当前证据，且胶囊等价与配置项完整性均被证明，才标记目标完成。
