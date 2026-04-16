"""Test configuration module."""

import os
from pathlib import Path

import pytest
import yaml

# Set a test API key for tests that don't actually call the API
os.environ.setdefault("DASHSCOPE_API_KEY", "test-api-key")


def test_config_load(tmp_path, monkeypatch):
    """Test loading default configuration."""
    from vocal_more.config import Config

    # Use a non-existent config path so defaults are used
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config.load()

    assert config.audio.sample_rate == 16000
    assert config.audio.channels == 1
    assert config.audio.blocksize == 1600
    assert config.audio.input_device is None
    assert config.asr.backend == "realtime_ws"
    assert config.asr.model == "qwen3.5-omni-flash-realtime"
    assert config.asr.language == "auto"
    assert config.asr.batch_mode == "manual"
    assert config.asr.use_dictionary_corpus is True
    assert config.asr.extra_corpus_terms == []
    assert config.llm.model == "qwen3.5-plus"
    assert config.llm.temperature == 0.0
    assert config.llm.enable_thinking is False
    assert config.llm.max_tokens == 1024
    assert config.llm.polish_mode == "smart"
    assert config.hotkey.primary_key == "fn"
    assert config.hotkey.active_hotkeys == ["fn"]
    assert config.ui.language == "zh"
    assert config.default_mode == "realtime_long"


def test_config_get_config_dir():
    """Test getting config directory."""
    from vocal_more.config import Config

    config_dir = Config.get_config_dir()
    assert config_dir == Path.home() / ".vocal-more"


def test_config_ensure_api_key_with_env():
    """Test API key from environment variable."""
    os.environ["DASHSCOPE_API_KEY"] = "test-key"

    from vocal_more.config import reload_config

    config = reload_config()

    assert config.api_key == "test-key"
    assert config.ensure_api_key() is None


def test_config_ensure_api_key_missing():
    """Test missing API key error."""
    # Temporarily remove the API key
    old_key = os.environ.pop("DASHSCOPE_API_KEY", None)

    try:
        from vocal_more.config import Config

        config = Config()  # Fresh config without loading
        error = config.ensure_api_key()

        assert error is not None
        assert "API key" in error
    finally:
        if old_key:
            os.environ["DASHSCOPE_API_KEY"] = old_key


def test_config_new_fields_defaults():
    """Test default values for new config fields."""
    from vocal_more.config import Config

    config = Config()
    assert config.audio.input_device is None
    assert config.asr.backend == "realtime_ws"
    assert config.asr.language == "auto"
    assert config.asr.batch_mode == "manual"
    assert config.asr.use_dictionary_corpus is True
    assert config.asr.extra_corpus_terms == []
    assert config.llm.enable_thinking is False
    assert config.llm.max_tokens == 1024
    assert config.llm.polish_mode == "smart"
    assert config.llm.structured is False
    assert config.hotkey.active_hotkeys == ["fn"]
    assert config.ui.language == "zh"


def test_config_new_fields_roundtrip(tmp_path, monkeypatch):
    """Test YAML serialization/deserialization of new fields."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    # Set custom values
    config = Config()
    config.audio.input_device = "MacBook Pro Microphone"
    config.apply_update("asr.backend", "short_file")
    config.asr.extra_corpus_terms = ["Vocal More", "DashScope"]
    config.llm.enable_thinking = True
    config.llm.max_tokens = 128
    config.llm.polish_mode = "always"
    config.hotkey.active_hotkeys = ["fn"]
    config.ui.language = "zh"
    config.save()

    # Reload and verify
    loaded = Config.load()
    assert loaded.audio.input_device == "MacBook Pro Microphone"
    assert loaded.asr.backend == "short_file"
    assert loaded.asr.model == "qwen3-asr-flash"
    assert loaded.asr.extra_corpus_terms == ["Vocal More", "DashScope"]
    assert loaded.llm.enable_thinking is True
    assert loaded.llm.max_tokens == 128
    assert loaded.llm.polish_mode == "always"
    assert loaded.hotkey.active_hotkeys == ["fn"]
    assert loaded.ui.language == "zh"


def test_audio_processing_fields_roundtrip(tmp_path, monkeypatch):
    """Audio processing toggles should survive save/load."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.audio.highpass_filter = False
    config.audio.highpass_freq = 350
    config.audio.soft_limiter = False
    config.save()

    loaded = Config.load()
    assert loaded.audio.highpass_filter is False
    assert loaded.audio.highpass_freq == 350
    assert loaded.audio.soft_limiter is False


