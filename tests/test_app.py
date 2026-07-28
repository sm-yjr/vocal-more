"""Tests for Python app config propagation."""

import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


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
            self._menu = None

        def set_callback(self, callback):
            self.callback = callback

        def add(self, menuitem):
            if self._menu is None:
                self._menu = object()
            self.children.append(menuitem)

        def clear(self):
            if self._menu is None:
                raise AttributeError("'NoneType' object has no attribute 'removeAllItems'")
            self.children.clear()

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
    assert app.config.hotkey.active_hotkeys == ["fn"]

    for recorder in (recorder_one, recorder_two):
        recorder.set_device.assert_called_with("USB Mic")
        recorder.set_gain.assert_called_with(5.0)
        recorder.set_highpass_filter.assert_called_with(False)
        recorder.set_highpass_freq.assert_called_with(410)
        recorder.set_soft_limiter.assert_called_with(False)

    app._hotkey_manager.set_active_hotkeys.assert_not_called()
    app._select_mode.assert_not_called()
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
                "active_hotkeys": ["fn"],
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
    app._hotkey_manager.set_active_hotkeys.assert_not_called()
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


def test_settings_can_disable_all_builtin_hotkeys(tmp_path, monkeypatch):
    """Custom-key users should be able to turn off every built-in hotkey."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._hotkey_manager = MagicMock()
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.IDLE)

    app._on_settings_set_hotkeys([])

    assert app.config.hotkey.active_hotkeys == []
    app._hotkey_manager.set_active_hotkeys.assert_called_once_with([])


def test_floating_capsule_show_marshals_to_main_thread(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    capsule_module = importlib.import_module("vocal_more.ui.floating_capsule")
    capsule_module = importlib.reload(capsule_module)

    scheduled = []

    class FakeTimer:
        def __init__(self, callback):
            self.callback = callback

        def invalidate(self):
            return None

    class FakeRunLoop:
        def addTimer_forMode_(self, timer, mode):
            scheduled.append((timer, mode))

    monkeypatch.setattr(
        capsule_module.NSTimer,
        "timerWithTimeInterval_repeats_block_",
        lambda interval, repeats, callback: FakeTimer(callback),
        raising=False,
    )
    monkeypatch.setattr(
        capsule_module.NSRunLoop,
        "mainRunLoop",
        lambda: FakeRunLoop(),
        raising=False,
    )

    worker = object()
    main = object()
    monkeypatch.setattr(capsule_module.threading, "current_thread", lambda: worker)
    monkeypatch.setattr(capsule_module.threading, "main_thread", lambda: main)

    capsule = capsule_module.FloatingCapsule.__new__(capsule_module.FloatingCapsule)
    capsule._main_thread_timers = set()
    capsule._show_on_main_thread = MagicMock()

    capsule.show("handsFree")

    assert capsule._show_on_main_thread.call_count == 0
    assert len(scheduled) == 1

    timer, _mode = scheduled[0]
    timer.callback(None)

    capsule._show_on_main_thread.assert_called_once_with("handsFree")


def test_floating_capsule_coalesces_audio_updates_and_pushes_silence_tail(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    capsule_module = importlib.import_module("vocal_more.ui.floating_capsule")
    capsule_module = importlib.reload(capsule_module)
    monkeypatch.setattr(capsule_module.time, "monotonic", lambda: 100.1)

    capsule = capsule_module.FloatingCapsule.__new__(capsule_module.FloatingCapsule)
    capsule._audio_level_lock = capsule_module.threading.Lock()
    capsule._latest_audio_level = 0.5
    capsule._last_pushed_audio_level = 0.49
    capsule._last_audio_level_push_at = 100.0
    capsule._push_count = 0
    capsule._eval_js = MagicMock()

    capsule._push_audio_level()

    capsule._eval_js.assert_not_called()

    capsule._latest_audio_level = 0.0
    capsule._push_audio_level()

    capsule._eval_js.assert_called_once_with("updateAudioLevel(0.0)")
    assert capsule._last_pushed_audio_level == 0.0
    assert 10 <= capsule_module.CAPSULE_AUDIO_PUSH_HZ <= 15


def test_floating_capsule_waveform_interpolates_and_respects_reduced_motion():
    html = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "floating_capsule"
        / "capsule.html"
    ).read_text(encoding="utf-8")

    assert "requestAnimationFrame(renderWaveform)" in html
    assert "prefers-reduced-motion: reduce" in html
    assert "style.transform = `scaleY(" in html


def test_app_state_change_marshals_to_main_thread(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    scheduled = []

    class FakeTimer:
        def __init__(self, callback):
            self.callback = callback

        def invalidate(self):
            return None

    class FakeRunLoop:
        def addTimer_forMode_(self, timer, mode):
            scheduled.append((timer, mode))

    monkeypatch.setattr(
        app_module.NSTimer,
        "timerWithTimeInterval_repeats_block_",
        lambda interval, repeats, callback: FakeTimer(callback),
        raising=False,
    )
    monkeypatch.setattr(
        app_module.NSRunLoop,
        "mainRunLoop",
        lambda: FakeRunLoop(),
        raising=False,
    )

    worker = object()
    main = object()
    monkeypatch.setattr(app_module.threading, "current_thread", lambda: worker)
    monkeypatch.setattr(app_module.threading, "main_thread", lambda: main)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app._main_thread_timers = set()
    app._apply_state_change = MagicMock()

    app._on_state_change(app_module.ModeState.PROCESSING)

    assert app._apply_state_change.call_count == 0
    assert len(scheduled) == 1

    timer, _mode = scheduled[0]
    timer.callback(None)

    app._apply_state_change.assert_called_once_with(app_module.ModeState.PROCESSING)


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
    assert "Microphone: System Default" in titles
    assert "ASR Model: Pro" in titles
    assert "Enable Polishing" in titles
    assert "Polish Strength: Strong" in titles
    assert "Export Diagnostics…" in titles
    assert app._quick_enable_polish_item.state == 0
    assert app._mode_menu_items["realtime_long"].state == 1
    assert app._asr_model_menu_items["qwen3.5-omni-plus"].state == 1
    assert app._polish_level_menu_items["strong"].state == 1
    assert app._microphone_default_item.state == 1


def test_status_bar_microphone_menu_switches_input_device(
    tmp_path, monkeypatch
):
    """Status-bar microphone settings should update the live audio input."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "en")
    app._walkie_talkie = SimpleNamespace(_recorder=MagicMock(), state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(_recorder=MagicMock(), state=app_module.ModeState.IDLE)
    app._current_mode = app._realtime_long
    app._refresh_environment_status = MagicMock()
    monkeypatch.setattr(
        app,
        "_list_devices",
        lambda: [
            {"name": "Built-in Mic", "is_default": True},
            {"name": "USB Mic", "is_default": False},
        ],
    )

    app._build_menu()
    app._on_quick_set_microphone_device("USB Mic")

    assert app.config.audio.input_device == "USB Mic"
    assert app._microphone_default_item.state == 0
    assert app._microphone_device_menu_items["USB Mic"].state == 1
    app._walkie_talkie._recorder.set_device.assert_called_with("USB Mic")
    app._realtime_long._recorder.set_device.assert_called_with("USB Mic")
    app._refresh_environment_status.assert_called()


def test_refresh_devices_rebuilds_status_menu_and_clears_missing_selection(
    tmp_path, monkeypatch
):
    """Device-list refresh should replace stale status-bar microphone choices."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "en")
    app.config.apply_update("audio.input_device", "USB Mic")
    app._walkie_talkie = SimpleNamespace(_recorder=MagicMock(), state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(_recorder=MagicMock(), state=app_module.ModeState.IDLE)
    app._current_mode = app._realtime_long
    app._runtime = SimpleNamespace(
        apply_update=lambda key, value: app.config.apply_update(key, value)
    )
    app._environment_checks = []
    app._refresh_environment_status = MagicMock()
    app._settings_window = MagicMock()

    devices = [
        {"name": "Built-in Mic", "is_default": True},
        {"name": "USB Mic", "is_default": False},
    ]
    monkeypatch.setattr(app, "_list_devices", lambda: devices)

    app._build_menu()
    assert "USB Mic" in app._microphone_device_menu_items

    devices = [
        {"name": "Built-in Mic", "is_default": True},
        {"name": "Desk Mic", "is_default": False},
    ]
    app._on_settings_refresh_devices()

    assert app.config.audio.input_device is None
    assert "USB Mic" not in app._microphone_device_menu_items
    assert "Desk Mic" in app._microphone_device_menu_items
    assert [
        child.title for child in app._quick_microphone_item.children
    ] == [
        "System Default",
        "Built-in Mic",
        "Desk Mic",
        "Microphone Settings...",
    ]
    app._settings_window.update_devices.assert_called_once_with(devices, None)
    app._refresh_environment_status.assert_called_once_with()


def test_status_menu_open_refreshes_microphone_devices_without_settings_window(
    tmp_path, monkeypatch
):
    """Opening the status-bar menu should refresh hot-plugged input devices."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "en")
    app.config.apply_update("audio.input_device", "USB Mic")
    app._walkie_talkie = SimpleNamespace(_recorder=MagicMock(), state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(_recorder=MagicMock(), state=app_module.ModeState.IDLE)
    app._current_mode = app._realtime_long
    app._runtime = SimpleNamespace(
        apply_update=lambda key, value: app.config.apply_update(key, value)
    )
    app._environment_checks = []

    devices = [
        {"name": "Built-in Mic", "is_default": True},
        {"name": "USB Mic", "is_default": False},
    ]
    monkeypatch.setattr(app, "_list_devices", lambda: devices)

    app._build_menu()
    assert "USB Mic" in app._microphone_device_menu_items

    devices = [
        {"name": "Built-in Mic", "is_default": True},
        {"name": "Desk Mic", "is_default": False},
    ]
    app._on_status_menu_will_open()

    assert app.config.audio.input_device is None
    assert "USB Mic" not in app._microphone_device_menu_items
    assert "Desk Mic" in app._microphone_device_menu_items
    assert [
        child.title for child in app._quick_microphone_item.children
    ] == [
        "System Default",
        "Built-in Mic",
        "Desk Mic",
        "Microphone Settings...",
    ]


def test_build_menu_installs_status_menu_delegate(tmp_path, monkeypatch):
    """The AppKit menu delegate should be installed for top-level menu opens."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    class FakeNSMenu:
        def __init__(self):
            self.delegate = None

        def setDelegate_(self, delegate):
            self.delegate = delegate

    ns_menu = FakeNSMenu()

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app._menu = SimpleNamespace(_menu=ns_menu)
    app.config = Config()
    app.config.apply_update("ui.language", "en")
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._current_mode = app._realtime_long
    app._environment_checks = []
    monkeypatch.setattr(
        app,
        "_list_devices",
        lambda: [{"name": "Built-in Mic", "is_default": True}],
    )

    app._build_menu()

    assert ns_menu.delegate is app._status_menu_delegate
    assert getattr(ns_menu.delegate, "_app") is app


def test_device_listing_does_not_reset_portaudio_while_recording(
    tmp_path, monkeypatch
):
    """Refreshing the UI list should not reinitialize PortAudio during capture."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    refresh_values = []
    monkeypatch.setattr(
        app_module.AudioRecorder,
        "list_input_devices",
        staticmethod(
            lambda *, refresh=False: refresh_values.append(refresh)
            or [{"name": "Built-in Mic", "is_default": True}]
        ),
    )

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.RECORDING)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._meeting = None

    assert app._list_devices() == [{"name": "Built-in Mic", "is_default": True}]
    assert refresh_values == [False]


def test_device_listing_does_not_reset_portaudio_during_mic_test(
    tmp_path, monkeypatch
):
    """Settings mic tests also own an input stream, so refresh must avoid resets."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    refresh_values = []
    monkeypatch.setattr(
        app_module.AudioRecorder,
        "list_input_devices",
        staticmethod(
            lambda *, refresh=False: refresh_values.append(refresh)
            or [{"name": "Built-in Mic", "is_default": True}]
        ),
    )

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._walkie_talkie = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._realtime_long = SimpleNamespace(state=app_module.ModeState.IDLE)
    app._meeting = None
    app._settings_window = SimpleNamespace(
        _mic_test_controller=SimpleNamespace(is_running=True)
    )

    assert app._list_devices() == [{"name": "Built-in Mic", "is_default": True}]
    assert refresh_values == [False]


def test_status_bar_microphone_settings_opens_audio_tab(
    tmp_path, monkeypatch
):
    """The microphone menu should deep-link into the Audio settings tab."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._show_settings = MagicMock()

    app._open_microphone_settings()

    app._show_settings.assert_called_once_with(initial_tab="audio")


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
    assert "检查更新…" in titles
    assert app._state_item.title == "状态：空闲"
    assert app._quick_enable_polish_item.title == "启用润色"
    assert app._settings_menu_item.title == "更多设置..."
    assert app._check_for_updates_item.title == "检查更新…"
    assert app._quit_menu_item.title == "退出 Vocal-More"


def test_check_for_updates_uses_sparkle_controller(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)
    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app._sparkle_updater = MagicMock()
    app._sparkle_updater.check_for_updates.return_value = True

    sender = object()
    app._check_for_updates(sender)

    app._sparkle_updater.check_for_updates.assert_called_once_with(sender)


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


def test_hotkey_listener_retries_after_accessibility_permission_granted(
    tmp_path, monkeypatch
):
    """Granting Accessibility permission should recover hotkeys without restarting."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    scheduled = []

    class FakeTimer:
        def __init__(self, callback):
            self.callback = callback
            self.invalidated = False

        def invalidate(self):
            self.invalidated = True

    class FakeRunLoop:
        def addTimer_forMode_(self, timer, mode):
            scheduled.append((timer, mode))

    monkeypatch.setattr(
        app_module.NSTimer,
        "timerWithTimeInterval_repeats_block_",
        lambda interval, repeats, callback: FakeTimer(callback),
        raising=False,
    )
    monkeypatch.setattr(
        app_module.NSRunLoop,
        "mainRunLoop",
        lambda: FakeRunLoop(),
        raising=False,
    )
    monkeypatch.setattr(app_module, "is_accessibility_trusted", lambda: True)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._hotkey_listener_ready = False
    app._hotkey_manager = MagicMock()
    app._hotkey_manager.start.return_value = True
    app._hotkey_permission_retry_timer = None
    app._refresh_environment_status = MagicMock()
    app._show_hotkeys_ready_notification = MagicMock()

    app._start_hotkey_permission_retry_timer()
    assert len(scheduled) == 1
    assert scheduled[0][0] is app._hotkey_permission_retry_timer

    timer, _mode = scheduled[0]
    timer.callback(timer)

    app._hotkey_manager.start.assert_called_once()
    assert app._hotkey_listener_ready is True
    assert timer.invalidated is True
    assert app._hotkey_permission_retry_timer is None
    app._refresh_environment_status.assert_called_once()
    app._show_hotkeys_ready_notification.assert_called_once()


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


def test_audio_level_uses_configured_waveform_calibration(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._capsule = MagicMock()

    app._on_audio_level(10 ** (-29.1 / 20))

    app._capsule.update_audio_level.assert_called_once_with(
        pytest.approx(30.9 / 54.0, abs=0.001)
    )


def test_hotkey_callbacks_enqueue_serial_commands(tmp_path, monkeypatch):
    """Hotkey entrypoints should enqueue work onto the shared command coordinator."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._command_coordinator = MagicMock()

    app._on_fn_pressed()
    app._on_fn_released()
    app._on_double_cmd()
    app._on_escape_pressed()
    app._on_capsule_cancel()
    app._on_capsule_finish()

    assert app._command_coordinator.submit.call_count == 6


def test_realtime_hotkey_quick_tap_latches_then_second_press_finishes(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config
    from vocal_more.domain.hotkey_gestures import HotkeyGestureController

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    mode = MagicMock(state=app_module.ModeState.IDLE)
    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._walkie_talkie = object()
    app._realtime_long = mode
    app._current_mode = mode
    app._capsule = MagicMock()
    app._hotkey_gesture_controller = HotkeyGestureController(
        hold_threshold=0.35
    )

    app._handle_fn_pressed_command(10.0)
    mode.state = app_module.ModeState.RECORDING
    app._handle_fn_released_command(10.1)

    assert mode.on_hotkey_pressed.call_count == 1
    mode.on_hotkey_released.assert_not_called()

    app._handle_fn_pressed_command(11.0)

    assert mode.on_hotkey_pressed.call_count == 2


def test_realtime_hotkey_hold_finishes_on_release(tmp_path, monkeypatch):
    from vocal_more.config import Config
    from vocal_more.domain.hotkey_gestures import HotkeyGestureController

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    mode = MagicMock(state=app_module.ModeState.IDLE)
    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._walkie_talkie = object()
    app._realtime_long = mode
    app._current_mode = mode
    app._capsule = MagicMock()
    app._hotkey_gesture_controller = HotkeyGestureController(
        hold_threshold=0.35
    )

    app._handle_fn_pressed_command(20.0)
    mode.state = app_module.ModeState.RECORDING
    app._handle_fn_released_command(20.6)

    assert mode.on_hotkey_pressed.call_count == 2
    mode.on_hotkey_released.assert_not_called()


def test_live_benchmark_marks_trigger_speech_end_partial_and_insert(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config
    from vocal_more.domain.hotkey_gestures import HotkeyGestureController

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )
    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    mode = MagicMock(state=app_module.ModeState.IDLE, name="Real-time Long")
    mode._recorder.benchmark_audio_delivery = "deterministic_wav_replay"
    trace = MagicMock()
    trace.active = False
    trace.begin.side_effect = lambda **_kwargs: setattr(trace, "active", True)
    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._walkie_talkie = object()
    app._realtime_long = mode
    app._current_mode = mode
    app._capsule = MagicMock()
    app._hotkey_gesture_controller = HotkeyGestureController(
        hold_threshold=0.35
    )
    app._benchmark_trace = trace
    app._run_on_main_thread = lambda callback: callback()
    app._show_result_notification = MagicMock()

    app._handle_fn_pressed_command(10.0)
    trace.begin.assert_called_once()
    assert (
        trace.begin.call_args.kwargs["metadata"]["audio_delivery"]
        == "deterministic_wav_replay"
    )
    mode.state = app_module.ModeState.RECORDING
    app._handle_fn_released_command(10.6)
    trace.mark.assert_any_call("speech_end", at=10.6)

    app._on_partial_result("private transcript")
    trace.mark.assert_any_call("first_partial")
    app._on_result("private transcript")

    trace.finish.assert_called_once_with(
        status="success",
        insert_completed=True,
    )


def test_live_benchmark_write_failure_does_not_block_result_delivery(
    tmp_path, monkeypatch
):
    """Opt-in benchmark telemetry must remain best-effort."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )
    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    trace = MagicMock(active=True)
    trace.finish.side_effect = OSError("trace directory is read-only")
    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._benchmark_trace = trace
    app._run_on_main_thread = lambda callback: callback()
    app._show_result_notification = MagicMock()

    app._on_result("result still delivered")

    app._show_result_notification.assert_called_once_with(
        "result still delivered"
    )


def test_escape_pressed_cancels_processing_mode(tmp_path, monkeypatch):
    """Escape should cancel a stuck processing session through the shared path."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._current_mode = MagicMock(state=app_module.ModeState.PROCESSING)

    app._handle_escape_pressed_command()

    app._current_mode.cancel.assert_called_once_with(reason="escape_cancel")


def test_escape_pressed_ignores_idle_mode(tmp_path, monkeypatch):
    """Escape should not cancel anything when the app is idle."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._current_mode = MagicMock(state=app_module.ModeState.IDLE)

    app._handle_escape_pressed_command()

    app._current_mode.cancel.assert_not_called()


def test_build_runtime_returns_shared_facade_for_menu_and_rpc(tmp_path, monkeypatch):
    """bootstrap.build_runtime should expose one facade object to both adapters."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)
    rpc_module = importlib.import_module("vocal_more.rpc_handler")
    rpc_module = importlib.reload(rpc_module)
    bootstrap_module = importlib.import_module("vocal_more.bootstrap")
    bootstrap_module = importlib.reload(bootstrap_module)

    with (
        patch.object(app_module, "TextPolisher", return_value=MagicMock()),
        patch.object(rpc_module, "TextPolisher", return_value=MagicMock()),
        patch.object(app_module, "FloatingCapsule", return_value=MagicMock()),
        patch.object(app_module, "SettingsWindow", return_value=MagicMock()),
        patch.object(app_module, "HotkeyManager", return_value=MagicMock()),
        patch.object(app_module, "RecordingStore", return_value=MagicMock()),
        patch.object(rpc_module, "RecordingStore", return_value=MagicMock()),
        patch.object(app_module, "run_environment_checks", return_value=[]),
        patch("vocal_more.modes.walkie_talkie.ASREngine", return_value=MagicMock()),
        patch("vocal_more.modes.walkie_talkie.AudioRecorder", return_value=MagicMock()),
        patch("vocal_more.modes.walkie_talkie.KeyboardSimulator", return_value=MagicMock()),
        patch("vocal_more.modes.realtime_long.ASREngine", return_value=MagicMock()),
        patch("vocal_more.modes.realtime_long.AudioRecorder", return_value=MagicMock()),
        patch("vocal_more.modes.realtime_long.KeyboardSimulator", return_value=MagicMock()),
    ):
        runtime = bootstrap_module.build_runtime(
            app_factory=app_module.VocalMoreApp,
            handler_factory=rpc_module.RPCHandler,
        )

    assert runtime.menu_bar is not None
    assert runtime.rpc_handler is not None
    assert runtime.menu_bar.runtime is runtime.rpc_handler.runtime


def test_main_does_not_enable_persistent_debug_dir_by_default(tmp_path, monkeypatch):
    """Normal app startup should not opt users into persistent ASR debug dumps."""
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )
    monkeypatch.delenv("VOCAL_MORE_DEBUG_DIR", raising=False)

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)
    bootstrap_module = importlib.import_module("vocal_more.bootstrap")
    bootstrap_module = importlib.reload(bootstrap_module)

    fake_app = MagicMock()
    monkeypatch.setattr(
        bootstrap_module,
        "build_menu_app",
        lambda app_factory=None: fake_app,
    )

    app_module.main()

    assert "VOCAL_MORE_DEBUG_DIR" not in app_module.os.environ
    assert not (tmp_path / "debug").exists()
    fake_app.run.assert_called_once()


