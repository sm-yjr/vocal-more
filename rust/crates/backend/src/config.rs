// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    catalog::{CONTRACT, asr_model, llm_model},
    persistence,
};
use anyhow::{Result, bail, ensure};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

#[derive(Clone)]
pub struct Config(pub Value);

impl Default for Config {
    fn default() -> Self {
        Self(CONTRACT["defaults"].clone())
    }
}

pub fn py_string(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        _ => v.to_string(),
    }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

pub fn boolean(v: &Value, default: bool) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => match s.trim().to_lowercase().as_str() {
            "1" | "true" | "yes" | "y" | "on" => true,
            "0" | "false" | "no" | "n" | "off" | "" => false,
            _ => default,
        },
        _ => default,
    }
}

fn number(v: &Value, default: f64) -> f64 {
    let number = match v {
        Value::Bool(b) => Some(u8::from(*b) as f64),
        Value::String(s) => s.trim().parse().ok(),
        _ => v.as_f64(),
    };
    number.filter(|n| n.is_finite()).unwrap_or(default)
}

fn choice(v: &Value, choices: &[&str], default: &str) -> Value {
    json!(
        v.as_str()
            .filter(|s| choices.contains(s))
            .unwrap_or(default)
    )
}

fn custom_key(v: &Value) -> Option<Value> {
    let code = v["key_code"].as_f64()?;
    CONTRACT["hotkey_keys"]
        .as_array()?
        .iter()
        .find(|key| {
            key["key_code"].as_f64() == Some(code)
                && number(&v["is_modifier"], -1.0) == number(&key["is_modifier"], -2.0)
                && number(&v["flag_mask"], -1.0) == number(&key["flag_mask"], -2.0)
                && (v["is_modifier"].is_boolean() || v["is_modifier"].is_number())
                && (v["flag_mask"].is_boolean() || v["flag_mask"].is_number())
        })
        .cloned()
}

impl Config {
    pub fn get(&self, key: &str) -> &Value {
        let mut value = &self.0;
        for part in key.split('.') {
            value = &value[part];
        }
        value
    }

    pub fn public(&self) -> Value {
        let mut value = self.0.clone();
        value["api_key"] = json!("");
        value
    }

    pub fn from_persisted(value: &Value) -> Self {
        let mut config = Self::default();
        if !value.is_object() {
            return config;
        }
        let _ = config.apply_form_inner(value, true);
        if value.as_object().is_some_and(|m| !m.is_empty()) {
            for field in ["onboarding_completed", "advanced_settings"] {
                if value["ui"].get(field).is_none() {
                    config.0["ui"][field] = json!(true);
                }
            }
            if value["audio"].get("gain_mode").is_none() {
                config.0["audio"]["gain_mode"] = json!("manual");
            }
        }
        if value["audio"].get("capture_channels").is_none()
            && value["audio"].get("channels").is_some()
        {
            let _ = config.apply_update("audio.capture_channels", &value["audio"]["channels"]);
        }
        config
    }

    /// Validate the whole form before publishing any changes.
    pub fn apply_form(&mut self, value: &Value) -> Result<()> {
        let mut next = self.clone();
        next.apply_form_inner(value, false)?;
        *self = next;
        Ok(())
    }

    fn apply_form_inner(&mut self, value: &Value, ignore_unknown: bool) -> Result<()> {
        ensure!(value.is_object(), "Form state must be a dict");
        for key in [
            "api_key",
            "default_mode",
            "auto_paste",
            "streaming_paste",
            "native_fast_paste",
            "restore_clipboard",
            "enable_polish",
        ] {
            if let Some(v) = value.get(key) {
                self.apply_update(key, v)?;
            }
        }
        for section in ["audio", "asr", "llm", "hotkey", "ui", "dictionary_learning"] {
            let Some(fields) = value[section].as_object() else {
                continue;
            };
            let priority: &[&str] = match section {
                "asr" => &["backend", "model"],
                "hotkey" => &["custom_key", "custom_keys"],
                _ => &[],
            };
            for field in priority.iter().copied().chain(
                fields
                    .keys()
                    .map(String::as_str)
                    .filter(|key| !priority.contains(key)),
            ) {
                if let Some(v) = fields.get(field) {
                    let result = self.apply_update(&format!("{section}.{field}"), v);
                    if !ignore_unknown {
                        result?;
                    }
                }
            }
        }
        Ok(())
    }

