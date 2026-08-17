"""Application boundary for sending completed text to the focused app.

The desktop backends do not all have the same way to paste text.  Keeping the
operation behind this small protocol lets the Linux host use its Wayland
clipboard/Shell integration while macOS and Windows continue to use the
existing keyboard simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PasteOutcome:
    """The result of one paste request.

    ``error`` is intentionally a short diagnostic rather than the text being
    pasted.  Implementations must not put dictated text in this value because
    it can be forwarded to a UI/logging boundary.
    """

    success: bool
    error: str | None = None

    @classmethod
    def succeeded(cls) -> PasteOutcome:
        return cls(success=True)

    @classmethod
    def failed(cls, error: str) -> PasteOutcome:
        message = str(error).strip() or "paste failed"
        return cls(success=False, error=message)


@runtime_checkable
class TextOutputPort(Protocol):
    """Port used by dictation workflows to deliver final text."""

    def paste_text(self, text: str) -> PasteOutcome:
        """Paste ``text`` and report whether delivery was confirmed."""


__all__ = ["PasteOutcome", "TextOutputPort"]
