#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Actual GNOME portal + private PipeWire + production screen worker; no cloud."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def session(output):
    env = os.environ.copy()
    processes = []

    def start(command, name):
        log = (output / name).open("w")
        logs.append(log)
        p = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(p)
        return p

    logs = []
    try:
        start(["pipewire"], "pipewire.log")
        start(["wireplumber"], "wireplumber.log")
        start(
            [
                "gnome-shell",
                "--headless",
                "--wayland",
                "--no-x11",
                "--wayland-display",
                "vocal-more-screen-test",
                "--virtual-monitor",
                "1280x960",
            ],
            "shell.log",
        )
        deadline = time.monotonic() + 15
        while not (output / "shell-ready").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not (output / "shell-ready").exists():
            raise TimeoutError("private GNOME did not become ready")
        subprocess.run(
            [
                "dbus-update-activation-environment",
                "WAYLAND_DISPLAY",
                "XDG_CURRENT_DESKTOP",
                "XDG_SESSION_TYPE",
                "XDG_RUNTIME_DIR",
                "XDG_DATA_HOME",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "HOME",
                "GSETTINGS_BACKEND",
                "GTK_A11Y",
                "GDK_BACKEND",
            ],
            env=env,
            check=True,
        )
        start(
            ["/usr/bin/python3", str(ROOT / "scripts/fixtures/screen_editor.py")],
            "editor.log",
        )
        time.sleep(1)
        cargo = env["VOCAL_MORE_SCREEN_CARGO"]
        test = start(
            [
                cargo,
                "test",
                "--locked",
                "--manifest-path",
                str(ROOT / "rust/Cargo.toml"),
                "-p",
                "vocal-more-desktop",
                "--lib",
                "linux_real_portal_captures_selected_window",
                "--",
                "--ignored",
                "--nocapture",
            ],
            "screen-test.log",
        )
        deadline = time.monotonic() + 60
        clicked = False
        cancelled = False
        while test.poll() is None and time.monotonic() < deadline:
            windows = (
                json.loads((output / "shell-windows.json").read_text())
                if (output / "shell-windows.json").exists()
                else []
            )
            if not clicked and any(w["title"] == "Share Screen" for w in windows):
                (output / "clicks.json").write_text(
                    json.dumps([[585, 383], [640, 465], [926, 269]])
                )
                clicked = True
            if (
                not cancelled
                and (output / "cancel-request").exists()
                and any(w["title"] == "Share Screen" for w in windows)
            ):
                (output / "clicks.json").write_text(json.dumps([[356, 269]]))
                cancelled = True
            time.sleep(0.1)
        test.wait(timeout=5)
        assert test.returncode == 0, (
            "capture acceptance failed; inspect screen-test.log and chooser.png"
        )
        assert clicked and cancelled, (
            "portal consent/denial controls were not activated"
        )
        deadline = time.monotonic() + 3
        while True:
            nodes = json.loads(
                subprocess.check_output(["pw-dump"], env=env, text=True, timeout=3)
            )
            sources = [
                node
                for node in nodes
                if node.get("info", {}).get("props", {}).get("media.class")
                == "Video/Source"
            ]
            if not sources:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("ScreenCast PipeWire node survived shutdown")
            time.sleep(0.1)
        report = json.loads((output / "screen-capture.json").read_text())
        report["stream_cleanup"] = True
        (output / "screen-capture.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
        for p in reversed(processes):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for log in logs:
            log.close()
    print(
        "PASS: real portal/PipeWire, crop/resize, local recognition transport, denial and stream cleanup; cloud not tested"
    )


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    for p in output.glob("frame-*.jpg"):
        p.unlink()
    for name in [
        "shell-ready",
        "screen-capture.json",
        "flip",
        "cancel-request",
        "clicks.json",
        "shell-windows.json",
    ]:
        (output / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocal-more-screen-") as temporary:
        home = Path(temporary)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_RUNTIME_DIR=str(runtime),
            WAYLAND_DISPLAY="vocal-more-screen-test",
            XDG_CURRENT_DESKTOP="GNOME",
            XDG_SESSION_TYPE="wayland",
            GSK_RENDERER="cairo",
            GDK_BACKEND="wayland",
            GTK_A11Y="atspi",
            GSETTINGS_BACKEND="keyfile",
            LIBGL_ALWAYS_SOFTWARE="1",
            VOCAL_MORE_PRIVATE_SCREEN_TEST="1",
            VOCAL_MORE_SCREEN_TEST_OUTPUT=str(output),
            VOCAL_MORE_SCREEN_CARGO=shutil.which("cargo")
            or str(Path(os.environ["HOME"]) / ".cargo/bin/cargo"),
            CARGO_HOME=os.environ.get(
                "CARGO_HOME", str(Path(os.environ["HOME"]) / ".cargo")
            ),
            RUSTUP_HOME=os.environ.get(
                "RUSTUP_HOME", str(Path(os.environ["HOME"]) / ".rustup")
            ),
        )
        for key in [
            "DISPLAY",
            "GNOME_SETUP_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "DASHSCOPE_API_KEY",
        ]:
            env.pop(key, None)
        wp = home / "config/wireplumber/wireplumber.conf.d"
        wp.mkdir(parents=True)
        (wp / "90-test.conf").write_text(
            "wireplumber.profiles = { main = { monitor.alsa = disabled monitor.bluez = disabled monitor.libcamera = disabled } }\n"
        )
        apps = home / "data/applications"
        apps.mkdir(parents=True)
        (apps / "org.example.VocalMoreScreenFixture.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Vocal More Screen Fixture\nExec=/usr/bin/true\n"
        )
        extension = home / "data/gnome-shell/extensions/vocal-more-screen-test@local"
        extension.mkdir(parents=True)
        (extension / "metadata.json").write_text(
            json.dumps(
                {
                    "uuid": "vocal-more-screen-test@local",
                    "name": "Private Screen Test",
                    "description": "Isolated test helper",
                    "shell-version": ["49", "50"],
                }
            )
        )
        shutil.copyfile(
            ROOT / "scripts/fixtures/gnome_screen_test.js", extension / "extension.js"
        )
        subprocess.run(
            [
                "gsettings",
                "set",
                "org.gnome.desktop.interface",
                "enable-hot-corners",
                "false",
            ],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                "gsettings",
                "set",
                "org.gnome.desktop.interface",
                "enable-animations",
                "false",
            ],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                "gsettings",
                "set",
                "org.gnome.shell",
                "enabled-extensions",
                "['vocal-more-screen-test@local']",
            ],
            env=env,
            check=True,
        )
        p = subprocess.Popen(
            [
                "dbus-run-session",
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                "--session",
                "--output",
                str(output),
            ],
            env=env,
            start_new_session=True,
        )
        try:
            p.wait(timeout=100)
            assert p.returncode == 0, "private screen acceptance failed"
        finally:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if p.poll() is None:
                p.wait(timeout=10)
            time.sleep(0.5)
            for mount in [runtime / "doc", runtime / "gvfs"]:
                if mount.is_mount():
                    subprocess.run(
                        ["fusermount3", "-u", str(mount)], check=True, timeout=5
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build/linux-screen-live"
    )
    parser.add_argument("--session", action="store_true")
    args = parser.parse_args()
    if args.session:
        session(args.output.resolve())
    else:
        run(args.output.resolve())
