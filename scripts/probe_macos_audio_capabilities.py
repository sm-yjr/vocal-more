#!/usr/bin/env python3
"""Print a privacy-safe, zero-capture macOS audio capability snapshot."""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import sys
from typing import Callable, Sequence, TextIO


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more.core.macos_audio_diagnostics import (  # noqa: E402
    macos_audio_capability_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit one compact JSON line instead of indented JSON",
    )
    parser.add_argument(
        "--verbose-paths",
        action="store_true",
        help="include absolute native-library paths in the local-only output",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    snapshot_provider: Callable[[], dict] = macos_audio_capability_snapshot,
    output: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    destination = output or sys.stdout

    # Planned-status dependencies may write ordinary diagnostics. Keep stdout
    # machine-readable by routing those messages to stderr during collection.
    with contextlib.redirect_stdout(sys.stderr):
        if snapshot_provider is macos_audio_capability_snapshot:
            snapshot = snapshot_provider(
                include_absolute_paths=args.verbose_paths,
            )
        else:
            snapshot = snapshot_provider()

    json.dump(
        snapshot,
        destination,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    destination.write("\n")
    destination.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
