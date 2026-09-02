"""Low-overhead adapter for native realtime conversation audio input."""

from __future__ import annotations

import base64
import threading


class BufferedRealtimeConversation:
    """Coalesce recorder blocks into provider-sized realtime audio frames.

    The macOS capture contract emits 40 ms blocks. Qwen Audio recommends
    roughly 100 ms per realtime request, so sending every recorder block adds
    avoidable Base64, JSON and WebSocket work. The final partial frame is
    flushed immediately before commit so no tail audio is lost.
    """

    def __init__(self, conversation: object, *, frame_bytes: int = 3200) -> None:
        self._conversation = conversation
        self._frame_bytes = max(1, int(frame_bytes))
        self._pending_audio = bytearray()
        self._send_lock = threading.Lock()

    def __getattr__(self, name: str):
        return getattr(self._conversation, name)

    def send_audio_frame(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        with self._send_lock:
            self._pending_audio.extend(pcm_bytes)
            while len(self._pending_audio) >= self._frame_bytes:
                frame = bytes(self._pending_audio[: self._frame_bytes])
                del self._pending_audio[: self._frame_bytes]
                self._append_frame(frame)

    def commit(self) -> None:
        with self._send_lock:
            if self._pending_audio:
                frame = bytes(self._pending_audio)
                self._pending_audio.clear()
                self._append_frame(frame)
            self._conversation.commit()

    def close(self) -> None:
        """Discard unsent audio before releasing the provider connection."""
        with self._send_lock:
            self._pending_audio.clear()
        self._conversation.close()

    def _append_frame(self, frame: bytes) -> None:
        audio_b64 = base64.b64encode(frame).decode("ascii")
        self._conversation.append_audio(audio_b64)


__all__ = ["BufferedRealtimeConversation"]
