// SPDX-License-Identifier: GPL-3.0-only
//! Linux GPUI frontend over the unchanged 0.6.1 business backend.
mod accessibility;
mod capsule;
#[cfg(test)]
mod cloud_live_test;
pub mod control;
mod gestures;
mod input_compat;
mod insertion;
mod platform;
mod screen;
#[cfg(test)]
mod screen_live_test;
#[cfg(test)]
mod screen_portal_test;
mod shell;
pub(crate) mod shortcuts;
mod tray;
use crate::{
    bridge::{BackendDriver, CommandSink, Request, UiEvent, durable_method, durable_request},
    delivery::{Delivery, PasteLane},
    options::DesktopOptions,
    settings::{self, Settings},
};
use anyhow::{Context as _, Result, bail};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use gpui_kit::*;
use platform::{DeliveryJob, DesktopWorker, Session};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

enum Pending {
    Claim(Delivery),
    Prepare(Delivery, Value, Value),
    Manual(Value, Value),
}
struct Capture {
    delivery: Option<Delivery>,
    data: Value,
    epoch: u64,
    generation: u64,
    expires: Instant,
}
struct ScreenSession {
    key: screen::Key,
    generation: Option<u64>,
    initial: Option<Vec<u8>>,
    started: Instant,
}
struct Desktop {
    driver: BackendDriver,
    commands: CommandSink,
    events: async_channel::Receiver<UiEvent>,
    control: Option<control::Server>,
    output: DesktopWorker,
    shortcuts: Option<shortcuts::Worker>,
    shortcuts_status: Value,
    gestures: gestures::Worker,
    gesture_active: bool,
    gesture_message: String,
    gesture_key: Option<String>,
    gesture_recording: bool,
    shortcut_capture: bool,
    session: Session,
    screen_worker: screen::ScreenWorker,
    screen: Option<ScreenSession>,
    accessibility: accessibility::AccessibilityWorker,
    insertion: insertion::Worker,
    input_snapshot: Value,
    input_status: Value,
    input_epoch: u64,
    wayland_failed: Option<u64>,
    captures: HashMap<u64, Capture>,
    capture_ticket: u64,
    observation: Option<String>,
    capsule_model: capsule::Model,
    capsule: Option<(AnyWindowHandle, Entity<capsule::View>)>,
    shell: Option<shell::ShellCapsule>,
    error_visible: bool,
    error_deadline: Option<Instant>,
    fixture: Option<Instant>,
    test_audio: Option<std::path::PathBuf>,
    snapshot: Value,
    state: String,
    preview: String,
    last_text: String,
    notice: String,
    settings: Option<(AnyWindowHandle, Entity<Settings>)>,
    tray: tray::Tray,
    started: Instant,
    tray_warning_shown: bool,
    popup_capable: bool,
    deliveries: PasteLane,
    pending: HashMap<u64, Pending>,
    delivering: Option<Delivery>,
    closing: bool,
    save_failures: usize,
    initialized: bool,
    show_settings: bool,
    background: bool,
    shutdown_notice_at: Option<std::time::Instant>,
}

pub fn start(options: DesktopOptions, server: control::Server, cx: &mut App) -> Result<()> {
    let acceptance = options.backend.allow_test_sources
        || options.quit_after_ms.is_some()
        || options.capsule_fixtures.is_some();
    let shortcut_data = options.backend.data_dir.clone();
    let input_data = shortcut_data.clone();
    let shortcut_worker = if (!acceptance && !options.no_hotkeys)
        || std::env::var("VOCAL_MORE_PRIVATE_SHORTCUT_TEST").as_deref() == Ok("1")
    {
        Some(shortcuts::Worker::new(shortcut_data)?)
    } else {
        None
    };
    if let Some(worker) = &shortcut_worker {
        worker.request(0, "linux_shortcuts_refresh", json!({}))?;
    }
    let fixture = options.capsule_fixtures.as_ref().map(|_| Instant::now());
    let (driver, commands, events) = BackendDriver::start(options.backend)?;
    let session = Session::current();
    let output = DesktopWorker::new(commands.clone(), session)?;
    let gesture_app = if std::env::var("VOCAL_MORE_PRIVATE_GESTURE_TEST").as_deref() == Ok("1") {
        "io.github.sm_yjr.VocalMore.Test"
    } else {
        "io.github.sm_yjr.VocalMore"
    };
    let gestures = gestures::Worker::new(commands.clone(), gesture_app.into())?;
    let show_settings = options.show_settings;
    let tray = tray::Tray::new(commands.clone())?;
    let popup_capable = cx
        .graphical_environment()
        .is_some_and(|display| display.wayland_display.is_none() && display.x11_display.is_some());
    let screen_worker = screen::ScreenWorker::new()?;
    let accessibility = accessibility::AccessibilityWorker::new()?;
    let insertion = insertion::Worker::new(commands.clone(), input_data)?;
    let shell = if session == Session::Wayland && !acceptance {
        Some(shell::ShellCapsule::new(commands.clone())?)
    } else {
        None
    };
    let host = cx.new(|cx: &mut Context<Desktop>| {
        let host = Desktop {
            driver,
            commands,
            events: events.clone(),
            control: Some(server),
            output,
            shortcuts: shortcut_worker,
            shortcuts_status: json!({"available":false,"items":[]}),
            gestures,
            gesture_active: false,
            gesture_message: String::new(),
            gesture_key: None,
            gesture_recording: false,
            shortcut_capture: false,
            session,
            screen_worker,
            screen: None,
            accessibility,
            insertion,
            input_snapshot: Value::Null,
            input_status: Value::Null,
            input_epoch: 0,
            wayland_failed: None,
            captures: HashMap::new(),
            capture_ticket: 0,
            observation: None,
            capsule_model: capsule::Model::default(),
            capsule: None,
            shell,
            error_visible: false,
            error_deadline: None,
            fixture,
            test_audio: options.test_audio,
            snapshot: json!({}),
            state: "initializing".into(),
            preview: String::new(),
            last_text: String::new(),
            notice: String::new(),
            settings: None,
            tray,
            started: Instant::now(),
            tray_warning_shown: acceptance,
            popup_capable,
            deliveries: PasteLane::default(),
            pending: HashMap::new(),
            delivering: None,
            closing: false,
            save_failures: 0,
            initialized: false,
            show_settings,
            background: options.background,
            shutdown_notice_at: None,
        };
        cx.spawn(async move |host, cx| {
            let mut batch = 0;
            while let Ok(event) = events.recv().await {
                if host.update(cx, |host, cx| host.event(event, cx)).is_err() {
                    break;
                }
                batch += 1;
                if batch == 128 {
                    batch = 0;
                    futures_lite::future::yield_now().await;
                }
            }
        })
        .detach();
        cx.spawn(async move |host, cx| {
            let mut interval = Duration::from_millis(50);
            loop {
                cx.background_executor().timer(interval).await;
                match host.update(cx, |host, cx| {
                    host.tick(cx);
                    host.capsule_model.active
                }) {
                    Ok(active) => interval = Duration::from_millis(if active { 16 } else { 50 }),
                    Err(_) => break,
                }
            }
        })
        .detach();
        host
    });
    let closed = host.clone();
    cx.on_window_closed(move |cx, id| {
        let closed = closed.clone();
        cx.defer(move |cx| {
            closed.update(cx, |host, cx| {
                if host
                    .settings
                    .as_ref()
                    .is_some_and(|(window, _)| window.window_id() == id)
                    && let Some((_, settings)) = host.settings.take()
                {
                    settings.update(cx, |settings, cx| {
                        if settings.flush(cx).is_err() {
                            host.save_failures += 1;
                        }
                        settings.close();
                    });
                }
            });
        });
    })
    .detach();
    if let Some(ms) = options.quit_after_ms {
        let timed = host.clone();
        cx.spawn(async move |cx| {
            cx.background_executor()
                .timer(Duration::from_millis(ms))
                .await;
            timed.update(cx, |host, cx| host.begin_quit(cx));
        })
        .detach();
    }
    // OS termination still closes admission. Normal Quit/window close drains
    // durable edits before calling GPUI's final (short-deadline) quit hook.
    cx.on_app_quit(move |cx| {
        host.update(cx, |host, cx| host.begin_quit(cx));
        async {}
    })
    .detach();
    Ok(())
}

