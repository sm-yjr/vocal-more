"""Walkie-talkie mode: hold to record, release to process."""

from types import SimpleNamespace
from typing import Callable, Optional

from ..application.background_executor import BackgroundExecutor, TaskHandle
from ..application.dictation_workflow import DictationWorkflow
from ..config import asr_model_handles_inline_polish, get_config
from ..core.audio_recorder import AudioRecorder
from ..core.asr_engine import ASREngine
from ..core.keyboard_sim import KeyboardSimulator
from ..dictionary import normalize_terms
from ..localization import t
from .base_mode import BaseMode, ModeState


class WalkieTalkieMode(BaseMode):
    """Walkie-talkie mode: hold Fn to record, release to process.

    Flow:
    1. Hold Fn key → Start recording + start streaming ASR
    2. Audio chunks streamed to ASR in real-time during recording
    3. Release Fn key → Stop recording, commit ASR, get result
    4. Polish → Paste
    """

    def __init__(
        self,
        on_state_change: Optional[Callable[[ModeState], None]] = None,
        on_result: Optional[Callable[[str], None]] = None,
        on_partial_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_processing_stage: Optional[Callable[[str], None]] = None,
        text_polisher: Optional[object] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        recording_store: Optional[object] = None,
    ):
        super().__init__(
            on_state_change,
            on_result,
            on_partial_result,
            on_error,
            on_processing_stage,
            on_audio_level,
        )

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
        self._workflow = DictationWorkflow(
            config=self.config,
            asr_engine=self._asr,
            keyboard=self._keyboard,
            recording_store=self._recording_store,
            normalize_text=normalize_terms,
        )

        self._processing_executor = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-walkie-finish",
        )
        self._processing_thread: Optional[TaskHandle[None]] = None
        self._recording_asr_model = self.config.asr.model
        self._active_session_token = 0

    @property
    def name(self) -> str:
        return "Walkie-Talkie"

    @property
    def description(self) -> str:
        return "Hold Fn to record, release to transcribe and paste"

    def on_hotkey_pressed(self) -> None:
        """Start recording + streaming ASR when hotkey is pressed."""
        if self._state != ModeState.IDLE:
            return

        self._active_session_token = self._begin_session()
        self._recording_asr_model = self.config.asr.model
        self._set_state(ModeState.STARTING)
        self._asr.start()       # Non-blocking: WebSocket connects in background
        try:
            self._recorder.start()  # Audio chunks → _on_audio_chunk → ASR
        except Exception as e:
            print(f"[WalkieTalkie] Failed to open audio device: {e}")
            try:
                self._asr.stop()
            except Exception:
                pass
            if self.on_error:
                self.on_error(
                    t(
                        self.config.ui.language,
                        "mode_microphone_unavailable",
                        details=str(e),
                    )
                )
            self._set_state(ModeState.FAILED)
            self._set_state(ModeState.IDLE)
            return

        self._set_state(ModeState.RECORDING)

    def on_hotkey_released(self) -> None:
        """Stop recording and get ASR result."""
        if self._state != ModeState.RECORDING:
            return

        self._set_state(ModeState.STOPPING)
        pcm_data = self._recorder.stop()

        if len(pcm_data) < 3200:  # Less than 100ms of audio
            self._asr.stop()
            if self.on_error:
                self.on_error(t(self.config.ui.language, "mode_recording_too_short"))
            self._set_state(ModeState.IDLE)
            return

        self._set_state(ModeState.PROCESSING)
        session_token = self._active_session_token
        self._processing_thread = self._processing_executor.submit(
            self._finish_transcription,
            pcm_data,
            session_token,
        )

    def _on_audio_chunk(self, chunk: bytes) -> None:
        """Forward audio chunks to streaming ASR in real-time."""
        self._asr.send_audio(chunk)

    def _on_asr_error(self, msg: str) -> None:
        if self.state in (ModeState.IDLE, ModeState.CANCELLING, ModeState.FAILED):
            return
        if self.on_error:
            self.on_error(
                t(self.config.ui.language, "mode_asr_error", details=msg)
            )

    def _on_asr_partial(self, result) -> None:
        if self.state in (ModeState.IDLE, ModeState.CANCELLING, ModeState.FAILED):
            return
        if self.on_partial_result and result.text:
            if (
                self.state == ModeState.PROCESSING
                and self.config.enable_polish
                and asr_model_handles_inline_polish(self._recording_asr_model)
            ):
                self._set_processing_stage("polishing")
            self.on_partial_result(result.text)

    def _finish_transcription(self, pcm_data: bytes, session_token: int) -> None:
        """Commit ASR, get result, polish, paste."""
        try:
            result = self._workflow.finish_recording(
                pcm_data,
                mode_name="walkie_talkie",
                asr_model=self._recording_asr_model,
                text_polisher=self.text_polisher,
                messages=SimpleNamespace(
                    empty_transcription=t(
                        self.config.ui.language,
                        "settings_empty_transcription",
                    ),
                    processing_error=lambda details: t(
                        self.config.ui.language,
                        "mode_processing_error",
                        details=details,
                    ),
                    polish_error=lambda details: t(
                        self.config.ui.language,
                        "mode_polish_error",
                        details=details,
                    ),
                ),
                on_processing_stage=self._set_processing_stage,
                should_abort=lambda: not self._is_active_session(session_token),
            )
            if self._is_active_session(session_token):
                if getattr(result, "error_message", None):
                    self._set_state(ModeState.FAILED)
                self._emit_workflow_result(result)
        finally:
            if self._is_active_session(session_token):
                self._set_state(ModeState.IDLE)

    def cancel(self, reason: str = "user_cancel") -> None:
        """Cancel current operation."""
        if self._state == ModeState.IDLE:
            return

        previous_state = self._state
        self._log_lifecycle(
            "cancel_requested",
            reason=reason,
            from_state=previous_state.value,
        )
        self._active_session_token = self._invalidate_session(reason=reason)
        self._set_state(ModeState.CANCELLING)
        if previous_state == ModeState.RECORDING:
            self._recorder.stop()
            try:
                self._asr.stop()
            except Exception:
                pass

        self._recording_asr_model = self.config.asr.model
        self._set_state(ModeState.IDLE)

    def close(self) -> None:
        if self.state != ModeState.IDLE:
            self.cancel(reason="mode_close")
        self._processing_executor.close(wait=True)
        if hasattr(self._asr, "close"):
            self._asr.close()
