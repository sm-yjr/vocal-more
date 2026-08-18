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
    assert config.audio.capture_channels == 1
    assert config.audio.blocksize == 640
    assert config.audio.input_device is None
    assert config.audio.gain_mode == "automatic"
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
    assert config.llm.polish_mode == "dictation"
    assert config.hotkey.primary_key == "fn"
    assert config.hotkey.active_hotkeys == ["fn"]
    assert config.ui.language == "zh"
    assert config.default_mode == "realtime_long"


def test_legacy_default_audio_blocksize_migrates_to_low_latency_default(tmp_path, monkeypatch):
    """A persisted historical default should not override the new default."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    config_path.write_text("audio:\n  blocksize: 1600\n", encoding="utf-8")

    config = Config.load()

    assert config.audio.blocksize == 640


def test_legacy_audio_gain_migrates_to_manual_without_changing_saved_gain(
    tmp_path,
    monkeypatch,
):
    """Upgrades must not silently stack Apple AGC onto an existing gain choice."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    config_path.write_text("audio:\n  gain: 8.0\n", encoding="utf-8")

    config = Config.load()

    assert config.audio.gain_mode == "manual"
    assert config.audio.gain == 8.0


def test_config_load_uses_in_memory_migration_when_repair_write_fails(
    tmp_path,
    monkeypatch,
):
    from vocal_more.config import Config
    import vocal_more.infrastructure.compatibility_repair as repair_module

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    config_path.write_text("audio:\n  gain: 8.0\n", encoding="utf-8")

    def refuse_write(*_args, **_kwargs):
        raise PermissionError("read only")

    monkeypatch.setattr(
        repair_module,
        "_write_yaml_data",
        refuse_write,
    )

    config = Config.load()

    assert config.audio.gain_mode == "manual"
    assert config.audio.gain == 8.0


def test_explicit_automatic_gain_mode_survives_config_roundtrip(tmp_path, monkeypatch):
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    config_path.write_text(
        "audio:\n  gain_mode: automatic\n  gain: 8.0\n",
        encoding="utf-8",
    )

    config = Config.load()

    assert config.audio.gain_mode == "automatic"
    assert config.to_dict()["audio"]["gain_mode"] == "automatic"


def test_invalid_gain_mode_normalizes_to_safe_manual_control():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict({"audio": {"gain_mode": "adaptive-ish"}})

    assert config.audio.gain_mode == "manual"


def test_legacy_stereo_setting_becomes_capture_topology_not_output_topology():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict(
        {"audio": {"channels": 2, "gain_mode": "manual"}}
    )

    assert config.audio.channels == 1
    assert config.audio.capture_channels == 2
    assert config.to_dict()["audio"]["channels"] == 1
    assert config.to_dict()["audio"]["capture_channels"] == 2


def test_capture_topology_allows_three_logical_channels_for_explicit_experiments():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict(
        {"audio": {"capture_channels": 99, "gain_mode": "manual"}}
    )

    assert config.audio.channels == 1
    assert config.audio.capture_channels == 3


def test_config_get_config_dir(monkeypatch):
    """Test getting config directory."""
    from vocal_more.config import Config
    from vocal_more.paths import default_data_dir

    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: default_data_dir()),
    )

    config_dir = Config.get_config_dir()
    if os.name == "nt":
        roaming = os.environ.get("APPDATA")
        expected = (
            Path(roaming) / "Vocal More"
            if roaming
            else Path.home() / "AppData" / "Roaming" / "Vocal More"
        )
    else:
        expected = Path.home() / ".vocal-more"
    assert config_dir == expected


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
    assert config.llm.polish_mode == "dictation"
    assert config.llm.structured is False
    assert config.llm.prompt_overrides == {}
    assert config.hotkey.active_hotkeys == ["fn"]
    assert config.ui.language == "zh"
    assert config.ui.onboarding_completed is False
    assert config.ui.advanced_settings is False
    assert config.dictionary_learning.enabled is False
    assert config.dictionary_learning.excluded_bundle_ids == []


def test_existing_config_migrates_without_interrupting_established_users():
    """Legacy users should keep the expert UI and should not be re-onboarded."""
    from vocal_more.config import Config

    config = Config.from_dict(
        {
            "api_key": "existing-key",
            "ui": {"language": "zh"},
        }
    )

    assert config.ui.onboarding_completed is True
    assert config.ui.advanced_settings is True


