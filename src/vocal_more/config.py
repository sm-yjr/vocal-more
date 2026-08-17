"""Configuration management compatibility facade for Vocal-More."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

from .domain.config_models import (
    ASRBackend,
    ASRConfig,
    ASRLanguage,
    AppConfig,
    AudioConfig,
    DictionaryLearningConfig,
    HOTKEY_ALIASES,
    HotkeyConfig,
    LEGACY_BUILT_IN_HOTKEYS,
    LLMConfig,
    UIConfig,
    VALID_DEFAULT_MODES,
    VALID_HOTKEYS,
    VALID_LINUX_ACCELERATORS,
    _parse_asr_language,
    _parse_asr_model,
    _parse_hotkeys,
    _parse_linux_accelerator,
    _parse_level,
    _parse_llm_model,
    _parse_persona,
    _parse_tone,
    _validate_custom_key,
    asr_model_handles_inline_polish,
    get_asr_model_info,
    get_llm_model_info,
)
from .domain.model_catalog import ASR_MODEL_CATALOG, LLM_MODEL_CATALOG
from .paths import default_app_paths


class Config(AppConfig):
    """Legacy config API with persistence and singleton behavior."""

    @classmethod
    def get_config_dir(cls) -> Path:
        return default_app_paths().base_dir

    @classmethod
    def get_data_dir(cls) -> Path:
        """Return the XDG data root (or the legacy root on other platforms)."""
        return default_app_paths().data_dir

    @classmethod
    def get_state_dir(cls) -> Path:
        """Return the XDG state/log root (or the legacy root on other platforms)."""
        return default_app_paths().state_dir

    @classmethod
    def get_config_path(cls) -> Path:
        return cls.get_config_dir() / "config.yaml"

    @classmethod
    def load(cls) -> "Config":
        from .infrastructure.config_repository import ConfigRepository
        from .paths import ensure_legacy_linux_migration

        ensure_legacy_linux_migration()

        config = ConfigRepository(
            base_dir=cls.get_config_dir(),
            config_path=cls.get_config_path(),
            config_cls=cls,
        ).load()

        env_api_key = os.environ.get("DASHSCOPE_API_KEY")
        if env_api_key:
            config.api_key = env_api_key
        return config

    @classmethod
    def _write_config_data(cls, data: dict[str, Any]) -> None:
        cls.get_config_dir().mkdir(parents=True, exist_ok=True)
        with open(cls.get_config_path(), "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def save(self) -> None:
        from .infrastructure.config_repository import ConfigRepository

        ConfigRepository(
            base_dir=self.get_config_dir(),
            config_path=self.get_config_path(),
            config_cls=type(self),
        ).save(self)

    def ensure_api_key(self) -> Optional[str]:
        return super().ensure_api_key(self.get_config_path())


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reload_config() -> Config:
    global _config
    _config = Config.load()
    return _config


__all__ = [
    "ASRBackend",
    "ASRConfig",
    "ASRLanguage",
    "ASR_MODEL_CATALOG",
    "AudioConfig",
    "DictionaryLearningConfig",
    "Config",
    "HOTKEY_ALIASES",
    "HotkeyConfig",
    "LEGACY_BUILT_IN_HOTKEYS",
    "LLMConfig",
    "LLM_MODEL_CATALOG",
    "UIConfig",
    "VALID_DEFAULT_MODES",
    "VALID_HOTKEYS",
    "VALID_LINUX_ACCELERATORS",
    "_parse_asr_language",
    "_parse_asr_model",
    "_parse_hotkeys",
    "_parse_linux_accelerator",
    "_parse_level",
    "_parse_llm_model",
    "_parse_persona",
    "_parse_tone",
    "_validate_custom_key",
    "asr_model_handles_inline_polish",
    "get_asr_model_info",
    "get_config",
    "get_llm_model_info",
    "reload_config",
]
