"""Tests for audio recorder callback behavior."""

import io
import threading
import time
from types import SimpleNamespace
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest
import yaml


@pytest.fixture(autouse=True)
def _authorized_microphone_permission(monkeypatch):
    """Recorder unit tests must not depend on the developer Mac's TCC state."""
    import vocal_more.core.audio_recorder as audio_recorder_module

    monkeypatch.setattr(
        audio_recorder_module,
        "microphone_permission_status",
        lambda: "authorized",
    )


def _write_config_with_input_device(tmp_path, monkeypatch, device_name):
    from vocal_more.config import Config, reload_config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump({"audio": {"input_device": device_name}}, f)

    reload_config()
    return config_path


def _fake_default_device(index=0):
    return type("FakeDefault", (), {"device": [index, None]})()


def _write_pcm_wav(path, samples, *, sample_rate=16000):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(np.asarray(samples, dtype=np.int16).tobytes())


def test_benchmark_wav_replay_is_disabled_without_trace_opt_in(
    tmp_path,
    monkeypatch,
):
    from vocal_more.core.audio_recorder import AudioRecorder

    audio_path = tmp_path / "sample.wav"
    _write_pcm_wav(audio_path, [1000] * 640)
    monkeypatch.setenv("VOCAL_MORE_BENCHMARK_AUDIO_FILE", str(audio_path))
    monkeypatch.delenv("VOCAL_MORE_BENCHMARK_TRACE_DIR", raising=False)

    recorder = AudioRecorder()

    assert recorder.benchmark_audio_delivery == "physical_microphone"


def test_benchmark_wav_replay_uses_real_audio_pipeline_without_portaudio(
    tmp_path,
    monkeypatch,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    samples = np.asarray([1000, -1000] * 640, dtype=np.int16)
    audio_path = tmp_path / "sample.wav"
    _write_pcm_wav(audio_path, samples)
    monkeypatch.setenv("VOCAL_MORE_BENCHMARK_AUDIO_FILE", str(audio_path))
    monkeypatch.setenv("VOCAL_MORE_BENCHMARK_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setattr(
        audio_recorder_module.sd,
        "InputStream",
        lambda **_kwargs: pytest.fail("PortAudio must not open during replay"),
    )
    chunks = []
    levels = []
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=1,
        blocksize=640,
        on_audio_chunk=chunks.append,
        on_audio_level=levels.append,
    )
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False

    recorder.start()
    assert recorder.wait_for_benchmark_replay(timeout=1)
    actual = recorder.stop()

    assert recorder.benchmark_audio_delivery == "deterministic_wav_replay"
    assert len(chunks) == 2
    assert levels
    assert np.max(
        np.abs(
            np.frombuffer(actual, dtype=np.int16).astype(np.int32)
            - samples.astype(np.int32)
        )
    ) <= 1


def test_benchmark_wav_replay_rejects_incompatible_audio(
    tmp_path,
    monkeypatch,
):
    from vocal_more.core.audio_recorder import (
        AudioRecorder,
        AudioRecorderStartError,
    )

    audio_path = tmp_path / "stereo.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 320)
    monkeypatch.setenv("VOCAL_MORE_BENCHMARK_AUDIO_FILE", str(audio_path))
    monkeypatch.setenv("VOCAL_MORE_BENCHMARK_TRACE_DIR", str(tmp_path / "traces"))
    recorder = AudioRecorder()

    with pytest.raises(AudioRecorderStartError, match="mono"):
        recorder.start()

    assert recorder.is_recording() is False


def test_audio_callback_returns_early_when_not_recording():
    """The PortAudio callback should skip work once recording has stopped."""
    from vocal_more.core.audio_recorder import AudioRecorder

    chunks = []
    levels = []
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=1,
        blocksize=1600,
        on_audio_chunk=chunks.append,
        on_audio_level=levels.append,
    )
    recorder._is_recording = False

    recorder._audio_callback(
        np.ones((1600, 1), dtype=np.float32) * 0.25,
        1600,
        {},
        0,
    )

    assert chunks == []
    assert levels == []
    assert recorder._audio_buffer == []


def test_recorder_output_contract_is_mono_even_when_capture_requests_stereo():
    """Capture topology must never leak into the ASR/WAV wire format."""
    from vocal_more.core.audio_recorder import AudioRecorder

    chunks = []
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=2,
        blocksize=4,
        on_audio_chunk=chunks.append,
    )
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True

    recorder._audio_callback(
        np.asarray(
            [[0.1, 0.1], [0.2, 0.2], [-0.1, -0.1], [-0.2, -0.2]],
            dtype=np.float32,
        ),
        4,
        {},
        0,
    )

    assert recorder.channels == 1
    assert recorder.capture_channels == 2
    assert len(chunks[0]) == 4 * 2
    with wave.open(io.BytesIO(recorder.pcm_to_wav_bytes(chunks[0])), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getnframes() == 4


def test_apple_agc_bypasses_software_gain_to_prevent_double_amplification():
    from vocal_more.core.audio_recorder import AudioRecorder

    chunks = []
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=1,
        blocksize=4,
        on_audio_chunk=chunks.append,
    )
    recorder._gain = 8.0
    recorder._gain_mode = "automatic"
    recorder._apple_agc_active = True
    recorder._highpass_filter = False
    recorder._soft_limiter = True
    recorder._is_recording = True

    recorder._audio_callback(
        np.full((4, 1), 0.1, dtype=np.float32),
        4,
        {},
        0,
    )

    output = np.frombuffer(chunks[0], dtype=np.int16).astype(np.float32) / 32767
    assert np.allclose(output, 0.1, atol=1e-4)


def test_pcm_conversion_clips_small_converter_overshoot_instead_of_wrapping():
    from vocal_more.core.audio_recorder import AudioRecorder

    chunks = []
    recorder = AudioRecorder(on_audio_chunk=chunks.append)
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True

    recorder._audio_callback(
        np.asarray([[1.001], [-1.001]], dtype=np.float32),
        2,
        {},
        0,
    )

    assert np.array_equal(
        np.frombuffer(chunks[0], dtype=np.int16),
        np.asarray([32767, -32767], dtype=np.int16),
    )


def test_audio_callback_drops_chunk_if_stop_wins_during_processing():
    """A callback already in flight must not publish audio after stop."""
    from vocal_more.core.audio_recorder import AudioRecorder

    chunks = []
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=1,
        blocksize=1600,
        on_audio_chunk=chunks.append,
    )
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True

    original_encode = recorder._encode_pcm

    def stop_during_conversion(processed):
        result = original_encode(processed)
        recorder._is_recording = False
        return result

    recorder._encode_pcm = stop_during_conversion

    recorder._audio_callback(
        np.ones((1600, 1), dtype=np.float32),
        1600,
        {},
        0,
    )

    assert chunks == []
    assert recorder._audio_buffer == []


def test_stop_returns_pcm_while_portaudio_release_is_blocked():
    """A wedged CoreAudio stream must not pin the mode in STOPPING."""
    from vocal_more.core.audio_recorder import AudioRecorder

    release_gate = threading.Event()
    close_started = threading.Event()

    class BlockingStream:
        def abort(self):
            close_started.set()
            release_gate.wait(timeout=2.0)

        def close(self):
            return None

    recorder = AudioRecorder()
    recorder._is_recording = True
    recorder._stream = BlockingStream()
    recorder._audio_buffer = [b"\x01\x00" * 1600]

    started_at = time.monotonic()
    actual = recorder.stop()
    elapsed = time.monotonic() - started_at

    assert actual == b"\x01\x00" * 1600
    assert elapsed < 0.25
    assert recorder.is_recording() is False
    assert recorder._stream is None
    assert close_started.wait(timeout=0.5)

    release_gate.set()
    recorder._stream_release_thread.join(timeout=0.5)
    assert recorder._stream_release_thread.is_alive() is False


def test_stop_serializes_pcm_snapshot_with_inflight_audio_observer():
    """A chunk is either published before stop returns or rejected entirely."""
    from vocal_more.core.audio_recorder import AudioRecorder

    observer_entered = threading.Event()
    release_observer = threading.Event()
    observer_finished = threading.Event()

    def on_audio_chunk(_chunk):
        observer_entered.set()
        assert release_observer.wait(timeout=1.0)
        observer_finished.set()

    recorder = AudioRecorder(on_audio_chunk=on_audio_chunk)
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True
    recorder._stream = type(
        "Stream",
        (),
        {
            "abort": lambda self: None,
            "close": lambda self: None,
        },
    )()
    callback_thread = threading.Thread(
        target=lambda: recorder._audio_callback(
            np.ones((640, 1), dtype=np.float32) * 0.1,
            640,
            {},
            0,
        )
    )
    callback_thread.start()
    assert observer_entered.wait(timeout=0.5)

    result = []
    stop_finished = threading.Event()

    def stop_recording():
        result.append(recorder.stop())
        stop_finished.set()

    stop_thread = threading.Thread(target=stop_recording)
    stop_thread.start()
    assert not stop_finished.wait(timeout=0.05)

    release_observer.set()
    callback_thread.join(timeout=0.5)
    stop_thread.join(timeout=0.5)

    assert observer_finished.is_set()
    assert not callback_thread.is_alive()
    assert not stop_thread.is_alive()
    assert result == [b"\xcc\x0c" * 640]


