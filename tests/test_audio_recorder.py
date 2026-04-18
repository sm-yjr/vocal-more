"""Tests for audio recorder callback behavior."""

import numpy as np


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
