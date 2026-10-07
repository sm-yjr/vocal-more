# macOS Rust 设置页迁移覆盖清单

Rust 设置窗口由 `rust/crates/desktop/src/settings/` 实现。窗口和控件采用 GPUI Kit 0.7.0；没有 WebView、JavaScript 或 Python UI 运行时。业务请求通过 `CommandSink` 异步提交给既有 Rust 后端，持久化结果以 `config_changed`、`snapshot` 等事件回读。本文是迁移审计清单；“已实现”不替代实际运行与平台验收。

设置窗口采用下述 Geist 色板及内嵌字体，并跟随系统外观。本文的实际像素验收使用 macOS Metal，不据此宣称 Windows 已运行验收。

## 配置项

`schema::FIELDS` 是独立控件的代码清单；`schema::COMPOSITE_KEYS` 是组合控件及工作流状态清单。`native_controls_cover_existing_form_contract` 从原前端 `FormState` 提取实际键名，验证每个键都有原生控件或组合入口。单次写入只提交对应键，保留未知配置字段。

| 页面 | 独立配置键 | 组合配置及行为 |
| --- | --- | --- |
| 通用 | `ui.advanced_settings`、`api_key`、`default_mode`、`ui.language`、`update_channel`、`network.proxy_url`、`auto_paste`、`native_fast_paste`、`restore_clipboard`、`streaming_paste` | API Key 默认遮罩，明确显示时 `revealApiKey`；显示、隐藏；模型/API 验证；获取 Key；打开配置；重新首次设置 |
| 音频 | `audio.input_device`、`audio.capture_backend`、`audio.gain_mode`、`audio.gain`、`audio.waveform_ceiling_dbfs`、`audio.highpass_filter`、`audio.highpass_freq`、`audio.soft_limiter` | 设备选择和刷新；低声/正常/嘈杂环境预设；麦克风测试、停止及原生回放；低声校准 |
| 识别 | `asr.model`、`asr.language`、`screen_context_enabled`、`asr.realtime_url` | `asr.backend` 由模型选择和后端契约确定；保留已保存但已下架模型；公共/空间端点；模型能力信息 |
| 润色 | `enable_polish`、`llm.polish_mode`、`llm.output_language`、`llm.level`、`llm.structured`、`llm.tone`、`llm.persona`、`llm.model`、`llm.temperature`、`llm.enable_thinking` | `llm.prompt_overrides` 的输出类型/程度/结构/语气/风格 5 分类；系统/自定义切换；编辑；载入当前系统预设 |
| 快捷键 | `hotkey.double_tap_threshold` | `hotkey.active_hotkeys` Fn 开关；`hotkey.custom_keys` 添加/删除/最多 8 个/去重；兼容 `hotkey.custom_key` 旧存档；原生 key code 和左右修饰键；修饰键两次确认；自动重复忽略；Esc 取消 |
| 词典 | `dictionary_learning.enabled`、`dictionary_learning.excluded_bundle_ids` | 词条及别名添加/删除；打开词典文件；学习记录、接受、拒绝、撤销 |
| 历史 | 无新增持久配置 | 列表、文本搜索、模型/模式/时间/时长/状态、文本与错误、费用分项与合计；播放/停止、复制、重新识别；删除及 5 秒撤销；压缩较早文件、压缩状态和存储统计；会议发言人/时间戳/转写/摘要/要点/待办；定位指定记录 |
| 首次设置 | `api_key`、`audio.input_device` 与低声预设相关键 | `ui.onboarding_completed`、`ui.onboarding_skipped`；API、麦克风、设备、辅助功能和热键状态；权限设置、重新检查、首次录音和回放；完成条件沿用原 UI；跳过后保留未完成提醒 |

设置窗口采用 Vercel Geist 设计：`rust/crates/desktop/assets/themes/geist.json` 定义取自 Geist token 的浅色/深色色板，`src/theme.rs` 嵌入 Geist Sans/Mono 字体并跟随系统外观切换。各页按 `schema::SECTIONS` 分组显示（如“录音与输入”“低声增强”“风格”），屏幕上下文与其依赖的空间端点同在识别页。面向个人用户，日常视图只保留常用选项：`update_channel`、`network.proxy_url`、`native_fast_paste`、`restore_clipboard`、`asr.realtime_url`、`dictionary_learning.excluded_bundle_ids` 和识别页的“模型能力”归入高级设置。已保存代理或空间端点、或开启屏幕上下文时，对应字段仍在日常视图显示，方便查看和撤销；“恢复公共实时端点”只在已填写空间端点时出现。词典学习记录显示可读状态，并只提供当前状态可执行的操作；首次设置在“完成设置”不可用时列出剩余步骤。

