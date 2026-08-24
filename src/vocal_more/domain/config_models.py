"""Pure configuration models and normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlsplit, urlunsplit

from ..localization import UILanguage, normalize_ui_language
from .audio_contract import OUTPUT_SAMPLE_RATE_HZ
from .config_parsing import (
    MAX_AUDIO_BLOCKSIZE,
    MAX_AUDIO_CHANNELS,
    MAX_AUDIO_GAIN,
    MAX_DOUBLE_TAP_THRESHOLD,
    MAX_HIGHPASS_FREQ,
    MAX_LLM_MAX_TOKENS,
    MAX_LLM_TEMPERATURE,
    MIN_AUDIO_BLOCKSIZE,
    MIN_AUDIO_CHANNELS,
    MIN_AUDIO_GAIN,
    MIN_DOUBLE_TAP_THRESHOLD,
    MIN_HIGHPASS_FREQ,
    MIN_LLM_MAX_TOKENS,
    MIN_LLM_TEMPERATURE,
    clamp_float,
    clamp_int,
    parse_bool,
)
from .hotkey_catalog import normalize_custom_key
from .model_catalog import (
    ASRBackend,
    ASR_MODEL_IDS,
    LLM_MODEL_IDS,
    asr_model_handles_inline_polish,
    default_asr_model_for_backend,
    get_asr_model_info,
    get_llm_model_info,
)
from .waveform_calibration import (
    DEFAULT_WAVEFORM_CEILING_DBFS,
    MAX_WAVEFORM_CEILING_DBFS,
    MIN_WAVEFORM_CEILING_DBFS,
)


VALID_HOTKEYS = (
    "fn",
)
LEGACY_BUILT_IN_HOTKEYS = (
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
VALID_DEFAULT_MODES = ("walkie_talkie", "realtime_long", "meeting")
ASRLanguage = Literal["zh", "en", "auto"]
PolishMode = Literal["dictation", "prompt"]
GainMode = Literal["automatic", "manual"]
CaptureBackend = Literal["low_latency", "voice_processing"]
POLISH_PROMPT_OVERRIDE_CATEGORIES = (
    "output_type",
    "level",
    "structured",
    "tone",
    "persona",
)
MAX_CUSTOM_POLISH_PROMPT_LENGTH = 20_000
HOTKEY_ALIASES = {
    "printscreen": "f13",
    "print_screen": "f13",
}


def _parse_realtime_url(value: object) -> str:
    """Normalize an optional DashScope realtime WebSocket endpoint."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "wss"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path.rstrip("/") != "/api-ws/v1/realtime"
        or parsed.query
        or parsed.fragment
        or not (
            hostname == "dashscope.aliyuncs.com"
            or hostname.endswith(".maas.aliyuncs.com")
        )
    ):
        raise ValueError(
            "ASR realtime_url must be the official public endpoint or a "
            "wss://*.maas.aliyuncs.com/api-ws/v1/realtime workspace endpoint"
        )
    netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    return urlunsplit(("wss", netloc, "/api-ws/v1/realtime", "", ""))


@dataclass
class AudioConfig:
    """Audio recording configuration."""

    # Device capture may run at its native rate (commonly 48 kHz). The
    # application PCM boundary is fixed so ASR, persistence, retry and playback
    # all interpret the same bytes identically.
    sample_rate: int = OUTPUT_SAMPLE_RATE_HZ
    channels: int = 1
    capture_channels: int = 1
    # 80 ms at the fixed 16 kHz PCM boundary. The provider matrix showed this
    # to be the best latency/stability point across the supported realtime
    # models while keeping callback and queue overhead bounded.
    blocksize: int = 1280
    input_device: Optional[str] = None
    capture_backend: CaptureBackend = "low_latency"
    gain_mode: GainMode = "automatic"
    gain: float = 2.0
    highpass_filter: bool = True
    highpass_freq: int = 200
    soft_limiter: bool = True
    waveform_ceiling_dbfs: float = DEFAULT_WAVEFORM_CEILING_DBFS

    def __post_init__(self) -> None:
        self.sample_rate = OUTPUT_SAMPLE_RATE_HZ
        legacy_channels = clamp_int(
            self.channels,
            default=1,
            minimum=MIN_AUDIO_CHANNELS,
            maximum=MAX_AUDIO_CHANNELS,
        )
        self.channels = 1
        self.capture_channels = clamp_int(
            legacy_channels if self.capture_channels == 1 else self.capture_channels,
            default=1,
            minimum=MIN_AUDIO_CHANNELS,
            maximum=MAX_AUDIO_CHANNELS,
        )


