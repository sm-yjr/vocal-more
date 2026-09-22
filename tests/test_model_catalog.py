"""Public dictation choices remain independent of legacy runtime lookup."""

from vocal_more.domain.model_catalog import (
    ASR_MODEL_CATALOG,
    asr_model_handles_inline_polish,
    asr_model_uses_single_pass,
    get_asr_model_info,
)


def test_dictation_choices_are_realtime_and_single_pass():
    ids = [model["id"] for model in ASR_MODEL_CATALOG]
    assert ids == [
        "qwen3.8-omni-flash-realtime",
        "qwen3.5-omni-plus-realtime",
        "qwen3.5-omni-flash-realtime",
        "qwen-audio-3.0-asr-flash-streaming",
        "qwen-audio-3.0-realtime-plus",
        "qwen-audio-3.0-realtime-flash",
    ]
    assert all(model["transport"] == "realtime_ws" for model in ASR_MODEL_CATALOG)
    assert all(asr_model_uses_single_pass(model_id) for model_id in ids)
    assert not asr_model_handles_inline_polish(ids[3])
    assert all(model["protocol"] for model in ASR_MODEL_CATALOG)


def test_hidden_models_remain_readable_for_saved_configs_and_recovery():
    for model_id in ("qwen3-asr-flash", "fun-asr-realtime", "qwen3.5-omni-plus"):
        assert get_asr_model_info(model_id) is not None
        assert model_id not in {model["id"] for model in ASR_MODEL_CATALOG}
    assert not asr_model_uses_single_pass("unknown-model")


def test_qwen38_realtime_routes_to_offline_sibling_for_long_audio():
    """The realtime model's offline fallback is derived by suffix removal."""
    from vocal_more.infrastructure.asr.routing import omni_offline_fallback_model

    assert omni_offline_fallback_model("qwen3.8-omni-flash-realtime") == "qwen3.8-omni-flash"
    assert get_asr_model_info("qwen3.8-omni-flash")["transport"] == "omni_offline"


def test_qwen38_exposes_verified_multimodal_context_capabilities():
    model = get_asr_model_info("qwen3.8-omni-flash-realtime")

    assert model["always_request_response"] is True
    assert model["supports_screen_context"] is True
    assert model["language_count"] == 74
    assert model["chinese_dialect_count"] == 39
    assert model["max_input_tokens"] == 196_608
    assert model["max_audio_turns"] == 100
    assert model["max_audio_seconds"] == 600
    assert model["rollover_audio_seconds"] == 540
