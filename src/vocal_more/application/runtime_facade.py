"""Shared runtime-side effects for config changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..runtime_config import flatten_config_keys, should_refresh_asr_runtime
from .mode_runtime import ModeRuntimePort, ModeRuntimeService

_MISSING = object()
_VISUAL_ONLY_AUDIO_KEYS = {"audio.waveform_ceiling_dbfs"}


def _config_snapshot_value(snapshot: dict[str, Any], key: str) -> Any:
    value: Any = snapshot
    for part in key.split("."):
        if not isinstance(value, dict):
            return _MISSING
        value = value.get(part, _MISSING)
    return value


@dataclass
class RuntimeUpdateResult:
    """Summary of side effects triggered by a config update."""

    changed_keys: set[str]
    refresh_text_polisher: bool = False
    refresh_audio_recorders: bool = False
    refresh_asr_runtime: bool = False
    refresh_environment_status: bool = False


class RuntimeFacade:
    """Apply config changes and synchronize live runtime components."""

    def __init__(
        self,
        *,
        config,
        mode_runtime: ModeRuntimePort | None = None,
        modes: dict[str, object] | None = None,
        get_current_mode: Callable[[], object] | None = None,
        set_current_mode: Callable[[object], None] | None = None,
        on_refresh_text_polisher: Callable[[], None] | None = None,
        on_set_active_hotkeys: Callable[[list[str]], None] | None = None,
        on_set_custom_key: Callable[[dict | None], None] | None = None,
        on_set_custom_keys: Callable[[list[dict]], None] | None = None,
        on_set_command_key: Callable[[dict | None], None] | None = None,
        on_apply_interface_language: Callable[[], None] | None = None,
        on_refresh_environment_status: Callable[[], None] | None = None,
        on_refresh_dictionary_learning: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        if mode_runtime is None:
            if modes is None or get_current_mode is None or set_current_mode is None:
                raise TypeError(
                    "RuntimeFacade requires mode_runtime or the legacy mode callbacks"
                )
            mode_runtime = ModeRuntimeService(
                modes=modes,
                get_current_mode=get_current_mode,
                set_current_mode=set_current_mode,
            )
        self._mode_runtime = mode_runtime
        self._on_refresh_text_polisher = on_refresh_text_polisher
        self._on_set_active_hotkeys = on_set_active_hotkeys
        self._on_set_custom_key = on_set_custom_key
        self._on_set_custom_keys = on_set_custom_keys
        self._on_set_command_key = on_set_command_key
        self._on_apply_interface_language = on_apply_interface_language
        self._on_refresh_environment_status = on_refresh_environment_status
        self._on_refresh_dictionary_learning = on_refresh_dictionary_learning

    @property
    def current_mode_name(self) -> str:
        return self._mode_runtime.current_mode_name

    def apply_update(self, key: str, value: Any) -> RuntimeUpdateResult:
        self.config.apply_update(key, value)
        changed_keys = {key}
        return self._apply_runtime_config_keys(changed_keys)

    def apply_form_state(self, form_state: dict[str, Any]) -> RuntimeUpdateResult:
        candidate_keys = flatten_config_keys(form_state)
        before = self.config.to_dict()
        self.config.apply_form_state(form_state)
        after = self.config.to_dict()
        changed_keys = {
            key
            for key in candidate_keys
            if _config_snapshot_value(before, key)
            != _config_snapshot_value(after, key)
        }
        return self._apply_runtime_config_keys(changed_keys)

    def _apply_runtime_config_keys(self, changed_keys: set[str]) -> RuntimeUpdateResult:
        result = RuntimeUpdateResult(changed_keys=set(changed_keys))
        if not changed_keys:
            return result
        audio_runtime_changed = any(
            key.startswith("audio.") and key not in _VISUAL_ONLY_AUDIO_KEYS
            for key in changed_keys
        )

        if "api_key" in changed_keys and self._on_refresh_text_polisher is not None:
            self._on_refresh_text_polisher()
            result.refresh_text_polisher = True

        if audio_runtime_changed:
            self._sync_audio_recorders()
            result.refresh_audio_recorders = True

        if "hotkey.active_hotkeys" in changed_keys and self._on_set_active_hotkeys is not None:
            self._on_set_active_hotkeys(self.config.hotkey.active_hotkeys)

        if "hotkey.custom_key" in changed_keys and self._on_set_custom_key is not None:
            self._on_set_custom_key(self.config.hotkey.custom_key)

        if (
            "hotkey.custom_keys" in changed_keys
            and self._on_set_custom_keys is not None
        ):
            self._on_set_custom_keys(self.config.hotkey.custom_keys)

        if (
            "hotkey.command_key" in changed_keys
            and self._on_set_command_key is not None
        ):
            self._on_set_command_key(self.config.hotkey.command_key)

        if "ui.language" in changed_keys and self._on_apply_interface_language is not None:
            self._on_apply_interface_language()

        if (
            "api_key" in changed_keys
            or any(key.startswith("dictionary_learning.") for key in changed_keys)
        ) and self._on_refresh_dictionary_learning is not None:
            self._on_refresh_dictionary_learning()

        if "default_mode" in changed_keys:
            self._select_default_mode_when_safe()

        if should_refresh_asr_runtime(changed_keys):
            self._refresh_mode_asr_runtime()
            result.refresh_asr_runtime = True

        if (
            "api_key" in changed_keys or audio_runtime_changed
        ) and self._on_refresh_environment_status is not None:
            self._on_refresh_environment_status()
            result.refresh_environment_status = True

        return result

    def _select_default_mode_when_safe(self) -> None:
        self._mode_runtime.select_mode_when_idle(self.config.default_mode)

    def _refresh_mode_asr_runtime(self) -> None:
        self._mode_runtime.refresh_asr_runtime()

    def _sync_audio_recorders(self) -> None:
        self._mode_runtime.apply_audio_config(self.config.audio)
