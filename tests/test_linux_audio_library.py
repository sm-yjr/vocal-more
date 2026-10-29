"""Device-free regression for Linux ABI 2, DSP and bounded PCM storage."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.skipif(sys.platform != "linux", reason="Linux PortAudio adapter")
def test_linux_audio_dsp_queue_and_abi(tmp_path):
    if not shutil.which("pkg-config") or not shutil.which("c++"):
        pytest.skip("Linux native build tools are unavailable")
    dependencies = subprocess.run(
        ["pkg-config", "--cflags", "--libs", "portaudio-2.0", "samplerate"],
        capture_output=True, text=True,
    )
    if dependencies.returncode:
        pytest.skip("Install portaudio19-dev and libsamplerate0-dev for native tests")
    executable = tmp_path / "linux-audio-test"
    subprocess.run([
        "c++", "-std=c++20", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-pthread",
        "-I", str(ROOT / "native/audio/include"),
        str(ROOT / "tests/native/linux_audio.cpp"), *dependencies.stdout.split(),
        "-o", str(executable),
    ], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=20,
                   env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1"})
