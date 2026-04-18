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

    manager = hotkey_module.HotkeyManager(
        on_fn_pressed=on_pressed,
        on_fn_released=on_released,
        on_double_cmd=on_double_cmd,
    )

    manager._enqueue_event(hotkey_module.HotkeyEvent.FN_PRESSED)
    manager._enqueue_event(hotkey_module.HotkeyEvent.DOUBLE_CMD)
    manager._enqueue_event(hotkey_module.HotkeyEvent.FN_RELEASED)

    assert done.wait(timeout=1.0)
    assert received == ["pressed", "double_cmd", "released"]

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
