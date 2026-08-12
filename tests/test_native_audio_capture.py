"""Tests for the optional Objective-C++ microphone capture bridge."""

from __future__ import annotations

from collections import deque
import threading
import time

import pytest


def test_native_stream_drains_pcm_on_a_non_realtime_consumer_thread():
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeAudioPacket,
        NativeMacOSVoiceProcessingStream,
    )

    callback_threads: list[int] = []
    chunks: list[bytes] = []
    levels: list[float] = []
    packet = NativeAudioPacket(pcm=b"\x01\x00\x02\x00", frames=2, rms=0.25)

    class FakeAPI:
        def __init__(self) -> None:
            self.events = deque([packet, NativeAudioEnd()])
            self.stop_calls = 0
            self.destroy_calls = 0

        def create(self, _config):
            return object()

        def start(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
            )

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            event = self.events.popleft()
            if isinstance(event, BaseException):
                raise event
            return event

        def stop(self, _handle):
            self.stop_calls += 1

        def destroy(self, _handle):
            self.destroy_calls += 1

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
            )

    api = FakeAPI()
    main_thread = threading.get_ident()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda pcm, rms: (
            callback_threads.append(threading.get_ident()),
            chunks.append(pcm),
            levels.append(rms),
        ),
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=8.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )

    stream.start()
    stream.drain()
    stream.close()

    assert chunks == [packet.pcm]
    assert levels == [0.25]
    assert callback_threads and callback_threads[0] != main_thread
    assert api.stop_calls == 1
    assert api.destroy_calls == 1
    assert stream.diagnostics.agc_enabled is True


def test_native_stop_can_interrupt_a_blocked_consumer_read():
    """Stop must reach the native queue while its consumer is waiting on it."""
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def __init__(self) -> None:
            self.read_entered = threading.Event()
            self.read_finished = threading.Event()
            self.read_starved = threading.Event()
            self.stop_called = threading.Event()

        def create(self, _config):
            return object()

        def start(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=False,
                dropped_blocks=0,
            )

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            self.read_entered.set()
            if not self.stop_called.wait(0.2):
                self.read_starved.set()
            self.read_finished.set()
            raise NativeAudioEnd()

        def stop(self, _handle):
            self.stop_called.set()

        def destroy(self, _handle):
            assert self.read_finished.is_set()

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=False,
                dropped_blocks=0,
            )

    api = FakeAPI()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=False,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )

    stream.start()
    assert api.read_entered.wait(0.5)
    stream.close()

    assert api.stop_called.is_set()
    assert api.read_finished.is_set()
    assert api.read_starved.is_set() is False


def test_native_stream_forwards_live_dsp_updates_but_not_agc_mode():
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeDSPConfig,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def __init__(self) -> None:
            self.updated: list[NativeDSPConfig] = []

        def create(self, _config):
            return object()

        def start(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=44100.0,
                agc_enabled=False,
                dropped_blocks=0,
            )

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            raise NativeAudioEnd()

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            return None

        def set_dsp(self, _handle, config):
            self.updated.append(config)

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=44100.0,
                agc_enabled=False,
                dropped_blocks=0,
            )

    api = FakeAPI()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=False,
        gain=2.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )
    stream.start()

    stream.set_dsp(
        gain=4.0,
        highpass_filter=False,
        highpass_freq=120,
        soft_limiter=False,
    )
    stream.close()

    assert api.updated == [
        NativeDSPConfig(
            gain=4.0,
            highpass_filter=False,
            highpass_freq=120.0,
            soft_limiter=False,
        )
    ]


def test_native_stream_invalidates_microphone_mode_when_route_state_drifts():
    """A post-stop Control Center value must not replace the running observation."""
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def __init__(self) -> None:
            self.diagnostics_calls = 0

        def create(self, _config):
            return object()

        def start(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                preferred_microphone_mode="standard",
                active_microphone_mode="standard",
            )

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            raise NativeAudioEnd()

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            return None

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            self.diagnostics_calls += 1
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                # Simulate the value observed after AVAudioEngine stopped.
                preferred_microphone_mode="voice_isolation",
                active_microphone_mode="voice_isolation",
            )

    api = FakeAPI()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
        microphone_mode_reader=lambda: ("voice_isolation", "voice_isolation"),
    )

    stream.start()
    stream.drain()
    diagnostics = stream.diagnostics

    assert diagnostics.preferred_microphone_mode is None
    assert diagnostics.active_microphone_mode is None
    # drain refreshes native counters once; the stopped property is cached and
    # cannot accidentally re-read a post-stop Control Center value.
    assert api.diagnostics_calls == 1
    stream.close()


def test_native_stream_keeps_an_immutable_start_mode_for_drift_detection():
    """A live diagnostics read must not redefine the session's start state."""
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def create(self, _config):
            return object()

        def start(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                preferred_microphone_mode="standard",
                active_microphone_mode="standard",
            )

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            raise NativeAudioEnd()

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            return None

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                preferred_microphone_mode="voice_isolation",
                active_microphone_mode="voice_isolation",
            )

    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=FakeAPI(),
        microphone_mode_reader=lambda: ("voice_isolation", "voice_isolation"),
    )

    stream.start()
    assert stream.diagnostics.active_microphone_mode == "voice_isolation"
    stream.drain()

    assert stream.diagnostics.preferred_microphone_mode is None
    assert stream.diagnostics.active_microphone_mode is None
    stream.close()


