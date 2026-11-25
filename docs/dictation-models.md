# 听写模型与通信分类

核对日期：2026-11-25。`domain/model_catalog.py` 保存模型通信协议和输出能力；`ASR_MODEL_CATALOG` 是设置页、菜单、RPC 初始化和 Windows 共用的可选听写列表。`ALL_ASR_MODELS` 保留旧配置解析、历史录音与失败恢复需要的模型资料。下架范围仅限可选列表，旧配置不会被静默改写，兼容后端及失败时的降级仍保留。

| 选择顺序 | 模型 | 通信协议 | 正常听写链路 |
| --- | --- | --- | --- |
| 1 | Qwen Audio 3.1 ASR Flash Message（默认） | WebSocket / audio recognition，二进制 PCM | 双轨 `hybrid_asr`：原生口语顺滑、超级热词（`weight=50`）、多轮 `input.context` 与 `continue-task`；默认精简模式单级直出，开启指令/强润色/翻译时自动级联 `qwen3.8-flash` |
| 2 | Qwen Audio 3.1 ASR Flash Streaming | WebSocket / audio recognition，二进制 PCM | 低延迟流式 ASR，原生热词、上下文、标点，直接输出转写 |
| 3 | Qwen3.8 Omni Flash Realtime | WebSocket / Omni realtime，Base64 PCM + JPEG 帧 | 主模型直接输出语义听写；可选内联润色与“看屏幕说话” |
| 4 | Qwen Audio 3.1 Realtime Plus | WebSocket / realtime conversation，Base64 PCM | 按听写指令返回文本响应 |
| 5 | Qwen Audio 3.1 Realtime Flash | WebSocket / realtime conversation，Base64 PCM | 同上 |
| 6 | Qwen3.5 Omni Plus Realtime | WebSocket / Omni realtime，Base64 PCM | 同一会话内按指令输出润色文本 |
| 7 | Qwen3.5 Omni Flash Realtime | WebSocket / Omni realtime，Base64 PCM | 同上 |
| 8 | Qwen Audio 3.0 ASR Flash Streaming | WebSocket / audio recognition，二进制 PCM | 原生热词、上下文、标点，直接输出转写 |
| 9 | Qwen Audio 3.0 Realtime Plus | WebSocket / realtime conversation，Base64 PCM | 按听写指令返回文本响应 |
| 10 | Qwen Audio 3.0 Realtime Flash | WebSocket / realtime conversation，Base64 PCM | 同上 |

## 双轨路由与默认模型

- **默认听写模型**：`qwen-audio-3.1-asr-flash-message`（输入法旗舰），默认短音频离线降级走 `qwen-audio-3.1-asr-flash`（`qwen_audio_http`），默认文本润色模型为 `qwen3.8-flash`（可选 `qwen3.8-max`）。
- **双轨 `hybrid_asr`**：当用户在 `qwen-audio-3.1-asr-flash-message` 下使用默认的日常听写（`polish.mode == "dictation"`、轻量强度 `minimal`、未设置翻译/自定义语气人设/结构化输出/Prompt 覆写）时，直接开启服务端原生 `disfluency_removal_enabled=true` 单级直出，跳过二级 LLM 以获得最低延迟；一旦启用 Prompt 模式、跨语种翻译、`balanced`/`strong` 润色、结构化列表或自定义语气/提示词，则自动关闭服务端顺滑并级联第二级文本润色模型。
- **动态光标语境与跨轮记忆**：`cursor_context_enabled` 默认开启（密码/安全输入框和黑名单应用严格 fail-closed）。听写启动时并发读取聚焦应用名、窗口标题、选中文本和光标前后语境，将代码/技术标识符提升为即时超级热词（`weight=50`，上限 50 条，遵循非 ASCII ≤15 字 / ASCII ≤7 词边界），并保留同一应用 5 分钟内最近 3 轮听写记录写入 `input.context`；若 AX 快照晚于 WebSocket 握手返回，通过 `continue-task` 会话中热更新上下文。
- **低声 VAD 适配**：使用 `vad_model="near_meeting_16k"`，结合 `audio.speech_noise_threshold`（低声/安静场景偏低、嘈杂场景偏高）和原生 4 阶 Butterworth 高通 + 动态压缩/软限幅管线，提升办公室低声听写召回率。
- **看屏幕说话**：开启后仍自动路由至 `qwen3.8-omni-flash-realtime`（离线降级走 `qwen3.8-omni-flash`），携带压缩 JPEG 帧并复用会话级语音指令。

排序以专用输入法/实时 ASR 优先，其次是支持“看屏幕说话”的 Qwen3.8 Omni Flash Realtime 与 Qwen Audio 3.1 Realtime 系列，最后保留 3.5 Omni 与 3.0 Audio 兼容项。`qwen3.8-omni-plus-realtime` 尚未开放（WebSocket 握手后即被静默断开），未加入目录。官方提供的日期快照没有逐一重复列出。专用翻译、电话 8 kHz 与非实时文件模型不加入普通听写菜单。