def test_productization_ui_flags_round_trip():
    from vocal_more.config import Config

    config = Config()
    config.apply_update("ui.onboarding_completed", True)
    config.apply_update("ui.advanced_settings", True)

    restored = Config.from_dict(config.to_dict())

    assert restored.ui.onboarding_completed is True
    assert restored.ui.advanced_settings is True


def test_waveform_ceiling_dbfs_is_configurable_and_bounded():
    from vocal_more.config import Config

    config = Config()
    assert config.audio.waveform_ceiling_dbfs == -6.0

    config.apply_update("audio.waveform_ceiling_dbfs", -18)
    assert Config.from_dict(config.to_dict()).audio.waveform_ceiling_dbfs == -18.0

    config.apply_update("audio.waveform_ceiling_dbfs", -999)
    assert config.audio.waveform_ceiling_dbfs == -30.0

    config.apply_update("audio.waveform_ceiling_dbfs", 999)
    assert config.audio.waveform_ceiling_dbfs == 0.0


def test_meeting_is_valid_default_mode():
    from vocal_more.config import Config

    config = Config()
    config.apply_update("default_mode", "meeting")

    assert config.default_mode == "meeting"


def test_config_repository_round_trips_app_config(tmp_path):
    """The repository should persist the pure app config without the legacy facade."""
    from vocal_more.domain.config_models import AppConfig
    from vocal_more.infrastructure.config_repository import ConfigRepository

    repo = ConfigRepository(base_dir=tmp_path)
    config = AppConfig()
    config.apply_update("audio.gain", 4.0)
    config.apply_update("asr.model", "qwen3.5-omni-plus")
    config.apply_update("llm.structured", True)
    config.apply_update("llm.polish_mode", "prompt")
    config.apply_update(
        "llm.prompt_overrides",
        {
            "tone": {"enabled": True, "prompt": "保持温暖但简洁"},
            "unknown": {"enabled": True, "prompt": "ignored"},
        },
    )
    config.apply_update("hotkey.active_hotkeys", ["fn"])
    config.apply_update("dictionary_learning.enabled", True)
    config.apply_update(
        "dictionary_learning.excluded_bundle_ids",
        ["com.1password.1password", " com.apple.Terminal "],
    )

    repo.save(config)
    loaded = repo.load()

    assert loaded == config


def test_dictionary_learning_config_sanitizes_exclusions():
    from vocal_more.config import Config

    config = Config()
    config.apply_form_state(
        {
            "dictionary_learning": {
                "enabled": "true",
                "excluded_bundle_ids": [
                    " com.1password.1password ",
                    "",
                    "com.1password.1password",
                    42,
                ],
            }
        }
    )

    assert config.dictionary_learning.enabled is True
    assert config.dictionary_learning.excluded_bundle_ids == [
        "com.1password.1password"
    ]


def test_context_personalization_config_defaults_to_private_app_categories():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict({})

    assert config.context_personalization.enabled is True
    assert config.context_personalization.excluded_bundle_ids == []
    assert config.to_dict()["context_personalization"] == {
        "enabled": True,
        "excluded_bundle_ids": [],
    }


def test_context_personalization_config_sanitizes_exclusions():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict(
        {
            "context_personalization": {
                "enabled": "false",
                "excluded_bundle_ids": [
                    " com.example.private ",
                    "com.example.private",
                    42,
                ],
            }
        }
    )

    assert config.context_personalization.enabled is False
    assert config.context_personalization.excluded_bundle_ids == [
        "com.example.private"
    ]


def test_custom_polish_prompt_config_is_sanitized():
    from vocal_more.config import Config

    config = Config()
    config.apply_update(
        "llm.prompt_overrides",
        {
            "level": {"enabled": "true", "prompt": "保留所有技术细节"},
            "persona": {"enabled": False, "prompt": "草稿"},
            "unknown": {"enabled": True, "prompt": "discarded"},
            "tone": {"enabled": True, "prompt": 42},
        },
    )

    assert config.llm.prompt_overrides == {
        "level": {"enabled": True, "prompt": "保留所有技术细节"},
        "persona": {"enabled": False, "prompt": "草稿"},
    }


