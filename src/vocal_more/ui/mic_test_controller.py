"""Own the settings-window microphone test lifecycle."""

from __future__ import annotations

import base64
import io
import threading
import wave
from typing import Callable, Optional


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
        self._device_changed_error = device_changed_error
        self._auto_stop_seconds = auto_stop_seconds

        self._recorder = None
        self._pcm_data: bytes | None = None
        self._timer = None

    @property
    def is_running(self) -> bool:
        return self._recorder is not None

    def start(self) -> None:
        self.cleanup()

        config = self._config_provider()
        try:
            self._recorder = self._recorder_factory(
                on_audio_level=self._handle_audio_level,
                device=config.audio.input_device,
            )
            self._recorder.set_gain(config.audio.gain)
            self._recorder.set_highpass_filter(config.audio.highpass_filter)
            self._recorder.set_highpass_freq(config.audio.highpass_freq)
            self._recorder.set_soft_limiter(config.audio.soft_limiter)
            self._recorder.start()
        except Exception as exc:
            self._recorder = None
            self._emit_error(str(exc))
            return

        if self._on_started is not None:
            self._on_started()

        self._timer = self._timer_factory(self._auto_stop_seconds, self._auto_stop)
        if hasattr(self._timer, "daemon"):
            self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        if self._recorder is None:
            return

        try:
            self._pcm_data = self._recorder.stop()
            self._recorder = None
            if self._on_complete is not None:
                self._on_complete()
        except Exception as exc:
            self._recorder = None
            self._emit_error(str(exc))

    def play(self) -> None:
        if not self._pcm_data:
            return

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(self._pcm_data)

        if self._on_playback is not None:
            self._on_playback(base64.b64encode(buffer.getvalue()).decode("ascii"))

    def cleanup(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        self._pcm_data = None

    def handle_device_changed(self) -> None:
        if not self.is_running:
            return
        self.cleanup()
        if self._device_changed_error is not None:
            self._emit_error(self._device_changed_error())

    def apply_audio_setting(self, key: object, value: object) -> None:
        if self._recorder is None or not isinstance(key, str):
            return
        if key == "audio.gain":
            self._recorder.set_gain(float(value))
        elif key == "audio.highpass_filter":
            self._recorder.set_highpass_filter(bool(value))
        elif key == "audio.highpass_freq":
            self._recorder.set_highpass_freq(int(value))
        elif key == "audio.soft_limiter":
            self._recorder.set_soft_limiter(bool(value))

    def _auto_stop(self) -> None:
        self.stop()

    def _handle_audio_level(self, rms: float) -> None:
        if self._on_level is not None:
            self._on_level(rms)

    def _emit_error(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(message)
