"""Bounded JSON-RPC transport for the Rust application service.

This module has no provider, audio, configuration or dictionary implementation.
Callbacks run on the reader thread; desktop clients marshal them to AppKit.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future
import io
import json
from pathlib import Path
import queue
import subprocess
import threading
from typing import Callable


MAX_RESPONSE_BYTES = 40 * 1024 * 1024


class RustBackendError(RuntimeError):
    pass


class RustBackendClient:
    def __init__(self, binary: Path, data_dir: Path, *, native_library: Path | None = None,
                 on_event: Callable[[dict], None] | None = None, extra_args: tuple[str, ...] = ()):
        command = [str(binary.resolve()), "--data-dir", str(data_dir.resolve())]
        if native_library is not None:
            command += ["--native-library", str(native_library.resolve())]
        command += list(extra_args)
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=0)
        # Keep writes unbuffered, but never read a pipe one byte at a time.
        # BufferedReader returns on newline/EOF without waiting to fill 64 KiB.
        self._stdout_reader = io.BufferedReader(self._process.stdout, buffer_size=64 * 1024)
        self._stderr_reader = io.BufferedReader(self._process.stderr, buffer_size=8192)
        self._on_event = on_event
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._sequence = 0
        self._closed = False
        self._closing = False
        self._outgoing: queue.Queue = queue.Queue(maxsize=64)
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self._threads = [threading.Thread(target=target, name=name, daemon=True) for target, name in
                         ((self._write, "rust-rpc-write"), (self._read, "rust-rpc-read"),
                          (self._read_errors, "rust-rpc-errors"))]
        for thread in self._threads:
            thread.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    def request(self, method: str, params: dict | None = None) -> Future:
        future = Future()
        with self._lock:
            if self._closed or (self._closing and method != "shutdown"):
                future.set_exception(RustBackendError("Rust backend is closed"))
                return future
            if len(self._pending) >= 128:
                future.set_exception(RustBackendError("Rust backend request queue is full"))
                return future
            self._sequence += 1
            identifier = self._sequence
            payload = json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method,
                                  "params": params or {}}, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(payload) > 1024 * 1024:
                future.set_exception(RustBackendError("Rust backend request exceeds 1 MiB"))
                return future
            self._pending[identifier] = future
            try:
                self._outgoing.put_nowait(payload)
            except queue.Full:
                self._pending.pop(identifier, None)
                future.set_exception(RustBackendError("Rust backend writer queue is full"))
        return future

    def call(self, method: str, params: dict | None = None, *, timeout: float = 10):
        return self.request(method, params).result(timeout=timeout)

    def _write(self):
        try:
            while True:
                payload = self._outgoing.get()
                if payload is None:
                    return
                # Unbuffered binary pipe writes can be partial.
                remaining = memoryview(payload)
                while remaining:
                    count = self._process.stdin.write(remaining)
                    if not count:
                        raise BrokenPipeError("Rust backend input closed")
                    remaining = remaining[count:]
        except (OSError, ValueError) as error:
            self._fail(str(error))

    def _read(self):
        try:
            while True:
                line = self._stdout_reader.readline(MAX_RESPONSE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
                    raise RustBackendError("Rust backend response exceeds protocol bounds")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RustBackendError("Rust backend sent a non-object message")
                if "id" in value:
                    with self._lock:
                        future = self._pending.pop(value["id"], None)
                    if future is not None and not future.done():
                        if "error" in value:
                            future.set_exception(RustBackendError(value["error"].get("message", "Backend request failed")))
                        else:
                            future.set_result(value.get("result"))
                elif isinstance(value.get("method"), str) and self._on_event is not None:
                    try:
                        self._on_event(value)
                    except Exception:
                        # A presentation callback must not strand pending RPCs.
                        self.stderr_tail.append("Frontend event callback failed")
        except (OSError, ValueError, RustBackendError) as error:
            self._fail(str(error))
        finally:
            self._fail("Rust backend exited")

    def _read_errors(self):
        while True:
            line = self._stderr_reader.readline(4096)
            if not line:
                return
            self.stderr_tail.append(line.decode("utf-8", errors="replace").rstrip())

    def _fail(self, message: str):
        with self._lock:
            unexpected = not self._closed and not self._closing
            self._closed = True
            pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(RustBackendError(message))
        if unexpected and self._on_event is not None:
            try:
                self._on_event({"method": "backend_disconnected", "params": {"message": message}})
            except Exception:
                pass

    def close(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
        try:
            if self._process.poll() is None:
                self.call("shutdown", timeout=8)
        except (RustBackendError, TimeoutError, OSError):
            pass
        finally:
            try:
                self._process.stdin.close()
            except OSError:
                pass
            try:
                self._outgoing.put_nowait(None)
            except queue.Full:
                pass
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            self._fail("Rust backend is closed")
            for thread in self._threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=1)
            self._stdout_reader.close()
            self._stderr_reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def backend_paths():
    from .paths import bundled_resource_path
    bundled = bundled_resource_path("rust-backend", "vocal-more-backend")
    development = Path(__file__).resolve().parents[2] / ".build/rust-host/vocal-more-backend"
    binary = bundled if bundled.is_file() else development
    native = binary.parent / "libvocal_more_audio.dylib"
    if not native.is_file():
        native = bundled_resource_path("..").resolve() / "Frameworks/libvocal_more_audio.dylib"
    return binary, native
