from __future__ import annotations

import json
import wave

import pytest


def _write_silence(path, *, frames: int = 160):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * frames)


def _complete_manifest(tmp_path):
    from vocal_more.benchmarking import REQUIRED_COVERAGE

    audio_path = tmp_path / "sample.wav"
    _write_silence(audio_path)
    return {
        "schema_version": 1,
        "suite_id": "core-v1",
        "truth_version": "2026-07-26",
        "samples": [
            {
                "id": "all-coverage",
                "audio": "sample.wav",
                "reference_text": "你好 Vocal More",
                "language": "mixed",
                "tags": sorted(REQUIRED_COVERAGE),
                "expected_terms": ["Vocal More"],
                "source": "human_recorded",
            }
        ],
    }


def test_manifest_requires_every_core_condition_from_active_audio(tmp_path):
    from vocal_more.benchmarking import ManifestValidationError, load_manifest

    manifest = _complete_manifest(tmp_path)
    manifest["samples"][0]["tags"].remove("whisper")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="whisper"):
        load_manifest(path)


def test_manifest_fingerprint_changes_with_truth_or_audio(tmp_path):
    from vocal_more.benchmarking import load_manifest

    manifest_data = _complete_manifest(tmp_path)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_data), encoding="utf-8")
    first = load_manifest(path)

    manifest_data["samples"][0]["reference_text"] += "。"
    path.write_text(json.dumps(manifest_data), encoding="utf-8")
    changed_truth = load_manifest(path)
    assert first.fingerprint != changed_truth.fingerprint

    manifest_data["samples"][0]["reference_text"] = "你好 Vocal More"
    _write_silence(tmp_path / "sample.wav", frames=320)
    changed_audio = load_manifest(path)
    assert first.fingerprint != changed_audio.fingerprint


def test_text_metrics_cover_cer_wer_and_term_recall():
    from vocal_more.benchmarking import score_text

    score = score_text(
        reference="你好，Vocal More ships Friday！",
        hypothesis="你好 vocal more ships Monday",
        expected_terms=["Vocal More", "Friday"],
    )

    assert score["cer"] > 0
    assert score["wer"] == pytest.approx(1 / 6)
    assert score["term_recall"] == 0.5
    assert score["term_hits"] == 1
    assert score["term_total"] == 2


def test_report_aggregates_quality_latency_failure_fallback_and_semantics(tmp_path):
    from vocal_more.benchmarking import build_report, load_manifest

    manifest = load_manifest(
        _write_manifest_with_two_samples(tmp_path)
    )
    run = {
        "schema_version": 1,
        "system": {
            "id": "vocal-more",
            "version": "0.2.8",
            "model": "qwen3.5-omni-flash-realtime",
        },
        "trace_level": "live_end_to_end",
        "conditions": {
            "os": "macOS 26.0",
            "hardware": "Apple Silicon",
            "network": "office-wifi",
            "repetitions": 1,
        },
        "manifest_fingerprint": manifest.fingerprint,
        "results": [
            {
                "sample_id": "sample-a",
                "status": "success",
                "hypothesis": "hello world",
                "result_source": "realtime",
                "fallback_reason": "",
                "timings_ms": {
                    "first_feedback": 20,
                    "first_partial": 200,
                    "speech_end_to_insert": 500,
                    "stop_to_result": 450,
                },
                "semantic_score": 5,
            },
            {
                "sample_id": "sample-b",
                "status": "failed",
                "hypothesis": "",
                "result_source": "batch_fallback",
                "fallback_reason": "connect_timeout",
                "timings_ms": {
                    "first_feedback": 30,
                    "first_partial": None,
                    "speech_end_to_insert": None,
                    "stop_to_result": None,
                },
                "semantic_score": 1,
            },
        ],
    }

    report = build_report(manifest, run)

    assert report["metrics"]["failure_rate"] == 0.5
    assert report["metrics"]["fallback_rate"] == 0.5
    assert report["metrics"]["semantic_score"]["mean"] == 3
    assert report["metrics"]["first_feedback_ms"]["p50"] == 25
    assert report["metrics"]["first_feedback_ms"]["p95"] == 29.5
    assert report["metrics"]["speech_end_to_insert_ms"]["p50"] == 500
    assert report["metrics"]["cer"] > 0
    assert report["metrics"]["wer"] > 0
    assert report["metrics"]["term_recall"] == 0.5
    assert report["metrics_by_tag"]["proper_noun"]["term_recall"] == 0
    assert report["metrics_by_tag"]["proper_noun"]["failure_rate"] == 1


def test_protocol_trace_does_not_fabricate_end_to_end_latency():
    from vocal_more.benchmarking import timings_from_asr_trace

    trace = {
        "timings_ms": {
            "first_partial_ms": 175,
            "commit_ms": 1000,
            "total_result_ms": 1500,
        }
    }

    timings = timings_from_asr_trace(trace, trace_level="protocol_replay")

    assert timings == {
        "first_feedback": None,
        "first_partial": 175,
        "speech_end_to_insert": None,
        "stop_to_result": 500,
    }


