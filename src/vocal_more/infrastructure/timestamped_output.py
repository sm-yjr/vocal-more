"""Helpers for prefixing stderr/stdout log lines with wall-clock timestamps."""

from datetime import datetime
import io
import sys
from typing import TextIO


class TimestampedTextStream(io.TextIOBase):
    """Wrap a text stream and prefix each completed line with a timestamp."""

    def __init__(self, target: TextIO):
        self._target = target
        self._buffer = ""
        self._vocal_more_timestamped = True

    def write(self, data: str) -> int:
        if not data:
            return 0

        pending = self._buffer + data
        self._buffer = ""
        written = len(data)

        for chunk in pending.splitlines(keepends=True):
            if chunk.endswith("\n"):
                self._target.write(f"{self._prefix()}{chunk}")
            else:
                self._buffer = chunk

        return written

    def flush(self) -> None:
        if self._buffer:
            self._target.write(f"{self._prefix()}{self._buffer}")
            self._buffer = ""
        self._target.flush()

    def fileno(self) -> int:
        return self._target.fileno()

    def isatty(self) -> bool:
        return self._target.isatty()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:
        return self._target.encoding

    @property
    def errors(self) -> str | None:
        return self._target.errors

    @staticmethod
    def _prefix() -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return f"[{timestamp}] "


def install_timestamped_stream(stream_name: str) -> None:
    """Wrap stdout/stderr once so plain print() logs become timestamped."""
    stream = getattr(sys, stream_name)
    if getattr(stream, "_vocal_more_timestamped", False):
        return
    setattr(sys, stream_name, TimestampedTextStream(stream))
