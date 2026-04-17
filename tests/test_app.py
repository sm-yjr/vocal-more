"""Tests for Python app config propagation."""

import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


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
    app.config.apply_update("ui.language", "en")

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
    assert app.config.audio.highpass_filter is False
    assert app.config.audio.highpass_freq == 410
    assert app.config.audio.soft_limiter is False
    assert app.config.hotkey.active_hotkeys == ["f13"]

    for recorder in (recorder_one, recorder_two):
        recorder.set_device.assert_called_with("USB Mic")
        recorder.set_gain.assert_called_with(5.0)
        recorder.set_highpass_filter.assert_called_with(False)
        recorder.set_highpass_freq.assert_called_with(410)
        recorder.set_soft_limiter.assert_called_with(False)

    app._hotkey_manager.set_active_hotkeys.assert_called_with(["f13"])
    app._select_mode.assert_called_with("realtime_long")
    app._refresh_text_polisher.assert_called_once()


def test_refresh_text_polisher_updates_mode_asr_runtime(
    tmp_path, monkeypatch
):
    """API key refresh should update text polishers and invalidate idle ASR sessions."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.api_key = "updated-key"

    asr_one = MagicMock()
    asr_two = MagicMock()
    app._walkie_talkie = SimpleNamespace(_asr=asr_one, text_polisher=None)
    app._realtime_long = SimpleNamespace(_asr=asr_two, text_polisher=None)

    with patch.object(app_module, "TextPolisher", return_value=object()) as polisher_cls:
        app._refresh_text_polisher()

    polisher_cls.assert_called_once()
    assert app._walkie_talkie.text_polisher is app._text_polisher
    assert app._realtime_long.text_polisher is app._text_polisher
    asr_one.refresh_api_key.assert_called_once()
    asr_two.refresh_api_key.assert_called_once()


def test_settings_form_sync_refreshes_runtime_sensitive_config_without_restart(
    tmp_path, monkeypatch
):
    """ASR/LLM/general settings should take effect in the running app immediately."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()

    walkie_asr = MagicMock()
    realtime_asr = MagicMock()
    recorder_one = MagicMock()
    recorder_two = MagicMock()
    app._walkie_talkie = SimpleNamespace(
        _asr=walkie_asr,
        _recorder=recorder_one,
        text_polisher=None,
        state=app_module.ModeState.IDLE,
    )
    app._realtime_long = SimpleNamespace(
        _asr=realtime_asr,
        _recorder=recorder_two,
        text_polisher=None,
        state=app_module.ModeState.IDLE,
    )
    app._current_mode = app._realtime_long
    app._hotkey_manager = MagicMock()
    app._refresh_text_polisher = MagicMock()
    app._apply_interface_language = MagicMock()
    app._refresh_quick_settings_menu = MagicMock()

    app._on_settings_sync_form_state(
        {
            "api_key": "updated-key",
            "default_mode": "walkie_talkie",
            "enable_polish": False,
            "ui": {"language": "en"},
            "audio": {"gain": 3.0},
            "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "en"},
            "llm": {"level": "strong"},
            "hotkey": {
                "active_hotkeys": ["f13"],
                "custom_key": {
                    "key_code": 105,
                    "display_name": "F13",
                    "is_modifier": False,
                    "flag_mask": 0,
                },
            },
        }
    )

    assert app._current_mode is app._walkie_talkie
    app._refresh_text_polisher.assert_called_once()
    app._apply_interface_language.assert_called_once()
    app._hotkey_manager.set_active_hotkeys.assert_called_once_with(["f13"])
    app._hotkey_manager.set_custom_key.assert_called_once_with(
        {
            "key_code": 105,
            "display_name": "F13",
            "is_modifier": False,
            "flag_mask": 0,
        }
    )
    recorder_one.set_gain.assert_called_with(3.0)
    recorder_two.set_gain.assert_called_with(3.0)
    walkie_asr.refresh_runtime_config.assert_called_once_with(drop_idle_session=True)
    realtime_asr.refresh_runtime_config.assert_called_once_with(drop_idle_session=True)


