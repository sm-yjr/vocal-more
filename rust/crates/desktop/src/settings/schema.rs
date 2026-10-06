// SPDX-License-Identifier: GPL-3.0-only
//! The editable surface is explicit, independent from its visual arrangement.
//! Backend validation remains authoritative; this schema adds early UI feedback.
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tab {
    General,
    Audio,
    Recognition,
    Polish,
    Shortcuts,
    Dictionary,
    History,
}
impl Tab {
    pub const ALL: [Self; 7] = [
        Self::General,
        Self::Audio,
        Self::Recognition,
        Self::Polish,
        Self::Shortcuts,
        Self::Dictionary,
        Self::History,
    ];
    pub fn id(self) -> &'static str {
        match self {
            Self::General => "general",
            Self::Audio => "audio",
            Self::Recognition => "recognition",
            Self::Polish => "polish",
            Self::Shortcuts => "shortcuts",
            Self::Dictionary => "dictionary",
            Self::History => "history",
        }
    }
    pub fn title(self, english: bool) -> &'static str {
        match (self, english) {
            (Self::General, false) => "通用",
            (Self::Audio, false) => "音频",
            (Self::Recognition, false) => "识别",
            (Self::Polish, false) => "润色",
            (Self::Shortcuts, false) => "快捷键",
            (Self::Dictionary, false) => "词典",
            (Self::History, false) => "录音历史",
            (Self::General, true) => "General",
            (Self::Audio, true) => "Audio",
            (Self::Recognition, true) => "Recognition",
            (Self::Polish, true) => "Polish",
            (Self::Shortcuts, true) => "Shortcuts",
            (Self::Dictionary, true) => "Dictionary",
            (Self::History, true) => "History",
        }
    }
    pub fn from_id(value: &str) -> Self {
        Self::ALL
            .into_iter()
            .find(|tab| tab.id() == value)
            .unwrap_or(Self::General)
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
        "ui.advanced_settings",
        General,
        "高级设置",
        "Advanced settings",
        false,
        Kind::Toggle,
        "显示服务配置和音频细节。",
        "Show provider configuration and audio details."
    ),
    field!(
        "api_key",
        General,
        "DashScope API Key",
        "DashScope API Key",
        true,
        Kind::Secret,
        "仅在本机明确点击显示时读取已保存的密钥。",
        "Read the saved key only when you explicitly choose Show."
    ),
    field!(
        "default_mode",
        General,
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
        "screen_context_enabled",
        General,
        "屏幕上下文",
        "Screen context",
        false,
        Kind::Toggle,
        "屏幕内容用于辅助识别，不会成为口述文本；需要兼容模型和空间端点。",
        "Screen content assists recognition and does not become dictated text; requires a compatible model and workspace endpoint."
    ),
    field!(
        "ui.language",
        General,
        "界面语言",
        "Interface language",
        false,
        Kind::Choice(&[("zh", "简体中文", "简体中文"), ("en", "English", "English")]),
        "",
        ""
    ),
    field!(
        "update_channel",
        General,
        "更新通道",
        "Update channel",
        false,
        Kind::Choice(&[
            ("stable", "稳定版", "Stable"),
            ("nightly", "预发布版", "Prerelease")
        ]),
        "通道由安装的正式版或预发布版决定。",
        "The installed distribution determines the update channel."
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
        "auto_paste",
        General,
        "自动输入识别结果",
        "Automatically paste results",
        false,
        Kind::Toggle,
        "完成识别后输入到原来聚焦的应用。",
        "Insert the completed result into the previously focused application."
    ),
    field!(
        "native_fast_paste",
        General,
        "原生快速输入",
        "Native fast paste",
        false,
        Kind::Toggle,
        "在支持的应用中优先使用原生输入路径。",
        "Prefer native input in supported applications."
    ),
    field!(
        "restore_clipboard",
        General,
        "恢复原剪贴板",
        "Restore clipboard",
        false,
        Kind::Toggle,
        "粘贴后恢复此前的剪贴板内容。",
        "Restore the previous clipboard content after pasting."
    ),
    field!(
        "streaming_paste",
        General,
        "边说边输入",
        "Streaming paste",
        false,
        Kind::Toggle,
        "在录音过程中输入实时识别结果。",
        "Insert interim recognition while recording."
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
        "",
        ""
    ),
    field!(
        "audio.soft_limiter",
        Audio,
        "软限幅",
        "Soft limiter",
        true,
        Kind::Toggle,
        "提高增益时缓和峰值，减少削波。",
        "Soften peaks at high gain and avoid clipping."
    ),
    field!(
        "asr.model",
        Recognition,
        "识别模型",
        "Recognition model",
        false,
        Kind::Model,
        "模型列表和能力来自后端产品契约。",
        "Models and capabilities come from the backend product contract."
    ),
    field!(
        "asr.language",
        Recognition,
        "识别语言",
        "Recognition language",
        false,
        Kind::Choice(&[
            ("auto", "自动识别", "Automatic"),
            ("zh", "中文", "Chinese"),
            ("en", "英语", "English")
        ]),
        "",
        ""
    ),
    field!(
        "asr.realtime_url",
        Recognition,
        "空间实时端点",
        "Workspace realtime endpoint",
        false,
        Kind::Text,
        "留空使用公共端点；空间地址必须为 wss://…maas.aliyuncs.com/api-ws/v1/realtime。",
        "Leave blank for the public endpoint; workspace URLs must use wss://…maas.aliyuncs.com/api-ws/v1/realtime."
    ),
    field!(
        "enable_polish",
        Polish,
        "启用润色",
        "Enable polish",
        false,
        Kind::Toggle,
        "原生 ASR 模型直接转写，跳过第二阶段润色。",
        "Native ASR transcribes directly and skips second-stage polish."
    ),
    field!(
        "llm.polish_mode",
        Polish,
        "输出类型",
        "Output type",
        false,
        Kind::Choice(&[
            ("dictation", "口述文本", "Dictation"),
            ("prompt", "提示词", "Prompt")
        ]),
        "",
        ""
    ),
    field!(
        "llm.output_language",
        Polish,
        "输出语言",
        "Output language",
        false,
        Kind::Choice(&[
            ("auto", "保留原语言", "Preserve original"),
            ("zh", "中文", "Chinese"),
            ("en", "英语", "English")
        ]),
        "",
        ""
    ),
    field!(
        "llm.level",
        Polish,
        "润色程度",
        "Polish level",
        false,
        Kind::Choice(&[
            ("minimal", "轻度", "Minimal"),
            ("balanced", "适中", "Balanced"),
            ("strong", "深度", "Strong")
        ]),
        "",
        ""
    ),
    field!(
        "llm.structured",
        Polish,
        "结构化输出",
        "Structured output",
        false,
        Kind::Toggle,
        "按语义组织段落与列表。",
        "Organize paragraphs and lists by meaning."
    ),
    field!(
        "llm.tone",
        Polish,
        "表达语气",
        "Tone",
        false,
        Kind::Choice(&[
            ("neutral", "自然", "Neutral"),
            ("gentle", "温和", "Gentle"),
            ("direct", "直接", "Direct")
        ]),
        "",
        ""
    ),
    field!(
        "llm.persona",
        Polish,
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
        "",
        ""
    ),
    field!(
        "llm.model",
        Polish,
        "润色模型",
        "Polish model",
        true,
        Kind::Model,
        "",
        ""
    ),
    field!(
        "llm.temperature",
        Polish,
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
        "",
        ""
    ),
    field!(
        "llm.enable_thinking",
        Polish,
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
        "",
        ""
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
        false,
        Kind::List,
        "用逗号分隔应用 Bundle ID。",
        "Separate application bundle IDs with commas."
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
pub fn validate(key: &str, value: &Value) -> Result<(), &'static str> {
    if key == "network.proxy_url" && !valid_proxy(value.as_str().unwrap_or("")) {
        return Err("Invalid proxy URL; use http://host:port or socks5://host:port");
    }
    if key == "asr.realtime_url" && !valid_endpoint(value.as_str().unwrap_or("")) {
        return Err("Invalid workspace endpoint");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
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
