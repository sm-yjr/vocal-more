"""Tests for the unified hold-or-tap dictation gesture."""


def test_quick_tap_latches_hands_free_until_the_next_press():
    from vocal_more.domain.hotkey_gestures import (
        HotkeyGestureAction,
        HotkeyGestureController,
    )
    from vocal_more.modes.base_mode import ModeState

    controller = HotkeyGestureController(hold_threshold=0.35)

    assert controller.on_pressed(10.0, ModeState.IDLE) == HotkeyGestureAction.START
    assert (
        controller.on_released(10.1, ModeState.RECORDING)
        == HotkeyGestureAction.LATCH
    )
    assert (
        controller.on_pressed(11.0, ModeState.RECORDING)
        == HotkeyGestureAction.STOP
    )
    assert (
        controller.on_released(11.1, ModeState.PROCESSING)
        == HotkeyGestureAction.IGNORE
    )


def test_hold_to_talk_stops_when_the_same_key_is_released():
    from vocal_more.domain.hotkey_gestures import (
        HotkeyGestureAction,
        HotkeyGestureController,
    )
    from vocal_more.modes.base_mode import ModeState

    controller = HotkeyGestureController(hold_threshold=0.35)

    assert controller.on_pressed(20.0, ModeState.IDLE) == HotkeyGestureAction.START
    assert (
        controller.on_released(20.6, ModeState.RECORDING)
        == HotkeyGestureAction.STOP
    )


def test_failed_start_resets_the_gesture_without_emitting_a_stop():
    from vocal_more.domain.hotkey_gestures import (
        HotkeyGestureAction,
        HotkeyGestureController,
    )
    from vocal_more.modes.base_mode import ModeState

    controller = HotkeyGestureController()

    controller.on_pressed(30.0, ModeState.IDLE)

    assert (
        controller.on_released(30.8, ModeState.IDLE)
        == HotkeyGestureAction.IGNORE
    )
    assert controller.latched is False
