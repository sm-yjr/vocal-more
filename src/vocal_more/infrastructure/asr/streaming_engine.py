"""Pure helpers used by the streaming ASR compatibility facade."""

from __future__ import annotations


def long_audio_minutes(duration_seconds: float) -> float:
    return max(0.0, duration_seconds - 60.0) / 60.0


def adaptive_response_start_timeout(
    duration_seconds: float,
    *,
    start_timeout_seconds: float,
    max_timeout_seconds: float,
) -> float:
    extra_minutes = long_audio_minutes(duration_seconds)
    timeout = start_timeout_seconds + extra_minutes * 1.5
    return min(max_timeout_seconds, timeout)


def adaptive_response_complete_timeout(
    duration_seconds: float,
    *,
    base_timeout: float,
    max_timeout_seconds: float,
) -> float:
    extra_minutes = long_audio_minutes(duration_seconds)
    timeout = max(base_timeout, 30.0 + extra_minutes * 4.0)
    return min(max_timeout_seconds, timeout)


__all__ = [
    "adaptive_response_complete_timeout",
    "adaptive_response_start_timeout",
    "long_audio_minutes",
]
