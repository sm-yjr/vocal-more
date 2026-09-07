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
        "qwen-audio-3.0-asr-flash-streaming",
        "qwen3.5-omni-plus-realtime",
        "qwen3.5-omni-flash-realtime",
        "qwen-audio-3.0-realtime-plus",
        "qwen-audio-3.0-realtime-flash",
    ]
    assert all(model["transport"] == "realtime_ws" for model in ASR_MODEL_CATALOG)
    assert all(asr_model_uses_single_pass(model_id) for model_id in ids)
    assert not asr_model_handles_inline_polish(ids[0])
    assert all(model["protocol"] for model in ASR_MODEL_CATALOG)


def test_hidden_models_remain_readable_for_saved_configs_and_recovery():
    for model_id in ("qwen3-asr-flash", "fun-asr-realtime", "qwen3.5-omni-plus"):
        assert get_asr_model_info(model_id) is not None
        assert model_id not in {model["id"] for model in ASR_MODEL_CATALOG}
    assert not asr_model_uses_single_pass("unknown-model")
