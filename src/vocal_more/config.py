"""Configuration management for Vocal-More."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from .localization import UILanguage, normalize_ui_language
from .yaml_compat import safe_load_compat

VALID_HOTKEYS = (
    "fn", "right_cmd", "double_cmd",
    "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20",
)
VALID_DEFAULT_MODES = ("walkie_talkie", "realtime_long")
ASRBackend = Literal["realtime_ws", "short_file", "omni_offline"]
ASRLanguage = Literal["zh", "en", "auto"]

# ---------------------------------------------------------------------------
# Model catalogs – served to the Swift frontend via the initialize RPC
# ---------------------------------------------------------------------------

LLM_MODEL_CATALOG = [
    {
        "id": "qwen3.5-plus",
        "display_name": "Qwen 3.5 Plus",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
    {
        "id": "qwen3.6-plus",
        "display_name": "Qwen 3.6 Plus",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
]

ASR_MODEL_CATALOG = [
    {
        "id": "qwen3.5-omni-flash-realtime",
        "display_name": "Lite Fast",
        "transport": "realtime_ws",
        "supports_transcription_params": False,
        "input_audio_transcription_model": "gummy-realtime-v1",
        "handles_inline_polish": True,
    },
    {
        "id": "qwen3.5-omni-flash",
        "display_name": "Lite",
        "transport": "omni_offline",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": True,
    },
    {
        "id": "qwen3.5-omni-plus-realtime",
        "display_name": "Pro Fast",
        "transport": "realtime_ws",
        "supports_transcription_params": False,
        "input_audio_transcription_model": "gummy-realtime-v1",
        "handles_inline_polish": True,
    },
    {
        "id": "qwen3.5-omni-plus",
        "display_name": "Pro",
        "transport": "omni_offline",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": True,
    },
    {"separator": True, "display_name": "───────────"},
    {
        "id": "qwen3-asr-flash-realtime-2026-02-10",
        "display_name": "Legacy Fast",
        "transport": "realtime_ws",
        "supports_transcription_params": True,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
    },
    {
        "id": "qwen3-asr-flash",
        "display_name": "Legacy",
        "transport": "short_file",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
    },
]

_LLM_MODEL_IDS = {m["id"] for m in LLM_MODEL_CATALOG}
_ASR_MODEL_IDS = {m["id"] for m in ASR_MODEL_CATALOG if "id" in m}
_DEFAULT_ASR_MODEL_BY_BACKEND = {
    "realtime_ws": "qwen3.5-omni-flash-realtime",
    "short_file": "qwen3-asr-flash",
    "omni_offline": "qwen3.5-omni-plus",
}


def get_llm_model_info(model_id: str) -> dict | None:
    """Look up an LLM model entry by id."""
    return next((m for m in LLM_MODEL_CATALOG if m["id"] == model_id), None)


def get_asr_model_info(model_id: str) -> dict | None:
    """Look up an ASR model entry by id."""
    return next((m for m in ASR_MODEL_CATALOG if m.get("id") == model_id), None)


def asr_model_handles_inline_polish(model_id: str) -> bool:
    """Whether the ASR model can directly produce the final polished text."""
    info = get_asr_model_info(model_id)
    return bool(info and info.get("handles_inline_polish"))

# Aliases normalized to canonical names on load
HOTKEY_ALIASES = {
    "printscreen": "f13",
    "print_screen": "f13",
}


@dataclass
class AudioConfig:
    """Audio recording configuration."""

    sample_rate: int = 16000
    channels: int = 1
    blocksize: int = 1600  # 100ms at 16kHz
    input_device: Optional[str] = None  # None = system default; store name not index
    gain: float = 2.0          # Software gain multiplier (1.0 = no gain)
    highpass_filter: bool = True   # High-pass filter to remove low-frequency rumble
    highpass_freq: int = 200       # High-pass cutoff frequency in Hz
    soft_limiter: bool = True      # tanh soft limiter instead of hard clip


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
    persona: Literal[
        "default", "technical", "bilingual", "professional", "chat"
    ] = "default"


@dataclass
class HotkeyConfig:
    """Hotkey configuration."""

    primary_key: str = "fn"  # keyCode 63
    fallback_key: str = "double_cmd"
    double_tap_threshold: float = 0.3  # seconds
    active_hotkeys: list[str] = field(default_factory=lambda: ["fn"])
    custom_key: Optional[dict] = None  # {"key_code": int, "display_name": str, "is_modifier": bool, "flag_mask": int}


@dataclass
class UIConfig:
    """UI configuration."""

    language: UILanguage = "zh"


_CUSTOM_KEY_REQUIRED_FIELDS = {"key_code", "display_name", "is_modifier", "flag_mask"}


def _validate_custom_key(raw: object) -> Optional[dict]:
    """Validate a custom_key dict has the expected shape, return None if invalid."""
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
    """Normalize aliases and filter invalid hotkey names."""
    seen = set()
    result = []
    for h in raw:
        canonical = HOTKEY_ALIASES.get(h, h)
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
    if raw and raw in _LLM_MODEL_IDS:
        return raw
    return "qwen3.5-plus"


def _parse_asr_model(raw: str) -> str:
    if raw and raw in _ASR_MODEL_IDS:
        return raw
    return "qwen3.5-omni-flash-realtime"


def _default_asr_model_for_backend(backend: ASRBackend) -> str:
    return _DEFAULT_ASR_MODEL_BY_BACKEND.get(
        backend,
        "qwen3.5-omni-flash-realtime",
    )


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


def _parse_persona(
    raw: str,
) -> Literal["default", "technical", "bilingual", "professional", "chat"]:
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
class Config:
    """Main configuration."""

    api_key: str = ""
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    enable_polish: bool = True
    auto_paste: bool = True
    default_mode: str = "realtime_long"  # "walkie_talkie" or "realtime_long"

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get the configuration directory."""
        return Path.home() / ".vocal-more"

    @classmethod
    def get_config_path(cls) -> Path:
        """Get the configuration file path."""
        return cls.get_config_dir() / "config.yaml"

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from file or environment."""
        config = cls()
        config_path = cls.get_config_path()

        # Load from file if exists
        if config_path.exists():
            from .compatibility import _backup_yaml_file, run_compatibility_check_and_repair

            run_compatibility_check_and_repair("config")
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = safe_load_compat(f) or {}
                    config = cls._from_dict(data)
            except Exception as e:
                backup_path = _backup_yaml_file(config_path, "config-load-failed")
                if backup_path is not None:
                    print(f"[Config] Preserved unreadable config backup at {backup_path}")
                print(f"[Config] Failed to load {config_path}, using defaults: {e}")

        # Override with environment variable if set
        env_api_key = os.environ.get("DASHSCOPE_API_KEY")
        if env_api_key:
            config.api_key = env_api_key

        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        """Create config from dictionary."""
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
            for field, value in cls._iter_section_items(section, section_data):
                try:
                    config.apply_update(f"{section}.{field}", value)
                except ValueError:
                    continue

        return config

    def apply_update(self, key: str, value: Any) -> None:
        """Apply a single config update with normalization."""
        parts = key.split(".")
        if len(parts) == 1:
            self._apply_top_level_update(parts[0], value)
            return
        if len(parts) == 2:
            self._apply_section_update(parts[0], parts[1], value)
            return
        raise ValueError(f"Invalid config key: {key}")

    def apply_form_state(self, form_state: dict[str, Any]) -> None:
        """Apply the normalized settings form payload to the config."""
        if not isinstance(form_state, dict):
            raise ValueError("Form state must be a dict")

        for key in ("api_key", "default_mode", "auto_paste", "enable_polish"):
            if key in form_state:
                self.apply_update(key, form_state[key])

        for section in ("audio", "asr", "llm", "hotkey", "ui"):
            section_data = form_state.get(section)
            if not isinstance(section_data, dict):
                continue
            for field, value in self._iter_section_items(section, section_data):
                self.apply_update(f"{section}.{field}", value)

    @staticmethod
    def _iter_section_items(section: str, section_data: dict[str, Any]):
        """Yield section items in a stable order where derived fields win last."""
        prioritized_fields: list[str] = []
        if section == "asr":
            prioritized_fields = ["backend", "model"]

        seen = set()
        for field in prioritized_fields:
            if field in section_data:
                seen.add(field)
                yield field, section_data[field]

        for field, value in section_data.items():
            if field in seen:
                continue
            yield field, value

    def _apply_top_level_update(self, field: str, value: Any) -> None:
        if field == "api_key":
            self.api_key = str(value or "")
        elif field == "enable_polish":
            self.enable_polish = bool(value)
        elif field == "auto_paste":
            self.auto_paste = bool(value)
        elif field == "default_mode":
            self.default_mode = _parse_default_mode(value)
        else:
            raise ValueError(f"Unknown config key: {field}")

    def _apply_section_update(self, section: str, field: str, value: Any) -> None:
        if section == "audio":
            self._apply_audio_update(field, value)
        elif section == "asr":
            self._apply_asr_update(field, value)
        elif section == "llm":
            self._apply_llm_update(field, value)
        elif section == "hotkey":
            self._apply_hotkey_update(field, value)
        elif section == "ui":
            self._apply_ui_update(field, value)
        else:
            raise ValueError(f"Unknown config section: {section}")

    def _apply_audio_update(self, field: str, value: Any) -> None:
        if field == "sample_rate":
            self.audio.sample_rate = int(value)
        elif field == "channels":
            self.audio.channels = int(value)
        elif field == "blocksize":
            self.audio.blocksize = int(value)
        elif field == "input_device":
            self.audio.input_device = str(value) if value else None
        elif field == "gain":
            self.audio.gain = float(value)
        elif field == "highpass_filter":
            self.audio.highpass_filter = bool(value)
        elif field == "highpass_freq":
            self.audio.highpass_freq = int(value)
        elif field == "soft_limiter":
            self.audio.soft_limiter = bool(value)
        else:
            raise ValueError(f"Unknown config key: audio.{field}")

    def _apply_asr_update(self, field: str, value: Any) -> None:
        if field == "backend":
            self.asr.backend = _parse_asr_backend(str(value))
            model_info = get_asr_model_info(self.asr.model)
            if not model_info or model_info.get("transport") != self.asr.backend:
                self.asr.model = _default_asr_model_for_backend(self.asr.backend)
        elif field == "model":
            self.asr.model = _parse_asr_model(str(value))
            model_info = get_asr_model_info(self.asr.model)
            if model_info:
                self.asr.backend = model_info["transport"]
        elif field == "language":
            self.asr.language = _parse_asr_language(value)
        elif field == "batch_mode":
            self.asr.batch_mode = _parse_batch_mode(str(value))
        elif field == "use_dictionary_corpus":
            self.asr.use_dictionary_corpus = bool(value)
        elif field == "extra_corpus_terms":
            self.asr.extra_corpus_terms = _parse_extra_corpus_terms(value)
        else:
            raise ValueError(f"Unknown config key: asr.{field}")

    def _apply_llm_update(self, field: str, value: Any) -> None:
        if field == "model":
            self.llm.model = _parse_llm_model(str(value))
        elif field == "temperature":
            self.llm.temperature = float(value)
        elif field == "enable_thinking":
            self.llm.enable_thinking = bool(value)
        elif field == "max_tokens":
            self.llm.max_tokens = int(value)
        elif field == "polish_mode":
            return
        elif field == "level":
            self.llm.level = _parse_level(str(value))
        elif field == "structured":
            self.llm.structured = bool(value)
        elif field == "tone":
            self.llm.tone = _parse_tone(str(value))
        elif field == "persona":
            self.llm.persona = _parse_persona(str(value))
        else:
            raise ValueError(f"Unknown config key: llm.{field}")

        model_info = get_llm_model_info(self.llm.model)
        if model_info and not model_info.get("supports_thinking"):
            self.llm.enable_thinking = False

    def _apply_hotkey_update(self, field: str, value: Any) -> None:
        if field == "primary_key":
            self.hotkey.primary_key = str(value)
        elif field == "fallback_key":
            self.hotkey.fallback_key = str(value)
        elif field == "double_tap_threshold":
            self.hotkey.double_tap_threshold = float(value)
        elif field == "active_hotkeys":
            self.hotkey.active_hotkeys = _parse_hotkeys(value if isinstance(value, list) else [])
        elif field == "custom_key":
            self.hotkey.custom_key = _validate_custom_key(value)
        else:
            raise ValueError(f"Unknown config key: hotkey.{field}")

    def _apply_ui_update(self, field: str, value: Any) -> None:
        if field == "language":
            self.ui.language = _parse_ui_language(value)
        else:
            raise ValueError(f"Unknown config key: ui.{field}")

    def to_dict(self) -> dict:
        """Convert configuration to a dictionary."""
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

    def save(self) -> None:
        """Save configuration to file."""
        self._write_config_data(self.to_dict())

    @classmethod
    def _write_config_data(cls, data: dict[str, Any]) -> None:
        """Write normalized configuration data to disk."""
        config_dir = cls.get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        with open(cls.get_config_path(), "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def ensure_api_key(self) -> Optional[str]:
        """Ensure API key is available, return error message if not."""
        if not self.api_key:
            return (
                "DashScope API key not configured. "
                "Set DASHSCOPE_API_KEY environment variable or add api_key to "
                f"{self.get_config_path()}"
            )
        return None


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reload_config() -> Config:
    """Reload configuration from file."""
    global _config
    _config = Config.load()
    return _config
