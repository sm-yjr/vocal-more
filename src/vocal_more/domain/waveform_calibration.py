"""RMS-to-waveform calibration for the floating capsule."""

from __future__ import annotations

import math

WAVEFORM_NOISE_FLOOR_DBFS = -60.0
DEFAULT_WAVEFORM_CEILING_DBFS = -6.0
MIN_WAVEFORM_CEILING_DBFS = -30.0
MAX_WAVEFORM_CEILING_DBFS = 0.0


def normalize_waveform_ceiling_dbfs(value: object) -> float:
    """Clamp the configurable full-scale waveform level to a useful range."""
    try:
        ceiling = float(value)
    except (TypeError, ValueError):
        return DEFAULT_WAVEFORM_CEILING_DBFS
    if not math.isfinite(ceiling):
        return DEFAULT_WAVEFORM_CEILING_DBFS
    return max(
        MIN_WAVEFORM_CEILING_DBFS,
        min(MAX_WAVEFORM_CEILING_DBFS, ceiling),
    )


def waveform_level_from_rms(
    rms: float,
    *,
    ceiling_dbfs: float = DEFAULT_WAVEFORM_CEILING_DBFS,
) -> float:
    """Map post-gain RMS onto a stable 0–1 dBFS waveform envelope."""
    try:
        level = float(rms)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(level) or level <= 0.0:
        return 0.0

    dbfs = 20.0 * math.log10(level)
    if dbfs <= WAVEFORM_NOISE_FLOOR_DBFS:
        return 0.0

    ceiling = normalize_waveform_ceiling_dbfs(ceiling_dbfs)
    normalized = (dbfs - WAVEFORM_NOISE_FLOOR_DBFS) / (
        ceiling - WAVEFORM_NOISE_FLOOR_DBFS
    )
    return max(0.0, min(1.0, normalized))


__all__ = [
    "DEFAULT_WAVEFORM_CEILING_DBFS",
    "MAX_WAVEFORM_CEILING_DBFS",
    "MIN_WAVEFORM_CEILING_DBFS",
    "WAVEFORM_NOISE_FLOOR_DBFS",
    "normalize_waveform_ceiling_dbfs",
    "waveform_level_from_rms",
]
