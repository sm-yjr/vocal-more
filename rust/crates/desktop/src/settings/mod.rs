// SPDX-License-Identifier: GPL-3.0-only
//! Native settings window. It holds view state and sends nonblocking intents;
//! the application backend owns all durable configuration and recordings.
mod calibration;
mod pages;
pub mod schema;
mod shortcut;
#[cfg(feature = "ui-test")]
pub mod testing;

use crate::bridge::CommandSink;
use gpui_kit::component::{
    IndexPath,
    input::{InputEvent, InputState, TextareaState},
    select::{SelectEvent, SelectItem, SelectState},
    slider::{SliderEvent, SliderState},
};
use gpui_kit::*;
use schema::{Field, Kind, Tab, get, put};
use serde_json::{Value, json};
use std::{collections::HashMap, time::Duration};

#[derive(Clone)]
pub(super) struct OptionItem {
    pub value: SharedString,
    pub label: SharedString,
    pub disabled: bool,
}
impl SelectItem for OptionItem {
    type Value = SharedString;
    fn title(&self) -> SharedString {
        self.label.clone()
    }
    fn value(&self) -> &SharedString {
        &self.value
    }
    fn disabled(&self) -> bool {
        self.disabled
    }
}
pub(super) enum Control {
    Input(Entity<InputState>),
    Select(Entity<SelectState<Vec<OptionItem>>>),
    Slider(Entity<SliderState>),
    Toggle,
}
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum MicState {
    Idle,
    Starting,
    Recording,
    Done,
    Error,
}

pub struct Settings {
    pub(super) snapshot: Value,
    committed_config: Value,
    pub(super) commands: CommandSink,
    pub(super) tab: Tab,
    pub(super) narrow: bool,
    pub(super) controls: HashMap<&'static str, Control>,
    pub(super) prompts: HashMap<&'static str, Entity<TextareaState>>,
    pub(super) prompt_category: &'static str,
    pub(super) term: Entity<InputState>,
    pub(super) aliases: Entity<InputState>,
    pub(super) filter: Entity<InputState>,
    pub(super) capture: shortcut::Capture,
    pub(super) show_key: bool,
    pub(super) model_checking: bool,
    pub(super) model_results: Value,
    pub(super) mic: MicState,
    pub(super) mic_level: f64,
    pub(super) mic_error: Option<String>,
    pub(super) mic_playable: bool,
    pub(super) mic_playing: bool,
    mic_auto_play: bool,
    pub(super) calibration: calibration::Calibration,
    pub(super) pending_deletion: Option<(String, u64)>,
    pub(super) deletion_epoch: u64,
    pub(super) playing: Option<String>,
    pub(super) copied: Option<String>,
    pub(super) focus_recording: Option<String>,
    pub(super) compacting: bool,
    pub(super) error: Option<String>,
    pub(super) rerun_confirm: bool,
    pending: HashMap<u64, String>,
    rejected_action: Option<String>,
    closed: bool,
}

pub fn open(
    snapshot: Value,
    commands: CommandSink,
    cx: &mut App,
) -> anyhow::Result<(AnyWindowHandle, Entity<Settings>)> {
    let options = WindowOptions {
        window_bounds: Some(WindowBounds::centered(size(px(900.), px(740.)), cx)),
        window_min_size: Some(size(px(640.), px(480.))),
        titlebar: Some(TitlebarOptions {
            title: Some("Vocal More".into()),
            ..Default::default()
        }),
        ..Default::default()
    };
    gpui_kit::open_window(options, cx, move |window, cx| {
        cx.new(|cx| Settings::new(snapshot, commands, window, cx))
    })
    .map_err(|error| anyhow::anyhow!(error.to_string()))
}

