#!/usr/bin/env python3
"""Convert timing-only app traces plus local hypotheses into a live run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more.benchmarking import load_manifest, timings_from_live_trace


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def build_live_run(
    *,
    manifest_path: Path,
    trace_dir: Path,
    hypotheses_path: Path,
    network_label: str,
    hardware_label: str | None = None,
) -> dict:
    manifest = load_manifest(manifest_path)
    hypotheses = _read_object(hypotheses_path)
    trace_by_sample: dict[str, dict] = {}
    for path in sorted(trace_dir.glob("*.json")):
        trace = _read_object(path)
        metadata = trace.get("metadata")
        if not isinstance(metadata, dict):
            continue
        sample_id = str(metadata.get("sample_id") or "").strip()
        if not sample_id:
            continue
        if sample_id in trace_by_sample:
            raise ValueError(f"Multiple live traces found for sample {sample_id}")
        trace_by_sample[sample_id] = trace

    results = []
    app_versions: set[str] = set()
    models: set[str] = set()
    audio_deliveries: set[str] = set()
    for sample in manifest.active_samples:
        trace = trace_by_sample.get(sample.id)
        if trace is None:
            results.append(
                {
                    "sample_id": sample.id,
                    "status": "failed",
                    "hypothesis": str(hypotheses.get(sample.id) or ""),
                    "result_source": "",
                    "fallback_reason": "",
                    "timings_ms": {
                        "first_feedback": None,
                        "first_partial": None,
                        "speech_end_to_insert": None,
                        "stop_to_result": None,
                    },
                    "error": "missing_live_trace",
                }
            )
            continue

        metadata = trace.get("metadata") or {}
        if metadata.get("app_version"):
            app_versions.add(str(metadata["app_version"]))
        if metadata.get("model"):
            models.add(str(metadata["model"]))
        if metadata.get("audio_delivery"):
            audio_deliveries.add(str(metadata["audio_delivery"]))
        results.append(
            {
                "sample_id": sample.id,
                "status": str(trace.get("status") or "failed"),
                "hypothesis": str(hypotheses.get(sample.id) or ""),
                "result_source": str(metadata.get("result_source") or ""),
                "fallback_reason": str(metadata.get("fallback_reason") or ""),
                "timings_ms": timings_from_live_trace(trace),
                "error": str(metadata.get("error_code") or ""),
            }
        )

    return {
        "schema_version": 1,
        "system": {
            "id": "vocal-more",
            "version": _single_value(app_versions),
            "model": _single_value(models),
            "transport": "live_app",
        },
        "trace_level": "live_end_to_end",
        "conditions": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "os": platform.platform(),
            "hardware": hardware_label or platform.machine(),
            "network": network_label,
            "sample_rate_hz": 16000,
            "repetitions": 1,
            "audio_delivery": _single_value(audio_deliveries),
        },
        "manifest_fingerprint": manifest.fingerprint,
        "results": results,
    }


def _single_value(values: set[str]) -> str:
    if not values:
        return "unknown"
    if len(values) > 1:
        raise ValueError(f"Live traces contain mixed conditions: {sorted(values)}")
    return next(iter(values))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--trace-dir", required=True)
    parser.add_argument("--hypotheses", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--network", default="unspecified")
    parser.add_argument("--hardware")
    args = parser.parse_args(argv)

    run = build_live_run(
        manifest_path=Path(args.manifest).resolve(),
        trace_dir=Path(args.trace_dir).resolve(),
        hypotheses_path=Path(args.hypotheses).resolve(),
        network_label=args.network,
        hardware_label=args.hardware,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
