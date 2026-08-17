"""DashScope Recognition adapter for Qwen-Audio streaming ASR."""

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


class QwenAudioStreamingConversation:
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
        self._lock = threading.Lock()
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
            kwargs["raw_input"] = context_instruction
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
        """Send PCM as a binary Recognition frame without JSON/Base64 transport."""
        self._recognition.send_audio_frame(pcm_bytes)

    def commit(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._recognition.stop()

    def create_response(self) -> None:
        """The dedicated ASR model has no inline response channel."""

    def end_session(self, timeout: float = 5) -> None:
        del timeout

    def close(self) -> None:
        try:
            self.commit()
        except Exception:
            pass


__all__ = ["QwenAudioStreamingConversation"]