def test_omni_offline_backend_roundtrip(tmp_path, monkeypatch):
    """The derived omni_offline backend should survive persistence."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.apply_update("asr.model", "qwen3.5-omni-plus")
    config.save()

    loaded = Config.load()
    assert loaded.asr.model == "qwen3.5-omni-plus"
    assert loaded.asr.backend == "omni_offline"


def test_config_model_wins_over_stale_backend(tmp_path, monkeypatch):
    """When both are present, model transport should be the final backend."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {"asr": {"model": "qwen3.5-omni-plus", "backend": "realtime_ws"}},
            f,
        )

    loaded = Config.load()
    assert loaded.asr.model == "qwen3.5-omni-plus"
    assert loaded.asr.backend == "omni_offline"


def test_apply_form_state_normalizes_nested_config():
    """Form-state application should normalize backend, hotkeys, and audio fields."""
    from vocal_more.config import Config

    config = Config()
    config.apply_form_state(
        {
            "default_mode": "realtime_long",
            "audio": {
                "input_device": "Built-in Mic",
                "gain": 4.0,
                "noise_gate": 0.12,
                "highpass_filter": False,
                "highpass_freq": 420,
                "soft_limiter": False,
            },
            "asr": {
                "backend": "realtime_ws",
                "model": "qwen3.5-omni-plus",
                "language": "en",
            },
            "hotkey": {
                "active_hotkeys": ["printscreen", "bogus"],
            },
            "ui": {
                "language": "zh",
            },
        }
    )

    assert config.default_mode == "realtime_long"
    assert config.audio.input_device == "Built-in Mic"
    assert config.audio.gain == 4.0
    assert config.audio.noise_gate == 0.12
    assert config.audio.highpass_filter is False
    assert config.audio.highpass_freq == 420
    assert config.audio.soft_limiter is False
    assert config.asr.model == "qwen3.5-omni-plus"
    assert config.asr.backend == "omni_offline"
    assert config.asr.language == "en"
    assert config.hotkey.active_hotkeys == ["f13"]
    assert config.ui.language == "zh"


def test_asr_language_mixed_aliases_normalize_to_auto():
    """Mixed-language aliases should map to automatic language detection."""
    from vocal_more.config import Config

    config = Config()

    for raw in ("auto", "mixed", "zh_en", "zh-en", "bilingual"):
        config.apply_update("asr.language", raw)
        assert config.asr.language == "auto"


def test_ui_language_normalizes_to_supported_values():
    """UI language should accept only the supported language codes."""
    from vocal_more.config import Config

    config = Config()

    config.apply_update("ui.language", "zh")
    assert config.ui.language == "zh"

    config.apply_update("ui.language", "en")
    assert config.ui.language == "en"

    config.apply_update("ui.language", "bogus")
    assert config.ui.language == "en"


