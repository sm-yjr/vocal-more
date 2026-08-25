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


class _BrokenSnapshotClipboard(_FakeClipboard):
    def paste(self):
        raise RuntimeError("clipboard unavailable")


class _TimerRecorder:
    """Stand-in for threading.Timer that never waits.

    With ``run_immediately=True`` the restore callback runs synchronously on
    ``start()``; otherwise the timer is recorded and must be fired manually.
    """

    def __init__(self, run_immediately: bool = True) -> None:
        self.run_immediately = run_immediately
        self.timers: list[SimpleNamespace] = []

    def __call__(self, interval, function, args=(), kwargs=None):
        recorder = self

        def start() -> None:
            if recorder.run_immediately:
                function(*args)

        timer = SimpleNamespace(
            interval=interval,
            function=function,
            args=tuple(args),
            daemon=False,
            start=start,
        )
        self.timers.append(timer)
        return timer

    def fire_all(self) -> None:
        for timer in self.timers:
            timer.function(*timer.args)


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
    monkeypatch.setattr(keyboard_sim.threading, "Timer", _TimerRecorder())

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=clipboard,
        restore_clipboard=False,
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
    monkeypatch.setattr(keyboard_sim.threading, "Timer", _TimerRecorder())

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="darwin",
        keyboard=keyboard,
        clipboard=_FakeClipboard(),
        native_fast_paste=False,
        restore_clipboard=False,
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
    monkeypatch.setattr(keyboard_sim.threading, "Timer", _TimerRecorder())

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="darwin",
        keyboard=keyboard,
        clipboard=clipboard,
        native_fast_paste=True,
        native_paster=native_paster,
        restore_clipboard=False,
    )
    simulator.paste_text("fallback")

    assert clipboard.value == "fallback"
    assert ("press", "v") in keyboard.events


def test_windows_select_all_uses_ctrl_a_then_paste(monkeypatch):
    keyboard = _FakeKeyboard()
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(keyboard_sim.threading, "Timer", _TimerRecorder())

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=_FakeClipboard(),
        restore_clipboard=False,
    )
    simulator.select_all_and_replace("replacement")

    assert keyboard.events[:4] == [
        ("modifier_down", "ctrl"),
        ("press", "a"),
        ("release", "a"),
        ("modifier_up", "ctrl"),
    ]
    assert ("press", "v") in keyboard.events


def test_windows_paste_restores_previous_clipboard(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _FakeClipboard()
    recorder = _TimerRecorder(run_immediately=True)
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(keyboard_sim.threading, "Timer", recorder)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=clipboard,
        restore_clipboard=True,
    )
    simulator.paste_text("hello")

    # The restore timer ran synchronously and put the snapshot back.
    assert len(recorder.timers) == 1
    assert recorder.timers[0].daemon is True
    assert clipboard.value == "before"
    assert ("press", "v") in keyboard.events


def test_windows_paste_skips_restore_when_clipboard_changed_elsewhere(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _FakeClipboard()
    recorder = _TimerRecorder(run_immediately=False)
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(keyboard_sim.threading, "Timer", recorder)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=clipboard,
        restore_clipboard=True,
    )
    simulator.paste_text("hello")
    assert clipboard.value == "hello"

    # Another app writes to the clipboard before the restore timer fires.
    clipboard.copy("third-party")

    recorder.fire_all()

    assert clipboard.value == "third-party"


def test_windows_paste_skips_restore_when_disabled(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _FakeClipboard()
    recorder = _TimerRecorder(run_immediately=True)
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(keyboard_sim.threading, "Timer", recorder)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=clipboard,
        restore_clipboard=False,
    )
    simulator.paste_text("hello")

    assert clipboard.value == "hello"
    assert recorder.timers == []


def test_windows_paste_survives_snapshot_failure(monkeypatch):
    keyboard = _FakeKeyboard()
    clipboard = _BrokenSnapshotClipboard()
    recorder = _TimerRecorder(run_immediately=True)
    monkeypatch.setattr(keyboard_sim, "Key", _keys())
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(keyboard_sim.threading, "Timer", recorder)

    simulator = keyboard_sim.KeyboardSimulator(
        platform_name="win32",
        keyboard=keyboard,
        clipboard=clipboard,
        restore_clipboard=True,
    )
    # Snapshot raising must not break the paste itself.
    simulator.paste_text("hello")

    assert clipboard.value == "hello"
    assert recorder.timers == []
    assert ("press", "v") in keyboard.events
