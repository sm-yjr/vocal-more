"""Full-key enumeration tests for customizable macOS hotkeys."""

from vocal_more.config import Config
from vocal_more.core import hotkey_manager as hotkey_module
from vocal_more.domain.hotkey_catalog import (
    CUSTOM_HOTKEY_KEYS,
    CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE,
)


def test_custom_hotkey_catalog_has_unique_physical_keys_and_browser_codes():
    key_codes = [definition.key_code for definition in CUSTOM_HOTKEY_KEYS]
    browser_codes = [
        definition.browser_code
        for definition in CUSTOM_HOTKEY_KEYS
        if definition.browser_code
    ]

    assert len(CUSTOM_HOTKEY_KEYS) >= 100
    assert len(key_codes) == len(set(key_codes))
    assert len(browser_codes) == len(set(browser_codes))
    assert len(CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE) == len(browser_codes)


def test_every_catalog_key_is_accepted_by_config_validation():
    config = Config()

    for definition in CUSTOM_HOTKEY_KEYS:
        config.apply_update("hotkey.custom_key", definition.to_config())

        assert config.hotkey.custom_key == definition.to_config()


def test_every_catalog_key_is_registered_by_hotkey_manager(monkeypatch):
    config = Config()
    monkeypatch.setattr(hotkey_module, "get_config", lambda: config)

    manager = hotkey_module.HotkeyManager()

    for definition in CUSTOM_HOTKEY_KEYS:
        manager.set_custom_key(definition.to_config())

        if definition.is_modifier:
            assert manager._modifier_lookup[definition.key_code] == definition.flag_mask
        else:
            assert definition.key_code in manager._regular_lookup

    manager.stop()


def test_unknown_custom_key_is_rejected_by_config_validation():
    config = Config()

    config.apply_update(
        "hotkey.custom_key",
        {
            "key_code": 999,
            "display_name": "Unknown",
            "is_modifier": False,
            "flag_mask": 0,
        },
    )

    assert config.hotkey.custom_key is None
