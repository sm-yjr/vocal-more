#!/usr/bin/env python3
"""Validate, score, and compare reproducible dictation benchmark runs."""

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

from vocal_more.benchmarking import (
    apply_semantic_reviews,
    build_report,
    compare_reports,
    load_manifest,
    render_markdown_report,
)


def _read_json(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_text(path: str | Path, content: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(destination)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate",
        help="Validate corpus schema, coverage, WAV format, and fingerprint",
    )
    validate.add_argument("--manifest", required=True)

    score = commands.add_parser(
        "score",
        help="Score one system run and write JSON plus Markdown reports",
    )
    score.add_argument("--manifest", required=True)
    score.add_argument("--run", required=True)
    score.add_argument("--json-output", required=True)
    score.add_argument("--markdown-output", required=True)

    compare = commands.add_parser(
        "compare",
        help="Check whether two reports support a valid comparison claim",
    )
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)
    compare.add_argument("--output", required=True)

    review = commands.add_parser(
        "review",
        help="Attach a corpus-bound semantic review sidecar to a raw run",
    )
    review.add_argument("--run", required=True)
    review.add_argument("--review", required=True)
    review.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "validate":
        manifest = load_manifest(args.manifest)
        print(
            json.dumps(
                {
                    "suite_id": manifest.suite_id,
                    "active_samples": len(manifest.active_samples),
                    "coverage": sorted(manifest.coverage),
                    "fingerprint": manifest.fingerprint,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "score":
        manifest = load_manifest(args.manifest)
        report = build_report(manifest, _read_json(args.run))
        _write_text(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        _write_text(args.markdown_output, render_markdown_report(report))
        return 0

    if args.command == "review":
        annotated = apply_semantic_reviews(
            _read_json(args.run),
            _read_json(args.review),
        )
        _write_text(
            args.output,
            json.dumps(annotated, ensure_ascii=False, indent=2) + "\n",
        )
        return 0

    comparison = compare_reports(
        _read_json(args.left),
        _read_json(args.right),
    )
    _write_text(
        args.output,
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
    )
    return 0 if comparison["claim_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
