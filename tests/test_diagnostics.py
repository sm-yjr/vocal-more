"""Tests for diagnostics export and runtime environment checks."""

import json
import sys
import types
import zipfile

import yaml


def test_export_support_bundle_includes_redacted_config_and_trace(
    tmp_path, monkeypatch
):
    """Support bundle should include redacted config, recordings, dictionary, and traces."""
    from vocal_more.config import Config
    from vocal_more.core.recording_store import RecordingStore
    from vocal_more.diagnostics import export_support_bundle
    from vocal_more.environment_check import EnvironmentCheckResult

    config_path = tmp_path / "config.yaml"
    debug_dir = tmp_path / "debug"
    dict_path = tmp_path / "dictionary.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    monkeypatch.setenv("VOCAL_MORE_DEBUG_DIR", str(debug_dir))

    config = Config()
    config.api_key = "sk-test-12345678"
    config.apply_update("asr.model", "qwen3.5-omni-plus")
    config.save()

    dict_path.write_text(
        yaml.dump({"entries": [{"term": "Claude", "aliases": ["可劳德"]}]}, allow_unicode=True),
        encoding="utf-8",
    )

    debug_dir.mkdir(parents=True)
    trace_path = debug_dir / "20260416-000000-batch-omni_offline.json"
    trace_path.write_text(json.dumps({"response_id": "resp-123"}, ensure_ascii=False), encoding="utf-8")
    trace_path.with_suffix(".wav").write_bytes(b"RIFFtest")

    recording_store = RecordingStore(str(tmp_path / "recordings"))
    rec_id = recording_store.save(b"\x01\x00" * 4000, "walkie_talkie", "qwen3.5-omni-plus")
    recording_store.update(rec_id, "failed", error="timeout")

    bundle_path = export_support_bundle(
        config=config,
        recording_store=recording_store,
        environment_checks=[
            EnvironmentCheckResult("api_key", "ok", "configured"),
            EnvironmentCheckResult("accessibility", "error", "missing"),
        ],
        app_version="0.2.0",
    )

    assert bundle_path.exists()
    with zipfile.ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        config_snapshot = json.loads(bundle.read("config.snapshot.json"))
        manifest = json.loads(bundle.read("manifest.json"))

    assert "dictionary.yaml" in names
    assert "recordings.json" in names
    assert "selected_recording.json" in names
    assert any(name.startswith("recordings/") and name.endswith(".wav") for name in names)
    assert f"debug/{trace_path.name}" in names
    assert f"debug/{trace_path.with_suffix('.wav').name}" in names
    assert config_snapshot["api_key"] == "sk-t***5678"
    assert manifest["trace_files"] == [trace_path.name]
    assert manifest["recording_id"] == rec_id
    assert manifest["config_path"] == "config.yaml"
    assert manifest["dictionary_path"] == "dictionary.yaml"
    assert manifest["debug_dir"] == "debug/"
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert str(tmp_path) not in manifest_text


def test_export_support_bundle_redacts_external_debug_dir_in_manifest(
    tmp_path, monkeypatch
):
    """Manifest should not expose absolute external debug paths."""
    from vocal_more.config import Config
    from vocal_more.core.recording_store import RecordingStore
    from vocal_more.diagnostics import export_support_bundle

    config_path = tmp_path / "config.yaml"
    external_debug_dir = tmp_path.parent / "private-debug-cache"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    monkeypatch.setenv("VOCAL_MORE_DEBUG_DIR", str(external_debug_dir))

    config = Config()
    config.save()

    external_debug_dir.mkdir(parents=True, exist_ok=True)
    trace_path = external_debug_dir / "20260416-000000-batch-omni_offline.json"
    trace_path.write_text(json.dumps({"response_id": "resp-123"}, ensure_ascii=False), encoding="utf-8")

    bundle_path = export_support_bundle(
        config=config,
        recording_store=RecordingStore(str(tmp_path / "recordings")),
        environment_checks=[],
        app_version="0.2.0",
    )

    with zipfile.ZipFile(bundle_path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))

    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert manifest["debug_dir"] == "<external-debug-dir>"
    assert manifest["trace_files"] == [trace_path.name]
    assert str(external_debug_dir) not in manifest_text
    assert str(tmp_path.parent) not in manifest_text


def test_export_support_bundle_redacts_environment_check_path_details(
    tmp_path, monkeypatch
):
    """Manifest should not leak absolute paths through environment-check details."""
    from vocal_more.config import Config
    from vocal_more.core.recording_store import RecordingStore
    from vocal_more.diagnostics import export_support_bundle
    from vocal_more.environment_check import run_environment_checks

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    checks = run_environment_checks(config, hotkey_listener_ready=False)

    bundle_path = export_support_bundle(
        config=config,
        recording_store=RecordingStore(str(tmp_path / "recordings")),
        environment_checks=checks,
        app_version="0.2.0",
    )

    with zipfile.ZipFile(bundle_path) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))

    manifest_text = json.dumps(manifest, ensure_ascii=False)
    api_key_check = next(check for check in manifest["environment_checks"] if check["key"] == "api_key")

    assert api_key_check["details"] == (
        "DashScope API key not configured. "
        "Set DASHSCOPE_API_KEY environment variable or add api_key to config.yaml"
    )
    assert str(config_path) not in manifest_text
    assert str(tmp_path) not in manifest_text


def test_run_environment_checks_reports_errors(monkeypatch):
    """Environment checks should surface missing prerequisites consistently."""
    from vocal_more.config import Config
    from vocal_more.environment_check import run_environment_checks

    fake_app_services = types.ModuleType("ApplicationServices")
    fake_app_services.AXIsProcessTrusted = lambda: False
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_app_services)
    monkeypatch.setattr(
        "vocal_more.environment_check.AudioRecorder.list_input_devices",
        lambda: [],
    )

    config = Config()
    config.api_key = ""
    checks = {check.key: check for check in run_environment_checks(config, hotkey_listener_ready=False)}

    assert checks["api_key"].status == "error"
    assert checks["accessibility"].status == "error"
    assert checks["input_device"].status == "error"
    assert checks["hotkey_listener"].status == "error"
