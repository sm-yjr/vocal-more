"""Compile-time contract tests for the optional macOS native audio library."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import platform
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS native library")
def test_native_audio_library_builds_and_uses_accelerate_dsp(tmp_path):
    output = tmp_path / "libvocal_more_audio.dylib"
    result = subprocess.run(
        [
            str(ROOT / "scripts" / "build_native_audio.sh"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={
            **os.environ,
            "MACOSX_DEPLOYMENT_TARGET": "14.0",
            "VOCAL_MORE_TARGET_ARCH": platform.machine(),
        },
    )
    assert result.returncode == 0, result.stderr
    assert output.is_file()

    library = ctypes.CDLL(str(output))
    library.vm_audio_abi_version.restype = ctypes.c_uint32
    assert library.vm_audio_abi_version() == 1

    library.vm_audio_test_process.argtypes = [
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_uint32,
        ctypes.c_float,
        ctypes.c_bool,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_float),
    ]
    library.vm_audio_test_process.restype = ctypes.c_int32
    source = (ctypes.c_float * 4)(0.25, -0.25, 0.75, 2.0)
    output_pcm = (ctypes.c_int16 * 4)()
    rms = ctypes.c_float()

    status = library.vm_audio_test_process(
        source,
        4,
        2.0,
        False,
        output_pcm,
        ctypes.byref(rms),
    )

    assert status == 0
    assert list(output_pcm) == [16383, -16383, 32767, 32767]
    # The displayed level follows the existing post-gain, 0..1 UI contract.
    assert rms.value == pytest.approx(1.0)

    library.vm_audio_test_queue.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.vm_audio_test_queue.restype = ctypes.c_int32
    read_blocks = ctypes.c_uint32()
    dropped_blocks = ctypes.c_uint64()

    queue_status = library.vm_audio_test_queue(
        4,
        8,
        ctypes.byref(read_blocks),
        ctypes.byref(dropped_blocks),
    )

    assert queue_status == 0
    assert read_blocks.value == 6
    # One full-queue rejection and one oversized realtime input are both
    # observable losses, rather than silent capture gaps.
    assert dropped_blocks.value == 2

    library.vm_audio_create.argtypes = [
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
    library.vm_audio_create.restype = ctypes.c_void_p
    library.vm_audio_destroy.argtypes = [ctypes.c_void_p]
    library.vm_audio_destroy.restype = None
    library.vm_audio_test_record_fault.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    library.vm_audio_test_record_fault.restype = ctypes.c_int32
    library.vm_audio_runtime_fault_count.argtypes = [ctypes.c_void_p]
    library.vm_audio_runtime_fault_count.restype = ctypes.c_uint64
    library.vm_audio_runtime_fault_code.argtypes = [ctypes.c_void_p]
    library.vm_audio_runtime_fault_code.restype = ctypes.c_int32
    error = ctypes.create_string_buffer(256)
    unsafe_handle = library.vm_audio_create(
        1,
        16384,
        4,
        True,
        1.0,
        True,
        200.0,
        True,
        error,
        len(error),
    )
    assert not unsafe_handle
    assert b"Invalid native audio format" in error.value

    error = ctypes.create_string_buffer(256)
    handle = library.vm_audio_create(
        16000,
        640,
        4,
        True,
        1.0,
        True,
        200.0,
        True,
        error,
        len(error),
    )
    assert handle, error.value.decode()
    try:
        assert library.vm_audio_test_record_fault(handle, 2) == 0
        assert library.vm_audio_runtime_fault_count(handle) == 1
        assert library.vm_audio_runtime_fault_code(handle) == 2
        assert library.vm_audio_test_record_fault(handle, 5) == 0
        assert library.vm_audio_runtime_fault_count(handle) == 2
        assert library.vm_audio_runtime_fault_code(handle) == 5
    finally:
        library.vm_audio_destroy(handle)

    library.vm_audio_test_exception_boundary.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_size_t,
    ]
    library.vm_audio_test_exception_boundary.restype = ctypes.c_int32
    for exception_kind, expected_message in (
        (1, "test C++ exception"),
        (2, "test Objective-C exception"),
    ):
        exception_error = ctypes.create_string_buffer(256)
        assert (
            library.vm_audio_test_exception_boundary(
                exception_kind,
                exception_error,
                len(exception_error),
            )
            == -1
        )
        assert expected_message in exception_error.value.decode()

    install_names = subprocess.run(
        ["otool", "-D", str(output)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "@rpath/libvocal_more_audio.dylib" in install_names

    architectures = subprocess.run(
        ["lipo", "-archs", str(output)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert architectures == [platform.machine()]

    build_version = subprocess.run(
        ["vtool", "-show-build", str(output)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "platform MACOS" in build_version
    assert "minos 14.0" in build_version

    dependencies = subprocess.run(
        ["otool", "-L", str(output)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "AVFoundation.framework" in dependencies
    assert "Accelerate.framework" in dependencies
    assert str(ROOT) not in dependencies


def test_official_packaging_builds_and_embeds_native_audio_library():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert 'build_native_audio.sh" --output' in build_script
    assert "VOCAL_MORE_NATIVE_AUDIO_LIBRARY" in build_script
    assert 'Contents/Frameworks/libvocal_more_audio.dylib' in build_script


def test_app_build_cleanup_is_scoped_to_the_repository():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert 'rm -rf "$ROOT/build" "$ROOT/dist"' in build_script
    assert "\nrm -rf build dist\n" not in build_script


def test_realtime_raw_queue_publish_avoids_dispatch_wakeup():
    source = (ROOT / "native" / "audio" / "src" / "VocalMoreAudio.mm").read_text()
    raw_queue = source.split("class RawQueue final", 1)[1].split(
        "class PCMQueue final", 1
    )[0]

    assert "publish_write(position, false)" in raw_queue
    assert "dispatch_semaphore_signal" not in raw_queue


def test_pcm_queue_consumes_one_wakeup_before_reading_one_block():
    source = (ROOT / "native" / "audio" / "src" / "VocalMoreAudio.mm").read_text()
    wait_for_read = source.split("ReadState wait_for_read", 1)[1].split(
        "void publish_read", 1
    )[0]

    # Signal-before-data publication plus wait-before-data consumption keeps
    # the dispatch semaphore one-to-one with PCM blocks. Reading first leaves
    # stale tokens that accumulate for the entire recording.
    assert wait_for_read.find("dispatch_semaphore_wait") < wait_for_read.find(
        "read_.load"
    )


def test_pcm_queue_undersized_read_reports_error_and_advances_the_queue():
    source = (ROOT / "native" / "audio" / "src" / "VocalMoreAudio.mm").read_text()
    pcm_queue = source.split("class PCMQueue final", 1)[1].split(
        "void convert_float_to_pcm", 1
    )[0]
    undersized = pcm_queue.split("slot->frames > capacity", 1)[1].split(
        "return ReadState::DestinationTooSmall", 1
    )[0]
    native_read = source.split("int32_t vm_audio_read", 1)[1].split(
        "int32_t vm_audio_stop", 1
    )[0]

    # A semaphore token has already been consumed when capacity is checked.
    # The rejected block therefore has to return an explicit error and restore
    # its token. Returning Timeout without restoring it permanently wedges the
    # published queue head; consuming it would silently lose valid audio.
    assert "restore_read_wakeup()" in undersized
    assert "publish_read(position)" not in undersized
    assert "return ReadState::DestinationTooSmall" in pcm_queue
    assert "ReadState::DestinationTooSmall" in native_read
    assert "destination buffer is too small" in native_read


def test_realtime_tap_stop_handshake_is_sequentially_consistent():
    source = (ROOT / "native" / "audio" / "src" / "VocalMoreAudio.mm").read_text()
    callback_scope = source.split("class RealtimeTapCallbackScope", 1)[1].split(
        "class PCMQueue final", 1
    )[0]
    stop = source.split("void stop_accepting_tap_callbacks", 1)[1].split(
        "bool stop_stream_locked", 1
    )[0]

    # The accepting flag and in-flight count form one proof obligation. Using
    # weaker independent acquire/release operations admits store buffering:
    # stop can observe zero while a late callback observes accepting=true.
    assert callback_scope.count("std::memory_order_seq_cst") >= 3
    assert stop.count("std::memory_order_seq_cst") >= 2


def test_native_worker_converts_exceptions_into_faulted_end_of_stream():
    source = (ROOT / "native" / "audio" / "src" / "VocalMoreAudio.mm").read_text()
    worker = source.split("void worker_main", 1)[1].split(
        "bool stop_stream_locked", 1
    )[0]

    assert "@catch (NSException *exception)" in worker
    assert "VM_AUDIO_RUNTIME_FAULT_CONVERTER_FAILED" in worker
    assert worker.rfind("stream->pcm_queue->end();") > worker.find("@catch")


def test_native_worker_drains_autoreleased_objects_for_each_audio_block():
    source = (ROOT / "native" / "audio" / "src" / "VocalMoreAudio.mm").read_text()
    worker = source.split("void worker_main", 1)[1].split(
        "bool stop_stream_locked", 1
    )[0]
    conversion_loop = worker.split("while (true)", 1)[1].split(
        "if (!conversion_failed", 1
    )[0]

    assert "@autoreleasepool" in conversion_loop
