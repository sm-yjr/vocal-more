# Linux 桌面实机验收：2026-10-18

基线为 `v0.6.1` / `0.6.1+2`，分支 `codex/linux-desktop`；本记录对应包含未提交 Linux 移植工作的开发包，不是正式发布。

## 环境与范围

在这台 Ubuntu 26.04.1 / GNOME 50.1 主机上，用独立 HOME、会话 D-Bus、Wayland compositor/虚拟显示器和 Xvfb 验证真实 GTK、AT-SPI、剪贴板和 Shell 扩展。屏幕测试使用真实 portal、PipeWire 1.6.2、GStreamer 1.28.2 与主机 NVIDIA 渲染器。没有启用、重启或操作用户当前桌面的 Shell 扩展。

所有窗口文本、按键、录音与图像均为测试内容。最初按要求跳过云端；用户随后明确授权恢复云端，才读取被 Git 忽略的项目 `.env` 文件。密钥不写入命令参数、报告或开发包；测试 backend 的临时配置在关闭前清除密钥，临时目录随测试结束删除。

## 已修复问题

1. **纠错轮询读取了新焦点输入框。** 原实现分别读取当前焦点与保留目标，焦点切换会多读无关字段。现在只读取原粘贴目标，通过该目标的 Focused 状态区分继续观察和失焦提交；不请求新焦点字段的文本。真实输入框切换测试要求 focused 快照为空，同时保留目标仍能提交正确的纠错证据。
2. **GNOME 扩展禁用时重复移除定时器。** `disable()` 移除 timer 后，actor 的 destroy 回调再次移除同一个 ID，产生 GLib critical。现在先清零 timer ID，再销毁 actor；重复禁用/启用和关闭复验无此警告。
3. **隔离测试继承了真实桌面的 GNOME_SETUP_DISPLAY。** 补齐胶囊、AT-SPI 与 GPUI 测试环境清理，避免 GTK 的初始化 display 指向用户桌面。

此前屏幕验收修复的 VideoCropMeta/stride 裁剪、黑边和小图放大问题也再次通过真实 portal 复验。

## 本机结果

- 胶囊：真实虚拟指针点击 Cancel/Finish，保持 GTK 客户端键盘焦点；Rust 模型经 D-Bus 到达 Shell，按钮经 CommandSink 返回 backend，Cancel 即时撤销粘贴 epoch。
- 生命周期：空闲隐藏、控制器断线隐藏、3.5 秒无更新隐藏、拒绝第二控制器、扩展重新启用后恢复、worker 正常退出。
- X11：生产 DesktopWorker 自动粘贴实际替换中文字段中被选中的两个字符；真实键盘将 `github` 改成 `GitHub`。
- Wayland：生产 `wl-copy` 输出，真实 Ctrl+V；先确认完整粘贴基线，再观察键盘纠错，切换到无关输入框后只提交原目标的 `github → GitHub`。
- 本地学习：真实纠错证据经过队列、确定性分类 fixture、策略授权、词典写入、SQLite 重新打开和撤销；排除应用、密码字段、禁用学习、关闭均通过。该 fixture 只验证本地规则；真实分类另行验证如下。
- 屏幕：三帧实际选择窗口的 JPEG，尺寸 `1052×669 → 828×549`，约两秒一帧，逐字节送达本机协议 fixture；窗口改变、过期代次拒绝、拒绝授权及 PipeWire 清理通过。

证据：`.build/linux-capsule-acceptance/gnome-capsule.json`、`learning-correction.json`、`gnome-capsule.png`，`.build/linux-accessibility-acceptance.log`，`.build/linux-screen-cloud-acceptance/screen-capture.json`。

## 经授权的云端结果

生产 Application/Provider 使用 `qwen3.8-omni-flash-realtime`，接收上述实际采集的三个测试 JPEG 与 16 kHz 合成英语语音，最终结果回到应用：

> I'm working on LumaHarbor. The verification code is VM-8617.

独立视觉探针只问“读取画面中的验证码”，其音频和指令均没有答案；模型从实际截图读出 `VM-8617`。该探针使用专门的测试问答指令，产品仍保持只输出听写内容的行为。生产识别与视觉探针分别验证，不能将探针回答当作默认产品输出。

