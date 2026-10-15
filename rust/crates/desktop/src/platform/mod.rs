// SPDX-License-Identifier: GPL-3.0-only
//! macOS services for the Rust desktop host. AppKit objects never cross threads.
mod accessibility;
mod bluetooth;
mod bluetooth_editor;
mod bluetooth_input;
pub use accessibility::AX_CAPTURE_BUDGET;
mod ffi;
mod hotkeys;
mod menu;
mod paste;
mod playback;
mod screen;
mod termination;
mod updater;

use crate::bluetooth_delivery::{Admission, Envelope, Ledger, SessionTarget, now_ms};
use crate::bridge::CommandSink;
use anyhow::{Context, Result, bail};
use objc2::{
    MainThreadMarker, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject},
};
use objc2_foundation::NSString;
pub use screen::{MAX_JPEG_BYTES, capture_screen};
use serde_json::{Value, json};
use std::{
    ffi::CStr,
    path::Path,
    ptr,
    time::{Duration, Instant},
};
pub use termination::TerminationGate;
pub use updater::effective_channel;

/// Run in the actual development app's bundle identity. Reads authorization
/// and session metadata only; no configuration, managers, capture or events.
pub fn bluetooth_preflight() -> Result<Value> {
    let _main = MainThreadMarker::new().context("preflight requires the main thread")?;
    let authorization = bluetooth::authorization()?;
    unsafe {
        let bundle: Retained<AnyObject> = msg_send![class(c"NSBundle"), mainBundle];
        let identifier: Option<Retained<NSString>> = msg_send![&*bundle, bundleIdentifier];
        let session = ffi::Owned::from_create(ffi::CGSessionCopyCurrentDictionary());
        let boolean = |key: &str| -> Option<bool> {
            let dictionary = session.as_ref()?;
            let key = ns(key);
            let value =
                ffi::CFDictionaryGetValue(dictionary.as_ptr(), (&*key as *const NSString).cast());
            if value.is_null() || ffi::CFGetTypeID(value) != ffi::CFBooleanGetTypeID() {
                return None;
            }
            Some(msg_send![&*(value.cast::<AnyObject>()), boolValue])
        };
        Ok(json!({
            "mode":"bluetooth-preflight-only","bundle_id":identifier.map(|s|s.to_string()),
            "bluetooth_authorization_code":authorization,
            "bluetooth_authorization":match authorization {0=>"not_determined",1=>"restricted",2=>"denied",3=>"allowed_always",_=>"unknown"},
            "accessibility_trusted":ffi::AXIsProcessTrusted(),
            "post_event_access":ffi::CGPreflightPostEventAccess(),
            "screen_locked":boolean("CGSSessionScreenIsLocked"),
            "on_console":boolean("kCGSSessionOnConsoleKey"),
            "permission_requests":0,"bluetooth_managers":0,"event_posts":0,
            "clipboard_reads":0,"config_reads":0,"audio_capture":false
        }))
    }
}

fn ns(value: &str) -> Retained<NSString> {
    NSString::from_str(value)
}
fn class(name: &CStr) -> &'static AnyClass {
    AnyClass::get(name)
        .unwrap_or_else(|| panic!("native class {} unavailable", name.to_string_lossy()))
}

