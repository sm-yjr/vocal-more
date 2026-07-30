"""Tests for runtime hotkey lookup configuration."""

import threading

from vocal_more.config import Config
from vocal_more.core import hotkey_manager as hotkey_module


def test_custom_regular_key_is_active(monkeypatch):
    """Custom regular keys should be added to the regular-key lookup."""
    config = Config()
    config.hotkey.custom_key = {
        "key_code": 49,
        "display_name": "Space",
        "is_modifier": False,
        "flag_mask": 0,
    }
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert 49 in manager._regular_lookup


def test_custom_modifier_key_is_active(monkeypatch):
    """Custom modifier keys should be added to the modifier lookup."""
    config = Config()
    config.hotkey.custom_key = {
        "key_code": 59,
        "display_name": "Control",
        "is_modifier": True,
        "flag_mask": 0x4_0000,
    }
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert manager._modifier_lookup[59] == 0x4_0000


def test_set_custom_key_updates_lookup_tables(monkeypatch):
    """Changing custom keys at runtime should refresh active lookups."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()
    assert 49 not in manager._regular_lookup

    manager.set_custom_key(
        {
            "key_code": 49,
            "display_name": "Space",
            "is_modifier": False,
            "flag_mask": 0,
        }
    )

    assert 49 in manager._regular_lookup

    manager.set_custom_key(None)

    assert 49 not in manager._regular_lookup


def test_multiple_custom_keys_share_the_same_dictation_action(monkeypatch):
    config = Config()
    config.hotkey.custom_keys = [
        {
            "key_code": 111,
            "display_name": "F12",
            "is_modifier": False,
            "flag_mask": 0,
        },
        {
            "key_code": 62,
            "display_name": "Right Control",
            "is_modifier": True,
            "flag_mask": 0x4_0000,
        },
    ]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert 111 in manager._regular_lookup
    assert manager._modifier_lookup[62] == 0x4_0000

    manager.set_custom_keys(
        [
            {
                "key_code": 103,
                "display_name": "F11",
                "is_modifier": False,
                "flag_mask": 0,
            }
        ]
    )

    assert 111 not in manager._regular_lookup
    assert 62 not in manager._modifier_lookup
    assert 103 in manager._regular_lookup


def test_overlapping_custom_keys_emit_one_aggregate_press_and_release(monkeypatch):
    """Two held bindings are one logical dictation gesture, not two."""
    config = Config()
    config.hotkey.active_hotkeys = []
    config.hotkey.custom_keys = [
        {
            "key_code": 111,
            "display_name": "F12",
            "is_modifier": False,
            "flag_mask": 0,
        },
        {
            "key_code": 103,
            "display_name": "F11",
            "is_modifier": False,
            "flag_mask": 0,
        },
    ]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetIntegerValueField",
        lambda event, _field: event["key_code"],
    )
    key_down = object()
    key_up = object()
    monkeypatch.setattr(hotkey_module, "kCGEventKeyDown", key_down)
    monkeypatch.setattr(hotkey_module, "kCGEventKeyUp", key_up)

    manager = hotkey_module.HotkeyManager(
        on_fn_pressed=lambda: None,
        on_fn_released=lambda: None,
    )
    captured = []
    monkeypatch.setattr(manager, "_enqueue_event", captured.append)

    manager._event_callback(
        None,
        key_down,
        {"key_code": 111},
        None,
    )
    manager._event_callback(
        None,
        key_down,
        {"key_code": 103},
        None,
    )
    manager._event_callback(
        None,
        key_up,
        {"key_code": 103},
        None,
    )
    manager._event_callback(
        None,
        key_up,
        {"key_code": 111},
        None,
    )

    assert captured == [
        hotkey_module.HotkeyEvent.FN_PRESSED,
        hotkey_module.HotkeyEvent.FN_RELEASED,
    ]


def test_same_mask_modifiers_release_without_leaving_pressed_state(monkeypatch):
    """Left/right modifiers sharing one Quartz flag must not stick."""
    command_mask = 0x10_0000
    config = Config()
    config.hotkey.active_hotkeys = []
    config.hotkey.custom_keys = [
        {
            "key_code": 55,
            "display_name": "Left Command",
            "is_modifier": True,
            "flag_mask": command_mask,
        },
        {
            "key_code": 54,
            "display_name": "Right Command",
            "is_modifier": True,
            "flag_mask": command_mask,
        },
    ]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetIntegerValueField",
        lambda event, _field: event["key_code"],
    )
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetFlags",
        lambda event: event["flags"],
    )
    flags_changed = object()
    monkeypatch.setattr(
        hotkey_module,
        "kCGEventFlagsChanged",
        flags_changed,
    )

    manager = hotkey_module.HotkeyManager(
        on_fn_pressed=lambda: None,
        on_fn_released=lambda: None,
    )
    captured = []
    monkeypatch.setattr(manager, "_enqueue_event", captured.append)

    for key_code, flags in (
        (55, command_mask),
        (54, command_mask),
        # Releasing left Command keeps the aggregate Command flag set
        # because right Command remains physically held.
        (55, command_mask),
        (54, 0),
    ):
        manager._event_callback(
            None,
            flags_changed,
            {"key_code": key_code, "flags": flags},
            None,
        )

    assert captured == [
        hotkey_module.HotkeyEvent.FN_PRESSED,
        hotkey_module.HotkeyEvent.FN_RELEASED,
    ]
    assert manager.is_fn_pressed() is False


def test_fn_shortcut_consumes_press_and_release(monkeypatch):
    """Configured Fn edges must not reach macOS input-source handling."""
    config = Config()
    config.hotkey.active_hotkeys = ["fn"]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetIntegerValueField",
        lambda event, _field: event["key_code"],
    )
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetFlags",
        lambda event: event["flags"],
    )
    flags_changed = object()
    monkeypatch.setattr(hotkey_module, "kCGEventFlagsChanged", flags_changed)

    manager = hotkey_module.HotkeyManager(
        on_fn_pressed=lambda: None,
        on_fn_released=lambda: None,
    )
    captured = []
    monkeypatch.setattr(manager, "_enqueue_event", captured.append)

    pressed_result = manager._event_callback(
        None,
        flags_changed,
        {
            "key_code": hotkey_module.FN_KEYCODE,
            "flags": hotkey_module.NX_SECONDARYFNMASK,
        },
        None,
    )
    released_result = manager._event_callback(
        None,
        flags_changed,
        {"key_code": hotkey_module.FN_KEYCODE, "flags": 0},
        None,
    )

    assert pressed_result is None
    assert released_result is None
    assert captured == [
        hotkey_module.HotkeyEvent.FN_PRESSED,
        hotkey_module.HotkeyEvent.FN_RELEASED,
    ]


def test_event_tap_filters_at_hid_entry_point(monkeypatch):
    """Fn must be filtered before WindowServer handles its system action."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)
    hid_event_tap = object()
    monkeypatch.setattr(hotkey_module, "kCGHIDEventTap", hid_event_tap)

    captured = {}

    def create_tap(location, placement, options, mask, callback, refcon):
        captured["location"] = location
        return object()

    monkeypatch.setattr(hotkey_module, "CGEventTapCreate", create_tap)
    monkeypatch.setattr(
        hotkey_module,
        "CFMachPortCreateRunLoopSource",
        lambda allocator, tap, order: object(),
    )
    monkeypatch.setattr(hotkey_module, "CFRunLoopGetCurrent", lambda: object())
    monkeypatch.setattr(hotkey_module, "CFRunLoopAddSource", lambda *args: None)
    monkeypatch.setattr(hotkey_module, "CGEventTapEnable", lambda *args: None)
    monkeypatch.setattr(hotkey_module, "CFRunLoopRun", lambda: None)

    manager = hotkey_module.HotkeyManager()
    manager._run_event_loop()

    assert captured["location"] is hid_event_tap