def test_config_invalid_modes_fall_back(tmp_path, monkeypatch):
    """Test invalid mode-like fields fall back to safe defaults."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {
        "asr": {"backend": "bogus", "batch_mode": "auto"},
        "llm": {"polish_mode": "fancy"},
    }
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.asr.backend == "realtime_ws"
    assert loaded.asr.batch_mode == "manual"
    assert loaded.llm.polish_mode == "smart"


def test_config_active_hotkeys_filters_invalid(tmp_path, monkeypatch):
    """Test that invalid hotkey values are filtered out during load."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    # Write config with invalid hotkey
    data = {"hotkey": {"active_hotkeys": ["fn", "invalid_key"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["fn"]


def test_config_active_hotkeys_empty_fallback(tmp_path, monkeypatch):
    """Test that empty active_hotkeys list falls back to default."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    # Write config with all-invalid hotkeys (would result in empty list)
    data = {"hotkey": {"active_hotkeys": ["bogus"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["fn"]


def test_config_hotkey_alias_normalization(tmp_path, monkeypatch):
    """Test that 'printscreen' alias is normalized to 'f13'."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"active_hotkeys": ["printscreen", "double_cmd"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["f13", "double_cmd"]


def test_config_accepts_right_command_hotkey(tmp_path, monkeypatch):
    """Built-in right Command hotkey should survive config parsing."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"active_hotkeys": ["right_cmd", "double_cmd"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["right_cmd", "double_cmd"]


def test_parse_llm_model_valid():
    """Test _parse_llm_model accepts valid catalog model IDs."""
    from vocal_more.config import _parse_llm_model

    assert _parse_llm_model("qwen3.6-plus") == "qwen3.6-plus"
    assert _parse_llm_model("qwen3.5-plus") == "qwen3.5-plus"


def test_parse_llm_model_unknown_falls_back():
    """Test _parse_llm_model falls back to default for unknown IDs."""
    from vocal_more.config import _parse_llm_model

    assert _parse_llm_model("nonexistent") == "qwen3.5-plus"
    assert _parse_llm_model("") == "qwen3.5-plus"


def test_parse_llm_model_alias_preserved():
    """Test qwen-plus alias maps to qwen3.5-plus."""
    from vocal_more.config import _parse_llm_model

    assert _parse_llm_model("qwen-plus") == "qwen3.5-plus"


def test_parse_asr_model_valid():
    """Test _parse_asr_model accepts valid catalog model IDs."""
    from vocal_more.config import _parse_asr_model

    assert _parse_asr_model("qwen3.5-omni-plus-realtime") == "qwen3.5-omni-plus-realtime"
    assert _parse_asr_model("qwen3-asr-flash") == "qwen3-asr-flash"
    assert _parse_asr_model("qwen3-asr-flash-realtime-2026-02-10") == "qwen3-asr-flash-realtime-2026-02-10"


def test_parse_asr_model_unknown_falls_back():
    """Test _parse_asr_model falls back to default for unknown IDs."""
    from vocal_more.config import _parse_asr_model

    assert _parse_asr_model("bogus") == "qwen3.5-omni-flash-realtime"
    assert _parse_asr_model("") == "qwen3.5-omni-flash-realtime"


def test_get_llm_model_info():
    """Test get_llm_model_info returns correct entries or None."""
    from vocal_more.config import get_llm_model_info

    info = get_llm_model_info("qwen3.5-plus")
    assert info is not None
    assert info["display_name"] == "Qwen 3.5 Plus"
    assert info["api"] == "multimodal_conversation"
    assert info["supports_thinking"] is True

    assert get_llm_model_info("nonexistent") is None


def test_get_asr_model_info():
    """Test get_asr_model_info returns correct entries or None."""
    from vocal_more.config import get_asr_model_info

    info = get_asr_model_info("qwen3-asr-flash")
    assert info is not None
    assert info["display_name"] == "Legacy"
    assert info["transport"] == "short_file"
    assert info["supports_transcription_params"] is False

    info2 = get_asr_model_info("qwen3.5-omni-plus-realtime")
    assert info2 is not None
    assert info2["input_audio_transcription_model"] == "gummy-realtime-v1"
    assert info2["handles_inline_polish"] is True

    assert get_asr_model_info("nonexistent") is None


def test_custom_key_defaults_to_none():
    """Test custom_key defaults to None."""
    from vocal_more.config import Config

    config = Config()
    assert config.hotkey.custom_key is None


def test_custom_key_roundtrip(tmp_path, monkeypatch):
    """Test custom_key round-trip through YAML save/load."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.hotkey.custom_key = {
        "key_code": 49,
        "display_name": "Space",
        "is_modifier": False,
        "flag_mask": 0,
    }
    config.save()

    loaded = Config.load()
    assert loaded.hotkey.custom_key is not None
    assert loaded.hotkey.custom_key["key_code"] == 49
    assert loaded.hotkey.custom_key["display_name"] == "Space"
    assert loaded.hotkey.custom_key["is_modifier"] is False
    assert loaded.hotkey.custom_key["flag_mask"] == 0


def test_custom_key_invalid_falls_back_to_none(tmp_path, monkeypatch):
    """Test invalid custom_key dict falls back to None."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    # Write config with invalid custom_key (missing fields)
    data = {"hotkey": {"custom_key": {"key_code": 49}}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.custom_key is None


def test_custom_key_none_roundtrip(tmp_path, monkeypatch):
    """Test that custom_key=None round-trips correctly."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.hotkey.custom_key = None
    config.save()

    loaded = Config.load()
    assert loaded.hotkey.custom_key is None


def test_custom_key_wrong_type_falls_back(tmp_path, monkeypatch):
    """Test that non-dict custom_key values fall back to None."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"custom_key": "not_a_dict"}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.custom_key is None


def test_config_fkey_hotkeys_roundtrip(tmp_path, monkeypatch):
    """Test F-key hotkeys survive save/load cycle."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.hotkey.active_hotkeys = ["f13", "f16"]
    config.save()

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["f13", "f16"]


def test_structured_defaults_to_false():
    """Test structured defaults to False."""
    from vocal_more.config import Config

    config = Config()
    assert config.llm.structured is False


def test_structured_roundtrip(tmp_path, monkeypatch):
    """Test structured flag round-trips through YAML save/load."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.llm.structured = True
    config.save()

    loaded = Config.load()
    assert loaded.llm.structured is True


def test_structured_level_migrates_to_balanced(tmp_path, monkeypatch):
    """Legacy level='structured' should migrate to level='balanced'."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"llm": {"level": "structured"}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.llm.level == "balanced"
