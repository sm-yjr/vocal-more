"""macOS AVAudioEngine capture with Apple voice processing enabled."""

from __future__ import annotations

import math
import platform
import threading
from typing import Callable, Optional

import numpy as np


class VoiceProcessingUnavailable(RuntimeError):
    """Raised when Apple voice processing cannot provide an input stream."""


class StreamingLinearResampler:
    """Convert a float32 mono stream while preserving phase across callbacks.

    AVAudioEngine's voice-processing tap runs at the hardware rate. Vocal More's
    ASR contract is fixed at 16 kHz mono, so the adapter converts the tap into
    stable application-sized blocks before entering ``AudioRecorder``.
    """

    def __init__(
        self,
        *,
        source_rate: float,
        target_rate: float,
        blocksize: int,
    ) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Audio sample rates must be positive")
        if blocksize <= 0:
            raise ValueError("Audio blocksize must be positive")
        self._source_rate = float(source_rate)
        self._target_rate = float(target_rate)
        self._step = self._source_rate / self._target_rate
        self._blocksize = int(blocksize)
        self._source_offset = 0
        self._next_source_position = 0.0
        self._tail: Optional[np.float32] = None
        self._pending = np.empty(0, dtype=np.float32)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        source = np.asarray(samples, dtype=np.float32).reshape(-1)
        if source.size == 0:
            return []

        if math.isclose(self._source_rate, self._target_rate):
            converted = source.copy()
            self._source_offset += int(source.size)
            self._next_source_position = float(self._source_offset)
            self._tail = source[-1]
        else:
            converted = self._resample(source)

        if converted.size:
            self._pending = np.concatenate((self._pending, converted))

        blocks: list[np.ndarray] = []
        while self._pending.size >= self._blocksize:
            blocks.append(self._pending[: self._blocksize].copy())
            self._pending = self._pending[self._blocksize :]
        return blocks

    def _resample(self, source: np.ndarray) -> np.ndarray:
        start = self._source_offset
        end = start + int(source.size) - 1
        if self._tail is None:
            values = source
            value_start = start
        else:
            values = np.concatenate(
                (np.asarray([self._tail], dtype=np.float32), source)
            )
            value_start = start - 1

        if self._next_source_position > end:
            converted = np.empty(0, dtype=np.float32)
        else:
            count = (
                int(
                    math.floor(
                        (end - self._next_source_position) / self._step
                        + 1e-10
                    )
                )
                + 1
            )
            positions = self._next_source_position + self._step * np.arange(
                count,
                dtype=np.float64,
            )
            source_positions = value_start + np.arange(
                values.size,
                dtype=np.float64,
            )
            converted = np.interp(positions, source_positions, values).astype(
                np.float32,
                copy=False,
            )
            self._next_source_position += count * self._step

        self._source_offset += int(source.size)
        self._tail = source[-1]
        return converted


def voice_processing_available() -> bool:
    """Return whether this process can construct AVAudioEngine voice I/O."""
    if platform.system() != "Darwin":
        return False
    version = platform.mac_ver()[0]
    if version:
        try:
            major, minor, *_ = (int(part) for part in version.split("."))
        except ValueError:
            return False
        if (major, minor) < (10, 15):
            return False
    try:
        import AVFoundation  # noqa: F401
    except Exception:
        return False
    return True


class MacOSVoiceProcessingStream:
    """Small stream facade matching the lifecycle used by ``AudioRecorder``."""

    def __init__(
        self,
        *,
        callback: Callable[[np.ndarray, int, dict, int], None],
        sample_rate: int,
        blocksize: int,
    ) -> None:
        self._callback = callback
        self._sample_rate = int(sample_rate)
        self._blocksize = int(blocksize)
        self._engine = None
        self._input_node = None
        self._resampler: Optional[StreamingLinearResampler] = None
        self._started = False
        self._stopped = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stopped.clear()

        try:
            import AVFoundation
        except ImportError as exc:
            raise VoiceProcessingUnavailable(
                "PyObjC AVFoundation bridge is unavailable"
            ) from exc

        engine = AVFoundation.AVAudioEngine.alloc().init()
        input_node = engine.inputNode()
        enabled, error = _objc_result(
            input_node.setVoiceProcessingEnabled_error_(True, None)
        )
        if not enabled:
            raise VoiceProcessingUnavailable(
                f"Apple voice processing could not be enabled: {error}"
            )

        # Vocal More owns low-voice gain and limiting. Keep Apple's AEC/noise
        # processing while avoiding a second automatic gain controller.
        set_agc = getattr(input_node, "setVoiceProcessingAGCEnabled_", None)
        if callable(set_agc):
            set_agc(False)

        source_format = input_node.outputFormatForBus_(0)
        source_rate = float(source_format.sampleRate())
        source_channels = int(source_format.channelCount())
        if source_rate <= 0 or source_channels <= 0:
            _disable_voice_processing(input_node)
            raise VoiceProcessingUnavailable(
                f"Apple voice processing returned an invalid format: {source_format}"
            )

        resampler = StreamingLinearResampler(
            source_rate=source_rate,
            target_rate=self._sample_rate,
            blocksize=self._blocksize,
        )

        def tap(buffer, _when) -> None:
            if self._stopped.is_set():
                return
            try:
                frames = int(buffer.frameLength())
                if frames <= 0:
                    return
                channels = buffer.floatChannelData()
                # VoiceProcessingIO exposes its processed mono uplink on the
                # first channel. Some Mac models duplicate it across the
                # device's aggregate channel layout.
                source = np.asarray(channels[0][:frames], dtype=np.float32).copy()
                for block in resampler.push(source):
                    if self._stopped.is_set():
                        return
                    mono = block.reshape(-1, 1)
                    self._callback(mono, len(block), {}, 0)
            except Exception as exc:
                # Never unwind through Apple's realtime callback thread.
                print(f"[AudioRecorder] Voice-processing callback failed: {exc}")

        native_buffer_size = max(
            1,
            int(round(self._blocksize * source_rate / self._sample_rate)),
        )
        try:
            input_node.installTapOnBus_bufferSize_format_block_(
                0,
                native_buffer_size,
                None,
                tap,
            )
            engine.prepare()
            started, error = _objc_result(engine.startAndReturnError_(None))
            if not started:
                raise VoiceProcessingUnavailable(
                    f"Apple voice-processing engine failed to start: {error}"
                )
        except Exception:
            try:
                input_node.removeTapOnBus_(0)
            except Exception:
                pass
            try:
                engine.stop()
            except Exception:
                pass
            _disable_voice_processing(input_node)
            raise

        with self._lock:
            self._engine = engine
            self._input_node = input_node
            self._resampler = resampler
            self._started = True

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            engine = self._engine
            input_node = self._input_node
            self._engine = None
            self._input_node = None
            self._resampler = None
            self._started = False

        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if input_node is not None:
            try:
                input_node.removeTapOnBus_(0)
            except Exception:
                pass
            _disable_voice_processing(input_node)

    def abort(self) -> None:
        self.stop()

    def close(self) -> None:
        self.stop()


def _objc_result(result) -> tuple[bool, object]:
    if isinstance(result, tuple):
        value = bool(result[0]) if result else False
        error = result[1] if len(result) > 1 else None
        return value, error
    return bool(result), None


def _disable_voice_processing(input_node) -> None:
    try:
        input_node.setVoiceProcessingEnabled_error_(False, None)
    except Exception:
        pass
