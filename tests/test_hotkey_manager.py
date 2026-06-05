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


def test_right_command_hotkey_is_registered_as_modifier(monkeypatch):
    """Built-in right Command should be treated as a modifier hotkey."""
    config = Config()
    config.hotkey.active_hotkeys = ["right_cmd"]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert manager._modifier_lookup[hotkey_module.CMD_RIGHT_KEYCODE] == hotkey_module.NX_COMMANDMASK


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
