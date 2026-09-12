#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""macOS release 后端测量；Python 仅是进程外控制器/本地协议夹具。

使用系统 Python 3.9+ 的标准库。结果与合成音频写入显式 --output；不访问
麦克风、真实 API Key 或云端。每个 Rust 目标进程独立计量，不含控制器。
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import ssl
import struct
import subprocess
import threading
import time
import wave


def command(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=15)
    return result.stdout + result.stderr


class RPC:
    def __init__(self, args, folder, env):
        self.folder = folder
        self.stderr = (folder / "stderr.txt").open("w")
        self.started = time.monotonic()
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr, bufsize=0, env=env)
        self.buffer = b""
        self.sequence = 0
        self.initial = self.call("initialize")
        self.ready_ms = (time.monotonic() - self.started) * 1000

    def call(self, method, params=None, allow_error=False):
        self.sequence += 1
        request = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params or {}}
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        deadline = time.monotonic() + 15
        while True:
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise TimeoutError(method)
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError("host exited during " + method)
                self.buffer += data
            line, self.buffer = self.buffer.split(b"\n", 1)
            reply = json.loads(line)
            if reply.get("id") == self.sequence:
                if "error" in reply and not allow_error:
                    raise RuntimeError(reply["error"])
                return reply if allow_error else reply["result"]

    def terminal(self, seconds=60, sample=None):
        deadline = time.monotonic() + seconds
        next_sample = 0
        while time.monotonic() < deadline:
            state = self.call("status")
            if state["phase"] in ("completed", "cancelled", "failed"):
                return state
            if sample and time.monotonic() >= next_sample:
                sample(state)
                next_sample = time.monotonic() + 0.5
            time.sleep(0.01)
        raise TimeoutError("terminal state")

    def close(self, explicit=True):
        start = time.monotonic()
        if explicit:
            self.call("shutdown")
        self.process.stdin.close()
        self.process.wait(timeout=8)
        self.stderr.close()
        assert self.process.returncode == 0
        return (time.monotonic() - start) * 1000


def read_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        block = connection.recv(count - len(data))
        if not block:
            raise EOFError()
        data.extend(block)
    return bytes(data)


def send_frame(connection, payload, opcode=1):
    length = len(payload)
    header = bytes([128 | opcode])
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += b"\x7e" + struct.pack("!H", length)
    else:
        header += b"\x7f" + struct.pack("!Q", length)
    connection.sendall(header + payload)


class Fixture:
    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.socket.settimeout(0.2)
        self.endpoint = "ws://127.0.0.1:%d/realtime" % self.socket.getsockname()[1]
        self.records = []
        self.errors = []
        self.active = 0
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while self.running:
            try:
                connection, _ = self.socket.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        self.active += 1
        try:
            connection.settimeout(15)
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                header += read_exact(connection, 1)
                assert len(header) < 8192
            headers = dict(line.split(":", 1) for line in header.decode().split("\r\n")[1:] if ":" in line)
            headers = {key.lower(): value.strip() for key, value in headers.items()}
            assert "authorization" not in headers
            key = headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(hashlib.sha1(key.encode()).digest()).decode()
            connection.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
            digest = hashlib.sha256()
            total = 0
            last_size = 0
            committed = False
            while True:
                first, second = read_exact(connection, 2)
                assert first & 128  # The host sends complete small frames.
                opcode = first & 15
                size = second & 127
                if size == 126:
                    size = struct.unpack("!H", read_exact(connection, 2))[0]
                elif size == 127:
                    size = struct.unpack("!Q", read_exact(connection, 8))[0]
                assert size <= 128 * 1024 and second & 128
                mask = read_exact(connection, 4)
                payload = read_exact(connection, size)
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                if opcode == 8:
                    send_frame(connection, payload, 8)
                    break
                if opcode == 9:
                    send_frame(connection, payload, 10)
                    continue
                event = json.loads(payload)
                kind = event["type"]
                if kind == "session.update":
                    assert event["session"]["modalities"] == ["text"]
                    send_frame(connection, b'{"type":"session.updated"}')
                elif kind == "input_audio_buffer.append":
                    assert not committed
                    audio = base64.b64decode(event["audio"], validate=True)
                    assert len(audio) <= 3200
                    digest.update(audio)
                    total += len(audio)
                    last_size = len(audio)
                elif kind == "input_audio_buffer.commit":
                    assert not committed
                    committed = True
                elif kind == "response.create":
                    assert committed
                    self.records.append({"pcm_bytes": total, "sha256": digest.hexdigest(), "last_frame_bytes": last_size})
                    send_frame(connection, b'{"type":"response.text.done","text":"fixture complete"}')
                    send_frame(connection, b'{"type":"response.done","response":{"status":"completed"}}')
                else:
                    raise AssertionError(kind)
        except (EOFError, ConnectionResetError, BrokenPipeError):
            pass  # Expected for cancellation.
        except Exception as error:
            self.errors.append(repr(error))
        finally:
            connection.close()
            self.active -= 1

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.socket.close()


