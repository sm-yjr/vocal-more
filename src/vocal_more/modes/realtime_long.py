"""Real-time long recording mode: toggle recording with Fn key."""

import threading
from typing import Callable, Optional

from ..config import asr_model_handles_inline_polish, get_config
from ..core.audio_recorder import AudioRecorder
from ..core.asr_engine import ASREngine
from ..core.keyboard_sim import KeyboardSimulator
from ..dictionary import normalize_terms
from .base_mode import BaseMode, ModeState


class RealtimeLongMode(BaseMode):
    """Real-time long recording mode with toggle activation.

    Flow:
    1. Press Fn (first) → Start recording + streaming ASR
    2. Audio chunks streamed to ASR in real-time during recording
    3. Press Fn (second) → Stop recording, commit ASR, get result
    4. Polish → Paste
    """

    def __init__(
        self,
        on_state_change: Optional[Callable[[ModeState], None]] = None,
        on_result: Optional[Callable[[str], None]] = None,
        on_partial_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        text_polisher: Optional[object] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        recording_store: Optional[object] = None,
    ):
        super().__init__(on_state_change, on_result, on_partial_result, on_error, on_audio_level)

        self.config = get_config()
        self.text_polisher = text_polisher
        self._recording_store = recording_store

        self._asr = ASREngine(
            on_partial_result=self._on_asr_partial,
            on_error=lambda msg: self._on_asr_error(msg),
        )
        self._recorder = AudioRecorder(
            on_audio_level=on_audio_level,
            on_audio_chunk=self._on_audio_chunk,
        )
        self._keyboard = KeyboardSimulator()

        self._processing_thread: Optional[threading.Thread] = None
        self._recording_asr_model = self.config.asr.model

    @property
    def name(self) -> str:
        return "Real-time Long"

    @property
    def description(self) -> str:
        return "Press Fn to start, press again to stop and polish"

    def on_hotkey_pressed(self) -> None:
        """Toggle recording on hotkey press."""
        if self._state == ModeState.IDLE:
            self._start_recording()
        elif self._state == ModeState.RECORDING:
            self._stop_recording()

    def on_hotkey_released(self) -> None:
        """Not used in toggle mode."""
        pass

    def _start_recording(self) -> None:
        """Start recording + streaming ASR."""
        self._recording_asr_model = self.config.asr.model
        self._set_state(ModeState.RECORDING)
        self._asr.start()
        try:
            self._recorder.start()
        except Exception as e:
            print(f"[RealtimeLong] Failed to open audio device: {e}")
            try:
                self._asr.stop()
            except Exception:
                pass
            self._set_state(ModeState.IDLE)

    def _stop_recording(self) -> None:
        """Stop recording and process."""
        pcm_data = self._recorder.stop()

        if len(pcm_data) < 3200:
            self._asr.stop()
            self._set_state(ModeState.IDLE)
            return

        self._set_state(ModeState.PROCESSING)
        self._processing_thread = threading.Thread(
            target=self._finish_transcription, args=(pcm_data,), daemon=True
        )
        self._processing_thread.start()

    def _on_audio_chunk(self, chunk: bytes) -> None:
        """Forward audio chunks to streaming ASR in real-time."""
        self._asr.send_audio(chunk)

    def _on_asr_error(self, msg: str) -> None:
        if self.on_error:
            self.on_error(f"ASR error: {msg}")

    def _on_asr_partial(self, result) -> None:
        if self.on_partial_result and result.text:
            self.on_partial_result(result.text)

    def _finish_transcription(self, pcm_data: bytes) -> None:
        """Commit ASR, get result, polish, paste."""
        recording_id = None
        if self._recording_store:
            try:
                recording_id = self._recording_store.save(
                    pcm_data, "realtime_long", self._recording_asr_model,
                    language=self.config.asr.language,
                )
            except Exception as e:
                print(f"[RealtimeLong] Failed to save recording: {e}")

        try:
            raw_text = self._asr.stop(pcm_data=pcm_data)
            print(f"[RealtimeLong] ASR result: '{raw_text}'")

            if not raw_text.strip():
                print("[RealtimeLong] Empty transcription result")
                if recording_id and self._recording_store:
                    self._recording_store.update(recording_id, "failed")
                self._set_state(ModeState.IDLE)
                return

            if recording_id and self._recording_store:
                self._recording_store.update(recording_id, "success", raw_text)

            final_text = normalize_terms(raw_text)
            uses_inline_polish = asr_model_handles_inline_polish(self._recording_asr_model)
            if self.config.enable_polish and self.text_polisher and not uses_inline_polish:
                try:
                    polish_result = self.text_polisher.polish(raw_text)
                    final_text = polish_result.polished_text
                except Exception as e:
                    if self.on_error:
                        self.on_error(f"Polish error: {e}")

            if self.config.auto_paste:
                self._keyboard.paste_text(final_text)

            if self.on_result:
                self.on_result(final_text)

        except Exception as e:
            if recording_id and self._recording_store:
                self._recording_store.update(recording_id, "failed")
            if self.on_error:
                self.on_error(f"Processing error: {e}")
        finally:
            self._set_state(ModeState.IDLE)

    def cancel(self) -> None:
        """Cancel current operation."""
        if self._state == ModeState.RECORDING:
            self._recorder.stop()
            try:
                self._asr.stop()
            except Exception:
                pass

        self._recording_asr_model = self.config.asr.model
        self._set_state(ModeState.IDLE)
