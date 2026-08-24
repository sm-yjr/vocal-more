"""Cross-platform shortcut tests for KeyboardSimulator."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from vocal_more.core import keyboard_sim


class _FakeKeyboard:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    @contextmanager
    def pressed(self, key):
        self.events.append(("modifier_down", key))
        try:
            yield self
        finally:
            self.events.append(("modifier_up", key))

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))

    def type(self, text):
        self.events.append(("type", text))


class _FakeClipboard:
    def __init__(self) -> None:
        self.value = "before"

    def paste(self):
        return self.value

    def copy(self, text):
        self.value = text


def _keys():
    return SimpleNamespace(
        cmd="cmd",
        ctrl="ctrl",
        backspace="backspace",
        shift="shift",
        left="left",
    )


def test_windows_paste_uses_ctrl_v(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _FakeClipboard()
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=clipboard,
    )
    simulator.paste_text("hello")

    assert clipboard.value == "hello"
    assert keyboard.events == [
        ("modifier_down", "ctrl"),
        ("press", "v"),
        ("release", "v"),
        ("modifier_up", "ctrl"),
    ]


def test_macos_paste_keeps_command_v(monkeypatch):
    keyboard = _FakeKeyboard()
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="darwin",
        keyboard=keyboard,
        clipboard=_FakeClipboard(),
        native_fast_paste=False,
    )
    simulator.paste_text("hello")

    assert keyboard.events[0] == ("modifier_down", "cmd")


def test_macos_native_fast_paste_skips_compatibility_delay(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _FakeClipboard()
    native_paster = SimpleNamespace(paste_text=lambda text: text == "hello")
    monkeypatch.setattr(keyboard_sim, "Key", _keys())

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="darwin",
        keyboard=keyboard,
        clipboard=clipboard,
        native_fast_paste=True,
        native_paster=native_paster,
    )
    simulator.paste_text("hello")

    assert clipboard.value == "before"
    assert keyboard.events == []


def test_macos_native_fast_paste_falls_back_when_dispatch_fails(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _FakeClipboard()
    native_paster = SimpleNamespace(paste_text=lambda _text: False)
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="darwin",
        keyboard=keyboard,
        clipboard=clipboard,
        native_fast_paste=True,
        native_paster=native_paster,
    )
    simulator.paste_text("fallback")

    assert clipboard.value == "fallback"
    assert ("press", "v") in keyboard.events


def test_windows_select_all_uses_ctrl_a_then_paste(monkeypatch):
    keyboard = _FakeKeyboard()
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=_FakeClipboard(),
    )
    simulator.select_all_and_replace("replacement")

    assert keyboard.events[:4] == [
        ("modifier_down", "ctrl"),
        ("press", "a"),
        ("release", "a"),
        ("modifier_up", "ctrl"),
    ]
    assert ("press", "v") in keyboard.events
