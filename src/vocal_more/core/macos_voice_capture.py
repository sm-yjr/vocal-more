"""macOS AVAudioEngine capture with Apple voice processing enabled."""

from __future__ import annotations

import platform
import threading
from typing import Callable, Optional

import numpy as np


class VoiceProcessingUnavailable(RuntimeError):
    """Raised when Apple voice processing cannot provide an input stream."""

    def __init__(
        self,
        details: str,
        *,
        code: str = "voice_processing_unavailable",
        stage: str = "voice_processing",
    ) -> None:
        super().__init__(details)
        self.code = code
        self.stage = stage


class FixedBlockFIFO:
    """Emit stable application blocks and preserve the final partial block."""

    def __init__(self, blocksize: int) -> None:
        if blocksize <= 0:
            raise ValueError("Audio blocksize must be positive")
        self._blocksize = int(blocksize)
        self._pending = np.empty(0, dtype=np.float32)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        source = np.asarray(samples, dtype=np.float32).reshape(-1)
        if source.size:
            self._pending = np.concatenate((self._pending, source))
        blocks: list[np.ndarray] = []
        while self._pending.size >= self._blocksize:
            blocks.append(self._pending[: self._blocksize].copy())
            self._pending = self._pending[self._blocksize :]
        return blocks

    def finish(self) -> list[np.ndarray]:
        if not self._pending.size:
            return []
        tail = self._pending.copy()
        self._pending = np.empty(0, dtype=np.float32)
        return [tail]


class AVAudioConverterSRC:
    """Continuous mono Float32 SRC backed by Apple's AVAudioConverter."""

    def __init__(
        self,
        *,
        av_module,
        source_rate: float,
        target_rate: float,
        max_input_frames: int,
        output_capacity: int,
    ) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Audio sample rates must be positive")
        self._av = av_module
        self._source_format = (
            av_module.AVAudioFormat.alloc()
            .initStandardFormatWithSampleRate_channels_(float(source_rate), 1)
        )
        self._target_format = (
            av_module.AVAudioFormat.alloc()
            .initStandardFormatWithSampleRate_channels_(float(target_rate), 1)
        )
        self._converter = (
            av_module.AVAudioConverter.alloc()
            .initFromFormat_toFormat_(self._source_format, self._target_format)
        )
        if self._converter is None:
            raise VoiceProcessingUnavailable(
                "Apple audio sample-rate converter could not be created"
            )
        set_quality = getattr(
            self._converter,
            "setSampleRateConverterQuality_",
            None,
        )
        if callable(set_quality):
            set_quality(av_module.AVAudioQualityHigh)
        self._input_capacity = max(1, int(max_input_frames))
        self._input_buffer = self._new_pcm_buffer(
            self._source_format,
            self._input_capacity,
        )
        self._output_buffer = self._new_pcm_buffer(
            self._target_format,
            max(1, int(output_capacity)),
        )
        self._finished = False

    def _new_pcm_buffer(self, audio_format, capacity: int):
        buffer = (
            self._av.AVAudioPCMBuffer.alloc()
            .initWithPCMFormat_frameCapacity_(audio_format, int(capacity))
        )
        if buffer is None:
            raise VoiceProcessingUnavailable(
                "Apple audio converter buffer allocation failed"
            )
        return buffer

    def push(self, samples: np.ndarray) -> np.ndarray:
        if self._finished:
            raise RuntimeError("Cannot push audio after AVAudioConverter EOS")
        source = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not source.size:
            return np.empty(0, dtype=np.float32)
        if source.size > self._input_capacity:
            self._input_capacity = int(source.size)
            self._input_buffer = self._new_pcm_buffer(
                self._source_format,
                self._input_capacity,
            )

        input_view = _float_channel_view(self._input_buffer, int(source.size))
        input_view[:] = source
        self._input_buffer.setFrameLength_(int(source.size))
        supplied = False

        def provider(_requested_packets, _status_placeholder):
            nonlocal supplied
            if not supplied:
                supplied = True
                return (
                    self._input_buffer,
                    self._av.AVAudioConverterInputStatus_HaveData,
                )
            return None, self._av.AVAudioConverterInputStatus_NoDataNow

        return self._convert_until_blocked(provider, finishing=False)

    def finish(self) -> np.ndarray:
        if self._finished:
            return np.empty(0, dtype=np.float32)

        def provider(_requested_packets, _status_placeholder):
            return None, self._av.AVAudioConverterInputStatus_EndOfStream

        converted = self._convert_until_blocked(provider, finishing=True)
        self._finished = True
        return converted

    def _convert_until_blocked(self, provider, *, finishing: bool) -> np.ndarray:
        chunks: list[np.ndarray] = []
        while True:
            self._output_buffer.setFrameLength_(0)
            status, error = (
                self._converter.convertToBuffer_error_withInputFromBlock_(
                    self._output_buffer,
                    None,
                    provider,
                )
            )
            frame_count = int(self._output_buffer.frameLength())
            if frame_count:
                chunks.append(
                    _float_channel_view(self._output_buffer, frame_count).copy()
                )
            if error is not None or status == self._av.AVAudioConverterOutputStatus_Error:
                raise VoiceProcessingUnavailable(
                    f"Apple audio sample-rate conversion failed: {error}"
                )
            if status == self._av.AVAudioConverterOutputStatus_HaveData:
                continue
            if finishing and status == self._av.AVAudioConverterOutputStatus_EndOfStream:
                break
            if not finishing and status == self._av.AVAudioConverterOutputStatus_InputRanDry:
                break
            raise VoiceProcessingUnavailable(
                f"Unexpected Apple audio converter status: {status}"
            )
        if not chunks:
            return np.empty(0, dtype=np.float32)
        if len(chunks) == 1:
            return chunks[0]
        return np.concatenate(chunks)


