"""Reproducible quality and latency benchmark primitives.

The module deliberately keeps scoring separate from network execution. A run
adapter may call Vocal More, Typeless, or another system, but every adapter
must emit the same run schema and bind its results to the same manifest
fingerprint before a comparison is considered valid.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import threading
import time
import unicodedata
import uuid
import wave
from datetime import datetime, timezone
from typing import Any, Iterable

import yaml


REQUIRED_COVERAGE = frozenset(
    {
        "normal_volume",
        "whisper",
        "ambient_noise",
        "zh",
        "en",
        "mixed",
        "fillers",
        "repetition",
        "self_correction",
        "list",
        "proper_noun",
    }
)
TRACE_LEVELS = frozenset(
    {"protocol_replay", "paced_replay", "live_end_to_end"}
)
LANGUAGES = frozenset({"zh", "en", "mixed"})


class ManifestValidationError(ValueError):
    """Raised when a benchmark manifest cannot support comparable scoring."""


@dataclass(frozen=True)
class BenchmarkSample:
    id: str
    audio: str
    reference_text: str
    language: str
    tags: tuple[str, ...]
    expected_terms: tuple[str, ...]
    source: str
    disabled: bool = False


@dataclass(frozen=True)
class BenchmarkManifest:
    path: Path
    schema_version: int
    suite_id: str
    truth_version: str
    samples: tuple[BenchmarkSample, ...]
    fingerprint: str

    @property
    def active_samples(self) -> tuple[BenchmarkSample, ...]:
        return tuple(sample for sample in self.samples if not sample.disabled)

    @property
    def coverage(self) -> frozenset[str]:
        return frozenset(
            tag
            for sample in self.active_samples
            for tag in sample.tags
        )


class LiveTraceRecorder:
    """Opt-in timing-only recorder for a full application dictation session.

    The recorder intentionally accepts only a small metadata allowlist. It
    never receives transcript or audio content, which keeps benchmark timing
    collection separate from private dictation data.
    """

    _METADATA_KEYS = frozenset(
        {
            "app_version",
            "model",
            "mode",
            "auto_paste",
            "sample_id",
            "audio_delivery",
            "result_source",
            "fallback_reason",
            "error_code",
        }
    )

    def __init__(
        self,
        output_dir: str | Path,
        *,
        clock=time.monotonic,
        wall_clock=None,
        session_id_factory=None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self._clock = clock
        self._wall_clock = wall_clock or (
            lambda: datetime.now(timezone.utc).isoformat()
        )
        self._session_id_factory = session_id_factory or (
            lambda: uuid.uuid4().hex
        )
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._session_id = ""
        self._created_at = ""
        self._metadata: dict[str, Any] = {}
        self._events_ms: dict[str, float] = {}

    @property
    def active(self) -> bool:
        with self._lock:
            return self._started_at is not None

    @property
    def events_ms(self) -> dict[str, float]:
        with self._lock:
            return dict(self._events_ms)

    def begin(
        self,
        *,
        started_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Begin one session; return false if another session is active."""
        with self._lock:
            if self._started_at is not None:
                return False
            self._started_at = (
                float(started_at)
                if started_at is not None
                else float(self._clock())
            )
            self._session_id = str(self._session_id_factory())
            self._created_at = str(self._wall_clock())
            self._metadata = self._sanitize_metadata(metadata or {})
            self._events_ms = {}
            return True

    def mark(self, event: str, *, at: float | None = None) -> bool:
        """Record the first occurrence of an event relative to trigger time."""
        with self._lock:
            if self._started_at is None or event in self._events_ms:
                return False
            event_time = float(at) if at is not None else float(self._clock())
            self._events_ms[event] = round(
                max(0.0, (event_time - self._started_at) * 1000),
                3,
            )
            return True

    def finish(
        self,
        *,
        status: str,
        insert_completed: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Path | None:
        """Atomically persist one timing trace and clear active state."""
        with self._lock:
            if self._started_at is None:
                return None
            if insert_completed and "insert_completed" not in self._events_ms:
                event_time = float(self._clock())
                self._events_ms["insert_completed"] = round(
                    max(0.0, (event_time - self._started_at) * 1000),
                    3,
                )
            combined_metadata = dict(self._metadata)
            combined_metadata.update(self._sanitize_metadata(metadata or {}))
            payload = {
                "schema_version": 1,
                "session_id": self._session_id,
                "created_at": self._created_at,
                "status": str(status),
                "events_ms": dict(self._events_ms),
                "metadata": combined_metadata,
            }
            session_id = self._session_id
            self._started_at = None
            self._session_id = ""
            self._created_at = ""
            self._metadata = {}
            self._events_ms = {}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        destination = self.output_dir / f"{session_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination

    @classmethod
    def _sanitize_metadata(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in metadata.items()
            if key in cls._METADATA_KEYS
            and isinstance(value, (str, int, float, bool, type(None)))
        }


def live_trace_recorder_from_env(
    environment: dict[str, str] | os._Environ[str] | None = None,
) -> LiveTraceRecorder | None:
    """Create the timing-only recorder only after explicit environment opt-in."""
    values = os.environ if environment is None else environment
    output_dir = str(values.get("VOCAL_MORE_BENCHMARK_TRACE_DIR", "")).strip()
    if not output_dir:
        return None
    return LiveTraceRecorder(output_dir)


def load_manifest(path: str | Path) -> BenchmarkManifest:
    """Load and validate a versioned corpus manifest and its active WAV files."""
    manifest_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestValidationError(f"Cannot read manifest: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestValidationError("Manifest root must be a mapping")
    if raw.get("schema_version") != 1:
        raise ManifestValidationError("schema_version must be 1")

    suite_id = _required_string(raw, "suite_id", "manifest")
    truth_version = _required_string(raw, "truth_version", "manifest")
    raw_samples = raw.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ManifestValidationError("samples must be a non-empty list")

    samples: list[BenchmarkSample] = []
    seen_ids: set[str] = set()
    fingerprint_rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw_samples):
        label = f"samples[{index}]"
        if not isinstance(item, dict):
            raise ManifestValidationError(f"{label} must be a mapping")
        sample_id = _required_string(item, "id", label)
        if sample_id in seen_ids:
            raise ManifestValidationError(f"Duplicate sample id: {sample_id}")
        seen_ids.add(sample_id)

        audio = _required_string(item, "audio", label)
        reference_text = _required_string(item, "reference_text", label)
        language = _required_string(item, "language", label)
        if language not in LANGUAGES:
            raise ManifestValidationError(
                f"{label}.language must be one of {sorted(LANGUAGES)}"
            )
        tags = _string_sequence(item.get("tags"), f"{label}.tags")
        expected_terms = _string_sequence(
            item.get("expected_terms", []),
            f"{label}.expected_terms",
            allow_empty=True,
        )
        source = _required_string(item, "source", label)
        disabled = bool(item.get("disabled", False))

        audio_path = (manifest_path.parent / audio).resolve()
        audio_digest = "disabled"
        if not disabled:
            _validate_wav(audio_path)
            audio_digest = _sha256_file(audio_path)

        sample = BenchmarkSample(
            id=sample_id,
            audio=audio,
            reference_text=reference_text,
            language=language,
            tags=tuple(tags),
            expected_terms=tuple(expected_terms),
            source=source,
            disabled=disabled,
        )
        samples.append(sample)
        if not disabled:
            fingerprint_rows.append(
                {
                    "id": sample.id,
                    "audio_sha256": audio_digest,
                    "reference_text": sample.reference_text,
                    "language": sample.language,
                    "tags": sorted(sample.tags),
                    "expected_terms": list(sample.expected_terms),
                    "source": sample.source,
                }
            )

    active = [sample for sample in samples if not sample.disabled]
    if not active:
        raise ManifestValidationError("Manifest has no active samples")
    coverage = {
        tag
        for sample in active
        for tag in sample.tags
    }
    missing = sorted(REQUIRED_COVERAGE - coverage)
    if missing:
        raise ManifestValidationError(
            "Active corpus is missing required coverage: " + ", ".join(missing)
        )

    fingerprint_payload = {
        "schema_version": 1,
        "suite_id": suite_id,
        "truth_version": truth_version,
        "samples": fingerprint_rows,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return BenchmarkManifest(
        path=manifest_path,
        schema_version=1,
        suite_id=suite_id,
        truth_version=truth_version,
        samples=tuple(samples),
        fingerprint=fingerprint,
    )


def _required_string(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def _string_sequence(
    raw: Any,
    label: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(raw, list):
        raise ManifestValidationError(f"{label} must be a list of strings")
    result: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ManifestValidationError(f"{label} must contain non-empty strings")
        normalized = value.strip()
        if normalized not in result:
            result.append(normalized)
    if not result and not allow_empty:
        raise ManifestValidationError(f"{label} must not be empty")
    return result


def _validate_wav(path: Path) -> None:
    if not path.is_file():
        raise ManifestValidationError(f"Missing active audio file: {path}")
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getnchannels() != 1:
                raise ManifestValidationError(f"{path} must be mono WAV")
            if audio.getsampwidth() != 2:
                raise ManifestValidationError(f"{path} must be 16-bit PCM WAV")
            if audio.getframerate() != 16000:
                raise ManifestValidationError(f"{path} must use a 16000 Hz sample rate")
            if audio.getnframes() <= 0:
                raise ManifestValidationError(f"{path} must contain audio frames")
    except (OSError, wave.Error) as exc:
        raise ManifestValidationError(f"Invalid WAV file {path}: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _edit_distance(left: list[str] | str, right: list[str] | str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for index, left_item in enumerate(left, start=1):
        current = [index]
        for right_index, right_item in enumerate(right, start=1):
            substitution = 0 if left_item == right_item else 1
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + substitution,
                )
            )
        previous = current
    return previous[-1]


def normalize_for_cer(text: str) -> str:
    """Normalize case, width, punctuation, and whitespace for dictation CER."""
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    )


def tokenize_for_wer(text: str) -> list[str]:
    """Tokenize Latin words and individual Han characters without third parties."""
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    tokens: list[str] = []
    latin_buffer: list[str] = []

    def flush() -> None:
        if latin_buffer:
            tokens.append("".join(latin_buffer))
            latin_buffer.clear()

    for character in normalized:
        if _is_han(character):
            flush()
            tokens.append(character)
        elif character.isalnum() or character in {"'", "’"}:
            latin_buffer.append("'" if character == "’" else character)
        else:
            flush()
    flush()
    return tokens


def _is_han(character: str) -> bool:
    value = ord(character)
    return (
        0x3400 <= value <= 0x4DBF
        or 0x4E00 <= value <= 0x9FFF
        or 0xF900 <= value <= 0xFAFF
        or 0x20000 <= value <= 0x2FA1F
    )


def score_text(
    *,
    reference: str,
    hypothesis: str,
    expected_terms: Iterable[str] = (),
) -> dict[str, int | float]:
    """Return additive edit counts plus CER, WER, and proper-term recall."""
    reference_chars = normalize_for_cer(reference)
    hypothesis_chars = normalize_for_cer(hypothesis)
    reference_words = tokenize_for_wer(reference)
    hypothesis_words = tokenize_for_wer(hypothesis)
    char_edits = _edit_distance(reference_chars, hypothesis_chars)
    word_edits = _edit_distance(reference_words, hypothesis_words)

    normalized_hypothesis = normalize_for_cer(hypothesis)
    term_total = 0
    term_hits = 0
    for term in expected_terms:
        normalized_term = normalize_for_cer(term)
        if not normalized_term:
            continue
        term_total += 1
        if normalized_term in normalized_hypothesis:
            term_hits += 1

    return {
        "char_edits": char_edits,
        "reference_chars": len(reference_chars),
        "cer": _safe_ratio(char_edits, len(reference_chars)),
        "word_edits": word_edits,
        "reference_words": len(reference_words),
        "wer": _safe_ratio(word_edits, len(reference_words)),
        "term_hits": term_hits,
        "term_total": term_total,
        "term_recall": _safe_ratio(term_hits, term_total),
    }


def build_report(
    manifest: BenchmarkManifest,
    run: dict[str, Any],
) -> dict[str, Any]:
    """Score one system run while preserving conditions and trace semantics."""
    if run.get("schema_version") != 1:
        raise ValueError("Run schema_version must be 1")
    trace_level = run.get("trace_level")
    if trace_level not in TRACE_LEVELS:
        raise ValueError(f"Unknown trace_level: {trace_level}")
    if run.get("manifest_fingerprint") != manifest.fingerprint:
        raise ValueError("Run manifest fingerprint does not match active corpus")
    system = run.get("system")
    conditions = run.get("conditions")
    if not isinstance(system, dict) or not system.get("id"):
        raise ValueError("Run system.id is required")
    if not isinstance(conditions, dict):
        raise ValueError("Run conditions are required")

    result_by_id: dict[str, dict[str, Any]] = {}
    for result in run.get("results", []):
        if not isinstance(result, dict) or not result.get("sample_id"):
            raise ValueError("Each result requires sample_id")
        sample_id = str(result["sample_id"])
        if sample_id in result_by_id:
            raise ValueError(f"Duplicate result for sample: {sample_id}")
        result_by_id[sample_id] = result

    rows: list[dict[str, Any]] = []
    totals = {
        "char_edits": 0,
        "reference_chars": 0,
        "word_edits": 0,
        "reference_words": 0,
        "term_hits": 0,
        "term_total": 0,
    }
    failures = 0
    fallbacks = 0
    semantic_scores: list[float] = []
    preferences: dict[str, int] = {}
    latency_values: dict[str, list[float]] = {
        "first_feedback": [],
        "first_partial": [],
        "speech_end_to_insert": [],
        "stop_to_result": [],
    }

    for sample in manifest.active_samples:
        result = result_by_id.get(
            sample.id,
            {
                "sample_id": sample.id,
                "status": "failed",
                "hypothesis": "",
                "error": "missing_result",
            },
        )
        status = str(result.get("status", "failed"))
        hypothesis = str(result.get("hypothesis") or "")
        score = score_text(
            reference=sample.reference_text,
            hypothesis=hypothesis,
            expected_terms=sample.expected_terms,
        )
        for key in totals:
            totals[key] += int(score[key])

        failed = status != "success"
        fallback_reason = str(result.get("fallback_reason") or "")
        result_source = str(result.get("result_source") or "")
        fallback = bool(fallback_reason) or "fallback" in result_source.casefold()
        failures += int(failed)
        fallbacks += int(fallback)

        timings = result.get("timings_ms")
        if not isinstance(timings, dict):
            timings = {}
        _validate_timing_scope(trace_level, timings)
        for key in latency_values:
            value = timings.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                latency_values[key].append(float(value))

        semantic_score = result.get("semantic_score")
        if isinstance(semantic_score, (int, float)) and not isinstance(
            semantic_score, bool
        ):
            if not 1 <= float(semantic_score) <= 5:
                raise ValueError("semantic_score must be between 1 and 5")
            semantic_scores.append(float(semantic_score))
        preference = result.get("human_preference")
        if isinstance(preference, str) and preference:
            preferences[preference] = preferences.get(preference, 0) + 1

        rows.append(
            {
                "sample_id": sample.id,
                "language": sample.language,
                "tags": list(sample.tags),
                "source": sample.source,
                "status": status,
                "hypothesis": hypothesis,
                "result_source": result_source,
                "fallback_reason": fallback_reason,
                "timings_ms": timings,
                "cer": round(float(score["cer"]), 6),
                "wer": round(float(score["wer"]), 6),
                "term_recall": round(float(score["term_recall"]), 6),
                "char_edits": int(score["char_edits"]),
                "reference_chars": int(score["reference_chars"]),
                "word_edits": int(score["word_edits"]),
                "reference_words": int(score["reference_words"]),
                "term_hits": int(score["term_hits"]),
                "term_total": int(score["term_total"]),
                "failed": failed,
                "fallback": fallback,
                "semantic_score": semantic_score,
                "human_preference": preference,
            }
        )

    sample_count = len(manifest.active_samples)
    metrics = {
        "cer": round(
            _safe_ratio(totals["char_edits"], totals["reference_chars"]),
            6,
        ),
        "wer": round(
            _safe_ratio(totals["word_edits"], totals["reference_words"]),
            6,
        ),
        "term_recall": round(
            _safe_ratio(totals["term_hits"], totals["term_total"]),
            6,
        ),
        "term_total": sum(int(row["term_total"]) for row in rows),
        "failure_rate": round(_safe_ratio(failures, sample_count), 6),
        "fallback_rate": round(_safe_ratio(fallbacks, sample_count), 6),
        "first_feedback_ms": _summarize(latency_values["first_feedback"]),
        "first_partial_ms": _summarize(latency_values["first_partial"]),
        "speech_end_to_insert_ms": _summarize(
            latency_values["speech_end_to_insert"]
        ),
        "stop_to_result_ms": _summarize(latency_values["stop_to_result"]),
        "semantic_score": _summarize(semantic_scores),
        "human_preferences": preferences,
    }
    return {
        "schema_version": 1,
        "system": dict(system),
        "trace_level": trace_level,
        "conditions": dict(conditions),
        "suite_id": manifest.suite_id,
        "truth_version": manifest.truth_version,
        "manifest_fingerprint": manifest.fingerprint,
        "coverage": sorted(manifest.coverage),
        "sample_count": sample_count,
        "metrics": metrics,
        "metrics_by_tag": {
            tag: _quality_summary(
                [row for row in rows if tag in row["tags"]]
            )
            for tag in sorted(manifest.coverage)
        },
        "sample_rows": rows,
        "semantic_review": copy.deepcopy(run.get("semantic_review")),
    }


def _validate_timing_scope(trace_level: str, timings: dict[str, Any]) -> None:
    if trace_level == "live_end_to_end":
        return
    forbidden = [
        key
        for key in ("first_feedback", "speech_end_to_insert")
        if timings.get(key) is not None
    ]
    if forbidden:
        raise ValueError(
            f"{trace_level} cannot report end-to-end timings: "
            + ", ".join(forbidden)
        )


def _quality_summary(rows: list[dict[str, Any]]) -> dict[str, int | float]:
    sample_count = len(rows)
    return {
        "sample_count": sample_count,
        "cer": round(
            _safe_ratio(
                sum(int(row["char_edits"]) for row in rows),
                sum(int(row["reference_chars"]) for row in rows),
            ),
            6,
        ),
        "wer": round(
            _safe_ratio(
                sum(int(row["word_edits"]) for row in rows),
                sum(int(row["reference_words"]) for row in rows),
            ),
            6,
        ),
        "term_recall": round(
            _safe_ratio(
                sum(int(row["term_hits"]) for row in rows),
                sum(int(row["term_total"]) for row in rows),
            ),
            6,
        ),
        "failure_rate": round(
            _safe_ratio(sum(bool(row["failed"]) for row in rows), sample_count),
            6,
        ),
        "fallback_rate": round(
            _safe_ratio(sum(bool(row["fallback"]) for row in rows), sample_count),
            6,
        ),
    }


def timings_from_asr_trace(
    trace: dict[str, Any],
    *,
    trace_level: str,
    first_feedback_ms: float | None = None,
    insert_completed_ms: float | None = None,
) -> dict[str, float | None]:
    """Map an ASR trace without relabeling protocol time as UI latency."""
    if trace_level not in TRACE_LEVELS:
        raise ValueError(f"Unknown trace_level: {trace_level}")
    timings = trace.get("timings_ms")
    if not isinstance(timings, dict):
        timings = {}
    commit = _number_or_none(timings.get("commit_ms"))
    total_result = _number_or_none(timings.get("total_result_ms"))
    stop_to_result = (
        max(0.0, total_result - commit)
        if total_result is not None and commit is not None
        else None
    )
    live = trace_level == "live_end_to_end"
    speech_end_to_insert = (
        max(0.0, float(insert_completed_ms) - commit)
        if live and insert_completed_ms is not None and commit is not None
        else None
    )
    partial_candidates = [
        value
        for value in (
            _number_or_none(timings.get("first_partial_ms")),
            _number_or_none(timings.get("response_first_delta_ms")),
        )
        if value is not None
    ]
    return {
        "first_feedback": float(first_feedback_ms)
        if live and first_feedback_ms is not None
        else None,
        "first_partial": min(partial_candidates) if partial_candidates else None,
        "speech_end_to_insert": speech_end_to_insert,
        "stop_to_result": stop_to_result,
    }


def timings_from_live_trace(
    trace: dict[str, Any],
) -> dict[str, float | None]:
    """Convert an opt-in app trace into true UI and insertion latencies."""
    events = trace.get("events_ms")
    if not isinstance(events, dict):
        events = {}
    speech_end = _number_or_none(events.get("speech_end"))
    insert_completed = _number_or_none(events.get("insert_completed"))
    return {
        "first_feedback": _number_or_none(events.get("first_feedback")),
        "first_partial": _number_or_none(events.get("first_partial")),
        "speech_end_to_insert": (
            max(0.0, insert_completed - speech_end)
            if insert_completed is not None and speech_end is not None
            else None
        ),
        # The app-level trace observes final insertion, not the ASR engine's
        # internal result-selection point. Keep these semantics separate.
        "stop_to_result": None,
    }


def compare_reports(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Return comparison eligibility before computing any winning claim."""
    reasons: list[str] = []
    same_manifest = (
        left.get("manifest_fingerprint")
        and left.get("manifest_fingerprint") == right.get("manifest_fingerprint")
    )
    if not same_manifest:
        reasons.append(
            "Quality comparison requires the same manifest fingerprint "
            "(identical active audio and truth)."
        )

    same_trace_level = (
        left.get("trace_level")
        and left.get("trace_level") == right.get("trace_level")
    )
    if not same_trace_level:
        reasons.append(
            "Latency comparison requires the same trace level."
        )

    left_conditions = left.get("conditions")
    right_conditions = right.get("conditions")
    left_conditions = left_conditions if isinstance(left_conditions, dict) else {}
    right_conditions = (
        right_conditions if isinstance(right_conditions, dict) else {}
    )
    same_audio_delivery = True
    same_hardware = True
    if same_trace_level and left.get("trace_level") == "live_end_to_end":
        left_delivery = left_conditions.get("audio_delivery")
        right_delivery = right_conditions.get("audio_delivery")
        same_audio_delivery = bool(
            left_delivery and left_delivery == right_delivery
        )
        if not same_audio_delivery:
            reasons.append(
                "Live end-to-end latency comparison requires the same "
                "audio delivery path."
            )
        left_hardware = left_conditions.get("hardware")
        right_hardware = right_conditions.get("hardware")
        same_hardware = bool(
            left_hardware and left_hardware == right_hardware
        )
        if not same_hardware:
            reasons.append(
                "Live end-to-end latency comparison requires the same "
                "hardware."
            )

    quality_comparable = bool(same_manifest)
    latency_comparable = bool(
        same_manifest
        and same_trace_level
        and same_audio_delivery
        and same_hardware
    )
    return {
        "left_system": (left.get("system") or {}).get("id"),
        "right_system": (right.get("system") or {}).get("id"),
        "quality_comparable": quality_comparable,
        "latency_comparable": latency_comparable,
        "claim_allowed": quality_comparable and latency_comparable,
        "reasons": reasons,
    }


def apply_semantic_reviews(
    run: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Attach an auditable human or model review sidecar to run results."""
    if review.get("schema_version") != 1:
        raise ValueError("Review schema_version must be 1")
    if review.get("manifest_fingerprint") != run.get("manifest_fingerprint"):
        raise ValueError("Review manifest fingerprint does not match run")
    reviewer = review.get("reviewer")
    rubric = review.get("rubric")
    samples = review.get("samples")
    if not isinstance(reviewer, dict) or not reviewer.get("type"):
        raise ValueError("Review reviewer.type is required")
    if not isinstance(rubric, str) or not rubric.strip():
        raise ValueError("Review rubric is required")
    if not isinstance(samples, dict):
        raise ValueError("Review samples must be a mapping")

    annotated = copy.deepcopy(run)
    known_ids = {
        str(result.get("sample_id"))
        for result in annotated.get("results", [])
        if isinstance(result, dict) and result.get("sample_id")
    }
    unknown = sorted(str(sample_id) for sample_id in samples if sample_id not in known_ids)
    if unknown:
        raise ValueError("Review contains unknown sample ids: " + ", ".join(unknown))

    for result in annotated.get("results", []):
        if not isinstance(result, dict):
            continue
        item = samples.get(result.get("sample_id"))
        if not isinstance(item, dict):
            continue
        score = item.get("semantic_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("semantic_score must be numeric")
        if not 1 <= float(score) <= 5:
            raise ValueError("semantic_score must be between 1 and 5")
        result["semantic_score"] = score
        preference = item.get("human_preference")
        if preference is not None:
            if not isinstance(preference, str) or not preference:
                raise ValueError("human_preference must be a non-empty string")
            result["human_preference"] = preference

    annotated["semantic_review"] = {
        "reviewer": dict(reviewer),
        "rubric": rubric.strip(),
    }
    return annotated


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a compact, reviewable Chinese Markdown benchmark report."""
    system = report.get("system") or {}
    metrics = report.get("metrics") or {}
    trace_level = str(report.get("trace_level") or "unknown")
    lines = [
        f"# {system.get('id', 'unknown')} 语音转写基准",
        "",
        f"- 版本：{system.get('version', 'unknown')}",
        f"- 模型：{system.get('model', 'unknown')}",
        f"- 链路级别：`{trace_level}`",
        f"- 语料指纹：`{report.get('manifest_fingerprint', 'unknown')}`",
        "",
    ]
    if trace_level == "protocol_replay":
        lines.extend(
            [
                "> `protocol_replay` 只衡量协议与服务处理，"
                "不能代表真实端到端的麦克风、UI 或插入延迟。",
                "",
            ]
        )
    elif trace_level == "paced_replay":
        lines.extend(
            [
                "> `paced_replay` 按音频时长回放，但不包含真实麦克风、"
                "胶囊反馈或文本插入。",
                "",
            ]
        )

    lines.extend(
        [
            "## 核心指标",
            "",
            "| 指标 | 结果 |",
            "| --- | ---: |",
            f"| CER | {_format_ratio(metrics.get('cer'))} |",
            f"| WER | {_format_ratio(metrics.get('wer'))} |",
            f"| 专有词召回率 | {_format_ratio(metrics.get('term_recall'))} |",
            f"| 失败率 | {_format_ratio(metrics.get('failure_rate'))} |",
            f"| 回退率 | {_format_ratio(metrics.get('fallback_rate'))} |",
            f"| 首个反馈 P50 / P95 | {_format_latency(metrics.get('first_feedback_ms'))} |",
            f"| 首个 partial P50 / P95 | {_format_latency(metrics.get('first_partial_ms'))} |",
            f"| 停说到插入 P50 / P95 | {_format_latency(metrics.get('speech_end_to_insert_ms'))} |",
            f"| 停说到结果 P50 / P95 | {_format_latency(metrics.get('stop_to_result_ms'))} |",
            f"| 语义质量（1–5） | {_format_score(metrics.get('semantic_score'))} |",
            "",
            "## 分类质量",
            "",
            "| 标签 | 样本数 | CER | WER | 专有词召回 | 失败率 | 回退率 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for tag, tag_metrics in sorted(
        (report.get("metrics_by_tag") or {}).items()
    ):
        lines.append(
            f"| {tag} | {tag_metrics.get('sample_count', 0)} "
            f"| {_format_ratio(tag_metrics.get('cer'))} "
            f"| {_format_ratio(tag_metrics.get('wer'))} "
            f"| {_format_term_recall(tag_metrics)} "
            f"| {_format_ratio(tag_metrics.get('failure_rate'))} "
            f"| {_format_ratio(tag_metrics.get('fallback_rate'))} |"
        )
    lines.extend(
        [
            "",
            "## 运行条件",
            "",
        ]
    )
    for key, value in sorted((report.get("conditions") or {}).items()):
        lines.append(f"- {key}: {value}")
    semantic_review = report.get("semantic_review")
    if isinstance(semantic_review, dict):
        reviewer = semantic_review.get("reviewer") or {}
        lines.append(
            "- semantic_reviewer: "
            f"{reviewer.get('type', 'unknown')} / {reviewer.get('id', 'unknown')}"
        )
    lines.extend(
        [
            "",
            "## 对照边界",
            "",
            "本报告未包含同音频 Typeless 对照，因此不能据此宣称优于 Typeless。",
            "只有两个报告的语料指纹和链路级别均一致时，才允许比较质量与延迟。",
            "",
        ]
    )
    return "\n".join(lines)


def _format_ratio(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{float(value) * 100:.2f}%"


def _format_latency(summary: Any) -> str:
    if not isinstance(summary, dict) or not summary:
        return "—"
    return f"{summary.get('p50', '—')} / {summary.get('p95', '—')} ms"


def _format_score(summary: Any) -> str:
    if not isinstance(summary, dict) or not summary:
        return "—"
    return f"{summary.get('mean', '—')}（n={summary.get('count', 0)}）"


def _format_term_recall(summary: Any) -> str:
    if not isinstance(summary, dict) or not summary.get("term_total"):
        return "—"
    return _format_ratio(summary.get("term_recall"))


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator) / float(denominator)


def _percentile(values: list[float], ratio: float) -> float:
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


def _summarize(values: list[float]) -> dict[str, int | float]:
    if not values:
        return {}
    return {
        "count": len(values),
        "p50": round(statistics.median(values), 2),
        "p95": round(_percentile(values, 0.95), 2),
        "mean": round(statistics.fmean(values), 2),
    }


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
