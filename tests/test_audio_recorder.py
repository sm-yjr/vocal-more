"""Tests for audio recorder callback behavior."""

import numpy as np
import pytest
import yaml


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
