#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build an APT-managed package from the verified Linux desktop bundle."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
MAINTAINER = "Vocal More contributors <76152255+sm-yjr@users.noreply.github.com>"


def debian_version(project: dict) -> str:
    """Debian '~' places a beta before its matching stable; build is revision."""
    spec = importlib.util.spec_from_file_location(
        "vocal_more_release_model", ROOT / "packaging/release/model.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    version = module.Version.project(
        project["project"]["version"], project["tool"]["vocal-more"]["build"]
    )
    upstream = version.base + (f"~beta.{version.number}" if version.stage else "")
    return f"{upstream}-{version.build}"


def build(bundle: Path, output: Path) -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = debian_version(project)
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if arch not in {"amd64", "arm64"}:
        raise ValueError(f"unsupported Linux desktop architecture: {arch}")
    for name in (
        "bin/vocal-more-desktop",
        "lib/vocal-more/libvocal_more_audio.so",
        "vocal-more",
        "LICENSE.txt",
        "source.tar.gz",
        "build-manifest.json",
    ):
        if not (bundle / name).is_file():
            raise ValueError(f"missing bundle file: {name}")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"vocal-more_{version}_{arch}.deb"
    with tempfile.TemporaryDirectory(prefix="vocal-more-deb-") as temporary:
        stage = Path(temporary) / "root"
        private = stage / "usr/lib/vocal-more"
        private.mkdir(parents=True)
        for name in ("bin", "lib"):
            shutil.copytree(bundle / name, private / name)
        shutil.copy2(bundle / "vocal-more", private / "vocal-more")
        (stage / "usr/bin").mkdir(parents=True)
        (stage / "usr/bin/vocal-more").symlink_to("../lib/vocal-more/vocal-more")
        doc = stage / "usr/share/doc/vocal-more"
        doc.mkdir(parents=True)
        for name in ("source.tar.gz", "build-manifest.json", "README.md"):
            shutil.copy2(bundle / name, doc / name)
        shutil.copytree(bundle / "notices", doc / "notices")
        shutil.copy2(bundle / "LICENSE.txt", doc / "copyright")
        applications = stage / "usr/share/applications"
        applications.mkdir(parents=True)
        (applications / "io.github.sm_yjr.VocalMore.desktop").write_text(
            "[Desktop Entry]\nType=Application\nVersion=1.0\nName=Vocal More\n"
            "Comment=Low-voice dictation\nExec=/usr/bin/vocal-more\n"
            "Icon=io.github.sm_yjr.VocalMore\nTerminal=false\nCategories=AudioVideo;Audio;\n"
            "StartupWMClass=io.github.sm_yjr.VocalMore\n"
        )
        extension = bundle / "gnome-extension/vocal-more@sm-yjr"
        if extension.is_dir():
            shutil.copytree(
                extension, stage / "usr/share/gnome-shell/extensions/vocal-more@sm-yjr"
            )
        icons = stage / "usr/share/icons/hicolor/scalable/apps"
        icons.mkdir(parents=True)
        shutil.copy2(bundle / "io.github.sm_yjr.VocalMore.svg", icons)
        # Derive ELF ABI dependencies on the builder, including the dlopened audio
        # library. Explicit dependencies cover GPUI's dlopen and desktop tools.
        debian = Path(temporary) / "debian"
        debian.mkdir()
        (debian / "control").write_text(
            f"Source: vocal-more\nSection: sound\nPriority: optional\nMaintainer: {MAINTAINER}\n\nPackage: vocal-more\nArchitecture: any\nDescription: Low-voice dictation\n"
        )
        result = subprocess.check_output(
            [
                "dpkg-shlibdeps",
                "-O",
                f"-e{private / 'bin/vocal-more-desktop'}",
                f"-e{private / 'lib/vocal-more/libvocal_more_audio.so'}",
            ],
            cwd=temporary,
            text=True,
        )
        depends = next(
            line.removeprefix("shlibs:Depends=")
            for line in result.splitlines()
            if line.startswith("shlibs:Depends=")
        )
        depends += ", libfontconfig1, libfreetype6, libwayland-client0, libwayland-cursor0, libwayland-egl1, libegl1, libvulkan1, xdg-utils, wl-clipboard, xclip, xdotool, ffmpeg, pipewire-bin | pulseaudio-utils, xdg-desktop-portal, at-spi2-core, gstreamer1.0-pipewire, gstreamer1.0-plugins-good, gstreamer1.0-plugins-base"
        control = stage / "DEBIAN"
        control.mkdir()
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024
        (control / "control").write_text(
            f"Package: vocal-more\nVersion: {version}\nArchitecture: {arch}\nSection: sound\n"
            f"Priority: optional\nMaintainer: {MAINTAINER}\nInstalled-Size: {size}\nDepends: {depends}\n"
            "Homepage: https://github.com/sm-yjr/vocal-more\n"
            "Description: Low-voice dictation with a native Rust desktop\n"
            " Development Linux port with GPUI settings, recognition, polish and history.\n"
            " Updates are managed by APT; no application-managed privileged updater.\n"
        )
        # No maintainer scripts, login services, apt source edits or user-data writes.
        subprocess.run(
            [
                "dpkg-deb",
                "--root-owner-group",
                "--build",
                "-Zxz",
                str(stage),
                str(destination),
            ],
            check=True,
        )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="extracted Linux bundle directory")
    parser.add_argument("--output", type=Path, default=ROOT / ".build/linux")
    args = parser.parse_args()
    print(build(args.bundle.resolve(), args.output.resolve()))


if __name__ == "__main__":
    main()
