// SPDX-License-Identifier: GPL-3.0-only
//! macOS services for the Rust desktop host. AppKit objects never cross threads.
mod accessibility;
mod ffi;
mod hotkeys;
mod menu;
mod paste;
mod playback;
mod screen;
mod termination;
mod updater;

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
        let menu = menu::Menu::new(mtm, commands.clone());
        let playback = playback::Playback::new(commands.clone());
        let updater = updater::Updater::new(mtm, config);
        Ok(Self {
            _main_thread: mtm,
            commands,
            hotkeys: Some(hotkeys),
            accessibility: Some(accessibility),
            clipboard: paste::Clipboard::default(),
            menu,
            playback,
            updater,
            config: config.clone(),
            last_watchdog: Instant::now(),
            closed: false,
        })
    }
    pub fn update_config(&mut self, config: &Value) {
        self.config = config.clone();
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
    pub fn set_status(&mut self, state: &str) {
        self.menu.set_status(state);
    }
    pub fn status(&self) -> Value {
        let diagnostics = self
            .hotkeys
            .as_ref()
            .map(|h| h.status())
            .unwrap_or(Value::Null);
        json!({"accessibility":unsafe {ffi::AXIsProcessTrusted()},"hotkey_listener":diagnostics["running"]==true&&diagnostics["event_tap_enabled"]==true,"hotkeys":diagnostics,"updater":self.updater.status()})
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
            json!({"active":active,"running":running,"activation_policy":policy,"key_window":key.is_some(),"main_window":main.is_some(),"foreground_pid":pid,"self_pid":std::process::id(),"screen_locked":session_boolean("CGSSessionScreenIsLocked"),"on_console":session_boolean("kCGSessionOnConsoleKey")})
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
        self.playback.tick();
        if self.last_watchdog.elapsed() >= Duration::from_secs(2) {
            self.last_watchdog = Instant::now();
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
        self.playback.close();
        self.menu.close();
        self.hotkeys.take();
        self.accessibility.take();
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