    pub fn apply_update(&mut self, key: &str, value: &Value) -> Result<()> {
        let current = self.get(key).clone();
        let v = match key {
            "api_key" => json!(if truthy(value) {
                py_string(value)
            } else {
                String::new()
            }),
            "enable_polish"
            | "auto_paste"
            | "streaming_paste"
            | "native_fast_paste"
            | "restore_clipboard"
            | "audio.highpass_filter"
            | "audio.soft_limiter"
            | "asr.use_dictionary_corpus"
            | "llm.enable_thinking"
            | "llm.structured"
            | "ui.onboarding_completed"
            | "ui.advanced_settings"
            | "dictionary_learning.enabled" => {
                json!(boolean(value, current.as_bool().unwrap_or(false)))
            }
            "default_mode" => choice(value, &["walkie_talkie", "realtime_long"], "realtime_long"),
            "audio.sample_rate" => json!(16000),
            "audio.channels" => json!(1),
            "audio.capture_channels"
            | "audio.blocksize"
            | "audio.highpass_freq"
            | "llm.max_tokens" => {
                let (min, max) = match key {
                    "audio.capture_channels" => (1.0, 3.0),
                    "audio.blocksize" => (128.0, 8192.0),
                    "audio.highpass_freq" => (50.0, 500.0),
                    _ => (1.0, 65536.0),
                };
                if key == "audio.blocksize" && ["640", "1600"].contains(&py_string(value).trim()) {
                    json!(1280)
                } else {
                    json!(
                        number(value, current.as_f64().unwrap())
                            .trunc()
                            .clamp(min, max) as i64
                    )
                }
            }
            "audio.gain"
            | "audio.waveform_ceiling_dbfs"
            | "llm.temperature"
            | "hotkey.double_tap_threshold" => {
                let (min, max) = match key {
                    "audio.gain" => (10_f64.powf(-6.0 / 20.0), 50.0),
                    "audio.waveform_ceiling_dbfs" => (-30.0, 0.0),
                    "llm.temperature" => (0.0, 1.0),
                    _ => (0.15, 0.5),
                };
                json!(number(value, current.as_f64().unwrap()).clamp(min, max))
            }
            "audio.input_device" => {
                let s = if truthy(value) {
                    py_string(value).trim().to_string()
                } else {
                    String::new()
                };
                if s.is_empty() { Value::Null } else { json!(s) }
            }
            "audio.capture_backend" => {
                choice(value, &["low_latency", "voice_processing"], "low_latency")
            }
            "audio.gain_mode" => choice(value, &["automatic", "manual"], "manual"),
            "asr.backend" => {
                let backend = choice(
                    value,
                    &["realtime_ws", "short_file", "omni_offline"],
                    "realtime_ws",
                );
                let model = self.get("asr.model").as_str().unwrap_or("");
                if asr_model(model).is_none_or(|m| m["transport"] != backend) {
                    self.0["asr"]["model"] = json!(match backend.as_str().unwrap() {
                        "short_file" => "qwen3-asr-flash",
                        "omni_offline" => "qwen3.5-omni-plus",
                        _ => "qwen3.5-omni-flash-realtime",
                    });
                }
                backend
            }
            "asr.model" => {
                let id = value
                    .as_str()
                    .filter(|id| asr_model(id).is_some())
                    .unwrap_or("qwen3.5-omni-flash-realtime");
                self.0["asr"]["backend"] = asr_model(id).unwrap()["transport"].clone();
                json!(id)
            }
            "asr.language" => choice(
                &json!(value.as_str().unwrap_or("").trim().to_lowercase()),
                &["zh", "en", "auto"],
                "auto",
            ),
            "asr.batch_mode" => json!("manual"),
            "asr.realtime_url" => json!(normalize_realtime_url(value)?),
            "asr.extra_corpus_terms" => json!(
                value
                    .as_array()
                    .map(|a| a
                        .iter()
                        .map(py_string)
                        .filter(|s| !s.trim().is_empty())
                        .collect::<Vec<_>>())
                    .unwrap_or_default()
            ),
            "llm.model" => json!(
                value
                    .as_str()
                    .filter(|s| llm_model(s).is_some())
                    .unwrap_or("qwen3.5-plus")
            ),
            "llm.polish_mode" => choice(value, &["dictation", "prompt"], "dictation"),
            "llm.level" => {
                if value == "structured" {
                    json!("balanced")
                } else {
                    choice(value, &["minimal", "balanced", "strong"], "minimal")
                }
            }
            "llm.tone" => choice(value, &["neutral", "gentle", "direct"], "neutral"),
            "llm.persona" => choice(
                value,
                &["default", "technical", "bilingual", "professional", "chat"],
                "default",
            ),
            "llm.output_language" => choice(value, &["auto", "zh", "en"], "auto"),
            "llm.prompt_overrides" => {
                let mut result = json!({});
                for category in ["output_type", "level", "structured", "tone", "persona"] {
                    let Some(prompt) = value[category]["prompt"].as_str() else {
                        continue;
                    };
                    let prompt: String = prompt.chars().take(20_000).collect();
                    let enabled = boolean(&value[category]["enabled"], false);
                    if !prompt.is_empty() || enabled {
                        result[category] = json!({"enabled":enabled,"prompt":prompt});
                    }
                }
                result
            }
            "hotkey.primary_key" | "hotkey.fallback_key" => json!("fn"),
            "hotkey.active_hotkeys" => {
                let legacy = [
                    "right_cmd",
                    "double_cmd",
                    "f13",
                    "f14",
                    "f15",
                    "f16",
                    "f17",
                    "f18",
                    "f19",
                    "f20",
                    "printscreen",
                    "print_screen",
                ];
                let enabled = value.as_array().is_some_and(|a| {
                    a.iter()
                        .any(|v| v == "fn" || v.as_str().is_some_and(|s| legacy.contains(&s)))
                });
                if enabled { json!(["fn"]) } else { json!([]) }
            }
            "hotkey.custom_key" => {
                let parsed = custom_key(value).unwrap_or(Value::Null);
                self.0["hotkey"]["custom_keys"] = if parsed.is_null() {
                    json!([])
                } else {
                    json!([parsed.clone()])
                };
                parsed
            }
            "hotkey.custom_keys" => {
                let mut result = Vec::new();
                if let Some(keys) = value.as_array() {
                    for key in keys {
                        if let Some(key) = custom_key(key) {
                            if !result.contains(&key) {
                                result.push(key);
                            }
                            if result.len() >= 8 {
                                break;
                            }
                        }
                    }
                }
                self.0["hotkey"]["custom_key"] = result.first().cloned().unwrap_or(Value::Null);
                json!(result)
            }
            "ui.language" => choice(
                &json!(value.as_str().unwrap_or("").trim().to_lowercase()),
                &["zh", "en"],
                "en",
            ),
            "dictionary_learning.excluded_bundle_ids" => {
                let mut result = Vec::new();
                if let Some(items) = value.as_array() {
                    for item in items.iter().filter_map(Value::as_str) {
                        let s: String = item.trim().chars().take(255).collect();
                        if !s.is_empty() && !result.contains(&s) {
                            result.push(s);
                        }
                        if result.len() == 100 {
                            break;
                        }
                    }
                }
                json!(result)
            }
            _ => bail!("Unknown config key: {key}"),
        };
        if let Some((section, field)) = key.split_once('.') {
            self.0[section][field] = v;
        } else {
            self.0[key] = v;
        }
        Ok(())
    }
}

