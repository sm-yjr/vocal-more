"""Print the project version used in macOS artifact names (no build number)."""

from __future__ import annotations

import re
from pathlib import Path


def read_project_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    in_project = False

    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if in_project:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', stripped)
            if match:
                return match.group(1)

    raise RuntimeError(f"Could not read project version from {pyproject}")


def file_version(version: str) -> str:
    """The version in artifact names: X.Y.Z, or X.Y.Z-beta.N for a PEP 440 beta."""
    return re.sub(r"b([1-9]\d*)$", r"-beta.\1", version)


print(file_version(read_project_version()))
