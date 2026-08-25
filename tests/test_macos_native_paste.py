"""Unit tests for the narrow AppKit/Quartz native paste bridge."""

from __future__ import annotations

from types import SimpleNamespace

from vocal_more.core import macos_native_paste


class _Pasteboard:
    def __init__(self, accepts: bool = True, initial=None) -> None:
        self.accepts = accepts
        self.cleared = False
        self.value = initial
        self.paste_type = None
        self.change_count = 0
        self.string_for_type_calls = 0

    def clearContents(self):
        self.cleared = True
        self.change_count += 1

    def setString_forType_(self, value, paste_type):
        if not self.accepts:
            return False
        self.value = value
        self.paste_type = paste_type
        self.change_count += 1
        return True

    def stringForType_(self, paste_type):
        self.string_for_type_calls += 1
        return self.value

    def changeCount(self):
        return self.change_count


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


def _make_appkit(pasteboard):
    return SimpleNamespace(
        NSPasteboard=SimpleNamespace(generalPasteboard=lambda: pasteboard),
        NSPasteboardTypeString="public.utf8-plain-text",
    )


def _make_quartz(events):
    return SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGEventFlagMaskCommand=2,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda state: ("source", state),
        CGEventCreateKeyboardEvent=lambda source, key, down: {
            "source": source,
            "key": key,
            "down": down,
        },
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        CGEventPost=lambda tap, event: events.append((tap, dict(event))),
    )


def test_native_paste_writes_string_and_posts_command_v(monkeypatch):
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard()
    events = []
    monkeypatch.setattr(
        macos_native_paste.threading, "Timer", _TimerRecorder()
    )

    paster = MacOSNativePaste(
        appkit=_make_appkit(pasteboard),
        quartz=_make_quartz(events),
        restore_clipboard=False,
    )
    assert paster.paste_text("你好") is True

    assert pasteboard.cleared is True
    assert pasteboard.value == "你好"
    assert pasteboard.paste_type == "public.utf8-plain-text"
    assert [(event[1]["key"], event[1]["down"]) for event in events] == [
        (9, True),
        (9, False),
    ]
    assert all(event[1]["flags"] == 2 for event in events)


def test_native_paste_stops_when_pasteboard_rejects_string():
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard(accepts=False)
    appkit = SimpleNamespace(
        NSPasteboard=SimpleNamespace(generalPasteboard=lambda: pasteboard),
        NSPasteboardTypeString="text",
    )
    quartz = SimpleNamespace()

    paster = MacOSNativePaste(
        appkit=appkit, quartz=quartz, restore_clipboard=True
    )
    assert paster.paste_text("text") is False


def test_native_paste_restores_previous_clipboard_text(monkeypatch):
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard(initial="old text")
    recorder = _TimerRecorder(run_immediately=True)
    monkeypatch.setattr(macos_native_paste.threading, "Timer", recorder)

    paster = MacOSNativePaste(
        appkit=_make_appkit(pasteboard),
        quartz=_make_quartz([]),
        restore_clipboard=True,
    )
    assert paster.paste_text("new text") is True

    # The restore timer ran synchronously and put the snapshot back.
    assert len(recorder.timers) == 1
    assert recorder.timers[0].daemon is True
    assert pasteboard.value == "old text"
    assert pasteboard.paste_type == "public.utf8-plain-text"


def test_native_paste_skips_restore_when_third_party_changed_clipboard(monkeypatch):
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard(initial="old text")
    recorder = _TimerRecorder(run_immediately=False)
    monkeypatch.setattr(macos_native_paste.threading, "Timer", recorder)

    paster = MacOSNativePaste(
        appkit=_make_appkit(pasteboard),
        quartz=_make_quartz([]),
        restore_clipboard=True,
    )
    assert paster.paste_text("new text") is True
    assert pasteboard.value == "new text"

    # Another app writes to the clipboard before the restore timer fires.
    pasteboard.clearContents()
    pasteboard.setString_forType_("third-party", "public.utf8-plain-text")
    change_count_before_fire = pasteboard.changeCount()

    recorder.fire_all()

    assert pasteboard.value == "third-party"
    assert pasteboard.changeCount() == change_count_before_fire


def test_native_paste_skips_restore_when_snapshot_is_none(monkeypatch):
    """Image/file/empty clipboard snapshots as None and must not be restored."""
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard(initial=None)
    recorder = _TimerRecorder(run_immediately=True)
    monkeypatch.setattr(macos_native_paste.threading, "Timer", recorder)

    paster = MacOSNativePaste(
        appkit=_make_appkit(pasteboard),
        quartz=_make_quartz([]),
        restore_clipboard=True,
    )
    assert paster.paste_text("new text") is True

    assert pasteboard.value == "new text"
    assert recorder.timers == []


def test_native_paste_skips_restore_when_disabled(monkeypatch):
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard(initial="old text")
    recorder = _TimerRecorder(run_immediately=True)
    monkeypatch.setattr(macos_native_paste.threading, "Timer", recorder)

    paster = MacOSNativePaste(
        appkit=_make_appkit(pasteboard),
        quartz=_make_quartz([]),
        restore_clipboard=False,
    )
    assert paster.paste_text("new text") is True

    # Disabled: no snapshot taken, no timer scheduled, clipboard untouched.
    assert pasteboard.string_for_type_calls == 0
    assert recorder.timers == []
    assert pasteboard.value == "new text"
