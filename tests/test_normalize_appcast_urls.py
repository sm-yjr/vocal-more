"""Tests for GitHub/Sparkle enclosure URL normalization."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "packaging" / "macos" / "normalize_appcast_urls.py"
SPEC = spec_from_file_location("normalize_appcast_urls", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_normalizes_delta_name_and_release_tag():
    xml = (
        '<enclosure url="https://github.com/sm-yjr/vocal-more/releases/download/'
        'v0.3.18/Vocal%20More0.3.18-0.3.17.delta"/>'
    )

    normalized = MODULE.normalize_appcast_urls(xml)

    assert normalized.endswith(
        '/releases/download/v0.3.18/Vocal.More0.3.18-0.3.17.delta"/>'
    )


def test_repairs_historical_dmg_rewritten_to_current_release():
    xml = (
        '<enclosure url="https://github.com/sm-yjr/vocal-more/releases/download/'
        'v0.3.18/Vocal-More-0.3.17.dmg"/>'
    )

    normalized = MODULE.normalize_appcast_urls(xml)

    assert normalized.endswith(
        '/releases/download/v0.3.17/Vocal-More-0.3.17.dmg"/>'
    )
