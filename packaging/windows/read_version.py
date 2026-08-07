"""Print the project version from pyproject.toml."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "pyproject.toml").open("rb") as file:
    print(tomllib.load(file)["project"]["version"])
