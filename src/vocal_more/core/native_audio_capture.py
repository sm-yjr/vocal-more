"""ctypes bridge to Vocal More's optional Objective-C++ audio runtime.

The native library owns AVAudioEngine's realtime tap, sample-rate conversion,
small-block DSP, and a bounded SPSC queue.  Python only consumes completed PCM
blocks on a normal daemon thread.  The module is optional so source installs
and non-macOS tests keep the existing PyObjC/PortAudio fallbacks.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
import os
from pathlib import Path
import sys
import threading
import time
from typing import Callable, Optional, Protocol


NATIVE_AUDIO_ABI_VERSION = 1
NATIVE_AUDIO_ABI_V1_REQUIRED_SYMBOLS = (
    "vm_audio_abi_version",
    "vm_audio_create",
    "vm_audio_start",
    "vm_audio_read",
    "vm_audio_stop",
    "vm_audio_destroy",
    "vm_audio_set_dsp",
    "vm_audio_source_sample_rate",
    "vm_audio_agc_enabled",
    "vm_audio_dropped_blocks",
    "vm_audio_runtime_fault_count",
    "vm_audio_runtime_fault_code",
)
_LIBRARY_NAME = "libvocal_more_audio.dylib"
_CONSUMER_THREAD_NAME = "vocal-more-native-audio-consumer"
_NATIVE_STOP_TIMEOUT_SECONDS = 2.0
_NATIVE_RUNTIME_FAULT_CODES = {
    1: "native_converter_input_unavailable",
    2: "native_converter_failed",
    3: "native_remove_tap_failed",
    4: "native_engine_stop_failed",
    5: "native_disable_voice_processing_failed",
}


class NativeAudioUnavailable(RuntimeError):
    """Raised when the optional native capture runtime cannot be used."""

    def __init__(
        self,
        details: str,
        *,
        code: str = "native_audio_unavailable",
        stage: str = "native_audio",
    ) -> None:
        super().__init__(details)
        self.code = code
        self.stage = stage


class NativeAudioEnd(Exception):
    """Internal sentinel raised after the native queue has been drained."""


@dataclass(frozen=True)
class NativeDSPConfig:
    gain: float
    highpass_filter: bool
    highpass_freq: float
    soft_limiter: bool


@dataclass(frozen=True)
class NativeCaptureConfig:
    sample_rate: int
    blocksize: int
    queue_blocks: int
    automatic_gain: bool
    dsp: NativeDSPConfig


@dataclass(frozen=True)
class NativeAudioPacket:
    pcm: bytes
    frames: int
    rms: float


@dataclass(frozen=True)
class NativeAudioDiagnostics:
    backend: str
    source_sample_rate_hz: float
    agc_enabled: bool
    dropped_blocks: int
    source_channels: int = 1
    # Voice Processing and AGC are verified snapshots taken at engine start;
    # ABI v1 does not continuously poll those Audio Unit properties.
    voice_processing_enabled: bool = False
    start_verified: bool = False
    # True means the latest requested native counter/mode read succeeded. It
    # does not turn the start snapshots above into continuous live getters.
    diagnostics_fresh: bool = False
    converter_name: str = "AVAudioConverter/vDSP"
    runtime_fault_count: int = 0
    runtime_fault_code: str | None = None
    preferred_microphone_mode: str | None = None
    active_microphone_mode: str | None = None


class NativeAudioAPI(Protocol):
    def create(self, config: NativeCaptureConfig): ...

    def start(self, handle) -> NativeAudioDiagnostics: ...

    def read(
        self,
        handle,
        *,
        timeout_seconds: float,
    ) -> NativeAudioPacket | None: ...

    def stop(self, handle) -> None: ...

    def destroy(self, handle) -> None: ...

    def set_dsp(self, handle, config: NativeDSPConfig) -> None: ...

    def diagnostics(self, handle) -> NativeAudioDiagnostics: ...


def native_audio_library_path() -> Optional[Path]:
    """Return the first configured native library without loading it."""
    override = os.environ.get("VOCAL_MORE_NATIVE_AUDIO_LIBRARY", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    module_dir = Path(__file__).resolve().parent
    candidates.extend(
        (
            module_dir / "native" / _LIBRARY_NAME,
            module_dir.parent / "native" / _LIBRARY_NAME,
            module_dir.parents[2] / ".build" / "native" / _LIBRARY_NAME,
        )
    )
    try:
        contents = Path(sys.executable).resolve().parents[1]
    except (IndexError, OSError):
        contents = None
    if contents is not None:
        candidates.extend(
            (
                contents / "Frameworks" / _LIBRARY_NAME,
                contents / "Resources" / "native" / _LIBRARY_NAME,
            )
        )

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def native_audio_library_available() -> bool:
    """Check the library ABI without creating an engine or opening the mic."""
    try:
        _CtypesNativeAudioAPI.from_default_path()
    except Exception:
        return False
    return True


class _CtypesNativeAudioAPI:
    """Typed adapter around the stable C ABI exported by the dylib."""

    _ERROR_CAPACITY = 1024

    def __init__(self, library: ctypes.CDLL) -> None:
        self._library = library
        self._buffers: dict[int, ctypes.Array] = {}
        self._configs: dict[int, NativeCaptureConfig] = {}
        self._start_verified_handles: set[int] = set()
        self._bind()
        observed = int(self._library.vm_audio_abi_version())
        if observed != NATIVE_AUDIO_ABI_VERSION:
            raise NativeAudioUnavailable(
                "Native audio ABI mismatch: "
                f"expected {NATIVE_AUDIO_ABI_VERSION}, observed {observed}"
            )

    @classmethod
    def from_default_path(cls) -> "_CtypesNativeAudioAPI":
        path = native_audio_library_path()
        if path is None:
            raise NativeAudioUnavailable("Native audio library is not bundled")
        try:
            return cls(ctypes.CDLL(str(path)))
        except OSError as exc:
            raise NativeAudioUnavailable(
                f"Native audio library could not be loaded: {exc}"
            ) from exc

    def _bind(self) -> None:
        lib = self._library
        lib.vm_audio_abi_version.argtypes = []
        lib.vm_audio_abi_version.restype = ctypes.c_uint32
        lib.vm_audio_create.argtypes = [
            ctypes.c_int32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_float,
            ctypes.c_bool,
            ctypes.c_float,
            ctypes.c_bool,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        lib.vm_audio_create.restype = ctypes.c_void_p
        lib.vm_audio_start.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        lib.vm_audio_start.restype = ctypes.c_int32
        lib.vm_audio_read.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        lib.vm_audio_read.restype = ctypes.c_int32
        lib.vm_audio_stop.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        lib.vm_audio_stop.restype = ctypes.c_int32
        lib.vm_audio_destroy.argtypes = [ctypes.c_void_p]
        lib.vm_audio_destroy.restype = None
        lib.vm_audio_set_dsp.argtypes = [
            ctypes.c_void_p,
            ctypes.c_float,
            ctypes.c_bool,
            ctypes.c_float,
            ctypes.c_bool,
        ]
        lib.vm_audio_set_dsp.restype = None
        lib.vm_audio_source_sample_rate.argtypes = [ctypes.c_void_p]
        lib.vm_audio_source_sample_rate.restype = ctypes.c_double
        lib.vm_audio_agc_enabled.argtypes = [ctypes.c_void_p]
        lib.vm_audio_agc_enabled.restype = ctypes.c_bool
        lib.vm_audio_dropped_blocks.argtypes = [ctypes.c_void_p]
        lib.vm_audio_dropped_blocks.restype = ctypes.c_uint64
        lib.vm_audio_runtime_fault_count.argtypes = [ctypes.c_void_p]
        lib.vm_audio_runtime_fault_count.restype = ctypes.c_uint64
        lib.vm_audio_runtime_fault_code.argtypes = [ctypes.c_void_p]
        lib.vm_audio_runtime_fault_code.restype = ctypes.c_int32

    @staticmethod
    def _handle_key(handle) -> int:
        if isinstance(handle, ctypes.c_void_p):
            return int(handle.value or 0)
        return int(handle)

    def _error_buffer(self):
        return ctypes.create_string_buffer(self._ERROR_CAPACITY)

    @staticmethod
    def _error_text(buffer) -> str:
        return buffer.value.decode("utf-8", errors="replace").strip()

    def create(self, config: NativeCaptureConfig):
        error = self._error_buffer()
        handle = self._library.vm_audio_create(
            int(config.sample_rate),
            int(config.blocksize),
            int(config.queue_blocks),
            bool(config.automatic_gain),
            float(config.dsp.gain),
            bool(config.dsp.highpass_filter),
            float(config.dsp.highpass_freq),
            bool(config.dsp.soft_limiter),
            error,
            len(error),
        )
        if not handle:
            raise NativeAudioUnavailable(
                self._error_text(error) or "Native audio allocation failed"
            )
        key = self._handle_key(handle)
        self._configs[key] = config
        self._buffers[key] = (ctypes.c_int16 * int(config.blocksize))()
        return handle

    def start(self, handle) -> NativeAudioDiagnostics:
        error = self._error_buffer()
        result = self._library.vm_audio_start(handle, error, len(error))
        if result != 0:
            raise NativeAudioUnavailable(
                self._error_text(error) or "Native audio engine failed to start"
            )
        self._start_verified_handles.add(self._handle_key(handle))
        return self.diagnostics(handle)

    def read(
        self,
        handle,
        *,
        timeout_seconds: float,
    ) -> NativeAudioPacket | None:
        key = self._handle_key(handle)
        config = self._configs[key]
        pcm = self._buffers[key]
        frames = ctypes.c_uint32()
        rms = ctypes.c_float()
        error = self._error_buffer()
        timeout_ms = max(0, min(60_000, round(timeout_seconds * 1000)))
        result = self._library.vm_audio_read(
            handle,
            pcm,
            int(config.blocksize),
            ctypes.byref(frames),
            ctypes.byref(rms),
            timeout_ms,
            error,
            len(error),
        )
        if result == 0:
            return None
        if result == 2:
            raise NativeAudioEnd()
        if result != 1:
            raise NativeAudioUnavailable(
                self._error_text(error) or "Native audio queue read failed"
            )
        frame_count = int(frames.value)
        return NativeAudioPacket(
            pcm=ctypes.string_at(pcm, frame_count * ctypes.sizeof(ctypes.c_int16)),
            frames=frame_count,
            rms=float(rms.value),
        )

    def stop(self, handle) -> None:
        error = self._error_buffer()
        result = self._library.vm_audio_stop(handle, error, len(error))
        if result != 0:
            raise NativeAudioUnavailable(
                self._error_text(error) or "Native audio engine failed to stop"
            )

    def destroy(self, handle) -> None:
        key = self._handle_key(handle)
        if key not in self._configs:
            # The Python adapter owns each ABI v1 handle exactly once. This
            # guard keeps facade close idempotent without claiming that the C
            # destroy function accepts a stale non-null pointer.
            return
        self._library.vm_audio_destroy(handle)
        self._buffers.pop(key, None)
        self._configs.pop(key, None)
        self._start_verified_handles.discard(key)

    def set_dsp(self, handle, config: NativeDSPConfig) -> None:
        self._library.vm_audio_set_dsp(
            handle,
            float(config.gain),
            bool(config.highpass_filter),
            float(config.highpass_freq),
            bool(config.soft_limiter),
        )

    def diagnostics(self, handle) -> NativeAudioDiagnostics:
        # AVCaptureDevice exposes the Control Center preference and the mode
        # that is actually active process-wide. Reading those class properties
        # neither enumerates devices nor opens another input stream, so the C
        # ABI can stay focused on realtime capture ownership.
        from .macos_microphone_mode import macos_microphone_modes

        key = self._handle_key(handle)
        start_verified = key in self._start_verified_handles
        preferred_microphone_mode, active_microphone_mode = (
            macos_microphone_modes()
        )
        runtime_fault_count = int(
            self._library.vm_audio_runtime_fault_count(handle)
        )
        native_fault_code = int(self._library.vm_audio_runtime_fault_code(handle))
        runtime_fault_code = None
        if runtime_fault_count > 0:
            runtime_fault_code = _NATIVE_RUNTIME_FAULT_CODES.get(
                native_fault_code,
                f"native_runtime_fault_{native_fault_code}",
            )
        return NativeAudioDiagnostics(
            backend="objective_cpp",
            source_sample_rate_hz=float(
                self._library.vm_audio_source_sample_rate(handle)
            ),
            agc_enabled=bool(self._library.vm_audio_agc_enabled(handle)),
            dropped_blocks=int(self._library.vm_audio_dropped_blocks(handle)),
            voice_processing_enabled=start_verified,
            start_verified=start_verified,
            diagnostics_fresh=True,
            runtime_fault_count=runtime_fault_count,
            runtime_fault_code=runtime_fault_code,
            preferred_microphone_mode=preferred_microphone_mode,
            active_microphone_mode=active_microphone_mode,
        )


class NativeMacOSVoiceProcessingStream:
    """Stream facade whose callback always runs outside the realtime thread."""

    backend_name = "objective_cpp"
    delivers_processed_pcm = True

    def __init__(
        self,
        *,
        pcm_callback: Callable[[bytes, float], None],
        sample_rate: int,
        blocksize: int,
        automatic_gain: bool,
        gain: float,
        highpass_filter: bool,
        highpass_freq: int,
        soft_limiter: bool,
        queue_blocks: int = 32,
        api: Optional[NativeAudioAPI] = None,
        microphone_mode_reader: Optional[
            Callable[[], tuple[str | None, str | None]]
        ] = None,
    ) -> None:
        self._pcm_callback = pcm_callback
        self._config = NativeCaptureConfig(
            sample_rate=int(sample_rate),
            blocksize=int(blocksize),
            queue_blocks=max(4, int(queue_blocks)),
            automatic_gain=bool(automatic_gain),
            dsp=NativeDSPConfig(
                gain=float(gain),
                highpass_filter=bool(highpass_filter),
                highpass_freq=float(highpass_freq),
                soft_limiter=bool(soft_limiter),
            ),
        )
        self._api = api or _CtypesNativeAudioAPI.from_default_path()
        if microphone_mode_reader is None:
            from .macos_microphone_mode import macos_microphone_modes

            microphone_mode_reader = macos_microphone_modes
        self._microphone_mode_reader = microphone_mode_reader
        self._handle = None
        self._consumer: Optional[threading.Thread] = None
        self._stopped = False
        self._closed = False
        self._consumer_error: Optional[Exception] = None
        self._consumer_fault_count = 0
        self._consumer_fault_code: Optional[str] = None
        # Serialize ownership changes separately from foreign calls. ctypes
        # releases the GIL, so a config update or diagnostics read can otherwise
        # race vm_audio_destroy() even though all callers are Python threads.
        self._lifecycle_lock = threading.RLock()
        self._foreign_call_lock = threading.RLock()
        self._deferred_cleanup: Optional[threading.Thread] = None
        self._stop_thread: Optional[threading.Thread] = None
        self._stop_done = threading.Event()
        self._stop_error: Optional[Exception] = None
        self._start_preferred_microphone_mode: Optional[str] = None
        self._start_active_microphone_mode: Optional[str] = None
        self._microphone_mode_drifted = False
        self._diagnostics = NativeAudioDiagnostics(
            backend=self.backend_name,
            source_sample_rate_hz=0.0,
            agc_enabled=False,
            dropped_blocks=0,
        )
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._handle is not None:
                    return
                if self._closed:
                    raise NativeAudioUnavailable("Native audio stream is closed")
            with self._foreign_call_lock:
                handle = self._api.create(self._config)
                try:
                    diagnostics = self._api.start(handle)
                except Exception:
                    self._api.destroy(handle)
                    raise

            consumer = threading.Thread(
                target=self._consume,
                args=(handle,),
                name=_CONSUMER_THREAD_NAME,
                daemon=True,
            )
            with self._lock:
                self._handle = handle
                self._diagnostics = diagnostics
                self._start_preferred_microphone_mode = (
                    diagnostics.preferred_microphone_mode
                )
                self._start_active_microphone_mode = (
                    diagnostics.active_microphone_mode
                )
                self._microphone_mode_drifted = False
                self._consumer = consumer
                self._consumer_error = None
                self._consumer_fault_count = 0
                self._consumer_fault_code = None
                self._stopped = False
                self._stop_thread = None
                self._stop_done = threading.Event()
                self._stop_error = None
            consumer.start()

    def _consume(self, handle) -> None:
        while True:
            with self._lock:
                if self._handle is not handle:
                    return
            try:
                with self._foreign_call_lock:
                    packet = self._api.read(handle, timeout_seconds=0.05)
            except NativeAudioEnd:
                return
            except Exception as exc:
                self._record_consumer_fault(exc, "native_consumer_read_failed")
                print(f"[AudioRecorder] Native audio consumer failed: {exc}")
                return
            if packet is None:
                continue
            try:
                self._pcm_callback(packet.pcm, packet.rms)
            except Exception as exc:
                # This is a normal Python worker. Keep one bad observer from
                # terminating capture, while leaving an actionable diagnostic.
                self._record_consumer_fault(exc, "native_pcm_callback_failed")
                print(f"[AudioRecorder] Native PCM callback failed: {exc}")

    def _record_consumer_fault(self, error: Exception, code: str) -> None:
        with self._lock:
            self._consumer_error = error
            self._consumer_fault_count += 1
            self._consumer_fault_code = code

    def _merge_consumer_faults(
        self,
        diagnostics: NativeAudioDiagnostics,
    ) -> NativeAudioDiagnostics:
        with self._lock:
            fault_count = self._consumer_fault_count
            fault_code = self._consumer_fault_code
        if fault_count == 0:
            return diagnostics
        return replace(
            diagnostics,
            runtime_fault_count=diagnostics.runtime_fault_count + fault_count,
            runtime_fault_code=fault_code or diagnostics.runtime_fault_code,
        )

    def set_dsp(
        self,
        *,
        gain: float,
        highpass_filter: bool,
        highpass_freq: int,
        soft_limiter: bool,
    ) -> None:
        config = NativeDSPConfig(
            gain=float(gain),
            highpass_filter=bool(highpass_filter),
            highpass_freq=float(highpass_freq),
            soft_limiter=bool(soft_limiter),
        )
        with self._lock:
            handle = self._handle
        if handle is not None:
            with self._foreign_call_lock:
                # Recheck ownership after waiting for an in-flight close.
                with self._lock:
                    if self._handle is not handle:
                        return
                self._api.set_dsp(handle, config)

    @property
    def diagnostics(self) -> NativeAudioDiagnostics:
        with self._lock:
            handle = self._handle
            cached = self._diagnostics
            stopped = self._stopped
        if handle is None or stopped:
            return self._merge_consumer_faults(cached)
        try:
            with self._foreign_call_lock:
                with self._lock:
                    still_owned = self._handle is handle
                    if not still_owned:
                        cached = self._diagnostics
                if not still_owned:
                    return self._merge_consumer_faults(cached)
                observed = self._api.diagnostics(handle)
        except Exception as exc:
            self._record_consumer_fault(
                exc,
                "native_diagnostics_read_failed",
            )
            failed_closed = replace(
                cached,
                voice_processing_enabled=False,
                agc_enabled=False,
                diagnostics_fresh=False,
            )
            with self._lock:
                if self._handle is handle:
                    self._diagnostics = failed_closed
            return self._merge_consumer_faults(failed_closed)
        with self._lock:
            if (
                observed.preferred_microphone_mode
                != self._start_preferred_microphone_mode
                or observed.active_microphone_mode
                != self._start_active_microphone_mode
            ):
                self._microphone_mode_drifted = True
            self._diagnostics = observed
        return self._merge_consumer_faults(observed)

    def drain(self) -> None:
        with self._lifecycle_lock:
            deadline = time.monotonic() + _NATIVE_STOP_TIMEOUT_SECONDS
            with self._lock:
                handle = self._handle
                consumer = self._consumer
                already_stopped = self._stopped
                start_preferred = self._start_preferred_microphone_mode
                start_active = self._start_active_microphone_mode
                microphone_mode_drifted = self._microphone_mode_drifted
                if handle is None:
                    return
                self._stopped = True
            if not already_stopped:
                try:
                    end_preferred, end_active = self._microphone_mode_reader()
                except Exception:
                    end_preferred, end_active = None, None
                microphone_mode_stable = bool(
                    not microphone_mode_drifted
                    and start_preferred is not None
                    and start_active is not None
                    and end_preferred == start_preferred
                    and end_active == start_active
                )
                with self._lock:
                    self._diagnostics = replace(
                        self._diagnostics,
                        preferred_microphone_mode=(
                            start_preferred if microphone_mode_stable else None
                        ),
                        active_microphone_mode=(
                            start_active if microphone_mode_stable else None
                        ),
                    )
            if not already_stopped or self._stop_thread is None:
                self._start_native_stop(handle)
            with self._lock:
                stop_done = self._stop_done
            if not stop_done.wait(max(0.0, deadline - time.monotonic())):
                raise NativeAudioUnavailable(
                    "Native audio stop did not complete within two seconds"
                )
            with self._lock:
                stop_error = self._stop_error
            if stop_error is not None:
                raise stop_error
            if consumer is not None and consumer is not threading.current_thread():
                consumer.join(timeout=max(0.0, deadline - time.monotonic()))
                if consumer.is_alive():
                    raise NativeAudioUnavailable(
                        "Native audio consumer did not drain within two seconds"
                    )
            try:
                with self._foreign_call_lock:
                    observed = self._api.diagnostics(handle)
            except Exception as exc:
                self._record_consumer_fault(
                    exc,
                    "native_diagnostics_read_failed",
                )
                with self._lock:
                    self._diagnostics = replace(
                        self._diagnostics,
                        voice_processing_enabled=False,
                        agc_enabled=False,
                        diagnostics_fresh=False,
                    )
                observed = None
            if observed is not None:
                with self._lock:
                    stable_preferred = (
                        self._diagnostics.preferred_microphone_mode
                    )
                    stable_active = self._diagnostics.active_microphone_mode
                    self._diagnostics = replace(
                        observed,
                        preferred_microphone_mode=stable_preferred,
                        active_microphone_mode=stable_active,
                    )

    def _start_native_stop(self, handle) -> None:
        with self._lock:
            if self._stop_thread is not None:
                return
            stop_done = self._stop_done

        def stop_native() -> None:
            error: Optional[Exception] = None
            try:
                with self._foreign_call_lock:
                    self._api.stop(handle)
            except Exception as exc:
                error = exc
            finally:
                with self._lock:
                    self._stop_error = error
                stop_done.set()

        stop_thread = threading.Thread(
            target=stop_native,
            name="vocal-more-native-audio-stop",
            daemon=True,
        )
        with self._lock:
            self._stop_thread = stop_thread
        stop_thread.start()

    def stop(self) -> None:
        self.drain()

    def abort(self) -> None:
        self.drain()

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._closed:
                    return
            try:
                self.drain()
            except Exception:
                with self._lock:
                    handle = self._handle
                    consumer = self._consumer
                    stop_thread = self._stop_thread
                consumer_is_supervisable = consumer is None or isinstance(
                    consumer,
                    threading.Thread,
                )
                has_live_consumer = isinstance(consumer, threading.Thread) and (
                    consumer.is_alive()
                )
                has_live_stop = stop_thread is not None and stop_thread.is_alive()
                if (
                    handle is not None
                    and consumer_is_supervisable
                    and (has_live_consumer or has_live_stop)
                ):
                    self._schedule_deferred_destroy(handle, consumer, stop_thread)
                # Never free a handle while the consumer may still be inside
                # vm_audio_read() or a user callback. A bounded leak is safer
                # than a use-after-free when an injected/fake consumer cannot be
                # supervised by the deferred cleanup worker.
                raise

            with self._lock:
                handle = self._handle
                consumer = self._consumer
                stop_thread = self._stop_thread
            if handle is not None and consumer is threading.current_thread():
                # close() was invoked from the PCM callback. The current stack
                # still owns `handle`, so a helper must wait for this consumer
                # thread to return before it performs the one-shot C destroy.
                self._schedule_deferred_destroy(handle, consumer, stop_thread)
                return
            with self._lock:
                self._handle = None
                self._consumer = None
                self._closed = True
            if handle is not None:
                with self._foreign_call_lock:
                    self._api.destroy(handle)

    def _schedule_deferred_destroy(
        self,
        handle,
        consumer: Optional[threading.Thread],
        stop_thread: Optional[threading.Thread],
    ) -> None:
        """Detach a wedged consumer and destroy only after it really exits."""
        with self._lock:
            if self._deferred_cleanup is not None:
                return
            self._handle = None
            self._consumer = None
            self._closed = True

        def cleanup() -> None:
            if stop_thread is not None:
                stop_thread.join()
            if consumer is not None:
                consumer.join()
            try:
                with self._foreign_call_lock:
                    self._api.destroy(handle)
            except Exception as exc:
                print(f"[AudioRecorder] Deferred native audio cleanup failed: {exc}")

        cleanup_thread = threading.Thread(
            target=cleanup,
            name="vocal-more-native-audio-cleanup",
            daemon=True,
        )
        with self._lock:
            self._deferred_cleanup = cleanup_thread
        cleanup_thread.start()


def build_native_voice_processing_stream(**kwargs):
    """Construct a native stream, raising before microphone access if absent."""
    return NativeMacOSVoiceProcessingStream(**kwargs)
