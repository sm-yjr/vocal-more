# 听写模型与通信分类

核对日期：2026-09-21。`domain/model_catalog.py` 保存模型通信协议和输出能力；`ASR_MODEL_CATALOG` 是设置页、菜单、RPC 初始化和 Windows 共用的可选听写列表。`ALL_ASR_MODELS` 保留旧配置解析、历史录音与失败恢复需要的模型资料。此次下架范围是可选列表，旧配置不会被静默改写，兼容后端及失败时的降级仍保留。

| 选择顺序 | 模型 | 通信协议 | 正常听写链路 |
| --- | --- | --- | --- |
| 1 | Qwen3.8 Omni Flash Realtime | WebSocket / Omni realtime，Base64 PCM + JPEG 帧 | 主模型直接输出语义听写；可选润色与“看屏幕说话” |
| 2 | Qwen3.5 Omni Plus Realtime | WebSocket / Omni realtime，Base64 PCM | 同一会话内按指令输出润色文本 |
| 3 | Qwen3.5 Omni Flash Realtime | WebSocket / Omni realtime，Base64 PCM | 同上 |
| 4 | Qwen Audio 3.0 ASR Flash Streaming | WebSocket / audio recognition，二进制 PCM | 原生热词、上下文、标点，直接输出转写 |
| 5 | Qwen Audio 3.0 Realtime Plus | WebSocket / realtime conversation，Base64 PCM | 按听写指令返回文本响应 |
| 6 | Qwen Audio 3.0 Realtime Flash | WebSocket / realtime conversation，Base64 PCM | 同上 |

排序采用官方语音识别推荐作为听写入口，Omni 家族按官方最新一代优先、同代 Plus、Flash 顺序排列，然后是语音对话 Plus、Flash。这是结合听写场景的产品排序；官方没有提供跨 ASR、Omni、语音对话三类模型的统一排名。现有默认模型仍为 Qwen3.5 Omni Flash Realtime，避免改变新建配置的指令润色能力；Qwen3.8 Omni Flash Realtime 已于 2026-09-21 用真实音频端到端验证（转写完整准确），但服务端 `session.created` 默认 voice `Chelsie` 提交音频会被拒（`Voice 'Chelsie' is not supported`），必须显式 `voice="Tina"`——现有 `asr_engine` 对 Omni realtime 系列的默认值恰好是 Tina，无需适配。`qwen3.8-omni-plus-realtime` 尚未开放（WebSocket 握手后即被静默断开），未加入目录。官方提供的日期快照没有逐一重复列出。专用翻译、电话 8 kHz 与非实时文件模型不加入普通听写菜单。

Qwen3.8 Omni Flash Realtime 长音频离线降级走 `qwen3.8-omni-flash`（按目录后缀规则推导，HTTP 通道已实测可用）。费用按 2026-09-21 官方原价接入：realtime 输入音频 6 元/百万 Token（约为 3.5 Flash Realtime 的 22%），输出语音时音频及对应文本分别计费；离线版输入不分模态统一 0.8 元/百万 Token，纯文本输出。

Qwen3.8 的主模型现在始终承担最终听写：关闭“润色”时使用严格的忠实转写指令，开启时使用现有内联润色指令；`gummy-realtime-v1` 旁路结果只用于流式反馈、诊断和恢复，避免绕过官方强调的口音、非标准发音与语义联合建模。自动语言模式直接使用模型的 74 种语言和 39 种中文方言识别能力，不把设置页的“中文/英文”提示伪装成完整语言清单。

“看屏幕说话”是默认关闭的持久开关，可在设置页或菜单栏切换。开启后，每次新听写都会临时使用 Qwen3.8 Omni Flash Realtime，并把 macOS 主显示器压缩后的 JPEG 帧随听写音频发送；首帧与音频采集并行，Realtime 链路会等到首个音频块后再发送图像，后续每 2 秒更新一帧，230 秒后停止画面更新以留出官方 240 秒视频上限。关闭开关会停止当前会话后续的画面采集。图像仅用于理解界面文本、代码和专有名词，不允许模型描述屏幕；帧不写入录音历史或诊断日志。Qwen3.8 Realtime 需使用 Model Studio 工作空间 WSS 地址，在识别设置中配置。

模型的累计音频历史上限为 600 秒、100 轮，总输入上限为 196,608 Token。Rust 实时链路在 540 秒主动提交当前段并立即建立下一会话，新音频进入独立有界队列，最终按段序合并主模型文本、旁路文本和 Token 用量。本地仍只有一条连续录音；60 秒安全余量避免触碰服务端自动丢弃旧媒体的边界。一次听写即使长时间运行也不会复用其他听写的屏幕或对话历史。

`transport`、`protocol`、`pipeline` 分别表示传输后端、报文协议和输出处理方式。`supports_instant_hotwords` 与 `handles_inline_polish` 是独立能力。原生热词提高识别准确率，不能据此宣称模型支持任意指令润色。`native_asr` 和 `inline_generation` 都跳过听写完成阶段的独立文本 LLM；`cascade` 保留兼容实现但从菜单隐藏。

Qwen Audio ASR 的词典标准词和额外词条随请求写入 `vocabulary`，去重、最多 2000 条，权重按官方建议从 4 开始；别名仍由本地确定性替换处理。词典开关和额外词条读取本次录音配置快照。上下文通过 Recognition 的 `start(raw_input=...)` 传入，保留 400 字符限制。交互听写使用官方建议的低延迟 VAD 断句；自动标点保留。文本规范化是模型能力，不发送文档未列出的参数。纯 ASR 不支持 Prompt 写作、语气、翻译等指令润色，因此设置页禁用这些选项并说明可切换至 Omni / Audio Realtime。

Fun-ASR 虽支持预编译词表与部分版本的上下文，但现有集成未实现预编译词表资源管理，仍作为旧级联模型隐藏。Qwen3 ASR Realtime 官方能力表未列热词增强；它与离线模型一并隐藏。历史配置仍可运行，旧模型也可能在异常恢复时使用；正常选择升级后的模型不会追加第二级文本润色。

## 官方依据

[语音识别模型与推荐](https://help.aliyun.com/zh/model-studio/asr-model/)支持首选专用实时 ASR 的决策。[全模态模型目录](https://help.aliyun.com/zh/model-studio/omni/)提供 Omni Plus / Flash 顺序及可用实时模型。

[提升识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)说明即时热词、权重和上下文边界。[Recognition Python SDK](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk)规定 vocabulary、raw_input 和 VAD 参数位置。

[Qwen Audio 实时语音对话](https://help.aliyun.com/zh/model-studio/qwen-audio-realtime-user-guides)确认 Plus 与 Flash 使用同系列接口。[Flash 模型信息](https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-realtime-flash)提供北京区定价；已接入现有费用估算。[更新记录](https://help.aliyun.com/en/model-studio/newly-released-models)用于核对新增系列。

[Qwen3.8 Omni Flash Realtime](https://help.aliyun.com/zh/model-studio/qwen3-8-omni-flash-realtime)给出多语言/方言、音频/视频/Token 上限；[Realtime 客户端事件](https://help.aliyun.com/zh/model-studio/client-events)规定图像必须为 JPEG、Base64 后不超过 256 KiB、发送图像前至少已有一个音频 append，以及图像随音频 commit 一起提交。

本地验证覆盖目录、协议参数、热词限制、单级完成流程、费用估算和设置页。单元测试模拟供应商响应，不代表已用真实音频验证线上识别质量或账户模型权限。
