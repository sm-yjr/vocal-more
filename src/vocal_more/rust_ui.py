"""Existing macOS surfaces driven by the Rust application protocol.

Only OS events, AppKit presentation, Accessibility reads and paste execution
live here. The child service owns settings, audio, providers and persistence.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from types import SimpleNamespace

import rumps
from Foundation import NSOperationQueue, NSRunLoop, NSRunLoopCommonModes, NSTimer

from .localization import t
from .paths import bundled_resource_path, default_data_dir
from .rust_client import RustBackendClient, backend_paths


def namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: item if key == "custom_key" else namespace(item) for key, item in value.items()})
    # Custom shortcut dictionaries are platform event descriptors.
    return value


def snapshot_json(snapshot):
    return None if snapshot is None else {f.name: getattr(snapshot, f.name) for f in fields(snapshot)
                                         if not f.name.startswith("_")}




class _RemoteHistory:
    def __init__(self, app):
        self.app = app

    def list_recordings(self):
        return self.app.snapshot.get("recordings", [])

    def storage_summary(self):
        return self.app.snapshot.get("recording_storage", {})


class _RemoteMic:
    def __init__(self, app):
        self.app = app

    def cleanup(self):
        self.app.request("stop_mic_test")
        self.app._stop_player()


class RustVocalMoreApp(rumps.App):
    def __init__(self, binary, data_dir, native_library=None, *, import_from=None, no_hotkeys=False):
        super().__init__("Vocal-More", icon=str(bundled_resource_path("resources", "icons", "icon_idle.png")),
                         template=True, quit_button=None)
        self._closing = False
        self._drain_lock = threading.Lock()
        self._drain_scheduled = False
        self._events = queue.Queue(maxsize=1024)
        self._os_slots = threading.BoundedSemaphore(16)
        self._observing = False
        self._hotkey_retry_at = 0.0
        self._os_lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vocal-more-platform")
        self._settings = None
        self._player = None
        self._hotkeys = None
        self._no_hotkeys = no_hotkeys
        self._last_text = ""
        self._prompt_hint = ""
        self._retained = {}
        self._updater = None
        args = ("--import-python", str(import_from)) if import_from else ()
        self.client = RustBackendClient(binary, data_dir, native_library=native_library,
                                        on_event=self._enqueue, extra_args=args)
        try:
            self.snapshot = self.client.call("initialize", timeout=30)
        except Exception:
            self.client.close()
            self._os_lane.shutdown(wait=False, cancel_futures=True)
            raise
        self.config = namespace(self.snapshot["config"])
        from .ui.floating_capsule import FloatingCapsule
        self.capsule = FloatingCapsule(on_cancel=lambda: self.request("cancel"),
                                      on_finish=lambda: self.request("finish"),
                                      config_provider=lambda: self.config,
                                      prompt_hint_provider=lambda: self._prompt_hint)
        self.capsule.set_interface_language(self.config.ui.language)
        from .core.accessibility_text import MacOSFocusedTextProvider
        self._text_provider = MacOSFocusedTextProvider()
        self._build_menu()
        if not no_hotkeys:
            from .core.hotkey_manager import HotkeyManager
            self._hotkeys = HotkeyManager(config=self.config,
                on_fn_pressed=lambda: self.request("hotkey_pressed"),
                on_fn_released=lambda: self.request("hotkey_released"),
                on_double_cmd=lambda: self.request("toggle_recording"),
                on_escape_pressed=lambda: self.request("cancel"))
            self._hotkeys.start()
        self._send_platform_status()
        # Events wake the main queue immediately. Only the permission/hotkey
        # watchdog needs an idle timer; do not poll all UI traffic at 25 Hz.
        self._timer = NSTimer.timerWithTimeInterval_repeats_block_(2.0, True, lambda _: self._watchdog())
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    def _enqueue(self, event):
        if self._closing:
            return
        # Backpressure preserves terminal/paste events. The main queue drains promptly.
        while not self._closing:
            try:
                if threading.current_thread() is threading.main_thread():
                    if self._events.full():
                        self._drain()
                    self._events.put_nowait(event)
                else:
                    self._events.put(event, timeout=0.1)
                self._schedule_drain()
                return
            except queue.Full:
                continue

    def _schedule_drain(self):
        with self._drain_lock:
            if self._closing or self._drain_scheduled:
                return
            self._drain_scheduled = True
        NSOperationQueue.mainQueue().addOperationWithBlock_(self._drain)

    def request(self, method, params=None, callback=None):
        if self._closing:
            return
        future = self.client.request(method, params)
        def complete(result):
            try:
                value = result.result()
            except Exception as error:
                self._enqueue({"method": "request_failed", "params": {"message": str(error),
                    "method": method, "action": (params or {}).get("action", "")}})
            else:
                if callback:
                    self._enqueue({"method": "_callback", "params": (callback, value)})
        future.add_done_callback(complete)

    def _submit_os(self, function, data):
        if self._closing or not self._os_slots.acquire(blocking=False):
            self._notify("系统操作繁忙，请稍后再试")
            return False
        task = self._os_lane.submit(function, data)
        task.add_done_callback(lambda _: self._os_slots.release())
        return True

    def _watchdog(self):
        if self._closing:
            return
        if self._hotkeys and not self._hotkeys.diagnostics()["running"] and time.monotonic() >= self._hotkey_retry_at:
            self._hotkey_retry_at = time.monotonic() + 2
            from ApplicationServices import AXIsProcessTrusted
            if AXIsProcessTrusted():
                self._hotkeys.start()
                self._send_platform_status()
        if not self._events.empty():
            self._schedule_drain()

    def _drain(self):
        with self._drain_lock:
            self._drain_scheduled = False
        if self._closing:
            return
        for _ in range(128):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                self._event(event["method"], event.get("params", {}))
            except Exception as error:
                self._notify(str(error))
        if not self._events.empty():
            self._schedule_drain()

    def _t(self, key):
        return t(self.config.ui.language, key)

    def _item(self, title, method, params=None):
        return rumps.MenuItem(title, callback=lambda _: self.request(method, params))

    def _build_menu(self):
        self.menu.clear()
        self.menu.add(rumps.MenuItem("Vocal-More " + self.snapshot["version"]))
        self._state_item = rumps.MenuItem(self._t("menu_status_" + self.snapshot.get("state", "idle")))
        self.menu.add(self._state_item)
        mode = rumps.MenuItem(self._t("menu_recording_mode"))
        for key in ("walkie_talkie", "realtime_long"):
            item = self._item(self._t("mode_" + key), "set_mode", {"mode": key})
            item.state = key == self.config.default_mode
            mode.add(item)
        self.menu.add(mode)
        models = rumps.MenuItem(self._t("menu_asr_model"))
        for model in self.snapshot["asr_models"]:
            item = self._item(model.get("name", model["id"]), "set_asr_model", {"model": model["id"]})
            item.state = model["id"] == self.config.asr.model
            models.add(item)
        self.menu.add(models)
        microphones = rumps.MenuItem(self._t("menu_microphone"))
        microphones.add(self._item(self._t("menu_microphone_system_default"), "set_device", {"device": None}))
        for device in self.snapshot["devices"]:
            item = self._item(device["name"], "set_device", {"device": device["name"]})
            item.state = self.config.audio.input_device in (device["name"], device.get("uid"))
            microphones.add(item)
        microphones.add(self._item(self._t("menu_run_environment_check"), "refresh_devices"))
        self.menu.add(microphones)
        polish = self._item(self._t("menu_enable_polishing"), "set_config", {"key": "enable_polish", "value": not self.config.enable_polish})
        polish.state = self.config.enable_polish
        self.menu.add(polish)
        levels = rumps.MenuItem(self._t("menu_polish_strength"))
        for level in ("minimal", "balanced", "strong"):
            item = self._item(self._t("polish_level_" + level), "set_config", {"key": "llm.level", "value": level})
            item.state = level == self.config.llm.level
            levels.add(item)
        self.menu.add(levels)
        self.menu.add(None)
        self.menu.add(rumps.MenuItem(self._t("menu_copy_last_result"), callback=lambda _: self._copy(self._last_text)))
        self.menu.add(rumps.MenuItem(self._t("menu_settings"), callback=lambda _: self.show_settings()))
        self.menu.add(rumps.MenuItem(self._t("menu_environment"), callback=lambda _: self.show_settings("general")))
        self.menu.add(rumps.MenuItem(self._t("menu_export_diagnostics"), callback=lambda _: self._export()))
        self.menu.add(rumps.MenuItem(self._t("menu_check_for_updates"), callback=lambda _: self._check_updates()))
        self.menu.add(None)
        self.menu.add(rumps.MenuItem(self._t("menu_quit"), callback=lambda _: self.quit()))

    def _send_platform_status(self):
        from ApplicationServices import AXIsProcessTrusted
        running = bool(self._hotkeys and self._hotkeys.diagnostics().get("running"))
        self.request("platform_status", {"accessibility": bool(AXIsProcessTrusted()), "hotkey_listener": running})
        self.request("refresh_environment")

    def show_settings(self, initial_tab=""):
        self.request("refresh_devices")
        self._send_platform_status()
        self.request("snapshot", callback=lambda data: self._show_snapshot(data, initial_tab))

    def _show_snapshot(self, data, initial_tab):
        self.snapshot = data
        if self._settings is None:
            from .ui.settings_window import SettingsWindow
            self._settings = SettingsWindow(message_dispatcher=lambda message: self.request("ui_action", message),
                on_sync_form_state=lambda state: self.request("sync_form_state", {"state": state}),
                recording_store=_RemoteHistory(self), mic_test_controller=_RemoteMic(self),
                audio_status_provider=lambda: self.snapshot.get("audio_input_status", {}))
        keys = ("config", "asr_models", "llm_models", "devices", "dictionary", "polish_prompt_presets", "version",
                "dictionary_learning_records", "environment_checks", "audio_input_status")
        values = {key: data[key] for key in keys}
        values["config"] = {**data["config"], "_api_key_set": data["api_key_set"]}
        self._settings.show(**values, initial_tab=initial_tab)

    def _js(self, function, *args):
        if self._settings is not None:
            self._settings._eval_js(function + "(" + ",".join(json.dumps(arg, ensure_ascii=False) for arg in args) + ")")

    def _event(self, method, data):
        if method == "_callback":
            data[0](data[1])
        elif method == "backend_disconnected":
            self._state_item.title = self._t("menu_status_failed")
            self.capsule.hide()
            if self._hotkeys:
                self._hotkeys.stop()
                self._hotkeys = None
            self._notify("Rust 服务已退出，请重新启动 Vocal More。" + data["message"])
        elif method in ("error", "warning", "request_failed"):
            action = data.get("action", "")
            if action in ("startMicTest", "playMicTest"):
                self._js("micTestError", data["message"])
            if method != "warning":
                self.capsule.hide()
            self._notify(data["message"])
        elif method == "state_changed":
            self.snapshot.update(data)
            state = data["state"]
            self._state_item.title = self._t("menu_status_" + state)
            icon = "icon_recording.png" if state in ("starting", "recording") else "icon_idle.png"
            self.icon = str(bundled_resource_path("resources", "icons", icon))
            if data.get("microphone_test"):
                return
            if state == "starting":
                self.capsule.show("handsFree" if data["current_mode"] == "realtime_long" else "pushToTalk")
            elif state == "idle":
                self.capsule.hide()
            else:
                self.capsule.update_state("processing" if state == "cancelling" else state)
        elif method == "gesture_changed":
            if data.get("latched"):
                self.capsule.show("handsFree")
        elif method == "audio_level":
            self.capsule.update_audio_level(data["waveform_level"])
        elif method == "partial_result":
            self.capsule.update_streaming_text(data["text"])
        elif method == "prompt_hint":
            self._prompt_hint = data["hint"]
            if self.capsule._current_state != "hidden":
                self.capsule._update_prompt_hint_on_main_thread()
        elif method == "processing_stage":
            self.capsule.set_processing_stage(data["stage"])
        elif method == "final_result":
            self._last_text = data["text"]
        elif method == "paste_requested":
            self._submit_os(self._paste, data)
        elif method == "observation_poll":
            if not self._observing:
                self._observing = self._submit_os(self._observe, data)
        elif method == "_observation_read":
            self._observing = False
        elif method == "observation_ended":
            self._retained.pop(data.get("observation_id"), None)
        elif method == "api_key_revealed":
            self._js("updateConfig", "api_key", data["value"])
        elif method == "config_changed":
            self.snapshot["config"] = data["config"]
            self.config = namespace(data["config"])
            self.capsule.set_interface_language(self.config.ui.language)
            if self._hotkeys:
                self._hotkeys.config = self.config
                self._hotkeys.set_active_hotkeys(self.config.hotkey.active_hotkeys)
                custom = self.config.hotkey.custom_keys or ([self.config.hotkey.custom_key] if self.config.hotkey.custom_key else [])
                self._hotkeys.set_custom_keys(custom)
            for key, value in data["config"].items():
                if key != "api_key":
                    self._js("updateConfig", key, value)
            self._js("updateConfig", "_api_key_set", data["api_key_set"])
            self._build_menu()
        elif method == "devices_changed":
            self.snapshot["devices"] = data["devices"]
            self.snapshot["audio_input_status"] = data["audio_input_status"]
            self._build_menu()
            if self._settings:
                self._settings.update_devices(data["devices"], data["selected_device"])
                self._settings.update_audio_input_status(data["audio_input_status"])
        elif method == "audio_input_status":
            self.snapshot["audio_input_status"] = data
            self._js("loadAudioInputStatus", data)
        elif method == "environment_changed":
            self.snapshot["environment_checks"] = data
            self._js("loadEnvironmentChecks", data)
        elif method == "dictionary_changed":
            self.snapshot["dictionary"] = data
            self._js("loadDictionary", data)
        elif method == "dictionary_learning_changed":
            self.snapshot["dictionary"] = data["dictionary"]
            self.snapshot["dictionary_learning_records"] = data["records"]
            self._js("loadDictionary", data["dictionary"])
            self._js("loadDictionaryLearning", data["records"])
        elif method == "dictionary_learning_summary":
            self._notify("已学习词条：" + "、".join(data["terms"]))
        elif method == "recordings_changed":
            self.snapshot["recordings"] = data["recordings"]
            self.snapshot["recording_storage"] = data["storage"]
            self._js("loadRecordings", data["recordings"])
        elif method == "recording_compaction_complete":
            self._js("recordingCompactionComplete", data["storage"])
            self.request("list_recordings")
        elif method == "recording_deleted":
            self._stop_player(data["id"])
            self._js("recordingDeleted", data["id"])
            self.request("list_recordings")
        elif method == "play_recording":
            from .ui.recording_player import RecordingPlayer
            if self._player is None:
                self._player = RecordingPlayer(lambda id: self._js("recordingPlaybackEnded", id))
            if self._player.play(data["id"], Path(data["path"])):
                self._js("playAudio", data["id"], None)
        elif method == "stop_recording":
            self._stop_player(data.get("id"))
        elif method == "copy_transcript":
            self._copy(data["text"])
            self._js("copiedFeedback", data["id"])
        elif method == "open_file":
            subprocess.Popen(["/usr/bin/open", "-t", data["path"]])
        elif method == "open_url":
            subprocess.Popen(["/usr/bin/open", data["url"]])
        elif method == "microphone_permission_required":
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda _: self.request("refresh_environment"))
        elif method == "model_check_complete":
            self._js("dashscopeModelCheckComplete", data)
        elif method == "resync":
            self.snapshot = data
            self._event("config_changed", data)
            self._last_text = (data.get("last_result") or {}).get("text", self._last_text)
            for paste in data.get("pending_pastes", []):
                self._submit_os(self._paste, paste)
            if self._settings and self._settings.is_visible():
                self._show_snapshot(data, "")
        else:
            events = {
                "mic_test_started": ("micTestStarted", []), "mic_test_complete": ("micTestComplete", []),
                "mic_test_error": ("micTestError", [data.get("message", "")]),
                "mic_test_level": ("micTestLevel", [data.get("rms", 0)]),
                "mic_test_playback": ("micTestPlayback", [data.get("wav_base64", "")]),
                "model_check_started": ("dashscopeModelCheckStarted", []),

                "retry_started": ("retryStarted", [data.get("id")]),
                "retry_completed": ("retryCompleted", [data.get("id"), data.get("transcript", "")]),
                "retry_failed": ("retryFailed", [data.get("id"), data.get("error", "")]),
                "recording_compaction_started": ("recordingCompactionStarted", []),
                "recording_compaction_complete": ("recordingCompactionComplete", [data.get("storage", data)]),
                "recording_compaction_failed": ("recordingCompactionFailed", [data.get("message", "")]),
            }
            if method in events:
                function, args = events[method]
                self._js(function, *args)

    def _paste(self, event):
        try:
            paste = self.client.call("claim_paste", {"token": event["token"]})
            if paste.get("cancelled") or self._closing:
                return
            before = self._text_provider.capture_focused() if paste["observe_correction"] else None
            prepared = self.client.call("prepare_paste_observation", {"token": paste["token"], "snapshot": snapshot_json(before)})
            if prepared.get("cancelled") or self._closing:
                return
            if prepared.get("observation_id"):
                self._retained[prepared["observation_id"]] = before
            from .core.keyboard_sim import KeyboardSimulator
            from .core.macos_native_paste import MacOSNativePaste
            KeyboardSimulator(native_fast_paste=paste["native_fast_paste"],
                native_paster=MacOSNativePaste(restore_clipboard=paste["restore_clipboard"]),
                restore_clipboard=paste["restore_clipboard"]).paste_text(paste["text"])
        except Exception as error:
            self.request("cancel_observation")
            self._enqueue({"method": "error", "params": {"message": str(error)}})

    def _observe(self, data):
        try:
            identifier = data["observation_id"]
            before = self._retained.get(identifier)
            focused = self._text_provider.capture_focused()
            retained = self._text_provider.capture_target(before) if before and (focused is None or not before.is_same_target(focused)) else None
            self.request("poll_observation", {"observation_id": identifier, "focused": snapshot_json(focused), "retained": snapshot_json(retained)})
        finally:
            self._enqueue({"method": "_observation_read"})

    def _stop_player(self, identifier=None):
        if self._player:
            self._player.stop(identifier)

    @staticmethod
    def _copy(text):
        if text:
            from AppKit import NSPasteboard, NSStringPboardType
            pasteboard = NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setString_forType_(text, NSStringPboardType)

    @staticmethod
    def _notify(message):
        rumps.notification("Vocal More", "", message)

    def _check_updates(self):
        from .infrastructure.sparkle_updater import SparkleUpdater
        if self._updater is None:
            self._updater = SparkleUpdater()
        if not self._updater.check_for_updates():
            subprocess.Popen(["/usr/bin/open", "https://github.com/sm-yjr/vocal-more/releases/latest"])

    def _export(self):
        from AppKit import NSSavePanel
        panel = NSSavePanel.savePanel()
        panel.setNameFieldStringValue_("vocal-more-diagnostics.json")
        if panel.runModal() == 1:
            self.request("export_diagnostics", {"path": str(panel.URL().path())}, callback=lambda _: self._notify("诊断文件已导出"))

    def close(self):
        if self._closing:
            return
        if self._settings:
            self._settings.close()
        self._stop_player()
        self._closing = True
        self._timer.invalidate()
        if self._hotkeys:
            self._hotkeys.stop()
        self.capsule.hide()
        self._os_lane.shutdown(wait=True, cancel_futures=True)
        self.client.close()

    def quit(self):
        self.close()
        rumps.quit_application()


def main():
    parser = argparse.ArgumentParser(description="Vocal More with the Rust application backend")
    parser.add_argument("--backend", choices=("rust",), default="rust")
    parser.add_argument("--backend-data-dir", type=Path)
    parser.add_argument("--backend-binary", type=Path)
    parser.add_argument("--no-import", action="store_true")
    parser.add_argument("--no-hotkeys", action="store_true", help="Isolated UI verification without global shortcuts")
    parser.add_argument("--show-settings", action="store_true")
    args = parser.parse_args()
    binary, native = backend_paths()
    binary = args.backend_binary or binary
    if not binary.is_file():
        parser.error("Rust backend is missing. Run bash scripts/build_rust_host.sh first.")
    data_dir = args.backend_data_dir or default_data_dir() / "rust-backend"
    import_from = default_data_dir() if not args.no_import and not args.backend_data_dir else None
    app = RustVocalMoreApp(binary, data_dir, native if native.is_file() else None,
                          import_from=import_from, no_hotkeys=args.no_hotkeys)
    if args.show_settings:
        app.show_settings()
    try:
        app.run()
    finally:
        app.close()


if __name__ == "__main__":
    main()
