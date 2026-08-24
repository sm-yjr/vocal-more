#!/usr/bin/env python3
"""Run the Vocal More realtime engine against a fingerprinted WAV corpus."""

from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import wave
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more import __version__, config as config_module
from vocal_more.benchmarking import (
    BenchmarkSample,
    load_manifest,
    timings_from_asr_trace,
)
from vocal_more.config import reload_config
from vocal_more.core.asr_engine import ASREngine, REALTIME_CHUNK_SIZE
from vocal_more.dictionary import reload_dictionary


def feed_audio(
    engine,
    pcm_data: bytes,
    *,
    chunk_bytes: int = REALTIME_CHUNK_SIZE,
    paced: bool,
    sample_rate: int = 16000,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Feed mono 16-bit PCM either immediately or at wall-clock audio speed."""
    bytes_per_second = sample_rate * 2
    for offset in range(0, len(pcm_data), chunk_bytes):
        chunk = pcm_data[offset:offset + chunk_bytes]
        engine.send_audio(chunk)
        if paced:
            sleep(len(chunk) / bytes_per_second)


def _read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as audio:
        return audio.readframes(audio.getnframes())


def _new_trace(trace_dir: Path, seen: set[Path]) -> dict:
    files = sorted(trace_dir.glob("*.json"))
    new_files = [path for path in files if path not in seen]
    seen.update(new_files)
    if len(new_files) != 1:
        raise RuntimeError(
            f"Expected exactly one ASR trace, found {len(new_files)}"
        )
    payload = json.loads(new_files[0].read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("ASR trace must contain a JSON object")
    return payload


def _run_sample(
    engine: ASREngine,
    sample: BenchmarkSample,
    *,
    manifest_dir: Path,
    trace_dir: Path,
    seen: set[Path],
    trace_level: str,
    chunk_bytes: int,
    prewarm: bool,
) -> dict:
    pcm_data = _read_pcm((manifest_dir / sample.audio).resolve())
    prewarm_ready = False
    try:
        if prewarm:
            engine.prepare_idle_session()
            prewarm_ready = engine.wait_for_idle_session_ready(timeout=12.0)
        engine.start()
        feed_audio(
            engine,
            pcm_data,
            chunk_bytes=chunk_bytes,
            paced=trace_level == "paced_replay",
        )
        hypothesis = engine.stop(pcm_data=pcm_data)
        trace = _new_trace(trace_dir, seen)
        return {
            "sample_id": sample.id,
            "status": "success" if hypothesis.strip() else "failed",
            "hypothesis": hypothesis,
            "result_source": str(trace.get("result_source") or ""),
            "fallback_reason": str(trace.get("fallback_reason") or ""),
            "prewarm_ready": prewarm_ready,
            "warm_session_reused": bool(trace.get("warm_session_reused")),
            "timings_ms": timings_from_asr_trace(
                trace,
                trace_level=trace_level,
            ),
            "error": str(trace.get("error") or ""),
        }
    except Exception as exc:
        try:
            engine.reset()
        except Exception:
            pass
        return {
            "sample_id": sample.id,
            "status": "failed",
            "hypothesis": "",
            "result_source": "",
            "fallback_reason": "",
            "prewarm_ready": prewarm_ready,
            "warm_session_reused": False,
            "timings_ms": {
                "first_feedback": None,
                "first_partial": None,
                "speech_end_to_insert": None,
                "stop_to_result": None,
            },
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_benchmark(
    *,
    manifest_path: Path,
    model: str,
    trace_level: str,
    network_label: str,
    enable_polish: bool | None,
    audio_chunk_ms: int,
    prewarm: bool,
    realtime_url: str = "",
) -> dict:
    manifest = load_manifest(manifest_path)
    config = copy.deepcopy(reload_config())
    config.apply_update("asr.model", model)
    if realtime_url:
        config.apply_update("asr.realtime_url", realtime_url)
    config.audio.blocksize = round(config.audio.sample_rate * audio_chunk_ms / 1000)
    if enable_polish is not None:
        config.enable_polish = enable_polish
    config_module._config = config
    reload_dictionary()

    results = []
    with tempfile.TemporaryDirectory(
        prefix="vocal-more-benchmark-traces-"
    ) as temporary_dir:
        trace_dir = Path(temporary_dir)
        previous_debug_dir = os.environ.get("VOCAL_MORE_DEBUG_DIR")
        os.environ["VOCAL_MORE_DEBUG_DIR"] = str(trace_dir)
        seen: set[Path] = set()
        engine = ASREngine()
        try:
            with contextlib.redirect_stdout(sys.stderr):
                for sample in manifest.active_samples:
                    results.append(
                        _run_sample(
                            engine,
                            sample,
                            manifest_dir=manifest.path.parent,
                            trace_dir=trace_dir,
                            seen=seen,
                            trace_level=trace_level,
                            chunk_bytes=config.audio.blocksize * 2,
                            prewarm=prewarm,
                        )
                    )
        finally:
            engine.reset()
            if previous_debug_dir is None:
                os.environ.pop("VOCAL_MORE_DEBUG_DIR", None)
            else:
                os.environ["VOCAL_MORE_DEBUG_DIR"] = previous_debug_dir

    return {
        "schema_version": 1,
        "system": {
            "id": "vocal-more",
            "version": __version__,
            "model": config.asr.model,
            "transport": config.asr.backend,
            "enable_polish": config.enable_polish,
            "realtime_url": config.asr.realtime_url,
        },
        "trace_level": trace_level,
        "conditions": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "os": platform.platform(),
            "hardware": platform.machine(),
            "python": platform.python_version(),
            "network": network_label,
            "sample_rate_hz": 16000,
            "repetitions": 1,
            "audio_delivery": (
                "wall_clock_paced"
                if trace_level == "paced_replay"
                else "unpaced"
            ),
            "audio_chunk_ms": audio_chunk_ms,
            "audio_chunk_bytes": config.audio.blocksize * 2,
            "prewarm": prewarm,
        },
        "manifest_fingerprint": manifest.fingerprint,
        "results": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--model",
        default="qwen3.5-omni-flash-realtime",
    )
    parser.add_argument(
        "--trace-level",
        choices=("protocol_replay", "paced_replay"),
        default="paced_replay",
    )
    parser.add_argument(
        "--network",
        default="unspecified",
        help="Human-readable network condition; no SSID is collected automatically",
    )
    parser.add_argument(
        "--audio-chunk-ms",
        type=int,
        choices=(40, 80, 100),
        default=100,
        help="PCM duration per realtime WebSocket audio packet",
    )
    parser.add_argument(
        "--realtime-url",
        default="",
        help="Optional workspace-specific DashScope realtime WebSocket endpoint",
    )
    parser.add_argument(
        "--cold-start",
        action="store_true",
        help="Skip idle-session prewarming before each sample",
    )
    polish = parser.add_mutually_exclusive_group()
    polish.add_argument("--enable-polish", action="store_true")
    polish.add_argument("--disable-polish", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    enable_polish = None
    if args.enable_polish:
        enable_polish = True
    elif args.disable_polish:
        enable_polish = False
    report = run_benchmark(
        manifest_path=Path(args.manifest).resolve(),
        model=args.model,
        trace_level=args.trace_level,
        network_label=args.network,
        enable_polish=enable_polish,
        audio_chunk_ms=args.audio_chunk_ms,
        prewarm=not args.cold_start,
        realtime_url=args.realtime_url,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
