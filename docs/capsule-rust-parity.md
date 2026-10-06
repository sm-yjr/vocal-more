# Rust 胶囊等价验证

Rust 桌面端的胶囊由 `rust/crates/desktop/src/capsule/` 直接持有 AppKit 对象，调用路径不加载 Python、PyObjC 或 WebView。设置页使用 GPUI Kit；胶囊继续使用 `NSPanel`、`NSView`、`CALayer`、`NSTextField`、`NSButton` 和 `NSTextView`，保留当前系统字体及原生栅格化结果。

基准是 `b537df6a3bbaeba9ab06cf0cdc0e3dbbcbcaef60` / `0.5.1` 中的 `floating_capsule.py` 和 `native_capsule_view.py`。

## 已保留的呈现与行为

| 项目 | 当前胶囊与 Rust 胶囊 |
| --- | --- |
| 紧凑容器 | 240 × 80 pt；表面高 36 pt，圆角 18 pt |
| 展开录音 | 容器宽 360 pt，表面宽 320 pt；文字按测量结果增减高度 |
| 展开通知 | 容器宽 400 pt；高度最多 200 pt |
| 录音紧凑表面宽 | pushToTalk 64、handsFree 126、prompt 178、promptPushToTalk 112 pt；本地化标签按实际字体测量扩展 |
| 字体 | 系统字体；标签 12 pt medium，正文 12 pt regular，按钮 13 pt medium |
| 外观 | 黑色背景、1 pt 白色边框 / 0.32 alpha；阴影 opacity 0.35、radius 15、offset (0, -8) |
| 控件 | 原生 × / ✓ 按钮、22 × 22 pt；按模式显示，后台命令通过 `CommandSink` 非阻塞发送 |
| 波形 | 紧凑 10 条，展开按可用宽度分配、最多 80 条；2 pt 宽、2 pt 间距、Gaussian 包络 |
| 波形积分 | phase 8.64 rad/s；attack 0.045 s，decay 0.18 s；elapsed 限制 1/120–0.05 s，按 backing pixels 舍入 |
| 静音 | ≤ 0.005 的校准电平按零处理，波形逐渐收敛到 2 pt |
| 处理进度 | 48 × 3 pt；时间常数 0.464 s，渐近目标 0.9，差值 ≤ 0.001 后停止刷新 |
| 正文 | 最近 4000 个 Unicode scalar，UTF-16 range 滚动到末尾；最多 122 pt 可见正文 |
| Prompt | 复用 Rust 后端的 contract-backed coach；首帧、partial 和界面语言变更同步更新 |
| 窗口 | 鼠标所在屏幕水平居中、距屏幕底部 20 pt；所有 Spaces、全屏辅助、Stationary、level 1000 |
| 焦点 | Nonactivating panel，明确拒绝成为 key/main window；pushToTalk、处理和普通失败通知忽略鼠标 |
| 隐藏 | 表面 alpha 立即归零，250 ms 后 `orderOut`；新录音撤销旧隐藏期限 |
| 通知 | 连接通知穿过 processing/idle 状态变化，ready 恢复底层状态；终态听写失败保留 4 s |
| 减少动态效果 | 每次进入 recording 读取 NSWorkspace 设置；保留音量响应，停止相位摆动 |

所有 AppKit 对象及呈现状态由主线程持有。主宿主在 `needs_tick()` 为 true 时以 60 Hz 的主线程 tick 驱动波形、进度和通知期限；空闲胶囊不执行渲染工作。重复 processing 通知不重置进度。

## SDK 兼容处理

本机基准 Python 可执行文件链接 macOS SDK 15.5，Rust 可执行文件链接 SDK 27.0。相同 NSTextView 调用在两个 linked-SDK 路径下产生不同文档宽度：旧路径保留初始化的 336 pt，新路径缩至 clip view 宽度。旧胶囊在较窄的录音面板中还会保留已有水平滚动位置。

使用临时 Rust 验证程序、仅改变其 `LC_BUILD_VERSION` 的受控对照确认了这个差异。产品实现显式保留 336 pt 文档宽度和原有的有界水平滚动规则，从而在当前 SDK 下得到相同换行和截图。该检查没有修改安装中的应用，也没有改变正式构建的 SDK 声明。

## 验证结果与复现

2026-10-03 验证结果：

- 胶囊 Rust 单元测试 5 项通过：六种模式与两种语言的控件包含关系、60/120 Hz 积分一致、静音收敛与进度边界、Unicode 最近尾部、连接/恢复文案。
- 实际 AppKit 验证覆盖中英文、六种录音模式的紧凑与展开布局共 24 个组合，验证按钮与正文边界、逐行增减高度、长文本末尾滚动、连接状态恢复、失败通知保留、首帧 Prompt、减少动态效果、七种处理标签与渐近进度。
- 总计 60 个 Rust / Python PNG 的像素逐个完全一致；所有可见几何、字符串和可见正文滚动位置一致。
- 8 个 processing 截图中，保留但隐藏的旧正文 clip view 在 SDK 路径间有不同 scroll_y。比较器单独报告这个隐藏位置差异；正文重新显示后的滚动位置和截图仍严格比较。
- 实际 `NSButton.performClick` 验证 cancel/finish action 各派发一次，并断言 key/main window 不可取得、前台应用 PID 未改变、cancel 由 tick 关闭胶囊。
- 显式宿主退出使用 `Capsule::close()`，立即隐藏 panel 并撤销失败/隐藏期限及待处理 cancel；原生导出断言退出时没有残留可见失败通知或待刷新的计时状态。

原生导出 API 为 `Capsule::export_parity_fixtures(&Path)`。主程序的 `--capsule-fixtures` 使用隔离数据目录，并在进入普通宿主事件循环前运行这套检查。例：

```sh
# 在 rust/ 目录执行；选择仅供验收的隔离目录。
cargo +stable run --locked -p vocal-more-desktop -- \
  --capsule-fixtures ../.build/capsule-rust \
  --data-dir /tmp/vocal-more-capsule-parity --no-import --no-hotkeys
```

在仓库根目录生成现有原生胶囊的基准并比较：

```sh
uv run --with pillow python \
  rust/crates/desktop/src/capsule/tests/parity_oracle.py \
  --reference .build/capsule-python-reference \
  --candidate .build/capsule-rust
```

测试用 Python 文件只加载当前基准源代码，控制动画的确切时间点，导出原生像素并比较 JSON 几何；它不进入 Rust 产品运行路径。`fixtures.json` 与 PNG 位于两个 `.build/` 目录，可用于独立复核。比较器只放过未显示旧正文的 scroll_y，任何可见几何差异或非零像素差异都会失败。

这套胶囊验收不证明实际麦克风、热键、输入注入、屏幕截取、Spaces 切换或安装包升级已经完成；这些需要由完整桌面宿主分别验证。
