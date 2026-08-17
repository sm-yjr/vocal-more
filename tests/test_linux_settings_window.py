from __future__ import annotations

import pytest

from vocal_more.linux_settings_window import build_linux_settings_updates


def _values():
    return {
        "api_key": " key ",
        "ui.language": "en",
        "default_mode": "realtime_long",
        "hotkey.linux_accelerator": "F10",
        "auto_paste": True,
        "enable_polish": True,
        "asr.model": "qwen3-asr-flash",
        "llm.model": "qwen3.5-plus",
        "llm.level": "minimal",
        "audio.input_device": "USB Mic",
        "audio.gain": 8,
        "audio.highpass_filter": True,
        "audio.highpass_freq": 180,
        "audio.soft_limiter": True,
    }


def test_linux_settings_payload_is_normalized():
    updates = build_linux_settings_updates(_values())

    assert updates["api_key"] == "key"
    assert updates["hotkey.linux_accelerator"] == "F10"
    assert updates["audio.gain_mode"] == "manual"
    assert updates["audio.input_device"] == "USB Mic"


@pytest.mark.parametrize("trigger", ["F7", "ControlRight", "Super+Space"])
def test_linux_settings_rejects_unsupported_trigger(trigger):
    values = _values()
    values["hotkey.linux_accelerator"] = trigger

    with pytest.raises(ValueError, match="F8-F12"):
        build_linux_settings_updates(values)
