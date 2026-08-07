"""Windows composition root for the shared JSON-RPC dictation runtime."""

from __future__ import annotations

from collections.abc import Callable

from .bootstrap import build_rpc_handler_dependencies
from .core.recording_store import RecordingStore
from .core.text_polisher import TextPolisher
from .modes.meeting import MeetingMode
from .modes.realtime_long import RealtimeLongMode
from .modes.walkie_talkie import WalkieTalkieMode
from .paths import default_data_dir
from .rpc_handler import RPCHandler


def build_windows_recording_store() -> RecordingStore:
    """Store Windows recordings with the rest of the per-user application data.

    The existing lossless archive codec is backed by macOS ``afconvert``. Keep
    Windows history as WAV until a verified cross-platform codec is introduced,
    rather than scheduling a background job that cannot succeed.
    """
    return RecordingStore(
        recordings_dir=str(default_data_dir() / "recordings"),
        auto_compact=False,
    )


class WindowsRPCHandler(RPCHandler):
    """RPC handler with a Windows-specific recording repository factory."""

    def __init__(self, send_notification: Callable[[str, dict], None]) -> None:
        self._send_notification = send_notification
        dependencies = build_rpc_handler_dependencies(
            self,
            send_notification=send_notification,
            text_polisher_factory=TextPolisher,
            recording_store_factory=build_windows_recording_store,
            walkie_talkie_factory=WalkieTalkieMode,
            realtime_long_factory=RealtimeLongMode,
            meeting_factory=MeetingMode,
        )
        self._apply_dependencies(dependencies)


__all__ = ["WindowsRPCHandler", "build_windows_recording_store"]
