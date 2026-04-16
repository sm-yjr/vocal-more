"""Floating capsule UI using NSPanel + WKWebView."""

import os
import threading
from pathlib import Path
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

from ..localization import normalize_ui_language
from .webview_bridge import objc_to_python

# NSWindow level constants
NSScreenSaverWindowLevel = 1000
# NSWindowCollectionBehavior flags
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorFullScreenAuxiliary = 1 << 8
NSWindowCollectionBehaviorStationary = 1 << 4

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

        # Thread-safe audio level: audio thread writes, main thread reads
        self._latest_rms: float = 0.0
        self._rms_lock = threading.Lock()
        self._push_timer: Optional[NSTimer] = None
        self._hide_timer: Optional[NSTimer] = None
        self._push_count: int = 0  # for throttled debug logging
        self._html_loaded: bool = False
        self._interface_language: str = "en"

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
        package_dir = Path(__file__).parent.parent.parent.parent
        html_path = package_dir / "resources" / "floating_capsule" / "capsule.html"
        if html_path.exists():
            return str(html_path)
        return None

    def show(self, mode: str = "pushToTalk") -> None:
        """Show the capsule."""
        self._current_mode = mode
        self._panel.setIgnoresMouseEvents_(mode == "pushToTalk")

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

        # Single JS call instead of two separate IPC roundtrips
        self._eval_js(
            f"setInterfaceLanguage('{self._interface_language}'); "
            f"setMode('{mode}'); updateState('recording')"
        )
        self._panel.orderFront_(None)
        self._start_push_timer()
        print(f"[Capsule] show(mode={mode}), html_loaded={self._html_loaded}")

    def set_interface_language(self, language: str) -> None:
        """Update the capsule language for the next visible state."""
        self._interface_language = normalize_ui_language(language)
        if self._panel and self._panel.isVisible():
            self._eval_js(f"setInterfaceLanguage('{self._interface_language}')")

    def hide(self) -> None:
        """Hide the capsule."""
        self._stop_push_timer()
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
        if state == "hidden":
            self.hide()
            return

        self._eval_js(f"updateState('{state}')")

        if state == "processing":
            self._stop_push_timer()
            self._panel.setIgnoresMouseEvents_(True)

    def update_audio_level(self, rms: float) -> None:
        """Store latest RMS. Safe to call from any thread (audio thread)."""
        with self._rms_lock:
            self._latest_rms = rms

    def set_processing_stage(self, stage: str) -> None:
        """Update the capsule's processing phase label."""
        escaped = stage.replace("\\", "\\\\").replace("'", "\\'")
        js = f"setProcessingStage('{escaped}')"
        timer = NSTimer.timerWithTimeInterval_repeats_block_(
            0, False, lambda _: self._eval_js(js)
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)

    def update_streaming_text(self, text: str) -> None:
        """Show streaming text below waveform/thinking. Safe to call from any thread."""
        escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        js = f"updateStreamingText('{escaped}')"
        timer = NSTimer.timerWithTimeInterval_repeats_block_(
            0, False, lambda _: self._eval_js(js)
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)

    def _start_push_timer(self) -> None:
        """Start a main-thread timer to push RMS to JS at ~30fps."""
        self._stop_push_timer()
        # Create timer and add to MAIN run loop (not current thread's run loop)
        # so it fires correctly regardless of which thread calls show().
        self._push_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            0.033, True, lambda _: self._push_rms()
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(
            self._push_timer, NSRunLoopCommonModes
        )

    def _stop_push_timer(self) -> None:
        """Stop the RMS push timer."""
        if self._push_timer:
            self._push_timer.invalidate()
            self._push_timer = None

    def _push_rms(self) -> None:
        """Read latest RMS and push to JS. Runs on main thread."""
        with self._rms_lock:
            rms = self._latest_rms
        self._eval_js(f"updateAudioLevel({rms})")
        # Throttled debug logging: print every 30th push (~1/sec at 30fps)
        self._push_count += 1
        if self._push_count % 30 == 1:
            print(f"[Capsule] push_rms={rms:.4f}")

    def _eval_js(self, js: str) -> None:
        """Evaluate JavaScript in the WKWebView. Must be called from main thread."""
        if self._webview:
            self._webview.evaluateJavaScript_completionHandler_(js, None)