真实 GTK 纠错证据送入生产学习 actor，`qwen3.7-plus` 分类后以 `same_term_correction` 自动加入 `GitHub`，词典查询确认写入，撤销后为空。

较早一次语音识别将 `LumaHarbor` 输出为 `Loom Harbor`；后一次正确。链路与视觉读取通过不代表专名准确率稳定，合成语音验收也不代表低声实录质量或端到端延迟测量。没有为此改动正式听写提示词。

脱敏报告：`.build/linux-cloud-acceptance/cloud.json`。在线测试必须显式运行，不进入普通测试或 CI 默认调用。

## 回归与交付

- Python 全量：1072 passed，29 skipped。
- Rust workspace：125 passed；平台 ignored 测试按上文的专用 runner 另行通过。
- Clippy `--all-targets --no-deps -D warnings`、Rust fmt、相关 Python Ruff 与 `git diff --check` 通过。
- `.deb` 与便携开发包保持 `0.6.1+2`，包含 GPL、对应源码和更新后的扩展；不构建 DMG，不发布、不安装到当前用户桌面。

复现命令见 [Linux 开发说明](linux.md)。真实麦克风/低声效果、物理快捷键及其他发行版或应用工具包仍需单独验收。

## 同日补充：快捷键 UI 与迁移要求

用户指出安装后的独立听写窗口与缺失胶囊不符合 macOS 体验。这些项目已在
[Linux 迁移要求](linux-migration-requirements.md) 中明确登记为未完成；此前底层测试通过
不能作为默认产品形态完成迁移的证据。

快捷键页已补齐 GNOME 的查看、录入、修改、移除与刷新，保留已有 F1，支持功能键及
Ctrl/Alt/Super 组合键。系统配置由独立有界 worker 管理；通过每个绑定的 IPC 标识，录入
已有按键时不会触发录音。冲突保留原绑定，Esc、切页、失焦及关闭取消录入；退出排空已接受的保存。

`scripts/smoke_linux_shortcuts.py` 使用隔离 HOME、D-Bus、keyfile GSettings、Xvfb 与
生产 GPUI 设置窗口。真实 XTest 按键验证增改移、组合键、外部冲突、Esc、切页取消、F1
绑定识别；实际私有 CLI/IPC 验证已有绑定录入保护。测试未生成录音，未使用云端密钥，也未改动用户的快捷键。
配置规则及退出排空另由真实 GSettings 测试验证。此处没有宣称已经验证用户 GNOME 会话的物理按键抓取，
也不提供按下/松开手势；这两项依照迁移要求单独验收。

测试还发现 Linux 输入设备枚举失败会阻止整个后端初始化，导致设置也打不开。现已允许设置启动，
输入设备仍报不可用，并提供检查音频服务/连接设备后刷新的提示；不伪造可用麦克风。

该轮全量回归：Python 1072 passed / 29 skipped，Rust 126 passed；Clippy、fmt、Ruff 通过。
界面与断言证据位于 `.build/linux-shortcuts-acceptance/`。

## 后台形态与胶囊整改

本轮从正常启动流程完全移除独立听写窗口，使用 StatusNotifierItem/dbusmenu
托盘承载开始/结束、取消、复制结果、设置及退出。GPUI 在 Linux 显式采用
`QuitMode::Explicit`；实测发现默认的 LastWindowClosed 会导致关闭设置退出，现已修复。
关闭设置只排空设置修改，后台服务继续运行；再次启动只重开现有设置实例。

GNOME 扩展在当前登录会话仍未被 Shell 发现，因此增加真实可用的 Xwayland
通知式胶囊兼容路径，扩展可用时优先使用 Shell actor。渲染选择通过 GPUI
`GraphicalEnvironment`/`with_windowing` 完成；单独设置 GPUI_BACKEND 并不会让
上游库切换后端，实机验证发现并修复了这一点。真实 Wayland 会话、portal、
剪贴板和安全边界保持不变；没有引入 Wayland 下的 X11 键盘注入。
无 Xwayland 且扩展不可用时，开始录音会打开设置提示恢复步骤，禁止静默录音。