impl Desktop {
    fn submit(&mut self, method: &str, params: Value, pending: Option<Pending>) {
        match self.commands.request_checked(method, params) {
            Ok(id) => {
                if let Some(pending) = pending {
                    self.pending.insert(id, pending);
                }
            }
            Err(error) => {
                self.notice = error.to_string();
                self.deliveries.clear();
                self.delivering = None;
            }
        }
    }
    fn event(&mut self, event: UiEvent, cx: &mut Context<Self>) {
        if self.closing {
            if let UiEvent::Backend { method, params, .. } = event
                && method == "rpc_error"
                && durable_method(params["method"].as_str().unwrap_or(""), &params["params"])
            {
                self.save_failures += 1;
            }
            return;
        }
        match event {
            UiEvent::Request(request) => self.request(request, cx),
            UiEvent::Backend { method, params, .. } => self.backend_event(&method, params, cx),
        }
        if self.commands.take_quit_request() {
            self.begin_quit(cx);
        }
        cx.notify();
    }
    fn request(&mut self, mut request: Request, cx: &mut Context<Self>) {
        if matches!(
            request.method.as_str(),
            "start" | "hotkey_pressed" | "toggle_recording"
        ) && self.state == "idle"
            && let Some(path) = &self.test_audio
        {
            request.params["source"] = json!({"kind":"wav","path":path,"paced":true});
        }
        // Never start invisible recording when the desktop cannot render a capsule.
        if matches!(
            request.method.as_str(),
            "start" | "hotkey_pressed" | "toggle_recording"
        ) && self.state == "idle"
            && !self.popup_capable
            && self.shell.as_ref().is_none_or(|shell| !shell.available())
        {
            self.notice =
                "悬浮胶囊尚未就绪。请启用 Vocal More Capsule 扩展；首次安装后需注销并重新登录。"
                    .into();
            self.open_settings(cx);
            return;
        }
        if matches!(
            request.method.as_str(),
            "start" | "hotkey_pressed" | "toggle_recording"
        ) && self.state == "idle"
            && self.session == Session::Wayland
            && self.snapshot["config"]["auto_paste"] == true
        {
            self.input_epoch = request.epoch;
            self.input_snapshot = Value::Null;
            self.wayland_failed = None;
            let _ = self.insertion.capture(request.epoch);
        }
        if matches!(
            request.method.as_str(),
            "start" | "hotkey_pressed" | "toggle_recording"
        ) && self.state == "idle"
            && self.snapshot["config"]["screen_context_enabled"] == true
        {
            self.stop_screen();
            let key = screen::Key {
                request: request.id,
                epoch: request.epoch,
            };
            request.params["screen_context"] = json!(true);
            self.screen = Some(ScreenSession {
                key,
                generation: None,
                initial: None,
                started: Instant::now(),
            });
            if let Err(error) = self.screen_worker.start(key) {
                self.notice = error.to_string();
                self.screen = None;
            }
        }
        if matches!(
            request.method.as_str(),
            "stop" | "hotkey_released" | "cancel"
        ) || (request.method == "toggle_recording" && self.state != "idle")
        {
            self.stop_screen();
        }
        match request.method.as_str() {
            "platform_ping" => {}
            "linux_shortcuts_refresh"
            | "linux_shortcuts_save"
            | "linux_shortcuts_remove"
            | "linux_shortcuts_enable_gestures"
            | "linux_shortcuts_disable_gestures" => {
                self.shortcut_request(request, cx);
            }
            "linux_input_enable" | "linux_input_disable" | "linux_input_codex" => {
                let result = if request.method == "linux_input_codex" {
                    self.insertion.configure_codex(request.id)
                } else {
                    self.insertion
                        .configure_compatibility(request.id, request.method == "linux_input_enable")
                };
                if let Err(error) = result {
                    self.forward_settings(
                        "linux_input_changed",
                        &json!({"request_id":request.id,"error":error.to_string()}),
                        cx,
                    );
                    if self.closing {
                        self.save_failures += 1;
                    }
                }
            }
            "begin_hotkey_capture" => {
                self.shortcut_capture = true;
            }
            "end_hotkey_capture" => {
                self.shortcut_capture = false;
            }
            "platform_shortcut_pressed" => {
                let id = request.params["shortcut_id"].as_str().unwrap_or("");
                let item = self.shortcuts_status["items"]
                    .as_array()
                    .and_then(|items| items.iter().find(|item| item["id"] == id));
                if let Some(item) = item {
                    if self.shortcut_capture {
                        self.forward_settings(
                            "linux_shortcut_captured",
                            &json!({"accelerator":item["accelerator"]}),
                            cx,
                        );
                    } else if self.gesture_key.is_none() && self.gesture_active {
                        self.gesture_key = Some(id.into());
                        if self.state == "idle" {
                            self.gesture_recording = true;
                        }
                        self.commands.request_admitted(
                            "hotkey_pressed",
                            json!({}),
                            request.admitted_at,
                        );
                    }
                }
            }
            "platform_shortcut_released" => {
                if self.gesture_key.as_deref() == request.params["shortcut_id"].as_str() {
                    self.gesture_key = None;
                    self.commands.request_admitted(
                        "hotkey_released",
                        json!({}),
                        request.admitted_at,
                    );
                }
            }
            "platform_shortcut_cancel" => {
                self.gesture_key = None;
                if self.gesture_recording {
                    self.gesture_recording = false;
                    self.commands.request("cancel", json!({}));
                }
            }
            "platform_shortcut_activated" => {
                let id = request.params["shortcut_id"].as_str().unwrap_or("");
                let item = self.shortcuts_status["items"]
                    .as_array()
                    .and_then(|items| items.iter().find(|item| item["id"] == id));
                if let Some(item) = item {
                    if self.shortcut_capture {
                        self.forward_settings(
                            "linux_shortcut_captured",
                            &json!({"accelerator":item["accelerator"]}),
                            cx,
                        );
                    } else {
                        self.commands.request("toggle_recording", json!({}));
                    }
                }
            }
            "platform_quit" | "quit" => self.begin_quit(cx),
            "platform_show_settings" | "show_settings" => self.open_settings(cx),
            "platform_copy_last" => {
                if let Err(error) = self.output.copy(self.last_text.clone()) {
                    self.notice = error.to_string();
                }
            }
            "platform_event" => {
                let method = request.params["method"].as_str().unwrap_or("").to_owned();
                self.backend_event(&method, request.params["params"].clone(), cx);
            }
            "open_url"
            | "open_file"
            | "copy_transcript"
            | "platform_check_updates"
            | "check_updates"
            | "open_microphone_settings"
            | "open_accessibility_settings"
            | "open_screen_recording_settings"
            | "request_accessibility_permission"
            | "request_screen_permission"
            | "stop_mic_test_playback"
            | "play_recording"
            | "mic_test_playback"
            | "stop_recording" => {
                let result = self.platform_action(&request.method, &request.params);
                let method = if result.is_ok() {
                    "rpc_response"
                } else {
                    "rpc_error"
                };
                let error = result.err().map(|error| error.to_string());
                self.backend_event(
                    method,
                    json!({"request_id":request.id,"method":request.method,
                    "params":request.params,"result":{"ok":true},"message":error}),
                    cx,
                );
            }
            "cancel" => {
                self.deliveries.clear();
                self.pending.clear();
                self.captures.clear();
                self.capsule_model.active = false;
                self.error_visible = false;
                self.error_deadline = None;
                self.cancel_observation();
                self.driver.send(request);
            }
            _ => self.driver.send(request),
        }
    }
    fn shortcut_request(&mut self, request: Request, cx: &mut Context<Self>) {
        let result = self
            .shortcuts
            .as_ref()
            .context("快捷键配置服务未启用")
            .and_then(|worker| worker.request(request.id, &request.method, request.params));
        if let Err(error) = result {
            if self.closing {
                self.save_failures += 1;
            }
            self.forward_settings(
                "linux_shortcuts_changed",
                &json!({"request_id":request.id,"error":error.to_string()}),
                cx,
            );
        }
    }
    fn platform_action(&self, method: &str, params: &Value) -> Result<()> {
        match method {
            "copy_transcript" => self
                .output
                .copy(params["text"].as_str().unwrap_or("").into())?,
            "open_url" => self.output.open(params["url"].as_str().unwrap_or(""))?,
            "open_file" => {
                let path = std::fs::canonicalize(params["path"].as_str().unwrap_or(""))?;
                let url = format!("file://{}", path.display());
                self.output.open(&url)?;
            }
            "platform_check_updates" | "check_updates" => {
                bail!("Linux 更新由 APT 管理，请在终端运行：sudo apt update && sudo apt upgrade")
            }
            "play_recording" => self.output.play(
                params["id"].as_str().map(str::to_owned),
                params["path"].as_str().unwrap_or("").into(),
                false,
            )?,
            "mic_test_playback" => self.output.play(
                None,
                params["wav_base64"].as_str().unwrap_or("").into(),
                true,
            )?,
            "stop_recording" | "stop_mic_test_playback" => {
                self.output.stop(params["id"].as_str().map(str::to_owned))?
            }
            "end_hotkey_capture" => {}
            _ => bail!("此功能尚未接入 Linux；请从托盘打开设置查看可用功能"),
        }
        Ok(())
    }
    fn backend_event(&mut self, method: &str, params: Value, cx: &mut Context<Self>) {
        if std::env::var_os("VOCAL_MORE_TRACE_UI").is_some()
            && matches!(
                method,
                "state_changed" | "final_result" | "paste_requested" | "linux_delivery_completed"
            )
        {
            eprintln!(
                "ui lifecycle event={method} state={} next={}",
                self.state,
                params["state"].as_str().unwrap_or("-")
            );
        }
        match method {
            "initialized" | "resync" => {
                self.snapshot = params.clone();
                self.snapshot["linux_shortcuts"] = self.shortcuts_status.clone();
                self.snapshot["linux_input"] = self.input_status.clone();
                self.initialized = true;
                self.configure_learning();
                self.state = params["state"].as_str().unwrap_or("idle").into();
                self.last_text = params["last_result"]["text"].as_str().unwrap_or("").into();
                self.commands
                    .update_session(&self.state, params["generation"].as_u64().unwrap_or(0));
                if self.show_settings
                    || (!self.background
                        && self.fixture.is_none()
                        && self.snapshot["config"]["ui"]["onboarding_completed"] != true)
                {
                    self.show_settings = false;
                    self.open_settings(cx);
                }
                for paste in params["pending_pastes"].as_array().into_iter().flatten() {
                    self.backend_event("paste_requested", paste.clone(), cx);
                }
            }
            "state_changed" => {
                for key in ["state", "generation", "current_mode", "audio_input_status"] {
                    if let Some(value) = params.get(key) {
                        self.snapshot[key] = value.clone();
                    }
                }
                self.state = params["state"].as_str().unwrap_or("idle").into();
                self.commands
                    .update_session(&self.state, params["generation"].as_u64().unwrap_or(0));
                if self.state == "idle" {
                    self.gesture_recording = false;
                }
            }
            "audio_level" if self.current(&params) => {
                self.capsule_model.level = params["waveform_level"].as_f64().unwrap_or(0.) as f32;
            }
            "observation_poll" => {
                if let Some(id) = params["observation_id"].as_str() {
                    self.accessibility.observe(id);
                }
            }
            "observation_ended" => {
                if let Some(id) = params["observation_id"].as_str() {
                    self.accessibility.end(id);
                    if self.observation.as_deref() == Some(id) {
                        self.observation = None;
                    }
                }
            }
            "partial_result" if self.current(&params) => {
                self.preview = params["text"].as_str().unwrap_or("").into()
            }
            "final_result" if self.current(&params) => {
                self.last_text = params["text"].as_str().unwrap_or("").into();
                self.preview = self.last_text.clone();
                if self.snapshot["config"]["auto_paste"] != true && !self.last_text.is_empty() {
                    self.capture_before_output(
                        None,
                        json!({"text":self.last_text}),
                        self.commands.paste_epoch(),
                        self.commands.generation(),
                    );
                }
            }
            "paste_requested" if self.current(&params) => {
                let delivery = Delivery {
                    token: params["token"].as_str().unwrap_or("").into(),
                    epoch: params["_ui_epoch"].as_u64().unwrap_or(u64::MAX),
                    generation: params["generation"].as_u64().unwrap_or(0),
                };
                if let Err(error) = self.deliveries.enqueue(delivery) {
                    self.notice = error.into();
                }
                self.next_delivery();
            }
            "rpc_response" => self.response(&params, cx),
            "rpc_error" => {
                let id = params["request_id"].as_u64().unwrap_or(0);
                if let Some(pending) = self.pending.remove(&id) {
                    let delivery = match pending {
                        Pending::Claim(d) | Pending::Prepare(d, _, _) => Some(d),
                        Pending::Manual(data, _) => {
                            let epoch = data["epoch"].as_u64().unwrap_or(u64::MAX);
                            let generation = data["generation"].as_u64().unwrap_or(0);
                            if self.commands.can_paste(epoch, generation) {
                                let _ = self.deliver_text(&data, epoch, generation, false);
                            }
                            None
                        }
                    };
                    if let Some(delivery) = delivery {
                        self.deliveries.complete(&delivery);
                        self.next_delivery();
                    }
                }
                if self.screen.as_ref().is_some_and(|s| s.key.request == id) {
                    self.stop_screen();
                }
                self.notice = params["message"].as_str().unwrap_or("操作失败").into();
                if durable_method(params["method"].as_str().unwrap_or(""), &params["params"]) {
                    self.submit("snapshot", json!({}), None);
                }
            }
            "linux_delivery_completed" => {
                if params["ok"] == false
                    && params["epoch"].as_u64() == Some(self.commands.paste_epoch())
                    && params["generation"].as_u64() == Some(self.commands.generation())
                {
                    self.cancel_observation();
                }
                self.notice = params["message"].as_str().unwrap_or("").into();
                if self.delivering.as_ref().is_some_and(|d| {
                    params["epoch"].as_u64() == Some(d.epoch)
                        && params["generation"].as_u64() == Some(d.generation)
                }) && let Some(delivery) = self.delivering.take()
                {
                    self.deliveries.complete(&delivery);
                    self.next_delivery();
                }
            }
            "backend_disconnected" => {
                self.notice = format!(
                    "后端已停止：{}",
                    params["message"].as_str().unwrap_or("初始化失败")
                );
                self.state = "error".into();
                self.initialized = false;
                self.output.close();
            }
            "linux_notice" | "error" | "warning" => {
                self.notice = params["message"].as_str().unwrap_or("").into()
            }
            "config_changed" => {
                self.snapshot["config"] = params["config"].clone();
                self.configure_learning();
                if self.snapshot["config"]["screen_context_enabled"] != true {
                    self.stop_screen();
                }
            }
            "devices_changed" => {
                self.snapshot["devices"] = params["devices"].clone();
                self.snapshot["audio_input_status"] = params["audio_input_status"].clone();
            }
            "dictionary_changed" => self.snapshot["dictionary"] = params.clone(),
            "recordings_changed" => {
                self.snapshot["recordings"] = params["recordings"].clone();
                self.snapshot["recording_storage"] = params["storage"].clone();
            }
            "recording_deleted" | "recording_compaction_complete" => {
                self.submit("list_recordings", json!({}), None)
            }
            "copy_transcript" | "open_url" | "open_file" => {
                if let Err(error) = self.platform_action(method, &params) {
                    self.notice = error.to_string();
                }
            }
            "play_recording" | "mic_test_playback" | "stop_recording" => {
                if let Err(error) = self.platform_action(method, &params) {
                    self.notice = error.to_string();
                    self.forward_settings(
                        if method == "mic_test_playback" {
                            "mic_test_playback_error"
                        } else {
                            "recording_playback_error"
                        },
                        &json!({"id":params["id"],"message":self.notice}),
                        cx,
                    );
                }
            }
            _ => {}
        }
        if method == "state_changed" {
            if self.state != "idle" {
                self.error_visible = false;
                self.error_deadline = None;
            }
            self.capsule_model.state = self.state.clone();
            self.capsule_model.active = matches!(
                self.state.as_str(),
                "starting" | "recording" | "processing" | "cancelling"
            );
            self.capsule_model.text.clear();
            if self.state == "idle" && self.error_visible {
                self.capsule_model.active = true;
                self.capsule_model.state = "error".into();
                self.capsule_model.text = self.notice.clone();
            }
            if matches!(self.state.as_str(), "idle" | "processing" | "error")
                && self.screen.as_ref().is_some_and(|s| {
                    params["_ui_epoch"].as_u64() == Some(s.key.epoch)
                        && s.generation.is_some()
                        && params["generation"].as_u64() == s.generation
                })
            {
                self.stop_screen();
            }
        }
        if method == "partial_result" && self.current(&params) {
            self.capsule_model.text = self.preview.clone();
        }
        if method == "error"
            || method == "backend_disconnected"
            || (method == "rpc_error"
                && matches!(
                    params["method"].as_str(),
                    Some("start" | "toggle_recording" | "hotkey_pressed")
                ))
        {
            self.capsule_model.active = true;
            self.capsule_model.state = "error".into();
            self.capsule_model.text = self.notice.clone();
            self.error_visible = true;
            self.error_deadline = Some(Instant::now() + Duration::from_secs(4));
        }
        self.forward_settings(method, &params, cx);
    }
    fn current(&self, params: &Value) -> bool {
        params["generation"] == self.snapshot["generation"]
            && params["_ui_epoch"].as_u64() == Some(self.commands.paste_epoch())
    }
    fn response(&mut self, response: &Value, cx: &mut Context<Self>) {
        let id = response["request_id"].as_u64().unwrap_or(0);
        let result = &response["result"];
        if let Some(screen) = &mut self.screen
            && screen.key.request == id
        {
            screen.generation = result["generation"].as_u64();
            self.flush_screen();
        }
        match self.pending.remove(&id) {
            Some(Pending::Claim(delivery)) => {
                if result["cancelled"] == true
                    || result["token"] != delivery.token
                    || result["generation"].as_u64() != Some(delivery.generation)
                    || !self.commands.can_paste(delivery.epoch, delivery.generation)
                {
                    self.deliveries.complete(&delivery);
                    self.next_delivery();
                    return;
                }
                if self.snapshot["config"]["auto_paste"] == true
                    && self.wayland_failed != Some(delivery.epoch)
                {
                    let epoch = delivery.epoch;
                    let generation = delivery.generation;
                    self.capture_before_output(Some(delivery), result.clone(), epoch, generation);
                } else {
                    self.submit("discard_paste", json!({"token":delivery.token}), None);
                    self.deliveries.complete(&delivery);
                    self.next_delivery();
                }
            }
            Some(Pending::Prepare(delivery, data, snapshot)) => {
                if result["cancelled"] != true
                    && self.commands.can_paste(delivery.epoch, delivery.generation)
                {
                    self.retain_observation(result, &snapshot);
                    if self
                        .deliver_text(&data, delivery.epoch, delivery.generation, true)
                        .is_ok()
                    {
                        self.delivering = Some(delivery);
                        return;
                    }
                }
                self.cancel_prepared(result);
                self.deliveries.complete(&delivery);
                self.next_delivery();
            }
            Some(Pending::Manual(data, snapshot)) => {
                let epoch = data["epoch"].as_u64().unwrap_or(u64::MAX);
                let generation = data["generation"].as_u64().unwrap_or(0);
                if self.commands.can_paste(epoch, generation) {
                    self.retain_observation(result, &snapshot);
                    if self.deliver_text(&data, epoch, generation, false).is_err() {
                        self.cancel_observation();
                    }
                } else if let Some(id) = result["observation_id"].as_str() {
                    self.submit("cancel_observation", json!({"observation_id":id}), None);
                }
            }
            None if response["method"] == "snapshot" => {
                self.backend_event("resync", result.clone(), cx)
            }
            _ => {}
        }
    }
    fn configure_learning(&mut self) {
        let learning = &self.snapshot["config"]["dictionary_learning"];
        self.insertion.exclude(
            learning["excluded_bundle_ids"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect(),
        );
        self.accessibility.configure(
            learning["enabled"] == true,
            learning["excluded_bundle_ids"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|s| s.as_str().map(str::to_owned))
                .collect(),
        );
        if learning["enabled"] != true {
            self.cancel_observation();
        }
    }
    fn cancel_prepared(&mut self, result: &Value) {
        if let Some(id) = result["observation_id"].as_str() {
            self.accessibility.end(id);
            if self.observation.as_deref() == Some(id) {
                self.observation = None;
            }
            self.submit("cancel_observation", json!({"observation_id":id}), None);
        }
    }
    fn cancel_observation(&mut self) {
        if let Some(id) = self.observation.take() {
            self.accessibility.end(&id);
            self.submit("cancel_observation", json!({"observation_id":id}), None);
        }
    }
    fn retain_observation(&mut self, result: &Value, snapshot: &Value) {
        if let Some(id) = result["observation_id"].as_str() {
            self.accessibility.retain(id, snapshot);
            self.observation = Some(id.into());
        }
    }
    fn capture_before_output(
        &mut self,
        delivery: Option<Delivery>,
        data: Value,
        epoch: u64,
        generation: u64,
    ) {
        let should_observe = delivery.is_none() || data["observe_correction"] == true;
        self.capture_ticket += 1;
        let ticket = self.capture_ticket;
        let capture = Capture {
            delivery,
            data,
            epoch,
            generation,
            expires: Instant::now() + accessibility::CAPTURE_BUDGET,
        };
        if self.session == Session::Wayland && capture.delivery.is_some() {
            let snapshot = if self.input_epoch == epoch {
                self.input_snapshot.clone()
            } else {
                Value::Null
            };
            self.prepare_output(capture, snapshot);
            return;
        }
        if should_observe
            && self.captures.len() < 8
            && self.snapshot["config"]["dictionary_learning"]["enabled"] == true
            && self.accessibility.capture(ticket).is_ok()
        {
            self.captures.insert(ticket, capture);
        } else {
            self.prepare_output(capture, Value::Null);
        }
    }
    fn prepare_output(&mut self, capture: Capture, snapshot: Value) {
        if !self.commands.can_paste(capture.epoch, capture.generation) {
            if let Some(d) = capture.delivery {
                self.deliveries.complete(&d);
                self.next_delivery();
            }
            return;
        }
        if let Some(delivery) = capture.delivery {
            self.submit(
                "prepare_paste_observation",
                json!({"token":delivery.token,"snapshot":snapshot}),
                Some(Pending::Prepare(delivery, capture.data, snapshot)),
            );
        } else if !snapshot.is_null() {
            let mut data = capture.data;
            data["epoch"] = json!(capture.epoch);
            data["generation"] = json!(capture.generation);
            self.submit(
                "prepare_manual_observation",
                json!({"generation":capture.generation,"snapshot":snapshot}),
                Some(Pending::Manual(data, snapshot)),
            );
        } else {
            let _ = self.deliver_text(&capture.data, capture.epoch, capture.generation, false);
        }
    }
    fn deliver_text(
        &mut self,
        data: &Value,
        epoch: u64,
        generation: u64,
        automatic: bool,
    ) -> Result<()> {
        if self.session == Session::Wayland && automatic {
            return self.insertion.insert(
                epoch,
                generation,
                data["text"].as_str().unwrap_or("").into(),
            );
        }
        let result = self.output.deliver(DeliveryJob {
            text: data["text"].as_str().unwrap_or("").into(),
            epoch,
            generation,
            auto_paste: automatic,
            restore: automatic && data["restore_clipboard"] == true,
        });
        if let Err(error) = &result {
            self.notice = error.to_string();
        }
        result
    }
    fn stop_screen(&mut self) {
        self.screen.take();
        self.screen_worker.stop();
    }
    fn flush_screen(&mut self) {
        if let Some(screen) = &mut self.screen
            && screen.key.epoch == self.commands.paste_epoch()
            && let Some(generation) = screen.generation
            && let Some(jpeg) = screen.initial.take()
        {
            self.submit(
                "append_screen_frame",
                json!({"generation":generation,"jpeg_base64":STANDARD.encode(jpeg)}),
                None,
            );
        }
    }
    fn update_capsule(&mut self, cx: &mut Context<Self>) {
        self.capsule_model.english = self.snapshot["config"]["ui"]["language"] == "en";
        self.capsule_model.reduced_motion = cx.reduce_motion();
        self.capsule_model.bound();
        if let Some(shell) = &self.shell {
            if shell.locked() {
                self.capsule_model.active = false;
                self.error_visible = false;
                self.error_deadline = None;
            }
            shell.update(self.capsule_model.clone());
            if self.capsule_model.active
                && !shell.available()
                && !self.popup_capable
                && self.notice.is_empty()
            {
                self.notice = "GNOME Wayland 悬浮胶囊需要启用 Vocal More Capsule 扩展".into();
                cx.notify();
            }
        }
        let use_popup =
            self.popup_capable && self.shell.as_ref().is_none_or(|shell| !shell.available());
        if self.capsule_model.active && use_popup {
            if self.capsule.is_none() {
                match capsule::open(self.capsule_model.clone(), self.commands.clone(), cx) {
                    Ok(view) => self.capsule = Some(view),
                    Err(error) => self.notice = error.to_string(),
                }
            }
            if let Some((_, view)) = &self.capsule {
                view.update(cx, |view, cx| {
                    view.model = self.capsule_model.clone();
                    view.tick(cx);
                });
            }
        } else if let Some((window, _)) = self.capsule.take() {
            let _ = window.update(cx, |_, window, _| window.remove_window());
        }
    }
    fn next_delivery(&mut self) {
        if self.delivering.is_some() {
            return;
        }
        if let Some(delivery) = self
            .deliveries
            .next(self.commands.paste_epoch(), self.commands.generation())
        {
            self.submit(
                "claim_paste",
                json!({"token":delivery.token}),
                Some(Pending::Claim(delivery)),
            );
        }
    }
    #[track_caller]
    fn open_settings(&mut self, cx: &mut Context<Self>) {
        if std::env::var_os("VOCAL_MORE_TRACE_UI").is_some() {
            eprintln!(
                "ui settings-request state={} caller={}",
                self.state,
                std::panic::Location::caller()
            );
        }
        if let Some(worker) = &self.shortcuts {
            let _ = worker.request(0, "linux_shortcuts_refresh", json!({}));
        }
        if !self.initialized {
            self.notice = "后端正在初始化，请稍后打开设置".into();
            return;
        }
        if let Some((_, settings)) = &self.settings {
            settings.update(cx, |settings, cx| {
                settings.snapshot["linux_notice"] = json!(self.notice);
                cx.notify();
            });
        }
        if let Some((window, _)) = &self.settings
            && window
                .update(cx, |_, window, _| window.activate_window())
                .is_ok()
        {
            return;
        }
        self.submit("refresh_devices", json!({}), None);
        self.snapshot["linux_notice"] = json!(self.notice);
        match settings::open(self.snapshot.clone(), self.commands.clone(), cx) {
            Ok(settings) => self.settings = Some(settings),
            Err(error) => self.notice = error.to_string(),
        }
    }
    fn forward_settings(&self, method: &str, params: &Value, cx: &mut Context<Self>) {
        if let Some((window, settings)) = &self.settings {
            let _ = window.update(cx, |_, window, cx| {
                settings.update(cx, |settings, cx| {
                    settings.on_event(method, params, window, cx)
                });
            });
        }
    }
    fn begin_quit(&mut self, cx: &mut Context<Self>) {
        if self.closing {
            return;
        }
        if let Some((_, settings)) = self.settings.take() {
            settings.update(cx, |settings, cx| {
                if settings.flush(cx).is_err() {
                    self.save_failures += 1;
                }
                settings.close();
            });
        }
        self.closing = true;
        let mut durable = Vec::new();
        while let Ok(event) = self.events.try_recv() {
            match event {
                UiEvent::Request(request)
                    if matches!(
                        request.method.as_str(),
                        "linux_shortcuts_save"
                            | "linux_shortcuts_remove"
                            | "linux_shortcuts_enable_gestures"
                            | "linux_shortcuts_disable_gestures"
                    ) =>
                {
                    self.shortcut_request(request, cx)
                }
                UiEvent::Request(request)
                    if matches!(
                        request.method.as_str(),
                        "linux_input_enable" | "linux_input_disable" | "linux_input_codex"
                    ) =>
                {
                    self.request(request, cx)
                }
                UiEvent::Request(request) if durable_request(&request) => durable.push(request),
                UiEvent::Backend { method, params, .. }
                    if method == "rpc_error"
                        && durable_method(
                            params["method"].as_str().unwrap_or(""),
                            &params["params"],
                        ) =>
                {
                    self.save_failures += 1
                }
                _ => {}
            }
        }
        self.shortcut_capture = false;
        self.gestures.close();
        if let Some(worker) = &mut self.shortcuts {
            worker.close();
        }
        self.state = "saving".into();
        self.deliveries.clear();
        self.pending.clear();
        self.output.close();
        self.stop_screen();
        self.screen_worker.close();
        self.accessibility.close();
        self.insertion.close();
        if let Some(shell) = &self.shell {
            shell.close();
        }
        self.capsule_model.active = false;
        self.update_capsule(cx);
        if let Err(error) = self.driver.close_with_requests(durable) {
            self.notice = error.to_string();
            self.save_failures += 1;
            self.driver.close();
        }
        cx.notify();
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        self.tray.update(tray::Model {
            state: self.state.clone(),
            notice: self.notice.clone(),
            english: self.snapshot["config"]["ui"]["language"] == "en",
        });
        if !self.closing
            && self.initialized
            && !self.tray_warning_shown
            && self.started.elapsed() > Duration::from_secs(3)
        {
            self.tray_warning_shown = true;
            if !self.tray.available() {
                self.notice = "系统托盘服务不可用。可从应用启动入口重新打开设置；请启用桌面的状态栏/托盘支持。".into();
                if !self.background && self.state == "idle" {
                    self.open_settings(cx);
                }
            }
        }
        // Drain reserved replies even during shutdown so the bounded owner can
        // finish every admitted settings write without waiting on a closed UI.
        while let Some(reply) = self.shortcuts.as_ref().and_then(|worker| worker.poll()) {
            let mut event = json!({"request_id":reply.request});
            match reply.result {
                Ok(mut status) => {
                    if self.gestures.configure(&status) {
                        self.gesture_active = false;
                        if status["gesture_enabled"] == true {
                            self.gesture_message = "正在等待系统快捷键授权".into();
                        }
                    } else if self.gesture_active
                        && let Some(items) = status["items"].as_array_mut()
                    {
                        for item in items {
                            if let Some(old) = self.shortcuts_status["items"]
                                .as_array()
                                .and_then(|old| old.iter().find(|old| old["id"] == item["id"]))
                            {
                                item["display_name"] = old["display_name"].clone();
                            }
                        }
                    }
                    self.shortcuts_status = status;
                    if self.shortcuts_status["gesture_enabled"] != true {
                        self.gesture_active = false;
                    }
                    self.shortcuts_status["gesture_active"] = json!(self.gesture_active);
                    self.shortcuts_status["gesture_message"] = json!(self.gesture_message);
                    self.snapshot["linux_shortcuts"] = self.shortcuts_status.clone();
                    event["status"] = self.shortcuts_status.clone();
                }
                Err(error) => {
                    event["error"] = json!(error.to_string());
                    if reply.request == 0 {
                        self.shortcuts_status =
                            json!({"available":false,"items":[],"error":error.to_string()});
                        event["status"] = self.shortcuts_status.clone();
                        self.snapshot["linux_shortcuts"] = self.shortcuts_status.clone();
                    }
                    if self.closing && reply.durable {
                        self.save_failures += 1;
                    }
                }
            }
            self.forward_settings("linux_shortcuts_changed", &event, cx);
            cx.notify();
        }
        while let Some(event) = self.gestures.poll() {
            self.gesture_active = event.active;
            self.gesture_message = event.message.clone();
            self.shortcuts_status["gesture_active"] = json!(event.active);
            self.shortcuts_status["gesture_message"] = json!(event.message);
            for (id, label) in event.labels {
                if let Some(items) = self.shortcuts_status["items"].as_array_mut() {
                    for item in items {
                        if item["id"] == id {
                            item["display_name"] = json!(label);
                        }
                    }
                }
            }
            self.snapshot["linux_shortcuts"] = self.shortcuts_status.clone();
            self.forward_settings(
                "linux_shortcuts_changed",
                &json!({"status":self.shortcuts_status}),
                cx,
            );
            if !event.active && !self.closing {
                self.notice = format!("按住说话未启用：{}；已恢复一键切换", self.gesture_message);
                if let Some(worker) = &self.shortcuts {
                    let _ = worker.request(0, "linux_shortcuts_disable_gestures", json!({}));
                }
            }
        }
        if self.shortcut_capture
            && !self.settings.as_ref().is_some_and(|(window, _)| {
                window
                    .update(cx, |_, window, _| window.is_window_active())
                    .unwrap_or(false)
            })
        {
            self.shortcut_capture = false;
            self.forward_settings("linux_shortcut_capture_cancelled", &json!({}), cx);
        }
        while let Some(event) = self.insertion.poll() {
            match event {
                insertion::Event::Compatibility { request, result } => {
                    let mut event = json!({"request_id":request});
                    match result {
                        Ok(status) => {
                            self.input_status = status.clone();
                            self.snapshot["linux_input"] = status.clone();
                            event["status"] = status;
                        }
                        Err(error) => {
                            event["error"] = json!(error.to_string());
                            if self.closing && request != 0 {
                                self.save_failures += 1;
                            }
                        }
                    }
                    self.forward_settings("linux_input_changed", &event, cx);
                }
                insertion::Event::Captured { epoch, snapshot } => {
                    if self.test_audio.is_some() {
                        eprintln!(
                            "Acceptance input capture: epoch={epoch}, target={}",
                            !snapshot.is_null()
                        );
                    }
                    if epoch == self.input_epoch && epoch == self.commands.paste_epoch() {
                        self.input_snapshot = snapshot;
                    }
                }
                insertion::Event::Completed {
                    epoch,
                    generation,
                    result,
                } => {
                    if !self.commands.can_paste(epoch, generation) {
                        continue;
                    }
                    let ok = result.is_ok();
                    if self.test_audio.is_some() {
                        eprintln!(
                            "Acceptance native delivery: {}",
                            result
                                .as_ref()
                                .map(|_| "confirmed".to_owned())
                                .unwrap_or_else(|e| e.to_string())
                        );
                    }
                    let message = match result {
                        Ok(snapshot) => {
                            let terminal = snapshot["terminal"] == true;
                            self.input_snapshot = snapshot;
                            if terminal {
                                "已发送到原终端，剪贴板已恢复"
                            } else {
                                "已插入原输入位置，剪贴板未改变"
                            }
                            .to_owned()
                        }
                        Err(error) => {
                            self.wayland_failed = Some(epoch);
                            self.error_visible = true;
                            self.error_deadline = Some(Instant::now() + Duration::from_secs(4));
                            self.capsule_model.active = true;
                            self.capsule_model.state = "error".into();
                            let message = format!(
                                "自动插入未完成：{error}。剪贴板已保留，可从托盘复制完整结果。"
                            );
                            self.capsule_model.text = message.clone();
                            message
                        }
                    };
                    self.backend_event(
                        "linux_delivery_completed",
                        json!({"epoch":epoch,"generation":generation,"ok":ok,"message":message}),
                        cx,
                    );
                }
            }
        }
        if self.closing {
            if self.gestures.finished()
                && self.driver.finished()
                && self.output.finished()
                && self
                    .shortcuts
                    .as_ref()
                    .is_none_or(|worker| worker.finished())
                && self.screen_worker.finished()
                && self.accessibility.finished()
                && self.insertion.finished()
                && self.shell.as_ref().is_none_or(|s| s.finished())
            {
                let failures = self.driver.shutdown_failures();
                if self.save_failures > 0
                    || failures.failed_durable_requests > 0
                    || failures.cleanup_failed
                {
                    self.notice = "退出时有更改未能保存，请检查日志后重新启动".into();
                    if self.shutdown_notice_at.is_none() {
                        self.shutdown_notice_at = Some(std::time::Instant::now());
                        eprintln!("Vocal More: {}", self.notice);
                        cx.notify();
                    }
                    if self
                        .shutdown_notice_at
                        .is_some_and(|at| at.elapsed() < Duration::from_secs(4))
                    {
                        return;
                    }
                }
                self.tray.close();
                if self.tray.finished() {
                    self.control.take();
                    cx.quit();
                }
            }
            return;
        }
        while let Some(frame) = self.screen_worker.poll() {
            if self
                .screen
                .as_ref()
                .is_some_and(|s| s.key == frame.key && s.key.epoch == self.commands.paste_epoch())
            {
                match frame.jpeg {
                    Ok(jpeg) => {
                        if let Some(s) = &mut self.screen {
                            s.initial = Some(jpeg);
                        }
                        self.flush_screen();
                    }
                    Err(error) => {
                        self.notice = format!("屏幕上下文未启用：{error}");
                        self.stop_screen();
                        cx.notify();
                    }
                }
            }
        }
        if self.screen.as_ref().is_some_and(|s| {
            s.started.elapsed() > Duration::from_secs(230)
                || s.key.epoch != self.commands.paste_epoch()
        }) {
            self.stop_screen();
        }
        while let Some(event) = self.accessibility.poll() {
            match event {
                accessibility::Event::Captured { ticket, value } => {
                    if let Some(capture) = self.captures.remove(&ticket) {
                        let snapshot = if Instant::now() < capture.expires {
                            value
                        } else {
                            Value::Null
                        };
                        self.prepare_output(capture, snapshot);
                    }
                }
                accessibility::Event::Polled {
                    id,
                    focused,
                    retained,
                } => {
                    if self.observation.as_deref() == Some(&id) {
                        self.submit(
                            "poll_observation",
                            json!({"observation_id":id,"focused":focused,"retained":retained}),
                            None,
                        );
                    }
                }
            }
        }
        let expired: Vec<_> = self
            .captures
            .iter()
            .filter(|(_, c)| Instant::now() >= c.expires)
            .map(|(id, _)| *id)
            .collect();
        for ticket in expired {
            if let Some(capture) = self.captures.remove(&ticket) {
                self.prepare_output(capture, Value::Null);
            }
        }
        if self.state == "idle"
            && self
                .error_deadline
                .is_some_and(|deadline| Instant::now() >= deadline)
        {
            self.error_deadline = None;
            self.error_visible = false;
            self.capsule_model.active = false;
        }
        if let Some(started) = self.fixture {
            let elapsed = started.elapsed().as_secs();
            self.capsule_model = capsule::Model {
                active: (2..8).contains(&elapsed),
                state: if elapsed < 4 {
                    "recording"
                } else {
                    "processing"
                }
                .into(),
                text: if elapsed < 4 {
                    "低声输入 · Linux 悬浮胶囊"
                } else {
                    "正在整理识别结果…"
                }
                .into(),
                level: 0.68,
                ..Default::default()
            };
        }
        self.update_capsule(cx);
        while let Some(result) = self.output.completion() {
            self.backend_event("linux_delivery_completed", result, cx);
            cx.notify();
        }
        if let Some(control) = &self.control {
            control.poll(&self.commands);
        }
        if self.commands.take_quit_request() {
            self.begin_quit(cx);
        }
        if let Some(error) = self.commands.take_overflow() {
            self.backend_event("rpc_error", error, cx);
            cx.notify();
        }
    }
}