pub struct Platform {
    _main_thread: MainThreadMarker,
    commands: CommandSink,
    hotkeys: Option<hotkeys::Hotkeys>,
    accessibility: Option<accessibility::AccessibilityWorker>,
    clipboard: paste::Clipboard,
    bluetooth: bluetooth::Bluetooth,
    editor: bluetooth_editor::Editor,
    current_input: Option<bluetooth_input::Input>,
    bluetooth_directory: Option<std::path::PathBuf>,
    delivery_ledger: Ledger,
    ledger_ready: bool,
    acceptance_directory: Option<std::path::PathBuf>,
    bluetooth_delivery: Option<u64>,
    menu: menu::Menu,
    playback: playback::Playback,
    updater: updater::Updater,
    config: Value,
    last_watchdog: Instant,
    closed: bool,
}
impl Platform {
    pub fn new(
        mtm: MainThreadMarker,
        commands: CommandSink,
        config: &Value,
        no_hotkeys: bool,
    ) -> Result<Self> {
        let hotkeys = hotkeys::Hotkeys::new(commands.clone(), config, no_hotkeys);
        let accessibility = accessibility::AccessibilityWorker::new(commands.clone());
        let menu = menu::Menu::new(mtm, commands.clone(), !no_hotkeys);
        let playback = playback::Playback::new(commands.clone());
        let updater = updater::Updater::new(mtm, config);
        let mut bluetooth = bluetooth::Bluetooth::default();
        bluetooth.configure(config, false);
        Ok(Self {
            _main_thread: mtm,
            commands,
            hotkeys: Some(hotkeys),
            accessibility: Some(accessibility),
            clipboard: paste::Clipboard::default(),
            bluetooth,
            editor: bluetooth_editor::Editor::default(),
            current_input: None,
            bluetooth_directory: None,
            delivery_ledger: Ledger::default(),
            ledger_ready: false,
            acceptance_directory: None,
            bluetooth_delivery: None,
            menu,
            playback,
            updater,
            config: config.clone(),
            last_watchdog: Instant::now(),
            closed: false,
        })
    }
    pub fn update_config(&mut self, config: &Value) {
        if self.config["bluetooth"]["trusted_peer_id"]
            .as_str()
            .is_some_and(|id| !id.is_empty())
            && self.config["bluetooth"]["trusted_peer_id"] != config["bluetooth"]["trusted_peer_id"]
        {
            self.bluetooth.close();
        }
        if self.config["bluetooth"]["output_target"] != config["bluetooth"]["output_target"]
            || self.config["bluetooth"]["peer_id"] != config["bluetooth"]["peer_id"]
        {
            self.bluetooth.cancel_outgoing();
        }
        if self.config["bluetooth"] != config["bluetooth"] {
            self.cancel_bluetooth_delivery();
            if let Some(input) = &mut self.current_input {
                input.stop();
            }
        }
        self.config = config.clone();
        self.bluetooth.configure(config, false);
        if let Some(hotkeys) = &mut self.hotkeys {
            hotkeys.configure(config);
        }
        self.updater.update(config);
    }
    pub fn update_snapshot(&mut self, snapshot: &Value) {
        if snapshot["config"].is_object() {
            self.update_config(&snapshot["config"]);
        }
        self.menu.update(snapshot);
    }
    pub fn paste_is_delivering(&self) -> bool {
        self.clipboard.is_delivering()
    }
    pub fn paste_is_pending(&self) -> bool {
        self.clipboard.is_pending() || self.clipboard.is_delivering()
    }
    pub fn poll_paste(&mut self) -> Option<Result<bool>> {
        let result = match self.clipboard.poll_delivery() {
            Ok(value) => value.map(Ok),
            Err(error) => Some(Err(error)),
        };
        if let Some(result) = &result
            && let Some(id) = self.bluetooth_delivery.take()
        {
            self.bluetooth
                .finish_receive(id, matches!(result, Ok(true)));
        }
        result
    }
    pub fn set_status(&mut self, state: &str) {
        self.menu.set_status(state);
    }
    pub fn set_has_result(&mut self, has_result: bool) {
        self.menu.set_has_result(has_result);
    }
    pub fn status(&self) -> Value {
        let diagnostics = self
            .hotkeys
            .as_ref()
            .map(|h| h.status())
            .unwrap_or(Value::Null);
        json!({"accessibility":unsafe {ffi::AXIsProcessTrusted()},"hotkey_listener":diagnostics["running"]==true&&diagnostics["event_tap_enabled"]==true,"hotkeys":diagnostics,"updater":self.updater.status(),"bluetooth":self.bluetooth.diagnostics()})
    }
    pub fn bluetooth_status(&self) -> Value {
        let mut status = self.bluetooth.status();
        status["development_actions"] = json!(
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent()?.parent().map(|p| p.join("Resources")))
                .is_some_and(|p| p.join("BluetoothFixedTextPrototype").is_file()
                    || p.join("BluetoothProductCandidate").is_file())
        );
        status["local_target"] = self.bluetooth_target().status();
        status["current_app_input"] = self.config["bluetooth"]["receive_into_current_app"].clone();
        status["input_worker_failed"] = json!(
            self.current_input
                .as_ref()
                .is_some_and(|input| input.finished() && !input.stopped())
        );
        status["output_target"] = self.config["bluetooth"]["output_target"].clone();
        status
    }
    pub fn configure_bluetooth_storage(&mut self, directory: &Path) -> Result<()> {
        self.bluetooth_directory = Some(directory.to_owned());
        self.delivery_ledger = Ledger::open(directory.join("bluetooth-deliveries.json"))?;
        self.ledger_ready = true;
        if std::env::current_exe()
            .ok()
            .and_then(|p| {
                p.parent()?
                    .parent()
                    .map(|p| p.join("Resources/BluetoothFixedTextPrototype"))
            })
            .is_some_and(|p| p.is_file())
        {
            self.acceptance_directory = Some(directory.to_owned());
        }
        Ok(())
    }
    fn acceptance_tick(&mut self) {
        let Some(directory) = self.acceptance_directory.clone() else {
            return;
        };
        let command = directory.join("bluetooth-test-command.json");
        if let Ok(bytes) = std::fs::read(&command) {
            let _ = std::fs::remove_file(&command);
            if bytes.len() <= 512
                && let Ok(value) = serde_json::from_slice::<Value>(&bytes)
            {
                let method = match value["action"].as_str() {
                    Some("start") => "platform_bluetooth_start",
                    Some("stop") => "platform_bluetooth_stop",
                    Some("open_editor") => "platform_bluetooth_editor",
                    Some("arm_external_editor") => "platform_bluetooth_external_target",
                    Some("send_fixed") => "platform_bluetooth_test",
                    _ => "",
                };
                if !method.is_empty()
                    && let Err(error) = self.action(method, &Value::Null)
                {
                    self.notify(&error.to_string());
                }
            }
        }
        let status = self.bluetooth.status();
        let report = json!({
            "connection":self.bluetooth.diagnostics(),"target":self.editor.target().status(),
            "role":self.config["bluetooth"]["mode"],"output_target":self.config["bluetooth"]["output_target"],
            "peer_target_ready":status["peer_target_ready"],
            "code_visible":status["pairing_code"].as_str().is_some_and(|s|s.len()==6),
            "pending_approval":status["pending_peer"].as_str().is_some_and(|s|!s.is_empty()),
            "discovered_receivers":status["devices"].as_array().map(Vec::len).unwrap_or(0),
            "ledger_ready":self.ledger_ready,"microphone":false,"hotkey_test":"not_started",
            "text_route":"BLE; management accepts action names only, never text",
            "input_scope":self.editor.scope(),
        });
        if let Ok(bytes) = serde_json::to_vec_pretty(&report) {
            let temporary = directory.join(".bluetooth-test-status.tmp");
            if std::fs::write(&temporary, bytes).is_ok() {
                let _ = std::fs::rename(temporary, directory.join("bluetooth-test-status.json"));
            }
        }
    }
    pub fn bluetooth_connected(&self) -> bool {
        self.bluetooth.connected()
    }
    pub fn bluetooth_session_target(&self, epoch: u64, generation: u64) -> Option<SessionTarget> {
        let revision = self.bluetooth.peer_target_revision();
        let connection = self.bluetooth.connection_revision();
        (self.bluetooth.connected() && revision != 0 && connection != 0).then(|| SessionTarget {
            epoch,
            generation,
            peer: self.bluetooth.peer().into(),
            revision,
            connection,
        })
    }
    pub fn bluetooth_send_bound(
        &mut self,
        text: &str,
        target: &SessionTarget,
        peer: &str,
    ) -> Result<()> {
        anyhow::ensure!(
            target.matches(
                self.commands.paste_epoch(),
                self.commands.generation(),
                peer,
                self.bluetooth.peer_target_revision(),
                self.bluetooth.connection_revision()
            ) && peer == self.bluetooth.peer()
                && self.config["bluetooth"]["trusted_peer_id"] == peer,
            "本次听写的对端、输入位置或连接已变化，未发送文字"
        );
        let message = Envelope::encode(text, target.revision, now_ms())?;
        self.bluetooth.send_text(&message, &self.commands)
    }
    pub fn bluetooth_send(&mut self, text: &str) -> Result<()> {
        match self.config["bluetooth"]["output_target"]
            .as_str()
            .unwrap_or("follow_uc")
        {
            "local" => {
                let target = self.editor.target();
                anyhow::ensure!(target.ready, "请先打开本机专用测试编辑器并保持其为输入窗口");
                anyhow::ensure!(
                    self.commands
                        .can_paste(self.commands.paste_epoch(), self.commands.generation()),
                    "本次结果已取消"
                );
                anyhow::ensure!(
                    self.editor.insert(text, target.revision),
                    "本机目标已变化，未输入文字"
                );
                Ok(())
            }
            "peer" => {
                let message =
                    Envelope::encode(text, self.bluetooth.peer_target_revision(), now_ms())?;
                self.bluetooth.send_text(&message, &self.commands)
            }
            _ => anyhow::bail!("无法可靠确认 UC 当前设备，请先明确选择本机或对端"),
        }
    }
    fn cancel_bluetooth_delivery(&mut self) {
        if let Some(input) = &self.current_input {
            input.invalidate();
        }
        if let Some(id) = self.bluetooth_delivery.take() {
            let _ = self.clipboard.cancel_delivery();
            self.bluetooth.finish_receive(id, false);
        }
    }
    fn tick_bluetooth(&mut self) {
        let current_app = self.config["bluetooth"]["receive_into_current_app"] == true;
        let input_allowed = current_app
            && self.bluetooth.connected()
            && self.config["bluetooth"]["trusted_peer_id"] == self.bluetooth.peer()
            && !self.bluetooth.peer().is_empty();
        // Retain a stopped owner until it finishes. In particular its pending
        // main-thread clipboard restoration must remain part of the quit gate.
        if self
            .current_input
            .as_ref()
            .is_some_and(|input| input.finished() && input.stopped())
        {
            self.current_input.take();
        }
        if input_allowed && self.current_input.is_none() {
            if let Some(directory) = &self.bluetooth_directory {
                self.current_input = bluetooth_input::Input::start(
                    directory.join("bluetooth-app-deliveries.json"),
                    self.config["restore_clipboard"] == true,
                )
                .ok();
            }
        } else if !input_allowed && let Some(input) = &mut self.current_input {
            input.stop();
        }
        if let Some(input) = &self.current_input {
            while let Some(done) = input.poll() {
                if done.peer == self.bluetooth.peer()
                    && done.connection == self.bluetooth.connection_revision()
                {
                    self.bluetooth.finish_receive(done.id, done.confirmed);
                }
            }
        }
        let target = self.bluetooth_target();
        self.bluetooth
            .advertise_target(if target.ready { target.revision } else { 0 }, target.f1);
        for notice in self.bluetooth.poll(&self.commands) {
            match notice {
                bluetooth::Notice::Text(id, text) => {
                    if current_app {
                        let accepted = self.current_input.as_ref().is_some_and(|input| {
                            input
                                .submit(
                                    id,
                                    self.bluetooth.peer().into(),
                                    self.bluetooth.connection_revision(),
                                    text.clone(),
                                )
                                .is_ok()
                        });
                        if !accepted {
                            self.bluetooth.finish_receive(id, false);
                        }
                        continue;
                    }
                    let target = self.editor.target();
                    let peer = self.bluetooth.peer().to_owned();
                    let submitted = (|| -> Result<bool> {
                        anyhow::ensure!(
                            self.bluetooth.connected() && self.ledger_ready,
                            "连接或去重记录未就绪"
                        );
                        let now = now_ms();
                        let message = Envelope::decode(&text, target.revision, target.ready, now)?;
                        match self
                            .delivery_ledger
                            .begin(&peer, id, message.expires(), now)?
                        {
                            Admission::Replay(done) => Ok(done),
                            Admission::New => {
                                let done = self.editor.insert(message.text(), message.revision());
                                self.delivery_ledger.finish(&peer, id, done)?;
                                Ok(done)
                            }
                        }
                    })()
                    .unwrap_or(false);
                    self.bluetooth.finish_receive(id, submitted);
                }
                bluetooth::Notice::Submitted(true) => self.notify("蓝牙接收端已确认提交输入"),
                bluetooth::Notice::Submitted(false) => {
                    self.notify("接收端未确认输入完成，请先核对目标应用，避免重复输入")
                }
                bluetooth::Notice::Disconnected => {
                    self.cancel_bluetooth_delivery();
                    self.notify("蓝牙连接已断开，文本不会自动重发");
                }
                bluetooth::Notice::Failed => {
                    self.cancel_bluetooth_delivery();
                    self.notify("蓝牙传输未完成；结果仍保存在发送端，请检查连接");
                }
                bluetooth::Notice::Trusted => {
                    let peer = self.bluetooth.peer().to_owned();
                    if !peer.is_empty() && self.config["bluetooth"]["trusted_peer_id"] != peer {
                        self.commands.request(
                            "set_config",
                            json!({"key":"bluetooth.trusted_peer_id","value":peer}),
                        );
                        self.commands.request(
                            "set_config",
                            json!({"key":"bluetooth.auto_connect","value":true}),
                        );
                    }
                }
            }
        }
    }
    fn bluetooth_target(&self) -> bluetooth_editor::Target {
        if self.config["bluetooth"]["receive_into_current_app"] == true {
            let target = self.current_input.as_ref().map(|input| input.target());
            bluetooth_editor::Target {
                revision: target.map_or(0, |v| v.revision),
                ready: target.is_some_and(|v| v.ready),
                ..Default::default()
            }
        } else {
            self.editor.target()
        }
    }
    pub fn activation_status(&self) -> Value {
        unsafe {
            let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
            let active: bool = msg_send![&*app, isActive];
            let running: bool = msg_send![&*app, isRunning];
            let policy: isize = msg_send![&*app, activationPolicy];
            let key: Option<Retained<AnyObject>> = msg_send![&*app, keyWindow];
            let main: Option<Retained<AnyObject>> = msg_send![&*app, mainWindow];
            let workspace: Retained<AnyObject> = msg_send![class(c"NSWorkspace"), sharedWorkspace];
            let frontmost: Option<Retained<AnyObject>> =
                msg_send![&*workspace, frontmostApplication];
            let pid = frontmost.map(|app| {
                let pid: i32 = msg_send![&*app, processIdentifier];
                pid
            });
            let session = ffi::Owned::from_create(ffi::CGSessionCopyCurrentDictionary());
            let session_boolean = |key: &str| -> Option<bool> {
                let dictionary = session.as_ref()?;
                let key = ns(key);
                let value = ffi::CFDictionaryGetValue(
                    dictionary.as_ptr(),
                    (&*key as *const NSString).cast(),
                );
                if value.is_null() || ffi::CFGetTypeID(value) != ffi::CFBooleanGetTypeID() {
                    return None;
                }
                let value: bool = msg_send![&*(value.cast::<AnyObject>()), boolValue];
                Some(value)
            };
            json!({"active":active,"running":running,"activation_policy":policy,"key_window":key.is_some(),"main_window":main.is_some(),"foreground_pid":pid,"self_pid":std::process::id(),"screen_locked":session_boolean("CGSSessionScreenIsLocked"),"on_console":session_boolean("kCGSSessionOnConsoleKey")})
        }
    }
    pub fn begin_hotkey_capture(&mut self) {
        if let Some(h) = &mut self.hotkeys {
            h.capture(true);
        }
    }
    pub fn end_hotkey_capture(&mut self) {
        if let Some(h) = &mut self.hotkeys {
            h.capture(false);
        }
    }
    pub fn capture_focused(&self, request_id: Value) -> Result<()> {
        self.ax()?.capture(request_id)
    }
    pub fn retain_observation(&self, id: &str, snapshot: &Value) -> Result<()> {
        self.ax()?.retain(id, snapshot)
    }
    pub fn observe(&self, id: &str) -> Result<()> {
        self.ax()?.observe(id)
    }
    pub fn end_observation(&self, id: &str) -> Result<()> {
        self.ax()?.end(id)
    }
    fn ax(&self) -> Result<&accessibility::AccessibilityWorker> {
        self.accessibility.as_ref().context("平台读取服务已关闭")
    }
    pub fn paste_guarded(
        &mut self,
        text: &str,
        restore: bool,
        native_fast: bool,
        epoch: u64,
        generation: u64,
    ) -> Result<bool> {
        if self.closed {
            return Ok(false);
        }
        self.clipboard.paste(
            &self.commands,
            text,
            restore,
            native_fast,
            epoch,
            generation,
        )
    }
    pub fn paste(&mut self, text: &str, restore: bool, native_fast: bool) -> Result<()> {
        self.paste_guarded(
            text,
            restore,
            native_fast,
            self.commands.paste_epoch(),
            self.commands.generation(),
        )?;
        Ok(())
    }
    pub fn copy_text(&self, text: &str) -> Result<()> {
        paste::copy(text)
    }
    pub fn notify(&self, message: &str) {
        self.menu.notify(message);
    }
    pub fn localized(&self, key: &str) -> String {
        let (zh, en) = match key {
            "screen_context_started" => (
                "已启用屏幕上下文；画面仅在内存中处理，并只用于本次听写。",
                "Screen context is active. Screen frames stay in memory and are sent only during this dictation.",
            ),
            "notification_transcription_complete_title" => ("识别完成", "Transcription Complete"),
            "notification_diagnostics_exported_title" => ("诊断包已导出", "Diagnostics Exported"),
            "dictionary_learning_summary" => ("已学习词条：", "Learned dictionary terms: "),
            "backend_disconnected" => (
                "Rust 服务已退出，请重新启动 Vocal More。",
                "The Rust service stopped. Please restart Vocal More.",
            ),
            "platform_busy" => (
                "系统操作繁忙，请稍后再试",
                "System operations are busy. Please try again shortly.",
            ),
            _ => (key, key),
        };
        if self.config.pointer("/ui/language").and_then(Value::as_str) == Some("en") {
            en
        } else {
            zh
        }
        .into()
    }
    pub fn notify_localized(&self, key: &str) {
        self.notify(&self.localized(key));
    }
    pub fn play(&mut self, id: &str, path: &Path) -> Result<()> {
        self.playback.play(id, path)
    }
    pub fn stop_playback(&mut self, id: Option<&str>) {
        self.playback.stop(id);
    }
    pub fn play_preview(&mut self, wav_base64: &str) -> Result<()> {
        self.playback.preview(wav_base64)
    }
    pub fn stop_preview(&mut self) {
        self.playback.stop_preview();
    }
    pub fn check_updates(&mut self) -> Result<()> {
        if !self.updater.check() {
            open_url("https://github.com/sm-yjr/vocal-more/releases/latest")?;
        }
        Ok(())
    }
    pub fn tick(&mut self) {
        if self.closed {
            return;
        }
        self.clipboard.tick();
        self.tick_bluetooth();
        self.playback.tick();
        if self.last_watchdog.elapsed() >= Duration::from_secs(2) {
            self.last_watchdog = Instant::now();
            self.acceptance_tick();
            if let Some(h) = &mut self.hotkeys {
                h.tick();
            }
            self.commands.request("platform_status", self.status());
        }
    }
    /// Idle listeners need a two-second watchdog. A pending clipboard restore
    /// or active playback shortens this deadline even when all windows hide.
    pub fn next_tick_delay(&self) -> Duration {
        [
            Some(Duration::from_secs(2).saturating_sub(self.last_watchdog.elapsed())),
            self.clipboard.next_tick_delay(),
            self.playback.next_tick_delay(),
            self.bluetooth.next_tick(),
        ]
        .into_iter()
        .flatten()
        .min()
        .unwrap_or(Duration::from_secs(2))
        .max(Duration::from_millis(1))
    }
    /// Perform a native action on the main AppKit thread; returns false for
    /// methods owned by the backend or host orchestration.
    pub fn action(&mut self, method: &str, data: &Value) -> Result<bool> {
        match method {
            "open_bluetooth_settings" => open_url(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth",
            )?,
            "platform_bluetooth_start" => {
                self.cancel_bluetooth_delivery();
                self.bluetooth.configure(&self.config, true);
            }
            "platform_bluetooth_external_target" => {
                anyhow::ensure!(
                    self.acceptance_directory.is_some(),
                    "外部测试目标仅用于隔离固定文字开发包"
                );
                self.bluetooth.cancel_outgoing();
                self.editor.select_external_fixture()?;
            }
            "platform_bluetooth_editor" if data["external_fixture"] == true => {
                anyhow::ensure!(
                    self.acceptance_directory.is_some(),
                    "外部测试目标仅用于隔离固定文字开发包"
                );
                self.bluetooth.cancel_outgoing();
                self.editor.select_external_fixture()?;
            }
            "platform_bluetooth_editor" => {
                let title = if self.config["bluetooth"]["mode"] == "send" {
                    "Studio — Vocal More Bluetooth Test"
                } else {
                    "MacBook — Vocal More Bluetooth Test"
                };
                self.editor.show(title)?;
            }
            "platform_bluetooth_stop" => {
                self.cancel_bluetooth_delivery();
                self.bluetooth.close();
            }
            "platform_bluetooth_test" => {
                if self.config["bluetooth"]["output_target"] == "local"
                    && !self.editor.is_external()
                {
                    let title = if self.config["bluetooth"]["mode"] == "send" {
                        "Studio — Vocal More Bluetooth Test"
                    } else {
                        "MacBook — Vocal More Bluetooth Test"
                    };
                    self.editor.show(title)?;
                }
                self.bluetooth_send("VM-BT-PROBE-20261013")?;
            }
            "open_file" => open_file(data["path"].as_str().context("缺少文件路径")?)?,
            "open_url" => open_url(data["url"].as_str().context("缺少链接")?)?,
            "open_microphone_settings" => open_url(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            )?,
            "open_accessibility_settings" => open_url(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            )?,
            "open_screen_recording_settings" => open_url(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            )?,
            "microphone_permission_required" => self.request_microphone_permission(),
            "request_accessibility_permission" => request_accessibility_permission(),
            "request_screen_permission" => unsafe {
                ffi::CGRequestScreenCaptureAccess();
            },
            "platform_check_updates" | "check_updates" => self.check_updates()?,
            "platform_export_diagnostics" => {
                if let Some(path) = choose_diagnostics_path()? {
                    self.commands
                        .request("export_diagnostics", json!({"path":path}));
                }
            }
            "play_recording" => self.play(
                data["id"].as_str().context("缺少录音 ID")?,
                Path::new(data["path"].as_str().context("缺少录音路径")?),
            )?,
            "stop_recording" => self.stop_playback(data["id"].as_str()),
            "mic_test_playback" => {
                self.play_preview(data["wav_base64"].as_str().context("缺少麦克风试听数据")?)?
            }
            "stop_mic_test_playback" => self.stop_preview(),
            "recording_deleted" => self.stop_playback(data["id"].as_str()),
            "copy_transcript" => {
                self.copy_text(data["text"].as_str().unwrap_or_default())?;
                self.commands.request(
                    "platform_event",
                    json!({"method":"copiedFeedback","params":{"id":data["id"]}}),
                );
            }
            "begin_hotkey_capture" => self.begin_hotkey_capture(),
            "end_hotkey_capture" => self.end_hotkey_capture(),
            _ => return Ok(false),
        }
        Ok(true)
    }
    fn request_microphone_permission(&self) {
        let commands = self.commands.clone();
        let callback = block2::RcBlock::new(move |_allowed: objc2::runtime::Bool| {
            commands.request("refresh_environment", json!({}));
        });
        unsafe {
            let _: () = msg_send![class(c"AVCaptureDevice"),requestAccessForMediaType:&*ns("soun"),completionHandler:&*callback];
        }
    }
    pub fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        self.cancel_bluetooth_delivery();
        self.bluetooth.close();
        self.editor.close();
        if let Some(input) = &mut self.current_input {
            input.stop();
        }
        if self.clipboard.cancel_delivery().is_err() {
            self.clipboard.mark_failed();
        }
        self.playback.close();
        self.menu.close();
        self.hotkeys.take();
        self.accessibility.take();
    }
    pub fn shutdown_finished(&mut self) -> bool {
        self.clipboard.tick();
        self.closed
            && !self.clipboard.is_pending()
            && !self.clipboard.is_delivering()
            && self
                .current_input
                .as_ref()
                .is_none_or(|input| input.finished())
    }
    pub fn shutdown_failed(&self) -> bool {
        self.clipboard.failed()
    }
}
impl Drop for Platform {
    fn drop(&mut self) {
        self.close();
    }
}

