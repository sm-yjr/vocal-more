#!/usr/bin/env python3
"""Benchmark verified recording-history compression on a local WAV fixture."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import platform
import shutil
import statistics
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from vocal_more.core.recording_store import (
    AppleLosslessAudioCodec,
    RecordingStore,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _portable_fixture_path(path: Path) -> str:
    """Return a reproducible path without publishing a developer home directory."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.name


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 3),
        "p50": round(statistics.median(values), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def _read_fixture(path: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    with wave.open(str(path), "rb") as wav_file:
        params = (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
            wav_file.getnframes(),
        )
        pcm = wav_file.readframes(wav_file.getnframes())
    if params[:3] != (1, 2, 16_000):
        raise ValueError(
            "fixture must be 16 kHz, mono, 16-bit PCM WAV; "
            f"received channels={params[0]}, width={params[1]}, rate={params[2]}"
        )
    return pcm, params


def _measure_codec(
    source: Path,
    codec: AppleLosslessAudioCodec,
    iterations: int,
) -> tuple[list[float], list[float], list[int]]:
    source_digest = RecordingStore._pcm_digest(source)
    encode_ms: list[float] = []
    verified_round_trip_ms: list[float] = []
    encoded_sizes: list[int] = []
    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="vocal-more-codec-bench-") as raw_dir:
            temp_dir = Path(raw_dir)
            source_copy = temp_dir / "source.wav"
            encoded = temp_dir / "recording.flac"
            decoded = temp_dir / "verified.wav"
            shutil.copy2(source, source_copy)

            started = time.perf_counter()
            codec.encode_wav_to_flac(source_copy, encoded)
            encoded_at = time.perf_counter()
            codec.decode_flac_to_wav(encoded, decoded)
            decoded_digest = RecordingStore._pcm_digest(decoded)
            finished = time.perf_counter()

            if decoded_digest != source_digest:
                raise RuntimeError("decoded FLAC does not match source PCM")
            encode_ms.append((encoded_at - started) * 1_000)
            verified_round_trip_ms.append((finished - started) * 1_000)
            encoded_sizes.append(encoded.stat().st_size)
    return encode_ms, verified_round_trip_ms, encoded_sizes


def _measure_terminal_update(
    pcm: bytes,
    *,
    auto_compact: bool,
) -> tuple[float, int]:
    with tempfile.TemporaryDirectory(prefix="vocal-more-store-bench-") as raw_dir:
        store = RecordingStore(raw_dir, auto_compact=auto_compact)
        recording_ids = [
            store.save(pcm, "realtime_long", "compression-benchmark")
            for _ in range(4)
        ]
        started = time.perf_counter()
        store.update(recording_ids[0], "success", "benchmark")
        elapsed_ms = (time.perf_counter() - started) * 1_000
        store.close()
        compressed_count = store.storage_summary()["compressed_count"]
    return elapsed_ms, compressed_count


def run(input_path: Path, iterations: int) -> dict:
    if iterations < 3:
        raise ValueError("iterations must be at least 3")
    input_path = input_path.resolve()
    pcm, params = _read_fixture(input_path)
    codec = AppleLosslessAudioCodec()
    encode_ms, round_trip_ms, encoded_sizes = _measure_codec(
        input_path,
        codec,
        iterations,
    )

    baseline_update_ms: list[float] = []
    background_update_ms: list[float] = []
    compressed_counts: list[int] = []
    with contextlib.redirect_stdout(io.StringIO()):
        for index in range(iterations):
            order = (True, False) if index % 2 == 0 else (False, True)
            measured: dict[bool, float] = {}
            for auto_compact in order:
                elapsed_ms, compressed_count = _measure_terminal_update(
                    pcm,
                    auto_compact=auto_compact,
                )
                measured[auto_compact] = elapsed_ms
                if auto_compact:
                    compressed_counts.append(compressed_count)
            background_update_ms.append(measured[True])
            baseline_update_ms.append(measured[False])

    delta_ms = [
        background - baseline
        for background, baseline in zip(
            background_update_ms,
            baseline_update_ms,
            strict=True,
        )
    ]
    source_bytes = input_path.stat().st_size
    stored_bytes = int(statistics.median(encoded_sizes))
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "macos": platform.mac_ver()[0],
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "codec": "/usr/bin/afconvert FLAC",
        },
        "fixture": {
            "path": _portable_fixture_path(input_path),
            "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "duration_seconds": round(params[3] / params[2], 3),
            "channels": params[0],
            "sample_width_bytes": params[1],
            "sample_rate_hz": params[2],
            "frame_count": params[3],
        },
        "iterations": iterations,
        "compression": {
            "source_bytes": source_bytes,
            "stored_bytes": stored_bytes,
            "bytes_saved": source_bytes - stored_bytes,
            "stored_percent": round(stored_bytes / source_bytes * 100, 2),
            "saved_percent": round((source_bytes - stored_bytes) / source_bytes * 100, 2),
            "pcm_and_parameters_exact_every_iteration": True,
            "encode_ms": _summary(encode_ms),
            "verified_round_trip_ms": _summary(round_trip_ms),
        },
        "foreground_terminal_update_ms": {
            "without_auto_compaction": _summary(baseline_update_ms),
            "with_background_scheduling": _summary(background_update_ms),
            "paired_delta": _summary(delta_ms),
            "background_archive_completed_every_iteration": all(
                count == 1 for count in compressed_counts
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.iterations), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
