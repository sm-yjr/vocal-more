from __future__ import annotations

import json
import math
from pathlib import Path
import wave

import numpy as np
import pytest
import yaml


def _write_wav(path: Path, samples: np.ndarray, *, sample_rate: int = 16_000) -> None:
    pcm = np.clip(np.asarray(samples), -1.0, 1.0 - (1 / 32768))
    encoded = np.rint(pcm * 32768).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(encoded.tobytes())


def _tone_with_silence(
    *,
    amplitude: float,
    frequency_hz: float = 1_000,
    sample_rate: int = 16_000,
) -> np.ndarray:
    leading = np.zeros(sample_rate // 10, dtype=np.float64)
    time = np.arange(sample_rate * 8 // 10) / sample_rate
    active = amplitude * np.sin(2 * math.pi * frequency_hz * time)
    trailing = np.zeros(sample_rate // 10, dtype=np.float64)
    return np.concatenate((leading, active, trailing))


def _runtime_facts(mode: str, **overrides):
    automatic = mode == "automatic"
    facts = {
        "phase": "completed",
        "processing_active": True,
        "device_name": "MacBook Pro Microphone",
        "system_default": True,
        "native_backend": "objective_cpp",
        "processing_mode": "macos_voice_processing",
        "converter_name": "AVAudioConverter",
        "source_sample_rate_hz": 48_000.0,
        "source_channels": 1,
        "output_sample_rate_hz": 16_000,
        "output_channels": 1,
        "output_sample_width_bytes": 2,
        "output_encoding": "pcm_s16le",
        "highpass_effective": True,
        "voice_processing_enabled_observed": True,
        "agc_enabled_observed": automatic,
        "gain_control_verified": True,
        "fallback_code": None,
        "fallback_stage": None,
        "queue_dropped_blocks": 0,
        "runtime_fault_count": 0,
        "runtime_fault_code": None,
    }
    facts.update(overrides)
    return facts


def _manifest(tmp_path: Path, *, auto: Path, manual: Path) -> Path:
    payload = {
        "schema_version": 1,
        "suite_id": "built-in-mic-whisper-v1",
        "provenance": {"kind": "caller_attestation"},
        "environment": {
            "hardware_model": "MacBookPro18,3",
            "microphone": "built-in microphone",
            "macos_version": "26.0",
            "app_version": "0.3.12",
            "room": "quiet office",
            "microphone_distance_cm": 45,
        },
        "recordings": [
            {
                "id": "whisper-01-auto",
                "pair_id": "whisper-01",
                "audio": auto.name,
                "capture_sequence": 1,
                "requested_gain_mode": "automatic",
                "actual_gain_control": "apple_agc",
                "preferred_microphone_mode": "standard",
                "active_microphone_mode": "standard",
                **_runtime_facts("automatic"),
                "tags": ["whisper", "quiet_office"],
            },
            {
                "id": "whisper-01-manual",
                "pair_id": "whisper-01",
                "audio": manual.name,
                "capture_sequence": 2,
                "requested_gain_mode": "manual",
                "actual_gain_control": "software",
                "preferred_microphone_mode": "standard",
                "active_microphone_mode": "standard",
                **_runtime_facts("manual"),
                "tags": ["whisper", "quiet_office"],
            },
        ],
    }
    path = tmp_path / "audio-quality.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_analyze_wav_reports_level_silence_length_and_spectrum(tmp_path):
    from vocal_more.audio_quality_benchmark import analyze_audio_file

    fixture = tmp_path / "tone.wav"
    _write_wav(fixture, _tone_with_silence(amplitude=0.5))

    metrics = analyze_audio_file(fixture)

    assert metrics["sample_rate_hz"] == 16_000
    assert metrics["frame_count"] == 16_000
    assert metrics["duration_ms"] == pytest.approx(1_000)
    assert metrics["peak_dbfs"] == pytest.approx(-6.02, abs=0.03)
    assert metrics["rms_dbfs"] == pytest.approx(-10.0, abs=0.05)
    assert metrics["clipping"]["sample_count"] == 0
    assert metrics["silence"]["leading_ms"] == pytest.approx(100, abs=20)
    assert metrics["silence"]["trailing_ms"] == pytest.approx(100, abs=20)
    assert metrics["silence"]["ratio"] == pytest.approx(0.2, abs=0.05)
    assert metrics["spectrum"]["speech_band_energy_ratio"] > 0.98
    assert metrics["spectrum"]["spectral_centroid_hz"] == pytest.approx(
        1_000,
        abs=30,
    )
    assert metrics["snr_proxy_db"] > 60


def test_analyze_wav_reports_clipping_and_quiet_segment_noise_floor(tmp_path):
    from vocal_more.audio_quality_benchmark import analyze_audio_file

    sample_rate = 16_000
    quiet = np.full(sample_rate // 2, 0.001, dtype=np.float64)
    clipped = np.ones(sample_rate // 2, dtype=np.float64)
    fixture = tmp_path / "clipped.wav"
    _write_wav(fixture, np.concatenate((quiet, clipped)))

    metrics = analyze_audio_file(fixture)

    assert metrics["clipping"]["sample_count"] == sample_rate // 2
    assert metrics["clipping"]["ratio"] == pytest.approx(0.5)
    assert metrics["noise_floor_dbfs"] == pytest.approx(-60.2, abs=0.3)
    assert metrics["noise_floor_source"] == "silence_frames"


def test_silence_longest_segment_uses_partial_frame_length(tmp_path):
    from vocal_more.audio_quality_benchmark import analyze_audio_file

    fixture = tmp_path / "partial-frame.wav"
    samples = np.concatenate((np.full(20, 0.5), np.zeros(25)))
    _write_wav(fixture, samples, sample_rate=1_000)

    metrics = analyze_audio_file(fixture)

    assert metrics["silence"]["longest_segment_ms"] == 25


def test_analyze_raw_pcm_requires_explicit_format_metadata(tmp_path):
    from vocal_more.audio_quality_benchmark import analyze_audio_file

    fixture = tmp_path / "sample.pcm"
    fixture.write_bytes(np.array([0, 16384, -16384, 0], dtype="<i2").tobytes())

    with pytest.raises(ValueError, match="sample_rate_hz"):
        analyze_audio_file(fixture)

    metrics = analyze_audio_file(
        fixture,
        sample_rate_hz=8_000,
        sample_width_bytes=2,
    )

    assert metrics["sample_rate_hz"] == 8_000
    assert metrics["frame_count"] == 4
    assert metrics["peak_dbfs"] == pytest.approx(-6.02, abs=0.01)


def test_analyze_rejects_stereo_wav(tmp_path):
    from vocal_more.audio_quality_benchmark import analyze_audio_file

    fixture = tmp_path / "stereo.wav"
    with wave.open(str(fixture), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 100)

    with pytest.raises(ValueError, match="mono"):
        analyze_audio_file(fixture)


def test_manifest_requires_complete_automatic_manual_pairs(tmp_path):
    from vocal_more.audio_quality_benchmark import (
        AudioQualityManifestError,
        load_audio_quality_manifest,
    )

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.4))
    _write_wav(manual, _tone_with_silence(amplitude=0.3))
    path = _manifest(tmp_path, auto=auto, manual=manual)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["recordings"].pop()
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(AudioQualityManifestError, match="automatic.*manual"):
        load_audio_quality_manifest(path)


def test_manifest_rejects_ambiguous_capture_order_and_pair_tags(tmp_path):
    from vocal_more.audio_quality_benchmark import (
        AudioQualityManifestError,
        load_audio_quality_manifest,
    )

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.4))
    _write_wav(manual, _tone_with_silence(amplitude=0.3))
    path = _manifest(tmp_path, auto=auto, manual=manual)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["recordings"][1]["capture_sequence"] = 1
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(AudioQualityManifestError, match="capture_sequence"):
        load_audio_quality_manifest(path)

    payload["recordings"][1]["capture_sequence"] = 2
    payload["recordings"][1]["tags"] = ["normal_volume"]
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(AudioQualityManifestError, match="same tags"):
        load_audio_quality_manifest(path)

    payload["recordings"][1]["tags"] = payload["recordings"][0]["tags"]
    payload["recordings"][1]["capture_sequence"] = 4
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(AudioQualityManifestError, match="contiguous"):
        load_audio_quality_manifest(path)


def test_manifest_fingerprint_binds_metadata_and_audio(tmp_path):
    from vocal_more.audio_quality_benchmark import load_audio_quality_manifest

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.4))
    _write_wav(manual, _tone_with_silence(amplitude=0.3))
    path = _manifest(tmp_path, auto=auto, manual=manual)

    first = load_audio_quality_manifest(path)
    _write_wav(auto, _tone_with_silence(amplitude=0.2))
    audio_changed = load_audio_quality_manifest(path)
    assert first.fingerprint != audio_changed.fingerprint

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["environment"]["microphone_distance_cm"] = 25
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    metadata_changed = load_audio_quality_manifest(path)
    assert audio_changed.fingerprint != metadata_changed.fingerprint


