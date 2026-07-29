"""Lazy public imports for Vocal-More recording modes."""

from importlib import import_module
from typing import Any

__all__ = [
    "BaseMode",
    "MeetingMode",
    "WalkieTalkieMode",
    "RealtimeLongMode",
]

_PUBLIC_IMPORTS = {
    "BaseMode": (".base_mode", "BaseMode"),
    "MeetingMode": (".meeting", "MeetingMode"),
    "WalkieTalkieMode": (".walkie_talkie", "WalkieTalkieMode"),
    "RealtimeLongMode": (".realtime_long", "RealtimeLongMode"),
}


def __getattr__(name: str) -> Any:
    target = _PUBLIC_IMPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value
