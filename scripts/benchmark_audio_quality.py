#!/usr/bin/env python3
"""Measure paired automatic/manual gain recordings without network access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more.audio_quality_benchmark import (
    build_audio_quality_report,
    load_audio_quality_manifest,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="YAML/JSON hardware capture manifest",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON destination; omit to print to stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = build_audio_quality_report(
        load_audio_quality_manifest(args.manifest)
    )
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(content, end="")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
