"""Pure configuration models and normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from ..localization import UILanguage, normalize_ui_language
from .config_parsing import (
    MAX_AUDIO_BLOCKSIZE,
    MAX_AUDIO_CHANNELS,
    MAX_AUDIO_GAIN,
    MAX_AUDIO_SAMPLE_RATE,
    MAX_DOUBLE_TAP_THRESHOLD,
    MAX_HIGHPASS_FREQ,
    MAX_LLM_MAX_TOKENS,
    MAX_LLM_TEMPERATURE,
    MIN_AUDIO_BLOCKSIZE,
    MIN_AUDIO_CHANNELS,
    MIN_AUDIO_GAIN,
    MIN_AUDIO_SAMPLE_RATE,
    MIN_DOUBLE_TAP_THRESHOLD,
    MIN_HIGHPASS_FREQ,
    MIN_LLM_MAX_TOKENS,
    MIN_LLM_TEMPERATURE,
    clamp_float,
    clamp_int,
    parse_bool,
)
from .model_catalog import (
    ASRBackend,
    ASR_MODEL_IDS,
    LLM_MODEL_IDS,
    asr_model_handles_inline_polish,
    default_asr_model_for_backend,
    get_asr_model_info,
    get_llm_model_info,
)


VALID_HOTKEYS = (
    "fn",
    "right_cmd",
    "double_cmd",
    "f13",
    "f14",
    "f15",
    "f16",
    "f17",
    "f18",
    "f19",
    "f20",
)
VALID_DEFAULT_MODES = ("walkie_talkie", "realtime_long")
ASRLanguage = Literal["zh", "en", "auto"]
HOTKEY_ALIASES = {
    "printscreen": "f13",
    "print_screen": "f13",
}
_CUSTOM_KEY_REQUIRED_FIELDS = {"key_code", "display_name", "is_modifier", "flag_mask"}


@dataclass
class AudioConfig:
    """Audio recording configuration."""

    sample_rate: int = 16000
    channels: int = 1
    blocksize: int = 1600
    input_device: Optional[str] = None
    gain: float = 2.0
    highpass_filter: bool = True
    highpass_freq: int = 200
    soft_limiter: bool = True


@dataclass
class ASRConfig:
    """ASR configuration."""

    backend: ASRBackend = "realtime_ws"
    model: str = "qwen3.5-omni-flash-realtime"
    language: ASRLanguage = "auto"
    batch_mode: Literal["manual"] = "manual"
    use_dictionary_corpus: bool = True
    extra_corpus_terms: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    """LLM configuration for text polishing."""

    model: str = "qwen3.5-plus"
    temperature: float = 0.0
    enable_thinking: bool = False
    max_tokens: int = 1024
    level: Literal["minimal", "balanced", "strong"] = "minimal"
    structured: bool = False
    tone: Literal["neutral", "gentle", "direct"] = "neutral"
    persona: Literal["default", "technical", "bilingual", "professional", "chat"] = "default"


@dataclass
class HotkeyConfig:
    """Hotkey configuration."""

    primary_key: str = "fn"
    fallback_key: str = "double_cmd"
    double_tap_threshold: float = 0.3
    active_hotkeys: list[str] = field(default_factory=lambda: ["fn"])
    custom_key: Optional[dict] = None


@dataclass
class UIConfig:
    """UI configuration."""

    language: UILanguage = "zh"


def _validate_custom_key(raw: object) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    if not _CUSTOM_KEY_REQUIRED_FIELDS.issubset(raw.keys()):
        return None
    if not isinstance(raw["key_code"], int):
        return None
    if not isinstance(raw["display_name"], str):
        return None
    if not isinstance(raw["is_modifier"], bool):
        return None
    if not isinstance(raw["flag_mask"], int):
        return None
    return {
        "key_code": raw["key_code"],
        "display_name": raw["display_name"],
        "is_modifier": raw["is_modifier"],
        "flag_mask": raw["flag_mask"],
    }


def _parse_hotkeys(raw: list) -> list[str]:
    seen = set()
    result = []
    for hotkey in raw:
        canonical = HOTKEY_ALIASES.get(hotkey, hotkey)
        if canonical in VALID_HOTKEYS and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result or ["fn"]


def _parse_asr_backend(raw: str) -> ASRBackend:
    if raw in ("realtime_ws", "short_file", "omni_offline"):
        return raw
    return "realtime_ws"


def _parse_batch_mode(raw: str) -> Literal["manual"]:
    if raw == "manual":
        return raw
    return "manual"


def _parse_asr_language(raw: object) -> ASRLanguage:
    if not isinstance(raw, str):
        return "auto"

    normalized = raw.strip().lower()
    if normalized in ("zh", "en", "auto"):
        return normalized
    if normalized in ("mixed", "mix", "zh_en", "zh-en", "bilingual"):
        return "auto"
    return "auto"


def _parse_llm_model(raw: str) -> str:
    if raw == "qwen-plus":
        return "qwen3.5-plus"
    if raw and raw in LLM_MODEL_IDS:
        return raw
    return "qwen3.5-plus"


def _parse_asr_model(raw: str) -> str:
    if raw and raw in ASR_MODEL_IDS:
        return raw
    return "qwen3.5-omni-flash-realtime"


def _parse_level(raw: str) -> Literal["minimal", "balanced", "strong"]:
    if raw in ("minimal", "balanced", "strong"):
        return raw
    if raw == "structured":
        return "balanced"
    return "minimal"


def _parse_tone(raw: str) -> Literal["neutral", "gentle", "direct"]:
    if raw in ("neutral", "gentle", "direct"):
        return raw
    return "neutral"


def _parse_persona(raw: str) -> Literal["default", "technical", "bilingual", "professional", "chat"]:
    if raw in ("default", "technical", "bilingual", "professional", "chat"):
        return raw
    return "default"


def _parse_default_mode(raw: object) -> str:
    if isinstance(raw, str) and raw in VALID_DEFAULT_MODES:
        return raw
    return "realtime_long"


def _parse_extra_corpus_terms(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _parse_ui_language(raw: object) -> UILanguage:
    return normalize_ui_language(raw)


@dataclass
class AppConfig:
    """Main application configuration."""

    api_key: str = ""
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    enable_polish: bool = True
    auto_paste: bool = True
    default_mode: str = "realtime_long"

    @classmethod
    def _from_dict(cls, data: dict) -> "AppConfig":
        if not isinstance(data, dict):
            return cls()

        config = cls()

        for key in ("api_key", "enable_polish", "auto_paste", "default_mode"):
            if key in data:
                config.apply_update(key, data[key])

        for section in ("audio", "asr", "llm", "hotkey", "ui"):
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            for field_name, value in cls._iter_section_items(section, section_data):
                try:
                    config.apply_update(f"{section}.{field_name}", value)
                except ValueError:
                    continue

        return config

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        return cls._from_dict(data)

    def apply_update(self, key: str, value: Any) -> None:
        parts = key.split(".")
        if len(parts) == 1:
            self._apply_top_level_update(parts[0], value)
            return
        if len(parts) == 2:
            self._apply_section_update(parts[0], parts[1], value)
            return
        raise ValueError(f"Invalid config key: {key}")

    def apply_form_state(self, form_state: dict[str, Any]) -> None:
        if not isinstance(form_state, dict):
            raise ValueError("Form state must be a dict")

        for key in ("api_key", "default_mode", "auto_paste", "enable_polish"):
            if key in form_state:
                self.apply_update(key, form_state[key])

        for section in ("audio", "asr", "llm", "hotkey", "ui"):
            section_data = form_state.get(section)
            if not isinstance(section_data, dict):
                continue
            for field_name, value in self._iter_section_items(section, section_data):
                self.apply_update(f"{section}.{field_name}", value)

    @staticmethod
    def _iter_section_items(section: str, section_data: dict[str, Any]):
        prioritized_fields: list[str] = []
        if section == "asr":
            prioritized_fields = ["backend", "model"]

        seen = set()
        for field_name in prioritized_fields:
            if field_name in section_data:
                seen.add(field_name)
                yield field_name, section_data[field_name]

        for field_name, value in section_data.items():
            if field_name in seen:
                continue
            yield field_name, value

    def _apply_top_level_update(self, field_name: str, value: Any) -> None:
        if field_name == "api_key":
            self.api_key = str(value or "")
        elif field_name == "enable_polish":
            self.enable_polish = parse_bool(value, self.enable_polish)
        elif field_name == "auto_paste":
            self.auto_paste = parse_bool(value, self.auto_paste)
        elif field_name == "default_mode":
            self.default_mode = _parse_default_mode(value)
        else:
            raise ValueError(f"Unknown config key: {field_name}")

    def _apply_section_update(self, section: str, field_name: str, value: Any) -> None:
        if section == "audio":
            self._apply_audio_update(field_name, value)
        elif section == "asr":
            self._apply_asr_update(field_name, value)
        elif section == "llm":
            self._apply_llm_update(field_name, value)
        elif section == "hotkey":
            self._apply_hotkey_update(field_name, value)
        elif section == "ui":
            self._apply_ui_update(field_name, value)
        else:
            raise ValueError(f"Unknown config section: {section}")

    def _apply_audio_update(self, field_name: str, value: Any) -> None:
        if field_name == "sample_rate":
            self.audio.sample_rate = clamp_int(
                value,
                default=self.audio.sample_rate,
                minimum=MIN_AUDIO_SAMPLE_RATE,
                maximum=MAX_AUDIO_SAMPLE_RATE,
            )
        elif field_name == "channels":
            self.audio.channels = clamp_int(
                value,
                default=self.audio.channels,
                minimum=MIN_AUDIO_CHANNELS,
                maximum=MAX_AUDIO_CHANNELS,
            )
        elif field_name == "blocksize":
            self.audio.blocksize = clamp_int(
                value,
                default=self.audio.blocksize,
                minimum=MIN_AUDIO_BLOCKSIZE,
                maximum=MAX_AUDIO_BLOCKSIZE,
            )
        elif field_name == "input_device":
            device = str(value).strip() if value else ""
            self.audio.input_device = device or None
        elif field_name == "gain":
            self.audio.gain = clamp_float(
                value,
                default=self.audio.gain,
                minimum=MIN_AUDIO_GAIN,
                maximum=MAX_AUDIO_GAIN,
            )
        elif field_name == "highpass_filter":
            self.audio.highpass_filter = parse_bool(value, self.audio.highpass_filter)
        elif field_name == "highpass_freq":
            self.audio.highpass_freq = clamp_int(
                value,
                default=self.audio.highpass_freq,
                minimum=MIN_HIGHPASS_FREQ,
                maximum=MAX_HIGHPASS_FREQ,
            )
        elif field_name == "soft_limiter":
            self.audio.soft_limiter = parse_bool(value, self.audio.soft_limiter)
        else:
            raise ValueError(f"Unknown config key: audio.{field_name}")

    def _apply_asr_update(self, field_name: str, value: Any) -> None:
        if field_name == "backend":
            self.asr.backend = _parse_asr_backend(str(value))
            model_info = get_asr_model_info(self.asr.model)
            if not model_info or model_info.get("transport") != self.asr.backend:
                self.asr.model = default_asr_model_for_backend(self.asr.backend)
        elif field_name == "model":
            self.asr.model = _parse_asr_model(str(value))
            model_info = get_asr_model_info(self.asr.model)
            if model_info:
                self.asr.backend = model_info["transport"]
        elif field_name == "language":
            self.asr.language = _parse_asr_language(value)
        elif field_name == "batch_mode":
            self.asr.batch_mode = _parse_batch_mode(str(value))
        elif field_name == "use_dictionary_corpus":
            self.asr.use_dictionary_corpus = parse_bool(
                value,
                self.asr.use_dictionary_corpus,
            )
        elif field_name == "extra_corpus_terms":
            self.asr.extra_corpus_terms = _parse_extra_corpus_terms(value)
        else:
            raise ValueError(f"Unknown config key: asr.{field_name}")

    def _apply_llm_update(self, field_name: str, value: Any) -> None:
        if field_name == "model":
            self.llm.model = _parse_llm_model(str(value))
        elif field_name == "temperature":
            self.llm.temperature = clamp_float(
                value,
                default=self.llm.temperature,
                minimum=MIN_LLM_TEMPERATURE,
                maximum=MAX_LLM_TEMPERATURE,
            )
        elif field_name == "enable_thinking":
            self.llm.enable_thinking = parse_bool(value, self.llm.enable_thinking)
        elif field_name == "max_tokens":
            self.llm.max_tokens = clamp_int(
                value,
                default=self.llm.max_tokens,
                minimum=MIN_LLM_MAX_TOKENS,
                maximum=MAX_LLM_MAX_TOKENS,
            )
        elif field_name == "polish_mode":
            return
        elif field_name == "level":
            self.llm.level = _parse_level(str(value))
        elif field_name == "structured":
            self.llm.structured = parse_bool(value, self.llm.structured)
        elif field_name == "tone":
            self.llm.tone = _parse_tone(str(value))
        elif field_name == "persona":
            self.llm.persona = _parse_persona(str(value))
        else:
            raise ValueError(f"Unknown config key: llm.{field_name}")

        model_info = get_llm_model_info(self.llm.model)
        if model_info and not model_info.get("supports_thinking"):
            self.llm.enable_thinking = False

    def _apply_hotkey_update(self, field_name: str, value: Any) -> None:
        if field_name == "primary_key":
            self.hotkey.primary_key = str(value)
        elif field_name == "fallback_key":
            self.hotkey.fallback_key = str(value)
        elif field_name == "double_tap_threshold":
            self.hotkey.double_tap_threshold = clamp_float(
                value,
                default=self.hotkey.double_tap_threshold,
                minimum=MIN_DOUBLE_TAP_THRESHOLD,
                maximum=MAX_DOUBLE_TAP_THRESHOLD,
            )
        elif field_name == "active_hotkeys":
            self.hotkey.active_hotkeys = _parse_hotkeys(value if isinstance(value, list) else [])
        elif field_name == "custom_key":
            self.hotkey.custom_key = _validate_custom_key(value)
        else:
            raise ValueError(f"Unknown config key: hotkey.{field_name}")

    def _apply_ui_update(self, field_name: str, value: Any) -> None:
        if field_name == "language":
            self.ui.language = _parse_ui_language(value)
        else:
            raise ValueError(f"Unknown config key: ui.{field_name}")

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "audio": {
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
                "blocksize": self.audio.blocksize,
                "input_device": self.audio.input_device,
                "gain": self.audio.gain,
                "highpass_filter": self.audio.highpass_filter,
                "highpass_freq": self.audio.highpass_freq,
                "soft_limiter": self.audio.soft_limiter,
            },
            "asr": {
                "backend": self.asr.backend,
                "model": self.asr.model,
                "language": self.asr.language,
                "batch_mode": self.asr.batch_mode,
                "use_dictionary_corpus": self.asr.use_dictionary_corpus,
                "extra_corpus_terms": self.asr.extra_corpus_terms,
            },
            "llm": {
                "model": self.llm.model,
                "temperature": self.llm.temperature,
                "enable_thinking": self.llm.enable_thinking,
                "max_tokens": self.llm.max_tokens,
                "level": self.llm.level,
                "structured": self.llm.structured,
                "tone": self.llm.tone,
                "persona": self.llm.persona,
            },
            "hotkey": {
                "primary_key": self.hotkey.primary_key,
                "fallback_key": self.hotkey.fallback_key,
                "double_tap_threshold": self.hotkey.double_tap_threshold,
                "active_hotkeys": self.hotkey.active_hotkeys,
                "custom_key": self.hotkey.custom_key,
            },
            "ui": {
                "language": self.ui.language,
            },
            "enable_polish": self.enable_polish,
            "auto_paste": self.auto_paste,
            "default_mode": self.default_mode,
        }

    def to_public_dict(self) -> dict:
        data = self.to_dict()
        data["api_key"] = ""
        return data

    def ensure_api_key(self, config_path: Path | None = None) -> Optional[str]:
        if not self.api_key:
            path_display = config_path or Path("config.yaml")
            return (
                "DashScope API key not configured. "
                "Set DASHSCOPE_API_KEY environment variable or add api_key to "
                f"{path_display}"
            )
        return None


__all__ = [
    "ASRBackend",
    "ASRConfig",
    "ASRLanguage",
    "AppConfig",
    "AudioConfig",
    "HOTKEY_ALIASES",
    "HotkeyConfig",
    "LLMConfig",
    "UIConfig",
    "VALID_DEFAULT_MODES",
    "VALID_HOTKEYS",
    "_parse_asr_language",
    "_parse_asr_model",
    "_parse_hotkeys",
    "_parse_level",
    "_parse_llm_model",
    "_parse_persona",
    "_parse_tone",
    "_validate_custom_key",
    "asr_model_handles_inline_polish",
    "get_asr_model_info",
    "get_llm_model_info",
]