错误也不再依赖听写窗口显示：开始请求校验失败会显示胶囊；终态错误保持到
用户取消或开始新会话。GNOME ScreenSaver 活跃时取消当前会话并隐藏兼容胶囊。
退出时先排空所属工作，托盘保留保存/失败状态，最后释放会话总线连接。

本轮证据：

- `scripts/smoke_linux_desktop.py`：私有 HOME、总线和 Xvfb。后台无常驻窗口、
  真正 dbusmenu 菜单调用、设置关闭后存活、重复启动单实例、托盘 owner 替换后
  自动注册、托盘退出及配置持久化通过。缺少密钥的启动错误保持超过原来的
  4 秒超时；真实 XTest 指针点击取消后隐藏且不抢焦点。合成录音/处理态仅用于
  渲染与空闲隐藏检查，不冒充真实音频。证据 `.build/linux-smoke/background.json`。
- 当前 Ubuntu 26.04 / GNOME 50 / Wayland 用户桌面上，生产宿主配合一次性测试
  profile 与本机实际 StatusNotifierWatcher 验证了无窗口启动、托盘打开设置、
  关闭设置后继续后台运行、胶囊显示、目标窗口焦点保持、取消隐藏与退出。
  使用已有密钥的短暂原生麦克风连接观察到 `recording` 和 15360 字节 PCM；
  截图只读取本程序胶囊窗口，见 `.build/linux-session-acceptance/real-capsule.png`。
  未读取用户输入框、未插入文字；测试数据随后随临时 profile 删除。
- 另一次无密钥的当前桌面复验验证了实际错误胶囊和托盘退出后注册项移除，见
  `.build/linux-session-error-acceptance/session.json`。
- `scripts/smoke_linux_gnome_capsule.py` 再次通过 GNOME actor 实际指针点击、
  Rust 传输、失联/重新启用恢复、清理和 Wayland 纠错观察，见
  `.build/linux-capsule-background-acceptance/`。
- 快捷键生产 UI 回归通过；保留 F1 识别、录入保护、冲突和持久化能力。
  本轮不声称已验证用户实际物理 F1 的 GNOME 抓取。
- Python 全量：1072 passed / 29 skipped；Rust 全量：126 passed。
  Clippy `-D warnings`、fmt、Ruff 和 diff 检查通过。

安装后使用同一个显式实机会话脚本对 `/usr/bin/vocal-more` 复验；结果保存在
`.build/linux-installed-background-acceptance/session.json`。该脚本仅在显式传入
`--real-session` 时操作当前桌面；可选 `--key-file .env` 开启短暂的真实麦克风/
provider 连接并取消，不验证云端识别准确率，密钥不会进入参数或报告。

本轮只关闭后台默认形态与当前桌面胶囊基本链路的差距。多显示器/工作区/全屏/
锁屏完整矩阵、其他桌面环境、按住/释放手势和 Wayland 自动原位插入仍需按
《Linux迁移要求》继续验收，不宣布整个跨平台迁移完成。


### 安装后复验结果

APT 已重新安装本机开发包 `vocal-more 0.6.1-2`（产品 `0.6.1+2`），
安装后二进制 SHA-256 与包内 manifest 一致。更新前后原有配置/历史摘要一致，
对应源码包不含 `.env`。

`/usr/bin/vocal-more` 在当前 GNOME Wayland 会话的实机验收通过：无常驻听写窗口、
实际托盘打开设置、关闭设置后继续运行、真实麦克风进入 recording、15360 字节 PCM、
胶囊文字/波形像素存在、焦点保持、取消隐藏、显式退出和托盘注册项清理。
详见 `.build/linux-installed-background-acceptance/session.json`；
该次胶囊图为 `capsule-471392.png`。GPU 合成窗口的验证改为读取所属窗口的
Composite backing pixmap，使用唯一图片文件名并检查波形和文字区域，避免读到
遮挡区域或复用图片路径造成验收误判；没有读取其他用户窗口。

这次不重复验证云端识别准确率。临时麦克风测试未输出文字，配置和音频均已删除。
安装版随后以用户原 profile 在后台启动，保留 F1。当前工作树未提交或发布新版本。
