"""Floating capsule UI using NSPanel + WKWebView."""

import json
import os
import threading
import time
from typing import Callable, Optional

import objc
from AppKit import (
    NSColor,
    NSEvent,
    NSPanel,
    NSPointInRect,
    NSScreen,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSURL
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

from ..config import get_config
from ..domain.prompt_coach import prompt_coach_hint
from ..localization import normalize_ui_language
from ..paths import bundled_resource_path
from .webview_bridge import objc_to_python

# NSWindow level constants
NSScreenSaverWindowLevel = 1000
# NSWindowCollectionBehavior flags
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorFullScreenAuxiliary = 1 << 8
NSWindowCollectionBehaviorStationary = 1 << 4

CAPSULE_AUDIO_PUSH_HZ = 12
CAPSULE_AUDIO_PUSH_INTERVAL_SECONDS = 1.0 / CAPSULE_AUDIO_PUSH_HZ
CAPSULE_LEVEL_CHANGE_THRESHOLD = 0.015
CAPSULE_LEVEL_HEARTBEAT_SECONDS = 0.25
CAPSULE_SILENCE_THRESHOLD = 0.005


class _MessageHandler(NSObject):
    """WKScriptMessageHandler to receive messages from JS."""

    def initWithCallbacks_(self, callbacks):
        self = objc.super(_MessageHandler, self).init()
        if self is None:
            return None
        self._callbacks = callbacks
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = objc_to_python(message.body())
        if isinstance(body, dict):
            action = body.get("action")
            if action == "cancel" and "cancel" in self._callbacks:
                self._callbacks["cancel"]()
            elif action == "finish" and "finish" in self._callbacks:
                self._callbacks["finish"]()


class FloatingCapsule:
    """Floating capsule overlay using NSPanel + WKWebView."""

    # Capsule dimensions
    CAPSULE_WIDTH = 200
    CAPSULE_HEIGHT = 80
    HINT_CAPSULE_WIDTH = 380
    HINT_CAPSULE_HEIGHT = 200

    def __init__(
        self,
        on_cancel: Optional[Callable[[], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ):
        self._on_cancel = on_cancel
        self._on_finish = on_finish
        self._panel: Optional[NSPanel] = None
        self._webview: Optional[WKWebView] = None
        self._current_mode: Optional[str] = None
        self._current_state: str = "hidden"

        # Thread-safe calibrated level: audio thread writes, main thread reads
        self._latest_audio_level: float = 0.0
        self._audio_level_lock = threading.Lock()
        self._push_timer: Optional[NSTimer] = None
        self._hide_timer: Optional[NSTimer] = None
        self._push_count: int = 0  # for throttled debug logging
        self._last_pushed_audio_level: Optional[float] = None
        self._last_audio_level_push_at: float = 0.0
        self._html_loaded: bool = False
        self._interface_language: str = "en"
        self._latest_prompt_text: str = ""
        self._main_thread_timers: set[NSTimer] = set()

    def warm_up(self) -> None:
        """Create the WebView after the menu bar item is already visible."""
        self._run_on_main_thread(self._ensure_setup)

    def _ensure_setup(self) -> None:
        """Create the capsule UI once, on demand."""
        if self._panel is not None:
            return
        self._setup()

    def _setup(self) -> None:
        """Create NSPanel and WKWebView."""
        screen = NSScreen.mainScreen()
        screen_frame = screen.frame()

        panel_x = (screen_frame.size.width - self.CAPSULE_WIDTH) / 2
        panel_y = 20
        panel_frame = ((panel_x, panel_y), (self.CAPSULE_WIDTH, self.CAPSULE_HEIGHT))

        style_mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            panel_frame, style_mask, 2, False
        )
        self._panel.setLevel_(NSScreenSaverWindowLevel)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setOpaque_(False)
        self._panel.setHasShadow_(False)
        self._panel.setIgnoresMouseEvents_(True)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self._panel.setHidesOnDeactivate_(False)

        config = WKWebViewConfiguration.alloc().init()
        content_controller = WKUserContentController.alloc().init()

        callbacks = {}
        if self._on_cancel:
            callbacks["cancel"] = self._on_cancel
        if self._on_finish:
            callbacks["finish"] = self._on_finish

        self._message_handler = _MessageHandler.alloc().initWithCallbacks_(callbacks)
        content_controller.addScriptMessageHandler_name_(self._message_handler, "capsule")
        config.setUserContentController_(content_controller)

        webview_frame = ((0, 0), (self.CAPSULE_WIDTH, self.CAPSULE_HEIGHT))
        self._webview = WKWebView.alloc().initWithFrame_configuration_(
            webview_frame, config
        )
        self._webview.setValue_forKey_(False, "drawsBackground")

        html_path = self._get_html_path()
        if html_path and os.path.exists(html_path):
            print(f"[Capsule] Loading HTML from: {html_path}")
            url = NSURL.fileURLWithPath_(html_path)
            self._webview.loadFileURL_allowingReadAccessToURL_(
                url, url.URLByDeletingLastPathComponent()
            )
            self._html_loaded = True
        else:
            print(f"[Capsule] WARNING: HTML not found at: {html_path}")

        self._panel.setContentView_(self._webview)

    def _get_html_path(self) -> Optional[str]:
        """Get path to capsule.html."""
        html_path = bundled_resource_path("resources", "floating_capsule", "capsule.html")
        if html_path.exists():
            return str(html_path)
        return None

    def _prompt_mode_enabled(self) -> bool:
        config = get_config()
        return bool(
            config.enable_polish
            and config.llm.polish_mode == "prompt"
        )

    def _display_mode(self, mode: str) -> str:
        if not self._prompt_mode_enabled():
            return mode
        if mode == "pushToTalk":
            return "promptPushToTalk"
        if mode == "handsFree":
            return "prompt"
        return mode

    @staticmethod
    def _escape_js_string(value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    def _update_prompt_hint_on_main_thread(self) -> None:
        hint = prompt_coach_hint(
            self._latest_prompt_text,
            self._interface_language,
        )
        self._set_capsule_size_on_main_thread(bool(hint.strip()))
        payload = json.dumps(hint, ensure_ascii=False)
        self._eval_js(f"updatePromptHint({payload})")

    def show(self, mode: str = "pushToTalk") -> None:
        """Show the capsule."""
        self._run_on_main_thread(lambda: self._show_on_main_thread(mode))

    def _show_on_main_thread(self, mode: str) -> None:
        self._ensure_setup()
        self._set_capsule_size_on_main_thread(False)
        display_mode = self._display_mode(mode)
        self._current_mode = display_mode
        self._current_state = "recording"
        self._latest_prompt_text = ""
        self._panel.setIgnoresMouseEvents_(
            display_mode in {"pushToTalk", "promptPushToTalk", "meeting"}
        )

        # Cancel any pending hide timer from a previous hide() call
        if self._hide_timer:
            self._hide_timer.invalidate()
            self._hide_timer = None

        # Position on the screen containing the mouse cursor
        mouse_loc = NSEvent.mouseLocation()
        active_screen = None
        for screen in NSScreen.screens():
            if NSPointInRect(mouse_loc, screen.frame()):
                active_screen = screen
                break
        if not active_screen:
            active_screen = NSScreen.mainScreen()

        if active_screen:
            sf = active_screen.frame()
            panel_x = sf.origin.x + (sf.size.width - self.CAPSULE_WIDTH) / 2
            panel_y = sf.origin.y + 20
            self._panel.setFrameOrigin_((panel_x, panel_y))

        # Single JS call instead of separate IPC roundtrips.
        javascript = (
            f"setInterfaceLanguage('{self._interface_language}'); "
            f"setMode('{display_mode}'); updateState('recording')"
        )
        if display_mode in {"prompt", "promptPushToTalk"}:
            hint = self._escape_js_string(
                prompt_coach_hint("", self._interface_language)
            )
            javascript += f"; updatePromptHint('{hint}')"
        self._eval_js(javascript)
        self._panel.orderFront_(None)
        self._last_pushed_audio_level = None
        self._last_audio_level_push_at = 0.0
        self._start_push_timer()
        print(
            f"[Capsule] show(mode={display_mode}), "
            f"html_loaded={self._html_loaded}"
        )

    def set_interface_language(self, language: str) -> None:
        """Update the capsule language for the next visible state."""
        normalized = normalize_ui_language(language)
        self._run_on_main_thread(
            lambda: self._set_interface_language_on_main_thread(normalized)
        )

    def _set_interface_language_on_main_thread(self, language: str) -> None:
        self._interface_language = language
        if self._panel and self._panel.isVisible():
            self._eval_js(f"setInterfaceLanguage('{self._interface_language}')")
            if (
                self._current_state == "recording"
                and self._current_mode in {"prompt", "promptPushToTalk"}
            ):
                self._update_prompt_hint_on_main_thread()

    def hide(self) -> None:
        """Hide the capsule."""
        self._run_on_main_thread(self._hide_on_main_thread)

    def _hide_on_main_thread(self) -> None:
        self._stop_push_timer()
        self._set_capsule_size_on_main_thread(False)
        self._current_state = "hidden"
        self._latest_prompt_text = ""
        self._eval_js("updateState('hidden')")

        # Cancel any previously scheduled hide timer
        if self._hide_timer:
            self._hide_timer.invalidate()
            self._hide_timer = None

        def _do_hide():
            if self._panel:
                self._panel.orderOut_(None)
            self._hide_timer = None

        self._hide_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            0.25, False, lambda _: _do_hide()
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(
            self._hide_timer, NSRunLoopCommonModes
        )

    def update_state(self, state: str) -> None:
        """Update capsule state: 'recording', 'processing', or 'hidden'."""
        self._run_on_main_thread(lambda: self._update_state_on_main_thread(state))

    def _update_state_on_main_thread(self, state: str) -> None:
        if state == "hidden":
            self._hide_on_main_thread()
            return

        # ModeState.STOPPING and ModeState.PROCESSING intentionally map to the
        # same visible state. Re-sending ``processing`` would restart the JS
        # asymptotic timer and make a growing progress bar jump backwards.
        if state == self._current_state:
            return

        self._ensure_setup()
        self._current_state = state
        self._eval_js(f"updateState('{state}')")

        if state == "processing":
            self._stop_push_timer()
            self._set_capsule_size_on_main_thread(False)
            if self._panel is not None:
                self._panel.setIgnoresMouseEvents_(True)

    def update_audio_level(self, level: float) -> None:
        """Store the latest calibrated 0–1 level from the audio thread."""
        with self._audio_level_lock:
            self._latest_audio_level = max(0.0, min(1.0, float(level)))

    def set_processing_stage(self, stage: str) -> None:
        """Update the capsule's processing phase label."""
        escaped = stage.replace("\\", "\\\\").replace("'", "\\'")
        self._run_on_main_thread(lambda: self._eval_js(f"setProcessingStage('{escaped}')"))

    def update_streaming_text(self, text: str) -> None:
        """Show model output or update the local Prompt coach."""
        value = str(text or "")
        payload = json.dumps(value, ensure_ascii=False)
        self._run_on_main_thread(
            lambda: self._update_streaming_text_on_main_thread(value, payload)
        )

    def _update_streaming_text_on_main_thread(self, text: str, payload: str) -> None:
        if (
            self._current_state == "recording"
            and self._current_mode in {"prompt", "promptPushToTalk"}
        ):
            self._latest_prompt_text = str(text or "")
            self._update_prompt_hint_on_main_thread()
            return
        self._eval_js(f"updateStreamingText({payload})")

    def _set_capsule_size_on_main_thread(self, expanded: bool) -> None:
        """Resize around the current horizontal center without moving screens."""
        panel = getattr(self, "_panel", None)
        webview = getattr(self, "_webview", None)
        if panel is None or webview is None:
            return
        width = self.HINT_CAPSULE_WIDTH if expanded else self.CAPSULE_WIDTH
        height = self.HINT_CAPSULE_HEIGHT if expanded else self.CAPSULE_HEIGHT
        frame = panel.frame()
        center_x = frame.origin.x + frame.size.width / 2
        origin_y = frame.origin.y
        panel.setFrame_display_(
            ((center_x - width / 2, origin_y), (width, height)),
            True,
        )
        webview.setFrame_(((0, 0), (width, height)))

    def _start_push_timer(self) -> None:
        """Push the latest coalesced audio envelope to JavaScript at 12Hz."""
        self._stop_push_timer()
        # Create timer and add to MAIN run loop (not current thread's run loop)
        # so it fires correctly regardless of which thread calls show().
        self._push_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            CAPSULE_AUDIO_PUSH_INTERVAL_SECONDS,
            True,
            lambda _: self._push_audio_level(),
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(
            self._push_timer, NSRunLoopCommonModes
        )

    def _stop_push_timer(self) -> None:
        """Stop the calibrated audio-level push timer."""
        if self._push_timer:
            self._push_timer.invalidate()
            self._push_timer = None

    def _push_audio_level(self) -> None:
        """Read the calibrated level and push it to JS on the main thread."""
        with self._audio_level_lock:
            level = self._latest_audio_level
        level = 0.0 if level <= CAPSULE_SILENCE_THRESHOLD else level
        now = time.monotonic()
        previous = self._last_pushed_audio_level
        is_silence_tail = level == 0.0 and previous not in (None, 0.0)
        heartbeat_due = (
            previous is None
            or now - self._last_audio_level_push_at >= CAPSULE_LEVEL_HEARTBEAT_SECONDS
        )
        changed_enough = (
            previous is None
            or abs(level - previous) >= CAPSULE_LEVEL_CHANGE_THRESHOLD
        )
        if not (is_silence_tail or heartbeat_due or changed_enough):
            return

        self._eval_js(f"updateAudioLevel({level})")
        self._last_pushed_audio_level = level
        self._last_audio_level_push_at = now
        self._push_count += 1
        if self._push_count % CAPSULE_AUDIO_PUSH_HZ == 1:
            print(f"[Capsule] waveform_level={level:.4f}")

    def _eval_js(self, js: str) -> None:
        """Evaluate JavaScript in the WKWebView. Must be called from main thread."""
        if self._webview:
            self._webview.evaluateJavaScript_completionHandler_(js, None)

    def _run_on_main_thread(self, callback: Callable[[], None]) -> None:
        """Marshal UI work onto the main run loop to avoid AppKit/WebKit crashes."""
        if threading.current_thread() is threading.main_thread():
            callback()
            return

        timer_ref: dict[str, Optional[NSTimer]] = {"timer": None}

        def _fire(_timer) -> None:
            timer = timer_ref["timer"]
            if timer is not None:
                self._main_thread_timers.discard(timer)
            callback()

        timer = NSTimer.timerWithTimeInterval_repeats_block_(0, False, _fire)
        timer_ref["timer"] = timer
        self._main_thread_timers.add(timer)
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
