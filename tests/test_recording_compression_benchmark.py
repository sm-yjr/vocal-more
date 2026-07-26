from __future__ import annotations

import wave

import pytest

from scripts.benchmark_recording_compression import (
    _portable_fixture_path,
    _read_fixture,
    _summary,
)


def test_compression_benchmark_summary_interpolates_p95():
    summary = _summary([1.0, 2.0, 3.0, 4.0, 5.0])

    assert summary == {
        "min": 1.0,
        "p50": 3.0,
        "p95": 4.8,
        "max": 5.0,
    }


def test_compression_benchmark_rejects_incompatible_wav(tmp_path):
    fixture = tmp_path / "stereo.wav"
    with wave.open(str(fixture), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 40)

    with pytest.raises(ValueError, match="16 kHz, mono, 16-bit"):
        _read_fixture(fixture)


def test_compression_benchmark_does_not_publish_absolute_fixture_paths(tmp_path):
    fixture = tmp_path / "private" / "sample.wav"

    assert _portable_fixture_path(fixture) == "sample.wav"