def test_manifest_rejects_capture_protocol_that_does_not_match_pairs(tmp_path):
    from vocal_more.audio_quality_benchmark import (
        AudioQualityManifestError,
        load_audio_quality_manifest,
    )

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.4))
    _write_wav(manual, _tone_with_silence(amplitude=0.3))
    path = _manifest(tmp_path, auto=auto, manual=manual)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["environment"]["capture_protocol"] = {
        "order": "ABBA",
        "pair_count": 4,
        "duration_seconds": 5,
        "manual_gain_db": 18,
        "highpass_enabled": True,
        "highpass_hz": 200,
        "soft_limiter_enabled": True,
        "capture_host": "terminal_process",
        "stimulus_count": 4,
        "stimulus_set_sha256": "0" * 64,
        "stimulus_text_persisted": False,
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(AudioQualityManifestError, match="pair_count"):
        load_audio_quality_manifest(path)


def test_build_report_keeps_actual_gain_path_and_paired_deltas(tmp_path):
    from vocal_more.audio_quality_benchmark import (
        build_audio_quality_report,
        load_audio_quality_manifest,
    )

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.5))
    _write_wav(manual, _tone_with_silence(amplitude=0.25))
    manifest = load_audio_quality_manifest(
        _manifest(tmp_path, auto=auto, manual=manual)
    )

    report = build_audio_quality_report(manifest)

    assert report["schema_version"] == 1
    assert report["analyzer"] == {
        "id": "vocal-more-offline-audio-quality",
        "version": 2,
    }
    assert len(report["result_fingerprint"]) == 64
    assert report["manifest_fingerprint"] == manifest.fingerprint
    assert len(report["recordings"]) == 2
    automatic = next(
        item
        for item in report["recordings"]
        if item["requested_gain_mode"] == "automatic"
    )
    assert automatic["actual_gain_control"] == "apple_agc"
    assert report["by_gain_mode"]["automatic"]["count"] == 1
    assert report["by_gain_mode"]["manual"]["count"] == 1
    delta = report["paired_deltas_automatic_minus_manual"]
    assert delta["pair_count"] == 1
    assert delta["metrics"]["peak_dbfs"]["mean"] == pytest.approx(6.02, abs=0.05)
    assert delta["metrics"]["duration_ms"]["mean"] == 0
    assert report["interpretation"]["snr_proxy_is_not_calibrated_snr"] is True
    assert report["comparison_readiness"][
        "apple_agc_metric_comparison_available"
    ] is False
    assert report["provenance"]["runtime_verified"] is False
    assert report["provenance"]["kind"] == "caller_attestation"
    assert report["paired_deltas_apple_agc_minus_manual"]["pair_count"] == 0

    repeated = build_audio_quality_report(manifest)
    assert repeated["result_fingerprint"] == report["result_fingerprint"]
    assert repeated["capture_order"] == {
        "requested_modes": ["automatic", "manual"],
        "abba_blocks_valid": False,
        "automatic_first_pair_count": 1,
        "manual_first_pair_count": 0,
        "counterbalanced": False,
    }


