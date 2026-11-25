// SPDX-License-Identifier: GPL-3.0-only
//! The editable surface is explicit, independent from its visual arrangement.
//! Backend validation remains authoritative; this schema adds early UI feedback.
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tab {
    Home,
    History,
    Dictionary,
    Audio,
    Ai,
    Shortcuts,
    General,
}
impl Tab {
    pub const WORKSPACE: [Self; 3] = [Self::Home, Self::History, Self::Dictionary];
    pub const SETTINGS: [Self; 4] = [Self::Audio, Self::Ai, Self::Shortcuts, Self::General];
    pub const ALL: [Self; 7] = [
        Self::Home,
        Self::History,
        Self::Dictionary,
        Self::Audio,
        Self::Ai,
        Self::Shortcuts,
        Self::General,
    ];
    pub fn id(self) -> &'static str {
        match self {
            Self::Home => "home",
            Self::History => "history",
            Self::Dictionary => "dictionary",
            Self::Audio => "audio",
            Self::Ai => "ai",
            Self::Shortcuts => "shortcuts",
            Self::General => "general",
        }
    }
    pub fn title(self, english: bool) -> &'static str {
        match (self, english) {
            (Self::Home, false) => "概览",
            (Self::History, false) => "录音历史",
            (Self::Dictionary, false) => "个人词典",
            (Self::Audio, false) => "声音与低声",
            (Self::Ai, false) => "识别与润色",
            (Self::Shortcuts, false) => "快捷键与输入",
            (Self::General, false) => "通用与连接",
            (Self::Home, true) => "Home",
            (Self::History, true) => "History",
            (Self::Dictionary, true) => "Dictionary",
            (Self::Audio, true) => "Audio & Whisper",
            (Self::Ai, true) => "AI & Output",
            (Self::Shortcuts, true) => "Shortcuts & Input",
            (Self::General, true) => "General",
        }
    }
    pub fn subtitle(self, english: bool) -> &'static str {
        match (self, english) {
            (Self::Home, false) => "系统就绪状态、常用快捷切换与最近听写记录",
            (Self::History, false) => "搜索、回放、重新识别并管理本地历史录音",
            (Self::Dictionary, false) => "审核自动学习建议并维护专有名词表",
            (Self::Audio, false) => "为办公室低声或日常环境选择拾音方案并试音校准",
            (Self::Ai, false) => "配置识别模型、自定义听写场景、看屏幕说话与文本润色风格",
            (Self::Shortcuts, false) => "自定义全局触发键、录音手势与自动粘贴行为",
            (Self::General, false) => "服务密钥、网络代理、悬浮胶囊位置与应用偏好",
            (Self::Home, true) => "Readiness status, quick toggles, and recent dictations",
            (Self::History, true) => "Search, replay, retry, and manage local recordings",
            (Self::Dictionary, true) => "Review learned suggestions and manage custom terms",
            (Self::Audio, true) => "Tailor pickup for office whispering or everyday speech",
            (Self::Ai, true) => {
                "Configure ASR models, dictation scenes, screen context, and polish styles"
            }
            (Self::Shortcuts, true) => {
                "Customize trigger hotkeys, recording gestures, and paste behavior"
            }
            (Self::General, true) => "API key, proxy, capsule placement, and app preferences",
        }
    }
    pub fn from_id(value: &str) -> Self {
        match value {
            "" | "home" => Self::Home,
            "recognition" | "polish" | "ai" => Self::Ai,
            other => Self::ALL
                .into_iter()
                .find(|tab| tab.id() == other)
                .unwrap_or(Self::Home),
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub enum Kind {
    Toggle,
    Text,
    Secret,
    List,
    Choice(&'static [(&'static str, &'static str, &'static str)]),
    Model,
    Device,
    Slider {
        min: f32,
        max: f32,
        step: f32,
        unit: &'static str,
        gain_db: bool,
    },
}
#[derive(Clone, Copy, Debug)]
pub struct Field {
    pub key: &'static str,
    pub tab: Tab,
    pub zh: &'static str,
    pub en: &'static str,
    pub hint_zh: &'static str,
    pub hint_en: &'static str,
    pub advanced: bool,
    pub kind: Kind,
}
impl Field {
    pub fn title(self, en: bool) -> &'static str {
        if en { self.en } else { self.zh }
    }
    pub fn hint(self, en: bool) -> &'static str {
        if en { self.hint_en } else { self.hint_zh }
    }
}
macro_rules! field {
    ($key:literal,$tab:ident,$zh:literal,$en:literal,$advanced:literal,$kind:expr,$hintzh:literal,$hinten:literal) => {
        Field {
            key: $key,
            tab: Tab::$tab,
            zh: $zh,
            en: $en,
            advanced: $advanced,
            kind: $kind,
            hint_zh: $hintzh,
            hint_en: $hinten,
        }
    };
}
pub const FIELDS: &[Field] = &[
    field!(
        "api_key",
        General,
        "DashScope API Key",
        "DashScope API Key",
        false,
        Kind::Secret,
        "仅在本机明确点击显示时读取已保存的密钥。",
        "Read the saved key only when you explicitly choose Show."
    ),
    field!(
        "network.proxy_url",
        General,
        "网络代理",
        "Network proxy",
        false,
        Kind::Text,
        "留空为直连；支持 http://host:port 和 socks5://host:port。",
        "Leave blank for direct access; supports http://host:port and socks5://host:port."
    ),
    field!(
        "ui.capsule_position",
        General,
        "悬浮胶囊位置",
        "Floating capsule position",
        false,
        Kind::Choice(&[
            ("bottom", "底部居中", "Bottom center"),
            ("top", "顶部居中", "Top center"),
            ("custom", "记住拖拽位置", "Remember dragged")
        ]),
        "选择胶囊默认停靠位置；在免提录音时可直接拖动胶囊调整。",
        "Choose where the capsule appears; drag it during hands-free recording to customize."
    ),
    field!(
        "ui.language",
        General,
        "界面语言",
        "Interface language",
        false,
        Kind::Choice(&[("zh", "简体中文", "简体中文"), ("en", "English", "English")]),
        "立即切换菜单栏、悬浮胶囊与主窗口语言。",
        "Immediately switch language across the menu bar, capsule, and main window."
    ),
    field!(
        "update_channel",
        General,
        "更新通道",
        "Update channel",
        false,
        Kind::Choice(&[("stable", "稳定版", "Stable"), ("beta", "Beta", "Beta")]),
        "选择接收稳定版或 Beta 预发布更新。",
        "Choose whether to receive stable or Beta prerelease updates."
    ),
    field!(
        "ui.advanced_settings",
        General,
        "默认展开高级参数",
        "Expand advanced sections",
        false,
        Kind::Toggle,
        "打开各设置页时默认展开所有工程参数与诊断面板。",
        "Keep advanced technical panels expanded by default across settings pages."
    ),
    field!(
        "default_mode",
        Shortcuts,
        "默认录音模式",
        "Default recording mode",
        false,
        Kind::Choice(&[
            ("walkie_talkie", "按住说话", "Push to talk"),
            ("realtime_long", "免提长录音", "Hands free")
        ]),
        "短按并松开进入免提；按住说话，松开结束。",
        "Tap to latch hands free; hold to speak and release to finish."
    ),
    field!(
        "auto_paste",
        Shortcuts,
        "自动输入识别结果",
        "Automatically paste results",
        false,
        Kind::Toggle,
        "完成识别后输入到原来聚焦的应用。",
        "Insert the completed result into the previously focused application."
    ),
    field!(
        "streaming_paste",
        Shortcuts,
        "边说边输入",
        "Streaming paste",
        false,
        Kind::Toggle,
        "在录音过程中输入实时识别结果。",
        "Insert interim recognition while recording."
    ),
    field!(
        "native_fast_paste",
        Shortcuts,
        "原生快速输入",
        "Native fast paste",
        true,
        Kind::Toggle,
        "在支持的应用中优先使用原生输入路径。",
        "Prefer native input in supported applications."
    ),
    field!(
        "restore_clipboard",
        Shortcuts,
        "恢复原剪贴板",
        "Restore clipboard",
        true,
        Kind::Toggle,
        "粘贴后恢复此前的剪贴板内容。",
        "Restore the previous clipboard content after pasting."
    ),
    field!(
        "audio.input_device",
        Audio,
        "输入设备",
        "Input device",
        false,
        Kind::Device,
        "选择系统默认设备或指定麦克风。",
        "Use the system default or select a microphone."
    ),
    field!(
        "audio.capture_backend",
        Audio,
        "采集模式",
        "Capture backend",
        true,
        Kind::Choice(&[
            (
                "low_latency",
                "低延迟原生采集",
                "Low latency native capture"
            ),
            (
                "voice_processing",
                "Apple 语音处理",
                "Apple Voice Processing"
            )
        ]),
        "语音处理的实际状态和回退原因显示在运行状态中。",
        "Runtime status reports actual processing and any fallback."
    ),
    field!(
        "audio.gain_mode",
        Audio,
        "增益控制",
        "Gain control",
        true,
        Kind::Choice(&[
            ("automatic", "自动增益", "Automatic gain"),
            ("manual", "手动软件增益", "Manual software gain")
        ]),
        "低声输入可采用手动增益与高通滤波。",
        "Manual gain with a high-pass filter helps low-voice input."
    ),
    field!(
        "audio.gain",
        Audio,
        "软件增益",
        "Software gain",
        true,
        Kind::Slider {
            min: -6.,
            max: 34.,
            step: 1.,
            unit: "dB",
            gain_db: true
        },
        "提高低声输入音量；避免将环境噪声放大过多。",
        "Raise low-voice input while avoiding excessive room noise."
    ),
    field!(
        "audio.waveform_ceiling_dbfs",
        Audio,
        "波形标定",
        "Waveform calibration",
        true,
        Kind::Slider {
            min: -30.,
            max: 0.,
            step: 1.,
            unit: "dBFS",
            gain_db: false
        },
        "只调整胶囊波形显示的满幅标尺。",
        "Adjusts the capsule waveform scale."
    ),
    field!(
        "audio.highpass_filter",
        Audio,
        "高通滤波",
        "High-pass filter",
        true,
        Kind::Toggle,
        "削减风扇、空调和桌面低频振动。",
        "Reduce fan, air-conditioning and low-frequency rumble."
    ),
    field!(
        "audio.highpass_freq",
        Audio,
        "高通截止频率",
        "High-pass cutoff",
        true,
        Kind::Slider {
            min: 50.,
            max: 500.,
            step: 10.,
            unit: "Hz",
            gain_db: false
        },
        "过滤该频率以下的环境低频轰鸣。",
        "Cuts low-frequency ambient rumble below this cutoff."
    ),
    field!(
        "audio.soft_limiter",
        Audio,
        "动态压缩与软限幅",
        "Dynamics & soft limiter",
        true,
        Kind::Toggle,
        "提高增益时平滑压限峰值并保护低声动态，减少削波失真。",
        "Smoothly compress peaks and protect low-voice dynamics at high gain."
    ),
    field!(
        "audio.tail_padding_ms",
        Audio,
        "松键尾音宽限",
        "Release tail padding",
        true,
        Kind::Slider {
            min: 0.,
            max: 500.,
            step: 25.,
            unit: "ms",
            gain_db: false
        },
        "松开快捷键后继续采集尾音的时长，避免吞掉句末最后一个字。",
        "Keep capturing briefly after key release to prevent clipped final syllables."
    ),
    field!(
        "asr.model",
        Ai,
        "识别模型",
        "Recognition model",
        false,
        Kind::Model,
        "模型列表和能力来自后端产品契约。",
        "Models and capabilities come from the backend product contract."
    ),
    field!(
        "asr.language",
        Ai,
        "识别语言",
        "Recognition language",
        false,
        Kind::Choice(&[
            ("auto", "自动识别", "Automatic"),
            ("zh", "中文", "Chinese"),
            ("en", "英语", "English")
        ]),
        "保持自动识别可流畅支持中英双语混说。",
        "Automatic works best for bilingual Chinese and English dictation."
    ),
    field!(
        "cursor_context_enabled",
        Ai,
        "光标上下文与动态热词",
        "Cursor context & dynamic hotwords",
        false,
        Kind::Toggle,
        "开始听写时读取光标前后文、选中文本和窗口标题以提升术语和同音词准确率；自动跳过密码框与排除应用。",
        "Read surrounding text, selection, and window title at dictation start for better terms; skips password fields and excluded apps."
    ),
    field!(
        "screen_context_enabled",
        Ai,
        "看屏幕说话",
        "Screen context",
        false,
        Kind::Toggle,
        "屏幕内容用于辅助识别专有名词和代码，不会成为口述文本；需要兼容模型和空间端点。",
        "Screen content assists recognition and does not become dictated text; requires a compatible model and workspace endpoint."
    ),
    field!(
        "asr.realtime_url",
        Ai,
        "空间实时端点",
        "Workspace realtime endpoint",
        true,
        Kind::Text,
        "留空使用公共端点；空间地址必须为 wss://…maas.aliyuncs.com/api-ws/v1/realtime。",
        "Leave blank for the public endpoint; workspace URLs must use wss://…maas.aliyuncs.com/api-ws/v1/realtime."
    ),
    field!(
        "enable_polish",
        Ai,
        "启用润色",
        "Enable polish",
        false,
        Kind::Toggle,
        "原生 ASR 模型直接转写，跳过第二阶段润色。",
        "Native ASR transcribes directly and skips second-stage polish."
    ),
    field!(
        "llm.polish_mode",
        Ai,
        "输出类型",
        "Output type",
        false,
        Kind::Choice(&[
            ("dictation", "口述文本", "Dictation"),
            ("prompt", "提示词", "Prompt")
        ]),
        "选择常规文本输出或结构化 AI 提示词模式。",
        "Choose general dictation output or AI prompt mode."
    ),
    field!(
        "llm.output_language",
        Ai,
        "输出语言",
        "Output language",
        false,
        Kind::Choice(&[
            ("auto", "保留原语言", "Preserve original"),
            ("zh", "中文", "Chinese"),
            ("en", "英语", "English")
        ]),
        "可在润色时保持原语言或统一输出为中文/英文。",
        "Keep spoken language or output consistently in Chinese or English."
    ),
    field!(
        "llm.level",
        Ai,
        "润色程度",
        "Polish level",
        false,
        Kind::Choice(&[
            ("minimal", "轻度", "Minimal"),
            ("balanced", "适中", "Balanced"),
            ("strong", "深度", "Strong")
        ]),
        "控制清理口语词与改写句式的力度。",
        "Controls how strongly filler words and phrasing are refined."
    ),
    field!(
        "llm.structured",
        Ai,
        "结构化输出",
        "Structured output",
        false,
        Kind::Toggle,
        "按语义组织段落与列表。",
        "Organize paragraphs and lists by meaning."
    ),
    field!(
        "llm.tone",
        Ai,
        "表达语气",
        "Tone",
        false,
        Kind::Choice(&[
            ("neutral", "自然", "Neutral"),
            ("gentle", "温和", "Gentle"),
            ("direct", "直接", "Direct")
        ]),
        "调整成文的情感色彩与直接程度。",
        "Adjust the politeness and directness of polished text."
    ),
    field!(
        "llm.persona",
        Ai,
        "写作风格",
        "Persona",
        false,
        Kind::Choice(&[
            ("default", "默认", "Default"),
            ("technical", "技术", "Technical"),
            ("bilingual", "双语", "Bilingual"),
            ("professional", "专业", "Professional"),
            ("chat", "聊天", "Chat")
        ]),
        "匹配代码文档、商务沟通或日常聊天习惯。",
        "Match technical docs, professional writing, or casual chat."
    ),
    field!(
        "llm.model",
        Ai,
        "润色模型",
        "Polish model",
        true,
        Kind::Model,
        "用于第二阶段文本润色的大语言模型。",
        "Language model used for second-stage text polishing."
    ),
    field!(
        "llm.temperature",
        Ai,
        "生成温度",
        "Temperature",
        true,
        Kind::Slider {
            min: 0.,
            max: 2.,
            step: 0.1,
            unit: "",
            gain_db: false
        },
        "较低温度输出更稳定克制，较高温度更具表达变化。",
        "Lower values keep output faithful; higher values add variety."
    ),
    field!(
        "llm.enable_thinking",
        Ai,
        "深度思考",
        "Thinking",
        true,
        Kind::Toggle,
        "仅适用于支持深度思考的模型。",
        "Available only for models that support thinking."
    ),
    field!(
        "hotkey.double_tap_threshold",
        Shortcuts,
        "双击间隔",
        "Double-tap interval",
        true,
        Kind::Slider {
            min: 0.1,
            max: 0.8,
            step: 0.05,
            unit: "s",
            gain_db: false
        },
        "用于判定修饰键双击手势的时间窗口。",
        "Time window used to detect modifier double-tap gestures."
    ),
    field!(
        "dictionary_learning.enabled",
        Dictionary,
        "自动学习词典",
        "Automatic dictionary learning",
        false,
        Kind::Toggle,
        "仅在本机观察输入后的修正；排除敏感应用。",
        "Observe corrections locally; exclude sensitive applications."
    ),
    field!(
        "dictionary_learning.excluded_bundle_ids",
        Dictionary,
        "排除的应用",
        "Excluded applications",
        true,
        Kind::List,
        "用逗号分隔应用 Bundle ID，失焦或按回车保存。",
        "Separate application bundle IDs with commas; saves on blur or Enter."
    ),
];

