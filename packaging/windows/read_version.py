"""Print the project version from pyproject.toml."""

import argparse
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
from release.model import Version

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--numeric", action="store_true", help="Numeric Windows version resource")
args = parser.parse_args()
with (ROOT / "pyproject.toml").open("rb") as file:
    project = tomllib.load(file)
version = Version.project(project["project"]["version"], project.get("tool", {}).get("vocal-more", {}).get("build"))
print(version.base if args.numeric else version.pep440)
