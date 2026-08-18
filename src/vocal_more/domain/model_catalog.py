"""Pure model catalog data and lookup helpers."""

from __future__ import annotations

from typing import Literal


ASRBackend = Literal["realtime_ws", "short_file", "omni_offline"]

LLM_MODEL_CATALOG = [
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
        "id": "qwen-audio-3.0-asr-flash-streaming",
        "display_name": "Qwen Audio 3.0 Fast",
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

LLM_MODEL_IDS = {model["id"] for model in LLM_MODEL_CATALOG}
ASR_MODEL_IDS = {model["id"] for model in ASR_MODEL_CATALOG if "id" in model}
DEFAULT_ASR_MODEL_BY_BACKEND = {
    "realtime_ws": "qwen3.5-omni-flash-realtime",
    "short_file": "qwen3-asr-flash",
    "omni_offline": "qwen3.5-omni-plus",
}


def get_llm_model_info(model_id: str) -> dict | None:
    """Look up an LLM model entry by id."""
    return next((model for model in LLM_MODEL_CATALOG if model["id"] == model_id), None)


def get_asr_model_info(model_id: str) -> dict | None:
    """Look up an ASR model entry by id."""
    return next((model for model in ASR_MODEL_CATALOG if model.get("id") == model_id), None)


def asr_model_handles_inline_polish(model_id: str) -> bool:
    """Whether the ASR model can directly produce the final polished text."""
    info = get_asr_model_info(model_id)
    return bool(info and info.get("handles_inline_polish"))


def default_asr_model_for_backend(backend: ASRBackend) -> str:
    """Return the default model for the provided backend."""
    return DEFAULT_ASR_MODEL_BY_BACKEND.get(backend, "qwen3.5-omni-flash-realtime")
