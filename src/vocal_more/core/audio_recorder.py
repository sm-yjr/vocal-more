"""Audio recording module using sounddevice."""

import io
import os
from collections import deque
from pathlib import Path
import platform
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from ..config import get_config
from ..domain.audio_contract import OUTPUT_CHANNELS, OUTPUT_SAMPLE_RATE_HZ
from .macos_audio_diagnostics import (
    microphone_permission_status,
    request_microphone_access,
)
from .macos_voice_capture import VoiceProcessingUnavailable
from .native_audio_capture import NativeAudioUnavailable

_PORTAUDIO_RECOVERY_MARKERS = (
    "-9986",
    "-10851",
    "painternalerror",
    "auhal",
    "invalidpropertyvalue",
)
_PORTAUDIO_RESET_LOCK = threading.Lock()
_STREAM_RELEASE_THREAD_NAME = "vocal-more-audio-stream-release"
_STREAM_START_THREAD_NAME = "vocal-more-audio-stream-start"
_NATIVE_DRAIN_THREAD_NAME = "vocal-more-native-audio-drain"
_DEFAULT_START_TIMEOUT_SECONDS = 3.0
_NATIVE_DRAIN_TIMEOUT_SECONDS = 0.5
_MAX_MACOS_ARRAY_CHANNELS = 3
_MACOS_ARRAY_DEVICE_MARKERS = (
    "macbook",
    "imac",
    "studio display",
    "built-in microphone",
    "internal microphone",
    "内建麦克风",
    "内置麦克风",
    "麦克风阵列",
)
_MACOS_LOW_LATENCY_ROUTE_MARKERS = (
    "studio display",
)


class _StartupAudioGate:
    """Hold early candidate frames until stream acceptance succeeds.

    AVAudioEngine and PortAudio may invoke a callback before ``start()`` returns.
    Publishing immediately would expose audio from a candidate that subsequently
    fails or loses a recovery race. The gate keeps a small bounded queue,
    preserves order while it is flushed, and drops rejected candidates.
    """

    _MAX_PENDING_EVENTS = 64

    def __init__(self, *, float_callback, pcm_callback) -> None:
        self._float_callback = float_callback
        self._pcm_callback = pcm_callback
        self._lock = threading.Lock()
        self._state = "pending"
        self._pending: deque[tuple[str, tuple]] = deque()
        self._dropped = 0

    def float_callback(self, indata, frames, time_info, status) -> None:
        # PyObjC's converter output is reusable; retain an owned copy while the
        # engine's getter state is still provisional.
        event = (
            "float",
            (
                np.asarray(indata, dtype=np.float32).copy(),
                int(frames),
                # AudioRecorder does not consume timing metadata. PortAudio
                # supplies a CFFI struct that is not necessarily dict-convertible.
                {},
                status,
            ),
        )
        self._submit(event)

    def pcm_callback(self, pcm: bytes, rms: float) -> None:
        self._submit(("pcm", (bytes(pcm), float(rms))))

    def _submit(self, event: tuple[str, tuple]) -> None:
        with self._lock:
            state = self._state
            if state in {"pending", "flushing"}:
                if len(self._pending) >= self._MAX_PENDING_EVENTS:
                    self._dropped += 1
                    return
                self._pending.append(event)
                return
            if state == "rejected":
                return
        self._dispatch(event)

    def activate(self) -> tuple[int, int]:
        """Publish queued events in order and return drop/error counts."""
        errors = 0
        while True:
            with self._lock:
                if self._state == "rejected":
                    return self._dropped, errors
                self._state = "flushing"
                if not self._pending:
                    self._state = "active"
                    return self._dropped, errors
                events = list(self._pending)
                self._pending.clear()
            for event in events:
                try:
                    self._dispatch(event)
                except Exception as exc:
                    errors += 1
                    print(
                        "[AudioRecorder] Failed to publish verified startup "
                        f"audio: {exc}"
                    )

    def reject(self) -> None:
        with self._lock:
            self._state = "rejected"
            self._pending.clear()

    def _dispatch(self, event: tuple[str, tuple]) -> None:
        kind, arguments = event
        if kind == "pcm":
            self._pcm_callback(*arguments)
        else:
            self._float_callback(*arguments)


class _VerifiedStreamCandidate:
    """A running capture stream whose provisional callbacks need release."""

    def __init__(self, stream, gate: _StartupAudioGate) -> None:
        self.stream = stream
        self.gate = gate


def _macos_voice_processing_available() -> bool:
    from .macos_voice_capture import voice_processing_available

    return voice_processing_available()


def _build_macos_voice_processing_stream(**kwargs):
    from .macos_voice_capture import build_voice_processing_stream

    return build_voice_processing_stream(**kwargs)


def _build_pyobjc_voice_processing_stream(**kwargs):
    from .macos_voice_capture import build_pyobjc_voice_processing_stream

    return build_pyobjc_voice_processing_stream(**kwargs)


class AudioRecorderStartError(RuntimeError):
    """Raised when microphone startup fails after all recovery attempts."""

    def __init__(
        self,
        details: str,
        *,
        device_change_detected: bool = False,
        startup_timed_out: bool = False,
        code: str = "unknown",
        stage: str = "startup",
        recoverable: bool = True,
    ):
        super().__init__(details)
        self.device_change_detected = device_change_detected
        self.startup_timed_out = startup_timed_out
        self.code = code
        self.stage = stage
        self.recoverable = recoverable


