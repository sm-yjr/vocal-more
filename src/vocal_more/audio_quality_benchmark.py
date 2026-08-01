"""Offline, corpus-bound audio quality measurements for gain-control A/B tests.

The metrics in this module are intentionally local and deterministic.  They
describe the captured signal; they do not claim perceptual quality or replace
transcription accuracy, calibrated SPL, or a laboratory SNR measurement.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable
import wave

import numpy as np
import yaml


GAIN_MODES = frozenset({"automatic", "manual"})
ACTUAL_GAIN_CONTROLS = frozenset(
    {"apple_agc", "software", "software_fallback"}
)
DBFS_FLOOR = -120.0
ANALYZER_ID = "vocal-more-offline-audio-quality"
ANALYZER_VERSION = 2

_RUNTIME_FACT_FIELDS = (
    "phase",
    "processing_active",
    "device_name",
    "system_default",
    "native_backend",
    "processing_mode",
    "converter_name",
    "source_sample_rate_hz",
    "source_channels",
    "output_sample_rate_hz",
    "output_channels",
    "output_sample_width_bytes",
    "output_encoding",
    "highpass_effective",
    "voice_processing_enabled_observed",
    "agc_enabled_observed",
    "gain_control_verified",
    "fallback_code",
    "fallback_stage",
    "queue_dropped_blocks",
    "runtime_fault_count",
    "runtime_fault_code",
)


class AudioQualityManifestError(ValueError):
    """Raised when a hardware A/B manifest cannot support paired analysis."""


@dataclass(frozen=True)
class AnalysisSettings:
    frame_ms: float = 20.0
    silence_threshold_dbfs: float = -50.0
    clipping_threshold: float = 0.999


@dataclass(frozen=True)
class AudioQualityRecording:
    id: str
    pair_id: str
    audio: str
    audio_path: Path
    audio_sha256: str
    capture_sequence: int
    requested_gain_mode: str
    actual_gain_control: str
    preferred_microphone_mode: str
    active_microphone_mode: str
    runtime_facts: dict[str, Any]
    tags: tuple[str, ...]
    sample_rate_hz: int | None = None
    sample_width_bytes: int | None = None


@dataclass(frozen=True)
class AudioQualityManifest:
    path: Path
    suite_id: str
    environment: dict[str, Any]
    settings: AnalysisSettings
    recordings: tuple[AudioQualityRecording, ...]
    fingerprint: str
    provenance: dict[str, Any]
    runtime_provenance_verified: bool
    provenance_status: str


@dataclass(frozen=True)
class _AudioData:
    samples: np.ndarray
    sample_rate_hz: int
    sample_width_bytes: int


def analyze_audio_file(
    path: str | Path,
    *,
    sample_rate_hz: int | None = None,
    sample_width_bytes: int | None = None,
    settings: AnalysisSettings | None = None,
) -> dict[str, Any]:
    """Analyze one mono PCM WAV or headerless signed 16-bit little-endian PCM."""

    audio = _read_audio(
        Path(path),
        sample_rate_hz=sample_rate_hz,
        sample_width_bytes=sample_width_bytes,
    )
    selected = settings or AnalysisSettings()
    samples = audio.samples
    rate = audio.sample_rate_hz
    duration_ms = len(samples) / rate * 1_000

    absolute = np.abs(samples)
    peak = float(np.max(absolute))
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    peak_dbfs = _to_dbfs(peak)
    rms_dbfs = _to_dbfs(rms)

    clipped = absolute >= selected.clipping_threshold
    clipping_count = int(np.count_nonzero(clipped))
    clipping_longest_run = _longest_true_run(clipped)

    frame_samples = max(1, round(rate * selected.frame_ms / 1_000))
    frame_rms, frame_lengths = _frame_levels(samples, frame_samples)
    silence_limit = 10 ** (selected.silence_threshold_dbfs / 20)
    silent_frames = frame_rms <= silence_limit
    silent_samples = int(np.sum(frame_lengths[silent_frames]))
    silent_segment_samples = _true_weighted_run_lengths(
        silent_frames,
        frame_lengths,
    )

    if bool(np.any(~silent_frames)):
        first_active = int(np.flatnonzero(~silent_frames)[0])
        last_active = int(np.flatnonzero(~silent_frames)[-1])
        leading_samples = int(np.sum(frame_lengths[:first_active]))
        trailing_samples = int(np.sum(frame_lengths[last_active + 1 :]))
    else:
        leading_samples = len(samples)
        trailing_samples = len(samples)

    if bool(np.any(silent_frames)):
        noise_rms = float(statistics.median(frame_rms[silent_frames]))
        noise_source = "silence_frames"
    else:
        noise_rms = float(np.percentile(frame_rms, 10))
        noise_source = "quietest_frames"
    noise_floor_dbfs = _to_dbfs(noise_rms)

    active_levels = frame_rms[~silent_frames]
    if len(active_levels):
        signal_level_dbfs = _to_dbfs(float(np.percentile(active_levels, 90)))
        snr_proxy_db = max(0.0, signal_level_dbfs - noise_floor_dbfs)
    else:
        signal_level_dbfs = noise_floor_dbfs
        snr_proxy_db = 0.0

    return {
        "sample_rate_hz": rate,
        "sample_width_bytes": audio.sample_width_bytes,
        "frame_count": len(samples),
        "duration_ms": _rounded(duration_ms),
        "active_duration_ms": _rounded(
            (len(samples) - silent_samples) / rate * 1_000
        ),
        "peak_dbfs": _rounded(peak_dbfs),
        "rms_dbfs": _rounded(rms_dbfs),
        "crest_factor_db": _rounded(max(0.0, peak_dbfs - rms_dbfs)),
        "dc_offset": _rounded(float(np.mean(samples))),
        "clipping": {
            "threshold": selected.clipping_threshold,
            "sample_count": clipping_count,
            "ratio": _rounded(clipping_count / len(samples)),
            "longest_run_ms": _rounded(
                clipping_longest_run / rate * 1_000
            ),
        },
        "silence": {
            "threshold_dbfs": selected.silence_threshold_dbfs,
            "frame_ms": selected.frame_ms,
            "frame_count": int(np.count_nonzero(silent_frames)),
            "segment_count": len(silent_segment_samples),
            "ratio": _rounded(silent_samples / len(samples)),
            "total_ms": _rounded(silent_samples / rate * 1_000),
            "longest_segment_ms": _rounded(
                max(silent_segment_samples, default=0) / rate * 1_000
            ),
            "leading_ms": _rounded(leading_samples / rate * 1_000),
            "trailing_ms": _rounded(trailing_samples / rate * 1_000),
        },
        "noise_floor_dbfs": _rounded(noise_floor_dbfs),
        "noise_floor_source": noise_source,
        "signal_level_proxy_dbfs": _rounded(signal_level_dbfs),
        "snr_proxy_db": _rounded(snr_proxy_db),
        "spectrum": _spectrum_metrics(samples, rate),
    }


def runtime_capture_facts(session: dict[str, Any]) -> dict[str, Any]:
    """Normalize the runtime facts that must be bound into a capture manifest."""

    def text_value(key: str, *, none_label: str = "unknown") -> str:
        value = str(session.get(key) or "").strip()
        return value or none_label

    def optional_text(key: str) -> str | None:
        value = str(session.get(key) or "").strip()
        return value or None

    return {
        "phase": text_value("phase"),
        "processing_active": session.get("processing_active"),
        "device_name": text_value("device_name"),
        "system_default": session.get("system_default"),
        "native_backend": text_value("native_backend"),
        "processing_mode": text_value("processing_mode"),
        "converter_name": text_value("converter_name", none_label="none"),
        "source_sample_rate_hz": session.get("source_sample_rate_hz"),
        "source_channels": session.get("source_channels"),
        "output_sample_rate_hz": session.get("output_sample_rate_hz"),
        "output_channels": session.get("output_channels"),
        "output_sample_width_bytes": session.get("output_sample_width_bytes"),
        "output_encoding": text_value("output_encoding"),
        "highpass_effective": session.get("highpass_effective"),
        "voice_processing_enabled_observed": session.get(
            "voice_processing_enabled_observed"
        ),
        "agc_enabled_observed": session.get("agc_enabled_observed"),
        "gain_control_verified": session.get("gain_control_verified"),
        "fallback_code": optional_text("fallback_code"),
        "fallback_stage": optional_text("fallback_stage"),
        "queue_dropped_blocks": session.get("queue_dropped_blocks"),
        "runtime_fault_count": session.get("runtime_fault_count"),
        "runtime_fault_code": optional_text("runtime_fault_code"),
    }


def runtime_records_sha256(sessions: list[dict[str, Any]]) -> str:
    """Fingerprint raw runtime records without derived sidecar annotations."""

    records = [
        {
            "id": session.get("id"),
            "runtime_status": session.get("runtime_status"),
        }
        for session in sessions
    ]
    return _canonical_sha256(records)


def load_audio_quality_manifest(
    path: str | Path,
    *,
    _verify_runtime_provenance: bool = True,
) -> AudioQualityManifest:
    """Load a manifest whose every pair contains automatic and manual captures."""

    manifest_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AudioQualityManifestError(f"Cannot read manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise AudioQualityManifestError("Manifest root must be a mapping")
    if raw.get("schema_version") != 1:
        raise AudioQualityManifestError("schema_version must be 1")

    suite_id = _required_string(raw, "suite_id", "manifest")
    provenance = _validate_provenance(raw.get("provenance"))
    environment = _validate_environment(raw.get("environment"))
    settings = _load_settings(raw.get("analysis", {}))
    raw_recordings = raw.get("recordings")
    if not isinstance(raw_recordings, list) or len(raw_recordings) < 2:
        raise AudioQualityManifestError(
            "recordings must contain at least one automatic/manual pair"
        )

    recordings: list[AudioQualityRecording] = []
    fingerprint_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_capture_sequences: set[int] = set()
    modes_by_pair: dict[str, list[str]] = {}
    tags_by_pair: dict[str, list[tuple[str, ...]]] = {}
    for index, item in enumerate(raw_recordings):
        label = f"recordings[{index}]"
        if not isinstance(item, dict):
            raise AudioQualityManifestError(f"{label} must be a mapping")
        recording_id = _required_string(item, "id", label)
        if recording_id in seen_ids:
            raise AudioQualityManifestError(f"Duplicate recording id: {recording_id}")
        seen_ids.add(recording_id)
        pair_id = _required_string(item, "pair_id", label)
        audio_value = _required_string(item, "audio", label)
        capture_sequence = _required_positive_int(
            item.get("capture_sequence"),
            f"{label}.capture_sequence",
        )
        if capture_sequence in seen_capture_sequences:
            raise AudioQualityManifestError(
                f"Duplicate capture_sequence: {capture_sequence}"
            )
        seen_capture_sequences.add(capture_sequence)
        requested_mode = _required_string(item, "requested_gain_mode", label)
        if requested_mode not in GAIN_MODES:
            raise AudioQualityManifestError(
                f"{label}.requested_gain_mode must be automatic or manual"
            )
        actual_control = _required_string(item, "actual_gain_control", label)
        if actual_control not in ACTUAL_GAIN_CONTROLS:
            raise AudioQualityManifestError(
                f"{label}.actual_gain_control must be one of "
                f"{sorted(ACTUAL_GAIN_CONTROLS)}"
            )
        if requested_mode == "manual" and actual_control != "software":
            raise AudioQualityManifestError(
                f"{label}: manual mode must report actual_gain_control=software"
            )
        if requested_mode == "automatic" and actual_control not in {
            "apple_agc",
            "software_fallback",
        }:
            raise AudioQualityManifestError(
                f"{label}: automatic mode must report apple_agc or software_fallback"
            )
        preferred_microphone_mode = _required_string(
            item,
            "preferred_microphone_mode",
            label,
        )
        active_microphone_mode = _required_string(
            item,
            "active_microphone_mode",
            label,
        )
        runtime_facts = _runtime_facts_from_manifest(item, label)

        tags = _string_list(item.get("tags", []), f"{label}.tags")
        rate = _optional_positive_int(item.get("sample_rate_hz"), f"{label}.sample_rate_hz")
        width = _optional_positive_int(
            item.get("sample_width_bytes"),
            f"{label}.sample_width_bytes",
        )
        audio_path = Path(audio_value)
        if not audio_path.is_absolute():
            audio_path = manifest_path.parent / audio_path
        audio_path = audio_path.resolve()
        try:
            _read_audio(
                audio_path,
                sample_rate_hz=rate,
                sample_width_bytes=width,
            )
            digest = _sha256_file(audio_path)
        except (OSError, ValueError, wave.Error) as exc:
            raise AudioQualityManifestError(f"{label}.audio: {exc}") from exc

        recording = AudioQualityRecording(
            id=recording_id,
            pair_id=pair_id,
            audio=_portable_path(audio_value),
            audio_path=audio_path,
            audio_sha256=digest,
            capture_sequence=capture_sequence,
            requested_gain_mode=requested_mode,
            actual_gain_control=actual_control,
            preferred_microphone_mode=preferred_microphone_mode,
            active_microphone_mode=active_microphone_mode,
            runtime_facts=runtime_facts,
            tags=tuple(tags),
            sample_rate_hz=rate,
            sample_width_bytes=width,
        )
        recordings.append(recording)
        modes_by_pair.setdefault(pair_id, []).append(requested_mode)
        tags_by_pair.setdefault(pair_id, []).append(tuple(tags))
        fingerprint_rows.append(
            {
                "id": recording_id,
                "pair_id": pair_id,
                "audio": recording.audio,
                "audio_sha256": digest,
                "capture_sequence": capture_sequence,
                "requested_gain_mode": requested_mode,
                "actual_gain_control": actual_control,
                "preferred_microphone_mode": preferred_microphone_mode,
                "active_microphone_mode": active_microphone_mode,
                "runtime_facts": runtime_facts,
                "tags": tags,
                "sample_rate_hz": rate,
                "sample_width_bytes": width,
            }
        )

    expected_sequences = set(range(1, len(recordings) + 1))
    if seen_capture_sequences != expected_sequences:
        raise AudioQualityManifestError(
            "capture_sequence values must be contiguous from 1 through "
            f"{len(recordings)}"
        )

    for pair_id, modes in modes_by_pair.items():
        if sorted(modes) != ["automatic", "manual"]:
            raise AudioQualityManifestError(
                f"pair {pair_id!r} must contain exactly one automatic and one manual recording"
            )
        pair_tags = tags_by_pair[pair_id]
        if len(set(pair_tags)) != 1:
            raise AudioQualityManifestError(
                f"pair {pair_id!r} recordings must use the same tags"
            )
    capture_protocol = environment.get("capture_protocol")
    if capture_protocol is not None and capture_protocol["pair_count"] != len(
        modes_by_pair
    ):
        raise AudioQualityManifestError(
            "environment.capture_protocol.pair_count must match the number of pairs"
        )

    canonical = {
        "schema_version": 1,
        "suite_id": suite_id,
        "provenance": provenance,
        "environment": environment,
        "analysis": {
            "frame_ms": settings.frame_ms,
            "silence_threshold_dbfs": settings.silence_threshold_dbfs,
            "clipping_threshold": settings.clipping_threshold,
        },
        "recordings": fingerprint_rows,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    runtime_verified = False
    provenance_status = "caller_attestation"
    if provenance["kind"] == "vocal_more_runtime_capture":
        if _verify_runtime_provenance:
            _verify_runtime_sidecar(
                manifest_path=manifest_path,
                manifest_fingerprint=fingerprint,
                provenance=provenance,
                environment=environment,
                recordings=recordings,
            )
            runtime_verified = True
            provenance_status = "runtime_sidecar_verified"
        else:
            provenance_status = "runtime_sidecar_pending"
    return AudioQualityManifest(
        path=manifest_path,
        suite_id=suite_id,
        environment=environment,
        settings=settings,
        recordings=tuple(recordings),
        fingerprint=fingerprint,
        provenance=provenance,
        runtime_provenance_verified=runtime_verified,
        provenance_status=provenance_status,
    )


def build_audio_quality_report(
    manifest: AudioQualityManifest,
) -> dict[str, Any]:
    """Analyze all captures and aggregate paired automatic-minus-manual deltas."""

    analyzed: list[dict[str, Any]] = []
    for recording in manifest.recordings:
        metrics = analyze_audio_file(
            recording.audio_path,
            sample_rate_hz=recording.sample_rate_hz,
            sample_width_bytes=recording.sample_width_bytes,
            settings=manifest.settings,
        )
        analyzed.append(
            {
                "id": recording.id,
                "pair_id": recording.pair_id,
                "audio": recording.audio,
                "audio_sha256": recording.audio_sha256,
                "capture_sequence": recording.capture_sequence,
                "requested_gain_mode": recording.requested_gain_mode,
                "actual_gain_control": recording.actual_gain_control,
                "preferred_microphone_mode": recording.preferred_microphone_mode,
                "active_microphone_mode": recording.active_microphone_mode,
                "runtime": dict(recording.runtime_facts),
                "tags": list(recording.tags),
                "metrics": metrics,
            }
        )

    grouped = {
        mode: _aggregate_mode(
            item for item in analyzed if item["requested_gain_mode"] == mode
        )
        for mode in ("automatic", "manual")
    }
    paired = _paired_deltas(analyzed)
    microphone_mode_validation = evaluate_microphone_mode_pairs(analyzed)
    suite_runtime_validation = _evaluate_suite_runtime(analyzed)
    apple_agc_validation = evaluate_apple_agc_pairs(
        analyzed,
        runtime_provenance_verified=manifest.runtime_provenance_verified,
        suite_runtime_valid=suite_runtime_validation["valid"],
    )
    valid_apple_agc_pairs = {
        pair["pair_id"]
        for pair in apple_agc_validation["pairs"]
        if pair["valid"]
    }
    apple_agc_pair_ids = {
        row["pair_id"]
        for row in analyzed
        if row["requested_gain_mode"] == "automatic"
        and row["actual_gain_control"] == "apple_agc"
        and row["pair_id"] in valid_apple_agc_pairs
    }
    apple_agc_capture_order = _capture_order_summary(
        [row for row in analyzed if row["pair_id"] in apple_agc_pair_ids]
    )
    apple_agc_order_valid = bool(
        apple_agc_capture_order["abba_blocks_valid"]
        and apple_agc_capture_order["counterbalanced"]
    )
    apple_agc_paired = _paired_deltas(
        analyzed,
        automatic_actual_gain_control="apple_agc",
        eligible_pair_ids=valid_apple_agc_pairs,
    )
    fallback_pair_count = sum(
        1
        for row in analyzed
        if row["requested_gain_mode"] == "automatic"
        and row["actual_gain_control"] == "software_fallback"
    )
    report = {
        "schema_version": 1,
        "analyzer": {
            "id": ANALYZER_ID,
            "version": ANALYZER_VERSION,
        },
        "suite_id": manifest.suite_id,
        "manifest_fingerprint": manifest.fingerprint,
        "provenance": {
            "kind": manifest.provenance["kind"],
            "runtime_verified": manifest.runtime_provenance_verified,
            "status": manifest.provenance_status,
        },
        "environment": manifest.environment,
        "analysis": {
            "frame_ms": manifest.settings.frame_ms,
            "silence_threshold_dbfs": manifest.settings.silence_threshold_dbfs,
            "clipping_threshold": manifest.settings.clipping_threshold,
        },
        "recordings": analyzed,
        "capture_order": _capture_order_summary(analyzed),
        "apple_agc_capture_order": apple_agc_capture_order,
        "microphone_mode_validation": microphone_mode_validation,
        "suite_runtime_validation": suite_runtime_validation,
        "apple_agc_validation": apple_agc_validation,
        "by_gain_mode": grouped,
        "paired_deltas_automatic_minus_manual": paired,
        "paired_deltas_apple_agc_minus_manual": apple_agc_paired,
        "comparison_readiness": {
            "total_pair_count": paired["pair_count"],
            "apple_agc_pair_count": apple_agc_paired["pair_count"],
            "software_fallback_pair_count": fallback_pair_count,
            "apple_agc_metric_comparison_available": (
                apple_agc_paired["pair_count"] > 0
                and apple_agc_order_valid
            ),
            "recommended_minimum_pair_count": 10,
            "sample_size_recommendation_met": (
                apple_agc_paired["pair_count"] >= 10
            ),
            "quality_claim_allowed_from_signal_metrics_alone": False,
            "microphone_mode_invalid_pair_count": (
                microphone_mode_validation["invalid_pair_count"]
            ),
            "apple_agc_microphone_mode_excluded_pair_count": sum(
                1
                for pair in microphone_mode_validation["pairs"]
                if not pair["valid"]
                and next(
                    row
                    for row in analyzed
                    if row["pair_id"] == pair["pair_id"]
                    and row["requested_gain_mode"] == "automatic"
                )["actual_gain_control"]
                == "apple_agc"
            ),
            "capture_order_valid_for_apple_agc": apple_agc_order_valid,
            "runtime_provenance_verified": manifest.runtime_provenance_verified,
            "suite_runtime_identity_valid": suite_runtime_validation["valid"],
            "apple_agc_invalid_pair_count": apple_agc_validation[
                "invalid_pair_count"
            ],
        },
        "interpretation": {
            "snr_proxy_is_not_calibrated_snr": True,
            "level_is_dbfs_not_spl": True,
            "positive_delta_means_automatic_is_numerically_higher": True,
            "quality_claim_requires_listening_and_asr_accuracy": True,
            "apple_agc_effect_requires_verified_runtime_provenance": True,
            "general_automatic_delta_is_not_an_apple_agc_effect": True,
            "device_identity_is_not_a_coreaudio_route_uid": True,
        },
    }
    result_fingerprint = hashlib.sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    report["result_fingerprint"] = result_fingerprint
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return report


_AGGREGATE_METRICS = {
    "duration_ms": ("duration_ms",),
    "active_duration_ms": ("active_duration_ms",),
    "peak_dbfs": ("peak_dbfs",),
    "rms_dbfs": ("rms_dbfs",),
    "noise_floor_dbfs": ("noise_floor_dbfs",),
    "snr_proxy_db": ("snr_proxy_db",),
    "clipping_ratio": ("clipping", "ratio"),
    "leading_silence_ms": ("silence", "leading_ms"),
    "trailing_silence_ms": ("silence", "trailing_ms"),
    "speech_band_energy_ratio": ("spectrum", "speech_band_energy_ratio"),
}


def _capture_order_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["capture_sequence"])
    requested_modes = [row["requested_gain_mode"] for row in ordered]
    abba = ["automatic", "manual", "manual", "automatic"]
    abba_blocks_valid = bool(requested_modes) and len(requested_modes) % 4 == 0
    if abba_blocks_valid:
        abba_blocks_valid = all(
            requested_modes[start : start + 4] == abba
            for start in range(0, len(requested_modes), 4)
        )

    pair_sequences: dict[str, dict[str, int]] = {}
    for row in rows:
        pair_sequences.setdefault(row["pair_id"], {})[
            row["requested_gain_mode"]
        ] = row["capture_sequence"]
    automatic_first = sum(
        1
        for pair in pair_sequences.values()
        if pair["automatic"] < pair["manual"]
    )
    manual_first = len(pair_sequences) - automatic_first
    return {
        "requested_modes": requested_modes,
        "abba_blocks_valid": abba_blocks_valid,
        "automatic_first_pair_count": automatic_first,
        "manual_first_pair_count": manual_first,
        "counterbalanced": bool(pair_sequences)
        and automatic_first == manual_first,
    }


_UNKNOWN_MICROPHONE_MODES = frozenset(
    {"", "unknown", "unavailable", "unsupported", "none", "null"}
)


def evaluate_microphone_mode_pairs(
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Validate that paired captures used the same known system mic mode."""

    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_pair.setdefault(str(row["pair_id"]), {})[
            str(row["requested_gain_mode"])
        ] = row

    pairs: list[dict[str, Any]] = []
    for pair_id in sorted(by_pair):
        automatic = by_pair[pair_id]["automatic"]
        manual = by_pair[pair_id]["manual"]
        automatic_preferred = str(
            automatic.get("preferred_microphone_mode") or "unknown"
        ).strip()
        automatic_active = str(
            automatic.get("active_microphone_mode") or "unknown"
        ).strip()
        manual_preferred = str(
            manual.get("preferred_microphone_mode") or "unknown"
        ).strip()
        manual_active = str(
            manual.get("active_microphone_mode") or "unknown"
        ).strip()
        normalized = {
            value.lower()
            for value in (
                automatic_preferred,
                automatic_active,
                manual_preferred,
                manual_active,
            )
        }
        reasons: list[str] = []
        if normalized & _UNKNOWN_MICROPHONE_MODES:
            reasons.append("unknown_microphone_mode")
        if (
            automatic_preferred.lower() != automatic_active.lower()
            or manual_preferred.lower() != manual_active.lower()
        ):
            reasons.append("preferred_active_microphone_mode_mismatch")
        if automatic_preferred.lower() != manual_preferred.lower():
            reasons.append("preferred_microphone_mode_mismatch")
        if automatic_active.lower() != manual_active.lower():
            reasons.append("active_microphone_mode_mismatch")
        pairs.append(
            {
                "pair_id": pair_id,
                "valid": not reasons,
                "reasons": reasons,
                "automatic": {
                    "preferred": automatic_preferred,
                    "active": automatic_active,
                },
                "manual": {
                    "preferred": manual_preferred,
                    "active": manual_active,
                },
            }
        )
    valid_count = sum(1 for pair in pairs if pair["valid"])
    return {
        "pair_count": len(pairs),
        "valid_pair_count": valid_count,
        "invalid_pair_count": len(pairs) - valid_count,
        "pairs": pairs,
    }


