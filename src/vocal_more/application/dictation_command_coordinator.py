"""Serial command execution for dictation control paths."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class _WorkItem:
    callback: Callable[[], Any]
    command_name: str
    sequence: int = 0
    done: Optional[threading.Event] = None
    result_box: Optional[dict[str, Any]] = None


_STOP = object()


class DictationCommandCoordinator:
    """Run dictation control commands on one dedicated serial worker."""

    def __init__(self, *, thread_name: str = "vocal-more-dictation") -> None:
        self._thread_name = thread_name
        self._queue: queue.Queue[Any] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_ident: Optional[int] = None
        self._lock = threading.Lock()
        self._next_sequence = 0
        self._closed = False
        self._ensure_worker()

    def submit(
        self,
        callback: Callable[[], Any],
        *,
        command_name: Optional[str] = None,
    ) -> None:
        """Queue a command for asynchronous serial execution."""
        self._enqueue(
            _WorkItem(
                callback=callback,
                command_name=self._resolve_command_name(callback, command_name),
            )
        )

    def call(
        self,
        callback: Callable[[], Any],
        *,
        command_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Run a command on the coordinator thread and wait for the result."""
        if threading.get_ident() == self._worker_ident:
            return callback()

        done = threading.Event()
        result_box: dict[str, Any] = {}
        self._enqueue(
            _WorkItem(
                callback=callback,
                command_name=self._resolve_command_name(callback, command_name),
                done=done,
                result_box=result_box,
            )
        )

        if not done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for dictation command")

        error = result_box.get("error")
        if error is not None:
            raise error
        return result_box.get("result")

    def close(self, *, timeout: float = 1.0) -> None:
        """Stop the coordinator worker after draining queued commands."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker
            self._worker = None

        if worker is None:
            return

        self._queue.put(_STOP)
        worker.join(timeout=timeout)

    def _enqueue(self, item: _WorkItem) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("DictationCommandCoordinator is closed")
            self._next_sequence += 1
            item.sequence = self._next_sequence
        self._ensure_worker()
        self._queue.put(item)
        print(
            "[DictationCoordinator] "
            f"queued seq={item.sequence} command={item.command_name} "
            f"pending={self._queue.qsize()}"
        )

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("DictationCommandCoordinator is closed")
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_loop,
                name=self._thread_name,
                daemon=True,
            )
            self._worker.start()

    def _run_loop(self) -> None:
        self._worker_ident = threading.get_ident()
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    break
                self._execute(item)
        finally:
            self._worker_ident = None

    def _execute(self, item: _WorkItem) -> None:
        print(
            "[DictationCoordinator] "
            f"running seq={item.sequence} command={item.command_name} "
            f"pending={self._queue.qsize()}"
        )
        try:
            result = item.callback()
        except Exception as exc:
            if item.result_box is not None:
                item.result_box["error"] = exc
            else:
                print(
                    "[DictationCoordinator] "
                    f"failed seq={item.sequence} command={item.command_name} error={exc}"
                )
        else:
            if item.result_box is not None:
                item.result_box["result"] = result
            print(
                "[DictationCoordinator] "
                f"completed seq={item.sequence} command={item.command_name}"
            )
        finally:
            if item.done is not None:
                item.done.set()

    @staticmethod
    def _resolve_command_name(
        callback: Callable[[], Any],
        explicit_name: Optional[str],
    ) -> str:
        if explicit_name:
            return explicit_name
        return getattr(callback, "__name__", "anonymous_command")
