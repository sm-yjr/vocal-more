"""Helpers for managing streaming ASR session reuse."""

from __future__ import annotations

from typing import Optional


def supports_warm_realtime_session(model_info: Optional[dict]) -> bool:
    return bool(
        model_info
        and model_info.get("transport") == "realtime_ws"
        and model_info.get("input_audio_transcription_model") is not None
    )


def conversation_socket_connected(conversation: object | None) -> bool:
    if conversation is None:
        return False

    ws = getattr(conversation, "ws", None)
    if ws is None:
        return True

    sock = getattr(ws, "sock", None)
    if sock is None:
        return False

    return bool(getattr(sock, "connected", False))


def should_reuse_warm_session(
    *,
    supports_warm_session: bool,
    is_connected: bool,
    matches_model: bool,
) -> bool:
    return supports_warm_session and is_connected and matches_model


__all__ = [
    "conversation_socket_connected",
    "should_reuse_warm_session",
    "supports_warm_realtime_session",
]

