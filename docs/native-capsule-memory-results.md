# 原生胶囊内存测量

测量日期为 2026-09-02，机器为当前 arm64 macOS 开发机。测试对象由工作区
源码通过 `packaging/macos/build_app.sh` 生成，胶囊在启动后的后台初始化中已
完成原生 `NSView` 预热，设置窗口从未打开。

## 结果

15 秒冷启动采样从 17 MiB physical footprint 回落到 16 MiB，RSS 从
56.9 MiB 回落到 54.1 MiB，socket 从 0 保持为 0，线程从 5 回落到 4。

随后进行 30 秒稳定采样，physical footprint 保持 16 MiB，RSS 保持
54.1 MiB，socket 保持 0，线程保持 4，CPU 均值为 0，所有增长项均为 0。
进程没有子进程，因此原生胶囊没有启动 WebContent、GPU 或 Networking helper。
该场景已经通过 100 MiB physical footprint、4 socket、20 线程和 5 MiB RSS
增长门槛。

此前已安装的 0.4.6 长时间运行状态曾测得 376 MiB physical footprint、
419 MiB RSS、26 socket 和 18 线程，其中包含 17 个 CLOSED 与 8 个
CLOSE_WAIT。两次采样的运行历史不同，不能把全部差值归因于胶囊；新构建的
结果已经证明原生胶囊冷启动基线低于 100 MiB，并消除了胶囊常驻 WebKit
helper 的结构性成本。

## 尚未覆盖

当前结论仅适用于启动空闲和原生胶囊预热状态。设置窗口仍按需使用 WebKit，
打开设置后会产生独立 helper。后续还需完成录音中、处理状态、停止后 warm
idle、cold idle、10 次录音循环和 50 次录音循环；这些场景通过后，才能确认
日常完整使用过程持续低于 100 MiB。

原生按钮的 cancel/finish target-action、波形状态更新和处理进度已在真实
AppKit 对象上执行成功。当前桌面会话的系统截图接口只返回黑屏，因此视觉
像素级验收仍需在可捕获桌面的会话中补做。
