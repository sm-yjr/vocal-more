#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Production background/tray/settings/popup acceptance, on private Xvfb and DBus."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, description, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError(description)


def run(binary: Path, output: Path):
    from gi.repository import Gio, GLib
    from Xlib import X, protocol
    from Xlib import display as xdisplay

    output.mkdir(parents=True, exist_ok=True)
    (output / "background.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocal-more-background-") as temporary:
        home = Path(temporary)
        environment = os.environ.copy()
        for key in ("WAYLAND_DISPLAY", "GNOME_SETUP_DISPLAY", "DASHSCOPE_API_KEY"):
            environment.pop(key, None)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        environment.update(
            HOME=str(home),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_RUNTIME_DIR=str(runtime),
            GSETTINGS_BACKEND="keyfile",
            XDG_SESSION_TYPE="x11",
            GPUI_BACKEND="X11",
            VOCAL_MORE_PRIVATE_TRAY_TEST="1",
            VOCAL_MORE_TRAY_REGISTRATION=str(home / "registration"),
        )
        children = []
        with (
            tempfile.TemporaryFile() as display,
            (output / "desktop.log").open("w") as log,
        ):

            def spawn(args):
                child = subprocess.Popen(
                    args, env=environment, stdout=log, stderr=subprocess.STDOUT
                )
                children.append(child)
                return child

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
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            children.append(xvfb)
            try:

                def number():
                    display.seek(0)
                    return display.read().decode().strip()

                environment["DISPLAY"] = ":" + wait_for(number, "Xvfb display")
                connection = xdisplay.Display(environment["DISPLAY"])
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                watcher = spawn(
                    ["/usr/bin/python3", str(ROOT / "scripts/fixtures/tray_watcher.py")]
                )
                registration = home / "registration"

                def control(data, command):
                    subprocess.run(
                        [str(binary), "--data-dir", str(data), "--command", command],
                        env=environment,
                        check=True,
                        timeout=5,
                    )

                def windows():
                    return [
                        w
                        for w in connection.screen().root.query_tree().children
                        if w.get_attributes().map_state == X.IsViewable
                    ]

                def settings_windows():
                    return [w for w in windows() if w.get_wm_name() == "Vocal More"]

                def close(window):
                    window.send_event(
                        protocol.event.ClientMessage(
                            window=window,
                            client_type=connection.intern_atom("WM_PROTOCOLS"),
                            data=(
                                32,
                                [
                                    connection.intern_atom("WM_DELETE_WINDOW"),
                                    X.CurrentTime,
                                    0,
                                    0,
                                    0,
                                ],
                            ),
                        ),
                        event_mask=0,
                    )
                    connection.sync()

                def menu(service, method, parameters, reply=None):
                    return bus.call_sync(
                        service,
                        "/MenuBar",
                        "com.canonical.dbusmenu",
                        method,
                        parameters,
                        reply,
                        Gio.DBusCallFlags.NONE,
                        4000,
                        None,
                    )

                data = home / "profile"
                data.mkdir()
                (data / "config.yaml").write_text("ui:\n  onboarding_completed: true\n")
                app = spawn(
                    [
                        str(binary),
                        "--data-dir",
                        str(data),
                        "--no-import",
                        "--no-hotkeys",
                    ]
                )
                wait_for(registration.exists, "tray registration")
                time.sleep(2)
                assert app.poll() is None, "app quit without a window"
                assert not windows(), "normal startup created a persistent window"
                service = registration.read_text()
                layout = menu(
                    service, "GetLayout", GLib.Variant("(iias)", (0, -1, []))
                ).unpack()
                assert "设置" in str(layout) and "退出" in str(layout), layout
                # Background service probes must not turn duplicate launches
                # into settings activation and steal the dictation target.
                for _ in range(3):
                    subprocess.run([str(binary), "--data-dir", str(data), "--background", "--no-import", "--no-hotkeys"], env=environment, check=True, timeout=5)
                time.sleep(0.3)
                assert not windows(), "background duplicate launch opened Settings"
                # A real tray-menu request opens the single Settings window.
                menu(
                    service,
                    "Event",
                    GLib.Variant("(isvu)", (5, "clicked", GLib.Variant("i", 0), 0)),
                )
                first = wait_for(settings_windows, "tray Settings menu")[0]
                subprocess.run(
                    [
                        str(binary),
                        "--data-dir",
                        str(data),
                        "--no-import",
                        "--no-hotkeys",
                    ],
                    env=environment,
                    check=True,
                    timeout=5,
                )
                time.sleep(0.3)
                assert len(settings_windows()) == 1, "duplicate Settings window"
                close(first)
                wait_for(lambda: not settings_windows(), "Settings close")
                time.sleep(0.3)
                assert app.poll() is None, "closing Settings quit the background app"
                # Watcher replacement must restore the entry without restarting the app.
                watcher.terminate()
                watcher.wait(timeout=5)
                registration.unlink()
                watcher = spawn(
                    ["/usr/bin/python3", str(ROOT / "scripts/fixtures/tray_watcher.py")]
                )
                wait_for(registration.exists, "tray reconnection")
                menu(
                    service,
                    "Event",
                    GLib.Variant("(isvu)", (5, "clicked", GLib.Variant("i", 0), 0)),
                )
                wait_for(settings_windows, "Settings after tray recovery")
                menu(
                    service,
                    "Event",
                    GLib.Variant("(isvu)", (6, "clicked", GLib.Variant("i", 0), 0)),
                )
                app.wait(timeout=12)
                assert app.returncode == 0
                assert (data / "config.yaml").is_file()
                # Keep a separate synthetic input target focused throughout popup states.
                target = connection.screen().root.create_window(
                    40,
                    50,
                    480,
                    120,
                    0,
                    connection.screen().root_depth,
                    X.InputOutput,
                    X.CopyFromParent,
                    background_pixel=connection.screen().white_pixel,
                )
                target.set_wm_name("Synthetic input target")
                target.map()
                target.set_input_focus(X.RevertToParent, X.CurrentTime)
                connection.sync()
                focus = connection.get_input_focus().focus.id
                # Failed starts remain readable for four seconds like macOS,
                # and can also be dismissed immediately without stealing focus.
                data = home / "error-profile"
                data.mkdir()
                (data / "config.yaml").write_text("ui:\n  onboarding_completed: true\n")
                app = spawn(
                    [
                        str(binary),
                        "--data-dir",
                        str(data),
                        "--no-import",
                        "--no-hotkeys",
                    ]
                )
                time.sleep(1)
                control(data, "toggle")
                error_popup = wait_for(
                    lambda: next(
                        (w for w in windows() if w.get_attributes().override_redirect),
                        None,
                    ),
                    "start error capsule",
                )
                time.sleep(0.6)
                assert error_popup.get_attributes().map_state == X.IsViewable, (
                    "terminal error vanished before it could be read"
                )
                wait_for(
                    lambda: (
                        not [
                            w for w in windows() if w.get_attributes().override_redirect
                        ]
                    ),
                    "terminal error auto-dismiss",
                    seconds=5,
                )
                assert connection.get_input_focus().focus.id == focus
                control(data, "toggle")
                error_popup = wait_for(
                    lambda: next(
                        (w for w in windows() if w.get_attributes().override_redirect),
                        None,
                    ),
                    "second start error capsule",
                )
                geometry = error_popup.get_geometry()
                subprocess.run(
                    [
                        "xdotool",
                        "mousemove",
                        "--window",
                        str(error_popup.id),
                        str(round(geometry.width * 0.84)),
                        str(round(geometry.height * 0.5)),
                        "click",
                        "1",
                    ],
                    env=environment,
                    check=True,
                    timeout=5,
                )
                wait_for(
                    lambda: (
                        not [
                            w for w in windows() if w.get_attributes().override_redirect
                        ]
                    ),
                    "error capsule cancel button",
                )
                assert connection.get_input_focus().focus.id == focus, (
                    "capsule Cancel stole focus"
                )
                assert app.poll() is None
                control(data, "quit")
                app.wait(timeout=12)
                assert app.returncode == 0
                data = home / "capsule-profile"
                app = spawn(
                    [
                        str(binary),
                        "--data-dir",
                        str(data),
                        "--no-import",
                        "--no-hotkeys",
                        "--capsule-fixtures",
                        str(output),
                        "--quit-after-ms",
                        "11000",
                    ]
                )
                time.sleep(3)
                popups = [w for w in windows() if w.get_attributes().override_redirect]
                assert len(popups) == 1, "capsule not visible"
                assert connection.get_input_focus().focus.id == focus, (
                    "capsule stole focus"
                )
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
                        environment["DISPLAY"],
                        "-frames:v",
                        "1",
                        str(output / "capsule-recording.png"),
                    ],
                    env=environment,
                    check=True,
                    timeout=10,
                )
                time.sleep(5)
                assert not [
                    w for w in windows() if w.get_attributes().override_redirect
                ], "capsule did not hide at idle"
                assert connection.get_input_focus().focus.id == focus
                app.wait(timeout=10)
                assert app.returncode == 0
                target.destroy()
                connection.close()
                (output / "background.json").write_text(
                    json.dumps(
                        {
                            "background_without_window": True,
                            "tray_menu": True,
                            "settings_close_keeps_alive": True,
                            "duplicate_settings": True,
                            "tray_reconnection": True,
                            "tray_quit_drained": True,
                            "capsule_focus_preserved": True,
                            "capsule_hides_at_idle": True,
                            "synthetic_states": True,
                            "startup_error_readable_then_auto_dismissed": True,
                            "capsule_cancel_pointer_click": True,
                        },
                        indent=2,
                    )
                    + "\n"
                )
            finally:
                for child in reversed(children):
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
    print(
        "PASS: windowless background, tray menu/recovery/quit, Settings close, capsule focus and idle cleanup"
    )