def test_stop_includes_a_late_inflight_observer_fault_in_last_session():
    from vocal_more.core.audio_recorder import AudioRecorder

    observer_entered = threading.Event()
    release_observer = threading.Event()

    def on_audio_chunk(_chunk):
        observer_entered.set()
        assert release_observer.wait(timeout=1.0)
        raise RuntimeError("late ASR handoff failure")

    recorder = AudioRecorder(on_audio_chunk=on_audio_chunk)
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True
    recorder._input_status.update(
        {
            "phase": "active",
            "gain_control_verified": True,
            "voice_processing_enabled_observed": True,
            "agc_enabled_observed": False,
        }
    )
    recorder._stream = type(
        "Stream",
        (),
        {"abort": lambda self: None, "close": lambda self: None},
    )()
    callback_thread = threading.Thread(
        target=lambda: recorder._audio_callback(
            np.full((8, 1), 0.1, dtype=np.float32),
            8,
            {},
            0,
        )
    )
    callback_thread.start()
    assert observer_entered.wait(timeout=0.5)

    stop_thread = threading.Thread(target=recorder.stop)
    stop_thread.start()
    release_observer.set()
    callback_thread.join(timeout=0.5)
    stop_thread.join(timeout=0.5)

    completed = recorder.input_status["last_session"]
    assert completed["runtime_fault_count"] == 1
    assert completed["runtime_fault_code"] == "audio_chunk_observer_failed"
    assert completed["gain_control_verified"] is False


def test_stop_drains_native_converter_tail_before_pcm_snapshot():
    """The final partial 16 kHz block must not disappear at key release."""
    from vocal_more.core.audio_recorder import AudioRecorder

    recorder = AudioRecorder(sample_rate=16000, channels=1, blocksize=640)
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True

    class DrainingStream:
        def drain(self):
            recorder._audio_callback(
                np.asarray([[0.25], [-0.25], [0.5]], dtype=np.float32),
                3,
                {},
                0,
            )

        def abort(self):
            return None

        def close(self):
            return None

    recorder._stream = DrainingStream()

    pcm = recorder.stop()

    assert len(pcm) == 3 * 2
    assert np.array_equal(
        np.frombuffer(pcm, dtype=np.int16),
        np.asarray([8191, -8191, 16383], dtype=np.int16),
    )


def test_stop_bounds_a_wedged_native_drain_and_marks_session_unverified(
    monkeypatch,
):
    """A CoreAudio drain stall must not pin the command coordinator."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    monkeypatch.setattr(
        audio_recorder_module,
        "_NATIVE_DRAIN_TIMEOUT_SECONDS",
        0.01,
    )
    drain_started = threading.Event()
    release_gate = threading.Event()
    diagnostics_calls = 0

    class BlockingNativeStream:
        backend_name = "objective_cpp"

        @property
        def diagnostics(self):
            nonlocal diagnostics_calls
            diagnostics_calls += 1
            if drain_started.is_set():
                # Model NativeMacOSVoiceProcessingStream: stop owns the same
                # foreign-call lock that diagnostics would need.
                release_gate.wait(timeout=1.0)
            return {
                "backend": "objective_cpp",
                "source_sample_rate_hz": 48000,
                "source_channels": 1,
                "voice_processing_enabled": True,
                "agc_enabled": True,
                "runtime_fault_count": 0,
            }

        def drain(self):
            drain_started.set()
            release_gate.wait(timeout=2.0)

        def abort(self):
            release_gate.set()

        def close(self):
            return None

    recorder = AudioRecorder()
    recorder._is_recording = True
    recorder._stream = BlockingNativeStream()
    recorder._input_status.update(
        {
            "phase": "active",
            "requested_gain_mode": "automatic",
            "gain_control": "apple_agc",
        }
    )
    recorder._audio_buffer = [b"\x01\x00"]

    started_at = time.monotonic()
    pcm = recorder.stop()
    elapsed = time.monotonic() - started_at

    assert drain_started.is_set()
    assert pcm == b"\x01\x00"
    assert elapsed < 0.25
    assert diagnostics_calls == 0
    last_session = recorder.input_status["last_session"]
    assert last_session["gain_control_verified"] is False
    assert last_session["runtime_fault_code"] == "native_drain_timeout"
    assert last_session["runtime_fault_count"] == 1

    release_gate.set()
    recorder._stream_release_thread.join(timeout=0.5)


def test_input_status_uses_cached_snapshot_while_native_drain_is_in_flight():
    """Status UI must not wait on the foreign-call lock owned by native drain."""
    from vocal_more.core.audio_recorder import AudioRecorder

    drain_started = threading.Event()
    release_drain = threading.Event()
    status_done = threading.Event()
    diagnostics_calls = 0
    status_box = {}

    class BlockingNativeStream:
        backend_name = "objective_cpp"

        @property
        def diagnostics(self):
            nonlocal diagnostics_calls
            diagnostics_calls += 1
            if drain_started.is_set():
                release_drain.wait(timeout=1.0)
            return {
                "backend": "objective_cpp",
                "voice_processing_enabled": True,
                "agc_enabled": True,
                "runtime_fault_count": 0,
            }

        def drain(self):
            drain_started.set()
            release_drain.wait(timeout=1.0)

        def abort(self):
            return None

        def close(self):
            return None

    recorder = AudioRecorder()
    recorder._is_recording = True
    recorder._stream = BlockingNativeStream()
    recorder._input_status.update(
        {
            "phase": "active",
            "native_backend": "objective_cpp",
            "requested_gain_mode": "automatic",
        }
    )

    stop_thread = threading.Thread(target=recorder.stop, daemon=True)
    stop_thread.start()
    assert drain_started.wait(timeout=0.5)

    def read_status():
        status_box["status"] = recorder.input_status
        status_done.set()

    status_thread = threading.Thread(target=read_status, daemon=True)
    status_thread.start()
    completed_while_draining = status_done.wait(timeout=0.05)
    calls_while_draining = diagnostics_calls

    release_drain.set()
    status_thread.join(timeout=0.5)
    stop_thread.join(timeout=0.5)
    if recorder._stream_release_thread is not None:
        recorder._stream_release_thread.join(timeout=0.5)

    assert completed_while_draining is True
    assert calls_while_draining == 0
    assert status_box["status"]["native_backend"] == "objective_cpp"


def test_input_status_refreshes_stream_diagnostics_during_active_capture():
    """The drain guard must not turn normal active diagnostics into stale data."""
    from vocal_more.core.audio_recorder import AudioRecorder

    diagnostics_calls = 0

    class ActiveNativeStream:
        backend_name = "objective_cpp"

        @property
        def diagnostics(self):
            nonlocal diagnostics_calls
            diagnostics_calls += 1
            return {
                "backend": "objective_cpp",
                "source_sample_rate_hz": 48000.0,
                "source_channels": 1,
                "voice_processing_enabled": True,
                "agc_enabled": True,
                "runtime_fault_count": 0,
            }

    recorder = AudioRecorder()
    recorder._is_recording = True
    recorder._stream = ActiveNativeStream()
    recorder._input_status.update(
        {
            "phase": "active",
            "native_backend": "pending",
            "requested_gain_mode": "automatic",
        }
    )

    status = recorder.input_status

    assert diagnostics_calls == 1
    assert status["native_backend"] == "objective_cpp"
    assert status["source_sample_rate_hz"] == 48000.0


def test_start_timeout_keeps_dictation_control_responsive_and_releases_late_stream(
    monkeypatch,
):
    """A blocked CoreAudio open must time out and discard its late stream."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    open_started = threading.Event()
    allow_open = threading.Event()
    stream_closed = threading.Event()

    class BlockingStream:
        def __init__(self, **_kwargs):
            return None

        def start(self):
            open_started.set()
            allow_open.wait(timeout=2.0)

        def abort(self):
            return None

        def close(self):
            stream_closed.set()

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": BlockingStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro麦克风",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: False,
    )

    recorder = AudioRecorder(start_timeout=0.05)
    started_at = time.monotonic()
    with pytest.raises(AudioRecorderStartError) as exc_info:
        recorder.start()
    elapsed = time.monotonic() - started_at

    assert open_started.is_set()
    assert exc_info.value.startup_timed_out is True
    assert elapsed < 0.25
    assert recorder.is_recording() is False
    assert recorder._stream is None

    allow_open.set()
    assert stream_closed.wait(timeout=0.5)
    assert recorder._stream is None


def test_default_start_deadline_covers_observed_slow_coreaudio_routes():
    """Normal external-display routes can need more than the former 1.5 seconds."""
    from vocal_more.core.audio_recorder import AudioRecorder

    recorder = AudioRecorder()

    assert recorder._start_timeout == 3.0


