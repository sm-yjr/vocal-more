"""Wayland text-output adapter coordinated with the GNOME Shell extension."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .core.text_output import PasteOutcome, TextOutputPort


@dataclass
class _PendingPaste:
    completed: threading.Event
    outcome: PasteOutcome | None = None


class LinuxTextOutputAdapter(TextOutputPort):
    """Write the GTK clipboard, then wait for Shell to confirm ``Ctrl+V``.

    ``paste_text`` is called by a dictation worker. GTK and D-Bus operations are
    delegated to callbacks owned by the Linux application main thread. No
    dictated text crosses the public D-Bus interface.
    """

    def __init__(
        self,
        *,
        write_clipboard: Callable[[str, float], bool],
        request_paste: Callable[[int], None],
        clipboard_timeout: float = 1.0,
        paste_timeout: float = 1.5,
    ) -> None:
        self._write_clipboard = write_clipboard
        self._request_paste = request_paste
        self._clipboard_timeout = max(0.01, float(clipboard_timeout))
        self._paste_timeout = max(0.01, float(paste_timeout))
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self._pending: dict[int, _PendingPaste] = {}
        self._closed = False
        self._last_error: str | None = None
        self._clipboard_write_confirmed = False

    def paste_text(self, text: str) -> PasteOutcome:
        """Paste into whichever application is focused when processing ends."""

        try:
            clipboard_ready = self._write_clipboard(text, self._clipboard_timeout)
        except Exception as exc:
            return self._failed(f"Wayland clipboard write failed: {exc}")
        if not clipboard_ready:
            with self._lock:
                self._clipboard_write_confirmed = False
            return self._failed("Wayland clipboard write timed out")
        with self._lock:
            self._clipboard_write_confirmed = True

        with self._lock:
            if self._closed:
                return self._failed("Linux desktop host is shutting down")
            request_id = next(self._ids)
            pending = _PendingPaste(completed=threading.Event())
            self._pending[request_id] = pending

        try:
            self._request_paste(request_id)
        except Exception as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            return self._failed(f"GNOME Shell paste request failed: {exc}")

        if not pending.completed.wait(self._paste_timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            return self._failed("GNOME Shell paste confirmation timed out")

        outcome = pending.outcome or PasteOutcome.failed(
            "GNOME Shell paste request completed without a result"
        )
        with self._lock:
            self._last_error = outcome.error
        return outcome

    def complete_paste(self, request_id: int, success: bool, error: str = "") -> bool:
        """Resolve a request from ``Desktop1.CompletePaste``.

        Unknown or late confirmations are ignored, making reconnects and
        timeout races harmless.
        """

        with self._lock:
            pending = self._pending.pop(int(request_id), None)
        if pending is None:
            return False
        pending.outcome = (
            PasteOutcome.succeeded()
            if success
            else PasteOutcome.failed(error or "GNOME Shell could not inject Ctrl+V")
        )
        pending.completed.set()
        return True

    def diagnostic_status(self) -> str:
        with self._lock:
            if self._closed:
                return "host closed"
            if self._pending:
                return "waiting for Shell confirmation; clipboard write confirmed"
            if self._last_error:
                ownership = (
                    "clipboard write confirmed"
                    if self._clipboard_write_confirmed
                    else "clipboard not owned"
                )
                return f"{self._last_error}; {ownership}"
            return (
                "ready; clipboard write confirmed"
                if self._clipboard_write_confirmed
                else "ready; clipboard not yet written"
            )

    def _failed(self, message: str) -> PasteOutcome:
        with self._lock:
            self._last_error = message
        return PasteOutcome.failed(message)

    def close(self) -> None:
        """Fail all outstanding requests and reject future output."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
        for request in pending:
            request.outcome = PasteOutcome.failed("Linux desktop host is shutting down")
            request.completed.set()


__all__ = ["LinuxTextOutputAdapter"]
