#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Real GTK/AT-SPI acceptance on an isolated display and session bus."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def session():
    environment = os.environ.copy()
    with (
        tempfile.TemporaryFile() as display,
        tempfile.TemporaryDirectory(prefix="vocal-more-atspi-") as temporary,
    ):
        home = Path(temporary)
        (home / "runtime").mkdir(mode=0o700)
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_RUNTIME_DIR=str(home / "runtime"),
            GSETTINGS_BACKEND="memory",
            GDK_BACKEND="x11",
            GTK_A11Y="atspi",
            GTK_USE_PORTAL="0",
            XDG_SESSION_TYPE="x11",
            VOCAL_MORE_ATSPI_TEST_CONTROL=str(home / "control"),
        )
        for key in ("WAYLAND_DISPLAY", "GNOME_SETUP_DISPLAY", "DASHSCOPE_API_KEY"):
            environment.pop(key, None)
        environment["CARGO_HOME"] = str(Path(os.environ["HOME"]) / ".cargo")
        environment["RUSTUP_HOME"] = str(Path(os.environ["HOME"]) / ".rustup")
        xvfb = subprocess.Popen(
            [
                "Xvfb",
                "-displayfd",
                str(display.fileno()),
                "-screen",
                "0",
                "800x600x24",
                "-nolisten",
                "tcp",
            ],
            pass_fds=(display.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        editor = None
        try:
            for _ in range(100):
                display.seek(0)
                number = display.read().decode().strip()
                if number:
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("Xvfb did not start")
            environment["DISPLAY"] = f":{number}"
            subprocess.run(
                [
                    "dbus-update-activation-environment",
                    "DISPLAY",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_RUNTIME_DIR",
                    "HOME",
                    "GTK_A11Y",
                ],
                env=environment,
                check=True,
            )
            editor = subprocess.Popen(
                ["/usr/bin/python3", str(ROOT / "scripts/fixtures/atspi_editor.py")],
                env=environment,
            )
            time.sleep(1)
            subprocess.run(
                [
                    "xdotool",
                    "search",
                    "--name",
                    "Vocal More AT-SPI Fixture",
                    "windowfocus",
                ],
                env=environment,
                check=True,
                timeout=5,
            )
            cargo = shutil.which("cargo") or str(
                Path(os.environ["HOME"]) / ".cargo/bin/cargo"
            )
            subprocess.run(
                [
                    cargo,
                    "test",
                    "--locked",
                    "--manifest-path",
                    str(ROOT / "rust/Cargo.toml"),
                    "-p",
                    "vocal-more-desktop",
                    "--lib",
                    os.environ.get("VOCAL_MORE_ATSPI_TEST_NAME", "linux_atspi_real_field_observation"),
                    "--",
                    "--ignored",
                    "--nocapture",
                ],
                env=environment,
                check=True,
                timeout=90,
            )
        finally:
            if editor:
                editor.terminate()
                editor.wait(timeout=5)
            xvfb.terminate()
            xvfb.wait(timeout=5)
            for mount in (home / "runtime/doc", home / "runtime/gvfs"):
                if mount.is_mount():
                    subprocess.run(
                        ["fusermount3", "-u", str(mount)], check=True, timeout=5
                    )


if __name__ == "__main__":
    if "--session" in sys.argv:
        session()
    else:
        subprocess.run(
            [
                "dbus-run-session",
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                "--session",
            ],
            check=True,
        )