def test_start_deadline_covers_blocked_device_enumeration(monkeypatch):
    """CoreAudio discovery must run behind the same hard startup deadline."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    block_queries = False
    query_entered = threading.Event()
    release_query = threading.Event()
    late_stream_closed = threading.Event()

    def query_devices():
        if block_queries:
            query_entered.set()
            release_query.wait(timeout=2.0)
        return [{"index": 0, "name": "USB Mic", "max_input_channels": 1}]

    class LateStream:
        samplerate = 16000

        def __init__(self, **_kwargs):
            return None

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            late_stream_closed.set()

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": LateStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(query_devices),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: False,
    )

    recorder = AudioRecorder(device="USB Mic", start_timeout=0.03)
    block_queries = True
    finished = threading.Event()
    errors = []

    def start_recorder():
        try:
            recorder.start()
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    start_thread = threading.Thread(target=start_recorder)
    start_thread.start()
    assert query_entered.wait(timeout=0.5)
    returned_before_query = finished.wait(timeout=0.15)
    release_query.set()
    start_thread.join(timeout=0.5)

    assert returned_before_query is True
    assert len(errors) == 1
    assert isinstance(errors[0], AudioRecorderStartError)
    assert errors[0].startup_timed_out is True
    assert late_stream_closed.wait(timeout=0.5)


def test_constructor_never_blocks_on_device_enumeration(monkeypatch):
    """Mode initialization must not wait indefinitely for CoreAudio discovery."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    query_entered = threading.Event()
    release_query = threading.Event()

    def blocking_query_devices():
        query_entered.set()
        release_query.wait(timeout=2.0)
        return [{"index": 0, "name": "USB Mic", "max_input_channels": 1}]

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(blocking_query_devices),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    constructed = []
    finished = threading.Event()

    def construct_recorder():
        constructed.append(AudioRecorder(device="USB Mic"))
        finished.set()

    constructor_thread = threading.Thread(target=construct_recorder)
    constructor_thread.start()
    returned_without_query = finished.wait(timeout=0.15)
    release_query.set()
    constructor_thread.join(timeout=0.5)

    assert returned_without_query is True
    assert query_entered.is_set() is False
    assert constructed[0].input_status["phase"] == "planned"
    assert constructed[0].input_status["native_backend"] == "pending"


def test_late_voice_processing_start_cannot_reactivate_agc_after_timeout(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    native_start_entered = threading.Event()
    allow_native_start = threading.Event()
    late_stream_closed = threading.Event()

    class BlockingVoiceStream:
        def start(self):
            native_start_entered.set()
            allow_native_start.wait(timeout=2.0)

        def abort(self):
            return None

        def close(self):
            late_stream_closed.set()

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": lambda **_kwargs: pytest.fail(
                "PortAudio should not open in this voice-processing timeout test"
            ),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **_kwargs: BlockingVoiceStream(),
    )

    recorder = AudioRecorder(start_timeout=0.03)
    recorder.set_capture_backend("voice_processing")
    recorder.set_gain_mode("automatic")
    with pytest.raises(AudioRecorderStartError) as exc_info:
        recorder.start()

    assert native_start_entered.is_set()
    assert exc_info.value.startup_timed_out is True
    assert recorder._apple_agc_active is False
    assert recorder.input_status["processing_active"] is False

    allow_native_start.set()
    assert late_stream_closed.wait(timeout=0.5)
    assert recorder._apple_agc_active is False
    assert recorder.input_status["processing_active"] is False
    assert recorder.input_status["echo_cancellation"] != "active"

def test_repeated_start_fails_fast_while_previous_native_open_is_still_blocked(
    monkeypatch,
):
    """Repeated hotkeys must not accumulate unkillable PortAudio open threads."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    allow_open = threading.Event()
    stream_count = 0

    class BlockingStream:
        def __init__(self, **_kwargs):
            nonlocal stream_count
            stream_count += 1

        def start(self):
            allow_open.wait(timeout=2.0)

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": BlockingStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1}
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder(start_timeout=0.03)
    with pytest.raises(AudioRecorderStartError):
        recorder.start()

    second_started_at = time.monotonic()
    with pytest.raises(AudioRecorderStartError) as exc_info:
        recorder.start()

    assert exc_info.value.startup_timed_out is True
    assert time.monotonic() - second_started_at < 0.02
    assert stream_count == 1
    allow_open.set()


def test_failed_stream_start_closes_unpublished_native_stream(monkeypatch):
    """A stream object created before start() fails must not leak CoreAudio handles."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    closed = threading.Event()

    class FailingStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            # Core Audio can invoke input before start() reports its failure.
            self.callback(
                np.ones((640, 1), dtype=np.float32) * 0.1,
                640,
                {},
                0,
            )
            raise ValueError("device refused to start")

        def abort(self):
            return None

        def close(self):
            closed.set()

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FailingStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(lambda: []),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    with pytest.raises(AudioRecorderStartError, match="device refused"):
        recorder.start()

    assert closed.wait(timeout=0.5)
    assert recorder.get_pcm_data() == b""


@pytest.mark.parametrize(
    ("requested_channels", "expected_channels", "array_active"),
    [(1, 1, False), (3, 3, True)],
)
def test_macos_builtin_microphone_honors_explicit_capture_channel_intent(
    monkeypatch,
    requested_channels,
    expected_channels,
    array_active,
):
    """Safe mono is default; multichannel mixing requires explicit intent."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    stream_kwargs = []

    class FakeStream:
        def __init__(self, **kwargs):
            stream_kwargs.append(kwargs)

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(2),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 2,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 3,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: False,
    )

    recorder = AudioRecorder(channels=requested_channels)
    recorder.start()

    assert stream_kwargs[0]["channels"] == expected_channels
    assert recorder.array_processing_active is array_active
    recorder.stop()


@pytest.mark.parametrize(
    ("requested_channels", "expected_mode", "expected_channels"),
    [(1, "system_managed_mono", 1), (3, "vocal_more_array", 3)],
)
def test_planned_macos_input_status_matches_explicit_capture_channel_intent(
    monkeypatch,
    requested_channels,
    expected_mode,
    expected_channels,
):
    """Idle status must describe the same topology the next start will use."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(2),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 2,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 3,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: False,
    )

    recorder = AudioRecorder(channels=requested_channels)
    assert recorder.refresh_planned_input_status() is True
    status = recorder.input_status

    assert status["phase"] == "planned"
    assert status["processing_mode"] == expected_mode
    assert status["capture_channels"] == expected_channels


def test_idle_runtime_config_refreshes_planned_status_and_preserves_last_session(
    monkeypatch,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    devices = [
        {
            "index": 1,
            "name": "MacBook Pro Microphone",
            "max_input_channels": 3,
        },
        {"index": 2, "name": "USB Mic", "max_input_channels": 1},
    ]
    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(1),
            "query_devices": staticmethod(lambda: devices),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: False,
    )

    recorder = AudioRecorder(device="MacBook Pro Microphone")
    recorder._last_input_session = {"phase": "completed", "device_name": "Old Mic"}
    recorder.set_device("USB Mic")
    recorder.set_gain_mode("manual")
    recorder.refresh_planned_input_status()

    status = recorder.input_status
    assert status["phase"] == "planned"
    assert status["device_name"] == "USB Mic"
    assert status["processing_mode"] == "standard"
    assert status["requested_gain_mode"] == "manual"
    assert status["gain_control"] == "software"
    assert status["last_session"] == {
        "phase": "completed",
        "device_name": "Old Mic",
    }


