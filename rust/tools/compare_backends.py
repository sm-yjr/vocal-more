#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""两版生产后端的隔离 A/B：真实本地 WebSocket、确定性 PCM、无系统粘贴。

uv run python rust/tools/compare_backends.py --output .build/experience-audit/performance
Python 使用生产 RPC/mode/ASR SDK/存储；只替换设备输入、数据目录和粘贴边界。
Rust 使用完整 release application binary。结果不代表真实声学或云端性能。
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import select
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def python_worker(data, wav):
    os.environ.pop("DASHSCOPE_API_KEY", None)
    os.environ["VOCAL_MORE_BENCHMARK_AUDIO_FILE"] = str(wav)
    os.environ["VOCAL_MORE_BENCHMARK_TRACE_DIR"] = str(data / "traces")
    # Fail closed if a provider fallback accidentally tries the real internet.
    original_connect = socket.socket.connect
    def loopback_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
            raise OSError("benchmark disallows non-loopback connections")
        return original_connect(sock, address)
    socket.socket.connect = loopback_only
    from vocal_more import paths
    paths.default_data_dir = lambda: data
    from vocal_more.config import Config
    Config.get_config_dir = classmethod(lambda cls: data)
    from vocal_more.config import get_config
    # Production config intentionally only accepts official wss endpoints.
    # Override solely this transport boundary after loading the isolated file.
    get_config().asr.realtime_url = (data / "fixture-url.txt").read_text()
    from vocal_more.core import recording_store
    original_store = recording_store.RecordingStore
    class IsolatedStore(original_store):
        def __init__(self, *args, **kwargs):
            super().__init__(recordings_dir=str(data / "recordings"))
    recording_store.RecordingStore = IsolatedStore
    from vocal_more.core.audio_recorder import AudioRecorder
    # Deliver already-converted PCM at the production native callback boundary.
    # Both engines receive byte-identical PCM; this is not a DSP benchmark.
    def replay(recorder, pcm):
        started = time.monotonic()
        try:
            for offset in range(0, len(pcm), 1280):
                delay = offset / 32000 - (time.monotonic() - started)
                if delay > 0 and recorder._benchmark_replay_stop.wait(delay):
                    break
                if recorder._benchmark_replay_stop.is_set():
                    break
                recorder._native_pcm_callback(pcm[offset:offset+1280], 0.1)
        finally:
            recorder._benchmark_replay_done.set()
    AudioRecorder._run_benchmark_replay = replay
    AudioRecorder.prepare_idle_capture = lambda self: False
    from vocal_more.core.keyboard_sim import KeyboardSimulator
    KeyboardSimulator.paste_text = lambda self, text: None
    from vocal_more import serve
    from vocal_more.rpc_handler import RPCHandler
    from vocal_more.application.lazy_resource import initialized_resource
    def status(handler, params):
        mode = handler._current_mode
        recorder = mode._recorder
        with recorder._lock:
            count = sum(map(len, recorder._audio_buffer))
        asr = initialized_resource(mode._asr)
        diagnostic = asr.diagnostic_snapshot() if asr else {}
        return {"state": mode.state.value, "core": {"pcm_bytes": count}, "asr": diagnostic,
                "history_compacting": handler._recording_store._compaction_pending}
    RPCHandler._handle_status = status
    RPCHandler._handle_start = RPCHandler._handle_hotkey_pressed
    RPCHandler._handle_finish = RPCHandler._handle_hotkey_pressed
    RPCHandler._handle_benchmark_prepare = lambda handler, params: {"accepted": handler._current_mode.prewarm_asr()}
    serve.main()


