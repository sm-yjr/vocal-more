"""Windows trigger choices backed by the shared physical-key catalog."""

from __future__ import annotations

from typing import Iterable

from .domain.hotkey_catalog import CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE, CUSTOM_HOTKEY_KEYS_BY_CODE


WINDOWS_TRIGGER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("F8", "F8"),
    ("F9", "F9"),
    ("F10", "F10"),
    ("F11", "F11"),
    ("F12", "F12"),
    ("CapsLock", "Caps Lock"),
    ("ControlRight", "Right Ctrl"),
    ("AltRight", "Right Alt"),
)
_SUPPORTED_CODES = {code for code, _label in WINDOWS_TRIGGER_OPTIONS}


def custom_key_config_for_browser_code(browser_code: str) -> dict | None:
    """Return a persisted custom-key record; F8 uses the built-in ``fn`` slot."""
    code = str(browser_code or "").strip()
    if code == "F8":
        return None
    if code not in _SUPPORTED_CODES:
        return None
    definition = CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE.get(code)
    return definition.to_config() if definition is not None else None


def current_trigger_browser_code(
    active_hotkeys: Iterable[str],
    custom_keys: Iterable[object],
) -> str:
    """Resolve the first supported Windows trigger from persisted settings."""
    if "fn" in set(active_hotkeys):
        return "F8"
    for raw in custom_keys:
        if not isinstance(raw, dict):
            continue
        key_code = raw.get("key_code")
        if isinstance(key_code, bool):
            continue
        try:
            definition = CUSTOM_HOTKEY_KEYS_BY_CODE.get(int(key_code))
        except (TypeError, ValueError):
            continue
        if definition is not None and definition.browser_code in _SUPPORTED_CODES:
            return definition.browser_code
    return "F8"


__all__ = [
    "WINDOWS_TRIGGER_OPTIONS",
    "current_trigger_browser_code",
    "custom_key_config_for_browser_code",
]
