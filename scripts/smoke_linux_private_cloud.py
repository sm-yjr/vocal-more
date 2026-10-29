#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Live synthetic dictation in a private GNOME desktop; never controls user windows."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("display", "authority", "done"):
        (output / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocal-more-private-cloud-") as temporary:
        home = Path(temporary)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        (home / "tmp").mkdir(mode=0o700)
        extension = home / "data/gnome-shell/extensions/vocal-more-cloud-test@local"
        extension.mkdir(parents=True)
        (extension / "metadata.json").write_text(
            json.dumps(
                {
                    "uuid": "vocal-more-cloud-test@local",
                    "name": "Owned cloud test",
                    "description": "Private cloud integration fixture",
                    "shell-version": ["49", "50"],
                }
            )
        )
        (extension / "extension.js").write_text("""
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
export default class Test extends Extension {
 enable(){
  const out=GLib.getenv('VOCAL_MORE_CLOUD_OUTPUT');
  GLib.file_set_contents(`${out}/display`,GLib.getenv('DISPLAY')??'');
  GLib.file_set_contents(`${out}/authority`,GLib.getenv('XAUTHORITY')??'');
  Main.overview.hide();
  this.diagnostic=GLib.timeout_add(GLib.PRIORITY_DEFAULT,4000,()=>{
   const windows=global.get_window_actors().map(a=>a.meta_window);
   GLib.file_set_contents(`${out}/windows.json`,JSON.stringify({windows:windows.map(w=>({title:w.title,mapped:w.get_compositor_private()?.mapped})),focus:global.display.focus_window?.title}));
   const w=windows.find(w=>w.title==='Vocal More AT-SPI Fixture');
   if(w){w.activate(global.get_current_time());const r=w.get_frame_rect();const pointer=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);pointer.notify_absolute_motion(GLib.get_monotonic_time(),r.x+35,r.y+50);pointer.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.PRESSED);pointer.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.RELEASED);this.pointer=pointer;}
   this.diagnostic=0;return GLib.SOURCE_REMOVE;
  });
  this.id=global.display.connect('window-created',(_d,w)=>{
   let attempts=0;
   GLib.timeout_add(GLib.PRIORITY_DEFAULT,100,()=>{
    if(w.title==='Vocal More AT-SPI Fixture' && w.get_compositor_private()?.mapped){
     w.activate(global.get_current_time());
     const pointer=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
     const r=w.get_frame_rect();
     pointer.notify_absolute_motion(GLib.get_monotonic_time(),r.x+35,r.y+50);
     pointer.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.PRESSED);
     pointer.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.RELEASED);
     this.pointer=pointer;
     this.keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
     this.keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Shift_L,Clutter.KeyState.PRESSED);
     this.keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Shift_L,Clutter.KeyState.RELEASED);
     return GLib.SOURCE_REMOVE;
    }
    return ++attempts<30?GLib.SOURCE_CONTINUE:GLib.SOURCE_REMOVE;
   });
  });
 }
 disable(){if(this.diagnostic)GLib.source_remove(this.diagnostic);if(this.id)global.display.disconnect(this.id);this.id=0;}
}
""")
        env = os.environ.copy()
        env.update(
            TMPDIR=str(home / "tmp"),
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_RUNTIME_DIR=str(runtime),
            GSETTINGS_BACKEND="keyfile",
            XDG_CURRENT_DESKTOP="GNOME",
            XDG_SESSION_TYPE="wayland",
            GDK_BACKEND="wayland",
            GTK_A11Y="atspi",
            GSK_RENDERER="cairo",
            LIBGL_ALWAYS_SOFTWARE="1",
            VOCAL_MORE_CLOUD_OUTPUT=str(output),
            VOCAL_MORE_CLOUD_ROOT=str(ROOT),
            VOCAL_MORE_CLOUD_BINARY=str(args.binary.resolve()),
            VOCAL_MORE_CLOUD_KEY=str(args.key_file.resolve()),
            VOCAL_MORE_CLOUD_SCENARIO=args.scenario,
        )
        for key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "DBUS_SESSION_BUS_ADDRESS",
            "DASHSCOPE_API_KEY",
        ):
            env.pop(key, None)
        script = """set -e
gsettings set org.gnome.shell enabled-extensions "['vocal-more-cloud-test@local']"
gsettings set org.gnome.desktop.interface toolkit-accessibility true
gsettings set org.gnome.desktop.interface enable-animations false
export WAYLAND_DISPLAY=vocal-more-private-cloud
gnome-shell --headless --wayland --wayland-display "$WAYLAND_DISPLAY" --virtual-monitor 1280x960 &
for i in $(seq 1 150); do test -s "$VOCAL_MORE_CLOUD_OUTPUT/display" && break; sleep 0.1; done
export DISPLAY="$(cat "$VOCAL_MORE_CLOUD_OUTPUT/display")"
export XAUTHORITY="$(cat "$VOCAL_MORE_CLOUD_OUTPUT/authority")"
dbus-update-activation-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_RUNTIME_DIR HOME XDG_CONFIG_HOME XDG_DATA_HOME GTK_A11Y
case "$VOCAL_MORE_CLOUD_SCENARIO" in
  short-tap) set -- --short-tap ;;
  streaming) set -- --streaming ;;
  focus-change) set -- --focus-change ;;
  walkie) set -- --mode walkie_talkie ;;
esac
python3 "$VOCAL_MORE_CLOUD_ROOT/scripts/smoke_linux_auto_input.py" --real-session --binary "$VOCAL_MORE_CLOUD_BINARY" --key-file "$VOCAL_MORE_CLOUD_KEY" --output "$VOCAL_MORE_CLOUD_OUTPUT/result" "$@"
touch "$VOCAL_MORE_CLOUD_OUTPUT/done"
wait
"""
        with (output / "desktop.log").open("w") as log:
            process = subprocess.Popen(
                ["dbus-run-session", "--", "bash", "-c", script],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                end = time.monotonic() + 120
                while not (output / "done").exists():
                    if process.poll() is not None:
                        raise RuntimeError(
                            "private cloud test exited; inspect desktop.log"
                        )
                    if time.monotonic() > end:
                        raise TimeoutError(
                            "private cloud test timed out; inspect desktop.log"
                        )
                    time.sleep(0.1)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
                time.sleep(0.5)
    print("PASS: live synthetic cloud dictation in owned GNOME/Wayland desktop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument(
        "--binary", type=Path, default=ROOT / "rust/target/debug/vocal-more-desktop"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build/linux-private-cloud"
    )
    parser.add_argument(
        "--scenario",
        choices=["short-tap", "streaming", "focus-change", "walkie"],
        default="short-tap",
    )
    run(parser.parse_args())