def test_config_repository_backs_up_unreadable_yaml_before_fallback(tmp_path):
    """Unreadable config should be preserved before the repository falls back."""
    from vocal_more.infrastructure.config_repository import ConfigRepository

    repo = ConfigRepository(base_dir=tmp_path)
    repo.config_path.write_text(
        "hotkey:\n  custom_key: !totally-broken value\n",
        encoding="utf-8",
    )

    loaded = repo.load()
    backups = sorted(tmp_path.glob("config.yaml.*.bak"))

    assert loaded.to_dict()["api_key"] == ""
    assert [path.name for path in backups] == ["config.yaml.config-load-failed.bak"]


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
    config.apply_update("llm.polish_mode", "prompt")
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
    assert loaded.llm.polish_mode == "prompt"
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
            "llm": {
                "polish_mode": "prompt",
            },
        }
    )

    assert config.default_mode == "realtime_long"
    assert config.llm.polish_mode == "prompt"
    assert config.audio.input_device == "Built-in Mic"
    assert config.audio.gain == 4.0
    assert config.audio.highpass_filter is False
    assert config.audio.highpass_freq == 420
    assert config.audio.soft_limiter is False
    assert config.asr.model == "qwen3.5-omni-plus"
    assert config.asr.backend == "omni_offline"
    assert config.asr.language == "en"
    assert config.hotkey.active_hotkeys == ["fn"]
    assert config.ui.language == "zh"


def test_config_boolean_strings_parse_as_booleans():
    """String booleans from WebView/RPC payloads should not rely on bool(str)."""
    from vocal_more.config import Config

    config = Config()

    config.apply_update("enable_polish", "false")
    config.apply_update("auto_paste", "0")
    config.apply_update("audio.highpass_filter", "no")
    config.apply_update("audio.soft_limiter", "off")
    config.apply_update("asr.use_dictionary_corpus", "false")
    config.apply_update("llm.structured", "yes")
    config.apply_update("llm.enable_thinking", "1")

    assert config.enable_polish is False
    assert config.auto_paste is False
    assert config.audio.highpass_filter is False
    assert config.audio.soft_limiter is False
    assert config.asr.use_dictionary_corpus is False
    assert config.llm.structured is True
    assert config.llm.enable_thinking is True


def test_config_numeric_fields_are_clamped_to_supported_ranges():
    """Malformed numeric payloads should be normalized before reaching runtime code."""
    from vocal_more.config import Config

    config = Config()

    config.apply_update("audio.gain", -999)
    assert round(config.audio.gain, 6) == round(10 ** (-6 / 20), 6)
    config.apply_update("audio.gain", 999)
    assert config.audio.gain == 50.0

    config.apply_update("audio.highpass_freq", -1)
    assert config.audio.highpass_freq == 50
    config.apply_update("audio.highpass_freq", 999999)
    assert config.audio.highpass_freq == 500

    config.apply_update("llm.temperature", -2)
    assert config.llm.temperature == 0.0
    config.apply_update("llm.temperature", 99)
    assert config.llm.temperature == 1.0

    config.apply_update("hotkey.double_tap_threshold", -5)
    assert config.hotkey.double_tap_threshold == 0.15
    config.apply_update("hotkey.double_tap_threshold", 9)
    assert config.hotkey.double_tap_threshold == 0.5

    config.apply_update("audio.sample_rate", 0)
    assert config.audio.sample_rate == 16000
    config.apply_update("audio.channels", 99)
    assert config.audio.channels == 1
    config.apply_update("audio.blocksize", 0)
    assert config.audio.blocksize == 128
    config.apply_update("llm.max_tokens", -1)
    assert config.llm.max_tokens == 1


def test_legacy_sample_rate_is_normalized_to_the_fixed_pcm_contract():
    from vocal_more.config import Config

    config = Config.from_dict({"audio": {"sample_rate": 48000}})

    assert config.audio.sample_rate == 16000


