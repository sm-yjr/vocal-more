"""Shared runtime-side effects for config changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..modes.base_mode import ModeState
from ..runtime_config import flatten_config_keys, should_refresh_asr_runtime

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
        modes: dict[str, object],
        get_current_mode: Callable[[], object],
        set_current_mode: Callable[[object], None],
        on_refresh_text_polisher: Callable[[], None] | None = None,
        on_set_active_hotkeys: Callable[[list[str]], None] | None = None,
        on_set_custom_key: Callable[[dict | None], None] | None = None,
        on_set_custom_keys: Callable[[list[dict]], None] | None = None,
        on_apply_interface_language: Callable[[], None] | None = None,
        on_refresh_environment_status: Callable[[], None] | None = None,
        on_refresh_dictionary_learning: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self._modes = modes
        self._get_current_mode = get_current_mode
        self._set_current_mode = set_current_mode
        self._on_refresh_text_polisher = on_refresh_text_polisher
        self._on_set_active_hotkeys = on_set_active_hotkeys
        self._on_set_custom_key = on_set_custom_key
        self._on_set_custom_keys = on_set_custom_keys
        self._on_apply_interface_language = on_apply_interface_language
        self._on_refresh_environment_status = on_refresh_environment_status
        self._on_refresh_dictionary_learning = on_refresh_dictionary_learning

    @property
    def current_mode_name(self) -> str:
        current_mode = self._get_current_mode()
        for name, mode in self._modes.items():
            if mode is current_mode:
                return name
        return "unknown"

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
        current_mode = self._get_current_mode()
        if current_mode is not None and getattr(current_mode, "state", ModeState.IDLE) != ModeState.IDLE:
            return
        self._set_current_mode(self._modes[self.config.default_mode])

    def _refresh_mode_asr_runtime(self) -> None:
        for mode in self._modes.values():
            asr = getattr(mode, "_asr", None)
            if asr is not None and hasattr(asr, "refresh_runtime_config"):
                asr.refresh_runtime_config(drop_idle_session=True)

    def _sync_audio_recorders(self) -> None:
        for mode in self._modes.values():
            recorder = getattr(mode, "_recorder", None)
            if recorder is None:
                continue
            recorder.set_device(self.config.audio.input_device)
            recorder.set_gain(self.config.audio.gain)
            recorder.set_highpass_filter(self.config.audio.highpass_filter)
            recorder.set_highpass_freq(self.config.audio.highpass_freq)
            recorder.set_soft_limiter(self.config.audio.soft_limiter)