def _evaluate_suite_runtime(rows: list[dict[str, Any]]) -> dict[str, Any]:
    identities = sorted(
        {
            (
                str(row["runtime"].get("device_name") or "unknown"),
                row["runtime"].get("system_default"),
            )
            for row in rows
        },
        key=lambda value: (value[0], str(value[1])),
    )
    reasons: list[str] = []
    if any(
        identity[0].strip().lower() in _UNKNOWN_MICROPHONE_MODES
        or not isinstance(identity[1], bool)
        for identity in identities
    ):
        reasons.append("unknown_device_identity")
    if len(identities) != 1:
        reasons.append("suite_device_identity_drift")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "identity_source": "runtime_reported_default_device_query",
        "coreaudio_route_uid_verified": False,
        "identities": [
            {"device_name": name, "system_default": system_default}
            for name, system_default in identities
        ],
    }


def evaluate_apple_agc_pairs(
    rows: Iterable[dict[str, Any]],
    *,
    runtime_provenance_verified: bool,
    suite_runtime_valid: bool,
) -> dict[str, Any]:
    """Fail closed unless a pair isolates AGC on one verified Apple VP path."""

    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_pair.setdefault(str(row["pair_id"]), {})[
            str(row["requested_gain_mode"])
        ] = row
    microphone_pairs = {
        pair["pair_id"]: pair
        for pair in evaluate_microphone_mode_pairs(rows)["pairs"]
    }
    results: list[dict[str, Any]] = []
    for pair_id in sorted(by_pair):
        automatic = by_pair[pair_id]["automatic"]
        manual = by_pair[pair_id]["manual"]
        auto_runtime = automatic["runtime"]
        manual_runtime = manual["runtime"]
        reasons: list[str] = []

        def reject(reason: str) -> None:
            if reason not in reasons:
                reasons.append(reason)

        if not runtime_provenance_verified:
            reject("runtime_provenance_unverified")
        if not suite_runtime_valid:
            reject("suite_device_identity_invalid")
        for reason in microphone_pairs[pair_id]["reasons"]:
            reject(reason)

        if not (
            automatic["actual_gain_control"] == "apple_agc"
            and auto_runtime.get("voice_processing_enabled_observed") is True
            and auto_runtime.get("agc_enabled_observed") is True
            and auto_runtime.get("gain_control_verified") is True
        ):
            reject("automatic_apple_agc_not_verified")
        if not (
            manual["actual_gain_control"] == "software"
            and manual_runtime.get("voice_processing_enabled_observed") is True
            and manual_runtime.get("agc_enabled_observed") is False
            and manual_runtime.get("gain_control_verified") is True
        ):
            reject("manual_voice_processing_agc_off_not_verified")

        for runtime in (auto_runtime, manual_runtime):
            if not (
                runtime.get("phase") == "completed"
                and runtime.get("processing_active") is True
            ):
                reject("runtime_session_incomplete")
            if runtime.get("processing_mode") != "macos_voice_processing":
                reject("voice_processing_path_mismatch")
            if runtime.get("system_default") is not True:
                reject("system_default_input_not_verified")
            if runtime.get("fallback_code") or runtime.get("fallback_stage"):
                reject("fallback_present")
            if (
                runtime.get("queue_dropped_blocks") != 0
                or runtime.get("runtime_fault_count") != 0
                or runtime.get("runtime_fault_code")
            ):
                reject("audio_drop_or_runtime_fault")
            if not (
                runtime.get("output_sample_rate_hz") == 16_000
                and runtime.get("output_channels") == 1
                and runtime.get("output_sample_width_bytes") == 2
                and runtime.get("output_encoding") == "pcm_s16le"
            ):
                reject("runtime_output_format_invalid")

        for row in (automatic, manual):
            if not (
                row["metrics"]["sample_rate_hz"] == 16_000
                and row["metrics"]["sample_width_bytes"] == 2
            ):
                reject("audio_file_format_invalid")

        equality_reasons = {
            "device_name": "device_identity_mismatch",
            "system_default": "device_identity_mismatch",
            "native_backend": "native_backend_mismatch",
            "processing_mode": "voice_processing_path_mismatch",
            "converter_name": "converter_mismatch",
            "source_sample_rate_hz": "source_format_mismatch",
            "source_channels": "source_format_mismatch",
            "output_sample_rate_hz": "output_format_mismatch",
            "output_channels": "output_format_mismatch",
            "output_sample_width_bytes": "output_format_mismatch",
            "output_encoding": "output_format_mismatch",
            "highpass_effective": "highpass_mismatch",
        }
        for field, reason in equality_reasons.items():
            if auto_runtime.get(field) != manual_runtime.get(field):
                reject(reason)
        if str(auto_runtime.get("native_backend") or "").lower() in {
            "",
            "unknown",
            "portaudio",
        }:
            reject("native_backend_not_voice_processing")
        if str(auto_runtime.get("converter_name") or "").lower() in {
            "",
            "none",
            "unknown",
        }:
            reject("converter_not_verified")
        if not (
            isinstance(auto_runtime.get("source_sample_rate_hz"), (int, float))
            and auto_runtime["source_sample_rate_hz"] > 0
            and isinstance(auto_runtime.get("source_channels"), int)
            and auto_runtime["source_channels"] > 0
        ):
            reject("source_format_not_verified")
        if not isinstance(auto_runtime.get("highpass_effective"), bool):
            reject("highpass_not_verified")

        results.append(
            {
                "pair_id": pair_id,
                "valid": not reasons,
                "reasons": reasons,
            }
        )
    valid_count = sum(1 for pair in results if pair["valid"])
    return {
        "pair_count": len(results),
        "valid_pair_count": valid_count,
        "invalid_pair_count": len(results) - valid_count,
        "pairs": results,
    }