def test_app_started_notification_uses_status_bar_message(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    notifications = []
    monkeypatch.setattr(
        app_module.rumps,
        "notification",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.apply_update("ui.language", "zh")

    app._show_app_started_notification()

    assert notifications == [
        (
            (
                "Vocal-More",
                "Vocal-More 已启动",
                "Vocal-More 已在状态栏运行。",
            ),
            {"icon": app._get_logo_path()},
        )
    ]


def test_run_shows_app_started_notification_before_entering_event_loop(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)

    run_calls = []
    monkeypatch.setattr(
        app_module.rumps.App,
        "run",
        lambda self: run_calls.append("run"),
        raising=False,
    )

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app.config.ui.onboarding_completed = True
    app._hotkey_manager = MagicMock()
    app._hotkey_manager.start.return_value = True
    app._refresh_environment_status = MagicMock()
    app._show_app_started_notification = MagicMock()

    app.run()

    app._hotkey_manager.start.assert_called_once_with()
    app._refresh_environment_status.assert_called_once_with(show_notification=True)
    app._show_app_started_notification.assert_called_once_with()
    assert run_calls == ["run"]


def test_run_opens_setup_automatically_for_a_fresh_install(tmp_path, monkeypatch):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)
    monkeypatch.setattr(app_module.rumps.App, "run", lambda self: None, raising=False)

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._hotkey_manager = MagicMock()
    app._hotkey_manager.start.return_value = True
    app._refresh_environment_status = MagicMock()
    app._show_app_started_notification = MagicMock()
    app._show_settings = MagicMock()

    app.run()

    app._show_settings.assert_called_once_with()


def test_dictionary_learning_success_notification_requires_real_automatic_change(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)
    notifications: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        app_module.rumps,
        "notification",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._settings_window = None

    app._handle_dictionary_learning_change(
        {
            "status": "applied",
            "source": "automatic",
            "dictionary_changed": False,
            "term": "阿里云百炼",
        }
    )
    app._handle_dictionary_learning_change(
        {
            "status": "applied",
            "source": "review",
            "dictionary_changed": True,
            "term": "阿里云百炼",
        }
    )
    app._handle_dictionary_learning_change(
        {
            "status": "applied",
            "source": "automatic",
            "dictionary_changed": True,
            "term": "阿里云百炼",
        }
    )

    assert len(notifications) == 1
    args, kwargs = notifications[0]
    assert args == (
        "Vocal-More",
        "已自动添加词条",
        "已添加“阿里云百炼”，可在词典设置中撤销。",
    )
    assert kwargs == {"icon": app._get_logo_path()}


def test_dictionary_learning_multiple_terms_emit_one_summary_notification(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config

    _install_rumps_stub(monkeypatch)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: tmp_path / "config.yaml"),
    )

    app_module = importlib.import_module("vocal_more.app")
    app_module = importlib.reload(app_module)
    notifications: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        app_module.rumps,
        "notification",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )

    app = app_module.VocalMoreApp.__new__(app_module.VocalMoreApp)
    app.config = Config()
    app._settings_window = None

    for term in ("FanUI", "Shadcn/ui"):
        app._handle_dictionary_learning_change(
            {
                "status": "applied",
                "source": "automatic",
                "dictionary_changed": True,
                "suppress_notification": True,
                "term": term,
            }
        )
    app._handle_dictionary_learning_change(
        {
            "status": "applied_group",
            "source": "automatic",
            "dictionary_changed": True,
            "terms": ["FanUI", "Shadcn/ui"],
        }
    )

    assert len(notifications) == 1
    args, kwargs = notifications[0]
    assert args == (
        "Vocal-More",
        "已自动添加 2 个词条",
        "已添加“FanUI”、“Shadcn/ui”，可在词典设置中分别撤销。",
    )
    assert kwargs == {"icon": app._get_logo_path()}