def test_load_auto_cleans_legacy_noise_gate_field(tmp_path, monkeypatch):
    """Old noise_gate config should disappear as soon as the config is loaded."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {"audio": {"gain": 4.0, "noise_gate": 0.12, "highpass_freq": 320}},
            f,
        )

    loaded = Config.load()
    assert loaded.audio.gain == 4.0
    assert loaded.audio.highpass_freq == 320
    assert "noise_gate" not in loaded.to_dict()["audio"]

    persisted = yaml.safe_load(config_path.read_text())
    assert "noise_gate" not in persisted["audio"]


def test_load_auto_cleans_unknown_and_normalized_fields(tmp_path, monkeypatch):
    """Loading should normalize the config file in place and drop stale keys."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "legacy_mode": True,
                "audio": {"gain": 4.0, "noise_gate": 0.15},
                "asr": {"model": "qwen3.5-omni-plus", "backend": "realtime_ws"},
                "hotkey": {"active_hotkeys": ["printscreen", "bogus"]},
            },
            f,
            allow_unicode=True,
        )

    loaded = Config.load()
    persisted = yaml.safe_load(config_path.read_text())

    assert loaded.asr.backend == "omni_offline"
    assert loaded.hotkey.active_hotkeys == ["fn"]
    assert "legacy_mode" not in persisted
    assert "noise_gate" not in persisted["audio"]
    assert persisted["asr"]["backend"] == "omni_offline"
    assert persisted["hotkey"]["active_hotkeys"] == ["fn"]
    assert persisted["api_key"] == ""


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
    }
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.asr.backend == "realtime_ws"
    assert loaded.asr.batch_mode == "manual"


def test_invalid_polish_mode_falls_back_and_rewrites_yaml(tmp_path, monkeypatch):
    """Invalid llm.polish_mode should be normalized on load."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump({"llm": {"polish_mode": "always", "level": "strong"}}, f)

    loaded = Config.load()
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert loaded.llm.level == "strong"
    assert loaded.llm.polish_mode == "dictation"
    assert loaded.to_dict()["llm"]["polish_mode"] == "dictation"
    assert persisted["llm"]["polish_mode"] == "dictation"


def test_load_handles_legacy_python_string_tags_and_rewrites_clean_yaml(
    tmp_path, monkeypatch
):
    """Legacy PyYAML python tags should load safely and be rewritten as plain YAML."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config_path.write_text(
        "\n".join(
            [
                "hotkey:",
                "  custom_key:",
                "    key_code: 105",
                "    display_name: !!python/object/apply:builtins.str ['F13']",
                "    is_modifier: false",
                "    flag_mask: 0",
                "  active_hotkeys:",
                "  - fn",
            ]
        ),
        encoding="utf-8",
    )

    loaded = Config.load()
    persisted = config_path.read_text(encoding="utf-8")

    assert loaded.hotkey.custom_key == {
        "key_code": 105,
        "display_name": "F13",
        "is_modifier": False,
        "flag_mask": 0,
    }
    assert "!!python/object/apply:builtins.str" not in persisted


def test_unreadable_config_is_backed_up_before_fallback_save(tmp_path, monkeypatch):
    """A load failure should preserve the raw config before later saves overwrite it."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    backup_path = tmp_path / "config.yaml.config-load-failed.bak"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    broken_text = "hotkey:\n  custom_key: !totally-broken value\n"
    config_path.write_text(broken_text, encoding="utf-8")

    loaded = Config.load()

    assert loaded.hotkey.active_hotkeys == ["fn"]
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == broken_text

    loaded.apply_update("ui.language", "zh")
    loaded.save()

    assert backup_path.read_text(encoding="utf-8") == broken_text
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["ui"]["language"] == "zh"


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


def test_config_active_hotkeys_can_be_empty(tmp_path, monkeypatch):
    """Users may disable all built-in hotkeys when using a custom key."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"active_hotkeys": []}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == []


def test_config_active_hotkeys_all_invalid_becomes_empty(tmp_path, monkeypatch):
    """All-invalid built-in hotkeys should not silently re-enable Fn."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"active_hotkeys": ["bogus"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == []


def test_config_legacy_hotkey_alias_migrates_to_fn(tmp_path, monkeypatch):
    """Old built-in hotkey aliases should migrate to Fn."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"active_hotkeys": ["printscreen", "double_cmd"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["fn"]


