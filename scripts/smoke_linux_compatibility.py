#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Keyboard-only system portal and guarded Chromium input on an owned Wayland session."""

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


def run(output, scenario="default", xwayland=False):
    output.mkdir(parents=True, exist_ok=True)
    for name in (
        "phase",
        "session.json",
        "rust-compatibility.json",
        "focused",
        "focus-changed",
        "permission.png",
        "after-toggle.png",
        "buttons.json",
    ):
        (output / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocal-more-compat-") as temporary:
        home = Path(temporary)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        extension = home / "data/gnome-shell/extensions/vocal-more-compat-test@local"
        extension.mkdir(parents=True)
        (extension / "metadata.json").write_text(
            json.dumps(
                {
                    "uuid": "vocal-more-compat-test@local",
                    "name": "Private compatible input test",
                    "description": "Disposable test input",
                    "shell-version": ["49", "50"],
                }
            )
        )
        shutil.copyfile(
            ROOT / "scripts/fixtures/gnome_compat_test.js", extension / "extension.js"
        )
        shutil.copytree(ROOT / "packaging/linux/gnome-extension/vocal-more@sm-yjr", home / "data/gnome-shell/extensions/vocal-more@sm-yjr")
        apps = home / "data/applications"
        apps.mkdir()
        (apps / "io.github.sm_yjr.VocalMore.Test.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Vocal More input fixture\nExec=/usr/bin/true\nIcon=audio-input-microphone\n"
        )
        environment = os.environ.copy()
        environment.update(
            HOME=str(home),
            XDG_RUNTIME_DIR=str(runtime),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_CURRENT_DESKTOP="GNOME",
            XDG_SESSION_TYPE="wayland",
            GSETTINGS_BACKEND="keyfile",
            GDK_BACKEND="wayland",
            GTK_A11Y="atspi",
            GSK_RENDERER="cairo",
            LIBGL_ALWAYS_SOFTWARE="1",
            CARGO_HOME=str(Path.home() / ".cargo"),
            RUSTUP_HOME=str(Path.home() / ".rustup"),
            VOCAL_MORE_COMPAT_OUTPUT=str(output),
            VOCAL_MORE_COMPAT_SCENARIO=scenario,
            VOCAL_MORE_PRIVATE_GESTURE_TEST="1",
            VOCAL_MORE_INPUT_ACCEPTANCE="1",
            VOCAL_MORE_ATSPI_TEST_CONTROL=str(home / "control"),
            VOCAL_MORE_COMPAT_ROOT=str(ROOT),
            VOCAL_MORE_COMPAT_XWAYLAND="1" if xwayland else "0",
            VOCAL_MORE_COMPAT_CARGO=shutil.which("cargo")
            or str(Path.home() / ".cargo/bin/cargo"),
        )
        for key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "GNOME_SETUP_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "DASHSCOPE_API_KEY",
        ):
            environment.pop(key, None)
        script = """set -e
gsettings set org.gnome.shell enabled-extensions "['vocal-more@sm-yjr', 'vocal-more-compat-test@local']"
gsettings set org.gnome.desktop.interface toolkit-accessibility true
gsettings set org.gnome.desktop.interface enable-hot-corners false
gsettings set org.gnome.desktop.interface enable-animations false
export WAYLAND_DISPLAY=vocal-more-compat-test
if [ "$VOCAL_MORE_COMPAT_XWAYLAND" = 1 ]; then
  gnome-shell --headless --wayland --wayland-display vocal-more-compat-test --virtual-monitor 1280x960 &
  for i in $(seq 1 150); do test -f "$VOCAL_MORE_COMPAT_OUTPUT/display" && break; sleep 0.1; done
  export DISPLAY="$(cat "$VOCAL_MORE_COMPAT_OUTPUT/display")"
  export XAUTHORITY="$(cat "$VOCAL_MORE_COMPAT_OUTPUT/authority")"
else
  gnome-shell --headless --wayland --no-x11 --wayland-display vocal-more-compat-test --virtual-monitor 1280x960 &
fi
for i in $(seq 1 150); do test -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" && break; sleep 0.1; done
dbus-update-activation-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_RUNTIME_DIR XDG_CONFIG_HOME XDG_DATA_HOME HOME GTK_A11Y GDK_BACKEND
if [ "$VOCAL_MORE_COMPAT_SCENARIO" = terminal ]; then
/usr/bin/ghostty --gtk-single-instance=false --title="Vocal More Terminal Fixture" -e /usr/bin/python3 "$VOCAL_MORE_COMPAT_ROOT/scripts/fixtures/terminal_input.py" "$VOCAL_MORE_COMPAT_OUTPUT" &
/usr/bin/python3 "$VOCAL_MORE_COMPAT_ROOT/scripts/fixtures/atspi_editor.py" &
else
/usr/bin/google-chrome --user-data-dir="$HOME/chrome-profile" --no-first-run --no-default-browser-check --disable-extensions --disable-background-networking --disable-features=Translate --password-store=basic --force-renderer-accessibility --ozone-platform=wayland --app="file://$VOCAL_MORE_COMPAT_ROOT/scripts/fixtures/chromium_editor.html?scenario=$VOCAL_MORE_COMPAT_SCENARIO" &
fi
"$VOCAL_MORE_COMPAT_CARGO" test --locked --manifest-path "$VOCAL_MORE_COMPAT_ROOT/rust/Cargo.toml" -p vocal-more-desktop --lib linux_real_compatible_chromium -- --ignored --nocapture

wait
"""
        with (output / "gnome.log").open("w") as log:
            process = subprocess.Popen(
                ["dbus-run-session", "--", "bash", "-c", script],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 130
                report = output / "session.json"
                while not report.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "private portal session failed; inspect gnome.log"
                        )
                    time.sleep(0.1)
                if not report.exists():
                    raise TimeoutError(
                        "private gesture test timed out; inspect gnome.log"
                    )
                result = json.loads(report.read_text())
                assert result.get("ok"), result
                assert (output / "rust-compatibility.json").exists()
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
                time.sleep(0.3)
                for _ in range(20):
                    mounts = [
                        line.split()[4]
                        for line in Path("/proc/self/mountinfo")
                        .read_text()
                        .splitlines()
                        if line.split()[4].startswith(str(runtime) + "/")
                    ]
                    if not mounts:
                        break
                    for mount in mounts:
                        subprocess.run(
                            ["fusermount3", "-u", mount],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    time.sleep(0.1)
    print(f"PASS: keyboard-only portal consent, {scenario} input, clipboard restoration and focus rejection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build/linux-compatibility-acceptance"
    )
    parser.add_argument(
        "--scenario",
        choices=["default", "long", "multiline", "rich", "image", "mixed", "terminal"],
        default="default",
    )
    parser.add_argument("--xwayland", action="store_true")
    args = parser.parse_args()
    run(args.output.resolve(), args.scenario, args.xwayland)