if __name__ == "__main__":
    if os.environ.get("VOCAL_MORE_PRIVATE_DESKTOP_BUS") != "1":
        with tempfile.TemporaryDirectory(
            prefix="vocal-more-background-bus-"
        ) as temporary:
            home = Path(temporary)
            runtime = home / "runtime"
            runtime.mkdir(mode=0o700)
            environment = os.environ.copy()
            environment.update(
                VOCAL_MORE_PRIVATE_DESKTOP_BUS="1",
                HOME=str(home),
                XDG_RUNTIME_DIR=str(runtime),
                XDG_CONFIG_HOME=str(home / "config"),
                XDG_DATA_HOME=str(home / "data"),
                XDG_CACHE_HOME=str(home / "cache"),
            )
            for key in (
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "GNOME_SETUP_DISPLAY",
                "DASHSCOPE_API_KEY",
            ):
                environment.pop(key, None)
            result = subprocess.run(
                ["dbus-run-session", "--", "/usr/bin/python3", __file__, *sys.argv[1:]],
                env=environment,
                check=False,
            )
            # Portal activation owns private FUSE mounts. Unmount them after
            # the private bus exits, before TemporaryDirectory removes HOME.
            for _ in range(20):
                mounts = [
                    line.split()[4]
                    for line in Path("/proc/self/mountinfo").read_text().splitlines()
                    if line.split()[4].startswith(str(runtime) + "/")
                ]
                if not mounts:
                    break
                for mount in mounts:
                    subprocess.run(
                        ["fusermount3", "-u", mount],
                        check=False,
                        timeout=5,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                time.sleep(0.1)
        raise SystemExit(result.returncode)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=ROOT / "rust/target/debug/vocal-more-desktop"
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".build/linux-smoke")
    args = parser.parse_args()
    run(args.binary.resolve(), args.output.resolve())