class Client:
    def __init__(self, args, folder):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.stderr = (folder / "stderr.log").open("w")
        env = os.environ.copy()
        for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
        self.started = time.monotonic()
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, env=env, bufsize=0)
        self.stdout = io.BufferedReader(self.process.stdout, buffer_size=64 * 1024)
        self.messages = queue.Queue()
        self.events = []
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.initial = self.call("initialize")
        self.ready_ms = (time.monotonic() - self.started) * 1000
    def _read(self):
        for line in self.stdout:
            self.messages.put((time.monotonic(), json.loads(line)))
        self.messages.put((time.monotonic(), {"exit": self.process.poll()}))
    def receive(self, timeout=20):
        at, msg = self.messages.get(timeout=timeout)
        if "exit" in msg:
            raise RuntimeError(f"backend exited: {self.folder / 'stderr.log'}")
        if "method" in msg:
            self.events.append((at, msg))
        return at, msg
    def call(self, method, params=None):
        self.sequence += 1
        request = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params or {}}
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        while True:
            _, msg = self.receive()
            if msg.get("id") == self.sequence:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]
    def event(self, method, since, timeout=15, predicate=lambda data: True):
        deadline = time.monotonic() + timeout
        while True:
            found = next(((at, m["params"]) for at, m in self.events
                          if at >= since and m["method"] == method and predicate(m["params"])), None)
            if found:
                return found
            self.receive(max(.01, deadline - time.monotonic()))
    def close(self):
        started = time.monotonic()
        try:
            self.call("shutdown")
            self.process.stdin.close()
            self.process.wait(10)
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(5)
            self.reader.join(2)
            self.stdout.close()
            self.stderr.close()
        return (time.monotonic()-started)*1000


def footprint(client, label):
    output = subprocess.run(["/usr/bin/vmmap", "-summary", str(client.process.pid)],
                            capture_output=True, text=True, timeout=15, check=True).stdout
    (client.folder / (label + "-vmmap.txt")).write_text(output)
    match = re.search(r"Physical footprint:\s*([\d.]+)([KMGT]?)", output)
    assert match
    return float(match[1]) * {"": 1/1048576, "K": 1/1024, "M": 1, "G": 1024, "T": 1048576}[match[2]]