/// Visual grouping for fields inside each tab; every field belongs to exactly one section.
pub const SECTIONS: &[(Tab, &str, &str, &[&str])] = &[
    (
        Tab::General,
        "语音服务与网络连接",
        "Speech Service & Network",
        &["api_key", "network.proxy_url"],
    ),
    (
        Tab::General,
        "外观与交互偏好",
        "Appearance & Experience",
        &["ui.capsule_position", "ui.language", "update_channel"],
    ),
    (
        Tab::General,
        "应用与维护",
        "Application & Maintenance",
        &["ui.advanced_settings"],
    ),
    (Tab::Audio, "麦克风", "Microphone", &["audio.input_device"]),
    (
        Tab::Audio,
        "高级音频参数",
        "Advanced Audio DSP",
        &[
            "audio.capture_backend",
            "audio.gain_mode",
            "audio.gain",
            "audio.highpass_filter",
            "audio.highpass_freq",
            "audio.soft_limiter",
            "audio.tail_padding_ms",
            "audio.waveform_ceiling_dbfs",
        ],
    ),
    (
        Tab::Ai,
        "语音识别",
        "Speech Recognition",
        &[
            "asr.model",
            "asr.language",
            "cursor_context_enabled",
            "screen_context_enabled",
        ],
    ),
    (
        Tab::Ai,
        "文本润色与输出风格",
        "Text Polish & Output Style",
        &[
            "enable_polish",
            "llm.polish_mode",
            "llm.output_language",
            "llm.level",
            "llm.structured",
            "llm.tone",
            "llm.persona",
        ],
    ),
    (
        Tab::Ai,
        "高级模型端点与参数",
        "Workspace Endpoint & LLM",
        &[
            "asr.realtime_url",
            "llm.model",
            "llm.temperature",
            "llm.enable_thinking",
        ],
    ),
    (
        Tab::Shortcuts,
        "录音模式与文本输入",
        "Recording Mode & Text Insertion",
        &["default_mode", "auto_paste", "streaming_paste"],
    ),
    (
        Tab::Shortcuts,
        "高级输入与热键参数",
        "Advanced Input & Hotkey Tuning",
        &[
            "hotkey.double_tap_threshold",
            "native_fast_paste",
            "restore_clipboard",
        ],
    ),
    (
        Tab::Dictionary,
        "自动学习",
        "Automatic Learning",
        &[
            "dictionary_learning.enabled",
            "dictionary_learning.excluded_bundle_ids",
        ],
    ),
];