fn normalize_realtime_url(value: &Value) -> Result<String> {
    let raw = if truthy(value) {
        py_string(value).trim().to_string()
    } else {
        String::new()
    };
    if raw.is_empty() {
        return Ok(raw);
    }
    let url = url::Url::parse(&raw).map_err(|_| anyhow::anyhow!("invalid ASR realtime URL"))?;
    let host = url.host_str().unwrap_or("");
    ensure!(
        url.scheme() == "wss"
            && (host == "dashscope.aliyuncs.com" || host.ends_with(".maas.aliyuncs.com"))
            && url.username().is_empty()
            && url.password().is_none()
            && url.port().is_none_or(|p| p == 443)
            && url.path().trim_end_matches('/') == "/api-ws/v1/realtime"
            && url.query().is_none()
            && url.fragment().is_none(),
        "ASR realtime_url must use an official DashScope WSS endpoint"
    );
    // Preserve an explicitly supplied default port, matching existing settings.
    let authority = raw
        .split("//")
        .nth(1)
        .unwrap_or("")
        .split('/')
        .next()
        .unwrap_or("");
    Ok(format!(
        "wss://{host}{}/api-ws/v1/realtime",
        if authority.ends_with(":443") {
            ":443"
        } else {
            ""
        }
    ))
}

pub struct ConfigRepository {
    pub path: PathBuf,
    pub config: Config,
}

impl ConfigRepository {
    pub fn open(path: &Path) -> Result<Self> {
        let raw = persistence::read_yaml(path)?;
        Ok(Self {
            path: path.into(),
            config: raw.as_ref().map(Config::from_persisted).unwrap_or_default(),
        })
    }
    pub fn update(&mut self, key: &str, value: &Value) -> Result<()> {
        let mut next = self.config.clone();
        next.apply_update(key, value)?;
        persistence::write_yaml(&self.path, &next.0)?;
        self.config = next;
        Ok(())
    }
    pub fn update_form(&mut self, value: &Value) -> Result<()> {
        let mut next = self.config.clone();
        next.apply_form(value)?;
        persistence::write_yaml(&self.path, &next.0)?;
        self.config = next;
        Ok(())
    }
}
