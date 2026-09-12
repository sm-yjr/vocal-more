#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""隔离复现迁移后的粘贴时序、设备选择和界面通知差异；不操作系统剪贴板。

uv run python rust/tools/probe_experience.py --output .build/experience-audit/probes
"""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import queue
import sys
import time
import wave
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("integration_fixture", ROOT / "tests/test_rust_backend_integration.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fixture.BINARY = ROOT / ".build/rust-host/vocal-more-backend"
    results = {}
    events = queue.Queue()
    with fixture.fixture_http() as (port, *_):
        with fixture.client(args.output / "rust-data", port, events.put) as client:
            client.call("set_asr_model", {"model": "qwen3-asr-flash"})
            client.call("set_config", {"key": "enable_polish", "value": False})
            client.call("set_config", {"key": "dictionary_learning.enabled", "value": False})
            fixture.start_audio(client)
            final = fixture.wait_event(events, "final_result")
            paste = fixture.wait_event(events, "paste_requested")
            deadline = time.monotonic() + 5
            while client.call("status")["state"] != "idle":
                assert time.monotonic() < deadline
                time.sleep(.01)
            snapshot = client.call("snapshot")
            assert len(snapshot["pending_pastes"]) == 1
            # Model a busy OS executor: the first paste has not been claimed.
            client.call("start", {"source": {"kind": "stream"}})
            claim = client.call("claim_paste", {"token": paste["token"]})
            assert claim == {"cancelled": True}, claim
            results["rust_pending_paste_lost_on_next_recording"] = {
                "idle_before_os_paste": True, "pending_before_next_start": 1,
                "claim_after_next_start": claim, "previous_text_retained": final["text"],
                "boundary": "production Rust actor; simulated delayed OS executor; no real paste"}
            client.call("cancel")
            client.call("set_device", {"device": "unplugged-test-microphone"})
            devices = client.call("refresh_devices")
            selected = client.call("get_config")["audio"]["input_device"]
            assert devices == [] and selected == "unplugged-test-microphone"
            results["rust_missing_device_after_refresh"] = {"devices": devices, "selected_device": selected,
                "boundary": "empty native device provider; physical unplug not exercised"}

    # Execute the Python production resolver with only device discovery and
    # configuration persistence replaced; do not enumerate/open real devices.
    from vocal_more.core.audio_recorder import AudioRecorder
    config = SimpleNamespace(audio=SimpleNamespace(input_device="unplugged-test-microphone"), save=Mock())
    recorder = AudioRecorder.__new__(AudioRecorder)
    recorder._device_name = config.audio.input_device
    recorder._use_config_device = True
    with patch("vocal_more.core.audio_recorder.get_config", return_value=config), patch("vocal_more.core.audio_recorder.sd.query_devices", return_value=[]):
        resolved = recorder._resolve_device()
    assert resolved is None and config.audio.input_device is None and config.save.call_count == 1
    results["python_missing_device_after_resolve"] = {"resolved": resolved, "selected_device": config.audio.input_device, "persisted": True}

    from vocal_more.modes.realtime_long import RealtimeLongMode
    fake = SimpleNamespace(_streaming_paste_active=True, _streamed_raw_parts=[],
        _is_active_session=lambda token: True,
        _keyboard=SimpleNamespace(paste_text=Mock(side_effect=RuntimeError("synthetic paste failure"))))
    with patch("vocal_more.modes.realtime_long.normalize_terms", side_effect=lambda text: text):
        RealtimeLongMode._paste_streamed_segment(fake, "测试", 1)
    assert fake._streamed_raw_parts == [] and not fake._streaming_paste_active
    results["python_failed_streaming_paste"] = {"streaming_active": fake._streaming_paste_active,
        "acknowledged_raw_parts": fake._streamed_raw_parts}

    # Execute final-result handlers without creating windows or notifications.
    from vocal_more.app import VocalMoreApp
    from vocal_more.rust_ui import RustVocalMoreApp
    calls = []
    python_ui = SimpleNamespace(config=SimpleNamespace(auto_paste=False),
        _finish_live_benchmark_trace=lambda **kwargs: None,
        _run_on_main_thread=lambda action: action(),
        _refresh_copy_last_result_item=lambda: calls.append("refresh_copy_item"),
        _show_result_notification=lambda text: calls.append("result_notification"))
    VocalMoreApp._on_result(python_ui, "测试")
    rust_calls = []
    rust_ui = SimpleNamespace(_notify=lambda text: rust_calls.append("result_notification"))
    RustVocalMoreApp._event(rust_ui, "final_result", {"text": "测试"})
    assert calls == ["refresh_copy_item", "result_notification"] and rust_calls == []
    results["completion_ui_actions"] = {"python": calls, "rust": rust_calls,
        "rust_retains_last_text": rust_ui._last_text == "测试"}

    from compare_backends import Client, Fixture
    wav = args.output / "input.wav"
    pcm = b"\x00\x10" * 3200
    with wave.open(str(wav), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    results["first_connection_rejected"] = {}
    for kind in ("python", "rust"):
        server = Fixture(reject_first=True)
        folder = args.output / (kind + "-network")
        data = folder / "data"
        data.mkdir(parents=True)
        (data / "config.yaml").write_text(json.dumps({"api_key": "benchmark-placeholder", "default_mode": "realtime_long",
            "asr": {"model": "qwen3.5-omni-flash-realtime"}, "auto_paste": False,
            "enable_polish": False, "dictionary_learning": {"enabled": False}}))
        (data / "fixture-url.txt").write_text(server.endpoint)
        command = ([sys.executable, "-u", str(ROOT / "rust/tools/compare_backends.py"), "--python-worker", str(data), "--wav", str(wav)]
            if kind == "python" else [str(fixture.BINARY), "--data-dir", str(data), "--allow-test-sources",
                "--fixture-http", "http://127.0.0.1:1", "--fixture-websocket", server.endpoint])
        connection = Client(command, folder)
        try:
            start = time.monotonic()
            started = connection.call("start", {"source": {"kind": "stream"}} if kind == "rust" else {})
            if kind == "rust":
                for offset in range(0, len(pcm), 1280):
                    connection.call("append", {"generation": started["generation"], "pcm_base64": base64.b64encode(pcm[offset:offset+1280]).decode()})
            # Installed Python SDK waits up to 5s before surfacing a failed
            # upgrade, then the app applies its first 1s retry delay.
            time.sleep(7.5)
            status = connection.call("status")
            partials = [m["params"]["text"] for at, m in connection.events if at >= start and m["method"] == "partial_result"]
            results["first_connection_rejected"][kind] = {"connection_attempts_by_7_5s": server.connection_count,
                "state": status["state"], "partial_results": partials,
                "captured_bytes": status["core"]["pcm_bytes"]}
            assert not server.errors, server.errors
            if kind == "python":
                assert server.connection_count >= 2 and partials
            else:
                assert server.connection_count == 1 and not partials
            connection.call("cancel")
        finally:
            connection.close()
            server.close()

    server = Fixture(early_segment=True)
    connection = Client([str(fixture.BINARY), "--data-dir", str(args.output / "stream-data"), "--allow-test-sources",
        "--fixture-http", "http://127.0.0.1:1", "--fixture-websocket", server.endpoint], args.output / "stream-process")
    try:
        connection.call("set_asr_model", {"model": "qwen3-asr-flash-realtime-2026-02-10"})
        connection.call("set_config", {"key": "enable_polish", "value": False})
        connection.call("set_config", {"key": "streaming_paste", "value": True})
        connection.call("set_config", {"key": "dictionary_learning.enabled", "value": False})
        start = time.monotonic()
        generation = connection.call("start", {"source": {"kind": "stream"}})["generation"]
        for offset in range(0, 6400, 1280):
            connection.call("append", {"generation": generation, "pcm_base64": base64.b64encode(pcm[offset:offset+1280]).decode()})
        _, event = connection.event("paste_requested", start)
        assert event["streaming"]
        connection.call("claim_paste", {"token": event["token"]})
        connection.call("prepare_paste_observation", {"token": event["token"], "snapshot": None})
        # The real Rust thin UI reports a paste exception by cancelling its
        # observation and showing a local error; it has no paste-failed ACK.
        connection.call("cancel_observation")
        connection.call("finish")
        _, final = connection.event("final_result", start)
        connection.call("snapshot")
        final_pastes = [m["params"] for at, m in connection.events if at >= start and m["method"] == "paste_requested" and not m["params"]["streaming"]]
        assert final["text"] and not final_pastes
        results["rust_failed_streaming_paste"] = {"simulated_failed_text": event["text"],
            "final_text": final["text"], "final_paste_requests": final_pastes,
            "boundary": "production actor; replayed the UI failure RPC path; no actual OS paste"}
    finally:
        connection.close()
        server.close()
    (args.output / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
