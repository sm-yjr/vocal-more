"""Pure ASR routing and transcript-composition helpers."""

from __future__ import annotations

from typing import Optional

from ...domain.model_catalog import get_asr_model_info


def omni_offline_fallback_model(model_id: str) -> Optional[str]:
    if not model_id.endswith("-realtime"):
        return None
    fallback_model = model_id.removesuffix("-realtime")
    fallback_info = get_asr_model_info(fallback_model)
    if fallback_info and fallback_info.get("transport") == "omni_offline":
        return fallback_model
    return None


def direct_offline_fallback_model(
    model_id: str,
    duration_seconds: float,
    *,
    threshold_seconds: float,
) -> Optional[str]:
    fallback_model = omni_offline_fallback_model(model_id)
    if fallback_model and duration_seconds >= threshold_seconds:
        return fallback_model
    return None


def join_transcript_segments(segments: list[str]) -> str:
    """Combine chunk transcripts without collapsing natural punctuation boundaries."""
    normalized = [segment.strip() for segment in segments if segment and segment.strip()]
    if not normalized:
        return ""

    result = normalized[0]
    trailing_punctuation = "。！？!?\n"
    leading_punctuation = "，。！？、；：,.!?;:"

    for segment in normalized[1:]:
        if not result:
            result = segment
            continue
        if result.endswith(tuple(trailing_punctuation)):
            result += segment
        elif segment[:1] in leading_punctuation:
            result += segment
        else:
            result += "\n" + segment

    return result
