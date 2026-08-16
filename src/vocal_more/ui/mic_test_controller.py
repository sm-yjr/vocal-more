"""Own the settings-window microphone test lifecycle."""

from __future__ import annotations

import base64
from copy import deepcopy
import io
import threading
import wave
from typing import Callable, Optional

from ..domain.audio_contract import (
    OUTPUT_CHANNELS,
    OUTPUT_SAMPLE_RATE_HZ,
    PCM_SAMPLE_WIDTH_BYTES,
)
from ..localization import format_microphone_start_error


class MicTestController:
    """Manage recording, auto-stop, playback, and cleanup for mic tests."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], object],
        recorder_factory: Callable[..., object],
        timer_factory: Callable[[float, Callable[[], None]], object] = threading.Timer,
        on_started: Optional[Callable[[], None]] = None,
        on_complete: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_level: Optional[Callable[[float], None]] = None,
        on_playback: Optional[Callable[[str], None]] = None,
        on_input_status: Optional[Callable[[dict], None]] = None,
        device_changed_error: Optional[Callable[[], str]] = None,
        auto_stop_seconds: float = 5.0,
    ) -> None:
        self._config_provider = config_provider
        self._recorder_factory = recorder_factory
        self._timer_factory = timer_factory
        self._on_started = on_started
        self._on_complete = on_complete
        self._on_error = on_error
        self._on_level = on_level
        self._on_playback = on_playback
        self._on_input_status = on_input_status
        self._device_changed_error = device_changed_error
        self._auto_stop_seconds = auto_stop_seconds

        self._recorder = None
        self._pcm_data: bytes | None = None
        self._timer = None
        self._lifecycle_lock = threading.Lock()
        self._session_generation = 0
        self._starting_generation: int | None = None

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._recorder is not None

    def start(self) -> None:
        self.cleanup()

        with self._lifecycle_lock:
            self._session_generation += 1
            generation = self._session_generation
            self._starting_generation = generation
            self._pcm_data = None

        config = self._config_provider()
        recorder = None
        started = False
        try:
            recorder = self._recorder_factory(
                on_audio_level=self._handle_audio_level,
            )
            session_audio_config = deepcopy(config.audio)
            start_session = getattr(
                recorder,
                "start_capture_session",
                None,
            )
            if callable(start_session):
                start_session(session_audio_config)
            else:
                # Compatibility for injected recorders that predate atomic
                # capture plans. Production AudioRecorder takes the branch
                # above so no mixed old/new configuration can reach a test.
                set_capture_backend = getattr(
                    recorder,
                    "set_capture_backend",
                    None,
                )
                if callable(set_capture_backend):
                    set_capture_backend(session_audio_config.capture_backend)
                recorder.set_gain_mode(session_audio_config.gain_mode)
                recorder.set_gain(session_audio_config.gain)
                recorder.set_highpass_filter(
                    session_audio_config.highpass_filter
                )
                recorder.set_highpass_freq(
                    session_audio_config.highpass_freq
                )
                recorder.set_soft_limiter(
                    session_audio_config.soft_limiter
                )
                recorder.start()
            started = True
            self._emit_input_status(recorder)
            timer = self._timer_factory(
                self._auto_stop_seconds,
                lambda: self._auto_stop(generation),
            )
            if hasattr(timer, "daemon"):
                timer.daemon = True
        except Exception as exc:
            self._emit_input_status(recorder)
            with self._lifecycle_lock:
                report_error = (
                    self._session_generation == generation
                    and self._starting_generation == generation
                )
                if self._starting_generation == generation:
                    self._starting_generation = None
            if started and recorder is not None:
                try:
                    recorder.stop()
                except Exception:
                    pass
            if report_error:
                self._emit_error(
                    format_microphone_start_error(
                        getattr(getattr(config, "ui", None), "language", "en"),
                        exc,
                    )
                )
            return

        with self._lifecycle_lock:
            accepted = (
                self._session_generation == generation
                and self._starting_generation == generation
            )
            if accepted:
                self._starting_generation = None
                self._recorder = recorder
                self._timer = timer

        if not accepted:
            timer.cancel()
            try:
                recorder.stop()
            except Exception:
                pass
            return

        timer.start()
        with self._lifecycle_lock:
            still_active = (
                self._session_generation == generation
                and self._recorder is recorder
            )
        if still_active and self._on_started is not None:
            self._on_started()

    def stop(self) -> None:
        self._stop(expected_generation=None)

    def _stop(self, *, expected_generation: int | None) -> None:
        with self._lifecycle_lock:
            if (
                expected_generation is not None
                and expected_generation != self._session_generation
            ):
                return
            recorder = self._recorder
            timer = self._timer
            if recorder is None:
                # A manual stop during startup invalidates the unpublished
                # recorder. Repeated stop calls while another caller owns the
                # recorder are idempotent and must not invalidate its result.
                if (
                    expected_generation is None
                    and self._starting_generation is not None
                ):
                    self._session_generation += 1
                    self._starting_generation = None
                return
            self._recorder = None
            self._timer = None
            self._session_generation += 1
            completion_generation = self._session_generation

        if timer is not None:
            timer.cancel()

        try:
            pcm_data = recorder.stop()
            with self._lifecycle_lock:
                publish = (
                    self._session_generation == completion_generation
                    and self._recorder is None
                )
                if publish:
                    self._pcm_data = pcm_data
            if publish:
                self._emit_input_status(recorder)
            if publish and self._on_complete is not None:
                self._on_complete()
        except Exception as exc:
            with self._lifecycle_lock:
                report_error = (
                    self._session_generation == completion_generation
                    and self._recorder is None
                )
            if report_error:
                self._emit_error(str(exc))

    def play(self) -> None:
        with self._lifecycle_lock:
            pcm_data = self._pcm_data
        if not pcm_data:
            return

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(OUTPUT_CHANNELS)
            wav_file.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(OUTPUT_SAMPLE_RATE_HZ)
            wav_file.writeframes(pcm_data)

        if self._on_playback is not None:
            self._on_playback(base64.b64encode(buffer.getvalue()).decode("ascii"))

    def cleanup(self) -> None:
        with self._lifecycle_lock:
            self._session_generation += 1
            self._starting_generation = None
            timer = self._timer
            recorder = self._recorder
            self._timer = None
            self._recorder = None
            self._pcm_data = None
        if timer is not None:
            timer.cancel()
        if recorder is not None:
            try:
                recorder.stop()
            except Exception:
                pass

    def handle_device_changed(self) -> None:
        if not self.is_running:
            return
        self.cleanup()
        if self._device_changed_error is not None:
            self._emit_error(self._device_changed_error())

    def apply_audio_setting(self, key: object, value: object) -> None:
        if not isinstance(key, str):
            return
        with self._lifecycle_lock:
            recorder = self._recorder
            if recorder is None:
                return
            if key == "audio.gain_mode":
                recorder.set_gain_mode(str(value))
            elif key == "audio.capture_backend":
                setter = getattr(recorder, "set_capture_backend", None)
                if callable(setter):
                    setter(str(value))
            elif key == "audio.gain":
                recorder.set_gain(float(value))
            elif key == "audio.highpass_filter":
                recorder.set_highpass_filter(bool(value))
            elif key == "audio.highpass_freq":
                recorder.set_highpass_freq(int(value))
            elif key == "audio.soft_limiter":
                recorder.set_soft_limiter(bool(value))

    def _auto_stop(self, generation: int) -> None:
        self._stop(expected_generation=generation)

    def _handle_audio_level(self, rms: float) -> None:
        if self._on_level is not None:
            self._on_level(rms)

    def _emit_error(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(message)

    def _emit_input_status(self, recorder: object | None) -> None:
        if self._on_input_status is None or recorder is None:
            return
        status = getattr(recorder, "input_status", None)
        if isinstance(status, dict):
            self._on_input_status(status)