def test_macos_builtin_default_microphone_prefers_voice_processing_for_aec(
    monkeypatch,
):
    """Apple voice processing should supersede raw array capture for AEC."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    voice_streams = []
    delivered_phases = []

    class FakeVoiceProcessingStream:
        backend_name = "objective_cpp"
        delivers_processed_pcm = True

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False
            voice_streams.append(self)

        def start(self):
            # A running engine may produce PCM before start() returns. The
            # recorder must publish it only after getter verification/status.
            self.kwargs["pcm_callback"](b"\x01\x00", 0.1)
            self.started = True

        def abort(self):
            return None

        def close(self):
            return None

        @property
        def diagnostics(self):
            return type(
                "Diagnostics",
                (),
                {
                    "backend": "objective_cpp",
                    "source_sample_rate_hz": 48000.0,
                    "source_channels": 1,
                    "voice_processing_enabled": True,
                    "agc_enabled": True,
                    "start_verified": True,
                    "diagnostics_fresh": True,
                    "converter_name": "AVAudioConverter/vDSP",
                    "dropped_blocks": 0,
                    "preferred_microphone_mode": "voice_isolation",
                    "active_microphone_mode": "standard",
                },
            )()

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": lambda **_kwargs: pytest.fail(
                "PortAudio must not open when Apple voice processing is available"
            ),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(2),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 2,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 3,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **kwargs: FakeVoiceProcessingStream(**kwargs),
    )

    recorder = AudioRecorder(
        channels=1,
        on_audio_chunk=lambda _chunk: delivered_phases.append(
            recorder.input_status["phase"]
        ),
    )
    recorder.set_capture_backend("voice_processing")
    recorder.set_gain_mode("automatic")
    recorder.start()

    assert voice_streams[0].started is True
    assert delivered_phases == ["active"]
    assert voice_streams[0].kwargs["sample_rate"] == 16000
    assert voice_streams[0].kwargs["blocksize"] == recorder.blocksize
    assert voice_streams[0].kwargs["automatic_gain"] is True
    assert callable(voice_streams[0].kwargs["pcm_callback"])
    assert voice_streams[0].kwargs["gain"] == recorder._gain
    assert voice_streams[0].kwargs["highpass_filter"] is True
    assert voice_streams[0].kwargs["highpass_freq"] == recorder._hp_freq
    assert voice_streams[0].kwargs["soft_limiter"] is True
    assert recorder.input_status["phase"] == "active"
    assert recorder.input_status["voice_processing_enabled_observed"] is True
    assert recorder.input_status["agc_enabled_observed"] is True
    assert recorder.input_status["start_verified"] is True
    assert recorder.input_status["diagnostics_fresh"] is True
    assert recorder.input_status["gain_control_verified"] is True
    assert recorder.input_status["native_backend"] == "objective_cpp"
    assert recorder.input_status["source_sample_rate_hz"] == 48000.0
    assert recorder.input_status["preferred_microphone_mode"] == "voice_isolation"
    assert recorder.input_status["active_microphone_mode"] == "standard"
    assert recorder.input_status["device_name"] == "MacBook Pro Microphone"
    assert recorder.input_status["echo_cancellation"] == "active"
    assert recorder.input_status["gain_control"] == "apple_agc"
    recorder.stop()
    assert recorder.input_status["phase"] == "inactive"
    assert recorder.input_status["agc_enabled_observed"] is None
    assert recorder.input_status["last_session"]["phase"] == "completed"
    assert recorder.input_status["last_session"]["agc_enabled_observed"] is True
    assert recorder.input_status["last_session"]["start_verified"] is True
    assert recorder.input_status["last_session"]["diagnostics_fresh"] is True
    assert recorder.input_status["last_session"]["active_microphone_mode"] == (
        "standard"
    )


def test_native_pcm_callback_bypasses_python_gain_and_preserves_pcm():
    from vocal_more.core.audio_recorder import AudioRecorder

    delivered = []
    levels = []
    recorder = AudioRecorder(
        on_audio_chunk=delivered.append,
        on_audio_level=levels.append,
    )
    recorder._gain = 16.0
    recorder._soft_limiter = True
    recorder._is_recording = True
    pcm = b"\x00\x10\x00\xf0"

    recorder._native_pcm_callback(pcm, 0.375)

    assert recorder.get_pcm_data() == pcm
    assert delivered == [pcm]
    assert levels == [0.375]


@pytest.mark.parametrize("permission", ["denied", "restricted"])
def test_microphone_permission_failure_has_a_stable_error_code(
    monkeypatch,
    permission,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    monkeypatch.setattr(
        audio_recorder_module,
        "microphone_permission_status",
        lambda: permission,
    )
    monkeypatch.setattr(
        AudioRecorder,
        "_resolve_device",
        lambda self: pytest.fail("device enumeration must not run after TCC denial"),
    )

    with pytest.raises(AudioRecorderStartError) as captured:
        AudioRecorder().start()

    assert captured.value.code == f"microphone_permission_{permission}"
    assert captured.value.stage == "permission"
    assert captured.value.recoverable is False


def test_first_recording_requests_permission_without_starting_or_waiting(
    monkeypatch,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    requests = []
    stream_starts = []
    monkeypatch.setattr(
        audio_recorder_module,
        "microphone_permission_status",
        lambda: "not_determined",
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "request_microphone_access",
        lambda: requests.append("requested") or True,
    )
    monkeypatch.setattr(
        AudioRecorder,
        "_start_stream_with_recovery",
        lambda self: stream_starts.append("started"),
    )
    recorder = AudioRecorder()

    started_at = time.monotonic()
    with pytest.raises(AudioRecorderStartError) as first:
        recorder.start()
    elapsed = time.monotonic() - started_at
    with pytest.raises(AudioRecorderStartError) as second:
        recorder.start()

    assert elapsed < 0.5
    assert first.value.code == "microphone_permission_requested"
    assert first.value.stage == "permission"
    assert first.value.recoverable is True
    assert second.value.code == "microphone_permission_requested"
    assert requests == ["requested"]
    assert stream_starts == []
    assert recorder.input_status["phase"] == "awaiting_permission"


def test_authorized_retry_starts_only_after_permission_admission(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    permission = {"value": "not_determined"}
    requests = []
    stream = MagicMock()
    monkeypatch.setattr(
        audio_recorder_module,
        "microphone_permission_status",
        lambda: permission["value"],
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "request_microphone_access",
        lambda: requests.append("requested") or True,
    )
    monkeypatch.setattr(
        AudioRecorder,
        "_start_stream_with_recovery",
        lambda self: stream,
    )
    recorder = AudioRecorder()

    with pytest.raises(AudioRecorderStartError):
        recorder.start()
    permission["value"] = "authorized"
    recorder.start()

    assert requests == ["requested"]
    assert recorder.is_recording() is True
    recorder.stop()


def test_start_ready_cannot_overtake_the_startup_audio_barrier(monkeypatch):
    from vocal_more.core.audio_recorder import (
        AudioRecorder,
        _StartupAudioGate,
        _VerifiedStreamCandidate,
    )

    pcm = b"\x01\x00\x02\x00"
    stream = MagicMock()
    recorder = AudioRecorder(start_timeout=1.0)
    gate = _StartupAudioGate(
        float_callback=recorder._audio_callback,
        pcm_callback=recorder._native_pcm_callback,
    )
    gate.pcm_callback(pcm, 0.25)
    monkeypatch.setattr(
        recorder,
        "_start_stream_with_recovery",
        lambda: _VerifiedStreamCandidate(stream, gate),
    )

    returned = threading.Event()
    failure = []

    def start_recording():
        try:
            recorder.start()
        except Exception as exc:  # pragma: no cover - assertion reports details
            failure.append(exc)
        finally:
            returned.set()

    # Model stop holding the publication barrier. start() must not report a
    # ready stream until its provisional PCM owns that same barrier; otherwise
    # stop can snapshot an empty buffer and reject the first verified frame.
    recorder._observer_lock.acquire()
    worker = threading.Thread(target=start_recording)
    worker.start()
    try:
        assert not returned.wait(timeout=0.1)
    finally:
        recorder._observer_lock.release()

    assert returned.wait(timeout=1.0)
    worker.join(timeout=1.0)
    assert failure == []
    assert recorder.stop() == pcm


def test_darwin_unknown_permission_fails_before_device_start(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "microphone_permission_status",
        lambda: "unknown",
    )
    monkeypatch.setattr(
        AudioRecorder,
        "_start_stream_with_recovery",
        lambda self: pytest.fail("unknown TCC state must fail closed on macOS"),
    )

    with pytest.raises(AudioRecorderStartError) as captured:
        AudioRecorder().start()

    assert captured.value.code == "microphone_permission_unknown"
    assert captured.value.stage == "permission"


def test_capture_dsp_plan_is_immutable_until_the_next_session():
    from vocal_more.core.audio_recorder import AudioRecorder

    updates = []

    class NativeStream:
        delivers_processed_pcm = True

        def set_dsp(self, **kwargs):
            updates.append(kwargs)

    recorder = AudioRecorder()
    recorder._stream = NativeStream()
    recorder._is_recording = True
    initial = (
        recorder._gain,
        recorder._highpass_filter,
        recorder._hp_freq,
        recorder._soft_limiter,
        recorder.sample_rate,
        recorder.blocksize,
        recorder.capture_channels,
    )

    recorder.set_gain(4.0)
    recorder.set_highpass_filter(False)
    recorder.set_highpass_freq(120)
    recorder.set_soft_limiter(False)
    recorder.set_sample_rate(24000)
    recorder.set_blocksize(960)
    recorder.set_capture_channels(2)

    assert updates == []
    assert (
        recorder._gain,
        recorder._highpass_filter,
        recorder._hp_freq,
        recorder._soft_limiter,
        recorder.sample_rate,
        recorder.blocksize,
        recorder.capture_channels,
    ) == initial

    recorder._is_recording = False
    with recorder._lock:
        recorder._apply_pending_capture_config_locked()

    assert recorder._gain == 4.0
    assert recorder._highpass_filter is False
    assert recorder._hp_freq == 120
    assert recorder._soft_limiter is False
    assert recorder.sample_rate == 16000
    assert recorder.blocksize == 960
    assert recorder.capture_channels == 2


def test_capture_config_batch_is_atomic_and_session_snapshot_is_authoritative(
    monkeypatch,
):
    from vocal_more.core.audio_recorder import AudioRecorder

    session_plan = SimpleNamespace(
        blocksize=800,
        capture_channels=2,
        input_device="Session Mic",
        gain_mode="manual",
        gain=4.0,
        highpass_filter=False,
        highpass_freq=120,
        soft_limiter=False,
    )
    concurrent_update = SimpleNamespace(
        blocksize=1200,
        capture_channels=3,
        input_device="Next Mic",
        gain_mode="automatic",
        gain=9.0,
        highpass_filter=True,
        highpass_freq=300,
        soft_limiter=True,
    )
    recorder = AudioRecorder()
    observed = []
    stream = MagicMock()

    def open_stream():
        observed.append(
            (
                recorder.blocksize,
                recorder.capture_channels,
                recorder._device_name,
                recorder._gain_mode,
                recorder._gain,
                recorder._highpass_filter,
                recorder._hp_freq,
                recorder._soft_limiter,
            )
        )
        return stream

    monkeypatch.setattr(recorder, "_start_stream_with_recovery", open_stream)
    start_entered = threading.Event()
    update_entered = threading.Event()

    def start_session():
        start_entered.set()
        recorder.start_capture_session(session_plan)

    def update_runtime():
        update_entered.set()
        recorder.apply_capture_config(concurrent_update)

    # Both operations contend on the same recorder lock. Regardless of which
    # arrives first, this session must open with the complete session snapshot;
    # the concurrent update is either overwritten before start or deferred.
    recorder._lock.acquire()
    start_thread = threading.Thread(target=start_session)
    update_thread = threading.Thread(target=update_runtime)
    start_thread.start()
    update_thread.start()
    assert start_entered.wait(timeout=0.5)
    assert update_entered.wait(timeout=0.5)
    recorder._lock.release()

    start_thread.join(timeout=1.0)
    update_thread.join(timeout=1.0)
    assert not start_thread.is_alive()
    assert not update_thread.is_alive()
    assert observed == [
        (800, 2, "Session Mic", "manual", 4.0, False, 120, False)
    ]
    recorder.stop()


def test_portaudio_observer_failures_are_faulted_without_stopping_capture():
    from vocal_more.core.audio_recorder import AudioRecorder

    chunk_calls = 0
    level_calls = 0

    def broken_chunk(_pcm):
        nonlocal chunk_calls
        chunk_calls += 1
        raise RuntimeError("ASR handoff failed")

    def broken_level(_rms):
        nonlocal level_calls
        level_calls += 1
        raise RuntimeError("UI handoff failed")

    recorder = AudioRecorder(
        on_audio_chunk=broken_chunk,
        on_audio_level=broken_level,
    )
    recorder._is_recording = True
    recorder._highpass_filter = False
    recorder._gain = 1.0
    block = np.full((4, 1), 0.1, dtype=np.float32)

    recorder._audio_callback(block, len(block), {}, 0)
    recorder._audio_callback(block, len(block), {}, 0)

    assert chunk_calls == 2
    assert level_calls == 2
    assert len(recorder._audio_buffer) == 2
    assert recorder.input_status["recorder_fault_count"] == 4
    assert recorder.input_status["runtime_fault_count"] == 4
    assert recorder.input_status["gain_control_verified"] is False


def test_portaudio_input_overflow_faults_the_session_without_realtime_logging(
    capsys,
):
    from vocal_more.core.audio_recorder import AudioRecorder

    class OverflowStatus:
        input_overflow = True

        def __bool__(self):
            return True

    recorder = AudioRecorder()
    recorder._is_recording = True
    recorder._highpass_filter = False
    recorder._gain = 1.0

    recorder._audio_callback(
        np.full((4, 1), 0.1, dtype=np.float32),
        4,
        object(),
        OverflowStatus(),
    )

    status = recorder.input_status
    assert status["runtime_fault_count"] == 1
    assert status["runtime_fault_code"] == "portaudio_input_overflow"
    assert status["gain_control_verified"] is False
    assert capsys.readouterr().out == ""


def test_portaudio_status_from_a_block_rejected_by_stop_is_not_attributed_late():
    from vocal_more.core.audio_recorder import AudioRecorder

    status_inspection_entered = threading.Event()
    release_status_inspection = threading.Event()
    recorder = AudioRecorder()
    recorder._is_recording = True
    recorder._highpass_filter = False
    recorder._gain = 1.0
    recorder._input_status.update(
        {
            "phase": "active",
            "voice_processing_enabled_observed": True,
            "agc_enabled_observed": False,
        }
    )
    recorder._stream = type(
        "Stream",
        (),
        {"abort": lambda self: None, "close": lambda self: None},
    )()

    def inspect_status(_status):
        status_inspection_entered.set()
        assert release_status_inspection.wait(timeout=1.0)
        return "portaudio_input_overflow"

    recorder._portaudio_status_fault_code = inspect_status
    callback_thread = threading.Thread(
        target=lambda: recorder._audio_callback(
            np.full((4, 1), 0.1, dtype=np.float32),
            4,
            {},
            object(),
        )
    )
    callback_thread.start()
    assert status_inspection_entered.wait(timeout=0.5)

    assert recorder.stop() == b""
    release_status_inspection.set()
    callback_thread.join(timeout=0.5)

    assert not callback_thread.is_alive()
    status = recorder.input_status
    assert status["runtime_fault_count"] == 0
    assert status["last_session"]["runtime_fault_count"] == 0


@pytest.mark.parametrize("name", ["Mac mini", "Mac Studio", "Mac Pro"])
def test_headless_mac_model_names_are_not_treated_as_builtin_microphones(
    monkeypatch,
    name,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")

    assert AudioRecorder._is_macos_builtin_microphone({"name": name}) is False


def test_failed_portaudio_candidate_cannot_publish_provisional_audio(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    class FakePortAudioError(Exception):
        pass

    delivered: list[bytes] = []
    delivered_event = threading.Event()
    created = []

    class FakeStream:
        samplerate = 16000

        def __init__(self, *, callback, device, **_kwargs):
            self.callback = callback
            self.device = device

        def start(self):
            value = 0.1 if self.device == 0 else 0.2
            samples = np.full((4, 1), value, dtype=np.float32)
            self.callback(samples, len(samples), object(), 0)
            if self.device == 0:
                raise FakePortAudioError("first device failed after callback")

        def abort(self):
            return None

        def close(self):
            return None

    def input_stream(**kwargs):
        stream = FakeStream(**kwargs)
        created.append(stream)
        return stream

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": staticmethod(input_stream),
            "PortAudioError": FakePortAudioError,
            "CallbackFlags": object,
            "default": _fake_default_device(1),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 0, "name": "Bad Mic", "max_input_channels": 1},
                    {"index": 1, "name": "Default Mic", "max_input_channels": 1},
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(
        audio_recorder_module,
        "microphone_permission_status",
        lambda: "authorized",
    )

    def on_chunk(pcm):
        delivered.append(pcm)
        delivered_event.set()

    recorder = AudioRecorder(device="Bad Mic", on_audio_chunk=on_chunk)
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False

    recorder.start()
    assert delivered_event.wait(timeout=1.0)

    rejected_pcm = (np.full(4, 0.1) * 32767).astype(np.int16).tobytes()
    accepted_pcm = (np.full(4, 0.2) * 32767).astype(np.int16).tobytes()
    assert len(created) == 2
    assert rejected_pcm not in delivered
    assert delivered == [accepted_pcm]
    assert recorder.get_pcm_data() == accepted_pcm
    recorder.stop()


def test_voice_processing_failure_falls_back_and_reports_aec_inactive(monkeypatch):
    """AEC startup failure must preserve recording and remain observable."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder
    from vocal_more.core.macos_voice_capture import VoiceProcessingUnavailable

    portaudio_channels = []
    delivered_chunks = []
    delivered_levels = []

    class FailingVoiceProcessingStream:
        def __init__(self, callback, pcm_callback):
            self._callback = callback
            self._pcm_callback = pcm_callback

        def start(self):
            # AVAudioEngine may deliver a tap before post-start verification.
            # A failed adapter must not contaminate the PortAudio fallback.
            self._callback(
                np.ones((640, 1), dtype=np.float32) * 0.25,
                640,
                {},
                0,
            )
            self._pcm_callback(b"\x01\x00", 0.25)
            raise VoiceProcessingUnavailable(
                "voice processing device unavailable",
                code="voice_processing_enable_failed",
                stage="engine_start",
            )

        def close(self):
            return None

    class FakeStream:
        def __init__(self, **kwargs):
            portaudio_channels.append(kwargs["channels"])

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **kwargs: FailingVoiceProcessingStream(
            kwargs["callback"],
            kwargs["pcm_callback"],
        ),
    )

    recorder = AudioRecorder(
        channels=1,
        on_audio_chunk=delivered_chunks.append,
        on_audio_level=delivered_levels.append,
    )
    recorder.set_capture_backend("voice_processing")
    recorder.set_gain_mode("automatic")
    recorder.start()

    assert portaudio_channels == [1]
    assert recorder.input_status["echo_cancellation"] == "fallback"
    assert recorder.input_status["processing_mode"] == "system_managed_mono"
    assert recorder.input_status["gain_control"] == "software_fallback"
    assert recorder.input_status["fallback_code"] == "voice_processing_enable_failed"
    assert recorder.input_status["fallback_stage"] == "engine_start"
    assert "voice processing device unavailable" in str(
        recorder.input_status["fallback_reason"]
    )
    assert recorder.get_pcm_data() == b""
    assert delivered_chunks == []
    assert delivered_levels == []
    assert recorder._hp_prev_in == 0.0
    assert recorder._hp_prev_out == 0.0
    recorder.stop()


