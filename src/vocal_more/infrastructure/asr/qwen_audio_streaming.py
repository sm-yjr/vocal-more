"""DashScope Recognition adapter for provider-native streaming ASR."""

from __future__ import annotations

import base64
import threading
from typing import Callable, Optional

from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult


class _RecognitionCallbackBridge(RecognitionCallback):
    """Translate DashScope sentence snapshots into the app's callback contract."""

    def __init__(
        self,
        *,
        on_ready: Callable[[], None],
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_complete: Callable[[], None],
        on_error: Callable[[str], None],
        on_close: Callable[[], None],
        on_usage: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._on_ready = on_ready
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_complete = on_complete
        self._on_error = on_error
        self._on_close = on_close
        self._on_usage = on_usage
        self._finalized_text = ""

    def on_open(self) -> None:
        self._on_ready()

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if not isinstance(sentence, dict):
            return

        text = str(sentence.get("text", "") or "")
        if not text:
            return

        if RecognitionResult.is_sentence_end(sentence):
            self._finalized_text += text
            usage = result.get_usage(sentence)
            if isinstance(usage, dict) and self._on_usage is not None:
                self._on_usage(usage)
            self._on_final(text)
            return

        # Recognition returns a replacement snapshot for the current sentence.
        # Prefix it with finalized sentences so the UI always receives the full
        # live transcript rather than a sequence of append-only deltas.
        self._on_partial(self._finalized_text + text)

    def on_complete(self) -> None:
        self._on_complete()

    def on_error(self, result: RecognitionResult) -> None:
        message = getattr(result, "message", None) or str(result)
        self._on_error(str(message))

    def on_close(self) -> None:
        self._on_close()


class StreamingRecognitionConversation:
    """Expose ``Recognition`` through the conversation methods used by ASREngine."""

    def __init__(
        self,
        *,
        model: str,
        sample_rate: int,
        language: Optional[str],
        context_instruction: str,
        on_ready: Callable[[], None],
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_complete: Callable[[], None],
        on_error: Callable[[str], None],
        on_close: Callable[[], None],
        on_usage: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._closed = False
        self._committing = False
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending_audio = bytearray()
        # DashScope recommends roughly 100 ms for Recognition binary frames.
        # The macOS recorder normally supplies 40 ms blocks, so coalesce them
        # here without changing the recorder's latency-sensitive contract.
        self._frame_bytes = max(1, round(sample_rate * 2 * 0.1))
        self._minimum_frame_bytes = max(1, round(sample_rate * 2 * 0.04))
        callback = _RecognitionCallbackBridge(
            on_ready=on_ready,
            on_partial=on_partial,
            on_final=on_final,
            on_complete=on_complete,
            on_error=on_error,
            on_close=on_close,
            on_usage=on_usage,
        )
        kwargs: dict = {
            "semantic_punctuation_enabled": True,
            "heartbeat": True,
        }
        if language:
            kwargs["language_hints"] = [language]
        if context_instruction:
            # Recognition forwards ``raw_input`` as the run-task ``input``
            # object. Provider-native streaming models expect context messages
            # under ``input.context``; a bare string makes the request malformed.
            kwargs["raw_input"] = {
                "context": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": context_instruction[:400],
                            }
                        ],
                    }
                ]
            }
        self._recognition = Recognition(
            model=model,
            callback=callback,
            format="pcm",
            sample_rate=sample_rate,
            **kwargs,
        )

    def connect(self) -> None:
        self._recognition.start()

    def update_session(self, **_kwargs) -> None:
        """Compatibility no-op: Recognition parameters are fixed at start."""

    def append_audio(self, audio_b64: str) -> None:
        self.send_audio_frame(base64.b64decode(audio_b64))

    def send_audio_frame(self, pcm_bytes: bytes) -> None:
        """Coalesce recorder PCM into provider-sized binary frames."""
        if not pcm_bytes:
            return
        with self._send_lock:
            frames: list[bytes] = []
            with self._lock:
                if self._closed or self._committing:
                    return
                self._pending_audio.extend(pcm_bytes)
                while len(self._pending_audio) >= self._frame_bytes:
                    frames.append(bytes(self._pending_audio[: self._frame_bytes]))
                    del self._pending_audio[: self._frame_bytes]
            for frame in frames:
                self._recognition.send_audio_frame(frame)

    def commit(self) -> None:
        with self._send_lock:
            with self._lock:
                if self._closed or self._committing:
                    return
                self._committing = True
                trailing_audio = bytes(self._pending_audio)
                self._pending_audio.clear()
            if trailing_audio:
                if len(trailing_audio) < self._minimum_frame_bytes:
                    trailing_audio = trailing_audio.ljust(self._minimum_frame_bytes, b"\0")
                try:
                    self._recognition.send_audio_frame(trailing_audio)
                except Exception:
                    with self._lock:
                        self._pending_audio[:0] = trailing_audio
                        self._committing = False
                    raise
            try:
                self._recognition.stop()
            except Exception:
                with self._lock:
                    self._committing = False
                raise
            with self._lock:
                self._closed = True
                self._committing = False

    def create_response(self) -> None:
        """The dedicated ASR model has no inline response channel."""

    def end_session(self, timeout: float = 5) -> None:
        del timeout

    def close(self) -> None:
        try:
            self.commit()
        except Exception:
            pass


# Keep the original internal name for third-party extensions and older tests.
QwenAudioStreamingConversation = StreamingRecognitionConversation


__all__ = ["QwenAudioStreamingConversation", "StreamingRecognitionConversation"]