def test_first_partial_uses_earliest_transcript_or_response_delta():
    from vocal_more.benchmarking import timings_from_asr_trace

    timings = timings_from_asr_trace(
        {
            "timings_ms": {
                "first_partial_ms": None,
                "response_first_delta_ms": 420,
                "commit_ms": 300,
                "total_result_ms": 800,
            }
        },
        trace_level="paced_replay",
    )

    assert timings["first_partial"] == 420


def test_live_trace_requires_explicit_ui_and_insert_events():
    from vocal_more.benchmarking import timings_from_asr_trace

    trace = {
        "timings_ms": {
            "first_partial_ms": 175,
            "commit_ms": 1000,
            "total_result_ms": 1500,
        }
    }

    timings = timings_from_asr_trace(
        trace,
        trace_level="live_end_to_end",
        first_feedback_ms=12,
        insert_completed_ms=1610,
    )

    assert timings["first_feedback"] == 12
    assert timings["speech_end_to_insert"] == 610
    assert timings["stop_to_result"] == 500


def test_comparison_requires_identical_corpus_and_trace_level(tmp_path):
    from vocal_more.benchmarking import compare_reports

    base = {
        "schema_version": 1,
        "manifest_fingerprint": "same-audio-and-truth",
        "trace_level": "live_end_to_end",
        "system": {"id": "vocal-more"},
        "metrics": {"cer": 0.1},
    }
    other = {
        **base,
        "system": {"id": "typeless"},
        "manifest_fingerprint": "different-corpus",
    }

    comparison = compare_reports(base, other)

    assert comparison["quality_comparable"] is False
    assert comparison["latency_comparable"] is False
    assert comparison["claim_allowed"] is False
    assert "manifest fingerprint" in " ".join(comparison["reasons"])

    other["manifest_fingerprint"] = base["manifest_fingerprint"]
    other["trace_level"] = "paced_replay"
    comparison = compare_reports(base, other)
    assert comparison["quality_comparable"] is True
    assert comparison["latency_comparable"] is False
    assert comparison["claim_allowed"] is False


def test_live_comparison_requires_identical_audio_delivery():
    from vocal_more.benchmarking import compare_reports

    base = {
        "schema_version": 1,
        "manifest_fingerprint": "same-audio-and-truth",
        "trace_level": "live_end_to_end",
        "conditions": {"audio_delivery": "physical_microphone"},
        "system": {"id": "vocal-more"},
    }
    other = {
        **base,
        "conditions": {"audio_delivery": "deterministic_wav_replay"},
        "system": {"id": "other"},
    }

    comparison = compare_reports(base, other)

    assert comparison["quality_comparable"] is True
    assert comparison["latency_comparable"] is False
    assert comparison["claim_allowed"] is False
    assert "audio delivery" in " ".join(comparison["reasons"]).lower()


def test_live_comparison_requires_identical_hardware():
    from vocal_more.benchmarking import compare_reports

    base = {
        "schema_version": 1,
        "manifest_fingerprint": "same-audio-and-truth",
        "trace_level": "live_end_to_end",
        "conditions": {
            "audio_delivery": "physical_microphone",
            "hardware": "MacBook Pro built-in microphone",
        },
        "system": {"id": "vocal-more"},
    }
    other = {
        **base,
        "conditions": {
            "audio_delivery": "physical_microphone",
            "hardware": "USB microphone",
        },
        "system": {"id": "other"},
    }

    comparison = compare_reports(base, other)

    assert comparison["quality_comparable"] is True
    assert comparison["latency_comparable"] is False
    assert comparison["claim_allowed"] is False
    assert "hardware" in " ".join(comparison["reasons"]).lower()


def test_semantic_review_sidecar_is_bound_to_corpus_and_sample_ids():
    from vocal_more.benchmarking import apply_semantic_reviews

    run = {
        "manifest_fingerprint": "corpus-a",
        "results": [
            {"sample_id": "one", "status": "success", "hypothesis": "text"}
        ],
    }
    review = {
        "schema_version": 1,
        "manifest_fingerprint": "corpus-a",
        "reviewer": {"type": "llm", "id": "judge-model"},
        "rubric": "1=no meaning retained; 5=all meaning retained",
        "samples": {"one": {"semantic_score": 4}},
    }

    annotated = apply_semantic_reviews(run, review)

    assert annotated["results"][0]["semantic_score"] == 4
    assert annotated["semantic_review"]["reviewer"]["id"] == "judge-model"
    assert "semantic_score" not in run["results"][0]

    review["manifest_fingerprint"] = "corpus-b"
    with pytest.raises(ValueError, match="fingerprint"):
        apply_semantic_reviews(run, review)