impl Settings {
    fn new(
        mut snapshot: Value,
        commands: CommandSink,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        normalize_snapshot(&mut snapshot);
        let mut this = Self {
            committed_config: snapshot["config"].clone(),
            tab: Tab::from_id(snapshot["initial_tab"].as_str().unwrap_or("general")),
            narrow: false,
            focus_recording: snapshot["focus_recording_id"].as_str().map(str::to_owned),
            snapshot,
            commands,
            controls: HashMap::new(),
            prompts: HashMap::new(),
            prompt_category: "output_type",
            term: cx.new(|cx| InputState::new(window, cx)),
            aliases: cx.new(|cx| InputState::new(window, cx)),
            filter: cx.new(|cx| InputState::new(window, cx)),
            capture: Default::default(),
            show_key: false,
            model_checking: false,
            model_results: json!([]),
            mic: MicState::Idle,
            mic_level: 0.,
            mic_error: None,
            mic_playable: false,
            mic_playing: false,
            mic_auto_play: true,
            calibration: Default::default(),
            pending_deletion: None,
            deletion_epoch: 0,
            playing: None,
            copied: None,
            compacting: false,
            error: None,
            rerun_confirm: false,
            pending: HashMap::new(),
            rejected_action: None,
            closed: false,
        };
        for field in schema::FIELDS {
            let value = get(this.config(), field.key);
            let control = match field.kind {
                Kind::Toggle => Control::Toggle,
                Kind::Text | Kind::Secret | Kind::List => {
                    let initial = schema::display_value(value);
                    let key = field.key;
                    let input = cx.new(|cx| {
                        InputState::new(window, cx)
                            .default_value(initial)
                            .masked(matches!(field.kind, Kind::Secret))
                            .placeholder(if matches!(field.kind, Kind::Secret) {
                                if get(this.config(), "_api_key_set") == true {
                                    "••••••••"
                                } else {
                                    "sk-…"
                                }
                            } else {
                                ""
                            })
                    });
                    cx.subscribe_in(
                        &input,
                        window,
                        move |this, input, event: &InputEvent, window, cx| {
                            if matches!(event, InputEvent::Blur | InputEvent::PressEnter { .. }) {
                                let draft = input.read(cx).value().to_string();
                                let value = if matches!(field.kind, Kind::List) {
                                    schema::list_from_text(&draft)
                                } else {
                                    json!(draft.trim())
                                };
                                if get(this.config(), key) != &value {
                                    this.set_config(key, value, window, cx);
                                }
                            }
                        },
                    )
                    .detach();
                    Control::Input(input)
                }
                Kind::Choice(_) | Kind::Model | Kind::Device => {
                    let items = this.options(*field);
                    let selected = items
                        .iter()
                        .position(|item| item.value.as_ref() == schema::display_value(value));
                    let select = cx.new(|cx| {
                        SelectState::new(
                            items,
                            selected.map(|row| IndexPath::default().row(row)),
                            window,
                            cx,
                        )
                        .searchable(matches!(field.kind, Kind::Model | Kind::Device))
                    });
                    let key = field.key;
                    cx.subscribe_in(&select,window,move |this,_,event:&SelectEvent<Vec<OptionItem>>,window,cx| {
                        let SelectEvent::Confirm(Some(value))=event else {return;};
                        match key {
                            "asr.model"=>{
                                let transport=this.array("asr_models").iter().find(|item|item["id"].as_str()==Some(value.as_ref())).map(|item|item["transport"].clone()).unwrap_or(Value::Null);
                                this.action("setAsrModel",json!({"model":value.as_ref(),"backend":transport}), cx);
                            },
                            "audio.input_device"=>{this.action("setDevice",json!({"device":if value.is_empty(){Value::Null}else{json!(value.as_ref())}}), cx);},
                            _=>this.set_config(key,json!(value.as_ref()),window,cx),
                        }
                    }).detach();
                    Control::Select(select)
                }
                Kind::Slider {
                    min,
                    max,
                    step,
                    gain_db,
                    ..
                } => {
                    let value = value.as_f64().unwrap_or(min as f64);
                    let display = if gain_db {
                        (20. * value.max(0.001).log10()).round() as f32
                    } else {
                        value as f32
                    };
                    let slider = cx.new(|_| {
                        SliderState::new()
                            .min(min)
                            .max(max)
                            .step(step)
                            .default_value(display)
                    });
                    let key = field.key;
                    cx.subscribe_in(
                        &slider,
                        window,
                        move |this, _, event: &SliderEvent, window, cx| {
                            let (value, release) = match event {
                                SliderEvent::Change(value) => (value.start(), false),
                                SliderEvent::Release(value) => (value.start(), true),
                            };
                            let value = if gain_db {
                                10_f64.powf(value as f64 / 20.)
                            } else {
                                value as f64
                            };
                            if release {
                                this.set_config(key, json!(value), window, cx);
                            } else {
                                put(this.config_mut(), key, json!(value));
                                this.commands
                                    .request("preview_config", json!({"key":key,"value":value}));
                                cx.notify();
                            }
                        },
                    )
                    .detach();
                    Control::Slider(slider)
                }
            };
            this.controls.insert(field.key, control);
        }
        for &(category, _, _) in schema::PROMPT_CATEGORIES {
            let text = this.prompt_text(category);
            let input = cx.new(|cx| TextareaState::new(window, cx).default_value(text));
            cx.subscribe_in(
                &input,
                window,
                move |this, input, event: &InputEvent, window, cx| {
                    if matches!(event, InputEvent::Blur) && this.prompt_enabled(category) {
                        this.set_prompt(
                            category,
                            true,
                            input.read(cx).value().to_string(),
                            window,
                            cx,
                        );
                    }
                },
            )
            .detach();
            this.prompts.insert(category, input);
        }
        cx.subscribe(&this.filter, |_, _, _: &InputEvent, cx| cx.notify())
            .detach();
        this.sync_placeholders(window, cx);
        // Geist light/dark follows the macOS appearance while the window is open.
        crate::theme::follow_appearance(window, cx);
        cx.observe_window_appearance(window, |_, window, cx| {
            crate::theme::follow_appearance(window, cx);
        })
        .detach();
        this
    }
    pub(super) fn config(&self) -> &Value {
        &self.snapshot["config"]
    }
    fn config_mut(&mut self) -> &mut Value {
        &mut self.snapshot["config"]
    }
    pub(super) fn english(&self) -> bool {
        get(self.config(), "ui.language") == "en"
    }
    pub(super) fn text(&self, zh: &'static str, en: &'static str) -> &'static str {
        if self.english() { en } else { zh }
    }
    pub(super) fn array(&self, key: &str) -> &[Value] {
        self.snapshot[key]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
    }
    pub(super) fn native_asr(&self) -> bool {
        self.array("asr_models")
            .iter()
            .find(|model| &model["id"] == get(self.config(), "asr.model"))
            .is_some_and(|model| model["pipeline"] == "native_asr")
    }
    pub(super) fn audio_busy(&self) -> bool {
        matches!(self.mic, MicState::Starting | MicState::Recording)
            || self.snapshot["audio_input_status"]["phase"]
                .as_str()
                .is_some_and(|phase| ["starting", "active", "stopping"].contains(&phase))
            || self.calibration.phase.is_some()
    }
    pub(super) fn advanced(&self) -> bool {
        get(self.config(), "ui.advanced_settings") == true
    }
    /// Advanced fields stay hidden for everyday use, except when a saved value
    /// or an enabled feature means the user needs to see or undo them.
    pub(super) fn shown(&self, field: Field) -> bool {
        if !field.advanced || self.advanced() {
            return true;
        }
        let saved = |key| !schema::display_value(get(self.config(), key)).is_empty();
        match field.key {
            "network.proxy_url" => saved("network.proxy_url"),
            "asr.realtime_url" => {
                saved("asr.realtime_url") || get(self.config(), "screen_context_enabled") == true
            }
            _ => false,
        }
    }
    pub(super) fn disabled(&self, key: &str) -> bool {
        if key.starts_with("audio.") && key != "audio.waveform_ceiling_dbfs" && self.audio_busy() {
            return true;
        }
        match key {
            "native_fast_paste" | "restore_clipboard" => get(self.config(), "auto_paste") == false,
            "audio.gain" | "audio.soft_limiter" => {
                get(self.config(), "audio.gain_mode") == "automatic"
                    && self.snapshot["audio_input_status"]["gain_control"] == "apple_agc"
            }
            "audio.highpass_freq" => get(self.config(), "audio.highpass_filter") == false,
            "enable_polish" => self.native_asr(),
            "llm.enable_thinking" => {
                self.native_asr()
                    || get(self.config(), "enable_polish") == false
                    || !self
                        .array("llm_models")
                        .iter()
                        .find(|model| &model["id"] == get(self.config(), "llm.model"))
                        .is_some_and(|model| model["supports_thinking"] == true)
            }
            _ if key.starts_with("llm.") => {
                self.native_asr() || get(self.config(), "enable_polish") == false
            }
            _ => false,
        }
    }
    fn options(&self, field: Field) -> Vec<OptionItem> {
        let english = self.english();
        let item = |value: String, label: String, disabled| OptionItem {
            value: value.into(),
            label: label.into(),
            disabled,
        };
        let mut options = match field.kind {
            Kind::Choice(options) => options
                .iter()
                .map(|(value, zh, en)| {
                    item(
                        value.to_string(),
                        if english { en } else { zh }.to_string(),
                        false,
                    )
                })
                .collect(),
            Kind::Model => self
                .array(if field.key == "asr.model" {
                    "asr_models"
                } else {
                    "llm_models"
                })
                .iter()
                .enumerate()
                .map(|(index, model)| {
                    item(
                        model["id"]
                            .as_str()
                            .map(str::to_owned)
                            .unwrap_or_else(|| format!("separator-{index}")),
                        model["display_name"].as_str().unwrap_or("Model").to_owned(),
                        model["separator"] == true,
                    )
                })
                .collect(),
            Kind::Device => {
                let mut options = vec![item(
                    String::new(),
                    self.text("系统默认", "System default").to_owned(),
                    false,
                )];
                options.extend(self.array("devices").iter().map(|device| {
                    item(
                        device["name"].as_str().unwrap_or_default().to_owned(),
                        device["name"].as_str().unwrap_or_default().to_owned(),
                        false,
                    )
                }));
                options
            }
            _ => vec![],
        };
        let current = schema::display_value(get(self.config(), field.key));
        if !current.is_empty()
            && !options
                .iter()
                .any(|option| option.value.as_ref() == current)
        {
            options.insert(
                0,
                item(
                    current.clone(),
                    format!(
                        "{} · {}",
                        current,
                        self.text("已保存 / 未列出", "Saved / unavailable")
                    ),
                    true,
                ),
            );
        }
        options
    }
    pub(super) fn set_config(
        &mut self,
        key: &str,
        value: Value,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if let Err((zh, en)) = schema::validate(key, &value) {
            self.error = Some(self.text(zh, en).into());
            cx.notify();
            return;
        }
        let request = match self
            .commands
            .request_checked("set_config", json!({"key":key,"value":value}))
        {
            Ok(request) => request,
            Err(error) => {
                self.error = Some(error.to_string());
                let committed = get(&self.committed_config, key).clone();
                put(self.config_mut(), key, committed);
                if key == "api_key" {
                    self.show_key = false;
                    if let Some(Control::Input(input)) = self.controls.get("api_key") {
                        input.update(cx, |input, cx| {
                            input.set_masked(true, window, cx);
                            input.set_value("", window, cx);
                        });
                    }
                }
                self.sync_controls(window, cx, true);
                cx.notify();
                return;
            }
        };
        self.pending.insert(request, key.to_owned());
        put(self.config_mut(), key, value);
        if key == "ui.language" {
            self.sync_controls(window, cx, false);
        }
        self.error = None;
        cx.notify();
    }
    pub(super) fn action(&mut self, action: &str, params: Value, cx: &mut Context<Self>) -> u64 {
        let request = self.action_request(action, params);
        cx.notify();
        request
    }
    fn action_request(&mut self, action: &str, mut params: Value) -> u64 {
        params["action"] = json!(action);
        let result = if action == "openMicrophoneSettings" {
            self.commands
                .request_checked("open_microphone_settings", json!({}))
        } else {
            self.commands.request_checked("ui_action", params)
        };
        let request = match result {
            Ok(request) => request,
            Err(error) => {
                self.error = Some(error.to_string());
                self.rejected_action = Some(action.into());
                return 0;
            }
        };
        self.pending.insert(request, action.to_owned());
        request
    }
    pub(super) fn recover_rejected_action(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(action) = self.rejected_action.take() else {
            return;
        };
        if action == "startMicTest" {
            self.mic = MicState::Error;
            self.mic_error = self.error.clone();
            self.calibration.phase = None;
        }
        if action == "checkDashScopeModels" {
            self.model_checking = false;
        }
        if action == "revealApiKey" {
            self.show_key = false;
            if let Some(Control::Input(input)) = self.controls.get("api_key") {
                input.update(cx, |input, cx| input.set_masked(true, window, cx));
            }
        }
        self.sync_controls(window, cx, true);
    }
    /// Placeholders follow the interface language; examples read as examples
    /// so they are not mistaken for saved entries.
    fn sync_placeholders(&self, window: &mut Window, cx: &mut Context<Self>) {
        for (input, zh, en) in [
            (&self.term, "例如 Vocal More", "e.g. Vocal More"),
            (
                &self.aliases,
                "例如 vocal more, vocalmore",
                "e.g. vocal more, vocalmore",
            ),
            (&self.filter, "搜索录音文本", "Search recording text"),
        ] {
            let placeholder = self.text(zh, en);
            input.update(cx, |input, cx| {
                input.set_placeholder(placeholder, window, cx)
            });
        }
    }
    fn sync_controls(&mut self, window: &mut Window, cx: &mut Context<Self>, force: bool) {
        for field in schema::FIELDS {
            let value = get(self.config(), field.key).clone();
            let options = self.options(*field);
            match &self.controls[field.key] {
                Control::Input(input) => {
                    if field.key == "api_key" && !self.show_key && value == "" {
                        continue;
                    }
                    if force || !input.read(cx).focus_handle(cx).is_focused(window) {
                        let text = schema::display_value(&value);
                        if input.read(cx).value().as_ref() != text {
                            input.update(cx, |input, cx| input.set_value(text, window, cx));
                        }
                    }
                }
                Control::Select(select) => select.update(cx, |select, cx| {
                    select.set_items(options, window, cx);
                    select.set_selected_value(&schema::display_value(&value).into(), window, cx);
                }),
                Control::Slider(slider) => {
                    if let Kind::Slider { gain_db, .. } = field.kind {
                        let value = value.as_f64().unwrap_or(0.);
                        let display = if gain_db {
                            (20. * value.max(0.001).log10()).round() as f32
                        } else {
                            value as f32
                        };
                        slider.update(cx, |slider, cx| slider.set_value(display, window, cx));
                    }
                }
                Control::Toggle => {}
            }
        }
        self.sync_placeholders(window, cx);
        for &(category, _, _) in schema::PROMPT_CATEGORIES {
            let input = &self.prompts[category];
            if force || !input.read(cx).focus_handle(cx).is_focused(window) {
                let text = self.prompt_text(category);
                if input.read(cx).value().as_ref() != text {
                    input.update(cx, |input, cx| input.set_value(text, window, cx));
                }
            }
        }
    }
    pub(super) fn prompt_enabled(&self, category: &str) -> bool {
        self.config()["llm"]["prompt_overrides"][category]["enabled"] == true
    }
    pub(super) fn prompt_preset(&self, category: &str) -> String {
        let key = match category {
            "output_type" => get(self.config(), "llm.polish_mode")
                .as_str()
                .unwrap_or("dictation"),
            "level" => get(self.config(), "llm.level")
                .as_str()
                .unwrap_or("minimal"),
            "structured" => "enabled",
            "tone" => get(self.config(), "llm.tone").as_str().unwrap_or("neutral"),
            _ => get(self.config(), "llm.persona")
                .as_str()
                .unwrap_or("default"),
        };
        self.snapshot["polish_prompt_presets"][category][key]
            .as_str()
            .unwrap_or_default()
            .to_owned()
    }
    fn prompt_text(&self, category: &str) -> String {
        if self.prompt_enabled(category) {
            self.config()["llm"]["prompt_overrides"][category]["prompt"]
                .as_str()
                .unwrap_or_default()
                .to_owned()
        } else {
            self.prompt_preset(category)
        }
    }
    pub(super) fn set_prompt(
        &mut self,
        category: &str,
        enabled: bool,
        prompt: String,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let mut overrides = self.config()["llm"]["prompt_overrides"].clone();
        if !overrides.is_object() {
            overrides = json!({});
        }
        overrides[category] = json!({"enabled":enabled,"prompt":prompt});
        self.set_config("llm.prompt_overrides", overrides, window, cx);
        self.sync_controls(window, cx, true);
    }
    pub(super) fn switch_tab(&mut self, tab: Tab, window: &mut Window, cx: &mut Context<Self>) {
        if tab != Tab::Audio && self.tab == Tab::Audio {
            self.stop_mic();
            self.close_calibration();
        }
        if self.capture.active {
            self.commands.request("end_hotkey_capture", json!({}));
            self.capture = Default::default();
        }
        self.tab = tab;
        if tab == Tab::History {
            self.action("getRecordings", json!({}), cx);
        }
        self.sync_controls(window, cx, false);
        cx.notify();
    }
    pub(super) fn start_mic(&mut self, cx: &mut Context<Self>) {
        self.mic = MicState::Starting;
        self.mic_error = None;
        self.mic_playable = false;
        self.mic_playing = false;
        self.mic_auto_play = self.calibration.phase.is_none();
        self.action("startMicTest", json!({}), cx);
        cx.notify();
    }
    pub(super) fn stop_mic(&mut self) {
        if matches!(self.mic, MicState::Starting | MicState::Recording) {
            self.action_request("stopMicTest", json!({}));
        }
    }
    pub(super) fn close_calibration(&mut self) {
        if self.calibration.open {
            self.mic_auto_play = false;
        }
        if self.calibration.phase.is_some() {
            self.stop_mic();
        }
        self.calibration.close();
        self.mic_playable = false;
    }
    pub(super) fn start_calibration(&mut self, cx: &mut Context<Self>) {
        self.calibration.begin(&self.snapshot["config"]);
        self.start_mic(cx);
    }
    fn mic_started(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.mic = MicState::Recording;
        self.calibration.starting = false;
        let epoch = self.calibration.epoch;
        let phase = self.calibration.phase;
        let duration = phase.map(calibration::Phase::duration_ms).unwrap_or(5500);
        cx.spawn_in(window, async move |this, cx| {
            cx.background_executor()
                .timer(Duration::from_millis(duration))
                .await;
            _ = this.update_in(cx, |this, _, cx| {
                if this.mic == MicState::Recording
                    && this.calibration.epoch == epoch
                    && this.calibration.phase == phase
                {
                    this.calibration.stopping = true;
                    this.stop_mic();
                    cx.notify();
                }
            });
        })
        .detach();
    }
    pub(super) fn preset(&mut self, name: &str, window: &mut Window, cx: &mut Context<Self>) {
        let (gain, freq) = match name {
            "whisper" => (8., 220),
            "noisy" => (6., 280),
            _ => (4., 200),
        };
        for (key, value) in [
            ("audio.gain_mode", json!("manual")),
            ("audio.gain", json!(gain)),
            ("audio.highpass_filter", json!(true)),
            ("audio.highpass_freq", json!(freq)),
            ("audio.soft_limiter", json!(true)),
        ] {
            self.set_config(key, value, window, cx);
        }
        self.sync_controls(window, cx, true);
    }
    pub(super) fn stage_delete(&mut self, id: String, window: &mut Window, cx: &mut Context<Self>) {
        if let Some((previous, _)) = self.pending_deletion.take() {
            self.action("deleteRecording", json!({"id":previous}), cx);
        }
        self.action("stopRecording", json!({"id":id}), cx);
        self.playing = None;
        self.deletion_epoch += 1;
        let epoch = self.deletion_epoch;
        self.pending_deletion = Some((id, epoch));
        cx.notify();
        cx.spawn_in(window, async move |this, cx| {
            cx.background_executor().timer(Duration::from_secs(5)).await;
            _ = this.update_in(cx, |this, _, cx| {
                if this
                    .pending_deletion
                    .as_ref()
                    .is_some_and(|(_, pending)| *pending == epoch)
                {
                    if let Some((id, _)) = this.pending_deletion.take() {
                        this.action("deleteRecording", json!({"id":id}), cx);
                    }
                    cx.notify();
                }
            });
        })
        .detach();
    }
    /// Host forwards every backend event and correlated RPC completion here.
    pub fn on_event(
        &mut self,
        method: &str,
        params: &Value,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.closed {
            return;
        }
        match method {
            "resync" | "snapshot" => {
                self.snapshot = params.clone();
                normalize_snapshot(&mut self.snapshot);
                self.committed_config = self.snapshot["config"].clone();
                self.sync_controls(window, cx, true);
            }
            "config_changed" => {
                let revealed_key = self
                    .show_key
                    .then(|| self.snapshot["config"]["api_key"].clone());
                self.snapshot["config"] = params["config"].clone();
                self.snapshot["api_key_set"] = params["api_key_set"].clone();
                normalize_snapshot(&mut self.snapshot);
                self.committed_config = self.snapshot["config"].clone();
                if let Some(key) = revealed_key {
                    self.snapshot["config"]["api_key"] = key;
                }
                if !self.show_key
                    && let Some(Control::Input(input)) = self.controls.get("api_key")
                {
                    input.update(cx, |input, cx| input.set_value("", window, cx));
                }
                self.sync_controls(window, cx, false);
            }
            "api_key_revealed" => {
                if self.show_key {
                    self.snapshot["config"]["api_key"] = params["value"].clone();
                    self.sync_controls(window, cx, true);
                }
            }
            "rpc_response" => {
                let id = params["request_id"].as_u64().unwrap_or_default();
                self.pending.remove(&id);
                if params["method"] == "snapshot" {
                    self.snapshot = params["result"].clone();
                    normalize_snapshot(&mut self.snapshot);
                    self.committed_config = self.snapshot["config"].clone();
                    self.sync_controls(window, cx, true);
                }
            }
            "rpc_error" | "request_failed" => {
                let id = params["request_id"].as_u64().unwrap_or_default();
                let key = self.pending.remove(&id);
                self.error = Some(
                    params["message"]
                        .as_str()
                        .unwrap_or("Request failed")
                        .to_owned(),
                );
                if key.as_deref() == Some("api_key") {
                    self.show_key = false;
                    if let Some(Control::Input(input)) = self.controls.get("api_key") {
                        input.update(cx, |input, cx| {
                            input.set_masked(true, window, cx);
                            input.set_value("", window, cx);
                        });
                    }
                }
                if key.as_deref().is_some_and(|key| {
                    schema::FIELDS.iter().any(|field| field.key == key)
                        || [
                            "setAsrModel",
                            "setDevice",
                            "setActiveHotkeys",
                            "llm.prompt_overrides",
                            "ui.onboarding_completed",
                            "ui.onboarding_skipped",
                            "hotkey.custom_keys",
                        ]
                        .contains(&key)
                }) {
                    self.commands.request("snapshot", json!({}));
                }
                if key.as_deref() == Some("startMicTest") {
                    self.mic = MicState::Error;
                    self.mic_error = self.error.clone();
                    self.calibration.phase = None;
                }
                if key.as_deref() == Some("checkDashScopeModels") {
                    self.model_checking = false;
                }
            }
            "devices_changed" => {
                self.snapshot["devices"] = params["devices"].clone();
                self.snapshot["audio_input_status"] = params["audio_input_status"].clone();
                self.sync_controls(window, cx, false);
            }
            "audio_input_status" => self.snapshot["audio_input_status"] = params.clone(),
            "state_changed" => {
                if let Some(state) = params.get("state") {
                    self.snapshot["state"] = state.clone();
                }
            }
            "environment_changed" => self.snapshot["environment_checks"] = params.clone(),
            "dictionary_changed" => self.snapshot["dictionary"] = params.clone(),
            "dictionary_learning_changed" => {
                self.snapshot["dictionary"] = params["dictionary"].clone();
                self.snapshot["dictionary_learning_records"] = params["records"].clone();
            }
            "recordings_changed" => {
                self.snapshot["recordings"] = params["recordings"].clone();
                self.snapshot["recording_storage"] = params["storage"].clone();
            }
            "recording_deleted" => {
                if let Some(recordings) = self.snapshot["recordings"].as_array_mut() {
                    recordings.retain(|recording| recording["id"] != params["id"]);
                }
            }
            "recording_compaction_started" => {
                self.compacting = true;
                self.error = None;
            }
            "recording_compaction_complete" => {
                self.compacting = false;
                self.snapshot["recording_storage"] = params["storage"].clone();
                self.action("getRecordings", json!({}), cx);
            }
            "recording_compaction_failed" => {
                self.compacting = false;
                self.error = Some(params["message"].as_str().unwrap_or_default().into());
            }
            "recordingPlaybackStarted" | "recording_playback_started" => {
                self.playing = params["id"].as_str().map(str::to_owned)
            }
            "stop_recording" | "recording_playback_ended" | "recordingPlaybackEnded" => {
                if params["id"].is_null() || self.playing.as_deref() == params["id"].as_str() {
                    self.playing = None;
                }
            }
            "recording_playback_error" => {
                self.playing = None;
                self.error = Some(params["message"].as_str().unwrap_or_default().into());
            }
            "copiedFeedback" | "copied_feedback" => {
                self.copied = params["id"].as_str().map(str::to_owned);
                let copied = self.copied.clone();
                cx.spawn_in(window, async move |this, cx| {
                    cx.background_executor()
                        .timer(Duration::from_millis(1500))
                        .await;
                    _ = this.update_in(cx, |this, _, cx| {
                        if this.copied == copied {
                            this.copied = None;
                            cx.notify();
                        }
                    });
                })
                .detach();
            }
            "retry_started" | "retry_completed" | "retry_failed" => {
                if let Some(recordings) = self.snapshot["recordings"].as_array_mut()
                    && let Some(recording) = recordings
                        .iter_mut()
                        .find(|recording| recording["id"] == params["id"])
                {
                    recording["status"] = json!(match method {
                        "retry_started" => "retrying",
                        "retry_completed" => "success",
                        _ => "failed",
                    });
                    if method == "retry_completed" {
                        recording["transcript"] = params["transcript"].clone();
                        recording["error"] = Value::Null;
                    }
                    if method == "retry_failed" {
                        recording["error"] = params["error"].clone();
                    }
                }
            }
            "mic_test_started" => self.mic_started(window, cx),
            "mic_test_level" => {
                self.mic_level = params["rms"].as_f64().unwrap_or_default();
                self.calibration.sample(self.mic_level);
            }
            "mic_test_complete" => {
                self.mic = MicState::Done;
                self.mic_playable = true;
                if self.calibration.phase.is_some() {
                    if self.calibration.complete() {
                        self.start_mic(cx);
                    }
                } else if self.mic_auto_play {
                    self.action("playMicTest", json!({}), cx);
                }
            }
            "mic_test_error" => {
                self.mic = MicState::Error;
                self.mic_error = Some(params["message"].as_str().unwrap_or_default().into());
                self.calibration.phase = None;
            }
            "mic_test_playback" => {
                self.mic_playable = true;
                self.mic_playing = true;
            }
            "mic_test_playback_ended" | "micTestPlaybackEnded" => self.mic_playing = false,
            "mic_test_playback_error" => {
                self.mic_playing = false;
                self.mic_error = Some(
                    params["message"]
                        .as_str()
                        .unwrap_or("Playback failed")
                        .into(),
                );
            }
            "model_check_started" => {
                self.model_checking = true;
                self.model_results = json!([]);
            }
            "model_check_complete" => {
                self.model_checking = false;
                self.model_results = params["results"]
                    .as_array()
                    .map(|_| params["results"].clone())
                    .unwrap_or_else(|| params.clone());
            }
            "hotkey_capture" => match self.capture.receive(params) {
                shortcut::Outcome::Commit(key) => {
                    if let Some(keys) = shortcut::append(&shortcut::custom_keys(self.config()), key)
                    {
                        self.set_config("hotkey.custom_keys", keys, window, cx);
                    }
                    self.commands.request("end_hotkey_capture", json!({}));
                }
                shortcut::Outcome::Cancel => {
                    self.commands.request("end_hotkey_capture", json!({}));
                }
                _ => {}
            },
            "settings_focus" => {
                if let Some(tab) = params["tab"].as_str() {
                    self.switch_tab(Tab::from_id(tab), window, cx);
                }
                self.focus_recording = params["recording_id"].as_str().map(str::to_owned);
            }
            "select_tab" => {
                if let Some(tab) = params["tab"].as_str() {
                    self.switch_tab(Tab::from_id(tab), window, cx);
                }
            }
            "backend_disconnected" => {
                self.error = Some("Backend disconnected; restart Vocal More".into());
                self.stop_mic();
            }
            _ => {}
        }
        cx.notify();
    }
    /// Persist a still-focused native editor before the host releases the
    /// window. A window-close callback may run without an InputEvent::Blur.
    /// Masked blank key fields never erase the stored credential implicitly.
    pub fn flush(&mut self, cx: &mut Context<Self>) -> anyhow::Result<()> {
        let mut failed = Vec::new();
        for field in schema::FIELDS {
            let Some(Control::Input(input)) = self.controls.get(field.key) else {
                continue;
            };
            let text = input.read(cx).value().to_string();
            if matches!(field.kind, Kind::Secret)
                && text.is_empty()
                && !self.show_key
                && get(self.config(), "_api_key_set") == true
            {
                continue;
            }
            let value = if matches!(field.kind, Kind::List) {
                schema::list_from_text(&text)
            } else {
                json!(text.trim())
            };
            if get(self.config(), field.key) != &value {
                if let Err((zh, en)) = schema::validate(field.key, &value) {
                    failed.push(format!(
                        "{}: {}",
                        field.title(self.english()),
                        self.text(zh, en)
                    ));
                } else if let Err(error) = self
                    .commands
                    .request_checked("set_config", json!({"key":field.key,"value":value}))
                {
                    failed.push(format!("{}: {error}", field.title(self.english())));
                }
            }
        }
        let mut overrides = self.config()["llm"]["prompt_overrides"].clone();
        let mut changed = false;
        for &(category, _, _) in schema::PROMPT_CATEGORIES {
            if self.prompt_enabled(category) {
                let draft = self.prompts[category].read(cx).value().to_string();
                if overrides[category]["prompt"].as_str() != Some(&draft) {
                    overrides[category]["prompt"] = json!(draft);
                    changed = true;
                }
            }
        }
        if changed
            && let Err(error) = self.commands.request_checked(
                "set_config",
                json!({"key":"llm.prompt_overrides","value":overrides}),
            )
        {
            failed.push(format!(
                "{}: {error}",
                self.text("自定义提示词", "Custom prompts")
            ));
        }
        if !failed.is_empty() {
            let message = format!(
                "{}: {}",
                self.text("部分设置未保存", "Some settings edits were not saved"),
                failed.join("; ")
            );
            self.error = Some(message.clone());
            cx.notify();
            anyhow::bail!(message);
        }
        Ok(())
    }
    /// Call `flush` before closing or releasing the settings entity.
    pub fn close(&mut self) {
        self.stop_mic();
        self.close_calibration();
        if let Some(id) = self.playing.take() {
            self.action_request("stopRecording", json!({"id":id}));
        }
        self.commands.request("stop_mic_test_playback", json!({}));
        self.commands.request("end_hotkey_capture", json!({}));
        if let Some((id, _)) = self.pending_deletion.take() {
            self.action_request("deleteRecording", json!({"id":id}));
        }
        self.show_key = false;
        self.snapshot["config"]["api_key"] = json!("");
        self.closed = true;
    }
}
fn normalize_snapshot(snapshot: &mut Value) {
    if !snapshot["config"].is_object() {
        snapshot["config"] = json!({});
    }
    snapshot["config"]["_api_key_set"] = snapshot["api_key_set"].clone();
    snapshot["config"]["_version"] = snapshot["version"].clone();
    if snapshot["config"]["update_channel"].is_null() {
        snapshot["config"]["update_channel"] = json!("stable");
    }
}
