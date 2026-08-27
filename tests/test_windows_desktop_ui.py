from unittest.mock import MagicMock

from vocal_more.windows_desktop_ui import (
    CapsuleSnapshot,
    SettingsSnapshot,
    _SettingsWindow,
    build_settings_payload,
    capsule_primary_text,
    capsule_secondary_text,
    catalog_options,
    device_options,
)


def test_catalog_options_omits_separators():
    assert catalog_options(
        [
            {"id": "model-a", "display_name": "Model A"},
            {"separator": True, "display_name": "---"},
            {"id": "model-b"},
        ]
    ) == [("model-a", "Model A"), ("model-b", "model-b")]


def test_device_options_include_default_and_deduplicate():
    options = device_options(
        [
            {"name": "USB Mic", "is_default": True},
            {"name": "USB Mic", "is_default": False},
            {"name": "Array", "is_default": False},
        ]
    )

    assert options == [
        (None, "System default"),
        ("USB Mic", "USB Mic (Default)"),
        ("Array", "Array"),
    ]


def test_capsule_uses_processing_stage_without_transcript_content():
    snapshot = CapsuleSnapshot(
        state="processing",
        mode="realtime_long",
        language="en",
        stage="Polishing",
        trigger_label="F8",
    )

    assert capsule_primary_text(snapshot) == "Polishing"
    assert capsule_secondary_text(snapshot) == "Long Dictation · F8 · Esc to cancel"


def test_build_settings_payload_normalizes_runtime_keys():
    payload = build_settings_payload(
        {
            "api_key": "  sk-test  ",
            "ui.language": "zh",
            "default_mode": "realtime_long",
            "trigger_browser_code": "F9",
            "auto_paste": True,
            "restore_clipboard": True,
            "streaming_paste": False,
            "enable_polish": True,
            "asr.model": "asr-a",
            "asr.language": "auto",
            "asr.use_dictionary_corpus": True,
            "llm.model": "llm-a",
            "llm.enable_thinking": False,
            "llm.level": "minimal",
            "llm.persona": "technical",
            "llm.tone": "neutral",
            "llm.polish_mode": "dictation",
            "llm.output_language": "en",
            "llm.temperature": "0.2",
            "llm.max_tokens": "1024",
            "audio.input_device": "USB Mic",
            "audio.gain_mode": "manual",
            "audio.gain": "6.5",
            "audio.highpass_filter": True,
            "audio.highpass_freq": "180",
            "audio.soft_limiter": True,
            "audio.waveform_ceiling_dbfs": "-8",
            "context_personalization.enabled": True,
        }
    )

    assert payload["trigger_browser_code"] == "F9"
    assert payload["updates"]["api_key"] == "sk-test"
    assert payload["updates"]["restore_clipboard"] is True
    assert payload["updates"]["streaming_paste"] is False
    assert payload["updates"]["audio.gain"] == 6.5
    assert payload["updates"]["audio.input_device"] == "USB Mic"
    assert payload["updates"]["llm.max_tokens"] == 1024
    assert payload["updates"]["llm.output_language"] == "en"


def test_build_settings_payload_defaults_output_language_to_auto():
    payload = build_settings_payload(
        {
            "ui.language": "zh",
            "default_mode": "realtime_long",
            "asr.language": "auto",
            "audio.gain_mode": "manual",
            "audio.gain": "6.5",
            "audio.highpass_freq": "200",
            "audio.waveform_ceiling_dbfs": "-6",
            "llm.level": "minimal",
            "llm.persona": "default",
            "llm.tone": "neutral",
            "llm.polish_mode": "dictation",
            "llm.temperature": "0",
            "llm.max_tokens": "1024",
        }
    )

    assert payload["updates"]["llm.output_language"] == "auto"


