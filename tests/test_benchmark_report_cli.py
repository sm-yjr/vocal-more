from __future__ import annotations

import json
import wave


def _write_corpus(tmp_path):
    from vocal_more.benchmarking import REQUIRED_COVERAGE, load_manifest

    audio_path = tmp_path / "sample.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 160)
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "suite_id: cli-test",
                "truth_version: '1'",
                "samples:",
                "  - id: sample",
                "    audio: sample.wav",
                "    reference_text: hello",
                "    language: en",
                "    source: human_recorded",
                "    expected_terms: [hello]",
                "    tags:",
                *[f"      - {tag}" for tag in sorted(REQUIRED_COVERAGE)],
            ]
        ),
        encoding="utf-8",
    )
    return manifest_path, load_manifest(manifest_path)


def test_score_cli_writes_json_and_markdown(tmp_path):
    from scripts.benchmark_report import main

    manifest_path, manifest = _write_corpus(tmp_path)
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "system": {
                    "id": "vocal-more",
                    "version": "0.2.8",
                    "model": "test-model",
                },
                "trace_level": "paced_replay",
                "conditions": {"hardware": "test", "network": "test"},
                "manifest_fingerprint": manifest.fingerprint,
                "results": [
                    {
                        "sample_id": "sample",
                        "status": "success",
                        "hypothesis": "hello",
                        "result_source": "realtime",
                        "fallback_reason": "",
                        "timings_ms": {
                            "first_feedback": None,
                            "first_partial": 100,
                            "speech_end_to_insert": None,
                            "stop_to_result": 250,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"

    exit_code = main(
        [
            "score",
            "--manifest",
            str(manifest_path),
            "--run",
            str(run_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert exit_code == 0
    assert json.loads(json_output.read_text())["metrics"]["cer"] == 0
    assert "# vocal-more 语音转写基准" in markdown_output.read_text()


def test_compare_cli_fails_closed_for_mismatched_corpus(tmp_path):
    from scripts.benchmark_report import main

    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    output = tmp_path / "comparison.json"
    left.write_text(
        json.dumps(
            {
                "system": {"id": "vocal-more"},
                "manifest_fingerprint": "left",
                "trace_level": "live_end_to_end",
            }
        )
    )
    right.write_text(
        json.dumps(
            {
                "system": {"id": "typeless"},
                "manifest_fingerprint": "right",
                "trace_level": "live_end_to_end",
            }
        )
    )

    exit_code = main(
        [
            "compare",
            "--left",
            str(left),
            "--right",
            str(right),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 2
    assert json.loads(output.read_text())["claim_allowed"] is False