class StreamingAudioConverter:
    """Convert native-rate mono audio into fixed 16 kHz application blocks."""

    def __init__(
        self,
        *,
        source_rate: float,
        target_rate: float,
        blocksize: int,
        max_input_frames: int,
        av_module,
    ) -> None:
        self._same_rate = abs(float(source_rate) - float(target_rate)) < 1e-6
        self._fifo = FixedBlockFIFO(blocksize)
        self._source = None
        if not self._same_rate:
            ratio = float(target_rate) / float(source_rate)
            output_capacity = max(
                int(blocksize) * 2,
                int(np.ceil(max_input_frames * ratio)) + int(blocksize),
            )
            self._source = AVAudioConverterSRC(
                av_module=av_module,
                source_rate=source_rate,
                target_rate=target_rate,
                max_input_frames=max_input_frames,
                output_capacity=output_capacity,
            )
        self._finished = False

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        if self._finished:
            return []
        source = np.asarray(samples, dtype=np.float32).reshape(-1)
        converted = source.copy() if self._same_rate else self._source.push(source)
        return self._fifo.push(converted)

    def finish(self) -> list[np.ndarray]:
        if self._finished:
            return []
        self._finished = True
        blocks: list[np.ndarray] = []
        if self._source is not None:
            blocks.extend(self._fifo.push(self._source.finish()))
        blocks.extend(self._fifo.finish())
        return blocks


def _float_channel_view(buffer, frame_count: int) -> np.ndarray:
    channel = buffer.floatChannelData()[0]
    as_buffer = getattr(channel, "as_buffer", None)
    if callable(as_buffer):
        return np.frombuffer(as_buffer(frame_count), dtype=np.float32)
    return np.asarray(channel[:frame_count], dtype=np.float32)


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
    # Official bundles prefer the Objective-C++ runtime. This ABI probe is
    # static: it loads the dylib but neither creates an engine nor touches TCC.
    from .native_audio_capture import native_audio_library_available

    if native_audio_library_available():
        return True
    try:
        import AVFoundation
    except Exception:
        return False
    return _voice_processing_api_available(AVFoundation)


def _voice_processing_api_available(av_module) -> bool:
    """Check required selectors without constructing an engine or opening I/O."""
    input_node_class = getattr(av_module, "AVAudioInputNode", None)
    if input_node_class is None:
        return False
    return all(
        hasattr(input_node_class, selector)
        for selector in (
            "setVoiceProcessingEnabled_error_",
            "isVoiceProcessingEnabled",
            "setVoiceProcessingAGCEnabled_",
            "isVoiceProcessingAGCEnabled",
        )
    )


