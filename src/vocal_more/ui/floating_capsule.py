"""Floating capsule UI using NSPanel with a native AppKit content view."""

from __future__ import annotations

import threading
from collections.abc import Callable

from AppKit import (
    NSColor,
    NSEvent,
    NSPanel,
    NSPointInRect,
    NSScreen,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

from ..config import get_config
from ..domain.prompt_coach import prompt_coach_hint
from ..localization import normalize_ui_language

# NSWindow level constants
NSScreenSaverWindowLevel = 1000
# NSWindowCollectionBehavior flags
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorFullScreenAuxiliary = 1 << 8
NSWindowCollectionBehaviorStationary = 1 << 4

CAPSULE_AUDIO_PUSH_HZ = 60
CAPSULE_AUDIO_PUSH_INTERVAL_SECONDS = 1.0 / CAPSULE_AUDIO_PUSH_HZ
CAPSULE_PROGRESS_HZ = 60
CAPSULE_PROGRESS_INTERVAL_SECONDS = 1.0 / CAPSULE_PROGRESS_HZ
CAPSULE_SILENCE_THRESHOLD = 0.005


class FloatingCapsule:
    """Floating capsule overlay using one NSPanel and a native renderer."""

    # Capsule dimensions
    CAPSULE_WIDTH = 200
    CAPSULE_HEIGHT = 80
    HINT_CAPSULE_WIDTH = 380
    HINT_CAPSULE_HEIGHT = 200

    def __init__(
        self,
        on_cancel: Callable[[], None] | None = None,
        on_finish: Callable[[], None] | None = None,
    ):
        self._on_cancel = on_cancel
        self._on_finish = on_finish
        self._panel: NSPanel | None = None
        self._renderer: object | None = None
        self._current_mode: str | None = None
        self._current_state: str = "hidden"

        # Thread-safe calibrated level: audio thread writes, main thread reads
        self._latest_audio_level: float = 0.0
        self._audio_level_lock = threading.Lock()
        self._push_timer: NSTimer | None = None
        self._progress_timer: NSTimer | None = None
        self._hide_timer: NSTimer | None = None
        self._push_count: int = 0  # for throttled debug logging
        self._interface_language: str = "en"
        self._latest_prompt_text: str = ""
        self._main_thread_timers: set[NSTimer] = set()

    def warm_up(self) -> None:
        """Create the native view after the menu bar item is already visible."""
        self._run_on_main_thread(self._ensure_setup)

    def _ensure_setup(self) -> None:
        """Create the capsule UI once, on demand."""
        if self._panel is not None:
            return
        self._setup()

    def _setup(self) -> None:
        """Create NSPanel and its native content view."""
        from .native_capsule_view import NativeCapsuleRenderer

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

        self._renderer = NativeCapsuleRenderer(
            width=self.CAPSULE_WIDTH,
            height=self.CAPSULE_HEIGHT,
            on_cancel=self._on_cancel,
            on_finish=self._on_finish,
        )
        self._renderer.set_interface_language(self._interface_language)
        self._panel.setContentView_(self._renderer.content_view)

    def _prompt_mode_enabled(self) -> bool:
        config = get_config()
        return bool(
            config.enable_polish
            and config.llm.polish_mode == "prompt"
        )

    def _display_mode(
        self,
        mode: str,
        prompt_mode: bool | None = None,
    ) -> str:
        prompt_enabled = (
            self._prompt_mode_enabled()
            if prompt_mode is None
            else bool(prompt_mode)
        )
        if not prompt_enabled:
            return mode
        if mode == "pushToTalk":
            return "promptPushToTalk"
        if mode == "handsFree":
            return "prompt"
        return mode

    def _update_prompt_hint_on_main_thread(self) -> None:
        hint = prompt_coach_hint(
            self._latest_prompt_text,
            self._interface_language,
        )
        self._set_capsule_size_on_main_thread(bool(hint.strip()))
        if self._renderer is not None:
            self._renderer.set_streaming_text(hint)

    def show(
        self,
        mode: str = "pushToTalk",
        *,
        prompt_mode: bool | None = None,
    ) -> None:
        """Show the capsule."""
        if prompt_mode is None:
            self._run_on_main_thread(lambda: self._show_on_main_thread(mode))
            return
        self._run_on_main_thread(
            lambda: self._show_on_main_thread(mode, prompt_mode=prompt_mode)
        )

    def _show_on_main_thread(
        self,
        mode: str,
        *,
        prompt_mode: bool | None = None,
    ) -> None:
        self._ensure_setup()
        self._set_capsule_size_on_main_thread(False)
        display_mode = self._display_mode(mode, prompt_mode)
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

        if self._renderer is not None:
            self._renderer.set_interface_language(self._interface_language)
            self._renderer.set_mode(display_mode)
            self._renderer.set_state("recording")
        if display_mode in {"prompt", "promptPushToTalk"}:
            hint = prompt_coach_hint("", self._interface_language)
            # The initial coach hint is already visible on the first frame.
            # Expand the native container before laying out multiline text so
            # the 80pt compact frame cannot clip its top edge.
            self._set_capsule_size_on_main_thread(bool(hint.strip()))
            if self._renderer is not None:
                self._renderer.set_streaming_text(hint)
        self._panel.orderFront_(None)
        self._stop_progress_timer()
        self._start_push_timer()
        print(
            f"[Capsule] show(mode={display_mode}), renderer=AppKit"
        )

    def set_interface_language(self, language: str) -> None:
        """Update the capsule language for the next visible state."""
        normalized = normalize_ui_language(language)
        self._run_on_main_thread(
            lambda: self._set_interface_language_on_main_thread(normalized)
        )

    def _set_interface_language_on_main_thread(self, language: str) -> None:
        self._interface_language = language
        if self._renderer is not None:
            self._renderer.set_interface_language(language)
        if (
            self._panel
            and self._panel.isVisible()
            and self._current_state == "recording"
            and self._current_mode in {"prompt", "promptPushToTalk"}
        ):
            self._update_prompt_hint_on_main_thread()

    def hide(self) -> None:
        """Hide the capsule."""
        self._run_on_main_thread(self._hide_on_main_thread)

    def _hide_on_main_thread(self) -> None:
        self._stop_push_timer()
        self._stop_progress_timer()
        self._set_capsule_size_on_main_thread(False)
        self._current_state = "hidden"
        self._latest_prompt_text = ""
        if self._renderer is not None:
            self._renderer.set_state("hidden")

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
        if self._renderer is not None:
            self._renderer.set_state(state)

        if state == "processing":
            self._stop_push_timer()
            self._set_capsule_size_on_main_thread(False)
            self._start_progress_timer()
            if self._panel is not None:
                self._panel.setIgnoresMouseEvents_(True)

    def update_audio_level(self, level: float) -> None:
        """Store the latest calibrated 0–1 level from the audio thread."""
        with self._audio_level_lock:
            self._latest_audio_level = max(0.0, min(1.0, float(level)))

    def set_processing_stage(self, stage: str) -> None:
        """Update the capsule's processing phase label."""
        value = str(stage or "transcribing")
        self._run_on_main_thread(
            lambda: self._renderer.set_processing_stage(value)
            if self._renderer is not None
            else None
        )

    def update_streaming_text(self, text: str) -> None:
        """Show model output or update the local Prompt coach."""
        value = str(text or "")
        self._run_on_main_thread(
            lambda: self._update_streaming_text_on_main_thread(value)
        )

    def _update_streaming_text_on_main_thread(self, text: str) -> None:
        if (
            self._current_state == "recording"
            and self._current_mode in {"prompt", "promptPushToTalk"}
        ):
            self._latest_prompt_text = str(text or "")
            self._update_prompt_hint_on_main_thread()
            return
        # Resize before showing a multiline partial so the native container
        # does not clip it.
        self._set_capsule_size_on_main_thread(bool(text.strip()))
        if self._renderer is not None:
            self._renderer.set_streaming_text(text)

    def _set_capsule_size_on_main_thread(self, expanded: bool) -> None:
        """Resize around the current horizontal center without moving screens."""
        panel = getattr(self, "_panel", None)
        renderer = getattr(self, "_renderer", None)
        if panel is None or renderer is None:
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
        renderer.set_container_size(width, height)
        renderer.set_expanded(expanded)

    def _start_push_timer(self) -> None:
        """Refresh the native waveform at display cadence while recording."""
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

    def _start_progress_timer(self) -> None:
        self._stop_progress_timer()
        self._progress_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            CAPSULE_PROGRESS_INTERVAL_SECONDS,
            True,
            lambda _: self._advance_progress(),
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(
            self._progress_timer,
            NSRunLoopCommonModes,
        )

    def _stop_progress_timer(self) -> None:
        if self._progress_timer:
            self._progress_timer.invalidate()
            self._progress_timer = None

    def _advance_progress(self) -> None:
        if self._renderer is not None and self._current_state == "processing":
            self._renderer.advance_progress()

    def _push_audio_level(self) -> None:
        """Read the latest envelope and advance the renderer by one frame."""
        with self._audio_level_lock:
            level = self._latest_audio_level
        level = 0.0 if level <= CAPSULE_SILENCE_THRESHOLD else level

        if self._renderer is not None:
            self._renderer.set_audio_level(level)
        self._push_count += 1
        if self._push_count % CAPSULE_AUDIO_PUSH_HZ == 1:
            print(f"[Capsule] waveform_level={level:.4f}")

    def _run_on_main_thread(self, callback: Callable[[], None]) -> None:
        """Marshal UI work onto the main run loop to avoid AppKit crashes."""
        if threading.current_thread() is threading.main_thread():
            callback()
            return

        timer_ref: dict[str, NSTimer | None] = {"timer": None}

        def _fire(_timer) -> None:
            timer = timer_ref["timer"]
            if timer is not None:
                self._main_thread_timers.discard(timer)
            callback()

        timer = NSTimer.timerWithTimeInterval_repeats_block_(0, False, _fire)
        timer_ref["timer"] = timer
        self._main_thread_timers.add(timer)
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
