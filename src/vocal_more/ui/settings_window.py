"""Settings window using NSWindow + WKWebView."""

import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

import objc
from AppKit import (
    NSApp,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSColor,
    NSScreen,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSDate, NSMakeRect, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSURL
from WebKit import (
    WKUserContentController,
    WKUserScript,
    WKWebView,
    WKWebViewConfiguration,
)

from ..application.background_executor import BackgroundExecutor
from ..infrastructure.pricing import merge_billing
from ..localization import normalize_ui_language, t
from .mic_test_controller import MicTestController
from .settings_actions import SettingsActionDispatcher
from .settings_bridge import SettingsBridge
from .webview_bridge import objc_to_python

# WKUserScriptInjectionTime
WKUserScriptInjectionTimeAtDocumentStart = 0

# Resolve WKScriptMessageHandler protocol for explicit conformance.
# Must happen after WebKit framework is loaded by the imports above.
try:
    _WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")
except (AttributeError, Exception):
    _WKScriptMessageHandler = None


def _handler_bases():
    if _WKScriptMessageHandler is not None:
        return {"protocols": [_WKScriptMessageHandler]}
    return {}

class _SettingsMessageHandler(NSObject, **_handler_bases()):
    """WKScriptMessageHandler to receive messages from settings JS."""

    def initWithCallback_(self, callback):
        self = objc.super(_SettingsMessageHandler, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            body = objc_to_python(message.body())
            if isinstance(body, dict) and self._callback:
                self._callback(body)
        except Exception:
            print("[Settings] Error handling JS message:", file=sys.stderr)
            traceback.print_exc()


class _WindowDelegate(NSObject):
    """NSWindowDelegate to detect when the settings window is closed."""

    def initWithCloseCallback_(self, close_callback):
        self = objc.super(_WindowDelegate, self).init()
        if self is None:
            return None
        self._close_callback = close_callback
        return self

    def windowShouldClose_(self, sender):
        if self._close_callback:
            self._close_callback()
        return False


class SettingsWindow:
    """Settings window with 6-tab WKWebView interface.

    Data flow for initial load:
        1. Python calls show() → injects config data as a WKUserScript
           that sets window._initData at document-start
        2. Python reloads the HTML page
        3. JS body script reads window._initData and calls loadAll()
        4. No postMessage round-trip needed for initial data

    Data flow for user changes:
        5. User changes a setting → JS sends postMessage({action, key, value})
        6. Python receives it → updates config → calls config.save()
    """

    WINDOW_WIDTH = 680
    WINDOW_HEIGHT = 480

    def __init__(
        self,
        on_set_config: Optional[Callable[[str, Any], None]] = None,
        on_set_asr_model: Optional[Callable[[str, str], None]] = None,
        on_sync_form_state: Optional[Callable[[dict], None]] = None,
        on_set_device: Optional[Callable[[Optional[str]], None]] = None,
        on_set_active_hotkeys: Optional[Callable[[list[str]], None]] = None,
        on_add_dict_entry: Optional[Callable[[str, list[str]], None]] = None,
        on_remove_dict_entry: Optional[Callable[[str], None]] = None,
        on_refresh_devices: Optional[Callable[[], None]] = None,
        on_open_config_file: Optional[Callable[[], None]] = None,
        on_open_dict_file: Optional[Callable[[], None]] = None,
        on_open_external: Optional[Callable[[str], None]] = None,
        recording_store: Optional[object] = None,
    ):
        self._on_set_config = on_set_config
        self._on_set_asr_model = on_set_asr_model
        self._on_sync_form_state = on_sync_form_state
        self._on_set_device = on_set_device
        self._on_set_active_hotkeys = on_set_active_hotkeys
        self._on_add_dict_entry = on_add_dict_entry
        self._on_remove_dict_entry = on_remove_dict_entry
        self._on_refresh_devices = on_refresh_devices
        self._on_open_config_file = on_open_config_file
        self._on_open_dict_file = on_open_dict_file
        self._on_open_external = on_open_external
        self._recording_store = recording_store

        self._window: Optional[NSWindow] = None
        self._webview: Optional[WKWebView] = None
        self._content_controller: Optional[WKUserContentController] = None
        self._message_handler: Optional[_SettingsMessageHandler] = None
        self._window_delegate: Optional[_WindowDelegate] = None
        self._html_url: Optional[NSURL] = None
        self._sync_timer: Optional[NSTimer] = None
        self._last_synced_state: Optional[str] = None
        self._js_queue: queue.Queue = queue.Queue()
        self._interface_language = "en"
        self._background_tasks = BackgroundExecutor(
            max_workers=2,
            thread_name_prefix="vocal-more-settings-tasks",
        )
        self._bridge = SettingsBridge()
        self._mic_test_controller = self._build_mic_test_controller()
        self._dispatcher = self._build_action_dispatcher()

        self._setup()

    def _setup(self) -> None:
        """Create NSWindow and WKWebView."""
        screen = NSScreen.mainScreen()
        screen_frame = screen.frame()
        x = (screen_frame.size.width - self.WINDOW_WIDTH) / 2
        y = (screen_frame.size.height - self.WINDOW_HEIGHT) / 2

        frame = NSMakeRect(x, y, self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        style_mask = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style_mask, NSBackingStoreBuffered, False
        )
        self._window.setTitle_(t(self._interface_language, "settings_title"))
        self._window.setMinSize_((520, 380))
        self._window.setReleasedWhenClosed_(False)
        self._window.setBackgroundColor_(NSColor.windowBackgroundColor())

        # Window delegate for close event
        self._window_delegate = _WindowDelegate.alloc().initWithCloseCallback_(
            self._on_window_close_requested
        )
        self._window.setDelegate_(self._window_delegate)

        # WKWebView with message handler for JS → Python communication
        config = WKWebViewConfiguration.alloc().init()
        self._content_controller = WKUserContentController.alloc().init()

        self._message_handler = _SettingsMessageHandler.alloc().initWithCallback_(
            self._on_js_message
        )
        self._content_controller.addScriptMessageHandler_name_(
            self._message_handler, "settings"
        )
        config.setUserContentController_(self._content_controller)

        webview_frame = NSMakeRect(0, 0, self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        self._webview = WKWebView.alloc().initWithFrame_configuration_(
            webview_frame, config
        )
        self._window.setContentView_(self._webview)

        # Resolve HTML path once
        html_path = self._get_html_path()
        if html_path and os.path.exists(html_path):
            self._html_url = NSURL.fileURLWithPath_(html_path)
            print(f"[Settings] HTML path: {html_path}")
        else:
            print(f"[Settings] WARNING: HTML not found at: {html_path}")

    def _get_html_path(self) -> Optional[str]:
        """Get path to settings.html."""
        package_dir = Path(__file__).parent.parent.parent.parent
        html_path = package_dir / "resources" / "settings" / "settings.html"
        if html_path.exists():
            return str(html_path)
        return None

    def _set_accessory_policy(self) -> None:
        """Hide the app from the dock when settings is dismissed."""
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def set_interface_language(
        self,
        language: str,
        update_frontend: bool = True,
    ) -> None:
        """Update the current interface language."""
        self._interface_language = normalize_ui_language(language)
        if self._window:
            self._window.setTitle_(t(self._interface_language, "settings_title"))
        if update_frontend and self._webview and self.is_visible():
            self._eval_js(f"setInterfaceLanguage({json.dumps(self._interface_language)})")

    def _request_form_state_sync(self) -> None:
        """Pull the current form state from JS as a persistence backstop."""
        if not self._webview or not self._on_sync_form_state:
            return

        result_box = {"value": None, "error": None}

        def callback(result, error):
            result_box["value"] = result
            result_box["error"] = error

        self._webview.evaluateJavaScript_completionHandler_(
            "JSON.stringify(collectFormState())", callback
        )

        for _ in range(10):
            if result_box["value"] is not None or result_box["error"] is not None:
                break
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.01)
            )

        if result_box["error"] is not None or result_box["value"] is None:
            return

        try:
            payload = json.loads(str(result_box["value"]))
        except (TypeError, json.JSONDecodeError):
            return

        serialized = json.dumps(payload, sort_keys=True)
        if serialized == self._last_synced_state:
            return

        self._last_synced_state = serialized
        self._on_sync_form_state(payload)

    def _start_live_sync(self) -> None:
        """Continuously sync form state while the settings window is visible."""
        if self._sync_timer is not None:
            self._sync_timer.invalidate()

        def _tick(_timer):
            self._drain_js_queue()
            self._request_form_state_sync()

        self._sync_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            0.2, True, _tick
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._sync_timer, NSRunLoopCommonModes
        )

    def _stop_live_sync(self) -> None:
        """Stop syncing once the settings window is hidden."""
        if self._sync_timer is not None:
            self._sync_timer.invalidate()
            self._sync_timer = None

    def _on_window_close_requested(self) -> None:
        """Persist the latest form state and hide the settings window."""
        self._mic_test_controller.cleanup()
        self._request_form_state_sync()
        self._stop_live_sync()
        if self._window:
            self._window.orderOut_(None)
        self._set_accessory_policy()

    def _on_js_message(self, body: dict) -> None:
        """Handle messages from JavaScript (user interactions)."""
        message = self._bridge.parse(body)
        if message is None:
            return
        self._dispatcher.dispatch(message)

    def _eval_js(self, js: str) -> None:
        """Evaluate JavaScript in the WKWebView (thread-safe)."""
        if not self._webview:
            return
        if threading.current_thread() is threading.main_thread():
            self._webview.evaluateJavaScript_completionHandler_(js, None)
        else:
            self._js_queue.put(js)

    def _drain_js_queue(self) -> None:
        """Process pending JS calls on the main thread."""
        while not self._js_queue.empty():
            try:
                js = self._js_queue.get_nowait()
                if self._webview:
                    self._webview.evaluateJavaScript_completionHandler_(js, None)
            except queue.Empty:
                break

    def _inject_data_and_reload(self, data: dict) -> None:
        """Inject data as a WKUserScript and reload the page.

        This sets window._initData at document-start, BEFORE any page
        scripts run. The page's own JS then reads it and calls loadAll().
        No postMessage round-trip needed — completely synchronous from
        the JS perspective.
        """
        if not self._webview or not self._html_url:
            return

        json_str = json.dumps(data)

        # Remove all previous user scripts (from prior show() calls)
        self._content_controller.removeAllUserScripts()

        # Inject a script that runs at document-start to set window._initData
        init_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            f"window._initData = {json_str};",
            WKUserScriptInjectionTimeAtDocumentStart,
            True,
        )
        self._content_controller.addUserScript_(init_script)

        # Reload the page — the injected script runs before body scripts
        self._webview.loadFileURL_allowingReadAccessToURL_(
            self._html_url, self._html_url.URLByDeletingLastPathComponent()
        )
        print("[Settings] Data injected and page reloading")

    def show(
        self,
        config: dict,
        asr_models: list,
        llm_models: list,
        devices: list,
        dictionary: list,
        version: str = "",
    ) -> None:
        """Show the settings window and populate with data."""
        # Bring app to front
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self.set_interface_language(
            config.get("ui", {}).get("language", "en"),
            update_frontend=False,
        )

        config_with_version = dict(config)
        config_with_version["_version"] = version

        data = {
            "config": config_with_version,
            "asr_models": asr_models,
            "llm_models": llm_models,
            "devices": devices,
            "dictionary": dictionary,
            "recordings": self._recording_store.list_recordings() if self._recording_store else [],
        }

        # Inject data into page and reload
        self._last_synced_state = None
        self._inject_data_and_reload(data)
        self._start_live_sync()

        self._window.makeKeyAndOrderFront_(None)

    def hide(self) -> None:
        """Hide the settings window."""
        if self._window:
            self._request_form_state_sync()
            self._stop_live_sync()
            self._window.orderOut_(None)
            self._set_accessory_policy()

    def close(self) -> None:
        """Release background resources owned by the settings window."""
        self.hide()
        self._mic_test_controller.cleanup()
        self._background_tasks.close(wait=False, cancel_futures=True)

    def is_visible(self) -> bool:
        """Check if the settings window is visible."""
        return bool(self._window and self._window.isVisible())

    def update_devices(self, devices: list) -> None:
        """Update the device list in the UI (while window is open)."""
        json_str = json.dumps(devices)
        self._eval_js(f"loadDevices({json_str})")

    def update_dictionary(self, entries: list) -> None:
        """Update the dictionary in the UI (while window is open)."""
        json_str = json.dumps(entries)
        self._eval_js(f"loadDictionary({json_str})")

    def _build_action_dispatcher(self) -> SettingsActionDispatcher:
        return SettingsActionDispatcher(
            on_set_config=self._on_set_config,
            on_set_asr_model=self._on_set_asr_model,
            on_sync_form_state=self._on_sync_form_state,
            on_set_device=self._on_set_device,
            on_set_active_hotkeys=self._on_set_active_hotkeys,
            on_add_dict_entry=self._on_add_dict_entry,
            on_remove_dict_entry=self._on_remove_dict_entry,
            on_refresh_devices=self._on_refresh_devices,
            on_open_config_file=self._on_open_config_file,
            on_open_dict_file=self._on_open_dict_file,
            on_open_external=self._on_open_external,
            on_get_recordings=self._handle_get_recordings,
            on_retry_transcription=self._handle_retry_transcription,
            on_delete_recording=self._handle_delete_recording,
            on_play_recording=self._handle_play_recording,
            on_copy_transcript=self._handle_copy_transcript,
            mic_test_controller=self._mic_test_controller,
        )

    def _build_mic_test_controller(self) -> MicTestController:
        from ..config import get_config
        from ..core.audio_recorder import AudioRecorder

        return MicTestController(
            config_provider=get_config,
            recorder_factory=AudioRecorder,
            on_started=lambda: self._eval_js("micTestStarted()"),
            on_complete=lambda: self._eval_js("micTestComplete()"),
            on_error=lambda message: self._eval_js(
                f"micTestError({json.dumps(message)})"
            ),
            on_level=lambda rms: self._eval_js(f"micTestLevel({rms:.4f})"),
            on_playback=lambda b64: self._eval_js(
                f"micTestPlayback({json.dumps(b64)})"
            ),
            device_changed_error=lambda: t(
                self._interface_language,
                "settings_device_changed",
            ),
        )

    # ── Recording history handlers ───────────────────────────

    def _handle_get_recordings(self) -> None:
        if not self._recording_store:
            return
        recordings = self._recording_store.list_recordings()
        json_str = json.dumps(recordings, ensure_ascii=False)
        self._eval_js(f"loadRecordings({json_str})")

    def _handle_retry_transcription(self, rec_id: str) -> None:
        if not self._recording_store:
            return

        self._eval_js(f"retryStarted({json.dumps(rec_id)})")

        def _do_retry():
            from ..core.asr_engine import BatchASREngine
            from ..core.recording_store import RETRY_ASR_MODEL

            try:
                pcm_data = self._recording_store.get_pcm_data(rec_id)
                if pcm_data is None:
                    error_message = t(
                        self._interface_language,
                        "settings_recording_not_found",
                    )
                    self._recording_store.update(rec_id, "failed", error=error_message)
                    self._eval_js(
                        f"retryFailed({json.dumps(rec_id)}, {json.dumps(error_message)})"
                    )
                    return

                engine = BatchASREngine()
                language = self._recording_store.get_language(rec_id)
                transcript = engine.transcribe(
                    pcm_data, model_override=RETRY_ASR_MODEL, language_override=language
                )
                billing = merge_billing(engine.get_last_metering())

                if transcript and transcript.strip():
                    self._recording_store.update(
                        rec_id,
                        "success",
                        transcript,
                        error=None,
                        billing=billing,
                    )
                    self._handle_get_recordings()
                else:
                    error_message = t(
                        self._interface_language,
                        "settings_empty_transcription",
                    )
                    self._recording_store.update(
                        rec_id,
                        "failed",
                        error=error_message,
                        billing=billing,
                    )
                    self._handle_get_recordings()
            except Exception as e:
                billing = merge_billing(engine.get_last_metering()) if "engine" in locals() else None
                self._recording_store.update(
                    rec_id,
                    "failed",
                    error=str(e),
                    billing=billing,
                )
                self._handle_get_recordings()

        self._background_tasks.submit(_do_retry)

    def _handle_delete_recording(self, rec_id: str) -> None:
        if not self._recording_store:
            return
        self._recording_store.delete(rec_id)
        self._eval_js(f"recordingDeleted({json.dumps(rec_id)})")

    def _handle_play_recording(self, rec_id: str) -> None:
        if not self._recording_store:
            return
        wav_b64 = self._recording_store.get_wav_base64(rec_id)
        if wav_b64:
            self._eval_js(f"playAudio({json.dumps(rec_id)}, {json.dumps(wav_b64)})")

    def _handle_copy_transcript(self, rec_id: str) -> None:
        if not self._recording_store:
            return
        recordings = self._recording_store.list_recordings()
        for rec in recordings:
            if rec["id"] == rec_id and rec.get("transcript"):
                from AppKit import NSPasteboard, NSStringPboardType

                pb = NSPasteboard.generalPasteboard()
                pb.clearContents()
                pb.setString_forType_(rec["transcript"], NSStringPboardType)
                self._eval_js(f"copiedFeedback({json.dumps(rec_id)})")
                return