/// Composite controls and workflow-owned persisted values are listed separately.
pub const COMPOSITE_KEYS: &[&str] = &[
    "hotkey.active_hotkeys",
    "hotkey.custom_key",
    "hotkey.custom_keys",
    "llm.prompt_overrides",
    "ui.onboarding_completed",
    "ui.onboarding_skipped",
    "asr.backend",
];
pub const PROMPT_CATEGORIES: &[(&str, &str, &str)] = &[
    ("output_type", "输出类型", "Output type"),
    ("level", "润色程度", "Level"),
    ("structured", "结构化", "Structure"),
    ("tone", "语气", "Tone"),
    ("persona", "风格", "Persona"),
];
pub const ACTIONS: &[&str] = &[
    "revealApiKey",
    "checkDashScopeModels",
    "openExternal",
    "openConfigFile",
    "refreshDevices",
    "refreshEnvironment",
    "openAccessibilitySettings",
    "openMicrophoneSettings",
    "setAsrModel",
    "setDevice",
    "setActiveHotkeys",
    "startMicTest",
    "stopMicTest",
    "playMicTest",
    "addDictEntry",
    "removeDictEntry",
    "openDictFile",
    "approveDictionaryLearning",
    "rejectDictionaryLearning",
    "undoDictionaryLearning",
    "getRecordings",
    "retryTranscription",
    "deleteRecording",
    "playRecording",
    "stopRecording",
    "copyTranscript",
    "compactRecordingHistory",
];