def test_native_start_failure_falls_back_to_raw_portaudio(monkeypatch):
    """A failed optional VPIO graph must retain standard microphone capture."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder
    from vocal_more.core.native_audio_capture import NativeAudioUnavailable

    attempts = []

    class FailingNativeStream:
        def start(self):
            attempts.append("native")
            raise NativeAudioUnavailable(
                "native route refused VoiceProcessingIO",
                code="native_audio_start_failed",
                stage="engine_start",
            )

        def close(self):
            attempts.append("native_close")

    class SuccessfulPortAudioStream:
        def __init__(self, **_kwargs):
            attempts.append("portaudio")

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": SuccessfulPortAudioStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **_kwargs: FailingNativeStream(),
    )
    recorder = AudioRecorder()
    recorder.set_capture_backend("voice_processing")
    recorder.set_gain_mode("automatic")
    recorder.start()

    assert attempts == ["native", "native_close", "portaudio"]
    assert recorder.input_status["processing_mode"] == "system_managed_mono"
    assert recorder.input_status["gain_control"] == "software_fallback"
    assert recorder.input_status["gain_control_verified"] is True
    recorder.stop()


def test_native_voice_builder_drops_float_callback_for_pcm_backend(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    import vocal_more.core.native_audio_capture as native_audio_capture

    observed = {}

    def fake_builder(**kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(
        native_audio_capture,
        "build_native_voice_processing_stream",
        fake_builder,
    )

    result = audio_recorder_module._build_macos_voice_processing_stream(
        callback=lambda *_args: None,
        pcm_callback=lambda *_args: None,
        sample_rate=16000,
    )

    assert result is not None
    assert "callback" not in observed
    assert callable(observed["pcm_callback"])
    assert observed["sample_rate"] == 16000


def test_observed_apple_agc_bypasses_software_gain_even_when_session_unverified(
    monkeypatch,
):
    """Loss telemetry must not cause Apple AGC and software gain to stack."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder
    delivered = []
    published = threading.Event()

    class LossyNativeStream:
        backend_name = "native"
        delivers_processed_pcm = False

        def __init__(self, callback):
            self._callback = callback

        def start(self):
            self._callback(
                np.full((8, 1), 0.1, dtype=np.float32),
                8,
                {},
                0,
            )

        @property
        def diagnostics(self):
            return {
                "backend": "native",
                "source_sample_rate_hz": 48000.0,
                "source_channels": 1,
                "voice_processing_enabled": True,
                "agc_enabled": True,
                "dropped_blocks": 0,
                "runtime_fault_count": 1,
                "runtime_fault_code": "converter_input_starved",
            }

        def drain(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": lambda **_kwargs: pytest.fail(
                "Apple fallback must precede PortAudio"
            ),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **kwargs: LossyNativeStream(kwargs["callback"]),
    )

    def observe(chunk):
        delivered.append(chunk)
        published.set()

    recorder = AudioRecorder(on_audio_chunk=observe)
    recorder.set_capture_backend("voice_processing")
    recorder.set_gain_mode("automatic")
    recorder.set_gain(10.0)
    recorder.set_highpass_filter(False)
    recorder.set_soft_limiter(False)
    recorder.start()
    assert published.wait(timeout=0.5)

    samples = np.frombuffer(delivered[0], dtype=np.int16)
    assert np.all((samples >= 3275) & (samples <= 3277))
    assert recorder._apple_agc_active is True
    assert recorder.input_status["runtime_fault_count"] == 1
    assert recorder.input_status["gain_control_verified"] is False
    recorder.stop()