pub fn open_url(url: &str) -> Result<()> {
    if url.contains('\0') {
        bail!("链接无效");
    }
    unsafe {
        let target: Option<Retained<AnyObject>> =
            msg_send![class(c"NSURL"),URLWithString:&*ns(url)];
        let target = target.context("链接无效")?;
        let workspace: Retained<AnyObject> = msg_send![class(c"NSWorkspace"), sharedWorkspace];
        let ok: bool = msg_send![&*workspace,openURL:&*target];
        if !ok {
            bail!("无法打开链接");
        }
    }
    Ok(())
}
pub fn open_file(path: &str) -> Result<()> {
    if !Path::new(path).is_file() {
        bail!("文件不存在");
    }
    // Preserve the established text-editor routing, including filenames that
    // contain shell syntax: pass arguments directly without invoking a shell.
    std::process::Command::new("/usr/bin/open")
        .args(["-t", path])
        .spawn()
        .context("无法打开文件")?;
    Ok(())
}
fn request_accessibility_permission() {
    unsafe {
        let key = ns("AXTrustedCheckOptionPrompt");
        let key = (&*key as *const NSString).cast();
        let value = ffi::kCFBooleanTrue;
        if let Some(dictionary) = ffi::Owned::from_create(ffi::CFDictionaryCreate(
            ptr::null(),
            &key,
            &value,
            1,
            ffi::kCFTypeDictionaryKeyCallBacks.as_ptr().cast(),
            ffi::kCFTypeDictionaryValueCallBacks.as_ptr().cast(),
        )) {
            ffi::AXIsProcessTrustedWithOptions(dictionary.as_ptr());
        }
    }
}
fn choose_diagnostics_path() -> Result<Option<String>> {
    unsafe {
        let panel: Retained<AnyObject> = msg_send![class(c"NSSavePanel"), savePanel];
        let _: () = msg_send![&*panel,setNameFieldStringValue:&*ns("vocal-more-diagnostics.json")];
        let result: isize = msg_send![&*panel, runModal];
        if result != 1 {
            return Ok(None);
        }
        let url: Option<Retained<AnyObject>> = msg_send![&*panel, URL];
        let url = url.context("未选择诊断包路径")?;
        let path: Option<Retained<NSString>> = msg_send![&*url, path];
        Ok(path.map(|v| v.to_string()))
    }
}

