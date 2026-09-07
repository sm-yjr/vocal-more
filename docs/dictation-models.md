# 听写模型与通信分类

核对日期：2026-09-07。`domain/model_catalog.py` 保存模型通信协议和输出能力；`ASR_MODEL_CATALOG` 是设置页、菜单、RPC 初始化和 Windows 共用的可选听写列表。`ALL_ASR_MODELS` 保留旧配置解析、历史录音与失败恢复需要的模型资料。此次下架范围是可选列表，旧配置不会被静默改写，兼容后端及失败时的降级仍保留。

| 选择顺序 | 模型 | 通信协议 | 正常听写链路 |
| --- | --- | --- | --- |
| 1 | Qwen Audio 3.0 ASR Flash Streaming | WebSocket / audio recognition，二进制 PCM | 原生热词、上下文、标点，直接输出转写 |
| 2 | Qwen3.5 Omni Plus Realtime | WebSocket / Omni realtime，Base64 PCM | 同一会话内按指令输出润色文本 |
| 3 | Qwen3.5 Omni Flash Realtime | WebSocket / Omni realtime，Base64 PCM | 同上 |
| 4 | Qwen Audio 3.0 Realtime Plus | WebSocket / realtime conversation，Base64 PCM | 按听写指令返回文本响应 |
| 5 | Qwen Audio 3.0 Realtime Flash | WebSocket / realtime conversation，Base64 PCM | 同上，本次新增 |

排序采用官方语音识别推荐作为听写入口，Omni 家族按官方 Plus、Flash 顺序排列，然后是语音对话 Plus、Flash。这是结合听写场景的产品排序；官方没有提供跨 ASR、Omni、语音对话三类模型的统一排名。现有默认模型仍为 Omni Flash Realtime，避免改变新建配置的指令润色能力。官方提供的日期快照没有逐一重复列出；此次核对未发现比现有 Qwen3.5 Omni 家族更新的通用 Omni 家族。专用翻译、电话 8 kHz 与非实时文件模型不加入普通听写菜单。

`transport`、`protocol`、`pipeline` 分别表示传输后端、报文协议和输出处理方式。`supports_instant_hotwords` 与 `handles_inline_polish` 是独立能力。原生热词提高识别准确率，不能据此宣称模型支持任意指令润色。`native_asr` 和 `inline_generation` 都跳过听写完成阶段的独立文本 LLM；`cascade` 保留兼容实现但从菜单隐藏。

Qwen Audio ASR 的词典标准词和额外词条随请求写入 `vocabulary`，去重、最多 2000 条，权重按官方建议从 4 开始；别名仍由本地确定性替换处理。词典开关和额外词条读取本次录音配置快照。上下文通过 Recognition 的 `start(raw_input=...)` 传入，保留 400 字符限制。交互听写使用官方建议的低延迟 VAD 断句；自动标点保留。文本规范化是模型能力，不发送文档未列出的参数。纯 ASR 不支持 Prompt 写作、语气、翻译等指令润色，因此设置页禁用这些选项并说明可切换至 Omni / Audio Realtime。

Fun-ASR 虽支持预编译词表与部分版本的上下文，但现有集成未实现预编译词表资源管理，仍作为旧级联模型隐藏。Qwen3 ASR Realtime 官方能力表未列热词增强；它与离线模型一并隐藏。历史配置仍可运行，旧模型也可能在异常恢复时使用；正常选择升级后的模型不会追加第二级文本润色。

## 官方依据

[语音识别模型与推荐](https://help.aliyun.com/zh/model-studio/asr-model/)支持首选专用实时 ASR 的决策。[全模态模型目录](https://help.aliyun.com/zh/model-studio/omni/)提供 Omni Plus / Flash 顺序及可用实时模型。

[提升识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)说明即时热词、权重和上下文边界。[Recognition Python SDK](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk)规定 vocabulary、raw_input 和 VAD 参数位置。

[Qwen Audio 实时语音对话](https://help.aliyun.com/zh/model-studio/qwen-audio-realtime-user-guides)确认 Plus 与 Flash 使用同系列接口。[Flash 模型信息](https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-realtime-flash)提供北京区定价；已接入现有费用估算。[更新记录](https://help.aliyun.com/en/model-studio/newly-released-models)用于核对新增系列。

本地验证覆盖目录、协议参数、热词限制、单级完成流程、费用估算和设置页。单元测试模拟供应商响应，不代表已用真实音频验证线上识别质量或账户模型权限。
