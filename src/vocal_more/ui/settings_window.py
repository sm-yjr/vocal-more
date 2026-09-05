"""Settings window using NSWindow + WKWebView."""

import json
import os
import queue
import sys
import threading
import traceback
from typing import Any, Callable, Optional

import objc
from AppKit import (
    NSApp,
    NSApplicationActivationPolicyAccessory,
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
from ..application.recording_retry import RecordingRetryEvent
from ..localization import normalize_ui_language, t
from ..paths import bundled_resource_path
from .mic_test_controller import MicTestController
from .settings_actions import SettingsActionDispatcher
from .settings_bridge import SettingsBridge
from .webview_bridge import objc_to_python

_UNSET = object()


def _activate_menu_bar_app() -> None:
    """Bring the app forward without adding it to the Dock."""
    NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    NSApp.activateIgnoringOtherApps_(True)


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
        on_preview_config: Optional[Callable[[str, Any], None]] = None,
        on_set_asr_model: Optional[Callable[[str, str], None]] = None,
        on_sync_form_state: Optional[Callable[[dict], None]] = None,
        on_set_device: Optional[Callable[[Optional[str]], None]] = None,
        on_set_active_hotkeys: Optional[Callable[[list[str]], None]] = None,
        on_add_dict_entry: Optional[Callable[[str, list[str]], None]] = None,
        on_remove_dict_entry: Optional[Callable[[str], None]] = None,
        on_approve_dictionary_learning: Optional[Callable[[str], None]] = None,
        on_reject_dictionary_learning: Optional[Callable[[str], None]] = None,
        on_undo_dictionary_learning: Optional[Callable[[str], None]] = None,
        on_refresh_devices: Optional[Callable[[], None]] = None,
        on_refresh_environment: Optional[Callable[[], None]] = None,
        on_open_accessibility_settings: Optional[Callable[[], None]] = None,
        on_open_config_file: Optional[Callable[[], None]] = None,
        on_open_dict_file: Optional[Callable[[], None]] = None,
        on_open_external: Optional[Callable[[str], None]] = None,
        recording_store: Optional[object] = None,
        recording_retry: Optional[object] = None,
        context_personalization: Optional[object] = None,
    ):
        self._closed = False
        self._recording_player = None
        self._on_set_config = on_set_config
        self._on_preview_config = on_preview_config
        self._on_set_asr_model = on_set_asr_model
        self._on_sync_form_state = on_sync_form_state
        self._on_set_device = on_set_device
        self._on_set_active_hotkeys = on_set_active_hotkeys
        self._on_add_dict_entry = on_add_dict_entry
        self._on_remove_dict_entry = on_remove_dict_entry
        self._on_approve_dictionary_learning = on_approve_dictionary_learning
        self._on_reject_dictionary_learning = on_reject_dictionary_learning
        self._on_undo_dictionary_learning = on_undo_dictionary_learning
        self._on_refresh_devices = on_refresh_devices
        self._on_refresh_environment = on_refresh_environment
        self._on_open_accessibility_settings = on_open_accessibility_settings
        self._on_open_config_file = on_open_config_file
        self._on_open_dict_file = on_open_dict_file
        self._on_open_external = on_open_external
        self._recording_store = recording_store
        self._recording_retry = recording_retry
        self._context_personalization = context_personalization

        self._window: Optional[NSWindow] = None
        self._webview: Optional[WKWebView] = None
        self._content_controller: Optional[WKUserContentController] = None
        self._message_handler: Optional[_SettingsMessageHandler] = None
        self._window_delegate: Optional[_WindowDelegate] = None
        self._html_url: Optional[NSURL] = None
        self._last_synced_state: Optional[str] = None
        self._js_queue: queue.Queue = queue.Queue()
        self._js_drain_timer: Optional[NSTimer] = None
        self._js_drain_lock = threading.Lock()
        self._dashscope_check_lock = threading.Lock()
        self._dashscope_check_running = False
        self._interface_language = "en"
        self._model_check_tasks = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-model-check",
        )
        self._recording_maintenance_tasks = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-recording-maintenance",
        )
        self._meeting_tasks = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-meeting-notes",
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
        html_path = bundled_resource_path("resources", "settings", "settings.html")
        if html_path.exists():
            return str(html_path)
        return None

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

    def _on_window_close_requested(self) -> None:
        """Persist form state and release the heavyweight WebKit surface."""
        self._mic_test_controller.cleanup()
        self._request_form_state_sync()
        self._teardown_surface()

    def _teardown_surface(self) -> None:
        """Drop the hidden window/WebView so WebKit can reclaim its processes."""
        webview = self._webview
        with self._js_drain_lock:
            self._webview = None
        self._stop_js_drain()
        self._handle_stop_recording()
        controller = getattr(self, "_content_controller", None)
        if controller is not None:
            try:
                controller.removeScriptMessageHandlerForName_("settings")
            except objc.error as exc:
                print(f"[Settings] Message handler was already detached: {exc}")
            controller.removeAllUserScripts()

        if webview is not None:
            webview.stopLoading()
            webview.removeFromSuperview()

        window = getattr(self, "_window", None)
        if window is not None:
            # Detach the delegate first: windowShouldClose_ intentionally
            # returns False for user clicks, but teardown owns final closure.
            window.setDelegate_(None)
            window.setContentView_(None)
            window.orderOut_(None)
            window.close()

        self._window = None
        self._webview = None
        self._content_controller = None
        self._message_handler = None
        self._window_delegate = None

    def _on_js_message(self, body: dict) -> None:
        """Handle messages from JavaScript (user interactions)."""
        message = self._bridge.parse(body)
        if message is None:
            return
        if message.get("action") == "sync_form_state":
            payload = message.get("payload")
            if isinstance(payload, dict):
                self._last_synced_state = json.dumps(payload, sort_keys=True)
        self._dispatcher.dispatch(message)

    def _eval_js(self, js: str) -> None:
        """Evaluate JavaScript in the WKWebView (thread-safe)."""
        if not self._webview:
            return
        if threading.current_thread() is threading.main_thread():
            self._webview.evaluateJavaScript_completionHandler_(js, None)
        else:
            with self._js_drain_lock:
                if self._webview is None:
                    return
                self._js_queue.put(js)
            self._schedule_js_drain()

    def _schedule_js_drain(self) -> None:
        """Schedule one main-run-loop drain when background work enqueues JS."""
        with self._js_drain_lock:
            if self._js_drain_timer is not None:
                return

            timer_ref: dict[str, Optional[NSTimer]] = {"timer": None}

            def _fire(_timer) -> None:
                timer = timer_ref["timer"]
                with self._js_drain_lock:
                    if self._js_drain_timer is timer:
                        self._js_drain_timer = None
                self._drain_js_queue()
                if not self._js_queue.empty():
                    self._schedule_js_drain()

            timer = NSTimer.timerWithTimeInterval_repeats_block_(0, False, _fire)
            timer_ref["timer"] = timer
            self._js_drain_timer = timer

        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)

    def _stop_js_drain(self) -> None:
        """Cancel a pending one-shot JavaScript queue drain."""
        with self._js_drain_lock:
            timer = self._js_drain_timer
            self._js_drain_timer = None
            while True:
                try:
                    self._js_queue.get_nowait()
                except queue.Empty:
                    break
        if timer is not None:
            timer.invalidate()

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
        polish_prompt_presets: dict | None = None,
        version: str = "",
        initial_tab: str = "",
        focus_recording_id: str = "",
        dictionary_learning_records: list | None = None,
        environment_checks: list | None = None,
        audio_input_status: dict | None = None,
    ) -> None:
        """Show the settings window and populate with data."""
        if self._window is None or self._webview is None:
            self._setup()
        # Bring app to front
        _activate_menu_bar_app()
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
            "audio_input_status": (
                dict(audio_input_status)
                if isinstance(audio_input_status, dict)
                else self._current_audio_input_status()
            ),
            "dictionary": dictionary,
            "dictionary_learning_records": dictionary_learning_records or [],
            "context_profile": (
                self._context_personalization.summary()
                if self._context_personalization is not None
                else {"counts": {}, "total": 0}
            ),
            "environment_checks": environment_checks or [],
            "polish_prompt_presets": polish_prompt_presets or {},
            "recordings": self._recording_store.list_recordings() if self._recording_store else [],
            "recording_storage": self._recording_storage_summary(),
            "initial_tab": initial_tab,
            "focus_recording_id": focus_recording_id,
            "focusRecordingId": focus_recording_id,
        }

        # Inject data into page and reload
        self._last_synced_state = None
        self._inject_data_and_reload(data)

        self._window.makeKeyAndOrderFront_(None)

    def hide(self) -> None:
        """Hide the settings window."""
        if self._window:
            self._request_form_state_sync()
            self._window.orderOut_(None)

    def close(self) -> None:
        """Release background resources owned by the settings window."""
        self._closed = True
        if getattr(self, "_window", None) is not None:
            self._request_form_state_sync()
        self._teardown_surface()
        self._mic_test_controller.cleanup()
        for executor in (
            self._model_check_tasks,
            self._recording_maintenance_tasks,
            self._meeting_tasks,
        ):
            executor.close(wait=False, cancel_futures=True)

    def is_visible(self) -> bool:
        """Check if the settings window is visible."""
        return bool(self._window and self._window.isVisible())

    def update_devices(self, devices: list, selected_device: object = _UNSET) -> None:
        """Update the device list in the UI (while window is open)."""
        json_str = json.dumps(devices)
        if selected_device is _UNSET:
            self._eval_js(f"loadDevices({json_str})")
            self.update_audio_input_status(self._current_audio_input_status())
            return

        selected_json = json.dumps(selected_device)
        self._eval_js(f"loadDevices({json_str}, {selected_json})")
        self.update_audio_input_status(self._current_audio_input_status())

    def update_audio_input_status(self, status: dict) -> None:
        """Refresh the actual/planned microphone processing path."""
        self._eval_js(f"loadAudioInputStatus({json.dumps(status)})")

    @staticmethod
    def _current_audio_input_status() -> dict:
        from ..core.audio_recorder import AudioRecorder

        try:
            return AudioRecorder.inspect_input_status()
        except Exception as exc:
            return {
                "device_name": "",
                "system_default": True,
                "max_input_channels": 1,
                "capture_channels": 1,
                "processing_mode": "standard",
                "processing_active": False,
                "array_processing_active": False,
                "echo_cancellation": "unavailable",
                "gain_control": "software_fallback",
                "fallback_reason": str(exc),
            }

    def update_environment_checks(self, checks: list) -> None:
        """Refresh prerequisite status shown by the guided setup."""
        self._eval_js(f"loadEnvironmentChecks({json.dumps(checks)})")

    def update_dictionary(self, entries: list) -> None:
        """Update the dictionary in the UI (while window is open)."""
        json_str = json.dumps(entries)
        self._eval_js(f"loadDictionary({json_str})")

    def update_dictionary_learning(self, records: list) -> None:
        """Update automatic-learning review and undo records."""
        json_str = json.dumps(records)
        self._eval_js(f"loadDictionaryLearning({json_str})")

    def _build_action_dispatcher(self) -> SettingsActionDispatcher:
        return SettingsActionDispatcher(
            on_set_config=self._on_set_config,
            on_preview_config=self._on_preview_config,
            on_set_asr_model=self._on_set_asr_model,
            on_sync_form_state=self._on_sync_form_state,
            on_set_device=self._on_set_device,
            on_set_active_hotkeys=self._on_set_active_hotkeys,
            on_add_dict_entry=self._on_add_dict_entry,
            on_remove_dict_entry=self._on_remove_dict_entry,
            on_approve_dictionary_learning=self._on_approve_dictionary_learning,
            on_reject_dictionary_learning=self._on_reject_dictionary_learning,
            on_undo_dictionary_learning=self._on_undo_dictionary_learning,
            on_refresh_devices=self._on_refresh_devices,
            on_refresh_environment=self._on_refresh_environment,
            on_check_dashscope_models=self._handle_check_dashscope_models,
            on_open_accessibility_settings=self._on_open_accessibility_settings,
            on_open_config_file=self._on_open_config_file,
            on_open_dict_file=self._on_open_dict_file,
            on_open_external=self._on_open_external,
            on_get_recordings=self._handle_get_recordings,
            on_retry_transcription=self._handle_retry_transcription,
            on_generate_meeting_notes=self._handle_generate_meeting_notes,
            on_delete_recording=self._handle_delete_recording,
            on_play_recording=self._handle_play_recording,
            on_stop_recording=self._handle_stop_recording,
            on_copy_transcript=self._handle_copy_transcript,
            on_reset_context_profile=self._handle_reset_context_profile,
            on_compact_recording_history=self._handle_compact_recording_history,
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
            on_input_status=self.update_audio_input_status,
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

    def _recording_storage_summary(self) -> dict:
        summary = getattr(self._recording_store, "storage_summary", None)
        if not callable(summary):
            return {}
        try:
            result = summary()
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    def _handle_reset_context_profile(self) -> None:
        if self._context_personalization is None:
            return
        self._context_personalization.reset()
        summary = self._context_personalization.summary()
        self._eval_js(f"loadContextProfile({json.dumps(summary)})")

    def _handle_check_dashscope_models(self) -> None:
        """Check Pro and Lite model access without blocking the WebView."""
        with self._dashscope_check_lock:
            if self._dashscope_check_running:
                return
            self._dashscope_check_running = True

        from ..config import get_config

        api_key = str(get_config().api_key or "")
        self._eval_js("dashscopeModelCheckStarted()")

        def _check() -> None:
            try:
                from ..application.dashscope_model_check import (
                    check_dashscope_model_families,
                )

                results = check_dashscope_model_families(api_key)
            except Exception as exc:
                results = [
                    {
                        "family": family,
                        "model": model,
                        "status": "error",
                        "latency_ms": 0,
                        "error": str(exc)[:300],
                    }
                    for family, model in (
                        ("pro", "qwen3.5-omni-plus"),
                        ("lite", "qwen3.5-omni-flash"),
                    )
                ]
            finally:
                with self._dashscope_check_lock:
                    self._dashscope_check_running = False
            self._eval_js(
                f"dashscopeModelCheckComplete({json.dumps(results)})"
            )

        self._model_check_tasks.submit(_check)

    def _handle_compact_recording_history(self) -> None:
        if self._recording_store is None:
            return
        compact = getattr(self._recording_store, "compact_history", None)
        if not callable(compact):
            return
        self._eval_js("recordingCompactionStarted()")

        def _compact():
            try:
                result = compact(keep_recent=3)
                summary = self._recording_store.storage_summary()
                self._eval_js(
                    f"recordingCompactionComplete({json.dumps(summary)}, "
                    f"{json.dumps(result)})"
                )
                self._handle_get_recordings()
            except Exception as exc:
                self._eval_js(
                    f"recordingCompactionFailed({json.dumps(str(exc))})"
                )

        self._recording_maintenance_tasks.submit(_compact)

    def _handle_retry_transcription(self, rec_id: str) -> None:
        if self._recording_retry is None:
            return
        submission = self._recording_retry.submit(
            rec_id,
            self._on_recording_retry_event,
        )
        if submission.status in {"busy", "closed"}:
            self._eval_js(
                f"retryFailed({json.dumps(rec_id)}, "
                f"{json.dumps('Retry service is busy')})"
            )

    def _on_recording_retry_event(self, event: RecordingRetryEvent) -> None:
        if getattr(self, "_closed", False):
            return
        if event.kind == "started":
            self._eval_js(f"retryStarted({json.dumps(event.recording_id)})")
            return
        if event.kind == "completed":
            self._eval_js(
                f"retryCompleted({json.dumps(event.recording_id)}, "
                f"{json.dumps(event.transcript or '')})"
            )
        elif event.kind == "failed":
            self._eval_js(
                f"retryFailed({json.dumps(event.recording_id)}, "
                f"{json.dumps(event.error or '')})"
            )
        self._handle_get_recordings()

    def _handle_generate_meeting_notes(self, rec_id: str) -> None:
        if not self._recording_store:
            return

        self._eval_js(f"meetingNotesStarted({json.dumps(rec_id)})")

        def _do_generate():
            from ..application.meeting_jobs import MeetingNotesRecordingRunner
            from ..config import get_config

            MeetingNotesRecordingRunner(
                config=get_config(),
                recording_store=self._recording_store,
            ).generate_for_recording(
                rec_id,
                on_stage=lambda stage: self._eval_js(
                    f"meetingNotesStage({json.dumps(rec_id)}, {json.dumps(stage)})"
                ),
            )
            self._handle_get_recordings()

        self._meeting_tasks.submit(_do_generate)

    def _handle_delete_recording(self, rec_id: str) -> None:
        if not self._recording_store:
            return
        self._handle_stop_recording(rec_id)
        self._recording_store.delete(rec_id)
        self._eval_js(f"recordingDeleted({json.dumps(rec_id)})")

    def _handle_play_recording(self, rec_id: str) -> None:
        if not self._recording_store:
            return
        path = self._recording_store.get_recording_path(rec_id)
        if path is None:
            return
        if self._recording_player is None:
            from .recording_player import RecordingPlayer

            self._recording_player = RecordingPlayer(
                on_stopped=lambda recording_id: self._eval_js(
                    f"recordingPlaybackEnded({json.dumps(recording_id)})"
                )
            )
        if self._recording_player.play(rec_id, path):
            # null selects native playback; the bridge carries only the ID.
            self._eval_js(f"playAudio({json.dumps(rec_id)}, null)")

    def _handle_stop_recording(self, rec_id: str | None = None) -> None:
        player = getattr(self, "_recording_player", None)
        if player is not None:
            player.stop(rec_id)

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
