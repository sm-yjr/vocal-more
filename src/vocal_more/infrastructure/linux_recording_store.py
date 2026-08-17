"""Linux RecordingStore composition boundary."""

from __future__ import annotations

from pathlib import Path

from ..core.flac_codec import SystemFlacAudioCodec
from ..core.recording_store import RecordingStore
from ..paths import default_data_dir


def build_linux_recording_store(
    recordings_dir: str | Path | None = None,
    *,
    audio_codec: object | None = None,
    auto_compact: bool = True,
) -> RecordingStore:
    """Build a Linux store with the verified cross-platform FLAC codec.

    The directory argument is injectable for tests and for the Linux path
    migration layer.  The factory intentionally does not fall back to the
    macOS ``afconvert`` codec when FLAC is unavailable; a failed compaction
    leaves the original WAV intact and reports a clear runtime error.
    """

    directory = (
        Path(recordings_dir).expanduser()
        if recordings_dir is not None
        else default_data_dir() / "recordings"
    )
    return RecordingStore(
        recordings_dir=str(directory),
        audio_codec=audio_codec or SystemFlacAudioCodec(),
        auto_compact=auto_compact,
    )


LinuxRecordingStoreFactory = build_linux_recording_store
linux_recording_store_factory = build_linux_recording_store


__all__ = [
    "LinuxRecordingStoreFactory",
    "build_linux_recording_store",
    "linux_recording_store_factory",
]