Qwen3.8 Omni Flash Realtime 长音频离线降级走 `qwen3.8-omni-flash`（按目录后缀规则推导，HTTP 通道已实测可用）。费用按 2026-09-21 官方原价接入：realtime 输入音频 6 元/百万 Token（约为 3.5 Flash Realtime 的 22%），输出语音时音频及对应文本分别计费；离线版输入不分模态统一 0.8 元/百万 Token，纯文本输出。

Qwen3.8 的主模型现在始终承担最终听写：关闭“润色”时使用严格的忠实转写指令，开启时使用现有内联润色指令。Omni Realtime 会话按客户端事件协议将 `input_audio_transcription.model` 设为 `qwen3-asr-flash-realtime`；该旁路转写只用于流式反馈、诊断和恢复，不改变主模型的最终文本选择，也不会绕过官方强调的口音、非标准发音与语义联合建模。自动语言模式直接使用模型的 74 种语言和 39 种中文方言识别能力，不把设置页的“中文/英文”提示伪装成完整语言清单。

“看屏幕说话”是默认关闭的持久开关，可在设置页或菜单栏切换。开启后，每次新听写都会临时使用 Qwen3.8 Omni Flash Realtime，并把 macOS 主显示器压缩后的 JPEG 帧随听写音频发送；首帧与音频采集并行，Realtime 链路会等到首个音频块后再发送图像，后续每 2 秒更新一帧，230 秒后停止画面更新以留出官方 240 秒视频上限。关闭开关会停止当前会话后续的画面采集。图像仅用于理解界面文本、代码和专有名词，不允许模型描述屏幕；帧不写入录音历史或诊断日志。Qwen3.8 Realtime 需使用 Model Studio 工作空间 WSS 地址，在识别设置中配置。

模型的累计音频历史上限为 600 秒、100 轮，总输入上限为 196,608 Token。Rust 实时链路在 540 秒主动提交当前段并立即建立下一会话，新音频进入独立有界队列，最终按段序合并主模型文本、旁路文本和 Token 用量。本地仍只有一条连续录音；60 秒安全余量避免触碰服务端自动丢弃旧媒体的边界。一次听写即使长时间运行也不会复用其他听写的屏幕或对话历史。

`transport`、`protocol`、`pipeline` 分别表示传输后端、报文协议和输出处理方式。`supports_instant_hotwords` 与 `handles_inline_polish` 是独立能力。原生热词提高识别准确率，不能据此宣称模型支持任意指令润色。`native_asr` 和 `inline_generation` 都跳过听写完成阶段的独立文本 LLM；`cascade` 保留兼容实现但从菜单隐藏。

Qwen Audio ASR 的词典标准词和额外词条随请求写入 `vocabulary`，去重、最多 2000 条，权重按官方建议从 4 开始；别名仍由本地确定性替换处理。词典开关和额外词条读取本次录音配置快照。上下文通过 Recognition 的 `start(raw_input=...)` 传入，保留 400 字符限制。交互听写使用官方建议的低延迟 VAD 断句；自动标点保留。文本规范化是模型能力，不发送文档未列出的参数。纯 ASR 不支持 Prompt 写作、语气、翻译等指令润色，因此设置页禁用这些选项并说明可切换至 Omni / Audio Realtime。

Fun-ASR 虽支持预编译词表与部分版本的上下文，但现有集成未实现预编译词表资源管理，仍作为旧级联模型隐藏。Qwen3 ASR Realtime 官方能力表未列热词增强；它与离线模型一并隐藏。历史配置仍可运行，旧模型也可能在异常恢复时使用；正常选择升级后的模型不会追加第二级文本润色。

## 官方依据

[语音识别模型与推荐](https://help.aliyun.com/zh/model-studio/asr-model/)支持首选专用实时 ASR 的决策。[全模态模型目录](https://help.aliyun.com/zh/model-studio/omni/)提供 Omni Plus / Flash 顺序及可用实时模型。

[提升识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)说明即时热词、权重和上下文边界。[Recognition Python SDK](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk)规定 vocabulary、raw_input 和 VAD 参数位置。

[Qwen Audio 实时语音对话](https://help.aliyun.com/zh/model-studio/qwen-audio-realtime-user-guides)确认 Plus 与 Flash 使用同系列接口。[Flash 模型信息](https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-realtime-flash)提供北京区定价；已接入现有费用估算。[更新记录](https://help.aliyun.com/en/model-studio/newly-released-models)用于核对新增系列。

[Qwen3.8 Omni Flash Realtime](https://help.aliyun.com/zh/model-studio/qwen3-8-omni-flash-realtime)给出多语言/方言、音频/视频/Token 上限；[Realtime 客户端事件](https://help.aliyun.com/zh/model-studio/client-events)规定转写模型、图像必须为 JPEG、Base64 后不超过 256 KiB、发送图像前至少已有一个音频 append，以及图像随音频 commit 一起提交。

本地验证覆盖目录、协议参数、热词限制、单级完成流程、费用估算和设置页。单元测试模拟供应商响应，不代表已用真实音频验证线上识别质量或账户模型权限。
