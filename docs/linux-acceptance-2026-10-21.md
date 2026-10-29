# Linux 原位输入与快捷键手势验收（2026-10-21）

基线仍为 `0.6.1+2`，分支 `codex/linux-desktop`。本轮是本机开发版整改，未发布正式版。
依据 [Linux 迁移要求](linux-migration-requirements.md)，支持路径通过不代表迁移全部完成。

## 实现与修复

- GPUI Kit 按官方最新 release 锁定 `0.8.0`（10 月 19 日发布；10 月 21 日核对），tag commit
  `1676826d9d49b90f4d7b6808b69924f58796f6f1`；默认 GPUI 后端仍使用匹配的 0.3.9 快照。
- Wayland 在录音开始时保留原输入框。输出前核对焦点、活动窗口、内容、选区、取消代次与隐私限制。
  光标处用原生 InsertText；普通单行选区用一次原生替换，保留前后文字。原生路径不改剪贴板。
- 修复实际桌面上应用树较多或存在失效辅助功能对象时，启动焦点恢复遗漏输入框的问题。
- GNOME 快捷键 UI 新增按住/松开手势开关，通过真实 GlobalShortcuts portal 授权。
  按原有模式支持长按松开结束、短按免提再按结束；按住说话模式每次松开结束。
  单一有序信号流保留连续按下/松开的顺序，过滤重复按下与过期会话。
- 修复慢启动或主线程排队将短按误判成长按的问题：单调时钟的原始事件时间穿过 UI/后端队列。
  本地诊断入口也保留有界单调时间；网络 JSON 接口不能提供后端类型化时间。
- 授权失败、停用、退出会恢复本配置所属的一键切换绑定，其他应用快捷键不变。
  设置窗口关闭后，后台、托盘、胶囊和快捷键继续工作。

## 已通过的验证

宿主为当前 Ubuntu 26.04 / GNOME 50 Wayland 桌面。所有输入框、音频和临时配置均属于专用测试，
不读取用户其他窗口文字。密钥从忽略的 `.env` 读取，仅写入权限受限、结束后删除的配置。

1. 私有真实 GNOME Wayland compositor：实际系统 Add Keyboard Shortcuts 授权、F8 按下/松开、
   长按、短按、重复抑制、按住时关闭 owner 的取消清理；同时验证原生 Unicode 选区替换与追加、
   焦点变化拒绝、取消拒绝和密码字段拒绝。不是 mock portal。
2. 当前真实桌面上的正式 GPUI 宿主：合成英文语音通过实际 DashScope 识别，自动写入自有 GTK4
   原生 Wayland 输入框，精确核对前缀/识别结果/后缀、光标、原剪贴板与正常退出。
3. 流式自动输出、短按免提再按结束、walkie-talkie 松开结束均通过完整云端/桌面链路。
   结束时间早于测试音频 EOF，避免将文件自然结束误当成快捷键成功。
4. 录音后切换输入框会拒绝自动写入：原文字和剪贴板不变，完整结果保留在历史记录中。
5. 私有 GSettings 与实际 GPUI 设置：录入、编辑、冲突、移除、已有 F1、其他应用绑定、
   手势开关的备份/恢复、退出持久化和录入期间禁止误录音。
6. 完整 Python 回归：1072 通过、29 跳过。Rust workspace 132 通过、10 跳过；桌面严格 Clippy 与格式检查通过。
   包含慢派发下手势间隔的后端集成回归。

证据位于忽略的 `.build/`：`linux-gestures-final2/session.json`、
`linux-auto-input-verified/session.json`、`linux-auto-input-streaming/session.json`、
`linux-auto-input-focus/session.json`、`linux-auto-input-tap-final/session.json`、
`linux-auto-input-walkie/session.json`、`linux-shortcuts-final/`。
语音识别把合成的 Vocal More 多次识别为 Google More 或中英混合；这里验证传输/输出一致性，
不将该样本当作识别准确率验收。

## 本机安装与尚待验收

本机 APT 开发包已更新为本轮构建（产品版本仍是 `0.6.1+2`，GPUI Kit `0.8.0`）。
安装后的 `/usr/bin/vocal-more` 在当前 Wayland 桌面通过短按免提、真实云端识别、自动选区替换、
光标和剪贴板验证：`.build/linux-installed-auto-input/session.json`。
安装后真实麦克风录制取得 15360 字节 PCM，托盘、设置关闭后后台存活、胶囊像素/不抢焦点、
取消隐藏和退出清理通过：`.build/linux-installed-input-background/session.json`。
该胶囊实测明确使用验收模式下的 Xwayland 兼容渲染器；当前桌面的原有 Shell 扩展保持运行，
原生 Shell 渲染与点击的自动化证据来自私有 compositor，不能混称为当前桌面的像素验收。
程序/安装清单摘要一致，用户 config.yaml 摘要未变；没有导入、覆盖或清空原有历史。

