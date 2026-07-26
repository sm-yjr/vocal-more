"""Helpers for reading project metadata in tests."""

import re
from pathlib import Path


def read_project_version(project_root: Path) -> str:
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (project_root / "pyproject.toml").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert match is not None
    return match.group(1)


def read_project_license(project_root: Path) -> str:
    match = re.search(
        r'^license\s*=\s*"([^"]+)"',
        (project_root / "pyproject.toml").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert match is not None
    return match.group(1)