def test_verified_start_is_not_timed_out_by_a_blocking_startup_observer(
    monkeypatch,
):
    """Publish the accepted stream before flushing provisional callbacks."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    observer_entered = threading.Event()
    release_observer = threading.Event()

    class EarlyPCMStream:
        backend_name = "objective_cpp"
        delivers_processed_pcm = True

        def __init__(self, pcm_callback):
            self._pcm_callback = pcm_callback

        def start(self):
            self._pcm_callback(b"\x01\x00", 0.1)

        @property
        def diagnostics(self):
            return {
                "backend": "objective_cpp",
                "source_sample_rate_hz": 48000.0,
                "source_channels": 1,
                "voice_processing_enabled": True,
                "agc_enabled": True,
                "dropped_blocks": 0,
            }

        def drain(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": lambda **_kwargs: pytest.fail("must stay native"),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **kwargs: EarlyPCMStream(kwargs["pcm_callback"]),
    )

    def blocking_observer(_chunk):
        observer_entered.set()
        assert release_observer.wait(timeout=1.0)

    recorder = AudioRecorder(
        on_audio_chunk=blocking_observer,
        start_timeout=0.05,
    )
    recorder.set_capture_backend("voice_processing")
    recorder.start()

    assert recorder.is_recording() is True
    assert observer_entered.wait(timeout=0.5)
    release_observer.set()
    recorder.stop()


def test_startup_gate_overflow_remains_unverified_through_last_session(
    monkeypatch,
):
    """Stream diagnostics must not erase recorder-owned startup loss."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    published = threading.Event()
    published_count = 0

    class BurstStream:
        backend_name = "objective_cpp"
        delivers_processed_pcm = True

        def __init__(self, pcm_callback):
            self._pcm_callback = pcm_callback

        def start(self):
            for _index in range(65):
                self._pcm_callback(b"\x01\x00", 0.1)

        @property
        def diagnostics(self):
            return {
                "backend": "objective_cpp",
                "source_sample_rate_hz": 48000.0,
                "source_channels": 1,
                "voice_processing_enabled": True,
                "agc_enabled": True,
                "dropped_blocks": 0,
                "runtime_fault_count": 0,
                "runtime_fault_code": None,
            }

        def drain(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": lambda **_kwargs: pytest.fail("must stay native"),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **kwargs: BurstStream(kwargs["pcm_callback"]),
    )

    def observe(_chunk):
        nonlocal published_count
        published_count += 1
        if published_count == 64:
            published.set()

    recorder = AudioRecorder(on_audio_chunk=observe)
    recorder.set_capture_backend("voice_processing")
    recorder.set_gain_mode("automatic")
    recorder.start()
    assert published.wait(timeout=0.5)

    active = recorder.input_status
    assert active["runtime_fault_count"] == 1
    assert active["runtime_fault_code"] == "startup_audio_gate_overflow"
    assert active["gain_control_verified"] is False

    recorder.stop()
    completed = recorder.input_status["last_session"]
    assert completed["runtime_fault_count"] == 1
    assert completed["runtime_fault_code"] == "startup_audio_gate_overflow"
    assert completed["gain_control_verified"] is False


def test_unexpected_voice_processing_bug_is_not_hidden_by_portaudio_fallback(
    monkeypatch,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    class BrokenVoiceProcessingStream:
        def start(self):
            raise AssertionError("programming defect")

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": lambda **_kwargs: pytest.fail(
                "unexpected defects must not be disguised as a normal fallback"
            ),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(0),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 0,
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **_kwargs: BrokenVoiceProcessingStream(),
    )

    recorder = AudioRecorder()
    recorder.set_capture_backend("voice_processing")
    with pytest.raises(AudioRecorderStartError, match="programming defect"):
        recorder.start()


def test_external_microphone_does_not_claim_voice_processing(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": MagicMock(),
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(4),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 4,
                        "name": "USB Conference Microphone",
                        "max_input_channels": 2,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )

    status = AudioRecorder.inspect_input_status()

    assert status["device_name"] == "USB Conference Microphone"
    assert status["processing_mode"] == "standard"
    assert status["echo_cancellation"] == "unavailable"
    assert status["phase"] == "planned"
    assert status["agc_enabled_observed"] is None
    assert status["last_session"] is None