原前端未提供独立入口的业务内部参数（如固定的 PCM 输出格式、块大小、最大 Token、已退役模型管线键）保持后端原配置与验证规则，未被设置窗口重建或删除。`llm.model`、`llm.temperature`、`llm.enable_thinking`、`hotkey.double_tap_threshold` 保留原 `FormState` 契约并提供高级设置入口。

## 操作及后端接口

`schema::ACTIONS` 显式列出全部设置操作。控件提交 `set_config` 或后端 `ui_action`，模型和设备分别使用 `setAsrModel`、`setDevice`，快捷键使用 `setActiveHotkeys`。原生宿主处理 `open_microphone_settings`、`begin_hotkey_capture`、`end_hotkey_capture`、`stop_mic_test_playback`。

API Key 在公开快照中脱敏。隐藏和关闭设置窗口时清除 UI 显示中的密钥；不进行把空白脱敏表单整体覆盖已保存 Key 的 `sync_form_state`。明确编辑字段才写入。后端拒绝配置操作时显示错误并请求权威 `snapshot`，不根据猜测还原。

设置写入和操作调用使用 `request_checked`：队列拒绝时立即显示错误，只有成功入队的请求才建立 pending。配置控件恢复后端最后确认的值；密钥草稿清空并重新遮罩，麦克风测试进入可见错误状态。关闭前的 `flush` 同样检查入队结果，失败返回字段级错误供宿主通知，不能把未入队草稿当成保存成功。已被 UI 或后端队列接受的持久化操作由宿主关闭流程排空；预览增益与新的录音请求不作为关闭时的持久配置执行。

模型能力来自后端 `asr_models`、`llm_models`，不会将静态列表当成可用性证据。原生 ASR 禁用第二阶段润色；不支持 thinking 的模型禁用该开关。模型/API 验证的进行中、成功、失败、耗时和错误有可见状态。

音频设置在实际采集/测试/校准期间禁用可能改变测量条件的控件。Apple AGC 生效时禁用软件增益和限幅；高通关闭时禁用截止频率。日常视图的音频状态只显示当前设备、麦克风权限和回退警告；高级模式显示声道、处理方式、回声消除、实际增益、原生后端、源/输出格式、系统麦克风模式、丢块和故障，以及完整后端诊断（包括最近一次实际会话）。

低声校准采用原实现的两阶段测量：环境安静 3 秒、低声 4.5 秒，每阶段至少 8 个有效 RMS 样本；底噪取 dBFS 中位数，语音取 90 百分位，至少 6 dB 余量。测量基于当时生效的增益（Apple AGC 视为 1），推荐目标 −14 dBFS、增益范围 1–50、波形额外 2 dB 余量。应用前显示完整变更；高通至少 220 Hz，并保留已设置的更高截止频率。关闭校准后使原定时器失效、停止测试，并抑制校准录音自动回放。

删除使用稳定 recording ID，与视觉列表索引无关；撤销取消待删除 ID。关闭设置窗口时提交已进入撤销窗口的删除，与旧 UI 卸载时行为一致。播放和麦克风测试在关闭时停止；修饰键捕获也结束。

## 验证及剩余验收

纯 Rust 测试覆盖旧 `FormState` 的字段映射、原 UI 工作流操作和后端/原生路由对照、嵌套配置无损修改、代理与空间 URL 校验、低声推荐的增益等价性/低 SNR 拒绝/样本不足/有序变更、阶段状态、左右修饰键描述保留/二次确认/自动重复/去重和取消。

2026-10-03 使用锁定的 GPUI Kit 0.7.0 实际执行以下验证。测试数据全部位于临时目录；密钥为固定合成字符串，PCM 为测试输入，未访问真实麦克风或云端模型。

