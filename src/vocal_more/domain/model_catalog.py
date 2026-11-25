"""Pure model catalog data and lookup helpers."""

from __future__ import annotations

from typing import Literal

ASRBackend = Literal["realtime_ws", "short_file", "omni_offline"]

LLM_MODEL_CATALOG = [
    {
        "id": "qwen3.8-flash",
        "display_name": "Qwen 3.8 Flash",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
    {
        "id": "qwen3.8-max",
        "display_name": "Qwen 3.8 Max",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
    {
        "id": "qwen3.7-plus",
        "display_name": "Qwen 3.7 Plus",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
    {
        "id": "qwen3.7-flash",
        "display_name": "Qwen 3.7 Flash",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
    {
        "id": "qwen3.6-plus",
        "display_name": "Qwen 3.6 Plus",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
    {
        "id": "qwen3.5-plus",
        "display_name": "Qwen 3.5 Plus",
        "api": "multimodal_conversation",
        "supports_thinking": True,
    },
]

ALL_ASR_MODELS = [
    {
        # 百炼输入法/短语音听写专用旗舰：支持即时/预编译热词 + 超级热词(weight=50)
        # + 多轮上下文(continue-task 动态更新) + 原生可开关口语润色 + 低声 VAD 调节。
        "id": "qwen-audio-3.1-asr-flash-message",
        "display_name": "Qwen Audio 3.1 ASR Flash Message",
        "transport": "realtime_ws",
        "protocol": "audio_recognition",
        "fallback_model": "qwen-audio-3.1-asr-flash",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
        "supports_instant_hotwords": True,
        "supports_super_hotwords": True,
        "supports_native_disfluency_removal": True,
        "supports_vad_tuning": True,
        "supports_continue_context": True,
    },
    {
        "id": "qwen-audio-3.1-asr-flash-streaming",
        "display_name": "Qwen Audio 3.1 ASR Flash Streaming",
        "transport": "realtime_ws",
        "protocol": "audio_recognition",
        "fallback_model": "qwen-audio-3.1-asr-flash",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
        "supports_instant_hotwords": True,
        "supports_super_hotwords": True,
    },
    {
        "id": "qwen-audio-3.1-asr-flash",
        "display_name": "Qwen Audio 3.1 ASR Flash",
        "transport": "short_file",
        "protocol": "qwen_audio_http",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
        "supports_instant_hotwords": True,
        "supports_super_hotwords": True,
        "supports_native_disfluency_removal": True,
    },
    {
        # 2026-09-21 实测：session.update 需显式 voice="Tina"；服务端默认
        # Chelsie 提交音频时报 "Voice 'Chelsie' is not supported"。
        "id": "qwen3.8-omni-flash-realtime",
        "display_name": "Qwen3.8 Omni Flash Realtime",
        "transport": "realtime_ws",
        "supports_transcription_params": False,
        "input_audio_transcription_model": "qwen3-asr-flash-realtime",
        "handles_inline_polish": True,
        # Qwen3.8 is the semantic transcription owner even when the optional
        # Vocal More polish switch is off.  The sidecar transcript remains a
        # diagnostic/fallback stream, not the user-visible result.
        "always_request_response": True,
        "supports_screen_context": True,
        "language_count": 74,
        "chinese_dialect_count": 39,
        "max_input_tokens": 196_608,
        "max_audio_turns": 100,
        "max_audio_seconds": 600,
        "max_video_seconds": 240,
        "rollover_audio_seconds": 540,
    },
    {
        "id": "qwen3.8-omni-flash",
        "display_name": "Lite",
        "transport": "omni_offline",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": True,
    },
    {
        "id": "qwen3.5-omni-flash-realtime",
        "display_name": "Qwen3.5 Omni Flash Realtime",
        "transport": "realtime_ws",
        "supports_transcription_params": False,
        "input_audio_transcription_model": "qwen3-asr-flash-realtime",
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
        "display_name": "Qwen3.5 Omni Plus Realtime",
        "transport": "realtime_ws",
        "supports_transcription_params": False,
        "input_audio_transcription_model": "qwen3-asr-flash-realtime",
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
    {
        "id": "qwen-audio-3.1-realtime-plus",
        "display_name": "Qwen Audio 3.1 Realtime Plus",
        "transport": "realtime_ws",
        "protocol": "realtime_conversation",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "voice": "longanqian",
        "handles_inline_polish": True,
        "always_request_response": True,
    },
    {
        "id": "qwen-audio-3.1-realtime-flash",
        "display_name": "Qwen Audio 3.1 Realtime Flash",
        "transport": "realtime_ws",
        "protocol": "realtime_conversation",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "voice": "longanqian",
        "handles_inline_polish": True,
        "always_request_response": True,
    },
    {
        "id": "qwen-audio-3.0-realtime-plus",
        "display_name": "Qwen Audio 3.0 Realtime Plus",
        "transport": "realtime_ws",
        "protocol": "realtime_conversation",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "voice": "longanqian",
        "handles_inline_polish": True,
        "always_request_response": True,
    },
    {
        "id": "qwen-audio-3.0-realtime-flash",
        "display_name": "Qwen Audio 3.0 Realtime Flash",
        "transport": "realtime_ws",
        "protocol": "realtime_conversation",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "voice": "longanqian",
        "handles_inline_polish": True,
        "always_request_response": True,
    },
    {
        "id": "fun-asr-realtime",
        "display_name": "Fun-ASR Realtime",
        "transport": "realtime_ws",
        "protocol": "audio_recognition",
        "fallback_model": "qwen3-asr-flash",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
    },
    {
        "id": "qwen-audio-3.0-asr-flash-streaming",
        "display_name": "Qwen Audio 3.0 ASR Flash Streaming",
        "transport": "realtime_ws",
        "protocol": "audio_recognition",
        "fallback_model": "qwen3-asr-flash",
        "supports_transcription_params": False,
        "input_audio_transcription_model": None,
        "handles_inline_polish": False,
    },
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

# Transport (WebSocket / HTTP), wire protocol and output capability are
# independent. Native hotwords do not imply instruction-following polish.
for _model in ALL_ASR_MODELS:
    _model.setdefault("protocol", {
        "realtime_ws": "omni_realtime",
        "short_file": "openai_audio",
        "omni_offline": "omni_completion",
    }[_model["transport"]])
    _model.setdefault(
        "supports_instant_hotwords",
        _model["id"] == "qwen-audio-3.0-asr-flash-streaming",
    )
    _model.setdefault("supports_super_hotwords", False)
    _model.setdefault("supports_native_disfluency_removal", False)
    _model.setdefault("supports_vad_tuning", False)
    _model.setdefault("supports_continue_context", False)
    _is_hybrid = (
        _model["protocol"] == "audio_recognition"
        and _model["supports_native_disfluency_removal"]
    )
    _native_asr = (
        _model["protocol"] == "audio_recognition"
        and _model["supports_instant_hotwords"]
        and not _is_hybrid
    )
    _model["pipeline"] = (
        "hybrid_asr" if _is_hybrid else
        "native_asr" if _native_asr else
        "inline_generation" if _model["handles_inline_polish"] else
        "cascade"
    )

# Dual-track ordering: input-method specialist first (hotwords + native
# disfluency removal + adaptive cascade), followed by latest Omni realtime
# (multimodal + screen context), 3.1 streaming ASR, 3.5 Omni, and 3.1 realtime.
_DICTATION_MODEL_ORDER = (
    "qwen-audio-3.1-asr-flash-message",
    "qwen3.8-omni-flash-realtime",
    "qwen-audio-3.1-asr-flash-streaming",
    "qwen3.5-omni-plus-realtime",
    "qwen3.5-omni-flash-realtime",
    "qwen-audio-3.1-realtime-plus",
    "qwen-audio-3.1-realtime-flash",
)
ASR_MODEL_CATALOG = [
    next(model for model in ALL_ASR_MODELS if model["id"] == model_id)
    for model_id in _DICTATION_MODEL_ORDER
]
DICTATION_MODEL_IDS = {model["id"] for model in ASR_MODEL_CATALOG}

LLM_MODEL_IDS = {model["id"] for model in LLM_MODEL_CATALOG}
ASR_MODEL_IDS = {model["id"] for model in ALL_ASR_MODELS}
DEFAULT_ASR_MODEL_BY_BACKEND = {
    "realtime_ws": "qwen-audio-3.1-asr-flash-message",
    "short_file": "qwen-audio-3.1-asr-flash",
    "omni_offline": "qwen3.8-omni-flash",
}


def get_llm_model_info(model_id: str) -> dict | None:
    """Look up an LLM model entry by id."""
    return next((model for model in LLM_MODEL_CATALOG if model["id"] == model_id), None)


def get_asr_model_info(model_id: str) -> dict | None:
    """Look up an ASR model entry by id."""
    return next((model for model in ALL_ASR_MODELS if model.get("id") == model_id), None)


def asr_model_handles_inline_polish(model_id: str) -> bool:
    """Whether the ASR model can directly produce the final polished text."""
    info = get_asr_model_info(model_id)
    return bool(info and info.get("handles_inline_polish"))


def default_asr_model_for_backend(backend: ASRBackend) -> str:
    """Return the default model for the provided backend."""
    return DEFAULT_ASR_MODEL_BY_BACKEND.get(backend, "qwen-audio-3.1-asr-flash-message")


def asr_model_uses_single_pass(model_id: str) -> bool:
    """Whether default dictation skips a separate text LLM request."""
    info = get_asr_model_info(model_id)
    return bool(info and info["pipeline"] != "cascade")