def test_build_settings_payload_rejects_invalid_output_language():
    values = {
        "ui.language": "zh",
        "default_mode": "realtime_long",
        "asr.language": "auto",
        "audio.gain_mode": "manual",
        "audio.gain": "6.5",
        "audio.highpass_freq": "200",
        "audio.waveform_ceiling_dbfs": "-6",
        "llm.level": "minimal",
        "llm.persona": "default",
        "llm.tone": "neutral",
        "llm.polish_mode": "dictation",
        "llm.output_language": "fr",
        "llm.temperature": "0",
        "llm.max_tokens": "1024",
    }

    try:
        build_settings_payload(values)
    except ValueError as exc:
        assert "output language" in str(exc)
    else:
        raise AssertionError("invalid output language should fail")


def test_build_settings_payload_rejects_out_of_range_gain():
    values = {
        "ui.language": "en",
        "default_mode": "meeting",
        "asr.language": "en",
        "audio.gain_mode": "manual",
        "audio.gain": "100",
        "audio.highpass_freq": "200",
        "audio.waveform_ceiling_dbfs": "-6",
        "llm.level": "balanced",
        "llm.persona": "default",
        "llm.tone": "neutral",
        "llm.polish_mode": "dictation",
        "llm.temperature": "0",
        "llm.max_tokens": "1024",
    }

    try:
        build_settings_payload(values)
    except ValueError as exc:
        assert "Software gain" in str(exc)
    else:
        raise AssertionError("invalid gain should fail")


class _FakeVar:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


_POPULATE_VAR_NAMES = (
    "api_key",
    "ui.language",
    "default_mode",
    "trigger_browser_code",
    "auto_paste",
    "restore_clipboard",
    "streaming_paste",
    "enable_polish",
    "asr.model",
    "asr.language",
    "asr.use_dictionary_corpus",
    "llm.model",
    "llm.enable_thinking",
    "llm.level",
    "llm.persona",
    "llm.tone",
    "llm.polish_mode",
    "llm.output_language",
    "llm.temperature",
    "llm.max_tokens",
    "audio.input_device",
    "audio.gain_mode",
    "audio.gain",
    "audio.highpass_filter",
    "audio.highpass_freq",
    "audio.soft_limiter",
    "audio.waveform_ceiling_dbfs",
    "context_personalization.enabled",
)


def _make_headless_settings_window() -> _SettingsWindow:
    window = _SettingsWindow.__new__(_SettingsWindow)
    window._vars = {name: _FakeVar() for name in _POPULATE_VAR_NAMES}
    window._version_var = MagicMock()
    window._data_var = MagicMock()
    window._config_var = MagicMock()
    window._log_var = MagicMock()
    window._status_var = MagicMock()
    window._save_button = MagicMock()
    window._trigger_combo = MagicMock()
    window._asr_combo = MagicMock()
    window._llm_combo = MagicMock()
    window._set_catalog_combo = MagicMock()
    window._set_devices = MagicMock()
    window._toggle_api_visibility = MagicMock()
    return window


def test_populate_backfills_paste_flags_from_config():
    window = _make_headless_settings_window()
    snapshot = SettingsSnapshot(
        version="0.9.0",
        config={
            "auto_paste": False,
            "restore_clipboard": False,
            "streaming_paste": True,
        },
        trigger_options=(("F8", "F8"),),
    )

    window._populate(snapshot)

    assert window._vars["auto_paste"].value is False
    assert window._vars["restore_clipboard"].value is False
    assert window._vars["streaming_paste"].value is True


def test_populate_defaults_paste_flags_when_config_keys_missing():
    window = _make_headless_settings_window()
    snapshot = SettingsSnapshot(
        version="0.9.0",
        config={},
        trigger_options=(("F8", "F8"),),
    )

    window._populate(snapshot)

    assert window._vars["auto_paste"].value is True
    assert window._vars["restore_clipboard"].value is True
    assert window._vars["streaming_paste"].value is False

def test_prompt_capsule_uses_local_coach_hint():
    snapshot = CapsuleSnapshot(
        state="recording",
        mode="prompt",
        language="zh",
        stage="可补充交付形式或验收标准",
        trigger_label="F8",
    )

    assert capsule_primary_text(snapshot) == "正在聆听"
    assert capsule_secondary_text(snapshot) == (
        "可补充交付形式或验收标准 · Esc 取消"
    )
