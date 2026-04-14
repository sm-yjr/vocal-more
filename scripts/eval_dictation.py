#!/usr/bin/env python3
"""Evaluate dictation quality across several ASR/polish profiles."""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from pathlib import Path
import statistics
import sys
import time
import wave

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more import config as config_module
from vocal_more.config import Config, reload_config
from vocal_more.core.asr_engine import BatchASREngine
from vocal_more.core.text_polisher import TextPolisher
from vocal_more.dictionary import normalize_terms, reload_dictionary


def load_manifest(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("samples", [])


def read_pcm_from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1:
            raise ValueError(f"{path} must be mono WAV")
        if wav_file.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16-bit PCM WAV")
        return wav_file.readframes(wav_file.getnframes())


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def compute_cer(reference: str, hypothesis: str) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return edit_distance(reference, hypothesis) / len(reference)


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


def build_profiles(base_config: Config) -> dict[str, Config]:
    def variant() -> Config:
        return copy.deepcopy(base_config)

    profiles = {}

    baseline = variant()
    baseline.asr.backend = "realtime_ws"
    baseline.asr.use_dictionary_corpus = False
    baseline.enable_polish = True
    baseline.llm.enable_thinking = True
    baseline.llm.temperature = 0.3
    baseline.llm.polish_mode = "always"
    profiles["baseline"] = baseline

    no_think_smart = variant()
    no_think_smart.asr.backend = "realtime_ws"
    no_think_smart.asr.use_dictionary_corpus = False
    no_think_smart.enable_polish = True
    no_think_smart.llm.enable_thinking = False
    no_think_smart.llm.temperature = 0.0
    no_think_smart.llm.polish_mode = "smart"
    profiles["no_think_smart"] = no_think_smart

    manual_ws_corpus = variant()
    manual_ws_corpus.asr.backend = "realtime_ws"
    manual_ws_corpus.asr.use_dictionary_corpus = True
    manual_ws_corpus.enable_polish = True
    manual_ws_corpus.llm.enable_thinking = False
    manual_ws_corpus.llm.temperature = 0.0
    manual_ws_corpus.llm.polish_mode = "smart"
    profiles["manual_ws_corpus"] = manual_ws_corpus

    short_file_corpus = variant()
    short_file_corpus.asr.backend = "short_file"
    short_file_corpus.asr.use_dictionary_corpus = True
    short_file_corpus.enable_polish = True
    short_file_corpus.llm.enable_thinking = False
    short_file_corpus.llm.temperature = 0.0
    short_file_corpus.llm.polish_mode = "smart"
    profiles["short_file_corpus"] = short_file_corpus

    return profiles


def apply_config(config: Config) -> None:
    config_module._config = config
    reload_dictionary()


def run_profile(samples: list[dict], config: Config) -> dict:
    apply_config(config)
    engine = BatchASREngine()
    polisher = TextPolisher()

    rows = []
    latencies = []
    cer_values = []
    clean_matches = []
    expected_term_hits = 0
    expected_term_total = 0

    for sample in samples:
        audio_path = ROOT / sample["audio"]
        pcm_data = read_pcm_from_wav(audio_path)

        start = time.perf_counter()
        raw_text = engine.transcribe(pcm_data)
        final_text = normalize_terms(raw_text)
        if config.enable_polish:
            final_text = polisher.polish(raw_text).polished_text
        latency_ms = (time.perf_counter() - start) * 1000

        cer = compute_cer(sample["reference_text"], final_text)
        cer_values.append(cer)
        latencies.append(latency_ms)

        if sample["group"] == "clean_zh":
            clean_matches.append(final_text == sample["reference_text"])

        for term in sample.get("expected_terms", []):
            expected_term_total += 1
            if term in final_text:
                expected_term_hits += 1

        rows.append(
            {
                "id": sample["id"],
                "group": sample["group"],
                "latency_ms": round(latency_ms, 2),
                "cer": round(cer, 4),
                "raw_text": raw_text,
                "final_text": final_text,
            }
        )

    return {
        "config": asdict(config),
        "rows": rows,
        "metrics": {
            "cer": round(sum(cer_values) / len(cer_values), 4) if cer_values else 0.0,
            "term_recall": round(
                expected_term_hits / expected_term_total, 4
            )
            if expected_term_total
            else 0.0,
            "clean_exact_match_rate": round(
                sum(clean_matches) / len(clean_matches), 4
            )
            if clean_matches
            else 0.0,
            "p50_latency_ms": round(statistics.median(latencies), 2)
            if latencies
            else 0.0,
            "p95_latency_ms": round(percentile(latencies, 0.95), 2)
            if latencies
            else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "eval" / "manifest.yaml"),
        help="Path to eval manifest",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    samples = [sample for sample in load_manifest(manifest_path) if not sample.get("disabled")]

    if not samples:
        print("No active samples found. Mark entries with disabled: false after adding WAV files.")
        return 0

    base_config = reload_config()
    results = {}
    for profile_name, profile_config in build_profiles(base_config).items():
        print(f"Running profile: {profile_name}")
        results[profile_name] = run_profile(samples, profile_config)

    print(yaml.safe_dump(results, allow_unicode=True, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
