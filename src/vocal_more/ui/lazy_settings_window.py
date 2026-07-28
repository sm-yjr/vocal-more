"""Delay construction of the settings window until it is first shown."""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

_UNSET = object()


class LazySettingsWindow:
    """Settings-window facade that keeps WebView resources out of idle startup."""

    def __init__(self, factory: Callable[..., object], **factory_kwargs: Any) -> None:
        self._factory = factory
        self._factory_kwargs = factory_kwargs
        self._instance: Optional[object] = None
        self._lock = threading.Lock()
        self._language: Optional[tuple[str, bool]] = None

    @property
    def is_initialized(self) -> bool:
        return self._instance is not None

    def _get_or_create(self) -> object:
        with self._lock:
            if self._instance is None:
                self._instance = self._factory(**self._factory_kwargs)
                language = self._language
            else:
                language = None
            instance = self._instance

        if language is not None:
            instance.set_interface_language(
                language[0],
                update_frontend=language[1],
            )
        return instance

    def show(self, **kwargs: Any) -> None:
        self._get_or_create().show(**kwargs)

    def close(self) -> None:
        instance = self._instance
        if instance is not None:
            instance.close()

    def is_visible(self) -> bool:
        instance = self._instance
        return bool(instance is not None and instance.is_visible())

    def set_interface_language(
        self,
        language: str,
        update_frontend: bool = True,
    ) -> None:
        self._language = (language, update_frontend)
        instance = self._instance
        if instance is not None:
            instance.set_interface_language(
                language,
                update_frontend=update_frontend,
            )

    def update_devices(self, devices: list, selected_device: object = _UNSET) -> None:
        instance = self._instance
        if instance is not None:
            if selected_device is _UNSET:
                instance.update_devices(devices)
            else:
                instance.update_devices(devices, selected_device)

    def update_environment_checks(self, checks: list) -> None:
        instance = self._instance
        if instance is not None:
            instance.update_environment_checks(checks)

    def update_dictionary(self, entries: list) -> None:
        instance = self._instance
        if instance is not None:
            instance.update_dictionary(entries)

    def update_dictionary_learning(self, records: list) -> None:
        instance = self._instance
        if instance is not None:
            instance.update_dictionary_learning(records)
