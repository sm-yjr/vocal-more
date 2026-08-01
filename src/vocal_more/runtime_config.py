"""Helpers for applying config changes to live runtime components."""

from __future__ import annotations

from typing import Any, Iterable


_ASR_RUNTIME_REFRESH_KEYS = {
    "enable_polish",
}

_ASR_RUNTIME_REFRESH_PREFIXES = (
    "asr.",
    "llm.",
)


def flatten_config_keys(payload: dict[str, Any] | None) -> set[str]:
    """Flatten a one-level config payload into dotted config keys."""
    if not isinstance(payload, dict):
        return set()

    keys: set[str] = set()
    for key, value in payload.items():
        if isinstance(value, dict):
            for field in value:
                keys.add(f"{key}.{field}")
        else:
            keys.add(key)
    return keys


def should_refresh_asr_runtime(changed_keys: Iterable[str]) -> bool:
    """Whether a config change should invalidate idle ASR runtime state."""
    for key in changed_keys:
        if key in _ASR_RUNTIME_REFRESH_KEYS:
            return True
        if key.startswith(_ASR_RUNTIME_REFRESH_PREFIXES):
            return True
    return False