pub fn get<'a>(config: &'a Value, key: &str) -> &'a Value {
    key.split('.').fold(config, |value, part| &value[part])
}
pub fn put(config: &mut Value, key: &str, value: Value) {
    let mut target = config;
    let mut parts = key.split('.').peekable();
    while let Some(part) = parts.next() {
        if !target.is_object() {
            *target = json!({});
        }
        if parts.peek().is_none() {
            target[part] = value;
            return;
        }
        target = target
            .as_object_mut()
            .unwrap()
            .entry(part)
            .or_insert(json!({}));
    }
}
pub fn list_from_text(value: &str) -> Value {
    json!(
        value
            .split([',', '，', '\n'])
            .map(str::trim)
            .filter(|v| !v.is_empty())
            .collect::<Vec<_>>()
    )
}
pub fn display_value(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(v) => v.clone(),
        Value::Array(a) => a
            .iter()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>()
            .join(", "),
        _ => value.to_string(),
    }
}
pub fn valid_proxy(value: &str) -> bool {
    if value.is_empty() {
        return true;
    }
    let Some((scheme, rest)) = value.split_once("://") else {
        return false;
    };
    if !["http", "socks5"].contains(&scheme.to_ascii_lowercase().as_str()) {
        return false;
    }
    let rest = rest.strip_suffix('/').unwrap_or(rest);
    if rest.contains(['@', '?', '#', '/', ' ']) {
        return false;
    }
    let Some((host, port)) = rest.rsplit_once(':') else {
        return false;
    };
    !host.is_empty()
        && (!host.contains(':') || (host.starts_with('[') && host.ends_with(']')))
        && port.parse::<u16>().is_ok_and(|port| port > 0)
}
pub fn valid_endpoint(value: &str) -> bool {
    if value.is_empty() {
        return true;
    }
    let Some((scheme, rest)) = value.split_once("://") else {
        return false;
    };
    if !scheme.eq_ignore_ascii_case("wss") {
        return false;
    }
    let Some(host) = rest
        .strip_suffix("/api-ws/v1/realtime")
        .or_else(|| rest.strip_suffix("/api-ws/v1/realtime/"))
    else {
        return false;
    };
    host.len() > ".maas.aliyuncs.com".len()
        && host.to_ascii_lowercase().ends_with(".maas.aliyuncs.com")
        && host
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
}
/// Errors carry Chinese and English text so the UI can follow its language.
pub fn validate(key: &str, value: &Value) -> Result<(), (&'static str, &'static str)> {
    if key == "network.proxy_url" && !valid_proxy(value.as_str().unwrap_or("")) {
        return Err((
            "代理地址格式不正确，请使用 http://主机:端口 或 socks5://主机:端口",
            "Invalid proxy URL; use http://host:port or socks5://host:port",
        ));
    }
    if key == "asr.realtime_url" && !valid_endpoint(value.as_str().unwrap_or("")) {
        return Err((
            "业务空间端点格式不正确，应为 wss://…maas.aliyuncs.com/api-ws/v1/realtime",
            "Invalid workspace endpoint",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_field_belongs_to_exactly_one_section_on_its_tab() {
        for field in FIELDS {
            let homes = SECTIONS
                .iter()
                .filter(|(_, _, _, keys)| keys.contains(&field.key))
                .collect::<Vec<_>>();
            assert_eq!(homes.len(), 1, "{} must be in one section", field.key);
            assert_eq!(
                homes[0].0, field.tab,
                "{} section is on another tab",
                field.key
            );
        }
        for (_, _, _, keys) in SECTIONS {
            for key in *keys {
                assert!(FIELDS.iter().any(|field| field.key == *key), "{key}");
            }
        }
    }
    #[test]
    fn nested_updates_preserve_unrelated_data() {
        let mut config =
            json!({"llm":{"unknown":{"future":true},"tone":"neutral"},"audio":{"gain":2}});
        put(&mut config, "llm.tone", json!("direct"));
        assert_eq!(get(&config, "llm.tone"), "direct");
        assert_eq!(get(&config, "llm.unknown.future"), true);
        assert_eq!(get(&config, "audio.gain"), 2);
    }
    #[test]
    fn legacy_tab_ids_resolve_to_redesigned_destinations() {
        assert_eq!(Tab::from_id(""), Tab::Home);
        assert_eq!(Tab::from_id("home"), Tab::Home);
        assert_eq!(Tab::from_id("recognition"), Tab::Ai);
        assert_eq!(Tab::from_id("polish"), Tab::Ai);
        assert_eq!(Tab::from_id("ai"), Tab::Ai);
        assert_eq!(Tab::from_id("general"), Tab::General);
        assert_eq!(Tab::from_id("unknown-future-tab"), Tab::Home);
    }
    #[test]
    fn endpoint_and_proxy_validation() {
        for value in ["", "http://127.0.0.1:7890", "socks5://[::1]:1080"] {
            assert!(valid_proxy(value), "{value}");
        }
        for value in [
            "http://host:0",
            "http://host:65536",
            "https://host:80",
            "http://user:password@host:80",
            "http://host:80/path",
        ] {
            assert!(!valid_proxy(value), "{value}");
        }
        assert!(valid_endpoint(
            "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        ));
        assert!(!valid_endpoint(
            "wss://maas.aliyuncs.com.evil.example/api-ws/v1/realtime"
        ));
    }
    /// Extract actual persisted keys from the existing TypeScript form model,
    /// then verify that every key is mapped to a native control or composite.
    /// This fails when a new old-UI option is added without migration coverage.
    #[test]
    fn native_controls_cover_existing_form_contract() {
        let source = include_str!("../../../../../frontend/settings/src/settings/types.ts");
        let form = source
            .split("export interface FormState {")
            .nth(1)
            .unwrap()
            .split("export type SettingsMessage")
            .next()
            .unwrap();
        let mut section = None::<String>;
        let mut keys = Vec::new();
        for line in form.lines() {
            let indentation = line.len() - line.trim_start().len();
            let line = line.trim();
            if indentation == 2 && line.ends_with('{') {
                section = Some(line.split(':').next().unwrap().to_string());
                continue;
            }
            if indentation == 2 && line == "}" {
                section = None;
                continue;
            }
            if !(indentation == 2 || indentation == 4) {
                continue;
            }
            let Some((key, _)) = line.split_once(':') else {
                continue;
            };
            if !key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
                continue;
            }
            keys.push(if indentation == 4 {
                format!("{}.{}", section.as_deref().unwrap(), key)
            } else {
                key.to_string()
            });
        }
        assert!(keys.len() > 30, "form parser must cover nested options");
        let missing = keys
            .iter()
            .filter(|key| {
                !FIELDS.iter().any(|field| field.key == key.as_str())
                    && !COMPOSITE_KEYS.contains(&key.as_str())
            })
            .collect::<Vec<_>>();
        assert!(missing.is_empty(), "Missing native controls: {missing:?}");
    }
    #[test]
    fn native_actions_cover_existing_frontend_workflows() {
        let sources = [
            include_str!(
                "../../../../../frontend/settings/src/components/settings/general-settings.tsx"
            ),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/audio-settings.tsx"
            ),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/recognition-settings.tsx"
            ),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/polish-settings.tsx"
            ),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/shortcuts-settings.tsx"
            ),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/dictionary-settings.tsx"
            ),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/history-settings.tsx"
            ),
            include_str!("../../../../../frontend/settings/src/components/settings/onboarding.tsx"),
            include_str!(
                "../../../../../frontend/settings/src/components/settings/whisper-calibration-wizard.tsx"
            ),
        ];
        let mut count = 0;
        for source in sources {
            for suffix in source.split("sendAction(\"").skip(1) {
                let action = suffix.split('"').next().unwrap();
                assert!(
                    ACTIONS.contains(&action),
                    "Missing migrated action: {action}"
                );
                count += 1;
            }
        }
        assert!(
            count > 25,
            "action parser must cover the existing workflow buttons"
        );
    }
    #[test]
    fn every_action_has_an_authoritative_backend_or_native_route() {
        let source = include_str!("../../../../../rust/crates/backend/src/application.rs");
        let registry = source.split("fn ui_method(action: &str)").nth(1).unwrap();
        let aliases = registry
            .lines()
            .filter_map(|line| {
                let line = line.trim();
                if !line.starts_with('"') || !line.contains(" => ") {
                    return None;
                }
                line[1..].split('"').next()
            })
            .collect::<Vec<_>>();
        assert!(
            aliases.len() > 25,
            "backend registry parser must see the full settings protocol"
        );
        for action in ACTIONS {
            if *action == "openMicrophoneSettings" {
                let platform = include_str!("../platform/mod.rs");
                assert!(
                    platform.contains("\"open_microphone_settings\""),
                    "microphone permission action must have a native host route"
                );
            } else {
                assert!(
                    aliases.contains(action),
                    "Settings action rejected by backend: {action}"
                );
            }
        }
    }
}