class Fixture:
    def __init__(self, handshake_ms=0, reject_first=False, early_segment=False):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.socket.settimeout(.2)
        self.endpoint = f"ws://127.0.0.1:{self.socket.getsockname()[1]}/realtime"
        self.handshake_ms = handshake_ms
        self.reject_first = reject_first
        self.early_segment = early_segment
        self.connection_count = 0
        self.records = []
        self.ready_count = 0
        self.errors = []
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
    def run(self):
        while self.running:
            try:
                conn, _ = self.socket.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()
    def handle(self, conn):
        from measure_host import read_exact, send_frame
        def send(event):
            send_frame(conn, json.dumps(event).encode())
        try:
            conn.settimeout(15)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header += read_exact(conn, 1)
                assert len(header) < 16384
            headers = dict(line.split(":", 1) for line in header.decode().split("\r\n")[1:] if ":" in line)
            headers = {key.lower(): value.strip() for key, value in headers.items()}
            assert headers.get("authorization", "") in ("", "Bearer benchmark-placeholder")
            self.connection_count += 1
            if self.reject_first and self.connection_count == 1:
                conn.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            key = headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(hashlib.sha1(key.encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
            send({"type": "session.created", "session": {"id": "fixture"}})
            audio = bytearray()
            partial = False
            completed = False
            while True:
                first, second = read_exact(conn, 2)
                opcode = first & 15
                size = second & 127
                if size == 126: size = struct.unpack("!H", read_exact(conn, 2))[0]
                elif size == 127: size = struct.unpack("!Q", read_exact(conn, 8))[0]
                assert size < 128*1024 and second & 128
                mask = read_exact(conn, 4)
                payload = read_exact(conn, size)
                payload = bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
                if opcode == 8:
                    send_frame(conn, payload, 8)
                    break
                if opcode == 9:
                    send_frame(conn, payload, 10)
                    continue
                event = json.loads(payload)
                kind = event["type"]
                if kind == "session.update":
                    time.sleep(self.handshake_ms/1000)
                    send({"type": "session.updated", "session": {"id": "fixture"}})
                    self.ready_count += 1
                elif kind == "input_audio_buffer.append":
                    audio.extend(base64.b64decode(event["audio"]))
                    if not partial:
                        partial = True
                        send({"type": "conversation.item.input_audio_transcription.text", "text": "性能测试", "stash": "", "item_id": "one"})
                    if self.early_segment and len(audio) >= 3200 and not completed:
                        completed = True
                        send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "one", "transcript": "性能测试 Rust Python。"})
                elif kind == "input_audio_buffer.commit":
                    self.records.append({"pcm_bytes": len(audio), "sha256": hashlib.sha256(audio).hexdigest(),
                                         "commit_received_at": time.monotonic()})
                    time.sleep(.02)
                    send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "one", "transcript": "性能测试 Rust Python。"})
                elif kind == "response.create":
                    send({"type": "response.text.done", "text": "性能测试 Rust Python。"})
                    send({"type": "response.done", "response": {"status": "completed"}})
                elif kind == "session.finish":
                    send({"type": "session.finished"})
                else:
                    raise AssertionError(kind)
        except (EOFError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as error:
            self.errors.append(repr(error))
        finally:
            conn.close()
    def close(self):
        self.running = False
        self.thread.join(2)
        self.socket.close()


def summary(values):
    ordered = sorted(values)
    return {"p50": statistics.median(ordered), "p95": ordered[max(0, int(len(ordered)*.95+.999)-1)], "min": min(ordered), "max": max(ordered)}


def measure(kind, folder, wav, rounds, handshake_ms, warm, rust_binary=None):
    folder.mkdir(parents=True, exist_ok=True)
    fixture = Fixture(handshake_ms)
    data = folder / "data"
    data.mkdir()
    config = {"api_key": "benchmark-placeholder", "default_mode": "realtime_long",
        "asr": {"model": "qwen3.5-omni-flash-realtime", "realtime_url": ""},
        "enable_polish": False, "auto_paste": False,
        "audio": {"blocksize": 1280, "capture_backend": "low_latency", "gain_mode": "manual",
                  "gain": 1, "highpass_filter": False, "soft_limiter": False},
        "dictionary_learning": {"enabled": False}, "ui": {"onboarding_completed": True}}
    (data / "config.yaml").write_text(json.dumps(config))
    (data / "fixture-url.txt").write_text(fixture.endpoint)
    if kind == "python":
        args = [sys.executable, "-u", str(Path(__file__).resolve()), "--python-worker", str(data), "--wav", str(wav)]
    else:
        args = [str(rust_binary or ROOT / ".build/rust-host/vocal-more-backend"), "--data-dir", str(data),
                "--allow-test-sources", "--fixture-http", "http://127.0.0.1:1", "--fixture-websocket", fixture.endpoint]
    client = Client(args, folder)
    assert client.initial["config"]["asr"]["model"] == config["asr"]["model"], client.initial["config"]["asr"]
    assert client.initial["config"]["audio"]["gain"] == 1
    result = {"kind": kind, "rounds": rounds, "handshake_ms": handshake_ms, "python_prewarm": kind == "python" and warm,
              "startup_ms": client.ready_ms, "idle_mib": footprint(client, "idle"), "sessions": [],
              "fixture_tcp_nodelay": True}
    try:
        if kind == "python" and warm:
            before = fixture.ready_count
            client.call("benchmark_prepare")
            deadline = time.monotonic() + 10
            while fixture.ready_count <= before:
                assert time.monotonic() < deadline, "prewarm timed out"
                time.sleep(.01)
        with wave.open(str(wav), "rb") as audio:
            expected = audio.readframes(audio.getnframes())
        from macos_process_stats import counters
        work_cpu_before = counters(client.process.pid)
        for index in range(rounds):
            cpu_before = counters(client.process.pid)
            started = time.monotonic()
            client.call("start", {"source": {"kind": "wav", "path": str(wav), "paced": True}} if kind == "rust" else {})
            feedback_at, _ = client.event("state_changed", started)
            first_audio_at, _ = client.event("audio_level", started, predicate=lambda d: d.get("rms", 0) > 0)
            partial_at, _ = client.event("partial_result", started)
            deadline = time.monotonic() + 10
            while client.call("status")["core"]["pcm_bytes"] < len(expected):
                assert time.monotonic() < deadline, "PCM did not complete"
                time.sleep(.005)
            state = client.call("status")["state"]
            # Reject EOF races rather than mislabelling an automatic finish as
            # latency measured from the explicit stop request.
            assert state in ("starting", "recording"), state
            stopped = time.monotonic()
            client.call("finish")
            final_at, final = client.event("final_result", started)
            while client.call("status")["state"] != "idle":
                assert time.monotonic() < deadline, "idle timed out"
                time.sleep(.005)
            assert final["text"] == "性能测试 Rust Python。", final
            cpu_after = counters(client.process.pid)
            result["sessions"].append({
                "process_cpu_ms": (cpu_after["cpu_seconds"] - cpu_before["cpu_seconds"]) * 1000,
                "interrupt_wakeups": cpu_after["interrupt_wakeups"] - cpu_before["interrupt_wakeups"],
                "feedback_ms": (feedback_at-started)*1000,
                "first_audio_event_ms": (first_audio_at-started)*1000,
                "first_partial_ms": (partial_at-started)*1000,
                "finish_to_result_ms": (final_at-stopped)*1000,
                "finish_to_wire_commit_ms": (fixture.records[index]["commit_received_at"]-stopped)*1000,
                "wire_commit_to_result_ms": (final_at-fixture.records[index]["commit_received_at"])*1000})
            if index == 0:
                result["after_first_mib"] = footprint(client, "after-first")
            if kind == "python" and warm:
                # Existing Python warm keeper opens the next clean session.
                time.sleep(handshake_ms/1000+.1)
        maintenance_deadline = time.monotonic() + 15
        while client.call("status").get("history_compacting", False):
            assert time.monotonic() < maintenance_deadline, "history maintenance timed out"
            time.sleep(.01)
        work_cpu_after = counters(client.process.pid)
        result["cpu_ms_including_maintenance_per_session"] = sum(work_cpu_after[key] - work_cpu_before[key]
            for key in ("cpu_seconds", "child_cpu_seconds")) * 1000 / rounds
        result["cpu_scope"] = "owned backend plus reaped codec children; all sessions and maintenance; excludes benchmark driver"
        result["after_repeated_mib"] = footprint(client, "after-repeated")
        audio_files = list((data / "recordings").glob("*.wav")) + list((data / "recordings").glob("*.flac"))
        result["archive"] = {"wav_count": sum(f.suffix == ".wav" for f in audio_files),
            "flac_count": sum(f.suffix == ".flac" for f in audio_files),
            "stored_bytes": sum(f.stat().st_size for f in audio_files)}
        for metric in result["sessions"][0]:
            result[metric] = summary([s[metric] for s in result["sessions"]])
        result["wire_audio"] = fixture.records
        assert len(fixture.records) == rounds, fixture.records
        assert all(r["pcm_bytes"] == len(expected) and r["sha256"] == hashlib.sha256(expected).hexdigest() for r in fixture.records)
        assert not fixture.errors, fixture.errors
        result["pcm_verified"] = True
        with tempfile.TemporaryDirectory(prefix="vocal-more-archive-check-") as decoded_dir:
            decoded = Path(decoded_dir) / "decoded.wav"
            for audio_file in audio_files:
                source = audio_file
                if source.suffix == ".flac":
                    subprocess.run(["/usr/bin/afconvert", str(source), str(decoded),
                        "-f", "WAVE", "-d", "LEI16@16000"], check=True, capture_output=True)
                    source = decoded
                with wave.open(str(source), "rb") as archived:
                    assert archived.getparams()[:3] == (1, 2, 16000)
                    assert archived.readframes(archived.getnframes()) == expected, audio_file
                if decoded.exists():
                    decoded.unlink()
        assert len(audio_files) == min(rounds, 30)
        result["archive_pcm_verified"] = True
    finally:
        result["fixture_errors"] = fixture.errors
        result["shutdown_ms"] = client.close()
        fixture.close()
        (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--handshake-ms", type=int, default=100)
    parser.add_argument("--seconds", type=float, default=1)
    parser.add_argument("--kind", choices=("python", "rust", "both"), default="both")
    parser.add_argument("--python-worker", type=Path)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--rust-binary", type=Path)
    parser.add_argument("--order", choices=("python-first", "rust-first"), default="python-first")
    args = parser.parse_args()
    if args.python_worker:
        return python_worker(args.python_worker, args.wav)
    assert args.output and 1 <= args.rounds <= 100 and .2 <= args.seconds <= 30
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    wav = args.output / "deterministic.wav"
    pcm = b"".join(struct.pack("<h", (n*97 % 12001)-6000) for n in range(int(16000*args.seconds)))
    with wave.open(str(wav), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    result = {"scope": "production backend, loopback ASR, paced PCM, no GUI/microphone/system paste",
              "rpc_reader": "bounded buffered stdout; same for both backends",
              "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "seconds": args.seconds, "runs": []}
    order = ["python", "rust"] if args.order == "python-first" else ["rust", "python"]
    for kind in (order if args.kind == "both" else [args.kind]):
        result["runs"].append(measure(kind, args.output / kind, wav, args.rounds, args.handshake_ms, True,
                                    args.rust_binary.resolve() if args.rust_binary else None))
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
