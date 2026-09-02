"""Small native AppKit renderer for the floating capsule."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import objc
from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSFontWeightMedium,
    NSLineBreakByWordWrapping,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextField,
    NSView,
    NSWorkspace,
)
from Foundation import NSObject
from Quartz import CGColorCreateGenericRGB

_TRANSLATIONS = {
    "en": {
        "command": "Command",
        "meeting": "Meeting recording",
        "prompt": "Prompt",
        "transcribing": "Transcribing",
        "polishing": "Polishing",
        "understanding": "Understanding",
        "searching": "Searching",
        "generating": "Generating",
        "meeting_transcribing": "Generating transcript",
        "meeting_summarizing": "Generating minutes",
    },
    "zh": {
        "command": "指令",
        "meeting": "会议录制中",
        "prompt": "提示词",
        "transcribing": "识别中",
        "polishing": "润色中",
        "understanding": "理解中",
        "searching": "搜索中",
        "generating": "生成中",
        "meeting_transcribing": "生成逐字稿中",
        "meeting_summarizing": "生成纪要中",
    },
}


class _CapsuleActionTarget(NSObject):
    def initWithCallbacks_(self, callbacks):
        instance = objc.super(_CapsuleActionTarget, self).init()
        if instance is None:
            return None
        instance._callbacks = callbacks
        return instance

    def cancel_(self, _sender) -> None:
        callback = self._callbacks.get("cancel")
        if callback is not None:
            callback()

    def finish_(self, _sender) -> None:
        callback = self._callbacks.get("finish")
        if callback is not None:
            callback()


def _cgcolor(white: float, alpha: float):
    return CGColorCreateGenericRGB(white, white, white, alpha)


def _configure_layer(
    view: NSView,
    color: tuple[float, float],
    radius: float = 0.0,
) -> None:
    view.setWantsLayer_(True)
    layer = view.layer()
    layer.setBackgroundColor_(_cgcolor(*color))
    layer.setCornerRadius_(radius)


def _label(text: str, *, size: float = 12.0, weight=NSFontWeightMedium):
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    label.setTextColor_(NSColor.colorWithWhite_alpha_(1.0, 0.82))
    label.setAlignment_(NSTextAlignmentCenter)
    return label


class NativeCapsuleRenderer:
    """Own the native view tree while ``FloatingCapsule`` owns state."""

    COMPACT_BAR_COUNT = 10
    EXPANDED_BAR_COUNT = 24
    NUM_BARS = EXPANDED_BAR_COUNT
    WAVEFORM_PHASE_RADIANS_PER_SECOND = 8.64
    WAVEFORM_ATTACK_SECONDS = 0.045
    WAVEFORM_DECAY_SECONDS = 0.18

    def __init__(
        self,
        *,
        width: float,
        height: float,
        on_cancel: Callable[[], None] | None,
        on_finish: Callable[[], None] | None,
    ) -> None:
        self.view = NSView.alloc().initWithFrame_(((0, 0), (width, height)))
        self.view.setWantsLayer_(True)

        self._surface = NSView.alloc().initWithFrame_(((0, 0), (64, 36)))
        _configure_layer(self._surface, (0.0, 1.0), 18.0)
        surface_layer = self._surface.layer()
        surface_layer.setBorderWidth_(1.0)
        surface_layer.setBorderColor_(_cgcolor(1.0, 0.32))
        surface_layer.setShadowColor_(_cgcolor(0.0, 1.0))
        surface_layer.setShadowOpacity_(0.35)
        surface_layer.setShadowRadius_(15.0)
        surface_layer.setShadowOffset_((0.0, -8.0))
        self.view.addSubview_(self._surface)

        callbacks = {}
        if on_cancel is not None:
            callbacks["cancel"] = on_cancel
        if on_finish is not None:
            callbacks["finish"] = on_finish
        self._action_target = _CapsuleActionTarget.alloc().initWithCallbacks_(callbacks)
        self._cancel_button = self._make_button("×", "cancel:")
        self._finish_button = self._make_button("✓", "finish:")

        self._recording_label = _label("")
        self._thinking_label = _label("Transcribing")
        self._streaming_label = _label("", size=12.0)
        self._streaming_label.setAlignment_(NSTextAlignmentLeft)
        self._streaming_label.setTextColor_(
            NSColor.colorWithWhite_alpha_(1.0, 0.72)
        )
        cell = self._streaming_label.cell()
        if cell is not None:
            cell.setWraps_(True)
            cell.setUsesSingleLineMode_(False)
            cell.setLineBreakMode_(NSLineBreakByWordWrapping)

        self._waveform = []
        for _ in range(self.NUM_BARS):
            bar = NSView.alloc().initWithFrame_(((0, 0), (2, 2)))
            _configure_layer(bar, (1.0, 0.9), 1.0)
            self._surface.addSubview_(bar)
            self._waveform.append(bar)

        self._progress_track = NSView.alloc().initWithFrame_(((0, 0), (40, 3)))
        _configure_layer(
            self._progress_track,
            (1.0, 0.15),
            1.5,
        )
        self._progress_fill = NSView.alloc().initWithFrame_(((0, 0), (0, 3)))
        _configure_layer(
            self._progress_fill,
            (1.0, 0.7),
            1.5,
        )
        self._progress_track.addSubview_(self._progress_fill)

        for view in (
            self._cancel_button,
            self._finish_button,
            self._recording_label,
            self._thinking_label,
            self._streaming_label,
            self._progress_track,
        ):
            self._surface.addSubview_(view)

        self._width = float(width)
        self._height = float(height)
        self._mode = "pushToTalk"
        self._state = "hidden"
        self._language = "en"
        self._stage = "transcribing"
        self._streaming_text = ""
        self._expanded = False
        self._progress = 0.0
        self._phase = 0.0
        self._smoothed_levels = [0.0] * self.NUM_BARS
        self._last_waveform_tick = time.monotonic()
        self._reduce_motion = self._read_reduce_motion()
        self._layout()
        self.set_state("hidden")

    def _make_button(self, title: str, action: str):
        button = NSButton.alloc().initWithFrame_(((0, 0), (22, 22)))
        button.setTitle_(title)
        button.setBordered_(False)
        button.setFont_(NSFont.systemFontOfSize_weight_(13.0, NSFontWeightMedium))
        button.setContentTintColor_(NSColor.colorWithWhite_alpha_(1.0, 0.82))
        button.setTarget_(self._action_target)
        button.setAction_(action)
        _configure_layer(button, (1.0, 0.15), 11.0)
        return button

    @staticmethod
    def _read_reduce_motion() -> bool:
        workspace = NSWorkspace.sharedWorkspace()
        getter = getattr(workspace, "accessibilityDisplayShouldReduceMotion", None)
        return bool(getter()) if callable(getter) else False

    @property
    def content_view(self):
        return self.view

    def set_container_size(self, width: float, height: float) -> None:
        self._width = float(width)
        self._height = float(height)
        self.view.setFrame_(((0, 0), (width, height)))
        self._layout()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._update_labels()
        self._layout()

    def set_interface_language(self, language: str) -> None:
        self._language = "zh" if language == "zh" else "en"
        self._update_labels()

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "hidden":
            self._streaming_text = ""
            self._progress = 0.0
        elif state == "recording":
            self._progress = 0.0
            self._smoothed_levels = [0.0] * self.NUM_BARS
            self._last_waveform_tick = time.monotonic()
        elif state == "processing":
            self._streaming_text = ""
            self._expanded = False
            self._progress = 0.0
        self._update_labels()
        self._layout()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._layout()

    def set_processing_stage(self, stage: str) -> None:
        self._stage = stage or "transcribing"
        self._update_labels()

    def set_streaming_text(self, text: str) -> None:
        self._streaming_text = str(text or "")
        visible = self._streaming_text
        if len(visible) > 700:
            visible = "…" + visible[-700:]
        self._streaming_label.setStringValue_(visible)
        self._layout()

    def set_audio_level(self, level: float) -> None:
        display_level = max(0.0, min(1.0, float(level)))
        now = time.monotonic()
        elapsed = max(1.0 / 120.0, min(0.05, now - self._last_waveform_tick))
        self._last_waveform_tick = now
        if not self._reduce_motion:
            self._phase += self.WAVEFORM_PHASE_RADIANS_PER_SECOND * elapsed
        active_count = self._active_bar_count()
        center = (active_count - 1) / 2
        for index, bar in enumerate(self._waveform):
            if index >= active_count:
                self._smoothed_levels[index] = 0.0
                continue
            distance = abs(index - center) / center
            bell = math.exp(-3.5 * distance * distance)
            movement = 0.86
            if not self._reduce_motion:
                movement += 0.14 * math.sin(self._phase + index * 0.83)
            target = min(1.0, display_level * bell * movement)
            current = self._smoothed_levels[index]
            time_constant = (
                self.WAVEFORM_ATTACK_SECONDS
                if target > current
                else self.WAVEFORM_DECAY_SECONDS
            )
            smoothing = 1.0 - math.exp(-elapsed / time_constant)
            current += (target - current) * smoothing
            self._smoothed_levels[index] = current
            height = 2.0 + current * 18.0
            frame = bar.frame()
            bar.setFrame_(
                ((frame.origin.x, self._row_center_y() - height / 2), (2, height))
            )

    def advance_progress(self) -> None:
        remaining = 0.9 - self._progress
        self._progress += remaining * 0.35
        frame = self._progress_fill.frame()
        self._progress_fill.setFrame_(
            ((frame.origin.x, frame.origin.y), (40 * self._progress, 3))
        )

    def _translation(self, key: str) -> str:
        translations = _TRANSLATIONS.get(self._language, _TRANSLATIONS["en"])
        return translations.get(key, _TRANSLATIONS["en"].get(key, key))

    def _update_labels(self) -> None:
        if self._mode == "command":
            recording = self._translation("command")
        elif self._mode in {"prompt", "promptPushToTalk"}:
            recording = self._translation("prompt")
        else:
            recording = self._translation("meeting")
        self._recording_label.setStringValue_(recording)
        self._thinking_label.setStringValue_(self._translation(self._stage))

    def _compact_surface_width(self) -> float:
        return {
            "pushToTalk": 64.0,
            "handsFree": 126.0,
            "prompt": 178.0,
            "promptPushToTalk": 112.0,
            "command": 172.0,
            "meeting": 168.0,
        }.get(self._mode, 126.0)

    def _row_center_y(self) -> float:
        surface_height = self._surface.frame().size.height
        return surface_height - 18.0 if self._is_expanded() else 18.0

    def _is_expanded(self) -> bool:
        return self._expanded and bool(self._streaming_text.strip())

    def _active_bar_count(self) -> int:
        if self._is_expanded():
            return self.EXPANDED_BAR_COUNT
        return self.COMPACT_BAR_COUNT

    def _layout(self) -> None:
        expanded = self._is_expanded()
        surface_width = 360.0 if expanded else self._compact_surface_width()
        surface_height = 176.0 if expanded else 36.0
        surface_x = (self._width - surface_width) / 2
        self._surface.setFrame_(((surface_x, 12.0), (surface_width, surface_height)))

        is_recording = self._state == "recording"
        is_processing = self._state == "processing"
        buttons_visible = is_recording and self._mode in {
            "handsFree",
            "prompt",
            "command",
        }
        label_visible = is_recording and self._mode in {
            "meeting",
            "command",
            "prompt",
            "promptPushToTalk",
        }

        self._cancel_button.setHidden_(not buttons_visible)
        self._finish_button.setHidden_(not buttons_visible)
        self._recording_label.setHidden_(not label_visible)
        self._thinking_label.setHidden_(not is_processing)
        self._progress_track.setHidden_(not is_processing)
        self._streaming_label.setHidden_(not expanded)
        active_bar_count = self._active_bar_count()
        for index, bar in enumerate(self._waveform):
            bar.setHidden_(not is_recording or index >= active_bar_count)

        row_y = self._row_center_y() - 11.0
        self._cancel_button.setFrame_(((10.0, row_y), (22.0, 22.0)))
        self._finish_button.setFrame_(((surface_width - 32.0, row_y), (22.0, 22.0)))

        bars_width = active_bar_count * 2.0 + (active_bar_count - 1) * 2.0
        content_center = surface_width / 2
        label_width = 0.0
        if label_visible:
            label_width = 76.0 if self._mode == "meeting" else 54.0
        group_width = label_width + (8.0 if label_width else 0.0) + bars_width
        group_x = content_center - group_width / 2
        self._recording_label.setFrame_(
            ((group_x, self._row_center_y() - 9.0), (label_width, 18.0))
        )
        bars_x = group_x + label_width + (8.0 if label_width else 0.0)
        for index, bar in enumerate(self._waveform[:active_bar_count]):
            frame = bar.frame()
            bar.setFrame_(
                (
                    (bars_x + index * 4.0, self._row_center_y() - frame.size.height / 2),
                    (2.0, frame.size.height),
                )
            )

        thinking_width = 92.0
        processing_group_width = thinking_width + 8.0 + 40.0
        processing_x = content_center - processing_group_width / 2
        self._thinking_label.setFrame_(
            ((processing_x, self._row_center_y() - 9.0), (thinking_width, 18.0))
        )
        self._progress_track.setFrame_(
            (
                (processing_x + thinking_width + 8.0, self._row_center_y() - 1.5),
                (40.0, 3.0),
            )
        )
        self._progress_fill.setFrame_(((0, 0), (40 * self._progress, 3.0)))
        self._streaming_label.setFrame_(((12.0, 12.0), (surface_width - 24.0, 122.0)))
