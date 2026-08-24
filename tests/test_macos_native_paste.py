"""Unit tests for the narrow AppKit/Quartz native paste bridge."""

from __future__ import annotations

from types import SimpleNamespace


class _Pasteboard:
    def __init__(self, accepts: bool = True) -> None:
        self.accepts = accepts
        self.cleared = False
        self.value = None
        self.paste_type = None

    def clearContents(self):
        self.cleared = True

    def setString_forType_(self, value, paste_type):
        self.value = value
        self.paste_type = paste_type
        return self.accepts


def test_native_paste_writes_string_and_posts_command_v():
    from vocal_more.core.macos_native_paste import MacOSNativePaste

    pasteboard = _Pasteboard()
    events = []
    appkit = SimpleNamespace(
        NSPasteboard=SimpleNamespace(generalPasteboard=lambda: pasteboard),
        NSPasteboardTypeString="public.utf8-plain-text",
    )
    quartz = SimpleNamespace(
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

    assert MacOSNativePaste(appkit=appkit, quartz=quartz).paste_text("你好") is True

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

    assert MacOSNativePaste(appkit=appkit, quartz=quartz).paste_text("text") is False
