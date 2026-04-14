"""Tests for runtime hotkey lookup configuration."""

from vocal_more.config import Config
from vocal_more.core import hotkey_manager as hotkey_module


def test_custom_regular_key_is_active(monkeypatch):
    """Custom regular keys should be added to the regular-key lookup."""
    config = Config()
    config.hotkey.custom_key = {
        "key_code": 49,
        "display_name": "Space",
        "is_modifier": False,
        "flag_mask": 0,
    }
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert 49 in manager._regular_lookup


def test_custom_modifier_key_is_active(monkeypatch):
    """Custom modifier keys should be added to the modifier lookup."""
    config = Config()
    config.hotkey.custom_key = {
        "key_code": 59,
        "display_name": "Control",
        "is_modifier": True,
        "flag_mask": 0x4_0000,
    }
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert manager._modifier_lookup[59] == 0x4_0000


def test_set_custom_key_updates_lookup_tables(monkeypatch):
    """Changing custom keys at runtime should refresh active lookups."""
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()
    assert 49 not in manager._regular_lookup

    manager.set_custom_key(
        {
            "key_code": 49,
            "display_name": "Space",
            "is_modifier": False,
            "flag_mask": 0,
        }
    )

    assert 49 in manager._regular_lookup

    manager.set_custom_key(None)

    assert 49 not in manager._regular_lookup


def test_right_command_hotkey_is_registered_as_modifier(monkeypatch):
    """Built-in right Command should be treated as a modifier hotkey."""
    config = Config()
    config.hotkey.active_hotkeys = ["right_cmd"]
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    assert manager._modifier_lookup[hotkey_module.CMD_RIGHT_KEYCODE] == hotkey_module.NX_COMMANDMASK
