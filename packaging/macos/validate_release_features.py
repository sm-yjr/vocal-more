"""Fail packaging when an unreleased product feature is enabled."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES_PATH = ROOT / "src" / "vocal_more" / "release_features.py"


def _literal_assignment(name: str) -> object:
    tree = ast.parse(FEATURES_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise RuntimeError(f"Missing release feature assignment: {name}")


def main() -> int:
    if _literal_assignment("ADAPTIVE_INPUT_MODE_ENABLED") is not False:
        raise SystemExit(
            "Adaptive input mode is not approved for release; "
            "set ADAPTIVE_INPUT_MODE_ENABLED = False before packaging."
        )
    print("Release feature validation passed: adaptive input mode is disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
