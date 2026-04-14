"""Tests for Python app config propagation."""

import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock


def _install_rumps_stub(monkeypatch) -> None:
    import AppKit

    rumps = types.ModuleType("rumps")

    class DummyApp:
        def __init__(self, *args, **kwargs):
            pass

    class DummyMenuItem:
        def __init__(self, title, callback=None):
            self.title = title
            self.callback = callback
            self.children = []
            self.state = 0

        def set_callback(self, callback):
            self.callback = callback

        def add(self, menuitem):
            self.children.append(menuitem)

    rumps.App = DummyApp
    rumps.MenuItem = DummyMenuItem
    rumps.notification = lambda *args, **kwargs: None
    rumps.quit_application = lambda: None
    monkeypatch.setitem(sys.modules, "rumps", rumps)

    AppKit.NSEvent = type("NSEvent", (), {})
    AppKit.NSPanel = type("NSPanel", (), {})
    AppKit.NSPointInRect = lambda *args, **kwargs: False
    AppKit.NSWindowStyleMaskBorderless = 0
    AppKit.NSWindowStyleMaskNonactivatingPanel = 0


def test_settings_form_sync_updates_all_audio_processing_controls(
    tmp_path, monkeypatch
):
    """Closing the settings window should push all audio settings into recorders."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()

    recorder_one = MagicMock()
    recorder_two = MagicMock()
    app._walkie_talkie = SimpleNamespace(_recorder=recorder_one, text_polisher=None)
    app._realtime_long = SimpleNamespace(_recorder=recorder_two, text_polisher=None)
    app._hotkey_manager = MagicMock()
    app._refresh_text_polisher = MagicMock()
    app._select_mode = MagicMock()

    app._on_settings_sync_form_state(
        {
            "api_key": "updated-key",
            "default_mode": "realtime_long",
            "auto_paste": False,
            "enable_polish": True,
            "audio": {
                "input_device": "USB Mic",
                "gain": 5.0,
                "noise_gate": 0.2,
                "highpass_filter": False,
                "highpass_freq": 410,
                "soft_limiter": False,
            },
            "hotkey": {
                "active_hotkeys": ["printscreen", "bogus"],
            },
        }
    )

    assert app.config.audio.input_device == "USB Mic"
    assert app.config.audio.gain == 5.0
    assert app.config.audio.noise_gate == 0.2
    assert app.config.audio.highpass_filter is False
    assert app.config.audio.highpass_freq == 410
    assert app.config.audio.soft_limiter is False
    assert app.config.hotkey.active_hotkeys == ["f13"]

    for recorder in (recorder_one, recorder_two):
        recorder.set_device.assert_called_with("USB Mic")
        recorder.set_gain.assert_called_with(5.0)
        recorder.set_noise_gate.assert_called_with(0.2)
        recorder.set_highpass_filter.assert_called_with(False)
        recorder.set_highpass_freq.assert_called_with(410)
        recorder.set_soft_limiter.assert_called_with(False)

    app._hotkey_manager.set_active_hotkeys.assert_called_with(["f13"])
    app._select_mode.assert_called_with("realtime_long")
    app._refresh_text_polisher.assert_called_once()


def test_build_menu_adds_quick_settings_and_marks_current_config(
    tmp_path, monkeypatch
):
    """Quick settings menu should mirror the active common config values."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("asr.model", "qwen3.5-omni-plus")
    app.config.apply_update("enable_polish", False)
    app.config.apply_update("llm.level", "strong")

    idle_mode = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._walkie_talkie = idle_mode
    app._realtime_long = idle_mode
    app._current_mode = idle_mode

    app._build_menu()

    assert [item.title for item in app.menu if item][2] == "Quick Settings"
    assert app._quick_mode_item.title == "Recording Mode: Walkie-Talkie (Hold)"
    assert app._quick_asr_model_item.title == "ASR Model: Pro"
    assert app._quick_enable_polish_item.state == 0
    assert app._quick_polish_level_item.title == "Polish Strength: Strong"
    assert app._mode_menu_items["walkie_talkie"].state == 1
    assert app._asr_model_menu_items["qwen3.5-omni-plus"].state == 1
    assert app._polish_level_menu_items["strong"].state == 1


def test_quick_settings_actions_update_config_and_menu_state(
    tmp_path, monkeypatch
):
    """Status-bar quick settings should save config and refresh checkmarks."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._current_mode = app._walkie_talkie
    app._build_menu()

    app._on_quick_set_asr_model("qwen3.5-omni-plus")
    app._on_quick_toggle_polish(None)
    app._on_quick_set_polish_level("balanced")
    app._on_quick_set_mode("realtime_long")

    assert app.config.asr.model == "qwen3.5-omni-plus"
    assert app.config.asr.backend == "omni_offline"
    assert app.config.enable_polish is False
    assert app.config.llm.level == "balanced"
    assert app.config.default_mode == "realtime_long"
    assert app._current_mode is app._realtime_long
    assert app._asr_model_menu_items["qwen3.5-omni-plus"].state == 1
    assert app._quick_enable_polish_item.state == 0
    assert app._polish_level_menu_items["balanced"].state == 1
    assert app._mode_menu_items["realtime_long"].state == 1
