"""Release-only feature gates must fail closed before packaging."""

import runpy
from pathlib import Path

from vocal_more.release_features import ADAPTIVE_INPUT_MODE_ENABLED


def test_adaptive_input_mode_is_disabled_for_release():
    assert ADAPTIVE_INPUT_MODE_ENABLED is False


def test_macos_packaging_validator_accepts_current_feature_gates():
    root = Path(__file__).resolve().parents[1]
    module = runpy.run_path(
        str(root / "packaging/macos/validate_release_features.py"),
        run_name="release_feature_validator",
    )

    assert module["main"]() == 0
