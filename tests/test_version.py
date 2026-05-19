"""Version metadata should have one source of truth."""

from pathlib import Path

from vocal_more import __version__
from tests.project_metadata import read_project_version


def test_runtime_version_matches_project_metadata():
    assert __version__ == read_project_version(Path(__file__).resolve().parents[1])