def _aggregate_mode(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    return {
        "count": len(rows),
        "actual_gain_controls": dict(
            sorted(Counter(row["actual_gain_control"] for row in rows).items())
        ),
        "metrics": {
            name: _summary(
                [_nested_number(row["metrics"], path) for row in rows]
            )
            for name, path in _AGGREGATE_METRICS.items()
        },
    }


def _paired_deltas(
    rows: list[dict[str, Any]],
    *,
    automatic_actual_gain_control: str | None = None,
    eligible_pair_ids: set[str] | None = None,
) -> dict[str, Any]:
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_pair.setdefault(row["pair_id"], {})[
            row["requested_gain_mode"]
        ] = row

    pair_rows: list[dict[str, Any]] = []
    values: dict[str, list[float]] = {name: [] for name in _AGGREGATE_METRICS}
    for pair_id in sorted(by_pair):
        if eligible_pair_ids is not None and pair_id not in eligible_pair_ids:
            continue
        automatic = by_pair[pair_id]["automatic"]
        manual = by_pair[pair_id]["manual"]
        if (
            automatic_actual_gain_control is not None
            and automatic["actual_gain_control"]
            != automatic_actual_gain_control
        ):
            continue
        deltas: dict[str, float] = {}
        for name, path in _AGGREGATE_METRICS.items():
            delta = _nested_number(automatic["metrics"], path) - _nested_number(
                manual["metrics"], path
            )
            rounded = _rounded(delta)
            deltas[name] = rounded
            values[name].append(rounded)
        pair_rows.append(
            {
                "pair_id": pair_id,
                "automatic_id": automatic["id"],
                "manual_id": manual["id"],
                "automatic_capture_sequence": automatic["capture_sequence"],
                "manual_capture_sequence": manual["capture_sequence"],
                "automatic_actual_gain_control": automatic["actual_gain_control"],
                "deltas": deltas,
            }
        )
    return {
        "pair_count": len(pair_rows),
        "pairs": pair_rows,
        "metrics": (
            {
                name: _summary(metric_values)
                for name, metric_values in values.items()
            }
            if pair_rows
            else {}
        ),
    }


def _read_audio(
    path: Path,
    *,
    sample_rate_hz: int | None,
    sample_width_bytes: int | None,
) -> _AudioData:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        with wave.open(str(path), "rb") as source:
            if source.getnchannels() != 1:
                raise ValueError(
                    f"audio must be mono; received {source.getnchannels()} channels"
                )
            if source.getcomptype() != "NONE":
                raise ValueError("WAV must contain uncompressed PCM")
            width = source.getsampwidth()
            rate = source.getframerate()
            raw = source.readframes(source.getnframes())
        if width not in {1, 2, 3, 4}:
            raise ValueError(f"unsupported PCM sample width: {width} bytes")
    elif suffix in {".pcm", ".raw"}:
        if sample_rate_hz is None:
            raise ValueError("raw PCM requires sample_rate_hz")
        if sample_width_bytes is None:
            raise ValueError("raw PCM requires sample_width_bytes=2")
        if sample_width_bytes != 2:
            raise ValueError("raw PCM supports signed 16-bit little-endian samples only")
        rate = sample_rate_hz
        width = sample_width_bytes
        raw = path.read_bytes()
    else:
        raise ValueError("audio must use .wav, .pcm, or .raw")

    if rate <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if not raw:
        raise ValueError("audio contains no samples")
    if len(raw) % width:
        raise ValueError("PCM byte length is not aligned to the sample width")
    return _AudioData(
        samples=_decode_pcm(raw, width),
        sample_rate_hz=rate,
        sample_width_bytes=width,
    )


def _decode_pcm(raw: bytes, width: int) -> np.ndarray:
    if width == 1:
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128
    if width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    if width == 3:
        triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        values = (
            triples[:, 0].astype(np.int32)
            | (triples[:, 1].astype(np.int32) << 8)
            | (triples[:, 2].astype(np.int32) << 16)
        )
        values = np.where(values & 0x800000, values - 0x1000000, values)
        return values.astype(np.float64) / 8388608
    return np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648


def _frame_levels(
    samples: np.ndarray,
    frame_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    levels: list[float] = []
    lengths: list[int] = []
    for start in range(0, len(samples), frame_samples):
        frame = samples[start : start + frame_samples]
        levels.append(float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))))
        lengths.append(len(frame))
    return np.asarray(levels), np.asarray(lengths, dtype=np.int64)


