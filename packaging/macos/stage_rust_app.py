#!/usr/bin/env python3
"""Stage the Rust desktop app using only build-time Python standard libraries."""
from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
from release.model import Version


def project_version() -> Version:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return Version.project(project["project"]["version"], project.get("tool", {}).get("vocal-more", {}).get("build"))


def app_info(release: Version, *, development: bool = False) -> dict:
    return {
        "CFBundleExecutable": "Vocal More",
        "CFBundleName": "Vocal More Dev" if development else "Vocal More",
        "CFBundleDisplayName": "Vocal More Dev" if development else "Vocal More",
        "CFBundleIdentifier": "com.sm-yjr.vocal-more.dev" if development else "com.sm-yjr.vocal-more",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "VocalMore.icns",
        "CFBundleShortVersionString": release.base,
        # Sparkle compares CFBundleVersion: the global build number.
        "CFBundleVersion": release.bundle_version,
        "VocalMoreVersion": release.text,
        "VocalMoreReleaseChannel": release.channel,
        "VocalMoreUIRuntime": "rust-gpui-kit",
        "SUFeedURL": release.feed_url,
        "SUPublicEDKey": "rX4Sp1huP0v763afpuPlVkpDuXYoMj/+2fNqnFFMHsk=",
        "SUVerifyUpdateBeforeExtraction": True,
        "SURequireSignedFeed": True,
        "NSHighResolutionCapable": True,
        "LSUIElement": True,
        "LSMinimumSystemVersion": "14.0",
        "NSPrincipalClass": "NSApplication",
        "NSMicrophoneUsageDescription": "Vocal More needs microphone access to convert your voice to text.",
        "NSAppleEventsUsageDescription": "Vocal More may use macOS automation to paste recognized text into the active app.",
    }


def stage_app(destination: Path, binary: Path, backend: Path, native: Path, *, development: bool = False) -> Path:
    destination = destination.absolute()
    if destination.suffix != ".app" or destination.is_symlink():
        raise ValueError("destination must be a real .app path")
    for source in (binary, backend, native, ROOT / "LICENSE", ROOT / "packaging/macos/VocalMore.icns"):
        if not source.is_file():
            raise FileNotFoundError(source)
    version = project_version()
    if destination.exists():
        shutil.rmtree(destination)
    contents = destination / "Contents"
    for name in ("MacOS", "Resources/rust-backend", "Frameworks"):
        (contents / name).mkdir(parents=True)
    shutil.copy2(binary, contents / "MacOS/Vocal More")
    shutil.copy2(backend, contents / "Resources/rust-backend/vocal-more-backend")
    shutil.copy2(native, contents / "Frameworks/libvocal_more_audio.dylib")
    for filename in ("MacOS/Vocal More", "Resources/rust-backend/vocal-more-backend"):
        (contents / filename).chmod(0o755)
    shutil.copy2(ROOT / "LICENSE", contents / "Resources/LICENSE.txt")
    shutil.copy2(ROOT / "packaging/macos/VocalMore.icns", contents / "Resources/VocalMore.icns")
    # Component design attribution is separate from the project's GPL license.
    shutil.copy2(ROOT / "resources/settings/SHADCN-UI-LICENSE.txt", contents / "Resources/Shadcn-UI-LICENSE.txt")
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(app_info(version, development=development), output)
    (contents / "PkgInfo").write_bytes(b"APPL????")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    print(stage_app(args.app, args.binary, args.backend, args.native, development=args.development))


if __name__ == "__main__":
    main()