def test_native_library_search_can_be_overridden_without_opening_microphone(
    tmp_path,
    monkeypatch,
):
    from vocal_more.core import native_audio_capture

    library = tmp_path / "libvocal_more_audio.dylib"
    library.write_bytes(b"not-a-real-library")
    monkeypatch.setenv("VOCAL_MORE_NATIVE_AUDIO_LIBRARY", str(library))

    assert native_audio_capture.native_audio_library_path() == library


def test_native_stream_never_destroys_a_handle_used_by_a_live_consumer():
    from vocal_more.core.native_audio_capture import (
        NativeAudioUnavailable,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def __init__(self) -> None:
            self.destroy_calls = 0

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            self.destroy_calls += 1

        def diagnostics(self, _handle):
            raise AssertionError("diagnostics should not run after a failed drain")

    class LiveConsumer:
        def join(self, timeout=None):
            del timeout

        def is_alive(self):
            return True

    api = FakeAPI()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )
    stream._handle = object()
    stream._consumer = LiveConsumer()

    with pytest.raises(NativeAudioUnavailable, match="did not drain"):
        stream.close()

    assert api.destroy_calls == 0


def test_native_close_serializes_destroy_against_live_foreign_calls():
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def __init__(self) -> None:
            self.dsp_entered = threading.Event()
            self.release_dsp = threading.Event()
            self.destroyed = threading.Event()

        def set_dsp(self, _handle, _config):
            self.dsp_entered.set()
            assert self.release_dsp.wait(1.0)

        def stop(self, _handle):
            return None

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
            )

        def destroy(self, _handle):
            self.destroyed.set()

    api = FakeAPI()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )
    stream._handle = object()
    stream._consumer = None

    dsp_thread = threading.Thread(
        target=lambda: stream.set_dsp(
            gain=2.0,
            highpass_filter=True,
            highpass_freq=180,
            soft_limiter=True,
        )
    )
    dsp_thread.start()
    assert api.dsp_entered.wait(1.0)

    close_thread = threading.Thread(target=stream.close)
    close_thread.start()
    try:
        assert not api.destroyed.wait(0.1)
    finally:
        api.release_dsp.set()
        dsp_thread.join(timeout=1.0)
        close_thread.join(timeout=1.0)

    assert not dsp_thread.is_alive()
    assert not close_thread.is_alive()
    assert api.destroyed.is_set()


@pytest.mark.parametrize(
    ("failure_source", "expected_code"),
    [
        ("read", "native_consumer_read_failed"),
        ("callback", "native_pcm_callback_failed"),
    ],
)
def test_native_consumer_failures_are_reported_in_runtime_diagnostics(
    failure_source,
    expected_code,
):
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeAudioPacket,
        NativeMacOSVoiceProcessingStream,
    )

    packet = NativeAudioPacket(pcm=b"\x01\x00", frames=1, rms=0.1)

    class FakeAPI:
        def __init__(self) -> None:
            self.read_count = 0

        def create(self, _config):
            return object()

        def start(self, _handle):
            return self.diagnostics(_handle)

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            self.read_count += 1
            if failure_source == "read":
                raise RuntimeError("read failed")
            if self.read_count == 1:
                return packet
            raise NativeAudioEnd()

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            return None

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
            )

    def callback(_pcm, _rms):
        if failure_source == "callback":
            raise RuntimeError("callback failed")

    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=callback,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=FakeAPI(),
    )

    stream.start()
    stream.drain()

    assert stream.diagnostics.runtime_fault_count == 1
    assert stream.diagnostics.runtime_fault_code == expected_code

    stream.close()


@pytest.mark.parametrize(
    ("native_code", "expected_code"),
    [
        (2, "native_converter_failed"),
        (3, "native_remove_tap_failed"),
        (4, "native_engine_stop_failed"),
        (5, "native_disable_voice_processing_failed"),
    ],
)
def test_ctypes_diagnostics_maps_native_worker_faults(
    monkeypatch,
    native_code,
    expected_code,
):
    from vocal_more.core import macos_microphone_mode
    from vocal_more.core.native_audio_capture import _CtypesNativeAudioAPI

    monkeypatch.setattr(
        macos_microphone_mode,
        "macos_microphone_modes",
        lambda: ("voice_isolation", "standard"),
    )

    class FakeLibrary:
        @staticmethod
        def vm_audio_source_sample_rate(_handle):
            return 48000.0

        @staticmethod
        def vm_audio_agc_enabled(_handle):
            return True

        @staticmethod
        def vm_audio_dropped_blocks(_handle):
            return 3

        @staticmethod
        def vm_audio_runtime_fault_count(_handle):
            return 2

        @staticmethod
        def vm_audio_runtime_fault_code(_handle):
            return native_code

    api = object.__new__(_CtypesNativeAudioAPI)
    api._library = FakeLibrary()
    api._start_verified_handles = {1}

    diagnostics = api.diagnostics(1)

    assert diagnostics.runtime_fault_count == 2
    assert diagnostics.runtime_fault_code == expected_code
    assert diagnostics.start_verified is True
    assert diagnostics.diagnostics_fresh is True
    assert diagnostics.voice_processing_enabled is True
    assert diagnostics.preferred_microphone_mode == "voice_isolation"
    assert diagnostics.active_microphone_mode == "standard"