def _spectrum_metrics(samples: np.ndarray, sample_rate_hz: int) -> dict[str, float]:
    n_fft = min(2_048, max(8, 1 << max(3, (len(samples) - 1).bit_length())))
    hop = max(1, n_fft // 2)
    window = np.hanning(n_fft)
    accumulated = np.zeros(n_fft // 2 + 1, dtype=np.float64)
    window_count = 0
    for start in range(0, len(samples), hop):
        frame = samples[start : start + n_fft]
        if not len(frame):
            continue
        padded = np.zeros(n_fft, dtype=np.float64)
        padded[: len(frame)] = frame
        spectrum = np.fft.rfft(padded * window)
        accumulated += np.square(np.abs(spectrum))
        window_count += 1
        if start + n_fft >= len(samples):
            break
    power = accumulated / max(1, window_count)
    frequencies = np.fft.rfftfreq(n_fft, 1 / sample_rate_hz)
    audible = frequencies >= 20
    total = float(np.sum(power[audible]))
    if total <= np.finfo(np.float64).tiny:
        return {
            "speech_band_energy_ratio": 0.0,
            "low_frequency_energy_ratio": 0.0,
            "high_frequency_energy_ratio": 0.0,
            "spectral_centroid_hz": 0.0,
        }

    def energy_ratio(lower: float, upper: float | None = None) -> float:
        mask = frequencies >= lower
        if upper is not None:
            mask &= frequencies <= upper
        return float(np.sum(power[mask]) / total)

    centroid = float(
        np.sum(frequencies[audible] * power[audible]) / total
    )
    return {
        "speech_band_energy_ratio": _rounded(energy_ratio(300, 3_400)),
        "low_frequency_energy_ratio": _rounded(energy_ratio(20, 250)),
        "high_frequency_energy_ratio": _rounded(energy_ratio(4_000)),
        "spectral_centroid_hz": _rounded(centroid),
    }


def _true_run_lengths(values: np.ndarray) -> list[int]:
    runs: list[int] = []
    current = 0
    for value in values:
        if bool(value):
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _longest_true_run(values: np.ndarray) -> int:
    return max(_true_run_lengths(values), default=0)


def _true_weighted_run_lengths(
    values: np.ndarray,
    weights: np.ndarray,
) -> list[int]:
    runs: list[int] = []
    current = 0
    for value, weight in zip(values, weights, strict=True):
        if bool(value):
            current += int(weight)
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _to_dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        return DBFS_FLOOR
    return max(DBFS_FLOOR, 20 * math.log10(amplitude))


def _rounded(value: float) -> float:
    return round(float(value), 6)


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("cannot summarize an empty metric")
    return {
        "min": _rounded(min(values)),
        "p50": _rounded(statistics.median(values)),
        "mean": _rounded(statistics.fmean(values)),
        "p95": _rounded(float(np.percentile(values, 95))),
        "max": _rounded(max(values)),
    }


def _nested_number(value: dict[str, Any], path: tuple[str, ...]) -> float:
    current: Any = value
    for key in path:
        current = current[key]
    return float(current)


def _load_settings(value: Any) -> AnalysisSettings:
    if not isinstance(value, dict):
        raise AudioQualityManifestError("analysis must be a mapping")
    try:
        frame_ms = float(value.get("frame_ms", 20.0))
        silence = float(value.get("silence_threshold_dbfs", -50.0))
        clipping = float(value.get("clipping_threshold", 0.999))
    except (TypeError, ValueError) as exc:
        raise AudioQualityManifestError("analysis values must be numeric") from exc
    if not 5 <= frame_ms <= 100:
        raise AudioQualityManifestError("analysis.frame_ms must be between 5 and 100")
    if not DBFS_FLOOR <= silence < 0:
        raise AudioQualityManifestError(
            "analysis.silence_threshold_dbfs must be between -120 and 0"
        )
    if not 0.5 <= clipping <= 1:
        raise AudioQualityManifestError(
            "analysis.clipping_threshold must be between 0.5 and 1"
        )
    return AnalysisSettings(frame_ms, silence, clipping)


def _validate_environment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AudioQualityManifestError("environment must be a mapping")
    environment = dict(value)
    for key in ("hardware_model", "microphone", "macos_version", "app_version"):
        _required_string(environment, key, "environment")
    if "capture_protocol" in environment:
        environment["capture_protocol"] = _validate_capture_protocol(
            environment["capture_protocol"]
        )
    distance = environment.get("microphone_distance_cm")
    if distance is not None and (
        isinstance(distance, bool)
        or not isinstance(distance, (int, float))
        or distance <= 0
    ):
        raise AudioQualityManifestError(
            "environment.microphone_distance_cm must be positive"
        )
    try:
        json.dumps(environment, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise AudioQualityManifestError(
            "environment values must be JSON-serializable"
        ) from exc
    return environment


def _validate_provenance(value: Any) -> dict[str, Any]:
    """Validate who supplied the runtime facts and how they are bound."""

    if not isinstance(value, dict):
        raise AudioQualityManifestError("provenance must be a mapping")
    kind = value.get("kind")
    if kind == "caller_attestation":
        if set(value) != {"kind"}:
            raise AudioQualityManifestError(
                "caller_attestation provenance only accepts the kind field"
            )
        return {"kind": kind}
    if kind != "vocal_more_runtime_capture":
        raise AudioQualityManifestError(
            "provenance.kind must be caller_attestation or "
            "vocal_more_runtime_capture"
        )

    expected_fields = {
        "kind",
        "capture_tool",
        "capture_schema_version",
        "runtime_sidecar",
        "runtime_records_sha256",
    }
    if set(value) != expected_fields:
        raise AudioQualityManifestError(
            "vocal_more_runtime_capture provenance fields are incomplete or unknown"
        )
    if value.get("capture_tool") != "scripts/capture_audio_quality_ab.py":
        raise AudioQualityManifestError(
            "provenance.capture_tool is not a supported runtime capture tool"
        )
    if value.get("capture_schema_version") != 2:
        raise AudioQualityManifestError(
            "provenance.capture_schema_version must be 2"
        )
    sidecar = value.get("runtime_sidecar")
    if not isinstance(sidecar, str) or not sidecar.strip():
        raise AudioQualityManifestError(
            "provenance.runtime_sidecar must be a non-empty relative path"
        )
    sidecar_path = Path(sidecar)
    if sidecar_path.as_posix() != "capture-runtime-status.json":
        raise AudioQualityManifestError(
            "provenance.runtime_sidecar must be capture-runtime-status.json"
        )
    digest = value.get("runtime_records_sha256")
    if not _is_sha256(digest):
        raise AudioQualityManifestError(
            "provenance.runtime_records_sha256 must be a lowercase SHA-256 digest"
        )
    return {
        "kind": kind,
        "capture_tool": value["capture_tool"],
        "capture_schema_version": value["capture_schema_version"],
        "runtime_sidecar": sidecar_path.as_posix(),
        "runtime_records_sha256": digest,
    }


def _runtime_facts_from_manifest(
    item: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Read the normalized runtime fact contract from one recording."""

    missing = [field for field in _RUNTIME_FACT_FIELDS if field not in item]
    if missing:
        raise AudioQualityManifestError(
            f"{label} is missing runtime facts: {', '.join(missing)}"
        )
    facts = {field: item[field] for field in _RUNTIME_FACT_FIELDS}

    for field in (
        "phase",
        "device_name",
        "native_backend",
        "processing_mode",
        "converter_name",
        "output_encoding",
    ):
        value = facts[field]
        if not isinstance(value, str) or not value.strip():
            raise AudioQualityManifestError(
                f"{label}.{field} must be a non-empty string"
            )
        facts[field] = value.strip()

    for field in (
        "processing_active",
        "system_default",
        "highpass_effective",
        "gain_control_verified",
    ):
        if not isinstance(facts[field], bool):
            raise AudioQualityManifestError(
                f"{label}.{field} must be boolean"
            )
    for field in (
        "voice_processing_enabled_observed",
        "agc_enabled_observed",
    ):
        if facts[field] is not None and not isinstance(facts[field], bool):
            raise AudioQualityManifestError(
                f"{label}.{field} must be boolean or null"
            )

    source_rate = facts["source_sample_rate_hz"]
    if (
        isinstance(source_rate, bool)
        or not isinstance(source_rate, (int, float))
        or not math.isfinite(source_rate)
        or source_rate <= 0
    ):
        raise AudioQualityManifestError(
            f"{label}.source_sample_rate_hz must be a positive finite number"
        )
    for field in (
        "source_channels",
        "output_sample_rate_hz",
        "output_channels",
        "output_sample_width_bytes",
    ):
        if (
            isinstance(facts[field], bool)
            or not isinstance(facts[field], int)
            or facts[field] <= 0
        ):
            raise AudioQualityManifestError(
                f"{label}.{field} must be a positive integer"
            )
    for field in ("queue_dropped_blocks", "runtime_fault_count"):
        if (
            isinstance(facts[field], bool)
            or not isinstance(facts[field], int)
            or facts[field] < 0
        ):
            raise AudioQualityManifestError(
                f"{label}.{field} must be a non-negative integer"
            )
    for field in ("fallback_code", "fallback_stage", "runtime_fault_code"):
        value = facts[field]
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise AudioQualityManifestError(
                f"{label}.{field} must be a non-empty string or null"
            )
        facts[field] = value.strip() if isinstance(value, str) else None
    return facts


def _verify_runtime_sidecar(
    *,
    manifest_path: Path,
    manifest_fingerprint: str,
    provenance: dict[str, Any],
    environment: dict[str, Any],
    recordings: list[AudioQualityRecording],
) -> None:
    """Verify generated runtime evidence before enabling Apple-specific deltas."""

    sidecar_path = (manifest_path.parent / provenance["runtime_sidecar"]).resolve()
    try:
        sidecar_path.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise AudioQualityManifestError(
            "runtime sidecar path escapes the manifest directory"
        ) from exc
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AudioQualityManifestError(
            f"Cannot read runtime sidecar: {exc}"
        ) from exc
    if not isinstance(sidecar, dict):
        raise AudioQualityManifestError("runtime sidecar root must be an object")
    if sidecar.get("schema_version") != 2:
        raise AudioQualityManifestError("runtime sidecar schema_version must be 2")
    if sidecar.get("manifest_fingerprint") != manifest_fingerprint:
        raise AudioQualityManifestError(
            "runtime sidecar manifest fingerprint does not match the manifest"
        )

    sessions = sidecar.get("sessions")
    if not isinstance(sessions, list) or any(
        not isinstance(session, dict) for session in sessions
    ):
        raise AudioQualityManifestError(
            "runtime sidecar sessions must be a list of objects"
        )
    expected_hash = provenance["runtime_records_sha256"]
    sidecar_hash = sidecar.get("runtime_records_sha256")
    if sidecar_hash != expected_hash:
        raise AudioQualityManifestError(
            "runtime sidecar hash does not match manifest provenance"
        )
    try:
        observed_hash = runtime_records_sha256(sessions)
    except (TypeError, ValueError) as exc:
        raise AudioQualityManifestError(
            f"runtime sidecar hash cannot be computed: {exc}"
        ) from exc
    if observed_hash != expected_hash:
        raise AudioQualityManifestError(
            "runtime sidecar hash does not match its session records"
        )

    by_id: dict[str, dict[str, Any]] = {}
    for session in sessions:
        recording_id = session.get("id")
        if not isinstance(recording_id, str) or not recording_id.strip():
            raise AudioQualityManifestError(
                "runtime sidecar session id must be a non-empty string"
            )
        if recording_id in by_id:
            raise AudioQualityManifestError(
                f"runtime sidecar has duplicate session id: {recording_id}"
            )
        by_id[recording_id] = session
    expected_ids = {recording.id for recording in recordings}
    if set(by_id) != expected_ids:
        raise AudioQualityManifestError(
            "runtime sidecar session ids do not match manifest recordings"
        )

    runtime_device_names = {
        recording.runtime_facts["device_name"] for recording in recordings
    }
    if runtime_device_names != {environment["microphone"]}:
        raise AudioQualityManifestError(
            "runtime device identity does not match environment.microphone"
        )

    for recording in recordings:
        evidence = by_id[recording.id]
        status = evidence.get("runtime_status")
        if not isinstance(status, dict):
            raise AudioQualityManifestError(
                f"runtime sidecar {recording.id} status must be an object"
            )
        if status.get("requested_gain_mode") != recording.requested_gain_mode:
            raise AudioQualityManifestError(
                f"runtime sidecar {recording.id} requested gain mode mismatch"
            )
        if status.get("gain_control") != recording.actual_gain_control:
            raise AudioQualityManifestError(
                f"runtime sidecar {recording.id} gain control mismatch"
            )
        if (
            _microphone_mode_from_status(status, "preferred_microphone_mode")
            != recording.preferred_microphone_mode
            or _microphone_mode_from_status(status, "active_microphone_mode")
            != recording.active_microphone_mode
        ):
            raise AudioQualityManifestError(
                f"runtime sidecar {recording.id} microphone mode mismatch"
            )
        if runtime_capture_facts(status) != recording.runtime_facts:
            raise AudioQualityManifestError(
                f"runtime sidecar {recording.id} facts do not match manifest"
            )


def _microphone_mode_from_status(status: dict[str, Any], field: str) -> str:
    value = str(status.get(field) or "").strip()
    return value or "unknown"


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_capture_protocol(value: Any) -> dict[str, Any]:
    label = "environment.capture_protocol"
    if not isinstance(value, dict):
        raise AudioQualityManifestError(f"{label} must be a mapping")
    protocol = dict(value)
    if protocol.get("order") != "ABBA":
        raise AudioQualityManifestError(f"{label}.order must be ABBA")
    pair_count = _required_positive_int(
        protocol.get("pair_count"),
        f"{label}.pair_count",
    )
    if pair_count < 2 or pair_count % 2:
        raise AudioQualityManifestError(
            f"{label}.pair_count must be a positive even number of at least 2"
        )
    stimulus_count = _required_positive_int(
        protocol.get("stimulus_count"),
        f"{label}.stimulus_count",
    )
    if stimulus_count != pair_count:
        raise AudioQualityManifestError(
            f"{label}.stimulus_count must equal pair_count"
        )
    digest = protocol.get("stimulus_set_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise AudioQualityManifestError(
            f"{label}.stimulus_set_sha256 must be a lowercase SHA-256 digest"
        )
    if protocol.get("stimulus_text_persisted") is not False:
        raise AudioQualityManifestError(
            f"{label}.stimulus_text_persisted must be false"
        )
    for key in ("highpass_enabled", "soft_limiter_enabled"):
        if not isinstance(protocol.get(key), bool):
            raise AudioQualityManifestError(f"{label}.{key} must be boolean")
    if protocol.get("capture_host") not in {
        "terminal_process",
        "vocal_more_app",
    }:
        raise AudioQualityManifestError(
            f"{label}.capture_host is not supported"
        )
    for key in ("duration_seconds", "manual_gain_db", "highpass_hz"):
        item = protocol.get(key)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise AudioQualityManifestError(f"{label}.{key} must be numeric")
    if protocol["duration_seconds"] < 2:
        raise AudioQualityManifestError(
            f"{label}.duration_seconds must be at least 2"
        )
    if not -6 <= protocol["manual_gain_db"] <= 34:
        raise AudioQualityManifestError(
            f"{label}.manual_gain_db must be between -6 and 34"
        )
    if protocol["highpass_hz"] <= 0:
        raise AudioQualityManifestError(f"{label}.highpass_hz must be positive")
    return protocol


def _required_string(value: dict[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise AudioQualityManifestError(f"{label}.{key} must be a non-empty string")
    return item.strip()


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise AudioQualityManifestError(f"{label} must be a list of strings")
    return [item.strip() for item in value]


def _optional_positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AudioQualityManifestError(f"{label} must be a positive integer")
    return value


def _required_positive_int(value: Any, label: str) -> int:
    parsed = _optional_positive_int(value, label)
    if parsed is None:
        raise AudioQualityManifestError(f"{label} must be a positive integer")
    return parsed


def _portable_path(value: str) -> str:
    path = Path(value)
    return path.name if path.is_absolute() else path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