class AudioRecorder:
    """Audio recorder using sounddevice for real-time audio capture."""

    def __init__(
        self,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
        blocksize: Optional[int] = None,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        device: Optional[str] = None,
        start_timeout: float = _DEFAULT_START_TIMEOUT_SECONDS,
    ):
        """Initialize the audio recorder.

        Args:
            sample_rate: Sample rate in Hz (default from config: 16000)
            channels: Number of audio channels (default from config: 1)
            blocksize: Number of frames per buffer (default from config: 640)
            on_audio_chunk: Callback for real-time audio chunks
            on_audio_level: Callback for real-time audio RMS level (0.0~1.0)
            device: Input device name (None = from config, then system default)
            start_timeout: Hard deadline for opening CoreAudio/PortAudio. Native
                calls that exceed it are abandoned on an isolated daemon thread.
        """
        config = get_config()
        # Source hardware is negotiated at its native rate and converted by
        # AVAudioConverter/PortAudio. Downstream PCM has one invariant format.
        self.sample_rate = OUTPUT_SAMPLE_RATE_HZ
        # ASR/WAV output is always mono. Keep the requested device topology as
        # a separate value so a stereo/array capture can still be coherently
        # downmixed without corrupting downstream frame accounting.
        self.channels = OUTPUT_CHANNELS
        self.capture_channels = max(
            1,
            int(
                channels
                if channels is not None
                else config.audio.capture_channels
            ),
        )
        self.blocksize = blocksize or config.audio.blocksize
        self.on_audio_chunk = on_audio_chunk
        self._on_audio_level = on_audio_level
        self._use_config_device = device is None
        self._device_name: Optional[str] = device if device is not None else config.audio.input_device
        self._gain_mode: str = config.audio.gain_mode
        self._gain: float = config.audio.gain
        self._apple_agc_active = False
        self._highpass_filter: bool = config.audio.highpass_filter
        self._soft_limiter: bool = config.audio.soft_limiter
        self._start_timeout = max(0.01, float(start_timeout))
        self._microphone_permission_requested = False

        self._stream: Optional[sd.InputStream] = None
        self._stream_release_thread: Optional[threading.Thread] = None
        self._stream_start_thread: Optional[threading.Thread] = None
        self._start_generation = 0
        self._array_processing_active = False
        self._last_input_session: Optional[dict] = None
        # Construction happens on the app dependency bootstrap worker. Core
        # Audio enumeration can wedge during a route transition, so initial
        # status is intentionally I/O-free; explicit idle inspection or the
        # bounded start worker replaces it with observed facts.
        self._input_status = self._initial_input_status()
        self._audio_buffer: list[bytes] = []
        self._is_recording = False
        self._is_stopping = False
        self._lock = threading.Lock()
        # Runtime/RPC updates may arrive during a capture even when the settings
        # UI is closed. Keep one utterance's device, AGC and DSP plan immutable;
        # pending values are applied atomically at the next start boundary.
        self._pending_capture_config: dict[str, object] = {}
        # Serializes buffer acceptance with application observer publication.
        # on_audio_chunk is contractually a fast queue handoff, never network
        # I/O; stop waits for an in-flight handoff so returned PCM and ASR input
        # describe the same prefix.
        self._observer_lock = threading.RLock()
        replay_path = os.environ.get("VOCAL_MORE_BENCHMARK_AUDIO_FILE", "").strip()
        trace_dir = os.environ.get("VOCAL_MORE_BENCHMARK_TRACE_DIR", "").strip()
        self._benchmark_replay_path = (
            Path(replay_path).expanduser().resolve()
            if replay_path and trace_dir
            else None
        )
        self._benchmark_replay_thread: Optional[threading.Thread] = None
        self._benchmark_replay_done = threading.Event()
        self._benchmark_replay_stop = threading.Event()

        # High-pass filter state (1st-order IIR)
        # α = 1 / (1 + 2π·fc/fs)
        self._hp_freq: int = config.audio.highpass_freq
        self._hp_alpha = self._calc_hp_alpha(self._hp_freq)
        self._hp_prev_in = 0.0
        self._hp_prev_out = 0.0

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info: dict, status: sd.CallbackFlags
    ) -> None:
        """Callback for audio stream."""
        with self._lock:
            is_recording = self._is_recording

        if not is_recording:
            return

        status_fault = self._portaudio_status_fault_code(status)

        highpass_filter = self._highpass_filter
        apple_agc_active = self._apple_agc_active
        gain = 1.0 if apple_agc_active else self._gain
        # Apple AGC already owns level control and saturation behavior. Running
        # the software limiter as a second nonlinear stage damages consonants.
        soft_limiter = False if apple_agc_active else self._soft_limiter
        on_audio_chunk = self.on_audio_chunk
        on_audio_level = self._on_audio_level

        # A built-in Mac input may expose two or three logical channels. Public
        # Core Audio metadata does not prove that they are independent physical
        # capsules, so treat them only as signals that can be coherently mixed.
        # The ASR wire format remains mono. This also fixes configured stereo
        # devices producing invalid interleaved data when HPF is disabled.
        mono_input = self._coherent_array_downmix(indata)

        # 1. High-pass filter: remove low-frequency rumble (fans, hum, plosives)
        if highpass_filter:
            samples = mono_input[:, 0].tolist()
            alpha = self._hp_alpha
            prev_in = self._hp_prev_in
            prev_out = self._hp_prev_out
            out = [0.0] * len(samples)
            for i, x in enumerate(samples):
                prev_out = alpha * (prev_out + x - prev_in)
                prev_in = x
                out[i] = prev_out
            self._hp_prev_in = prev_in
            self._hp_prev_out = prev_out
            filtered = np.asarray(out, dtype=np.float32).reshape(-1, 1)
        else:
            filtered = mono_input

        # 2. Compute RMS on filtered signal
        rms = float(np.sqrt(np.mean(filtered ** 2)))

        # 3. Gain + limiter
        if gain != 1.0:
            if soft_limiter:
                processed = np.tanh(filtered * gain)
            else:
                processed = np.clip(filtered * gain, -1.0, 1.0)
        else:
            processed = filtered

        # Convert float32 to int16 PCM
        # Band-limited native SRC may overshoot source peaks slightly. Clip at
        # the integer boundary even when no gain stage is active so float-to-
        # int conversion cannot wrap +1.001 into a large negative sample.
        audio_data = (
            np.clip(processed, -1.0, 1.0) * 32767
        ).astype(np.int16).tobytes()

        with self._observer_lock:
            with self._lock:
                if not self._is_recording:
                    return
                self._audio_buffer.append(audio_data)

            if status_fault is not None:
                # Attribute flags only after this block crosses the same
                # acceptance barrier as PCM. A late block rejected by stop is
                # not part of the completed session. Never log on this path.
                self._record_recorder_fault(status_fault)

            # These observers must remain fast, non-blocking queue/UI handoffs.
            # The serialization boundary makes stop's PCM snapshot consistent
            # with the exact chunks already accepted by ASR.
            if on_audio_chunk:
                try:
                    on_audio_chunk(audio_data)
                except Exception:
                    self._record_recorder_fault("audio_chunk_observer_failed")

            if on_audio_level:
                if gain != 1.0 and rms > 0:
                    rms = min(1.0, rms * gain)
                try:
                    on_audio_level(rms)
                except Exception:
                    self._record_recorder_fault("audio_level_observer_failed")

    def _native_pcm_callback(self, audio_data: bytes, rms: float) -> None:
        """Accept PCM produced by the native worker, never the realtime tap."""
        with self._observer_lock:
            with self._lock:
                if not self._is_recording:
                    return
                self._audio_buffer.append(audio_data)
                on_audio_chunk = self.on_audio_chunk
                on_audio_level = self._on_audio_level
            if on_audio_chunk:
                try:
                    on_audio_chunk(audio_data)
                except Exception:
                    self._record_recorder_fault("audio_chunk_observer_failed")
            if on_audio_level:
                try:
                    on_audio_level(max(0.0, min(1.0, float(rms))))
                except Exception:
                    self._record_recorder_fault("audio_level_observer_failed")

    def _record_recorder_fault(self, code: str) -> None:
        """Record an application-boundary failure without escaping a callback."""
        with self._lock:
            self._input_status["recorder_fault_count"] = (
                int(self._input_status.get("recorder_fault_count") or 0) + 1
            )
            self._input_status["recorder_fault_code"] = code
            self._input_status["runtime_fault_count"] = (
                int(self._input_status.get("runtime_fault_count") or 0) + 1
            )
            self._input_status["runtime_fault_code"] = code
            self._input_status["gain_control_verified"] = False

    def start(self) -> None:
        """Start recording audio.

        Raises:
            AudioRecorderStartError: If no audio device could be opened.
        """
        self._start_with_capture_plan(None)

    def start_capture_session(self, audio_config: object) -> None:
        """Atomically bind one config snapshot to this capture session."""
        self._start_with_capture_plan(self._normalized_capture_plan(audio_config))

    def _start_with_capture_plan(
        self,
        capture_plan: Optional[dict[str, object]],
    ) -> None:
        with self._lock:
            if self._is_recording:
                return
            if self._stream_start_thread and self._stream_start_thread.is_alive():
                raise AudioRecorderStartError(
                    "A previous microphone startup is still blocked in CoreAudio",
                    startup_timed_out=True,
                )
            if capture_plan is None:
                self._apply_pending_capture_config_locked()
                if self._use_config_device:
                    # get_config() is already resident after construction and
                    # performs no device I/O. Direct recorder users retain the
                    # historical latest-config behavior; streaming modes pass
                    # an explicit immutable session snapshot instead.
                    self._device_name = get_config().audio.input_device
            else:
                # The mode and ASR share this snapshot. It is authoritative for
                # the utterance even if Settings/RPC publishes a newer plan in
                # the gap between ASR admission and microphone start.
                self._pending_capture_config.clear()
                self._apply_capture_plan_locked(capture_plan)
            self._admit_microphone_permission_locked()
            # Do not enumerate devices or probe native selectors here. All
            # potentially blocking discovery belongs to the startup worker so
            # the command-facing hard deadline covers it.
            self._audio_buffer = []
            self._is_recording = True
            self._hp_prev_in = 0.0
            self._hp_prev_out = 0.0
            self._array_processing_active = False
            self._apple_agc_active = False
            self._mark_input_inactive(phase="starting")
            self._start_generation += 1
            generation = self._start_generation

        done = threading.Event()
        result_box: dict[str, object] = {}
        worker = threading.Thread(
            target=self._run_stream_start,
            args=(generation, done, result_box),
            name=_STREAM_START_THREAD_NAME,
            daemon=True,
        )
        with self._lock:
            self._stream_start_thread = worker
        worker.start()

        if not done.wait(timeout=self._start_timeout):
            stream = None
            with self._lock:
                if generation == self._start_generation:
                    self._start_generation += 1
                self._is_recording = False
                stream = self._stream
                self._stream = None
                self._array_processing_active = False
                self._apple_agc_active = False
                # A backend may invoke its callback before start() reports a
                # failure. Never expose those unverified frames after the
                # startup attempt has been rejected.
                self._audio_buffer = []
                self._mark_input_inactive(phase="failed")
            if stream is not None:
                self._release_stream_async(stream)
            raise AudioRecorderStartError(
                f"Microphone startup exceeded {self._start_timeout:.2f}s",
                startup_timed_out=True,
                code="startup_timeout",
                stage="stream_start",
            )

        error = result_box.get("error")
        if error is not None:
            with self._lock:
                self._is_recording = False
                self._array_processing_active = False
                self._apple_agc_active = False
                self._audio_buffer = []
                self._mark_input_inactive(phase="failed")
            if isinstance(error, AudioRecorderStartError):
                raise error
            raise AudioRecorderStartError(str(error)) from error

    def _admit_microphone_permission_locked(self) -> None:
        """Admit one explicit capture without consuming device-start time.

        ``requestAccess`` is asynchronous. The first action starts the system
        prompt and returns a structured, recoverable error; it never waits and
        never auto-replays the recording after the user has released a hotkey.
        A later explicit action must observe ``authorized`` before CoreAudio's
        hard startup deadline begins.
        """
        if self._benchmark_replay_path is not None:
            return

        permission = microphone_permission_status()
        self._input_status["microphone_permission"] = permission
        if permission == "authorized":
            self._microphone_permission_requested = False
            return
        if permission in ("denied", "restricted"):
            self._input_status["phase"] = "permission_denied"
            raise AudioRecorderStartError(
                "Microphone access is not available for this application",
                code=f"microphone_permission_{permission}",
                stage="permission",
                recoverable=False,
            )
        if permission == "not_determined":
            if not self._microphone_permission_requested:
                try:
                    requested = request_microphone_access()
                except Exception as exc:
                    self._input_status["phase"] = "permission_failed"
                    raise AudioRecorderStartError(
                        f"Could not request microphone access: {exc}",
                        code="microphone_permission_request_failed",
                        stage="permission",
                    ) from exc
                if not requested:
                    self._input_status["phase"] = "permission_failed"
                    raise AudioRecorderStartError(
                        "AVFoundation could not request microphone access",
                        code="microphone_permission_request_unavailable",
                        stage="permission",
                    )
                self._microphone_permission_requested = True
            self._input_status["phase"] = "awaiting_permission"
            raise AudioRecorderStartError(
                "Microphone permission was requested; grant access and start again",
                code="microphone_permission_requested",
                stage="permission",
            )
        if platform.system() == "Darwin":
            self._input_status["phase"] = "permission_failed"
            raise AudioRecorderStartError(
                "Microphone permission state could not be determined",
                code="microphone_permission_unknown",
                stage="permission",
            )

    def _run_stream_start(
        self,
        generation: int,
        done: threading.Event,
        result_box: dict[str, object],
    ) -> None:
        """Own one potentially blocking native startup attempt."""
        stream = None
        startup_gate = None
        try:
            result = self._start_stream_with_recovery()
            if isinstance(result, _VerifiedStreamCandidate):
                stream = result.stream
                startup_gate = result.gate
            else:
                stream = result
        except Exception as exc:
            result_box["error"] = exc
        else:
            with self._lock:
                accepted = (
                    generation == self._start_generation and self._is_recording
                )
                if accepted:
                    self._stream = stream
                else:
                    self._array_processing_active = False
                    self._apple_agc_active = False
                    self._mark_input_inactive()
            if not accepted:
                if startup_gate is not None:
                    startup_gate.reject()
                if stream is not None:
                    self._release_stream_async(stream)
            else:
                if startup_gate is not None:
                    self._activate_startup_gate(
                        startup_gate,
                        stream=stream,
                        generation=generation,
                        ready=done,
                    )
                else:
                    done.set()
        finally:
            with self._lock:
                if self._stream_start_thread is threading.current_thread():
                    self._stream_start_thread = None
            done.set()

    def _activate_startup_gate(
        self,
        gate: _StartupAudioGate,
        *,
        stream,
        generation: int,
        ready: threading.Event,
    ) -> None:
        # Hold the same publication barrier used by stop so all provisional
        # events and their fault state land before a completed PCM snapshot.
        with self._observer_lock:
            with self._lock:
                if (
                    generation != self._start_generation
                    or not self._is_recording
                    or self._stream is not stream
                ):
                    gate.reject()
                    ready.set()
                    return
            # Publish readiness only after this worker owns stop's barrier.
            # start() can return before observers finish, but stop cannot race
            # ahead, empty the PCM snapshot, and reject a verified first block.
            ready.set()
            dropped, publish_errors = gate.activate()
            if dropped or publish_errors:
                with self._lock:
                    recorder_faults = dropped + publish_errors
                    self._input_status["recorder_fault_count"] = (
                        int(self._input_status.get("recorder_fault_count") or 0)
                        + recorder_faults
                    )
                    recorder_fault_code = (
                        "startup_audio_gate_overflow"
                        if dropped
                        else "startup_audio_publish_failed"
                    )
                    self._input_status["recorder_fault_code"] = recorder_fault_code
                    self._input_status["runtime_fault_count"] = (
                        int(self._input_status.get("runtime_fault_count") or 0)
                        + recorder_faults
                    )
                    self._input_status["runtime_fault_code"] = recorder_fault_code
                    self._input_status["gain_control_verified"] = False

    def stop(self) -> bytes:
        """Stop recording and return PCM audio data.

        PortAudio/CoreAudio can occasionally block while stopping or closing a
        stream after an input-device transition. Detach the stream and release
        it on a daemon worker so dictation state can always advance past
        ``STOPPING``. The callback sees ``_is_recording`` become false before
        the stream is detached, so late audio is discarded.

        Returns:
            Raw PCM audio data (int16, mono, 16kHz)
        """
        self._benchmark_replay_stop.set()
        replay_thread = self._benchmark_replay_thread
        if replay_thread is not None and replay_thread is not threading.current_thread():
            replay_thread.join(timeout=1.0)
        self._benchmark_replay_thread = None

        # AVAudioConverter keeps filter latency and the final sub-block frames
        # until EOS. Drain them while callbacks are still accepted, before the
        # PCM snapshot below. PortAudio streams do not expose this method.
        with self._lock:
            # input_status normally refreshes native diagnostics. Once drain
            # begins that getter may need the same foreign-call lock, so status
            # readers must use the cached snapshot until this stream detaches.
            self._is_stopping = True
            stream_to_drain = self._stream
        drain_thread = None
        drain_fault_code = None
        drain_fault_detail = None
        drain = getattr(stream_to_drain, "drain", None)
        if callable(drain):
            (
                drain_thread,
                drain_fault_code,
                drain_fault_detail,
            ) = self._drain_stream_bounded(
                drain,
                timeout=_NATIVE_DRAIN_TIMEOUT_SECONDS,
            )
            if drain_fault_code is not None:
                print(
                    "[AudioRecorder] Native audio tail was not fully drained: "
                    f"{drain_fault_detail}"
                )

        with self._observer_lock:
            # The observer barrier must be acquired before taking the final
            # status snapshot. An already accepted chunk can still report a
            # handoff failure while stop waits for this lock.
            final_status = None
            if stream_to_drain is not None:
                with self._lock:
                    current_status = dict(self._input_status)
                # A timed-out native stop may still own its foreign-call lock.
                # Re-entering diagnostics here would defeat the command-facing
                # 500 ms deadline and can deadlock behind that same stop call.
                final_status = (
                    current_status
                    if drain_fault_code == "native_drain_timeout"
                    else self._merge_stream_diagnostics(
                        current_status,
                        stream_to_drain,
                    )
                )
                if drain_fault_code is not None:
                    final_status["recorder_fault_count"] = (
                        int(final_status.get("recorder_fault_count") or 0) + 1
                    )
                    final_status["recorder_fault_code"] = drain_fault_code
                    final_status["runtime_fault_count"] = max(
                        1,
                        int(final_status.get("runtime_fault_count") or 0) + 1,
                    )
                    final_status["runtime_fault_code"] = drain_fault_code
                    final_status["gain_control_verified"] = False
                    final_status["fallback_reason"] = drain_fault_detail

            with self._lock:
                self._is_recording = False
                self._start_generation += 1
                stream = self._stream
                self._stream = None
                self._array_processing_active = False
                self._apple_agc_active = False
                self._is_stopping = False
                if (
                    final_status is not None
                    and final_status.get("phase") == "active"
                ):
                    final_status["phase"] = "completed"
                    self._last_input_session = dict(final_status)
                self._mark_input_inactive()
                audio_data = b"".join(self._audio_buffer)
                self._audio_buffer = []

        if stream is not None:
            self._release_stream_async(stream, after_thread=drain_thread)

        return audio_data

    @staticmethod
    def _release_stream(stream) -> None:
        """Release a detached PortAudio stream without blocking dictation."""
        try:
            abort = getattr(stream, "abort", None)
            if callable(abort):
                abort()
            else:
                stream.stop()
        except Exception as exc:
            print(f"[AudioRecorder] Failed to stop detached stream: {exc}")
        try:
            stream.close()
        except Exception as exc:
            print(f"[AudioRecorder] Failed to close detached stream: {exc}")

    @staticmethod
    def _drain_stream_bounded(
        drain: Callable[[], None],
        *,
        timeout: float,
    ) -> tuple[threading.Thread, Optional[str], Optional[str]]:
        """Run converter drain behind a hard command-thread deadline."""
        completed = threading.Event()
        error_box: list[Exception] = []

        def run() -> None:
            try:
                drain()
            except Exception as exc:
                error_box.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(
            target=run,
            name=_NATIVE_DRAIN_THREAD_NAME,
            daemon=True,
        )
        worker.start()
        if not completed.wait(timeout=max(0.0, float(timeout))):
            return (
                worker,
                "native_drain_timeout",
                f"drain exceeded {float(timeout):.2f}s",
            )
        if error_box:
            return worker, "native_drain_failed", str(error_box[0])[:500]
        return worker, None, None

    @classmethod
    def _release_stream_after(
        cls,
        stream,
        after_thread: Optional[threading.Thread],
    ) -> None:
        # Never call stop/destroy concurrently with a drain that may still be
        # using the same opaque native handle. A permanently wedged CoreAudio
        # call is contained in daemon workers rather than risking a UAF.
        if (
            after_thread is not None
            and after_thread is not threading.current_thread()
        ):
            after_thread.join()
        cls._release_stream(stream)

    def _release_stream_async(
        self,
        stream,
        *,
        after_thread: Optional[threading.Thread] = None,
    ) -> None:
        release_thread = threading.Thread(
            target=self._release_stream_after,
            args=(stream, after_thread),
            name=_STREAM_RELEASE_THREAD_NAME,
            daemon=True,
        )
        self._stream_release_thread = release_thread
        release_thread.start()

    @property
    def array_processing_active(self) -> bool:
        """Whether the active stream mixes multiple Mac input channels."""
        with self._lock:
            return self._array_processing_active

    @property
    def input_status(self) -> dict:
        """Describe the selected and, when recording, actual input path."""
        with self._lock:
            status = dict(self._input_status)
            stream = self._stream
            is_stopping = self._is_stopping
            last_session = (
                dict(self._last_input_session)
                if self._last_input_session is not None
                else None
            )
        if stream is not None and not is_stopping:
            status = self._merge_stream_diagnostics(status, stream)
        status["last_session"] = last_session
        return status

    @classmethod
    def inspect_input_status(cls, device: Optional[str] = None) -> dict:
        """Describe the input path that a new recorder would attempt."""
        status = cls(device=device)._planned_input_status()
        status["last_session"] = None
        return status

    @property
    def benchmark_audio_delivery(self) -> str:
        """Describe the active input path without exposing the source filename."""
        if self._benchmark_replay_path is not None:
            return "deterministic_wav_replay"
        return "physical_microphone"

    def wait_for_benchmark_replay(self, timeout: float | None = None) -> bool:
        """Wait for an opt-in deterministic replay to feed its final block."""
        return self._benchmark_replay_done.wait(timeout)

    def is_recording(self) -> bool:
        """Check if currently recording."""
        with self._lock:
            return self._is_recording

    def get_pcm_data(self) -> bytes:
        """Get current recorded PCM data without stopping."""
        with self._lock:
            return b"".join(self._audio_buffer)

    def _start_stream_with_recovery(self):
        """Open the input stream, then rebuild PortAudio once if it is wedged."""
        if self._benchmark_replay_path is not None:
            self._start_benchmark_replay(self._benchmark_replay_path)
            return None

        permission = microphone_permission_status()
        if permission in ("denied", "restricted"):
            raise AudioRecorderStartError(
                "Microphone access is not available for this application",
                code=f"microphone_permission_{permission}",
                stage="permission",
                recoverable=False,
            )
        if platform.system() == "Darwin" and permission != "authorized":
            raise AudioRecorderStartError(
                "Microphone permission changed before device startup",
                code=f"microphone_permission_{permission}",
                stage="permission",
            )

        device_index = self._resolve_device()

        try:
            return self._open_stream_with_fallback(device_index)
        except Exception as initial_error:
            if self._should_retry_after_portaudio_reset(initial_error):
                if self._recover_portaudio_state(initial_error):
                    try:
                        return self._open_stream_with_fallback(self._resolve_device())
                    except Exception as retry_error:
                        raise AudioRecorderStartError(
                            str(retry_error),
                            device_change_detected=True,
                        ) from retry_error
                raise AudioRecorderStartError(
                    str(initial_error),
                    device_change_detected=True,
                ) from initial_error
            raise AudioRecorderStartError(str(initial_error)) from initial_error

    def _start_benchmark_replay(self, path: Path) -> None:
        """Feed a validated WAV through the normal gain/filter/chunk callbacks."""
        try:
            with wave.open(str(path), "rb") as source:
                if source.getnchannels() != self.channels or self.channels != 1:
                    raise AudioRecorderStartError(
                        "Benchmark replay WAV must be mono"
                    )
                if source.getsampwidth() != 2:
                    raise AudioRecorderStartError(
                        "Benchmark replay WAV must use 16-bit PCM"
                    )
                if source.getframerate() != self.sample_rate:
                    raise AudioRecorderStartError(
                        f"Benchmark replay WAV must be {self.sample_rate} Hz"
                    )
                pcm_data = source.readframes(source.getnframes())
        except AudioRecorderStartError:
            raise
        except (OSError, EOFError, wave.Error) as exc:
            raise AudioRecorderStartError(
                f"Cannot read benchmark replay WAV: {exc}"
            ) from exc

        if not pcm_data:
            raise AudioRecorderStartError("Benchmark replay WAV is empty")

        self._benchmark_replay_done.clear()
        self._benchmark_replay_stop.clear()
        self._benchmark_replay_thread = threading.Thread(
            target=self._run_benchmark_replay,
            args=(pcm_data,),
            name="vocal-more-benchmark-wav-replay",
            daemon=True,
        )
        self._benchmark_replay_thread.start()

    def _run_benchmark_replay(self, pcm_data: bytes) -> None:
        bytes_per_frame = self.channels * 2
        block_bytes = self.blocksize * bytes_per_frame
        started_at = time.monotonic()
        try:
            for offset in range(0, len(pcm_data), block_bytes):
                target_seconds = (
                    offset / bytes_per_frame / self.sample_rate
                )
                delay = target_seconds - (time.monotonic() - started_at)
                if delay > 0 and self._benchmark_replay_stop.wait(delay):
                    break
                if self._benchmark_replay_stop.is_set():
                    break

                chunk = pcm_data[offset : offset + block_bytes]
                samples = (
                    np.frombuffer(chunk, dtype="<i2")
                    .astype(np.float32)
                    .reshape(-1, self.channels)
                    / 32768.0
                )
                self._audio_callback(samples, len(samples), {}, 0)
        finally:
            self._benchmark_replay_done.set()

    def _open_stream_with_fallback(self, device_index: Optional[int]):
        try:
            return self._open_stream(device_index)
        except sd.PortAudioError:
            if device_index is None:
                raise
            print(f"Device '{self._device_name}' failed, falling back to default")
            if self._use_config_device:
                self._clear_unavailable_device()
            return self._open_stream(None)

    def _open_stream(self, device_index: Optional[int]):
        device = self._device_info(device_index)
        voice_processing_error: Optional[Exception] = None
        if self._should_use_macos_voice_processing(device_index, device):
            native_failure: Optional[NativeAudioUnavailable] = None
            candidates = [_build_macos_voice_processing_stream]
            for builder in candidates:
                try:
                    return self._start_verified_voice_processing_candidate(
                        builder,
                        device_index=device_index,
                        device=device,
                    )
                except NativeAudioUnavailable as exc:
                    native_failure = exc
                    break
                except VoiceProcessingUnavailable as exc:
                    voice_processing_error = exc
                    break

            if native_failure is not None:
                try:
                    return self._start_verified_voice_processing_candidate(
                        _build_pyobjc_voice_processing_stream,
                        device_index=device_index,
                        device=device,
                    )
                except VoiceProcessingUnavailable as exc:
                    voice_processing_error = VoiceProcessingUnavailable(
                        "Objective-C++ voice processing failed: "
                        f"{native_failure}; PyObjC voice processing failed: {exc}",
                        code=getattr(exc, "code", "voice_processing_unavailable"),
                        stage=getattr(exc, "stage", "voice_processing"),
                    )
                except NativeAudioUnavailable as exc:
                    # The direct PyObjC builder should not normally emit this,
                    # but preserve an expected fallback boundary if injected.
                    voice_processing_error = exc

            if voice_processing_error is not None:
                print(
                    "[AudioRecorder] Apple voice processing unavailable; "
                    f"falling back to CoreAudio input: {voice_processing_error}"
                )

        capture_channels, array_processing = self._capture_profile(device_index)
        with self._lock:
            self._apple_agc_active = False
        actual_capture_channels = capture_channels
        try:
            candidate = self._create_started_stream(
                device_index,
                capture_channels,
            )
        except sd.PortAudioError:
            if not array_processing:
                raise
            print(
                "[AudioRecorder] Raw Mac microphone-array format unavailable; "
                "using the system beamformed mono input"
            )
            candidate = self._create_started_stream(device_index, 1)
            array_processing = False
            actual_capture_channels = 1
        stream = candidate.stream
        with self._lock:
            self._array_processing_active = array_processing
            if array_processing:
                processing_mode = "vocal_more_array"
            elif self._is_macos_builtin_microphone(device):
                processing_mode = "system_managed_mono"
            else:
                processing_mode = "standard"
            self._input_status = self._status_for_device(
                device_index,
                device,
                processing_mode=processing_mode,
                capture_channels=actual_capture_channels,
                processing_active=True,
                echo_cancellation=(
                    "fallback" if voice_processing_error is not None else "unavailable"
                ),
                fallback_reason=(
                    str(voice_processing_error)
                    if voice_processing_error is not None
                    else None
                ),
                fallback_code=(
                    getattr(
                        voice_processing_error,
                        "code",
                        "voice_processing_start_failed",
                    ) if voice_processing_error is not None else None
                ),
                fallback_stage=(
                    getattr(
                        voice_processing_error,
                        "stage",
                        "voice_processing_start",
                    ) if voice_processing_error is not None else None
                ),
            )
            self._input_status["source_sample_rate_hz"] = float(
                getattr(stream, "samplerate", self.sample_rate)
            )
            self._input_status["source_channels"] = actual_capture_channels
        return candidate

    def _start_verified_voice_processing_candidate(
        self,
        builder,
        *,
        device_index: Optional[int],
        device: Optional[dict],
    ):
        """Start one Apple backend without leaking provisional audio."""
        gate = _StartupAudioGate(
            float_callback=self._audio_callback,
            pcm_callback=self._native_pcm_callback,
        )
        voice_stream = None
        try:
            # The callbacks are gated, but the eventual DSP plan must already
            # be correct when queued Float32 frames are released.
            with self._lock:
                self._apple_agc_active = self._gain_mode == "automatic"
            voice_stream = builder(
                callback=gate.float_callback,
                pcm_callback=gate.pcm_callback,
                sample_rate=self.sample_rate,
                blocksize=self.blocksize,
                automatic_gain=self._gain_mode == "automatic",
                gain=self._gain,
                highpass_filter=self._highpass_filter,
                highpass_freq=self._hp_freq,
                soft_limiter=self._soft_limiter,
            )
            voice_stream.start()
            active_status = self._status_for_device(
                device_index,
                device,
                processing_mode="macos_voice_processing",
                capture_channels=1,
                processing_active=True,
                echo_cancellation="active",
            )
            active_status = self._merge_stream_diagnostics(
                active_status,
                voice_stream,
            )
            with self._lock:
                self._array_processing_active = False
                self._input_status = active_status
                # Quality verification and DSP ownership are separate facts.
                # A drop/fault makes the session unverified, but if the live
                # getters still prove that Apple AGC is on, applying software
                # gain would stack two level controllers and can clip badly.
                self._apple_agc_active = bool(
                    self._gain_mode == "automatic"
                    and active_status.get("voice_processing_enabled_observed")
                    is True
                    and active_status.get("agc_enabled_observed") is True
                )
            return _VerifiedStreamCandidate(voice_stream, gate)
        except Exception:
            gate.reject()
            with self._lock:
                self._apple_agc_active = False
                self._array_processing_active = False
                self._audio_buffer = []
                self._hp_prev_in = 0.0
                self._hp_prev_out = 0.0
            if voice_stream is not None:
                try:
                    voice_stream.close()
                except Exception:
                    pass
            raise

    def _create_started_stream(self, device_index: Optional[int], channels: int):
        gate = _StartupAudioGate(
            float_callback=self._audio_callback,
            pcm_callback=self._native_pcm_callback,
        )
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=channels,
            blocksize=self.blocksize,
            dtype=np.float32,
            callback=gate.float_callback,
            device=device_index,
        )
        try:
            stream.start()
        except Exception:
            gate.reject()
            self._release_stream_async(stream)
            raise
        return _VerifiedStreamCandidate(stream, gate)

    def _capture_profile(self, device_index: Optional[int]) -> tuple[int, bool]:
        """Choose device input channels without changing mono ASR output."""
        device = self._device_info(device_index)
        if device is None:
            return self.capture_channels, False

        max_channels = max(1, int(device.get("max_input_channels", 1)))
        requested_channels = min(self.capture_channels, max_channels)
        if not self._is_macos_builtin_array(device):
            return requested_channels, False

        # Public APIs expose logical channels, not calibrated capsule geometry.
        # Mono remains the safe default; only an explicit capture_channels > 1
        # request opts into the experimental coherent-mix fallback.
        capture_channels = min(requested_channels, _MAX_MACOS_ARRAY_CHANNELS)
        return capture_channels, capture_channels > 1

    @staticmethod
    def _is_macos_builtin_array(device: dict) -> bool:
        return (
            AudioRecorder._is_macos_builtin_microphone(device)
            and int(device.get("max_input_channels", 0)) >= 2
        )

    @staticmethod
    def _is_macos_builtin_microphone(device: Optional[dict]) -> bool:
        if platform.system() != "Darwin" or not device:
            return False
        name = str(device.get("name", "")).strip().lower()
        return any(marker in name for marker in _MACOS_ARRAY_DEVICE_MARKERS)

    def _should_use_macos_voice_processing(
        self,
        device_index: Optional[int],
        device: Optional[dict],
    ) -> bool:
        if not self._is_macos_builtin_microphone(device):
            return False
        # Studio Display's VoiceProcessingIO route spends roughly 0.6 s
        # enabling voice processing and another 0.45 s starting the engine on
        # observed hardware. The CoreAudio compatibility route reaches its
        # first frame in about half that time and matches the pre-native app's
        # dictation behavior. Prefer complete sentence capture over AEC on this
        # route; keeping a prewarmed input engine alive would leave the user's
        # microphone continuously occupied while the app is idle.
        name = str(device.get("name", "")).strip().lower() if device else ""
        if any(marker in name for marker in _MACOS_LOW_LATENCY_ROUTE_MARKERS):
            return False
        target_index = (
            self._default_input_device_index()
            if device_index is None
            else int(device_index)
        )
        default_index = self._default_input_device_index()
        if target_index is None or target_index != default_index:
            return False
        return _macos_voice_processing_available()

    def _planned_input_status(self) -> dict:
        device_index = self._resolved_device_index_for_status()
        device = self._device_info(device_index)
        max_channels = max(
            1,
            int(device.get("max_input_channels", 1)) if device else 1,
        )
        if self._should_use_macos_voice_processing(device_index, device):
            return self._status_for_device(
                device_index,
                device,
                processing_mode="macos_voice_processing",
                capture_channels=1,
                processing_active=False,
                echo_cancellation="ready",
            )
        if self._is_macos_builtin_array(device):
            capture_channels = min(
                self.capture_channels,
                max_channels,
                _MAX_MACOS_ARRAY_CHANNELS,
            )
            processing_mode = (
                "vocal_more_array"
                if capture_channels > 1
                else "system_managed_mono"
            )
        elif self._is_macos_builtin_microphone(device):
            processing_mode = "system_managed_mono"
            capture_channels = 1
        else:
            processing_mode = "standard"
            capture_channels = min(self.capture_channels, max_channels)
        return self._status_for_device(
            device_index,
            device,
            processing_mode=processing_mode,
            capture_channels=capture_channels,
            processing_active=False,
            echo_cancellation="unavailable",
        )

    def _initial_input_status(self) -> dict:
        """Return a complete, I/O-free status for dependency construction."""
        automatic = self._gain_mode == "automatic"
        return {
            "phase": "planned",
            "device_name": self._device_name or "",
            "system_default": self._device_name is None,
            "max_input_channels": max(1, int(self.capture_channels)),
            "capture_channels": max(1, int(self.capture_channels)),
            "processing_mode": "pending",
            "processing_active": False,
            "array_processing_active": False,
            "echo_cancellation": "pending",
            "microphone_permission": "unknown",
            "requested_gain_mode": self._gain_mode,
            "gain_control": "pending" if automatic else "software",
            "gain_control_verified": False,
            "voice_processing_enabled_observed": None,
            "agc_enabled_observed": None,
            "start_verified": None,
            "diagnostics_fresh": None,
            "software_gain_effective": None,
            "soft_limiter_effective": None,
            "highpass_effective": None,
            "native_backend": "pending",
            "source_sample_rate_hz": None,
            "source_channels": None,
            "output_sample_rate_hz": self.sample_rate,
            "output_channels": OUTPUT_CHANNELS,
            "converter_name": None,
            "capture_block_frames": self.blocksize,
            "queue_dropped_blocks": 0,
            "recorder_fault_count": 0,
            "recorder_fault_code": None,
            "runtime_fault_count": 0,
            "runtime_fault_code": None,
            "preferred_microphone_mode": None,
            "active_microphone_mode": None,
            "fallback_code": None,
            "fallback_stage": None,
            "fallback_reason": None,
        }

    def _resolved_device_index_for_status(self) -> Optional[int]:
        if not self._device_name:
            return None
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for device in devices:
            if (
                str(device.get("name", "")) == self._device_name
                and int(device.get("max_input_channels", 0)) > 0
            ):
                return int(device.get("index", -1))
        return None

    def _status_for_device(
        self,
        device_index: Optional[int],
        device: Optional[dict],
        *,
        processing_mode: str,
        capture_channels: int,
        processing_active: bool,
        echo_cancellation: str,
        fallback_reason: Optional[str] = None,
        fallback_code: Optional[str] = None,
        fallback_stage: Optional[str] = None,
    ) -> dict:
        default_index = self._default_input_device_index()
        resolved_index = (
            default_index if device_index is None else int(device_index)
        )
        if self._gain_mode == "automatic":
            gain_control = (
                "apple_agc"
                if processing_mode == "macos_voice_processing"
                else "software_fallback"
            )
        else:
            gain_control = "software"
        voice_processing_path = processing_mode == "macos_voice_processing"
        if processing_active and voice_processing_path:
            voice_processing_observed: Optional[bool] = True
            agc_observed: Optional[bool] = self._gain_mode == "automatic"
        elif processing_active and fallback_reason:
            voice_processing_observed = False
            agc_observed = None
        else:
            voice_processing_observed = None
            agc_observed = None
        effective_software = processing_active and not (
            voice_processing_path and self._gain_mode == "automatic"
        )
        return {
            "phase": "active" if processing_active else "planned",
            "device_name": (
                str(device.get("name", "")).strip()
                if device
                else self._device_name or ""
            ),
            "system_default": (
                resolved_index is not None and resolved_index == default_index
            ),
            "max_input_channels": max(
                1,
                int(device.get("max_input_channels", 1)) if device else 1,
            ),
            "capture_channels": max(1, int(capture_channels)),
            "processing_mode": processing_mode,
            "processing_active": bool(processing_active),
            "array_processing_active": (
                processing_active and processing_mode == "vocal_more_array"
            ),
            "echo_cancellation": echo_cancellation,
            "microphone_permission": microphone_permission_status(),
            "requested_gain_mode": self._gain_mode,
            "gain_control": gain_control,
            "gain_control_verified": bool(processing_active),
            "voice_processing_enabled_observed": voice_processing_observed,
            "agc_enabled_observed": agc_observed,
            "start_verified": None,
            "diagnostics_fresh": None,
            "software_gain_effective": self._gain if effective_software else None,
            "soft_limiter_effective": (
                self._soft_limiter if effective_software else False
            ) if processing_active else None,
            "highpass_effective": (
                self._highpass_filter if processing_active else None
            ),
            "native_backend": (
                "pending" if voice_processing_path else "portaudio"
            ),
            "source_sample_rate_hz": None,
            "source_channels": None,
            "output_sample_rate_hz": self.sample_rate,
            "output_channels": OUTPUT_CHANNELS,
            "converter_name": (
                "AVAudioConverter" if voice_processing_path else None
            ),
            "capture_block_frames": self.blocksize,
            "queue_dropped_blocks": 0,
            "recorder_fault_count": 0,
            "recorder_fault_code": None,
            "runtime_fault_count": 0,
            "runtime_fault_code": None,
            "preferred_microphone_mode": None,
            "active_microphone_mode": None,
            "fallback_code": fallback_code,
            "fallback_stage": fallback_stage,
            "fallback_reason": fallback_reason,
        }

    @staticmethod
    def _diagnostic_value(diagnostics, field: str, default=None):
        if isinstance(diagnostics, dict):
            return diagnostics.get(field, default)
        return getattr(diagnostics, field, default)

    @classmethod
    def _merge_stream_diagnostics(cls, status: dict, stream) -> dict:
        merged = dict(status)
        try:
            diagnostics = getattr(stream, "diagnostics", None)
            if callable(diagnostics):
                diagnostics = diagnostics()
        except Exception as exc:
            recorder_faults = int(merged.get("recorder_fault_count") or 0) + 1
            merged["recorder_fault_count"] = recorder_faults
            merged["recorder_fault_code"] = "diagnostics_unavailable"
            merged["runtime_fault_count"] = recorder_faults
            merged["runtime_fault_code"] = merged["recorder_fault_code"]
            merged["gain_control_verified"] = False
            merged["start_verified"] = False
            merged["diagnostics_fresh"] = False
            merged["fallback_reason"] = str(exc)
            return merged
        if diagnostics is None:
            merged["native_backend"] = getattr(
                stream,
                "backend_name",
                merged.get("native_backend"),
            )
            return merged

        source_rate = cls._diagnostic_value(
            diagnostics,
            "source_sample_rate_hz",
            0.0,
        )
        recorder_faults = int(merged.get("recorder_fault_count") or 0)
        recorder_fault_code = merged.get("recorder_fault_code")
        stream_faults = int(
            cls._diagnostic_value(
                diagnostics,
                "runtime_fault_count",
                0,
            )
            or 0
        )
        stream_fault_code = cls._diagnostic_value(
            diagnostics,
            "runtime_fault_code",
            None,
        )
        merged.update(
            {
                "native_backend": cls._diagnostic_value(
                    diagnostics,
                    "backend",
                    getattr(stream, "backend_name", "unknown"),
                ),
                "source_sample_rate_hz": (
                    float(source_rate) if source_rate else None
                ),
                "source_channels": cls._diagnostic_value(
                    diagnostics,
                    "source_channels",
                    None,
                ),
                "voice_processing_enabled_observed": cls._diagnostic_value(
                    diagnostics,
                    "voice_processing_enabled",
                    merged.get("voice_processing_enabled_observed"),
                ),
                "agc_enabled_observed": cls._diagnostic_value(
                    diagnostics,
                    "agc_enabled",
                    merged.get("agc_enabled_observed"),
                ),
                "start_verified": cls._diagnostic_value(
                    diagnostics,
                    "start_verified",
                    merged.get("start_verified"),
                ),
                "diagnostics_fresh": cls._diagnostic_value(
                    diagnostics,
                    "diagnostics_fresh",
                    merged.get("diagnostics_fresh"),
                ),
                "converter_name": cls._diagnostic_value(
                    diagnostics,
                    "converter_name",
                    merged.get("converter_name"),
                ),
                "queue_dropped_blocks": int(
                    cls._diagnostic_value(
                        diagnostics,
                        "dropped_blocks",
                        0,
                    )
                    or 0
                ),
                "runtime_fault_count": recorder_faults + stream_faults,
                "runtime_fault_code": recorder_fault_code or stream_fault_code,
                "preferred_microphone_mode": cls._diagnostic_value(
                    diagnostics,
                    "preferred_microphone_mode",
                    None,
                ),
                "active_microphone_mode": cls._diagnostic_value(
                    diagnostics,
                    "active_microphone_mode",
                    None,
                ),
            }
        )
        requested = merged.get("requested_gain_mode")
        voice_observed = merged.get("voice_processing_enabled_observed")
        agc_observed = merged.get("agc_enabled_observed")
        merged["gain_control_verified"] = bool(
            merged.get("phase") == "active"
            and voice_observed is True
            and int(merged.get("runtime_fault_count") or 0) == 0
            and int(merged.get("queue_dropped_blocks") or 0) == 0
            and merged.get("start_verified") is not False
            and merged.get("diagnostics_fresh") is not False
            and (
                (requested == "automatic" and agc_observed is True)
                or (requested == "manual" and agc_observed is False)
            )
        )
        return merged

    def _mark_input_inactive(self, *, phase: str = "inactive") -> None:
        status = dict(self._input_status)
        status["phase"] = phase
        status["processing_active"] = False
        status["array_processing_active"] = False
        status["gain_control_verified"] = False
        status["voice_processing_enabled_observed"] = None
        status["agc_enabled_observed"] = None
        status["start_verified"] = None
        status["diagnostics_fresh"] = None
        status["software_gain_effective"] = None
        status["soft_limiter_effective"] = None
        status["highpass_effective"] = None
        if status.get("echo_cancellation") == "active":
            status["echo_cancellation"] = "ready"
        self._input_status = status

    @staticmethod
    def _coherent_array_downmix(indata: np.ndarray) -> np.ndarray:
        """Polarity-align coherent array channels and return float32 mono.

        macOS normally performs device-level beamforming for its built-in
        microphones. Some Core Audio devices nevertheless expose multiple
        logical input channels; the public API does not establish whether they
        map one-to-one to physical capsules. A raw mean can cancel speech when
        a channel has inverted polarity, while selecting channel zero may throw
        away useful correlated signal. This gate keeps speech-correlated
        channels, aligns polarity, and averages them. Uncorrelated channels are
        excluded so an unrelated noisy input cannot dominate the mix.
        """
        samples = (
            indata
            if isinstance(indata, np.ndarray) and indata.dtype == np.float32
            else np.asarray(indata, dtype=np.float32)
        )
        if samples.ndim == 1:
            return samples.reshape(-1, 1)
        if samples.shape[1] <= 1:
            return samples[:, :1]

        centered = samples - np.mean(samples, axis=0, keepdims=True)
        energy = np.sqrt(np.sum(centered * centered, axis=0))
        active = energy > 1e-7
        if not np.any(active):
            return np.mean(samples, axis=1, keepdims=True, dtype=np.float32)

        denominator = np.outer(energy, energy)
        correlation = np.zeros(
            (samples.shape[1], samples.shape[1]),
            dtype=np.float32,
        )
        np.divide(
            centered.T @ centered,
            denominator,
            out=correlation,
            where=denominator > 1e-12,
        )
        # Pick the channel most coherent with the rest, rather than the loudest
        # channel, which may be dominated by a fan or keyboard.
        reference = int(np.argmax(np.sum(np.abs(correlation), axis=1)))
        anchor = int(np.flatnonzero(active)[0])
        reference_correlation = correlation[reference]
        included = active & (np.abs(reference_correlation) >= 0.15)
        included[reference] = True
        polarity = np.where(reference_correlation < 0, -1.0, 1.0).astype(
            np.float32
        )
        aligned = samples[:, included] * polarity[included]
        mixed = np.mean(aligned, axis=1, keepdims=True, dtype=np.float32)
        # Coherence chooses the cleanest reference, but preserve the polarity
        # convention of the device's first active channel for stable output.
        if correlation[reference, anchor] < 0:
            mixed = -mixed
        return mixed

    @staticmethod
    def _default_input_device_index() -> Optional[int]:
        try:
            default_device = sd.default.device
            index = default_device[0]
            return int(index) if index is not None and int(index) >= 0 else None
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _device_info(self, device_index: Optional[int]) -> Optional[dict]:
        target_index = (
            device_index
            if device_index is not None
            else self._default_input_device_index()
        )
        if target_index is None:
            return None
        try:
            devices = sd.query_devices()
        except Exception as exc:
            print(f"[AudioRecorder] Failed to inspect input device: {exc}")
            return None
        for device in devices:
            try:
                if int(device.get("index", -1)) == int(target_index):
                    return device
            except (AttributeError, TypeError, ValueError):
                continue
        return None

    def _should_retry_after_portaudio_reset(self, error: Exception) -> bool:
        if isinstance(error, sd.PortAudioError):
            return True
        details = str(error).lower()
        return any(marker in details for marker in _PORTAUDIO_RECOVERY_MARKERS)

    def _recover_portaudio_state(self, error: Exception) -> bool:
        print(f"[AudioRecorder] Reinitializing PortAudio after startup failure: {error}")
        self._close_stream_quietly()
        return self._reset_portaudio_state(error)

    @staticmethod
    def _reset_portaudio_state(error: Exception | str) -> bool:
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if not callable(terminate) or not callable(initialize):
            print(
                "[AudioRecorder] PortAudio reset hooks unavailable: "
                f"{error}"
            )
            return False

        with _PORTAUDIO_RESET_LOCK:
            terminate()

            default = getattr(sd, "default", None)
            reset = getattr(default, "reset", None)
            if callable(reset):
                reset()

            initialize()
        return True

    def _close_stream_quietly(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _resolve_device(self) -> Optional[int]:
        """Resolve device name to sounddevice index. Returns None for default."""
        if not self._device_name:
            return None
        for dev in sd.query_devices():
            if dev["name"] == self._device_name and dev["max_input_channels"] > 0:
                return dev["index"]
        print(f"Device '{self._device_name}' not found, using default")
        self._clear_unavailable_device()
        return None

    def _clear_unavailable_device(self) -> None:
        missing_device = self._device_name
        if not missing_device:
            return

        self._device_name = None
        if not self._use_config_device:
            return

        config = get_config()
        if config.audio.input_device != missing_device:
            return

        config.audio.input_device = None
        try:
            config.save()
        except Exception as exc:
            print(
                "[AudioRecorder] Failed to persist system-default fallback after "
                f"device '{missing_device}' became unavailable: {exc}"
            )

    def set_device(self, name: Optional[str]) -> None:
        """Set the input device by name. Takes effect on next start()."""
        self._set_capture_config_value("_device_name", name)

    @staticmethod
    def _normalized_capture_plan(audio_config: object) -> dict[str, object]:
        gain_mode = str(getattr(audio_config, "gain_mode", "manual"))
        if gain_mode not in ("automatic", "manual"):
            gain_mode = "manual"
        input_device = getattr(audio_config, "input_device", None)
        if input_device is not None:
            input_device = str(input_device).strip() or None
        return {
            "sample_rate": OUTPUT_SAMPLE_RATE_HZ,
            "blocksize": max(1, int(getattr(audio_config, "blocksize", 640))),
            "capture_channels": max(
                1,
                min(
                    _MAX_MACOS_ARRAY_CHANNELS,
                    int(getattr(audio_config, "capture_channels", 1)),
                ),
            ),
            "_device_name": input_device,
            "_gain_mode": gain_mode,
            "_gain": float(getattr(audio_config, "gain", 1.0)),
            "_highpass_filter": bool(
                getattr(audio_config, "highpass_filter", True)
            ),
            "_hp_freq": int(getattr(audio_config, "highpass_freq", 200)),
            "_soft_limiter": bool(getattr(audio_config, "soft_limiter", True)),
        }

    def apply_capture_config(self, audio_config: object) -> None:
        """Apply a complete future capture plan under one recorder lock.

        An active/stopping session keeps its immutable plan and receives the
        whole update at the next ordinary start boundary. Idle readers can see
        only the previous complete plan or the new complete plan, never a mix.
        """
        plan = self._normalized_capture_plan(audio_config)
        with self._lock:
            if self._is_recording or self._is_stopping:
                self._pending_capture_config.update(plan)
                return
            self._pending_capture_config.clear()
            self._apply_capture_plan_locked(plan)

    def set_sample_rate(self, sample_rate: int) -> None:
        """Normalize the legacy runtime setting to the fixed PCM contract."""
        self._set_capture_config_value("sample_rate", OUTPUT_SAMPLE_RATE_HZ)

    def set_blocksize(self, blocksize: int) -> None:
        self._set_capture_config_value("blocksize", max(1, int(blocksize)))

    def set_capture_channels(self, channels: int) -> None:
        self._set_capture_config_value("capture_channels", max(1, int(channels)))

    def set_gain(self, gain: float) -> None:
        """Set software gain for the next session boundary."""
        self._set_capture_config_value("_gain", float(gain))

    def set_gain_mode(self, mode: str) -> None:
        """Select level control for the next stream.

        AGC is a native stream property. Changing it mid-utterance would cause
        an audible level jump, so an active recording retains its current
        effective path and the new preference applies on the next start.
        """
        if mode in ("automatic", "manual"):
            self._set_capture_config_value("_gain_mode", mode)

    def set_highpass_filter(self, enabled: bool) -> None:
        self._set_capture_config_value("_highpass_filter", bool(enabled))

    def set_highpass_freq(self, freq: int) -> None:
        self._set_capture_config_value("_hp_freq", int(freq))

    def set_soft_limiter(self, enabled: bool) -> None:
        self._set_capture_config_value("_soft_limiter", bool(enabled))

    def refresh_planned_input_status(self) -> bool:
        """Atomically rebuild idle status after a batch of config updates.

        Active sessions keep their observed status and immutable DSP plan. A
        deferred update is reflected when ``start()`` applies it at the next
        session boundary.
        """
        with self._lock:
            if self._is_recording:
                return False
            self._input_status = self._planned_input_status()
            return True

    def _set_capture_config_value(self, attribute: str, value: object) -> None:
        with self._lock:
            if self._is_recording:
                self._pending_capture_config[attribute] = value
                return
            self._pending_capture_config.pop(attribute, None)
            setattr(self, attribute, value)
            if attribute in {"_hp_freq", "sample_rate"}:
                self._hp_alpha = self._calc_hp_alpha(self._hp_freq)

    def _apply_pending_capture_config_locked(self) -> None:
        """Apply deferred runtime config while ``self._lock`` is held."""
        if not self._pending_capture_config:
            return
        pending = self._pending_capture_config
        self._pending_capture_config = {}
        self._apply_capture_plan_locked(pending)

    def _apply_capture_plan_locked(self, plan: dict[str, object]) -> None:
        """Apply normalized values while the caller owns ``self._lock``."""
        for attribute, value in plan.items():
            setattr(self, attribute, value)
        if "_hp_freq" in plan or "sample_rate" in plan:
            self._hp_alpha = self._calc_hp_alpha(self._hp_freq)

    def _calc_hp_alpha(self, freq: int) -> float:
        import math
        if freq <= 0:
            return 1.0
        return 1.0 / (1.0 + 2.0 * math.pi * freq / self.sample_rate)

    @staticmethod
    def _portaudio_status_fault_code(status: object) -> Optional[str]:
        """Map a nonzero PortAudio callback flag to a stable diagnostic code."""
        try:
            has_status = bool(status)
        except Exception:
            has_status = True
        if not has_status:
            return None
        for attribute, code in (
            ("input_overflow", "portaudio_input_overflow"),
            ("input_underflow", "portaudio_input_underflow"),
            ("output_overflow", "portaudio_output_overflow"),
            ("output_underflow", "portaudio_output_underflow"),
            ("priming_output", "portaudio_priming_output"),
        ):
            try:
                if bool(getattr(status, attribute, False)):
                    return code
            except Exception:
                continue
        return "portaudio_callback_status"

    @staticmethod
    def list_input_devices(*, refresh: bool = False) -> list[dict]:
        """Return all available input devices.

        Args:
            refresh: Reinitialize PortAudio before enumerating devices. This is
                useful after macOS input devices are plugged in or removed,
                because PortAudio can otherwise return a stale device cache.

        Returns:
            List of dicts with 'index', 'name', 'is_default' keys.
        """
        if refresh:
            AudioRecorder._reset_portaudio_state("refreshing input device list")

        default_device = sd.default.device[0]  # default input device index
        devices = []
        for dev in sd.query_devices():
            if dev["max_input_channels"] > 0:
                devices.append({
                    "index": dev["index"],
                    "name": dev["name"],
                    "is_default": dev["index"] == default_device,
                })
        return devices

    def save_wav(self, pcm_data: bytes, filepath: str) -> None:
        """Save PCM data to WAV file.

        Args:
            pcm_data: Raw PCM audio data
            filepath: Path to save WAV file
        """
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm_data)

    def pcm_to_wav_bytes(self, pcm_data: bytes) -> bytes:
        """Convert PCM data to WAV format bytes.

        Args:
            pcm_data: Raw PCM audio data

        Returns:
            WAV format audio data
        """
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm_data)
        return buffer.getvalue()


if __name__ == "__main__":
    import time

    print("Testing AudioRecorder...")
    print("Recording for 3 seconds...")

    recorder = AudioRecorder(
        on_audio_chunk=lambda chunk: print(f"Chunk: {len(chunk)} bytes")
    )

    recorder.start()
    time.sleep(3)
    pcm_data = recorder.stop()

    print(f"Recorded {len(pcm_data)} bytes of PCM data")

    # Save to file
    test_file = "/tmp/test_recording.wav"
    recorder.save_wav(pcm_data, test_file)
    print(f"Saved to {test_file}")