def test_native_close_is_bounded_while_native_stop_is_blocked(monkeypatch):
    from vocal_more.core import native_audio_capture
    from vocal_more.core.native_audio_capture import (
        NativeAudioUnavailable,
        NativeMacOSVoiceProcessingStream,
    )

    monkeypatch.setattr(native_audio_capture, "_NATIVE_STOP_TIMEOUT_SECONDS", 0.05)

    class FakeAPI:
        def __init__(self) -> None:
            self.stop_entered = threading.Event()
            self.release_stop = threading.Event()
            self.destroyed = threading.Event()

        def stop(self, _handle):
            self.stop_entered.set()
            self.release_stop.wait()

        def destroy(self, _handle):
            self.destroyed.set()

    api = FakeAPI()
    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )
    stream._handle = object()

    started_at = time.monotonic()
    try:
        with pytest.raises(NativeAudioUnavailable, match="stop did not complete"):
            stream.close()
        assert time.monotonic() - started_at < 0.5
        assert api.stop_entered.is_set()
        assert not api.destroyed.is_set()
    finally:
        api.release_stop.set()

    assert api.destroyed.wait(1.0)


def test_native_callback_can_close_its_own_stream_without_destroying_inflight_handle():
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeAudioPacket,
        NativeMacOSVoiceProcessingStream,
    )

    packet = NativeAudioPacket(pcm=b"\x01\x00", frames=1, rms=0.1)

    class FakeAPI:
        def __init__(self) -> None:
            self.read_calls = 0
            self.callback_finishing = threading.Event()
            self.destroyed = threading.Event()

        def create(self, _config):
            return object()

        def start(self, _handle):
            return self.diagnostics(_handle)

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            self.read_calls += 1
            if self.read_calls == 1:
                return packet
            raise NativeAudioEnd()

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            # Destruction must be owned by the deferred cleanup thread, after
            # the consumer has returned from both the user callback and read
            # loop. Calling destroy directly inside close() would be a UAF.
            assert self.callback_finishing.is_set()
            self.destroyed.set()

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                voice_processing_enabled=True,
                start_verified=True,
                diagnostics_fresh=True,
            )

    api = FakeAPI()
    stream = None

    def callback(_pcm, _rms):
        assert stream is not None
        stream.close()
        assert not api.destroyed.is_set()
        api.callback_finishing.set()

    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=callback,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=api,
    )

    stream.start()

    assert api.destroyed.wait(1.0)
    assert api.read_calls == 1
    stream.close()  # Python facade close remains idempotent.


def test_native_diagnostics_failure_is_monotonic_and_fails_closed():
    from vocal_more.core.native_audio_capture import (
        NativeAudioDiagnostics,
        NativeAudioEnd,
        NativeMacOSVoiceProcessingStream,
    )

    class FakeAPI:
        def __init__(self) -> None:
            self.diagnostics_calls = 0

        def create(self, _config):
            return object()

        def start(self, _handle):
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                voice_processing_enabled=True,
                start_verified=True,
                diagnostics_fresh=True,
            )

        def read(self, _handle, *, timeout_seconds):
            del timeout_seconds
            raise NativeAudioEnd()

        def stop(self, _handle):
            return None

        def destroy(self, _handle):
            return None

        def set_dsp(self, _handle, _config):
            return None

        def diagnostics(self, _handle):
            self.diagnostics_calls += 1
            if self.diagnostics_calls == 1:
                raise RuntimeError("diagnostics read failed")
            return NativeAudioDiagnostics(
                backend="objective_cpp",
                source_sample_rate_hz=48000.0,
                agc_enabled=True,
                dropped_blocks=0,
                voice_processing_enabled=True,
                start_verified=True,
                diagnostics_fresh=True,
            )

    stream = NativeMacOSVoiceProcessingStream(
        pcm_callback=lambda _pcm, _rms: None,
        sample_rate=16000,
        blocksize=640,
        automatic_gain=True,
        gain=1.0,
        highpass_filter=True,
        highpass_freq=200,
        soft_limiter=True,
        api=FakeAPI(),
    )
    stream.start()

    failed = stream.diagnostics
    assert failed.start_verified is True
    assert failed.diagnostics_fresh is False
    assert failed.voice_processing_enabled is False
    assert failed.agc_enabled is False
    assert failed.runtime_fault_count == 1
    assert failed.runtime_fault_code == "native_diagnostics_read_failed"

    recovered_read = stream.diagnostics
    assert recovered_read.diagnostics_fresh is True
    assert recovered_read.runtime_fault_count == 1
    assert recovered_read.runtime_fault_code == "native_diagnostics_read_failed"
    stream.close()
