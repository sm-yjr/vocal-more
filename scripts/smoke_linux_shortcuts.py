#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Private GTK/GSettings and production GPUI shortcut acceptance; no cloud/audio."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def session(output):
    from gi.repository import Gio

    env = os.environ.copy()
    processes = []
    logs = []
    try:
        with tempfile.TemporaryFile() as display:
            xvfb = subprocess.Popen(
                [
                    "Xvfb",
                    "-displayfd",
                    str(display.fileno()),
                    "-screen",
                    "0",
                    "1280x960x24",
                    "-nolisten",
                    "tcp",
                ],
                pass_fds=(display.fileno(),),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(xvfb)
            for _ in range(100):
                display.seek(0)
                number = display.read().decode().strip()
                if number:
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("Xvfb did not start")
        env["DISPLAY"] = f":{number}"
        subprocess.run(
            [
                "dbus-update-activation-environment",
                "DISPLAY",
                "XDG_RUNTIME_DIR",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "HOME",
                "GSETTINGS_BACKEND",
            ],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                env["VOCAL_MORE_TEST_CARGO"],
                "test",
                "--locked",
                "--manifest-path",
                str(ROOT / "rust/Cargo.toml"),
                "-p",
                "vocal-more-desktop",
                "--lib",
                "linux_real_gsettings_shortcuts",
                "--",
                "--ignored",
                "--nocapture",
            ],
            env=env,
            check=True,
            timeout=60,
        )
        parent = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
        base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
        child_schema = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
        executable = str(ROOT / "rust/target/debug/vocal-more-desktop")
        own = base + "vocal-more-toggle/"
        foreign = base + "existing-editor/"
        for path, name, command, binding in [
            (own, "Vocal More", executable + " --command toggle", "F1"),
            (foreign, "Existing editor", "/usr/bin/true", "F3"),
        ]:
            child = Gio.Settings.new_with_path(child_schema, path)
            for key, value in [
                ("name", name),
                ("command", command),
                ("binding", binding),
            ]:
                child.set_string(key, value)
        parent.set_strv("custom-keybindings", [own, foreign])
        Gio.Settings.sync()
        profile = Path(env["XDG_DATA_HOME"]) / "vocal-more"
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text(
            "ui:\n  onboarding_completed: true\n  advanced_settings: true\n  language: zh\n"
        )
        launcher = Path(env["XDG_DATA_HOME"]) / "applications/chatgpt.desktop"
        launcher.parent.mkdir(parents=True)
        original_launcher = "[Desktop Entry]\nType=Application\nName=Owned Codex fixture\nExec=chatgpt --profile owned %U\n[Desktop Action New]\nExec=chatgpt --new-window\n"
        launcher.write_text(original_launcher)
        log = (output / "desktop.log").open("w")
        logs.append(log)
        app = subprocess.Popen(
            [
                executable,
                "--data-dir",
                str(profile),
                "--no-import",
                "--no-hotkeys",
                "--show-settings",
                "--quit-after-ms",
                "240000",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(app)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "^Vocal More$"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                window = result.stdout.splitlines()[-1]
                break
            assert app.poll() is None, "GPUI exited during launch"
            time.sleep(0.2)
        else:
            result = subprocess.run(
                ["xwininfo", "-root", "-tree"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            print(result.stdout, flush=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "x11grab",
                    "-video_size",
                    "1280x960",
                    "-i",
                    env["DISPLAY"],
                    "-frames:v",
                    "1",
                    str(output / "missing-settings.png"),
                ],
                env=env,
                check=True,
            )
            raise TimeoutError("Settings window did not appear")
        subprocess.run(["xdotool", "windowfocus", window], env=env, check=True)

        def bindings():
            code = """import json
from gi.repository import Gio
parent=Gio.Settings.new('org.gnome.settings-daemon.plugins.media-keys')
items=[]
for path in parent.get_strv('custom-keybindings'):
 child=Gio.Settings.new_with_path('org.gnome.settings-daemon.plugins.media-keys.custom-keybinding',path)
 items.append({'path':path,'binding':child.get_string('binding'),'command':child.get_string('command')})
print(json.dumps(items))
"""
            return json.loads(
                subprocess.check_output(
                    ["/usr/bin/python3", "-c", code], env=env, text=True
                )
            )

        def step(name, *, click=None, keys=None, control=None):
            if click:
                subprocess.run(
                    ["xdotool", "mousemove", *map(str, click), "click", "1"],
                    env=env,
                    check=True,
                )
            if keys:
                # XTest keycodes avoid xdotool's keysym synthesis adding Alt to
                # function keys on this Xvfb keymap. No real session receives IO.
                from Xlib import XK, X
                from Xlib import display as xdisplay
                from Xlib.ext import xtest

                connection = xdisplay.Display(env["DISPLAY"])
                try:
                    codes = [
                        connection.keysym_to_keycode(XK.string_to_keysym(key))
                        for key in keys
                    ]
                    assert all(codes)
                    for code in codes:
                        xtest.fake_input(connection, X.KeyPress, code)
                    connection.sync()
                    time.sleep(0.05)
                    for code in reversed(codes):
                        xtest.fake_input(connection, X.KeyRelease, code)
                    connection.sync()
                finally:
                    connection.close()
            if control:
                subprocess.run(
                    [executable, "--data-dir", str(profile), "--command", control],
                    env=env,
                    check=True,
                )
            time.sleep(0.6)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "x11grab",
                    "-video_size",
                    "1280x960",
                    "-i",
                    env["DISPLAY"],
                    "-frames:v",
                    "1",
                    str(output / (name + ".png")),
                ],
                env=env,
                check=True,
                timeout=10,
            )
            result = bindings()
            (output / "bindings.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2)
            )
            return result

        # Known geometry belongs to this explicitly controlled 1280x960 display
        # and the production 900x740 settings window, never the user's desktop.
        time.sleep(0.5)
        initial = step("shortcuts", click=(260, 334))
        assert [item["binding"] for item in initial] == ["F1", "F3"]
        assert "shortcut:vocal-more-toggle" in initial[0]["command"]
        step("capture-f2", click=(450, 395))
        added = step("added-f2", keys=["F2"])
        assert [item["binding"] for item in added] == ["F1", "F3", "F2"]
        key_path = added[2]["path"]
        step("edit-f2", click=(982, 333))
        edited = step("edited-combination", keys=["Control_L", "Alt_L", "r"])
        assert (
            edited[2]["path"] == key_path and edited[2]["binding"] == "<Control><Alt>r"
        )
        step("edit-conflict", click=(982, 333))
        conflict = step("conflict-preserves-binding", keys=["F3"])
        assert conflict == edited
        step("capture-escape", click=(450, 433))
        assert step("escape-cancelled", keys=["Escape"]) == edited
        step("capture-existing-f1", click=(450, 433))
        assert (
            step("global-trigger-captured", control="shortcut:vocal-more-toggle")
            == edited
        )
        removed = step("removed", click=(1034, 333))
        assert [item["binding"] for item in removed] == ["F1", "F3"]
        assert removed[1]["command"] == "/usr/bin/true"
        step("capture-before-switch", click=(450, 395))
        step("switched-tab", click=(260, 198))
        assert step("capture-cancelled-on-switch", keys=["F6"]) == removed
        step("general-input", click=(260, 198))
        step("codex-configured", click=(735, 776))
        assert launcher.read_text() == original_launcher.replace(
            "Exec=chatgpt --profile", "Exec=chatgpt --force-renderer-accessibility --profile", 1
        )
        assert (profile / "codex-launcher-before-accessibility.desktop").read_text() == original_launcher
        subprocess.run(
            [executable, "--data-dir", str(profile), "--command", "quit"],
            env=env,
            check=True,
        )
        app.wait(timeout=10)
        assert app.returncode == 0
        assert not list((profile / "recordings").rglob("*.wav")), (
            "shortcut capture created audio"
        )
        assert bindings() == removed
        (output / "passed.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "real_ui": True,
                    "real_keyboard": True,
                    "gsettings": True,
                    "cloud_tested": False,
                    "checks": [
                        "adopt-existing-F1",
                        "add",
                        "edit-combination",
                        "conflict-preserves-original",
                        "escape",
                        "bound-trigger-capture",
                        "remove-owned-only",
                        "cancel-on-tab-change",
                        "no-audio-recorded",
                        "durable-settings",
                        "codex-compatibility-UI-and-launcher-backup",
                    ],
                },
                indent=2,
            )
        )
        print(
            "PASS: production shortcut UI, keyboard capture, conflicts, persistence and capture protection"
        )
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        for log in logs:
            log.close()


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("*.png"):
        old.unlink()
    for name in ["ready", "action.json", "ack", "passed.json"]:
        (output / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocal-more-shortcuts-") as temporary:
        home = Path(temporary)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        env = os.environ.copy()
        env.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_RUNTIME_DIR=str(runtime),
            GSETTINGS_BACKEND="keyfile",
            XDG_CURRENT_DESKTOP="GNOME",
            XDG_SESSION_TYPE="x11",
            GPUI_BACKEND="X11",
            VOCAL_MORE_PRIVATE_SHORTCUT_TEST="1",
            VOCAL_MORE_TEST_CARGO=shutil.which("cargo")
            or str(Path.home() / ".cargo/bin/cargo"),
            CARGO_HOME=str(Path.home() / ".cargo"),
            RUSTUP_HOME=str(Path.home() / ".rustup"),
        )
        for key in [
            "DISPLAY",
            "GNOME_SETUP_DISPLAY",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "DASHSCOPE_API_KEY",
        ]:
            env.pop(key, None)
        subprocess.run(
            [
                "dbus-run-session",
                "--",
                "/usr/bin/python3",
                str(Path(__file__).resolve()),
                "--session",
                "--output",
                str(output),
            ],
            env=env,
            check=True,
            timeout=240,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build/linux-shortcuts-acceptance"
    )
    parser.add_argument("--session", action="store_true")
    args = parser.parse_args()
    (session if args.session else run)(args.output.resolve())
