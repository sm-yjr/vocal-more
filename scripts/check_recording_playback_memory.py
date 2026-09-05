"""Measure real macOS recording playback using a disposable silent WAV.

uv run python scripts/check_recording_playback_memory.py --backend native
Repeat with --backend base64 to compare the previous transport. No microphone,
credentials, saved recordings or network API is used. A test settings window opens.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path

import objc
from AppKit import NSApplication
from Foundation import NSDate, NSRunLoop

from vocal_more.core.recording_store import RecordingStore
from vocal_more.ui.settings_window import SettingsWindow


def settle(seconds: float = 1.0) -> None:
    with objc.autorelease_pool():
        NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))
    gc.collect()


def footprint() -> dict[str, str]:
    output = subprocess.check_output(
        ["footprint", "--pid", str(os.getpid()), "--noCategories"], text=True,
    )
    return {
        line.strip().split(":", 1)[0]: line.strip().split(":", 1)[1].strip()
        for line in output.splitlines() if "phys_footprint" in line
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("native", "base64"), default="native")
    parser.add_argument("--minutes", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.minutes <= 0:
        parser.error("--minutes must be positive")
    NSApplication.sharedApplication()
    with tempfile.TemporaryDirectory(prefix="vocal-playback-check-") as directory:
        path = Path(directory) / "sample.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            for _ in range(args.minutes * 60):
                wav.writeframesraw(bytes(32000))
        (Path(directory) / "recordings.json").write_text(json.dumps([
            {"id": "sample", "filename": "sample.wav", "status": "completed",
             "duration_seconds": args.minutes * 60, "transcript": "Memory profile"},
        ]))
        store = RecordingStore(directory, auto_compact=False)
        window = SettingsWindow(recording_store=store)
        window.show(config={"ui": {"language": "en"}}, asr_models=[], llm_models=[],
                    devices=[], dictionary=[], audio_input_status={"phase": "benchmark"})
        window._window.setTitle_("Vocal More — playback memory check")
        settle()
        report = {"backend": args.backend, "audio_bytes": path.stat().st_size,
                  "scope": "source settings host, main process; no ASR or helper totals",
                  "before": footprint()}
        with objc.autorelease_pool():
            if args.backend == "native":
                window._handle_play_recording("sample")
                window._recording_player._player.setMuted_(True)
            else:
                # The pre-optimization handler, including its real JS transport.
                def legacy_play():
                    wav_b64 = store.get_wav_base64("sample")
                    window._eval_js(f"playAudio('sample', {json.dumps(wav_b64)})")
                legacy_play()
        settle(2)
        report["playing"] = footprint()
        if args.backend == "native":
            from CoreMedia import CMTimeGetSeconds

            player = window._recording_player._player
            assert player is not None and player.error() is None
            assert CMTimeGetSeconds(player.currentTime()) > 0
            # A late stop for another recording must not stop this one.
            window._handle_stop_recording("previous")
            assert window._recording_player._player is player
            del player
        with objc.autorelease_pool():
            window.close()
        settle(2)
        assert window._js_queue.empty()
        assert window._webview is None
        if args.backend == "native":
            assert window._recording_player._player is None
            assert window._recording_player._timer is None
        report["closed"] = footprint()
        store.close()
        result = json.dumps(report, indent=2)
        if args.output:
            args.output.write_text(result + "\n")
        print(result)


if __name__ == "__main__":
    main()