class MacOSVoiceProcessingStream:
    """Small stream facade matching the lifecycle used by ``AudioRecorder``."""

    backend_name = "pyobjc"
    delivers_processed_pcm = False

    def __init__(
        self,
        *,
        callback: Callable[[np.ndarray, int, dict, int], None],
        sample_rate: int,
        blocksize: int,
        automatic_gain: bool,
    ) -> None:
        self._callback = callback
        self._sample_rate = int(sample_rate)
        self._blocksize = int(blocksize)
        self._automatic_gain = bool(automatic_gain)
        self._engine = None
        self._input_node = None
        self._av_module = None
        self._resampler: Optional[StreamingAudioConverter] = None
        self._started = False
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._conversion_lock = threading.Lock()
        self._source_sample_rate_hz = 0.0
        self._source_channels = 0
        self._observed_agc: Optional[bool] = None
        self._observed_voice_processing: Optional[bool] = None
        self._start_verified = False
        self._diagnostics_fresh = False
        self._preferred_microphone_mode: Optional[str] = None
        self._active_microphone_mode: Optional[str] = None
        self._runtime_fault_count = 0
        self._runtime_fault_code: Optional[str] = None

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

        # AEC/noise processing and AGC are configured as one explicit native
        # stream plan. Verification is mandatory: an uncertain AGC state could
        # otherwise stack Apple's level control with Vocal More's software gain.
        try:
            _configure_voice_processing_agc(
                input_node,
                enabled=self._automatic_gain,
            )
        except Exception:
            _disable_voice_processing(input_node)
            raise

        source_format = input_node.outputFormatForBus_(0)
        source_rate = float(source_format.sampleRate())
        source_channels = int(source_format.channelCount())
        if source_rate <= 0 or source_channels <= 0:
            _disable_voice_processing(input_node)
            raise VoiceProcessingUnavailable(
                f"Apple voice processing returned an invalid format: {source_format}"
            )

        native_buffer_size = max(
            1,
            int(round(self._blocksize * source_rate / self._sample_rate)),
        )
        resampler = StreamingAudioConverter(
            source_rate=source_rate,
            target_rate=self._sample_rate,
            blocksize=self._blocksize,
            max_input_frames=native_buffer_size,
            av_module=AVFoundation,
        )

        def tap(buffer, _when) -> None:
            if self._stopped.is_set():
                return
            try:
                with self._conversion_lock:
                    if self._stopped.is_set():
                        return
                    frames = int(buffer.frameLength())
                    if frames <= 0:
                        return
                    # VoiceProcessingIO exposes its processed mono uplink on
                    # channel zero. Copy before Apple's buffer is recycled.
                    source = _float_channel_view(buffer, frames).copy()
                    blocks = resampler.push(source)
                    # Emit while owning the converter sequence. drain() first
                    # marks the stream stopped, then waits here, so a tap that
                    # was already in flight cannot strand completed blocks.
                    for block in blocks:
                        self._emit_block(block)
            except Exception as exc:
                # Never unwind through Apple's realtime callback thread.
                self._runtime_fault_count += 1
                self._runtime_fault_code = "callback_processing_failed"
                print(f"[AudioRecorder] Voice-processing callback failed: {exc}")

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
            observed_voice_processing, observed_agc = (
                _verify_running_voice_processing(
                    input_node,
                    automatic_gain=self._automatic_gain,
                )
            )
            from .macos_microphone_mode import macos_microphone_modes

            preferred_microphone_mode, active_microphone_mode = (
                macos_microphone_modes(
                    av_module=AVFoundation,
                    system="Darwin",
                )
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
            self._av_module = AVFoundation
            self._resampler = resampler
            self._source_sample_rate_hz = source_rate
            self._source_channels = source_channels
            self._observed_voice_processing = observed_voice_processing
            self._observed_agc = observed_agc
            self._start_verified = True
            self._diagnostics_fresh = True
            self._preferred_microphone_mode = preferred_microphone_mode
            self._active_microphone_mode = active_microphone_mode
            self._started = True

    @property
    def diagnostics(self) -> dict:
        return {
            "backend": self.backend_name,
            "source_sample_rate_hz": self._source_sample_rate_hz,
            "source_channels": self._source_channels,
            "voice_processing_enabled": self._observed_voice_processing,
            "agc_enabled": self._observed_agc,
            "start_verified": self._start_verified,
            "diagnostics_fresh": self._diagnostics_fresh,
            "converter_name": "AVAudioConverter",
            "dropped_blocks": 0,
            "runtime_fault_count": self._runtime_fault_count,
            "runtime_fault_code": self._runtime_fault_code,
            "preferred_microphone_mode": self._preferred_microphone_mode,
            "active_microphone_mode": self._active_microphone_mode,
        }

    def _emit_block(self, block: np.ndarray) -> None:
        mono = np.asarray(block, dtype=np.float32).reshape(-1, 1)
        self._callback(mono, len(mono), {}, 0)

    def drain(self) -> None:
        """Synchronously emit converter/FIFO tail before recorder snapshot."""
        self._stopped.set()
        from .macos_microphone_mode import macos_microphone_modes

        av_module = self._av_module
        if av_module is not None:
            end_preferred, end_active = macos_microphone_modes(
                av_module=av_module,
                system="Darwin",
            )
            if not (
                self._preferred_microphone_mode is not None
                and self._active_microphone_mode is not None
                and end_preferred == self._preferred_microphone_mode
                and end_active == self._active_microphone_mode
            ):
                # Unknown or changing system processing makes an AGC-only A/B
                # attribution invalid. Preserve audio but fail closed in the
                # runtime metadata consumed by the capture protocol.
                self._preferred_microphone_mode = None
                self._active_microphone_mode = None
        with self._conversion_lock:
            with self._lock:
                resampler = self._resampler
            blocks = resampler.finish() if resampler is not None else []
        for block in blocks:
            self._emit_block(block)

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
                self._runtime_fault_count += 1
                self._runtime_fault_code = "pyobjc_engine_stop_failed"
        if input_node is not None:
            try:
                input_node.removeTapOnBus_(0)
            except Exception:
                self._runtime_fault_count += 1
                self._runtime_fault_code = "pyobjc_remove_tap_failed"
            if not _disable_voice_processing(input_node):
                self._runtime_fault_count += 1
                self._runtime_fault_code = (
                    "pyobjc_disable_voice_processing_failed"
                )

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


def _configure_voice_processing_agc(input_node, *, enabled: bool) -> None:
    """Set and verify VoiceProcessingIO automatic gain control."""
    setter = getattr(input_node, "setVoiceProcessingAGCEnabled_", None)
    getter = getattr(input_node, "isVoiceProcessingAGCEnabled", None)
    if not callable(setter) or not callable(getter):
        raise VoiceProcessingUnavailable(
            "Apple voice-processing AGC controls are unavailable"
        )

    setter(bool(enabled))
    actual = bool(getter())
    if actual != bool(enabled):
        requested = "enabled" if enabled else "disabled"
        actual_label = "enabled" if actual else "disabled"
        raise VoiceProcessingUnavailable(
            "Apple voice-processing AGC could not be configured: "
            f"requested {requested}, observed {actual_label}"
        )


def _verify_running_voice_processing(
    input_node,
    *,
    automatic_gain: bool,
) -> tuple[bool, bool]:
    """Read back VoiceProcessingIO after the engine is actually running."""

    def observed(selector: str) -> bool:
        value = getattr(input_node, selector, None)
        if value is None:
            raise VoiceProcessingUnavailable(
                f"Apple voice-processing running state lacks {selector}"
            )
        return bool(value() if callable(value) else value)

    voice_processing = observed("isVoiceProcessingEnabled")
    agc = observed("isVoiceProcessingAGCEnabled")
    if not voice_processing or agc != bool(automatic_gain):
        raise VoiceProcessingUnavailable(
            "Apple voice-processing running state does not match the requested plan"
        )
    return voice_processing, agc


def _disable_voice_processing(input_node) -> bool:
    try:
        disabled, _error = _objc_result(
            input_node.setVoiceProcessingEnabled_error_(False, None)
        )
        return bool(disabled)
    except Exception:
        return False


def build_voice_processing_stream(
    *,
    callback,
    pcm_callback,
    sample_rate: int,
    blocksize: int,
    automatic_gain: bool,
    gain: float,
    highpass_filter: bool,
    highpass_freq: int,
    soft_limiter: bool,
):
    """Select the no-GIL native runtime when its static ABI probe succeeds.

    The recorder retries :func:`build_pyobjc_voice_processing_stream` if this
    native candidate later fails during route-specific engine startup.
    """
    from .native_audio_capture import (
        build_native_voice_processing_stream,
        native_audio_library_available,
    )

    if native_audio_library_available():
        return build_native_voice_processing_stream(
            pcm_callback=pcm_callback,
            sample_rate=sample_rate,
            blocksize=blocksize,
            automatic_gain=automatic_gain,
            gain=gain,
            highpass_filter=highpass_filter,
            highpass_freq=highpass_freq,
            soft_limiter=soft_limiter,
        )
    return build_pyobjc_voice_processing_stream(
        callback=callback,
        sample_rate=sample_rate,
        blocksize=blocksize,
        automatic_gain=automatic_gain,
    )


def build_pyobjc_voice_processing_stream(
    *,
    callback,
    sample_rate: int,
    blocksize: int,
    automatic_gain: bool,
    **_ignored,
):
    """Construct the compatibility Apple Voice Processing implementation."""
    return MacOSVoiceProcessingStream(
        callback=callback,
        sample_rate=sample_rate,
        blocksize=blocksize,
        automatic_gain=automatic_gain,
    )
