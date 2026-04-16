"""Tests for settings window message routing."""

from vocal_more.ui.settings_window import SettingsWindow


def test_sync_form_state_message_dispatches_callback():
    """syncFormState messages should reach the registered callback immediately."""
    captured = {}

    window = SettingsWindow.__new__(SettingsWindow)
    window._on_set_config = None
    window._on_set_asr_model = None
    window._on_sync_form_state = lambda payload: captured.update(payload)
    window._on_set_device = None
    window._on_set_active_hotkeys = None
    window._on_add_dict_entry = None
    window._on_remove_dict_entry = None
    window._on_refresh_devices = None
    window._on_open_config_file = None
    window._on_open_dict_file = None
    window._on_open_external = None

    window._on_js_message(
        {
            "action": "syncFormState",
            "state": {"asr": {"model": "qwen3-asr-flash"}, "enable_polish": True},
        }
    )

    assert captured == {"asr": {"model": "qwen3-asr-flash"}, "enable_polish": True}


def test_refresh_devices_message_dispatches_callback():
    """refreshDevices messages should trigger the device refresh callback."""
    called = {"value": False}

    window = SettingsWindow.__new__(SettingsWindow)
    window._on_set_config = None
    window._on_set_asr_model = None
    window._on_sync_form_state = None
    window._on_set_device = None
    window._on_set_active_hotkeys = None
    window._on_add_dict_entry = None
    window._on_remove_dict_entry = None
    window._on_refresh_devices = lambda: called.__setitem__("value", True)
    window._on_open_config_file = None
    window._on_open_dict_file = None
    window._on_open_external = None

    window._on_js_message({"action": "refreshDevices"})

    assert called["value"] is True
