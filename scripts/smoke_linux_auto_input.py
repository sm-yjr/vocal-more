#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit live provider -> production desktop -> owned Wayland field acceptance.

Only synthetic audio and the owned GTK field are inspected. The real clipboard
is compared in memory, never persisted. Credentials live in a deleted profile.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from Xlib import X, error
from Xlib import display as xdisplay

ROOT = Path(__file__).resolve().parents[1]


def wait_for(check, description, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError(description)


def clipboard():
    result = subprocess.run(
        ["wl-paste", "--no-newline"], capture_output=True, timeout=3, check=False
    )
    return result.returncode, hashlib.sha256(result.stdout).hexdigest()


def run(args):
    assert args.real_session and os.environ.get("XDG_SESSION_TYPE") == "wayland"
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocal-more-auto-input-") as temporary:
        data = Path(temporary)
        wav = data / "speech.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "flite=text='Vocal More automatic input works.':voice=slt",
                "-af",
                "apad=pad_dur=8",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav),
            ],
            check=True,
            timeout=15,
        )
        secret = args.key_file.read_text().strip()
        assert secret.startswith("sk-") and "\n" not in secret
        config = data / "config.yaml"
        config.touch(mode=0o600)
        config.write_text(
            json.dumps(
                {
                    "api_key": secret,
                    "ui": {"onboarding_completed": True},
                    "screen_context_enabled": False,
                    "dictionary_learning": {"enabled": False},
                    "auto_paste": True,
                    "streaming_paste": args.streaming,
                    "default_mode": args.mode,
                }
            )
        )
        del secret
        env = os.environ.copy()
        env.pop("DASHSCOPE_API_KEY", None)
        env.update(
            VOCAL_MORE_INPUT_ACCEPTANCE="1",
            GDK_BACKEND="wayland",
            GTK_A11Y="atspi",
            VOCAL_MORE_ATSPI_TEST_CONTROL=str(data / "control"),
            VOCAL_MORE_EDITOR_REPORT=str(data / "editor.json"),
        )
        logs = (args.output / "desktop.log").open("w")
        editor = subprocess.Popen(
            ["/usr/bin/python3", str(ROOT / "scripts/fixtures/atspi_editor.py")],
            env=env,
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        app = None
        display = xdisplay.Display()

        def capsule_visible():
            for window in display.screen().root.query_tree().children:
                try:
                    pid = window.get_full_property(
                        display.intern_atom("_NET_WM_PID"), X.AnyPropertyType
                    )
                    attributes = window.get_attributes()
                    if (
                        pid is not None
                        and int(pid.value[0]) == app.pid
                        and attributes.override_redirect
                        and attributes.map_state == X.IsViewable
                    ):
                        return True
                except error.BadWindow:
                    pass
            return False

        def field(command):
            wait_for(lambda: not (data / "control").exists(), "editor busy")
            (data / "control").write_text(command)
            wait_for(lambda: not (data / "control").exists(), "editor control")

        def snapshot():
            (data / "editor.json").unlink(missing_ok=True)
            field("snapshot")
            wait_for(lambda: (data / "editor.json").exists(), "editor report")
            return json.loads((data / "editor.json").read_text())

        def control(command):
            subprocess.run(
                [str(args.binary), "--data-dir", str(data), "--command", command],
                env=env,
                check=True,
                timeout=5,
                capture_output=True,
            )

        try:
            time.sleep(1)
            field("raise")
            wait_for(lambda: snapshot()["active"], "owned Wayland editor focus")
            before_clipboard = clipboard()
            app = subprocess.Popen(
                [
                    str(args.binary),
                    "--data-dir",
                    str(data),
                    "--no-import",
                    "--no-hotkeys",
                    "--allow-test-sources",
                    "--test-audio",
                    str(wav),
                ],
                env=env,
                stdout=logs,
                stderr=subprocess.STDOUT,
            )
            time.sleep(2)
            assert app.poll() is None, "desktop exited"
            field("reset")
            field("raise")
            wait_for(
                lambda: (s := snapshot())["active"] and s["selection"] == [3, 5],
                "original input focus/selection",
            )
            control("press")
            if args.short_tap:
                control("release")
            time.sleep(4)
            if args.focus_change:
                field("other")
            control("press" if args.short_tap else "release")

            def completed():
                path = data / "recordings/history.json"
                if path.exists():
                    rows = json.loads(path.read_text())["recordings"]
                    return next((r for r in rows if r.get("status") == "success"), None)

            row = wait_for(completed, "provider result/history", seconds=40)
            assert 3 <= row["duration_seconds"] < 7, (
                "release did not end recording before WAV EOF"
            )
            assert row["mode"] == args.mode
            transcript = row.get("transcript", "")
            assert transcript and len(transcript) > 8, "synthetic speech not recognized"
            expected = (
                "前缀：旧值后缀"
                if args.focus_change
                else "前缀：" + transcript + "后缀"
            )
            if not args.focus_change:
                wait_for(
                    lambda: snapshot()["value"] == expected,
                    "automatic native insertion",
                )
            else:
                time.sleep(1)
            result = snapshot()
            assert result["value"] == expected, (
                "original field corrupted or output missing"
            )
            if not args.focus_change:
                assert result["caret"] == 3 + len(transcript), "caret not retained"
            assert clipboard() == before_clipboard, "clipboard changed"
            hide_started = time.monotonic()
            if args.focus_change:
                assert capsule_visible(), "failure notice was not readable"
            wait_for(
                lambda: not capsule_visible(),
                "capsule lingered after completion",
                seconds=5 if args.focus_change else 0.3,
            )
            hide_ms = round((time.monotonic() - hide_started) * 1000)
            control("quit")
            assert app.wait(timeout=15) == 0
            report = {
                "session": "wayland",
                "binary": str(args.binary),
                "synthetic_cloud_recognition": True,
                "native_auto_insert": not args.focus_change,
                "streaming": args.streaming,
                "mode": args.mode,
                "short_tap_latched": args.short_tap,
                "focus_change_refused": args.focus_change,
                "clipboard_preserved": True,
                "transcript": transcript,
                "owned_field": result,
                "clean_shutdown": True,
                "capsule_hide_after_input_ms": hide_ms,
            }
            (args.output / "session.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
        finally:
            if app and app.poll() is None:
                try:
                    control("quit")
                    app.wait(timeout=10)
                except (subprocess.SubprocessError, OSError):
                    app.kill()
                    app.wait()
            editor.terminate()
            editor.wait(timeout=5)
            display.close()
            logs.close()
    print("PASS: synthetic recognition, guarded Wayland output, clipboard and shutdown")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-session", action="store_true", required=True)
    parser.add_argument("--binary", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--streaming", action="store_true")
    parser.add_argument("--focus-change", action="store_true")
    parser.add_argument("--short-tap", action="store_true")
    parser.add_argument(
        "--mode", choices=["realtime_long", "walkie_talkie"], default="realtime_long"
    )
    run(parser.parse_args())