@dataclass
class ASRConfig:
    """ASR configuration."""

    backend: ASRBackend = "realtime_ws"
    model: str = "qwen3.5-omni-flash-realtime"
    language: ASRLanguage = "auto"
    batch_mode: Literal["manual"] = "manual"
    realtime_url: str = ""
    use_dictionary_corpus: bool = True
    extra_corpus_terms: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    """LLM configuration for text polishing."""

    model: str = "qwen3.5-plus"
    temperature: float = 0.0
    enable_thinking: bool = False
    max_tokens: int = 1024
    polish_mode: PolishMode = "dictation"
    level: Literal["minimal", "balanced", "strong"] = "minimal"
    structured: bool = False
    tone: Literal["neutral", "gentle", "direct"] = "neutral"
    persona: Literal["default", "technical", "bilingual", "professional", "chat"] = "default"
    prompt_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class HotkeyConfig:
    """Hotkey configuration."""

    primary_key: str = "fn"
    fallback_key: str = "fn"
    double_tap_threshold: float = 0.3
    active_hotkeys: list[str] = field(default_factory=lambda: ["fn"])
    custom_key: Optional[dict] = None
    custom_keys: list[dict] = field(default_factory=list)
    command_key: Optional[dict] = None


@dataclass
class UIConfig:
    """UI configuration."""

    language: UILanguage = "zh"
    onboarding_completed: bool = False
    advanced_settings: bool = False


@dataclass
class DictionaryLearningConfig:
    """Privacy-sensitive automatic dictionary-learning settings."""

    enabled: bool = False
    excluded_bundle_ids: list[str] = field(default_factory=list)


@dataclass
class ContextPersonalizationConfig:
    """Coarse app-category personalization with no content collection."""

    enabled: bool = True
    excluded_bundle_ids: list[str] = field(default_factory=list)


def _validate_custom_key(raw: object) -> Optional[dict]:
    return normalize_custom_key(raw)


