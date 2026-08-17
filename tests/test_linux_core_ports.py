"""Focused tests for the cross-platform Linux core seams."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_keyboard_simulator_reports_shortcut_failure_and_keeps_clipboard(monkeypatch):
    from vocal_more.core import keyboard_sim

    class Clipboard:
        def __init__(self):
            self.value = "old"

        def paste(self):
            return self.value

        def copy(self, value):
            self.value = value

    class Keyboard:
        @contextmanager
        def pressed(self, _key):
            raise RuntimeError("input injection unavailable")
            yield  # pragma: no cover

        def press(self, _key):
            raise AssertionError("shortcut should not be sent")

        def release(self, _key):
            raise AssertionError("shortcut should not be sent")

    monkeypatch.setattr(keyboard_sim, "Key", SimpleNamespace(cmd="cmd", ctrl="ctrl"))
    monkeypatch.setattr(keyboard_sim.time, "sleep", lambda _seconds: None)
    clipboard = Clipboard()
    result = keyboard_sim.KeyboardSimulator(
        platform_name="linux",
        keyboard=Keyboard(),
        clipboard=clipboard,
    ).paste_text("kept for retry")

    assert result.success is False
    assert "injection" in (result.error or "")
    assert clipboard.value == "kept for retry"


def test_workflow_does_not_claim_paste_success_on_failed_port():
    from vocal_more.application.dictation_workflow import DictationWorkflow
    from vocal_more.core.text_output import PasteOutcome

    output = MagicMock()
    output.paste_text.return_value = PasteOutcome.failed("Wayland injection timed out")
    asr = MagicMock()
    asr.stop.return_value = "hello"
    asr.get_last_metering.return_value = None
    store = MagicMock()
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="en"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        text_output=output,
        recording_store=store,
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="realtime_long",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=SimpleNamespace(
            empty_transcription="empty",
            processing_error=lambda details: f"processing: {details}",
            polish_error=lambda details: f"polish: {details}",
        ),
    )

    assert result.pasted is False
    assert result.error_code == "paste_failed"
    assert "Wayland" in (result.error_message or "")
    assert store.update.call_args.args[1] == "failed"


def test_linux_accelerator_is_limited_to_f8_through_f12():
    from vocal_more.domain.config_models import AppConfig

    assert AppConfig.from_dict({}).hotkey.linux_accelerator == "F8"
    assert (
        AppConfig.from_dict({"hotkey": {"linux_accelerator": "f11"}})
        .hotkey.linux_accelerator
        == "F11"
    )
    assert (
        AppConfig.from_dict({"hotkey": {"linux_accelerator": "Caps_Lock"}})
        .hotkey.linux_accelerator
        == "F8"
    )


def test_linux_xdg_paths_copy_legacy_data_once_without_deleting_source(
    tmp_path,
    monkeypatch,
):
    from vocal_more import paths

    home = tmp_path / "home"
    legacy = home / ".vocal-more"
    legacy.mkdir(parents=True)
    (legacy / "config.yaml").write_text("api_key: old\n", encoding="utf-8")
    (legacy / "recordings").mkdir()
    (legacy / "recordings" / "one.wav").write_bytes(b"wav")
    (legacy / "debug").mkdir()
    (legacy / "debug" / "trace.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    assert paths.ensure_legacy_linux_migration() is True
    assert (
        tmp_path / "xdg-config" / "vocal-more" / "config.yaml"
    ).read_text(encoding="utf-8") == "api_key: old\n"
    assert (
        tmp_path / "xdg-data" / "vocal-more" / "recordings" / "one.wav"
    ).read_bytes() == b"wav"
    assert (
        tmp_path / "xdg-state" / "vocal-more" / "debug" / "trace.json"
    ).exists()
    assert legacy.exists()
    assert paths.ensure_legacy_linux_migration() is False
