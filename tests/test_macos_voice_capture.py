"""Tests for the macOS voice-processing capture adapter."""

import os
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_native_converter_assertions() -> None:
    if platform.system() != "Darwin":
        pytest.skip("AVAudioConverter is only available on macOS")
    script = r'''
import AVFoundation as AV
import numpy as np
from vocal_more.core.macos_voice_capture import StreamingAudioConverter

def convert(source, source_rate, chunks, max_input_frames):
    converter = StreamingAudioConverter(
        source_rate=source_rate,
        target_rate=16000,
        blocksize=640,
        max_input_frames=max_input_frames,
        av_module=AV,
    )
    blocks = []
    for chunk in chunks:
        blocks.extend(converter.push(chunk))
    blocks.extend(converter.finish())
    return blocks

source_rate = 44100
tone = np.sin(
    2 * np.pi * 440 * np.arange(source_rate // 5, dtype=np.float32) / source_rate
).astype(np.float32)
blocks = convert(tone, source_rate, np.array_split(tone, 2), len(tone) // 2)
assert [block.shape for block in blocks] == [(640,)] * 5
output = np.concatenate(blocks)
assert len(output) == 3200
assert output.dtype == np.float32
assert np.max(np.abs(output)) <= 1.01

source_rate = 48000
above_nyquist = np.sin(
    2 * np.pi * 12000 * np.arange(source_rate, dtype=np.float32) / source_rate
).astype(np.float32)
blocks = convert(
    above_nyquist,
    source_rate,
    [above_nyquist[offset:offset + 480] for offset in range(0, len(above_nyquist), 480)],
    480,
)
output = np.concatenate(blocks)
assert len(output) == 16000
assert float(np.sqrt(np.mean(output**2))) < 0.02

partial = np.linspace(-0.25, 0.25, 5904, dtype=np.float32)
blocks = convert(partial, 48000, np.array_split(partial, 7), 960)
assert [len(block) for block in blocks] == [640, 640, 640, 48]
assert sum(map(len, blocks)) == 1968
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_voice_processing_agc_is_set_and_verified_for_both_modes():
    from vocal_more.core.macos_voice_capture import (
        _configure_voice_processing_agc,
    )

    class InputNode:
        def __init__(self):
            self.enabled = False

        def setVoiceProcessingAGCEnabled_(self, enabled):
            self.enabled = bool(enabled)

        def isVoiceProcessingAGCEnabled(self):
            return self.enabled

    node = InputNode()
    _configure_voice_processing_agc(node, enabled=True)
    assert node.enabled is True
    _configure_voice_processing_agc(node, enabled=False)
    assert node.enabled is False


def test_voice_processing_agc_rejects_an_unverifiable_native_state():
    from vocal_more.core.macos_voice_capture import (
        VoiceProcessingUnavailable,
        _configure_voice_processing_agc,
    )

    class RefusingInputNode:
        def setVoiceProcessingAGCEnabled_(self, enabled):
            del enabled

        def isVoiceProcessingAGCEnabled(self):
            return False

    with pytest.raises(VoiceProcessingUnavailable, match="could not be configured"):
        _configure_voice_processing_agc(RefusingInputNode(), enabled=True)


def test_running_voice_processing_state_is_verified_after_engine_start():
    from vocal_more.core.macos_voice_capture import (
        VoiceProcessingUnavailable,
        _verify_running_voice_processing,
    )

    active = type(
        "InputNode",
        (),
        {
            "isVoiceProcessingEnabled": lambda self: True,
            "isVoiceProcessingAGCEnabled": lambda self: True,
        },
    )()
    mismatched = type(
        "InputNode",
        (),
        {
            "isVoiceProcessingEnabled": lambda self: True,
            "isVoiceProcessingAGCEnabled": lambda self: False,
        },
    )()

    assert _verify_running_voice_processing(active, automatic_gain=True) == (
        True,
        True,
    )
    with pytest.raises(VoiceProcessingUnavailable, match="running state"):
        _verify_running_voice_processing(mismatched, automatic_gain=True)


def test_voice_processing_static_capability_requires_agc_and_vpio_selectors():
    from vocal_more.core.macos_voice_capture import (
        _voice_processing_api_available,
    )

    complete = type(
        "CompleteInputNode",
        (),
        {
            "setVoiceProcessingEnabled_error_": object(),
            "isVoiceProcessingEnabled": object(),
            "setVoiceProcessingAGCEnabled_": object(),
            "isVoiceProcessingAGCEnabled": object(),
        },
    )
    missing_getter = type(
        "IncompleteInputNode",
        (),
        {
            "setVoiceProcessingEnabled_error_": object(),
            "setVoiceProcessingAGCEnabled_": object(),
            "isVoiceProcessingAGCEnabled": object(),
        },
    )

    assert _voice_processing_api_available(
        type("AV", (), {"AVAudioInputNode": complete})
    ) is True
    assert _voice_processing_api_available(
        type("AV", (), {"AVAudioInputNode": missing_getter})
    ) is False


def test_native_apple_converter_is_band_limited_and_drains_exact_tail_frames():
    _run_native_converter_assertions()


def test_streaming_converter_preserves_fixed_blocks_at_matching_rate():
    from vocal_more.core.macos_voice_capture import StreamingAudioConverter

    resampler = StreamingAudioConverter(
        source_rate=16000,
        target_rate=16000,
        blocksize=640,
        max_input_frames=640,
        av_module=None,
    )
    source = np.linspace(-0.5, 0.5, 1280, dtype=np.float32)

    blocks = resampler.push(source)

    assert len(blocks) == 2
    assert np.array_equal(np.concatenate(blocks), source)


def test_streaming_converter_finish_emits_partial_same_rate_block():
    from vocal_more.core.macos_voice_capture import StreamingAudioConverter

    converter = StreamingAudioConverter(
        source_rate=16000,
        target_rate=16000,
        blocksize=640,
        max_input_frames=640,
        av_module=None,
    )
    source = np.linspace(-0.25, 0.25, 1968, dtype=np.float32)

    blocks = converter.push(source)
    blocks.extend(converter.finish())

    assert [len(block) for block in blocks] == [640, 640, 640, 48]


def test_stream_factory_prefers_bundled_native_runtime(monkeypatch):
    from vocal_more.core import macos_voice_capture

    sentinel = object()
    captured = {}
    monkeypatch.setattr(
        "vocal_more.core.native_audio_capture.native_audio_library_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "vocal_more.core.native_audio_capture.build_native_voice_processing_stream",
        lambda **kwargs: captured.update(kwargs) or sentinel,
    )

    stream = macos_voice_capture.build_voice_processing_stream(
        callback=lambda *_args: None,
        pcm_callback=lambda *_args: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=2.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
    )

    assert stream is sentinel
    assert "callback" not in captured
    assert callable(captured["pcm_callback"])


def test_stream_factory_keeps_pyobjc_as_native_library_fallback(monkeypatch):
    from vocal_more.core import macos_voice_capture

    monkeypatch.setattr(
        "vocal_more.core.native_audio_capture.native_audio_library_available",
        lambda: False,
    )

    stream = macos_voice_capture.build_voice_processing_stream(
        callback=lambda *_args: None,
        pcm_callback=lambda *_args: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=False,
        gain=8.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
    )

    assert isinstance(stream, macos_voice_capture.MacOSVoiceProcessingStream)
    assert stream.backend_name == "pyobjc"


def test_pyobjc_diagnostics_preserve_control_center_microphone_modes():
    from vocal_more.core.macos_voice_capture import MacOSVoiceProcessingStream

    stream = MacOSVoiceProcessingStream(
        callback=lambda *_args: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
    )
    stream._preferred_microphone_mode = "voice_isolation"
    stream._active_microphone_mode = "standard"

    assert stream.diagnostics["preferred_microphone_mode"] == "voice_isolation"
    assert stream.diagnostics["active_microphone_mode"] == "standard"
    assert stream.diagnostics["start_verified"] is False
    assert stream.diagnostics["diagnostics_fresh"] is False


def test_pyobjc_drain_invalidates_microphone_mode_if_it_changed(monkeypatch):
    from vocal_more.core import macos_microphone_mode
    from vocal_more.core.macos_voice_capture import MacOSVoiceProcessingStream

    monkeypatch.setattr(
        macos_microphone_mode,
        "macos_microphone_modes",
        lambda **_kwargs: ("voice_isolation", "voice_isolation"),
    )
    stream = MacOSVoiceProcessingStream(
        callback=lambda *_args: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
    )
    stream._av_module = object()
    stream._preferred_microphone_mode = "standard"
    stream._active_microphone_mode = "standard"

    stream.drain()

    assert stream.diagnostics["preferred_microphone_mode"] is None
    assert stream.diagnostics["active_microphone_mode"] is None


def test_pyobjc_stop_cleanup_exceptions_are_runtime_faults():
    from vocal_more.core.macos_voice_capture import MacOSVoiceProcessingStream

    class FailingEngine:
        def stop(self):
            raise RuntimeError("stop failed")

    class FailingInput:
        def removeTapOnBus_(self, _bus):
            raise RuntimeError("remove failed")

        def setVoiceProcessingEnabled_error_(self, _enabled, _error):
            raise RuntimeError("disable failed")

    stream = MacOSVoiceProcessingStream(
        callback=lambda *_args: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
    )
    stream._engine = FailingEngine()
    stream._input_node = FailingInput()

    stream.stop()

    assert stream.diagnostics["runtime_fault_count"] == 3
    assert (
        stream.diagnostics["runtime_fault_code"]
        == "pyobjc_disable_voice_processing_failed"
    )
