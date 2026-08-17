"""Cross-platform lossless FLAC codec backed by system capabilities.

The Linux distribution can depend on the small ``flac`` command-line utility
instead of a platform-specific Python package.  A ``soundfile``/libsndfile
installation is also accepted when available, which keeps the adapter useful
on developer machines where the binary is not on ``PATH``.  Both paths use
WAV files as the application boundary and never silently resample or change
sample width.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


class FlacCodecUnavailable(RuntimeError):
    """Raised when neither the system FLAC nor libsndfile backend is usable."""


class SystemFlacAudioCodec:
    """Encode/decode WAV and FLAC using ``flac`` or optional libsndfile."""

    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = self._resolve_executable(executable)

    @staticmethod
    def _resolve_executable(executable: str | Path | None) -> str | None:
        if executable is not None:
            candidate = str(executable)
            # Keep explicit overrides even when the path is not present yet;
            # invocation then reports the concrete OS error and remains easy
            # to exercise with a test double.
            return candidate or None
        return shutil.which("flac") or next(
            (
                candidate
                for candidate in ("/usr/bin/flac", "/usr/local/bin/flac")
                if Path(candidate).exists()
            ),
            None,
        )

    @property
    def backend(self) -> str:
        if self.executable:
            return "flac"
        try:
            import soundfile  # type: ignore[import-not-found]

            del soundfile
            return "libsndfile"
        except Exception:
            return "unavailable"

    def _require_backend(self) -> None:
        if self.backend == "unavailable":
            raise FlacCodecUnavailable(
                "FLAC support requires the system 'flac' utility or Python "
                "soundfile backed by libsndfile"
            )

    def encode_wav_to_flac(self, source: Path, destination: Path) -> None:
        """Encode one WAV without changing its PCM representation."""

        self._run_or_soundfile(source, destination, encode=True)

    def decode_flac_to_wav(self, source: Path, destination: Path) -> None:
        """Decode one FLAC into a PCM WAV."""

        self._run_or_soundfile(source, destination, encode=False)

    def verify_round_trip(self, source: Path, encoded: Path) -> bool:
        """Verify FLAC preserves WAV parameters and PCM bytes exactly."""

        with tempfile.TemporaryDirectory(prefix="vocal-more-flac-verify-") as directory:
            decoded = Path(directory) / "decoded.wav"
            self.decode_flac_to_wav(encoded, decoded)
            return _wav_digest(source) == _wav_digest(decoded)

    # Keep both spellings available to small composition roots and diagnostics.
    verify_roundtrip = verify_round_trip
    verify_lossless_roundtrip = verify_round_trip

    def _run_or_soundfile(
        self,
        source: Path,
        destination: Path,
        *,
        encode: bool,
    ) -> None:
        self._require_backend()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.executable:
            if encode:
                command = [
                    self.executable,
                    "--force",
                    "--silent",
                    "--best",
                    "--output-name",
                    str(destination),
                    str(source),
                ]
            else:
                command = [
                    self.executable,
                    "--force",
                    "--silent",
                    "--decode",
                    "--output-name",
                    str(destination),
                    str(source),
                ]
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError as exc:
                raise RuntimeError(f"flac execution failed: {exc}") from exc
            if result.returncode != 0 or not destination.exists():
                details = (result.stderr or result.stdout or "conversion failed").strip()
                raise RuntimeError(f"flac conversion failed: {details}")
            return

        try:
            import soundfile as sf  # type: ignore[import-not-found]

            if encode:
                data, sample_rate = sf.read(str(source), dtype="int16")
                sf.write(str(destination), data, sample_rate, format="FLAC", subtype="PCM_16")
            else:
                data, sample_rate = sf.read(str(source), dtype="int16")
                sf.write(str(destination), data, sample_rate, format="WAV", subtype="PCM_16")
        except Exception as exc:
            raise RuntimeError(f"libsndfile conversion failed: {exc}") from exc
        if not destination.exists():
            raise RuntimeError("libsndfile conversion failed: output was not created")


def _wav_digest(path: Path) -> tuple[str, tuple[int, int, int, int]]:
    """Digest format parameters and PCM frames for exact verification."""

    with wave.open(str(path), "rb") as wav_file:
        parameters = (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
            wav_file.getnframes(),
        )
        digest = hashlib.sha256()
        while chunk := wav_file.readframes(65_536):
            digest.update(chunk)
    return digest.hexdigest(), parameters


# Names used by composition roots and downstream integrations.
CrossPlatformFlacCodec = SystemFlacAudioCodec
CrossPlatformLosslessAudioCodec = SystemFlacAudioCodec
LinuxFlacAudioCodec = SystemFlacAudioCodec
LinuxLosslessAudioCodec = SystemFlacAudioCodec
FlacAudioCodec = SystemFlacAudioCodec
FLACAudioCodec = SystemFlacAudioCodec


__all__ = [
    "CrossPlatformFlacCodec",
    "CrossPlatformLosslessAudioCodec",
    "FLACAudioCodec",
    "FlacAudioCodec",
    "FlacCodecUnavailable",
    "LinuxFlacAudioCodec",
    "LinuxLosslessAudioCodec",
    "SystemFlacAudioCodec",
]
