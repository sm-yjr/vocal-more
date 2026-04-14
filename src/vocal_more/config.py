"""Configuration management for Vocal-More."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml

VALID_HOTKEYS = (
    "fn", "right_cmd", "double_cmd",
    "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20",
)

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
    noise_gate: float = 0.0    # RMS threshold below which audio is zeroed (0 = disabled)
    highpass_filter: bool = True   # High-pass filter to remove low-frequency rumble
    highpass_freq: int = 200       # High-pass cutoff frequency in Hz
    soft_limiter: bool = True      # tanh soft limiter instead of hard clip


@dataclass
class ASRConfig:
    """ASR configuration."""

    backend: Literal["realtime_ws", "short_file"] = "realtime_ws"
    model: str = "qwen3-asr-flash-realtime-2026-02-10"
    language: str = "zh"
    batch_mode: Literal["manual"] = "manual"
    use_dictionary_corpus: bool = True
    extra_corpus_terms: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    """LLM configuration for text polishing."""

    model: str = "qwen3.5-plus"
    temperature: float = 0.0
    enable_thinking: bool = False
    max_tokens: int = 65536
    polish_mode: Literal["smart", "always"] = "smart"
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
    active_hotkeys: list[str] = field(default_factory=lambda: ["fn", "double_cmd"])
    custom_key: Optional[dict] = None  # {"key_code": int, "display_name": str, "is_modifier": bool, "flag_mask": int}


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
    return result or ["fn", "double_cmd"]


def _parse_asr_backend(raw: str) -> Literal["realtime_ws", "short_file"]:
    if raw in ("realtime_ws", "short_file"):
        return raw
    return "realtime_ws"


def _parse_batch_mode(raw: str) -> Literal["manual"]:
    if raw == "manual":
        return raw
    return "manual"


def _parse_polish_mode(raw: str) -> Literal["smart", "always"]:
    if raw in ("smart", "always"):
        return raw
    return "smart"


def _parse_llm_model(raw: str) -> str:
    if raw == "qwen-plus":
        return "qwen3.5-plus"
    if raw and raw in _LLM_MODEL_IDS:
        return raw
    return "qwen3.5-plus"


def _parse_asr_model(raw: str) -> str:
    if raw and raw in _ASR_MODEL_IDS:
        return raw
    return "qwen3-asr-flash-realtime-2026-02-10"


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


@dataclass
class Config:
    """Main configuration."""

    api_key: str = ""
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    enable_polish: bool = True
    auto_paste: bool = True
    default_mode: str = "walkie_talkie"  # "walkie_talkie" or "realtime_long"

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
            try:
                with open(config_path) as f:
                    data = yaml.safe_load(f) or {}
                    config = cls._from_dict(data)
            except Exception as e:
                print(f"[Config] Failed to load {config_path}, using defaults: {e}")

        # Override with environment variable if set
        env_api_key = os.environ.get("DASHSCOPE_API_KEY")
        if env_api_key:
            config.api_key = env_api_key

        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        """Create config from dictionary."""
        audio_data = data.get("audio", {})
        asr_data = data.get("asr", {})
        llm_data = data.get("llm", {})
        hotkey_data = data.get("hotkey", {})

        return cls(
            api_key=data.get("api_key", ""),
            audio=AudioConfig(
                sample_rate=audio_data.get("sample_rate", 16000),
                channels=audio_data.get("channels", 1),
                blocksize=audio_data.get("blocksize", 1600),
                input_device=audio_data.get("input_device", None),
                gain=audio_data.get("gain", 2.0),
                noise_gate=audio_data.get("noise_gate", 0.0),
                highpass_filter=bool(audio_data.get("highpass_filter", True)),
                highpass_freq=int(audio_data.get("highpass_freq", 200)),
                soft_limiter=bool(audio_data.get("soft_limiter", True)),
            ),
            asr=ASRConfig(
                backend=_parse_asr_backend(asr_data.get("backend", "realtime_ws")),
                model=_parse_asr_model(asr_data.get("model", "qwen3-asr-flash-realtime-2026-02-10")),
                language=asr_data.get("language", "zh"),
                batch_mode=_parse_batch_mode(asr_data.get("batch_mode", "manual")),
                use_dictionary_corpus=asr_data.get("use_dictionary_corpus", True),
                extra_corpus_terms=asr_data.get("extra_corpus_terms", []),
            ),
            llm=LLMConfig(
                model=_parse_llm_model(llm_data.get("model", "qwen3.5-plus")),
                temperature=llm_data.get("temperature", 0.0),
                enable_thinking=llm_data.get("enable_thinking", False),
                max_tokens=llm_data.get("max_tokens", 65536),
                polish_mode=_parse_polish_mode(
                    llm_data.get("polish_mode", "smart")
                ),
                level=_parse_level(llm_data.get("level", "minimal")),
                structured=bool(llm_data.get("structured", False)),
                tone=_parse_tone(llm_data.get("tone", "neutral")),
                persona=_parse_persona(llm_data.get("persona", "default")),
            ),
            hotkey=HotkeyConfig(
                primary_key=hotkey_data.get("primary_key", "fn"),
                fallback_key=hotkey_data.get("fallback_key", "double_cmd"),
                double_tap_threshold=hotkey_data.get("double_tap_threshold", 0.3),
                active_hotkeys=_parse_hotkeys(
                    hotkey_data.get("active_hotkeys", ["fn", "double_cmd"])
                ),
                custom_key=_validate_custom_key(hotkey_data.get("custom_key")),
            ),
            enable_polish=data.get("enable_polish", True),
            auto_paste=data.get("auto_paste", True),
            default_mode=data.get("default_mode", "walkie_talkie")
                if data.get("default_mode") in ("walkie_talkie", "realtime_long")
                else "walkie_talkie",
        )

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
                "noise_gate": self.audio.noise_gate,
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
                "polish_mode": self.llm.polish_mode,
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
            "enable_polish": self.enable_polish,
            "auto_paste": self.auto_paste,
            "default_mode": self.default_mode,
        }

    def save(self) -> None:
        """Save configuration to file."""
        config_dir = self.get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        with open(self.get_config_path(), "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False)

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
