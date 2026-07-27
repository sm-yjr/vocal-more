"""Config parsing primitives shared by domain config models."""

from __future__ import annotations

import math


MIN_AUDIO_SAMPLE_RATE = 8000
MAX_AUDIO_SAMPLE_RATE = 48000
MIN_AUDIO_CHANNELS = 1
MAX_AUDIO_CHANNELS = 2
MIN_AUDIO_BLOCKSIZE = 128
MAX_AUDIO_BLOCKSIZE = 8192
MIN_AUDIO_GAIN = 10 ** (-6 / 20)
MAX_AUDIO_GAIN = 50.0
MIN_HIGHPASS_FREQ = 50
MAX_HIGHPASS_FREQ = 500
MIN_LLM_TEMPERATURE = 0.0
MAX_LLM_TEMPERATURE = 1.0
MIN_LLM_MAX_TOKENS = 1
MAX_LLM_MAX_TOKENS = 65536
MIN_DOUBLE_TAP_THRESHOLD = 0.15
MAX_DOUBLE_TAP_THRESHOLD = 0.5


def parse_bool(raw: object, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw != 0
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in ("1", "true", "yes", "y", "on"):
            return True
        if normalized in ("0", "false", "no", "n", "off", ""):
            return False
    return default


def parse_finite_float(raw: object, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return value


def parse_finite_int(raw: object, default: int) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def clamp_float(raw: object, *, default: float, minimum: float, maximum: float) -> float:
    value = parse_finite_float(raw, default)
    return min(max(value, minimum), maximum)


def clamp_int(raw: object, *, default: int, minimum: int, maximum: int) -> int:
    value = parse_finite_int(raw, default)
    return min(max(value, minimum), maximum)