def _parse_custom_keys(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []

    result: list[dict] = []
    seen_codes: set[int] = set()
    for item in raw:
        key = _validate_custom_key(item)
        if key is None or key["key_code"] in seen_codes:
            continue
        seen_codes.add(key["key_code"])
        result.append(key)
        if len(result) >= 8:
            break
    return result


def _parse_builtin_hotkey(raw: object) -> str:
    if not isinstance(raw, str):
        return "fn"
    canonical = HOTKEY_ALIASES.get(raw, raw)
    if canonical in VALID_HOTKEYS:
        return canonical
    if canonical in LEGACY_BUILT_IN_HOTKEYS:
        return "fn"
    return "fn"


def _parse_hotkeys(raw: list) -> list[str]:
    seen = set()
    result = []
    saw_legacy_builtin = False
    for hotkey in raw:
        canonical = HOTKEY_ALIASES.get(hotkey, hotkey)
        if canonical in LEGACY_BUILT_IN_HOTKEYS:
            saw_legacy_builtin = True
            continue
        if canonical in VALID_HOTKEYS and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    if not result and saw_legacy_builtin:
        return ["fn"]
    return result


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


def _parse_polish_mode(raw: object) -> PolishMode:
    if raw in ("dictation", "prompt"):
        return raw
    return "dictation"


def _parse_prompt_overrides(raw: object) -> dict[str, dict[str, Any]]:
    """Normalize editable polish prompt overrides from config or the settings UI."""
    if not isinstance(raw, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for category in POLISH_PROMPT_OVERRIDE_CATEGORIES:
        item = raw.get(category)
        if not isinstance(item, dict):
            continue
        prompt = item.get("prompt")
        if not isinstance(prompt, str):
            continue
        prompt = prompt[:MAX_CUSTOM_POLISH_PROMPT_LENGTH]
        enabled = parse_bool(item.get("enabled"), False)
        if prompt or enabled:
            result[category] = {"enabled": enabled, "prompt": prompt}
    return result


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


def _parse_excluded_bundle_ids(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        bundle_id = item.strip()[:255]
        if not bundle_id or bundle_id in seen:
            continue
        seen.add(bundle_id)
        result.append(bundle_id)
        if len(result) >= 100:
            break
    return result


@dataclass
class AppConfig:
    """Main application configuration."""

    api_key: str = ""
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    dictionary_learning: DictionaryLearningConfig = field(
        default_factory=DictionaryLearningConfig
    )
    context_personalization: ContextPersonalizationConfig = field(
        default_factory=ContextPersonalizationConfig
    )
    enable_polish: bool = True
    auto_paste: bool = True
    native_fast_paste: bool = False
    default_mode: str = "realtime_long"

    @classmethod
    def _from_dict(cls, data: dict) -> "AppConfig":
        if not isinstance(data, dict):
            return cls()

        config = cls()
        ui_data = data.get("ui")
        audio_data = data.get("audio")
        legacy_existing_config = bool(data)

        for key in (
            "api_key",
            "enable_polish",
            "auto_paste",
            "native_fast_paste",
            "default_mode",
        ):
            if key in data:
                config.apply_update(key, data[key])

        for section in (
            "audio",
            "asr",
            "llm",
            "hotkey",
            "ui",
            "dictionary_learning",
            "context_personalization",
        ):
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            for field_name, value in cls._iter_section_items(section, section_data):
                try:
                    config.apply_update(f"{section}.{field_name}", value)
                except ValueError:
                    continue

        # Existing installations predate the guided setup and advanced-mode
        # split. Preserve their current workflow instead of interrupting them
        # with onboarding or hiding controls after an upgrade. A fresh config
        # is represented by an empty input and retains the new-user defaults.
        if legacy_existing_config and (
            not isinstance(ui_data, dict)
            or "onboarding_completed" not in ui_data
        ):
            config.ui.onboarding_completed = True
        if legacy_existing_config and (
            not isinstance(ui_data, dict)
            or "advanced_settings" not in ui_data
        ):
            config.ui.advanced_settings = True
        # Before gain_mode existed, every saved gain value meant Vocal More's
        # software gain. Keep that behavior on upgrade so Apple AGC is never
        # silently stacked on top of an existing low-voice preset.
        if legacy_existing_config and (
            not isinstance(audio_data, dict)
            or "gain_mode" not in audio_data
        ):
            config.audio.gain_mode = "manual"
        if (
            isinstance(audio_data, dict)
            and "capture_channels" not in audio_data
            and "channels" in audio_data
        ):
            config.audio.capture_channels = clamp_int(
                audio_data["channels"],
                default=1,
                minimum=MIN_AUDIO_CHANNELS,
                maximum=MAX_AUDIO_CHANNELS,
            )

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

        for key in (
            "api_key",
            "default_mode",
            "auto_paste",
            "native_fast_paste",
            "enable_polish",
        ):
            if key in form_state:
                self.apply_update(key, form_state[key])

        for section in (
            "audio",
            "asr",
            "llm",
            "hotkey",
            "ui",
            "dictionary_learning",
            "context_personalization",
        ):
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
        elif section == "hotkey":
            # Read the compatibility field first so the plural field remains
            # authoritative even if a hand-edited YAML file changes key order.
            prioritized_fields = ["custom_key", "custom_keys", "command_key"]

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
        elif field_name == "native_fast_paste":
            self.native_fast_paste = parse_bool(value, self.native_fast_paste)
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
        elif section == "dictionary_learning":
            self._apply_dictionary_learning_update(field_name, value)
        elif section == "context_personalization":
            self._apply_context_personalization_update(field_name, value)
        else:
            raise ValueError(f"Unknown config section: {section}")

    def _apply_audio_update(self, field_name: str, value: Any) -> None:
        if field_name == "sample_rate":
            # Preserve this legacy key when loading old YAML/RPC payloads, but
            # normalize it to the single end-to-end PCM transport contract.
            self.audio.sample_rate = OUTPUT_SAMPLE_RATE_HZ
        elif field_name == "channels":
            # The ASR and WAV transport is deliberately fixed to mono. Device
            # capture topology is negotiated separately by AudioRecorder.
            self.audio.channels = 1
        elif field_name == "capture_channels":
            self.audio.capture_channels = clamp_int(
                value,
                default=self.audio.capture_channels,
                minimum=MIN_AUDIO_CHANNELS,
                maximum=MAX_AUDIO_CHANNELS,
            )
        elif field_name == "blocksize":
            # 1600 and 640 were historical defaults. Treat them as unset when
            # loading saved settings so existing users receive the measured
            # 80 ms provider packet size without rewriting custom values.
            if str(value).strip() in {"640", "1600"}:
                self.audio.blocksize = AudioConfig().blocksize
                return
            self.audio.blocksize = clamp_int(
                value,
                default=self.audio.blocksize,
                minimum=MIN_AUDIO_BLOCKSIZE,
                maximum=MAX_AUDIO_BLOCKSIZE,
            )
        elif field_name == "input_device":
            device = str(value).strip() if value else ""
            self.audio.input_device = device or None
        elif field_name == "capture_backend":
            self.audio.capture_backend = (
                value
                if value in ("low_latency", "voice_processing")
                else "low_latency"
            )
        elif field_name == "gain_mode":
            if value in ("automatic", "manual"):
                self.audio.gain_mode = value
            else:
                # Manual is the safe normalization for malformed persisted
                # values because it cannot accidentally double-process gain.
                self.audio.gain_mode = "manual"
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
        elif field_name == "waveform_ceiling_dbfs":
            self.audio.waveform_ceiling_dbfs = clamp_float(
                value,
                default=self.audio.waveform_ceiling_dbfs,
                minimum=MIN_WAVEFORM_CEILING_DBFS,
                maximum=MAX_WAVEFORM_CEILING_DBFS,
            )
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
        elif field_name == "realtime_url":
            self.asr.realtime_url = _parse_realtime_url(value)
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
            self.llm.polish_mode = _parse_polish_mode(value)
        elif field_name == "level":
            self.llm.level = _parse_level(str(value))
        elif field_name == "structured":
            self.llm.structured = parse_bool(value, self.llm.structured)
        elif field_name == "tone":
            self.llm.tone = _parse_tone(str(value))
        elif field_name == "persona":
            self.llm.persona = _parse_persona(str(value))
        elif field_name == "prompt_overrides":
            self.llm.prompt_overrides = _parse_prompt_overrides(value)
        else:
            raise ValueError(f"Unknown config key: llm.{field_name}")

        model_info = get_llm_model_info(self.llm.model)
        if model_info and not model_info.get("supports_thinking"):
            self.llm.enable_thinking = False

    def _apply_hotkey_update(self, field_name: str, value: Any) -> None:
        if field_name == "primary_key":
            self.hotkey.primary_key = _parse_builtin_hotkey(value)
        elif field_name == "fallback_key":
            self.hotkey.fallback_key = _parse_builtin_hotkey(value)
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
            if (
                self.hotkey.custom_key is not None
                and self.hotkey.command_key is not None
                and self.hotkey.custom_key["key_code"]
                == self.hotkey.command_key["key_code"]
            ):
                self.hotkey.custom_key = None
            self.hotkey.custom_keys = (
                [self.hotkey.custom_key]
                if self.hotkey.custom_key is not None
                else []
            )
        elif field_name == "custom_keys":
            self.hotkey.custom_keys = _parse_custom_keys(value)
            if self.hotkey.command_key is not None:
                command_code = self.hotkey.command_key["key_code"]
                self.hotkey.custom_keys = [
                    key
                    for key in self.hotkey.custom_keys
                    if key["key_code"] != command_code
                ]
            self.hotkey.custom_key = (
                self.hotkey.custom_keys[0]
                if self.hotkey.custom_keys
                else None
            )
        elif field_name == "command_key":
            self.hotkey.command_key = _validate_custom_key(value)
            if self.hotkey.command_key is not None:
                command_code = self.hotkey.command_key["key_code"]
                self.hotkey.custom_keys = [
                    key
                    for key in self.hotkey.custom_keys
                    if key["key_code"] != command_code
                ]
                self.hotkey.custom_key = (
                    self.hotkey.custom_keys[0]
                    if self.hotkey.custom_keys
                    else None
                )
        else:
            raise ValueError(f"Unknown config key: hotkey.{field_name}")

    def _apply_ui_update(self, field_name: str, value: Any) -> None:
        if field_name == "language":
            self.ui.language = _parse_ui_language(value)
        elif field_name == "onboarding_completed":
            self.ui.onboarding_completed = parse_bool(
                value,
                self.ui.onboarding_completed,
            )
        elif field_name == "advanced_settings":
            self.ui.advanced_settings = parse_bool(
                value,
                self.ui.advanced_settings,
            )
        else:
            raise ValueError(f"Unknown config key: ui.{field_name}")

    def _apply_dictionary_learning_update(
        self,
        field_name: str,
        value: Any,
    ) -> None:
        if field_name == "enabled":
            self.dictionary_learning.enabled = parse_bool(
                value,
                self.dictionary_learning.enabled,
            )
        elif field_name == "excluded_bundle_ids":
            self.dictionary_learning.excluded_bundle_ids = (
                _parse_excluded_bundle_ids(value)
            )
        else:
            raise ValueError(
                f"Unknown config key: dictionary_learning.{field_name}"
            )

    def _apply_context_personalization_update(
        self,
        field_name: str,
        value: Any,
    ) -> None:
        if field_name == "enabled":
            self.context_personalization.enabled = parse_bool(
                value,
                self.context_personalization.enabled,
            )
        elif field_name == "excluded_bundle_ids":
            self.context_personalization.excluded_bundle_ids = (
                _parse_excluded_bundle_ids(value)
            )
        else:
            raise ValueError(
                f"Unknown config key: context_personalization.{field_name}"
            )

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "audio": {
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
                "capture_channels": self.audio.capture_channels,
                "blocksize": self.audio.blocksize,
                "input_device": self.audio.input_device,
                "capture_backend": self.audio.capture_backend,
                "gain_mode": self.audio.gain_mode,
                "gain": self.audio.gain,
                "highpass_filter": self.audio.highpass_filter,
                "highpass_freq": self.audio.highpass_freq,
                "soft_limiter": self.audio.soft_limiter,
                "waveform_ceiling_dbfs": self.audio.waveform_ceiling_dbfs,
            },
            "asr": {
                "backend": self.asr.backend,
                "model": self.asr.model,
                "language": self.asr.language,
                "batch_mode": self.asr.batch_mode,
                "realtime_url": self.asr.realtime_url,
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
                "prompt_overrides": {
                    category: dict(override)
                    for category, override in self.llm.prompt_overrides.items()
                },
            },
            "hotkey": {
                "primary_key": self.hotkey.primary_key,
                "fallback_key": self.hotkey.fallback_key,
                "double_tap_threshold": self.hotkey.double_tap_threshold,
                "active_hotkeys": self.hotkey.active_hotkeys,
                "custom_key": self.hotkey.custom_key,
                "custom_keys": (
                    list(self.hotkey.custom_keys)
                    if self.hotkey.custom_keys
                    else (
                        [self.hotkey.custom_key]
                        if self.hotkey.custom_key is not None
                        else []
                    )
                ),
                "command_key": self.hotkey.command_key,
            },
            "ui": {
                "language": self.ui.language,
                "onboarding_completed": self.ui.onboarding_completed,
                "advanced_settings": self.ui.advanced_settings,
            },
            "dictionary_learning": {
                "enabled": self.dictionary_learning.enabled,
                "excluded_bundle_ids": list(
                    self.dictionary_learning.excluded_bundle_ids
                ),
            },
            "context_personalization": {
                "enabled": self.context_personalization.enabled,
                "excluded_bundle_ids": list(
                    self.context_personalization.excluded_bundle_ids
                ),
            },
            "enable_polish": self.enable_polish,
            "auto_paste": self.auto_paste,
            "native_fast_paste": self.native_fast_paste,
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
    "ContextPersonalizationConfig",
    "DictionaryLearningConfig",
    "HOTKEY_ALIASES",
    "HotkeyConfig",
    "LEGACY_BUILT_IN_HOTKEYS",
    "LLMConfig",
    "MAX_CUSTOM_POLISH_PROMPT_LENGTH",
    "POLISH_PROMPT_OVERRIDE_CATEGORIES",
    "UIConfig",
    "VALID_DEFAULT_MODES",
    "VALID_HOTKEYS",
    "_parse_asr_language",
    "_parse_asr_model",
    "_parse_hotkeys",
    "_parse_level",
    "_parse_llm_model",
    "_parse_persona",
    "_parse_prompt_overrides",
    "_parse_tone",
    "_validate_custom_key",
    "asr_model_handles_inline_polish",
    "get_asr_model_info",
    "get_llm_model_info",
]
