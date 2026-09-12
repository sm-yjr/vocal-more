#!/usr/bin/env python3
"""Verify the signed native-audio contract inside a release DMG."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

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


def verify_rust_backend(app: Path, *, command_runner: CommandRunner = subprocess.run) -> None:
    """Require the real bundled application service and the product version."""
    binary = app / "Contents/Resources/rust-backend/vocal-more-backend"
    if not binary.is_file():
        raise RuntimeError(f"Rust application backend is missing: {binary}")
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
        version = info.get("VocalMoreVersion", info["CFBundleShortVersionString"])
    actual = _run([binary, "--version"], command_runner=command_runner).strip()
    if actual != f"Vocal More Rust backend {version}":
        raise RuntimeError(f"Rust application version does not match the app: {actual}")
    if _run(["lipo", "-archs", binary], command_runner=command_runner).split() != [EXPECTED_ARCHITECTURE]:
        raise RuntimeError("Rust application backend must be arm64-only")
    dependencies = _otool_values(_run(["otool", "-L", binary], command_runner=command_runner))
    if any(not item.startswith(("/System/Library/", "/usr/lib/")) for item in dependencies):
        raise RuntimeError("Rust application backend has a non-Apple runtime dependency")


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
) -> dict:
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
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from release.model import Version
        version = Version.parse(info.get("VocalMoreVersion", info["CFBundleShortVersionString"]))
        if (info["CFBundleShortVersionString"] != version.base
                or info["CFBundleVersion"] != version.text
                or info.get("SUFeedURL") != version.feed_url
                or info.get("VocalMoreReleaseChannel", "stable") != version.channel):
            raise RuntimeError("App version or release channel metadata mismatch")
        license_bytes = (Path(__file__).resolve().parents[2] / "LICENSE").read_bytes()
        for license_path in (mount_point / "LICENSE.txt", app / "Contents/Resources/LICENSE.txt"):
            if license_path.read_bytes() != license_bytes:
                raise RuntimeError(f"Missing or incorrect project license: {license_path}")
        verify_native_audio_library(app, command_runner=command_runner)
        verify_rust_backend(app, command_runner=command_runner)
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

    return {
        "status": "passed", "version": version.text, "channel": version.channel,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "dmg_sha256": hashlib.sha256(dmg.read_bytes()).hexdigest(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the notarized Vocal More DMG and embedded native audio dylib."
    )
    parser.add_argument("dmg", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--notary-result", type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_release_artifact(args.dmg)
        if args.notary_result:
            notary = json.loads(args.notary_result.read_text())
            if notary.get("id") or notary.get("status") != "Accepted":
                raise RuntimeError("Missing accepted notarization result")
            report["notarization"] = {"id": notary["id"], "status": notary["status"]}
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
    except RuntimeError as exc:
        parser.exit(1, f"Release artifact verification failed: {exc}\n")
    print(f"Verified release artifact: {args.dmg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