当前桌面的 GNOME 原生 Add Keyboard Shortcuts 授权已实际确认，手势配置保留 F1。
实体键盘验证窗口收到 `今天的测试很顺利。`，光标为 9；正式用户配置的历史记录显示
`realtime_long`、2.1 秒、`success`（23:11:29）。本次没有通过诊断入口触发录音，
验证窗口只提供输入框：说明系统授权后的本机 F1、真实麦克风、云端结果与原位写回已串联。
证据：`.build/linux-installed-f1/session.json`。
用户已确认胶囊、F1 松开结束和自动写入正常，并指出完成后胶囊收起过慢。
后续修复：完成/隐藏状态立即唤醒 Shell 传输；错误提示对齐 macOS 的四秒自动收起。
私有真实 GNOME actor 的隐藏实测为 50 ms（验收上限 300 ms），见
`.build/linux-capsule-hide-acceptance/capsule-hide-latency.json`。
当前桌面的完整云端输入也检查了写入后的胶囊消失，错误提示的保留/自动收起另有专项记录。

## Codex / Chromium 兼容输入整改

用户在 Codex 遇到“自动插入未完成”。已确认运行中的 Electron 辅助功能树只有窗口壳；
Chromium 即使启用辅助功能，网页输入框也可能仅提供可读 Text、没有 EditableText。
新增通用设置中的“兼容自动插入”：真实 RemoteDesktop portal 只申请键盘权限，
原生写入接口缺失且尚未执行写入时，才允许经原输入框核对后的自动 Ctrl+V。
写入后精确确认文字，临时纯文本剪贴板在仍由本操作持有时恢复；图片/富文本剪贴板暂时拒绝，保留原内容。
焦点/内容/选区变化、密码、排除应用及取消继续拒绝，不自动重试无法确认的写入。

“配置 Codex 兼容启动”保留原参数与其他桌面动作，在用户应用入口添加
`--force-renderer-accessibility`，保存原入口备份。运行中的 Codex 需要完整退出后从应用入口重新启动。
没有自动重启当前 Codex，也没有打开系统屏幕阅读器。

真实私有 GNOME Wayland + Chromium 已通过：系统原生键盘授权、Unicode/表情选区替换、
剪贴板恢复、焦点切换与取消拒绝、关闭清理、保存授权后重建 owner 无再次询问。
1800 条自有历史节点、8 层嵌套输入框的冷启动定位也通过。
修复 Collection 规则未明确指定其他 MatchAll 条件而返回空结果，以及全量 StateChanged
信号阻塞 Chromium 响应的问题；只订阅 focused，并保留完整的 32 层隐私祖先检查。
证据：`.build/linux-compatible-input-cold-all/{session.json,rust-compatibility.json}`。
原生 GTK/快捷键回归：`.build/linux-compat-native-regression/`。
用户当前 Codex 的安装后输入体验仍待兼容启动重启后的实际确认，不能用私有 Chromium 测试替代。

本机 `.deb` 已更新为上述兼容构建：安装程序摘要与清单一致，原 config.yaml 摘要不变，
F1 手势配置保留。已通过当前 GNOME 原生授权窗口启用键盘交互，并在正式宿主重启后
自动恢复授权；设置界面显示“兼容自动插入已就绪”和“Codex 兼容启动已配置”。
原有 Codex 启动项中的 Wayland 输入法参数保留，原入口备份摘要与改动前一致。
证据：`.build/linux/installed-codex-compat.json`、`installed-codex-compat-settings.png`。
设置按钮的实际 GPUI 点击和备份持久化另通过 `.build/linux-compat-settings-verified/passed.json`。

仍未完成：多种工具包/浏览器/编辑器的通用自动插入、富文本文档/多行选区安全替换、
超过 8000 字符的输入框、其他桌面环境、裸修饰键，以及全屏/多显示器/工作区/锁屏的完整矩阵。
不支持或状态变化时胶囊明确报错，结果留存，可从托盘主动复制；手动粘贴不计自动插入通过。

参考：[GPUI Kit v0.8.0](https://github.com/longbridge/gpui-kit/releases/tag/v0.8.0)、
[GlobalShortcuts 官方协议](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)、
[AT-SPI EditableText](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/iface.EditableText.html)。
兼容路径参考：[RemoteDesktop 官方协议](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)、
[Electron 辅助功能](https://www.electronjs.org/docs/latest/tutorial/accessibility)。