def test_configured_modifier_releases_while_unconfigured_sibling_remains_held(
    monkeypatch,
):
    """A configured side must not stick behind the sibling's aggregate flag."""
    command_mask = 0x10_0000
    config = Config()
    config.hotkey.active_hotkeys = []
    config.hotkey.custom_keys = [
        {
            "key_code": 54,
            "display_name": "Right Command",
            "is_modifier": True,
            "flag_mask": command_mask,
        }
    ]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetIntegerValueField",
        lambda event, _field: event["key_code"],
    )
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetFlags",
        lambda event: event["flags"],
    )
    flags_changed = object()
    monkeypatch.setattr(
        hotkey_module,
        "kCGEventFlagsChanged",
        flags_changed,
    )

    manager = hotkey_module.HotkeyManager(
        on_fn_pressed=lambda: None,
        on_fn_released=lambda: None,
    )
    captured = []
    monkeypatch.setattr(manager, "_enqueue_event", captured.append)

    # Right Command is configured. Left Command is not, but both share
    # Quartz's aggregate Command flag.
    for key_code, flags in (
        (54, command_mask),
        (55, command_mask),
        (54, command_mask),
        (55, 0),
    ):
        manager._event_callback(
            None,
            flags_changed,
            {"key_code": key_code, "flags": flags},
            None,
        )

    assert captured == [
        hotkey_module.HotkeyEvent.FN_PRESSED,
        hotkey_module.HotkeyEvent.FN_RELEASED,
    ]
    assert manager.is_fn_pressed() is False


