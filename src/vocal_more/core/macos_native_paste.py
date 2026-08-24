"""Small AppKit/Quartz bridge for low-latency macOS paste delivery."""

_VIRTUAL_KEY_V = 9


class MacOSNativePaste:
    """Write one string to NSPasteboard and post Cmd+V through CoreGraphics."""

    def __init__(self, *, appkit=None, quartz=None) -> None:
        if appkit is None:
            import AppKit as appkit_module

            appkit = appkit_module
        if quartz is None:
            import Quartz as quartz_module

            quartz = quartz_module
        self._appkit = appkit
        self._quartz = quartz

    def paste_text(self, text: str) -> bool:
        """Return whether native pasteboard and event dispatch both succeeded."""
        pasteboard = self._appkit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        if not pasteboard.setString_forType_(
            str(text),
            self._appkit.NSPasteboardTypeString,
        ):
            return False

        source = self._quartz.CGEventSourceCreate(
            self._quartz.kCGEventSourceStateHIDSystemState
        )
        key_down = self._quartz.CGEventCreateKeyboardEvent(
            source,
            _VIRTUAL_KEY_V,
            True,
        )
        key_up = self._quartz.CGEventCreateKeyboardEvent(
            source,
            _VIRTUAL_KEY_V,
            False,
        )
        if key_down is None or key_up is None:
            return False

        for event in (key_down, key_up):
            self._quartz.CGEventSetFlags(
                event,
                self._quartz.kCGEventFlagMaskCommand,
            )
            self._quartz.CGEventPost(
                self._quartz.kCGHIDEventTap,
                event,
            )
        return True


__all__ = ["MacOSNativePaste"]
