"""Audio recording module using sounddevice."""

import io
import threading
import wave
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from ..config import get_config

_PORTAUDIO_RECOVERY_MARKERS = (
    "-9986",
    "-10851",
    "painternalerror",
    "auhal",
    "invalidpropertyvalue",
)


class AudioRecorderStartError(RuntimeError):
    """Raised when microphone startup fails after all recovery attempts."""

    def __init__(self, details: str, *, device_change_detected: bool = False):
        super().__init__(details)
        self.device_change_detected = device_change_detected


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
    ):
        """Initialize the audio recorder.

        Args:
            sample_rate: Sample rate in Hz (default from config: 16000)
            channels: Number of audio channels (default from config: 1)
            blocksize: Number of frames per buffer (default from config: 1600)
            on_audio_chunk: Callback for real-time audio chunks
            on_audio_level: Callback for real-time audio RMS level (0.0~1.0)
            device: Input device name (None = from config, then system default)
        """
        config = get_config()
        self.sample_rate = sample_rate or config.audio.sample_rate
        self.channels = channels or config.audio.channels
        self.blocksize = blocksize or config.audio.blocksize
        self.on_audio_chunk = on_audio_chunk
        self._on_audio_level = on_audio_level
        self._use_config_device = device is None
        self._device_name: Optional[str] = device if device is not None else config.audio.input_device
        self._gain: float = config.audio.gain
        self._highpass_filter: bool = config.audio.highpass_filter
        self._soft_limiter: bool = config.audio.soft_limiter

        self._stream: Optional[sd.InputStream] = None
        self._audio_buffer: list[bytes] = []
        self._is_recording = False
        self._lock = threading.Lock()

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
        if status:
            print(f"Audio status: {status}")

        with self._lock:
            is_recording = self._is_recording

        if not is_recording:
            return

        highpass_filter = self._highpass_filter
        gain = self._gain
        soft_limiter = self._soft_limiter
        on_audio_chunk = self.on_audio_chunk
        on_audio_level = self._on_audio_level

        # 1. High-pass filter: remove <80Hz rumble (fans, hum, plosives)
        if highpass_filter:
            mono = indata[:, 0].copy()
            for i in range(len(mono)):
                x = mono[i]
                self._hp_prev_out = self._hp_alpha * (self._hp_prev_out + x - self._hp_prev_in)
                self._hp_prev_in = x
                mono[i] = self._hp_prev_out
            filtered = mono.reshape(-1, 1)
        else:
            filtered = indata

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
        audio_data = (processed * 32767).astype(np.int16).tobytes()

        with self._lock:
            if self._is_recording:
                self._audio_buffer.append(audio_data)

        # Call real-time callback if set
        if on_audio_chunk:
            on_audio_chunk(audio_data)

        # Call audio level callback (use post-gain RMS for visualization)
        if on_audio_level:
            if gain != 1.0 and rms > 0:
                rms = min(1.0, rms * gain)
            on_audio_level(rms)

    def start(self) -> None:
        """Start recording audio.

        Raises:
            AudioRecorderStartError: If no audio device could be opened.
        """
        with self._lock:
            if self._is_recording:
                return
            self._audio_buffer = []
            self._is_recording = True
            self._hp_prev_in = 0.0
            self._hp_prev_out = 0.0

        try:
            self._start_stream_with_recovery()
        except Exception as exc:
            with self._lock:
                self._is_recording = False
            if isinstance(exc, AudioRecorderStartError):
                raise
            raise AudioRecorderStartError(str(exc)) from exc

    def stop(self) -> bytes:
        """Stop recording and return PCM audio data.

        Returns:
            Raw PCM audio data (int16, mono, 16kHz)
        """
        with self._lock:
            self._is_recording = False

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            audio_data = b"".join(self._audio_buffer)
            self._audio_buffer = []

        return audio_data

    def is_recording(self) -> bool:
        """Check if currently recording."""
        with self._lock:
            return self._is_recording

    def get_pcm_data(self) -> bytes:
        """Get current recorded PCM data without stopping."""
        with self._lock:
            return b"".join(self._audio_buffer)

    def _start_stream_with_recovery(self) -> None:
        """Open the input stream, then rebuild PortAudio once if it is wedged."""
        if self._use_config_device:
            self._device_name = get_config().audio.input_device
        device_index = self._resolve_device()

        try:
            self._open_stream_with_fallback(device_index)
            return
        except Exception as initial_error:
            if self._should_retry_after_portaudio_reset(initial_error):
                if self._recover_portaudio_state(initial_error):
                    try:
                        self._open_stream_with_fallback(self._resolve_device())
                        return
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

    def _open_stream_with_fallback(self, device_index: Optional[int]) -> None:
        try:
            self._open_stream(device_index)
        except sd.PortAudioError:
            if device_index is None:
                raise
            print(f"Device '{self._device_name}' failed, falling back to default")
            if self._use_config_device:
                self._clear_unavailable_device()
            self._open_stream(None)

    def _open_stream(self, device_index: Optional[int]) -> None:
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=self.blocksize,
            dtype=np.float32,
            callback=self._audio_callback,
            device=device_index,
        )
        stream.start()
        self._stream = stream

    def _should_retry_after_portaudio_reset(self, error: Exception) -> bool:
        if isinstance(error, sd.PortAudioError):
            return True
        details = str(error).lower()
        return any(marker in details for marker in _PORTAUDIO_RECOVERY_MARKERS)

    def _recover_portaudio_state(self, error: Exception) -> bool:
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if not callable(terminate) or not callable(initialize):
            print(
                "[AudioRecorder] PortAudio reset hooks unavailable after startup "
                f"failure: {error}"
            )
            return False

        print(f"[AudioRecorder] Reinitializing PortAudio after startup failure: {error}")
        self._close_stream_quietly()
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
        self._device_name = name

    def set_gain(self, gain: float) -> None:
        """Update software gain for subsequent callbacks immediately."""
        self._gain = gain

    def set_highpass_filter(self, enabled: bool) -> None:
        self._highpass_filter = enabled

    def set_highpass_freq(self, freq: int) -> None:
        self._hp_freq = freq
        self._hp_alpha = self._calc_hp_alpha(freq)

    def set_soft_limiter(self, enabled: bool) -> None:
        self._soft_limiter = enabled

    def _calc_hp_alpha(self, freq: int) -> float:
        import math
        if freq <= 0:
            return 1.0
        return 1.0 / (1.0 + 2.0 * math.pi * freq / self.sample_rate)

    @staticmethod
    def list_input_devices() -> list[dict]:
        """Return all available input devices.

        Returns:
            List of dicts with 'index', 'name', 'is_default' keys.
        """
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