/// Synthetic, nonactivating benchmark: no hotkeys, AX, clipboard or microphone.
#[cfg(feature = "perf-test")]
pub fn measure_status_updates(mtm: MainThreadMarker, commands: CommandSink) -> Value {
    fn distribution(mut values: Vec<f64>) -> Value {
        values.sort_by(f64::total_cmp);
        json!({"samples":values.len(),"median_ms":values[values.len()/2],
            "p95_ms":values[(values.len()*95/100).min(values.len()-1)]})
    }
    let mut menu = menu::Menu::new(mtm, commands, false);
    let mut snapshot = json!({"config":vocal_more_backend::catalog::CONTRACT["defaults"],
        "devices":[{"name":"Synthetic microphone","uid":"fixture-device"}],
        "asr_models":vocal_more_backend::catalog::CONTRACT["asr_models"],"state":"idle"});
    menu.update(&snapshot);
    let mut rebuild = Vec::new();
    let mut status = Vec::new();
    for _ in 0..40 {
        for state in ["starting", "recording", "processing", "idle"] {
            snapshot["state"] = json!(state);
            let start = Instant::now();
            menu.update(&snapshot); // The exact release 0.6.0 state-change path.
            rebuild.push(start.elapsed().as_secs_f64() * 1000.0);
            let start = Instant::now();
            menu.set_status(state); // The revised state-change path.
            status.push(start.elapsed().as_secs_f64() * 1000.0);
        }
    }
    json!({"legacy_menu_rebuild":distribution(rebuild),"status_only":distribution(status)})
}
