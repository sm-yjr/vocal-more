"""Small AppKit/Quartz bridge for low-latency macOS paste delivery."""

import threading

_VIRTUAL_KEY_V = 9
_RESTORE_DELAY_SECONDS = 0.6


class MacOSNativePaste:
    """Write one string to NSPasteboard and post Cmd+V through CoreGraphics."""

    def __init__(
        self,
        *,
        appkit=None,
        quartz=None,
        restore_clipboard: bool | None = None,
        restore_delay: float = _RESTORE_DELAY_SECONDS,
    ) -> None:
        if appkit is None:
            import AppKit as appkit_module

            appkit = appkit_module
        if quartz is None:
            import Quartz as quartz_module

            quartz = quartz_module
        self._appkit = appkit
        self._quartz = quartz
        self._restore_clipboard = restore_clipboard
        self._restore_delay = restore_delay

    def paste_text(self, text: str) -> bool:
        """Return whether native pasteboard and event dispatch both succeeded."""
        pasteboard = self._appkit.NSPasteboard.generalPasteboard()

        snapshot = None
        restore_enabled = self._should_restore_clipboard()
        if restore_enabled:
            try:
                snapshot = pasteboard.stringForType_(
                    self._appkit.NSPasteboardTypeString
                )
            except Exception:  # noqa: BLE001,S110 - restore must never break paste
                snapshot = None

        pasteboard.clearContents()
        if not pasteboard.setString_forType_(
            str(text),
            self._appkit.NSPasteboardTypeString,
        ):
            return False

        change_count_after_write = None
        if restore_enabled and snapshot is not None:
            try:
                change_count_after_write = pasteboard.changeCount()
            except Exception:  # noqa: BLE001,S110 - restore must never break paste
                change_count_after_write = None

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

        if (
            restore_enabled
            and snapshot is not None
            and change_count_after_write is not None
        ):
            self._schedule_clipboard_restore(snapshot, change_count_after_write)
        return True

    def _should_restore_clipboard(self) -> bool:
        if self._restore_clipboard is not None:
            return bool(self._restore_clipboard)
        try:
            from ..config import get_config

            return bool(get_config().restore_clipboard)
        except (AttributeError, ImportError):
            return False

    def _schedule_clipboard_restore(
        self, snapshot: str, expected_change_count
    ) -> None:
        try:
            timer = threading.Timer(
                self._restore_delay,
                self._restore_clipboard_snapshot,
                args=(snapshot, expected_change_count),
            )
            timer.daemon = True
            timer.start()
        except Exception:  # noqa: BLE001,S110 - restore must never break paste
            pass

    def _restore_clipboard_snapshot(self, snapshot: str, expected_change_count) -> None:
        try:
            pasteboard = self._appkit.NSPasteboard.generalPasteboard()
            if pasteboard.changeCount() != expected_change_count:
                # Someone else wrote to the clipboard in the meantime;
                # restoring would destroy their content.
                return
            pasteboard.clearContents()
            pasteboard.setString_forType_(
                snapshot,
                self._appkit.NSPasteboardTypeString,
            )
        except Exception:  # noqa: BLE001,S110 - restore must never break paste
            pass


__all__ = ["MacOSNativePaste"]
