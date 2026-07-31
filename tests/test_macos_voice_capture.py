"""Tests for the macOS voice-processing capture adapter."""

import numpy as np


def test_streaming_resampler_emits_fixed_16khz_mono_blocks_without_drift():
    from vocal_more.core.macos_voice_capture import StreamingLinearResampler

    source_rate = 44100
    target_rate = 16000
    source = np.sin(
        2 * np.pi * 440 * np.arange(source_rate // 5, dtype=np.float32) / source_rate
    ).astype(np.float32)
    resampler = StreamingLinearResampler(
        source_rate=source_rate,
        target_rate=target_rate,
        blocksize=640,
    )

    blocks = []
    for chunk in np.array_split(source, 2):
        blocks.extend(resampler.push(chunk))

    assert [block.shape for block in blocks] == [(640,)] * 5
    output = np.concatenate(blocks)
    assert len(output) == target_rate // 5
    assert output.dtype == np.float32
    assert np.max(np.abs(output)) <= 1.0


def test_streaming_resampler_preserves_fixed_blocks_at_matching_rate():
    from vocal_more.core.macos_voice_capture import StreamingLinearResampler

    resampler = StreamingLinearResampler(
        source_rate=16000,
        target_rate=16000,
        blocksize=640,
    )
    source = np.linspace(-0.5, 0.5, 1280, dtype=np.float32)

    blocks = resampler.push(source)

    assert len(blocks) == 2
    assert np.array_equal(np.concatenate(blocks), source)
