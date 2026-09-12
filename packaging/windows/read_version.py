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
    version = Version.parse(tomllib.load(file)["project"]["version"])
    print(version.base if args.numeric else version.text)
