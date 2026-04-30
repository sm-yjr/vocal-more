"""Version metadata should have one source of truth."""

import tomllib
from pathlib import Path

from vocal_more import __version__


def test_runtime_version_matches_project_metadata():
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert __version__ == pyproject["project"]["version"]
