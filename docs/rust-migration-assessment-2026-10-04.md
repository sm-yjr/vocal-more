# Rust 桌面迁移必要性复核

日期：2026-10-04。远端 `main` 最新提交为 `b537df6a3bbaeba9ab06cf0cdc0e3dbbcbcaef60`，产品版本 `0.5.1`；已执行 `git fetch` 和 `git pull --ff-only`，本地提交与远端一致。工作区中的 Rust 桌面迁移仍是未提交的开发改动，未发布。

## 结论

建议继续完成 **Rust 业务核心 + GPUI Kit 设置界面 + 原生平台适配**。价值主要在于减少双语言桥接、统一状态与生命周期、简化运行依赖，并为 Windows 复用业务与界面创造条件。现有 Rust 后端已能承担产品业务；迁移桌面外壳对修复 ASR 协议或改善低声采集没有必然作用。

保留现有 Objective-C++ 音频 C ABI 与低声 DSP。Rust 全栈的边界是产品主程序、业务与界面，不要求把已验证的 CoreAudio、Voice Processing、转换和 vDSP 实现全部重写。

## 当前代码支持什么判断

| 边界 | `b537df6` 已交付的状态 | 继续迁移的收益与成本 |
| --- | --- | --- |
| 业务核心 | Rust `backend::Application` 已拥有 ASR、润色、会话、配置、词典、历史、录音与取消令牌 | 直接复用 `open`、`subscribe`、`call`，无需再迁一遍业务；保留有界队列、代次与恢复语义 |
| macOS 主壳 | Python/PyObjC 主进程，经 stdio 驱动 Rust 子进程；设置页使用 WebView | Rust 主程序可以直接嵌入现有 backend，减少 IPC 与运行依赖；设置、菜单、胶囊与退出流程需要实际验收 |
| 原生音频 | Objective-C++ 库经 C ABI 供 Rust 使用 | 继续复用已验证的采集与低声链路；保留启动期限、晚返回隔离和设备恢复 |
| 平台行为 | Fn/Globe、AX、粘贴、剪贴板恢复、屏幕、播放、权限和 Sparkle 分布在薄壳内 | 使用 Rust 原生适配器统一所有权；需要验证焦点、取消后不回填、TCC、Spaces 与升级行为 |
| Windows | 产品入口与平台路径仍主要是 Python | 长期统一价值较大；GPUI 窗口本身不能补齐 Windows 采集、DSP、播放、文本输入和文件格式适配 |

本次迁移限定 macOS。设置页保留全部配置项，胶囊按 `0.5.1` 原生实现做像素与行为对照；Windows 的完整产品适配单独推进。

## 收益需要怎样证明

移除 Python/WebView 的运行依赖已经可以通过实际 `.app` 的进程与加载文件读回证明。内存、能耗、启动和听写延迟则需要在同一机器、相同输入、网络、窗口状态及构建模式下与旧版比较；目前不承诺这些指标必然改善，也不以框架演示数据估算产品收益。

GPUI Kit 当前官方最新发布为 [0.7.0](https://github.com/longbridge/gpui-kit/releases/tag/v0.7.0)，本实现锁定该版本。其底层 GPUI 仍需按具体版本处理生命周期与平台行为；本次真实退出测试已暴露并修复 200 ms 最终清理期限和 AppKit/GCD 重入问题，说明编译通过不足以证明迁移完成。

## 当前交付边界

实现及验收进展见 [迁移清单](plans/2026-10-03-macos-rust-desktop.md)、[胶囊对照](capsule-rust-parity.md)、[设置功能对照](macos-rust-settings-parity.md) 和 [平台验收](macos-rust-platform-parity.md)。

本机登录会话仍锁定，真实快捷键、跨应用回填、焦点与权限交互尚不能验收。真实设备低声场景、中文 IME、Spaces/多显示器及正式签名后的升级行为仍需对应环境的验证。当前成果可以评审和运行，但尚不代表完整迁移验收或正式发布完成。