def test_studio_display_prefers_low_latency_capture_over_voice_processing(
    monkeypatch,
):
    """The external-display route must not reintroduce a one-second startup gap."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    stream_kwargs = []

    class FakeStream:
        samplerate = 16000

        def __init__(self, **kwargs):
            stream_kwargs.append(kwargs)

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(2),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 2,
                        "name": "Studio Display Microphone",
                        "max_input_channels": 1,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: True,
    )
    monkeypatch.setattr(
        audio_recorder_module,
        "_build_macos_voice_processing_stream",
        lambda **_kwargs: pytest.fail(
            "Studio Display must stay on the low-latency capture path"
        ),
    )

    recorder = AudioRecorder(channels=1)
    recorder.start()

    assert len(stream_kwargs) == 1
    assert recorder.input_status["native_backend"] == "portaudio"
    assert recorder.input_status["processing_mode"] == "system_managed_mono"
    assert recorder.input_status["echo_cancellation"] == "unavailable"
    recorder.stop()


def test_macos_array_falls_back_to_system_beamformed_mono_when_format_is_rejected(
    monkeypatch,
):
    """Unsupported raw-array formats should retry the same device as mono."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    stream_channels = []

    class FakePortAudioError(Exception):
        pass

    class FakeStream:
        def __init__(self, **kwargs):
            stream_channels.append(kwargs["channels"])
            if kwargs["channels"] > 1:
                raise FakePortAudioError("invalid channel count at 16 kHz")

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": FakePortAudioError,
            "CallbackFlags": object,
            "default": _fake_default_device(2),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 2,
                        "name": "Studio Display Microphone",
                        "max_input_channels": 3,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        audio_recorder_module,
        "_macos_voice_processing_available",
        lambda: False,
    )

    recorder = AudioRecorder(channels=3)
    recorder.start()

    assert stream_channels == [3, 1]
    assert recorder.array_processing_active is False
    recorder.stop()


def test_external_microphone_keeps_requested_channel_count_on_macos(monkeypatch):
    """Array expansion must not silently change USB microphone channel routing."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    stream_kwargs = []

    class FakeStream:
        def __init__(self, **kwargs):
            stream_kwargs.append(kwargs)

        def start(self):
            return None

        def abort(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(4),
            "query_devices": staticmethod(
                lambda: [
                    {
                        "index": 4,
                        "name": "USB Conference Microphone",
                        "max_input_channels": 8,
                    }
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)
    monkeypatch.setattr(audio_recorder_module.platform, "system", lambda: "Darwin")

    recorder = AudioRecorder(channels=2)
    recorder.start()

    assert stream_kwargs[0]["channels"] == 2
    assert recorder.input_status["capture_channels"] == 2
    assert recorder.channels == 1
    assert recorder.array_processing_active is False
    recorder.stop()


def test_array_downmix_reinforces_coherent_voice_and_avoids_polarity_cancellation():
    """Coherent speech stays strong when one logical channel has inverted polarity."""
    from vocal_more.core.audio_recorder import AudioRecorder

    frames = 640
    voice = np.sin(np.linspace(0, 8 * np.pi, frames, endpoint=False)).astype(
        np.float32
    ) * 0.2
    noise = np.tile(np.asarray([0.04, -0.04], dtype=np.float32), frames // 2)
    array_input = np.column_stack(
        [
            voice + noise,
            voice - noise,
            -voice,
        ]
    )
    chunks = []
    recorder = AudioRecorder(
        sample_rate=16000,
        channels=1,
        blocksize=frames,
        on_audio_chunk=chunks.append,
    )
    recorder._gain = 1.0
    recorder._highpass_filter = False
    recorder._soft_limiter = False
    recorder._is_recording = True

    recorder._audio_callback(array_input, frames, {}, 0)

    output = np.frombuffer(chunks[0], dtype=np.int16).astype(np.float32) / 32767
    assert output.shape == (frames,)
    assert np.sqrt(np.mean((output - voice) ** 2)) < 0.005


def test_highpass_filter_matches_previous_sample_by_sample_implementation():
    """The callback's list loop must retain the original IIR filter math."""
    from vocal_more.core.audio_recorder import AudioRecorder

    samples = np.array([[0.25], [-0.5], [0.75], [0.125]], dtype=np.float32)
    recorder = AudioRecorder(sample_rate=16000, channels=1, blocksize=len(samples))
    recorder._gain = 1.0
    recorder._highpass_filter = True
    recorder._soft_limiter = False
    recorder._is_recording = True
    actual = []
    recorder.on_audio_chunk = actual.append

    recorder._audio_callback(samples, len(samples), {}, 0)

    prev_in = prev_out = 0.0
    expected = []
    for x in samples[:, 0]:
        prev_out = recorder._hp_alpha * (prev_out + x - prev_in)
        prev_in = x
        expected.append(prev_out)
    expected_pcm = (np.asarray(expected, dtype=np.float32) * 32767).astype(np.int16)
    actual_pcm = np.frombuffer(actual[0], dtype=np.int16)

    assert np.max(np.abs(actual_pcm.astype(np.int32) - expected_pcm.astype(np.int32))) <= 1


def test_portaudio_dsp_uses_vectorized_gain_limiter_and_encoding(monkeypatch):
    """The compatibility callback must not call Python math per audio sample."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    monkeypatch.setattr(
        audio_recorder_module.math,
        "tanh",
        lambda _value: pytest.fail("sample-wise math.tanh must not run"),
    )
    frames = 640
    recorder = AudioRecorder(sample_rate=16000, channels=1, blocksize=frames)
    recorder._gain = 8.0
    recorder._highpass_filter = True
    recorder._soft_limiter = True
    recorder._is_recording = True
    chunks = []
    recorder.on_audio_chunk = chunks.append

    recorder._audio_callback(
        np.linspace(-0.25, 0.25, frames, dtype=np.float32).reshape(-1, 1),
        frames,
        {},
        0,
    )

    assert len(chunks) == 1
    assert len(chunks[0]) == frames * 2


def test_highpass_filter_is_continuous_across_callback_blocks():
    """Filter state must produce the same result for whole and split signals."""
    from vocal_more.core.audio_recorder import AudioRecorder

    signal = np.array([[0.1], [0.3], [-0.2], [0.5], [-0.4], [0.2]], dtype=np.float32)

    def filter_chunks(chunks):
        recorder = AudioRecorder()
        recorder._gain = 1.0
        recorder._highpass_filter = True
        recorder._soft_limiter = False
        recorder._is_recording = True
        output = []
        recorder.on_audio_chunk = output.append
        for chunk in chunks:
            recorder._audio_callback(chunk, len(chunk), {}, 0)
        return np.concatenate([np.frombuffer(chunk, dtype=np.int16) for chunk in output])

    assert np.array_equal(filter_chunks([signal]), filter_chunks([signal[:2], signal[2:]]))


def test_warm_voice_stream_expires_and_releases_native_graph(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    timers = []

    class FakeTimer:
        def __init__(self, interval, callback, args):
            self.interval = interval
            self.callback = callback
            self.args = args
            self.cancelled = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    stream = MagicMock()
    monkeypatch.setattr(audio_recorder_module.threading, "Timer", FakeTimer)
    recorder = AudioRecorder()

    recorder._retain_warm_voice_stream(stream)
    timers[0].callback(*timers[0].args)

    assert timers[0].interval == audio_recorder_module._WARM_VOICE_STREAM_TTL_SECONDS
    stream.abort.assert_called_once_with()
    stream.close.assert_called_once_with()
    assert recorder._warm_voice_stream is None


def test_warm_voice_stream_is_transferred_without_waiting_for_expiry(monkeypatch):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    timers = []

    class FakeTimer:
        def __init__(self, interval, callback, args):
            self.cancelled = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    stream = MagicMock()
    monkeypatch.setattr(audio_recorder_module.threading, "Timer", FakeTimer)
    recorder = AudioRecorder()
    recorder._retain_warm_voice_stream(stream)

    reused = recorder._take_warm_voice_stream()

    assert reused is stream
    assert timers[0].cancelled is True
    stream.close.assert_not_called()


@pytest.mark.parametrize("cycle_count", [10, 50])
def test_repeated_warm_voice_cycles_keep_only_one_owned_stream(
    monkeypatch,
    cycle_count,
):
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    class FakeTimer:
        def __init__(self, _interval, _callback, args):
            self.cancelled = False

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    streams = [MagicMock() for _ in range(cycle_count)]
    monkeypatch.setattr(audio_recorder_module.threading, "Timer", FakeTimer)
    recorder = AudioRecorder()
    monkeypatch.setattr(
        recorder,
        "_release_stream_async",
        lambda stream, **_kwargs: recorder._release_stream(stream),
    )

    for stream in streams:
        previous = recorder._retain_warm_voice_stream(stream)
        if previous is not None and previous is not stream:
            recorder._release_stream_async(previous)
    recorder.close()

    assert recorder._warm_voice_stream is None
    assert all(stream.close.call_count == 1 for stream in streams)


def test_highpass_filter_rejects_dc_and_passes_high_frequency_signal():
    """The first-order filter should attenuate DC while retaining rapid changes."""
    from vocal_more.core.audio_recorder import AudioRecorder

    recorder = AudioRecorder()
    recorder._gain = 1.0
    recorder._highpass_filter = True
    recorder._soft_limiter = False
    recorder._is_recording = True
    output = []
    recorder.on_audio_chunk = output.append
    dc = np.ones((1600, 1), dtype=np.float32) * 0.5
    recorder._audio_callback(dc, len(dc), {}, 0)
    dc_output = np.frombuffer(output.pop(), dtype=np.int16).astype(np.float32) / 32767
    recorder._audio_callback(np.tile([[0.5], [-0.5]], (400, 1)).astype(np.float32), 800, {}, 0)
    high_output = np.frombuffer(output.pop(), dtype=np.int16).astype(np.float32) / 32767

    assert abs(dc_output[-1]) < 0.001
    assert np.sqrt(np.mean(high_output**2)) > 0.2


def test_audio_recorder_reinitializes_portaudio_once_on_recoverable_start_failure(monkeypatch):
    """A wedged PortAudio backend should get one reset-and-retry before giving up."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    call_devices = []
    reset_calls = []

    class FakePortAudioError(Exception):
        pass

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)
            if len(call_devices) < 3:
                raise FakePortAudioError("PaErrorCode -9986 paInternalError")
            self.device = device

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    class FakeDefault:
        def reset(self):
            reset_calls.append("default.reset")

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": FakePortAudioError,
            "CallbackFlags": object,
            "default": FakeDefault(),
            "query_devices": staticmethod(
                lambda: [{"index": 7, "name": "Desk Mic", "max_input_channels": 1}]
            ),
            "_terminate": staticmethod(lambda: reset_calls.append("_terminate")),
            "_initialize": staticmethod(lambda: reset_calls.append("_initialize")),
        },
    )()

    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder(device="Desk Mic")
    recorder.start()

    assert recorder.is_recording() is True
    assert call_devices == [7, None, 7]
    assert reset_calls == ["_terminate", "default.reset", "_initialize"]


