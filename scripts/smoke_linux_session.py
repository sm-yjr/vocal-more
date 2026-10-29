#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit opt-in real desktop/microphone acceptance; only own windows/data are inspected.

Uses the real session bus/tray and a disposable profile. Explicit acceptance
mode exercises the Xwayland compatibility capsule without attaching to the
user's live Shell extension (private GNOME tests cover its renderer). --key-file authorizes a
short native microphone + provider connection, always cancelled without output.
Screenshots include only the application's capsule. No user fields are read.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from gi.repository import Gio, GLib
from PIL import Image
from Xlib import X, error, protocol
from Xlib import display as xdisplay
from Xlib.ext import composite

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, description, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError(description)


def run(binary, output, key_file):
    assert os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"), (
        "real desktop required"
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "session.json").unlink(missing_ok=True)
    connection = xdisplay.Display()
    root = connection.screen().root
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    environment = os.environ.copy()
    environment.pop("DASHSCOPE_API_KEY", None)
    # Exercise the default renderer selection; retain the real Wayland session.
    environment.pop("GPUI_BACKEND", None)

    def visible(pid):
        atom = connection.intern_atom("_NET_WM_PID")
        result = []
        candidates = {w.id: w for w in root.query_tree().children}
        clients = root.get_full_property(
            connection.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType
        )
        if clients is not None:
            for xid in clients.value:
                candidates[int(xid)] = connection.create_resource_object(
                    "window", int(xid)
                )
        for window in candidates.values():
            try:
                prop = window.get_full_property(atom, X.AnyPropertyType)
                if (
                    prop is not None
                    and int(prop.value[0]) == pid
                    and window.get_attributes().map_state == X.IsViewable
                ):
                    result.append(window)
            except error.BadWindow:
                pass  # GNOME's client list can lag a just-destroyed window.
        return result

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

    with (
        tempfile.TemporaryDirectory(prefix="vocal-more-real-session-") as temporary,
        (output / "session.log").open("w") as log,
    ):
        data = Path(temporary)
        config = {
            "ui": {"onboarding_completed": True},
            "screen_context_enabled": False,
            "dictionary_learning": {"enabled": False},
        }
        if key_file:
            secret = key_file.read_text().strip()
            assert secret.startswith("sk-") and "\n" not in secret, (
                "use the existing raw key file"
            )
            config["api_key"] = secret
        path = data / "config.yaml"
        path.touch(mode=0o600)
        path.write_text(json.dumps(config))
        config.clear()
        app = subprocess.Popen(
            [
                str(binary),
                "--data-dir",
                str(data),
                "--no-import",
                "--no-hotkeys",
                "--allow-test-sources",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        service = f"org.kde.StatusNotifierItem-{app.pid}-1"
        target = None
        report = {
            "session": os.environ.get("XDG_SESSION_TYPE"),
            "installed_binary": str(binary),
            "cloud_content_verification": False,
        }

        def control(command):
            subprocess.run(
                [str(binary), "--data-dir", str(data), "--command", command],
                env=environment,
                check=True,
                timeout=5,
            )

        def call(method, params):
            return bus.call_sync(
                service,
                "/MenuBar",
                "com.canonical.dbusmenu",
                method,
                params,
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )

        unique_owner = [""]

        def registered():
            try:
                unique_owner[0] = bus.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "GetNameOwner",
                    GLib.Variant("(s)", (service,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                ).unpack()[0]
            except GLib.Error:
                pass  # Keep the last owner to detect stale watcher items after Quit.
            try:
                items = bus.call_sync(
                    "org.kde.StatusNotifierWatcher",
                    "/StatusNotifierWatcher",
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    GLib.Variant(
                        "(ss)",
                        (
                            "org.kde.StatusNotifierWatcher",
                            "RegisteredStatusNotifierItems",
                        ),
                    ),
                    None,
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                ).unpack()[0]
                return any(
                    service in item
                    or (
                        unique_owner[0]
                        and (
                            item.startswith(
                                (unique_owner[0] + "@", unique_owner[0] + "/")
                            )
                        )
                    )
                    for item in items
                )
            except GLib.Error:
                return False

        def layout():
            return str(call("GetLayout", GLib.Variant("(iias)", (0, -1, []))).unpack())

        try:
            wait_for(registered, "real GNOME tray registration")
            wait_for(lambda: "就绪" in layout() or "Ready" in layout(), "backend ready")
            assert app.poll() is None and not visible(app.pid), (
                "persistent window on real desktop"
            )
            report["windowless_background"] = True
            call(
                "Event", GLib.Variant("(isvu)", (5, "clicked", GLib.Variant("i", 0), 0))
            )
            window = wait_for(
                lambda: next(
                    (
                        w
                        for w in visible(app.pid)
                        if not w.get_attributes().override_redirect
                    ),
                    None,
                ),
                "real Settings",
            )
            close(window)
            wait_for(lambda: not visible(app.pid), "Settings closed")
            assert app.poll() is None
            report["settings_close_keeps_alive"] = True
            target = root.create_window(
                60,
                70,
                480,
                100,
                0,
                connection.screen().root_depth,
                X.InputOutput,
                X.CopyFromParent,
                background_pixel=connection.screen().white_pixel,
            )
            target.set_wm_name("Vocal More synthetic focus target")
            target.map()
            connection.sync()
            wait_for(
                lambda: (
                    target.get_full_property(
                        connection.intern_atom("_NET_WM_DESKTOP"), X.AnyPropertyType
                    )
                    is not None
                ),
                "owned target managed",
            )
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(target.id)],
                check=True,
                timeout=5,
            )
            wait_for(
                lambda: connection.get_input_focus().focus.id == target.id,
                "owned focus target",
            )
            control("toggle")

            capsule = wait_for(
                lambda: next(
                    (
                        w
                        for w in visible(app.pid)
                        if w.get_attributes().override_redirect
                    ),
                    None,
                ),
                "real desktop capsule",
            )
            if key_file:
                wait_for(
                    lambda: "正在聆听" in layout() or "Listening" in layout(),
                    "native microphone recording",
                )
                time.sleep(0.3)
                report["native_microphone_recording"] = True
            else:
                wait_for(
                    lambda: "密钥" in layout() or "key" in layout().lower(),
                    "actionable no-key error",
                )
                report["native_microphone_recording"] = False
            assert connection.get_input_focus().focus.id == target.id, (
                "capsule stole focus on Wayland"
            )
            geometry = capsule.get_geometry()
            # Capture only our redirected window; never capture other desktop
            # contents. Use a unique evidence filename for viewers that cache
            # image paths across acceptance runs.
            image_path = output / f"capsule-{app.pid}.png"
            for attempt in range(30):
                pixmap = composite.name_window_pixmap(capsule)
                connection.sync()
                pixels = pixmap.get_image(
                    0, 0, geometry.width, geometry.height, X.ZPixmap, 0xFFFFFFFF
                )
                captured = Image.frombytes(
                    "RGB",
                    (geometry.width, geometry.height),
                    pixels.data,
                    "raw",
                    "BGRX",
                )
                pixmap.free()
                scale = geometry.width / 420

                def has_content(left, right, image=captured, factor=scale):
                    region = image.crop(
                        (
                            round(left * factor),
                            round(20 * factor),
                            round(right * factor),
                            round(70 * factor),
                        )
                    )
                    return sum(region.convert("L").histogram()[121:]) > 20

                if has_content(18, 110) and has_content(118, 330):
                    captured.save(image_path)
                    break
                time.sleep(0.02)
            else:
                captured.save(image_path)
                raise AssertionError(
                    "caption/waveform missing in owned capsule pixel readback"
                )
            report["pixel_readback_attempts"] = attempt + 1
            report["capsule_image"] = image_path.name
            report["capsule_renderer"] = "xwayland-popup-compatibility"
            report["capsule_visible_on_current_desktop"] = True
            report["focus_preserved"] = True
            # Wayland rejects XTest pointer warps outside the focused X11
            # surface. Exercise cancellation through the production tray/IPC;
            # pointer hitboxes are covered in the private X11/GNOME tests.
            control("cancel")
            wait_for(lambda: not visible(app.pid), "capsule hidden after cancel")
            assert connection.get_input_focus().focus.id == target.id
            report["cancel_hides_capsule"] = True
            call(
                "Event", GLib.Variant("(isvu)", (6, "clicked", GLib.Variant("i", 0), 0))
            )
            app.wait(timeout=15)
            assert app.returncode == 0
            report["tray_quit_drained"] = True
            wait_for(lambda: not registered(), "tray removal after quit")
            report["tray_removed_after_quit"] = True
            pcm = [
                json.loads(p.read_text()).get("pcm_bytes", 0)
                for p in data.rglob("*.json")
            ]
            report["native_pcm_bytes"] = max(pcm, default=0)
            if key_file:
                assert report["native_pcm_bytes"] > 0, "no native PCM observed"
            (output / "session.json").write_text(json.dumps(report, indent=2) + "\n")
        finally:
            if app.poll() is None:
                try:
                    control("quit")
                    app.wait(timeout=15)
                except (subprocess.SubprocessError, OSError):
                    app.terminate()
                    app.wait(timeout=5)
            if target is not None:
                target.destroy()
                connection.sync()
            connection.close()
    print(
        "PASS: current desktop background, real tray, Settings close, capsule, focus, cancel and quit"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-session", action="store_true", required=True)
    parser.add_argument(
        "--binary", type=Path, default=ROOT / "rust/target/debug/vocal-more-desktop"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build/linux-session-acceptance"
    )
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    run(args.binary.resolve(), args.output.resolve(), args.key_file)
