from vocal_more.windows_trigger import (
    WINDOWS_TRIGGER_OPTIONS,
    current_trigger_browser_code,
    custom_key_config_for_browser_code,
)


def test_windows_trigger_options_include_function_and_modifier_choices():
    codes = {code for code, _label in WINDOWS_TRIGGER_OPTIONS}

    assert {"F8", "F9", "F12", "CapsLock", "ControlRight", "AltRight"} <= codes


def test_f8_uses_builtin_fn_compatibility_slot():
    assert custom_key_config_for_browser_code("F8") is None
    assert current_trigger_browser_code(["fn"], []) == "F8"


def test_custom_trigger_round_trips_through_shared_key_catalog():
    config = custom_key_config_for_browser_code("F9")

    assert config is not None
    assert current_trigger_browser_code([], [config]) == "F9"


def test_unknown_custom_trigger_falls_back_to_f8():
    assert custom_key_config_for_browser_code("F25") is None
    assert current_trigger_browser_code([], [{"key_code": 999}]) == "F8"
