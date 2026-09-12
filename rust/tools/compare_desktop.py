#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""同一 Python 环境下，两版 AppKit/WKWebView 主进程的隔离资源测量。

uv run python rust/tools/compare_desktop.py --output .build/experience-audit/desktop
不注册快捷键、不打开麦克风、不加载用户配置；短暂打开真实设置窗口。
WebKit 辅助进程不计入主进程合计，不能当作整机总占用。
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def worker(kind, output, binary):
    started = time.monotonic()
    data = output / "data"
    data.mkdir(parents=True)
    os.environ.pop("DASHSCOPE_API_KEY", None)
    from vocal_more.config import Config
    Config.get_config_dir = classmethod(lambda cls: data)
    config = {"api_key": "", "dictionary_learning": {"enabled": False},
              "ui": {"onboarding_completed": True, "advanced_settings": True}}
    (data / "config.yaml").write_text(json.dumps(config))
    from AppKit import NSApplication
    from Foundation import NSDate, NSRunLoop
    NSApplication.sharedApplication()
    def pump(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.01))
    def evaluate(webview, expression):
        results = []
        webview.evaluateJavaScript_completionHandler_(expression, lambda value, error: results.append((value, error)))
        deadline = time.monotonic() + 5
        while not results and time.monotonic() < deadline:
            pump(.01)
        assert results and results[0][1] is None, results
        return results[0][0]
    if kind == "python":
        from vocal_more.core import recording_store
        original_store = recording_store.RecordingStore
        class IsolatedStore(original_store):
            def __init__(self, *args, **kwargs):
                super().__init__(recordings_dir=str(data / "recordings"))
        recording_store.RecordingStore = IsolatedStore
        from vocal_more.core.audio_recorder import AudioRecorder
        AudioRecorder.prepare_idle_capture = lambda self: False
        from vocal_more.app import VocalMoreApp
        app = VocalMoreApp()
        app._ensure_dependencies()
        show = app._show_settings
        settings = lambda: app._settings_window._instance
        import rumps
        rumps.quit_application = lambda: None
        close = lambda: app._quit_app(None)
        pids = [os.getpid()]
    else:
        from vocal_more.rust_ui import RustVocalMoreApp
        app = RustVocalMoreApp(binary, data, None, no_hotkeys=True)
        show = app.show_settings
        settings = lambda: app._settings
        close = app.close
        pids = [os.getpid(), app.client.pid]
    result = {"kind": kind, "initialize_including_imports_ms": (time.monotonic()-started)*1000,
              "scope": "owned main processes; no hotkeys, microphone or ASR; excludes WebKit helpers", "measurements": {}}
    def measure(label):
        rows = []
        for index, pid in enumerate(pids):
            raw = subprocess.run(["/usr/bin/vmmap", "-summary", str(pid)], capture_output=True, text=True, check=True).stdout
            (output / f"{label}-{index}-vmmap.txt").write_text(raw)
            match = re.search(r"Physical footprint:\s*([\d.]+)([KMGT]?)", raw)
            rows.append(float(match[1])*{"":1/1048576,"K":1/1024,"M":1,"G":1024,"T":1048576}[match[2]])
        return {"main_process_mib": rows, "sum_mib": sum(rows)}
    from macos_process_stats import counters
    def usage():
        rows = [counters(pid) for pid in pids]
        return {key: sum(row[key] for row in rows) for key in rows[0]}
    try:
        pump(1)
        result["measurements"]["idle"] = measure("idle")
        usage_start, wall_start = usage(), time.monotonic()
        # Let the real run loop block; a synthetic 100 Hz pump would dominate
        # the idle CPU measurement and hide changes in application wakeups.
        idle_deadline = time.monotonic() + 10
        while time.monotonic() < idle_deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(max(0, idle_deadline-time.monotonic())))
        elapsed = time.monotonic() - wall_start
        usage_end = usage()
        result["idle_cpu_percent_one_core"] = (usage_end["cpu_seconds"] - usage_start["cpu_seconds"]) / elapsed * 100
        result["idle_counter_method"] = "proc_pid_rusage RUSAGE_INFO_V1; Mach CPU ticks converted using mach_timebase_info"
        result["idle_counter_delta"] = {key: usage_end[key] - usage_start[key] for key in usage_start}
        result["idle_sample_seconds"] = elapsed
        latencies = []
        for index in range(3):
            start = time.monotonic()
            show()
            deadline = time.monotonic() + 10
            while True:
                pump(.02)
                window = settings()
                if window and window._webview is not None:
                    count = evaluate(window._webview, 'document.querySelectorAll("[role=tab]").length')
                    if count == 7:
                        break
                assert time.monotonic() < deadline, "settings tabs never ready"
            latencies.append((time.monotonic()-start)*1000)
            if index == 0:
                pump(.5)
                result["measurements"]["settings_open"] = measure("settings-open")
            window._on_window_close_requested()
            pump(.5)
        result["settings_ready_ms"] = latencies
        result["measurements"]["after_three_open_close"] = measure("after-close")
    finally:
        close()
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=("python", "rust"))
    parser.add_argument("--rust-binary", type=Path, default=ROOT / ".build/rust-host/vocal-more-backend")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return worker(args.worker, args.output, args.rust_binary.resolve())
    results = []
    for kind in ("python", "rust"):
        folder = args.output / kind
        folder.mkdir()
        with (folder / "run.log").open("w") as log:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", kind, "--output", str(folder), "--rust-binary", str(args.rust_binary.resolve())], stdout=log, stderr=log, check=True, timeout=90)
        results.append(json.loads((folder / "result.json").read_text()))
    (args.output / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
