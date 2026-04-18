"""Small helpers for long-lived background executors."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Callable, Generic, Optional, TypeVar


T = TypeVar("T")


class TaskHandle(Generic[T]):
    """Thread-like wrapper around a Future for simple fire-and-forget tasks."""

    def __init__(self, future: Future[T]) -> None:
        self._future = future

    def join(self, timeout: Optional[float] = None) -> Optional[T]:
        """Wait for completion and return the task result when available."""
        return self._future.result(timeout=timeout)

    def done(self) -> bool:
        return self._future.done()

    def cancel(self) -> bool:
        return self._future.cancel()

    def result(self, timeout: Optional[float] = None) -> T:
        return self._future.result(timeout=timeout)


class BackgroundExecutor:
    """Tiny wrapper over ThreadPoolExecutor with an explicit close lifecycle."""

    def __init__(
        self,
        *,
        max_workers: int,
        thread_name_prefix: str,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._closed = False
        self._lock = threading.Lock()

    def submit(self, callback: Callable[..., T], *args, **kwargs) -> TaskHandle[T]:
        with self._lock:
            if self._closed:
                raise RuntimeError("background executor is closed")
            future: Future[T] = self._executor.submit(callback, *args, **kwargs)
        return TaskHandle(future)

    def close(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
        executor.shutdown(wait=wait, cancel_futures=cancel_futures)
