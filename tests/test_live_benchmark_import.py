from __future__ import annotations

import json
import wave


def test_live_trace_import_builds_end_to_end_run_without_audio_or_text_in_trace(
    tmp_path,
):
    from vocal_more.benchmarking import REQUIRED_COVERAGE, load_manifest
    from scripts.import_live_benchmark import build_live_run

    audio_path = tmp_path / "sample.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 160)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "suite_id": "live-test",
                "truth_version": "1",
                "samples": [
                    {
                        "id": "sample",
                        "audio": "sample.wav",
                        "reference_text": "hello",
                        "language": "en",
                        "tags": sorted(REQUIRED_COVERAGE),
                        "expected_terms": [],
                        "source": "human_recorded",
                    }
                ],
            }
        )
    )
    manifest = load_manifest(manifest_path)
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "session",
                "status": "success",
                "metadata": {
                    "sample_id": "sample",
                    "app_version": "0.2.8",
                    "model": "model-a",
                    "mode": "realtime_long",
                    "auto_paste": True,
                    "audio_delivery": "deterministic_wav_replay",
                },
                "events_ms": {
                    "first_feedback": 15,
                    "first_partial": 400,
                    "speech_end": 2000,
                    "insert_completed": 2600,
                },
            }
        )
    )
    hypotheses_path = tmp_path / "hypotheses.json"
    hypotheses_path.write_text(json.dumps({"sample": "hello"}))

    run = build_live_run(
        manifest_path=manifest_path,
        trace_dir=trace_dir,
        hypotheses_path=hypotheses_path,
        network_label="test-network",
        hardware_label="test-mac",
    )

    assert run["manifest_fingerprint"] == manifest.fingerprint
    assert run["trace_level"] == "live_end_to_end"
    assert (
        run["conditions"]["audio_delivery"]
        == "deterministic_wav_replay"
    )
    assert run["results"][0]["hypothesis"] == "hello"
    assert run["results"][0]["timings_ms"]["first_feedback"] == 15
    assert run["results"][0]["timings_ms"]["speech_end_to_insert"] == 600