def snapshot(rpc, label):
    pid = rpc.process.pid
    summary = command(["/usr/bin/vmmap", "-summary", str(pid)])
    (rpc.folder / (label + "-vmmap.txt")).write_text(summary)
    def mib(pattern):
        match = re.search(pattern + r"\s*([\d.]+)([KMGT]?)", summary)
        if not match:
            raise RuntimeError("vmmap missing footprint: " + summary[:160])
        return float(match[1]) * {"": 1 / 1048576, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1048576}[match[2]]
    ps = command(["/bin/ps", "-p", str(pid), "-o", "rss=,%cpu=,time="]).split()
    threads = command(["/bin/ps", "-M", "-p", str(pid), "-o", "pid="]).splitlines()
    return {"label": label, "elapsed_seconds": time.monotonic() - rpc.started,
            "footprint_mib": mib(r"Physical footprint:"),
            "peak_footprint_mib": mib(r"Physical footprint \(peak\):"),
            "rss_mib": float(ps[0]) / 1024, "cpu_percent": float(ps[1]),
            "cpu_time": ps[2], "threads": len(threads)}


def make_audio(path, seconds):
    digest = hashlib.sha256()
    # Generated waveform only. Bounded generator, no user audio or giant array.
    block = b"".join(struct.pack("<h", (n * 97 % 16000) - 8000) for n in range(16000))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        for _ in range(seconds):
            output.writeframesraw(block)
            digest.update(block)
    return digest.hexdigest()


