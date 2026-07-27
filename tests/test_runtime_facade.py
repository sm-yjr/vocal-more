from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from vocal_more.modes.base_mode import ModeState


def _build_runtime_facade():
    from vocal_more.config import Config
    from vocal_more.application.runtime_facade import RuntimeFacade

    config = Config()

    walkie = SimpleNamespace(
        state=ModeState.IDLE,
        _recorder=MagicMock(),
        _asr=MagicMock(),
        text_polisher=None,
    )
    realtime = SimpleNamespace(
        state=ModeState.IDLE,
        _recorder=MagicMock(),
        _asr=MagicMock(),
        text_polisher=None,
    )
    current_mode = {"value": realtime}

    callbacks = {
        "refresh_text_polisher": MagicMock(),
        "set_active_hotkeys": MagicMock(),
        "set_custom_key": MagicMock(),
        "set_custom_keys": MagicMock(),
        "apply_interface_language": MagicMock(),
        "refresh_environment_status": MagicMock(),
        "refresh_dictionary_learning": MagicMock(),
    }

    facade = RuntimeFacade(
        config=config,
        modes={
            "walkie_talkie": walkie,
            "realtime_long": realtime,
        },
        get_current_mode=lambda: current_mode["value"],
        set_current_mode=lambda mode: current_mode.__setitem__("value", mode),
        on_refresh_text_polisher=callbacks["refresh_text_polisher"],
        on_set_active_hotkeys=callbacks["set_active_hotkeys"],
        on_set_custom_key=callbacks["set_custom_key"],
        on_set_custom_keys=callbacks["set_custom_keys"],
        on_apply_interface_language=callbacks["apply_interface_language"],
        on_refresh_environment_status=callbacks["refresh_environment_status"],
        on_refresh_dictionary_learning=callbacks["refresh_dictionary_learning"],
    )

    return facade, config, walkie, realtime, current_mode, callbacks


def test_runtime_facade_reports_only_effective_form_state_changes():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()

    result = facade.apply_form_state(
        {
            "audio": {"gain": 4.0},
            "hotkey": {"active_hotkeys": ["printscreen", "bogus"]},
        }
    )

    assert result.changed_keys == {"audio.gain"}
    assert config.audio.gain == 4.0
    assert config.hotkey.active_hotkeys == ["fn"]
    assert result.refresh_audio_recorders is True
    assert result.refresh_asr_runtime is False
    callbacks["set_active_hotkeys"].assert_not_called()
    walkie._recorder.set_gain.assert_called_once_with(4.0)
    realtime._recorder.set_gain.assert_called_once_with(4.0)


def test_runtime_facade_applies_multiple_custom_hotkeys_without_restart():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()
    keys = [
        {
            "key_code": 111,
            "display_name": "F12",
            "is_modifier": False,
            "flag_mask": 0,
        },
        {
            "key_code": 103,
            "display_name": "F11",
            "is_modifier": False,
            "flag_mask": 0,
        },
    ]

    facade.apply_update("hotkey.custom_keys", keys)

    assert config.hotkey.custom_keys == keys
    callbacks["set_custom_keys"].assert_called_once_with(keys)


def test_gain_form_sync_does_not_reset_existing_custom_hotkeys():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()
    keys = [
        {
            "key_code": 111,
            "display_name": "F12",
            "is_modifier": False,
            "flag_mask": 0,
        },
        {
            "key_code": 103,
            "display_name": "F11",
            "is_modifier": False,
            "flag_mask": 0,
        },
    ]
    facade.apply_update("hotkey.custom_keys", keys)
    callbacks["set_custom_key"].reset_mock()
    callbacks["set_custom_keys"].reset_mock()

    result = facade.apply_form_state(
        {
            "audio": {"gain": 4.0},
            "hotkey": {
                "active_hotkeys": ["fn"],
                "custom_key": {
                    **keys[0],
                    "key_code": 111.0,
                    "flag_mask": 0.0,
                },
                "custom_keys": [
                    {**keys[0], "key_code": 111.0, "flag_mask": 0.0},
                    {**keys[1], "key_code": 103.0, "flag_mask": 0.0},
                ],
            },
        }
    )

    assert result.changed_keys == {"audio.gain"}
    assert config.hotkey.custom_keys == keys
    callbacks["set_custom_key"].assert_not_called()
    callbacks["set_custom_keys"].assert_not_called()


def test_waveform_calibration_does_not_reconfigure_audio_recorders():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()

    result = facade.apply_update("audio.waveform_ceiling_dbfs", -12)

    assert result.changed_keys == {"audio.waveform_ceiling_dbfs"}
    assert result.refresh_audio_recorders is False
    assert result.refresh_environment_status is False
    assert config.audio.waveform_ceiling_dbfs == -12.0
    assert not walkie._recorder.method_calls
    assert not realtime._recorder.method_calls
    callbacks["refresh_environment_status"].assert_not_called()


def test_runtime_facade_switches_default_mode_only_when_idle():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()

    current_mode["value"] = realtime
    realtime.state = ModeState.RECORDING

    facade.apply_update("default_mode", "walkie_talkie")
    assert current_mode["value"] is realtime

    realtime.state = ModeState.IDLE
    result = facade.apply_update("default_mode", "walkie_talkie")

    assert result.changed_keys == {"default_mode"}
    assert current_mode["value"] is walkie


def test_runtime_facade_refreshes_asr_runtime_for_asr_and_llm_changes():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()

    result = facade.apply_form_state(
        {
            "api_key": "updated-key",
            "asr": {"model": "qwen3.5-omni-plus-realtime"},
            "llm": {"level": "strong"},
            "ui": {"language": "en"},
        }
    )

    assert result.refresh_text_polisher is True
    assert result.refresh_asr_runtime is True
    callbacks["refresh_text_polisher"].assert_called_once()
    callbacks["apply_interface_language"].assert_called_once()
    callbacks["refresh_environment_status"].assert_called_once()
    walkie._asr.refresh_runtime_config.assert_called_once_with(drop_idle_session=True)
    realtime._asr.refresh_runtime_config.assert_called_once_with(drop_idle_session=True)


def test_runtime_facade_wakes_dictionary_learning_for_privacy_or_key_changes():
    facade, config, walkie, realtime, current_mode, callbacks = _build_runtime_facade()

    facade.apply_update("dictionary_learning.enabled", True)
    facade.apply_update("api_key", "updated-key")

    assert callbacks["refresh_dictionary_learning"].call_count == 2
