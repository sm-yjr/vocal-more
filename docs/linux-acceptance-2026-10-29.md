# Linux 迁移进展与验收（2026-10-29）

状态：开发候选，尚未全面对齐；不是正式发布或安装后验收完成声明。

## 基线与实现

- 已 fetch 远端；`origin/main` 和本分支基线仍为 `6d46f4f`（macOS `0.6.1+2`）。保留此前未提交的 Linux 迁移工作。
- 核对 [GPUI Kit 官方发布](https://github.com/longbridge/gpui-kit/releases/tag/v0.8.1)，将精确依赖更新为 `=0.8.1`，锁文件采用匹配的 GPUI `0.3.10`。产品版本未变。
- 输出目标上限与学习采集上限解耦：输出允许最多 262,144 个 Unicode 字符，仍有原有超时与隐私检查；纠错学习继续只接受最多 8,000 字符。上限是资源约束，不表示所有目标应用都能暴露这么多文本。
- 多行/文档选区在原生写入前转入已授权的兼容输入路径。避免用 SetTextContents 覆盖文档、丢失格式和撤销语义；未授权时保持拒绝，不执行删除后再插入。
- 选区计算使用检查过的整数转换与加法，非法或溢出选区不能导致 panic。
- 构建清单从 Cargo.toml 读取 GPUI Kit 版本，避免依赖升级后仍记录旧版。
- 更新过期桌面验收断言：终态错误应保留约四秒后隐藏，也支持提前点击取消；不再要求错误永久显示。

## 本轮证据

所有系统集成测试使用独立配置、私有 DBus/显示服务和自有测试输入框，不操作用户现有文档。

- Python 完整回归：1072 通过、29 跳过。
- Rust workspace（包含文档测试）：134 通过、10 忽略；桌面库 41 通过、9 忽略。
- 严格桌面 Clippy 与 Rust 格式检查通过。
- 私有真实 GNOME Wayland：系统快捷键授权、按住/松开、短按免提、重复抑制、持键关闭清理通过；原生 GTK 选区替换、12,004 字符的中文/表情输入目标、无授权多行选区拒绝、焦点切换拒绝和取消通过。证据 `.build/linux-20261029-gestures/gnome.log`。
- 私有真实 Chromium/Wayland：原有 contenteditable 输入、多行 textarea 选区替换、中文/表情、原剪贴板恢复、焦点变化拒绝、取消、退出和保存授权恢复通过。证据 `.build/linux-20261029-final-{chromium,multiline}/`。
- 生产 GPUI 宿主在私有 X11 桌面：后台启动、托盘、关闭设置后存活、托盘恢复、胶囊不抢焦点、错误四秒隐藏/提前取消、空闲隐藏及退出通过。证据 `.build/linux-20261029-desktop-fixed/`。
- 编译与开发包构建通过；仍沿用开发版本号，本候选不可覆盖已经发布的同版本包。

## 暴露的新限制与剩余工作

1. 长 Chromium 输入框并非都能读取完整值：本机 Chrome 的长 textarea/input 实测仅暴露一段文本，却报告文档范围的选区。输出继续安全拒绝；不能将 GTK 长输入通过推广为通用编辑器支持。失败证据 `.build/linux-20261029-long*/gnome.log`。尚未实现完整虚拟化文档定位。
2. 兼容粘贴仍拒绝图片/富文本剪贴板以保留原内容；没有多格式剪贴板保存/恢复实现。
3. 全屏、多显示器、工作区、锁屏、非 GNOME 桌面及裸修饰键的完整产品验收尚未完成；屏幕上下文和学习的正常产品流程仍按迁移要求继续验收。
4. 当前环境的 `no new privileges` 使 `sudo` 无法获得 root，不能更新 `/usr/bin/vocal-more`。开发候选可构建和解包验证，但本轮未安装；当前桌面的已安装包仍是此前版本，不能用候选测试替代安装后验收。
5. 本机不能验证 macOS 运行与签名，未发布 GitHub Release 或修改 Sparkle/APT 公共源。

依据：[Linux 迁移要求](linux-migration-requirements.md)。


## 当日续作：长输入、剪贴板与桌面矩阵

本节补充上面的第一轮结果，不代表 Linux 迁移整体完成。

### 已实现并验证

- GNOME 胶囊按录音开始时的鼠标所在显示器定位；同一会话固定位置，显示器移除时回退到主屏。
- 锁屏时抑制当前模型，防止解锁后重新显示旧录音。沿用 GNOME 在锁屏时卸载普通扩展、解锁后重建的生命周期。
- 单一内容格式的剪贴板可恢复二进制数据：例如 PNG、HTML，以及内容相同的文本别名。备份时核对各格式字节及格式列表；不同内容的多格式剪贴板继续明确拒绝，原内容不变。
- 12,004 字符的 Chromium 中文输入目标通过原位替换，包含插入表情、光标位置及剪贴板恢复。此前失败样本含大量重复表情；不能把普通长文本通过推广到所有 Chromium 文档。

### 系统验证证据

- `.build/continue-long-bmp/`：真实 Chromium/Wayland 长中文选区替换及输入服务退出。
- `.build/continue-html-final/` 与 `.build/continue-image/`：HTML、实际 PNG 字节在自动插入后恢复，输入服务退出后仍可读取原内容。
- `.build/continue-mixed-refusal/`：浏览器实际复制内容不同的 HTML/纯文本；自动插入被拒绝，原输入框及两种剪贴板格式保持不变。
- `.build/continue-shell-pixels/`：两个虚拟显示器上的真实 GNOME；全屏目标窗口、工作区切换、不抢焦点、屏幕锁定/解锁、胶囊取消/完成、失联、扩展重建与纠错学习。全屏和工作区均保存第二显示器的实际像素截图。
- `.build/continue-screen/`：真实 portal/PipeWire、裁剪缩放、拒绝授权、清理及本地识别传输（不当作云端视觉验收）。
- `.build/continue-shortcuts/`：生产 GPUI 快捷键录入、冲突、保存及录入保护。
- `.build/continue-python.log`：1072 通过、29 跳过；`.build/continue-workspace.log`：134 通过、10 忽略；`.build/continue-clippy.log`：严格 Clippy 通过。

### 仍需完成

- 不同内容的多格式剪贴板：尝试的 Clipboard portal 能恢复运行中的内容，但关闭其会话会丢失剪贴板，因此没有保留这条实现；不得降低退出后的数据保留要求。
- 大量连续辅助平面字符触发的 Chromium 辅助功能文本/选区不一致，仍安全拒绝。
- Xwayland 兼容胶囊的多屏矩阵、非 GNOME 桌面，以及安装后的完整用户流程仍不能由上述原生 Shell 私有测试替代。
- 本次执行环境仍不允许通过 sudo 安装；没有更新用户已安装的 `/usr/bin/vocal-more`。


### 云端与验收工具补充

当前受控桌面中，GNOME compositor 可显示焦点窗口，但没有键盘设备时 GTK 不会报告真实键盘激活。新增 `scripts/smoke_linux_private_cloud.py` 建立独立 GNOME、虚拟键盘与鼠标，仅激活本次专用测试窗口。临时配置和密钥副本均放入所属临时目录，异常退出也由外层清理。

`.build/continue-private-cloud-keyboard/result/session.json` 已通过真实 DashScope 合成语音识别、短按免提、原位替换、剪贴板保留、完成后 5 ms 胶囊隐藏和正常退出。该测试使用开发可执行文件与私有真实桌面，不是当前用户已安装版本验收。识别将合成的 Vocal More 转为 Google More，因此仅证明链路，不能当作准确率评估。

直接在当前用户桌面运行的同类自动测试未能取得专用输入框键盘焦点，保留为未通过记录 `.build/continue-cloud-run.log`，不把私有桌面结果冒充当前用户桌面结果。


云端追加矩阵已完成：`.build/continue-cloud-streaming/result/session.json` 验证流式原位输出；`.build/continue-cloud-focus/result/session.json` 验证切换输入框后拒绝写入，原内容和剪贴板不变；`.build/continue-cloud-walkie/result/session.json` 验证按住说话松开结束及原位写回。三项均使用真实识别服务、合成测试音频和私有 GNOME 桌面。正常输出的胶囊隐藏分别为 5 ms、11 ms，焦点变化错误提示按剩余显示期限收起。没有把合成音频当作真实低声麦克风验收。


当前用户桌面的阻碍已进一步定位：`org.gnome.ScreenSaver.GetActive` 返回 `true`，锁屏状态下用户扩展停用，实际桌面的输入框无法取得键盘焦点，StatusNotifierWatcher 也未提供服务。本轮没有尝试解锁、停用锁屏或重启桌面。诊断期间短暂启用的 AppIndicators 偏好已恢复为原来的停用状态。继续实机验收需要用户解锁；安装候选仍需在不受 `no new privileges` 限制的普通终端完成。上述私有桌面测试不替代这一步。


## 安装后验证与 Ghostty 修复

用户已通过 APT 安装候选，安装路径摘要与清单一致。当前桌面已解锁。
恢复已安装的 AppIndicators 后，真实托盘、关闭设置后后台运行、胶囊像素与焦点、取消、退出及托盘移除通过（`.build/installed-20261029-desktop/`）。
已安装程序的短按免提、流式输出、按住说话和切换目标拒绝写入均通过真实云端/Wayland 链路（`.build/installed-20261029-{cloud,streaming,walkie,focus}/`）。
本机原生音频库采到 8000 帧/16000 字节并正常停止；当时没有非零 RMS，不能据此宣称真人低声质量已验收。
用户级启动项仍指向旧便携版的问题已修复，两个启动入口改为 `/usr/bin/vocal-more`，旧文件已备份；运行进程已核对为安装程序，用户 config.yaml 摘要未变（`.build/installed-20261029-migration.json`）。

用户随后在 Ghostty 实测报告“无法确认录音开始时的输入位置”。元数据检查确认 Ghostty 只暴露聚焦 Panel 和 Component/Action，无 Text/EditableText。
新增 Ghostty 专用兼容路径：仅在原进程、原对象、活动窗口及焦点匹配时通过已授权 portal 发送 Ctrl+Shift+V；不读取终端缓冲区、不触发自动纠错学习、不发送 Enter。终端多行粘贴保护继续由终端处理。
`.build/ghostty-terminal-test/terminal-input.json` 记录真实私有 GNOME/Ghostty 收到精确的“终端🦀输入”；接收器不执行命令。剪贴板恢复、切换窗口拒绝、取消及关闭也通过。
这项修复发生在上述用户安装之后，需更新候选包再做当前桌面的 Ghostty 验证，不能将之前安装的版本算作已包含此修复。


Ghostty 修复最终回归：Rust workspace 135 通过、10 忽略；Python 1072 通过、29 跳过；严格 Clippy 通过。最新端到端证据位于 `.build/ghostty-terminal-final/`，同时核验准确接收、剪贴板恢复、焦点切换拒绝、取消及权限恢复。新 `.deb` 的负载摘要与 GPL 许可证已核对，等待用户安装该后续修复包并重启后的实机确认。


用户已安装 Ghostty 修复包。检测到旧进程仍持有被替换的二进制后，已通过应用 IPC 正常退出并重新启动；当前运行进程的 SHA-256 与 Ghostty 修复候选清单一致，记录见 `.build/ghostty-installed-verification.json`。等待实体 F1/真人低声在用户 Ghostty 窗口中的最终反馈；不能用进程摘要或私有测试替代该反馈。


### Ghostty 实机追加：BMP 剪贴板容量

用户复测时提示 `desktop response exceeds size limit`。仅查询剪贴板格式和大小，确认它是 `image/bmp`，11,652,950 字节；未保存其内容。原通用桌面命令的 4 MiB 限制误用于图片剪贴板。
新增独立的 64 MiB 剪贴板读取预算，其他桌面命令与识别文本继续保留原限制。读取前后仍检查实际大小；超过上限不加载内容。私有真实 Ghostty 使用 8 MiB BMP 验证了 Unicode 输入、原始图片字节恢复、切换焦点拒绝和取消（`.build/ghostty-bmp-final/`）。
追加回归：Rust 136 通过、10 忽略；Python 1072 通过、29 跳过；严格 Clippy 通过。
为了避免在实机确认前重复安装，当前临时运行修复后的开发程序（`.build/bmp-live-host.json`），启动入口仍指向系统安装版。等待用户复测成功后更新最终安装包并切回安装版。


### 实机焦点问题的最终定位

用户确认没有切换窗口。开启只记录状态/焦点、不记录听写内容的诊断后，捕获到 `state=recording` 期间明确的 `open_settings` 请求；systemd 日志同时显示旧用户服务约每五秒被再次启动。独立启动的开发进程占据数据目录时，服务的第二个实例按既有“再次启动打开设置”语义抢走焦点。

新增 `--background`：服务启动遇到已有实例时只进行 `ping`，不打开设置；后台首次初始化和托盘缺失也不再自动抢焦点。交互式应用入口保留主动打开设置的语义。开发验证统一交给已有的 `vocal-more-desktop.service`，通过运行时 drop-in 使用开发可执行文件，避免两个生命周期管理入口。

消除设置弹窗后，另捕获到临时剪贴板准备后的短暂焦点丢失。现在在原有 800 ms 预算内等待原终端恢复，只在原对象/原窗口/原内容与选区仍匹配时发送粘贴；发现其他可访问输入目标则拒绝。焦点等待后再次核对临时剪贴板所有权，避免使用用户期间复制的新内容。

`.build/background-launch-regression/` 验证重复后台启动不会打开设置；`.build/focus-wait-ghostty/` 与 `.build/final-focus-chromium/` 验证终端和原有浏览器输入路径。实机日志已出现焦点恢复与“已发送到原终端，剪贴板已恢复”，等待用户确认实际文字。


用户最新明确确认“文字已正常写入”，Ghostty 当前开发进程的真人 F1 原位输出通过；先前“仍失败”反馈被其后这次明确确认更新。设置自动弹出与大 BMP 剪贴板的实机问题已完成当前开发进程复核。
最终候选再次通过 Rust workspace 136 项（10 忽略），Python 1072 项（29 跳过）及严格 Clippy；负载、GPUI Kit 0.8.1、`--background` 和 GPL 文件校验通过。包 SHA-256：`5c3213aeeb0953db8948be27865c58d5c31b2f7e7dfe0b6ecaa44bd83baa764c`。用户正在安装最终候选；正式后台入口切换及安装后复核仍待完成。


### 实机追加：闪屏与终端控制字符

用户在确认可写入后继续报告中途多次闪屏、成功后胶囊停留数秒，并提供含异常终端字符的截图。因此上一候选不能作为本轮体验验收终点。

私有 GNOME 的焦点/窗口事件记录进一步确认：不仅 `wl-copy`，`wl-paste` 在缺少 data-control 的 GNOME 上也创建临时窗口；一次输出可产生多次 `wl-clipboard` 焦点切换。新增扩展协议通过 Mutter 的 `Meta.SelectionSourceMemory` 和有界异步 selection transfer 完成读写，不创建窗口。恢复后的原始剪贴板由 compositor 保留，应用退出后继续可读。输入 owner、源身份、限额、取消和原焦点校验仍保留；旧扩展及其他 compositor 继续原兼容路径，不能宣称都消除闪屏。

Ghostty 改用已绑定的专用 Paste 键，通过既有系统键盘授权发送，不再合成 Ctrl/Shift/V。接收器开启完整扩展键盘协议并捕获粘贴后的按键释放，精确验证中文与 emoji，无额外控制字符。`.build/no-flash-native-ghostty-final/` 同时验证 8 MiB BMP 恢复、应用退出后保留、切换焦点拒绝、取消和权限恢复；生产插入阶段 `focusEvents`、`windowEvents` 均为空。`.build/no-flash-native-chromium/` 和 `.build/no-flash-native-rich/` 的原位输入与焦点事件同样通过。

仍需安装更新后的扩展并在用户桌面复核。GNOME 已加载模块有缓存，更新代码可能需要重新登录。增加不含识别文本的状态事件诊断，用于区分最终识别、实际发送和胶囊收起；不能仅凭输入测试宣布用户报告的胶囊延迟已解决。


用户已安装无闪屏候选；磁盘二进制与扩展脚本摘要匹配该候选（包 SHA-256 `4de213a74c9d2868fc9f8ad1855aefd46b8deb36f95b1d36bdaed77ca84b8d80`）。后台服务已永久改为 `/usr/bin/vocal-more --background`，清除临时开发 ExecStart 覆盖并通过正常 IPC 退出旧开发进程。新安装版进程摘要核验记录为 `.build/no-flash-installed-process.json`。

用户反馈已重新登录后，实际检查显示当前远程 GNOME 会话仍是旧进程，D-Bus 只有旧 `Update` 接口，无新剪贴板方法；`ReloadExtension` 接口返回未实现。当前会话 `Remote=yes`、`Service=gdm-password`，因此已明确请求在这个远程 Linux 桌面内执行 `gnome-session-quit --logout` 并确认，而不是仅断开远程查看器。没有代用户结束整个桌面会话。等待新扩展接口出现后继续 F1/闪屏/收起延迟实机验收。

追加私有验证：`.build/no-flash-ownership-ghostty/` 证明用户期间用相同文字替换剪贴板后，原 owner 不会恢复覆盖新副本；`.build/no-flash-xwayland-ghostty/` 在 Xwayland 可用的真实 Wayland GNOME 内仍无焦点/窗口事件；`.build/no-flash-shell-timing/gnome-capsule.json` 的空闲收起时间为 51.067 ms。混合剪贴板仍保持拒绝，不将其降级为单一格式。


### 安装版实机结果：本次问题闭环

用户在远程 Linux 桌面内确认注销后，GNOME 进程与会话已实际重建；新 `ClipboardVersion` 返回 1，运行进程与最新安装包二进制摘要一致。用户再次在 Ghostty 使用 F1 明确确认：“写入正常，无闪屏，胶囊迅速消失”。本次设置自动弹出、终端原位写入、剪贴板临时窗口闪屏和胶囊滞留反馈在当前 GNOME Wayland/Ghostty 安装版范围内通过实机复核。

诊断记录显示两次实际听写均收到最终结果后立即转为空闲，兼容粘贴完成并恢复剪贴板，没有设置打开请求。该记录不包含听写内容。验收后已删除临时 `VOCAL_MORE_TRACE_UI` 服务覆盖，通过正常 IPC 退出并重启安装版，核对新进程无诊断环境变量、安装负载摘要一致、原生剪贴板接口仍可用。后台入口保持 `/usr/bin/vocal-more --background`，不会再运行工作区开发二进制。最终记录为 `.build/no-flash-final-acceptance.json`。

这一结论仅覆盖当前用户桌面与上述问题，不将不同内容的多格式剪贴板、病理性 Chromium Unicode 辅助功能不一致、非 GNOME 桌面或完整平台矩阵改记为完成。迁移总体差距继续保留。
