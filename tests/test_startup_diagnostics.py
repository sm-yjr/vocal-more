"""Tests for persistent dictation-start diagnostics."""

import json
import zipfile

import pytest


def test_startup_event_is_correlated_and_redacted(tmp_path, monkeypatch):
    from vocal_more.startup_diagnostics import record_startup_event

    path = tmp_path / "startup-events.jsonl"
    monkeypatch.setenv("VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH", str(path))

    record_startup_event(
        "microphone_start_failed",
        attempt_id="attempt-1",
        session_token=7,
        error_message=f"failure at {tmp_path} api_key=sk-private-value",
        transcript="private words",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["attempt_id"] == "attempt-1"
    assert payload["session_token"] == 7
    assert payload["transcript"] == "<redacted>"
    assert "sk-private-value" not in payload["error_message"]


def test_startup_event_log_rotates_at_bounded_size(tmp_path, monkeypatch):
    import vocal_more.startup_diagnostics as diagnostics

    path = tmp_path / "startup-events.jsonl"
    monkeypatch.setenv("VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(diagnostics, "STARTUP_EVENT_MAX_BYTES", 300)

    for index in range(8):
        diagnostics.record_startup_event(
            "test_event", attempt_id=f"attempt-{index}", detail="x" * 100
        )

    assert path.exists()
    assert path.with_suffix(".1.jsonl").exists()


def test_support_bundle_includes_startup_timeline_and_runtime_snapshot(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config
    from vocal_more.core.recording_store import RecordingStore
    from vocal_more.diagnostics import export_support_bundle
    from vocal_more.startup_diagnostics import record_startup_event

    startup_path = tmp_path / "diagnostics" / "startup-events.jsonl"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml")
    )
    monkeypatch.setenv("VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH", str(startup_path))
    record_startup_event("hotkey_received", attempt_id="attempt-2")

    bundle_path = export_support_bundle(
        config=Config(),
        recording_store=RecordingStore(str(tmp_path / "recordings")),
        environment_checks=[],
        app_version="0.4.10",
        runtime_diagnostics={
            "hotkey": {"running": True},
            "api_key": "sk-private-value",
            "session_token": 9,
        },
    )

    with zipfile.ZipFile(bundle_path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        runtime = json.loads(bundle.read("runtime.snapshot.json"))
        timeline = bundle.read("startup/startup-events.jsonl").decode("utf-8")

    assert manifest["app_version"] == "0.4.10"
    assert manifest["startup_event_files"] == ["startup-events.jsonl"]
    assert manifest["runtime_snapshot_included"] is True
    assert runtime["hotkey"]["running"] is True
    assert runtime["api_key"] == "<redacted>"
    assert runtime["session_token"] == 9
    assert "attempt-2" in timeline


def test_mode_records_structured_microphone_start_failure(tmp_path, monkeypatch):
    from vocal_more.core.audio_recorder import AudioRecorderStartError
    from vocal_more.modes.base_mode import BaseMode

    class FailingRecorder:
        def start_capture_session(self, _audio_config):
            raise AudioRecorderStartError(
                "CoreAudio timed out",
                startup_timed_out=True,
                code="startup_timeout",
                stage="stream_start",
            )

        def diagnostic_snapshot(self):
            return {"start_worker_alive": True, "input_status": {"phase": "failed"}}

    class TestMode(BaseMode):
        name = "test_mode"
        description = "test"

        def on_hotkey_pressed(self):
            return None

        def on_hotkey_released(self):
            return None

        def cancel(self, reason="user_cancel"):
            return None

    path = tmp_path / "startup-events.jsonl"
    monkeypatch.setenv("VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH", str(path))
    mode = TestMode()
    mode._recorder = FailingRecorder()
    mode.set_startup_diagnostic_context("attempt-3", "dictation")

    with pytest.raises(AudioRecorderStartError):
        mode._start_audio_capture(object())

    events = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    failure = events[-1]
    assert [event["event"] for event in events] == [
        "microphone_start_requested",
        "microphone_start_failed",
    ]
    assert failure["attempt_id"] == "attempt-3"
    assert failure["code"] == "startup_timeout"
    assert failure["stage"] == "stream_start"
    assert failure["startup_timed_out"] is True
    assert failure["audio_input"]["start_worker_alive"] is True