| 验证 | 实际结果与证据 |
| --- | --- |
| 设置逻辑与兼容契约 | 11 项模块测试通过。[日志](../.build/settings-unit-tests.log) |
| BackendDriver 集成 | 7 项测试通过：初始化脱敏、写入与重启回读、非法写入、词典增删、真实历史存储/文件操作、PCM 取消与 generation 隔离、两级有界队列、关闭排空已接受持久化操作、`finished` 收敛。[日志](../.build/bridge-lifecycle/latest-test.log)；[测试](../rust/crates/desktop/tests/bridge_lifecycle.rs) |
| 原生页面与 Metal 像素 | 7 页面和首次设置 × 中英 × 深浅主题 × 900×740/640×480，64 个组合通过；每个页面检查真实原生控件、主题表面像素、非空渲染以及横向控件边界。[矩阵日志](../.build/settings-rendering-matrix.log)；[矩阵报告](../.build/settings-rendering/report-matrix.json) |
| 原生交互与真实后端 | Key 聚焦/输入/保存/显示/隐藏/清除；真实文件写入失败后的错误及权威回读；下拉键盘选择；滑块拖动预览与释放持久化；代理验证；多行自定义提示词聚焦/编辑/失焦保存；词典增删；历史筛选/删除/5 秒撤销。[完整日志](../.build/settings-rendering-full.log)；[测试](../rust/crates/desktop/tests/settings_rendering.rs) |
| 两阶段低声校准 | 点击真实控件启动两个测试 PCM 预览，按真实 RMS 事件推进安静/低声阶段，应用推荐后回读增益约 39.93、高通 220 Hz、软限幅和 −12 dBFS 波形上限；取消后旧定时器不能再次启动预览。[推荐截图](../.build/settings-rendering/calibration-recommendation.png) |
| 首次设置与队列拒绝 | 条件不足时实际点击“完成”不能提交配置；“跳过”持久化回读通过。填满 UI 队列后开关回滚、Key 草稿清除、测试启动恢复错误状态，pending 不增长；聚焦编辑器 `flush` 返回无草稿内容的错误，后端旧值保持不变。 |
| 设置代码检查 | 设置库与两项集成测试 `clippy --no-deps -D warnings` 通过。[日志](../.build/settings-rendering-clippy.log) |

完整 Metal 验收报告是 [report.json](../.build/settings-rendering/report.json)。2026-10-04 主代理独立重跑 `all` 后，报告记录 112 张实际截图、500 个接受的 UI 请求和 `driver_finished=true`；其中新增配置覆盖贡献 36 张截图。请求总数包含查询和保存屏障，不等于用户操作次数。各组单独写 `report-<case>.json`，不会覆盖完整报告。可通过 `VOCAL_MORE_UI_CASE=matrix|key|controls|calibration|onboarding|admission|configuration-coverage` 定位运行。

测试使用 GPUI 公开 `Window::bounds_changed` 同步 TestPlatform 的 resize 回调，并断言请求尺寸、布局视口与 Metal 画布一致。人工截图检查发现并修正了仅缩小画布的无效测试；旧报告保存在 `report-before-resize-callback-fix.json`，不作为布局通过的证据。窄窗口采用上下布局，说明文字自动换行；[640×480 英文通用页](../.build/settings-rendering/minimum-en-light-general.png) 和 [中文深色通用页](../.build/settings-rendering/minimum-zh-dark-general.png) 可直接查看。

复现命令在仓库根目录执行：

```sh
cargo +stable test --offline -p vocal-more-desktop --lib settings --manifest-path rust/Cargo.toml
cargo +stable test --offline -p vocal-more-desktop --test bridge_lifecycle --manifest-path rust/Cargo.toml
cargo +stable test --offline -p vocal-more-desktop --features ui-test --test settings_rendering --manifest-path rust/Cargo.toml
cargo +stable clippy --offline -p vocal-more-desktop --features ui-test --lib --test bridge_lifecycle --test settings_rendering --no-deps --manifest-path rust/Cargo.toml -- -D warnings
```

这些结果证明原生控件、Metal 渲染和后端数据路径。真实设备与 TCC 权限、声卡/麦克风采集及扬声器回放、云端模型/API 验证与重试、学习审核全流程、系统级左右修饰键捕获和 VoiceOver 朗读仍应由产品级验收补齐。GPUI Kit 0.7.0 的 Button 尚不在无障碍快照中暴露 disabled 状态，本次通过实际点击及后端无请求验证“完成”按钮的禁用行为，未据此宣称 VoiceOver 完整通过。

## 逐字段真实控件配置验收（2026-10-04）