def test_default_mode_change_waits_until_idle_before_switching_runtime_mode(
    tmp_path, monkeypatch
):
    """Changing default_mode should not replace the active mode mid-recording."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "en")
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.RECORDING)
    app._current_mode = app._realtime_long
    app._refresh_quick_settings_menu = MagicMock()
    app._apply_runtime_config_keys = app_module.VocalMoreApp._apply_runtime_config_keys.__get__(app, app_module.VocalMoreApp)

    app._on_settings_config_change("default_mode", "walkie_talkie")

    assert app._current_mode is app._realtime_long

    app._realtime_long.state = app_module.ModeState.IDLE
    app._select_default_mode_when_safe()

    assert app._current_mode is app._walkie_talkie


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
    app.config.apply_update("ui.language", "en")
    app.config.apply_update("asr.model", "qwen3.5-omni-plus")
    app.config.apply_update("enable_polish", False)
    app.config.apply_update("llm.level", "strong")

    idle_mode = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._walkie_talkie = idle_mode
    app._realtime_long = idle_mode
    app._current_mode = idle_mode

    app._build_menu()

    titles = [item.title for item in app.menu if item]
    assert "Environment: Pending" in titles
    assert "Recording Mode: Real-time Long (Toggle)" in titles
    assert "ASR Model: Pro" in titles
    assert "Enable Polishing" in titles
    assert "Polish Strength: Strong" in titles
    assert "Export Diagnostics…" in titles
    assert app._quick_enable_polish_item.state == 0
    assert app._mode_menu_items["realtime_long"].state == 1
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
    app.config.apply_update("ui.language", "en")
    app.config.apply_update("default_mode", "walkie_talkie")
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


def test_build_menu_localizes_titles_when_ui_language_is_chinese(
    tmp_path, monkeypatch
):
    """Status-bar menu strings should respect the configured UI language."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "zh")

    idle_mode = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._walkie_talkie = idle_mode
    app._realtime_long = idle_mode
    app._current_mode = idle_mode

    app._build_menu()

    titles = [item.title for item in app.menu if item]
    assert "环境：待检查" in titles
    assert "录音模式：实时长录（切换）" in titles
    assert "识别模型：Lite Fast" in titles
    assert "启用润色" in titles
    assert "润色强度：轻度" in titles
    assert "导出诊断包…" in titles
    assert app._state_item.title == "状态：空闲"
    assert app._quick_enable_polish_item.title == "启用润色"
    assert app._settings_menu_item.title == "更多设置..."
    assert app._quit_menu_item.title == "退出 Vocal-More"


def test_refresh_environment_status_updates_menu_titles(
    tmp_path, monkeypatch
):
    """Environment submenu should reflect the latest runtime checks."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "en")
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._current_mode = app._realtime_long
    app._hotkey_listener_ready = False

    monkeypatch.setattr(
        app_module,
        "run_environment_checks",
        lambda config, hotkey_listener_ready=None: [
            SimpleNamespace(key="api_key", status="ok"),
            SimpleNamespace(key="accessibility", status="error"),
            SimpleNamespace(key="input_device", status="ok"),
            SimpleNamespace(key="hotkey_listener", status="error"),
        ],
    )

    app._build_menu()
    app._refresh_environment_status()

    assert app._environment_item.title == "Environment: Needs Attention"
    assert app._environment_check_items["api_key"].title == "API Key: Configured"
    assert app._environment_check_items["accessibility"].title == "Accessibility: Missing"
    assert app._environment_check_items["hotkey_listener"].title == "Hotkey Listener: Failed"


def test_processing_state_updates_capsule_stage(tmp_path, monkeypatch):
    """Entering processing should reset the capsule to the transcribing phase."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._state_item = SimpleNamespace(title="")
    app._capsule = MagicMock()
    app._get_icon_path = lambda _name: None

    app._on_state_change(app_module.ModeState.PROCESSING)

    assert app._state_item.title == "状态：处理中..."
    app._capsule.update_state.assert_called_once_with("processing")
    app._capsule.set_processing_stage.assert_called_once_with("transcribing")


def test_processing_stage_callback_forwards_to_capsule(tmp_path, monkeypatch):
    """Mode stage updates should be reflected by the floating capsule."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._capsule = MagicMock()

    app._on_processing_stage("polishing")

    app._capsule.set_processing_stage.assert_called_once_with("polishing")