def test_report_excludes_software_fallback_from_apple_agc_comparison(tmp_path):
    from vocal_more.audio_quality_benchmark import (
        build_audio_quality_report,
        load_audio_quality_manifest,
    )

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.5))
    _write_wav(manual, _tone_with_silence(amplitude=0.25))
    path = _manifest(tmp_path, auto=auto, manual=manual)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["recordings"][0]["actual_gain_control"] = "software_fallback"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    report = build_audio_quality_report(load_audio_quality_manifest(path))

    assert report["comparison_readiness"]["total_pair_count"] == 1
    assert report["comparison_readiness"]["apple_agc_pair_count"] == 0
    assert report["comparison_readiness"]["software_fallback_pair_count"] == 1
    assert report["comparison_readiness"][
        "apple_agc_metric_comparison_available"
    ] is False
    assert report["paired_deltas_apple_agc_minus_manual"]["pair_count"] == 0
    assert report["paired_deltas_automatic_minus_manual"]["pair_count"] == 1


@pytest.mark.parametrize(
    ("automatic_mode", "manual_mode", "reason"),
    [
        ("voice_isolation", "standard", "active_microphone_mode_mismatch"),
        ("unknown", "standard", "unknown_microphone_mode"),
        (
            "voice_isolation",
            "voice_isolation",
            "preferred_active_microphone_mode_mismatch",
        ),
    ],
)
def test_report_excludes_uncomparable_microphone_modes_from_apple_agc(
    tmp_path,
    automatic_mode,
    manual_mode,
    reason,
):
    from vocal_more.audio_quality_benchmark import (
        build_audio_quality_report,
        load_audio_quality_manifest,
    )

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.5))
    _write_wav(manual, _tone_with_silence(amplitude=0.25))
    path = _manifest(tmp_path, auto=auto, manual=manual)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["recordings"][0]["active_microphone_mode"] = automatic_mode
    payload["recordings"][1]["active_microphone_mode"] = manual_mode
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    report = build_audio_quality_report(load_audio_quality_manifest(path))

    assert report["paired_deltas_automatic_minus_manual"]["pair_count"] == 1
    assert report["paired_deltas_apple_agc_minus_manual"]["pair_count"] == 0
    validation = report["microphone_mode_validation"]
    assert validation["invalid_pair_count"] == 1
    assert reason in validation["pairs"][0]["reasons"]


def test_repository_contains_machine_readable_hardware_manifest_schema():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (root / "eval" / "audio-quality-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert schema["$schema"].endswith("2020-12/schema")
    assert {"suite_id", "environment", "recordings"}.issubset(
        schema["required"]
    )
    assert schema["properties"]["recordings"]["minItems"] == 2


def test_audio_quality_cli_writes_json(tmp_path):
    from scripts.benchmark_audio_quality import main

    auto = tmp_path / "auto.wav"
    manual = tmp_path / "manual.wav"
    _write_wav(auto, _tone_with_silence(amplitude=0.5))
    _write_wav(manual, _tone_with_silence(amplitude=0.25))
    manifest = _manifest(tmp_path, auto=auto, manual=manual)
    output = tmp_path / "report.json"

    exit_code = main(
        ["--manifest", str(manifest), "--output", str(output)]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["suite_id"] == "built-in-mic-whisper-v1"
    assert payload["paired_deltas_automatic_minus_manual"]["pair_count"] == 1
