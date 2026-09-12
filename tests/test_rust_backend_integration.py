"""Actual Python UI transport → Rust process → local HTTP → durable history.

Build first with cargo build --manifest-path rust/Cargo.toml -p
vocal-more-backend. No cloud key, microphone, or user directory is used.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import wave

import pytest

from vocal_more.rust_client import RustBackendClient, RustBackendError

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("VOCAL_MORE_TEST_RUST_BACKEND", ROOT / "rust/target/debug/vocal-more-backend"))
pytestmark = pytest.mark.skipif(not BINARY.is_file(), reason="Build the Rust application service before integration tests")


@contextmanager
def fixture_http():
    requests = []
    arrived = threading.Event()
    release = threading.Event()
    stalled = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            assert "Authorization" not in self.headers
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(value)
            arrived.set()
            if stalled.is_set():
                release.wait(5)
            result = {"output": {"choices": [{"message": {"role": "assistant", "content": [{"text": "这是rust集成测试。"}]}, "finish_reason": "stop"}]}, "usage": {"input_tokens": 10, "output_tokens": 4}}
            if "compatible-mode" in self.path:
                result = {"choices": [{"delta": {"content": "这是rust集成测试。"}, "finish_reason": "stop"}], "usage": {"input_tokens": 10, "output_tokens": 4}}
            data = ("data: " + json.dumps(result) + "\n\ndata: [DONE]\n\n").encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, requests, arrived, stalled, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def client(path, port, callback=None):
    return RustBackendClient(BINARY, path, on_event=callback,
        extra_args=("--allow-test-sources", "--fixture-http", f"http://127.0.0.1:{port}",
                    "--fixture-websocket", f"ws://127.0.0.1:{port}/realtime"))


def wait_event(events, method, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = events.get(timeout=max(0.01, deadline-time.monotonic()))
        if event["method"] == method:
            return event["params"]
    raise AssertionError(f"Missing {method}")


def start_audio(connection):
    started = connection.call("start", {"source": {"kind": "stream"}})
    pcm = b"\x00\x10" * 3200
    for offset in range(0, len(pcm), 1280):
        connection.call("append", {"generation": started["generation"],
            "pcm_base64": base64.b64encode(pcm[offset:offset+1280]).decode()})
    connection.call("finish")
    return started, pcm


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX native ABI fixture")
def test_native_preparation_and_reuse_through_real_application_process(tmp_path):
    """The shipped actor must prepare its configured source, not just Host tests."""
    native = tmp_path / "fixture.dylib"
    subprocess.run(["cc", "-dynamiclib" if sys.platform == "darwin" else "-shared",
        "-fPIC", "-pthread", "-O1", "-DVM_TEST_WARM", "-I", str(ROOT / "native/audio/include"),
        str(ROOT / "rust/crates/core/tests/native_fixture.c"), "-o", str(native)], check=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text(json.dumps({
        "audio": {"input_device": "fixture mic", "capture_channels": 3,
                  "blocksize": 1280, "capture_backend": "low_latency", "gain": 8,
                  "gain_mode": "manual"},
        "asr": {"model": "qwen3-asr-flash"}, "enable_polish": False, "auto_paste": False,
    }))
    events = queue.Queue()
    with fixture_http() as (port, *_):
        with RustBackendClient(BINARY, data, native_library=native, on_event=events.put,
            extra_args=("--allow-test-sources", "--fixture-http", f"http://127.0.0.1:{port}",
                        "--fixture-websocket", f"ws://127.0.0.1:{port}/realtime")) as connection:
            deadline = time.monotonic() + 5
            while not connection.call("snapshot")["audio_input_status"]["warm_prepared"]:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            initial = connection.call("status")
            assert initial["state"] == "idle" and initial["core"]["pcm_bytes"] == 0
            for _ in range(2):
                connection.call("start")
                deadline = time.monotonic() + 5
                while connection.call("status")["core"]["pcm_bytes"] < 6400:
                    assert time.monotonic() < deadline
                    time.sleep(0.005)
                connection.call("finish")
                wait_event(events, "final_result")
                while connection.call("status")["state"] != "idle":
                    assert time.monotonic() < deadline
                    time.sleep(0.005)
                snapshot = connection.call("snapshot")
                audio_status = snapshot["audio_input_status"]
                assert audio_status["warm_prepared"]
                assert audio_status["last_session"]["warm_reused"]
                assert audio_status["session_startup_timing_ms"]["first_pcm_ms"] < 150
                record = snapshot["recordings"][0]
                audio_path = connection.call("play_recording", {"id": record["id"]})["path"]
                with wave.open(audio_path, "rb") as audio:
                    pcm = audio.readframes(audio.getnframes())
                samples = [int.from_bytes(pcm[i:i+2], "little", signed=True) for i in range(0, len(pcm), 2)]
                assert all(sample == 42 + i // 1280 for i, sample in enumerate(samples))


def test_stdio_http_history_paste_retry_restart_and_cleanup(tmp_path):
    events = queue.Queue()
    with fixture_http() as (port, requests, *_):
        with client(tmp_path, port, events.put) as connection:
            initial = connection.call("initialize")
            assert initial["runtime"] == "rust"
            connection.call("ui_action", {"action": "setAsrModel", "model": "qwen3-asr-flash"})
            connection.call("set_config", {"key": "enable_polish", "value": False})
            connection.call("ui_action", {"action": "addDictEntry", "term": "Rust", "aliases": ["rust"]})
            started, pcm = start_audio(connection)
            result = wait_event(events, "final_result")
            assert "Rust" in result["text"]
            paste = wait_event(events, "paste_requested")
            assert connection.call("claim_paste", {"token": paste["token"]})["text"] == result["text"]
            assert connection.call("claim_paste", {"token": paste["token"]})["cancelled"]
            record = connection.call("list_recordings")[0]
            assert record["status"] == "success" and "rust" in record["transcript"]
            path = Path(connection.call("play_recording", {"id": record["id"]})["path"])
            with wave.open(str(path), "rb") as audio:
                assert audio.readframes(audio.getnframes()) == pcm
            connection.call("retry_transcription", {"id": record["id"]})
            wait_event(events, "retry_completed")
            assert requests[-1]["model"] == "qwen3.5-omni-plus"
            pid = connection.pid
        assert connection._process.returncode == 0
        assert all(not thread.is_alive() for thread in connection._threads)
        with client(tmp_path, port) as restarted:
            assert restarted.call("get_dictionary") == [{"term": "Rust", "aliases": ["rust"]}]
            assert len(restarted.call("list_recordings")) == 1
            restarted.call("delete_recording", {"id": record["id"]})
            assert restarted.call("list_recordings") == []
            assert not path.exists()
            assert restarted.pid != pid


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS lossless codec")
def test_completed_sessions_compact_automatically_and_keep_recent_wav(tmp_path):
    events = queue.Queue()
    with fixture_http() as (port, *_):
        with client(tmp_path, port, events.put) as connection:
            connection.call("set_asr_model", {"model": "qwen3-asr-flash"})
            connection.call("set_config", {"key": "enable_polish", "value": False})
            connection.call("set_config", {"key": "auto_paste", "value": False})
            for _ in range(6):
                started = connection.call("start", {"source": {"kind": "stream"}})
                for _ in range(25):
                    connection.call("append", {"generation": started["generation"],
                        "pcm_base64": base64.b64encode(b"\x00\x10" * 640).decode()})
                connection.call("finish")
                wait_event(events, "final_result")
                deadline = time.monotonic() + 10
                while connection.call("status")["state"] != "idle":
                    assert time.monotonic() < deadline
                    time.sleep(.01)
            deadline = time.monotonic() + 10
            while connection.call("status")["history_compacting"]:
                assert time.monotonic() < deadline
                time.sleep(.01)
            records = connection.call("list_recordings")
            assert [r["storage_format"] for r in records] == ["wav"] * 3 + ["flac"] * 3
            assert all(r["status"] == "success" and r["duration_seconds"] == 1.0 for r in records)
            assert all(Path(connection.call("play_recording", {"id": r["id"]})["path"]).is_file() for r in records)


def test_stalled_http_cancel_and_rapid_new_session_never_paste_old_result(tmp_path):
    events = queue.Queue()
    with fixture_http() as (port, _, arrived, stalled, release):
        with client(tmp_path, port, events.put) as connection:
            connection.call("set_asr_model", {"model": "qwen3-asr-flash"})
            connection.call("set_config", {"key": "enable_polish", "value": False})
            stalled.set()
            started, _ = start_audio(connection)
            assert arrived.wait(3)
            before = time.monotonic()
            connection.call("cancel")
            assert time.monotonic()-before < .5
            while connection.call("status")["state"] != "idle":
                assert time.monotonic()-before < 2
                time.sleep(.01)
            stalled.clear()
            release.set()
            new, _ = start_audio(connection)
            assert new["generation"] > started["generation"]
            assert wait_event(events, "final_result")["generation"] == new["generation"]
            assert wait_event(events, "paste_requested")["generation"] == new["generation"]


def test_key_masking_validation_diagnostics_and_callback_failure(tmp_path):
    def bad_callback(_):
        raise RuntimeError("broken presentation")
    with RustBackendClient(BINARY, tmp_path, on_event=bad_callback) as connection:
        connection.call("set_config", {"key": "api_key", "value": "synthetic-secret-only"})
        connection.call("sync_form_state", {"state": {"api_key": "", "ui": {"language": "en"}}})
        snapshot = connection.call("snapshot")
        assert snapshot["api_key_set"] and snapshot["config"]["api_key"] == ""
        assert "synthetic-secret-only" not in json.dumps(snapshot)
        with pytest.raises(RustBackendError, match="not allowed"):
            connection.call("open_external", {"url": "https://example.invalid/"})
        with pytest.raises(RustBackendError, match="test sources are disabled"):
            connection.call("start", {"source": {"kind": "stream"}})
        output = tmp_path / "support.json"
        connection.call("export_diagnostics", {"path": str(output)})
        assert "synthetic-secret-only" not in output.read_text()
        with pytest.raises(RustBackendError, match="exceeds 1 MiB"):
            connection.call("set_config", {"key": "api_key", "value": "x"*1024*1024})
        assert connection.call("status")["state"] == "idle"


def test_legacy_migration_copies_live_wal_and_original_files_without_modifying_them(tmp_path):
    original = tmp_path / "python"
    original.mkdir()
    (original / "config.yaml").write_text("ui:\n  language: en\napi_key: synthetic-import-key\n")
    (original / "dictionary.yaml").write_text("entries:\n- term: Rust\n  aliases: [rust]\n")
    recordings = original / "recordings"
    recordings.mkdir()
    path = recordings / "legacy.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1,2,16000,0,"NONE","not compressed"))
        audio.writeframes(b"\0\0"*1600)
    (recordings / "recordings.json").write_text(json.dumps([{"id":"legacy","filename":"legacy.wav","status":"success","transcript":"旧录音","timestamp":"2026-09-10T01:02:03","duration_seconds":.1,"asr_model":"qwen3-asr-flash","language":"zh","mode":"realtime_long"}]))
    # An extra table isolates online-backup behavior from the queue schema.
    with sqlite3.connect(original / "dictionary-learning.sqlite3") as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE migration_probe(value TEXT)")
        live.execute("INSERT INTO migration_probe VALUES ('committed-in-wal')")
        live.commit()
        assert (original / "dictionary-learning.sqlite3-wal").stat().st_size > 0
        files = [original / "config.yaml", original / "dictionary.yaml", path, recordings / "recordings.json"]
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        destination = tmp_path / "rust"
        with RustBackendClient(BINARY, destination, extra_args=("--import-python",str(original))) as connection:
            state = connection.call("initialize")
            assert state["config"]["ui"]["language"] == "en"
            assert state["api_key_set"]
            assert state["dictionary"][0]["term"] == "Rust"
            assert state["recordings"][0]["transcript"] == "旧录音"
            connection.call("set_config", {"key": "ui.language", "value": "zh"})
        with sqlite3.connect(destination / "dictionary-learning.sqlite3") as copied:
            assert copied.execute("SELECT value FROM migration_probe").fetchone() == ("committed-in-wal",)
        assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        with RustBackendClient(BINARY, destination, extra_args=("--import-python",str(original))) as connection:
            assert connection.call("get_config")["ui"]["language"] == "zh"
            assert len(connection.call("list_recordings")) == 1


def test_eof_cancels_unfinished_capture_and_clean_imports(tmp_path):
    with fixture_http() as (port, *_):
        with client(tmp_path, port) as connection:
            connection.call("set_asr_model", {"model": "qwen3-asr-flash"})
            connection.call("start", {"source": {"kind": "stream"}})
            connection._process.stdin.close()
            connection._process.wait(timeout=4)
            assert connection._process.returncode == 0
    code = """