def tls_rejection_probe(rpc, certificate, private_key):
    """Exercise rustls initialization and certificate rejection on local TLS only."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    observed = []
    def serve():
        try:
            connection, _ = listener.accept()
            connection.settimeout(5)
            with connection:
                try:
                    with context.wrap_socket(connection, server_side=True) as secure:
                        observed.append(secure.recv(1024).decode(errors="replace"))
                except ssl.SSLError:
                    observed.append("certificate rejected before HTTP authorization")
        finally:
            listener.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    rpc.call("start", {"asr": {"endpoint": "wss://127.0.0.1:%d/realtime" % listener.getsockname()[1]}})
    state = rpc.terminal()
    thread.join(timeout=6)
    assert state["phase"] == "failed" and not state["transcript"]
    assert state["error"] == "WebSocket connection or TLS handshake failed", state
    assert observed == ["certificate rejected before HTTP authorization"], observed
    return {"status": state, "server_observed": observed}


def verify_file(folder, state, digest):
    path = folder / "store" / (state["recording_id"] + ".wav")
    actual = hashlib.sha256()
    with wave.open(str(path), "rb") as recording:
        while True:
            block = recording.readframes(32768)
            if not block:
                break
            actual.update(block)
    assert actual.hexdigest() == digest


def measure_rust(args, label, native, fixture, short_path, long_path, long_hash):
    folder = args.output / label
    folder.mkdir()
    env = os.environ.copy()
    # This non-secret fixture value only enables a local TLS certificate test.
    # Plain WS receives no Authorization; rejected TLS sends no HTTP request.
    env["DASHSCOPE_API_KEY"] = "local-fixture-not-a-real-api-key"
    invocation = [str(args.binary), "--data-dir", str(folder / "store")]
    if native:
        invocation += ["--native-library", str(args.native_library)]
    rpc = RPC(invocation, folder, env)
    result = {"pid": rpc.process.pid, "ready_ms": rpc.ready_ms, "initialize": rpc.initial, "samples": []}
    try:
        for seconds in (1, 5, 15, 30):
            time.sleep(max(0, rpc.started + seconds - time.monotonic()))
            result["samples"].append(snapshot(rpc, "idle-%ds" % seconds))
        asr = {"endpoint": fixture.endpoint}
        short_request = {"source": {"kind": "wav", "path": str(short_path), "paced": False}, "asr": asr}
        rpc.call("start", short_request)
        assert rpc.terminal()["phase"] == "completed"
        result["samples"].append(snapshot(rpc, "after-first-ws"))
        for _ in range(50):
            rpc.call("start", short_request)
            assert rpc.terminal()["phase"] == "completed"
        result["samples"].append(snapshot(rpc, "after-50-ws"))
        for mode in ("file", "websocket"):
            request = {"source": {"kind": "wav", "path": str(long_path), "paced": False}}
            if mode == "websocket":
                request["asr"] = asr
            started = time.monotonic()
            rpc.call("start", request)
            progress = []
            state = rpc.terminal(seconds=180, sample=lambda state: progress.append({"pcm_bytes": state["pcm_bytes"], **snapshot(rpc, "%s-progress-%03d" % (mode, len(progress)))}))
            assert state["phase"] == "completed", state
            assert state["pcm_bytes"] == args.seconds * 32000
            verify_file(folder, state, long_hash)
            if mode == "websocket":
                assert fixture.records[-1]["sha256"] == long_hash
            result[mode] = {"elapsed_seconds": time.monotonic() - started, "status": state, "progress": progress}
            result["samples"].append(snapshot(rpc, "after-long-" + mode))
        generation = rpc.call("start", {"source": {"kind": "wav", "path": str(long_path), "paced": True}, "asr": asr})["generation"]
        time.sleep(0.2)
        started = time.monotonic()
        rpc.call("cancel", {"generation": generation})
        state = rpc.terminal()
        assert state["phase"] == "cancelled" and not state["transcript"]
        result["cancel_ms"] = (time.monotonic() - started) * 1000
        result["samples"].append(snapshot(rpc, "after-cancel"))
        result["tls_rejection"] = tls_rejection_probe(rpc, args.output / "fixture-cert.pem", args.output / "fixture-key.pem")
        result["samples"].append(snapshot(rpc, "after-tls-rejection"))
        time.sleep(10)
        result["samples"].append(snapshot(rpc, "used-idle-10s"))
        maps = command(["/usr/bin/vmmap", str(rpc.process.pid)])
        (folder / "images.txt").write_text(maps)
        result["python_images_found"] = bool(re.search(r"Python.framework|libpython|_objc.*so|site-packages", maps, re.I))
        assert not result["python_images_found"]
        children = command(["/usr/bin/pgrep", "-P", str(rpc.process.pid)]).strip()
        result["child_pids"] = children.splitlines()
        assert not children
        connections = command(["/usr/sbin/lsof", "-nP", "-a", "-p", str(rpc.process.pid), "-iTCP"])
        (folder / "idle-connections.txt").write_text(connections)
        result["idle_tcp_connections"] = connections.strip()
        assert not connections.strip()
        (folder / "open-files.txt").write_text(command(["/usr/sbin/lsof", "-nP", "-p", str(rpc.process.pid)]))
        result["shutdown_ms"] = rpc.close()
        assert "panicked" not in (folder / "stderr.txt").read_text()
        return result
    finally:
        if rpc.process.poll() is None:
            rpc.process.kill()
            rpc.process.wait()


def measure_python(args):
    folder = args.output / "python-reference"
    folder.mkdir()
    env = os.environ.copy()
    env.pop("DASHSCOPE_API_KEY", None)
    if args.python_home:
        env["PYTHONHOME"] = str(args.python_home)
        env["PYTHONPATH"] = os.pathsep.join(str(args.python_home / path) for path in ("lib/python312.zip", "lib/python3.12", "lib/python3.12/lib-dynload"))
    rpc = RPC([str(args.python_runtime), str(Path(__file__).with_name("python_reference.py")), str(folder / "data")], folder, env)
    result = {"pid": rpc.process.pid, "ready_ms": rpc.ready_ms, "version": rpc.initial["version"], "samples": []}
    try:
        for seconds in (1, 5, 15, 30):
            time.sleep(max(0, rpc.started + seconds - time.monotonic()))
            result["samples"].append(snapshot(rpc, "idle-%ds" % seconds))
        (folder / "open-files.txt").write_text(command(["/usr/sbin/lsof", "-nP", "-p", str(rpc.process.pid)]))
        result["shutdown_ms"] = rpc.close(explicit=False)
        return result
    finally:
        if rpc.process.poll() is None:
            rpc.process.kill()
            rpc.process.wait()


def crash_recovery(args):
    folder = args.output / "crash-recovery"
    folder.mkdir()
    invocation = [str(args.binary), "--data-dir", str(folder / "store")]
    env = os.environ.copy()
    env.pop("DASHSCOPE_API_KEY", None)
    rpc = RPC(invocation, folder, env)
    state = rpc.call("start")
    generation = state["generation"]
    pcm = bytes([3]) * 1280
    try:
        for _ in range(200):
            while True:
                response = rpc.call("append", {"generation": generation, "pcm_base64": base64.b64encode(pcm).decode()}, allow_error=True)
                if "error" not in response:
                    break
                assert "queue full" in response["error"]["message"]
                time.sleep(0.005)
        deadline = time.monotonic() + 3
        while rpc.call("status")["pcm_bytes"] != 256000:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        rpc.process.kill()  # This deliberately terminates only the created fixture host.
        rpc.process.wait(timeout=5)
        rpc.stderr.close()
        recovered = RPC(invocation, folder, env)
        try:
            record = recovered.call("recordings")[0]
            assert record["status"] == "interrupted" and record["transcript"] == ""
            assert 0 < record["pcm_bytes"] <= 256000
            with wave.open(str(folder / "store" / record["filename"]), "rb") as audio:
                assert audio.readframes(256000) == bytes([3]) * record["pcm_bytes"]
            recovered.close()
            return {"accepted_pcm_bytes": 256000, "recovered": record}
        finally:
            if recovered.process.poll() is None:
                recovered.process.kill()
                recovered.process.wait()
    finally:
        if rpc.process.poll() is None:
            rpc.process.kill()
            rpc.process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--native-library", type=Path)
    parser.add_argument("--python-runtime", type=Path)
    parser.add_argument("--python-home", type=Path)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.binary = args.binary.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    long_path = args.output / "synthetic-long.wav"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(args.output / "fixture-key.pem"), "-out", str(args.output / "fixture-cert.pem"),
                    "-days", "1", "-subj", "/CN=localhost"], check=True, capture_output=True)
    long_hash = make_audio(long_path, args.seconds)
    short_path = args.output / "synthetic-short.wav"
    make_audio(short_path, 1)
    result = {"host": command(["/usr/bin/sw_vers"]), "hardware": command(["/usr/sbin/sysctl", "hw.model", "hw.memsize"]),
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "audio_seconds": args.seconds, "pcm_sha256": long_hash, "runs": {}}
    fixture = Fixture()
    try:
        for label, native in [("rust-core", False)] + ([("rust-native-loaded", True)] if args.native_library else []):
            print("Measuring " + label, flush=True)
            result["runs"][label] = measure_rust(args, label, native, fixture, short_path, long_path, long_hash)
            (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if args.python_runtime:
            print("Measuring Python RPC reference", flush=True)
            result["runs"]["python-reference"] = measure_python(args)
        result["crash_recovery"] = crash_recovery(args)
        assert not fixture.errors, fixture.errors
        assert fixture.active == 0
        result["fixture_records"] = fixture.records
        result["fixture_errors"] = fixture.errors
        (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(str(args.output / "results.json"), flush=True)
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