def test_legacy_right_command_hotkey_is_not_registered_as_builtin(monkeypatch):
    """Only Fn should be registered as a built-in hotkey."""
    config = Config()
    config.hotkey.active_hotkeys = ["right_cmd"]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert 54 not in manager._modifier_lookup


def test_hotkey_events_are_dispatched_serially_in_order(monkeypatch):
    """Queued hotkey events should run on one worker in enqueue order."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    received = []
    done = threading.Event()

    def on_pressed():
        received.append("pressed")

    def on_double_cmd():
        received.append("double_cmd")

    def on_released():
        received.append("released")
        done.set()

    def on_escape():
        received.append("escape")

    manager = hotkey_module.HotkeyManager(
        on_fn_pressed=on_pressed,
        on_fn_released=on_released,
        on_double_cmd=on_double_cmd,
        on_escape_pressed=on_escape,
    )

    manager._enqueue_event(hotkey_module.HotkeyEvent.FN_PRESSED)
    manager._enqueue_event(hotkey_module.HotkeyEvent.DOUBLE_CMD)
    manager._enqueue_event(hotkey_module.HotkeyEvent.ESC_PRESSED)
    manager._enqueue_event(hotkey_module.HotkeyEvent.FN_RELEASED)

    assert done.wait(timeout=1.0)
    assert received == ["pressed", "double_cmd", "escape", "released"]

    manager.stop()


def test_escape_keydown_dispatches_escape_callback_without_consuming_event(monkeypatch):
    """Escape should trigger cancellation callback but still pass through to the focused app."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    captured = []
    manager = hotkey_module.HotkeyManager(on_escape_pressed=lambda: None)
    monkeypatch.setattr(manager, "_enqueue_event", lambda event: captured.append(event))

    event = object()
    monkeypatch.setattr(
        hotkey_module,
        "CGEventGetIntegerValueField",
        lambda event_ref, field: hotkey_module.ESC_KEYCODE,
    )
    keydown_event = object()
    monkeypatch.setattr(hotkey_module, "kCGEventKeyDown", keydown_event)

    returned = manager._event_callback(None, keydown_event, event, None)

    assert captured == [hotkey_module.HotkeyEvent.ESC_PRESSED]
    assert returned is event

    manager.stop()


def test_hotkey_manager_stop_shuts_down_callback_worker(monkeypatch):
    """Stopping the manager should also stop its serial callback worker."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    started = threading.Event()
    allow_exit = threading.Event()

    def fake_run_event_loop():
        manager._running = True
        started.set()
        allow_exit.wait(timeout=1.0)

    manager = hotkey_module.HotkeyManager()
    monkeypatch.setattr(manager, "_run_event_loop", fake_run_event_loop)

    assert manager.start() is True
    assert manager._callback_thread is not None
    assert manager._callback_thread.is_alive()

    allow_exit.set()
    manager.stop()

    assert manager._callback_thread is None


def test_hotkey_manager_failed_event_tap_leaves_clean_retry_state(monkeypatch):
    """A denied event tap should leave the manager ready for a later retry."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)
    monkeypatch.setattr(hotkey_module, "CGEventTapCreate", lambda *args, **kwargs: None)

    manager = hotkey_module.HotkeyManager()

    assert manager.start() is False
    assert manager._running is False
    assert manager._tap is None
    assert manager._run_loop is None
    assert manager._run_loop_source is None

    manager.stop()
    assert manager._thread is None
    assert manager._callback_thread is None