import json,sys
from pathlib import Path
from vocal_more.rust_client import RustBackendClient
with RustBackendClient(Path(sys.argv[1]),Path(sys.argv[2])) as client:
    assert client.call('initialize')['runtime']=='rust'
print(json.dumps([x for x in ('numpy','sounddevice','dashscope','vocal_more.config','vocal_more.app') if x in sys.modules]))
"""
    result = subprocess.run([sys.executable,"-c",code,str(BINARY),str(tmp_path / "clean")], capture_output=True,text=True,timeout=10,check=True)
    assert json.loads(result.stdout) == []


def test_paste_cancel_after_claim_is_revoked_before_platform_operation(tmp_path):
    events = queue.Queue()
    with fixture_http() as (port, *_):
        with client(tmp_path, port, events.put) as connection:
            connection.call("set_asr_model", {"model": "qwen3-asr-flash"})
            connection.call("set_config", {"key": "enable_polish", "value": False})
            start_audio(connection)
            paste = wait_event(events, "paste_requested")
            assert not connection.call("claim_paste", {"token": paste["token"]}).get("cancelled")
            connection.call("cancel")
            assert connection.call("prepare_paste_observation", {"token": paste["token"], "snapshot": None})["cancelled"]


def test_walkie_release_and_long_mode_tap_have_distinct_semantics(tmp_path):
    events = queue.Queue()
    with fixture_http() as (port, *_):
        with client(tmp_path, port, events.put) as connection:
            connection.call("set_asr_model", {"model": "qwen3-asr-flash"})
            connection.call("set_config", {"key": "enable_polish", "value": False})
            for mode in ("walkie_talkie", "realtime_long"):
                connection.call("set_mode", {"mode": mode})
                start = connection.call("hotkey_pressed", {"source": {"kind": "stream"}})
                connection.call("append", {"generation": start["generation"], "pcm_base64": base64.b64encode(b"\x00\x10"*640).decode()})
                connection.call("hotkey_released")
                if mode == "realtime_long":
                    assert connection.call("status")["latched"]
                    assert connection.call("status")["state"] in ("starting", "recording")
                    connection.call("hotkey_pressed")
                before = time.monotonic()
                while connection.call("status")["state"] != "idle":
                    assert time.monotonic()-before < 3
                    time.sleep(.01)
                assert not connection.call("status")["latched"]


def test_unexpected_service_exit_reports_failure_even_without_pending_requests(tmp_path):
    events = queue.Queue()
    with RustBackendClient(BINARY, tmp_path, on_event=events.put) as connection:
        connection.call("initialize")
        connection._process.kill()
        event = wait_event(events, "backend_disconnected")
        assert "exited" in event["message"]
        with pytest.raises(RustBackendError, match="closed"):
            connection.call("status")
    assert all(not thread.is_alive() for thread in connection._threads)
