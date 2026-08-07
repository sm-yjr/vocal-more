"""Tests for the Windows global-key adapter."""

from __future__ import annotations

from types import SimpleNamespace
import threading
import time

from vocal_more.core.windows_hotkey_manager import (
    WindowsHotkeyManager,
    token_for_browser_code,
    token_for_pynput_key,
)
from vocal_more.domain.hotkey_catalog import CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE


def _config(*, active_hotkeys=None, custom_keys=None):
    return SimpleNamespace(
        hotkey=SimpleNamespace(
            active_hotkeys=list(active_hotkeys or []),
            custom_keys=list(custom_keys or []),
            custom_key=None,
        )
    )


def _wait_until(predicate, timeout: float = 0.75) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def _named(name: str):
    return SimpleNamespace(name=name, vk=None, char=None)


def _keycode(*, vk: int | None, char: str | None):
    return SimpleNamespace(name=None, vk=vk, char=char)


def test_browser_codes_use_physical_windows_virtual_keys():
    assert token_for_browser_code("KeyA") == "vk:65"
    assert token_for_browser_code("Digit1") == "vk:49"
    assert token_for_browser_code("Semicolon") == "vk:186"
    assert token_for_browser_code("Numpad1") == "vk:97"
    assert token_for_browser_code("F8") == "key:f8"


def test_pynput_token_prefers_vk_over_shifted_character():
    assert token_for_pynput_key(_keycode(vk=49, char="!")) == "vk:49"
    assert token_for_pynput_key(_keycode(vk=65, char="A")) == "vk:65"
    assert token_for_pynput_key(_named("escape")) == "key:esc"


def test_f8_autorepeat_emits_one_press_and_one_release():
    events: list[str] = []
    manager = WindowsHotkeyManager(
        on_fn_pressed=lambda: events.append("pressed"),
        on_fn_released=lambda: events.append("released"),
        config=_config(active_hotkeys=["fn"]),
    )

    manager._on_press(_named("f8"))
    manager._on_press(_named("f8"))
    manager._on_release(_named("f8"))
    manager._on_release(_named("f8"))

    _wait_until(lambda: events == ["pressed", "released"])
    assert manager.is_fn_pressed() is False
    manager.stop()


def test_multiple_trigger_keys_are_one_aggregate_gesture():
    custom = CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE["KeyA"].to_config()
    events: list[str] = []
    manager = WindowsHotkeyManager(
        on_fn_pressed=lambda: events.append("pressed"),
        on_fn_released=lambda: events.append("released"),
        config=_config(active_hotkeys=["fn"], custom_keys=[custom]),
    )

    manager._on_press(_named("f8"))
    manager._on_press(_keycode(vk=65, char="a"))
    manager._on_release(_named("f8"))
    time.sleep(0.02)
    assert events == ["pressed"]

    manager._on_release(_keycode(vk=65, char="a"))
    _wait_until(lambda: events == ["pressed", "released"])
    manager.stop()


def test_escape_cancels_once_per_physical_press():
    calls = 0
    lock = threading.Lock()

    def on_escape() -> None:
        nonlocal calls
        with lock:
            calls += 1

    manager = WindowsHotkeyManager(
        on_escape_pressed=on_escape,
        config=_config(active_hotkeys=["fn"]),
    )

    manager._on_press(_named("esc"))
    manager._on_press(_named("esc"))
    _wait_until(lambda: calls == 1)
    manager._on_release(_named("esc"))
    manager._on_press(_named("esc"))
    _wait_until(lambda: calls == 2)
    manager.stop()


def test_configured_escape_is_a_trigger_not_cancel():
    custom = CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE["Escape"].to_config()
    events: list[str] = []
    manager = WindowsHotkeyManager(
        on_fn_pressed=lambda: events.append("pressed"),
        on_fn_released=lambda: events.append("released"),
        on_escape_pressed=lambda: events.append("cancel"),
        config=_config(custom_keys=[custom]),
    )

    manager._on_press(_named("esc"))
    manager._on_release(_named("esc"))

    _wait_until(lambda: events == ["pressed", "released"])
    manager.stop()


def test_listener_lifecycle_is_lazy_and_bounded(monkeypatch):
    class FakeListener:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.alive = False
            self.joined = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def stop(self):
            self.alive = False

        def join(self, timeout=None):
            self.joined = True

    keyboard = SimpleNamespace(Listener=FakeListener)
    monkeypatch.setattr(
        "vocal_more.core.windows_hotkey_manager.time.sleep",
        lambda _seconds: None,
    )
    manager = WindowsHotkeyManager(
        config=_config(active_hotkeys=["fn"]),
        keyboard_module=keyboard,
    )

    assert manager.start() is True
    listener = manager._listener
    assert listener.kwargs["suppress"] is False
    manager.stop()
    assert listener.joined is True


def test_windows_modifier_label_does_not_say_command():
    command_key = CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE["MetaLeft"].to_config()
    manager = WindowsHotkeyManager(
        config=_config(custom_keys=[command_key]),
    )

    assert manager.trigger_label == "Left Windows"
    manager.stop()
