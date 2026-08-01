#!/usr/bin/env python3
"""Verify the signed native-audio contract inside a release DMG."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


NATIVE_LIBRARY_NAME = "libvocal_more_audio.dylib"
EXPECTED_ARCHITECTURE = "arm64"
EXPECTED_MIN_MACOS = "14.0"
EXPECTED_INSTALL_NAME = f"@rpath/{NATIVE_LIBRARY_NAME}"
REQUIRED_C_ABI_EXPORTS = (
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

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    command: Sequence[str | Path],
    *,
    command_runner: CommandRunner,
) -> str:
    normalized = [str(value) for value in command]
    result = command_runner(
        normalized,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(f"{' '.join(normalized)}: {details}")
    return result.stdout


def _otool_values(output: str) -> list[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines and lines[0].endswith(":"):
        lines = lines[1:]
    return [line.split(" (", 1)[0].strip() for line in lines]


def verify_native_audio_library(
    app: Path,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> None:
    """Validate the embedded dylib without trusting the build workspace."""
    app = Path(app)
    if not app.is_dir():
        raise RuntimeError(f"App bundle is missing: {app}")
    library = app / "Contents" / "Frameworks" / NATIVE_LIBRARY_NAME
    if not library.is_file():
        raise RuntimeError(f"Native audio library is missing: {library}")

    architectures = _run(
        ["lipo", "-archs", library],
        command_runner=command_runner,
    ).split()
    if architectures != [EXPECTED_ARCHITECTURE]:
        raise RuntimeError(
            "Native audio library must be arm64-only; observed: "
            f"{' '.join(architectures) or 'none'}"
        )

    build_version = _run(
        ["xcrun", "vtool", "-show-build", library],
        command_runner=command_runner,
    )
    if not re.search(r"^\s*platform\s+MACOS\s*$", build_version, re.MULTILINE):
        raise RuntimeError("Native audio library does not declare the macOS platform")
    if not re.search(
        rf"^\s*minos\s+{re.escape(EXPECTED_MIN_MACOS)}\s*$",
        build_version,
        re.MULTILINE,
    ):
        raise RuntimeError(
            f"Native audio library must declare macOS {EXPECTED_MIN_MACOS}"
        )

    install_names = _otool_values(
        _run(["otool", "-D", library], command_runner=command_runner)
    )
    if install_names != [EXPECTED_INSTALL_NAME]:
        raise RuntimeError(
            "Unexpected native audio install name: "
            f"{', '.join(install_names) or 'none'}"
        )

    dependencies = _otool_values(
        _run(["otool", "-L", library], command_runner=command_runner)
    )
    unexpected_dependencies = [
        dependency
        for dependency in dependencies
        if dependency != EXPECTED_INSTALL_NAME
        and not dependency.startswith("/System/Library/")
        and not dependency.startswith("/usr/lib/")
    ]
    if unexpected_dependencies:
        raise RuntimeError(
            "Native audio library has a non-Apple dependency: "
            + ", ".join(unexpected_dependencies)
        )

    symbols = _run(
        ["nm", "-gU", library],
        command_runner=command_runner,
    )
    exported_names = {
        line.rsplit(maxsplit=1)[-1].removeprefix("_")
        for line in symbols.splitlines()
        if line.split()
    }
    missing_exports = sorted(set(REQUIRED_C_ABI_EXPORTS) - exported_names)
    if missing_exports:
        raise RuntimeError(
            "Native audio library is missing C ABI exports: "
            + ", ".join(missing_exports)
        )

    _run(
        ["codesign", "--verify", "--strict", "--verbose=2", library],
        command_runner=command_runner,
    )


def verify_release_artifact(
    dmg: Path,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> None:
    """Mount the final DMG and verify the exact app that will be uploaded."""
    dmg = Path(dmg).resolve()
    if not dmg.is_file():
        raise RuntimeError(f"Release DMG is missing: {dmg}")

    _run(
        ["xcrun", "stapler", "validate", dmg],
        command_runner=command_runner,
    )
    _run(
        ["codesign", "--verify", "--verbose=2", dmg],
        command_runner=command_runner,
    )

    verification_root = Path(
        tempfile.mkdtemp(prefix="vocal-more-release-verification-")
    )
    mount_point = verification_root / "volume"
    mount_point.mkdir()
    attached = False
    try:
        _run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                mount_point,
                dmg,
            ],
            command_runner=command_runner,
        )
        attached = True
        app = mount_point / "Vocal More.app"
        verify_native_audio_library(app, command_runner=command_runner)
        _run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", app],
            command_runner=command_runner,
        )
    finally:
        active_error = sys.exc_info()[0] is not None
        detach_error: RuntimeError | None = None
        if attached:
            try:
                _run(
                    ["hdiutil", "detach", mount_point],
                    command_runner=command_runner,
                )
            except RuntimeError as exc:
                detach_error = exc
        if detach_error is None:
            if mount_point.exists():
                mount_point.rmdir()
            if verification_root.exists():
                verification_root.rmdir()
        elif not active_error:
            raise detach_error
        else:
            print(f"Warning: {detach_error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the notarized Vocal More DMG and embedded native audio dylib."
    )
    parser.add_argument("dmg", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_release_artifact(args.dmg)
    except RuntimeError as exc:
        parser.exit(1, f"Release artifact verification failed: {exc}\n")
    print(f"Verified release artifact: {args.dmg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
