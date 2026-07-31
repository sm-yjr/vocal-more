"""Public runtime-control port for a group of dictation modes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol


class RuntimeModePort(Protocol):
    """Narrow mode surface used by live configuration synchronization."""

    @property
    def runtime_is_idle(self) -> bool: ...

    def apply_audio_runtime_config(self, audio_config: object) -> None: ...

    def refresh_asr_runtime(self) -> None: ...


class ModeRuntimePort(Protocol):
    """Operations RuntimeFacade needs without knowing concrete mode objects."""

    @property
    def current_mode_name(self) -> str: ...

    def select_mode_when_idle(self, mode_name: str) -> bool: ...

    def apply_audio_config(self, audio_config: object) -> None: ...

    def refresh_asr_runtime(self) -> None: ...


class ModeRuntimeService:
    """Coordinate public runtime operations across owned dictation modes."""

    def __init__(
        self,
        *,
        modes: Mapping[str, RuntimeModePort],
        get_current_mode: Callable[[], RuntimeModePort | None],
        set_current_mode: Callable[[RuntimeModePort], None],
    ) -> None:
        self._modes = dict(modes)
        self._get_current_mode = get_current_mode
        self._set_current_mode = set_current_mode

    @property
    def current_mode_name(self) -> str:
        current_mode = self._get_current_mode()
        for name, mode in self._modes.items():
            if mode is current_mode:
                return name
        return "unknown"

    def select_mode_when_idle(self, mode_name: str) -> bool:
        selected_mode = self._modes.get(mode_name)
        if selected_mode is None:
            return False
        current_mode = self._get_current_mode()
        if current_mode is not None and not current_mode.runtime_is_idle:
            return False
        self._set_current_mode(selected_mode)
        return True

    def apply_audio_config(self, audio_config: object) -> None:
        for mode in self._modes.values():
            mode.apply_audio_runtime_config(audio_config)

    def refresh_asr_runtime(self) -> None:
        for mode in self._modes.values():
            mode.refresh_asr_runtime()


__all__ = ["ModeRuntimePort", "ModeRuntimeService", "RuntimeModePort"]
