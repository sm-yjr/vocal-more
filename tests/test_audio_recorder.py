"""Tests for audio recorder callback behavior."""

import numpy as np
import pytest


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
