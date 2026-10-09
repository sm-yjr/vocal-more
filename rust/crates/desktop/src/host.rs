// SPDX-License-Identifier: GPL-3.0-only
//! Main-thread presentation and platform policy. Business state stays in the
//! backend; stale screen frames and paste work never cross session boundaries.
use crate::{
    bridge::{BackendDriver, CommandSink, Request, UiEvent, durable_method, durable_request},
    capsule::Capsule,
    delivery::{Delivery, PasteLane},
    options::DesktopOptions,
    platform::{self, Platform},
    settings::{self, Settings},
};
use anyhow::{Context as _, Result};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use gpui_kit::{AnyWindowHandle, App, AppContext, Context, Entity, WindowId};
use objc2::MainThreadMarker;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    path::PathBuf,
    time::{Duration, Instant},
};

struct PasteWork {
    data: Value,
    epoch: u64,
    snapshot: Value,
    waiting_for_ax: Option<Instant>,
}
impl PasteWork {
    fn accepts_ax(&self, request: &Value) -> bool {
        self.accepts_ax_at(request, Instant::now())
    }
    fn accepts_ax_at(&self, request: &Value, now: Instant) -> bool {
        self.waiting_for_ax.is_some_and(|deadline| now < deadline)
            && request["epoch"].as_u64() == Some(self.epoch)
            && request["generation"] == self.data["generation"]
    }
    fn ax_expired(&self, now: Instant) -> bool {
        self.waiting_for_ax.is_some_and(|deadline| now >= deadline)
    }
}
enum Pending {
    Claim(Delivery),
    Prepare(Delivery),
    SnapshotForSettings(String),
}
struct ScreenSession {
    request: u64,
    epoch: u64,
    generation: Option<u64>,
    initial: Option<String>,
    started: Instant,
    last_capture: Instant,
}
impl ScreenSession {
    fn matches_frame(&self, frame: &Value) -> bool {
        frame["request"].as_u64() == Some(self.request)
            && frame["epoch"].as_u64() == Some(self.epoch)
    }
    fn take_initial(&mut self) -> Option<(u64, String)> {
        let generation = self.generation?;
        self.initial.take().map(|jpeg| (generation, jpeg))
    }
}
struct ScreenJob {
    serial: u64,
    request: u64,
    epoch: u64,
    generation: Option<u64>,
    initial: bool,
}
struct ScreenWorker {
    sender: Option<std::sync::mpsc::SyncSender<ScreenJob>>,
}
impl ScreenWorker {
    fn new(commands: CommandSink) -> Self {
        let (sender, receiver) = std::sync::mpsc::sync_channel::<ScreenJob>(1);
        std::thread::Builder::new().name("vocal-more-screen-context".into()).spawn(move || {
            while let Ok(job)=receiver.recv() {
                let result=platform::capture_screen(job.initial);
                let params=match result {
                    Ok(jpeg)=>json!({"serial":job.serial,"request":job.request,"epoch":job.epoch,"generation":job.generation,
                        "initial":job.initial,"jpeg_base64":STANDARD.encode(jpeg)}),
                    Err(error)=>json!({"serial":job.serial,"request":job.request,"epoch":job.epoch,"generation":job.generation,
                        "initial":job.initial,"error":error.to_string()}),
                };
                commands.request("platform_event",json!({"method":"screen_frame","params":params}));
            }
        }).expect("screen worker creation failed");
        Self {
            sender: Some(sender),
        }
    }
    fn submit(&self, job: ScreenJob) -> bool {
        self.sender
            .as_ref()
            .is_some_and(|sender| sender.try_send(job).is_ok())
    }
    fn close(&mut self) {
        self.sender.take();
    }
}

pub struct DesktopHost {
    driver: BackendDriver,
    events: async_channel::Receiver<UiEvent>,
    commands: CommandSink,
    capsule: Capsule,
    platform: Option<Platform>,
    termination: platform::TerminationGate,
    settings: Option<(AnyWindowHandle, Entity<Settings>)>,
    snapshot: Value,
    no_hotkeys: bool,
    show_settings_on_start: bool,
    pending: HashMap<u64, Pending>,
    pastes: HashMap<String, PasteWork>,
    deliveries: PasteLane,
    native_delivery: Option<Delivery>,
    activity: Option<gpui_kit::ActivityGuard>,
    hotkey_start: Option<(u64, u64, Instant)>,
    last_text: String,
    prompt_hint: String,
    screen: Option<ScreenSession>,
    screen_worker: ScreenWorker,
    screen_serial: u64,
    // A frame dropped by a full UI queue must not block capture forever.
    screen_inflight: Option<(u64, Instant)>,
    watchdog: Instant,
    wake: async_channel::Sender<()>,
    closing: bool,
    shutdown_failed_requests: usize,
}