def test_audio_recorder_raises_device_change_error_after_failed_recovery(monkeypatch):
    """Persistent recoverable errors should bubble up as a device-change hint."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    reset_calls = []

    class FakePortAudioError(Exception):
        pass

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            raise FakePortAudioError("PaErrorCode -9986 paInternalError")

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    class FakeDefault:
        def reset(self):
            reset_calls.append("default.reset")

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": FakePortAudioError,
            "CallbackFlags": object,
            "default": FakeDefault(),
            "query_devices": staticmethod(lambda: []),
            "_terminate": staticmethod(lambda: reset_calls.append("_terminate")),
            "_initialize": staticmethod(lambda: reset_calls.append("_initialize")),
        },
    )()

    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()

    with pytest.raises(AudioRecorderStartError) as exc_info:
        recorder.start()

    assert exc_info.value.device_change_detected is True
    assert recorder.is_recording() is False
    assert reset_calls == ["_terminate", "default.reset", "_initialize"]


def test_missing_configured_input_device_falls_back_to_default_and_persists(
    tmp_path, monkeypatch
):
    """A removed selected mic should be cleared so future starts use the system default."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder

    config_path = _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset Mic")
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [{"index": 0, "name": "Built-in Mic", "max_input_channels": 1}]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    recorder.start()

    assert call_devices == [None]
    assert recorder._device_name is None
    assert reload_config().audio.input_device is None
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["audio"]["input_device"] is None


def test_configured_input_device_open_failure_clears_selection_after_default_fallback(
    tmp_path, monkeypatch
):
    """If a named mic is listed but cannot open, successful default fallback should stick."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset Mic")
    call_devices = []

    class FakePortAudioError(Exception):
        pass

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)
            if device == 3:
                raise FakePortAudioError("device unavailable")

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": FakePortAudioError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1},
                    {"index": 3, "name": "USB Headset Mic", "max_input_channels": 1},
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    recorder.start()

    assert call_devices == [3, None]
    assert recorder._device_name is None
    assert reload_config().audio.input_device is None


def test_configured_input_device_still_present_uses_named_device_and_preserves_config(
    tmp_path, monkeypatch
):
    """The selected mic should stay selected when it is still usable."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset Mic")
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1},
                    {"index": 4, "name": "USB Headset Mic", "max_input_channels": 1},
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    recorder.start()

    assert call_devices == [4]
    assert recorder._device_name == "USB Headset Mic"
    assert reload_config().audio.input_device == "USB Headset Mic"


def test_output_only_device_with_selected_name_is_ignored_and_cleared(
    tmp_path, monkeypatch
):
    """A matching output-only device should not block fallback to the input default."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset")
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 2, "name": "USB Headset", "max_input_channels": 0},
                    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1},
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    recorder.start()

    assert call_devices == [None]
    assert reload_config().audio.input_device is None


def test_duplicate_device_names_choose_input_capable_match(tmp_path, monkeypatch):
    """Duplicate CoreAudio names should skip output-only entries and use the input one."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "Studio Display")
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 1, "name": "Studio Display", "max_input_channels": 0},
                    {"index": 5, "name": "Studio Display", "max_input_channels": 1},
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    recorder.start()

    assert call_devices == [5]
    assert reload_config().audio.input_device == "Studio Display"


def test_config_device_is_reloaded_when_recorder_starts_after_config_change(
    tmp_path, monkeypatch
):
    """Long-lived recorders should not keep a stale selected mic after config updates."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import get_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset Mic")
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 3, "name": "USB Headset Mic", "max_input_channels": 1},
                    {"index": 6, "name": "Desk Mic", "max_input_channels": 1},
                ]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    get_config().audio.input_device = "Desk Mic"
    recorder.start()

    assert call_devices == [6]
    assert recorder._device_name == "Desk Mic"


def test_explicit_device_fallback_does_not_mutate_saved_config(tmp_path, monkeypatch):
    """One-off explicit recorder devices may fallback locally without touching config."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "Configured Mic")
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [{"index": 0, "name": "Built-in Mic", "max_input_channels": 1}]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder(device="Temporary USB Mic")
    recorder.start()

    assert call_devices == [None]
    assert recorder._device_name is None
    assert reload_config().audio.input_device == "Configured Mic"


def test_named_device_and_default_failure_marks_device_change_and_clears_config(
    tmp_path, monkeypatch
):
    """When neither selected nor default input can open, surface a device-change error."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import reload_config
    from vocal_more.core.audio_recorder import AudioRecorder, AudioRecorderStartError

    _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset Mic")
    call_devices = []
    reset_calls = []

    class FakePortAudioError(Exception):
        pass

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)
            raise FakePortAudioError("device unavailable")

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": FakePortAudioError,
            "CallbackFlags": object,
            "default": type("FakeDefault", (), {"device": [0, None], "reset": lambda self: reset_calls.append("default.reset")})(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 0, "name": "Built-in Mic", "max_input_channels": 1},
                    {"index": 3, "name": "USB Headset Mic", "max_input_channels": 1},
                ]
            ),
            "_terminate": staticmethod(lambda: reset_calls.append("_terminate")),
            "_initialize": staticmethod(lambda: reset_calls.append("_initialize")),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    with pytest.raises(AudioRecorderStartError) as exc_info:
        recorder.start()

    assert exc_info.value.device_change_detected is True
    assert call_devices == [3, None, None]
    assert reload_config().audio.input_device is None
    assert reset_calls == ["_terminate", "default.reset", "_initialize"]


def test_config_save_failure_does_not_block_default_fallback(tmp_path, monkeypatch):
    """A persistence problem should not prevent recording with the default mic."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.config import Config, get_config
    from vocal_more.core.audio_recorder import AudioRecorder

    _write_config_with_input_device(tmp_path, monkeypatch, "USB Headset Mic")
    monkeypatch.setattr(Config, "save", lambda self: (_ for _ in ()).throw(RuntimeError("disk full")))
    call_devices = []

    class FakeStream:
        def __init__(self, device=None, **kwargs):
            call_devices.append(device)

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "InputStream": FakeStream,
            "PortAudioError": RuntimeError,
            "CallbackFlags": object,
            "default": _fake_default_device(),
            "query_devices": staticmethod(
                lambda: [{"index": 0, "name": "Built-in Mic", "max_input_channels": 1}]
            ),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    recorder = AudioRecorder()
    recorder.start()

    assert call_devices == [None]
    assert recorder._device_name is None
    assert get_config().audio.input_device is None


def test_list_input_devices_can_refresh_portaudio_before_enumerating(monkeypatch):
    """Refreshing the settings list should rebuild PortAudio's device cache first."""
    import vocal_more.core.audio_recorder as audio_recorder_module
    from vocal_more.core.audio_recorder import AudioRecorder

    calls = []

    class FakeDefault:
        device = [2, None]

        def reset(self):
            calls.append("default.reset")

    fake_sd = type(
        "FakeSoundDevice",
        (),
        {
            "default": FakeDefault(),
            "query_devices": staticmethod(
                lambda: [
                    {"index": 2, "name": "Built-in Mic", "max_input_channels": 1},
                    {"index": 4, "name": "USB Headset Mic", "max_input_channels": 1},
                    {"index": 5, "name": "USB Headset Output", "max_input_channels": 0},
                ]
            ),
            "_terminate": staticmethod(lambda: calls.append("_terminate")),
            "_initialize": staticmethod(lambda: calls.append("_initialize")),
        },
    )()
    monkeypatch.setattr(audio_recorder_module, "sd", fake_sd)

    devices = AudioRecorder.list_input_devices(refresh=True)

    assert calls == ["_terminate", "default.reset", "_initialize"]
    assert devices == [
        {"index": 2, "name": "Built-in Mic", "is_default": True},
        {"index": 4, "name": "USB Headset Mic", "is_default": False},
    ]
