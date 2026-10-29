#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Test the production extension in a private headless GNOME, never the user's shell."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    report = output / "gnome-capsule.json"
    report.unlink(missing_ok=True)
    (output / "learning-correction.json").unlink(missing_ok=True)
    (output / "keys.json").unlink(missing_ok=True)
    for file in output.glob("transport-*"):
        file.unlink()
    for binary in ("gnome-shell", "dbus-run-session", "gsettings"):
        if not shutil.which(binary):
            raise RuntimeError(f"Install {binary} for GNOME acceptance")
    with tempfile.TemporaryDirectory(prefix="vocal-more-gnome-test-") as temporary:
        home = Path(temporary)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        extensions = home / "data/gnome-shell/extensions"
        shutil.copytree(
            ROOT / "packaging/linux/gnome-extension/vocal-more@sm-yjr",
            extensions / "vocal-more@sm-yjr",
        )
        driver = extensions / "vocal-more-test@local"
        driver.mkdir()
        (driver / "metadata.json").write_text(
            json.dumps(
                {
                    "uuid": "vocal-more-test@local",
                    "name": "Isolated capsule test",
                    "description": "Disposable acceptance driver",
                    "shell-version": ["49", "50"],
                    "session-modes": ["user", "unlock-dialog"],
                }
            )
        )
        shutil.copyfile(
            ROOT / "scripts/fixtures/gnome_capsule_test.js", driver / "extension.js"
        )
        environment = os.environ.copy()
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_RUNTIME_DIR=str(runtime),
            GSETTINGS_BACKEND="keyfile",
            LIBGL_ALWAYS_SOFTWARE="1",
            GDK_BACKEND="wayland",
            GSK_RENDERER="cairo",
            GTK_A11Y="atspi",
            CARGO_HOME=str(Path(os.environ["HOME"]) / ".cargo"),
            RUSTUP_HOME=str(Path(os.environ["HOME"]) / ".rustup"),
            VOCAL_MORE_ATSPI_TEST_CONTROL=str(home / "control"),
            VOCAL_MORE_CAPSULE_ROOT=str(ROOT),
            VOCAL_MORE_CAPSULE_CARGO=shutil.which("cargo")
            or str(Path(os.environ["HOME"]) / ".cargo/bin/cargo"),
            VOCAL_MORE_GNOME_TEST_OUTPUT=str(output),
            VOCAL_MORE_ATSPI_TEST_KEYS=str(output / "keys.json"),
            VOCAL_MORE_ATSPI_TEST_REPORT=str(output / "learning-correction.json"),
        )
        for key in (
            "DISPLAY",
            "GNOME_SETUP_DISPLAY",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "DASHSCOPE_API_KEY",
        ):
            environment.pop(key, None)
        script = """set -e
gsettings set org.gnome.shell enabled-extensions "['vocal-more@sm-yjr', 'vocal-more-test@local']"
gsettings set org.gnome.desktop.interface enable-hot-corners false
gsettings set org.gnome.mutter dynamic-workspaces false
gsettings set org.gnome.desktop.wm.preferences num-workspaces 2
gsettings set org.gnome.desktop.interface enable-animations false
export WAYLAND_DISPLAY=vocal-more-test
gnome-shell --headless --wayland --no-x11 --wayland-display vocal-more-test --virtual-monitor 1280x960 --virtual-monitor 1024x768 &
for i in $(seq 1 150); do test -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" && break; sleep 0.1; done
/usr/bin/python3 "$VOCAL_MORE_CAPSULE_ROOT/scripts/fixtures/atspi_editor.py" &
"$VOCAL_MORE_CAPSULE_CARGO" test --locked --manifest-path "$VOCAL_MORE_CAPSULE_ROOT/rust/Cargo.toml" -p vocal-more-desktop --lib linux_real_shell_capsule_transport -- --ignored --nocapture
for i in $(seq 1 150); do test -f "$VOCAL_MORE_GNOME_TEST_OUTPUT/transport-atspi-ready" && break; sleep 0.1; done
"$VOCAL_MORE_CAPSULE_CARGO" test --locked --manifest-path "$VOCAL_MORE_CAPSULE_ROOT/rust/Cargo.toml" -p vocal-more-desktop --lib linux_native_wayland_insertion -- --ignored --nocapture
"$VOCAL_MORE_CAPSULE_CARGO" test --locked --manifest-path "$VOCAL_MORE_CAPSULE_ROOT/rust/Cargo.toml" -p vocal-more-desktop --lib linux_atspi_real_field_observation -- --ignored --nocapture
touch "$VOCAL_MORE_GNOME_TEST_OUTPUT/transport-atspi-done"
wait
"""
        with (output / "gnome-shell.log").open("w") as log:
            process = subprocess.Popen(
                ["dbus-run-session", "--", "bash", "-c", script],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 150
                while not report.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Headless GNOME exited; inspect gnome-shell.log"
                        )
                    time.sleep(0.1)
                if not report.exists():
                    raise TimeoutError(
                        "GNOME capsule acceptance timed out; inspect gnome-shell.log"
                    )
                result = json.loads(report.read_text())
                if not result.get("ok"):
                    raise RuntimeError(result)
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
                # Private bus-activated GVfs can briefly retain a FUSE mount.
                time.sleep(0.5)
                for mount in (runtime / "gvfs", runtime / "doc"):
                    if mount.is_mount():
                        subprocess.run(
                            ["fusermount3", "-u", str(mount)], check=True, timeout=5
                        )
    if "Source ID" in (output / "gnome-shell.log").read_text():
        raise RuntimeError("GNOME timer cleanup warning; inspect gnome-shell.log")
    print(
        "PASS: GNOME capsule real clicks, Rust transport, recovery and Wayland correction learning"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".build/linux-smoke")
    args = parser.parse_args()
    run(args.output.resolve())
