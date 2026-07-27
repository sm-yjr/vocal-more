from __future__ import annotations

import math

import pytest


def _rms_at_dbfs(dbfs: float) -> float:
    return 10 ** (dbfs / 20)


@pytest.mark.parametrize(
    ("dbfs", "expected_level"),
    [
        (-61.8, 0.0),
        (-38.7, 21.3 / 54.0),
        (-29.1, 30.9 / 54.0),
        (-9.6, 50.4 / 54.0),
        (-6.0, 1.0),
    ],
)
def test_waveform_calibration_maps_measured_rms_levels(
    dbfs: float,
    expected_level: float,
):
    from vocal_more.domain.waveform_calibration import waveform_level_from_rms

    level = waveform_level_from_rms(_rms_at_dbfs(dbfs))

    assert level == pytest.approx(expected_level, abs=0.001)


def test_waveform_calibration_gates_invalid_and_sub_floor_input():
    from vocal_more.domain.waveform_calibration import waveform_level_from_rms

    assert waveform_level_from_rms(0.0) == 0.0
    assert waveform_level_from_rms(-1.0) == 0.0
    assert waveform_level_from_rms(math.nan) == 0.0
    assert waveform_level_from_rms(_rms_at_dbfs(-60.1)) == 0.0


def test_waveform_calibration_supports_a_custom_full_scale_level():
    from vocal_more.domain.waveform_calibration import waveform_level_from_rms

    assert waveform_level_from_rms(
        _rms_at_dbfs(-18.0),
        ceiling_dbfs=-18.0,
    ) == 1.0
    assert waveform_level_from_rms(
        _rms_at_dbfs(-39.0),
        ceiling_dbfs=-18.0,
    ) == pytest.approx(0.5)
