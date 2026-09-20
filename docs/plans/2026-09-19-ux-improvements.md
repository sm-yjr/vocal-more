# UX 改进清单(0.5.0a3)

来源:2026-09-19 对 0.5.0a3 的 UX 走查,覆盖听写主流程(胶囊、快捷键、失败反馈)与设置界面(React 前端 + Python 桥接)。

用法:先逐项评估优先级(P0–P3 或 Won't fix),再按顺序实施;实施后在状态列打勾并注明提交。

图例:
- 优先级:`P0`(信任级,尽快)/ `P1`(高频痛点)/ `P2`(体验补全)/ `P3`(打磨)/ `W`(不修)
- 状态:☐ 待评估 → ◐ 已排期 → ☑ 已完成(附提交号)

## 一、听写主流程(胶囊与反馈)

### UX-01 失败时胶囊静默消失
- 现状:`app.py:1699` 将 `ModeState.FAILED` 映射为胶囊 `hidden`,错误仅靠系统通知(`_on_error`);用户关掉通知后失败完全无感知。
- 建议:复用胶囊已有的 `connection_error` 内嵌错误态(`floating_capsule.py` / `native_capsule_view.set_connection_message`),FAILED 时短显原因 2–3 秒后淡出,通知作为兜底。
- 优先级:P0 | 状态:☑ 已实现(2026-09-19,待提交)。FAILED 后的错误回调通过胶囊内失败通知显示原因 4 秒后自动淡出,系统通知保留为兜底;新会话/陈旧连接回调不会打断通知。

### UX-02 成功通知过吵
- 现状:`app.py:1817` `_show_result_notification` 每次成功都弹系统通知(50 字预览),与设计原则 "Respect attention" 冲突;且 `text[:50] + "..."` 按字符截断可能劈开 emoji 代理对。
- 建议:默认关闭,或仅在 `auto_paste=false`(文本只进剪贴板)时提示;截断改用安全切片。
- 优先级:P1 | 状态:☑ 已实现(2026-09-19,待提交)。`auto_paste=true` 时不再弹成功通知,文本只进剪贴板时保留提示;预览截断改用 UTF-16 安全切片,不再劈开 emoji。

### UX-03 录音中 Esc 无法取消
- 现状:`app.py:1657` Esc 取消只覆盖 STARTING/STOPPING/PROCESSING/CANCELLING;handsFree 录音中只能点胶囊上 22px 的 ✕。
- 建议:RECORDING 态也让 Esc 触发取消(hotkey_manager 已在拦截 Esc 并透传,改动集中在 app 层)。
- 优先级:P1 | 状态:☑ 已实现(2026-09-19,待提交)。Esc 取消现已覆盖 RECORDING 态。

### UX-04 胶囊位置固定不可调
- 现状:`floating_capsule.py:194-208` 固定在鼠标所在屏底部居中、y=20,不可拖动、无配置,可能遮挡正在输入的文本区。
- 建议:至少支持记住用户拖放位置(鼠标按下检测),或提供上/下位置偏好。
- 优先级:待定 | 状态:☐

### UX-05 双份胶囊实现漂移
- 现状:`resources/floating_capsule/capsule.html` 已无源码引用(仅打包残留),实际使用 `native_capsule_view.py`;HTML 内含 native 缺失的 meeting 阶段翻译,两份已不同步。
- 建议:删除 HTML 死代码及其打包引用,消除"改了不生效"的陷阱。
- 优先级:待定 | 状态:☐

### UX-06 ✕/✓ 按钮命中区偏小
- 现状:`native_capsule_view.py` 按钮 22×22,低于 HIG 44pt;紧凑胶囊可理解,但可扩大 `hitTest` 热区而不改视觉尺寸。
- 优先级:待定 | 状态:☐

### UX-07 润色失败警告的文案误导
- 现状:`base_mode.py:373-375` 润色失败 warning 走错误通知通道,但此时原始文本已粘贴;用户易误以为整次失败。
- 建议:区分"已粘贴原文 + 润色失败"与"整体失败"的文案。
- 优先级:待定 | 状态:☐

## 二、设置 · 全局

### UX-08 配置保存零反馈、失败不可见
- 现状:所有控件即时保存,`setConfig` fire-and-forget,无脏状态、无回滚、无错误通道(mic test / 模型检查 / 压缩均有错误回调,唯独配置写入没有)。
- 建议:为 `setConfig` 增加通用错误回传;高风险项给轻量"已保存"瞬时指示。
- 优先级:P0 | 状态:☑ 已实现(2026-09-19,待提交)。宿主拒绝配置写入时回传 `updateConfig`(回滚乐观 UI)+ `configError`(错误提示);保存失败(如磁盘错误)回传错误但不回滚;设置窗口底部显示可关闭的 destructive 提示条,6 秒自动消失。

### UX-09 识别标签页藏得过深
- 现状:`App.tsx:46-48` "识别"(ASR 模型)页隐藏于高级模式之后,侧边栏无任何提示还有更多分区。
- 建议:侧边栏给"更多分区"提示,或把模型选择露出为非高级项。
- 优先级:待定 | 状态:☐

## 三、设置 · 快捷键

### UX-10 快捷键录制三坑
- 现状(`shortcuts-settings.tsx:216-233`):
  1. Esc 被录成热键(想取消反而录进 "Escape");
  2. 裸修饰键被直接接受,误触左 Cmd 即成触发键;
  3. 不支持组合键(单键平铺模型),`double_tap_threshold` 有配置无 UI(`types.ts:59`)。
- 建议:录制中 Esc 作为取消手势;裸修饰键等待组合或警告;组合键支持与双击阈值 UI 可分开评估。
- 优先级:P1 | 状态:☑ 已实现(2026-09-19,待提交)。录制中 Esc 取消;裸修饰键需双击确认(自动重复的 keydown 不计为第二次),录制状态与确认提示通过 `role="status"` 播报;组合键与双击阈值 UI 仍待评估。

## 四、设置 · 引导

### UX-11 Onboarding 无跳过
- 现状:`onboarding.tsx:88-100` Finish 被五项就绪(API key/权限/麦克风/热键/首次录音)门控,无 skip;General 的"重新运行设置"无确认直接进入(`general-settings.tsx:324-334`)。
- 建议:提供 skip(保留引导角标提醒未完成项);"重新运行设置"加确认。
- 优先级:P1 | 状态:☑ 已实现(2026-09-19,待提交)。Skip 保留 `ui.onboarding_skipped` 标记,侧边栏"通用"入口显示"设置未完成"角标直至正式完成引导;"重新运行设置"加确认,焦点移至确认按钮且有 `role="status"` 播报,焦点在确认对内时不自动收起。

### UX-12 麦克风权限无引导触点
- 现状:mic 权限在 onboarding 缺席,Audio 页只有只读状态行,无"打开麦克风系统设置"动作;宿主已有 `_open_microphone_settings`(`app.py:577`)。
- 建议:onboarding 增加麦克风权限卡;Audio 页死胡同状态接上打开动作。
- 优先级:P1 | 状态:☑ 已实现(2026-09-19,待提交)。openMicrophoneSettings 桥接全链路(bridge→actions→window→bootstrap→Privacy_Microphone 系统面板);onboarding 增加麦克风权限卡(环境检查驱动,不门控 Finish);Audio 页 denied/restricted 时提供打开按钮。

## 五、设置 · 各分区

### UX-13 工作区 endpoint 静默校验
- 现状:`recognition-settings.tsx:147-170` 仅设 `aria-invalid`,无可见错误文案,非法 URL 默默不提交。
- 建议:照抄代理输入的"校验失败时提示文案切换"模式(`ProxySetting`,general-settings)。
- 优先级:待定 | 状态:☐

### UX-14 LLM 模型选择缺失(死数据)
- 现状:`store.ts:136` `llmModels` 已入 store 但无组件渲染;`llm.model` 只读不写,设置里选不了润色模型。
- 优先级:待定 | 状态:☐

### UX-15 词典学习审批界面缺失(死数据)
- 现状:`store.ts:141` `dictionaryLearningRecords` 已入 store 但从未渲染;宿主侧 approve/reject/undo action 均已打通(`settings_actions.py:157-171`)。
- 优先级:待定 | 状态:☐

### UX-16 预设无当前态指示、无撤销
- 现状:`audio-settings.tsx` 耳语/普通/嘈杂预设即点即生效,无 active 指示、无 undo。
- 优先级:待定 | 状态:☐

### UX-17 音频控件禁用无解释
- 现状:采集忙碌时设备选择/刷新禁用,Apple AGC 激活时手动增益/限幅器禁用,均无 tooltip/提示说明原因。
- 优先级:待定 | 状态:☐

### UX-18 mic test 错误用提示样式渲染
- 现状:`audio-settings.tsx:541-543` `mic.error` 占用 `softwareGainHint` 位置,弱化色而非错误样式。
- 优先级:待定 | 状态:☐

### UX-19 词典"排除应用"每键击提交
- 现状:`dictionary-settings.tsx:74-84` 排除应用输入框每次键击发一次 `setConfig` 给宿主,无防抖。
- 建议:换 draft + blur/Enter 提交(同代理输入模式)。
- 优先级:待定 | 状态:☐

### UX-20 自定义词条/自定义键删除即生效
- 现状:删除词典词条、自定义键立即生效无确认无 undo;历史记录删除已有 5 秒 Undo 范式可推广。
- 优先级:待定 | 状态:☐

## 六、历史与杂项

### UX-21 复制成功反馈未渲染
- 现状:`history-settings.tsx:474-478` `copiedRecordingId` 状态与 1.5s 定时器均在,但 JSX 从未渲染——反馈机器在,视觉不在。
- 优先级:P2 | 状态:☑ 已实现(2026-09-19,待提交)。复制按钮切换为 1.5 秒"已复制"确认态;两条剪贴板路径都失败时不再误报成功,改以错误样式提示手动复制。

### UX-22 使用废弃的 execCommand 复制
- 现状:`history-settings.tsx:291` `document.execCommand("copy")`,应换 async Clipboard API。
- 优先级:P2 | 状态:☑ 已实现(2026-09-19,待提交)。优先使用 async Clipboard API,拒绝时回退 execCommand(WKWebView file:// 非安全上下文仍需回退);回退返回值被检查,失败不再显示成功。

### UX-23 硬编码英文残留
- `general-settings.tsx:136` "Show"/"Hide";`history-settings.tsx:233` "Speaker"。
- 优先级:P2 | 状态:☑ 已实现(2026-09-19,待提交)。两处均已换为双语 copy。

### UX-24 import 语句位置异常
- 现状:`recognition-settings.tsx:175` `import` 写在文件末尾、组件代码之后,明显残留。
- 优先级:P3 | 状态:☑ 已实现(2026-09-19,待提交)。import 已移至文件顶部。

## 值得推广的既有范本

- 历史记录删除:5 秒暂存 + Undo(`stageDelete`)→ 可推广至 UX-20。
- 代理输入校验:提示文案切换 + `aria-invalid` → 可推广至 UX-13、UX-19。
- 耳语校准向导:状态机 reducer、失败原因区分、双语朗读句 → 全应用最 polished 的流程,新向导照此标准。

## 评估记录

| 日期 | 条目 | 决定 | 备注 |
|---|---|---|---|
| 2026-09-19 | UX-01, UX-08 | P0,先实施 | 信任级问题,用户选定首个批次 |
| 2026-09-19 | UX-02, UX-03, UX-10, UX-11, UX-12, UX-21, UX-22, UX-23, UX-24 | P1–P3,已实施 | 高频痛点/新用户卡点/打包清理批次 |
| 2026-09-19 | UX-02, 03, 10, 11, 12, 21–24 | P1/P3,第二批 | 高频痛点 + 新用户卡点 + 杂项清理;ultracode workflow(6 实现者 + 全量验证 + 三视角评审 + 修复轮),9 条 minor 发现全部处理 |

## 0.5.0a4 发布前复审（2026-09-20）

- 补齐 `ui.onboarding_skipped` 的 Python 桥接白名单、Rust 配置解析、默认值和持久化契约。
- Rust 薄 UI 接入麦克风隐私设置、配置错误反馈和权威值回读、终态失败胶囊；仅剪贴板模式实际复制结果并提示。
- 保留连接失败和麦克风恢复提示，避免被普通短时失败消息覆盖。
- 引导页显示配置错误；跳过时停止试录；复制兼容回退无论成功或抛错均移除临时控件并恢复焦点；取消重新引导后恢复按钮焦点。
- 验证：Python 1051 项、前端 105 项测试通过；前端 typecheck/lint/build、Rust 工作区测试和 fmt、24 组原生胶囊布局检查通过。隔离数据下真实 AppKit/WKWebView 完成设置动作到 Rust 持久化、关闭保存和重新打开引导验证。未使用真实麦克风或云端 ASR 做端到端听写验收。
