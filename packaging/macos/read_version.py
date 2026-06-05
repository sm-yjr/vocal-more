"""Print the project version for packaging scripts."""

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


print(read_project_version())