def test_config_legacy_command_hotkeys_migrate_to_fn(tmp_path, monkeypatch):
    """Old Command hotkeys should migrate to Fn."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    data = {"hotkey": {"active_hotkeys": ["right_cmd", "double_cmd"]}}
    with open(config_path, "w") as f:
        yaml.dump(data, f)

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["fn"]


def test_parse_llm_model_valid():
    """Test _parse_llm_model accepts valid catalog model IDs."""
    from vocal_more.config import _parse_llm_model

    assert _parse_llm_model("qwen3.7-plus") == "qwen3.7-plus"
    assert _parse_llm_model("qwen3.7-flash") == "qwen3.7-flash"
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
    assert _parse_asr_model("qwen-audio-3.0-asr-flash-streaming") == "qwen-audio-3.0-asr-flash-streaming"


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

    for model_id, display_name in (
        ("qwen3.7-plus", "Qwen 3.7 Plus"),
        ("qwen3.7-flash", "Qwen 3.7 Flash"),
    ):
        info = get_llm_model_info(model_id)
        assert info is not None
        assert info["display_name"] == display_name
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

    info3 = get_asr_model_info("qwen-audio-3.0-asr-flash-streaming")
    assert info3 is not None
    assert info3["transport"] == "realtime_ws"
    assert info3["protocol"] == "audio_recognition"
    assert info3["fallback_model"] == "qwen3-asr-flash"
    assert info3["handles_inline_polish"] is False

    assert get_asr_model_info("nonexistent") is None


def test_custom_key_defaults_to_none():
    """Test custom_key defaults to None."""
    from vocal_more.config import Config

    config = Config()
    assert config.hotkey.custom_key is None
    assert config.hotkey.custom_keys == []


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
    assert loaded.hotkey.custom_keys == [loaded.hotkey.custom_key]


def test_multiple_custom_hotkeys_are_validated_deduplicated_and_bounded():
    from vocal_more.config import Config

    config = Config()
    f12 = {
        "key_code": 111,
        "display_name": "F12",
        "is_modifier": False,
        "flag_mask": 0,
    }
    right_control = {
        "key_code": 62,
        "display_name": "Right Control",
        "is_modifier": True,
        "flag_mask": 0x40000,
    }

    config.apply_update(
        "hotkey.custom_keys",
        [
            f12,
            f12,
            {"key_code": 999, "is_modifier": False, "flag_mask": 0},
            right_control,
        ],
    )

    assert config.hotkey.custom_keys == [f12, right_control]
    assert config.hotkey.custom_key == f12
    assert Config.from_dict(config.to_dict()).hotkey.custom_keys == [
        f12,
        right_control,
    ]


def test_multiple_custom_hotkeys_accept_integral_webview_numbers(
    tmp_path,
    monkeypatch,
):
    """WKWebView may bridge JavaScript key codes as integer-valued floats."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: config_path),
    )

    config = Config()
    config.apply_update(
        "hotkey.custom_keys",
        [
            {
                "key_code": 111.0,
                "display_name": "F12",
                "is_modifier": False,
                "flag_mask": 0.0,
            },
            {
                "key_code": 103.0,
                "display_name": "F11",
                "is_modifier": False,
                "flag_mask": 0.0,
            },
        ],
    )

    expected = [
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
    assert config.hotkey.custom_keys == expected

    config.save()

    assert Config.load().hotkey.custom_keys == expected


def test_custom_keys_take_precedence_over_legacy_field_regardless_of_yaml_order():
    from vocal_more.config import Config

    f12 = {
        "key_code": 111,
        "display_name": "F12",
        "is_modifier": False,
        "flag_mask": 0,
    }
    f11 = {
        "key_code": 103,
        "display_name": "F11",
        "is_modifier": False,
        "flag_mask": 0,
    }

    loaded = Config.from_dict(
        {
            "hotkey": {
                "custom_keys": [f12, f11],
                "custom_key": f12,
            }
        }
    )

    assert loaded.hotkey.custom_keys == [f12, f11]


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


def test_config_legacy_fkey_hotkeys_migrate_to_fn(tmp_path, monkeypatch):
    """Old F-key built-ins should migrate to Fn on load."""
    from vocal_more.config import Config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    config = Config()
    config.hotkey.active_hotkeys = ["f13", "f16"]
    config.save()

    loaded = Config.load()
    assert loaded.hotkey.active_hotkeys == ["fn"]


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