impl DesktopHost {
    pub fn start(options: DesktopOptions, cx: &mut App) -> Result<Entity<Self>> {
        let DesktopOptions {
            backend,
            no_hotkeys,
            show_settings,
            quit_after_ms,
            capsule_fixtures: _,
        } = options;
        let (driver, commands, events) = BackendDriver::start(backend)?;
        let (wake, wake_rx) = async_channel::bounded(1);
        let capsule = Capsule::new(
            MainThreadMarker::new().context("desktop must run on the macOS main thread")?,
            commands.clone(),
        )?;
        let screen_worker = ScreenWorker::new(commands.clone());
        let termination = platform::TerminationGate::install(
            MainThreadMarker::new().context("desktop must run on the macOS main thread")?,
            commands.clone(),
        )?;
        let host = cx.new(|cx: &mut Context<Self>| {
            let this = Self {
                driver,
                events: events.clone(),
                commands,
                capsule,
                platform: None,
                termination,
                settings: None,
                snapshot: json!({}),
                no_hotkeys,
                show_settings_on_start: show_settings,
                pending: HashMap::new(),
                pastes: HashMap::new(),
                deliveries: PasteLane::default(),
                native_delivery: None,
                activity: None,
                hotkey_start: None,
                last_text: String::new(),
                prompt_hint: String::new(),
                screen: None,
                screen_worker,
                screen_serial: 0,
                screen_inflight: None,
                watchdog: Instant::now(),
                wake,
                closing: false,
                shutdown_failed_requests: 0,
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
                loop {
                    let duration = match host.update(cx, |host, _| host.tick_interval()) {
                        Ok(value) => value,
                        Err(_) => break,
                    };
                    let timer = cx.background_executor().timer(duration);
                    futures_lite::future::or(
                        async {
                            timer.await;
                        },
                        async {
                            let _ = wake_rx.recv().await;
                        },
                    )
                    .await;
                    if host.update(cx, |host, cx| host.tick(cx)).is_err() {
                        break;
                    }
                }
            })
            .detach();
            this
        });
        let quit_host = host.clone();
        cx.on_app_quit(move |cx| {
            quit_host.update(cx, |host, cx| host.close(cx));
            async {}
        })
        .detach();
        let closed_host = host.clone();
        cx.on_window_closed(move |cx, id| {
            closed_host.update(cx, |host, cx| host.window_closed(id, cx));
        })
        .detach();
        if let Some(milliseconds) = quit_after_ms {
            let timed_host = host.clone();
            cx.spawn(async move |cx| {
                cx.background_executor()
                    .timer(Duration::from_millis(milliseconds))
                    .await;
                timed_host.update(cx, |host, cx| host.begin_quit(cx));
            })
            .detach();
        }
        Ok(host)
    }
    fn event(&mut self, event: UiEvent, cx: &mut Context<Self>) {
        if self.closing {
            self.retain_shutdown_error(&event);
            return;
        }
        match event {
            UiEvent::Request(request) => self.request(request, cx),
            UiEvent::Backend {
                method,
                params,
                queued_at,
            } => {
                if matches!(
                    method.as_str(),
                    "state_changed" | "partial_result" | "final_result" | "paste_requested"
                ) {
                    vocal_more_core::diagnostics::record(
                        vocal_more_core::diagnostics::Stage::BackendToMain,
                        queued_at.elapsed(),
                    );
                }
                self.backend_event(&method, params, cx)
            }
        }
        if !self.closing && self.commands.take_quit_request() {
            self.begin_quit(cx);
            return;
        }
        if let Some(error) = self.commands.take_overflow() {
            self.backend_event("rpc_error", error, cx);
        }
    }
    fn send(&mut self, method: &str, params: Value, pending: Option<Pending>) -> u64 {
        match self.commands.request_checked(method, params) {
            Ok(id) => {
                if let Some(pending) = pending {
                    self.pending.insert(id, pending);
                }
                id
            }
            Err(error) => {
                if let Some(Pending::Claim(delivery) | Pending::Prepare(delivery)) = pending {
                    self.pastes.remove(&delivery.token);
                    self.complete_delivery(&delivery);
                }
                self.notify(&error.to_string());
                0
            }
        }
    }
    fn request(&mut self, mut request: Request, cx: &mut Context<Self>) {
        if matches!(
            request.method.as_str(),
            "hotkey_pressed" | "hotkey_released" | "toggle_recording"
        ) {
            vocal_more_core::diagnostics::record(
                vocal_more_core::diagnostics::Stage::HotkeyToMain,
                request.admitted_at.elapsed(),
            );
            if self.snapshot["state"] == "idle" && request.method != "hotkey_released" {
                self.hotkey_start = Some((request.id, request.epoch, request.admitted_at));
                // The nonactivating accessory app is often entirely occluded.
                // Acquire activity as soon as this user intent reaches AppKit,
                // keeping timers/network responsive through result delivery.
                if self.activity.is_none() {
                    self.activity = Some(
                        cx.background_executor()
                            .prevent_app_nap("Vocal More dictation"),
                    );
                }
            }
        }
        if request.method == "platform_event" {
            let method = request.params["method"].as_str().unwrap_or("").to_owned();
            self.backend_event(&method, request.params["params"].clone(), cx);
            return;
        }
        if matches!(request.method.as_str(), "platform_quit" | "quit") {
            self.begin_quit(cx);
            return;
        }
        if matches!(
            request.method.as_str(),
            "platform_show_settings" | "show_settings"
        ) {
            let tab = request.params["tab"].as_str().unwrap_or("").to_owned();
            self.send("refresh_devices", json!({}), None);
            self.send(
                "snapshot",
                json!({}),
                Some(Pending::SnapshotForSettings(tab)),
            );
            return;
        }
        if request.method == "platform_copy_last" {
            if let Some(platform) = &self.platform {
                let _ = platform.copy_text(&self.last_text);
            }
            return;
        }
        let action = self
            .platform
            .as_mut()
            .map(|platform| platform.action(&request.method, &request.params));
        match action {
            Some(Ok(true)) => {
                self.backend_event(
                    "rpc_response",
                    json!({
                        "request_id":request.id,"_ui_epoch":request.epoch,
                        "method":request.method,"params":request.params,"result":{"ok":true}
                    }),
                    cx,
                );
                return;
            }
            Some(Err(error)) => {
                self.backend_event(
                    "rpc_error",
                    json!({
                        "request_id":request.id,"_ui_epoch":request.epoch,
                        "method":request.method,"params":request.params,"message":error.to_string()
                    }),
                    cx,
                );
                return;
            }
            _ => {}
        }
        if matches!(
            request.method.as_str(),
            "start" | "hotkey_pressed" | "toggle_recording"
        ) && self.snapshot["state"] == "idle"
            && self.snapshot["config"]["screen_context_enabled"] == true
            && self.screen.is_none()
        {
            request.params["screen_context"] = json!(true);
            let now = Instant::now();
            self.screen = Some(ScreenSession {
                request: request.id,
                epoch: request.epoch,
                generation: None,
                initial: None,
                started: now,
                last_capture: now,
            });
            self.capture_screen(true);
        }
        if request.method == "cancel" {
            self.hotkey_start.take();
            self.screen = None;
            self.pastes.clear();
            self.deliveries.clear();
            self.capsule.hide();
        }
        self.driver.send(request);
    }
    fn prompt_enabled(&self) -> bool {
        self.snapshot["config"]["enable_polish"] == true
            && self.snapshot["config"]["llm"]["polish_mode"] == "prompt"
    }
    fn backend_event(&mut self, method: &str, params: Value, cx: &mut Context<Self>) {
        let _ui_span = match method {
            "state_changed" => Some(vocal_more_core::diagnostics::Span::new(
                vocal_more_core::diagnostics::Stage::StateUiUpdate,
            )),
            "partial_result" => Some(vocal_more_core::diagnostics::Span::new(
                vocal_more_core::diagnostics::Stage::PreviewUiUpdate,
            )),
            _ => None,
        };
        match method {
            "initialized" => {
                self.snapshot = params.clone();
                self.last_text = params["last_result"]["text"].as_str().unwrap_or("").into();
                self.commands.update_session(
                    params["state"].as_str().unwrap_or("idle"),
                    params["generation"].as_u64().unwrap_or(0),
                );
                self.capsule.set_language(language(&self.snapshot));
                match Platform::new(
                    MainThreadMarker::new().unwrap(),
                    self.commands.clone(),
                    &params["config"],
                    self.no_hotkeys,
                ) {
                    Ok(mut platform) => {
                        platform.set_has_result(!self.last_text.is_empty());
                        platform.update_snapshot(&self.snapshot);
                        self.platform = Some(platform);
                    }
                    Err(error) => self.notify(&error.to_string()),
                }
                self.platform_status();
                if self.show_settings_on_start {
                    self.open_settings("", cx);
                }
            }
            "rpc_response" => self.response(&params, cx),
            "rpc_error" => {
                if matches!(
                    params["method"].as_str(),
                    Some("hotkey_pressed" | "toggle_recording")
                ) && self.hotkey_start.is_some_and(|(id, epoch, _)| {
                    params["request_id"].as_u64() == Some(id)
                        && params["_ui_epoch"].as_u64() == Some(epoch)
                }) {
                    self.hotkey_start.take();
                    self.update_activity(cx);
                }
                let id = params["request_id"].as_u64().unwrap_or(0);
                if let Some(Pending::Claim(delivery) | Pending::Prepare(delivery)) =
                    self.pending.remove(&id)
                {
                    self.pastes.remove(&delivery.token);
                    self.complete_delivery(&delivery);
                }
                if self
                    .screen
                    .as_ref()
                    .is_some_and(|screen| screen.request == id)
                {
                    self.screen = None;
                }
                if params["method"] != "append_screen_frame" {
                    self.notify(params["message"].as_str().unwrap_or("Operation failed"));
                }
                if matches!(
                    params["method"].as_str(),
                    Some(
                        "set_config"
                            | "set_asr_model"
                            | "set_device"
                            | "set_active_hotkeys"
                            | "sync_form_state"
                    )
                ) {
                    self.send("snapshot", json!({}), None);
                }
            }
            "state_changed" => {
                merge(&mut self.snapshot, &params);
                let state = params["state"].as_str().unwrap_or("idle");
                self.commands
                    .update_session(state, params["generation"].as_u64().unwrap_or(0));
                if params["microphone_test"] != true && self.event_epoch(&params) {
                    if state == "starting" {
                        let mode = if params["current_mode"] == "realtime_long" {
                            "handsFree"
                        } else {
                            "pushToTalk"
                        };
                        self.capsule
                            .show(mode, self.prompt_enabled(), &self.prompt_hint);
                        if let Some((_, _, start)) = self.hotkey_start.take() {
                            vocal_more_core::diagnostics::record(
                                vocal_more_core::diagnostics::Stage::CapsuleShow,
                                start.elapsed(),
                            );
                        }
                    } else if state == "idle" {
                        if self.screen.as_ref().is_some_and(|screen| {
                            screen.epoch == params["_ui_epoch"].as_u64().unwrap_or(u64::MAX)
                                && screen.generation == params["generation"].as_u64()
                        }) {
                            self.screen = None;
                        }
                        if !self.capsule.is_failure() {
                            self.capsule.update_state("hidden");
                        }
                    } else {
                        self.capsule.update_state(if state == "cancelling" {
                            "processing"
                        } else {
                            state
                        });
                    }
                }
                if let Some(platform) = &mut self.platform {
                    // A session transition changes only the status item. Full
                    // menu reconstruction and hotkey/updater configuration
                    // belong to config/device updates, outside this hot path.
                    platform.set_status(state);
                }
                self.update_activity(cx);
                let _ = self.wake.try_send(());
            }
            "gesture_changed" => {
                if params["latched"] == true
                    && current(&self.snapshot, &params)
                    && self.event_epoch(&params)
                {
                    self.capsule
                        .show("handsFree", self.prompt_enabled(), &self.prompt_hint);
                }
            }
            "audio_level" => {
                if current(&self.snapshot, &params) && self.event_epoch(&params) {
                    self.capsule
                        .update_audio_level(params["waveform_level"].as_f64().unwrap_or(0.));
                }
            }
            "partial_result" => {
                if current(&self.snapshot, &params) && self.event_epoch(&params) {
                    self.capsule
                        .update_streaming_text(params["text"].as_str().unwrap_or(""));
                }
            }
            "processing_stage" => {
                if current(&self.snapshot, &params) && self.event_epoch(&params) {
                    self.capsule
                        .set_processing_stage(params["stage"].as_str().unwrap_or("transcribing"));
                }
            }
            "prompt_hint" => {
                if current(&self.snapshot, &params) && self.event_epoch(&params) {
                    self.prompt_hint = params["hint"].as_str().unwrap_or("").into();
                    self.capsule.update_prompt_hint(&self.prompt_hint);
                }
            }
            "connection_status" => {
                if params.get("generation").is_none()
                    || (current(&self.snapshot, &params) && self.event_epoch(&params))
                {
                    self.capsule.show_connection(&params);
                }
            }
            "final_result" => {
                if current(&self.snapshot, &params) && self.event_epoch(&params) {
                    self.last_text = params["text"].as_str().unwrap_or("").into();
                    if let Some(platform) = &mut self.platform {
                        platform.set_has_result(!self.last_text.is_empty());
                    }
                    if self.snapshot["config"]["auto_paste"] != true
                        && !self.last_text.is_empty()
                        && self.commands.can_paste(
                            params["_ui_epoch"].as_u64().unwrap_or(u64::MAX),
                            params["generation"].as_u64().unwrap_or(0),
                        )
                    {
                        if let Some(platform) = &self.platform {
                            let _ = platform.copy_text(&self.last_text);
                        }
                        self.notify(if language(&self.snapshot) == "zh" {
                            "转写完成，已复制到剪贴板"
                        } else {
                            "Transcription copied to clipboard"
                        });
                    }
                }
            }
            "paste_requested" => {
                if current(&self.snapshot, &params) && self.event_epoch(&params) {
                    let delivery = Delivery {
                        token: params["token"].as_str().unwrap_or_default().into(),
                        epoch: params["_ui_epoch"].as_u64().unwrap_or(u64::MAX),
                        generation: params["generation"].as_u64().unwrap_or_default(),
                    };
                    if let Err(error) = self.deliveries.enqueue(delivery) {
                        self.send("cancel", json!({}), None);
                        self.deliveries.clear();
                        self.pastes.clear();
                        self.capsule.show_failure(error);
                    } else {
                        self.next_delivery();
                    }
                }
            }
            "platform_focused_snapshot" => self.focused_snapshot(&params),
            "observation_poll" => {
                if let Some(platform) = &mut self.platform
                    && let Some(id) = params["observation_id"].as_str()
                {
                    let _ = platform.observe(id);
                }
            }
            "observation_ended" => {
                if let Some(platform) = &mut self.platform
                    && let Some(id) = params["observation_id"].as_str()
                {
                    let _ = platform.end_observation(id);
                }
            }
            "screen_frame" => self.screen_frame(&params),
            "config_changed" => {
                self.snapshot["config"] = params["config"].clone();
                self.snapshot["api_key_set"] = params["api_key_set"].clone();
                self.capsule.set_language(language(&self.snapshot));
                if self.snapshot["config"]["screen_context_enabled"] != true {
                    self.screen = None;
                }
                if let Some(platform) = &mut self.platform {
                    platform.update_config(&self.snapshot["config"]);
                    platform.update_snapshot(&self.snapshot);
                }
            }
            "devices_changed" => {
                self.snapshot["devices"] = params["devices"].clone();
                self.snapshot["audio_input_status"] = params["audio_input_status"].clone();
                if let Some(platform) = &mut self.platform {
                    platform.update_snapshot(&self.snapshot);
                }
            }
            "audio_input_status" => self.snapshot["audio_input_status"] = params.clone(),
            "environment_changed" => self.snapshot["environment_checks"] = params.clone(),
            "dictionary_changed" => self.snapshot["dictionary"] = params.clone(),
            "dictionary_learning_changed" => {
                self.snapshot["dictionary"] = params["dictionary"].clone();
                self.snapshot["dictionary_learning_records"] = params["records"].clone();
            }
            "dictionary_learning_summary" => {
                let terms = params["terms"]
                    .as_array()
                    .map(|terms| {
                        terms
                            .iter()
                            .filter_map(Value::as_str)
                            .collect::<Vec<_>>()
                            .join(if language(&self.snapshot) == "en" {
                                ", "
                            } else {
                                "、"
                            })
                    })
                    .unwrap_or_default();
                let prefix = self
                    .platform
                    .as_ref()
                    .map(|platform| platform.localized("dictionary_learning_summary"))
                    .unwrap_or_default();
                self.notify(&format!("{prefix}{terms}"));
            }
            "recordings_changed" => {
                self.snapshot["recordings"] = params["recordings"].clone();
                self.snapshot["recording_storage"] = params["storage"].clone();
            }
            "recording_deleted" => {
                if let Some(platform) = &mut self.platform {
                    platform.stop_playback(params["id"].as_str());
                }
                self.send("list_recordings", json!({}), None);
            }
            "recording_compaction_complete" => {
                self.send("list_recordings", json!({}), None);
            }
            "copy_transcript" => {
                if let Some(platform) = &mut self.platform {
                    let _ = platform.action(method, &params);
                }
            }
            "play_recording" => {
                if let Some(platform) = &mut self.platform
                    && let (Some(id), Some(path)) = (params["id"].as_str(), params["path"].as_str())
                    && let Err(error) = platform.play(id, &PathBuf::from(path))
                {
                    let message = error.to_string();
                    self.notify(&message);
                    self.forward_settings(
                        "recording_playback_error",
                        &json!({"id":id,"message":message}),
                        cx,
                    );
                }
            }
            "stop_recording" => {
                if let Some(platform) = &mut self.platform {
                    platform.stop_playback(params["id"].as_str());
                }
            }
            "mic_test_playback" => {
                if let Some(platform) = &mut self.platform
                    && let Some(data) = params["wav_base64"].as_str()
                    && let Err(error) = platform.play_preview(data)
                {
                    let message = error.to_string();
                    self.notify(&message);
                    self.forward_settings(
                        "mic_test_playback_error",
                        &json!({"message":message}),
                        cx,
                    );
                }
            }
            "open_file" | "open_url" | "microphone_permission_required" => {
                if let Some(platform) = &mut self.platform
                    && let Err(error) = platform.action(method, &params)
                {
                    self.notify(&error.to_string());
                }
            }
            "resync" => {
                self.snapshot = params.clone();
                if let Some(text) = recovered_text(&params, self.commands.paste_epoch()) {
                    self.last_text = text.to_owned();
                    if let Some(platform) = &mut self.platform {
                        platform.set_has_result(!self.last_text.is_empty());
                    }
                }
                self.commands.update_session(
                    params["state"].as_str().unwrap_or("idle"),
                    params["generation"].as_u64().unwrap_or(0),
                );
                self.backend_event("config_changed", params.clone(), cx);
                for paste in params["pending_pastes"].as_array().into_iter().flatten() {
                    self.backend_event("paste_requested", paste.clone(), cx);
                }
            }
            "error" | "warning" => {
                if method == "error"
                    && params.get("generation").is_some()
                    && current(&self.snapshot, &params)
                    && self.event_epoch(&params)
                {
                    self.capsule
                        .show_failure(params["message"].as_str().unwrap_or("Dictation failed"));
                    let _ = self.wake.try_send(());
                }
                self.notify(params["message"].as_str().unwrap_or("Operation failed"));
            }
            "backend_disconnected" => {
                self.capsule.hide();
                self.screen = None;
                self.pastes.clear();
                self.deliveries.clear();
                let prefix = self
                    .platform
                    .as_ref()
                    .map(|platform| platform.localized("backend_disconnected"))
                    .unwrap_or_default();
                self.notify(&format!(
                    "{prefix} {}",
                    params["message"].as_str().unwrap_or("")
                ));
                if let Some(platform) = &mut self.platform {
                    platform.close();
                }
            }
            _ => {}
        }
        self.forward_settings(method, &params, cx);
    }
    fn response(&mut self, response: &Value, cx: &mut Context<Self>) {
        let id = response["request_id"].as_u64().unwrap_or(0);
        let result = &response["result"];
        match self.pending.remove(&id) {
            Some(Pending::SnapshotForSettings(tab)) => {
                self.snapshot = result.clone();
                self.open_settings(&tab, cx);
            }
            Some(Pending::Claim(delivery)) => {
                let epoch = delivery.epoch;
                let generation = result["generation"].as_u64().unwrap_or(0);
                if result["cancelled"] == true
                    || result["token"] != delivery.token
                    || generation != delivery.generation
                    || !self.commands.can_paste(epoch, generation)
                {
                    self.complete_delivery(&delivery);
                    return;
                }
                let token = delivery.token.clone();
                self.pastes.insert(
                    token.clone(),
                    PasteWork {
                        data: result.clone(),
                        epoch,
                        snapshot: Value::Null,
                        waiting_for_ax: (result["observe_correction"] == true)
                            .then(|| Instant::now() + platform::AX_CAPTURE_BUDGET),
                    },
                );
                let _ = self.wake.try_send(());
                if result["observe_correction"] == true {
                    let request = json!({"token":token,"epoch":epoch,"generation":generation});
                    if let Some(platform) = &mut self.platform {
                        if let Err(error) = platform.capture_focused(request) {
                            self.notify(&error.to_string());
                            self.prepare_paste(&token, Value::Null);
                        }
                    } else {
                        self.prepare_paste(&token, Value::Null);
                    }
                } else {
                    self.prepare_paste(&token, Value::Null);
                }
            }
            Some(Pending::Prepare(delivery)) => {
                let Some(work) = self.pastes.remove(&delivery.token) else {
                    self.complete_delivery(&delivery);
                    return;
                };
                let generation = work.data["generation"].as_u64().unwrap_or(0);
                if result["cancelled"] == true || !self.commands.can_paste(work.epoch, generation) {
                    self.complete_delivery(&delivery);
                    return;
                }
                if let Some(platform) = &mut self.platform {
                    if let Some(id) = result["observation_id"].as_str() {
                        let _ = platform.retain_observation(id, &work.snapshot);
                    }
                    let deferred = match platform.paste_guarded(
                        work.data["text"].as_str().unwrap_or(""),
                        work.data["restore_clipboard"] == true,
                        work.data["native_fast_paste"] == true,
                        work.epoch,
                        generation,
                    ) {
                        Ok(true) => platform.paste_is_delivering(),
                        Ok(false) => {
                            self.send("cancel_observation", json!({}), None);
                            false
                        }
                        Err(error) => {
                            self.send("cancel_observation", json!({}), None);
                            self.notify(&error.to_string());
                            false
                        }
                    };
                    if deferred {
                        self.native_delivery = Some(delivery);
                        self.update_activity(cx);
                        let _ = self.wake.try_send(());
                        return;
                    }
                }
                self.complete_delivery(&delivery);
            }
            None => {
                if response["result"]["ignored"] == true
                    && matches!(
                        response["method"].as_str(),
                        Some("hotkey_pressed" | "toggle_recording")
                    )
                    && self.hotkey_start.is_some_and(|(id, epoch, _)| {
                        response["request_id"].as_u64() == Some(id)
                            && response["_ui_epoch"].as_u64() == Some(epoch)
                    })
                {
                    self.hotkey_start.take();
                    self.update_activity(cx);
                }
                if response["method"] == "snapshot" {
                    self.backend_event("resync", result.clone(), cx);
                }
            }
        }
        if let Some(screen) = &mut self.screen
            && screen.request == id
        {
            screen.generation = result["generation"].as_u64();
            screen.started = Instant::now();
            if screen.generation.is_some()
                && let Some(platform) = &self.platform
            {
                platform.notify_localized("screen_context_started");
            }
        }
        self.send_initial_frame();
    }
    fn focused_snapshot(&mut self, params: &Value) {
        if let Some(token) = params["request_id"]["token"].as_str()
            && self
                .pastes
                .get(token)
                .is_some_and(|work| work.accepts_ax(&params["request_id"]))
        {
            self.prepare_paste(token, params["snapshot"].clone());
        }
    }
    fn prepare_paste(&mut self, token: &str, snapshot: Value) {
        let Some(work) = self.pastes.get_mut(token) else {
            return;
        };
        let delivery = Delivery {
            token: token.into(),
            epoch: work.epoch,
            generation: work.data["generation"].as_u64().unwrap_or_default(),
        };
        if !self.commands.can_paste(delivery.epoch, delivery.generation) {
            self.pastes.remove(token);
            self.complete_delivery(&delivery);
            return;
        }
        work.snapshot = snapshot.clone();
        work.waiting_for_ax = None;
        self.send(
            "prepare_paste_observation",
            json!({"token":token,"snapshot":snapshot}),
            Some(Pending::Prepare(delivery)),
        );
    }
    fn event_epoch(&self, params: &Value) -> bool {
        params["_ui_epoch"].as_u64() == Some(self.commands.paste_epoch())
    }
    fn next_delivery(&mut self) {
        if self.native_delivery.is_some() {
            return;
        }
        while let Some(delivery) = self
            .deliveries
            .next(self.commands.paste_epoch(), self.commands.generation())
        {
            if self.commands.can_paste(delivery.epoch, delivery.generation) {
                self.send(
                    "claim_paste",
                    json!({"token":delivery.token}),
                    Some(Pending::Claim(delivery)),
                );
                return;
            }
            self.deliveries.complete(&delivery);
        }
    }
    fn complete_delivery(&mut self, delivery: &Delivery) {
        self.deliveries.complete(delivery);
        self.next_delivery();
    }
    fn capture_screen(&mut self, initial: bool) {
        let Some(screen) = &self.screen else {
            return;
        };
        if self
            .screen_inflight
            .is_some_and(|(_, at)| at.elapsed() < Duration::from_secs(10))
        {
            return;
        }
        self.screen_serial += 1;
        let serial = self.screen_serial;
        if self.screen_worker.submit(ScreenJob {
            serial,
            request: screen.request,
            epoch: screen.epoch,
            generation: screen.generation,
            initial,
        }) {
            self.screen_inflight = Some((serial, Instant::now()));
        }
    }
    fn screen_frame(&mut self, params: &Value) {
        if params["serial"].as_u64() != self.screen_inflight.map(|(serial, _)| serial) {
            return;
        }
        self.screen_inflight = None;
        let Some(screen) = &mut self.screen else {
            return;
        };
        if !screen.matches_frame(params) {
            if screen.initial.is_none() {
                self.capture_screen(true);
            }
            return;
        }
        if self.commands.paste_epoch() != screen.epoch {
            return;
        }
        if let Some(error) = params["error"].as_str() {
            let message = error.to_owned();
            self.screen = None;
            self.notify(&message);
            return;
        }
        if params["initial"] == true {
            screen.initial = params["jpeg_base64"].as_str().map(str::to_owned);
            self.send_initial_frame();
        } else if params["generation"].as_u64() == screen.generation {
            let generation = screen.generation;
            let jpeg = params["jpeg_base64"].clone();
            self.send(
                "append_screen_frame",
                json!({"generation":generation,"jpeg_base64":jpeg}),
                None,
            );
        }
    }
    fn send_initial_frame(&mut self) {
        let Some(screen) = &mut self.screen else {
            return;
        };
        if screen.epoch != self.commands.paste_epoch() {
            return;
        }
        let Some((generation, jpeg)) = screen.take_initial() else {
            return;
        };
        self.send(
            "append_screen_frame",
            json!({"generation":generation,"jpeg_base64":jpeg}),
            None,
        );
    }
    fn open_settings(&mut self, tab: &str, cx: &mut Context<Self>) {
        if let Some((window, view)) = &self.settings {
            let view = view.clone();
            if window
                .update(cx, |_, window, cx| {
                    view.update(cx, |settings, cx| {
                        settings.on_event("select_tab", &json!({"tab":tab}), window, cx)
                    });
                    window.activate_window();
                })
                .is_ok()
            {
                cx.activate(true);
                return;
            }
        }
        let mut snapshot = self.snapshot.clone();
        snapshot["initial_tab"] = json!(tab);
        self.display_channel(&mut snapshot["config"]);
        match settings::open(snapshot, self.commands.clone(), cx) {
            Ok(settings) => {
                self.settings = Some(settings);
                cx.activate(true);
            }
            Err(error) => self.notify(&error.to_string()),
        }
    }
    fn forward_settings(&mut self, method: &str, params: &Value, cx: &mut Context<Self>) {
        let mut params = params.clone();
        if matches!(method, "initialized" | "config_changed" | "resync") {
            self.display_channel(&mut params["config"]);
        }
        if method == "rpc_response" && params["method"] == "snapshot" {
            self.display_channel(&mut params["result"]["config"]);
        }
        if let Some((window, view)) = &self.settings {
            let view = view.clone();
            let _ = window.update(cx, |_, window, cx| {
                view.update(cx, |settings, cx| {
                    settings.on_event(method, &params, window, cx)
                })
            });
        }
    }
    fn display_channel(&self, config: &mut Value) {
        if config["update_channel"].is_null()
            && let Some(platform) = &self.platform
        {
            config["update_channel"] = platform.status()["updater"]["channel"].clone();
        }
    }
    fn window_closed(&mut self, id: WindowId, cx: &mut Context<Self>) {
        if self
            .settings
            .as_ref()
            .is_some_and(|(window, _)| window.window_id() == id)
            && let Some((_, view)) = self.settings.take()
        {
            let flushed = view.update(cx, |settings, cx| {
                let result = settings.flush(cx);
                settings.close();
                result
            });
            if let Err(error) = flushed {
                self.notify(&error.to_string());
            }
        }
    }
    fn platform_status(&mut self) {
        if let Some(platform) = &self.platform {
            let status = platform.status();
            self.send("platform_status", status, None);
        }
    }
    fn notify(&self, message: &str) {
        if let Some(platform) = &self.platform {
            platform.notify(message);
        } else {
            eprintln!("Vocal More: {message}");
        }
    }
    fn update_activity(&mut self, cx: &Context<Self>) {
        let active = self.hotkey_start.is_some()
            || self.snapshot["state"] != "idle"
            || self.native_delivery.is_some()
            || self.deliveries.has_work()
            || !self.pastes.is_empty()
            || self
                .platform
                .as_ref()
                .is_some_and(Platform::paste_is_pending);
        if active && self.activity.is_none() {
            self.activity = Some(
                cx.background_executor()
                    .prevent_app_nap("Vocal More dictation"),
            );
        } else if !active {
            self.activity.take();
        }
    }
    fn tick_interval(&self) -> Duration {
        let mut ui = if self.capsule.needs_tick() {
            Duration::from_secs_f64(1. / 60.)
        } else {
            Duration::from_secs(2)
        };
        for work in self.pastes.values() {
            if let Some(deadline) = work.waiting_for_ax {
                ui = ui.min(deadline.saturating_duration_since(Instant::now()));
            }
        }
        self.platform
            .as_ref()
            .map_or(ui, |platform| ui.min(platform.next_tick_delay()))
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        if self.closing {
            return;
        }
        if self.commands.take_quit_request() {
            self.begin_quit(cx);
            return;
        }
        let now = Instant::now();
        let expired: Vec<_> = self
            .pastes
            .iter()
            .filter(|(_, work)| work.ax_expired(now))
            .map(|(token, _)| token.clone())
            .collect();
        for token in expired {
            self.prepare_paste(&token, Value::Null);
        }
        self.capsule.tick();
        let delivery = self.platform.as_mut().and_then(Platform::poll_paste);
        if let Some(result) = delivery {
            if !matches!(result, Ok(true)) {
                self.send("cancel_observation", json!({}), None);
            }
            if let Err(error) = result {
                self.notify(&error.to_string());
            }
            if let Some(delivery) = self.native_delivery.take() {
                self.complete_delivery(&delivery);
            }
        }
        if let Some(platform) = &mut self.platform {
            platform.tick();
        }
        self.update_activity(cx);
        if self.watchdog.elapsed() >= Duration::from_secs(2) {
            self.watchdog = Instant::now();
            self.platform_status();
        }
        if let Some(screen) = &mut self.screen
            && screen.generation.is_some()
            && screen.started.elapsed() < Duration::from_secs(230)
            && screen.last_capture.elapsed() >= Duration::from_secs(2)
            && matches!(
                self.snapshot["state"].as_str(),
                Some("starting" | "recording")
            )
        {
            screen.last_capture = Instant::now();
            self.capture_screen(false);
        }
        if let Some(error) = self.commands.take_overflow() {
            self.backend_event("rpc_error", error, cx);
        }
    }
    fn close(&mut self, cx: &mut Context<Self>) {
        if self.closing {
            return;
        }
        if let Some((_, view)) = self.settings.take() {
            let flushed = view.update(cx, |settings, cx| {
                let result = settings.flush(cx);
                settings.close();
                result
            });
            if let Err(error) = flushed {
                self.shutdown_failed_requests = self.shutdown_failed_requests.saturating_add(1);
                self.notify(&error.to_string());
            }
        }
        let mut durable = Vec::new();
        while let Ok(event) = self.events.try_recv() {
            self.retain_shutdown_error(&event);
            if let UiEvent::Request(request) = event
                && durable_request(&request)
            {
                durable.push(request);
            }
        }
        while let Some(error) = self.commands.take_overflow() {
            if durable_method(error["method"].as_str().unwrap_or(""), &error["params"]) {
                self.shutdown_failed_requests = self.shutdown_failed_requests.saturating_add(1);
            }
            self.notify(
                error["message"]
                    .as_str()
                    .unwrap_or("Operation was not accepted"),
            );
        }
        self.closing = true;
        self.screen = None;
        self.screen_worker.close();
        self.pastes.clear();
        self.deliveries.clear();
        self.capsule.close();
        self.native_delivery.take();
        if let Err(error) = self.driver.close_with_requests(durable) {
            self.notify(&error.to_string());
            self.driver.close();
        }
        // Close admission now, but keep the clipboard cleanup owner until the
        // deferred quit loop has observed its read grace and bounded retry.
        if let Some(platform) = &mut self.platform {
            platform.close();
        }
    }
    fn retain_shutdown_error(&mut self, event: &UiEvent) {
        if let UiEvent::Backend { method, params, .. } = event
            && method == "rpc_error"
            && durable_method(params["method"].as_str().unwrap_or(""), &params["params"])
        {
            self.shutdown_failed_requests = self.shutdown_failed_requests.saturating_add(1);
        }
    }
    fn begin_quit(&mut self, cx: &mut Context<Self>) {
        if self.closing {
            return;
        }
        self.close(cx);
        cx.spawn(async move |host, cx| {
            loop {
                match host.update(cx, |host, _| {
                    let platform_done = host
                        .platform
                        .as_mut()
                        .is_none_or(Platform::shutdown_finished);
                    host.driver.finished() && platform_done
                }) {
                    Ok(true) => break,
                    Ok(false) => {
                        cx.background_executor()
                            .timer(Duration::from_millis(10))
                            .await
                    }
                    Err(_) => return,
                }
            }
            let display_failure = match host.update(cx, |host, _| {
                // A response may be admitted between the initial drain and
                // channel close. Once finished, no driver send can race this
                // final drain; successful and failed sends are counted once.
                while let Ok(event) = host.events.try_recv() {
                    host.retain_shutdown_error(&event);
                }
                let failures = host.driver.shutdown_failures();
                if let Some(message) = shutdown_notice(
                    language(&host.snapshot),
                    failures
                        .failed_durable_requests
                        .saturating_add(host.shutdown_failed_requests),
                    failures.cleanup_failed
                        || host
                            .platform
                            .as_ref()
                            .is_some_and(Platform::shutdown_failed),
                ) {
                    host.notify(&message);
                    host.capsule.show_failure(&message);
                    true
                } else {
                    false
                }
            }) {
                Ok(value) => value,
                Err(_) => return,
            };
            let _ = host.update(cx, |host, _| {
                host.platform.take();
                host.activity.take();
            });
            if display_failure {
                // Ordinary host ticks stop at close. Keep only the established
                // four-second failure notice alive before final termination.
                loop {
                    match host.update(cx, |host, _| {
                        host.capsule.tick();
                        host.capsule.needs_tick()
                    }) {
                        Ok(true) => {
                            cx.background_executor()
                                .timer(Duration::from_millis(16))
                                .await
                        }
                        Ok(false) => break,
                        Err(_) => return,
                    }
                }
            }
            let _ = host.update(cx, |host, cx| {
                host.capsule.close();
                if let Err(error) = host.termination.finish() {
                    host.notify(&error.to_string());
                    cx.quit();
                }
            });
        })
        .detach();
    }
}
fn shutdown_notice(language: &str, failed_requests: usize, cleanup_failed: bool) -> Option<String> {
    if failed_requests == 0 && !cleanup_failed {
        return None;
    }
    let zh = language.to_lowercase().starts_with("zh");
    let mut message = if failed_requests > 0 {
        if zh {
            format!("退出前有 {failed_requests} 项更改未能保存。")
        } else {
            format!("Could not save {failed_requests} changes before quitting.")
        }
    } else {
        String::new()
    };
    if cleanup_failed {
        if !message.is_empty() {
            message.push(' ');
        }
        message.push_str(if zh {
            "退出清理失败，请重新启动应用。"
        } else {
            "Cleanup failed. Restart the app before using it again."
        });
    }
    Some(message)
}
fn language(snapshot: &Value) -> &str {
    snapshot["config"]["ui"]["language"]
        .as_str()
        .unwrap_or("en")
}
fn current(snapshot: &Value, event: &Value) -> bool {
    event.get("generation").is_none() || event["generation"] == snapshot["generation"]
}
fn recovered_text(snapshot: &Value, epoch: u64) -> Option<&str> {
    let result = &snapshot["last_result"];
    (result["generation"] == snapshot["generation"] && result["_ui_epoch"].as_u64() == Some(epoch))
        .then(|| result["text"].as_str())
        .flatten()
}
fn merge(target: &mut Value, source: &Value) {
    if let Some(object) = source.as_object() {
        for (key, value) in object {
            target[key] = value.clone();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn screen() -> ScreenSession {
        let now = Instant::now();
        ScreenSession {
            request: 102,
            epoch: 3,
            generation: None,
            initial: None,
            started: now,
            last_capture: now,
        }
    }
    #[test]
    fn old_initial_frame_cannot_attach_to_new_start_even_with_equal_epoch() {
        let session = screen();
        assert!(!session.matches_frame(&json!({"request":101,"epoch":3,"generation":null})));
        assert!(session.matches_frame(&json!({"request":102,"epoch":3,"generation":null})));
        assert!(!session.matches_frame(&json!({"request":102,"epoch":1,"generation":null})));
    }
    #[test]
    fn first_image_is_retained_until_start_generation_is_known_and_consumed_once() {
        let mut session = screen();
        session.initial = Some("fixture-jpeg".into());
        assert_eq!(session.take_initial(), None);
        session.generation = Some(8);
        assert_eq!(session.take_initial(), Some((8, "fixture-jpeg".into())));
        assert_eq!(session.take_initial(), None);
    }
    #[test]
    fn resync_recovers_dropped_final_text_only_from_the_current_session() {
        let snapshot = json!({"generation":8,"last_result":{
            "generation":8,"_ui_epoch":3,"text":"Recovered transcription"}});
        assert_eq!(
            recovered_text(&snapshot, 3),
            Some("Recovered transcription")
        );
        assert_eq!(recovered_text(&snapshot, 4), None);
        let stale = json!({"generation":9,"last_result":snapshot["last_result"]});
        assert_eq!(recovered_text(&stale, 3), None);
    }
    #[test]
    fn ax_snapshot_after_deadline_is_rejected_even_with_matching_session() {
        let work = PasteWork {
            data: json!({"generation":8}),
            epoch: 3,
            snapshot: Value::Null,
            waiting_for_ax: Some(Instant::now() - Duration::from_millis(1)),
        };
        assert!(!work.accepts_ax(&json!({"epoch":3,"generation":8})));
    }
    #[test]
    fn lost_ax_callback_expires_and_late_callbacks_cannot_prepare_twice() {
        let now = Instant::now();
        let mut work = PasteWork {
            data: json!({"generation":8}),
            epoch: 3,
            snapshot: Value::Null,
            waiting_for_ax: Some(now + platform::AX_CAPTURE_BUDGET),
        };
        assert!(work.accepts_ax_at(&json!({"epoch":3,"generation":8}), now));
        assert!(!work.accepts_ax_at(
            &json!({"epoch":3,"generation":8}),
            now + platform::AX_CAPTURE_BUDGET
        ));
        assert!(!work.accepts_ax(&json!({"epoch":1,"generation":8})));
        assert!(!work.ax_expired(now));
        assert!(work.ax_expired(now + Duration::from_secs(5)));
        work.waiting_for_ax = None;
        assert!(!work.accepts_ax(&json!({"epoch":3,"generation":8})));
        assert!(!work.ax_expired(now + Duration::from_secs(10)));
    }
}