新增 `configuration-coverage` case，补足原矩阵每页只检查一个 anchor 的覆盖缺口。独立运行结果为 **35/35 个 `FIELDS`、7/7 个 `COMPOSITE_KEYS` 入口通过**，缺失清单为空。[configuration-coverage.json](../.build/settings-rendering/configuration-coverage.json) 逐项记录实际控件角色与路径、滚动后的边界、操作方式、保存接口、回读结果和未验证范围。[完整 case 报告](../.build/settings-rendering/report-configuration-coverage.json) 包含 36 张真实 Metal 截图及 `driver_finished`；[验收日志](../.build/settings-configuration-coverage.log)、[Clippy 日志](../.build/settings-configuration-coverage-clippy.log) 可核对执行结果。

验收固定使用 640×480 布局视口及对应 Metal 画布。逐项查找生产 Settings 渲染的原生控件，通过真实滚动移入视口，再实际点击、聚焦输入、下拉键盘选择或拖动滑块。每项必须找到对应的成功 `rpc_response`，重新打开隔离目录的 `config.yaml` 核对落盘，并与后端 `snapshot` 比较。Key 使用合成字符串，磁盘只读比较后验证公开快照仍脱敏，JSON 不导出其值。此路径不调用 `Harness.set` 代替控件操作；报告请求总数包含状态查询和保存屏障，不等于用户操作次数。

本次 20 个字段在操作前位于滚动视口之外，随后均通过真实滚动可达；34 个字段实际改变持久化值。`audio.input_device` 通过原生下拉确认并持久化“系统默认”，无硬件后端的设备列表为空，故该项没有制造假设备或声称完成物理设备切换。高级模式通过实际开关关闭和开启，确认高级控件消失并重新渲染。另验证 13 个条件控件：关闭自动粘贴时两项输入选项不能写入；关闭高通时截止频率不能写入；选择后端目录中的原生 ASR 时润色不能写入；关闭润色时九项 `llm.*` 控件不能写入。实际操作依赖控件重新开启后，逐字段保存证明它们恢复可操作。

GPUI Kit 0.7.0 在上述 13 次观察中均未通过控件快照提供 disabled 布尔值。JSON 保留实际 `null`，不将其改写成禁用或启用。禁用证据来自实际点击或拖动不产生保存/预览请求、磁盘保持不变；恢复证据来自依赖操作后的对应字段保存。这没有验证原生 disabled 无障碍元数据或 VoiceOver。

组合入口核对五类提示词的按钮、开关、textarea 实际输入及失焦保存、重新载入系统预设；Fn 开关保存；“添加触发键”按钮、合成 F13 平台事件及“移除”按钮对 `custom_keys` 和兼容 `custom_key` 的同步落盘；模型选择对后端目录 transport 的关联保存；重新首次设置、权限条件不足时“完成”不写入、“跳过”同时保存两项状态。热键 begin/end 请求由测试宿主按生产 Host 的平台边界接收，不转发成未知后端 RPC；合成事件验证设置捕获入口与持久化，没有验证全局 Quartz 捕获。

整个 case 使用临时目录和合成 Key，不启动真实麦克风，不发 provider 请求，不触碰用户配置，也不打开系统权限页面。代理仅保存本地地址；实时 URL 仅保存合规的合成空间地址并通过真实按钮恢复公共端点，未连接该地址。JSON 保留未验证项：物理设备切换、Apple AGC 与实际音频忙碌状态的禁用条件、不支持 thinking 的模型（当前权威目录全部支持）、全局左右修饰键捕获、取得真实 TCC 权限后的首次设置完成，以及可选目录之外的 ASR transport。

在仓库根目录复现：

```sh
VOCAL_MORE_UI_CASE=configuration-coverage cargo +stable test --locked --offline -p vocal-more-desktop --features ui-test --test settings_rendering --manifest-path rust/Cargo.toml
cargo +stable clippy --locked --offline -p vocal-more-desktop --features ui-test --lib --test settings_rendering --no-deps --manifest-path rust/Cargo.toml -- -D warnings
```

`all` 包含新增 case。主代理独立运行原有病例及新增配置覆盖，全部通过：112 张截图、35 个字段、7 个组合入口及 14 个依赖检查，缺失清单为空，driver 实际完成，关闭失败摘要为零。[独立完整日志](../.build/settings-rendering-root-final.log) 与 [完整报告](../.build/settings-rendering/report.json) 对应本次运行；桌面全部 targets 的 `ui-test` Clippy `-D warnings` 和 workspace 格式检查也通过，[Clippy 日志](../.build/settings-rendering-root-clippy-final.log)。独立 JSON 在任一依赖、字段或组合入口失败时记录缺失项并使 case 失败；成功退出后补写实际 driver 完成状态和数值形式的关闭失败摘要。
