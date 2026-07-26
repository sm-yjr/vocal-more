"""Pure state machine for the default hold-or-tap dictation gesture."""

from __future__ import annotations

from enum import Enum


DEFAULT_HOLD_THRESHOLD_SECONDS = 0.35


class HotkeyGestureAction(Enum):
    """Intent emitted by a raw press or release event."""

    START = "start"
    STOP = "stop"
    LATCH = "latch"
    IGNORE = "ignore"


def _state_value(state: object) -> str:
    value = getattr(state, "value", state)
    return str(value).strip().lower()


class HotkeyGestureController:
    """Distinguish hold-to-talk from a quick hands-free tap.

    Recording starts immediately on the first key-down, so gesture
    classification does not add microphone or network latency. Releasing
    after the hold threshold stops the recording. A quick release latches
    the same recording into hands-free mode until the next key-down.
    """

    def __init__(
        self,
        *,
        hold_threshold: float = DEFAULT_HOLD_THRESHOLD_SECONDS,
    ) -> None:
        self._hold_threshold = max(0.15, min(1.0, float(hold_threshold)))
        self._pressed_at: float | None = None
        self._latched = False

    @property
    def latched(self) -> bool:
        return self._latched

    def reset(self) -> None:
        self._pressed_at = None
        self._latched = False

    def on_pressed(
        self,
        event_time: float,
        mode_state: object,
    ) -> HotkeyGestureAction:
        state = _state_value(mode_state)
        if state == "idle":
            self.reset()
            self._pressed_at = float(event_time)
            return HotkeyGestureAction.START

        if state in {"starting", "recording"} and self._latched:
            self.reset()
            return HotkeyGestureAction.STOP

        return HotkeyGestureAction.IGNORE

    def on_released(
        self,
        event_time: float,
        mode_state: object,
    ) -> HotkeyGestureAction:
        if self._pressed_at is None:
            return HotkeyGestureAction.IGNORE

        pressed_at = self._pressed_at
        self._pressed_at = None
        state = _state_value(mode_state)
        if state not in {"starting", "recording"}:
            self.reset()
            return HotkeyGestureAction.IGNORE

        if float(event_time) - pressed_at >= self._hold_threshold:
            self.reset()
            return HotkeyGestureAction.STOP

        self._latched = True
        return HotkeyGestureAction.LATCH


__all__ = [
    "DEFAULT_HOLD_THRESHOLD_SECONDS",
    "HotkeyGestureAction",
    "HotkeyGestureController",
]
