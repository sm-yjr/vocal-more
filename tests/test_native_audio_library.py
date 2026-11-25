"""Compile-time contract tests for the optional macOS native audio library."""

from __future__ import annotations

import ctypes
import json
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
    assert library.vm_audio_abi_version() == 2

    # ABI extensions are exercised without preparing an audio graph or asking
    # for microphone access. Core Audio IDs are intentionally not PortAudio IDs.
    library.vm_audio_list_devices.argtypes = [ctypes.POINTER(ctypes.c_char), ctypes.c_size_t]
    library.vm_audio_list_devices.restype = ctypes.c_int32
    devices_json = ctypes.create_string_buffer(256 * 1024)
    assert library.vm_audio_list_devices(devices_json, len(devices_json)) == 0
    devices = json.loads(devices_json.value)
    assert isinstance(devices, list)
    assert all(device["max_input_channels"] > 0 and device["name"] for device in devices)
    too_small = ctypes.create_string_buffer(1)
    assert library.vm_audio_list_devices(too_small, 1) == -1
    library.vm_audio_microphone_authorization.restype = ctypes.c_int32
    assert library.vm_audio_microphone_authorization() in range(4)

    import numpy as np
    from vocal_more.core.audio_recorder import AudioRecorder

    float_pointer = ctypes.POINTER(ctypes.c_float)
    library.vm_audio_test_downmix.argtypes = [ctypes.POINTER(float_pointer), ctypes.c_uint32,
                                             ctypes.c_uint32, float_pointer]
    library.vm_audio_test_downmix.restype = ctypes.c_int32
    generator = np.random.default_rng(42)
    signal = np.sin(np.arange(1280) * 0.2).astype(np.float32)
    matrices = [signal[:, None], np.column_stack([signal, -signal]),
                np.column_stack([signal, -signal, generator.normal(0, 0.5, 1280)]),
                np.full((1280, 3), [0.1, -0.2, 0.3]), np.zeros((1280, 3)),
                np.column_stack([np.zeros(1280), signal, -signal])]
    matrices += [generator.normal(size=(257, channels)) for channels in (1, 2, 3)]
    for matrix in matrices:
        matrix = np.asarray(matrix, dtype=np.float32)
        planar = [np.ascontiguousarray(matrix[:, channel]) for channel in range(matrix.shape[1])]
        pointers = (float_pointer * len(planar))(*(channel.ctypes.data_as(float_pointer) for channel in planar))
        mixed = np.empty(len(matrix), dtype=np.float32)
        assert library.vm_audio_test_downmix(pointers, len(planar), len(matrix), mixed.ctypes.data_as(float_pointer)) == 0
        expected = AudioRecorder._coherent_array_downmix(matrix, len(matrix))
        np.testing.assert_allclose(mixed, expected, rtol=1e-6, atol=1e-7)

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

    # Low-voice DRC + thresholded soft limiter: keep quiet whisper signals linear
    # while smoothly limiting sudden loud plosives below full-scale clipping.
    quiet_frames = 128
    quiet_source = (ctypes.c_float * quiet_frames)(*([0.05] * quiet_frames))
    quiet_pcm = (ctypes.c_int16 * quiet_frames)()
    quiet_rms = ctypes.c_float()
    assert (
        library.vm_audio_test_process(
            quiet_source, quiet_frames, 2.0, True, quiet_pcm, ctypes.byref(quiet_rms)
        )
        == 0
    )
    # 0.05 * 2.0 = 0.10 (-20 dBFS, below -19 dBFS DRC knee) stays linear.
    assert abs(quiet_pcm[-1] - round(0.10 * 32767)) <= 2

    loud_frames = 256
    loud_source = (ctypes.c_float * loud_frames)(*([0.9] * loud_frames))
    loud_pcm = (ctypes.c_int16 * loud_frames)()
    loud_rms = ctypes.c_float()
    assert (
        library.vm_audio_test_process(
            loud_source, loud_frames, 4.0, True, loud_pcm, ctypes.byref(loud_rms)
        )
        == 0
    )
    # Must be smoothly compressed and strictly below full-scale hard clipping.
    assert 12000 < loud_pcm[-1] < 31200

    # 4th-order Butterworth high-pass filter (-24 dB/oct): strongly attenuates
    # 60 Hz HVAC hum while preserving 800 Hz speech energy.
    library.vm_audio_test_highpass.argtypes = [
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_uint32,
        ctypes.c_float,
        ctypes.c_float,
        ctypes.POINTER(ctypes.c_float),
    ]
    library.vm_audio_test_highpass.restype = ctypes.c_int32
    sr = 16000.0
    n_hp = 3200
    t = np.arange(n_hp, dtype=np.float32) / sr
    hum_60 = np.sin(2.0 * np.pi * 60.0 * t).astype(np.float32)
    speech_800 = np.sin(2.0 * np.pi * 800.0 * t).astype(np.float32)
    out_60 = np.empty(n_hp, dtype=np.float32)
    out_800 = np.empty(n_hp, dtype=np.float32)
    assert (
        library.vm_audio_test_highpass(
            hum_60.ctypes.data_as(float_pointer),
            n_hp,
            sr,
            200.0,
            out_60.ctypes.data_as(float_pointer),
        )
        == 0
    )
    assert (
        library.vm_audio_test_highpass(
            speech_800.ctypes.data_as(float_pointer),
            n_hp,
            sr,
            200.0,
            out_800.ctypes.data_as(float_pointer),
        )
        == 0
    )
    # Measure steady-state RMS in the second half (skip initial transient).
    attenuation_60_db = 20.0 * np.log10(
        np.sqrt(np.mean(out_60[1600:] ** 2)) / np.sqrt(np.mean(hum_60[1600:] ** 2))
    )
    passband_800_db = 20.0 * np.log10(
        np.sqrt(np.mean(out_800[1600:] ** 2)) / np.sqrt(np.mean(speech_800[1600:] ** 2))
    )
    assert attenuation_60_db < -18.0
    assert passband_800_db > -0.5

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
