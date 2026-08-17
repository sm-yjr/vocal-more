"""Lazy public imports for Vocal-More core modules."""

from importlib import import_module
from typing import Any

__all__ = [
    "AudioRecorder",
    "ASREngine",
    "TextPolisher",
    "HotkeyManager",
    "KeyboardSimulator",
    "PasteOutcome",
    "TextOutputPort",
]

_PUBLIC_IMPORTS = {
    "AudioRecorder": (".audio_recorder", "AudioRecorder"),
    "ASREngine": (".asr_engine", "ASREngine"),
    "TextPolisher": (".text_polisher", "TextPolisher"),
    "HotkeyManager": (".hotkey_manager", "HotkeyManager"),
    "KeyboardSimulator": (".keyboard_sim", "KeyboardSimulator"),
    "PasteOutcome": (".text_output", "PasteOutcome"),
    "TextOutputPort": (".text_output", "TextOutputPort"),
}


def __getattr__(name: str) -> Any:
    target = _PUBLIC_IMPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value
