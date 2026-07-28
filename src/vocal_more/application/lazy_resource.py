"""Thread-safe lazy ownership for optional heavyweight runtime resources."""

from __future__ import annotations

import threading
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class LazyResource(Generic[T]):
    """Create one owned resource on first operational access."""

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._instance: Optional[T] = None
        self._lock = threading.Lock()

    @property
    def is_initialized(self) -> bool:
        return self._instance is not None

    def get(self) -> T:
        instance = self._instance
        if instance is not None:
            return instance
        with self._lock:
            if self._instance is None:
                self._instance = self._factory()
            return self._instance

    def peek(self) -> Optional[T]:
        return self._instance

    def close(self) -> None:
        instance = self._instance
        if instance is None:
            return
        close = getattr(instance, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str):
        return getattr(self.get(), name)


def initialized_resource(resource: object | None) -> object | None:
    """Return a lazy resource's value without causing initialization."""
    if isinstance(resource, LazyResource):
        return resource.peek()
    return resource