def test_markdown_report_states_scope_and_comparison_limit(tmp_path):
    from vocal_more.benchmarking import render_markdown_report

    report = {
        "system": {"id": "vocal-more", "version": "0.2.8", "model": "model-a"},
        "trace_level": "protocol_replay",
        "manifest_fingerprint": "abc123",
        "conditions": {"network": "office-wifi", "hardware": "M-series"},
        "coverage": ["zh", "en"],
        "metrics": {
            "cer": 0.1,
            "wer": 0.2,
            "term_recall": 0.9,
            "failure_rate": 0.0,
            "fallback_rate": 0.0,
            "first_feedback_ms": {},
            "first_partial_ms": {"p50": 200, "p95": 300, "count": 2},
            "speech_end_to_insert_ms": {},
            "stop_to_result_ms": {"p50": 400, "p95": 600, "count": 2},
            "semantic_score": {},
        },
        "sample_rows": [],
    }

    markdown = render_markdown_report(report)

    assert "protocol_replay" in markdown
    assert "不能代表真实端到端" in markdown
    assert "未包含同音频 Typeless 对照" in markdown


def test_live_trace_recorder_writes_timing_only_without_transcript(tmp_path):
    from vocal_more.benchmarking import LiveTraceRecorder

    ticks = iter([10.012, 10.2, 11.0, 11.61])
    recorder = LiveTraceRecorder(
        tmp_path,
        clock=lambda: next(ticks),
        wall_clock=lambda: "2026-07-26T16:00:00Z",
        session_id_factory=lambda: "session-1",
    )

    recorder.begin(
        started_at=10.0,
        metadata={
            "model": "model-a",
            "mode": "realtime_long",
            "audio_delivery": "deterministic_wav_replay",
        },
    )
    recorder.mark("first_feedback")
    recorder.mark("first_partial")
    recorder.mark("speech_end")
    output_path = recorder.finish(
        status="success",
        insert_completed=True,
        metadata={"result_source": "realtime", "fallback_reason": ""},
    )

    payload = json.loads(output_path.read_text())
    assert payload["session_id"] == "session-1"
    assert payload["events_ms"] == {
        "first_feedback": 12.0,
        "first_partial": 200.0,
        "speech_end": 1000.0,
        "insert_completed": 1610.0,
    }
    assert payload["status"] == "success"
    assert payload["metadata"]["audio_delivery"] == "deterministic_wav_replay"
    assert "transcript" not in json.dumps(payload).lower()
    assert recorder.active is False


def test_live_trace_recorder_keeps_first_partial_timestamp(tmp_path):
    from vocal_more.benchmarking import LiveTraceRecorder

    ticks = iter([2.1, 2.3])
    recorder = LiveTraceRecorder(
        tmp_path,
        clock=lambda: next(ticks),
        wall_clock=lambda: "now",
        session_id_factory=lambda: "session",
    )
    recorder.begin(started_at=2.0, metadata={})
    recorder.mark("first_partial")
    recorder.mark("first_partial")

    assert recorder.events_ms["first_partial"] == pytest.approx(100)


def test_live_trace_recorder_is_opt_in_via_environment(tmp_path):
    from vocal_more.benchmarking import live_trace_recorder_from_env

    assert live_trace_recorder_from_env({}) is None
    recorder = live_trace_recorder_from_env(
        {"VOCAL_MORE_BENCHMARK_TRACE_DIR": str(tmp_path)}
    )
    assert recorder is not None
    assert recorder.output_dir == tmp_path


def test_live_trace_payload_maps_to_end_to_end_metrics():
    from vocal_more.benchmarking import timings_from_live_trace

    timings = timings_from_live_trace(
        {
            "events_ms": {
                "first_feedback": 12,
                "first_partial": 200,
                "speech_end": 1000,
                "insert_completed": 1610,
            }
        }
    )

    assert timings == {
        "first_feedback": 12,
        "first_partial": 200,
        "speech_end_to_insert": 610,
        "stop_to_result": None,
    }


def _write_manifest_with_two_samples(tmp_path):
    from vocal_more.benchmarking import REQUIRED_COVERAGE

    for name in ("a.wav", "b.wav"):
        _write_silence(tmp_path / name)
    data = {
        "schema_version": 1,
        "suite_id": "core-v1",
        "truth_version": "2026-07-26",
        "samples": [
            {
                "id": "sample-a",
                "audio": "a.wav",
                "reference_text": "hello world",
                "language": "en",
                "tags": sorted(REQUIRED_COVERAGE - {"proper_noun"}),
                "expected_terms": ["hello"],
                "source": "human_recorded",
            },
            {
                "id": "sample-b",
                "audio": "b.wav",
                "reference_text": "Vocal More",
                "language": "en",
                "tags": ["proper_noun"],
                "expected_terms": ["Vocal More"],
                "source": "human_recorded",
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
