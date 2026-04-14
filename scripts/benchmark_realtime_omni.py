#!/usr/bin/env python3
"""Benchmark realtime Omni latency for lite/pro models."""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more import config as config_module
from vocal_more.config import Config, reload_config
from vocal_more.core.asr_engine import ASREngine, REALTIME_CHUNK_SIZE
from vocal_more.dictionary import reload_dictionary

DEFAULT_MODELS = [
    "qwen3.5-omni-flash-realtime",
    "qwen3.5-omni-plus-realtime",
]


def read_pcm_from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1:
            raise ValueError(f"{path} must be mono WAV")
        if wav_file.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16-bit PCM WAV")
        return wav_file.readframes(wav_file.getnframes())


def apply_config(config: Config) -> None:
    config_module._config = config
    reload_dictionary()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = ratio * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "count": len(values),
        "p50": round(statistics.median(values), 2),
        "p95": round(percentile(values, 0.95), 2),
        "mean": round(statistics.fmean(values), 2),
    }


def collect_new_traces(trace_dir: Path, seen: set[Path]) -> list[dict]:
    files = sorted(trace_dir.glob("*.json"))
    new_files = [path for path in files if path not in seen]
    for path in new_files:
        seen.add(path)
    return [json.loads(path.read_text()) for path in new_files]


def run_once(engine: ASREngine, pcm_data: bytes, trace_dir: Path, seen: set[Path]) -> dict:
    engine.start()
    for idx in range(0, len(pcm_data), REALTIME_CHUNK_SIZE):
        engine.send_audio(pcm_data[idx:idx + REALTIME_CHUNK_SIZE])
    result_text = engine.stop(pcm_data=pcm_data)
    traces = collect_new_traces(trace_dir, seen)
    if len(traces) != 1:
        raise RuntimeError(f"Expected exactly one new trace, got {len(traces)}")
    trace = traces[0]
    trace["result_text_preview"] = result_text[:80]
    return trace


def trace_metrics(trace: dict) -> dict[str, float | str | bool | None]:
    timings = trace.get("timings_ms", {})
    commit_ms = timings.get("commit_ms")
    total_result_ms = timings.get("total_result_ms")
    response_requested_ms = timings.get("response_requested_ms")
    response_done_ms = timings.get("response_done_ms")
    transcript_complete_ms = timings.get("transcription_complete_ms")
    return {
        "request_mode": trace.get("request_mode"),
        "result_source": trace.get("result_source"),
        "warm_session_reused": trace.get("warm_session_reused", False),
        "session_ready_ms": timings.get("session_ready_ms"),
        "commit_ms": commit_ms,
        "total_result_ms": total_result_ms,
        "post_commit_ms": (
            round(total_result_ms - commit_ms, 2)
            if commit_ms is not None and total_result_ms is not None
            else None
        ),
        "response_tail_ms": (
            round(response_done_ms - response_requested_ms, 2)
            if response_requested_ms is not None and response_done_ms is not None
            else None
        ),
        "transcription_tail_ms": (
            round(transcript_complete_ms - commit_ms, 2)
            if commit_ms is not None and transcript_complete_ms is not None
            else None
        ),
    }


def benchmark_model(
    base_config: Config,
    model_id: str,
    sample_paths: list[Path],
    keep_polish: bool,
) -> dict:
    config = copy.deepcopy(base_config)
    config.asr.model = model_id
    config.asr.backend = "realtime_ws"
    apply_config(config)

    results = []
    with tempfile.TemporaryDirectory(prefix="vocal-more-bench-") as temp_dir:
        trace_dir = Path(temp_dir)
        os.environ["VOCAL_MORE_DEBUG_DIR"] = str(trace_dir)
        seen: set[Path] = set()

        for sample_path in sample_paths:
            pcm_data = read_pcm_from_wav(sample_path)

            cold_engine = ASREngine()
            cold_trace = run_once(cold_engine, pcm_data, trace_dir, seen)
            cold_engine.reset()

            warm_engine = ASREngine()
            _ = run_once(warm_engine, pcm_data, trace_dir, seen)
            warm_trace = run_once(warm_engine, pcm_data, trace_dir, seen)
            warm_engine.reset()

            results.append(
                {
                    "sample": sample_path.name,
                    "cold": trace_metrics(cold_trace),
                    "warm": trace_metrics(warm_trace),
                    "cold_source": cold_trace.get("result_source"),
                    "warm_source": warm_trace.get("result_source"),
                }
            )

    cold_ready = [r["cold"]["session_ready_ms"] for r in results if r["cold"]["session_ready_ms"] is not None]
    cold_tail = [r["cold"]["post_commit_ms"] for r in results if r["cold"]["post_commit_ms"] is not None]
    warm_ready = [r["warm"]["session_ready_ms"] for r in results if r["warm"]["session_ready_ms"] is not None]
    warm_tail = [r["warm"]["post_commit_ms"] for r in results if r["warm"]["post_commit_ms"] is not None]
    cold_response = [r["cold"]["response_tail_ms"] for r in results if r["cold"]["response_tail_ms"] is not None]
    warm_response = [r["warm"]["response_tail_ms"] for r in results if r["warm"]["response_tail_ms"] is not None]

    return {
        "model": model_id,
        "enable_polish": keep_polish,
        "samples": results,
        "summary": {
            "cold_session_ready_ms": summarize(cold_ready),
            "cold_post_commit_ms": summarize(cold_tail),
            "cold_response_tail_ms": summarize(cold_response),
            "warm_session_ready_ms": summarize(warm_ready),
            "warm_post_commit_ms": summarize(warm_tail),
            "warm_response_tail_ms": summarize(warm_response),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", nargs="+", help="Mono 16-bit WAV samples to benchmark")
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Realtime Omni model ids to benchmark",
    )
    parser.add_argument(
        "--disable-polish",
        action="store_true",
        help="Benchmark raw transcript path instead of inline polish",
    )
    args = parser.parse_args()

    sample_paths = [Path(sample).resolve() for sample in args.samples]
    missing = [str(path) for path in sample_paths if not path.exists()]
    if missing:
        raise SystemExit(f"Missing sample files: {', '.join(missing)}")

    base_config = reload_config()
    base_config.enable_polish = not args.disable_polish

    report = {
        "samples": [str(path) for path in sample_paths],
        "models": [],
    }
    for model_id in args.models:
        with contextlib.redirect_stdout(sys.stderr):
            report["models"].append(
                benchmark_model(
                    base_config=base_config,
                    model_id=model_id,
                    sample_paths=sample_paths,
                    keep_polish=base_config.enable_polish,
                )
            )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
