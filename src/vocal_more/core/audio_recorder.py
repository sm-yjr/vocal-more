"""Audio recording module using sounddevice."""

import io
import os
from pathlib import Path
import platform
import threading
import time
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
_PORTAUDIO_RESET_LOCK = threading.Lock()
_STREAM_RELEASE_THREAD_NAME = "vocal-more-audio-stream-release"
_STREAM_START_THREAD_NAME = "vocal-more-audio-stream-start"
_DEFAULT_START_TIMEOUT_SECONDS = 1.5
_MAX_MACOS_ARRAY_CHANNELS = 3
_MACOS_ARRAY_DEVICE_MARKERS = (
    "macbook",
    "imac",
    "studio display",
    "mac studio",
    "mac pro",
    "mac mini",
    "built-in microphone",
    "internal microphone",
    "内建麦克风",
    "内置麦克风",
    "麦克风阵列",
)


class AudioRecorderStartError(RuntimeError):
    """Raised when microphone startup fails after all recovery attempts."""

    def __init__(
        self,
        details: str,
        *,
        device_change_detected: bool = False,
        startup_timed_out: bool = False,
    ):
        super().__init__(details)
        self.device_change_detected = device_change_detected
        self.startup_timed_out = startup_timed_out


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
        self._start_timeout = max(0.01, float(start_timeout))

        self._stream: Optional[sd.InputStream] = None
        self._stream_release_thread: Optional[threading.Thread] = None
        self._stream_start_thread: Optional[threading.Thread] = None
        self._start_generation = 0
        self._array_processing_active = False
        self._audio_buffer: list[bytes] = []
        self._is_recording = False
        self._lock = threading.Lock()
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

        # Built-in Mac microphone arrays may expose two or three capsules. Mix
        # coherent speech before the voice pipeline while keeping the ASR wire
        # format mono. This also fixes configured stereo devices producing
        # invalid interleaved data when the high-pass filter is disabled.
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
        audio_data = (processed * 32767).astype(np.int16).tobytes()

        with self._lock:
            if not self._is_recording:
                return
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
            if self._stream_start_thread and self._stream_start_thread.is_alive():
                raise AudioRecorderStartError(
                    "A previous microphone startup is still blocked in CoreAudio",
                    startup_timed_out=True,
                )
            self._audio_buffer = []
            self._is_recording = True
            self._hp_prev_in = 0.0
            self._hp_prev_out = 0.0
            self._array_processing_active = False
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
            if stream is not None:
                self._release_stream_async(stream)
            raise AudioRecorderStartError(
                f"Microphone startup exceeded {self._start_timeout:.2f}s",
                startup_timed_out=True,
            )

        error = result_box.get("error")
        if error is not None:
            with self._lock:
                self._is_recording = False
                self._array_processing_active = False
            if isinstance(error, AudioRecorderStartError):
                raise error
            raise AudioRecorderStartError(str(error)) from error

    def _run_stream_start(
        self,
        generation: int,
        done: threading.Event,
        result_box: dict[str, object],
    ) -> None:
        """Own one potentially blocking native startup attempt."""
        stream = None
        try:
            stream = self._start_stream_with_recovery()
        except Exception as exc:
            result_box["error"] = exc
        else:
            with self._lock:
                accepted = (
                    generation == self._start_generation and self._is_recording
                )
                if accepted:
                    self._stream = stream
            if not accepted and stream is not None:
                self._release_stream_async(stream)
        finally:
            with self._lock:
                if self._stream_start_thread is threading.current_thread():
                    self._stream_start_thread = None
            done.set()

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

        with self._lock:
            self._is_recording = False
            self._start_generation += 1
            stream = self._stream
            self._stream = None
            self._array_processing_active = False
            audio_data = b"".join(self._audio_buffer)
            self._audio_buffer = []

        if stream is not None:
            self._release_stream_async(stream)

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

    def _release_stream_async(self, stream) -> None:
        release_thread = threading.Thread(
            target=self._release_stream,
            args=(stream,),
            name=_STREAM_RELEASE_THREAD_NAME,
            daemon=True,
        )
        self._stream_release_thread = release_thread
        release_thread.start()

    @property
    def array_processing_active(self) -> bool:
        """Whether the active stream uses multiple Mac array capsules."""
        with self._lock:
            return self._array_processing_active

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

        if self._use_config_device:
            self._device_name = get_config().audio.input_device
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
        capture_channels, array_processing = self._capture_profile(device_index)
        try:
            stream = self._create_started_stream(device_index, capture_channels)
        except sd.PortAudioError:
            if not array_processing:
                raise
            print(
                "[AudioRecorder] Raw Mac microphone-array format unavailable; "
                "using the system beamformed mono input"
            )
            stream = self._create_started_stream(device_index, 1)
            array_processing = False
        with self._lock:
            self._array_processing_active = array_processing
        return stream

    def _create_started_stream(self, device_index: Optional[int], channels: int):
        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=channels,
            blocksize=self.blocksize,
            dtype=np.float32,
            callback=self._audio_callback,
            device=device_index,
        )
        try:
            stream.start()
        except Exception:
            self._release_stream_async(stream)
            raise
        return stream

    def _capture_profile(self, device_index: Optional[int]) -> tuple[int, bool]:
        """Choose physical capture channels without changing mono ASR output."""
        device = self._device_info(device_index)
        if device is None:
            return max(1, self.channels), False

        max_channels = max(1, int(device.get("max_input_channels", 1)))
        requested_channels = min(max(1, self.channels), max_channels)
        if not self._is_macos_builtin_array(device):
            return requested_channels, False

        capture_channels = min(max_channels, _MAX_MACOS_ARRAY_CHANNELS)
        return capture_channels, capture_channels > 1

    @staticmethod
    def _is_macos_builtin_array(device: dict) -> bool:
        if platform.system() != "Darwin":
            return False
        if int(device.get("max_input_channels", 0)) < 2:
            return False
        name = str(device.get("name", "")).strip().lower()
        return any(marker in name for marker in _MACOS_ARRAY_DEVICE_MARKERS)

    @staticmethod
    def _coherent_array_downmix(indata: np.ndarray) -> np.ndarray:
        """Polarity-align coherent array channels and return float32 mono.

        macOS normally performs device-level beamforming for its built-in
        microphones. Some CoreAudio devices expose the capsules separately,
        though. A raw mean can cancel speech when a capsule has inverted
        polarity, while selecting channel zero throws away spatial noise
        reduction. This coherence gate keeps speech-correlated capsules,
        aligns polarity, and averages them. Uncorrelated channels are excluded
        so an unrelated noisy input cannot dominate the mix.
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
        # Pick the capsule most coherent with the rest, rather than the loudest
        # capsule, which is often the one nearest a fan or keyboard.
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
