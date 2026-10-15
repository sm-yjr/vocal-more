#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage an isolated, passive Bluetooth acceptance candidate; never launch it."""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
from pathlib import Path

from stage_rust_app import stage_app

ROOT = Path(__file__).resolve().parents[2]


def code_sections(path: Path) -> dict[str, str]:
    """Freeze code/data proof before stripping; build caches may change later."""
    data = path.read_bytes()
    magic, _, _, _, count, _, _, _ = struct.unpack_from("<8I", data)
    if magic != 0xFEEDFACF:
        raise ValueError("candidate requires thin 64-bit Mach-O components")
    offset = 32
    result = {}
    for _ in range(count):
        command, length = struct.unpack_from("<II", data, offset)
        if command == 0x19:
            segment = data[offset + 8:offset + 24].split(b"\0")[0].decode()
            section_count = struct.unpack_from("<I", data, offset + 64)[0]
            for index in range(section_count):
                section = struct.unpack_from("<16s16sQQIIIIIIII", data, offset + 72 + index * 80)
                size, start = section[3], section[4]
                if start and size and segment != "__DWARF":
                    name = section[0].split(b"\0")[0].decode()
                    result[f"{segment},{name}"] = hashlib.sha256(data[start:start + size]).hexdigest()
        offset += length
    return result


def clear_signing_metadata(app: Path) -> None:
    for path in (app, *app.rglob("*")):
        for attribute in subprocess.check_output(["xattr", str(path)], text=True).splitlines():
            if attribute in {"com.apple.FinderInfo", "com.apple.ResourceFork"}:
                subprocess.run(["xattr", "-d", attribute, str(path)], check=True)


def package(output: Path, binary: Path, backend: Path, native: Path, notices: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    app = output / "Vocal More Bluetooth Candidate.app"
    if app.exists():
        raise FileExistsError("Use a fresh candidate directory; existing candidates are retained")
    stage_app(app, binary, backend, native, development=True)
    contents = app / "Contents"
    info_path = contents / "Info.plist"
    with info_path.open("rb") as source:
        info = plistlib.load(source)
    info.update(
        CFBundleIdentifier="com.sm-yjr.vocal-more.dev.bluetooth-candidate",
        CFBundleName="Vocal More Bluetooth Candidate",
        CFBundleDisplayName="Vocal More Bluetooth Candidate",
        VocalMoreCandidate="bluetooth-product-candidate-1",
    )
    for key in tuple(info):
        if key.startswith("SU"):
            del info[key]
    with info_path.open("wb") as target:
        plistlib.dump(info, target)
    (contents / "Resources/BluetoothProductCandidate").write_text(
        "Passive by default: isolated profile, no credential import, audio or hotkeys.\n"
    )
    shutil.copyfile(notices, contents / "Resources/Rust-THIRD-PARTY-NOTICES.txt")
    shutil.copyfile(ROOT / "docs/bluetooth-productization.md", output / "README.md")
    clear_signing_metadata(app)
    section_proof = {}
    for code in (
        contents / "MacOS/Vocal More",
        contents / "Resources/rust-backend/vocal-more-backend",
        contents / "Frameworks/libvocal_more_audio.dylib",
    ):
        # Strip copied debug metadata only. Never mutate the build outputs.
        before = code_sections(code)
        subprocess.run(["strip", "-S", "-x", str(code)], check=True)
        if code_sections(code) != before:
            raise ValueError("stripping changed non-debug code or data")
        section_proof[str(code.relative_to(app))] = before
        clear_signing_metadata(app)
        subprocess.run(["codesign", "--force", "--sign", "-", str(code)], check=True)
    clear_signing_metadata(app)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    manifest = {
        "bundle_id": info["CFBundleIdentifier"],
        "version": info["VocalMoreVersion"],
        "candidate": info["VocalMoreCandidate"],
        "signing": "ad-hoc; no signing credentials; not notarized",
        "default_audio": False,
        "default_hotkeys": False,
        "default_current_app_input": False,
        "credential_import": False,
        "normal_launch_expiry_ms": 900000,
        "launched": False,
        "non_debug_sections_before_and_after_strip": section_proof,
        "files": {
            str(path.relative_to(app)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(app.rglob("*"))
            if path.is_file()
        },
    }
    (output / "candidate-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    for name in ("binary", "backend", "native", "notices"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    print(package(args.output_dir, args.binary, args.backend, args.native, args.notices))


if __name__ == "__main__":
    main()
