"""Tests for the explicit physical-microphone A/B capture protocol."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest


def _verified_runtime_session(mode: str, **overrides):
    automatic = mode == "automatic"
    session = {
        "phase": "completed",
        "requested_gain_mode": mode,
        "gain_control": "apple_agc" if automatic else "software",
        "agc_enabled_observed": automatic,
        "voice_processing_enabled_observed": True,
        "gain_control_verified": True,
        "processing_active": True,
        "device_name": "MacBook Pro Microphone",
        "system_default": True,
        "native_backend": "objective_cpp",
        "processing_mode": "macos_voice_processing",
        "converter_name": "AVAudioConverter",
        "source_sample_rate_hz": 48_000.0,
        "source_channels": 1,
        "highpass_effective": True,
        "fallback_code": None,
        "fallback_stage": None,
        "queue_dropped_blocks": 0,
        "runtime_fault_count": 0,
        "runtime_fault_code": None,
        "output_sample_rate_hz": 16_000,
        "output_channels": 1,
        "preferred_microphone_mode": "standard",
        "active_microphone_mode": "standard",
    }
    session.update(overrides)
    return session


def _recorder_factory(session_builder):
    class FakeRecorder:
        def __init__(self, **_kwargs):
            self.mode = "manual"

        def set_gain_mode(self, mode):
            self.mode = mode

        def set_gain(self, _gain):
            pass

        def set_highpass_filter(self, _enabled):
            pass

        def set_highpass_freq(self, _frequency):
            pass

        def set_soft_limiter(self, _enabled):
            pass

        def start(self):
            pass

        def stop(self):
            return b"\x00\x00" * 800

        @property
        def input_status(self):
            return {"last_session": session_builder(self.mode)}

    return FakeRecorder


def test_capture_plan_uses_abba_order_across_adjacent_pairs():
    from scripts.capture_audio_quality_ab import build_capture_plan

    plan = build_capture_plan(4)

    assert [(trial.pair_id, trial.gain_mode) for trial in plan] == [
        ("pair-01", "automatic"),
        ("pair-01", "manual"),
        ("pair-02", "manual"),
        ("pair-02", "automatic"),
        ("pair-03", "automatic"),
        ("pair-03", "manual"),
        ("pair-04", "manual"),
        ("pair-04", "automatic"),
    ]


@pytest.mark.parametrize("pairs", [-1, 0, 1, 3])
def test_capture_plan_requires_complete_abba_blocks(pairs):
    from scripts.capture_audio_quality_ab import build_capture_plan

    with pytest.raises(ValueError, match="positive even"):
        build_capture_plan(pairs)


def test_stimulus_file_binds_one_private_phrase_to_each_pair(tmp_path):
    from scripts.capture_audio_quality_ab import _load_stimuli

    prompts = tmp_path / "private-prompts.txt"
    prompts.write_text("第一句\nSecond phrase\n", encoding="utf-8")

    stimuli, digest = _load_stimuli(prompts, pairs=2)

    assert stimuli == ("第一句", "Second phrase")
    assert digest == hashlib.sha256(
        "第一句\nSecond phrase".encode("utf-8")
    ).hexdigest()

    with pytest.raises(SystemExit, match="exactly 4"):
        _load_stimuli(prompts, pairs=4)


def test_capture_protocol_writes_runtime_observed_controls_and_valid_manifest(
    tmp_path,
):
    from scripts.capture_audio_quality_ab import (
        capture_protocol,
        write_capture_outputs,
    )
    from vocal_more.audio_quality_benchmark import load_audio_quality_manifest

    requested_modes = []
    prompts = []

    class FakeRecorder:
        def __init__(self, **_kwargs):
            self.mode = "manual"

        def set_gain_mode(self, mode):
            self.mode = mode
            requested_modes.append(mode)

        def set_gain(self, _gain):
            return None

        def set_highpass_filter(self, _enabled):
            return None

        def set_highpass_freq(self, _frequency):
            return None

        def set_soft_limiter(self, _enabled):
            return None

        def start(self):
            return None

        def stop(self):
            return (b"\x00\x00\x00\x10" * 400)

        @property
        def input_status(self):
            return {"last_session": _verified_runtime_session(self.mode)}

    recordings = capture_protocol(
        output_dir=tmp_path,
        pairs=2,
        duration_seconds=0,
        manual_gain=8.0,
        tags=("whisper", "quiet_office"),
        recorder_factory=FakeRecorder,
        prompt=lambda trial: prompts.append(trial.capture_sequence),
        sleep=lambda _seconds: None,
    )
    manifest_path, report_path, runtime_path = write_capture_outputs(
        output_dir=tmp_path,
        recordings=recordings,
        environment={
            "hardware_model": "MacBookPro-test",
            "microphone": "built-in microphone",
            "macos_version": "test",
            "app_version": "test",
        },
        suite_id="test-suite",
    )

    assert requested_modes == ["automatic", "manual", "manual", "automatic"]
    assert prompts == [1, 2, 3, 4]
    manifest = load_audio_quality_manifest(manifest_path)
    assert len(manifest.recordings) == 4
    assert [row.actual_gain_control for row in manifest.recordings] == [
        "apple_agc",
        "software",
        "software",
        "apple_agc",
    ]
    assert {row.runtime_facts["device_name"] for row in manifest.recordings} == {
        "MacBook Pro Microphone"
    }
    assert manifest.runtime_provenance_verified is True
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["comparison_readiness"][
        "apple_agc_metric_comparison_available"
    ] is True
    assert report["capture_order"]["abba_blocks_valid"] is True
    assert report["capture_order"]["counterbalanced"] is True
    assert all(not Path(row.audio).is_absolute() for row in manifest.recordings)
    assert runtime_path.exists()
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert runtime["manifest_fingerprint"] == manifest.fingerprint
    assert runtime["runtime_records_sha256"] == manifest.provenance[
        "runtime_records_sha256"
    ]
    assert len(runtime["sessions"]) == 4
    assert all(
        session["pair_validation"]["valid"]
        for session in runtime["sessions"]
    )
    for private_output in (manifest_path, report_path, runtime_path):
        if os.name != "nt":
            assert private_output.stat().st_mode & 0o777 == 0o600
        assert str(tmp_path.resolve()) not in private_output.read_text(
            encoding="utf-8"
        )
    for capture in (tmp_path / "captures").glob("*.wav"):
        if os.name != "nt":
            assert capture.stat().st_mode & 0o777 == 0o600


def test_automatic_software_fallback_is_recorded_but_not_claimed_as_apple_agc(
    tmp_path,
):
    from scripts.capture_audio_quality_ab import (
        capture_protocol,
        write_capture_outputs,
    )

    class FallbackRecorder:
        def __init__(self, **_kwargs):
            self.mode = "manual"

        def set_gain_mode(self, mode):
            self.mode = mode

        def set_gain(self, _gain):
            return None

        def set_highpass_filter(self, _enabled):
            return None

        def set_highpass_freq(self, _frequency):
            return None

        def set_soft_limiter(self, _enabled):
            return None

        def start(self):
            return None

        def stop(self):
            return b"\x00\x00" * 800

        @property
        def input_status(self):
            fallback = self.mode == "automatic"
            return {
                "last_session": _verified_runtime_session(
                    self.mode,
                    gain_control=("software_fallback" if fallback else "software"),
                    agc_enabled_observed=None,
                    voice_processing_enabled_observed=False,
                    native_backend="portaudio",
                    processing_mode="system_managed_mono",
                    converter_name=None,
                    fallback_code=(
                        "voice_processing_start_failed" if fallback else None
                    ),
                )
            }

    recordings = capture_protocol(
        output_dir=tmp_path,
        pairs=2,
        duration_seconds=0,
        manual_gain=8.0,
        tags=("whisper",),
        recorder_factory=FallbackRecorder,
        prompt=lambda _trial: None,
        sleep=lambda _seconds: None,
    )

    assert recordings[0]["actual_gain_control"] == "software_fallback"
    assert recordings[0]["runtime_status"]["fallback_code"] == (
        "voice_processing_start_failed"
    )
    _manifest, report_path, _runtime = write_capture_outputs(
        output_dir=tmp_path,
        recordings=recordings,
        environment={
            "hardware_model": "MacBookPro-test",
            "microphone": "built-in microphone",
            "macos_version": "test",
            "app_version": "test",
        },
        suite_id="fallback-test",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["comparison_readiness"]["apple_agc_pair_count"] == 0
    assert report["paired_deltas_apple_agc_minus_manual"]["pairs"] == []


def test_capture_rejects_runtime_device_identity_drift(tmp_path):
    from scripts.capture_audio_quality_ab import capture_protocol

    def session(mode):
        return _verified_runtime_session(
            mode,
            device_name=(
                "MacBook Pro Microphone"
                if mode == "automatic"
                else "USB Microphone"
            ),
        )

    with pytest.raises(RuntimeError, match="device identity changed"):
        capture_protocol(
            output_dir=tmp_path,
            pairs=2,
            duration_seconds=0,
            manual_gain=8.0,
            tags=("whisper",),
            recorder_factory=_recorder_factory(session),
            prompt=lambda _trial: None,
            sleep=lambda _seconds: None,
        )


def test_capture_microphone_option_matches_runtime_reported_device(tmp_path):
    from scripts.capture_audio_quality_ab import capture_protocol

    with pytest.raises(RuntimeError, match="does not match --microphone"):
        capture_protocol(
            output_dir=tmp_path,
            pairs=2,
            duration_seconds=0,
            manual_gain=8.0,
            tags=("whisper",),
            recorder_factory=_recorder_factory(_verified_runtime_session),
            prompt=lambda _trial: None,
            sleep=lambda _seconds: None,
            expected_device_name="USB Microphone",
        )

    assert not list(tmp_path.rglob("*.wav"))


def test_verified_provenance_still_excludes_backend_route_difference(tmp_path):
    from scripts.capture_audio_quality_ab import (
        capture_protocol,
        write_capture_outputs,
    )

    def session(mode):
        return _verified_runtime_session(
            mode,
            native_backend=("objective_cpp" if mode == "automatic" else "pyobjc"),
        )

    recordings = capture_protocol(
        output_dir=tmp_path,
        pairs=2,
        duration_seconds=0,
        manual_gain=8.0,
        tags=("whisper",),
        recorder_factory=_recorder_factory(session),
        prompt=lambda _trial: None,
        sleep=lambda _seconds: None,
    )
    manifest_path, report_path, _runtime = write_capture_outputs(
        output_dir=tmp_path,
        recordings=recordings,
        environment={
            "hardware_model": "MacBookPro-test",
            "microphone": "free-form label must be replaced",
            "macos_version": "test",
            "app_version": "test",
        },
        suite_id="backend-mismatch",
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["paired_deltas_automatic_minus_manual"]["pair_count"] == 2
    assert report["paired_deltas_apple_agc_minus_manual"]["pair_count"] == 0
    assert "native_backend_mismatch" in report["apple_agc_validation"][
        "pairs"
    ][0]["reasons"]
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "microphone: MacBook Pro Microphone" in manifest_text
    assert "free-form label must be replaced" not in manifest_text


def test_runtime_sidecar_tampering_invalidates_generated_provenance(tmp_path):
    from scripts.capture_audio_quality_ab import (
        capture_protocol,
        write_capture_outputs,
    )
    from vocal_more.audio_quality_benchmark import (
        AudioQualityManifestError,
        load_audio_quality_manifest,
    )

    recordings = capture_protocol(
        output_dir=tmp_path,
        pairs=2,
        duration_seconds=0,
        manual_gain=8.0,
        tags=("whisper",),
        recorder_factory=_recorder_factory(_verified_runtime_session),
        prompt=lambda _trial: None,
        sleep=lambda _seconds: None,
    )
    manifest_path, _report, runtime_path = write_capture_outputs(
        output_dir=tmp_path,
        recordings=recordings,
        environment={
            "hardware_model": "MacBookPro-test",
            "microphone": "MacBook Pro Microphone",
            "macos_version": "test",
            "app_version": "test",
        },
        suite_id="tamper-test",
    )
    sidecar = json.loads(runtime_path.read_text(encoding="utf-8"))
    sidecar["sessions"][0]["runtime_status"]["device_name"] = "Other Device"
    runtime_path.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(AudioQualityManifestError, match="runtime.*hash"):
        load_audio_quality_manifest(manifest_path)


def test_generated_manifest_runtime_facts_are_bound_to_sidecar(tmp_path):
    from scripts.capture_audio_quality_ab import (
        capture_protocol,
        write_capture_outputs,
    )
    from vocal_more.audio_quality_benchmark import (
        AudioQualityManifestError,
        load_audio_quality_manifest,
    )

    recordings = capture_protocol(
        output_dir=tmp_path,
        pairs=2,
        duration_seconds=0,
        manual_gain=8.0,
        tags=("whisper",),
        recorder_factory=_recorder_factory(_verified_runtime_session),
        prompt=lambda _trial: None,
        sleep=lambda _seconds: None,
    )
    manifest_path, _report, _runtime = write_capture_outputs(
        output_dir=tmp_path,
        recordings=recordings,
        environment={
            "hardware_model": "MacBookPro-test",
            "microphone": "MacBook Pro Microphone",
            "macos_version": "test",
            "app_version": "test",
        },
        suite_id="manifest-tamper-test",
    )
    manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        manifest.replace(
            "device_name: MacBook Pro Microphone",
            "device_name: Other Device",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(AudioQualityManifestError, match="runtime.*fingerprint"):
        load_audio_quality_manifest(manifest_path)


def test_unverified_apple_agc_is_rejected_before_private_audio_is_written(
    tmp_path,
):
    from scripts.capture_audio_quality_ab import capture_protocol

    class UnverifiedRecorder:
        def __init__(self, **_kwargs):
            self.mode = "manual"

        def set_gain_mode(self, mode):
            self.mode = mode

        def set_gain(self, _gain):
            pass

        def set_highpass_filter(self, _enabled):
            pass

        def set_highpass_freq(self, _frequency):
            pass

        def set_soft_limiter(self, _enabled):
            pass

        def start(self):
            pass

        def stop(self):
            return b"\x00\x00" * 800

        @property
        def input_status(self):
            return {
                "last_session": {
                    "phase": "completed",
                    "requested_gain_mode": self.mode,
                    "gain_control": "apple_agc",
                    "gain_control_verified": False,
                    "voice_processing_enabled_observed": True,
                    "agc_enabled_observed": None,
                    "processing_active": True,
                    "queue_dropped_blocks": 0,
                    "runtime_fault_count": 0,
                    "output_sample_rate_hz": 16000,
                    "output_channels": 1,
                }
            }

    with pytest.raises(RuntimeError, match="not verified"):
        capture_protocol(
            output_dir=tmp_path,
            pairs=2,
            duration_seconds=0,
            manual_gain=8.0,
            tags=("whisper",),
            recorder_factory=UnverifiedRecorder,
            prompt=lambda _trial: None,
            sleep=lambda _seconds: None,
        )

    assert not list(tmp_path.rglob("*.wav"))


def test_capture_stops_and_discards_audio_when_timing_is_interrupted(tmp_path):
    from scripts.capture_audio_quality_ab import capture_protocol

    instances = []

    class InterruptibleRecorder:
        def __init__(self, **_kwargs):
            self.stopped = False
            instances.append(self)

        def set_gain_mode(self, _mode):
            pass

        def set_gain(self, _gain):
            pass

        def set_highpass_filter(self, _enabled):
            pass

        def set_highpass_freq(self, _frequency):
            pass

        def set_soft_limiter(self, _enabled):
            pass

        def start(self):
            pass

        def stop(self):
            self.stopped = True
            return b"private"

    def interrupted(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        capture_protocol(
            output_dir=tmp_path,
            pairs=2,
            duration_seconds=1,
            manual_gain=8.0,
            tags=("whisper",),
            recorder_factory=InterruptibleRecorder,
            prompt=lambda _trial: None,
            sleep=interrupted,
        )

    assert instances[0].stopped is True
    assert not list(tmp_path.rglob("*.wav"))


def test_capture_refuses_to_overwrite_an_existing_private_recording(tmp_path):
    from scripts.capture_audio_quality_ab import capture_protocol

    destination = tmp_path / "captures" / "pair-01-automatic.wav"
    destination.parent.mkdir()
    destination.write_bytes(b"do-not-overwrite")

    with pytest.raises(FileExistsError, match="new output directory"):
        capture_protocol(
            output_dir=tmp_path,
            pairs=2,
            duration_seconds=0,
            manual_gain=8.0,
            tags=("whisper",),
            recorder_factory=lambda **_kwargs: None,
            prompt=lambda _trial: None,
            sleep=lambda _seconds: None,
        )

    assert destination.read_bytes() == b"do-not-overwrite"


def test_cli_rejects_too_short_protocol_before_opening_microphone(
    tmp_path,
    monkeypatch,
):
    import scripts.capture_audio_quality_ab as capture

    monkeypatch.setattr(capture.platform, "system", lambda: "Darwin")
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("one\ntwo\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="at least 2 seconds"):
        capture.main(
            [
                "--yes",
                "--output-dir",
                str(tmp_path / "new-output"),
                "--pairs",
                "2",
                "--prompt-file",
                str(prompts),
                "--duration",
                "1",
            ]
        )


def test_cli_refuses_private_audio_inside_repository(tmp_path, monkeypatch):
    import scripts.capture_audio_quality_ab as capture

    monkeypatch.setattr(capture.platform, "system", lambda: "Darwin")
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("one\ntwo\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="inside the repository"):
        capture.main(
            [
                "--yes",
                "--output-dir",
                str(capture.ROOT / "eval" / "private-capture"),
                "--pairs",
                "2",
                "--prompt-file",
                str(prompts),
                "--duration",
                "5",
            ]
        )
