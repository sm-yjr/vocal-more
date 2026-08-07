from vocal_more.windows_desktop_ui import (
    CapsuleSnapshot,
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
    assert payload["updates"]["audio.gain"] == 6.5
    assert payload["updates"]["audio.input_device"] == "USB Mic"
    assert payload["updates"]["llm.max_tokens"] == 1024


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
